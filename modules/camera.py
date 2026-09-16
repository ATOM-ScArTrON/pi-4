"""
Picamera2 still photography and LoRa thumbnail generation module.
"""
import os
import sys
import time
import io
import queue
import threading
from modules.terminal import display_on_terminal

print = display_on_terminal
from picamera2 import Picamera2
from config import PHOTO_DIR, CAMERA_COOLDOWN

class CameraManager:
    def __init__(self, photo_dir=PHOTO_DIR, cooldown=CAMERA_COOLDOWN):
        self.photo_dir, self.cooldown = photo_dir, cooldown
        self.last_capture = 0
        self.picam2 = None
        self._init_camera()

    def _init_camera(self):
        try:
            self.picam2 = Picamera2()
            self.picam2.configure(self.picam2.create_still_configuration(main={"size": (640, 480)}))
            self.picam2.start()
            time.sleep(1.0)
        except Exception as e:
            print(f"[Camera Init Warning]: {e}")
            if self.picam2:
                try:
                    self.picam2.close()
                except Exception:
                    pass
            self.picam2 = None

    def capture_photo(self, source="MANUAL"):
        now = time.time()
        if not self.picam2 or now - self.last_capture < self.cooldown:
            return None

        self.last_capture = now
        filepath = os.path.join(self.photo_dir, f"capture_{time.strftime('%Y%m%d_%H%M%S')}.jpg")

        try:
            self.picam2.capture_file(filepath)
            return filepath
        except Exception as e:
            print(f"[Camera Error]: {e}")
            return None

    def capture_thumbnail_bytes(self, size=(160, 120)):
        if not self.picam2: return None
        try:
            buf = io.BytesIO()
            self.picam2.capture_file(buf, format="jpeg")
            return buf.getvalue()
        except Exception as e:
            print(f"[Camera Thumbnail Error]: {e}")
            return None

    def close(self):
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception:
                pass
            finally:
                self.picam2 = None


def run_standalone(lcd=None):
    """Standalone camera runner: Listens for Voice commands, Keyboard input, and Enter key with TTS audio."""
    from modules.stt import SpeechToText
    from modules.tts import TextToSpeech

    cam = CameraManager()
    stt = SpeechToText()
    tts = TextToSpeech()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    if not cam.picam2:
        print("[Camera] Hardware not available - check init warning above.")
        lcd.log("CAMERA FAILED", "CHECK RIBBON", duration=3.0)
        tts.speak("Camera initialization failed. Check ribbon cable.")
        if own_lcd:
            lcd.close()
        return

    if stt.model:
        stt.start()

    input_queue = queue.Queue()
    def keyboard_listener():
        while True:
            try:
                line = sys.stdin.readline()
                if not line: break
                input_queue.put(line.strip())
            except Exception:
                break

    threading.Thread(target=keyboard_listener, daemon=True).start()

    print(f"[Camera] Ready. Photos will save to {PHOTO_DIR}")
    print("Triggers (Voice or Terminal): 'click', 'capture', 'photo' (or press Enter).")
    print("Press Ctrl+C to exit.\n")
    lcd.log("CAMERA READY", "VOICE / TYPE", duration=2.0)
    tts.speak("Camera ready.")

    try:
        while True:
            source, trigger = None, None

            if stt.model:
                cmd = stt.get_command()
                if cmd:
                    source, trigger = "VOICE", cmd.lower()

            if not source and not input_queue.empty():
                source, trigger = "TYPED", input_queue.get_nowait().lower()

            if trigger is not None:
                is_photo_cmd = (
                    trigger in ("", "click", "capture", "photo", "picture", "snap")
                    or "click" in trigger
                    or "capture" in trigger
                    or "photo" in trigger
                )

                if is_photo_cmd:
                    print(f"\n[{source} TRIGGER]: Capture requested -> '{trigger if trigger else 'Enter'}'")
                    lcd.log("TAKING PHOTO", f"SRC: {source}", duration=1.5)

                    path = cam.capture_photo(source=source)
                    if path:
                        filename = os.path.basename(path)
                        print(f"[Camera Output]: Saved -> {path}")
                        lcd.log("PICTURE TAKEN", filename[-16:], duration=2.5)
                        tts.speak("Photo captured.")
                    else:
                        print("[Camera Output]: Capture failed (cooldown active or hardware error).")
                        lcd.log("CAPTURE FAILED", "COOLDOWN/ERROR", duration=2.5)
                        tts.speak("Capture failed. Cooldown active or camera error.")
                else:
                    print(f"\n[{source} IGNORED]: '{trigger}' (unrecognized command for camera standalone mode)")

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopping standalone camera...")
    finally:
        stt.stop()
        cam.close()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()
