#!/usr/bin/env python3
"""
Emit sim/allyears.js — the data backbone for the live optimizer dashboard.

For each year (2022–2025 historical, 2026 current):
  bracket:        { "East": [{team_id, team_name, seed}, …8], "West": […8] }
  pool_by_seed:   { "1": [{slug, name, team, team_id, ppg}, …], … "8": […] }
  opponents:      [{name, players: [{slug, name, team, ppg, seed}, …8]}, …]

Reads:
  data/<year>/{stats,picks}.json for historical years
  data/{budget,picks}.json + entrant rosters for 2026

Usage:
  python3 scripts/build_simdata.py
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\u2019']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def derive_bracket_from_stats(stats: dict) -> dict:
    """Walk historical stats.json, extract one entry per team (with seed)."""
    teams_by_seed: dict[int, dict] = {}
    for slug, p in stats.get("players", {}).items():
        team = p.get("team")
        seed = p.get("seed")
        if not team or not seed:
            continue
        if seed not in teams_by_seed:
            teams_by_seed[seed] = {}
        if team not in teams_by_seed[seed]:
            teams_by_seed[seed][team] = True
    # Each seed should have ~2 teams (East + West). Without conference info we
    # split arbitrarily; the optimizer doesn't actually need East/West labels —
    # it only needs the 16-team list with seeds.
    rows = []
    for seed in range(1, 9):
        for team in sorted(teams_by_seed.get(seed, {})):
            rows.append({"team_name": team, "seed": seed})
    return rows


def historical_year(year: int, data_dir: Path) -> dict | None:
    stats_path = data_dir / str(year) / "stats.json"
    picks_path = data_dir / str(year) / "picks.json"
    if not stats_path.exists() or not picks_path.exists():
        return None
    stats = json.loads(stats_path.read_text())
    picks = json.loads(picks_path.read_text())

    bracket = derive_bracket_from_stats(stats)

    # pool_by_seed: every player tracked in stats.json, grouped by seed
    pool: dict[int, list] = defaultdict(list)
    for slug, p in stats.get("players", {}).items():
        seed = p.get("seed")
        if not seed or not (1 <= seed <= 8):
            continue
        cost = p.get("cost")
        if cost is None:
            continue
        pool[seed].append({
            "slug": slug,
            "name": p["name"],
            "team": p.get("team", ""),
            "ppg": round(float(cost), 2),
        })
    # Sort each seed's pool by PPG desc for nice display
    for seed in pool:
        pool[seed].sort(key=lambda x: -x["ppg"])

    # opponents: every entrant's roster. Historical picks.json lacks team info
    # per pick (Excel didn't record teams), so cross-reference stats.json.
    stats_players = stats.get("players", {})
    opponents = []
    for ent in picks.get("entrants", []):
        plist = []
        for s in range(1, 9):
            pick = (ent.get("picks") or {}).get(str(s))
            if not pick:
                continue
            slug = pick.get("player_id")
            stats_p = stats_players.get(slug, {})
            team = pick.get("team") or stats_p.get("team") or ""
            ppg = pick.get("cost")
            if ppg is None:
                ppg = stats_p.get("cost", 0)
            plist.append({
                "slug": slug,
                "name": pick.get("name"),
                "team": team,
                "ppg": ppg,
                "seed": s,
            })
        opponents.append({"name": ent["name"], "players": plist})

    return {
        "bracket": bracket,
        "pool_by_seed": {str(s): pool[s] for s in sorted(pool.keys())},
        "opponents": opponents,
    }


def current_year(data_dir: Path) -> dict | None:
    bp = data_dir / "budget.json"
    pp = data_dir / "picks.json"
    if not bp.exists():
        return None
    budget = json.loads(bp.read_text())

    # Bracket: each playoff team appears once
    seen: set[tuple[str, int]] = set()
    bracket = []
    for p in budget.get("players", []):
        key = (p["team"], p["seed"])
        if key in seen:
            continue
        seen.add(key)
        bracket.append({"team_name": p["team"], "team_abbrev": p.get("team_abbrev"), "seed": p["seed"]})
    bracket.sort(key=lambda x: (x["seed"], x["team_name"]))

    pool: dict[int, list] = defaultdict(list)
    for p in budget["players"]:
        pool[p["seed"]].append({
            "slug": p["player_id"],
            "name": p["name"],
            "team": p["team"],
            "ppg": round(float(p["cost"]), 2),
        })
    for seed in pool:
        pool[seed].sort(key=lambda x: -x["ppg"])

    opponents = []
    if pp.exists():
        picks = json.loads(pp.read_text())
        for ent in picks.get("entrants", []):
            plist = []
            for s in range(1, 9):
                pick = (ent.get("picks") or {}).get(str(s))
                if not pick:
                    continue
                plist.append({
                    "slug": pick.get("player_id"),
                    "name": pick.get("name"),
                    "team": pick.get("team"),
                    "ppg": (pick.get("cost") or 0),
                    "seed": s,
                })
            opponents.append({"name": ent["name"], "players": plist})

    return {
        "bracket": bracket,
        "pool_by_seed": {str(s): pool[s] for s in sorted(pool.keys())},
        "opponents": opponents,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / "data"
    out_path = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "sim" / "allyears.js"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_data: dict[str, dict] = {}
    cur = current_year(data_dir)
    if cur:
        all_data["2026"] = cur
        print(f"  2026: {len(cur['bracket'])} teams, "
              f"{sum(len(v) for v in cur['pool_by_seed'].values())} players, "
              f"{len(cur['opponents'])} opponents")

    for y in [2022, 2023, 2024, 2025]:
        d = historical_year(y, data_dir)
        if d:
            all_data[str(y)] = d
            print(f"  {y}: {len(d['bracket'])} teams, "
                  f"{sum(len(v) for v in d['pool_by_seed'].values())} players, "
                  f"{len(d['opponents'])} opponents")

    body = "const ALL_YEARS_DATA = " + json.dumps(all_data, indent=2) + ";\n"
    out_path.write_text(body)
    print(f"\nWrote {out_path} ({out_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
