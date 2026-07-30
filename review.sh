#!/usr/bin/env bash
# One command: generate a fresh batch, score every quality we track against the last run, and open the
# review page. Exists so "did anything improve?" is something you run, not something you have to ask.
#   ./review.sh              # default channel
#   ./review.sh @veritasium  # any channel
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/measure.py "${1:-@ColdFusion}"
open -a "Google Chrome" ~/Downloads/review.html 2>/dev/null || true
