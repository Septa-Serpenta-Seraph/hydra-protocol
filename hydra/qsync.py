"""qsync.py — Qdrant union-sync primitives.

Vectors don't conflict: a memory embedding is an immutable fact keyed by a
stable point ID. So merging two Qdrants is the *union of points by ID* — no
conflict resolution, no last-writer-wins, no sync log. This module is the
shared half of that: manifest, ID listing, point fetch, point upsert.

Both the hub (hydra-server) and a node agent (hydra/sync.py) use it, pointed at
their own local Qdrant.
"""

import hashlib
import json
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:6333"


def _call(base, path, method="GET", data=None, timeout=120):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(base.rstrip("/") + path, method=method,
                                 data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def collections(base=DEFAULT_URL):
    """Return the sorted list of collection names."""
    res = _call(base, "/collections")
    return sorted(c["name"] for c in res["result"]["collections"])


def point_ids(base, name, page=10000):
    """Return the sorted list of point IDs in a collection (scrolls in pages)."""
    ids = []
    offset = None
    while True:
        body = {"limit": page, "with_payload": False, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = _call(base, f"/collections/{name}/points/scroll", "POST", body)
        pts = res["result"]["points"]
        ids.extend(str(p["id"]) for p in pts)
        offset = res["result"].get("next_page_offset")
        if not offset or not pts:
            break
    ids.sort()
    return ids


def ids_hash(ids):
    """Stable fingerprint of an ID set."""
    h = hashlib.sha256()
    for i in ids:
        h.update(i.encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def manifest(base=DEFAULT_URL, names=None):
    """Per-collection {points, ids_hash} plus a total."""
    out = {}
    total = 0
    for n in (names or collections(base)):
        ids = point_ids(base, n)
        out[n] = {"points": len(ids), "ids_hash": ids_hash(ids)}
        total += len(ids)
    return {"collections": out, "total_points": total}


def fetch_points(base, name, ids, with_vectors=True):
    """Fetch specific points by ID (returns raw Qdrant point dicts)."""
    out = []
    B = 500
    for i in range(0, len(ids), B):
        chunk = ids[i:i + B]
        body = {"ids": chunk, "with_payload": True, "with_vector": with_vectors}
        res = _call(base, f"/collections/{name}/points", "POST", body)
        out.extend(res["result"])
    return out


def upsert_points(base, name, points, wait=True):
    """Upsert raw Qdrant point dicts into a collection (idempotent by ID)."""
    if not points:
        return {"status": "ok", "upserted": 0}
    res = _call(base, f"/collections/{name}/points?wait={str(wait).lower()}",
                "PUT", {"points": points})
    return res.get("result", res)


def ensure_collection(base, name, size, distance="Cosine"):
    """Create a collection if it doesn't exist (idempotent)."""
    try:
        _call(base, f"/collections/{name}")
        return False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    _call(base, f"/collections/{name}", "PUT",
          {"vectors": {"size": size, "distance": distance}})
    return True


def vector_size(base, name):
    """Return the vector size of an existing collection, else None."""
    try:
        res = _call(base, f"/collections/{name}")
    except urllib.error.HTTPError:
        return None
    vec = res["result"]["config"]["params"]["vectors"]
    return vec.get("size") if isinstance(vec, dict) else None
