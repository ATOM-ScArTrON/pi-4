from types import SimpleNamespace
from wearable.system.module_registry import _dht_payload


def test_dht_payload():
    assert _dht_payload(SimpleNamespace(temp=25.54, humidity=61.26)) == {"TMP": 25.5, "HUM": 61.3}
    assert _dht_payload(SimpleNamespace(temp=None, humidity=None)) is None
