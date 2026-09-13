"""
Modular hardware drivers and subsystems for Raspberry Pi 4 edge node.
"""
import sys
import os

# Ensure package root is always in path
pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)