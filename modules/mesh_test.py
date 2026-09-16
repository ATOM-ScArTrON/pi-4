"""Compatibility entry point; integration tests live in the tests package."""

from tests.mesh_test import run_standalone

__all__ = ["run_standalone"]


if __name__ == "__main__":
    run_standalone()
