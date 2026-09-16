"""
ascon.py
--------
Minimal, dependency-free, pure-Python implementation of the Ascon
permutation and the Ascon-XOF extendable-output function (Ascon v1.2 /
NIST SP 800-232 family).

Only the pieces this project actually uses are implemented:
  - ascon_permutation(state, rounds)   the core 320-bit permutation
  - ascon_xof(message, hashlength)     sponge-based XOF, arbitrary output length

This file intentionally does NOT implement Ascon-AEAD (encrypt/decrypt) --
mesh_crypto.py builds its own XOF-keystream + XOF-tag construction on top
of ascon_xof() instead, per project requirements (Ascon-XOF is the
mandated primitive, not Ascon-AEAD).

NOTE: this follows the published Ascon permutation/sponge structure
(S-box, linear layer, round constants, padding rule) but has not been
run against the official NIST/Ascon-team test vectors. Treat it as a
reference-quality building block: fine for an offline field-mesh demo,
but re-validate against the official KATs (or swap in a vetted library,
e.g. `pip install ascon`) before relying on it for anything that has to
resist a real adversary.
"""

MASK64 = 0xFFFFFFFFFFFFFFFF

# Round constants for the 12-round permutation (p^a). p^b (6 rounds) just
# uses the last 6 of these, since rounds always run from (12 - rounds) to 12.
ROUND_CONSTANTS = [0xF0, 0xE1, 0xD2, 0xC3, 0xB4, 0xA5, 0x96, 0x87, 0x78, 0x69, 0x5A, 0x4B]


def _rotr(val, r):
    """64-bit rotate-right."""
    val &= MASK64
    return ((val >> r) | (val << (64 - r))) & MASK64


def _bytes_to_int(data):
    return int.from_bytes(data, byteorder="big")


def _int_to_bytes(val, length=8):
    return (val & MASK64).to_bytes(length, byteorder="big")


def _zero_bytes(n):
    return b"\x00" * n


def bytes_to_state(data):
    """40 bytes -> list of 5 64-bit words."""
    return [_bytes_to_int(data[8 * w:8 * w + 8]) for w in range(5)]


def ascon_permutation(S, rounds=12):
    """In-place Ascon permutation over state S (list of 5 64-bit ints)."""
    for r in range(12 - rounds, 12):
        # --- add round constant ---
        S[2] ^= ROUND_CONSTANTS[r]

        # --- substitution layer (5-bit S-box, bit-sliced across the 5 words) ---
        S[0] ^= S[4]
        S[4] ^= S[3]
        S[2] ^= S[1]
        T = [(S[i] ^ MASK64) & S[(i + 1) % 5] for i in range(5)]
        for i in range(5):
            S[i] ^= T[(i + 1) % 5]
        S[1] ^= S[0]
        S[0] ^= S[4]
        S[3] ^= S[2]
        S[2] ^= MASK64

        # --- linear diffusion layer ---
        S[0] ^= _rotr(S[0], 19) ^ _rotr(S[0], 28)
        S[1] ^= _rotr(S[1], 61) ^ _rotr(S[1], 39)
        S[2] ^= _rotr(S[2], 1) ^ _rotr(S[2], 6)
        S[3] ^= _rotr(S[3], 10) ^ _rotr(S[3], 17)
        S[4] ^= _rotr(S[4], 7) ^ _rotr(S[4], 41)

        for i in range(5):
            S[i] &= MASK64


def ascon_xof(message: bytes, hashlength: int = 32) -> bytes:
    """
    Ascon-Xof: sponge-based extendable-output function.
    message: arbitrary-length bytes to absorb.
    hashlength: number of output bytes to squeeze (arbitrary length, unlike
                the fixed-output Ascon-Hash).
    """
    a, b, rate = 12, 12, 8  # Ascon-Xof uses a=b=12, rate=8 bytes

    iv = bytes([0, rate * 8, a, a - b, 0]) + _zero_bytes(35)
    S = bytes_to_state(iv)
    ascon_permutation(S, a)

    # --- padding: 0x80 then zero bytes, always at least 1 padding byte ---
    pad_len = rate - (len(message) % rate) - 1
    if pad_len < 0:
        pad_len += rate
    m_padded = message + b"\x80" + _zero_bytes(pad_len)

    # --- absorb ---
    for block in range(0, len(m_padded) - rate, rate):
        S[0] ^= _bytes_to_int(m_padded[block:block + rate])
        ascon_permutation(S, b)
    S[0] ^= _bytes_to_int(m_padded[len(m_padded) - rate:len(m_padded)])

    # --- squeeze ---
    out = b""
    ascon_permutation(S, a)
    while len(out) < hashlength:
        out += _int_to_bytes(S[0], 8)
        if len(out) < hashlength:
            ascon_permutation(S, b)
    return out[:hashlength]
