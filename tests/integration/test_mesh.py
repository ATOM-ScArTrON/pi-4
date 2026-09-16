"""Hardware-free tests for the encrypted JSON payload path."""

import json
import os
import tempfile

from wearable.communications.lora_protocol import LoRaAssembler, LoRaProtocol
from wearable.crypto.mesh_crypto import AsconAuthError, AsconCipher, NonceManager
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal


def run_standalone():
    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    nonce_file = os.path.join(tempfile.gettempdir(), "mesh-test-sender-nonce")
    sender = LoRaProtocol(key, nonce_manager=NonceManager(nonce_file))
    receiver = LoRaProtocol(key)
    assembler = LoRaAssembler(receiver, output_dir=tempfile.gettempdir())

    payload = {"T": "TXT", "MSG": "encrypted JSON survives binary LoRa framing " * 20}
    frames = sender.encode_json_payload(payload)
    diagnostic_plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    diagnostic_nonce = bytes.fromhex("000000010000000000000001")
    diagnostic_cipher = AsconCipher(key)
    diagnostic_ciphertext = diagnostic_cipher.encrypt(diagnostic_plaintext, diagnostic_nonce)
    display_on_terminal("\n[Mesh Diagnostic] plaintext JSON:")
    display_on_terminal(diagnostic_plaintext)
    display_on_terminal(f"[Mesh Diagnostic] nonce: {diagnostic_nonce.hex()}")
    display_on_terminal(f"[Mesh Diagnostic] ciphertext+tag: {diagnostic_ciphertext.hex()}")
    display_on_terminal(f"[Mesh Diagnostic] ciphertext differs from plaintext: {diagnostic_ciphertext != diagnostic_plaintext.encode('utf-8')}")
    display_on_terminal(f"[Mesh Diagnostic] binary frame lengths: {[len(frame) for frame in frames]}")
    display_on_terminal(f"[Mesh Diagnostic] first wire frame: {frames[0].hex()}")
    assert diagnostic_cipher.decrypt(diagnostic_ciphertext, diagnostic_nonce) == diagnostic_plaintext
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

    broadcast_key = bytes.fromhex("aabbccddeeff00112233445566778899")
    sender.broadcast_key = broadcast_key
    receiver.broadcast_key = broadcast_key
    receiver._pseudo_tables.clear()
    alert_frames = sender.encode_broadcast_alert({"level": "HIGH"})
    alert = None
    for frame in alert_frames:
        alert = assembler.process_frame(frame) or alert
    assert alert["type"] == "ALERT"
    assert alert["data"]["level"] == "HIGH"

    print("[Mesh Test] JSON round-trip: PASS")
    print("[Mesh Test] binary framing/chunking: PASS")
    print("[Mesh Test] tamper rejection: PASS")
    print("[Mesh Test] replay rejection: PASS")
    print("[Mesh Test] broadcast alert path: PASS")


if __name__ == "__main__":
    run_standalone()
