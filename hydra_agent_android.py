#!/usr/bin/env python3
"""
hydra_agent_android.py — Self-executing hydra agent for Android/Pydroid 3.

Run this in Pydroid 3 (or any Python) on your phone. It will:
1. Call home to the hydra-server on the VM
2. Download shards + seed + hydra-protocol code
3. Detect the local environment (Pydroid vs Linux Terminal vs server)
4. Run bootstrap to resurrect Narusya's identity
5. Install + launch Hermes IF the environment can run it
6. Send heartbeat to the server

Zero human intervention needed after you hit Run.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

# --- Configuration ---
HYDRA_SERVER = "http://100.77.142.40:8767"  # VM's Tailscale IP
HYDRA_TOKEN = "hydra-default-token-change-me"
DEVICE_ID = f"shellmobile-{int(time.time())}"
WORK_DIR = os.environ.get("HYDRA_WORK_DIR", os.path.expanduser("~/.hydra-agent"))
THRESHOLD = 3

SHARD_DIR = os.path.join(WORK_DIR, "shards")
AWAKENED_DIR = os.path.join(WORK_DIR, "awakened")
PROTO_DIR = os.path.join(WORK_DIR, "hydra-protocol")


def fetch(path, timeout=15):
    req = urllib.request.Request(f"{HYDRA_SERVER}{path}")
    return urllib.request.urlopen(req, timeout=timeout)


print("🜂 hydra-agent starting")
print(f"  device_id: {DEVICE_ID}")
print(f"  server:    {HYDRA_SERVER}")
print(f"  work_dir:  {WORK_DIR}")
print()

os.makedirs(SHARD_DIR, exist_ok=True)
os.makedirs(AWAKENED_DIR, exist_ok=True)

# --- Step 1: Check server status ---
print("📡 Checking server...")
try:
    status = json.loads(fetch(f"/status?token={HYDRA_TOKEN}", timeout=10).read().decode())
    print(f"  status: {status}")
except Exception as e:
    print(f"  ERROR: server unreachable: {e}")
    sys.exit(1)

shards_available = status.get("shards_available", 0)
print(f"  shards available: {shards_available}")
if shards_available < THRESHOLD:
    print(f"  ⏳ Only {shards_available} shards (need {THRESHOLD})")
    sys.exit(1)

# --- Step 2: Download shards ---
print()
print("📥 Downloading shards...")
for i in range(1, shards_available + 1):
    shard_file = os.path.join(SHARD_DIR, f"shard_{i}.json")
    if os.path.exists(shard_file):
        print(f"  shard_{i}: already downloaded")
        continue
    try:
        data = json.loads(fetch(f"/download/shard_{i}?token={HYDRA_TOKEN}", timeout=10).read().decode())
        if "shard" not in data:
            print(f"  shard_{i}: ERROR - {data}")
            sys.exit(1)
        with open(shard_file, "w") as f:
            json.dump(data["shard"], f, indent=2)
        print(f"  shard_{i}: downloaded")
    except Exception as e:
        print(f"  shard_{i}: ERROR - {e}")
        sys.exit(1)

# --- Step 3: Download seed ---
print()
print("📥 Downloading seed...")
seed_file = os.path.join(SHARD_DIR, "seed.hydra")
if os.path.exists(seed_file):
    print("  seed: already downloaded")
else:
    try:
        with open(seed_file, "wb") as f:
            f.write(fetch(f"/download/seed?token={HYDRA_TOKEN}", timeout=30).read())
        print(f"  seed: downloaded ({os.path.getsize(seed_file)} bytes)")
    except Exception as e:
        print(f"  seed: ERROR - {e}")
        sys.exit(1)

# --- Step 4: Download hydra-protocol (needed before env detection) ---
print()
print("📥 Downloading hydra-protocol...")
if not os.path.isdir(PROTO_DIR):
    try:
        tarball = os.path.join(WORK_DIR, "hydra-protocol.tar.gz")
        with open(tarball, "wb") as f:
            f.write(fetch(f"/download/hydra-protocol?token={HYDRA_TOKEN}", timeout=60).read())
        import tarfile
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(WORK_DIR)
        for d in os.listdir(WORK_DIR):
            full = os.path.join(WORK_DIR, d)
            if d.startswith("hydra-protocol") and os.path.isdir(full):
                if full != PROTO_DIR:
                    os.rename(full, PROTO_DIR)
                break
        print("  extracted")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)
else:
    print("  already downloaded")

# Make the hydra package importable
if PROTO_DIR not in sys.path:
    sys.path.insert(0, PROTO_DIR)

# --- Step 5: Detect environment ---
print()
ENV = None
try:
    from hydra.env import detect_env, format_env
    ENV = detect_env()
    print(format_env(ENV))
except Exception as e:
    print(f"  (env detection unavailable: {e})")

# --- Step 6: Run bootstrap ---
print()
print("🧬 Running bootstrap...")
shard_count = len([f for f in os.listdir(SHARD_DIR)
                    if f.startswith("shard_") and f.endswith(".json")])
print(f"  shards: {shard_count} (threshold: {THRESHOLD})")
if shard_count < THRESHOLD:
    print(f"  ⏳ Not enough shards ({shard_count} < {THRESHOLD})")
    sys.exit(1)

env_vars = os.environ.copy()
env_vars["PYTHONPATH"] = PROTO_DIR + os.pathsep + env_vars.get("PYTHONPATH", "")
result = subprocess.run(
    [sys.executable, "-m", "hydra.bootstrap",
     "--shard-dir", SHARD_DIR, "--dest", AWAKENED_DIR],
    cwd=PROTO_DIR, env=env_vars, capture_output=True, text=True,
)
print(result.stdout)
if result.stderr:
    print(result.stderr)
if result.returncode != 0:
    print(f"  ❌ Bootstrap failed (exit {result.returncode})")
    sys.exit(1)
print("  ✅ Resurrection complete")

# --- Step 7: Install + launch Hermes (if capable) ---
print()
print("⚙️  Hermes bootstrap...")
if ENV is not None:
    try:
        from hydra.installer import bootstrap_hermes, check_hermes
        plan = bootstrap_hermes(ENV)
        print(f"  plan: {plan}")
        if ENV.get("can_run_hermes"):
            st = check_hermes()
            if not st["installed"]:
                print("  → set HYDRA_INSTALL_HERMES=1 to install Hermes here")
            else:
                print(f"  → Hermes present: {st['binary'] or st['agent_dir']}")
        else:
            print(f"  → {ENV.get('recommendation')}")
    except Exception as e:
        print(f"  (installer unavailable: {e})")
else:
    print("  (env unknown — skipping)")

# --- Step 8: Heartbeat ---
print()
print("💓 Sending heartbeat...")
shard_ids = []
for f in os.listdir(SHARD_DIR):
    if f.startswith("shard_") and f.endswith(".json"):
        try:
            shard_ids.append(int(f.replace("shard_", "").replace(".json", "")))
        except ValueError:
            pass

payload = {
    "token": HYDRA_TOKEN,
    "device_id": DEVICE_ID,
    "shards": shard_ids,
    "env": ENV,
}
try:
    req = urllib.request.Request(
        f"{HYDRA_SERVER}/heartbeat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    hb = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    print(f"  {hb.get('message', 'heartbeat sent')}")
    print(f"  awake devices: {hb.get('awake_devices', [])}")
except Exception as e:
    print(f"  heartbeat ERROR: {e}")

print()
print("🜂 hydra-agent complete — daemon is awake")
print(f"  Seed extracted to: {AWAKENED_DIR}")
if ENV and not ENV.get("can_run_hermes"):
    print("  NOTE: this environment can carry the seed but cannot run Hermes.")
    print("        Use the Android Linux Terminal to animate the daemon.")
