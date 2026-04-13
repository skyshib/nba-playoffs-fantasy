# NBA Playoffs Fantasy

Web scoreboard for a private NBA playoffs fantasy league. Static site + Python ingestion + ESPN API.

## Rules
- Each entrant drafts **8 NBA players, one per seed 1–8** (either conference per slot).
- **Budget** = current-season league-wide team PPG. Stored in `data/config.json`.
- **Player cost** = regular-season PPG. Floor: `max(cost, budget/16)`.
- Multiple entrants may pick the same player.
- **Round score** = average of a player's top 4 games that round (fewer games → average over what they played).
- **Seed multiplier**: `pts × (1 + seed/10)`. Applies 2025 and later; historical 2022–2024 have no multiplier.
- **Play-in excluded** (eligibility + stats).
- Winner = max total across R1, Conf Semis, Conf Finals, Finals × 8 seed slots.

## Layout
```
index.html              – scoreboard shell
css/style.css           – theme
js/
  app.js                – data loading, year routing, live refresh
  scoreboard.js         – scoring math + table render
  espn.js               – NBA ESPN live polling
  picks_ui.js           – in-browser roster builder
data/
  config.json           – { year, budget, years_available, ... }
  picks.json            – current-year entrants
  stats.json            – current-year per-game stats (cron-updated)
  budget.json           – eligible players w/ costs + seed
  2022..2025/           – historical snapshots (picks, stats, totals)
scripts/
  fetch_budget.py       – season-end league avg + per-player PPG + seed
  update_scores.py      – ESPN NBA playoff ingestion
  import_picks.py       – merge entrant JSON blobs into picks.json
  import_historical.py  – parse Excel scoreboards → per-year JSON
```

## Running locally
```
cd /Users/skylarshibayama/nba-fantasy
python3 -m http.server 8000
```

## Data pipeline
1. After play-in finalizes seeds: `python3 scripts/fetch_budget.py` → writes `data/budget.json` + sets `config.json` budget.
2. Entrants open the site, click "Enter Picks", build roster, submit → receive a JSON blob.
3. Admin runs `python3 scripts/import_picks.py picks_blob.json` to merge into `data/picks.json`.
4. During playoffs: `python3 scripts/update_scores.py --year 2026` (cron every 15 min) → updates `data/stats.json`.
5. Front-end polls ESPN every 60s for live overlay.

## Historical import
```
python3 scripts/import_historical.py --src ~/Downloads --year 2022
# ...repeat for 2023, 2024, 2025
```
Writes per-year `data/<year>/{picks.json, stats.json, totals.json}`.
