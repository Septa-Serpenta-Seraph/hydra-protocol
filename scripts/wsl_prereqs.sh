#!/usr/bin/env bash
# Install Hermes build prerequisites inside WSL Debian.
set -u
export DEBIAN_FRONTEND=noninteractive
echo "== apt update =="
apt-get update -qq || { echo "APT UPDATE FAILED"; exit 1; }
echo "== apt install =="
apt-get install -y -qq \
  git curl ca-certificates build-essential tar unzip xz-utils \
  python3 python3-venv python3-dev pkg-config libssl-dev \
  >/tmp/apt.log 2>&1
rc=$?
echo "apt rc=$rc"
[ $rc -ne 0 ] && tail -20 /tmp/apt.log
echo
echo "== tool check =="
for t in git curl tar unzip sha256sum python3 node; do
  printf '%-12s %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo MISSING)"
done
echo
echo "== versions =="
git --version 2>&1 | head -1
python3 --version 2>&1 | head -1
