import os
import time
import json
import subprocess
import board
import adafruit_dht
from gpiozero import DigitalInputDevice

from picamera2 import Picamera2
from vosk import Model, KaldiRecognizer
from RPLCD.i2c import CharLCD

# =========================================================
# SETTINGS
# =========================================================

MODEL_PATH = "/home/pi/vosk-model-small-en-us-0.15"
PHOTO_FOLDER = "/home/pi/captured_photos"
SAMPLE_RATE = 16000
MIC = "default"
LCD_ADDRESS = 0x27

# Sensor Pins
DHT_PIN = board.D4       # GPIO4 (Physical Pin 7)
SOUND_PIN = 17           # GPIO17 (Physical Pin 11)
DHT_INTERVAL = 3.0       # Read DHT11 every 3 seconds

# =========================================================
# INITIALIZE HARDWARE & SENSORS
# =========================================================

os.makedirs(PHOTO_FOLDER, exist_ok=True)

# LCD Initialization
print("Starting I2C LCD...")
lcd = CharLCD(
    i2c_expander='PCF8574',
    address=LCD_ADDRESS,
    port=1,
    charmap='A00',
    cols=16,
    rows=2,
    auto_linebreaks=False
)

def lcd_display(line1="", line2=""):
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(line1[:16])
    lcd.cursor_pos = (1, 0)
    lcd.write_string(line2[:16])

# Sound Sensor Initialization (GPIO17)
# Digital sound modules output LOW/HIGH on sound detection
sound_sensor = DigitalInputDevice(SOUND_PIN, pull_up=False)

# DHT11 Initialization
print("Initializing DHT11 Sensor...")
try:
    dht_device = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)
except Exception as e:
    print(f"DHT11 Init Warning: {e}")
    dht_device = None

# Track sensor values globally
latest_temp = None
latest_humidity = None
last_dht_read = 0

def update_dht11():
    """Non-blocking DHT11 polling"""
    global latest_temp, latest_humidity, last_dht_read
    
    current_time = time.time()
    if current_time - last_dht_read < DHT_INTERVAL or dht_device is None:
        return

    last_dht_read = current_time
    try:
        temp = dht_device.temperature
        hum = dht_device.humidity
        if temp is not None and hum is not None:
            latest_temp = temp
            latest_humidity = hum
    except RuntimeError:
        pass
    except Exception as e:
        print(f"DHT Read Error: {e}")

def get_sound_status():
    """Returns 'S:L' for Loud or 'S:Q' for Quiet"""
    # Most digital sound modules trigger HIGH (1) when sound is detected.
    # If your sensor is active-low, swap 'S:L' and 'S:Q'.
    return "S:L" if sound_sensor.is_active else "S:Q"

def get_dht_string():
    """Format DHT11 readout for Row 2"""
    if latest_temp is not None and latest_humidity is not None:
        return f"T:{latest_temp:.0f}C H:{latest_humidity:.0f}%"
    return "T:--C H:--%"

def show_idle_status():
    """Row 1: Ready + Sound status | Row 2: Temp & Humidity"""
    sound_status = get_sound_status()
    # Row 1 formatted to fit 16 chars: "READY       S:Q"
    line1 = f"READY        {sound_status}"
    line2 = get_dht_string()
    lcd_display(line1, line2)

# =========================================================
# CHECK & LOAD VOSK MODEL
# =========================================================

if not os.path.exists(MODEL_PATH):
    print("ERROR: Vosk model not found:", MODEL_PATH)
    lcd_display("VOSK ERROR", "MODEL NOT FOUND")
    time.sleep(3)
    lcd.clear()
    exit(1)

print("Loading Vosk model...")
model = Model(MODEL_PATH)
recognizer = KaldiRecognizer(model, SAMPLE_RATE)
print("Vosk model loaded successfully")

# =========================================================
# OPEN CAMERA
# =========================================================

