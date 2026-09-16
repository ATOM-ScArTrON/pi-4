from types import SimpleNamespace
from wearable.system.module_registry import _sound_payload


def test_sound_payload():
    assert _sound_payload(SimpleNamespace(status="S:L")) == {"STA": "S:L"}
