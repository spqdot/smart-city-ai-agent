"""Simple local-first journey planner: choose two stops, see routes and a map."""

import importlib

import streamlit as st

st.set_page_config(page_title="Lisbon Journey Planner", page_icon="🚋", layout="wide")

import smartcity.modes as modes_module

# Streamlit may preserve imported modules across reruns. Reload this module so
# the selector functions always match the current app code.
modes_module = importlib.reload(modes_module)

from smartcity.config import load_settings
import smartcity.pipeline as pipeline_module
pipeline_module = importlib.reload(pipeline_module)
import smartcity.journey_service as journey_service_module
journey_service_module = importlib.reload(journey_service_module)
search_journey = journey_service_module.search_journey
import smartcity.map_view as map_view_module

map_view_module = importlib.reload(map_view_module)


def _network_data_signature():
    from smartcity.feeds import MODES_DIR

    files = sorted(MODES_DIR.glob("*/stops.txt")) + sorted(MODES_DIR.glob("*/graph.graphml"))
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files)


@st.cache_resource
def get_registry(data_signature):
    return modes_module.default_registry(load_settings())


registry = get_registry(_network_data_signature())
mode_short_names = {
    "mts": "MST", "train": "CP", "fertagus": "Fertagus", "bus": "Bus",
    "metro": "Metro Lisboa", "carris": "Carris",
}


def _picker_options(query):
    """Search every loaded operator while keeping operator identity visible."""
    area = modes_module.municipality_area_option(
        registry, query, target_modes=set(registry.modes))
    if area:
        count = sum(area["area_stop_counts"].values())
        modes = ", ".join(mode_short_names.get(mode, mode)
                          for mode in area["area_stop_counts"])
        label = f"{area['name']} area - {count} stops ({modes})"
        return {label: area}

    choices = modes_module.stop_options(registry, "Any available transport")
    titles = modes_module.match_stop_options(choices, query)
    labels = {}
    for title in titles:
        stop = choices[title]
        operator = mode_short_names.get(stop["mode"], stop["mode"])
        municipality = stop.get("municipality")
        label = f"{stop['name']} - {operator}"
        if municipality:
            label += f" - {municipality}"
        if label in labels:
            label += f" - {stop['stop_id']}"
        labels[label] = stop
    return labels


def _spelling_hint(query, matches):
    if not query.strip():
        return None
    needle = modes_module.normalize(query)
    exact = any(needle in modes_module.normalize(stop.get("name", ""))
                for stop in matches.values())
    if exact:
        return None
    if matches:
        return "No exact spelling match; showing the closest stop names."
    return "No close stop names found. Check the spelling or try a nearby area."

st.title("Lisbon Journey Planner")
st.caption("Choose your starting stop and destination to see all available journeys in the next two hours.")

left, right = st.columns(2)
with left:
    st.subheader("Where are you going from?")
    origin_query = st.text_input(
        "Starting stop", placeholder="Type a stop name; small spelling mistakes are okay",
        key="origin_query")
    origin_matches = _picker_options(origin_query)
    if hint := _spelling_hint(origin_query, origin_matches):
        st.caption(hint)
    st.caption("Suggestions appear here as you type. Select a stop to continue.")
    origin_label = st.radio("Starting stop suggestions", list(origin_matches), index=None,
                            key="origin_choice", label_visibility="collapsed") if origin_matches else None
    origin = origin_matches.get(origin_label)
with right:
    st.subheader("Where do you want to go?")
    destination_query = st.text_input("Destination stop",
                                      placeholder="Type a stop name; small spelling mistakes are okay",
                                      key="destination_query")
    destination_matches = _picker_options(destination_query)
    if hint := _spelling_hint(destination_query, destination_matches):
        st.caption(hint)
    st.caption("Suggestions appear here as you type. Select a stop to continue.")
    destination_label = st.radio("Destination suggestions", list(destination_matches), index=None,
                                 key="destination_choice", label_visibility="collapsed") if destination_matches else None
    destination = destination_matches.get(destination_label)

if origin and destination:
    same_stop = (origin.get("mode") == destination.get("mode")
                 and origin.get("stop_id") is not None
                 and origin.get("stop_id") == destination.get("stop_id"))
    same_area = (origin.get("area") and destination.get("area")
                 and origin.get("stop_ids_by_mode") == destination.get("stop_ids_by_mode"))
    if same_stop or same_area:
        st.warning("Choose two different stops.")
    else:
        result = search_journey(registry, origin, destination, "Any available transport")
        st.subheader(f"{origin['name']} → {destination['name']}")
        st.subheader("Possible journeys")
        if not result.get("options"):
            st.info(result.get("message", "No timetable-validated journey was found in the next two hours."))
        else:
            st.caption(result.get("window", {}).get("label", "Next two hours, Lisbon time"))
            for number, option in enumerate(result["options"], start=1):
                legs = option.get("legs") or [{
                    "vehicle": option.get("vehicle", "Transit"),
                    "line": option.get("line", ""),
                    "board": option.get("board", origin["name"]),
                    "alight": option.get("alight", destination["name"]),
                    "departs": option.get("departs", ""),
                    "arrives": option.get("arrives", ""),
                }]
                changes = option.get("transfers", 0)
                change_label = "Direct · 0 changes" if changes == 0 else f"{changes} change"
                title = (f"{number}. {option.get('departs', '')} · "
                         f"{option.get('vehicle', 'Transit')} {option.get('line', '')} · "
                         f"{change_label}")
                with st.expander(title, expanded=(number == 1)):
                    for leg_number, leg in enumerate(legs, start=1):
                        st.write(
                            f"**Leg {leg_number}:** {leg.get('vehicle', 'Transit')} "
                            f"{leg.get('line', '')} — {leg.get('board', '')} "
                            f"({leg.get('departs', '')}) → {leg.get('alight', '')} "
                            f"({leg.get('arrives', '')})"
                        )
                    st.caption(option.get("transfer_info", "Direct journey"))
        if result.get("notes"):
            for note in result["notes"]:
                st.caption(note)

        st.subheader("Map")
        st.pydeck_chart(
            map_view_module.build_journey_deck(origin, destination,
                                               result.get("options", []), registry),
            height=420,
            key="journey-map",
        )
else:
    st.info("Search for a stop above, then choose it from the list. The map and journeys appear after both stops are selected.")
