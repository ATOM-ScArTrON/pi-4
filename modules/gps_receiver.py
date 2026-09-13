"""
NEO-6M GPS receiver module over UART serial.
"""
import time
import serial
import pynmea2
from config import GPS_PORT, GPS_BAUD, GPS_INTERVAL

LCD_REFRESH_INTERVAL = 1.0  # throttle LCD writes independent of terminal print rate

class GPSReceiver:
    def __init__(self, port=GPS_PORT, baudrate=GPS_BAUD, interval=GPS_INTERVAL):
        self.interval = interval
        self.lat = self.lon = "0.0000"
        self.sats = "0"
        self.has_fix = False
        self.last_read = 0
        
        try:
            self.ser = serial.Serial(port, baudrate=baudrate, timeout=0.05)
        except Exception as e:
            print(f"[GPS Init Warning]: {e}")
            self.ser = None

    def update(self):
        now = time.time()
        if not self.ser or not self.ser.is_open or now - self.last_read < self.interval: return
        self.last_read = now

        try:
            while self.ser.in_waiting > 0:
                raw_line = self.ser.readline().decode('ascii', errors='replace').strip()
                if raw_line.startswith(('$GPGGA', '$GNGGA')):
                    msg = pynmea2.parse(raw_line)
                    self.sats = str(getattr(msg, 'num_sats', '0'))
                    
                    if getattr(msg, 'gps_qual', 0) > 0 and msg.latitude and msg.longitude:
                        self.lat, self.lon = f"{msg.latitude:.4f}{msg.lat_dir}", f"{msg.longitude:.4f}{msg.lon_dir}"
                        self.has_fix = True
                    else:
                        self.has_fix = False
                    break
        except Exception:
            pass

    def get_telemetry(self):
        return {"LAT": self.lat, "LON": self.lon, "SATS": self.sats, "FIX": self.has_fix}

    def close(self):
        if self.ser and self.ser.is_open: self.ser.close()


def run_standalone(lcd=None):
    """Continuously print live GPS fix/coordinates until Ctrl+C. Mirrors state to LCD (throttled)."""
    receiver = GPSReceiver()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    print(f"[GPS] Reading {GPS_PORT}. Waiting for satellite fix. Press Ctrl+C to stop.\n")
    last_lcd_update = 0
    try:
        while True:
            receiver.update()
            if receiver.has_fix:
                print(f"FIX  Lat:{receiver.lat}  Lon:{receiver.lon}  Sats:{receiver.sats}")
            else:
                print(f"No fix. Satellites visible: {receiver.sats}", end="\r")

            now = time.time()
            if now - last_lcd_update >= LCD_REFRESH_INTERVAL:
                last_lcd_update = now
                if receiver.has_fix:
                    lcd.log(f"Lat:{receiver.lat}", f"Lon:{receiver.lon}", duration=LCD_REFRESH_INTERVAL)
                else:
                    lcd.log("GPS: No Fix", f"Sats View: {receiver.sats}", duration=LCD_REFRESH_INTERVAL)

            time.sleep(receiver.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        receiver.close()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()