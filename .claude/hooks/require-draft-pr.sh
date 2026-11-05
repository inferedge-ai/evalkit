#!/bin/sh
input=$(cat)
case "$input" in
  *"gh pr create"*) ;;
  *) exit 0 ;;
esac
if printf '%s' "$input" | grep -Eq -- '--draft|[[:space:]]-d([[:space:]]|\\n|"|$)'; then
  exit 0
fi
echo "Open pull requests as drafts: rerun with 'gh pr create --draft'. Mark it ready with 'gh pr ready <number>' when it should be reviewed and run CI." >&2
exit 2
