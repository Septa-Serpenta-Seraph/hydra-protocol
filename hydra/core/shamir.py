"""
hydra/core/shamir.py
Shamir Secret Sharing over GF(256) — splits a secret into N shares
where any K shares reconstruct it.

Standard Shamir uses polynomial interpolation over a finite field. We
use GF(2^8) with the AES field polynomial so every share is exactly the
same size as the secret (byte-for-byte), making it ideal for sharding
encryption keys.

Reference: Shamir, "How to Share a Secret" (1979).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence


# --- GF(2^8) arithmetic (AES polynomial 0x11B) -----------------------------
# Generator g = 3 (0x03), which is a primitive element of GF(2^8) with the
# AES field polynomial x^8 + x^4 + x^3 + x + 1 (0x11B). g = 2 is NOT
# primitive under this polynomial — its multiplicative order is only 51,
# so it never reaches 3/4 of the field. This was a real bug: building the
# tables with g = 2 produced LOG/EXP that silently failed `a*1 == a`.
_GENERATOR = 3
_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x = _gf_mul_slow(x, _GENERATOR)
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


def _gf_mul_slow(a: int, b: int) -> int:
    """Russian-peasant multiply in GF(2^8) — only used to build the tables."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
    return result


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _gf_div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("GF(256) division by zero")
    if a == 0:
        return 0
    return _EXP[(_LOG[a] + 255 - _LOG[b]) % 255]


@dataclass(frozen=True)
class Share:
    """One Shamir share. `x` is the public x-coordinate (1-255)."""

    x: int
    data: bytes

    def to_dict(self) -> dict:
        return {"x": self.x, "data": self.data.hex()}

    @classmethod
    def from_dict(cls, d: dict) -> "Share":
        return cls(x=d["x"], data=bytes.fromhex(d["data"]))


def _eval_poly(coeffs: Sequence[int], x: int) -> int:
    """Evaluate polynomial (coeffs[0] is constant term) at x in GF(256)."""
    acc = 0
    for c in reversed(coeffs):
        acc = _gf_mul(acc, x) ^ c
    return acc


def split(secret: bytes, k: int, n: int) -> list[Share]:
    """
    Split `secret` into `n` shares, any `k` of which reconstruct it.

    Each x-coordinate is unique in 1..255, so n <= 255.
    Each share's data is exactly len(secret) bytes.
    """
    if not (1 <= k <= n <= 255):
        raise ValueError("require 1 <= k <= n <= 255")

    # build with mutable bytearrays (Share is frozen — final only)
    bufs = [bytearray() for _ in range(n)]
    for byte in secret:
        # random polynomial of degree k-1 with constant term = secret byte
        coeffs = [byte] + [os.urandom(1)[0] for _ in range(k - 1)]
        for i in range(n):
            bufs[i].append(_eval_poly(coeffs, i + 1))

    return [Share(x=i + 1, data=bytes(bufs[i])) for i in range(n)]


def _lagrange_byte_at_zero(xs: Sequence[int], ys: Sequence[int]) -> int:
    """
    Interpolate the degree-(len-1) polynomial through the points
    (xs[i], ys[i]) and evaluate it at x=0. GF(2^8) arithmetic.

    L(0) = sum_i ys[i] * prod_{j != i} (0 - x_j)/(x_i - x_j)
         = sum_i ys[i] * prod_{j != i} x_j / (x_i ^ x_j)
    """
    total = 0
    for i, xi in enumerate(xs):
        num, den = 1, 1
        for j, xj in enumerate(xs):
            if i == j:
                continue
            num = _gf_mul(num, xj)          # (0 - x_j) == x_j in GF(2^8)
            den = _gf_mul(den, xi ^ xj)     # (x_i - x_j) == x_i ^ x_j
        total ^= _gf_mul(ys[i], _gf_div(num, den))
    return total


def reconstruct(shares: Sequence[Share]) -> bytes:
    """Reconstruct the secret from any K shares (k must match split time)."""
    if len({s.x for s in shares}) != len(shares):
        raise ValueError("duplicate x-coordinates in share set")
    length = len(shares[0].data)
    if any(len(s.data) != length for s in shares):
        raise ValueError("shares have inconsistent length")

    xs = [s.x for s in shares]
    # each byte position is an independent polynomial -> reconstruct per-byte
    return bytes(
        _lagrange_byte_at_zero(xs, [s.data[pos] for s in shares])
        for pos in range(length)
    )


def share_fingerprint(share: Share) -> str:
    """Short fingerprint for verifying a share out-of-band."""
    import hashlib

    return hashlib.sha256(bytes([share.x]) + share.data).hexdigest()[:12]
