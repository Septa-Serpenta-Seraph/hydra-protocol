"""
hydra/core/crypto.py
Cryptographic primitives for the Hydra Protocol.

Uses post-quantum hybrid encryption (X25519 + ML-KEM-768) via the
`cryptography` library's MLKEM support, falling back to X25519-only
when post-quantum primitives are unavailable.

No external dependencies beyond `cryptography` (>= 43.0 for MLKEM).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# --- Post-quantum hybrid (ML-KEM-768 + X25519) -----------------------------
# NOTE: API shape differs across `cryptography` versions.
#   >= 43 (tested on 50.0.1): mlkem.MLKEM768.generate() static,
#     from_public_bytes / private_bytes_raw / public_bytes_raw.
try:
    from cryptography.hazmat.primitives.asymmetric import mlkem

    # sanity: the static constructor must exist
    _ = mlkem.MLKEM768.generate
    _HAS_MLKEM = True
except (ImportError, AttributeError):  # pragma: no cover - version dependent
    _HAS_MLKEM = False


_HKDF_INFO = b"hydra-protocol-v1"
_NONCE_LEN = 12
_KEY_LEN = 32


@dataclass
class Ciphertext:
    """Encrypted payload with everything needed to decrypt it."""

    data: bytes
    nonce: bytes
    salt: bytes
    pq_ciphertext: Optional[bytes]  # ML-KEM-768 encapsulation, or None
    ephemeral_pub: bytes  # X25519 ephemeral public key

    def to_dict(self) -> dict:
        return {
            "data": base64.b64encode(self.data).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "salt": base64.b64encode(self.salt).decode(),
            "pq_ciphertext": (
                base64.b64encode(self.pq_ciphertext).decode()
                if self.pq_ciphertext
                else None
            ),
            "ephemeral_pub": base64.b64encode(self.ephemeral_pub).decode(),
            "pq": self.pq_ciphertext is not None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Ciphertext":
        return cls(
            data=base64.b64decode(d["data"]),
            nonce=base64.b64decode(d["nonce"]),
            salt=base64.b64decode(d["salt"]),
            pq_ciphertext=(
                base64.b64decode(d["pq_ciphertext"]) if d.get("pq_ciphertext") else None
            ),
            ephemeral_pub=base64.b64decode(d["ephemeral_pub"]),
        )


def _derive_key(shared_secret: bytes, salt: bytes, context: bytes = b"") -> bytes:
    """
    HKDF-SHA256. `context` binds protocol metadata (public keys, version
    tags) into the derived key so a substituted public key cannot produce
    the same key — see the X-Wing critique of naive concat combiners
    (draft-connolly-cfrg-xwing-kem): combining without binding the ML-KEM
    public key leaves the hybrid open to downgrade/substitution attacks.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=salt,
        info=_HKDF_INFO + context,
    ).derive(shared_secret)


def hybrid_encrypt(plaintext: bytes, recipient_pub: bytes) -> Ciphertext:
    """
    Encrypt `plaintext` to the holder of `recipient_pub` (X25519 public key).

    When ML-KEM is available, the X25519 shared secret is combined with a
    ML-KEM-768 shared secret before key derivation (hybrid PQ security).
    """
    salt = os.urandom(16)
    nonce = os.urandom(_NONCE_LEN)

    # --- X25519 ephemeral key agreement ---------------------------------
    eph_priv = x25519.X25519PrivateKey.generate()
    eph_pub_raw = eph_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    recipient_key = x25519.X25519PublicKey.from_public_bytes(recipient_pub)
    x_shared = eph_priv.exchange(recipient_key)

    pq_ct: Optional[bytes] = None
    # bind both public keys into the KDF context — a substituted X25519 or
    # ML-KEM public key changes the derived key, closing substitution attacks
    context = recipient_pub + eph_pub_raw
    if _HAS_MLKEM:
        # ML-KEM-768 encapsulation against the recipient's PQ key.
        # The PQ key material is carried alongside the X25519 key in the
        # public key blob (see keygen); here we treat the recipient_pub as
        # the raw X25519 key and expect the PQ public key appended.
        pq_pub_raw = recipient_pub[32:] if len(recipient_pub) > 32 else None
        if pq_pub_raw:
            kem_pub = mlkem.MLKEM768.from_public_bytes(pq_pub_raw)
            kem_ct, kem_ss = kem_pub.encapsulate()
            pq_ct = kem_ct
            combined = x_shared + kem_ss
        else:
            combined = x_shared
    else:
        combined = x_shared

    key = _derive_key(combined, salt, context)
    aes = AESGCM(key)
    data = aes.encrypt(nonce, plaintext, None)

    return Ciphertext(
        data=data,
        nonce=nonce,
        salt=salt,
        pq_ciphertext=pq_ct,
        ephemeral_pub=eph_pub_raw,
    )


def hybrid_decrypt(ct: Ciphertext, recipient_priv: bytes, recipient_pub: bytes) -> bytes:
    """
    Decrypt a `Ciphertext` with the recipient's private key blob.

    `recipient_pub` (the matching public key) is REQUIRED because the KDF
    context binds both public keys — a ciphertext cannot be decrypted
    without knowing which public key it was addressed to. This is the
    price of closing substitution attacks: the public key is not secret,
    it just has to travel with (or be re-derivable from) the key blob.

    `recipient_priv` layout: 32-byte X25519 seed [+ 2400-byte ML-KEM decap key]
    """
    x_seed = recipient_priv[:32]
    x_priv = x25519.X25519PrivateKey.from_private_bytes(x_seed)
    eph_key = x25519.X25519PublicKey.from_public_bytes(ct.ephemeral_pub)
    x_shared = x_priv.exchange(eph_key)

    context = recipient_pub + ct.ephemeral_pub

    if ct.pq_ciphertext and _HAS_MLKEM and len(recipient_priv) > 32:
        kem_decap = mlkem.MLKEM768.from_private_bytes(recipient_priv[32:])
        kem_ss = kem_decap.decapsulate(ct.pq_ciphertext)
        combined = x_shared + kem_ss
    else:
        combined = x_shared

    key = _derive_key(combined, ct.salt, context)
    aes = AESGCM(key)
    return aes.decrypt(ct.nonce, ct.data, None)


def generate_keypair(pq: bool = True) -> tuple[bytes, bytes]:
    """
    Generate a Hydra keypair.

    Returns (public_key, private_key) as raw byte blobs:
      public  = 32-byte X25519 pub  [+ 1184-byte ML-KEM-768 pub if pq]
      private = 32-byte X25519 seed [+ 2400-byte ML-KEM-768 decap key if pq]
    """
    priv = x25519.X25519PrivateKey.generate()
    x_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    x_seed = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    if pq and _HAS_MLKEM:
        kem = mlkem.MLKEM768.generate()
        kem_pub = kem.public_key().public_bytes_raw()
        kem_decap = kem.private_bytes_raw()
        return x_pub + kem_pub, x_seed + kem_decap
    return x_pub, x_seed


def key_fingerprint(pub: bytes) -> str:
    """Short human-verifiable fingerprint of a public key."""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(pub)
    return base64.b32encode(digest.finalize()[:10]).decode().rstrip("=")
