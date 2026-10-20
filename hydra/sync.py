"""sync.py — union-sync a node's Qdrant with the hub.

Vectors are immutable facts keyed by stable IDs, so a "sync" is just: for every
collection, exchange the points one side has and the other doesn't. No conflict
resolution, no ordering, no log — the union is always the correct answer.

    python3 -m hydra.sync --hub http://100.77.142.40:8767 --token XXX [--dry-run]

Runs against the node's LOCAL Qdrant (default 127.0.0.1:6333) and the hub's
/sync/* endpoints. Safe to re-run: upserts are idempotent by ID.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

from . import qsync

DEFAULT_HUB = "http://100.77.142.40:8767"


def hub_get(hub, path, token, timeout=300):
    url = f"{hub.rstrip('/')}{path}"
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}token={token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def hub_post(hub, path, token, payload, timeout=600):
    body = dict(payload)
    body["token"] = token
    req = urllib.request.Request(
        f"{hub.rstrip('/')}{path}", method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def sync(local_url, hub, token, dry_run=False, verbose=True):
    stats = {"pulled": 0, "pushed": 0, "collections_new": 0, "errors": []}

    hub_man = hub_get(hub, "/sync/manifest", token)
    local_man = qsync.manifest(local_url)
    hc, lc = hub_man["collections"], local_man["collections"]

    if verbose:
        print(f"hub   : {len(hc)} collections, {hub_man['total_points']} points")
        print(f"local : {len(lc)} collections, {local_man['total_points']} points")

    for name in sorted(set(hc) | set(lc)):
        try:
            h = hc.get(name)
            l = lc.get(name)

            # Collection only on the hub -> pull it whole.
            if h and not l:
                ids = hub_get(hub, f"/sync/ids?collection={name}", token)["ids"]
                if verbose:
                    print(f"  + {name}: new here, pulling {len(ids)}")
                stats["collections_new"] += 1
                if not dry_run:
                    _pull(local_url, hub, token, name, ids, stats, verbose)
                else:
                    stats["pulled"] += len(ids)
                continue

            # Collection only on this node -> push it whole.
            if l and not h:
                ids = qsync.point_ids(local_url, name)
                if verbose:
                    print(f"  + {name}: new on hub, pushing {len(ids)}")
                stats["collections_new"] += 1
                if not dry_run:
                    _push(local_url, hub, token, name, ids, stats, verbose)
                else:
                    stats["pushed"] += len(ids)
                continue

            # Identical -> nothing to do.
            if h and l and h["ids_hash"] == l["ids_hash"]:
                if verbose:
                    print(f"  = {name}: in sync ({l['points']})")
                continue

            hub_ids = hub_get(hub, f"/sync/ids?collection={name}", token)["ids"]
            local_ids = qsync.point_ids(local_url, name)
            hs, ls = set(hub_ids), set(local_ids)
            to_pull = sorted(hs - ls)
            to_push = sorted(ls - hs)

            if verbose:
                print(f"  ~ {name}: pull {len(to_pull)}, push {len(to_push)}")

            if dry_run:
                stats["pulled"] += len(to_pull)
                stats["pushed"] += len(to_push)
                continue

            _pull(local_url, hub, token, name, to_pull, stats, verbose)
            _push(local_url, hub, token, name, to_push, stats, verbose)
        except Exception as e:  # noqa: BLE001
            stats["errors"].append((name, str(e)[:120]))
            if verbose:
                print(f"  ! {name}: {str(e)[:120]}")

    return stats


def _pull(local_url, hub, token, name, ids, stats, verbose):
    if not ids:
        return
    # Ensure the collection exists locally with the hub's vector size.
    if qsync.vector_size(local_url, name) is None:
        size = None
        try:
            res = hub_post(hub, "/sync/fetch", token, {"collection": name, "ids": ids[:1]})
            pts = res.get("points") or []
            if pts and isinstance(pts[0].get("vector"), list):
                size = len(pts[0]["vector"])
        except Exception:  # noqa: BLE001
            pass
        if size:
            qsync.ensure_collection(local_url, name, size)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        res = hub_post(hub, "/sync/fetch", token, {"collection": name, "ids": chunk})
        pts = res.get("points") or []
        if pts:
            qsync.upsert_points(local_url, name, pts)
            stats["pulled"] += len(pts)


def _push(local_url, hub, token, name, ids, stats, verbose):
    if not ids:
        return
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        pts = qsync.fetch_points(local_url, name, chunk)
        if pts:
            hub_post(hub, "/sync/push", token, {"collection": name, "points": pts})
            stats["pushed"] += len(pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default=DEFAULT_HUB)
    ap.add_argument("--token", required=True)
    ap.add_argument("--local", default=qsync.DEFAULT_URL)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = sync(args.local, args.hub, args.token, dry_run=args.dry_run)
    print()
    print(f"pulled {s['pulled']}  pushed {s['pushed']}  "
          f"new collections {s['collections_new']}  errors {len(s['errors'])}")
    for n, e in s["errors"]:
        print(f"  ! {n}: {e}")
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
