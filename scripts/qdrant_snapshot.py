#!/usr/bin/env python3
"""Snapshot every Qdrant collection and download the snapshot files.

Usage:
    python3 qdrant_snapshot.py --out /mnt/data/qdrant-snapshots [--only a,b,c]

Each collection gets one native Qdrant snapshot (consistent, compressed).
The server-side copy is deleted right after download so we don't fill the disk.
"""
import argparse
import json
import os
import sys
import urllib.request

BASE = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")


def api(path, method="GET", data=None, raw=False, timeout=900):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(BASE + path, method=method, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        blob = r.read()
    return blob if raw else json.loads(blob)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/mnt/data/qdrant-snapshots")
    ap.add_argument("--only", default="", help="comma-separated collection names")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    names = [c["name"] for c in api("/collections")["result"]["collections"]]
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        names = [n for n in names if n in want]

    print(f"snapshotting {len(names)} collection(s) -> {args.out}")
    total = 0
    failed = []
    for n in names:
        try:
            snap = api(f"/collections/{n}/snapshots", method="POST", data={})["result"]["name"]
            blob = api(f"/collections/{n}/snapshots/{snap}", raw=True)
            with open(os.path.join(args.out, f"{n}.snapshot"), "wb") as fh:
                fh.write(blob)
            total += len(blob)
            print(f"  {n:<40} {len(blob) / 1e6:8.2f} MB")
            try:
                api(f"/collections/{n}/snapshots/{snap}", method="DELETE")
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            failed.append((n, str(e)[:90]))
            print(f"  {n:<40} FAILED: {str(e)[:90]}")

    print(f"\ntotal: {total / 1e6:.1f} MB -> {args.out}")
    if failed:
        print("failed:", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
