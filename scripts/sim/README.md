# Simulations

Two research tools for studying roster strategy.

## `optimize.py` — Hindsight optimizer (Phase 1)

Given a past year's fully-played postseason, find the roster that would have
maximized fantasy score under the budget cap.

```
python3 scripts/sim/optimize.py --year 2024
python3 scripts/sim/optimize.py --all-years
python3 scripts/sim/optimize.py --year 2025 --json > /tmp/2025opt.json
```

Uses a seed-wise DP knapsack (≤budget × 100 cents × 8 seeds × ~35 candidates → a
few million ops, runs instantly). Prunes dominated candidates first.

Output includes the gap to the actual winner, so you can see how much upside
was left in any given year.

## `montecarlo.py` — Bootstrap Monte Carlo (Phase 2)

Given a roster and a past year, bootstrap-resample each player's per-game
points within their actual round appearances to generate alternate scoring
realities. Shows mean, stdev, and p5/p25/p50/p75/p95 of the score distribution.

```
# Distribution for an actual entrant
python3 scripts/sim/montecarlo.py --year 2025 --actual "Austin Yamada" -N 5000

# Custom roster from JSON
python3 scripts/sim/montecarlo.py --year 2024 --roster-json /tmp/my_roster.json

# EV-maximizing roster under the budget
python3 scripts/sim/montecarlo.py --year 2025 --optimize-ev -N 2000
```

**What this is**: scoring-variance sim. Team advancement is held fixed to
history (i.e. we know who made the Finals). This answers "given the same
bracket, how much can scoring luck move your total?"

**What this isn't** (yet): forward-looking bracket simulation. Adding that
requires modeling series win probabilities + per-game points distributions —
possible future work; would need regular-season game logs for the current
season.

## Roster JSON format (for `--roster-json`)

```json
[
  {"seed": 1, "player_id": "shai-gilgeous-alexander", "name": "...", "cost": 32.7},
  {"seed": 2, "player_id": "jaylen-brown", "name": "...", "cost": 22.2},
  ...
]
```
