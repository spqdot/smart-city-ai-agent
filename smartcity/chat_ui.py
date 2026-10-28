import uuid

import streamlit as st

from .agent import LLMConfigError, build_agent, create_llm, run_turn
from .config import load_settings
from .geo import make_geocoder
from .map_view import build_deck
from .modes import default_registry
from .timetable import Timetable


def _render_route(route: dict, key: str) -> None:
    st.markdown(
        f"**Graph connection (sampled graph, not a schedule):** board at "
        f"**{route['board_at']['name']}**, get off at "
        f"**{route['alight_at']['name']}** — {route['stops_travelled']} stops, "
        f"{route['num_transfers']} transfer(s)"
    )
    st.table(
        [{"Service": f"{leg.get('vehicle', 'Bus')} {leg['bus_number']}",
          "Line": leg["bus_description"],
          "From": leg["from_name"], "To": leg["to_name"],
          "Stops": leg["stops_travelled"]}
         for leg in route["legs"]]
    )
    deck = build_deck(route)
    if deck is not None:
        st.pydeck_chart(deck, height=420, key=f"map-{key}")
    st.caption(route["note"] + " " + route["sample_note"])


def render_departures(result: dict) -> None:
    """Show a find_departures / pipeline result: options with stops, or the no-journey message."""
    window = result.get("window", {})
    st.markdown(f"**Departures in the next {window.get('minutes', 30)} minutes** — "
                f"{window.get('label', '')}")
    if result["status"] != "ok":
        (st.error if result["status"] in {"invalid_input", "search_failed"}
         else st.warning)(result["message"])
    else:
        st.success(result["message"])
        for option in result["options"]:
            walk = max(option["walk_from_km"], option["walk_to_km"])
            if option["vehicle"] == "Bus":
                title = (f"{option['departs']} · Bus {option['line']} → "
                         f"{option['headsign'] or option['alight']} · "
                         f"{option.get('transfer_info', 'Direct · 0 changes')}")
            else:
                title = (f"{option['departs']} · {option['vehicle']} {option['line']}"
                         f" → {option['headsign'] or option['alight']}"
                         f" · {option.get('agent', option['label'])}"
                         f" · {option.get('transfer_info', 'Direct · 0 changes')}")
            with st.expander(title):
                facts = {
                    "Departs": f"{option['departs']} from {option['board']}",
                    "Transfers": option["transfer_info"],
                }
                if option["vehicle"] == "Bus":
                    facts["Bus number"] = str(option["line"])
                else:
                    facts["Service"] = f"{option['vehicle']} {option['line']}"
                if option["arrives"]:
                    facts["Arrives"] = f"{option['arrives']} at {option['alight']}"
                    facts["Duration"] = f"{option['duration_minutes']} min"
                if walk >= 0.3:
                    facts["Note"] = (f"Nearest {option['label']} stops are up to "
                                     f"{walk} km from your places.")
                for key, value in facts.items():
                    st.write(f"**{key}:** {value}")
                if option.get("legs"):
                    st.table([{
                        "#": i + 1,
                        "Bus number" if leg["vehicle"] == "Bus" else "Service":
                            str(leg["line"]) if leg["vehicle"] == "Bus" else
                            f"{leg['vehicle']} {leg['line']}",
                        "Departs": f"{leg['departs']} · {leg['board']}",
                        "Arrives": f"{leg['arrives']} · {leg['alight']}",
                    } for i, leg in enumerate(option["legs"])])
                else:
                    st.table([{"#": i + 1, "Stop": s["name"], "Time": s["time"]}
                              for i, s in enumerate(option["stops"])])
    for hint in result.get("graph_hint", []):
        st.caption(f"Hint (not a scheduled service): {hint}")
    if result.get("agents"):
        with st.expander("Operator search status"):
            for item in result["agents"]:
                if item["status"] in {"ok", "searched"}:
                    detail = (f"searched; {item['count']} matching journey(s); "
                              f"{item.get('origin_departures', 0)} origin departure(s)")
                else:
                    detail = f"{item['status'].replace('_', ' ')}: {item['reason']}"
                st.write(f"**{item['agent']}**: {detail}")
    if result.get("skipped"):
        with st.expander("Modes not searched or without coverage"):
            for item in result["skipped"]:
                st.write(f"**{item['label']}** — {item['reason']}")
    for note in result.get("notes", []):
        st.caption(note)


def render_chat() -> None:
    st.divider()
    st.subheader("💬 Ask the journey agent")
    st.caption(
        "Tell me where you start and where you want to go, e.g. “From Pragal to "
        "Cacilhas” or “metro from Oriente to Alameda”. I list scheduled departures "
        "in the next two hours (Lisbon time). Routes and times come from "
        "deterministic tools on published timetables, not from the LLM."
    )

    if "agent" not in st.session_state:
        settings = load_settings()
        try:
            llm = create_llm(settings)
        except LLMConfigError as error:
            st.warning(str(error))
            return
        st.session_state.agent = build_agent(
            llm, default_registry(settings),
            geocoder=make_geocoder(settings.geocoder),
            timetable=Timetable(settings.timetable_path),
        )
        st.session_state.thread_id = uuid.uuid4().hex
        st.session_state.chat = []

    for number, entry in enumerate(st.session_state.chat):
        with st.chat_message(entry["role"]):
            st.write(entry["text"])
            for departures in entry.get("departures", []):
                render_departures(departures)
            for index, route in enumerate(entry.get("routes", [])):
                _render_route(route, f"{number}-{index}")

    prompt = st.chat_input("Where are you starting from, and where do you want to go?")
    if not prompt:
        return

    st.session_state.chat.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                turn = run_turn(
                    st.session_state.agent, st.session_state.thread_id, prompt
                )
        except Exception as error:
            st.error(f"The agent failed: {error}")
            return
        st.write(turn.reply)
        for departures in turn.departures:
            render_departures(departures)
        for index, route in enumerate(turn.routes):
            _render_route(route, f"{len(st.session_state.chat)}-{index}")

    st.session_state.chat.append(
        {"role": "assistant", "text": turn.reply, "routes": turn.routes,
         "departures": turn.departures}
    )
