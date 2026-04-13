"""
Data integrity tests for sim dashboard historical data.
Run: python3 /tmp/sim_dashboard_fresh/test_data.py
"""
import json, re, sys

def load_allyears():
    with open('/tmp/sim_dashboard_fresh/allyears.js') as f:
        content = f.read()
    return json.loads(content.replace('const ALL_YEARS_DATA = ', '').rstrip(';\n'))

data = load_allyears()
failures = []

def check(condition, msg):
    if not condition:
        failures.append(msg)
        print(f"  FAIL: {msg}")
    return condition

print("=" * 60)
print("SIM DASHBOARD DATA INTEGRITY TESTS")
print("=" * 60)

for year in sorted(data.keys()):
    yd = data[year]
    print(f"\n--- {year} ---")

    bracket = yd['bracket']
    opponents = yd['opponents']
    pool = yd['pool_by_seed']

    # 1. Bracket structure
    regions = list(bracket.keys())
    check(len(regions) == 4, f"{year}: expected 4 regions, got {len(regions)}: {regions}")
    for reg in regions:
        check(reg in ('East', 'West', 'South', 'Midwest'),
              f"{year}: unexpected region '{reg}'")
        pairs = bracket[reg]
        check(len(pairs) == 4, f"{year} {reg}: expected 4 game pairs, got {len(pairs)}")
        for pi, pair in enumerate(pairs):
            check(len(pair) == 2, f"{year} {reg} pair {pi}: expected 2 games, got {len(pair)}")
            for gi, game in enumerate(pair):
                check(len(game) == 4, f"{year} {reg} pair {pi} game {gi}: expected [team,seed,team,seed], got {len(game)} elements")
                t0, s0, t1, s1 = game
                check(isinstance(t0, str) and len(t0) > 0, f"{year} {reg}: empty team name in game")
                check(isinstance(s0, int) and 1 <= s0 <= 16, f"{year} {reg}: bad seed {s0} for '{t0}'")
                check(isinstance(s1, int) and 1 <= s1 <= 16, f"{year} {reg}: bad seed {s1} for '{t1}'")
                check(t0 == t0.lower(), f"{year} {reg}: team '{t0}' not lowercase")
                check(t1 == t1.lower(), f"{year} {reg}: team '{t1}' not lowercase")

    # Collect all bracket teams
    bracket_teams = set()
    for reg, pairs in bracket.items():
        for pair in pairs:
            for game in pair:
                bracket_teams.add(game[0])
                bracket_teams.add(game[2])
    check(len(bracket_teams) == 64, f"{year}: expected 64 bracket teams, got {len(bracket_teams)}")

    # 2. All opponent/pool team names exist in bracket
    opp_teams = set()
    for opp in opponents:
        for p in opp['players']:
            opp_teams.add(p['espn'])
    pool_teams = set()
    for seed, players in pool.items():
        for p in players:
            pool_teams.add(p['espn'])

    all_picked_teams = opp_teams | pool_teams
    missing = all_picked_teams - bracket_teams
    if missing:
        # Only WARN for teams not in bracket (could be non-tourney picks)
        print(f"  WARN: {len(missing)} picked teams not in bracket (0 games): {missing}")

    # CRITICAL: check that high-usage teams ARE in bracket
    # Count how many opponents use each team
    team_usage = {}
    for opp in opponents:
        for p in opp['players']:
            team_usage[p['espn']] = team_usage.get(p['espn'], 0) + 1
    high_usage_missing = {t: team_usage.get(t, 0) for t in missing if team_usage.get(t, 0) >= 3}
    check(len(high_usage_missing) == 0,
          f"{year}: HIGH-USAGE teams missing from bracket (likely name mismatch): {high_usage_missing}")

    # 3. Opponent structure
    check(len(opponents) >= 20, f"{year}: only {len(opponents)} opponents (expected 20+)")
    for oi, opp in enumerate(opponents):
        check('name' in opp, f"{year} opp {oi}: missing 'name'")
        check('players' in opp, f"{year} opp {oi}: missing 'players'")
        players = opp.get('players', [])
        check(len(players) == 16, f"{year} opp '{opp.get('name',oi)}': expected 16 players, got {len(players)}")

        # Captain checks
        scorer_count = sum(1 for p in players if p.get('captain') == 'scorer')
        playmaker_count = sum(1 for p in players if p.get('captain') == 'playmaker')
        check(scorer_count == 1,
              f"{year} opp '{opp.get('name',oi)}': expected 1 scorer captain, got {scorer_count}")
        check(playmaker_count == 1,
              f"{year} opp '{opp.get('name',oi)}': expected 1 playmaker captain, got {playmaker_count}")

        # No bad captain values
        for p in players:
            cap = p.get('captain')
            check(cap in (None, 'scorer', 'playmaker'),
                  f"{year} opp '{opp.get('name',oi)}': bad captain value '{cap}' for {p.get('slug')}")

        # All players have required fields with reasonable values
        for p in players:
            check('slug' in p and p['slug'], f"{year} opp '{opp.get('name',oi)}': player missing slug")
            check('espn' in p and p['espn'], f"{year} opp '{opp.get('name',oi)}': player missing espn")
            check(p.get('ppg', 0) > 0, f"{year} opp '{opp.get('name',oi)}': {p.get('slug')} has 0 PPG")
            check(p.get('rpg', 0) >= 0, f"{year} opp '{opp.get('name',oi)}': {p.get('slug')} missing RPG")
            check(p.get('apg', 0) >= 0, f"{year} opp '{opp.get('name',oi)}': {p.get('slug')} missing APG")

    # 4. Pool structure
    check(len(pool) == 16, f"{year}: expected 16 seeds in pool, got {len(pool)}")
    for seed_str in [str(s) for s in range(1, 17)]:
        players = pool.get(seed_str, pool.get(int(seed_str), []))
        check(len(players) >= 1, f"{year} Sd{seed_str}: empty player pool")
        # Should be sorted by PPG desc
        ppgs = [p['ppg'] for p in players]
        check(ppgs == sorted(ppgs, reverse=True),
              f"{year} Sd{seed_str}: pool not sorted by PPG desc")
        for p in players:
            check(p.get('ppg', 0) > 0, f"{year} Sd{seed_str}: {p.get('name')} has 0 PPG")
            check(p.get('rpg') is not None, f"{year} Sd{seed_str}: {p.get('name')} missing RPG")
            check(p.get('apg') is not None, f"{year} Sd{seed_str}: {p.get('name')} missing APG")
            check(p.get('espn', '') == p.get('espn', '').lower(),
                  f"{year} Sd{seed_str}: espn name '{p.get('espn')}' not lowercase")

    # 5. Cross-consistency: every opponent player slug should be in the pool
    pool_slugs = set()
    for seed, players in pool.items():
        for p in players:
            pool_slugs.add(p['slug'])
    for opp in opponents:
        for p in opp['players']:
            check(p['slug'] in pool_slugs,
                  f"{year} opp '{opp['name']}': player {p['slug']} not found in pool")

    # 6. Bracket seed coverage: each region should have seeds 1-16
    for reg in bracket:
        seeds_in_region = set()
        for pair in bracket[reg]:
            for game in pair:
                seeds_in_region.add(game[1])
                seeds_in_region.add(game[3])
        check(seeds_in_region == set(range(1, 17)),
              f"{year} {reg}: missing seeds {set(range(1,17)) - seeds_in_region}")

    # 7. No duplicate teams in bracket
    for reg in bracket:
        reg_teams = []
        for pair in bracket[reg]:
            for game in pair:
                reg_teams.extend([game[0], game[2]])
        check(len(reg_teams) == len(set(reg_teams)),
              f"{year} {reg}: duplicate teams in bracket")

    if not any(year in f for f in failures):
        print("  All checks passed!")

# Summary
print(f"\n{'=' * 60}")
if failures:
    print(f"FAILED: {len(failures)} checks")
    for f in failures[:20]:
        print(f"  - {f}")
    if len(failures) > 20:
        print(f"  ... and {len(failures) - 20} more")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
    sys.exit(0)
