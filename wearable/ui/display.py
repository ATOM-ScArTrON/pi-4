"""
16x2 Character LCD display module via PCF8574 I2C backpack.
"""
import time
import threading
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal
from RPLCD.i2c import CharLCD
from config import LCD_ADDR, I2C_BUS, LCD_CYCLE_INTERVAL

class Display:
    def __init__(self, address=LCD_ADDR, port=I2C_BUS):
        self.lcd = None
        self.lock = threading.Lock()  # I2C Thread Lock
        self.current_page = self.last_refresh = 0
        self.last_page_switch = time.time()
        self.banner_expiry = 0
        
        try:
            self.lcd = CharLCD('PCF8574', address, port=port, charmap='A00', cols=16, rows=2, auto_linebreaks=False)
            self.write_lines("BIOMETRIC SYSTEM", "INITIALIZING...")
        except Exception as e:
            print(f"[LCD Init Warning]: {e}")

    def write_lines(self, line1="", line2=""):
        if not self.lcd: return
        with self.lock:
            try:
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string(line1[:16].ljust(16))
                self.lcd.cursor_pos = (1, 0)
                self.lcd.write_string(line2[:16].ljust(16))
            except Exception:
                pass

    def scroll_text(self, line1, line2, target_duration=None, default_delay=0.15):
        if not self.lcd: return

        if len(line1) <= 16 and len(line2) <= 16:
            self.write_lines(line1, line2)
            return

        pad1 = line1 + " " * 16 if len(line1) > 16 else line1[:16].ljust(16)
        pad2 = line2 + " " * 16 if len(line2) > 16 else line2[:16].ljust(16)

        steps1 = len(pad1) - 16 + 1 if len(line1) > 16 else 1
        steps2 = len(pad2) - 16 + 1 if len(line2) > 16 else 1
        total_steps = max(steps1, steps2)

        # Dynamically compute step delay if duration is provided
        if target_duration and total_steps > 0:
        # Clamp delay between 0.05s (readable max speed) and 0.4s (slowest threshold)
            delay = max(0.05, min(0.4, target_duration / total_steps))
        else:
            delay = default_delay

        try:
            for i in range(total_steps):
                sub1 = pad1[i:i + 16] if len(line1) > 16 else pad1
                sub2 = pad2[i:i + 16] if len(line2) > 16 else pad2

                with self.lock:
                    self.lcd.cursor_pos = (0, 0)
                    self.lcd.write_string(sub1)
                    self.lcd.cursor_pos = (1, 0)
                    self.lcd.write_string(sub2)
                time.sleep(delay)
        except Exception as e:
            print(f"[LCD Scroll Warning]: {e}")

    def show_banner(self, line1, line2="", duration=2.0):
        self.banner_expiry = time.time() + duration
        if len(line1) > 16 or len(line2) > 16:
            self.scroll_text(line1, line2, target_duration=duration)
        else:
            self.write_lines(line1, line2)
    
    def log(self, line1, line2="", duration=2.0, also_print=True):
        if also_print:
            tag = line1 if not line2 else f"{line1} | {line2}"
            print(f"[STATUS] {tag}")
        self.show_banner(line1, line2, duration=duration)

    def update_cyclic(self, vitals=None, dht=None, motion=None, gps=None, sound=None):
        now = time.time()
        if now < self.banner_expiry or now - self.last_refresh < 0.4: return
        
        self.last_refresh = now
        if now - self.last_page_switch > LCD_CYCLE_INTERVAL:
            self.current_page = (self.current_page + 1) % 3
            self.last_page_switch = now

        if self.current_page == 0:
            l1 = f"BPM:{int(vitals.bpm):<2} SpO2:{int(vitals.spo2)}%" if vitals and getattr(vitals, 'bpm', None) else "Reading Vitals..."
            l1 = "Place Finger..." if vitals and not getattr(vitals, 'finger_detected', True) else l1
            t_str = f"T:{dht.temp:.1f}C" if getattr(dht, 'temp', None) is not None else "T:--C"
            h_str = f"H:{dht.humidity:.1f}%" if getattr(dht, 'humidity', None) is not None else "H:--%"
            s_str = getattr(sound, 'status', "S:Q")
            self.write_lines(l1, f"{t_str:<5} {h_str:<5} {s_str}")

        elif self.current_page == 1:
            fb, lr = getattr(motion, 'dir_fb', 'Level'), getattr(motion, 'dir_lr', 'Level')
            self.write_lines(f"FB: {fb:<12}", f"LR: {lr:<12}")

        elif self.current_page == 2:
            if getattr(gps, 'has_fix', False):
                self.write_lines(f"Lat:{gps.lat}", f"Lon:{gps.lon}")
            else:
                self.write_lines("GPS: No Fix", f"Sats View: {getattr(gps, 'sats', '0')}")

    def close(self):
        if self.lcd:
            with self.lock:
                try: self.lcd.clear()
                except Exception: pass


def run_standalone():
    """Cycle demo pages including scrolling text on the LCD. Ctrl+C to exit."""
    lcd = Display()
    if not lcd.lcd:
        print("[LCD] Not available - check init warning above.")
        return

    print("[LCD] Cycling demo pages with scrolling test. Press Ctrl+C to stop.\n")
    try:
        while True:
            # Test 1: Standard Static Lines
            lcd.write_lines("LCD TEST MODE", "STANDALONE RUN")
            time.sleep(2)

            # Test 2: Text Scrolling
            lcd.scroll_text("THIS IS A VERY LONG SCROLLING MESSAGE FOR ROW 1", "Static Line 2", delay=0.15)

            # Test 3: Log / Banner integrated scroll
            lcd.log("VERY LONG BANNER NOTIFICATION THAT AUTO SCROLLS", "Status: OK", duration=2.0)
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        lcd.close()


if __name__ == "__main__":
    run_standalone()
