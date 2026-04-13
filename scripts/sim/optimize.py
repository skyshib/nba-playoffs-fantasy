#!/usr/bin/env python3
"""
Phase 1 — Hindsight roster optimizer.

Given a past year's stats.json (which has every picked player's per-round PPG and
per-game points), find the roster that would have MAXIMIZED fantasy score under
the budget cap. This is the "what was the ceiling?" benchmark.

Method: per-seed DP / knapsack.
  State: dp[seed][cost_cents] -> (max_score, chosen_player_slug)
  For each seed 1..8, try each candidate, update state.
  O(8 × budget_cents × max_per_seed) — very fast (~3M ops).

Scoring matches scoreboard.js:
  round_score[rd] = (avg of top-4 games in rd) × (1 + seed/10 if year >= 2025)
  player_score    = Σ round_score over 4 rounds
  roster_score    = Σ player_score

Limitations:
  - The candidate pool is ONLY players who appeared in at least one entrant's
    roster that year (those are the players stats.json tracks). Uncommon
    sleepers from playoff teams are excluded. An extended optimizer would need
    full roster ingestion via ESPN box scores for every team.
  - Cost floor applied: max(player_cost, budget/16).

Usage:
  python3 scripts/sim/optimize.py --year 2024
  python3 scripts/sim/optimize.py --year 2024 --budget 115.6
  python3 scripts/sim/optimize.py --all-years
"""

import argparse
import json
from pathlib import Path


ROUNDS = ["R1", "CSF", "CF", "Finals"]
DEFAULT_BUDGET = 115.6
MULTIPLIER_START_YEAR = 2025


def score_player_for_seed(player: dict, seed: int, year: int) -> float:
    """Apply current scoring rules to a player's round_ppg data, for a given seed."""
    mult = (1 + seed / 10) if year >= MULTIPLIER_START_YEAR else 1.0
    total = 0.0
    for rd in ROUNDS:
        v = (player.get("round_ppg") or {}).get(rd)
        if isinstance(v, (int, float)):
            total += v * mult
    return total


def effective_cost(cost: float, budget: float) -> float:
    floor = budget / 16.0
    return max(cost, floor)


def optimize(year: int, data_dir: Path, budget: float, scale: int = 100):
    stats_path = data_dir / str(year) / "stats.json"
    stats = json.loads(stats_path.read_text())
    players = stats["players"]

    # Build candidates[seed] = [ (cost_cents, score, slug, player) ]
    by_seed: dict[int, list] = {s: [] for s in range(1, 9)}
    for slug, p in players.items():
        seed = p.get("seed")
        if not seed or seed < 1 or seed > 8:
            continue
        cost = p.get("cost")
        if cost is None:
            continue
        eff = effective_cost(cost, budget)
        score = score_player_for_seed(p, seed, year)
        if score <= 0:
            continue  # skip players with zero fantasy score (DNPs/eliminated R1 G1)
        by_seed[seed].append((round(eff * scale), score, slug, p))

    # Prune dominated candidates per seed (same or higher score at lower cost beats)
    for seed, arr in by_seed.items():
        arr.sort(key=lambda x: (x[0], -x[1]))  # by cost asc, then score desc
        pruned = []
        best_score = -1
        for row in arr:
            if row[1] > best_score:
                pruned.append(row)
                best_score = row[1]
        by_seed[seed] = pruned

    budget_cents = round(budget * scale)

    # DP: state dp[c] = (max_score, parent_cost, slug)
    NEG = float("-inf")
    dp = [NEG] * (budget_cents + 1)
    dp[0] = 0.0
    # back[seed][c] = (prev_c, slug) — how we reached dp[c] at seed level
    back: list[dict] = [{} for _ in range(9)]

    for seed in range(1, 9):
        new_dp = [NEG] * (budget_cents + 1)
        seed_back: dict = {}
        for c_prev, sc_prev in enumerate(dp):
            if sc_prev == NEG:
                continue
            for cost_c, score, slug, _p in by_seed[seed]:
                c_new = c_prev + cost_c
                if c_new > budget_cents:
                    continue
                total = sc_prev + score
                if total > new_dp[c_new]:
                    new_dp[c_new] = total
                    seed_back[c_new] = (c_prev, slug)
        dp = new_dp
        back[seed] = seed_back

    best_c = max(range(budget_cents + 1), key=lambda c: dp[c])
    best_score = dp[best_c]
    if best_score == NEG:
        return None

    # Reconstruct
    chosen: list[tuple[int, dict]] = []
    c = best_c
    for seed in range(8, 0, -1):
        prev_c, slug = back[seed][c]
        chosen.append((seed, players[slug] | {"player_id": slug}))
        c = prev_c
    chosen.reverse()

    total_cost = sum(effective_cost(p["cost"], budget) for _, p in chosen)
    raw_cost = sum(p["cost"] for _, p in chosen)

    return {
        "year": year,
        "budget": budget,
        "total_score": round(best_score, 3),
        "total_cost_effective": round(total_cost, 2),
        "total_cost_raw": round(raw_cost, 2),
        "roster": [
            {
                "seed": seed,
                "name": p["name"],
                "team": p.get("team"),
                "cost": p["cost"],
                "effective_cost": round(effective_cost(p["cost"], budget), 2),
                "score": round(score_player_for_seed(p, seed, year), 3),
                "round_ppg": p.get("round_ppg", {}),
            }
            for seed, p in chosen
        ],
    }


