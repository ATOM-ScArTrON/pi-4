"""
DHT11 Temperature and Humidity sensor driver.
"""
import time
import board
import adafruit_dht
from modules.terminal import display_on_terminal

print = display_on_terminal
from config import DHT_PIN, DHT_INTERVAL

class DHTSensor:
    def __init__(self, pin_number=DHT_PIN, interval=DHT_INTERVAL):
        self.interval = interval
        self.temp = self.humidity = self.is_connected = None
        self.last_read = self.consecutive_errors = 0
        self.dht_device = None
        
        try:
            self.dht_device = adafruit_dht.DHT11(getattr(board, f"D{pin_number}"), use_pulseio=False)
        except Exception as e:
            print(f"[DHT11 Init Warning]: {e}")

    def update(self):
        now = time.time()
        if not self.dht_device or now - self.last_read < self.interval: return
        self.last_read = now

        try:
            t, h = self.dht_device.temperature, self.dht_device.humidity
            if t is not None and h is not None:
                self.temp, self.humidity, self.consecutive_errors = t, h, 0
                if not self.is_connected:
                    self.is_connected = True
                    print("\n[DHT11 STATUS] Wiring is CORRECT!")
        except RuntimeError as e:
            self.consecutive_errors += 1
            if self.consecutive_errors >= 3 and self.is_connected is not False:
                self.is_connected = False
                print(f"\n[DHT11 WIRING ERROR] Sensor read failed ({e}).")
        except Exception as e:
            print(f"\n[DHT11 Read Error]: {e}")

    def close(self):
        if self.dht_device: self.dht_device.exit()


def run_standalone(lcd=None):
    """Continuously read and print temperature/humidity until Ctrl+C. Mirrors readings to LCD."""
    sensor = DHTSensor()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    print(f"[DHT11] Reading GPIO{DHT_PIN} every {DHT_INTERVAL}s. Press Ctrl+C to stop.\n")
    try:
        while True:
            sensor.update()
            if sensor.temp is not None:
                print(f"Temp: {sensor.temp:.1f}C   Humidity: {sensor.humidity:.1f}%")
                lcd.log(f"T:{sensor.temp:.1f}C H:{sensor.humidity:.1f}%", "DHT11 OK", duration=sensor.interval)
            else:
                print("Waiting for valid reading...")
                lcd.log("DHT11", "WAITING...", duration=sensor.interval)
            time.sleep(sensor.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sensor.close()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()
