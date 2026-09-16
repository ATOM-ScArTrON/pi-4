from types import SimpleNamespace
from wearable.system.module_registry import _vitals_payload


def test_vitals_payload():
    sensor = SimpleNamespace(finger_detected=True, bpm=72.45, spo2=98.12)
    assert _vitals_payload(sensor) == {"BPM": 72.5, "SPO2": 98.1}
    assert _vitals_payload(SimpleNamespace(finger_detected=False, bpm=72, spo2=98)) is None
