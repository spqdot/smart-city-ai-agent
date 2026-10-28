import networkx as nx
import pandas as pd
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from smartcity.data import TransportNetwork
from smartcity.modes import NetworkRegistry


class ScriptedChatModel(BaseChatModel):
    """Fake LLM that replays a fixed list of AI messages."""

    responses: list
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call(name, args, call_id="1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture
def network():
    rows = [
        ("1", "PRAGAL (PORTAGEM)", "Almada"),
        ("2", "Pragal (Av Torrado Silva) Estação", "Almada"),
        ("3", "Pragal (Av Torrado Silva) Estação", "Almada"),
        ("4", "ALMADA (CRISTO REI) TERMINAL", "Almada"),
        ("5", "Cacilhas Terminal", "Almada"),
        ("6", "Ilha Isolada", "Seixal"),
        ("7", "Fora da Amostra", "Seixal"),
        ("8", "Ponto Inverso", "Almada"),
        ("9", "Pragal Norte", "Seixal"),
    ]
    stops = pd.DataFrame(rows, columns=["stop_id", "stop_name", "municipality_name"])
    stops["district_name"] = "Setubal"
    coords = {"1": (38.660, -9.160), "2": (38.661, -9.161), "3": (38.662, -9.162),
              "4": (38.670, -9.150), "5": (38.685, -9.147), "6": (38.900, -9.000),
              "7": (38.910, -9.010), "8": (38.665, -9.155), "9": (38.640, -9.100)}
    stops["stop_lat"] = stops["stop_id"].map(lambda i: str(coords[i][0]))
    stops["stop_lon"] = stops["stop_id"].map(lambda i: str(coords[i][1]))
    stops["locality_name"] = "Pragal"
    stops["parish_name"] = ""

    graph = nx.MultiDiGraph()
    names = {"1": "PRAGAL (PORTAGEM)", "2": "Pragal (Av Torrado Silva)",
             "3": "Pragal (Av Torrado Silva)", "4": "ALMADA (CRISTO REI)",
             "5": "Cacilhas", "6": "Ilha Isolada", "8": "Ponto Inverso",
             "9": "Pragal Norte"}
    for stop_id, name in names.items():
        graph.add_node(stop_id, name=name)
    graph.add_edge("1", "4", route_id="R1", trip_id="t1")
    graph.add_edge("4", "5", route_id="R1", trip_id="t1")
    graph.add_edge("2", "8", route_id="R2", trip_id="t2")
    graph.add_edge("8", "4", route_id="R3", trip_id="t3")
    graph.add_node("6", name="Ilha Isolada")
    return TransportNetwork(mode="bus", stops=stops, graph=graph)


@pytest.fixture
def registry(network):
    reg = NetworkRegistry()
    reg.register_network(network)
    return reg
