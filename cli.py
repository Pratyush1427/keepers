"""Command-line use without the web app.

  python cli.py scan ~/Pictures
  python cli.py export ~/Pictures/Sorted --layout date_person --mode copy
"""
import argparse
import sys

from organizer.db import db, init_db
from organizer.export import LAYOUTS, MODES, SELECTIONS, export_library
from organizer.scanner import Scanner


def print_progress(s):
    sys.stdout.write(f"\r[{s['processed']}/{s['total']}] {s['state']}: {s['message'][:70]:<70}")
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="Sort photos by date and person.")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="index a folder of photos and group faces")
    scan.add_argument("folder")
    export = sub.add_parser("export", help="write sorted folders")
    export.add_argument("out_dir")
    export.add_argument("--layout", choices=LAYOUTS, default="date_person")
    export.add_argument("--mode", choices=MODES, default="copy")
    export.add_argument("--select", choices=SELECTIONS, default="not_rejected", help="which photos to export")
    args = parser.parse_args()

    init_db()
    if args.command == "scan":
        status = Scanner(on_progress=print_progress).run(args.folder)
        print()
        print(status["message"])
        sys.exit(1 if status["state"] == "error" else 0)
    else:
        with db() as conn:
            print(export_library(conn, args.out_dir, args.layout, args.mode, args.select))


if __name__ == "__main__":
    main()
