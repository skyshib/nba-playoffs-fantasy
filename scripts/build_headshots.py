#!/usr/bin/env python3
"""
Build data/headshots.json by searching ESPN for each unique player name
across picks/stats files (current + historical years).

Output format: { slug: "https://a.espncdn.com/i/headshots/nba/players/full/<id>.png" }

Also writes data/headshots_meta.json with extra info (espn_id, current team).
Cached: existing headshots are not re-fetched.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install: pip install requests", file=sys.stderr)
    sys.exit(1)


SEARCH = "https://site.web.api.espn.com/apis/common/v3/search?query={q}&type=player&limit=5"
HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nba/players/full/{id}.png"


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\u2019']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def collect_player_names(data_dir: Path) -> dict[str, str]:
    """Walk data/ and grab all unique player names (slug -> display name)."""
    names: dict[str, str] = {}

    def add(slug, name):
        if slug and name and slug not in names:
            names[slug] = name

    # Current + historical picks
    for picks_path in [data_dir / "picks.json"] + list(data_dir.glob("*/picks.json")):
        if not picks_path.exists():
            continue
        doc = json.loads(picks_path.read_text())
        for ent in doc.get("entrants", []):
            for _, pick in (ent.get("picks") or {}).items():
                add(pick.get("player_id"), pick.get("name"))

    # Stats (covers current + historical players who weren't necessarily picked)
    for stats_path in [data_dir / "stats.json"] + list(data_dir.glob("*/stats.json")):
        if not stats_path.exists():
            continue
        doc = json.loads(stats_path.read_text())
        for slug, p in (doc.get("players") or {}).items():
            add(slug, p.get("name"))

    # Budget if present
    bp = data_dir / "budget.json"
    if bp.exists():
        for p in json.loads(bp.read_text()).get("players", []):
            add(p.get("player_id"), p.get("name"))

    return names


def search_athlete(name: str) -> dict | None:
    """Return { id, displayName } for the best NBA match, or None."""
    try:
        url = SEARCH.format(q=requests.utils.quote(name))
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        items = r.json().get("items", [])
        for it in items:
            if it.get("league") == "nba" and it.get("type") == "player":
                return {"id": it.get("id"), "displayName": it.get("displayName")}
    except Exception:
        return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--rebuild", action="store_true",
                    help="Re-fetch even if a slug is already in headshots.json")
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / "data"
    out_path = data_dir / "headshots.json"
    meta_path = data_dir / "headshots_meta.json"

    headshots: dict[str, str] = {}
    meta: dict[str, dict] = {}
    if out_path.exists() and not args.rebuild:
        headshots = json.loads(out_path.read_text())
    if meta_path.exists() and not args.rebuild:
        meta = json.loads(meta_path.read_text())

    names = collect_player_names(data_dir)
    print(f"Collected {len(names)} unique players")

    todo = [(s, n) for s, n in names.items() if s not in headshots]
    print(f"Fetching {len(todo)} new headshots...")

    misses = []
    for i, (slug, name) in enumerate(todo, start=1):
        result = search_athlete(name)
        if result and result.get("id"):
            headshots[slug] = HEADSHOT_URL.format(id=result["id"])
            meta[slug] = {"espn_id": result["id"], "name": name}
        else:
            misses.append((slug, name))
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} (misses: {len(misses)})")
            out_path.write_text(json.dumps(headshots, indent=2, sort_keys=True))
            meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
        time.sleep(args.sleep)

    out_path.write_text(json.dumps(headshots, indent=2, sort_keys=True))
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True))
    print(f"\nWrote {out_path} ({len(headshots)} entries) — {len(misses)} unresolved")
    if misses:
        for s, n in misses[:20]:
            print(f"  - {n} ({s})")


if __name__ == "__main__":
    main()
