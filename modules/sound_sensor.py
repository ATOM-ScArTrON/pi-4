"""
Digital sound sensor driver. Detects ambient noise thresholds (Quiet vs Loud).
"""

import time
from gpiozero import DigitalInputDevice
from config import SOUND_PIN

LCD_REFRESH_INTERVAL = 1.0  # throttle LCD writes independent of terminal print rate

class SoundSensor:
    def __init__(self, pin=SOUND_PIN):
        self.pin = pin
        try:
            self.device = DigitalInputDevice(pin, pull_up=False)
        except Exception as e:
            print(f"[SoundSensor Init Warning]: {e}")
            self.device = None

    @property
    def is_loud(self):
        return self.device.is_active if self.device else False

    @property
    def status(self):
        """Returns 'S:L' for Loud or 'S:Q' for Quiet."""
        return "S:L" if self.is_loud else "S:Q"

    def close(self):
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass


def run_standalone(lcd=None):
    """Continuously print live loud/quiet status until Ctrl+C. Mirrors state to LCD (throttled)."""
    sensor = SoundSensor()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    print(f"[Sound Sensor] Monitoring GPIO{SOUND_PIN}. Clap or speak if sensor doesn't show loud readings. Press Ctrl+C to stop.\n")
    last_lcd_update = 0
    try:
        while True:
            state = sensor.status
            print(f"Sound state: {state}", end="\r")

            now = time.time()
            if now - last_lcd_update >= LCD_REFRESH_INTERVAL:
                last_lcd_update = now
                label = "LOUD" if state == "S:L" else "QUIET"
                lcd.log("SOUND SENSOR", label, duration=LCD_REFRESH_INTERVAL)

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sensor.close()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()