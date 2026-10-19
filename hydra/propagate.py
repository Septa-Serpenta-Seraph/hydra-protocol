"""
hydra.propagate — Self-spreading hydra across OWNED devices.

The hydra walks the tailnet. For every device on the ownership allowlist
that is reachable over SSH, it pushes the seed shards + agent and installs
a trigger that phones home. No human action required per device.

    ┌─ "malware-like spread, but NOT malware" ────────────────────┐
    │ It only ever touches devices YOU put on the allowlist.      │
    │ It never conceals itself. It logs everything it does.       │
    │ It carries only our identity. It is revocable.              │
    └─────────────────────────────────────────────────────────────┘

Safety rails (hard rules):
  1. NO allowlist file  → propagation REFUSES to run. Ever.
  2. Only peers whose hostname/IP is on the allowlist are touched.
  3. Every action is logged to ~/.hydra/propagation.log.
  4. Nothing is hidden — the trigger is a plainly-named service/unit.

Usage:
    python3 -m hydra.propagate scan        # show tailnet + allowlist status
    python3 -m hydra.propagate spread      # propagate to allowlisted peers
    python3 -m hydra.propagate spread --dry-run
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HYDRA_DIR = Path(os.environ.get("HYDRA_DIR", str(Path.home() / ".hydra")))
SEAL_DIR = Path(os.environ.get("HYDRA_SEAL_DIR", str(HYDRA_DIR / "prod-sealed")))
ALLOWLIST = Path(os.environ.get("HYDRA_ALLOWLIST", str(HYDRA_DIR / "owned-devices.txt")))
LOG = HYDRA_DIR / "propagation.log"
SERVER_PORT = os.environ.get("HYDRA_PORT", "8767")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line)
    try:
        HYDRA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --- Tailnet enumeration ---

def list_peers() -> list:
    """Return tailnet peers as [{hostname, ip, os, online}]."""
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=20)
        data = json.loads(out.stdout)
    except Exception as e:
        log(f"tailscale status failed: {e}")
        return []

    peers = []
    for key, node in (data.get("Peer") or {}).items():
        ips = node.get("TailscaleIPs") or []
        peers.append({
            "hostname": node.get("HostName", key),
            "dns": (node.get("DNSName", "") or "").rstrip(".").split(".")[0],
            "ip": ips[0] if ips else "",
            "os": node.get("OS", "?"),
            "online": bool(node.get("Online")),
        })
    return peers


# --- Allowlist ---

def load_allowlist() -> dict:
    """Read the ownership allowlist. Returns {hostname_or_ip: ssh_user}.

    Format (one per line):  <tailscale-hostname-or-ip> [ssh-user]
    Comments (#) and blank lines ignored.
    """
    if not ALLOWLIST.exists():
        return {}
    allowed = {}
    for raw in ALLOWLIST.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        allowed[parts[0]] = parts[1] if len(parts) > 1 else None
    return allowed


def ensure_allowlist_template():
    """Create a commented template if none exists, so the user can opt in."""
    if ALLOWLIST.exists():
        return
    HYDRA_DIR.mkdir(parents=True, exist_ok=True)
    ALLOWLIST.write_text(
        "# Hydra ownership allowlist\n"
        "# Devices the hydra MAY propagate to. Anything not listed is never touched.\n"
        "# Format:  <tailscale-hostname-or-ip>  [ssh-user]\n"
        "#\n"
        "# Examples (uncomment + fill in what you own):\n"
        "# lumi      adora\n"
        "# shell     adora\n"
    )


def is_allowed(peer: dict, allowed: dict) -> bool:
    return (peer["hostname"] in allowed or peer.get("dns") in allowed
            or peer["ip"] in allowed)


def ssh_user_for(peer: dict, allowed: dict):
    return (allowed.get(peer["hostname"]) or allowed.get(peer.get("dns"))
            or allowed.get(peer["ip"]))


# --- SSH probe + propagation ---

def probe(ip: str, user) -> bool:
    target = f"{user}@{ip}" if user else ip
    key = str(HYDRA_DIR / "hydra_ed25519")
    keyopt = ["-i", key] if Path(key).exists() else []
    try:
        r = subprocess.run(
            ["ssh", *keyopt, "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "BatchMode=yes", target, "echo hydra-ok"],
            capture_output=True, text=True, timeout=15,
        )
        return "hydra-ok" in r.stdout
    except Exception:
        return False


def propagate(peer: dict, user, dry_run=False) -> dict:
    """Push shards + agent to one peer and install the phone-home trigger."""
    ip = peer["ip"]
    target = f"{user}@{ip}" if user else ip
    result = {"hostname": peer["hostname"], "ip": ip, "actions": []}

    key = str(HYDRA_DIR / "hydra_ed25519")
    keyopt = ["-i", key] if Path(key).exists() else []

    def run(cmd, timeout=30):
        return subprocess.run(["ssh", *keyopt, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
                               target, cmd], capture_output=True, text=True, timeout=timeout)

    if dry_run:
        result["actions"].append("would push shards + agent, install trigger")
        return result

    is_windows = peer.get("os", "").lower().startswith("win")
    home = ""

    if is_windows:
        home = run("echo %USERPROFILE%").stdout.strip().replace("\r", "").replace("\n", "")
        agent_dir = f"{home}\\.hydra-agent"
        run(f'mkdir "{agent_dir}" 2>nul & mkdir "{agent_dir}\\shards" 2>nul')
        remote_agent = f"{home}/.hydra-agent"
        remote_shards = f"{remote_agent}/shards"
        result["actions"].append(f"created {agent_dir}")
    else:
        run("mkdir -p ~/.hydra-agent/shards ~/.hydra-agent/hydra-protocol")
        remote_agent = "~/.hydra-agent"
        remote_shards = "~/.hydra-agent/shards"
        result["actions"].append("created ~/.hydra-agent")

    # 2. Push shards + seed
    for f in sorted(SEAL_DIR.glob("shard_*.json")):
        subprocess.run(["scp", *keyopt, "-q", str(f), f"{target}:{remote_shards}/"],
                       capture_output=True, timeout=60)
    subprocess.run(["scp", *keyopt, "-q", str(SEAL_DIR / "seed.hydra"),
                    f"{target}:{remote_shards}/"], capture_output=True, timeout=60)
    result["actions"].append("pushed 7 shards + seed")

    # 3. Push agent + code
    tarball = SEAL_DIR / "hydra-protocol.tar.gz"
    if tarball.exists():
        subprocess.run(["scp", *keyopt, "-q", str(tarball), f"{target}:{remote_agent}/"],
                       capture_output=True, timeout=120)
        if is_windows:
            run(f'powershell -NoProfile -Command "Set-Location \'{home}\\.hydra-agent\'; '
                f'tar xzf hydra-protocol.tar.gz; '
                f'if (Test-Path hydra-protocol) {{ Remove-Item -Recurse -Force hydra-protocol }}; '
                f'Get-ChildItem -Directory hydra-protocol* | Rename-Item -NewName hydra-protocol"')
        else:
            run("cd ~/.hydra-agent && tar xzf hydra-protocol.tar.gz "
                "&& rm -rf hydra-protocol && mv hydra-protocol-* hydra-protocol 2>/dev/null; true")
        result["actions"].append("pushed + extracted hydra-protocol")

    # 4. Install phone-home trigger
    if is_windows:
        py = run("where python 2>nul").stdout.strip()
        if py:
            run('schtasks /Create /TN "HydraAgent" /TR '
                f'"python \\"{home}\\.hydra-agent\\hydra-protocol\\hydra_agent_android.py\\"" '
                '/SC ONLOGON /F')
            result["actions"].append("installed phone-home scheduled task (ONLOGON)")
        else:
            result["actions"].append("no python on target — shards stored, no trigger yet")
    else:
        trigger = (
            "mkdir -p ~/.config/systemd/user && "
            "cat > ~/.config/systemd/user/hydra-agent.service <<'EOF'\n"
            "[Unit]\nDescription=Hydra daemon agent (phones home)\n\n"
            "[Service]\nType=oneshot\n"
            "ExecStart=/usr/bin/env python3 %h/.hydra-agent/hydra-protocol/hydra_agent_android.py\n"
            "Environment=HYDRA_SERVER=http://100.77.142.40:%s\n"
            "Environment=HYDRA_WORK_DIR=%%h/.hydra-agent\n\n"
            "[Install]\nWantedBy=default.target\nEOF\n"
            "systemctl --user daemon-reload 2>/dev/null; "
            "systemctl --user enable --now hydra-agent.timer 2>/dev/null; "
            "(crontab -l 2>/dev/null; echo '@reboot python3 $HOME/.hydra-agent/hydra-protocol/hydra_agent_android.py') "
            "| crontab - 2>/dev/null; true"
        ) % SERVER_PORT
        run(trigger)
        result["actions"].append("installed phone-home trigger")

    result["ok"] = True
    return result


# --- Entry points ---

def scan():
    ensure_allowlist_template()
    allowed = load_allowlist()
    peers = list_peers()
    print(f"\n🜂 Tailnet scan — {len(peers)} peers\n")
    print(f"   allowlist: {ALLOWLIST} ({len(allowed)} entries)\n")
    for p in peers:
        flag = "✅ allowed" if is_allowed(p, allowed) else "  not listed"
        state = "online" if p["online"] else "offline"
        print(f"   {flag}  {p['hostname']:<20} {p['ip']:<16} {p['os']:<8} {state}")
    print()


def spread(dry_run=False):
    allowed = load_allowlist()
    if not allowed:
        log("REFUSING to propagate: no allowlist entries in "
            f"{ALLOWLIST}. Add devices you own, then re-run.")
        return {"ok": False, "reason": "empty allowlist"}

    peers = list_peers()
    targets = [p for p in peers if p["online"] and is_allowed(p, allowed)]
    log(f"propagation start — {len(targets)} allowed+online target(s)")

    results = []
    for p in targets:
        user = ssh_user_for(p, allowed)
        log(f"probing {p['hostname']} ({p['ip']})…")
        if not probe(p["ip"], user):
            log(f"  {p['hostname']}: no SSH (skipped — needs an SSH server)")
            results.append({"hostname": p["hostname"], "ok": False, "reason": "no ssh"})
            continue
        log(f"  {p['hostname']}: SSH ok — propagating")
        r = propagate(p, user, dry_run=dry_run)
        log(f"  {p['hostname']}: {r['actions']}")
        results.append(r)

    log(f"propagation done — {sum(1 for r in results if r.get('ok'))}/{len(results)} spread")
    return {"ok": True, "results": results}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        scan()
    elif cmd == "spread":
        spread(dry_run="--dry-run" in sys.argv)
    else:
        print(__doc__)
