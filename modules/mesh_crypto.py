"""
mesh_crypto.py
--------------
Cryptography module for the LoRa field-mesh (Tier 2 of the Client-Server
Architecture doc). Implements the wrap()/unwrap() step called out in the
implementation roadmap (§Step 1: "mesh_auth.py core: wrap()/unwrap()
... keyed by pairwise session keys"), but built on Ascon-XOF rather than
AES-GCM.

Design
======
Ascon-XOF (see ascon.py) is a sponge-based extendable-output function, not
an AEAD cipher by itself -- there's no built-in "encrypt" mode. This module
builds a small, explicit construction out of it, kept deliberately simple
so it's auditable in one read-through:

  keystream = Ascon-XOF(key || nonce, len(plaintext))
  ciphertext = plaintext XOR keystream

  tag = Ascon-XOF(key || nonce || ciphertext, TAG_SIZE)
  wire_bytes = ciphertext || tag

This mirrors the wire layout already specified in the architecture doc
(§3.1 Wire packet layout: Nonce | Ciphertext | Auth Tag) -- only the
Pseudo ID field and the AES-GCM primitive are out of scope here; this
module only owns the crypto step, matching the "modular architecture"
requirement so a future pseudo-ID / nonce-counter layer (§3.2, §3.3) can
sit on top of it without touching this file.

Every module-level primitive lives in ascon.py; this file only ever
imports ascon_xof() from it -- no other crypto dependency.

Class attributes (per requirements)
====================================
An AsconCipher instance carries exactly four pieces of cryptographic
state as attributes, always in sync with the most recent operation:
    self.key         bytes   -- the pairwise/broadcast session key (static)
    self.nonce        bytes   -- the nonce used for the most recent op
    self.plaintext    bytes   -- JSON-encoded payload (telemetry or chat)
    self.ciphertext   bytes   -- ciphertext || tag from the most recent op

Payload convention
===================
The plaintext is always a JSON-encoded payload -- never a raw Python
dict -- so telemetry (VIT/DHT/GPS/...) and chat (TEXT) packets go through
the exact same encode/decode path as everything else in lora_protocol.py.
encrypt() accepts a JSON string or UTF-8 JSON bytes; decrypt() returns the
JSON string, with self.plaintext holding the raw JSON bytes in between.
"""

import hmac
import json
import os
import struct
import threading
import time
from modules.terminal import display_on_terminal

print = display_on_terminal

try:
    from .ascon import ascon_xof
except ImportError:  # supports `python modules/mesh_crypto.py` on the Pi
    from ascon import ascon_xof

TAG_SIZE = 16          # bytes, authentication tag appended to ciphertext
KEY_SIZE = 16          # bytes, pairwise/broadcast session key length
NONCE_SIZE = 12        # bytes, matches the §3.1 wire layout (4B epoch || 8B counter)


class AsconAuthError(Exception):
    """Raised when a ciphertext fails tag verification on decrypt()."""


