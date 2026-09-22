"""
Central hardware configuration and environment settings for the Raspberry Pi 4 edge node.
Provides a single source of truth for all modules.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =========================================================
# SYSTEM & ENVIRONMENT PATHS
# =========================================================
USER_HOME = os.path.expanduser("~")

# Path to offline Vosk speech recognition model
VOSK_MODEL_PATH = os.environ.get(
    "VOSK_MODEL_PATH",
    os.path.join(USER_HOME, "vosk-model-small-en-us-0.15")
)

# Folder to store captured photos
PHOTO_DIR = os.environ.get(
    "PHOTO_DIR",
    os.path.join(USER_HOME, "captured_photos")
)

# Folder to store received LoRa files (images, audio notes)
RECEIVED_DIR = os.environ.get(
    "RECEIVED_DIR",
    os.path.join(USER_HOME, "received_lora_files")
)

# =========================================================
# BLUETOOTH
# =========================================================
BT_CHECK_INTERVAL = 5.0   # seconds between connection status polls
BT_AUTOCONNECT = os.environ.get("BT_AUTOCONNECT", "1") == "1"

# Audio capture device (PipeWire default source, or override via env)
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "@DEFAULT_SOURCE@")
SAMPLE_RATE = 16000

# =========================================================
# I2C BUS & DEVICE ADDRESSES
# =========================================================
I2C_BUS = 1
LCD_ADDR = 0x27           # 16x2 Character LCD via PCF8574 backpack
MAX30102_ADDR = 0x57      # Pulse oximeter & heart rate sensor
MPU6050_ADDR = 0x68       # 6-axis accelerometer & gyroscope

# =========================================================
# SERIAL (UART) COMMUNICATION PORTS
# =========================================================
# Waveshare LoRa HAT (SX1262/SX1268) primary UART
LORA_PORT = os.environ.get("LORA_PORT", "/dev/serial0")
LORA_BAUD = 9600

# Production deployments use only the provisioned mission keyset. There is no
# shared development fallback key: an unprovisioned Pi must fail closed.
MESH_NONCE_FILE = os.environ.get(
    "MESH_NONCE_FILE", os.path.join(USER_HOME, ".wearable_mesh_nonce")
)
MISSION_KEYSET_PATH = os.environ.get("MISSION_KEYSET_PATH", os.path.join(USER_HOME, ".wearable_mission_keyset.json"))
DEVICE_ID = os.environ.get("DEVICE_ID", "")
PEER_ID = os.environ.get("PEER_ID", "")
PROVISION_SERVER_URL = os.environ.get("PROVISION_SERVER_URL", "")
TLS_CA_FILE = os.environ.get("TLS_CA_FILE", "")
TLS_CERT_FILE = os.environ.get("TLS_CERT_FILE", "")
TLS_KEY_FILE = os.environ.get("TLS_KEY_FILE", "")
GATEWAY_QUEUE_PATH = os.environ.get(
    "GATEWAY_QUEUE_PATH", os.path.join(USER_HOME, "gateway_queue.jsonl")
)
GATEWAY_ENABLED = os.environ.get("GATEWAY_ENABLED", "0") == "1"

# NEO-6M GPS receiver UART (typically UART3 on Pi 4 /dev/ttyAMA3)
GPS_PORT = os.environ.get("GPS_PORT", "/dev/ttyAMA3")
GPS_BAUD = 9600

# =========================================================
# GPIO PIN ASSIGNMENTS (BCM Numbers)
# =========================================================
# Sensors
DHT_PIN = 17              # GPIO17 (Physical Pin 11) - Verified working
SOUND_PIN = 26            # GPIO26 (Physical Pin 37)

# Push buttons (Active Low with pull-up)
BUTTON_PHOTO = 21         # GPIO21 (Physical Pin 40) - Photo capture
BUTTON_LORA_TX = 20       # GPIO20 (Physical Pin 38) - LoRa Send Trigger
BUTTON_LORA_RX = 16       # GPIO16 (Physical Pin 36) - LoRa Receive Trigger

# Waveshare LoRa HAT hardware mode pins
LORA_M0_PIN = 22          # GPIO22 (Physical Pin 15)
LORA_M1_PIN = 23          # GPIO23 (Physical Pin 16)

# =========================================================
# THRESHOLDS & TIMINGS
# =========================================================
DHT_INTERVAL = 2.5        # Seconds between DHT11 queries
MPU_INTERVAL = 0.1        # 10Hz throttle for MPU tilt queries
GPS_INTERVAL = 0.2        # 5Hz throttle for GPS NMEA reading
LCD_CYCLE_INTERVAL = 3.0  # Seconds per LCD dashboard page

TILT_THRESHOLD = 15.0     # Angle degrees before classifying Forward/Backward/Left/Right
FINGER_THRESHOLD = 30000  # MAX30102 IR threshold to detect finger touch
CAMERA_COOLDOWN = 3.0     # Cooldown between consecutive photo captures
LORA_COOLDOWN = 3.0       # Cooldown between consecutive LoRa transmissions

# Ensure storage directories exist
os.makedirs(PHOTO_DIR, exist_ok=True)
os.makedirs(RECEIVED_DIR, exist_ok=True)
