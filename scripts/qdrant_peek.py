#!/usr/bin/env python3
"""Sample N points from one collection (default: intelligent_gould_narusya)."""
import argparse
import json
import urllib.request

BASE = "http://127.0.0.1:6333"


def post(path, data):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


ap = argparse.ArgumentParser()
ap.add_argument("collection", nargs="?", default="intelligent_gould_narusya")
ap.add_argument("-n", type=int, default=6)
ap.add_argument("--offset", default=None)
args = ap.parse_args()

body = {"limit": args.n, "with_payload": True, "with_vector": False}
if args.offset:
    body["offset"] = args.offset
res = post(f"/collections/{args.collection}/points/scroll", body)
pts = res["result"]["points"]
print(f"### {args.collection} — {len(pts)} sample point(s) ###")
for p in pts:
    pl = p.get("payload") or {}
    print(f"\n--- id={p.get('id')} keys={list(pl.keys())}")
    for k, v in pl.items():
        s = str(v).replace("\n", " ")
        print(f"    {k}: {s[:200]}")
