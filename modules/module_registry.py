"""
Central declarative registry describing every module the SessionManager can
lazily activate, switch to, or pull payload data from.

Single source of truth for:
  - how to import + instantiate a module ("path" + "class")
  - what kind of module it is -- determines how SessionManager runs it:
        continuous : polls .update() on its own background thread
        action     : instantiate-only, produces a payload on demand (camera)
        service    : manages its own thread(s) internally (lora, bluetooth)
  - what spoken/typed words activate it (whole-utterance matched)
  - whether it can be promoted to "primary" via switch
  - how to pull a sendable payload out of it, if any (feeds the LoRa
    send-selector menu)
"""

MOD_CONTINUOUS = "continuous"
MOD_ACTION = "action"
MOD_SERVICE = "service"


# ----------------------------------------------------------------------
# Payload extractors -- each takes the live module instance and returns
# a dict of fields to send, or None if there's nothing sendable right now
# (e.g. DHT hasn't taken a reading yet, GPS has no fix, no finger on the
# vitals sensor). Keeping these here rather than on the classes themselves
# keeps "what's sendable" a protocol-layer decision, not a sensor-driver one.
# ----------------------------------------------------------------------

def _dht_payload(m):
    if m.temp is None:
        return None
    return {"TMP": round(m.temp, 1), "HUM": round(m.humidity, 1)}


def _sound_payload(m):
    return {"STA": m.status}


def _motion_payload(m):
    return {"FB": m.dir_fb, "LR": m.dir_lr}


def _vitals_payload(m):
    if not m.finger_detected or m.bpm is None:
        return None
    return {"BPM": round(m.bpm, 1), "SPO2": round(m.spo2, 1) if m.spo2 else 0}


def _gps_payload(m):
    if not m.has_fix:
        return None
    return {"LAT": m.lat, "LON": m.lon, "SAT": m.sats}


def _camera_payload(m):
    """Triggers a fresh capture at send-time rather than returning stale
    data -- freshness matters more than latency for a photo payload."""
    return m.capture_thumbnail_bytes()


REGISTRY = {
    "dht": {
        "kind": MOD_CONTINUOUS,
        "path": "modules.dht_sensor",
        "class": "DHTSensor",
        "aliases": {"dht", "temperature", "humidity"},
        "switchable": True,
        "payload_type": "DHT",
        "payload": _dht_payload,
    },
    "sound": {
        "kind": MOD_CONTINUOUS,
        "path": "modules.sound_sensor",
        "class": "SoundSensor",
        "aliases": {"sound", "noise"},
        "switchable": True,
        "payload_type": "SND",
        "payload": _sound_payload,
    },
    "motion": {
        "kind": MOD_CONTINUOUS,
        "path": "modules.motion_sensor",
        "class": "MotionSensor",
        "aliases": {"motion", "tilt"},
        "switchable": True,
        "payload_type": "MOT",
        "payload": _motion_payload,
    },
    "vitals": {
        "kind": MOD_CONTINUOUS,
        "path": "modules.vitals_sensor",
        "class": "VitalsSensor",
        "aliases": {"vitals", "heart rate", "pulse"},
        "switchable": True,
        "payload_type": "VIT",
        "payload": _vitals_payload,
    },
    "gps": {
        "kind": MOD_CONTINUOUS,
        "path": "modules.gps_receiver",
        "class": "GPSReceiver",
        "aliases": {"gps", "location"},
        "switchable": True,
        "payload_type": "GPS",
        "payload": _gps_payload,
    },
    "camera": {
        "kind": MOD_ACTION,
        "path": "modules.camera",
        "class": "CameraManager",
        "aliases": {"camera", "photo", "picture"},
        "switchable": True,
        "payload_type": "IMG",
        "payload": _camera_payload,
    },
    "lora": {
        "kind": MOD_SERVICE,
        "path": "modules.lora_radio",
        "class": "LoRaRadio",
        "aliases": {"lora", "radio"},
        "switchable": True,
        "payload_type": None,
        "payload": None,
    },
    "bluetooth": {
        "kind": MOD_SERVICE,
        "path": "modules.bluetooth_manager",
        "class": "BluetoothManager",
        "aliases": {"bluetooth", "bt"},
        "switchable": True,
        "payload_type": None,
        "payload": None,
    },
    # stt / tts are deliberately NOT in the registry: they're session-wide
    # utilities (input/output mechanisms), not activatable-by-name peers.
    # SessionManager owns them directly, same as it owns Display.
}

# Reserved session-level commands, whole-utterance matched same as module
# aliases -- handled by SessionManager.route_command() before anything
# falls through to a module's own domain-input handling.
SESSION_COMMANDS = {"exit", "quit", "menu", "status", "switch back", "switch home"}


def find_module_by_alias(utterance: str):
    """Whole-utterance match against every registered module's aliases.
    Returns the registry key, or None if nothing matches exactly -- a
    sentence merely *containing* an alias is never a match."""
    text = utterance.strip().lower()
    for name, entry in REGISTRY.items():
        if text == name or text in entry["aliases"]:
            return name
    return None