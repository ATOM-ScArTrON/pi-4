"""
Digital sound sensor driver. Detects ambient noise thresholds (Quiet vs Loud).
"""

import time
from gpiozero import DigitalInputDevice
from config import SOUND_PIN

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

if __name__ == "__main__":
    print(f"Testing SoundSensor module on GPIO{SOUND_PIN}...")
    snd = SoundSensor()
    print("Clap or speak into the sensor:")
    for _ in range(20):
        print(f"Sound state: {snd.status}", end="\r")
        time.sleep(0.1)
    print("\nSoundSensor test complete.")
    snd.close()

