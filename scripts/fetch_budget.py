#!/usr/bin/env python3
"""
Fetch the 2025-26 league-wide team PPG (budget cap) and per-player regular-season
PPG, plus seed mapping for each playoff team.

Strategy:
  1. Hit ESPN postseason scoreboard to discover the 16 playoff teams + seeds.
     (Run AFTER play-in to get the final 16; before that, the bracket isn't set.)
  2. Hit ESPN team roster + season averages to get each player's reg-season PPG.
  3. League PPG = sum(team_ppg) / N_teams across all 30 teams.

Writes:
  data/budget.json — { generated_at, season, league_ppg, players: [...] }
  Also updates data/config.json budget field if --update-config is passed.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install: pip install requests", file=sys.stderr)
    sys.exit(1)


ESPN_NBA = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def slugify(name: str) -> str:
    import re
    s = name.lower().strip()
    s = re.sub(r"[\u2019']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def fetch_postseason_teams(season: int):
    """Return { team_id: { name, abbrev, seed, conference } } from the playoff scoreboard."""
    url = f"{ESPN_NBA}/scoreboard?seasontype=3&limit=200&dates={season-1}1015-{season}0701"
    print(f"Fetching postseason scoreboard...")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    teams = {}
    for ev in r.json().get("events", []):
        notes = ev.get("competitions", [{}])[0].get("notes", [])
        if any("play-in" in (n.get("headline", "").lower()) for n in notes):
            continue
        for comp in ev.get("competitions", []):
            for t in comp.get("competitors", []):
                tid = t.get("id")
                if not tid:
                    continue
                seed = t.get("curatedRank", {}).get("current") or t.get("seed")
                try:
                    seed = int(seed) if seed else None
                except (ValueError, TypeError):
                    seed = None
                team_info = t.get("team", {})
                if seed and 1 <= seed <= 8:
                    teams[tid] = {
                        "team_id": tid,
                        "name": team_info.get("displayName", ""),
                        "abbrev": team_info.get("abbreviation", ""),
                        "seed": seed,
                    }
    return teams


def fetch_preliminary_teams(season: int):
    """
    Preliminary bracket: top 8 per conference by current standings.
    Assumes top seed wins all play-in games (i.e., 7 = 7th-place team, 8 = 8th-place).
    """
    url = f"https://cdn.espn.com/core/nba/standings?xhr=1&season={season}"
    print(f"Fetching preliminary standings (assuming top seed wins play-in)...")
    r = requests.get(url, timeout=30, headers={"Accept-Encoding": "identity"})
    r.raise_for_status()
    sb = r.json().get("content", {}).get("standings", {})
    teams = {}
    for grp in sb.get("groups", []):
        conf = grp.get("name", "")
        entries = grp.get("standings", {}).get("entries", [])
        # Standings are ordered best→worst within the conference; first 8 = playoff teams.
        for seed_idx, entry in enumerate(entries[:8], start=1):
            team_info = entry.get("team", {})
            tid = str(team_info.get("id"))
            teams[tid] = {
                "team_id": tid,
                "name": team_info.get("displayName", ""),
                "abbrev": team_info.get("abbreviation", ""),
                "seed": seed_idx,
                "conference": conf,
            }
    return teams


def fetch_all_teams():
    """Return list of all 30 NBA teams (id, abbrev, name)."""
    url = f"{ESPN_NBA}/teams?limit=50"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    out = []
    for grp in r.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = grp.get("team", {})
        out.append({"id": t.get("id"), "abbrev": t.get("abbreviation"), "name": t.get("displayName")})
    return out


def fetch_team_ppg(team_id: str, season: int) -> float | None:
    """Get team's points-per-game from team statistics endpoint."""
    url = f"{ESPN_CORE}/seasons/{season}/types/2/teams/{team_id}/statistics"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        for cat in r.json().get("splits", {}).get("categories", []):
            for stat in cat.get("stats", []):
                if stat.get("name") == "avgPoints":
                    return float(stat.get("value"))
    except Exception:
        return None
    return None


def fetch_team_roster(team_id: str):
    url = f"{ESPN_NBA}/teams/{team_id}/roster"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json().get("athletes", [])


def fetch_player_ppg(athlete_id: str, season: int) -> float | None:
    url = f"{ESPN_CORE}/seasons/{season}/types/2/athletes/{athlete_id}/statistics/0"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        for cat in r.json().get("splits", {}).get("categories", []):
            for stat in cat.get("stats", []):
                if stat.get("name") == "avgPoints":
                    return float(stat.get("value"))
    except Exception:
        return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026,
                    help="ESPN season year (use the END year — 2026 = 2025-26 season)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--update-config", action="store_true",
                    help="Update data/config.json budget value with computed league PPG.")
    ap.add_argument("--preliminary", action="store_true",
                    help="Build pool from current standings (assume top seed wins play-in). "
                         "Use this before play-in finalizes the bracket.")
    args = ap.parse_args()

    base = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "data"

    # League average PPG: avg of all 30 teams' avgPoints
    print("Fetching all 30 teams for league PPG...")
    all_teams = fetch_all_teams()
    ppgs = []
    for t in all_teams:
        ppg = fetch_team_ppg(t["id"], args.season)
        if ppg:
            ppgs.append(ppg)
            print(f"  {t['abbrev']}: {ppg:.2f}")
    league_ppg = round(sum(ppgs) / len(ppgs), 2) if ppgs else None
    print(f"\nLeague PPG: {league_ppg} (across {len(ppgs)} teams)")

    # Playoff teams + seeds
    if args.preliminary:
        teams = fetch_preliminary_teams(args.season)
    else:
        teams = fetch_postseason_teams(args.season)
    if not teams:
        print("WARN: no playoff teams found — try --preliminary or wait for play-in to set the bracket.")

    # Per-player PPG for each playoff team's roster
    players = []
    print(f"\nFetching {len(teams)} playoff teams' rosters...")
    for tid, info in teams.items():
        try:
            roster = fetch_team_roster(tid)
        except Exception as e:
            print(f"  {info['name']}: roster fetch failed: {e}")
            continue
        for a in roster:
            athlete_id = a.get("id")
            name = a.get("displayName") or a.get("fullName")
            if not athlete_id or not name:
                continue
            ppg = fetch_player_ppg(athlete_id, args.season)
            if ppg is None or ppg <= 0:
                continue
            players.append({
                "player_id": slugify(name),
                "espn_id": athlete_id,
                "name": name,
                "team": info["name"],
                "team_abbrev": info["abbrev"],
                "seed": info["seed"],
                "cost": round(ppg, 2),
            })
        n_added = sum(1 for p in players if p["team"] == info["name"])
        print(f"  {info['name']} (seed {info['seed']}): {n_added} players")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": args.season,
        "league_ppg": league_ppg,
        "n_playoff_teams": len(teams),
        "players": players,
    }
    (base / "budget.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote {base / 'budget.json'} — {len(players)} players, {len(teams)} teams")

    if args.update_config and league_ppg:
        cfg_path = base / "config.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["budget"] = league_ppg
        cfg["budget_note"] = f"Set by fetch_budget.py on {datetime.now(timezone.utc).date().isoformat()} from season {args.season}."
        cfg_path.write_text(json.dumps(cfg, indent=2))
        print(f"Updated config.json budget = {league_ppg}")


if __name__ == "__main__":
    main()
