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
from wearable.system.epoch_clock import EPOCH_DURATION
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
        return ascon_xof(b"\x01" + self.key + nonce, length)

    def _tag(self, nonce: bytes, ciphertext: bytes) -> bytes:
        return ascon_xof(b"\x02" + self.key + nonce + ciphertext, TAG_SIZE)

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


BOOT_FORWARD_SKIP = 1000


class NonceManager:
    """Persistent, per-peer monotonic nonce source: 4-byte epoch + 8-byte counter.

    Counters are stored per peer so each pairwise channel has independent
    nonce space. On boot, every counter advances by BOOT_FORWARD_SKIP (+1,000)
    to guarantee no reuse after sudden power loss. Writes are atomic:
    stream.flush() + os.fsync() before os.replace().
    """

    def __init__(self, state_file=None):
        self.state_file = state_file
        self._lock = threading.Lock()
        # epoch is shared (wall-clock based); counters are per-peer
        self.epoch = int(time.time()) // EPOCH_DURATION
        self.counters: dict[str, int] = {}  # peer_id -> counter
        if state_file:
            self._load()

    def _state_path(self, peer_id):
        """Return a peer-specific state file path."""
        if self.state_file is None:
            return None
        base, ext = os.path.splitext(self.state_file)
        safe = peer_id.replace("/", "_").replace("\\", "_")
        return f"{base}.{safe}{ext}"

    def _load(self):
        """Load persisted counters for all peer state files adjacent to state_file."""
        if not self.state_file:
            return
        base, ext = os.path.splitext(self.state_file)
        directory = os.path.dirname(self.state_file) or "."
        try:
            for name in os.listdir(directory):
                full = os.path.join(directory, name)
                if not (name.startswith(os.path.basename(base) + ".") and name.endswith(ext)):
                    continue
                # extract peer_id from filename
                prefix = os.path.basename(base) + "."
                suffix = ext
                peer_id = name[len(prefix):-len(suffix)] if suffix else name[len(prefix):]
                try:
                    raw = open(full, "rb").read()
                    if len(raw) == 12:
                        _, counter = struct.unpack(">IQ", raw)
                        # advance by BOOT_FORWARD_SKIP to handle power-loss nonce reuse
                        self.counters[peer_id] = counter + BOOT_FORWARD_SKIP
                except (OSError, struct.error):
                    pass
        except OSError:
            pass

    def _save(self, peer_id, counter):
        """Atomically persist the counter for a peer."""
        path = self._state_path(peer_id)
        if path is None:
            return
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "wb") as stream:
            stream.write(struct.pack(">IQ", self.epoch, counter))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def set_epoch(self, epoch):
        """Use the current provisioned/GPS-derived epoch for new nonces."""
        with self._lock:
            self.epoch = int(epoch) & 0xFFFFFFFF

    def next(self, peer_id: str = "default") -> bytes:
        """Return the next nonce bytes for the given peer."""
        with self._lock:
            counter = self.counters.get(peer_id, 0) + 1
            if counter > 0xFFFFFFFFFFFFFFFF:
                raise OverflowError("nonce counter exhausted")
            self.counters[peer_id] = counter
            nonce = struct.pack(">IQ", self.epoch, counter)
            self._save(peer_id, counter)
            return nonce


class ReplayGuard:
    """Replay guard that persists highest-seen nonce per peer to disk.

    On accept_highest(), if the incoming nonce is strictly newer than
    the stored maximum it is accepted and the new maximum is atomically
    written to disk (flush + fsync before replace) so the guard survives
    a restart.
    """

    _GUARD_FILE = os.path.expanduser("~/.wearable_replay_guard.json")

    def __init__(self, max_entries=4096, guard_file=None):
        self.max_entries = max_entries
        self._guard_file = guard_file or self._GUARD_FILE
        self._seen = set()
        self._highest: dict[str, int] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            with open(self._guard_file, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            if isinstance(data, dict):
                self._highest = {k: int(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    def _save(self):
        """Atomically persist _highest to disk."""
        temp = self._guard_file + ".tmp"
        try:
            directory = os.path.dirname(self._guard_file)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(temp, "w", encoding="utf-8") as stream:
                json.dump(self._highest, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self._guard_file)
        except OSError:
            pass

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

    @staticmethod
    def _counter(nonce):
        if len(nonce) != NONCE_SIZE:
            return None
        return int.from_bytes(nonce, "big")

    def accept_highest(self, peer_id, nonce: bytes) -> bool:
        """Accept only a strictly newer epoch/counter for this peer.
        Persists the new maximum to disk when accepted.
        """
        value = self._counter(nonce)
        if value is None:
            return False
        with self._lock:
            previous = self._highest.get(peer_id, -1)
            if value <= previous:
                return False
            self._highest[peer_id] = value
            self._save()
            return True
