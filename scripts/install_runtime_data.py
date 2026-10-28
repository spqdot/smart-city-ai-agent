"""Download and safely install a runtime-data ZIP during Render's build."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile, BadZipFile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "data/raw/stops.txt",
    "data/processed/transport_graph.graphml",
    "data/processed/timetable.sqlite",
    "data/raw/metro_sul_tejo/mst_schedule.json",
)


def main():
    url = os.getenv("SMARTCITY_DATA_ARCHIVE_URL", "").strip()
    if not url:
        raise SystemExit("Set SMARTCITY_DATA_ARCHIVE_URL to the runtime-data ZIP URL in Render.")
    if not url.startswith("https://"):
        raise SystemExit("SMARTCITY_DATA_ARCHIVE_URL must use HTTPS.")
    expected = os.getenv("SMARTCITY_DATA_ARCHIVE_SHA256", "").strip().lower()
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = Path(temp_dir) / "runtime-data.zip"
        request = Request(url, headers={"User-Agent": "lisbon-journey-planner/1.0"})
        digest = hashlib.sha256()
        try:
            with urlopen(request, timeout=120) as response, archive_path.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
        except Exception as error:
            raise SystemExit(f"Could not download the runtime-data archive: {error}") from error
        if expected and digest.hexdigest() != expected:
            raise SystemExit("Runtime-data archive SHA-256 does not match SMARTCITY_DATA_ARCHIVE_SHA256.")
        try:
            with ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                absent = [name for name in REQUIRED if name not in names]
                if absent:
                    raise SystemExit("Runtime-data archive is missing: " + ", ".join(absent))
                for name in names:
                    member = PurePosixPath(name)
                    if member.is_absolute() or ".." in member.parts:
                        raise SystemExit("Runtime-data archive contains an unsafe path.")
                    destination = (ROOT / Path(*member.parts)).resolve()
                    if not destination.is_relative_to(ROOT.resolve()):
                        raise SystemExit("Runtime-data archive contains an unsafe path.")
                archive.extractall(ROOT)
        except BadZipFile as error:
            raise SystemExit("Runtime-data URL did not return a valid ZIP archive.") from error
    print("Runtime data installed and required timetable files are present.")


if __name__ == "__main__":
    main()
