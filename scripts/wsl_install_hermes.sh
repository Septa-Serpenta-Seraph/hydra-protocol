#!/usr/bin/env bash
# Run the official Hermes installer inside WSL Debian, non-interactively.
# Logs everything to /tmp/hermes-install.log and prints a tail at the end.
set -u
LOG=/tmp/hermes-install.log
echo "== fetching installer =="
curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/hermes-install.sh || {
  echo "FETCH FAILED"; exit 1; }
echo "installer bytes: $(wc -c </tmp/hermes-install.sh)"

echo
echo "== running installer (non-interactive) =="
echo "started: $(date -u +%H:%M:%S)"
bash /tmp/hermes-install.sh --non-interactive >"$LOG" 2>&1
rc=$?
echo "installer rc=$rc  finished: $(date -u +%H:%M:%S)"
echo
echo "== last 40 lines of log =="
tail -40 "$LOG"
echo
echo "== does hermes exist? =="
for p in "$HOME/.hermes/bin/hermes" "$HOME/.local/bin/hermes" /usr/local/bin/hermes; do
  [ -e "$p" ] && echo "FOUND $p"
done
command -v hermes || echo "hermes not on PATH"
