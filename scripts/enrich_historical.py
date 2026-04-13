#!/usr/bin/env python3
"""
Enrich data/<year>/stats.json with team + opponent + game_id metadata pulled
from ESPN's historical postseason scoreboard.

For each year:
  1. Fetch all postseason events (seasontype=3).
  2. Classify each event's round (R1 / CSF / CF / Finals) and series game number
     from the headline (e.g. "West 1st Round - Game 3").
  3. For each event, fetch the box score to find which players appeared.
  4. Build:
       team_seed[year][team_name]  -> seed (for cross-check)
       series[(team, round)]       -> { opponent, game_ids[1..7] }
       player_team[player_slug]    -> team they appeared for most often
  5. Patch stats.json:
       players[slug].team   = current team
       players[slug].games[i].team, opponent, game_id
       (game_num index aligns with series chronological order)

Usage:
  python3 scripts/enrich_historical.py            # all years 2022-2025
  python3 scripts/enrich_historical.py --year 2024
"""

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

try:
    import requests
except ImportError:
    print("Install: pip install requests", file=sys.stderr)
    sys.exit(1)


ESPN_NBA = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

# Date ranges per playoffs year (covers full postseason including Finals)
YEAR_DATE_RANGES = {
    2022: ("20220416", "20220622"),
    2023: ("20230415", "20230615"),
    2024: ("20240420", "20240620"),
    2025: ("20250419", "20250622"),
}


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\u2019']", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def classify_round_and_game(headline: str) -> tuple[str | None, int | None]:
    """Headline examples seen across 2022-2025:
       'West 1st Round - Game 3'
       'East Semifinals - Game 6'        (Conf Semis)
       'West Finals - Game 1'            (Conf Finals)
       'NBA Finals - Game 4'
    """
    h = headline.lower()
    rd = None
    if "nba finals" in h:
        rd = "Finals"
    elif "east finals" in h or "west finals" in h:
        rd = "CF"
    elif "conf finals" in h or "conference final" in h:
        rd = "CF"
    elif "semifinal" in h or "semi-final" in h or "conf semi" in h:
        rd = "CSF"
    elif "1st round" in h or "first round" in h:
        rd = "R1"
    m = re.search(r"game\s+(\d+)", h)
    game_num = int(m.group(1)) if m else None
    return rd, game_num


def fetch_events(season_year: int):
    start, end = YEAR_DATE_RANGES[season_year]
    url = f"{ESPN_NBA}/scoreboard?seasontype=3&limit=200&dates={start}-{end}"
    r = requests.get(url, timeout=30, headers={"Accept-Encoding": "identity"})
    r.raise_for_status()
    return r.json().get("events", [])


def fetch_summary(event_id: str):
    url = f"{ESPN_NBA}/summary?event={event_id}"
    r = requests.get(url, timeout=30, headers={"Accept-Encoding": "identity"})
    r.raise_for_status()
    return r.json()


# Excel name → ESPN canonical (normalized form). Covers typos / spelling
# variants in the historical scoreboards so we can still match the box scores.
NAME_FIXUPS = {
    "domantis sabonis": "domantas sabonis",
    "terrence mann": "terance mann",
    "jonathon kuminga": "jonathan kuminga",
    "jared vanderbilt": "jarred vanderbilt",
    "donte divicenzo": "donte divincenzo",
    "mo wagner": "moritz wagner",
    "precious achuiuwa": "precious achiuwa",
    "cam payne": "cameron payne",
    "nicolas claxton": "nic claxton",
}


