from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from support_agent.cli.export_schemas import build_schema
from support_agent.events import AgentEventAdapter, ErrorCode, ErrorEvent, StepKind, StepStarted

BASE: dict[str, Any] = {"session_id": "s1", "turn_id": "t1", "seq": 0, "ts": datetime.now(UTC)}


def test_discriminated_union_round_trip() -> None:
    event = StepStarted(
        **BASE,
        step_id="st1",
        kind=StepKind.TOOL,
        name="verify_identity",
        label="Verifying identity...",
    )
    parsed = AgentEventAdapter.validate_json(event.model_dump_json())
    assert isinstance(parsed, StepStarted)
    assert parsed == event


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentEventAdapter.validate_python({**BASE, "type": "nope"})


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentEventAdapter.validate_python({**BASE, "type": "token", "text": "hi", "oops": 1})


def test_error_event_serialises_code_as_string() -> None:
    err = ErrorEvent(
        **BASE, code=ErrorCode.RATE_LIMITED, message="Slow down", retryable=True, retry_after_s=3
    )
    assert err.model_dump(mode="json")["code"] == "rate_limited"


def test_exported_schema_covers_every_event_type() -> None:
    schema = build_schema()
    defs = schema["$defs"]
    assert isinstance(defs, dict)
    objects = [d["properties"] for d in defs.values() if "properties" in d]
    types = {p["type"]["const"] for p in objects if "type" in p}
    assert types == {
        "turn.started",
        "step.started",
        "step.completed",
        "token",
        "guardrail.result",
        "escalated",
        "metrics",
        "turn.completed",
        "error",
    }
