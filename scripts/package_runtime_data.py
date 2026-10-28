"""Package only the local files required by the hosted journey API.

The archive is a deployment artifact; keep it out of the Git repository.
"""
import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    Path("data/raw/stops.txt"),
    Path("data/processed/transport_graph.graphml"),
    Path("data/processed/timetable.sqlite"),
    Path("data/raw/metro_sul_tejo/mst_schedule.json"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime-data.zip")
    args = parser.parse_args()
    paths = [ROOT / item for item in REQUIRED]
    for mode_dir in sorted((ROOT / "data/modes").glob("*")):
        if mode_dir.is_dir():
            paths.extend(path for path in sorted(mode_dir.iterdir()) if path.is_file())
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Runtime data is incomplete; missing: " + ", ".join(missing))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
        for path in paths:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"Created {args.output} ({args.output.stat().st_size / 1_000_000:.1f} MB)")
    print("Upload this archive as a private GitHub Release asset; do not commit it.")


if __name__ == "__main__":
    main()