def normalize_name(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[\u2019'\.]", "", s)
    s = re.sub(r"\s+(jr|sr|iii|ii|iv|v)$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return NAME_FIXUPS.get(s, s)


def enrich_year(year: int, data_dir: Path):
    print(f"\n=== {year} ===")
    stats_path = data_dir / str(year) / "stats.json"
    if not stats_path.exists():
        print(f"  SKIP: {stats_path} missing")
        return
    stats = json.loads(stats_path.read_text())

    events = fetch_events(year)
    print(f"  {len(events)} postseason events")

    # event_id -> { round, series_game_num, teams: [name, name], date }
    event_meta: dict[str, dict] = {}
    # (team_name, round) -> list of (date, event_id, opponent)
    series_games: dict[tuple[str, str], list] = defaultdict(list)

    for ev in events:
        comp = ev.get("competitions", [{}])[0]
        notes = comp.get("notes", [])
        if not notes:
            continue
        headline = notes[0].get("headline", "")
        rd, game_num = classify_round_and_game(headline)
        if not rd:
            continue
        teams = [t.get("team", {}).get("displayName", "") for t in comp.get("competitors", [])]
        if len(teams) != 2:
            continue
        date = ev.get("date") or comp.get("date") or ""
        eid = ev.get("id")
        event_meta[eid] = {
            "round": rd,
            "headline": headline,
            "game_num": game_num,
            "teams": teams,
            "date": date,
        }
        series_games[(teams[0], rd)].append((date, eid, teams[1]))
        series_games[(teams[1], rd)].append((date, eid, teams[0]))

    # Sort each series chronologically and assign a 1-indexed game number per team
    series_lookup: dict[tuple[str, str], list] = {}
    for key, lst in series_games.items():
        lst.sort(key=lambda x: x[0])
        # Build a list of (eid, opponent) ordered by series game number
        series_lookup[key] = [(eid, opp) for (_, eid, opp) in lst]

    # Walk every box score to determine each player's team for the year
    print(f"  Fetching {len(event_meta)} box scores...")
    player_team_counts: dict[str, Counter] = defaultdict(Counter)
    # also: player slug -> { event_id -> { team, pts } } so we can fix DNP/zero alignment if needed
    player_appearances: dict[str, dict[str, dict]] = defaultdict(dict)

    target_slugs = set(stats.get("players", {}).keys())
    target_norms = {normalize_name(p["name"]): slug
                    for slug, p in stats.get("players", {}).items()}

    for i, eid in enumerate(event_meta, start=1):
        try:
            summary = fetch_summary(eid)
        except Exception as e:
            print(f"    WARN summary {eid}: {e}")
            continue
        box = summary.get("boxscore", {}) or {}
        for tb in box.get("players", []):
            team_name = tb.get("team", {}).get("displayName", "")
            for sg in tb.get("statistics", []) or []:
                labels = [l.lower() for l in sg.get("labels", [])]
                ipt = labels.index("pts") if "pts" in labels else -1
                for ad in sg.get("athletes", []) or []:
                    a = ad.get("athlete", {}) or {}
                    nm = a.get("displayName") or ""
                    if not nm:
                        continue
                    norm = normalize_name(nm)
                    slug = target_norms.get(norm)
                    if not slug:
                        # also allow slug-direct match (collisions)
                        sl = slugify(nm)
                        if sl in target_slugs:
                            slug = sl
                    if not slug:
                        continue
                    player_team_counts[slug][team_name] += 1
                    row = ad.get("stats") or []
                    try:
                        pts = int(row[ipt]) if 0 <= ipt < len(row) else 0
                    except (ValueError, TypeError):
                        pts = 0
                    player_appearances[slug][eid] = {"team": team_name, "pts": pts}
        if i % 20 == 0:
            print(f"    {i}/{len(event_meta)}")
        time.sleep(0.05)

    # Patch stats.json: assign team + per-game opponent + game_id
    n_team_set = 0
    n_games_enriched = 0
    n_unmatched = 0

    for slug, p in stats.get("players", {}).items():
        counts = player_team_counts.get(slug)
        team = counts.most_common(1)[0][0] if counts else None
        if team:
            p["team"] = team
            n_team_set += 1
        else:
            # No appearances → keep going but we can't enrich this player's games
            continue

        for g in p.get("games", []):
            rd = g.get("round")
            game_num = g.get("game_num")
            series = series_lookup.get((team, rd), [])
            if not series:
                n_unmatched += 1
                continue
            idx = (game_num - 1) if game_num else None
            if idx is None or idx >= len(series):
                n_unmatched += 1
                continue
            eid, opp = series[idx]
            g["team"] = team
            g["opponent"] = opp
            g["game_id"] = eid
            n_games_enriched += 1

    stats_path.write_text(json.dumps(stats, indent=2))
    print(f"  Set team for {n_team_set} players")
    print(f"  Enriched {n_games_enriched} games · {n_unmatched} unmatched")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--year", type=int, action="append")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / "data"
    years = args.year or [2022, 2023, 2024, 2025]
    for y in years:
        enrich_year(y, data_dir)


if __name__ == "__main__":
    main()
