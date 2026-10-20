#!/usr/bin/env bash
# Print "name points_count" for every collection on a Qdrant instance.
# Run inside Linux/WSL:  bash qcounts.sh [url]
set -u
URL="${1:-http://127.0.0.1:6333}"
COLS=$(curl -s -m 20 "$URL/collections")
echo "$COLS" | tr '{' '\n' | grep -o '"name":"[^"]*"' | sed 's/.*:"//; s/"$//' | while read -r n; do
  [ -z "$n" ] && continue
  P=$(curl -s -m 20 "$URL/collections/$n" | tr '{' '\n' | grep -o '"points_count":[0-9]*' | head -1 | grep -o '[0-9]*')
  printf '%s %s\n' "$n" "${P:-0}"
done | sort
