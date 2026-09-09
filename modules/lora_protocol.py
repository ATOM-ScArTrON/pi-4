"""
Multi-data framing and chunking protocol for LoRa transmissions.
Supports Vitals, Arbitrary Text, Chunked Compressed Images, and Audio clips.
"""

import os
import time
import json
import zlib
import base64
from config import RECEIVED_DIR

MAX_CHUNK_BYTES = 120  # Base64 payload chunk size to guarantee packet fits in <= 220 bytes

class LoRaProtocol:
    @staticmethod
    def encode_vitals(vitals_data):
        """Encodes sensor vitals into a single LoRa packet string."""
        pkt = {
            "T": "VIT",
            "TS": time.strftime("%H:%M:%S"),
            "BPM": int(vitals_data.get("BPM", 0)),
            "SPO2": int(vitals_data.get("SPO2", 0)),
            "TMP": int(vitals_data.get("TEMP", 0)),
            "HUM": int(vitals_data.get("HUM", 0)),
            "SND": vitals_data.get("SOUND", "Q"),
            "FB": vitals_data.get("FB", "Level"),
            "LR": vitals_data.get("LR", "Level"),
            "LAT": vitals_data.get("LAT", "0.0000"),
            "LON": vitals_data.get("LON", "0.0000"),
            "SAT": str(vitals_data.get("SATS", "0"))
        }
        return [json.dumps(pkt) + "\n"]

    @staticmethod
    def encode_text(text):
        """Encodes arbitrary text message, chunking into multiple packets if long."""
        text_bytes = text.encode("utf-8")
        session_id = f"txt_{int(time.time()) % 10000}"
        chunks = [text_bytes[i:i + MAX_CHUNK_BYTES] for i in range(0, len(text_bytes), MAX_CHUNK_BYTES)]
        total = len(chunks)

        packets = []
        for idx, chunk in enumerate(chunks):
            pkt = {
                "T": "TXT",
                "ID": session_id,
                "IDX": idx + 1,
                "TOT": total,
                "MSG": chunk.decode("utf-8", errors="replace"),
                "TS": time.strftime("%H:%M:%S")
            }
            packets.append(json.dumps(pkt) + "\n")
        return packets

    @staticmethod
    def encode_binary(data_bytes, data_type="IMG"):
        """Encodes binary data (e.g. JPEG image or WAV audio) into chunked Base64 packets."""
        crc = hex(zlib.crc32(data_bytes) & 0xFFFFFFFF)[2:]
        b64_str = base64.b64encode(data_bytes).decode("ascii")
        prefix = "img" if data_type == "IMG" else "aud"
        session_id = f"{prefix}_{int(time.time()) % 10000}"

        chunks = [b64_str[i:i + MAX_CHUNK_BYTES] for i in range(0, len(b64_str), MAX_CHUNK_BYTES)]
        total = len(chunks)

        packets = []
        for idx, chunk in enumerate(chunks):
            pkt = {
                "T": data_type,
                "ID": session_id,
                "IDX": idx + 1,
                "TOT": total,
                "CRC": crc,
                "D": chunk
            }
            packets.append(json.dumps(pkt) + "\n")
        return packets


