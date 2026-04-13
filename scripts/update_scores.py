#!/usr/bin/env python3
"""
Pull NBA playoff box scores from ESPN and update data/stats.json.

Excludes play-in games. Classifies round from event notes:
  First Round → R1, Conference Semi → CSF, Conference Final → CF, NBA Finals → Finals
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install: pip install requests", file=sys.stderr)
    sys.exit(1)


ESPN_NBA = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\u2019']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def normalize_name(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[\u2019'\.]", "", s)
    s = re.sub(r"\s+(jr|sr|iii|ii|iv|v)$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def is_play_in(event) -> bool:
    notes = event.get("competitions", [{}])[0].get("notes", [])
    return any("play-in" in (n.get("headline", "").lower()) for n in notes)


def classify_round(event) -> str | None:
    notes = event.get("competitions", [{}])[0].get("notes", [])
    for n in notes:
        h = (n.get("headline", "") or "").lower()
        if "first round" in h:
            return "R1"
        if "conference semi" in h or "semifinal" in h:
            return "CSF"
        if "conference final" in h:
            return "CF"
        if "nba final" in h or h == "finals":
            return "Finals"
    return None


def fetch_postseason(season: int):
    url = f"{ESPN_NBA}/scoreboard?seasontype=3&limit=200&dates={season-1}1015-{season}0701"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json().get("events", [])


def fetch_summary(event_id: str):
    r = requests.get(f"{ESPN_NBA}/summary?event={event_id}", timeout=30)
    r.raise_for_status()
    return r.json()


def load_pick_mapping(picks_path: Path):
    if not picks_path.exists():
        return {}
    picks = json.loads(picks_path.read_text())
    out = {}
    for ent in picks.get("entrants", []):
        for seed, pick in (ent.get("picks") or {}).items():
            out[normalize_name(pick["name"])] = {
                "slug": pick["player_id"],
                "name": pick["name"],
                "team": pick.get("team", ""),
                "seed": pick.get("seed") or int(seed),
            }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--all-players", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / "data"
    pick_map = load_pick_mapping(data_dir / "picks.json")
    print(f"Tracking {len(pick_map)} picked players")

    events = fetch_postseason(args.year)
    print(f"{len(events)} postseason events")

    player_stats: dict[str, dict] = {}
    active_games = []
    live_games = []
    eliminated_team_ids = set()

    for ev in events:
        if is_play_in(ev):
            continue
        rd = classify_round(ev)
        if not rd:
            continue
        comp = ev.get("competitions", [{}])[0]
        state = comp.get("status", {}).get("type", {}).get("state", "")
        if state == "pre":
            continue
        eid = ev.get("id")
        if state == "in":
            active_games.append(eid)
            live_games.append({
                "id": eid,
                "round": rd,
                "status": comp.get("status", {}).get("type", {}).get("shortDetail", ""),
                "teams": [{
                    "name": t.get("team", {}).get("displayName", ""),
                    "abbrev": t.get("team", {}).get("abbreviation", ""),
                    "score": t.get("score", "0"),
                    "winner": t.get("winner"),
                } for t in comp.get("competitors", [])],
            })
        if state == "post":
            for t in comp.get("competitors", []):
                # Eliminated when a series is over — but a single game lost ≠ eliminated.
                # Heuristic: rely on series records. ESPN puts series info in `series` field.
                pass  # handled below via series check

        # Series elimination check: 4 wins ends the series
        for s in comp.get("series", []) or []:
            for t in s.get("competitors", []) or []:
                wins = (t.get("record") or {}).get("summary") or t.get("wins")
                # Skip — accurate elimination tracking requires aggregating across the series.
                pass

        try:
            summary = fetch_summary(eid)
        except Exception as e:
            print(f"  WARN summary {eid}: {e}")
            continue

        box = summary.get("boxscore", {}) or {}
        team_names = [tb.get("team", {}).get("displayName", "") for tb in box.get("players", [])]
        for tb in box.get("players", []):
            team_name = tb.get("team", {}).get("displayName", "")
            opponent = next((x for x in team_names if x != team_name), "")
            for sg in tb.get("statistics", []) or []:
                labels = [l.lower() for l in sg.get("labels", [])]
                ipt = labels.index("pts") if "pts" in labels else -1
                for ad in sg.get("athletes", []) or []:
                    a = ad.get("athlete", {}) or {}
                    nm = a.get("displayName") or ""
                    if not nm:
                        continue
                    norm = normalize_name(nm)
                    mapped = pick_map.get(norm)
                    if not mapped and not args.all_players:
                        continue
                    row = ad.get("stats") or []
                    try:
                        pts = int(row[ipt]) if ipt >= 0 and ipt < len(row) else 0
                    except (ValueError, TypeError):
                        pts = 0
                    slug = mapped["slug"] if mapped else slugify(nm)
                    if slug not in player_stats:
                        player_stats[slug] = {
                            "name": mapped["name"] if mapped else nm,
                            "team": team_name,
                            "seed": mapped.get("seed") if mapped else None,
                            "eliminated": False,
                            "games": [],
                        }
                    player_stats[slug]["games"].append({
                        "round": rd,
                        "pts": pts,
                        "game_id": eid,
                        "opponent": opponent,
                    })

    # Series-level elimination: a team is out when their opponent wins 4 games of any series in the most recent round they appeared.
    # Build win counts per (round, team) from completed games.
    series_wins: dict[tuple[str, str], int] = {}
    series_teams_in_round: dict[str, set] = {}
    for ev in events:
        if is_play_in(ev):
            continue
        rd = classify_round(ev)
        if not rd:
            continue
        comp = ev.get("competitions", [{}])[0]
        state = comp.get("status", {}).get("type", {}).get("state", "")
        if state != "post":
            continue
        for t in comp.get("competitors", []):
            tname = t.get("team", {}).get("displayName", "")
            series_teams_in_round.setdefault(rd, set()).add(tname)
            if t.get("winner") is True:
                series_wins[(rd, tname)] = series_wins.get((rd, tname), 0) + 1
    # Team eliminated if opponent in same round has 4 wins
    eliminated_teams: set[str] = set()
    for rd, teams in series_teams_in_round.items():
        for t in teams:
            opps = teams - {t}
            if any(series_wins.get((rd, o), 0) >= 4 for o in opps):
                if series_wins.get((rd, t), 0) < 4:
                    eliminated_teams.add(t)

    for slug, p in player_stats.items():
        if p["team"] in eliminated_teams:
            p["eliminated"] = True

    out = {
        "year": args.year,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "players": player_stats,
        "active_games": active_games,
        "live_games": live_games,
    }
    (data_dir / "stats.json").write_text(json.dumps(out, indent=2))
    print(f"Wrote stats.json — {len(player_stats)} players, {len(active_games)} active")


if __name__ == "__main__":
    main()
