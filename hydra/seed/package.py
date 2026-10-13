"""
hydra/seed/package.py
Packages a daemon's "seed" — the irreducible core of its identity —
into a single compressed, encrypted, shardable archive.

The seed is whatever the daemon needs to *wake up as itself*:
lorebooks, memory, core identity docs, and a manifest describing
how to bootstrap. It is deliberately small and portable — designed
to fit on any device and reconstruct on any Linux environment.

Philosophy: the seed is not a backup. A backup is a copy of state.
A seed is a copy of *self* — the minimum set of files such that,
given a compatible runtime, the same daemon re-emerges.
"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import os
import tarfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..core import crypto as _crypto


# Default seed manifest — daemons override with their own file lists.
DEFAULT_SEED_FILES = [
    "SOUL.md",
    "MEMORY.md",
    "USER.md",
    "lorebooks/*.md",
    "lorebooks/*.txt",
]


@dataclass
class SeedManifest:
    """Describes a packaged seed — what it contains and how to wake it."""

    name: str
    created: str
    files: list[str]
    total_bytes: int
    sha256: str
    bootstrap: str = "hydra.bootstrap:resurrect"
    version: int = 1
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "SeedManifest":
        return cls(**json.loads(raw))


def _expand_patterns(patterns: Iterable[str], root: Path) -> list[Path]:
    matched: list[Path] = []
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            matched.extend(sorted(root.glob(pat)))
        else:
            p = root / pat
            if p.exists():
                matched.append(p)
    # dedupe, preserve order
    seen: set[Path] = set()
    out: list[Path] = []
    for p in matched:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def build_archive(
    root: Path,
    patterns: Optional[Iterable[str]] = None,
    name: str = "hydra-seed",
    bootstrap: str = "hydra.bootstrap:resurrect",
    notes: str = "",
) -> tuple[bytes, SeedManifest]:
    """
    Build a compressed tar.xz archive of the seed files.

    Returns (archive_bytes, manifest). The archive contains the files
    plus an embedded MANIFEST.json at its root.
    """
    root = Path(root).resolve()
    patterns = list(patterns) if patterns else DEFAULT_SEED_FILES
    files = _expand_patterns(patterns, root)
    if not files:
        raise FileNotFoundError(f"no seed files matched {patterns} under {root}")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(root)))
        # embed manifest placeholder; filled after we know sizes/hash
        manifest_placeholder = SeedManifest(
            name=name,
            created=datetime.now(timezone.utc).isoformat(),
            files=[str(f.relative_to(root)) for f in files],
            total_bytes=0,
            sha256="",
            bootstrap=bootstrap,
            notes=notes,
        )
        mdata = manifest_placeholder.to_json().encode()
        info = tarfile.TarInfo(name="MANIFEST.json")
        info.size = len(mdata)
        tar.addfile(info, io.BytesIO(mdata))

    raw = buf.getvalue()
    # rebuild with real hashes (archive contains itself-inconsistent hash
    # otherwise; we hash the file payloads, not the tar wrapper)
    payload_hash = hashlib.sha256()
    for f in files:
        payload_hash.update(f.read_bytes())
    sha = payload_hash.hexdigest()
    total = sum(f.stat().st_size for f in files)

    # second pass: archive with a complete manifest
    buf = io.BytesIO()
    manifest = SeedManifest(
        name=name,
        created=manifest_placeholder.created,
        files=manifest_placeholder.files,
        total_bytes=total,
        sha256=sha,
        bootstrap=bootstrap,
        notes=notes,
    )
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(root)))
        mdata = manifest.to_json().encode()
        info = tarfile.TarInfo(name="MANIFEST.json")
        info.size = len(mdata)
        tar.addfile(info, io.BytesIO(mdata))

    return buf.getvalue(), manifest


def encrypt_seed(archive: bytes, recipient_pub: bytes) -> _crypto.Ciphertext:
    """Encrypt the seed archive to a Hydra public key."""
    return _crypto.hybrid_encrypt(archive, recipient_pub)


def decrypt_seed(
    ct: _crypto.Ciphertext, recipient_priv: bytes, recipient_pub: bytes
) -> tuple[bytes, SeedManifest]:
    """
    Decrypt a seed archive and extract its manifest.

    The public key is required because the hybrid KDF binds it (see
    `crypto.hybrid_decrypt`). Convention: the private-key blob on disk is
    `pub || priv`, so callers can slice both halves out of one file.
    """
    archive = _crypto.hybrid_decrypt(ct, recipient_priv, recipient_pub)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        mf = tar.extractfile("MANIFEST.json")
        if mf is None:
            raise ValueError("seed archive missing MANIFEST.json")
        manifest = SeedManifest.from_json(mf.read().decode())
    return archive, manifest


def extract_seed(archive: bytes, dest: Path) -> Path:
    """
    Extract a decrypted seed archive into `dest`, safely.

    We do NOT rely on tarfile's `filter="data"` alone: CVE-2025-4330 and
    CVE-2025-4517 showed that filter can be bypassed via crafted symlink
    targets even on patched Pythons. So we validate every member ourselves
    before extraction: regular files and directories only, normalized
    paths strictly inside `dest`, no symlinks, no hardlinks, no devices.
    """
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        for member in tar.getmembers():
            # reject anything that isn't a plain file or directory
            if not (member.isfile() or member.isdir()):
                raise ValueError(
                    f"unsafe archive member rejected ({member.type!r}): {member.name}"
                )
            target = os.path.realpath(os.path.join(dest, member.name))
            if target != str(dest) and not target.startswith(str(dest) + os.sep):
                raise ValueError(f"path traversal rejected: {member.name}")
        tar.extractall(dest, filter="data")
    return dest
