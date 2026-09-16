"""
Bluetooth connection manager — actively hunts for paired devices,
runs an interactive pairing mode if nothing is known, and automatically
routes audio. Integrates full interactive bluetoothctl functionality natively.

The public surface mirrors the Pi 5 PUC's modules/bluetooth.py
(connect / disconnect / pair_new), but exposed through the menu-driven
run_standalone() used by this node's main.py instead of CLI flags, since
this launcher is menu-driven rather than argv-driven.
"""
import re
import time
import threading
import subprocess
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal

try:
    from config import BT_CHECK_INTERVAL, BT_AUTOCONNECT
except ImportError:
    BT_CHECK_INTERVAL = 5
    BT_AUTOCONNECT = True

# Increased to 20 seconds to allow slow headphones to broadcast their readable name
SCAN_DURATION   = 20
CONNECT_TIMEOUT = 10
PIPEWIRE_WAIT   = 3

# Pre-compiled regex for execution speed
DEVICE_REGEX = re.compile(r"Device ([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(.+)")
WPCTL_ID_REGEX = re.compile(r"\*?\s*(\d+)\.")


class BluetoothCtl:
    """Persistent bluetoothctl process wrapper for fast, interactive commands."""
    def __init__(self):
        self.proc = None
        self.output_lines = []
        # output_lines is written by the reader thread and read/cleared by
        # the main thread — a plain list isn't safe for that without a lock.
        self._lock = threading.Lock()
        self._ready = False

        try:
            self.proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0
            )
        except Exception as e:
            # A missing `bluetoothctl` binary degrades gracefully instead of
            # crashing BluetoothManager() (and main.py) outright.
            print(f"[BluetoothCtl Init Warning]: {e}")
            return

        self._start_reader()
        self._ready = True
        time.sleep(0.5)

        # Guarantee power, agents, and active scanning are on immediately
        self.send("power on", wait=0.5)
        self.send("agent on", wait=0.5)
        self.send("default-agent", wait=0.5)
        self.send("scan on", wait=0.5)

    def _start_reader(self):
        def reader():
            for line in self.proc.stdout:
                decoded = line.decode(errors='ignore').strip()
                if decoded:
                    with self._lock:
                        self.output_lines.append(decoded)
        threading.Thread(target=reader, daemon=True).start()

    def send(self, cmd: str, wait: float = 1.0):
        if not self._ready:
            return
        self.proc.stdin.write((cmd + "\n").encode())
        self.proc.stdin.flush()
        time.sleep(wait)

    def get_output(self) -> list:
        with self._lock:
            lines = self.output_lines.copy()
            self.output_lines.clear()
        return lines

    def close(self):
        if not self._ready:
            return
        # Stop the scan cleanly so the Pi's radio isn't stuck in discovery mode
        self.send("scan off", wait=0.5)
        self.send("quit", wait=0.5)
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.terminate()


def parse_devices(lines: list) -> list:
    """Extracts MAC and Name from bluetoothctl output securely."""
    devices = []
    seen = set()
    for line in lines:
        match = DEVICE_REGEX.search(line)
        if match:
            mac  = match.group(1).upper()
            name = match.group(2).strip()
            # Ignore MAC-only nameless broadcasts
            if mac not in seen and not re.match(r"^[0-9A-Fa-f]{2}(-[0-9A-Fa-f]{2}){5}$", name):
                devices.append((mac, name))
                seen.add(mac)
    return devices


def pick_device(devices: list, title: str = "Available devices", lcd=None) -> tuple:
    """Interactive terminal menu for selecting a discovered device."""
    if not devices:
        return None, None
    print(f"\n[BT] {title}:")
    for i, (mac, name) in enumerate(devices):
        print(f"  [{i + 1}] {name}  —  {mac}")

    while True:
        try:
            idx = int(input("\nEnter number to select (0 to cancel): ").strip())
            if idx == 0:
                return None, None
            if 1 <= idx <= len(devices):
                mac, name = devices[idx - 1]
                if lcd:
                    lcd.log("DEVICE SELECTED", name[:16])
                else:
                    print(f"[STATUS] Selected: {name}")
                return mac, name
            print(f"Enter a number between 1 and {len(devices)}.")
        except ValueError:
            print("Please enter a valid number.")


