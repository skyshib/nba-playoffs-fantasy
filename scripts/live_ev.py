#!/usr/bin/env python3
"""
Live EV calculator — runs the Monte Carlo sim using ACTUAL playoff scores
for completed rounds + blended PPG (playoff-weighted) for future games.

For each player:
  blended_ppg = (playoff_games * playoff_ppg + reg_weight * reg_ppg) / (playoff_games + reg_weight)

Where reg_weight starts at 3 and decays as more playoff games are played,
so a player with 4+ playoff games is basically ignoring reg-season PPG.

Usage:
  python3 scripts/live_ev.py                     # all entrants
  python3 scripts/live_ev.py --name "Skylar"     # specific entrant
  python3 scripts/live_ev.py --exclude "Skylar"  # everyone except
"""

import argparse
import json
import random
from pathlib import Path


ROUNDS = ['R1', 'CSF', 'CF', 'Finals']
# Series length determined by per-game simulation (no fixed distribution).
REG_WEIGHT = 1  # 50/50 blend: playoff PPG and reg-season PPG weighted equally per game

ELOS = {
    "Oklahoma City Thunder": 1811, "Detroit Pistons": 1735,
    "San Antonio Spurs": 1735, "Boston Celtics": 1732,
    "New York Knicks": 1679, "Houston Rockets": 1651,
    "Denver Nuggets": 1646, "Cleveland Cavaliers": 1615,
    "Minnesota Timberwolves": 1587, "Toronto Raptors": 1581,
    "Atlanta Hawks": 1562, "Los Angeles Lakers": 1542,
    "Phoenix Suns": 1539, "Orlando Magic": 1517,
    "Portland Trail Blazers": 1489, "Philadelphia 76ers": 1489,
}
EAST = {'Detroit Pistons','Boston Celtics','New York Knicks','Cleveland Cavaliers',
        'Toronto Raptors','Atlanta Hawks','Philadelphia 76ers','Orlando Magic'}

PAYOUTS_16_29 = {1: 0.50, 2: 0.25, 3: 0.13, 4: 0.07, 5: 0.05}


def wp(a, b):
    return 1 / (1 + 10 ** ((ELOS.get(b, 1500) - ELOS.get(a, 1500)) / 400))


def play_series_games(t1, t2, rng):
    """Simulate a best-of-7 series game by game. Returns (winner, total_games)."""
    w1, w2 = 0, 0
    while w1 < 4 and w2 < 4:
        if rng.random() < wp(t1, t2):
            w1 += 1
        else:
            w2 += 1
    return (t1 if w1 >= 4 else t2), w1 + w2


def cv(ppg):
    if ppg >= 30: return 0.25
    if ppg >= 20: return 0.35
    if ppg >= 10: return 0.45
    return 0.60


def mult(seed):
    return 1 + seed / 10


def blended_ppg(reg_ppg, playoff_games):
    """Blend reg-season and playoff PPG. As playoff sample grows, it dominates."""
    if not playoff_games:
        return reg_ppg, reg_ppg
    playoff_ppg = sum(g['pts'] for g in playoff_games) / len(playoff_games)
    n_playoff = len(playoff_games)
    blended = (n_playoff * playoff_ppg + REG_WEIGHT * reg_ppg) / (n_playoff + REG_WEIGHT)
    return blended, playoff_ppg


def build_bracket(budget):
    bracket = {}
    for p in budget['players']:
        conf = 'East' if p['team'] in EAST else 'West'
        key = (conf, p['seed'])
        if key not in bracket:
            bracket[key] = p['team']
    return bracket


def get_series_state(stats):
    """Return {team_name: {round: wins}} from completed games."""
    ts = stats.get('team_series', {})
    state = {}
    for team, info in ts.items():
        state[team] = {'round': info.get('round'), 'wins': info.get('wins', 0),
                       'losses': info.get('losses', 0)}
    return state


def sim_bracket(rng, bracket, series_state):
    """Simulate bracket, respecting already-played series results."""
    games = {t: {'R1': 0, 'CSF': 0, 'CF': 0, 'Finals': 0}
             for t in set(bracket.values())}

    def play(t1, t2, rd):
        if not t1 or not t2:
            return t1 or t2
        # Check if series is already decided
        s1 = series_state.get(t1, {})
        s2 = series_state.get(t2, {})
        if s1.get('round') == rd and s2.get('round') == rd:
            if s1.get('wins', 0) >= 4:
                # t1 already won this series
                total_g = s1['wins'] + s1.get('losses', 0)
                if t1 in games: games[t1][rd] = total_g
                if t2 in games: games[t2][rd] = total_g
                return t1
            if s2.get('wins', 0) >= 4:
                total_g = s2['wins'] + s2.get('losses', 0)
                if t1 in games: games[t1][rd] = total_g
                if t2 in games: games[t2][rd] = total_g
                return t2
            # Series in progress — simulate remaining games
            w1, w2 = s1.get('wins', 0), s2.get('wins', 0)
            played = w1 + w2
            while w1 < 4 and w2 < 4:
                if rng.random() < wp(t1, t2):
                    w1 += 1
                else:
                    w2 += 1
            total_g = w1 + w2
            if t1 in games: games[t1][rd] = total_g
            if t2 in games: games[t2][rd] = total_g
            return t1 if w1 >= 4 else t2

        # Series hasn't started — simulate game by game
        winner, total_g = play_series_games(t1, t2, rng)
        if t1 in games: games[t1][rd] = total_g
        if t2 in games: games[t2][rd] = total_g
        return winner

    def run_conf(conf):
        ts = {s: bracket.get((conf, s)) for s in range(1, 9)}
        r1 = [play(ts.get(1), ts.get(8), 'R1'), play(ts.get(4), ts.get(5), 'R1'),
              play(ts.get(3), ts.get(6), 'R1'), play(ts.get(2), ts.get(7), 'R1')]
        csf = [play(r1[0], r1[1], 'CSF'), play(r1[2], r1[3], 'CSF')]
        return play(csf[0], csf[1], 'CF')

    ca = run_conf('East')
    cb = run_conf('West')
    if ca and cb:
        play(ca, cb, 'Finals')
    return games


