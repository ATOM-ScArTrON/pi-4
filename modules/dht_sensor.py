"""
DHT11 Temperature and Humidity sensor driver with wiring diagnostics.
"""

import time
import board
import adafruit_dht
from config import DHT_PIN, DHT_INTERVAL

class DHTSensor:
    def __init__(self, pin_number=DHT_PIN, interval=DHT_INTERVAL):
        self.pin_number = pin_number
        self.interval = interval
        self.temp = None
        self.humidity = None
        self.is_connected = None
        self.last_read = 0
        self.consecutive_errors = 0
        self.dht_device = None
        self._init_sensor()

    def _init_sensor(self):
        try:
            # Map BCM pin number to CircuitPython board pin
            pin = getattr(board, f"D{self.pin_number}")
            self.dht_device = adafruit_dht.DHT11(pin, use_pulseio=False)
        except Exception as e:
            print(f"[DHT11 Init Warning]: {e}")
            self.dht_device = None

    def update(self):
        """Non-blocking poll. Updates self.temp and self.humidity."""
        if not self.dht_device:
            return

        now = time.time()
        if now - self.last_read < self.interval:
            return
        self.last_read = now

        try:
            t = self.dht_device.temperature
            h = self.dht_device.humidity
            if t is not None and h is not None:
                self.temp = t
                self.humidity = h
                self.consecutive_errors = 0
                if self.is_connected is not True:
                    self.is_connected = True
                    print(f"\n[DHT11 STATUS] Wiring is CORRECT! Sensor detected on GPIO{self.pin_number}.")
                print(f"[DHT11 READING] Temperature: {self.temp:.1f}°C | Humidity: {self.humidity:.1f}%")

        except RuntimeError as e:
            err_msg = str(e)
            self.consecutive_errors += 1
            if "not found" in err_msg.lower() or "wiring" in err_msg.lower():
                if self.is_connected is not False:
                    self.is_connected = False
                    print(f"\n[DHT11 WIRING ERROR] Sensor NOT detected on GPIO{self.pin_number}! Check wiring ({e}).")
            elif self.consecutive_errors >= 3:
                if self.is_connected is not False:
                    self.is_connected = False
                    print(f"\n[DHT11 WIRING WARNING] Multiple failed read attempts ({e}). Check VCC/GND/Data.")
            else:
                pass  # Minor transient timing jitter

        except Exception as e:
            print(f"\n[DHT11 Read Error]: {e}")

    def close(self):
        if self.dht_device:
            try:
                self.dht_device.exit()
            except Exception:
                pass

if __name__ == "__main__":
    print("Testing DHTSensor module on GPIO17...")
    sensor = DHTSensor()
    for _ in range(5):
        time.sleep(1.0)
        sensor.update()
    sensor.close()
    print("DHTSensor test finished.")