def set_default_audio(mac: str, name: str, lcd=None):
    """Finds whichever sink/source matches this MAC/name in PipeWire and sets it default."""
    print("[BT] Waiting for PipeWire to register device...")
    time.sleep(PIPEWIRE_WAIT)

    try:
        result = subprocess.run(["wpctl", "status"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=5)
        out = result.stdout.decode(errors="ignore").strip()
    except subprocess.SubprocessError:
        print("[BT] Failed to query PipeWire status.")
        if lcd:
            lcd.log("BT AUDIO", "PW QUERY FAIL", duration=2.5)
        return

    if name not in out and mac not in out:
        print("[BT] Device not visible in PipeWire. Try again.")
        if lcd:
            lcd.log("BT AUDIO", "NOT VISIBLE", duration=2.5)
        return

    # Anchored regex (node id immediately followed by the device name) —
    # ported from the Pi 5 PUC's bluetooth.py. This avoids matching the
    # wrong node when the name also happens to appear elsewhere in the
    # wpctl status output (e.g. under "Sink endpoints").
    sink_id = None
    name_pattern = re.compile(r"\*?\s*(\d+)\.\s+" + re.escape(name))
    for line in out.splitlines():
        match = name_pattern.search(line)
        if match:
            sink_id = match.group(1)
            break

    source_id = None
    for line in out.splitlines():
        if "bluez_input" in line and mac in line:
            match = WPCTL_ID_REGEX.search(line)
            if match:
                source_id = match.group(1)
                break

    if sink_id:
        subprocess.run(["wpctl", "set-default", sink_id])
        print(f"[BT] Default audio output → {name} (id: {sink_id})")
    else:
        print("[BT] Could not find output sink.")

    if source_id:
        subprocess.run(["wpctl", "set-default", source_id])
        print(f"[BT] Default audio input  → bluez_input (id: {source_id})")
    else:
        print("[BT] No mic source found.")

    if lcd:
        lcd.log("BT AUDIO SET", name[:16], duration=2.5)


class BluetoothManager:
    def __init__(self, check_interval=BT_CHECK_INTERVAL, lcd=None):
        self.check_interval = check_interval
        self.connected = False
        self.mac = None
        self.name = "Unknown"
        self.last_check = 0
        self.lcd = lcd

        # Ensure adapter is on immediately at startup before any passive checks
        subprocess.run(["bluetoothctl", "power", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _get_connected_device(self):
        """Fast, non-blocking check using a standard subprocess for the main loop."""
        try:
            out = subprocess.check_output(
                ["bluetoothctl", "devices", "Connected"],
                text=True,
                stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                match = DEVICE_REGEX.search(line)
                if match:
                    return match.group(1).upper(), match.group(2).strip()
        except subprocess.SubprocessError:
            pass
        return None, "Unknown"

    def is_connected(self):
        """Throttled check, safe to call every main-loop tick."""
        now = time.time()
        if now - self.last_check < self.check_interval:
            return self.connected
        self.last_check = now

        mac, name = self._get_connected_device()
        if mac:
            self.mac, self.name = mac, name
            self.connected = True
        else:
            self.mac, self.name = None, "Unknown"
            self.connected = False
        return self.connected

    # ------------------------------------------------------------------
    # Public API — mirrors the Pi 5 PUC's bluetooth.py connect/disconnect
    # ------------------------------------------------------------------

    def connect(self, mac=None, name=None, verbose=False):
        """
        Mirrors bluetooth.py's connect(). If a MAC is given, connects to
        that specific device (the Pi 5 PUC always targets one known
        headset). If no MAC is given, hunts through every paired device
        instead and connects to the first that responds, since this node
        isn't locked to a single known device.
        """
        if mac:
            return self._connect_specific(mac, name or "Unknown", verbose=verbose)
        return self.try_connect_paired(verbose=verbose)

    def _connect_specific(self, mac, name, verbose=False):
        bt = BluetoothCtl()
        try:
            bt.send(f"info {mac}", wait=1.0)
            info = "\n".join(bt.get_output())

            if "Connected: yes" in info:
                print(f"[BT] Already connected to {name}.")
                self.mac, self.name, self.connected = mac, name, True
                set_default_audio(mac, name, lcd=self.lcd)
                return True

            print(f"[BT] Connecting to {name}...")
            if self.lcd:
                self.lcd.log("BT CONNECTING", name[:16], duration=2.0)
            bt.send(f"connect {mac}", wait=2.0)

            connected = False
            for i in range(CONNECT_TIMEOUT):
                bt.send(f"info {mac}", wait=0.5)
                info = "\n".join(bt.get_output())
                if "Connected: yes" in info:
                    connected = True
                    break
                if verbose:
                    print(f"[BT] Waiting... ({i + 1}/{CONNECT_TIMEOUT})")

            if connected:
                print(f"[BT] Connected to {name}.")
                if self.lcd:
                    self.lcd.log("BT CONNECTED", name[:16], duration=2.5)
                self.mac, self.name, self.connected = mac, name, True
                set_default_audio(mac, name, lcd=self.lcd)
                return True

            print("[BT] Connection failed.")
            if self.lcd:
                self.lcd.log("BT CONNECT", "FAILED", duration=2.5)
            return False
        finally:
            bt.close()

    def disconnect(self, mac=None, name=None):
        """
        Mirrors bluetooth.py's disconnect(). Targets a given MAC, or
        whichever device this manager currently tracks as connected.
        """
        target_mac = mac or self.mac
        target_name = name or self.name

        if not target_mac:
            print("[BT] No device to disconnect.")
            if self.lcd:
                self.lcd.log("BT DISCONNECT", "NONE CONNECTED", duration=2.0)
            return False

        bt = BluetoothCtl()
        try:
            bt.send(f"disconnect {target_mac}", wait=2.0)
        finally:
            bt.close()

        print(f"[BT] Disconnected from {target_name}.")
        if self.lcd:
            self.lcd.log("BT DISCONNECTED", target_name[:16], duration=2.5)

        if target_mac == self.mac:
            self.connected = False
            self.mac, self.name = None, "Unknown"
        return True

    def try_connect_paired(self, verbose=False):
        """Hunts for known paired devices and attempts connection."""
        bt = BluetoothCtl()
        try:
            bt.send("devices Paired", wait=0.5)
            devices = parse_devices(bt.get_output())

            if not devices:
                return False

            for mac, name in devices:
                if verbose:
                    print(f"  -> Polling paired device: {name} ({mac})...")
                bt.send(f"connect {mac}", wait=3.0)

                bt.send(f"info {mac}", wait=0.5)
                info = "\n".join(bt.get_output())

                if "Connected: yes" in info:
                    self.mac = mac
                    self.name = name
                    self.connected = True
                    if self.lcd:
                        self.lcd.log("BT CONNECTED", name[:16], duration=2.5)
                    set_default_audio(self.mac, self.name, lcd=self.lcd)
                    return True

            return False
        finally:
            # Any exception above previously left the bluetoothctl child
            # process/reader thread running, and the adapter stuck in
            # "scan on" since only close() turns it off — guard with finally.
            bt.close()

    def scan_and_pair(self) -> bool:
        """Scan, pair, trust, and connect to a new device using an interactive prompt."""
        bt = BluetoothCtl()
        try:
            print(f"[BT] Scanning for {SCAN_DURATION} seconds...")
            if self.lcd:
                self.lcd.log("BT SCANNING", f"{SCAN_DURATION}s...", duration=SCAN_DURATION)

            # Scan was already initiated in __init__, just wait out the duration
            for i in range(SCAN_DURATION):
                time.sleep(1)
                print(f"[BT] Scanning... ({i + 1}/{SCAN_DURATION})", end="\r")

            bt.send("scan off", wait=1.0)
            bt.send("devices", wait=1.0)
            lines = bt.get_output()
            print()

            devices = parse_devices(lines)
            if not devices:
                print("[BT] No devices found.")
                if self.lcd:
                    self.lcd.log("BT SCAN", "NO DEVICES")
                return False

            mac, name = pick_device(devices, title="Discovered devices", lcd=self.lcd)
            if not mac:
                return False

            bt.send(f"info {mac}", wait=0.5)
            info = "\n".join(bt.get_output())

            if "Paired: yes" not in info:
                print(f"[BT] Pairing with {name}...")
                if self.lcd:
                    self.lcd.log("BT PAIRING", name[:16], duration=5.0)
                bt.send(f"pair {mac}", wait=5.0)
                bt.send(f"trust {mac}", wait=1.0)
                bt.get_output()
                print("[BT] Paired and trusted.")

            print(f"[BT] Connecting to {name}...")
            if self.lcd:
                self.lcd.log("BT CONNECTING", name[:16], duration=2.0)
            bt.send(f"connect {mac}", wait=2.0)

            connected = False
            for i in range(CONNECT_TIMEOUT):
                bt.send(f"info {mac}", wait=0.5)
                info = "\n".join(bt.get_output())
                if "Connected: yes" in info:
                    connected = True
                    break
                print(f"[BT] Waiting... ({i + 1}/{CONNECT_TIMEOUT})")

            if connected:
                print(f"[BT] Successfully connected to {name}.")
                if self.lcd:
                    self.lcd.log("BT CONNECTED", name[:16])

                self.mac = mac
                self.name = name
                self.connected = True
                set_default_audio(mac, name, lcd=self.lcd)
                return True
            else:
                print("[BT] Connection failed.")
                if self.lcd:
                    self.lcd.log("BT CONNECT", "FAILED")
                return False
        finally:
            bt.close()

    def pair_new(self):
        """Alias matching bluetooth.py's naming — scans, pairs, trusts, and connects."""
        return self.scan_and_pair()

    def autoconnect_async(self, on_result=None):
        """Background thread for the full integrated system startup."""
        if not BT_AUTOCONNECT:
            return

        def _worker():
            if not self._get_connected_device()[0]:
                self.try_connect_paired(verbose=False)

            is_conn = self.is_connected()
            if on_result:
                on_result(is_conn, self.name if is_conn else None)

        threading.Thread(target=_worker, daemon=True).start()


# --- STANDALONE FUNCTION FOR YOUR MENU LAUNCHER ---
def run_standalone(lcd=None):
    """
    Menu-driven equivalent of the Pi 5 PUC's bluetooth.py --scan / --pair /
    --disconnect flags. Since this node's main.py is menu-driven rather than
    argv-driven, the same connect / disconnect / pair_new actions are
    surfaced here as a small submenu instead.
    """
    own_lcd = lcd is None
    if own_lcd:
        from wearable.ui.display import Display
        lcd = Display()

    bt_manager = BluetoothManager(check_interval=2, lcd=lcd)

    SUBMENU = {
        "1": "Connect to known device",
        "2": "Disconnect",
        "3": "Pair new device (scan)",
        "4": "Auto-manage (loop until Ctrl+C)",
        "0": "Back",
    }

    try:
        while True:
            print("\n==================================================")
            print("               Bluetooth Manager")
            print("==================================================")
            for k in sorted(SUBMENU, key=lambda x: (len(x), x)):
                print(f"  [{k}] {SUBMENU[k]}")

            choice = input("\nSelect an option: ").strip()

            if choice == "0":
                break

            elif choice == "1":
                print("[Action] Searching for known paired devices online...")
                if not bt_manager.connect(verbose=True):
                    print("[BT] No known devices could be connected.")
                    lcd.log("BT CONNECT", "NO DEVICES", duration=2.5)

            elif choice == "2":
                bt_manager.disconnect()

            elif choice == "3":
                bt_manager.pair_new()

            elif choice == "4":
                _run_auto_loop(bt_manager)

            else:
                print("Invalid option, please enter a number from the list.")

    except KeyboardInterrupt:
        print("\nExiting Bluetooth Manager...")
    finally:
        if own_lcd:
            lcd.close()


def _run_auto_loop(bt_manager):
    """The original hunt-then-scan behavior, now one option among several."""
    print("\n[BT] Auto-managing connection. Press Ctrl+C to return to the Bluetooth menu.\n")
    last_state = None

    try:
        while True:
            # 1. Check if we already have an active connection
            if bt_manager.is_connected():
                if last_state != True:
                    print(f"\n[Status] Connected to active device: {bt_manager.name}")
                    print("[Status] Monitoring connection... (Press Ctrl+C to return to menu)")
                    last_state = True
                time.sleep(2)
                continue

            # 2. We lost connection or never had one
            if last_state != False:
                print("\n[Status] No device connected.")
                last_state = False

            # 3. Try to hunt for known paired devices
            print("[Action] Searching for known paired devices online...")
            if bt_manager.try_connect_paired(verbose=True):
                continue

            # 4. No known devices responded, launch the interactive scanner
            print("[Action] No paired devices found online. Launching Scanner...")
            if bt_manager.pair_new():
                continue

            # 5. If everything failed or user aborted, wait before restarting the loop
            print("\n[Status] No devices could be connected. Retrying in 5 seconds...")
            time.sleep(5)

    except KeyboardInterrupt:
        print("\n[BT] Returning to Bluetooth menu...")


if __name__ == "__main__":
    run_standalone()
