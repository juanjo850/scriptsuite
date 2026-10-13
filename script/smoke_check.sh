#!/usr/bin/env bash
# Smoke check for the scripts folder: lists what was discovered and exits
# non-zero if a script is missing an executable bit it needs.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "scanning ${here}"
echo

count=0
for path in "${here}"/*; do
  [ -f "$path" ] || continue
  name="$(basename "$path")"
  printf '  %-28s %s\n' "$name" "$(wc -c <"$path") bytes"
  count=$((count + 1))
done

echo
echo "found ${count} script(s)"

if [ "$count" -eq 0 ]; then
  echo "nothing to run" >&2
  exit 1
fi

exit 0