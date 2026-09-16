"""JSON application payloads over binary, length-prefixed LoRa frames."""

import base64
import json
import os
import time

from config import RECEIVED_DIR
from modules.mesh_crypto import AsconAuthError, AsconCipher, NonceManager, ReplayGuard

MAX_FRAME_BYTES = 120
FRAME_OVERHEAD = 4 + 12 + 16  # pseudo ID + nonce + Ascon-XOF tag
# Each decrypted fragment is JSON containing base64 chunk metadata. Keep the
# application chunk smaller than the theoretical 88-byte budget so that this
# metadata also remains inside the 120-byte frame limit.
MAX_PLAINTEXT_BYTES = 24
PSEUDO_ID_SIZE = 4
NONCE_SIZE = 12
TAG_SIZE = 16


class LoRaProtocol:
    READING_TYPES = {"DHT", "SND", "MOT", "VIT", "GPS"}

    def __init__(self, key, nonce_manager=None, replay_guard=None):
        self.cipher = AsconCipher(key)
        self.nonce_manager = nonce_manager or NonceManager()
        self.replay_guard = replay_guard or ReplayGuard()

    def _pseudo_id(self, nonce):
        # Ascon-XOF is the only cryptographic primitive used by the project.
        from modules.ascon import ascon_xof
        return ascon_xof(self.cipher.key + nonce, PSEUDO_ID_SIZE)

    def _frame_json(self, plaintext_json):
        raw = plaintext_json.encode("utf-8")
        chunks = [raw[i:i + MAX_PLAINTEXT_BYTES]
                  for i in range(0, len(raw), MAX_PLAINTEXT_BYTES)] or [b""]
        session_id = f"{int(time.time() * 1000)}"
        frames = []
        for index, chunk in enumerate(chunks, start=1):
            fragment = json.dumps({"I": session_id, "N": len(chunks), "X": index,
                                   "P": base64.b64encode(chunk).decode("ascii")},
                                  separators=(",", ":"))
            # A fragment is itself JSON plaintext, then becomes opaque binary.
            nonce = self.nonce_manager.next()
            ciphertext = self.cipher.encrypt(fragment, nonce)
            body = self._pseudo_id(nonce) + nonce + ciphertext
            if len(body) > MAX_FRAME_BYTES:
                raise ValueError("encrypted frame exceeds the 120-byte LoRa limit")
            frames.append(bytes([len(body)]) + body)
        return frames

    def encode_json_payload(self, payload):
        """Encode one application payload dict as encrypted binary frames."""
        return self._frame_json(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    def encode_telemetry(self, telemetry_data):
        return self.encode_json_payload({
            "T": "TEL", "TS": time.strftime("%H:%M:%S"),
            "BPM": int(telemetry_data.get("BPM", 0)),
            "SPO2": int(telemetry_data.get("SPO2", 0)),
            "TMP": int(telemetry_data.get("TEMP", 0)),
            "HUM": int(telemetry_data.get("HUM", 0)),
            "SND": telemetry_data.get("SOUND", "Q"),
            "FB": telemetry_data.get("FB", "Level"),
            "LR": telemetry_data.get("LR", "Level"),
            "LAT": telemetry_data.get("LAT", "0.0000"),
            "LON": telemetry_data.get("LON", "0.0000"),
            "SAT": str(telemetry_data.get("SATS", "0")),
        })

    def encode_reading(self, packet_type, data_dict):
        payload = {"T": packet_type, "TS": time.strftime("%H:%M:%S")}
        payload.update(data_dict)
        return self.encode_json_payload(payload)

    def encode_text(self, text):
        return self.encode_json_payload({"T": "TXT", "MSG": text,
                                         "TS": time.strftime("%H:%M:%S")})

    def encode_binary(self, data_bytes, data_type="IMG"):
        return self.encode_json_payload({"T": data_type,
                                         "D": base64.b64encode(data_bytes).decode("ascii")})


class LoRaAssembler:
    def __init__(self, protocol, output_dir=RECEIVED_DIR):
        self.protocol = protocol
        self.output_dir = output_dir
        self.sessions = {}

    def process_frame(self, frame):
        """Consume one complete binary frame and return a decoded app packet."""
        if not isinstance(frame, (bytes, bytearray)) or len(frame) < 1:
            return None
        body_length = frame[0]
        body = bytes(frame[1:])
        if body_length != len(body) or body_length > MAX_FRAME_BYTES:
            return None
        if len(body) < FRAME_OVERHEAD:
            return None
        pseudo_id = body[:PSEUDO_ID_SIZE]
        nonce = body[PSEUDO_ID_SIZE:PSEUDO_ID_SIZE + NONCE_SIZE]
        ciphertext = body[PSEUDO_ID_SIZE + NONCE_SIZE:]
        if pseudo_id != self.protocol._pseudo_id(nonce):
            return None
        if self.protocol.replay_guard.seen(nonce):
            return None
        try:
            plaintext = self.protocol.cipher.decrypt(ciphertext, nonce)
            fragment = json.loads(plaintext)
            self.protocol.replay_guard.remember(nonce)
        except (AsconAuthError, ValueError, TypeError, json.JSONDecodeError):
            return None

        try:
            session_id = fragment["I"]
            total = int(fragment["N"])
            index = int(fragment["X"])
            chunk = base64.b64decode(fragment["P"], validate=True)
            if total < 1 or not 1 <= index <= total:
                return None
        except (KeyError, TypeError, ValueError, base64.binascii.Error):
            return None

        session = self.sessions.setdefault(session_id, {"total": total, "chunks": {}})
        if session["total"] != total:
            self.sessions.pop(session_id, None)
            return None
        session["chunks"][index] = chunk
        if len(session["chunks"]) != total:
            return None
        try:
            payload = json.loads(b"".join(session["chunks"][i] for i in range(1, total + 1)))
        except (KeyError, TypeError, json.JSONDecodeError):
            self.sessions.pop(session_id, None)
            return None
        self.sessions.pop(session_id, None)
        return self._to_application_packet(payload, session_id)

    def _to_application_packet(self, payload, session_id):
        packet_type = payload.get("T")
        if packet_type == "TEL":
            return {"type": "VITALS", "data": payload}
        if packet_type in LoRaProtocol.READING_TYPES:
            return {"type": "READING", "sensor": packet_type, "data": payload}
        if packet_type == "TXT":
            return {"type": "TEXT", "text": payload.get("MSG", ""),
                    "timestamp": payload.get("TS", time.strftime("%H:%M:%S"))}
        if packet_type in ("IMG", "AUD"):
            try:
                raw_bytes = base64.b64decode(payload.get("D", ""), validate=True)
                suffix = "jpg" if packet_type == "IMG" else "wav"
                filepath = os.path.join(self.output_dir, f"received_{session_id}.{suffix}")
                with open(filepath, "wb") as stream:
                    stream.write(raw_bytes)
                return {"type": "IMAGE" if packet_type == "IMG" else "AUDIO",
                        "path": filepath, "bytes": len(raw_bytes)}
            except (OSError, ValueError, base64.binascii.Error):
                return None
        return {"type": "JSON", "data": payload}
