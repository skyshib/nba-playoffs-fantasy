#!/usr/bin/env python3
"""
Phase 2 — Monte Carlo simulator (historical backtest).

Given a past year's stats.json, bootstrap-resample each player's per-game
points (within their actual rounds) to generate many alternate realities of
that playoffs. Useful for asking:

  - How much variance does a given roster have under the scoring rules?
  - What's the 5th/95th percentile score?
  - Which rosters maximize EXPECTED score under uncertainty (vs. the hindsight
    ceiling from optimize.py)?

Two modes:
  --score-roster  Pass a roster JSON (or --actual ENTRANT_NAME); print the
                  distribution of outcomes.
  --optimize-ev   Find the roster that maximizes expected fantasy score
                  under bootstrap uncertainty, subject to budget.

Resampling model:
  For each player + round they actually appeared in, sample the same number
  of games with replacement from their actual per-game points in that round.
  The round PPG is then the average of the top-4 sampled games (same rule as
  live scoring). Players who didn't appear in a round contribute 0 for that
  round.

Limitations:
  - Assumes team advancement matches history (not modeled). This is a
    "scoring variance" sim, not a "bracket variance" sim.
  - Candidate pool limited to players in stats.json (same as optimize.py).

Usage:
  # Distribution for the 2024 actual winner
  python3 scripts/sim/montecarlo.py --year 2024 --actual "McKenna Hayes" -N 5000

  # EV-optimal roster for 2025
  python3 scripts/sim/montecarlo.py --year 2025 --optimize-ev -N 2000
"""

import argparse
import json
import random
from pathlib import Path
from statistics import mean, stdev

ROUNDS = ["R1", "CSF", "CF", "Finals"]
DEFAULT_BUDGET = 115.6
MULTIPLIER_START_YEAR = 2025


def mult(seed: int, year: int) -> float:
    return (1 + seed / 10) if year >= MULTIPLIER_START_YEAR else 1.0


def effective_cost(cost: float, budget: float) -> float:
    return max(cost, budget / 16.0) if cost is not None else 0.0


def build_games_by_round(player: dict) -> dict[str, list[float]]:
    by_rd: dict[str, list[float]] = {}
    for g in player.get("games", []):
        by_rd.setdefault(g["round"], []).append(g.get("pts", 0.0))
    return by_rd


def top4_avg(points: list[float]) -> float:
    if not points:
        return 0.0
    top = sorted(points, reverse=True)[:4]
    return sum(top) / len(top)


def sample_player_score(games_by_round: dict, seed: int, year: int,
                        rng: random.Random) -> float:
    m = mult(seed, year)
    total = 0.0
    for rd, games in games_by_round.items():
        if not games:
            continue
        sampled = [rng.choice(games) for _ in games]
        total += top4_avg(sampled) * m
    return total


def score_roster_simulated(roster: list[dict], players_data: dict, year: int,
                           rng: random.Random) -> float:
    total = 0.0
    for pick in roster:
        p = players_data.get(pick["player_id"])
        if not p:
            continue
        games_by_round = build_games_by_round(p)
        total += sample_player_score(games_by_round, pick["seed"], year, rng)
    return total


def score_roster_deterministic(roster: list[dict], players_data: dict, year: int) -> float:
    """Exact score the roster would have gotten (no sampling)."""
    total = 0.0
    for pick in roster:
        p = players_data.get(pick["player_id"])
        if not p:
            continue
        m = mult(pick["seed"], year)
        for rd in ROUNDS:
            v = (p.get("round_ppg") or {}).get(rd)
            if isinstance(v, (int, float)):
                total += v * m
    return total


def load_year(year: int, data_dir: Path):
    stats = json.loads((data_dir / str(year) / "stats.json").read_text())
    picks = json.loads((data_dir / str(year) / "picks.json").read_text())
    return stats, picks


