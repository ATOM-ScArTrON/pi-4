"""Hardware-free tests for the encrypted JSON payload path."""

import json
import os
import tempfile

from modules.lora_protocol import LoRaAssembler, LoRaProtocol
from modules.mesh_crypto import AsconAuthError, AsconCipher, NonceManager
from modules.terminal import display_on_terminal

print = display_on_terminal


def run_standalone():
    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    nonce_file = os.path.join(tempfile.gettempdir(), "mesh-test-sender-nonce")
    sender = LoRaProtocol(key, nonce_manager=NonceManager(nonce_file))
    receiver = LoRaProtocol(key)
    assembler = LoRaAssembler(receiver, output_dir=tempfile.gettempdir())

    payload = {"T": "TXT", "MSG": "encrypted JSON survives binary LoRa framing " * 20}
    frames = sender.encode_json_payload(payload)
    recovered = None
    for frame in frames:
        recovered = assembler.process_frame(frame)
    assert recovered["type"] == "TEXT"
    assert recovered["text"] == payload["MSG"]

    cipher = AsconCipher(key)
    nonce = bytes.fromhex("000000010000000000000001")
    ciphertext = bytearray(cipher.encrypt(json.dumps(payload), nonce))
    ciphertext[0] ^= 1
    try:
        cipher.decrypt(bytes(ciphertext), nonce)
    except AsconAuthError:
        pass
    else:
        raise AssertionError("tampered ciphertext was accepted")

    replay = sender.encode_json_payload({"T": "TXT", "MSG": "replay"})
    first = None
    for frame in replay:
        first = assembler.process_frame(frame) or first
    assert first["text"] == "replay"
    assert all(assembler.process_frame(frame) is None for frame in replay)

    print("[Mesh Test] JSON round-trip: PASS")
    print("[Mesh Test] binary framing/chunking: PASS")
    print("[Mesh Test] tamper rejection: PASS")
    print("[Mesh Test] replay rejection: PASS")


if __name__ == "__main__":
    run_standalone()
