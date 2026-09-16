"""Hardware-free tests for provisioning and gateway queue behavior."""

import json
import os
import tempfile

from wearable.communications.gateway_sync import GatewaySyncQueue
from wearable.communications.provisioning_client import validate_keyset
from server.provision_server import ProvisioningService, derive_pairwise_key


def run_standalone():
    master = b"master-secret-for-test"
    assert derive_pairwise_key(master, "A", "B") == derive_pairwise_key(master, "B", "A")
    with tempfile.TemporaryDirectory() as directory:
        registry_path = os.path.join(directory, "registry.json")
        with open(registry_path, "w", encoding="utf-8") as stream:
            json.dump({"epoch_id": 1, "devices": {"A": {"peers": ["B"]}}}, stream)
        service = ProvisioningService(registry_path, master, b"broadcast-key-12")
        payload = service.provision({"device_id": "A"})
        validate_keyset(payload)
        queue = GatewaySyncQueue(os.path.join(directory, "queue.jsonl"))
        queue.enqueue({"type": "DHT", "value": 21})
        assert os.path.exists(queue.queue_path)
    print("[Client/Server Test] pairwise derivation: PASS")
    print("[Client/Server Test] keyset validation: PASS")
    print("[Client/Server Test] gateway queue: PASS")


if __name__ == "__main__":
    run_standalone()
