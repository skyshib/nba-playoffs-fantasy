#!/usr/bin/env python3
"""
One-off: update data/budget.json to reflect play-in bracket state.

  - W7 locked to POR (move POR 8 → 7)
  - W8 candidates: LAC, GSW, PHX (move PHX 7 → 8; add LAC, GSW rosters at 8)
  - E7 candidates: PHI, ORL (duplicate ORL at 7)
  - E8 candidates: CHA, PHI, ORL (duplicate PHI at 8; add CHA at 8)

Re-applies cost overrides from config.json at the end so Tatum stays
at his adjusted value.
"""

import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install: pip install requests", file=sys.stderr)
    sys.exit(1)


ESPN_NBA = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"

NEW_TEAMS = {
    "12": ("LA Clippers",           "LAC"),
    "9":  ("Golden State Warriors", "GS"),
    "30": ("Charlotte Hornets",     "CHA"),
}

SEASON = 2026


def slugify(name: str) -> str:
    import re
    s = name.lower().strip()
    s = re.sub(r"[\u2019']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def fetch_roster(team_id):
    r = requests.get(f"{ESPN_NBA}/teams/{team_id}/roster", timeout=20)
    r.raise_for_status()
    return r.json().get("athletes", [])


def fetch_player_ppg(athlete_id):
    url = f"{ESPN_CORE}/seasons/{SEASON}/types/2/athletes/{athlete_id}/statistics/0"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        for cat in r.json().get("splits", {}).get("categories", []):
            for stat in cat.get("stats", []):
                if stat.get("name") == "avgPoints":
                    return float(stat.get("value"))
    except Exception:
        return None
    return None


def build_team_players(team_id, team_name, team_abbrev, seed):
    print(f"  Fetching {team_name} roster...")
    roster = fetch_roster(team_id)
    out = []
    for a in roster:
        athlete_id = a.get("id")
        name = a.get("displayName") or a.get("fullName")
        if not athlete_id or not name:
            continue
        ppg = fetch_player_ppg(athlete_id)
        if ppg is None or ppg <= 0:
            continue
        out.append({
            "player_id": slugify(name),
            "espn_id": athlete_id,
            "name": name,
            "team": team_name,
            "team_abbrev": team_abbrev,
            "seed": seed,
            "cost": round(ppg, 2),
        })
        time.sleep(0.05)
    return out


def main():
    data_dir = Path(__file__).resolve().parent.parent / "data"
    budget_path = data_dir / "budget.json"
    budget = json.loads(budget_path.read_text())
    players = budget["players"]

    def by_team_seed(team, seed):
        return [p for p in players if p["team"] == team and p["seed"] == seed]

    # 1) Swap POR 8 -> 7 and PHX 7 -> 8
    for p in players:
        if p["team"] == "Portland Trail Blazers" and p["seed"] == 8:
            p["seed"] = 7
        elif p["team"] == "Phoenix Suns" and p["seed"] == 7:
            p["seed"] = 8

    # 2) Duplicate PHI (at 7) -> add at 8; duplicate ORL (at 8) -> add at 7
    dupes = []
    for team, existing_seed, extra_seed in [
        ("Philadelphia 76ers", 7, 8),
        ("Orlando Magic", 8, 7),
    ]:
        for p in by_team_seed(team, existing_seed):
            copy = dict(p)
            copy["seed"] = extra_seed
            dupes.append(copy)
    players.extend(dupes)

    # 3) Add LAC, GSW, CHA rosters (all at seed 8)
    new_additions = []
    for team_id, (team_name, abbrev) in NEW_TEAMS.items():
        existing = [p for p in players if p["team"] == team_name]
        if existing:
            print(f"  {team_name}: already in budget ({len(existing)} players) - skipping")
            continue
        new_additions.extend(build_team_players(team_id, team_name, abbrev, 8))
    players.extend(new_additions)

    # Re-apply cost overrides from config.json (Tatum, etc.)
    cfg = json.loads((data_dir / "config.json").read_text())
    overrides = {k: v for k, v in (cfg.get("cost_overrides") or {}).items() if not k.startswith("_")}
    for p in players:
        if p["player_id"] in overrides:
            if "cost_original" not in p:
                p["cost_original"] = p["cost"]
            p["cost"] = overrides[p["player_id"]]
            p["cost_overridden"] = True

    budget["n_playoff_teams"] = len({p["team"] for p in players})
    budget_path.write_text(json.dumps(budget, indent=2))

    # Summary
    by_seed = {}
    for p in players:
        by_seed.setdefault(p["seed"], {}).setdefault(p["team"], 0)
        by_seed[p["seed"]][p["team"]] += 1
    print(f"\nUpdated budget.json - {len(players)} entries")
    for s in sorted(by_seed):
        teams = ', '.join(f"{t} ({n})" for t, n in sorted(by_seed[s].items()))
        print(f"  Seed {s}: {teams}")


if __name__ == "__main__":
    main()
