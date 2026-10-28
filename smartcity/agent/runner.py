import json
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, ToolMessage


@dataclass
class AgentTurn:
    reply: str
    tool_results: list[dict] = field(default_factory=list)

    @property
    def departures(self) -> list[dict]:
        """find_departures results that have options or a no-journey message."""
        return [r for r in self.tool_results
                if r.get("_tool") in {"find_departures", "find_mst_departures", "find_any_departures"}
                and r.get("status") not in {"error", "unknown_mode", "unverified_stop_ids"}]

    @property
    def routes(self) -> list[dict]:
        """Graph routes from find_route (connectivity only, not a schedule)."""
        routes, seen = [], set()
        for result in self.tool_results:
            if result.get("_tool") != "find_route" or result.get("status") != "ok":
                continue
            route = result
            key = tuple((l["route_id"], l["from_stop_id"], l["to_stop_id"])
                        for l in route["legs"])
            if key not in seen:
                seen.add(key)
                routes.append(route)
        return routes


def run_turn(agent, thread_id: str, user_text: str) -> AgentTurn:
    """Send one user message and return the reply plus this turn's tool outputs."""
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 12}
    before = len(agent.get_state(config).values.get("messages", []))
    state = agent.invoke({"messages": [HumanMessage(content=user_text)]}, config)

    messages = state["messages"]
    results = []
    for message in messages[before:]:
        if isinstance(message, ToolMessage):
            try:
                data = json.loads(message.content)
            except (TypeError, ValueError):
                data = {"status": "error", "message": str(message.content)}
            if isinstance(data, dict):
                data["_tool"] = message.name
                results.append(data)

    turn = AgentTurn(reply=str(messages[-1].content), tool_results=results)
    if turn.departures:
        result = turn.departures[-1]
        options = result.get("options", [])
        if options:
            lines = [f"{o.get('departs', '?')} · {o.get('vehicle', '')} "
                     f"{o.get('line', '')}: {o.get('board', '')} → {o.get('alight', '')}"
                     for o in options]
            turn.reply = result.get("message", "") + "\n" + "\n".join(lines)
        else:
            turn.reply = result.get("message", str(messages[-1].content))
    return turn
