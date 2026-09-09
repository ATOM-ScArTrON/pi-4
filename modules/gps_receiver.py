"""
NEO-6M GPS receiver module over UART serial. Parses NMEA sentences for coordinates and satellite telemetry.
"""

import time
import serial
import pynmea2
from config import GPS_PORT, GPS_BAUD, GPS_INTERVAL

class GPSReceiver:
    def __init__(self, port=GPS_PORT, baudrate=GPS_BAUD, interval=GPS_INTERVAL):
        self.port = port
        self.baudrate = baudrate
        self.interval = interval
        self.ser = None
        self.lat = "0.0000"
        self.lon = "0.0000"
        self.sats = "0"
        self.has_fix = False
        self.last_read = 0
        self._init_serial()

    def _init_serial(self):
        try:
            self.ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=0.05)
            print(f"[GPS] Connected on {self.port}")
        except Exception as e:
            print(f"[GPS Init Warning]: {e}")
            self.ser = None

    def update(self):
        """Non-blocking UART buffer poll. Updates lat, lon, sats, and has_fix."""
        if not self.ser or not self.ser.is_open:
            return

        now = time.time()
        if now - self.last_read < self.interval:
            return
        self.last_read = now

        try:
            for _ in range(5):
                if self.ser.in_waiting > 0:
                    raw_line = self.ser.readline().decode('ascii', errors='replace').strip()
                    if raw_line.startswith('$GPGGA') or raw_line.startswith('$GNGGA'):
                        msg = pynmea2.parse(raw_line)
                        gps_qual = int(getattr(msg, 'gps_qual', 0) or 0)
                        self.sats = str(getattr(msg, 'num_sats', '0'))

                        if gps_qual > 0 and msg.latitude and msg.longitude:
                            self.lat = f"{msg.latitude:.4f}{msg.lat_dir}"
                            self.lon = f"{msg.longitude:.4f}{msg.lon_dir}"
                            self.has_fix = True
                        else:
                            self.has_fix = False
                        break
                else:
                    break
        except Exception:
            pass

    def get_telemetry(self):
        return {
            "LAT": self.lat,
            "LON": self.lon,
            "SATS": self.sats,
            "FIX": self.has_fix
        }

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

if __name__ == "__main__":
    print(f"Testing GPSReceiver on {GPS_PORT}...")
    gps = GPSReceiver()
    for _ in range(20):
        gps.update()
        fix_s = "LOCK" if gps.has_fix else "SEARCHING"
        print(f"GPS Status: {fix_s} | Sats: {gps.sats} | Lat: {gps.lat} | Lon: {gps.lon}", end="\r")
        time.sleep(0.2)
    print("\nGPSReceiver test complete.")
    gps.close()

