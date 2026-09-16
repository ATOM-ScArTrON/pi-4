"""Entry point for the full-system coordinator.

The coordinator implementation remains private to the launcher for now so
that its existing hardware lifecycle is preserved; this module owns the
public module boundary used by the menu.
"""

import sys


def run_full_system():
    launcher = sys.modules.get("__main__")
    if launcher is None or not hasattr(launcher, "_run_full_system"):
        from main import _run_full_system as launcher_runner
    else:
        launcher_runner = launcher._run_full_system
    return launcher_runner()
