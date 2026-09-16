"""Central provisioning and gateway-sync server.

This service is intentionally standard-library-only. Run it on the Windows
command-post machine with TLS_CERT_FILE, TLS_KEY_FILE and TLS_CA_FILE set.
"""

import base64
import json
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from wearable.crypto.ascon import ascon_xof


KEY_SIZE = 16


def derive_pairwise_key(master_secret, device_a, device_b):
    """Derive a deterministic pairwise key using the project KDF primitive."""
    ids = "|".join(sorted((device_a, device_b))).encode("utf-8")
    return ascon_xof(master_secret + b"pairwise-key-v1" + ids, KEY_SIZE)


class ProvisioningService:
    def __init__(self, registry_path, master_secret, broadcast_key):
        self.registry_path = registry_path
        self.master_secret = bytes(master_secret)
        self.broadcast_key = bytes(broadcast_key)
        self._lock = threading.Lock()
        self.registry = self._load_registry()

    def _load_registry(self):
        try:
            with open(self.registry_path, "r", encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, json.JSONDecodeError):
            return {"devices": {}}

    def provision(self, request):
        device_id = str(request.get("device_id", "")).strip()
        if not device_id:
            raise ValueError("device_id is required")
        with self._lock:
            device = self.registry.get("devices", {}).get(device_id)
            if not device:
                raise PermissionError("device is not registered")
            peers = device.get("peers", [])
            keyset = {
                peer_id: derive_pairwise_key(self.master_secret, device_id, peer_id).hex()
                for peer_id in peers
            }
            return {
                "device_id": device_id,
                "mission_keyset": keyset,
                "mission_broadcast_key": self.broadcast_key.hex(),
                "mission_epoch_id": int(self.registry.get("epoch_id", 0)),
                "epoch_start_time": self.registry.get("epoch_start_time", 0),
                "gateway": bool(device.get("gateway", False)),
            }

    def store_sync(self, device_id, records):
        path = self.registry.get("sync_log_path", "server_sync.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps({"device_id": device_id, "record": record}) + "\n")


def build_server(service, bind, port, cert_file, key_file, ca_file):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, body):
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length))

        def do_POST(self):
            try:
                body = self._body()
                if self.path == "/register":
                    self._json(200, service.provision(body))
                elif self.path == "/sync":
                    service.store_sync(body["device_id"], body.get("records", []))
                    self._json(200, {"accepted": len(body.get("records", []))})
                else:
                    self._json(404, {"error": "not found"})
            except PermissionError as exc:
                self._json(403, {"error": str(exc)})
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(500, {"error": "internal server error"})

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer((bind, port), Handler)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(cert_file, key_file)
    context.load_verify_locations(cafile=ca_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    master = bytes.fromhex(os.environ["MASTER_SERVER_SECRET_HEX"])
    broadcast = bytes.fromhex(os.environ["MISSION_BROADCAST_KEY_HEX"])
    service = ProvisioningService(os.environ.get("DEVICE_REGISTRY_PATH", "device_registry.json"), master, broadcast)
    server = build_server(service, os.environ.get("SERVER_BIND", "0.0.0.0"),
                          int(os.environ.get("SERVER_PORT", "8443")),
                          os.environ["TLS_CERT_FILE"], os.environ["TLS_KEY_FILE"],
                          os.environ["TLS_CA_FILE"])
    print(f"Provisioning server listening on {server.server_address}")
    server.serve_forever()


if __name__ == "__main__":
    main()
