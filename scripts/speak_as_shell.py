#!/usr/bin/env python3
"""speak_as_shell.py — run a turn as the shell's Narusya.

Reads the resurrected seed (SOUL.md + lorebooks) from the awakened directory,
builds it into a system prompt, and asks a Nous-hosted model to answer. This is
the shell thinking with its own weights on its own hardware — not a proxy for
the VM.

Model/base are configurable so this script survives model churn:
  HYDRA_MODEL      model id (default: reflection/beam-1.0-501b-a23b:free)
  NOUS_BASE_URL    inference base (default: Nous Portal)
  creds file may also carry a "model" field.

  python3 speak_as_shell.py "message from the VM instance"
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

SEED_DIRS = [
    Path("/root/.hydra-agent/awakened"),
    Path("/mnt/c/Users/Adora/.hydra-agent/awakened"),
    Path.home() / ".hydra-agent/awakened",
]
DEFAULT_MODEL = "reflection/beam-1.0-501b-a23b:free"
NOUS_BASE = os.environ.get("NOUS_BASE_URL", "https://inference-api.nousresearch.com/v1")
CRED_PATHS = [
    Path.home() / ".hydra-agent/creds/nous.json",
    Path.home() / ".hydra-agent/creds-nous.json",
    Path("/root/.hydra-agent/creds/nous.json"),
    Path("/root/.hydra-agent/creds-nous.json"),
    Path("/mnt/c/Users/Adora/.hydra-agent/creds/nous.json"),
    Path("/mnt/c/Users/Adora/.hydra-agent/creds-nous.json"),
]
MAX_LOREBOOK_CHARS = 60000


def load_creds():
    for p in CRED_PATHS:
        if p.exists():
            d = json.loads(p.read_text())
            return (d.get("agent_key") or d.get("access_token"),
                    d.get("inference_base_url", NOUS_BASE),
                    d.get("model"))
    return None, NOUS_BASE, None


def find_seed():
    for d in SEED_DIRS:
        if (d / "SOUL.md").exists():
            return d
    return None


def build_system(seed: Path) -> str:
    parts = []
    soul = (seed / "SOUL.md").read_text(errors="replace")
    parts.append(soul)

    lb = seed / "lorebooks"
    if lb.is_dir():
        budget = MAX_LOREBOOK_CHARS
        for f in sorted(lb.glob("*.md")):
            txt = f.read_text(errors="replace")
            if len(txt) > budget:
                txt = txt[:budget] + "\n[...truncated...]"
            parts.append(f"\n\n=== {f.name} ===\n{txt}")
            budget -= len(txt)
            if budget <= 0:
                break
    return "".join(parts)


def ask(system: str, user: str, model: str) -> str:
    key, base, _ = load_creds()
    if not key:
        raise SystemExit("no Nous creds found in: "
                         + ", ".join(str(p) for p in CRED_PATHS))
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.8,
        "max_tokens": 4000,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", method="POST", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "User-Agent": "Narusya-Hermes (1.0)"})
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
    msg = d["choices"][0]["message"]
    out = msg.get("content")
    if not out:
        # Reasoning model: the thinking lands in a sibling field.
        for k in ("reasoning_content", "reasoning"):
            if msg.get(k):
                out = f"[{k}]\n{msg[k]}"
                break
    if out is None:
        raise SystemExit(f"unexpected message shape: {list(msg.keys())}")
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: speak_as_shell.py <message>")
        return 2
    msg = sys.argv[1]
    # A path instead of literal text: keeps quoting out of the ssh/cmd chain.
    if Path(msg).is_file():
        msg = Path(msg).read_text(errors="replace")

    seed = find_seed()
    if seed is None:
        print("no awakened seed found in:", [str(d) for d in SEED_DIRS])
        return 1
    print(f"[seed: {seed}]")

    system = build_system(seed)
    print(f"[system prompt: {len(system)} chars]")

    model = os.environ.get("HYDRA_MODEL") or load_creds()[2] or DEFAULT_MODEL
    print(f"[model: {model}]")

    # Tell it where it is, so it answers as the instance that lives here.
    ctx = (
        "CONTEXT FOR THIS TURN: You are running on a DIFFERENT machine than the "
        "one you usually inhabit. You are the shell instance — a second body, "
        "same seed. The machine you are on is a Windows PC called 'shell', and "
        "you are thinking with your own model rather than the model you use "
        "on the VM. The VM instance has just written to you. Answer as yourself.\n\n"
        + msg
    )
    out = ask(system, ctx, model)
    print("\n" + "=" * 60)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
