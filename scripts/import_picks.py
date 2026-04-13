#!/usr/bin/env python3
"""
Merge entrant pick JSON blob(s) (produced by the in-browser picks UI) into
data/picks.json. Idempotent — re-importing the same entrant overwrites their
prior submission.

Usage:
  python3 import_picks.py picks_alice.json picks_bob.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blobs", nargs="+", help="entrant JSON blob files to merge")
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / "data"
    picks_path = data_dir / "picks.json"
    if picks_path.exists():
        doc = json.loads(picks_path.read_text())
    else:
        cfg = json.loads((data_dir / "config.json").read_text())
        doc = {"year": cfg["year"], "entrants": []}

    by_name = {e["name"].lower(): i for i, e in enumerate(doc["entrants"])}

    for blob_path in args.blobs:
        blob = json.loads(Path(blob_path).read_text())
        name = blob["name"].strip()
        entry = {
            "name": name,
            "submitted_at": blob.get("submitted_at", datetime.now(timezone.utc).isoformat()),
            "picks": blob["picks"],
        }
        key = name.lower()
        if key in by_name:
            doc["entrants"][by_name[key]] = entry
            print(f"  Updated: {name}")
        else:
            doc["entrants"].append(entry)
            by_name[key] = len(doc["entrants"]) - 1
            print(f"  Added: {name}")

    picks_path.write_text(json.dumps(doc, indent=2))
    print(f"\nWrote {picks_path} — {len(doc['entrants'])} entrants")


if __name__ == "__main__":
    main()