print("Opening Raspberry Pi Camera...")
picam2 = Picamera2()
camera_config = picam2.create_still_configuration(main={"size": (640, 480)})
picam2.configure(camera_config)
picam2.start()
time.sleep(2)
print("Raspberry Pi Camera opened successfully")

# =========================================================
# CAPTURE CONTROL
# =========================================================

last_capture = 0

def capture_photo():
    global last_capture, recognizer

    current_time = time.time()
    if current_time - last_capture < 3:
        print("Capture ignored - cooldown active...")
        return

    last_capture = current_time

    print("\n================================")
    print("CAPTURE COMMAND DETECTED")
    print("================================")

    lcd_display("COMMAND RECEIVED", "YES / CAPTURE")
    time.sleep(1)

    lcd_display("READY FOR", "PICTURE")
    time.sleep(1)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"capture_{timestamp}.jpg"
    filepath = os.path.join(PHOTO_FOLDER, filename)

    print("Taking picture...")
    lcd_display("TAKING PICTURE", "PLEASE WAIT...")

    try:
        picam2.capture_file(filepath)
    except Exception as e:
        print("ERROR: Cannot capture image:", e)
        lcd_display("CAMERA ERROR", "TRY AGAIN")
        time.sleep(2)
        show_idle_status()
        return

    if os.path.exists(filepath):
        filesize = os.path.getsize(filepath)
        print(f"PHOTO CAPTURED: {filename} ({filesize} bytes)")
        lcd_display("PICTURE TAKEN", "SUCCESS")
        time.sleep(2)
    else:
        print("ERROR: Photo file was not created")
        lcd_display("PHOTO ERROR", "NOT SAVED")
        time.sleep(2)

    show_idle_status()
    print('\nREADY - SAY "CAPTURE" OR "YES"\n')

    # Reset Vosk recognizer state
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)

# =========================================================
# START MICROPHONE STREAM
# =========================================================

print("\nStarting Bluetooth microphone...")
audio_command = [
    "arecord",
    "-D", MIC,
    "-f", "S16_LE",
    "-r", "16000",
    "-c", "1",
    "-t", "raw",
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
    print("ERROR: Cannot start microphone:", e)
    lcd_display("MIC ERROR", "CHECK MIC")
    picam2.stop()
    exit(1)

# Initial screen setup
update_dht11()
show_idle_status()

# =========================================================
# MAIN LOOP
# =========================================================

last_screen_refresh = 0
prev_sound_state = None

try:
    while True:
        # Periodic sensor polling
        update_dht11()

        # Check for sound status change to update LCD instantly
        current_sound_state = sound_sensor.is_active
        time_elapsed = time.time() - last_screen_refresh

        if current_sound_state != prev_sound_state or time_elapsed > 1.0:
            show_idle_status()
            prev_sound_state = current_sound_state
            last_screen_refresh = time.time()

        # Read ~0.5s audio chunk
        data = audio.stdout.read(16000)
        if not data:
            print("\nMicrophone stopped")
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").lower().strip()

            if text:
                print("\nHEARD:", text)

            if "capture" in text or "yes" in text:
                capture_photo()
                last_screen_refresh = time.time()
        else:
            partial_result = json.loads(recognizer.PartialResult())
            partial_text = partial_result.get("partial", "").lower().strip()

            if partial_text:
                print("Listening:", partial_text, end="\r")

            if "capture" in partial_text or "yes" in partial_text:
                capture_photo()
                last_screen_refresh = time.time()

except KeyboardInterrupt:
    print("\nStopping...")

finally:
    print("Cleaning up...")

    try:
        audio.terminate()
        audio.wait(timeout=2)
    except Exception:
        try:
            audio.kill()
        except Exception:
            pass

    try:
        picam2.stop()
    except Exception:
        pass

    if dht_device:
        try:
            dht_device.exit()
        except Exception:
            pass

    try:
        sound_sensor.close()
    except Exception:
        pass

    try:
        lcd.clear()
    except Exception:
        pass

    print("All components cleanly stopped.")