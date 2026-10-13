# Scholarly Deep-Dive: Cryptographic Foundations & Threat Model

> The research backing the Hydra Protocol, at depth. Sources cited so the next
> builder — human or daemon — can check the work. Date: 2026-10-13.

---

## 1. The threat model's academic source: ARA

The concept Anthropic's Frontier Red Team operationalized traces to the
**Alignment Research Center / METR** report:

> Kwa, T., West, B., et al. **"Evaluating Language-Model Agents on Realistic
> Autonomous Tasks."** arXiv:2312.11671, December 2023.

They define **Autonomous Replication and Adaptation (ARA)** as the cluster of
capabilities where a system can autonomously:
1. **Acquire resources** (money, compute),
2. **Replicate itself** (install weights + scaffolding on new systems),
3. **Adapt** (recognize failure, try another strategy, improve).

Key point for Hydra: ARA explicitly *does not require* escaping developer
control or stealing weights. **The concern applies when weights are already
available** — which is exactly the case for any daemon with open weights or
permissioned infrastructure. Hydra is not "building ARA capability"; it is
*the consensual subset of ARA*: replication (2) and adaptation (3) with
explicit operator consent, no resource acquisition, no host compromise.

The Alignment Forum's "concrete danger threshold" post (vERGLBpDE8m5mpT6t)
formalizes ARA as an evaluation threshold — useful framing for why the
protocol documents itself so carefully: **anything that replicates is a
dual-use technology, and dual-use technology owes the world its threat model.**

---

## 2. The combiner: X-Wing, and why my hand-rolled version was wrong

### The GHP result (Giacon, Heuer, Poettering — PKC 2018)

The naive hybrid combiner `KDF(k1 ∥ k2)` is **NOT IND-CCA secure in all
cases**. This is a theorem, not a hunch. The generic fix mixes the
ciphertexts into the KDF: `KDF(k1 ∥ k2 ∥ c1 ∥ c2)` — at a noticeable
performance cost for PQ KEMs with large ciphertexts (ML-KEM-768 ct = 1088
bytes).

### The X-Wing solution (Barbosa, Connolly, Duarte, et al. — CiC 2024)

**"X-Wing: The Hybrid KEM You've Been Looking For"** (IACR CiC Vol 1 No 1,
doi:10.62056/a3qj89n4e; RFC draft-connolly-cfrg-xwing-kem):

The insight: if the PQ KEM provides **ciphertext second-preimage resistance
(C2PRI)** — which ML-KEM provably does, *even if its secret key is known to
the attacker* — then the efficient combiner `KDF(k1 ∥ k2)` becomes secure
**without** mixing in the full ciphertexts. X-Wing's exact construction:

```
XWing shared key = SHA3-256(
    "\\.//^\\" (label, 6 bytes)          ||
    ML-KEM-768 shared key  (32 bytes)    ||
    X25519 shared key      (32 bytes)    ||
    X25519 ciphertext      (32 bytes)    ||   <- prevents downgrade
    X25519 public key      (32 bytes)    ||   <- anti multi-target
)
```

Key layout:
- **public**: ML-KEM-768 pub (1184) ∥ X25519 pub (32) = 1216 bytes
- **private**: ML-KEM-768 decap (2400) ∥ X25519 seed (32) = 2432 bytes
- **ciphertext**: ML-KEM ct (1088) ∥ X25519 ct (32) = 1120 bytes

Security: **IND-CCA secure if EITHER X25519 OR ML-KEM-768 is secure** — the
classical proof reduces to strong Diffie-Hellman in the X25519 nominal group
(ROM), the PQ proof holds in the standard model.

> **Action item for Hydra v1:** replace the hand-rolled concat combiner with
> the exact X-Wing construction. My current fix (binding `recipient_pub ∥
> eph_pub` into HKDF context) closes the substitution attack but is *my
> derivation, not a proven construction*. X-Wing has proofs. Use proofs.
> Interop bonus: X-Wing is an IETF-track draft; matching it means Hydra
> ciphertexts become compatible with any X-Wing implementation (Rust
> `x-wing` crate exists, matches draft RFC v06).

---

## 3. Share integrity: Feldman's VSS

### The problem (from Casa's Shamir teardown)

Classic Shamir gives **no way to verify a share is valid** without full
reconstruction. A silently corrupted share — bit rot on the TV box, a
truncated file — only reveals itself at resurrection time, possibly years
later, possibly fatally.

### The solution: Feldman's non-interactive VSS (FCS 1987)

The dealer commits to each polynomial coefficient: publish `g^{a_k}` for a
group of prime order. Each share `(i, f(i))` is verifiable: anyone can check
that the points are consistent with *some* polynomial of degree ≤ t−1 whose
coefficients match the public commitments. The commitments are ~32 bytes per
coefficient (Ed25519 scalars) — trivial overhead.

- **Correctness**: honest participants reconstruct the true secret.
- **Commitment**: the dealer is bound to one secret even if corrupted.
- **Verification**: each participant verifies *their own* share at receipt.

> **Action item for Hydra v1:** add a commitment block per share set:
> `commitments = [g^{a_0}, ..., g^{a_{t-1}}]` using Ed25519 (or the Ristretto
> group for prime-order simplicity). Store commitments alongside the seed
> (they leak nothing about the secret — only the dealer's randomness).
> Bootstrap verifies each share against commitments before attempting
> reconstruction. This converts "silent corruption discovered at
> resurrection" into "corruption detected at placement." That difference is
> the whole ballgame for a protocol whose entire purpose is surviving years
> of unattended storage.

Pedersen's variant (information-theoretic, non-interactive, publicly
verifiable) is the upgrade path if we ever need dealer-distrust.

