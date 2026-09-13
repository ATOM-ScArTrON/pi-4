"""
Multi-data framing and chunking protocol for LoRa transmissions.
"""
import os
import time
import json
import zlib
import base64
from config import RECEIVED_DIR

MAX_CHUNK_BYTES = 120

class LoRaProtocol:
    @staticmethod
    def encode_vitals(vitals_data):
        """Bundled telemetry packet used by the full-system loop (main.py) --
        carries every sensor's latest value in one packet. Tagged 'TEL'
        (not 'VIT') so it doesn't collide with the per-sensor VIT reading
        type below, which has a different, smaller schema."""
        return [json.dumps({
            "T": "TEL", "TS": time.strftime("%H:%M:%S"),
            "BPM": int(vitals_data.get("BPM", 0)), "SPO2": int(vitals_data.get("SPO2", 0)),
            "TMP": int(vitals_data.get("TEMP", 0)), "HUM": int(vitals_data.get("HUM", 0)),
            "SND": vitals_data.get("SOUND", "Q"), "FB": vitals_data.get("FB", "Level"),
            "LR": vitals_data.get("LR", "Level"), "LAT": vitals_data.get("LAT", "0.0000"),
            "LON": vitals_data.get("LON", "0.0000"), "SAT": str(vitals_data.get("SATS", "0"))
        }) + "\n"]

    # Per-sensor reading types -- one sensor's fields per packet, so a
    # receiver never has to guess whether an absent/zeroed field means
    # "not sent" vs "read as zero". Used by the LoRa send-selector menu.
    READING_TYPES = {"DHT", "SND", "MOT", "VIT", "GPS"}

    @staticmethod
    def encode_reading(packet_type, data_dict):
        """Generic single-packet encoder for one sensor's reading. Unlike
        encode_vitals() above (a fixed bundle for the full-system loop),
        this sends exactly the fields for one sensor, tagged with its own
        type code."""
        payload = {"T": packet_type, "TS": time.strftime("%H:%M:%S")}
        payload.update(data_dict)
        return [json.dumps(payload) + "\n"]

    @staticmethod
    def _encode_chunked(data_bytes, data_type):
        prefix = {"TXT": "txt", "IMG": "img", "AUD": "aud"}[data_type]
        session_id = f"{prefix}_{int(time.time() * 1000) % 10000}"
        
        if data_type == "TXT":
            encoded_str = data_bytes.decode("utf-8", errors="replace")
            chunks = [encoded_str[i:i + MAX_CHUNK_BYTES] for i in range(0, len(encoded_str), MAX_CHUNK_BYTES)]
            base_pkt = {"T": "TXT", "ID": session_id, "TOT": len(chunks), "TS": time.strftime("%H:%M:%S")}
            return [json.dumps({**base_pkt, "IDX": idx + 1, "MSG": chunk}) + "\n" for idx, chunk in enumerate(chunks)]
        
        crc = hex(zlib.crc32(data_bytes) & 0xFFFFFFFF)[2:]
        b64_str = base64.b64encode(data_bytes).decode("ascii")
        chunks = [b64_str[i:i + MAX_CHUNK_BYTES] for i in range(0, len(b64_str), MAX_CHUNK_BYTES)]
        base_pkt = {"T": data_type, "ID": session_id, "TOT": len(chunks), "CRC": crc}
        return [json.dumps({**base_pkt, "IDX": idx + 1, "D": chunk}) + "\n" for idx, chunk in enumerate(chunks)]

    @classmethod
    def encode_text(cls, text):
        return cls._encode_chunked(text.encode("utf-8"), "TXT")

    @classmethod
    def encode_binary(cls, data_bytes, data_type="IMG"):
        return cls._encode_chunked(data_bytes, data_type)


class LoRaAssembler:
    def __init__(self, output_dir=RECEIVED_DIR):
        self.output_dir = output_dir
        self.sessions = {}

    def process_packet(self, raw_line):
        raw_line = raw_line.strip()
        if not (raw_line.startswith("{") and raw_line.endswith("}")): return None

        try: pkt = json.loads(raw_line)
        except json.JSONDecodeError: return None

        pkt_type = pkt.get("T")
        if pkt_type == "TEL": return {"type": "VITALS", "data": pkt}
        if pkt_type in LoRaProtocol.READING_TYPES:
            return {"type": "READING", "sensor": pkt_type, "data": pkt}

        session_id, idx, tot = pkt.get("ID"), pkt.get("IDX", 1), pkt.get("TOT", 1)
        if tot == 1 and pkt_type == "TXT":
            return {"type": "TEXT", "text": pkt.get("MSG", ""), "timestamp": pkt.get("TS", time.strftime("%H:%M:%S"))}

        if session_id not in self.sessions:
            self.sessions[session_id] = {"type": pkt_type, "chunks": {}, "total": tot, "crc": pkt.get("CRC")}
        
        session = self.sessions[session_id]
        session["chunks"][idx] = pkt.get("MSG") if pkt_type == "TXT" else pkt.get("D")

        if len(session["chunks"]) == tot:
            full_data = "".join(session["chunks"][i] for i in range(1, tot + 1))
            del self.sessions[session_id]

            if pkt_type == "TXT":
                return {"type": "TEXT", "text": full_data, "timestamp": pkt.get("TS", time.strftime("%H:%M:%S"))}
            
            try:
                raw_bytes = base64.b64decode(full_data)
                filepath = os.path.join(self.output_dir, f"received_{session_id}.{'jpg' if pkt_type == 'IMG' else 'wav'}")
                with open(filepath, "wb") as f: f.write(raw_bytes)
                return {"type": "IMAGE" if pkt_type == "IMG" else "AUDIO", "path": filepath, "bytes": len(raw_bytes)}
            except Exception as e:
                print(f"[LoRa Assembler Error]: {e}")
        return None