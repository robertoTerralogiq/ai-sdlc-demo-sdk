#!/usr/bin/env bash
# Push the current branch and open a PR that merges itself once required checks pass.
set -euo pipefail
title="${1:?usage: open_pr.sh \"<ID>: <title>\"}"
base="${PR_BASE:-main}"
branch="$(git branch --show-current)"
[[ "$branch" == "$base" ]] && { echo "refusing to open a PR from $base" >&2; exit 1; }

git push -u origin "$branch"
gh pr view "$branch" --json url -q .url 2>/dev/null \
  || gh pr create --base "$base" --head "$branch" --title "$title" --fill-verbose
# Needs "Allow auto-merge" on the repo and branch protection with required checks.
gh pr merge "$branch" --auto --squash --delete-branch
gh pr view "$branch" --json url,autoMergeRequest -q '"\(.url)  auto-merge: \(.autoMergeRequest != null)"'