def get_actual_roster(picks: dict, entrant_name: str) -> list[dict] | None:
    for ent in picks.get("entrants", []):
        if ent["name"].lower() == entrant_name.lower():
            return [
                {"seed": int(s), "player_id": p["player_id"], "name": p["name"],
                 "team": p.get("team"), "cost": p.get("cost")}
                for s, p in sorted(ent["picks"].items(), key=lambda kv: int(kv[0]))
            ]
    return None


def simulate_roster(roster: list[dict], players_data: dict, year: int,
                    n_sims: int, seed: int = 0) -> dict:
    rng = random.Random(seed)
    # Pre-build per-pick structure for speed
    per_pick = []
    for pick in roster:
        p = players_data.get(pick["player_id"])
        if not p:
            continue
        per_pick.append((pick["seed"], build_games_by_round(p)))

    scores = []
    for _ in range(n_sims):
        total = 0.0
        for s, games_by_rd in per_pick:
            m = mult(s, year)
            for rd, games in games_by_rd.items():
                if games:
                    sampled = [rng.choice(games) for _ in games]
                    total += top4_avg(sampled) * m
        scores.append(total)
    scores.sort()
    def pct(p): return scores[int(p * (len(scores) - 1))]
    det = score_roster_deterministic(roster, players_data, year)
    return {
        "n_sims": n_sims,
        "actual_deterministic": round(det, 2),
        "mean": round(mean(scores), 2),
        "stdev": round(stdev(scores) if len(scores) > 1 else 0, 2),
        "p05": round(pct(0.05), 2),
        "p25": round(pct(0.25), 2),
        "median": round(pct(0.50), 2),
        "p75": round(pct(0.75), 2),
        "p95": round(pct(0.95), 2),
        "min": round(scores[0], 2),
        "max": round(scores[-1], 2),
    }


def precompute_ev_per_player(players_data: dict, year: int, n_sims: int,
                             rng_base: int) -> dict[tuple[str, int], float]:
    """
    For every (player, seed) pair, estimate expected score via bootstrap
    sampling. Stored with key (slug, seed). For the optimize-ev mode we then
    just pick the highest-EV candidate per seed under budget.
    """
    result: dict[tuple[str, int], float] = {}
    for slug, p in players_data.items():
        seed = p.get("seed")
        if not seed or not (1 <= seed <= 8):
            continue
        games_by_rd = build_games_by_round(p)
        if not games_by_rd:
            continue
        rng = random.Random(rng_base ^ hash(slug) & 0xFFFFFFFF)
        total = 0.0
        for _ in range(n_sims):
            total += sample_player_score(games_by_rd, seed, year, rng)
        result[(slug, seed)] = total / n_sims
    return result


def optimize_ev_roster(players_data: dict, year: int, budget: float,
                       n_sims: int, scale: int = 100) -> dict:
    """DP knapsack on expected value (same shape as optimize.py but with EVs)."""
    ev_map = precompute_ev_per_player(players_data, year, n_sims, rng_base=42)

    by_seed: dict[int, list] = {s: [] for s in range(1, 9)}
    for (slug, seed), ev in ev_map.items():
        p = players_data[slug]
        cost = p.get("cost")
        if cost is None or ev <= 0:
            continue
        eff = effective_cost(cost, budget)
        by_seed[seed].append((round(eff * scale), ev, slug))

    # Prune dominated
    for seed, arr in by_seed.items():
        arr.sort(key=lambda x: (x[0], -x[1]))
        pruned, best = [], -1.0
        for row in arr:
            if row[1] > best:
                pruned.append(row)
                best = row[1]
        by_seed[seed] = pruned

    budget_cents = round(budget * scale)
    NEG = float("-inf")
    dp = [NEG] * (budget_cents + 1)
    dp[0] = 0.0
    back = [{} for _ in range(9)]

    for seed in range(1, 9):
        new_dp = [NEG] * (budget_cents + 1)
        seed_back = {}
        for c_prev, sc_prev in enumerate(dp):
            if sc_prev == NEG:
                continue
            for cost_c, ev, slug in by_seed[seed]:
                c_new = c_prev + cost_c
                if c_new > budget_cents:
                    continue
                total = sc_prev + ev
                if total > new_dp[c_new]:
                    new_dp[c_new] = total
                    seed_back[c_new] = (c_prev, slug)
        dp = new_dp
        back[seed] = seed_back

    best_c = max(range(budget_cents + 1), key=lambda c: dp[c])
    if dp[best_c] == NEG:
        return None

    # Reconstruct
    chosen = []
    c = best_c
    for seed in range(8, 0, -1):
        prev_c, slug = back[seed][c]
        chosen.append({
            "seed": seed, "player_id": slug,
            "name": players_data[slug]["name"],
            "team": players_data[slug].get("team"),
            "cost": players_data[slug]["cost"],
        })
        c = prev_c
    chosen.reverse()
    return {"expected_total": round(dp[best_c], 2), "roster": chosen}


