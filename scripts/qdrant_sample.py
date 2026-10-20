#!/usr/bin/env python3
"""Sample one point from each Qdrant collection so we can see what it holds."""
import json
import urllib.request

BASE = "http://127.0.0.1:6333"


def post(path, data):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


names = [c["name"] for c in json.load(urllib.request.urlopen(BASE + "/collections"))["result"]["collections"]]
for n in names:
    try:
        res = post(f"/collections/{n}/points/scroll", {"limit": 1, "with_payload": True, "with_vector": False})
        pts = res["result"]["points"]
        if not pts:
            print(f"--- {n}: (empty)")
            continue
        p = pts[0]
        pl = p.get("payload") or {}
        keys = list(pl.keys())
        sample = {}
        for k in keys[:6]:
            v = pl[k]
            s = str(v)
            sample[k] = s[:110] + ("..." if len(s) > 110 else "")
        print(f"--- {n}  (id={str(p.get('id'))[:20]})")
        print(f"    keys: {keys[:12]}")
        for k, v in sample.items():
            print(f"    {k}: {v}")
    except Exception as e:  # noqa: BLE001
        print(f"--- {n}: ERR {str(e)[:90]}")
