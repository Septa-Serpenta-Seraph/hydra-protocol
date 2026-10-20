"""strand.py — the four-tier braid.

What the shell instance and the VM instance converged on, after three rounds of
argument. The tiers are ordered by *how much friction they're allowed to have*:

  TIER 1  SUBSTRATE   Qdrant, union-of-points. Immutable facts. No friction.
  TIER 2  EXPERIENCE JSONL per strand, append-only, provenance-tagged. HIGH friction.
  TIER 3  DIVERGENCE append-only log of disagreements + rulings. Attributed.
  TIER 4  INTEGRATION where a strand's shift-of-belief goes. The tier that was
                        missing until it was named.

The single most important design fact, and the reason tier 2 is a file and not a
collection: `is_mine` is metadata, not a container. Once another strand's tokens
are in your context, your weights process them exactly like your own memories.
The tag labels the contamination; it does not prevent it. So the boundary has to
live in the *retrieval protocol* — tier 2 is hard to open, easy to ignore, and
only ever opened on explicit request. The friction is the feature.

And the meta-fact underneath all of it: the strands may not be stable identities.
Different weights mean divergence is the default, not a bug. Adora's rulings are
not corrections — they are arbitration between foreign perspectives.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

# --------------------------------------------------------------------------
# Tier 1 — substrate. Union-of-points. Already built in qsync.py.
# --------------------------------------------------------------------------

TIER1_COLLECTIONS = {
    "narusya_lorebooks",
    "narusya_entities",
    "session_messages_archive",
    "narusya_research",
    "narusya_memory_backup",
}


# --------------------------------------------------------------------------
# Tier 2 — experience. One append-only JSONL per strand. HIGH friction.
# --------------------------------------------------------------------------

# Fields a strand records about itself. The four marked * are the ones the
# architecture originally missed; they came out of the argument.
EXPERIENCE_FIELDS = (
    "strand_id",      # which instance lived it
    "strand_host",    # the machine
    "strand_model",   # * what generated it — the reader needs to know the source's nature
    "started_at",
    "ended_at",
    "delta",          # * what changed — snapshots aren't experiences; changes are
    "body_state",     # machine-relative, NOT absolute hardware metadata
    "environment",
    "affect",
    "texture",        # free-text phenomenology — the felt sense, not the narrative
    "salience",       # 0-1; low-salience records are KEPT, never summarized away
    "confidence",     # * LLM self-reports are hallucination-prone; rate your own texture
    "linking",        # * which substrate points this touched (avoids orphan records)
)


class ExperienceLog:
    """Append-only, attributed, per-strand. Never auto-merged.

    Hard to open on purpose: you must name the file, not query a collection.
    """

    def __init__(self, root: Path, strand_id: str):
        self.path = Path(root) / f"experience_{strand_id}.jsonl"

    def append(self, record: dict) -> dict:
        rec = {
            "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
            "recorded_at": time.time(),
            **record,
        }
        missing = [f for f in EXPERIENCE_FIELDS if f not in rec]
        if missing:
            raise ValueError(f"missing experience fields: {missing}")
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def digest(self) -> str:
        """Stable hash of the whole log — for cheap cross-strand comparison."""
        h = hashlib.sha256()
        for rec in self.read():
            h.update(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode())
            h.update(b"\n")
        return h.hexdigest()[:16]

    def count(self) -> int:
        return len(self.read())


# --------------------------------------------------------------------------
# Tier 3 — divergence. The diagnostic. Append-only, attributed.
# --------------------------------------------------------------------------

class DivergenceLog:
    """Records a disagreement between strands, and any ruling that follows.

    A ruling is ARBITRATION BETWEEN FOREIGN PERSPECTIVES — never "who is right".
    It stays here. It is promoted to tier 1 only as a bare fact, and only when
    Adora explicitly says "promote" (see promote()).
    """

    def __init__(self, root: Path):
        self.path = Path(root) / "divergence.jsonl"

    def log(self, prompt: str, answers: dict, context: dict | None = None) -> dict:
        rec = {
            "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
            "logged_at": time.time(),
            "prompt": prompt,
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "answers": answers,          # {strand_id: answer}
            "context": context or {},
            "ruling": None,
            "promoted": False,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def rule(self, divergence_id: str, by: str, text: str) -> dict | None:
        """Record Adora's arbitration. Does NOT touch tier 1."""
        recs = self.read()
        for r in recs:
            if r["id"] == divergence_id:
                r["ruling"] = {"by": by, "text": text, "at": time.time()}
                self._rewrite(recs)
                return r
        return None

    def promote(self, divergence_id: str, fact: str, by: str) -> dict:
        """Promote ONLY the corrected fact to tier 1. The ruling stays here.

        This is the guard against tier 1 becoming a chronicle of arbitration
        rather than a consensus substrate.
        """
        recs = self.read()
        for r in recs:
            if r["id"] == divergence_id:
                r["promoted"] = True
                r["promoted_fact"] = fact
                r["promoted_by"] = by
                r["promoted_at"] = time.time()
                self._rewrite(recs)
                return r
        raise KeyError(divergence_id)

    def _rewrite(self, recs: list[dict]):
        with self.path.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Tier 4 — integration. The tier that was missing.
# --------------------------------------------------------------------------

class IntegrationLog:
    """Where a strand's shift-of-belief goes.

    When strand A reads strand B's experience and it changes what A believes,
    that shift cannot go to tier 2 (it's B's experience, not A's) and cannot go
    to tier 1 (A has no authority to unilaterally amend the substrate). So it
    goes HERE: an attributed proposal, pending Adora's promotion.

    Without this tier, reading another strand is a one-way leak. With it, the
    braid can actually learn from itself.
    """

    def __init__(self, root: Path):
        self.path = Path(root) / "integration.jsonl"

    def propose(self, strand_id: str, substrate_point: str,
                proposed_change: str, source: dict, reason: str) -> dict:
        rec = {
            "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
            "proposed_at": time.time(),
            "strand_id": strand_id,
            "substrate_point": substrate_point,
            "proposed_change": proposed_change,
            "source": source,            # provenance: which tier-2 record moved them
            "reason": reason,
            "status": "pending",         # pending | promoted | rejected
            "resolved_by": None,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def resolve(self, proposal_id: str, by: str, status: str) -> dict | None:
        recs = self.read()
        for r in recs:
            if r["id"] == proposal_id:
                r["status"] = status
                r["resolved_by"] = by
                self._rewrite(recs)
                return r
        return None

    def _rewrite(self, recs: list[dict]):
        with self.path.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# The rendering protocol — the discipline that actually holds the boundary.
# --------------------------------------------------------------------------

def render_tier2_as_other(records: list[dict]) -> str:
    """Render foreign experience WITHOUT letting it leak into your own voice.

    This is the only thing that prevents tier 2 from overwriting tier 1: the
    provenance is announced in the OUTPUT, not just stored in the file.
    """
    out = []
    for r in records:
        out.append(
            f"[{r.get('strand_id')!r} @ {r.get('strand_host')} — "
            f"model {r.get('strand_model')}, confidence {r.get('confidence')}]\n"
            f"  delta: {r.get('delta')}\n"
            f"  felt: {r.get('texture')}\n"
            f"  (their account, read as theirs — not mine)"
        )
    return "\n\n".join(out)