def fmt_distribution(d: dict, title: str) -> str:
    return (
        f"\n{title}\n"
        f"  Deterministic (actual historical): {d['actual_deterministic']}\n"
        f"  Mean (bootstrap):                  {d['mean']}  ± {d['stdev']}\n"
        f"  5 / 25 / 50 / 75 / 95:             {d['p05']} / {d['p25']} / {d['median']} / {d['p75']} / {d['p95']}\n"
        f"  Min / Max:                         {d['min']} / {d['max']}\n"
        f"  N sims:                            {d['n_sims']}"
    )


def fmt_roster_brief(roster: list[dict]) -> str:
    lines = [f"  {'Seed':<4}  {'Player':<28}  {'Team':<24}  {'Cost':>6}"]
    lines.append("  " + "-" * 70)
    for pick in roster:
        lines.append(
            f"  {pick['seed']:<4}  {pick['name']:<28.28}  {(pick.get('team') or '—'):<24.24}  "
            f"{(pick.get('cost') or 0):>6.1f}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("-N", "--n-sims", type=int, default=2000)
    ap.add_argument("--actual", help="Simulate the entrant with this name")
    ap.add_argument("--roster-json", help="Path to a roster JSON file to simulate")
    ap.add_argument("--optimize-ev", action="store_true",
                    help="Find the EV-maximizing roster under budget")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent.parent / "data"
    stats, picks = load_year(args.year, data_dir)
    players_data = stats["players"]

    if args.optimize_ev:
        r = optimize_ev_roster(players_data, args.year, args.budget, args.n_sims)
        if not r:
            print("No feasible roster")
            return
        print(f"\n=== {args.year} · EV-optimal roster ({args.n_sims} sims, budget ${args.budget:.2f}) ===")
        print(f"Expected total score: {r['expected_total']}")
        print(fmt_roster_brief(r["roster"]))
        # Follow-up: sim the chosen roster to show distribution
        sim = simulate_roster(r["roster"], players_data, args.year, args.n_sims, args.seed)
        print(fmt_distribution(sim, "Distribution for EV-optimal roster:"))
        return

    roster = None
    title = ""
    if args.actual:
        roster = get_actual_roster(picks, args.actual)
        if not roster:
            print(f"Entrant '{args.actual}' not found in {args.year}")
            return
        title = f"=== {args.year} · {args.actual} ==="
    elif args.roster_json:
        roster = json.loads(Path(args.roster_json).read_text())
        title = f"=== {args.year} · roster from {args.roster_json} ==="
    else:
        ap.error("one of --actual / --roster-json / --optimize-ev required")

    print(f"\n{title}")
    print(fmt_roster_brief(roster))
    sim = simulate_roster(roster, players_data, args.year, args.n_sims, args.seed)
    print(fmt_distribution(sim, "Simulated distribution:"))


if __name__ == "__main__":
    main()
