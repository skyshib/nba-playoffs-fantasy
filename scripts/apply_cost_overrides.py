#!/usr/bin/env python3
"""
Apply cost_overrides from data/config.json to data/budget.json.

Intended workflow:
  1. Admin edits data/config.json → adds/changes entries under "cost_overrides".
  2. Run `python3 scripts/apply_cost_overrides.py` to bake those into
     data/budget.json so the picks UI + sim see the adjusted costs.
  3. (Optional) Re-run `python3 scripts/build_simdata.py` so the sim's
     allyears.js picks up the new 2026 pool costs.

Idempotent — re-running uses the current override values. Stores the
original cost alongside in budget.json so we can see what was adjusted.
"""

import json
from pathlib import Path


def main():
    data = Path(__file__).resolve().parent.parent / "data"
    cfg = json.loads((data / "config.json").read_text())
    overrides = {k: v for k, v in cfg.get("cost_overrides", {}).items()
                 if not k.startswith("_")}
    budget = json.loads((data / "budget.json").read_text())

    changed = 0
    for p in budget.get("players", []):
        slug = p.get("player_id")
        if slug in overrides:
            new_cost = overrides[slug]
            if "cost_original" not in p:
                p["cost_original"] = p["cost"]
            if p["cost"] != new_cost:
                changed += 1
                print(f"  {p['name']:<30s} {p['cost_original']:>6.2f} -> {new_cost:>6.2f}")
            p["cost"] = new_cost
            p["cost_overridden"] = True

    # Clear overrides that are no longer in config
    for p in budget.get("players", []):
        slug = p.get("player_id")
        if p.get("cost_overridden") and slug not in overrides:
            p["cost"] = p.get("cost_original", p["cost"])
            del p["cost_overridden"]
            if "cost_original" in p:
                del p["cost_original"]
            print(f"  {p['name']:<30s} reverted to {p['cost']:.2f}")
            changed += 1

    (data / "budget.json").write_text(json.dumps(budget, indent=2))
    print(f"\nApplied {len(overrides)} overrides · {changed} costs changed")


if __name__ == "__main__":
    main()
