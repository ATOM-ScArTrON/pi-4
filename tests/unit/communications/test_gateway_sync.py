"""Tests for durable per-record gateway acknowledgements."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from wearable.communications.gateway_sync import GatewaySyncQueue


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"accepted": ["m1"], "duplicates": [], "rejected": ["m2"]}).encode()


class FakeTlsContext:
    def load_cert_chain(self, *_args):
        return None


class GatewaySyncTests(unittest.TestCase):
    def test_only_acknowledged_records_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "queue.jsonl")
            queue = GatewaySyncQueue(path)
            queue.enqueue({"message_id": "m1", "type": "VIT"})
            queue.enqueue({"message_id": "m2", "type": "GPS"})
            with patch("wearable.communications.gateway_sync.ssl.create_default_context", return_value=FakeTlsContext()), \
                 patch("wearable.communications.gateway_sync.urlopen", return_value=FakeResponse()):
                count = queue.sync_once("https://server", "Pi-A", "ca", "cert", "key")
            self.assertEqual(count, 1)
            with open(path, encoding="utf-8") as stream:
                remaining = [json.loads(line) for line in stream if line.strip()]
            self.assertEqual([record["message_id"] for record in remaining], ["m2"])


if __name__ == "__main__":
    unittest.main()
