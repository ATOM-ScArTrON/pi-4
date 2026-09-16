"""Offline-first gateway queue and mTLS upload client."""

import json
import os
import ssl
import hashlib
import uuid
from urllib.request import Request, urlopen
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal


PRIORITY = {"DHT": 10, "SND": 10, "MOT": 10, "GPS": 20, "VIT": 50,
            "IMG": 60, "TEXT": 60, "TEL": 50}


class GatewaySyncQueue:
    def __init__(self, queue_path, max_bytes=50 * 1024 * 1024):
        self.queue_path = queue_path
        self.max_bytes = max_bytes
        os.makedirs(os.path.dirname(queue_path) or ".", exist_ok=True)

    def enqueue(self, payload):
        payload = dict(payload)
        payload.setdefault("message_id", uuid.uuid4().hex)
        with open(self.queue_path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self._rotate()

    def _rotate(self):
        if not os.path.exists(self.queue_path) or os.path.getsize(self.queue_path) <= self.max_bytes:
            return
        with open(self.queue_path, "r", encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        records.sort(key=lambda item: PRIORITY.get(item.get("type", ""), 10), reverse=True)
        while records and len(json.dumps(records).encode("utf-8")) > self.max_bytes:
            records.pop()
        temporary = self.queue_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        os.replace(temporary, self.queue_path)

    def sync_once(self, server_url, device_id, ca_file, cert_file, key_file):
        if not os.path.exists(self.queue_path):
            return 0
        with open(self.queue_path, "r", encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        if not records:
            return 0
        context = ssl.create_default_context(cafile=ca_file)
        context.load_cert_chain(cert_file, key_file)
        normalized = []
        for index, record in enumerate(records):
            record = dict(record)
            if not record.get("message_id"):
                digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
                record["message_id"] = f"legacy-{digest}-{index}"
            normalized.append(record)
        request = Request(server_url.rstrip("/") + "/device/sync",
                          data=json.dumps({"device_id": device_id, "records": normalized}).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, context=context, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        removable = set(result.get("accepted", [])) | set(result.get("duplicates", []))
        remaining = [record for record in normalized if record.get("message_id") not in removable]
        if remaining:
            temporary = self.queue_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                for record in remaining:
                    stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            os.replace(temporary, self.queue_path)
        else:
            os.remove(self.queue_path)
        return len(removable)


def run_standalone():
    from config import (DEVICE_ID, GATEWAY_QUEUE_PATH, PROVISION_SERVER_URL,
                        TLS_CA_FILE, TLS_CERT_FILE, TLS_KEY_FILE)
    queue = GatewaySyncQueue(GATEWAY_QUEUE_PATH)
    count = queue.sync_once(PROVISION_SERVER_URL, DEVICE_ID, TLS_CA_FILE,
                            TLS_CERT_FILE, TLS_KEY_FILE)
    print(f"Synchronized {count} queued gateway records.")
