"""
16x2 Character LCD display module via PCF8574 I2C backpack.
"""
import time
import threading
import queue

from wearable.ui.terminal import display_on_terminal

print = display_on_terminal

from RPLCD.i2c import CharLCD
from config import LCD_ADDR, I2C_BUS, LCD_CYCLE_INTERVAL


class Display:
    def __init__(self, address=LCD_ADDR, port=I2C_BUS):
        self.lcd = None

        # Protect physical I2C access
        self.lock = threading.Lock()

        # Banner queue
        self.banner_queue = queue.Queue()
        self.banner_active = False

        # Worker shutdown
        self.stop_event = threading.Event()

        self.current_page = 0
        self.last_refresh = 0
        self.last_page_switch = time.time()

        try:
            self.lcd = CharLCD(
                'PCF8574',
                address,
                port=port,
                charmap='A00',
                cols=16,
                rows=2,
                auto_linebreaks=False
            )
        except Exception as e:
            print(f"[LCD Init Warning]: {e}")

        # Start ONE worker responsible for banners
        self.banner_thread = threading.Thread(
            target=self._banner_worker,
            daemon=True
        )
        self.banner_thread.start()

    # ---------------------------------------------------------
    # Low-level LCD write
    # ---------------------------------------------------------

    def write_lines(self, line1="", line2=""):
        """Write two lines to the LCD."""
        if not self.lcd:
            return

        with self.lock:
            try:
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string(line1[:16].ljust(16))

                self.lcd.cursor_pos = (1, 0)
                self.lcd.write_string(line2[:16].ljust(16))

            except Exception as e:
                print(f"[LCD Write Warning]: {e}")

    # ---------------------------------------------------------
    # Scrolling
    # ---------------------------------------------------------

    def scroll_text(self, line1, line2, default_delay=0.15):
        """
        Scroll long text from left to right.

        This function is called only by the banner worker,
        so another banner cannot interrupt it.
        """
        if not self.lcd:
            return

        self.banner_active = True

        len1 = len(line1)
        len2 = len(line2)

        # Nothing to scroll
        if len1 <= 16 and len2 <= 16:
            self.write_lines(line1, line2)
            return

        max_extra = max(
            max(0, len1 - 16),
            max(0, len2 - 16)
        )

        total_steps = max_extra + 1

        try:
            for i in range(total_steps):

                sub1 = (
                    line1[i:i + 16].ljust(16)
                    if len1 > 16
                    else line1[:16].ljust(16)
                )

                sub2 = (
                    line2[i:i + 16].ljust(16)
                    if len2 > 16
                    else line2[:16].ljust(16)
                )

                self.write_lines(sub1, sub2)

                time.sleep(default_delay)

        except Exception as e:
            print(f"[LCD Scroll Warning]: {e}")

        finally:
            self.banner_active = False

    # ---------------------------------------------------------
    # Banner worker
    # ---------------------------------------------------------

    def _banner_worker(self):
        """
        Single worker that processes banners sequentially.

        Only this thread processes the banner queue, which
        guarantees that two banners can never scroll
        simultaneously.
        """

        while not self.stop_event.is_set():

            try:
                line1, line2, duration = self.banner_queue.get(
                    timeout=0.1
                )
            except queue.Empty:
                continue

            self.banner_active = True

            try:
                # Scroll the entire message before moving on
                if len(line1) > 16 or len(line2) > 16:
                    self.scroll_text(
                        line1,
                        line2,
                        default_delay=0.15
                    )
                else:
                    self.write_lines(line1, line2)

                # Keep the completed message visible
                if duration > 0:
                    time.sleep(duration)

            except Exception as e:
                print(f"[LCD Banner Warning]: {e}")

            finally:
                self.banner_active = False
                self.banner_queue.task_done()

    # ---------------------------------------------------------
    # Public banner API
    # ---------------------------------------------------------

    def show_banner(self, line1, line2="", duration=2.0):
        """
        Queue a banner for display.

        Returns immediately. Banners are displayed FIFO.
        """
        self.banner_queue.put(
            (line1, line2, duration)
        )

    # ---------------------------------------------------------
    # Logging
    # ---------------------------------------------------------

    def log(self, line1, line2="", duration=2.0, also_print=True):
        if also_print:
            tag = line1 if not line2 else f"{line1} | {line2}"
            print(f"[STATUS] {tag}")

        self.show_banner(
            line1,
            line2,
            duration=duration
        )

    # ---------------------------------------------------------
    # Normal cyclic display
    # ---------------------------------------------------------

    def update_cyclic(
        self,
        vitals=None,
        dht=None,
        motion=None,
        gps=None,
        sound=None
    ):
        now = time.time()

        # Don't allow normal display updates to overwrite
        # an active or waiting banner.
        if self.banner_active or not self.banner_queue.empty():
            return

        if now - self.last_refresh < 0.4:
            return

        self.last_refresh = now

        if now - self.last_page_switch > LCD_CYCLE_INTERVAL:
            self.current_page = (
                self.current_page + 1
            ) % 3

            self.last_page_switch = now

        # -----------------------------------------------------
        # Page 0: Vitals
        # -----------------------------------------------------

        if self.current_page == 0:

            l1 = (
                f"BPM:{int(vitals.bpm):<2} "
                f"SpO2:{int(vitals.spo2)}%"
                if vitals
                and getattr(vitals, 'bpm', None)
                else "Reading Vitals..."
            )

            if (
                vitals
                and not getattr(
                    vitals,
                    'finger_detected',
                    True
                )
            ):
                l1 = "Place Finger..."

            t_str = (
                f"T:{dht.temp:.1f}C"
                if getattr(dht, 'temp', None) is not None
                else "T:--C"
            )

            h_str = (
                f"H:{dht.humidity:.1f}%"
                if getattr(dht, 'humidity', None) is not None
                else "H:--%"
            )

            s_str = getattr(
                sound,
                'status',
                "S:Q"
            )

            self.write_lines(
                l1,
                f"{t_str:<5} {h_str:<5} {s_str}"
            )

        # -----------------------------------------------------
        # Page 1: Motion
        # -----------------------------------------------------

        elif self.current_page == 1:

            fb = getattr(
                motion,
                'dir_fb',
                'Level'
            )

            lr = getattr(
                motion,
                'dir_lr',
                'Level'
            )

            self.write_lines(
                f"FB: {fb:<12}",
                f"LR: {lr:<12}"
            )

        # -----------------------------------------------------
        # Page 2: GPS
        # -----------------------------------------------------

        elif self.current_page == 2:

            if getattr(gps, 'has_fix', False):

                self.write_lines(
                    f"Lat:{gps.lat}",
                    f"Lon:{gps.lon}"
                )

            else:

                self.write_lines(
                    "GPS: No Fix",
                    f"Sats View: {getattr(gps, 'sats', '0')}"
                )

    # ---------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------

    def close(self):
        self.stop_event.set()

        if (
            hasattr(self, 'banner_thread')
            and self.banner_thread.is_alive()
        ):
            self.banner_thread.join(timeout=1.0)

        if self.lcd:
            with self.lock:
                try:
                    self.lcd.clear()
                except Exception:
                    pass


