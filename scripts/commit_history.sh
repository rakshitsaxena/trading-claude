#!/usr/bin/env bash
# After an agent run, commit any new history/*.jsonl rows and push.
# Uses GITHUB_TOKEN from .env.  No-op if nothing changed.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

MSG="${1:-agent: history update}"

if [ -z "$(git status --porcelain history/)" ]; then
  echo "no history changes to commit"
  exit 0
fi

git config user.email "trading-claude@noreply.local"
git config user.name  "trading-claude agent"
git add history/
git commit -m "$MSG"

# Pull PAT from .env
if [ -z "${GITHUB_TOKEN:-}" ]; then
  if [ -f .env ]; then
    GITHUB_TOKEN="$(grep '^GITHUB_TOKEN=' .env | cut -d= -f2-)"
  fi
fi
if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "no GITHUB_TOKEN available; skipping push"
  exit 0
fi

REMOTE_URL="$(git remote get-url origin)"
# Strip https:// prefix, inject token
PUSH_URL="https://x-access-token:${GITHUB_TOKEN}@${REMOTE_URL#https://}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git push "$PUSH_URL" "HEAD:${BRANCH}"
echo "pushed history to $BRANCH"
