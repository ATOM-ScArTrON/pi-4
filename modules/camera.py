"""
Picamera2 still photography and LoRa thumbnail generation module.
"""

import os
import time
from picamera2 import Picamera2
from config import PHOTO_DIR, CAMERA_COOLDOWN

class CameraManager:
    def __init__(self, photo_dir=PHOTO_DIR, cooldown=CAMERA_COOLDOWN):
        self.photo_dir = photo_dir
        self.cooldown = cooldown
        self.last_capture = 0
        self.picam2 = None
        self._init_camera()

    def _init_camera(self):
        try:
            self.picam2 = Picamera2()
            config = self.picam2.create_still_configuration(main={"size": (640, 480)})
            self.picam2.configure(config)
            self.picam2.start()
            time.sleep(1.0)
            print("[Camera] Picamera2 initialized successfully.")
        except Exception as e:
            print(f"[Camera Init Warning]: {e}")
            self.picam2 = None

    def capture_photo(self, source="MANUAL"):
        """Captures full still image to disk. Returns filepath or None."""
        if not self.picam2:
            return None

        now = time.time()
        if now - self.last_capture < self.cooldown:
            return None
        self.last_capture = now

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{timestamp}.jpg"
        filepath = os.path.join(self.photo_dir, filename)

        try:
            self.picam2.capture_file(filepath)
            print(f"[Camera] Photo saved: {filepath} ({os.path.getsize(filepath)} bytes)")
            return filepath
        except Exception as e:
            print(f"[Camera Error]: {e}")
            return None

    def capture_thumbnail_bytes(self, size=(160, 120)):
        """Captures a lightweight micro-thumbnail suitable for chunked LoRa transmission."""
        if not self.picam2:
            return None

        try:
            import io
            buf = io.BytesIO()
            # Capture still directly into byte stream as JPEG
            self.picam2.capture_file(buf, format="jpeg")
            jpeg_data = buf.getvalue()
            return jpeg_data
        except Exception as e:
            print(f"[Camera Thumbnail Error]: {e}")
            return None

    def close(self):
        if self.picam2:
            try:
                self.picam2.stop()
            except Exception:
                pass

if __name__ == "__main__":
    print("Testing CameraManager...")
    cam = CameraManager()
    time.sleep(1)
    path = cam.capture_photo(source="TEST")
    print(f"Captured path: {path}")
    cam.close()
    print("CameraManager test complete.")

