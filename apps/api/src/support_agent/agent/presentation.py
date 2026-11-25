"""Human-readable step summaries and size-bounded previews for the UI and the trace."""

from __future__ import annotations

from typing import Any

from support_agent.tools.executor import ToolOutcome

_REDACTED_ARGS = frozenset({"card_last4"})
_MAX_STR = 160
_MAX_LIST = 5


def redact_args(args: dict[str, Any] | None) -> dict[str, Any] | None:
    if args is None:
        return None
    return {k: ("****" if k in _REDACTED_ARGS else preview(v, 1)) for k, v in args.items()}


def preview(value: Any, depth: int = 0) -> Any:
    """Bound a JSON-ish value so events stay small (long excerpts, big invoice lists)."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "..."
    if isinstance(value, list):
        items = [preview(v, depth + 1) for v in value[:_MAX_LIST]]
        if len(value) > _MAX_LIST:
            items.append(f"... {len(value) - _MAX_LIST} more")
        return items
    if isinstance(value, dict):
        if depth > 3:
            return "{...}"
        return {str(k): preview(v, depth + 1) for k, v in value.items()}
    return value


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def summarize(outcome: ToolOutcome) -> str:
    if not outcome.ok:
        assert outcome.error is not None
        retried = f" after {outcome.attempts} attempts" if outcome.attempts > 1 else ""
        return f"{outcome.error.kind.value}{retried}"
    out = outcome.output or {}
    match outcome.tool:
        case "verify_identity":
            if out.get("verified"):
                return f"Verified {out.get('customer_name')}"
            return f"Not verified ({out.get('attempts_remaining')} attempt(s) left)"
        case "get_account_profile":
            invoices = len(out.get("recent_invoices") or [])
            return (
                f"{out.get('tier')} plan, {out.get('account_status')}, {invoices} recent invoices"
            )
        case "search_policies":
            return (
                f"{out.get('confidence')} match ({out.get('top_similarity')}), "
                f"{len(out.get('excerpts') or [])} excerpt(s)"
            )
        case "issue_refund":
            decision = out.get("decision")
            if decision == "approved":
                return f"Approved {_money(out.get('amount_refunded'))} ({out.get('refund_id')})"
            if decision == "requires_approval":
                return f"Needs approval by {out.get('approver')}"
            return f"Denied: {out.get('denial_code')}"
        case "update_account_field":
            if out.get("updated"):
                return f"Updated {out.get('field')}"
            return str(out.get("message", "No change"))[:_MAX_STR]
        case "create_escalation_ticket":
            return f"{out.get('ticket_id')} ({out.get('priority')}, {out.get('category')})"
    return "ok"


def completed_action(outcome: ToolOutcome) -> str | None:
    """A state change worth restating if the turn degrades before the model can report it."""
    if not outcome.ok or outcome.output is None:
        return None
    out = outcome.output
    if outcome.tool == "issue_refund" and out.get("decision") == "approved":
        amount = _money(out.get("amount_refunded"))
        return f"a refund of {amount} was approved ({out.get('refund_id')})"
    if outcome.tool == "update_account_field" and out.get("updated"):
        return f"your {str(out.get('field')).replace('_', ' ')} was updated"
    if outcome.tool == "create_escalation_ticket":
        return f"ticket {out.get('ticket_id')} was opened for our team"
    if outcome.tool == "verify_identity" and out.get("verified"):
        return "your identity was verified"
    return None
