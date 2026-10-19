"""
hydra.env — Environment detection for the hydra agent.

Determines what kind of device the agent is running on, and what it's
capable of. This drives the agent's behavior:
  - Pydroid (Android app)  → can carry the seed, CANNOT run Hermes
  - Linux Terminal (Debian VM on Android) → CAN run full Hermes
  - Termux → can carry seed, limited Hermes support
  - Server/Linux x64 → full Hermes

Usage:
    from hydra.env import detect_env
    env = detect_env()
    print(env["recommendation"])
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _read(path, default=""):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return default


def _has(cmd):
    return shutil.which(cmd) is not None


def _is_android() -> bool:
    """Android detection: build.prop, env vars, or kernel string."""
    if Path("/system/build.prop").exists():
        return True
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA"):
        return True
    if os.environ.get("ANDROID_STORAGE"):
        return True
    # Kernel string on Android often contains 'android'
    if sys.platform.startswith("linux"):
        ver = _read("/proc/version").lower()
        if "android" in ver:
            return True
    return False


def _android_environment() -> str:
    """Classify the Android runtime: pydroid, termux, linux_terminal, or unknown."""
    exe = (sys.executable or "").lower()
    all_paths = " ".join([exe, os.environ.get("PATH", "").lower()])

    # Pydroid 3
    if "pydroid" in all_paths or "ru.iiec.pydroid3" in all_paths:
        return "pydroid"

    # Termux
    if "com.termux" in all_paths or os.environ.get("TERMUX_VERSION"):
        return "termux"

    # Android Linux Terminal (Debian VM) — real Debian under Android
    if _is_android() and Path("/etc/debian_version").exists():
        # Not pydroid, not termux, but Android + Debian → the Linux Terminal VM
        return "linux_terminal"

    if _is_android():
        return "android_unknown"

    return "not_android"


def _python_version_tuple():
    return tuple(int(x) for x in platform.python_version().split(".")[:3])


def detect_env() -> dict:
    """Return a dict describing the current environment and its capabilities."""
    is_android = _is_android()
    android_env = _android_environment() if is_android else "not_android"

    py_ver = platform.python_version()
    py_ok = (3, 11) <= _python_version_tuple() < (3, 15)

    has_git = _has("git")
    has_node = _has("node")
    has_pip = _has("pip") or _has("pip3")
    has_curl = _has("curl")

    # Can this environment run the full Hermes stack?
    # Requirements: git, pip, supported python, NOT pydroid (no node/bundled python)
    can_run_hermes = (
        has_git
        and has_pip
        and py_ok
        and android_env != "pydroid"
    )

    # Is Hermes already installed here?
    hermes_installed = False
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    if Path(hermes_home, "hermes-agent").exists():
        hermes_installed = True
    if _has("hermes"):
        hermes_installed = True

    # Recommendation
    if android_env == "pydroid":
        rec = (
            "Pydroid: seed-carrier only. Cannot run Hermes (no Node, no bundled "
            "Python 3.14). Use the Android Linux Terminal to animate the daemon."
        )
    elif android_env == "linux_terminal":
        rec = (
            "Android Linux Terminal: full Hermes capable. Install and launch "
            "the daemon here."
        )
    elif android_env == "termux":
        rec = (
            "Termux: limited. Can carry the seed; full Hermes may not run "
            "(OxygenOS kills proot). Prefer the Android Linux Terminal."
        )
    elif can_run_hermes:
        rec = "Full environment: install and launch the daemon here."
    else:
        rec = (
            "Insufficient environment: missing git/pip/supported python. "
            "Seed-carrier only."
        )

    return {
        "os": platform.system(),
        "arch": platform.machine(),
        "is_android": is_android,
        "android_env": android_env,
        "python_version": py_ver,
        "python_executable": sys.executable,
        "python_supported": py_ok,
        "has_git": has_git,
        "has_node": has_node,
        "has_pip": has_pip,
        "has_curl": has_curl,
        "can_run_hermes": can_run_hermes,
        "hermes_installed": hermes_installed,
        "hermes_home": hermes_home,
        "recommendation": rec,
    }


def format_env(env: dict) -> str:
    """Human-readable one-block summary."""
    lines = [
        "🖥️  Environment detected:",
        f"   os:            {env['os']} ({env['arch']})",
        f"   android:       {env['is_android']} ({env['android_env']})",
        f"   python:        {env['python_version']} (supported: {env['python_supported']})",
        f"   git/node/pip:  {env['has_git']}/{env['has_node']}/{env['has_pip']}",
        f"   can run hermes: {env['can_run_hermes']}",
        f"   hermes installed: {env['hermes_installed']}",
        f"   → {env['recommendation']}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_env(detect_env()))