class LoRaAssembler:
    """Buffers incoming chunked packets and reassembles complete multi-data payloads."""
    def __init__(self, output_dir=RECEIVED_DIR):
        self.output_dir = output_dir
        self.sessions = {}  # session_id -> {"type": ..., "chunks": {}, "total": ..., "crc": ...}

    def process_packet(self, raw_line):
        """Parses a received raw serial line. Returns completed payload dict or None."""
        raw_line = raw_line.strip()
        if not (raw_line.startswith("{") and raw_line.endswith("}")):
            return None

        try:
            pkt = json.loads(raw_line)
        except Exception:
            return None

        pkt_type = pkt.get("T")

        # 1. Single-packet Vitals
        if pkt_type == "VIT":
            return {
                "type": "VITALS",
                "data": pkt
            }

        # 2. Text Message
        if pkt_type == "TXT":
            session_id = pkt.get("ID", "default")
            idx = pkt.get("IDX", 1)
            tot = pkt.get("TOT", 1)
            msg = pkt.get("MSG", "")

            if tot == 1:
                return {
                    "type": "TEXT",
                    "text": msg,
                    "timestamp": pkt.get("TS", time.strftime("%H:%M:%S"))
                }

            if session_id not in self.sessions:
                self.sessions[session_id] = {"type": "TXT", "chunks": {}, "total": tot, "time": time.time()}

            self.sessions[session_id]["chunks"][idx] = msg
            if len(self.sessions[session_id]["chunks"]) == tot:
                full_text = "".join(self.sessions[session_id]["chunks"][i] for i in range(1, tot + 1))
                del self.sessions[session_id]
                return {
                    "type": "TEXT",
                    "text": full_text,
                    "timestamp": pkt.get("TS", time.strftime("%H:%M:%S"))
                }
            return None

        # 3. Binary chunk (IMG or AUD)
        if pkt_type in ["IMG", "AUD"]:
            session_id = pkt.get("ID")
            idx = pkt.get("IDX")
            tot = pkt.get("TOT")
            crc = pkt.get("CRC")
            chunk_data = pkt.get("D")

            if session_id not in self.sessions:
                self.sessions[session_id] = {
                    "type": pkt_type,
                    "chunks": {},
                    "total": tot,
                    "crc": crc,
                    "time": time.time()
                }

            self.sessions[session_id]["chunks"][idx] = chunk_data

            if len(self.sessions[session_id]["chunks"]) == tot:
                # All chunks arrived! Reconstruct binary
                b64_full = "".join(self.sessions[session_id]["chunks"][i] for i in range(1, tot + 1))
                del self.sessions[session_id]

                try:
                    raw_bytes = base64.b64decode(b64_full)
                    # Verify CRC
                    calc_crc = hex(zlib.crc32(raw_bytes) & 0xFFFFFFFF)[2:]
                    if crc and calc_crc.lower() != crc.lower():
                        print(f"[LoRa Assembler] CRC mismatch! Expected {crc}, got {calc_crc}")

                    ext = "jpg" if pkt_type == "IMG" else "wav"
                    filename = f"received_{session_id}.{ext}"
                    filepath = os.path.join(self.output_dir, filename)

                    with open(filepath, "wb") as f:
                        f.write(raw_bytes)

                    print(f"[LoRa Assembler] Successfully reassembled {pkt_type} -> {filepath} ({len(raw_bytes)} bytes)")
                    return {
                        "type": "IMAGE" if pkt_type == "IMG" else "AUDIO",
                        "path": filepath,
                        "bytes": len(raw_bytes)
                    }
                except Exception as e:
                    print(f"[LoRa Assembler Error]: {e}")
                    return None

        return None

if __name__ == "__main__":
    print("Testing LoRaProtocol and Assembler...")
    proto = LoRaProtocol()
    assembler = LoRaAssembler()

    # Test Vitals
    vitals_pkts = proto.encode_vitals({"BPM": 75, "SPO2": 99, "TEMP": 28, "HUM": 60})
    result = assembler.process_packet(vitals_pkts[0])
    assert result["type"] == "VITALS", "Vitals test failed"
    print("Vitals encoding/decoding: OK")

    # Test Text
    text_pkts = proto.encode_text("Patient telemetry is normal. No abnormalities detected.")
    for p in text_pkts:
        res = assembler.process_packet(p)
    assert res["type"] == "TEXT", "Text test failed"
    print(f"Text encoding/decoding: OK ('{res['text']}')")

    # Test Image binary chunking
    dummy_img = b"\xFF\xD8\xFF" + b"A" * 350 + b"\xFF\xD9"
    img_pkts = proto.encode_binary(dummy_img, data_type="IMG")
    print(f"Image chunked into {len(img_pkts)} packets.")
    for p in img_pkts:
        res = assembler.process_packet(p)
    assert res and res["type"] == "IMAGE", "Image reassembly failed"
    print(f"Image reassembly: OK -> {res['path']}")
    print("All LoRa protocol tests passed successfully!")

