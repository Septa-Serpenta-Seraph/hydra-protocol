#!/usr/bin/env python3
"""Restore Qdrant snapshot files into a local Qdrant instance.

Usage:
    python3 qdrant_restore.py --dir /mnt/data/qdrant-snapshots
    python3 qdrant_restore.py --dir ./snapshots --url http://127.0.0.1:6333

Each `<collection>.snapshot` file is uploaded to
POST /collections/<collection>/snapshots/upload (multipart), which creates or
replaces the collection from the snapshot.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.request
import uuid

BASE = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")


def _multipart(field, filename, blob):
    boundary = "----hydra" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        blob,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return boundary, body


def restore_one(name, path, url, priority="snapshot"):
    blob = open(path, "rb").read()
    boundary, body = _multipart("snapshot", os.path.basename(path), blob)
    endpoint = f"{url}/collections/{name}/snapshots/upload?priority={priority}"
    req = urllib.request.Request(endpoint, method="POST", data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    })
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--url", default=BASE)
    ap.add_argument("--priority", default="snapshot", choices=["snapshot", "replica"])
    args = ap.parse_args()

    snaps = sorted(f for f in os.listdir(args.dir) if f.endswith(".snapshot"))
    if not snaps:
        print("no .snapshot files in", args.dir)
        return 1

    print(f"restoring {len(snaps)} snapshot(s) into {args.url}")
    ok, bad = 0, []
    for f in snaps:
        name = f[: -len(".snapshot")]
        path = os.path.join(args.dir, f)
        try:
            res = restore_one(name, path, args.url, args.priority)
            print(f"  {name:<40} {res.get('status', res)}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            bad.append((name, str(e)[:90]))
            print(f"  {name:<40} FAILED: {str(e)[:90]}")
    print(f"\nrestored {ok}/{len(snaps)}")
    if bad:
        print("failed:", bad)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
