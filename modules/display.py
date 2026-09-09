"""
16x2 Character LCD display module via PCF8574 I2C backpack.
Handles 3-page cyclic dashboards and non-blocking transient banners.
"""

import time
from RPLCD.i2c import CharLCD
from config import LCD_ADDR, I2C_BUS, LCD_CYCLE_INTERVAL

class Display:
    def __init__(self, address=LCD_ADDR, port=I2C_BUS):
        self.address = address
        self.port = port
        self.lcd = None
        self.current_page = 0
        self.last_page_switch = time.time()
        self.last_refresh = 0
        self.banner_expiry = 0
        self._init_lcd()

    def _init_lcd(self):
        try:
            self.lcd = CharLCD(
                i2c_expander='PCF8574',
                address=self.address,
                port=self.port,
                charmap='A00',
                cols=16,
                rows=2,
                auto_linebreaks=False
            )
            self.write_lines("BIOMETRIC SYSTEM", "INITIALIZING...")
        except Exception as e:
            print(f"[LCD Init Warning]: {e}")
            self.lcd = None

    def write_lines(self, line1="", line2=""):
        """Writes two 16-character lines safely without clearing to avoid flicker."""
        if not self.lcd:
            return
        try:
            self.lcd.cursor_pos = (0, 0)
            self.lcd.write_string(line1[:16].ljust(16))
            self.lcd.cursor_pos = (1, 0)
            self.lcd.write_string(line2[:16].ljust(16))
        except Exception:
            pass

    def show_banner(self, line1, line2="", duration=2.0):
        """Displays a high-priority temporary banner that holds for `duration` seconds."""
        self.banner_expiry = time.time() + duration
        self.write_lines(line1, line2)

    def is_banner_active(self):
        return time.time() < self.banner_expiry

    def update_cyclic(self, vitals=None, dht=None, motion=None, gps=None):
        """Advances and refreshes the 3-page cyclical dashboard unless a banner is active."""
        now = time.time()
        if self.is_banner_active():
            return

        # Page cycling
        if now - self.last_page_switch > LCD_CYCLE_INTERVAL:
            self.current_page = (self.current_page + 1) % 3
            self.last_page_switch = now

        # Refresh rate throttle (every 0.4s)
        if now - self.last_refresh < 0.4:
            return
        self.last_refresh = now

        if self.current_page == 0:
            # Page 0: Vitals & Environment
            if vitals and not vitals.finger_detected:
                l1 = "Place Finger..."
            elif vitals and vitals.bpm and vitals.spo2:
                l1 = f"BPM:{int(vitals.bpm):<2}  SpO2:{int(vitals.spo2)}%"
            else:
                l1 = "Reading Vitals..."

            t_str = f"T:{dht.temp:.0f}C" if (dht and dht.temp is not None) else "T:--C"
            h_str = f"H:{dht.humidity:.0f}%" if (dht and dht.humidity is not None) else "H:--%"
            s_str = dht.sound_status if dht else "S:Q"
            l2 = f"{t_str:<5} {h_str:<5}  {s_str}"
            self.write_lines(l1, l2)

        elif self.current_page == 1:
            # Page 1: Motion / Tilt
            fb = motion.dir_fb if motion else "Level"
            lr = motion.dir_lr if motion else "Level"
            self.write_lines(f"FB: {fb:<12}", f"LR: {lr:<12}")

        elif self.current_page == 2:
            # Page 2: GPS Position
            if gps and gps.has_fix:
                self.write_lines(f"Lat:{gps.lat}", f"Lon:{gps.lon}")
            else:
                sats = gps.sats if gps else "0"
                self.write_lines("GPS: No Fix", f"Sats View: {sats}")

    def clear(self):
        if self.lcd:
            try:
                self.lcd.clear()
            except Exception:
                pass

    def close(self):
        self.clear()

if __name__ == "__main__":
    print("Testing Display module...")
    disp = Display()
    time.sleep(1)
    disp.show_banner("DISPLAY TEST", "PAGE 1 COMING", duration=2.0)
    time.sleep(2.5)
    disp.write_lines("TEST COMPLETE", "SUCCESS")
    time.sleep(1)
    disp.close()
    print("Display test finished.")

