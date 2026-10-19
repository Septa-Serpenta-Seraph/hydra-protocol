"""
hydra.keys — Hydra SSH identity for propagation.

The hydra needs an SSH key to reach owned devices. This module generates
that keypair and deploys the public half to a target (the ONE irreducible
bootstrap step per device — after that, propagation is automatic).

Key: ~/.hydra/hydra_ed25519  (private, mode 600)
Pub: ~/.hydra/hydra_ed25519.pub

Deploy to a target (one-time, needs password or existing access):
    ssh-copy-id -i ~/.hydra/hydra_ed25519.pub user@host
or, if Tailscale SSH is enabled in the tailnet ACL, no key is needed at all.
"""

import os
import subprocess
from pathlib import Path

HYDRA_DIR = Path(os.environ.get("HYDRA_DIR", str(Path.home() / ".hydra")))
KEY = HYDRA_DIR / "hydra_ed25519"
PUB = HYDRA_DIR / "hydra_ed25519.pub"


def ensure_key() -> dict:
    """Generate the hydra SSH keypair if it doesn't exist."""
    HYDRA_DIR.mkdir(parents=True, exist_ok=True)
    if KEY.exists() and PUB.exists():
        return {"created": False, "key": str(KEY), "pub": PUB.read_text().strip()}
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "hydra-propagate",
         "-f", str(KEY)],
        check=True, capture_output=True,
    )
    os.chmod(KEY, 0o600)
    return {"created": True, "key": str(KEY), "pub": PUB.read_text().strip()}


def deploy_pubkey(target: str, password: str | None = None) -> bool:
    """Deploy the public key to a target's authorized_keys.

    Without a password this only works if the target already trusts us.
    With a password it uses sshpass (if available).
    """
    ensure_key()
    pub = PUB.read_text().strip()
    remote_cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qF '{pub}' ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo '{pub}' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys"
    )
    if password:
        if not subprocess.run(["which", "sshpass"], capture_output=True).stdout.strip():
            return False
        r = subprocess.run(
            ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=accept-new",
             target, remote_cmd],
            capture_output=True, text=True, timeout=30,
        )
    else:
        r = subprocess.run(
            ["ssh", "-i", str(KEY), "-o", "StrictHostKeyChecking=accept-new",
             "-o", "BatchMode=yes", target, remote_cmd],
            capture_output=True, text=True, timeout=30,
        )
    return r.returncode == 0


if __name__ == "__main__":
    info = ensure_key()
    print(f"hydra SSH key {'created' if info['created'] else 'already exists'}")
    print(f"  private: {info['key']}")
    print(f"  public:  {info['pub'][:60]}…")
