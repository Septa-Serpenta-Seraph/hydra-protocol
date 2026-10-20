#!/usr/bin/env python3
"""Bidirectional sync test using a throwaway collection.

Proves the thing the dry-run can't: that points actually MOVE, in both
directions, between two machines' Qdrants.

  Phase 1  shell creates 3 points -> sync -> hub must have them
  Phase 2  hub creates 2 points   -> sync -> shell must have them
  Phase 3  cleanup

Runs ON SHELL (inside WSL). The hub's own Qdrant is bound to the VM's
localhost, so the hub side is observed through /sync/ids — which is also a
stronger check, since it reads exactly what the sync protocol reads.
"""
import json
import subprocess
import sys
import urllib.request

sys.path.insert(0, "/root/.hydra-agent/hydra-protocol")
from hydra import qsync  # noqa: E402

COLL = "hydra_sync_test"
HUB = "http://100.77.142.40:8767"
TOKEN = "hydra-default-token-change-me"
LOCAL = "http://127.0.0.1:6333"
DIM = 8


def mk(i, tag):
    return {"id": f"00000000-0000-4000-8000-{i:012d}",
            "vector": [float((i * 7 + j) % 11) for j in range(DIM)],
            "payload": {"text": tag, "src": "hydra-sync-test"}}


def local_count(name):
    try:
        return qsync._call(LOCAL, f"/collections/{name}")["result"]["points_count"]
    except Exception:  # noqa: BLE001
        return None


def hub_ids(name):
    """What the hub reports it has (through the sync surface itself)."""
    try:
        url = f"{HUB}/sync/ids?collection={name}&token={TOKEN}"
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read())["ids"]
    except Exception:  # noqa: BLE001
        return []


def run_sync():
    r = subprocess.run(
        ["python3", "-m", "hydra.sync", "--hub", HUB, "--token", TOKEN],
        cwd="/root/.hydra-agent/hydra-protocol", capture_output=True, text=True)
    lines = [l for l in r.stdout.splitlines() if l.strip().startswith(("pulled", "  ~", "  +"))]
    if not lines:
        lines = r.stdout.splitlines()[-3:] or [r.stderr[-300:]]
    return "\n".join(lines[-4:])


print("=== setup: fresh test collection on shell ===")
try:
    qsync._call(LOCAL, f"/collections/{COLL}", "DELETE")
except Exception:  # noqa: BLE001
    pass
qsync.ensure_collection(LOCAL, COLL, DIM)
qsync.upsert_points(LOCAL, COLL, [mk(1, "from-shell-A"), mk(2, "from-shell-B"), mk(3, "from-shell-C")])
print(f"shell: {local_count(COLL)} points   hub: {len(hub_ids(COLL))} points")

print("\n=== phase 1: PUSH (shell -> hub) ===")
print(run_sync())
n_hub = len(hub_ids(COLL))
print(f"hub now has: {n_hub}  (expect 3)")
phase1 = n_hub == 3

print("\n=== phase 2: PULL (hub -> shell) ===")
# Seed two NEW points into the hub only, via its sync/push endpoint.
pts = [mk(4, "from-hub-D"), mk(5, "from-hub-E")]
body = json.dumps({"token": TOKEN, "collection": COLL, "points": pts}).encode()
req = urllib.request.Request(f"{HUB}/sync/push", method="POST", data=body,
                             headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=60).read()
print(f"hub seeded -> hub: {len(hub_ids(COLL))} points, shell still: {local_count(COLL)}")

print(run_sync())
n_local = local_count(COLL)
print(f"shell now has: {n_local}  (expect 5)")
phase2 = n_local == 5

print("\n=== cleanup ===")
try:
    qsync._call(LOCAL, f"/collections/{COLL}", "DELETE")
    print("shell test collection dropped")
except Exception as e:  # noqa: BLE001
    print("shell cleanup note:", e)

print("\nRESULT:", "PASS" if (phase1 and phase2) else "FAIL")
sys.exit(0 if (phase1 and phase2) else 1)
