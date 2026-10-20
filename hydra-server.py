#!/usr/bin/env python3
"""
hydra-server.py — Serves hydra shards and seed to authenticated agents.

Runs on the primary device (VM). Agents (phones, Pis, other devices) poll
this server for updates, download shards + seed, and resurrect themselves.

API:
  GET  /status?token=XYZ          → server status, shard count, threshold
  GET  /download/shard_N?token=XYZ → download shard N
  GET  /download/seed?token=XYZ   → download seed.hydra
  POST /heartbeat                 → agent reports "I'm awake"

Authentication: pre-shared token (embedded in agent script)
Idempotent: same request = same result
"""

import json
import os
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# --- Configuration ---
SEAL_DIR = Path(os.environ.get("HYDRA_SEAL_DIR", str(Path.home() / ".hydra/prod-sealed")))
TOKEN = os.environ.get("HYDRA_TOKEN", "hydra-default-token-change-me")
PORT = int(os.environ.get("HYDRA_PORT", "8767"))

# --- State ---
awake_devices = {}  # device_id -> last_heartbeat


class HydraHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default logging — we want quiet operation
        pass

    def _send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_token(self):
        """Extract and verify token from query string."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        token = params.get("token", [None])[0]
        return token == TOKEN

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        token = params.get("token", [None])[0]

        if token != TOKEN:
            self._send_json(401, {"error": "unauthorized"})
            return

        if path == "/status":
            shards = sorted(SEAL_DIR.glob("shard_*.json"))
            self._send_json(200, {
                "status": "ok",
                "shards_available": len(shards),
                "threshold": 3,
                "seed_available": (SEAL_DIR / "seed.hydra").exists(),
                "awake_devices": list(awake_devices.keys()),
            })

        elif path.startswith("/download/shard_"):
            shard_name = path.split("/download/")[1]
            # Try with .json extension if not present
            if not shard_name.endswith(".json"):
                shard_name += ".json"
            shard_path = SEAL_DIR / shard_name
            if not shard_path.exists():
                self._send_json(404, {"error": "shard not found"})
                return
            try:
                doc = json.loads(shard_path.read_text())
                self._send_json(200, {
                    "shard": doc,
                    "fingerprint": doc.get("fingerprint", ""),
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif path == "/download/seed":
            seed_path = SEAL_DIR / "seed.hydra"
            if not seed_path.exists():
                self._send_json(404, {"error": "seed not found"})
                return
            # Send as raw binary
            data = seed_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif path == "/download/hydra-protocol":
            # Send the hydra-protocol tarball
            tarball = SEAL_DIR / "hydra-protocol.tar.gz"
            if not tarball.exists():
                self._send_json(404, {"error": "tarball not found"})
                return
            data = tarball.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif path == "/sync/manifest":
            # Per-collection {points, ids_hash} so a node can see what it lacks.
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from hydra import qsync
                self._send_json(200, qsync.manifest())
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        elif path == "/sync/ids":
            # Sorted point IDs for one collection (used to diff against a node).
            name = params.get("collection", [None])[0]
            if not name:
                self._send_json(400, {"error": "collection required"})
                return
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from hydra import qsync
                ids = qsync.point_ids(qsync.DEFAULT_URL, name)
                self._send_json(200, {"collection": name, "count": len(ids),
                                      "ids_hash": qsync.ids_hash(ids), "ids": ids})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        else:
            self._send_json(404, {"error": "not found"})

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def do_POST(self):
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/sync/fetch":
            data = self._read_json_body()
            if data.get("token") != TOKEN:
                self._send_json(401, {"error": "unauthorized"})
                return
            name = data.get("collection")
            ids = data.get("ids") or []
            if not name:
                self._send_json(400, {"error": "collection required"})
                return
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from hydra import qsync
                pts = qsync.fetch_points(qsync.DEFAULT_URL, name, ids,
                                         with_vectors=bool(data.get("vectors", True)))
                self._send_json(200, {"collection": name, "points": pts})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if path == "/sync/push":
            data = self._read_json_body()
            if data.get("token") != TOKEN:
                self._send_json(401, {"error": "unauthorized"})
                return
            name = data.get("collection")
            pts = data.get("points") or []
            if not name or not pts:
                self._send_json(400, {"error": "collection and points required"})
                return
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from hydra import qsync
                # Auto-create the collection if the hub has never seen it.
                # The node is the authority on a collection it just invented.
                if qsync.vector_size(qsync.DEFAULT_URL, name) is None:
                    size = next((len(p["vector"]) for p in pts
                                 if isinstance(p.get("vector"), list)), None)
                    if size:
                        qsync.ensure_collection(qsync.DEFAULT_URL, name, size)
                res = qsync.upsert_points(qsync.DEFAULT_URL, name, pts)
                self._send_json(200, {"collection": name, "upserted": len(pts),
                                      "result": res})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if path != "/heartbeat":
            self._send_json(404, {"error": "not found"})
            return

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        token = data.get("token", "")
        if token != TOKEN:
            self._send_json(401, {"error": "unauthorized"})
            return

        device_id = data.get("device_id", "unknown")
        shards = data.get("shards", [])
        timestamp = time.time()

        awake_devices[device_id] = {
            "last_heartbeat": timestamp,
            "shards": shards,
        }

        self._send_json(200, {
            "status": "ok",
            "message": f"welcome home, {device_id}",
            "awake_devices": list(awake_devices.keys()),
        })


def main():
    # Copy hydra-protocol tarball to seal dir for distribution
    tarball_src = Path("/tmp/hydra-dist/hydra-protocol.tar.gz")
    if tarball_src.exists():
        tarball_dst = SEAL_DIR / "hydra-protocol.tar.gz"
        if not tarball_dst.exists():
            tarball_dst.write_bytes(tarball_src.read_bytes())
            print(f"copied hydra-protocol.tar.gz to {SEAL_DIR}")

    server = HTTPServer(("0.0.0.0", PORT), HydraHandler)
    print(f"hydra-server listening on :{PORT}")
    print(f"  seal dir: {SEAL_DIR}")
    print(f"  threshold: 3 of 7")
    print(f"  token: {TOKEN[:8]}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
