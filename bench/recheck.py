#!/usr/bin/env python3
"""Recompute checks/stats on every stored snapshot using the CURRENT checks.py.

Why this exists: when a new check is added, older snapshots have no value for it, so the
before/after page can only say "new" and the question "did it help?" goes unanswered for exactly
the thing you just changed. The snapshots keep their raw ideas, so the honest fix is to re-score
history with today's rules. Ideas are never modified, only the derived numbers.
"""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import checks  # noqa: E402

n = 0
for f in sorted(glob.glob(os.path.join(HERE, 'runs', '*.json'))):
    try:
        snap = json.load(open(f))
    except Exception:
        continue
    groups = snap.get('groups') or []
    if not groups:
        continue
    for g in groups:
        ideas = g.get('ideas') or []
        g['checks'] = {k: v for k, v in checks.run_checks(ideas).items()}
        g['stats'] = checks.stats(ideas)
    allideas = [x for g in groups for x in (g.get('ideas') or [])]
    snap['totals'] = {
        'ideas': len(allideas),
        'violations': {k: sum(len(g['checks'].get(k, [])) for g in groups)
                       for k, _t, _f, _s, _fn in checks.CHECKS},
        'stats': checks.stats(allideas),
    }
    snap['rechecked_with'] = len(checks.CHECKS)
    json.dump(snap, open(f, 'w'), ensure_ascii=False, indent=1)
    n += 1
print("re-scored %d snapshots against the current %d checks" % (n, len(checks.CHECKS)))
