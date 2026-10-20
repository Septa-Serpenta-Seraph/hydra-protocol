#!/usr/bin/env python3
"""Report Qdrant collection sizes + point counts."""
import json
import urllib.request

BASE = "http://127.0.0.1:6333"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))["result"]


cols = get("/collections")["collections"]
rows = []
for c in cols:
    n = c["name"]
    try:
        info = get(f"/collections/{n}")
        params = info.get("config", {}).get("params", {})
        vec = params.get("vectors", {})
        if isinstance(vec, dict) and "size" in vec:
            cfg = f"size={vec.get('size')} {vec.get('distance')}"
        elif isinstance(vec, dict):
            cfg = "named:" + ",".join(vec.keys())
        else:
            cfg = str(vec)[:50]
        rows.append((n, info.get("points_count") or 0, info.get("vectors_count") or 0, cfg))
    except Exception as e:  # noqa: BLE001
        rows.append((n, -1, -1, f"ERR {e}"[:50]))

rows.sort(key=lambda r: -max(r[1], 0))
print(f"{'collection':<40}{'points':>10}{'vectors':>10}  config")
total = 0
for n, p, v, cfg in rows:
    total += max(p, 0)
    print(f"{n:<40}{p:>10}{v:>10}  {cfg}")
print("TOTAL points:", total)
