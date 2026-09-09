import os
import sys
import time
import json
import re
import subprocess

from picamera2 import Picamera2
from vosk import Model, KaldiRecognizer

# =========================================================
# SYSTEM & ENVIRONMENT CONFIGURATION
# =========================================================

USER_HOME = os.path.expanduser("~")
MODEL_PATH = os.environ.get("VOSK_MODEL_PATH", os.path.join(USER_HOME, "vosk-model-small-en-us-0.15"))
PHOTO_FOLDER = os.environ.get("PHOTO_DIR", os.path.join(USER_HOME, "captured_photos"))

SAMPLE_RATE = 16000
CAPTURE_COOLDOWN = 3.0

# =========================================================
# HARDWARE AGNOSTIC HELPERS
# =========================================================

def detect_audio_device():
    """Dynamically locates an available ALSA capture device or defaults."""
    env_mic = os.environ.get("AUDIO_DEVICE")
    if env_mic:
        return env_mic

    try:
        output = subprocess.check_output(["arecord", "-l"], text=True, stderr=subprocess.DEVNULL)
        cards = re.findall(r"card (\d+):", output)
        if cards:
            return f"hw:{cards[0]},0"
    except Exception:
        pass
    
    return "default"

MIC = detect_audio_device()

# =========================================================
# INITIALIZATION & VERIFICATION
# =========================================================

os.makedirs(PHOTO_FOLDER, exist_ok=True)

if not os.path.exists(MODEL_PATH):
    sys.exit(f"ERROR: Vosk model directory not found at: {MODEL_PATH}")

print("Loading Vosk model...")
model = Model(MODEL_PATH)
recognizer = KaldiRecognizer(model, SAMPLE_RATE)

print("Opening camera interface...")
picam2 = Picamera2()
camera_config = picam2.create_still_configuration(main={"size": (640, 480)})
picam2.configure(camera_config)
picam2.start()

time.sleep(2)  # Sensor stabilization phase

# =========================================================
# CAPTURE ROUTINE
# =========================================================

last_capture = 0.0

def capture_photo():
    global last_capture
    current_time = time.time()

    if current_time - last_capture < CAPTURE_COOLDOWN:
        print("Capture ignored - cooldown active...")
        return

    last_capture = current_time

    print("\n" + "=" * 32 + "\nCAPTURE COMMAND DETECTED\n" + "=" * 32)
    time.sleep(0.5)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(PHOTO_FOLDER, f"capture_{timestamp}.jpg")

    print("Taking picture...")
    try:
        picam2.capture_file(filepath)
    except Exception as e:
        print(f"ERROR: Image capture failed: {e}")
        return

    if os.path.exists(filepath):
        print(f"\nPHOTO CAPTURED!\nPath: {filepath}\nSize: {os.path.getsize(filepath)} bytes")
    else:
        print("ERROR: Photo file was not created.")

    print('\nREADY - SAY "CAPTURE" OR "YES"\n')

# =========================================================
# MICROPHONE STREAM SETUP
# =========================================================

print("Starting PipeWire audio capture stream...")

audio_command = [
    "pw-record",
    "--target", "@DEFAULT_SOURCE@",
    "--rate", str(SAMPLE_RATE),
    "--channels", "1",
    "--format", "s16",
    "-"
]

try:
    audio = subprocess.Popen(
        audio_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0
    )
except Exception as e:
    picam2.stop()
    sys.exit(f"ERROR: Failed to start PipeWire audio stream: {e}")

# =========================================================
# RUNTIME STATUS DISPLAY
# =========================================================

print("\n" + "=" * 38)
print("        VOICE CAMERA READY")
print("=" * 38 + "\n")
print('Trigger keywords: "CAPTURE" / "YES"\n')
print(f"Audio Device : {MIC}")
print(f"Audio Format : {SAMPLE_RATE} Hz / Mono")
print(f"Storage Path : {PHOTO_FOLDER}\n")
print("=" * 38 + "\n")

# =========================================================
# MAIN RECOGNITION LOOP
# =========================================================

try:
    while True:
        data = audio.stdout.read(4000)
        if not data:
            print("\nAudio stream stopped.")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").lower().strip()

            if text:
                print(f"\nHEARD: {text}")

            if "capture" in text or "yes" in text:
                capture_photo()
                recognizer.Reset()
        else:
            partial_result = json.loads(recognizer.PartialResult())
            partial_text = partial_result.get("partial", "").lower().strip()

            if partial_text:
                print(f"Listening: {partial_text}", end="\r")

            if "capture" in partial_text or "yes" in partial_text:
                capture_photo()
                recognizer.Reset()

except KeyboardInterrupt:
    print("\n\nStopping application...")

finally:
    print("Cleaning up resources...")
    if 'audio' in locals():
        audio.terminate()
        try:
            audio.wait(timeout=1)
        except subprocess.TimeoutExpired:
            audio.kill()

    try:
        picam2.stop()
    except Exception:
        pass

    print("Hardware shutdown complete.")