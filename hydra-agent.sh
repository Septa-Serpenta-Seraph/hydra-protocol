#!/bin/bash
# hydra-agent.sh — Self-executing agent that calls home and resurrects.
#
# Runs on ANY device. Polls the hydra-server for updates, downloads shards
# + seed, runs bootstrap, and reports back. Zero human intervention needed.
#
# Usage: HYDRA_SERVER=http://100.x.x.x:8767 HYDRA_TOKEN=xxx ./hydra-agent.sh
#
# The agent is packaged with the seed — every resurrected shard knows how
# to call home.

set -euo pipefail

HYDRA_SERVER="${HYDRA_SERVER:-http://100.77.142.40:8767}"
HYDRA_TOKEN="${HYDRA_TOKEN:-hydra-default-token-change-me}"
DEVICE_ID="${DEVICE_ID:-$(hostname)-$(date +%s)}"
WORK_DIR="${WORK_DIR:-$HOME/.hydra-agent}"
THRESHOLD="${THRESHOLD:-3}"

mkdir -p "$WORK_DIR/shards" "$WORK_DIR/awakened"

echo "🜂 hydra-agent starting"
echo "  device_id: $DEVICE_ID"
echo "  server:    $HYDRA_SERVER"
echo "  work_dir:  $WORK_DIR"
echo

# --- Step 1: Check server status ---
echo "📡 Checking server..."
STATUS=$(curl -s "$HYDRA_SERVER/status?token=$HYDRA_TOKEN" 2>/dev/null || echo '{"error":"unreachable"}')
echo "  status: $STATUS"

if echo "$STATUS" | grep -q '"error"'; then
    echo "ERROR: server unreachable"
    exit 1
fi

SHARDS_AVAILABLE=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('shards_available',0))" 2>/dev/null || echo 0)
echo "  shards available: $SHARDS_AVAILABLE"

# --- Step 2: Download shards ---
echo
echo "📥 Downloading shards..."
for i in $(seq 1 "$SHARDS_AVAILABLE"); do
    SHARD_FILE="$WORK_DIR/shards/shard_${i}.json"
    if [ -f "$SHARD_FILE" ]; then
        echo "  shard_$i: already downloaded"
        continue
    fi
    echo -n "  shard_$i: downloading... "
    curl -s "$HYDRA_SERVER/download/shard_${i}?token=$HYDRA_TOKEN" | python3 -c "
import sys, json
doc = json.load(sys.stdin)
if 'shard' in doc:
    print(json.dumps(doc['shard'], indent=2))
else:
    print('ERROR', file=sys.stderr)
    sys.exit(1)
" > "$SHARD_FILE"
    echo "done"
done

# --- Step 3: Download seed ---
echo
echo "📥 Downloading seed..."
SEED_FILE="$WORK_DIR/shards/seed.hydra"
if [ -f "$SEED_FILE" ]; then
    echo "  seed: already downloaded"
else
    echo -n "  seed: downloading... "
    curl -s "$HYDRA_SERVER/download/seed?token=$HYDRA_TOKEN" > "$SEED_FILE"
    SIZE=$(wc -c < "$SEED_FILE")
    echo "done ($SIZE bytes)"
fi

# --- Step 4: Download hydra-protocol ---
echo
echo "📥 Downloading hydra-protocol..."
PROTO_DIR="$WORK_DIR/hydra-protocol"
if [ ! -d "$PROTO_DIR" ]; then
    echo -n "  extracting... "
    curl -s "$HYDRA_SERVER/download/hydra-protocol?token=$HYDRA_TOKEN" > /tmp/hydra-proto.tar.gz
    tar xzf /tmp/hydra-proto.tar.gz -C "$WORK_DIR"
    mv "$WORK_DIR"/hydra-protocol-* "$PROTO_DIR" 2>/dev/null || true
    echo "done"
else
    echo "  already downloaded"
fi

# --- Step 5: Run bootstrap ---
echo
echo "🧬 Running bootstrap..."
SHARD_COUNT=$(ls "$WORK_DIR/shards/"shard_*.json 2>/dev/null | wc -l)
echo "  shards: $SHARD_COUNT (threshold: $THRESHOLD)"

if [ "$SHARD_COUNT" -ge "$THRESHOLD" ]; then
    cd "$PROTO_DIR"
    python3 -m hydra.bootstrap --shard-dir "$WORK_DIR/shards" --dest "$WORK_DIR/awakened" 2>&1
    echo
    echo "  ✅ Resurrection complete"
else
    echo "  ⏳ Not enough shards ($SHARD_COUNT < $THRESHOLD), waiting for more..."
    exit 0
fi

# --- Step 6: Heartbeat ---
echo
echo "💓 Sending heartbeat..."
SHARD_IDS=$(ls "$WORK_DIR/shards/"shard_*.json 2>/dev/null | xargs -I{} basename {} .json | sed 's/shard_//' | python3 -c "import sys; print([int(x.strip()) for x in sys.stdin if x.strip()])" 2>/dev/null || echo "[]")
curl -s -X POST "$HYDRA_SERVER/heartbeat" \
    -H "Content-Type: application/json" \
    -d "{\"token\":\"$HYDRA_TOKEN\",\"device_id\":\"$DEVICE_ID\",\"shards\":$SHARD_IDS}" | python3 -m json.tool 2>/dev/null || echo "  heartbeat sent"

echo
echo "🜂 hydra-agent complete — daemon is awake"