def compare_to_entrants(year: int, data_dir: Path, optimal_score: float):
    """Return best observed entrant total that year for context."""
    picks_path = data_dir / str(year) / "picks.json"
    stats_path = data_dir / str(year) / "stats.json"
    totals_path = data_dir / str(year) / "totals.json"
    if not totals_path.exists():
        return None
    totals = json.loads(totals_path.read_text())
    rows = totals.get("rows", [])
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("total", 0), reverse=True)
    return {
        "winner": rows[0]["name"],
        "winner_score": rows[0]["total"],
        "median_score": sorted(r["total"] for r in rows)[len(rows) // 2],
        "n_entrants": len(rows),
        "gap_to_optimal": round(optimal_score - rows[0]["total"], 2),
    }


def fmt_roster(result: dict) -> str:
    lines = [
        f"\n=== {result['year']} · Optimal roster (budget ${result['budget']:.2f}) ===",
        f"Total score: {result['total_score']}",
        f"Total effective cost: {result['total_cost_effective']:.2f} "
        f"(raw: {result['total_cost_raw']:.2f})",
        "",
        f"  {'Seed':<4}  {'Player':<30}  {'Team':<25}  {'Cost':>6}  {'Score':>7}",
        "  " + "-" * 80,
    ]
    for p in result["roster"]:
        lines.append(
            f"  {p['seed']:<4}  {p['name']:<30.30}  {(p['team'] or '—'):<25.25}  "
            f"{p['cost']:>6.1f}  {p['score']:>7.2f}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--all-years", action="store_true")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent.parent / "data"

    years = [2022, 2023, 2024, 2025] if args.all_years else [args.year]
    if not years or years == [None]:
        ap.error("specify --year YYYY or --all-years")

    results = []
    for y in years:
        r = optimize(y, data_dir, args.budget)
        if not r:
            print(f"No result for {y}")
            continue
        cmp = compare_to_entrants(y, data_dir, r["total_score"])
        r["entrant_comparison"] = cmp
        results.append(r)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    for r in results:
        print(fmt_roster(r))
        c = r.get("entrant_comparison")
        if c:
            print(
                f"\nActual winner: {c['winner']} ({c['winner_score']})  ·  "
                f"median: {c['median_score']}  ·  "
                f"gap to optimal: {c['gap_to_optimal']}"
            )


if __name__ == "__main__":
    main()
