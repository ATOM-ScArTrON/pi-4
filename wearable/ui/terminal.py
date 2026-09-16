"""Shared terminal output boundary."""

import builtins


def display_on_terminal(*messages, sep=" ", end="\n"):
    """Display one terminal message through a stable, replaceable API."""
    builtins.print(*messages, sep=sep, end=end)
