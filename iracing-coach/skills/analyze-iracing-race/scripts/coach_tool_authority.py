"""What the Race Coach may call, decided before anything is dispatched.

`AI-TOOL-AUTHORITY-001`, and the security half of `AI-COACH-CAPABILITY-001`.

The prompt describes the assistant as bounded while the session registers all
seventeen backend tools with ``approvalPolicy="never"`` and broad roots. Nothing
has misused that, but "bounded" is currently a sentence in a prompt rather than
a property of the system, and a sentence cannot deny a call.

This module makes it a property. Three rules carry the whole design.

* **Default deny.** :func:`authorize` refuses any tool it has not been told
  about. Adding a tool to the server therefore does not grant it to the coach;
  somebody has to classify it first, and :data:`TOOL_CAPABILITIES` is where
  that happens. The alternative - allow unless denied - fails open exactly once
  and silently.
* **Capability before workflow.** A tool that writes, inventories the
  filesystem, touches the network or reads credentials is denied to every
  workflow, and no per-workflow allowlist can grant it. The workflow allowlist
  can only ever narrow what its capability class already permits, so a mistake
  in one allowlist cannot widen authority.
* **Deny before dispatch.** :func:`guard_dispatch` raises rather than returning
  a value, and it is the only sanctioned path to a call. A decision that is
  merely returned can be ignored by a caller that forgets to read it.

The read-only facade this produces is the deterministic app's, unchanged: the
coach gets a narrower view of tools that already exist, never a new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Version of the coach tool authority contract.
COACH_AUTHORITY_VERSION = 1

CAPABILITY_READ_ANALYSIS = "read_analysis"
CAPABILITY_READ_SETUP = "read_setup"
CAPABILITY_INVENTORY = "inventory"
CAPABILITY_WRITE = "write"
CAPABILITY_NETWORK = "network"
CAPABILITY_CREDENTIAL = "credential"

#: What a tool is capable of, named for the authority it needs rather than for
#: the feature it serves.
TOOL_CAPABILITIES: Mapping[str, str] = {
    "analyze_iracing_race": CAPABILITY_READ_ANALYSIS,
    "archive_iracing_knowledge": CAPABILITY_WRITE,
    "build_open_setup_package": CAPABILITY_WRITE,
    "catalog_iracing_setups": CAPABILITY_READ_SETUP,
    "discover_iracing_sessions": CAPABILITY_READ_ANALYSIS,
    "find_iracing_telemetry_events": CAPABILITY_READ_ANALYSIS,
    "garage61_auth_status": CAPABILITY_CREDENTIAL,
    "inventory_iracing_data": CAPABILITY_INVENTORY,
    "iracing_companion_dashboard": CAPABILITY_READ_ANALYSIS,
    "iracing_knowledge_cache_status": CAPABILITY_READ_ANALYSIS,
    "iracing_setup_history": CAPABILITY_READ_SETUP,
    "iracing_strategy_history": CAPABILITY_READ_ANALYSIS,
    "query_iracing_telemetry": CAPABILITY_READ_ANALYSIS,
    "recommend_open_setup_tuning": CAPABILITY_READ_SETUP,
    "recommend_structured_open_setup_tuning": CAPABILITY_READ_SETUP,
    "record_open_setup_feedback": CAPABILITY_WRITE,
    "sync_garage61_references": CAPABILITY_NETWORK,
}

#: The only capabilities the coach may ever exercise. Everything else is denied
#: to every workflow, which is what makes the facade read-only by construction
#: rather than by inspection of each allowlist.
COACH_PERMITTED_CAPABILITIES = (CAPABILITY_READ_ANALYSIS, CAPABILITY_READ_SETUP)

WORKFLOW_RACE_REVIEW = "race_review"
WORKFLOW_SETUP_ADVICE = "setup_advice"
WORKFLOW_LIVE_COACH = "live_coach"

#: Per-workflow allowlists. These narrow; they never widen. A name here that is
#: not permitted by its capability class is a contract error, not a grant, and
#: :func:`_validate_allowlists` refuses to import the module in that state.
WORKFLOW_ALLOWLISTS: Mapping[str, tuple[str, ...]] = {
    WORKFLOW_RACE_REVIEW: (
        "analyze_iracing_race",
        "discover_iracing_sessions",
        "find_iracing_telemetry_events",
        "iracing_companion_dashboard",
        "iracing_strategy_history",
        "query_iracing_telemetry",
    ),
    WORKFLOW_SETUP_ADVICE: (
        "catalog_iracing_setups",
        "iracing_setup_history",
        "recommend_open_setup_tuning",
        "recommend_structured_open_setup_tuning",
    ),
    WORKFLOW_LIVE_COACH: (
        "iracing_companion_dashboard",
        "query_iracing_telemetry",
    ),
}

COACH_WORKFLOWS = tuple(sorted(WORKFLOW_ALLOWLISTS))

DENY_UNKNOWN_TOOL = "unknown_tool"
DENY_UNKNOWN_WORKFLOW = "unknown_workflow"
DENY_CAPABILITY_FORBIDDEN = "capability_forbidden"
DENY_NOT_IN_WORKFLOW = "not_in_workflow_allowlist"

DENY_REASONS = (
    DENY_UNKNOWN_TOOL,
    DENY_UNKNOWN_WORKFLOW,
    DENY_CAPABILITY_FORBIDDEN,
    DENY_NOT_IN_WORKFLOW,
)

__all__ = [
    "COACH_AUTHORITY_VERSION",
    "COACH_PERMITTED_CAPABILITIES",
    "COACH_WORKFLOWS",
    "CoachAuthorityError",
    "DENY_REASONS",
    "TOOL_CAPABILITIES",
    "ToolDenied",
    "ToolDecision",
    "WORKFLOW_ALLOWLISTS",
    "authorize",
    "effective_tools",
    "guard_dispatch",
]


class CoachAuthorityError(ValueError):
    """The authority contract itself was used incorrectly."""


class ToolDenied(PermissionError):
    """A dispatch was refused. Raised before the call, never after."""

    def __init__(self, decision: "ToolDecision") -> None:
        super().__init__(decision.detail)
        self.decision = decision


@dataclass(frozen=True)
class ToolDecision:
    """Whether one workflow may call one tool, and why not when it may not."""

    workflow: str
    tool: str
    allowed: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if self.allowed and self.reason:
            raise CoachAuthorityError("an allowed decision has no denial reason")
        if not self.allowed and self.reason not in DENY_REASONS:
            raise CoachAuthorityError(f"unknown denial reason: {self.reason!r}")

    @property
    def detail(self) -> str:
        if self.allowed:
            return f"{self.tool} is permitted in {self.workflow}"
        return f"{self.tool} is denied in {self.workflow}: {self.reason}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": COACH_AUTHORITY_VERSION,
            "workflow": self.workflow,
            "tool": self.tool,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def _validate_allowlists() -> None:
    """Refuse to load if any allowlist tries to widen authority.

    Runs at import so a mistake is a startup failure rather than a permission
    that quietly exists until something exercises it.
    """
    for workflow, tools in WORKFLOW_ALLOWLISTS.items():
        for tool in tools:
            capability = TOOL_CAPABILITIES.get(tool)
            if capability is None:
                raise CoachAuthorityError(
                    f"{workflow} allowlists the unclassified tool {tool!r}"
                )
            if capability not in COACH_PERMITTED_CAPABILITIES:
                raise CoachAuthorityError(
                    f"{workflow} allowlists {tool!r}, whose {capability} capability "
                    "the coach may never exercise"
                )


def authorize(workflow: str, tool: str) -> ToolDecision:
    """Decide one call. Unknown anything is a denial, never a default allow."""
    if tool not in TOOL_CAPABILITIES:
        return ToolDecision(
            workflow=workflow, tool=tool, allowed=False, reason=DENY_UNKNOWN_TOOL
        )
    if workflow not in WORKFLOW_ALLOWLISTS:
        return ToolDecision(
            workflow=workflow, tool=tool, allowed=False, reason=DENY_UNKNOWN_WORKFLOW
        )
    if TOOL_CAPABILITIES[tool] not in COACH_PERMITTED_CAPABILITIES:
        # Checked before the allowlist so that a forbidden capability reports
        # the real reason even if some allowlist wrongly contained it.
        return ToolDecision(
            workflow=workflow,
            tool=tool,
            allowed=False,
            reason=DENY_CAPABILITY_FORBIDDEN,
        )
    if tool not in WORKFLOW_ALLOWLISTS[workflow]:
        return ToolDecision(
            workflow=workflow, tool=tool, allowed=False, reason=DENY_NOT_IN_WORKFLOW
        )
    return ToolDecision(workflow=workflow, tool=tool, allowed=True)


def guard_dispatch(workflow: str, tool: str) -> ToolDecision:
    """Authorize or raise. The only sanctioned path to a coach tool call."""
    decision = authorize(workflow, tool)
    if not decision.allowed:
        raise ToolDenied(decision)
    return decision


def effective_tools(workflow: str) -> tuple[str, ...]:
    """Exactly what this workflow can reach, for the installed-list evidence.

    Derived from the same predicate :func:`authorize` uses, so the list shown as
    evidence cannot drift from the list actually enforced.
    """
    if workflow not in WORKFLOW_ALLOWLISTS:
        return ()
    return tuple(
        sorted(tool for tool in TOOL_CAPABILITIES if authorize(workflow, tool).allowed)
    )


_validate_allowlists()
