"""Behavioral parity check between the two independent ascon.py copies.

wearable/crypto/ascon.py and server/crypto/ascon.py are intentionally
separate files (their docstrings differ, since each documents where its
sibling copy lives), so this doesn't diff the source text -- it diffs
behavior, which is what actually matters if one copy drifts from the other.
"""

from wearable.crypto.ascon import (
    ascon_xof as wearable_xof,
    ascon_permutation as wearable_permutation,
    bytes_to_state as wearable_bytes_to_state,
)
from server.crypto.ascon import (
    ascon_xof as server_xof,
    ascon_permutation as server_permutation,
    bytes_to_state as server_bytes_to_state,
)
from wearable.ui.terminal import display_on_terminal


def test_xof_parity_across_copies():
    cases = [(b"", 32), (b"hello world", 32), (b"x" * 100, 64), (b"\x00\x01\x02", 16)]
    for message, length in cases:
        assert wearable_xof(message, length) == server_xof(message, length)


def test_permutation_parity_across_copies():
    state_bytes = bytes(range(40))
    wearable_state = wearable_bytes_to_state(state_bytes)
    server_state = server_bytes_to_state(state_bytes)
    wearable_permutation(wearable_state, 12)
    server_permutation(server_state, 12)
    assert wearable_state == server_state
    display_on_terminal("[Ascon Parity Test] wearable/ vs server/ copies: PASS")


if __name__ == "__main__":
    test_xof_parity_across_copies()
    test_permutation_parity_across_copies()


def run_standalone():
    """Compatibility wrapper for the Raspberry Pi launcher menu."""
    test_xof_parity_across_copies()
    test_permutation_parity_across_copies()