def score_roster(roster_picks, stats_players, rng, games, cache):
    """Score a roster using actual completed-round scores + simulated future."""
    total = 0
    for s_str, pick in roster_picks.items():
        s = int(s_str)
        team = pick.get('team', '')
        tg = games.get(team)
        if not tg:
            continue
        slug = pick.get('player_id', '')
        reg_ppg_val = pick.get('cost', 0)
        p_stats = stats_players.get(slug, {})
        playoff_games = p_stats.get('games', [])
        ppg_blend, _ = blended_ppg(reg_ppg_val, playoff_games)
        sd = ppg_blend * cv(ppg_blend)
        m = mult(s)

        for rd in ROUNDS:
            ng = tg.get(rd, 0)
            if not ng:
                continue
            # Use actual scores for completed rounds
            actual_games = [g for g in playoff_games if g.get('round') == rd]
            key = f"{slug}:{rd}"
            if key in cache:
                avg = cache[key]
            elif len(actual_games) >= ng:
                # Round fully completed — use actual top-4
                pts = [g['pts'] for g in actual_games[:ng]]
                top = sorted(pts, reverse=True)[:4]
                avg = sum(top) / len(top)
                cache[key] = avg
            else:
                # Partially or not yet played — simulate remaining games
                pts = [g['pts'] for g in actual_games]
                remaining = ng - len(pts)
                for _ in range(remaining):
                    pts.append(max(0, ppg_blend + rng.gauss(0, sd)))
                top = sorted(pts, reverse=True)[:4]
                avg = sum(top) / len(top)
                cache[key] = avg
            total += avg * m
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', help='Score only this entrant')
    ap.add_argument('--exclude', help='Exclude this entrant from opponents')
    ap.add_argument('-N', type=int, default=3000)
    ap.add_argument('--data-dir', default=None)
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent.parent / 'data'
    picks = json.loads((data_dir / 'picks.json').read_text())
    stats = json.loads((data_dir / 'stats.json').read_text())
    budget = json.loads((data_dir / 'budget.json').read_text())

    bracket = build_bracket(budget)
    series_state = get_series_state(stats)
    stats_players = stats.get('players', {})

    entrants = [e for e in picks['entrants'] if not e.get('_synthetic')]
    n_entrants = len(entrants)
    buyin = 50
    free_entries = 2
    pot = (n_entrants - free_entries) * buyin
    payouts = {i + 1: pot * sh for i, sh in enumerate(
        [0.50, 0.25, 0.13, 0.07, 0.05] if n_entrants < 30 else
        [0.45, 0.23, 0.13, 0.09, 0.06, 0.04]
    )}

    # Show blended PPG for context
    print(f"\n{'Player':<28s} {'Reg':>5} {'Plf':>5} {'Blend':>6} {'Games':>5}")
    print('-' * 55)
    shown = set()
    for e in entrants:
        for s_str, pick in e['picks'].items():
            slug = pick.get('player_id')
            if slug in shown: continue
            shown.add(slug)
            p = stats_players.get(slug, {})
            pg = p.get('games', [])
            bl, plf = blended_ppg(pick.get('cost', 0), pg)
            if pg:
                print(f"  {pick['name']:<26s} {pick.get('cost',0):>5.1f} {plf:>5.1f} {bl:>6.1f} {len(pg):>5}")

    # Simulate EV for each entrant
    target_name = args.name
    exclude_name = args.exclude

    results = []
    for ent in entrants:
        if target_name and ent['name'].lower() != target_name.lower():
            continue
        opponents = [o for o in entrants if o['name'] != ent['name']]
        if exclude_name:
            opponents = [o for o in opponents if o['name'].lower() != exclude_name.lower()]

        rng = random.Random(42)
        ev = 0
        pts_sum = 0
        ranks = [0] * 30
        for _ in range(args.N):
            g = sim_bracket(rng, bracket, series_state)
            cache = {}
            my = score_roster(ent['picks'], stats_players, rng, g, cache)
            pts_sum += my
            scores = [(my, -1)]
            for i, o in enumerate(opponents):
                scores.append((score_roster(o['picks'], stats_players, rng, g, cache), i))
            scores.sort(key=lambda x: -x[0])
            r = [i for i, s in enumerate(scores) if s[1] == -1][0] + 1
            ranks[r] += 1
            ev += payouts.get(r, 0)

        results.append({
            'name': ent['name'],
            'ev': ev / args.N,
            'pts': pts_sum / args.N,
            'win': ranks[1] / args.N,
            'top5': sum(ranks[1:6]) / args.N,
            'ranks': ranks,
        })

    results.sort(key=lambda r: -r['ev'])
    print(f"\n{'Rank':<5} {'Entrant':<25s} {'EV':>8} {'Avg Pts':>8} {'Win%':>6} {'Top5%':>6}")
    print('-' * 62)
    for i, r in enumerate(results, 1):
        print(f"  {i:<3d} {r['name']:<25s} ${r['ev']:>6.2f} {r['pts']:>8.1f} {r['win']*100:>5.1f}% {r['top5']*100:>5.1f}%")


if __name__ == '__main__':
    main()
