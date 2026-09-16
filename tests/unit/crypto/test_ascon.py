"""Known basic properties for the Ascon-XOF primitive."""

from wearable.crypto.ascon import ascon_xof
from wearable.ui.terminal import display_on_terminal


def run_standalone():
    first = ascon_xof(b"hello world", 32)
    assert first == ascon_xof(b"hello world", 32)
    assert first != ascon_xof(b"hello world!", 32)
    assert len(ascon_xof(b"hello world", 64)) == 64
    display_on_terminal("[Ascon Test] XOF consistency and length: PASS")


if __name__ == "__main__":
    run_standalone()
