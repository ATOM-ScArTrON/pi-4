import os
import time
import json
import subprocess

from picamera2 import Picamera2
from vosk import Model, KaldiRecognizer

# =========================================================
# SETTINGS
# =========================================================

MODEL_PATH = "/home/pi/vosk-model-small-en-us-0.15"

PHOTO_FOLDER = "/home/pi/captured_photos"

SAMPLE_RATE = 16000

# Bluetooth microphone
MIC = "default"


# =========================================================
# CREATE PHOTO FOLDER
# =========================================================

os.makedirs(PHOTO_FOLDER, exist_ok=True)


# =========================================================
# CHECK VOSK MODEL
# =========================================================

if not os.path.exists(MODEL_PATH):
    print("ERROR: Vosk model not found:")
    print(MODEL_PATH)
    exit(1)


# =========================================================
# LOAD VOSK
# =========================================================

print("Loading Vosk model...")

model = Model(MODEL_PATH)

recognizer = KaldiRecognizer(
    model,
    SAMPLE_RATE
)

print("Vosk model loaded successfully")


# =========================================================
# OPEN RASPBERRY PI CAMERA
# =========================================================

print("Opening Raspberry Pi Camera...")

picam2 = Picamera2()

camera_config = picam2.create_still_configuration(
    main={
        "size": (640, 480)
    }
)

picam2.configure(camera_config)

picam2.start()

# Allow camera to stabilize
time.sleep(2)

print("Raspberry Pi Camera opened successfully")


# =========================================================
# CAPTURE CONTROL
# =========================================================

last_capture = 0


def capture_photo():

    global last_capture

    current_time = time.time()

    # Prevent duplicate captures
    if current_time - last_capture < 3:
        print("Capture ignored - waiting...")
        return

    last_capture = current_time

    print()
    print("================================")
    print("CAPTURE COMMAND DETECTED")
    print("================================")

    # Small delay before taking picture
    time.sleep(0.5)

    # =====================================================
    # CREATE FILE NAME
    # =====================================================

    timestamp = time.strftime(
        "%Y%m%d_%H%M%S"
    )

    filename = (
        "capture_" +
        timestamp +
        ".jpg"
    )

    filepath = os.path.join(
        PHOTO_FOLDER,
        filename
    )

    # =====================================================
    # TAKE PHOTO
    # =====================================================

    print("Taking picture...")

    try:

        picam2.capture_file(filepath)

    except Exception as e:

        print("ERROR: Cannot capture image")
        print(e)
        return

    # =====================================================
    # CHECK FILE
    # =====================================================

    if os.path.exists(filepath):

        filesize = os.path.getsize(filepath)

        print()
        print("PHOTO CAPTURED!")
        print("File:", filename)
        print("Saved:", filepath)
        print("Size:", filesize, "bytes")

    else:

        print("ERROR: Photo file was not created")
        return

    print()
    print('READY - SAY "CAPTURE" OR "YES"')
    print()


# =========================================================
# START BLUETOOTH MICROPHONE
# =========================================================

print()
print("Starting Bluetooth microphone...")

audio_command = [
    "arecord",
    "-D",
    MIC,
    "-f",
    "S16_LE",
    "-r",
    "16000",
    "-c",
    "1",
    "-t",
    "raw",
    "-q"
]

try:

    audio = subprocess.Popen(
        audio_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0
    )

except Exception as e:

    print("ERROR: Cannot start microphone")
    print(e)

    picam2.stop()
    exit(1)


# =========================================================
# READY MESSAGE
# =========================================================

print()
print("======================================")
print("        VOICE CAMERA READY")
print("======================================")
print()

print('Say "CAPTURE" to take a picture')
print('Say "YES" to take a picture')

print()

print("Camera : Raspberry Pi CSI Camera")
print("Mic    : Noise Buds VS201")
print("Audio  : 16000 Hz / Mono")
print("Image  : 640 x 480")

print()
print("Photos:")
print(PHOTO_FOLDER)

print()
print("======================================")
print()


# =========================================================
# MAIN VOICE LOOP
# =========================================================

try:

    while True:

        # Read approximately 1 second of audio
        data = audio.stdout.read(32000)

        if not data:

            print()
            print("Microphone stopped")
            break

        # =================================================
        # FINAL VOSK RESULT
        # =================================================

        if recognizer.AcceptWaveform(data):

            result = json.loads(
                recognizer.Result()
            )

            text = result.get(
                "text",
                ""
            ).lower().strip()

            if text:

                print()
                print("HEARD:", text)

            # =================================================
            # CAPTURE COMMAND
            # =================================================

            if (
                "capture" in text
                or "yes" in text
            ):

                capture_photo()

                # Reset recognizer after capture
                recognizer = KaldiRecognizer(
                    model,
                    SAMPLE_RATE
                )

        else:

            # =================================================
            # PARTIAL RESULT
            # =================================================

            partial_result = json.loads(
                recognizer.PartialResult()
            )

            partial_text = partial_result.get(
                "partial",
                ""
            ).lower().strip()

            if partial_text:

                print(
                    "Listening:",
                    partial_text,
                    end="\r"
                )

            # =================================================
            # CAPTURE COMMAND IN PARTIAL RESULT
            # =================================================

            if (
                "capture" in partial_text
                or "yes" in partial_text
            ):

                capture_photo()

                # Reset recognizer
                recognizer = KaldiRecognizer(
                    model,
                    SAMPLE_RATE
                )


# =========================================================
# STOP WITH CTRL+C
# =========================================================

except KeyboardInterrupt:

    print()
    print()
    print("Stopping...")


# =========================================================
# CLEANUP
# =========================================================

finally:

    print("Cleaning up...")

    # Stop microphone
    try:

        audio.terminate()
        audio.wait(timeout=2)

    except Exception:

        try:
            audio.kill()
        except Exception:
            pass

    # Stop camera
    try:

        picam2.stop()

    except Exception:
        pass

    print("Camera stopped.")
    print("Microphone stopped.")
    print("Program stopped.")