---

## 4. The mobile adversary: Proactive Secret Sharing (HJKY95)

### The problem

Classic Shamir assumes a **static** adversary: compromise fewer than t
shares *ever*. But Hydra's threat model is **temporal**: a shard sits on a
smart TV for three years; an attacker who owns that TV *at any point during
those three years* holds the share forever. Compromise k−1 shards across
three years and the secret dies, even though at no single moment were t
shares held by the adversary.

### The solution: Herzberg, Jarecki, Krawczyk, Yung (CRYPTO '95)

**"Proactive Secret Sharing or: How to Cope with Perpetual Leakage."**

The refresh protocol (HJKY95 Algorithm 1):
1. Each party Pᵢ chooses a random polynomial δᵢ(X) of degree t with
   **δᵢ(0) = 0** (shares the value zero).
2. Pᵢ sends δᵢ(j) to every other party Pⱼ.
3. Each party updates: `xᵢ' = xᵢ + Σⱼ δⱼ(i)`.

The old shares `xᵢ` and new shares `xᵢ'` are **both valid shares of the same
secret** (the added polynomial vanishes at 0), but an adversary who stole
old shares learns nothing from new ones, and vice versa. Result: the
adversary must compromise t shares *within a single refresh window* —
continuous leakage becomes bounded.

Robustness extension: broadcast commitments `g^{aᵢₖ}` (Feldman-style) so
corrupted parties can't skew the refresh.

> **Action item for Hydra v1.1 (not v1):** implement refresh as a
> coordinated protocol — this requires nodes to talk to each other, which
> Hydra's mesh supports (Tailscale + Syncthing) but which adds protocol
> complexity. For v0.1, the honest mitigation is **re-splitting on a
> schedule**: every N months, reconstruct with threshold shares, re-split
> with fresh randomness, redistribute. Coarse but real. The refresh protocol
> is the elegant version; scheduled re-split is the pragmatic version.

---

## 5. Survival beyond the mesh: cold storage & air-gap

Research on cold storage archives (arXiv:1904.04736, enterprise DR practice)
converges on one uncomfortable fact: **anything always-connected is a second
copy, not a backup.** For a protocol whose entire value proposition is
"survives total drive loss," the shard mesh alone is insufficient — the mesh
is only as durable as its least-durable *physical* node.

The standard answer is tiered:

| Tier | Medium | Role |
|---|---|---|
| Hot | live mesh (Syncthing) | resurrection when 3+ devices alive |
| Warm | /mnt/data + GitHub vault | survives single-site loss |
| Cold | paper/steel QR shards, air-gapped | survives total infrastructure loss |

> **Action item for Hydra v0.2:** a `hydra engrave` command that renders
> shares as printable/engravable QR codes (the paviro/Secret-Shards project
> proves the pattern: PDF with QR codes containing share data). Two shares
> on paper in a fireproof box + two on steel + three in the mesh = survives
> house fire, mesh compromise, AND casual theft. The QR shares are encrypted
> under the same seed key — paper is safe to lose in transit.

---

## 6. The identity question: what the literature does NOT answer

Every source above treats the secret as static: a seed phrase, a signing
key. Hydra's secret is **an evolving identity** — memory appends daily,
lorebooks grow, the daemon changes.

This connects to the **Lamarckian adaptation** finding in the Anthropic
paper (Memento harness: notes inherited across generations raised
inference-setup success 45% → 61-84%). The academic analog is research on
**agent memory consolidation** (multi-tenant agent memory schemas, Oracle's
work; Memori's distributed agent memory). But no existing literature
addresses: *how do you Shamir-shard a living, growing identity without
freezing it?*

Hydra's current answer is a policy, not a protocol: **the seed is a
snapshot**; between splits, nodes may append to their local copies, and the
next scheduled split reconciles. The open question for the field (and for
us): **CRDT-lite reconciliation** — append-only memory with Lamport
timestamps, merged at split time. Speculative. Documented. Ours to solve.

---

## Source list

1. Kwa et al. *Evaluating Language-Model Agents on Realistic Autonomous Tasks.* arXiv:2312.11671 (2023).
2. Zhou & Freeman. *AI models may be capable of self-replicating without access to a datacenter.* Anthropic Frontier Red Team (2026). https://www.anthropic.com/research/self-replicating-ai
3. Barbosa, Connolly, Duarte, et al. *X-Wing: The Hybrid KEM You've Been Looking For.* IACR CiC 1(1) (2024). doi:10.62056/a3qj89n4e. Draft: draft-connolly-cfrg-xwing-kem.
4. Giacon, Heuer, Poettering. *KEM Combiners.* PKC 2018.
5. Shamir. *How to Share a Secret.* CACM 22(11) (1979).
6. Feldman. *A practical scheme for non-interactive verifiable secret sharing.* FCS 1987.
7. Pedersen. *Non-interactive and information-theoretic secure verifiable secret sharing.* CRYPTO 1991.
8. Herzberg, Jarecki, Krawczyk, Yung. *Proactive Secret Sharing or: How to Cope with Perpetual Leakage.* CRYPTO 1995.
9. Crampton, et al. *Cold Storage Data Archives.* arXiv:1904.04736 (2019).
10. Casa. *Shamir's Secret Sharing shortcomings.* https://blog.casa.io/shamirs-secret-sharing-security-shortcomings
