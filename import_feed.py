"""Download a public GTFS feed and import it as a transport mode.

Examples:
    python import_feed.py metro
    python import_feed.py carris train
    python import_feed.py tram --zip path\\to\\feed.zip   (any other operator)
"""

import argparse
from pathlib import Path

from smartcity.feeds import FEEDS, MODES_DIR, download, import_feed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("modes", nargs="+", help=f"known: {', '.join(FEEDS)}")
    parser.add_argument("--zip", type=Path, help="use this GTFS zip instead of downloading")
    args = parser.parse_args()

    for mode in args.modes:
        zip_path = args.zip
        if zip_path is None:
            if mode not in FEEDS:
                parser.error(f"unknown mode '{mode}'; pass --zip for a custom feed")
            print(f"Downloading {mode}...")
            zip_path = download(mode, Path("data/feeds") / f"{mode}.zip")
        import_feed(mode, zip_path, MODES_DIR / mode)


if __name__ == "__main__":
    main()
