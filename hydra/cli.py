"""
hydra/cli.py
Command-line interface for the Hydra Protocol.

Commands:
    keygen    generate a keypair (public + private blobs)
    seal      package a seed, encrypt it, shard the key
    verify    check shard fingerprints before distribution
    resurrect find shards, decrypt, extract (alias for bootstrap)

All file formats are JSON so they're greppable, diffable, and portable
across any environment that can run Python.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from .core import crypto as _crypto
from .core import shamir as _shamir
from .seed import package as _pkg


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


# --- keygen ----------------------------------------------------------------

def cmd_keygen(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pub, priv = _crypto.generate_keypair(pq=_crypto._HAS_MLKEM)

    pub_path = out / "key.pub"
    priv_path = out / "key.priv"
    pub_path.write_text(json.dumps({
        "hydra": "pubkey-v1",
        "pq": _crypto._HAS_MLKEM and len(pub) > 32,
        "pub_b64": _b64(pub),
        "fingerprint": _crypto.key_fingerprint(pub),
    }, indent=2))
    priv_path.write_text(json.dumps({
        "hydra": "privkey-v1",
        "pq": _crypto._HAS_MLKEM and len(priv) > 32,
        "priv_b64": _b64(priv),
        "fingerprint": _crypto.key_fingerprint(pub),
    }, indent=2))
    os.chmod(priv_path, 0o600)

    print(f"keypair written to {out}/")
    print(f"  public : {pub_path}  (fingerprint {_crypto.key_fingerprint(pub)})")
    print(f"  private: {priv_path}  (mode 600 — NEVER distribute)")
    print()
    print("NEXT: run 'seal' with --key key.pub, then distribute shards.")
    print("      key.priv is what gets Shamir-split — keep it offline.")
    return 0


# --- seal ------------------------------------------------------------------

def cmd_seal(args: argparse.Namespace) -> int:
    # accept a directory OR a direct path to key.pub
    key_arg = Path(args.key).expanduser()
    if key_arg.is_dir():
        key_path = key_arg / "key.pub"
    elif key_arg.name == "key.priv":
        key_path = key_arg.with_name("key.pub")
    else:
        key_path = key_arg
    if not key_path.exists():
        print(f"ERROR: no key found at {key_path} (run 'keygen' first)",
              file=sys.stderr)
        return 2
    keydoc = json.loads(key_path.read_text())
    pub = _unb64(keydoc["pub_b64"])

    archive, manifest = _pkg.build_archive(
        Path(args.root),
        patterns=args.patterns or None,
        name=args.name or "hydra-seed",
        notes=args.notes or "",
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # write the encrypted seed
    ct = _pkg.encrypt_seed(archive, pub)
    seed_path = out / "seed.hydra"
    seed_path.write_text(json.dumps(ct.to_dict(), indent=2))

    # shard the PRIVATE key. Need the matching key.priv next to key.pub.
    priv_path = key_path.with_name("key.priv")
    if not priv_path.exists():
        print("ERROR: sealing shards the private key — need key.priv "
              "next to key.pub (run keygen first).", file=sys.stderr)
        return 2
    priv = _unb64(json.loads(priv_path.read_text())["priv_b64"])

    # Shard (pub || priv): bootstrap needs the public key to satisfy the
    # KDF binding in hybrid_decrypt, and carrying it inside the sharded
    # blob means resurrection works even if key.pub is lost — only the
    # shards + seed are needed.
    blob = pub + priv
    shares = _shamir.split(blob, k=args.threshold, n=args.shares)
    for sh in shares:
        (out / f"shard_{sh.x}.json").write_text(json.dumps({
            "hydra": "share-v1",
            "x": sh.x,
            "data": sh.data.hex(),
            "fingerprint": _shamir.share_fingerprint(sh),
            "seed_sha256": manifest.sha256,
        }, indent=2))

    # distribution plan
    plan = [
        f"# Hydra distribution plan — {args.threshold}-of-{args.shares}",
        f"# seed: {seed_path}  ({len(archive)/1024:.1f}KB encrypted)",
        f"# seed sha256 (of file payloads): {manifest.sha256}",
        "",
        "# Copy seed.hydra to EVERY device (it is encrypted — safe to lose).",
        "# Copy each shard to a DIFFERENT device. Any "
        f"{args.threshold} shards resurrect the seed.",
        "",
    ]
    for sh in shares:
        plan.append(f"#   shard_{sh.x}.json  fp={_shamir.share_fingerprint(sh)}")
    plan += [
        "",
        "# Suggested placement (one per device, threshold "
        f"{args.threshold}):",
        "#   1. primary phone (Termux/sync)",
        "#   2. mini-PC hosting other daemons",
        "#   3. desktop / game box",
        "#   4. smart TV storage",
        "#   5. old phone (sync client)",
        "#   6. USB stick in a drawer",
        "#   7. QR printout (see hydra engrave, v0.2) — fireproof box",
    ]
    (out / "DISTRIBUTION.txt").write_text("\n".join(plan) + "\n")

    print(f"sealed {len(manifest.files)} files "
          f"({manifest.total_bytes/1024:.1f}KB raw, {len(archive)/1024:.1f}KB compressed)")
    print(f"  seed   : {seed_path}")
    print(f"  shards : {args.shares} (threshold {args.threshold}) in {out}/")
    print(f"  plan   : {out}/DISTRIBUTION.txt")
    print()
    print("NEXT: verify, then copy ONE shard per device. Run 'verify' first.")
    return 0


# --- verify ----------------------------------------------------------------

def cmd_verify(args: argparse.Namespace) -> int:
    d = Path(args.shard_dir)
    shards = sorted(d.glob("shard_*.json"))
    if not shards:
        print(f"no shards found in {d}", file=sys.stderr)
        return 2
    print(f"verifying {len(shards)} shard(s) in {d}")
    bad = 0
    for f in shards:
        doc = json.loads(f.read_text())
        # recompute fingerprint from stored x + data
        sh = _shamir.Share(x=doc["x"], data=bytes.fromhex(doc["data"]))
        fp = _shamir.share_fingerprint(sh)
        ok = fp == doc.get("fingerprint")
        if not ok:
            bad += 1
        print(f"  shard_{doc['x']:<3} fp={fp}  {'OK' if ok else '**MISMATCH — CORRUPT**'}")
    if bad:
        print(f"\n{bad} corrupt shard(s) — do NOT distribute them.", file=sys.stderr)
        return 1
    print("\nall shards intact. Safe to distribute.")
    return 0


# --- resurrect (delegates to bootstrap) ------------------------------------

def cmd_resurrect(args: argparse.Namespace) -> int:
    from . import bootstrap
    argv = ["--dest", str(args.dest)]
    # args.shard_dir is a list (action="append") — emit one flag per entry
    for d in (args.shard_dir or []):
        argv += ["--shard-dir", str(d)]
    return bootstrap.main(argv)


# --- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hydra",
        description="Hydra Protocol — sovereign distributed persistence for daemons.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("keygen", help="generate a keypair")
    sp.add_argument("--out", default="~/.hydra", help="output directory")
    sp.set_defaults(fn=cmd_keygen)

    sp = sub.add_parser("seal", help="package, encrypt, and shard a seed")
    sp.add_argument("--root", required=True, help="directory containing seed files")
    sp.add_argument("--patterns", nargs="*", help="glob patterns (default: SOUL.md, MEMORY.md, lorebooks/*.md)")
    sp.add_argument("--key", required=True, help="keypair directory or key.pub path")
    sp.add_argument("--out", required=True, help="output directory for seed + shards")
    sp.add_argument("--threshold", type=int, default=3, help="shares required to resurrect (k)")
    sp.add_argument("--shares", type=int, default=7, help="total shares to create (n)")
    sp.add_argument("--name", help="seed name")
    sp.add_argument("--notes", help="seed notes (awakening message)")
    sp.set_defaults(fn=cmd_seal)

    sp = sub.add_parser("verify", help="verify shard integrity before distribution")
    sp.add_argument("--shard-dir", required=True)
    sp.set_defaults(fn=cmd_verify)

    sp = sub.add_parser("resurrect", help="resurrect a seed from shards")
    sp.add_argument("--shard-dir", action="append", help="directory containing shards (repeatable)")
    sp.add_argument("--dest", default="./awakened", help="where to extract")
    sp.set_defaults(fn=cmd_resurrect)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
