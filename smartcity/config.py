import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1:8b",
}


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    stops_path: Path
    graph_path: Path
    geocoder: str = "nominatim"
    timetable_path: Path = ROOT / "data/processed/timetable.sqlite"


def load_settings(env=None) -> Settings:
    """Read configuration from environment variables (and a local .env file)."""
    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        env = os.environ

    provider = (env.get("LLM_PROVIDER") or "openai").strip().lower()
    model = env.get("LLM_MODEL") or DEFAULT_MODELS.get(provider, "")

    return Settings(
        provider=provider,
        model=model,
        base_url=env.get("LLM_BASE_URL") or None,
        api_key=env.get("OPENAI_API_KEY") or env.get("LLM_API_KEY") or None,
        geocoder=(env.get("GEOCODER") or "nominatim").strip().lower(),
        timetable_path=Path(
            env.get("TIMETABLE_PATH") or ROOT / "data/processed/timetable.sqlite"
        ),
        stops_path=Path(env.get("STOPS_PATH") or ROOT / "data/raw/stops.txt"),
        graph_path=Path(
            env.get("GRAPH_PATH") or ROOT / "data/processed/transport_graph.graphml"
        ),
    )
