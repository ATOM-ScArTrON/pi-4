from types import SimpleNamespace
from wearable.system.module_registry import _motion_payload


def test_motion_payload():
    sensor = SimpleNamespace(dir_fb="Forward", dir_lr="Level")
    assert _motion_payload(sensor) == {"FB": "Forward", "LR": "Level"}
