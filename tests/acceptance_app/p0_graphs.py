"""Complex, bounded Agent fixtures for the P0 compatibility gate.

These graphs model the five production shapes without importing product code.
They intentionally keep tool outputs deterministic so live model variability is
measured separately from GraphHarbor protocol behavior.
"""

import asyncio
import operator
import os
from typing import Annotated, Any

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from typing_extensions import TypedDict

try:
    from graphs import _langchain_model
except ImportError:
    from .graphs import _langchain_model


class SupervisorState(TypedDict, total=False):
    request: str
    domains: list[str]
    findings: Annotated[list[str], operator.add]
    summary: str


def _plan_domains(state: SupervisorState) -> SupervisorState:
    del state
    return {"domains": ["billing", "technical"]}


def _fan_out(state: SupervisorState) -> list[Send]:
    return [Send("specialist", {"domain": domain}) for domain in state.get("domains", [])]


def _specialist(state: dict[str, object]) -> dict[str, list[str]]:
    domain = str(state.get("domain", "unknown"))
    return {"findings": [f"{domain}: deterministic specialist finding"]}


def _summarize(state: SupervisorState) -> SupervisorState:
    findings = state.get("findings", [])
    return {"summary": " | ".join(sorted(findings))}


_supervisor_builder = StateGraph(SupervisorState)
_supervisor_builder.add_node("plan", _plan_domains)
_supervisor_builder.add_node("specialist", _specialist)
_supervisor_builder.add_node("summarize", _summarize)
_supervisor_builder.add_edge(START, "plan")
_supervisor_builder.add_conditional_edges("plan", _fan_out, ["specialist"])
_supervisor_builder.add_edge("specialist", "summarize")
_supervisor_builder.add_edge("summarize", END)
assistant_supervisor = _supervisor_builder.compile()


class HandoffState(TypedDict, total=False):
    issue: str
    route: str
    specialist: str
    approved: bool
    response: str


def _triage(state: HandoffState) -> Command[str]:
    issue = state.get("issue", "").lower()
    route = (
        "billing" if any(word in issue for word in ("refund", "charge", "bill")) else "technical"
    )
    return Command(update={"route": route}, goto=route)


def _billing(state: HandoffState) -> HandoffState:
    approved = interrupt({"action": "refund", "issue": state.get("issue", "")})
    if approved is not True:
        return {"specialist": "billing", "approved": False, "response": "refund rejected"}
    return {"specialist": "billing", "approved": True, "response": "refund approved"}


def _technical(state: HandoffState) -> HandoffState:
    return {"specialist": "technical", "approved": True, "response": "technical handoff complete"}


_handoff_builder = StateGraph(HandoffState)
_handoff_builder.add_node("triage", _triage)
_handoff_builder.add_node("billing", _billing)
_handoff_builder.add_node("technical", _technical)
_handoff_builder.add_edge(START, "triage")
_handoff_builder.add_edge("billing", END)
_handoff_builder.add_edge("technical", END)
customer_support_handoff = _handoff_builder.compile()


@tool
def read_project_scope(project: str, attachments: list[str]) -> str:
    """Read deterministic project scope and attachment metadata for a test request."""
    rendered_attachments = ", ".join(attachments) or "no attachments"
    return f"{project}: checkout scope; attachments: {rendered_attachments}."


@tool
def fetch_requirements(project: str) -> str:
    """Return fixed requirements for a test-case request."""
    return f"{project}: login requires MFA; payment failures must be retried once."


@tool
def run_validation(case: str) -> str:
    """Run one deterministic validation case."""
    return f"{case}: PASS"


test_case_agent = create_agent(
    model=_langchain_model(),
    tools=[read_project_scope, fetch_requirements, run_validation],
    system_prompt=(
        "You are a test-case planner. Always call read_project_scope once with the project "
        "and attachment metadata, then call fetch_requirements once, then call run_validation "
        "once using the returned requirement. Finish with a concise report."
    ),
    name="p0_test_case_agent",
)


@tool
def read_preference(user: str) -> str:
    """Return a fixed user preference."""
    return f"{user}: prefers morning meetings and 30-minute slots."


@tool
def draft_schedule(request: str) -> str:
    """Draft a deterministic schedule proposal."""
    return f"{request}: proposed 09:00-09:30 tomorrow."


@tool
def coordinate_delegate(request: str) -> str:
    """Return a deterministic delegated calendar availability result."""
    return f"{request}: delegate confirms attendee availability."


