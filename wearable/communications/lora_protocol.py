"""JSON application payloads over binary, length-prefixed LoRa frames."""

import base64
import json
import os
import struct
import time

from config import RECEIVED_DIR, LORA_DEBUG
from wearable.crypto.mesh_crypto import AsconAuthError, AsconCipher, NonceManager, ReplayGuard

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

    def __init__(self, key=None, keyset=None, peer_id="default", broadcast_key=None,
                 nonce_manager=None, replay_guard=None, epoch_clock=None):
        self.keyset = {name: bytes(value) for name, value in (keyset or {}).items()}
        if key is None:
            if not self.keyset:
                raise ValueError("a pairwise key or keyset is required")
            key = next(iter(self.keyset.values()))
        self.peer_id = peer_id if peer_id in self.keyset else next(iter(self.keyset), peer_id)
        self.cipher = AsconCipher(key)
        self.broadcast_key = bytes(broadcast_key) if broadcast_key else None
        self._pseudo_tables = {}
        self.nonce_manager = nonce_manager or NonceManager()
        self.replay_guard = replay_guard or ReplayGuard()
        self.epoch_clock = epoch_clock

    def _active_epochs(self):
        if self.epoch_clock is not None:
            return self.epoch_clock.get_active_epochs()
        return [int(time.time()) // 3600, int(time.time()) // 3600 + 1]

    def _refresh_pseudo_tables(self):
        keys = dict(self.keyset or {self.peer_id: self.cipher.key})
        if self.broadcast_key:
            keys["__broadcast__"] = self.broadcast_key
        active = {int(epoch) & 0xFFFFFFFF for epoch in self._active_epochs()}
        for epoch_number in active:
            epoch = struct.pack(">I", epoch_number)
            if epoch in self._pseudo_tables:
                continue
            self._pseudo_tables[epoch] = {
                self._pseudo_id(epoch + b"\x00" * 8, key): (peer_id, key)
                for peer_id, key in keys.items()
            }
        self._pseudo_tables = {
            epoch: table for epoch, table in self._pseudo_tables.items()
            if epoch in {struct.pack(">I", n) for n in active}
        }

    def _pseudo_id(self, nonce, key=None):
        # Ascon-XOF is the only cryptographic primitive used by the project.
        from wearable.crypto.ascon import ascon_xof
        return ascon_xof((key or self.cipher.key) + nonce[:4], PSEUDO_ID_SIZE)

    def _frame_json(self, plaintext_json):
        raw = plaintext_json.encode("utf-8")
        chunks = [raw[i:i + MAX_PLAINTEXT_BYTES]
                  for i in range(0, len(raw), MAX_PLAINTEXT_BYTES)] or [b""]
        session_id = f"{time.time_ns()}"
        frames = []
        if self.epoch_clock is not None:
            epoch, _ = self.epoch_clock.get_epoch()
            self.nonce_manager.set_epoch(epoch)
        for index, chunk in enumerate(chunks, start=1):
            fragment = json.dumps({"I": session_id, "N": len(chunks), "X": index,
                                   "P": base64.b64encode(chunk).decode("ascii")},
                                  separators=(",", ":"))
            # A fragment is itself JSON plaintext, then becomes opaque binary.
            nonce = self.nonce_manager.next(self.peer_id)
            ciphertext = self.cipher.encrypt(fragment, nonce)
            body = self._pseudo_id(nonce, self.cipher.key) + nonce + ciphertext
            if len(body) > MAX_FRAME_BYTES:
                raise ValueError("encrypted frame exceeds the 120-byte LoRa limit")
            frames.append(bytes([len(body)]) + body)
        return frames

    def encode_json_payload(self, payload):
        """Encode one application payload dict as encrypted binary frames."""
        return self._frame_json(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

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

    def encode_broadcast_alert(self, alert):
        """Encode an alert with the separately provisioned broadcast key."""
        key = getattr(self, "broadcast_key", None)
        if key is None:
            raise ValueError("mission_broadcast_key is not provisioned")
        original = self.cipher
        self.cipher = AsconCipher(key)
        try:
            return self.encode_json_payload({"T": "ALERT", "DATA": alert})
        finally:
            self.cipher = original


class LoRaAssembler:
    def __init__(self, protocol, output_dir=RECEIVED_DIR):
        self.protocol = protocol
        self.output_dir = output_dir
        self.sessions = {}
        self.protocol._refresh_pseudo_tables()

    def process_frame(self, frame):
        if not isinstance(frame, (bytes, bytearray)) or len(frame) < 1:
            return None
        body_length = frame[0]
        body = bytes(frame[1:])
        if body_length != len(body) or body_length > MAX_FRAME_BYTES:
            if LORA_DEBUG: print("[LoRa RX DEBUG] rejected: body length mismatch")
            return None
        if len(body) < FRAME_OVERHEAD:
            if LORA_DEBUG: print("[LoRa RX DEBUG] rejected: body shorter than frame overhead")
            return None
        pseudo_id = body[:PSEUDO_ID_SIZE]
        nonce = body[PSEUDO_ID_SIZE:PSEUDO_ID_SIZE + NONCE_SIZE]
        ciphertext = body[PSEUDO_ID_SIZE + NONCE_SIZE:]
        cipher, peer_id = self._lookup_cipher(pseudo_id, nonce)
        if cipher is None:
            if LORA_DEBUG:
                print(f"[LoRa RX DEBUG] rejected: no pseudo-ID match for epoch {nonce[:4].hex()} "
                      f"(active epochs: {[e.hex() for e in self.protocol._pseudo_tables]})")
            return None
        try:
            plaintext = cipher.decrypt(ciphertext, nonce)
            fragment = json.loads(plaintext)
        except (AsconAuthError, ValueError, TypeError, json.JSONDecodeError) as e:
            if LORA_DEBUG: print(f"[LoRa RX DEBUG] rejected: decrypt/parse failed ({type(e).__name__})")
            return None
        if not self.protocol.replay_guard.accept_highest(peer_id, nonce):
            if LORA_DEBUG: print(f"[LoRa RX DEBUG] rejected: replay guard (peer={peer_id})")
            return None
        try:
            session_id = fragment["I"]
            total = int(fragment["N"])
            index = int(fragment["X"])
            chunk = base64.b64decode(fragment["P"], validate=True)
            if total < 1 or not 1 <= index <= total:
                if LORA_DEBUG: print("[LoRa RX DEBUG] rejected: fragment index/total out of range")
                return None
        except (KeyError, TypeError, ValueError, base64.binascii.Error) as e:
            if LORA_DEBUG: print(f"[LoRa RX DEBUG] rejected: malformed fragment metadata ({type(e).__name__})")
            return None
        session = self.sessions.setdefault(session_id, {"total": total, "chunks": {}})
        if session["total"] != total:
            if LORA_DEBUG: print(f"[LoRa RX DEBUG] rejected: session '{session_id}' total mismatch, dropping session")
            self.sessions.pop(session_id, None)
            return None
        session["chunks"][index] = chunk
        if len(session["chunks"]) != total:
            if LORA_DEBUG: print(f"[LoRa RX DEBUG] fragment {index}/{total} buffered for session '{session_id}', waiting on the rest")
            return None
        try:
            payload = json.loads(b"".join(session["chunks"][i] for i in range(1, total + 1)))
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            if LORA_DEBUG: print(f"[LoRa RX DEBUG] rejected: reassembled payload invalid JSON ({type(e).__name__})")
            self.sessions.pop(session_id, None)
            return None
        self.sessions.pop(session_id, None)
        if LORA_DEBUG: print(f"[LoRa RX DEBUG] session '{session_id}' fully reassembled -> {payload.get('T')}")
        return self._to_application_packet(payload, session_id)

    def _lookup_cipher(self, pseudo_id, nonce):
        self.protocol._refresh_pseudo_tables()
        epoch = nonce[:4]
        table = self.protocol._pseudo_tables.get(epoch)
        if table is None:
            return None, None
        match = table.get(pseudo_id)
        return (AsconCipher(match[1]), match[0]) if match else (None, None)

    def _to_application_packet(self, payload, session_id):
        packet_type = payload.get("T")
        ts = payload.get("TS", time.strftime("%H:%M:%S"))
        if packet_type in LoRaProtocol.READING_TYPES:
            data = {k: v for k, v in payload.items() if k not in ("T", "TS")}
            return {"type": packet_type, "timestamp": ts, **data}
        if packet_type == "TXT":
            return {"type": "TEXT", "timestamp": ts,
                    "text": payload.get("MSG", "")}
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
        if packet_type == "ALERT":
            return {"type": "ALERT", "data": payload.get("DATA")}
        return {"type": "JSON", "data": payload}