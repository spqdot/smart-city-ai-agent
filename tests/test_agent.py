import pytest
from langchain_core.messages import AIMessage

from smartcity.agent import LLMConfigError, build_agent, create_llm, run_turn
from smartcity.config import load_settings

from .conftest import ScriptedChatModel, tool_call


def make_agent(registry, responses):
    return build_agent(ScriptedChatModel(responses=responses), registry)


def test_agent_asks_clarification_for_ambiguous_stop(registry):
    agent = make_agent(registry, [
        tool_call("search_stops", {"query": "Pragal"}),
        AIMessage(content="Which Pragal stop: Portagem or Av Torrado Silva?"),
    ])
    turn = run_turn(agent, "t1", "Find a bus journey from Pragal to Almada")

    assert turn.tool_results[0]["status"] == "ambiguous"
    assert "Which Pragal stop" in turn.reply
    assert turn.routes == []


def test_agent_finds_route_through_tools(registry):
    agent = make_agent(registry, [
        tool_call("search_stops", {"query": "Pragal Portagem"}, "a"),
        tool_call("search_stops", {"query": "Almada Cristo Rei"}, "b"),
        tool_call("find_route", {"origin_stop_ids": ["1"],
                                 "destination_stop_ids": ["4"]}, "c"),
        AIMessage(content="Take route R1 from Pragal to Almada."),
    ])
    turn = run_turn(agent, "t2", "Pragal Portagem to Almada Cristo Rei")

    assert len(turn.routes) == 1
    assert turn.routes[0]["legs"][0]["route_id"] == "R1"
    assert "R1" in turn.reply


def test_agent_clarification_then_follow_up_keeps_thread(registry):
    agent = make_agent(registry, [
        tool_call("search_stops", {"query": "Pragal"}, "a"),
        AIMessage(content="Which one?"),
        tool_call("search_stops", {"query": "Almada Cristo Rei"}, "b"),
        tool_call("find_route", {"origin_stop_ids": ["1"],
                                 "destination_stop_ids": ["4"]}, "c"),
        AIMessage(content="Route found."),
    ])
    first = run_turn(agent, "t3", "Pragal to Almada")
    assert first.reply == "Which one?"
    second = run_turn(agent, "t3", "Pragal (Portagem)")
    assert second.routes and second.reply == "Route found."


def test_agent_reports_missing_route(registry):
    agent = make_agent(registry, [
        tool_call("search_stops", {"query": "Almada Cristo Rei"}, "a"),
        tool_call("search_stops", {"query": "Pragal Portagem"}, "b"),
        tool_call("find_route", {"origin_stop_ids": ["4"],
                                 "destination_stop_ids": ["1"]}, "c"),
        AIMessage(content="No route in the sampled graph."),
    ])
    turn = run_turn(agent, "t4", "Almada to Pragal")

    result = turn.tool_results[-1]
    assert result["status"] == "no_route"
    assert "sample" in result["message"].lower()
    assert turn.routes == []
    assert "sampled" in turn.reply


def test_agent_cannot_route_with_invented_ids(registry):
    agent = make_agent(registry, [
        tool_call("find_route", {"origin_stop_ids": ["1"],
                                 "destination_stop_ids": ["4"]}),
        AIMessage(content="I need to search first."),
    ])
    turn = run_turn(agent, "t5", "Route please")

    assert turn.tool_results[0]["status"] == "unverified_stop_ids"
    assert turn.routes == []


def test_llm_config_from_environment():
    settings = load_settings({"LLM_PROVIDER": "ollama", "LLM_MODEL": "qwen2.5:7b"})
    assert (settings.provider, settings.model) == ("ollama", "qwen2.5:7b")

    default = load_settings({"OPENAI_API_KEY": "sk-test"})
    assert default.provider == "openai" and default.api_key == "sk-test"
    assert default.model == "gpt-4o-mini"


def test_openai_requires_key_from_environment():
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        create_llm(load_settings({"LLM_PROVIDER": "openai"}))
    with pytest.raises(LLMConfigError):
        create_llm(load_settings({"LLM_PROVIDER": "nonsense"}))
