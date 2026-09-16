from types import SimpleNamespace
from wearable.system.module_registry import _gps_payload


def test_gps_payload():
    sensor = SimpleNamespace(has_fix=True, lat="17.4N", lon="78.4E", sats="7")
    assert _gps_payload(sensor) == {"LAT": "17.4N", "LON": "78.4E", "SAT": "7"}
    assert _gps_payload(SimpleNamespace(has_fix=False)) is None
