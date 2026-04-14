#!/usr/bin/env python3
"""
Pull current pick submissions from the Google Sheet (via the Apps Script
?action=picks endpoint) and write data/picks.json.

Workflow:
  1. Curate the Sheet directly — delete test/invalid rows, fix typos, etc.
  2. Run this script. The script fetches whatever's currently in the Sheet,
     dedupes by entrant name (latest submission wins), and writes picks.json.
  3. The scoreboard auto-refreshes from picks.json on the next reload.

Usage:
  python3 scripts/sync_picks.py            # uses picks_endpoint from data/config.json
  python3 scripts/sync_picks.py --dry-run  # show what would change without writing
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin

try:
    import requests
except ImportError:
    print("Install: pip install requests", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--endpoint", default=None,
                    help="Override picks_endpoint from config.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / "data"
    cfg = json.loads((data_dir / "config.json").read_text())
    endpoint = args.endpoint or cfg.get("picks_endpoint", "")
    if not endpoint:
        print("ERROR: picks_endpoint not set in config.json", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching submissions from {endpoint}...")
    r = requests.get(endpoint, params={"action": "picks"}, timeout=30, allow_redirects=True)
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("rows", [])
    print(f"  {len(rows)} rows in sheet")

    # Dedupe by entrant name (case-insensitive); keep latest submitted_at
    by_name: dict[str, dict] = {}
    for row in rows:
        try:
            blob = json.loads(row["raw"]) if isinstance(row["raw"], str) else row["raw"]
        except Exception as e:
            print(f"  WARN row {row.get('row')}: bad JSON ({e})")
            continue
        name = (blob.get("name") or row.get("entrant") or "").strip()
        if not name:
            continue
        key = name.lower()
        ts = blob.get("submitted_at") or row.get("submitted") or ""
        existing = by_name.get(key)
        if existing is None or ts > existing.get("submitted_at", ""):
            by_name[key] = {
                "name": name,
                "email": blob.get("email"),
                "submitted_at": ts,
                "tiebreaker": blob.get("tiebreaker"),
                "picks": blob.get("picks", {}),
            }

    entrants = sorted(by_name.values(), key=lambda e: e["name"].lower())
    doc = {"year": cfg.get("year"), "entrants": entrants}

    picks_path = data_dir / "picks.json"
    if args.dry_run:
        print(f"\n[dry-run] would write {picks_path}")
        for e in entrants:
            print(f"  - {e['name']} ({e['submitted_at']})")
        return

    picks_path.write_text(json.dumps(doc, indent=2))
    print(f"\nWrote {picks_path} — {len(entrants)} entrants:")
    for e in entrants:
        print(f"  - {e['name']}")


if __name__ == "__main__":
    main()
