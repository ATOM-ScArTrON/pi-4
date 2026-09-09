"""
Bluetooth connection and audio routing manager.
Monitors earbud connection state and assists auto-reconnection.
"""

import re
import subprocess
import shutil

class BluetoothManager:
    def __init__(self):
        self.device_name = "Unknown"
        self.mac_address = None

    def is_connected(self):
        """Checks if a Bluetooth audio sink/source is currently connected to PipeWire or BlueZ."""
        # 1. Check via pw-cli or pactl (PipeWire)
        if shutil.which("pactl"):
            try:
                output = subprocess.check_output(["pactl", "list", "sources", "short"], text=True, stderr=subprocess.DEVNULL)
                for line in output.splitlines():
                    if "bluez" in line.lower():
                        return True
            except Exception:
                pass

        # 2. Check via bluetoothctl
        if shutil.which("bluetoothctl"):
            try:
                output = subprocess.check_output(["bluetoothctl", "info"], text=True, stderr=subprocess.DEVNULL)
                if "Connected: yes" in output:
                    match = re.search(r"Name:\s+(.*)", output)
                    if match:
                        self.device_name = match.group(1)
                    return True
            except Exception:
                pass

        return False

    def reconnect(self, mac_address=None):
        """Attempts to reconnect to known Bluetooth device."""
        target = mac_address or self.mac_address
        if not target or not shutil.which("bluetoothctl"):
            return False

        try:
            cmd = f"bluetoothctl connect {target}"
            subprocess.run(cmd, shell=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self.is_connected()
        except Exception:
            return False

if __name__ == "__main__":
    print("Testing BluetoothManager...")
    bt = BluetoothManager()
    connected = bt.is_connected()
    print(f"Bluetooth Audio Status: {'CONNECTED' if connected else 'DISCONNECTED'}")
    if connected:
        print(f"Device: {bt.device_name}")

