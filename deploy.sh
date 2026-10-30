#!/usr/bin/env bash
# One command to build, deploy, and push, so the GitHub repo always reflects what is live.
#
#   ./deploy.sh              build frontend + deploy to Firebase + commit & push
#   ./deploy.sh --backend    also deploy the backend to Railway
#
set -euo pipefail
cd "$(dirname "$0")"

echo "==> baking the curator's live anchor votes into the bank"
# The backend applies anchors.html votes the moment they arrive and keeps them on its /data volume. This
# copies them into backend/sources.json + tools/votes so the repo matches what is live. A failed pull must
# not block a deploy: the votes are still on the volume and the new server replays them at boot.
if python3 tools/apply_anchor_votes.py; then
  python3 tools/build_anchor_page.py && cp ~/Downloads/anchors.html frontend/species-web-deploy/anchors.html
else
  echo "   !! WARNING: could not pull anchor votes from the live backend. Deploying anyway; the volume keeps them."
fi

echo "==> building frontend"
( cd frontend && python3 build_leads.py )

echo "==> deploying frontend (Firebase Hosting)"
( cd frontend/species-web-deploy && firebase deploy --only hosting )

if [[ "${1:-}" == "--backend" ]]; then
  echo "==> deploying backend (Railway)"
  ( cd backend && railway up --detach )
fi

echo "==> committing + pushing to GitHub"
git add -A
if git diff --cached --quiet; then
  echo "   (nothing changed)"
else
  ver="$(grep -oE 'v ?[0-9]{4}\.[0-9]{4}' frontend/species-web-deploy/index.html | head -1 || echo update)"
  git commit -m "deploy: ${ver:-update}"
  if git remote | grep -q .; then
    git push
  else
    echo "   (no git remote configured yet -> committed locally, push skipped)"
  fi
fi
echo "==> done"
