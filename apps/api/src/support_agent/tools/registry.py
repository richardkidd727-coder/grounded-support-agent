"""The agent's tool set."""

from typing import Any

from support_agent.tools.account import GetAccountProfile, UpdateAccountField
from support_agent.tools.base import Tool
from support_agent.tools.executor import ToolExecutor
from support_agent.tools.identity import VerifyIdentity
from support_agent.tools.policies import SearchPolicies
from support_agent.tools.refunds import IssueRefund
from support_agent.tools.tickets import CreateEscalationTicket


def default_tools() -> list[Tool[Any, Any]]:
    return [
        VerifyIdentity(),
        GetAccountProfile(),
        SearchPolicies(),
        IssueRefund(),
        UpdateAccountField(),
        CreateEscalationTicket(),
    ]


def build_executor(**kwargs: Any) -> ToolExecutor:
    return ToolExecutor(default_tools(), **kwargs)
