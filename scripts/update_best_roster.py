#!/usr/bin/env python3
"""
Recompute the ⭐ Best Possible Roster (So Far) from ALL playoff players
and inject/update it in data/picks.json.

Run after update_scores.py --all-players to have the full player pool.
"""

import json
from pathlib import Path

ROUNDS = ['R1', 'CSF', 'CF', 'Finals']


def main():
    data_dir = Path(__file__).resolve().parent.parent / 'data'
    stats = json.loads((data_dir / 'stats.json').read_text())
    budget = json.loads((data_dir / 'budget.json').read_text())
    cfg = json.loads((data_dir / 'config.json').read_text())

    BUDGET = cfg['budget']
    FLOOR = BUDGET / 16

    def eff_cost(c):
        return max(c, FLOOR)

    def score_player(p, seed):
        m = 1 + seed / 10
        by_rd = {}
        for g in p.get('games', []):
            by_rd.setdefault(g['round'], []).append(g.get('pts', 0))
        t = 0
        for rd in ROUNDS:
            pts = by_rd.get(rd, [])
            if pts:
                top = sorted(pts, reverse=True)[:4]
                t += (sum(top) / len(top)) * m
        return t

    player_seeds = {p['player_id']: p['seed'] for p in budget['players']}
    player_costs = {p['player_id']: p['cost'] for p in budget['players']}

    candidates = {}
    scale = 100
    for slug, p in stats['players'].items():
        seed = player_seeds.get(slug) or p.get('seed')
        if not seed or not (1 <= seed <= 8):
            continue
        cost = player_costs.get(slug) or p.get('cost')
        if cost is None:
            continue
        score = score_player(p, seed)
        if score <= 0:
            continue
        candidates.setdefault(seed, []).append(
            (round(eff_cost(cost) * scale), score, slug, p, cost))

    for seed in candidates:
        arr = sorted(candidates[seed], key=lambda x: (x[0], -x[1]))
        pruned, best = [], -1
        for row in arr:
            if row[1] > best:
                pruned.append(row)
                best = row[1]
        candidates[seed] = pruned

    budget_cents = round(BUDGET * scale)
    NEG = float('-inf')
    dp = [NEG] * (budget_cents + 1)
    dp[0] = 0.0
    back = [None] + [{} for _ in range(8)]

    for seed in range(1, 9):
        ndp = [NEG] * (budget_cents + 1)
        sb = {}
        for c, sc in enumerate(dp):
            if sc == NEG:
                continue
            for cc, score, slug, _p, _raw in candidates.get(seed, []):
                nc = c + cc
                if nc > budget_cents:
                    continue
                t = sc + score
                if t > ndp[nc]:
                    ndp[nc] = t
                    sb[nc] = (c, slug)
        dp = ndp
        back[seed] = sb

    best_c = max(range(budget_cents + 1), key=lambda c: dp[c])
    if dp[best_c] == NEG:
        print('No feasible roster found')
        return

    chosen = []
    c = best_c
    for seed in range(8, 0, -1):
        pc, slug = back[seed][c]
        p = stats['players'][slug]
        cost = player_costs.get(slug, 0)
        chosen.append({
            'seed': seed, 'player_id': slug,
            'name': p['name'], 'team': p.get('team', ''),
            'cost': cost, 'score': score_player(p, seed),
        })
        c = pc
    chosen.reverse()

    # Update picks.json
    picks_path = data_dir / 'picks.json'
    picks = json.loads(picks_path.read_text())
    picks['entrants'] = [e for e in picks['entrants']
                         if not e.get('_synthetic')]
    synth = {
        'name': '\u2B50 Best Possible Roster (So Far)',
        '_synthetic': True,
        'picks': {
            str(p['seed']): {
                'player_id': p['player_id'], 'name': p['name'],
                'team': p['team'], 'seed': p['seed'], 'cost': p['cost'],
            } for p in chosen
        }
    }
    picks['entrants'].insert(0, synth)
    picks_path.write_text(json.dumps(picks, indent=2))

    tc = sum(eff_cost(p['cost']) for p in chosen)
    print(f'Best Possible: {dp[best_c]:.2f} pts (${tc:.2f})')
    for p in chosen:
        print(f'  Seed {p["seed"]}: {p["name"]:25s} ({p["team"]}) '
              f'${p["cost"]:.1f} -> {p["score"]:.2f}')


if __name__ == '__main__':
    main()
