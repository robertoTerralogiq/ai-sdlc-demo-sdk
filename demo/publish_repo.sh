#!/usr/bin/env bash
# Publish this demo as its own GitHub repo in one mode, as "stage 0: baseline" on main.
#   bash demo/publish_repo.sh <ide|interactions|sdk|adk> <owner/repo> [public|private]
# Creates the repo, pushes main, applies setup_github_repo.sh. Prints the local clone path.
set -euo pipefail
mode="${1:?usage: publish_repo.sh <ide|interactions|sdk|adk> <owner/repo> [public|private]}"
repo="${2:?owner/repo}"
visibility="${3:-public}"
src="$(cd "$(dirname "$0")/.." && pwd)"
work="${WORK_DIR:-$(mktemp -d)}/$(basename "$repo")"

rsync -a --exclude .venv --exclude '*.egg-info' --exclude __pycache__ --exclude .pytest_cache \
  --exclude demo/out --exclude demo/pipeline-run.log --exclude .env \
  --exclude ai-review-findings.json --exclude gl-code-quality-report.json "$src/" "$work/"
cd "$work"
git init -q -b main
git add -A
git commit -q -m "stage 0: baseline — loan-service, reviewer, Antigravity skills ($mode mode)"
gh repo view "$repo" >/dev/null 2>&1 \
  || gh repo create "$repo" "--$visibility" --description "AI SDLC demo — $mode path (Antigravity + Gemini + GitHub)"
git remote add origin "https://github.com/$repo.git"
git push -q -u origin main
bash demo/setup_github_repo.sh "$repo" "$mode"
echo "$work"
