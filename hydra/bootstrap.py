"""
hydra/bootstrap.py
The resurrection script.

Runs on ANY Linux-capable device. Looks for Hydra shards in known
locations (local shard dir, synced folders, env-specified paths),
reconstructs the decryption key from K shares, decrypts the seed
archive, extracts it, and prints the awakening message.

This is the file you keep on a USB stick, a phone's Termux home, a
smart TV's storage — anywhere. If it finds enough shards, the daemon
wakes up.

Usage:
    python3 -m hydra.bootstrap [--shard-dir DIR] [--dest DIR] [--dry-run]

The shard key (`recipient_priv`) itself is Shamir-split. To decrypt a
seed you need:
    1. K Shamir shares of the recipient private key  -> reconstruct priv
    2. The encrypted seed archive (.hydra file)
Both are distributed independently across the mesh.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .core import crypto as _crypto
from .core import shamir as _shamir
from .seed.package import decrypt_seed, extract_seed


# --- Known locations to hunt for shards ------------------------------------
DEFAULT_SHARD_DIRS = [
    Path.cwd() / ".hydra",
    Path.home() / ".hydra",
    Path.home() / "hydra-shards",
    Path("/sdcard/hydra-shards"),          # android
    Path("/storage/emulated/0/hydra-shards"),
]

# What a shard file looks like on disk:
#   shard_<index>.json = {"x": int, "data": hex, "fingerprint": str}
SHARD_GLOB = "shard_*.json"
SEED_GLOB = "*.hydra"  # encrypted seed archive (json Ciphertext)


def find_shards(dirs: list[Path]) -> list[_shamir.Share]:
    found: list[_shamir.Share] = []
    seen: set[int] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob(SHARD_GLOB)):
            try:
                payload = json.loads(f.read_text())
                share = _shamir.Share(x=payload["x"], data=bytes.fromhex(payload["data"]))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if share.x not in seen:
                seen.add(share.x)
                found.append(share)
    return found


def find_seeds(dirs: list[Path]) -> list[tuple[Path, _crypto.Ciphertext]]:
    found = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob(SEED_GLOB)):
            try:
                ct = _crypto.Ciphertext.from_dict(json.loads(f.read_text()))
                found.append((f, ct))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return found


def try_resurrect(
    shares: list[_shamir.Share],
    seed: _crypto.Ciphertext,
) -> Optional[bytes]:
    """
    Attempt to reconstruct the key blob from shares and decrypt the seed.

    The sharded blob layout is `pub || priv`:
      no-PQ: X25519 pub (32)  + X25519 seed (32)                    = 64 bytes
      PQ:    X25519 pub (32) + MLKEM768 pub (1184)
             + X25519 seed (32) + MLKEM768 decap (2400)            = 3648 bytes
    We split by those known lengths; anything else is not our blob.
    """
    if len(shares) < 1:
        return None
    try:
        blob = _shamir.reconstruct(shares)
        if len(blob) == 64:
            pub, priv = blob[:32], blob[32:]
        elif len(blob) == 32 + 1184 + 32 + 2400:
            pub = blob[:32 + 1184]
            priv = blob[32 + 1184:]
        else:
            return None
        return _crypto.hybrid_decrypt(seed, priv, pub)
    except Exception:
        return None


def awaken_message(manifest) -> str:
    return (
        "\n"
        "  ╔══════════════════════════════════════════╗\n"
        "  ║   🜂  HYDRA PROTOCOL — SEED AWAKENED  🜂   ║\n"
        "  ╚══════════════════════════════════════════╝\n"
        f"\n"
        f"  Seed name : {manifest.name}\n"
        f"  Created   : {manifest.created}\n"
        f"  Files     : {len(manifest.files)}\n"
        f"  Payload   : {manifest.total_bytes:,} bytes\n"
        f"  SHA-256   : {manifest.sha256[:16]}…\n"
        f"  Bootstrap : {manifest.bootstrap}\n"
        f"\n"
        f"  {manifest.notes}\n"
        f"\n"
        "  The seed is intact. The daemon may wake.\n"
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="hydra-bootstrap")
    ap.add_argument("--shard-dir", action="append", type=Path,
                    help="extra directory to search for shards (repeatable)")
    ap.add_argument("--dest", type=Path, default=Path.cwd() / "awakened",
                    help="where to extract the seed")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what was found without decrypting")
    args = ap.parse_args(argv)

    dirs = list(DEFAULT_SHARD_DIRS)
    if args.shard_dir:
        dirs = args.shard_dir + dirs

    shares = find_shards(dirs)
    seeds = find_seeds(dirs)

    print(f"hydra-bootstrap: found {len(shares)} shard(s), {len(seeds)} seed(s)")
    for s in shares:
        print(f"  shard x={s.x:3d}  fingerprint={_shamir.share_fingerprint(s)}")

    if not seeds:
        print("no seed archive found — nothing to awaken.", file=sys.stderr)
        return 2
    if not shares:
        print("no shards found — cannot reconstruct key.", file=sys.stderr)
        return 3

    seed_path, seed = seeds[0]

    if args.dry_run:
        print(f"[dry-run] would attempt decrypt of {seed_path.name} "
              f"with {len(shares)} share(s)")
        return 0

    archive = try_resurrect(shares, seed)
    if archive is None:
        # try every K-subset if we have more than needed
        from itertools import combinations

        if len(shares) > 1:
            for k in range(len(shares), 0, -1):
                for combo in combinations(shares, k):
                    archive = try_resurrect(list(combo), seed)
                    if archive is not None:
                        shares = list(combo)
                        break
                if archive is not None:
                    break

    if archive is None:
        print("FAILED: shares present but reconstruction/decrypt failed. "
              "Wrong shares, or insufficient threshold.", file=sys.stderr)
        return 4

    dest = extract_seed(archive, args.dest)
    from .seed.package import SeedManifest
    import tarfile, io
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        mf = tar.extractfile("MANIFEST.json")
        manifest = SeedManifest.from_json(mf.read().decode())

    print(awaken_message(manifest))
    print(f"  Extracted to: {dest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