class AsconCipher:
    """
    Modular Ascon-XOF cipher: one instance per session key (e.g. one per
    pairwise peer, or one shared instance for mission_broadcast_key).
    Call encrypt()/decrypt() per message with a fresh nonce each time --
    nonce persistence/uniqueness is the caller's responsibility (see
    §3.2 of the architecture doc), this class only performs the crypto.
    """

    def __init__(self, key: bytes):
        if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_SIZE:
            raise ValueError(f"key must be {KEY_SIZE} bytes, got {0 if key is None else len(key)}")

        # --- class attributes -------------------------------------------------
        self.key = bytes(key)      # session key, fixed for the life of this instance
        self.nonce = None          # bytes, set by the most recent encrypt()/decrypt()
        self.plaintext = None      # bytes, JSON-encoded payload
        self.ciphertext = None     # bytes, ciphertext || tag
        # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        return ascon_xof(self.key + nonce, length)

    def _tag(self, nonce: bytes, ciphertext: bytes) -> bytes:
        return ascon_xof(self.key + nonce + ciphertext, TAG_SIZE)

    @staticmethod
    def _xor(a: bytes, b: bytes) -> bytes:
        return bytes(x ^ y for x, y in zip(a, b))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encrypt(self, payload: str | bytes, nonce: bytes) -> bytes:
        """
        payload: a JSON string or UTF-8 JSON bytes. The protocol deliberately
                 accepts JSON, not a Python dict, so serialization happens at
                 the payload boundary and is identical on client and server.
        nonce:   NONCE_SIZE bytes, unique per (key, message) -- caller-supplied
                 so the persisted monotonic-counter scheme (§3.2) can own
                 nonce generation independently of this class.
        Returns ciphertext || tag, and leaves self.plaintext/self.ciphertext/
        self.nonce set to the values used for this call.
        """
        if len(nonce) != NONCE_SIZE:
            raise ValueError(f"nonce must be {NONCE_SIZE} bytes, got {len(nonce)}")

        self.nonce = bytes(nonce)
        if isinstance(payload, str):
            self.plaintext = payload.encode("utf-8")
        elif isinstance(payload, (bytes, bytearray)):
            self.plaintext = bytes(payload)
        else:
            raise TypeError("payload must be a JSON string or UTF-8 bytes")
        try:
            json.loads(self.plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("payload must contain valid JSON") from exc

        keystream = self._keystream(self.nonce, len(self.plaintext))
        ct = self._xor(self.plaintext, keystream)
        tag = self._tag(self.nonce, ct)

        self.ciphertext = ct + tag
        return self.ciphertext

    def decrypt(self, ciphertext: bytes, nonce: bytes) -> str:
        """
        ciphertext: ciphertext || tag, as produced by encrypt() (or received
                    off the LoRa link).
        nonce:      the NONCE_SIZE-byte nonce that was used to produce it
                    (carried alongside the ciphertext on the wire, per §3.1).
        Returns the plaintext JSON string. Raises AsconAuthError (and leaves
        self.plaintext unset) if the tag doesn't verify -- callers should
        treat that as "drop silently, no reply, no log naming the sender"
        per §3.6, not as a crash.
        """
        if len(nonce) != NONCE_SIZE:
            raise ValueError(f"nonce must be {NONCE_SIZE} bytes, got {len(nonce)}")
        if len(ciphertext) < TAG_SIZE:
            raise AsconAuthError("ciphertext shorter than tag size")

        self.nonce = bytes(nonce)
        self.ciphertext = bytes(ciphertext)

        ct, tag = self.ciphertext[:-TAG_SIZE], self.ciphertext[-TAG_SIZE:]
        expected_tag = self._tag(self.nonce, ct)

        if not hmac.compare_digest(tag, expected_tag):
            raise AsconAuthError("tag verification failed")

        keystream = self._keystream(self.nonce, len(ct))
        self.plaintext = self._xor(ct, keystream)
        try:
            json.loads(self.plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AsconAuthError("decrypted payload is not valid JSON") from exc
        return self.plaintext.decode("utf-8")


# ------------------------------------------------------------------
# Thin module-level wrap()/unwrap(), matching the naming used in the
# architecture doc's roadmap ("Insert mesh_auth.wrap()/unwrap() at the
# single join point before ser.write() ... and after successful frame
# read in the RX worker"). These are convenience one-shot wrappers around
# AsconCipher for call sites that don't want to hold a cipher instance.
# ------------------------------------------------------------------

def wrap(key: bytes, nonce: bytes, payload: str | bytes) -> bytes:
    """One-shot encrypt: return ciphertext || tag for a JSON payload."""
    return AsconCipher(key).encrypt(payload, nonce)


def unwrap(key: bytes, nonce: bytes, ciphertext: bytes) -> str:
    """One-shot decrypt: return the JSON payload, or raise AsconAuthError."""
    return AsconCipher(key).decrypt(ciphertext, nonce)


class NonceManager:
    """Process-local monotonic nonce source: 4-byte epoch + 8-byte counter."""

    def __init__(self, state_file=None):
        self.state_file = state_file
        self._lock = threading.Lock()
        self.epoch = int(time.time()) & 0xFFFFFFFF
        self.counter = 0
        if state_file:
            self._load()

    def _load(self):
        try:
            raw = open(self.state_file, "rb").read()
            if len(raw) == 12:
                self.epoch, self.counter = struct.unpack(">IQ", raw)
        except (OSError, ValueError, struct.error):
            pass

    def next(self) -> bytes:
        with self._lock:
            self.counter += 1
            if self.counter > 0xFFFFFFFFFFFFFFFF:
                raise OverflowError("nonce counter exhausted")
            nonce = struct.pack(">IQ", self.epoch, self.counter)
            if self.state_file:
                directory = os.path.dirname(self.state_file)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                temp = self.state_file + ".tmp"
                with open(temp, "wb") as stream:
                    stream.write(nonce)
                os.replace(temp, self.state_file)
            return nonce


class ReplayGuard:
    """Small replay cache for already accepted nonces."""

    def __init__(self, max_entries=4096):
        self.max_entries = max_entries
        self._seen = set()
        self._lock = threading.Lock()

    def accept(self, nonce: bytes) -> bool:
        with self._lock:
            if nonce in self._seen:
                return False
            self._seen.add(bytes(nonce))
            if len(self._seen) > self.max_entries:
                self._seen.pop()
            return True

    def seen(self, nonce: bytes) -> bool:
        with self._lock:
            return bytes(nonce) in self._seen

    def remember(self, nonce: bytes):
        with self._lock:
            self._seen.add(bytes(nonce))
            if len(self._seen) > self.max_entries:
                self._seen.pop()


def run_standalone():
    """Demo/self-test: round-trips a sample telemetry payload and a sample
    chat payload through AsconCipher, mirroring how the rest of this
    project's modules expose a run_standalone() for manual testing."""
    import os

    key = os.urandom(KEY_SIZE)
    cipher = AsconCipher(key)

    print("[MeshCrypto] Demo key:", key.hex())

    # --- Telemetry-style payload (mirrors module_registry.py's payload extractors) ---
    nonce1 = os.urandom(NONCE_SIZE)
    telemetry = json.dumps({"T": "VIT", "TS": "12:00:00", "BPM": 72.4, "SPO2": 98.1}, separators=(",", ":"))
    ct1 = cipher.encrypt(telemetry, nonce1)
    print(f"\n[Telemetry] plaintext JSON : {cipher.plaintext}")
    print(f"[Telemetry] ciphertext+tag : {ct1.hex()}")

    recovered1 = cipher.decrypt(ct1, nonce1)
    print(f"[Telemetry] recovered      : {recovered1}")
    assert json.loads(recovered1) == json.loads(telemetry), "Telemetry round-trip mismatch!"

    # --- Chat-style payload (mirrors chat_mode.py's TEXT packets) ---
    nonce2 = os.urandom(NONCE_SIZE)
    chat = json.dumps({"T": "TXT", "MSG": "rendezvous at checkpoint 3"}, separators=(",", ":"))
    ct2 = cipher.encrypt(chat, nonce2)
    print(f"\n[Chat] plaintext JSON : {cipher.plaintext}")
    print(f"[Chat] ciphertext+tag : {ct2.hex()}")

    recovered2 = cipher.decrypt(ct2, nonce2)
    print(f"[Chat] recovered      : {recovered2}")
    assert json.loads(recovered2) == json.loads(chat), "Chat round-trip mismatch!"

    # --- Tamper check: flipping a ciphertext byte must fail verification ---
    tampered = bytearray(ct2)
    tampered[0] ^= 0xFF
    try:
        cipher.decrypt(bytes(tampered), nonce2)
        print("\n[Tamper check] FAILED -- tampered ciphertext was accepted!")
    except AsconAuthError:
        print("\n[Tamper check] OK -- tampered ciphertext correctly rejected.")

    print("\n[MeshCrypto] All round-trip checks passed.")


if __name__ == "__main__":
    run_standalone()