# =============================================================
# Standalone test
# =============================================================

def run_standalone():
    """Cycle demo pages including scrolling text on the LCD."""

    lcd = Display()

    if not lcd.lcd:
        print(
            "[LCD] Not available - "
            "check init warning above."
        )
        return

    print(
        "[LCD] Cycling demo pages with "
        "scrolling test. Press Ctrl+C to stop.\n"
    )

    try:
        while True:

            # Test 1: Standard Static Lines
            lcd.log(
                "LCD TEST MODE",
                "STANDALONE RUN"
            )

            # Test 2: Direct scrolling
            lcd.log(
                "THIS IS A VERY LONG SCROLLING MESSAGE FOR ROW 1",
                "Static Line 2",
                duration=2.0
            )

            # Test 3: Multiple queued banners
            lcd.log(
                "VERY LONG BANNER NOTIFICATION THAT AUTO SCROLLS",
                "Status: OK",
                duration=2.0
            )

            lcd.log(
                "SECOND MESSAGE SHOULD WAIT",
                "Status: WAITING",
                duration=2.0
            )

            lcd.log(
                "THIRD MESSAGE SHOULD WAIT TOO",
                "Status: DONE",
                duration=2.0
            )

            # Wait for all queued banners to finish
            lcd.banner_queue.join()

    except KeyboardInterrupt:
        print("\nStopped.")

    finally:
        lcd.close()


if __name__ == "__main__":
    run_standalone()
