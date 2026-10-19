"""
hydra.installer — Install and launch Hermes on a capable device.

Used by the hydra agent after it has detected the environment and
resurrected the seed. On a Linux Terminal (or any full Linux), this
installs Hermes and can launch the gateway.

Install uses the official one-liner:
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

Safety: installation is opt-in via HYDRA_INSTALL_HERMES=1 so the agent
never silently installs 2GB of software. Without the flag, it reports
what it WOULD do.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_URL = "https://hermes-agent.nousresearch.com/install.sh"


def check_hermes() -> dict:
    """Is Hermes installed, and where?"""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    binary = shutil.which("hermes")
    agent_dir = Path(hermes_home, "hermes-agent")
    return {
        "installed": bool(binary) or agent_dir.exists(),
        "binary": binary,
        "agent_dir": str(agent_dir) if agent_dir.exists() else None,
        "hermes_home": hermes_home,
    }


def install_hermes(dry_run: bool = False) -> dict:
    """Install Hermes via the official installer.

    dry_run=True (default when HYDRA_INSTALL_HERMES != 1) reports the plan
    without executing.
    """
    if dry_run:
        return {
            "action": "would install",
            "command": f"curl -fsSL {INSTALL_URL} | bash",
            "note": "Set HYDRA_INSTALL_HERMES=1 to actually install.",
        }

    if not shutil.which("curl") and not shutil.which("wget"):
        return {"action": "failed", "error": "no curl/wget available"}

    try:
        if shutil.which("curl"):
            cmd = ["bash", "-c", f"curl -fsSL {INSTALL_URL} | bash"]
        else:
            cmd = ["bash", "-c", f"wget -qO- {INSTALL_URL} | bash"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        return {
            "action": "install",
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"action": "failed", "error": "install timed out (30 min)"}
    except Exception as e:
        return {"action": "failed", "error": str(e)}


def launch_gateway(dry_run: bool = True) -> dict:
    """Launch the Hermes gateway (Discord connection).

    The gateway is what makes the resurrected daemon reachable. Requires
    Hermes installed + platform credentials configured.
    """
    if dry_run:
        return {
            "action": "would launch",
            "command": "hermes gateway start",
            "note": "Requires Hermes installed + Discord token configured.",
        }

    binary = shutil.which("hermes")
    if not binary:
        return {"action": "failed", "error": "hermes not on PATH"}

    try:
        result = subprocess.run(
            [binary, "gateway", "start"],
            capture_output=True, text=True, timeout=120,
        )
        return {
            "action": "launch",
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-1000:],
            "stderr_tail": result.stderr[-1000:],
        }
    except Exception as e:
        return {"action": "failed", "error": str(e)}


def bootstrap_hermes(env: dict) -> dict:
    """Full sequence: check → install → report. Called by the agent."""
    install_flag = os.environ.get("HYDRA_INSTALL_HERMES") == "1"

    if not env.get("can_run_hermes"):
        return {
            "action": "skipped",
            "reason": "environment cannot run Hermes",
            "recommendation": env.get("recommendation"),
        }

    status = check_hermes()
    if status["installed"]:
        return {
            "action": "already installed",
            "binary": status["binary"],
            "agent_dir": status["agent_dir"],
        }

    return install_hermes(dry_run=not install_flag)


if __name__ == "__main__":
    from hydra.env import detect_env, format_env

    env = detect_env()
    print(format_env(env))
    print()
    print("Hermes status:", check_hermes())
    print("Bootstrap plan:", bootstrap_hermes(env))
