from typing import Annotated, TypedDict

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from ..modes import NetworkRegistry
from ..tools import build_tools
from .prompts import SYSTEM_PROMPT


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def build_agent(llm, registry: NetworkRegistry, checkpointer=None, geocoder=None,
                timetable=None, clock=None):
    """Compile the LangGraph agent: LLM node <-> deterministic tool node."""
    tools = build_tools(registry, geocoder, timetable, clock)
    model = llm.bind_tools(tools)

    def agent_node(state: AgentState):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        return {"messages": [model.invoke(messages)]}

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer or MemorySaver())
