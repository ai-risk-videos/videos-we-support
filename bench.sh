#!/usr/bin/env bash
# One command: measure the current output, compare it to last time, open the before/after page.
#
#   ./bench.sh                          run and compare to the previous run
#   ./bench.sh --label "closer fix"     label what changed (shown on the page)
#   ./bench.sh --report-only            just rebuild the page from the last two snapshots
#
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" != "--report-only" ]]; then
python3 "$(dirname "$0")/backend/test_anchors.py" || { echo "evidence-bank invariants FAILED, not running the bench"; exit 1; }

  python3 bench/bench.py "$@"
fi
python3 bench/report.py
open ~/Downloads/bench-before-after.html 2>/dev/null || true