personal_assistant = create_agent(
    model=_langchain_model(),
    tools=[read_preference, coordinate_delegate, draft_schedule],
    system_prompt=(
        "You are a personal assistant. Always call read_preference once, coordinate_delegate "
        "once, then call draft_schedule once. Never execute external side effects; return a "
        "proposal that requires approval before booking."
    ),
    name="p0_personal_assistant",
)


class PersonalWorkflowState(TypedDict, total=False):
    messages: list[Any]
    proposal: str
    booking: str


async def _personal_plan(state: PersonalWorkflowState) -> PersonalWorkflowState:
    result = await personal_assistant.ainvoke({"messages": state.get("messages", [])})
    messages = result.get("messages") or []
    rendered = " ".join(str(getattr(message, "content", message)) for message in messages)
    return {"messages": messages, "proposal": rendered}


def _personal_approval(state: PersonalWorkflowState) -> PersonalWorkflowState:
    approved = interrupt({"action": "book_schedule", "proposal": state.get("proposal", "")})
    return {"booking": "booking confirmed" if approved is True else "booking rejected"}


_personal_workflow_builder = StateGraph(PersonalWorkflowState)
_personal_workflow_builder.add_node("plan", _personal_plan)
_personal_workflow_builder.add_node("approval", _personal_approval)
_personal_workflow_builder.add_edge(START, "plan")
_personal_workflow_builder.add_edge("plan", "approval")
_personal_workflow_builder.add_edge("approval", END)
personal_assistant_demo = _personal_workflow_builder.compile()


@tool
def research_fact(topic: str) -> str:
    """Return a fixed fact for delegated research."""
    return f"{topic}: GraphHarbor uses PostgreSQL checkpoints and Redis transport."


_research_subagent = {
    "name": "fact_researcher",
    "description": "Delegate one bounded fact lookup and return the source fact.",
    "system_prompt": "Call research_fact exactly once and return its result.",
    "tools": [research_fact],
}

deepagent_demo = create_deep_agent(
    model=_langchain_model(),
    tools=[],
    system_prompt=(
        "You are a bounded research coordinator. First call write_todos to plan. Then call task "
        "exactly once with subagent_type fact_researcher to delegate the fact lookup. After task "
        "returns, call write_todos to complete the plan, then summarize. Do not use filesystem or shell tools."
    ),
    subagents=[_research_subagent],
    middleware=[TodoListMiddleware()],
    name="p0_deepagent_demo",
)


class McpState(TypedDict, total=False):
    topic: str
    mcp_tool: str
    mcp_result: str


async def _mcp_lookup(state: McpState) -> McpState:
    url = os.environ.get("ACCEPTANCE_MCP_URL", "http://127.0.0.1:8765/mcp")
    client = MultiServerMCPClient({"acceptance": {"transport": "http", "url": url}})
    tools = await client.get_tools()
    if not tools:
        raise RuntimeError("MCP server exposed no tools")
    selected = next((item for item in tools if item.name == "project_fact"), tools[0])
    result = await selected.ainvoke({"topic": state.get("topic", "GraphHarbor")})
    return {"mcp_tool": selected.name, "mcp_result": str(result)}


_mcp_builder = StateGraph(McpState)
_mcp_builder.add_node("mcp_lookup", _mcp_lookup)
_mcp_builder.add_edge(START, "mcp_lookup")
_mcp_builder.add_edge("mcp_lookup", END)
mcp_agent = _mcp_builder.compile()


class NetworkStreamState(TypedDict, total=False):
    phases: Annotated[list[str], operator.add]


async def _network_phase(state: NetworkStreamState) -> NetworkStreamState:
    del state
    await asyncio.sleep(0.25)
    return {"phases": ["phase-complete"]}


_network_builder = StateGraph(NetworkStreamState)
_network_builder.add_node("phase_one", _network_phase)
_network_builder.add_node("phase_two", _network_phase)
_network_builder.add_node("phase_three", _network_phase)
_network_builder.add_edge(START, "phase_one")
_network_builder.add_edge("phase_one", "phase_two")
_network_builder.add_edge("phase_two", "phase_three")
_network_builder.add_edge("phase_three", END)
network_sse = _network_builder.compile()


__all__ = [
    "assistant_supervisor",
    "coordinate_delegate",
    "customer_support_handoff",
    "deepagent_demo",
    "mcp_agent",
    "network_sse",
    "personal_assistant",
    "personal_assistant_demo",
    "read_project_scope",
    "test_case_agent",
]
