#!/usr/bin/env bash
set -euo pipefail

branch="$1"
alias="$2"
repo="$(pwd)"
work="$(mktemp -d)"
trap 'git -C "$repo" worktree remove --force "$work/pages" 2>/dev/null; rm -rf "$work"' EXIT

DOCS_ROOT_ALIAS="$alias" uv run --frozen --group docs mkdocs build --strict --site-dir "$work/site"
git worktree add "$work/pages" "$branch"
cd "$work/pages"
keep="$(jq -r '.[] | .version, .aliases[]' versions.json)"
for entry in *; do
  if [ "$entry" != versions.json ] && ! grep -qxF "$entry" <<<"$keep"; then
    git rm -rq "$entry"
  fi
done
cp -R "$work/site/." .
git add -A .
git commit --no-verify -qm "Deployed the $alias docs at the site root"
