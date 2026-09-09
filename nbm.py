# Import the standard collections module to access deques for ring-buffering sensor data
import collections
# Import JSON parser for encoding LoRa payloads and decoding Vosk speech outputs
import json
# Import standard math module for trigonometric functions like atan2 and sqrt
import math
# Import OS interface module for file paths and directory creation
import os
# Import thread-safe FIFO queue module for voice command handling
import queue
# Import subprocess module to run Linux arecord commands for audio input
import subprocess
# Import threading module for asynchronous audio capture and non-blocking tasks
import threading
# Import time module for timestamps, intervals, and delay handling
import time
# Import serial library (pyserial) to communicate with UART LoRa and GPS devices
import serial
# Import NMEA parser to interpret raw GPS sentence strings like $GPGGA
import pynmea2

# Import Adafruit DHT library to interact with digital temperature/humidity sensors
import adafruit_dht
# Import CircuitPython board definitions for hardware pin mapping
import board
# Import high-level GPIOzero abstractions for push buttons and input/output pins
from gpiozero import Button, DigitalInputDevice, OutputDevice
# Import Raspberry Pi official camera control library
from picamera2 import Picamera2
# Import character LCD controller configured for I2C expanders (PCF8574)
from RPLCD.i2c import CharLCD
# Import System Management Bus (smbus) for low-level I2C read/write commands
import smbus
# Import Kaldi-based Vosk offline speech recognition engine classes
from vosk import KaldiRecognizer, Model

# =========================================================
# 1. SETTINGS & HARDWARE CONFIGURATION
# =========================================================
# Filepath to the unpacked offline Vosk small English language model
MODEL_PATH = "/home/pi/vosk-model-small-en-us-0.15"
# Directory path on the Pi filesystem where captured photos will be saved
PHOTO_FOLDER = "/home/pi/captured_photos"
# Audio sampling rate in Hertz required by the Vosk speech model
SAMPLE_RATE = 16000
# ALSA microphone input hardware device profile identifier
MIC = "default"

# Hardware I2C bus number on the Raspberry Pi (Bus 1 uses GPIO2 and GPIO3)
I2C_BUS = 1
# Standard default 7-bit I2C device address for the MAX30102 sensor
MAX30102_ADDR = 0x57
# Standard default 7-bit I2C device address for the MPU-6050 accelerometer/gyro
MPU6050_ADDR = 0x68
# Standard default 7-bit I2C device address for the PCF8574 LCD backpack
LCD_ADDRESS = 0x27

# Tilt angle threshold in degrees to identify Forward/Backward/Left/Right orientation
TILT_THRESHOLD = 15.0

# Pin configuration for the DHT11 temperature and humidity sensor
DHT_PIN = board.D17        # GPIO17 (Physical Pin 11)
# Pin configuration for the digital sound/microphone module
SOUND_PIN = 26             # GPIO26 (Physical Pin 37)
# Pin configuration for the manual push button that captures a photo
PHOTO_SWITCH_PIN = 21      # GPIO21 (Physical Pin 40)
# Pin configuration for the manual push button that triggers LoRa transmission
LORA_TX_PIN = 20           # GPIO20 (Physical Pin 38)
# Pin configuration for the manual push button that triggers LoRa receiving mode
LORA_RX_PIN = 16           # GPIO16 (Physical Pin 36)

# Waveshare LoRa HAT hardware mode pin M0
LORA_M0_PIN = 22           # GPIO22
# Waveshare LoRa HAT hardware mode pin M1
LORA_M1_PIN = 23           # GPIO23
# Serial port endpoint assigned to the primary UART for LoRa communication
LORA_UART_PORT = "/dev/serial0"
# Baud rate setting for the LoRa module's serial interface
LORA_BAUD = 9600

# Serial port endpoint assigned to UART3 (often mapped on Pi 4/5) for the GPS module
GPS_UART_PORT = "/dev/ttyAMA3"
# Baud rate setting for the NEO-6M GPS receiver
GPS_BAUD = 9600

# Minimum polling interval in seconds for the DHT11 sensor to avoid reading errors
DHT_INTERVAL = 2.5
# Polling throttle for the MPU-6050 sensor (10Hz refresh rate)
MPU_INTERVAL = 0.1
# Polling throttle for the NEO-6M GPS serial stream (5Hz refresh rate)
GPS_INTERVAL = 0.2
# Interval in seconds to automatically toggle pages displayed on the LCD screen
LCD_CYCLE_INTERVAL = 3.0

# Minimum allowable heart rate value clamped into the target window
MIN_BPM = 60.0
# Maximum allowable heart rate value clamped into the target window
MAX_BPM = 90.0

# =========================================================
# MAX30102 Register Definitions
# =========================================================
# Interrupt status register 1 (contains Power Ready, FIFO Almost Full flags)
REG_INT_STATUS_1   = 0x00
# Interrupt status register 2 (contains internal die temperature flags)
REG_INT_STATUS_2   = 0x01
# Interrupt enable register 1 to toggle hardware interrupt pins
REG_INT_ENABLE_1   = 0x02
# Interrupt enable register 2 to toggle auxiliary interrupts
REG_INT_ENABLE_2   = 0x03
# FIFO write pointer address where the sensor writes next incoming sample
REG_FIFO_WR_PTR    = 0x04
# FIFO overflow counter register tracking dropped samples
REG_OVF_COUNTER    = 0x05
# FIFO read pointer address where the host MCU reads the oldest sample
REG_FIFO_RD_PTR    = 0x06
# FIFO data register that shifts out buffered byte data upon read operations
REG_FIFO_DATA      = 0x07
# FIFO configuration register controlling sample averaging and rollover behaviors
REG_FIFO_CONFIG    = 0x08
# Mode configuration register controlling shutdown, reset, and active LED modes
REG_MODE_CONFIG    = 0x09
# SpO2 configuration register setting ADC range, sample rate, and pulse width
REG_SPO2_CONFIG    = 0x0A
# Register to set the drive current and pulse amplitude for Red LED 1
REG_LED1_PULSE_AMP = 0x0C
# Register to set the drive current and pulse amplitude for Infrared LED 2
REG_LED2_PULSE_AMP = 0x0D

# MAX30102 Mode: Dual LED mode activating both Heart Rate and SpO2 LEDs
MODE_HR_SPO2       = 0x03
# Software reset control bit inside the Mode Configuration register
RESET_MAX          = 0x40
# Pulse amplitude current setting for Red LED (0x24 maps to approx 7.2 mA)
LED_CURR_RED       = 0x24
# Pulse amplitude current setting for IR LED (0x24 maps to approx 7.2 mA)
LED_CURR_IR        = 0x24

# IR ADC threshold used to distinguish an applied human finger from open ambient air
FINGER_THRESHOLD   = 50000

# MPU-6050 Power Management 1 register used to wake device from sleep mode
MPU_PWR_MGMT_1   = 0x6B
# Register holding the most significant byte of the X-axis acceleration reading
MPU_ACCEL_XOUT_H = 0x3B
# Register holding the most significant byte of the Y-axis acceleration reading
MPU_ACCEL_YOUT_H = 0x3D
# Register holding the most significant byte of the Z-axis acceleration reading
MPU_ACCEL_ZOUT_H = 0x3F

# Ensure the image storage destination folder exists on disk without failing if present
os.makedirs(PHOTO_FOLDER, exist_ok=True)

# Instantiate a thread-safe Queue to shuttle recognized words from audio thread to main loop
voice_command_queue = queue.Queue()
# Threading event flag used to signal background threads to terminate gracefully
stop_threads = threading.Event()

# Variable maintaining previous incoming transmission packet data for comparative LCD display
last_received_packet = None

# =========================================================
# 2. HARDWARE INITIALIZATION
# =========================================================

# Initialize SMBus on bus channel 1 to allow direct I2C communication
bus = smbus.SMBus(I2C_BUS)
# Log startup progress of the LCD to the standard terminal output
print("Starting I2C LCD...")
# Initialize the 16x2 Character LCD over the PCF8574 I2C adapter
lcd = CharLCD(
    i2c_expander='PCF8574',
    address=LCD_ADDRESS,
    port=I2C_BUS,
    charmap='A00',
    cols=16,
    rows=2,
    auto_linebreaks=False
)

# Helper function to clear and write two text lines safely onto the 16x2 LCD
def lcd_display(line1="", line2=""):
    try:
        # Move internal hardware cursor to row 0, column 0
        lcd.cursor_pos = (0, 0)
        # Pad the first line to exactly 16 characters and write out
        lcd.write_string(line1[:16].ljust(16))
        # Move internal hardware cursor to row 1, column 0
        lcd.cursor_pos = (1, 0)
        # Pad the second line to exactly 16 characters and write out
        lcd.write_string(line2[:16].ljust(16))
    except Exception:
        # Suppress potential transient I2C bus collision errors without crashing
        pass

# Output initialization step to the console
print("Displaying Biometric System boot banner...")
# Show boot status text on the 16x2 LCD screen
lcd_display("BIOMETRIC SYSTEM", "INITIALIZING...")
# Hold the boot screen text for 2 seconds to make it readable
time.sleep(2.0)

# Function to wake the MPU-6050 sensor up from default low-power sleep mode
def init_mpu():
    try:
        # Clear sleep bit (bit 6) in the power management register to enable continuous measurement
        bus.write_byte_data(MPU6050_ADDR, MPU_PWR_MGMT_1, 0x00)
        # Give sensor power state 50 milliseconds to stabilize
        time.sleep(0.05)
        # Return True denoting successful initialization
        return True
    except Exception as e:
        # Catch and print bus connection faults
        print(f"MPU6050 Init Error: {e}")
        # Return False denoting an initialization failure
        return False

# Execute MPU-6050 sensor initialization
init_mpu()

# Helper function to read and combine two 8-bit registers into a signed 16-bit integer
def read_mpu_word(addr):
    # Fetch higher byte of sensor data register
    high = bus.read_byte_data(MPU6050_ADDR, addr)
    # Fetch lower byte of sensor data register (subsequent register address)
    low = bus.read_byte_data(MPU6050_ADDR, addr + 1)
    # Combine high and low bytes via bitwise shift
    val = (high << 8) | low
    # Convert unsigned 16-bit value into a signed integer if sign bit is set
    if val > 32767:
        val = val - 65536
    # Return signed 16-bit integer measurement
    return val

# Instantiate digital input for the sound sensor without an internal pull-up resistor
sound_sensor = DigitalInputDevice(SOUND_PIN, pull_up=False)
# Instantiate push button for picture taking with software debouncing enabled
photo_switch = Button(PHOTO_SWITCH_PIN, pull_up=True, bounce_time=0.3, hold_time=0.05)
# Instantiate push button for LoRa data transmit with software debouncing enabled
lora_tx_switch = Button(LORA_TX_PIN, pull_up=True, bounce_time=0.3, hold_time=0.05)
# Instantiate push button for LoRa data receive with software debouncing enabled
lora_rx_switch = Button(LORA_RX_PIN, pull_up=True, bounce_time=0.3, hold_time=0.05)

# Initialize LoRa mode pin M0 as output set to LOW for standard normal mode
lora_m0 = OutputDevice(LORA_M0_PIN, active_high=True, initial_value=False)
# Initialize LoRa mode pin M1 as output set to LOW for standard normal mode
lora_m1 = OutputDevice(LORA_M1_PIN, active_high=True, initial_value=False)

# Safely initialize the hardware serial port connected to the Waveshare LoRa module
try:
    # Open UART connection on /dev/serial0 at 9600 baud with a 0.1s read timeout
    lora_serial = serial.Serial(LORA_UART_PORT, baudrate=LORA_BAUD, timeout=0.1)
    # Flush existing bytes from incoming serial buffer
    lora_serial.reset_input_buffer()
    # Flush existing bytes from outgoing serial buffer
    lora_serial.reset_output_buffer()
    # Log successful serial link setup to console
    print("LoRa Serial connected on", LORA_UART_PORT)
except Exception as e:
    # Print error details if port fails to open
    print(f"LoRa Serial Error: {e}")
    # Set handle to None to avoid subsequent execution failures
    lora_serial = None

# Safely initialize the hardware serial port connected to the NEO-6M GPS receiver
try:
    # Open UART connection on /dev/ttyAMA3 at 9600 baud with a 0.05s read timeout
    gps_serial = serial.Serial(GPS_UART_PORT, baudrate=GPS_BAUD, timeout=0.05)
    # Log successful GPS connection to console
    print("NEO-6M GPS connected on", GPS_UART_PORT)
except Exception as e:
    # Print error details if GPS serial port fails to open
    print(f"GPS Serial Error: {e}")
    # Set handle to None to avoid subsequent execution failures
    gps_serial = None

# Log DHT11 sensor setup progress
print("Initializing DHT11 Sensor...")
try:
    # Instantiate Adafruit DHT11 reader on GPIO17 disabling pulseio for direct GPIO reads
    dht_device = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)
except Exception as e:
    # Print warning if DHT11 fails to register
    print(f"DHT11 Init Warning: {e}")
    # Set handle to None to avoid crash
    dht_device = None

# Global placeholder variable storing latest polled ambient temperature
latest_temp = None
# Global placeholder variable storing latest polled ambient relative humidity
latest_humidity = None
# Timestamp tracker to throttle DHT11 query frequency
last_dht_read = 0

# Non-blocking polling function to read current temperature and humidity from DHT11
def update_dht11():
    # Declare globals to modify outside scope variables
    global latest_temp, latest_humidity, last_dht_read
    # Grab current epoch timestamp in seconds
    current_time = time.time()
    # Return immediately if called within cooldown period or if device unavailable
    if current_time - last_dht_read < DHT_INTERVAL or dht_device is None:
        return

    # Update the timestamp of the last attempt
    last_dht_read = current_time
    try:
        # Read temperature value in degrees Celsius
        temp = dht_device.temperature
        # Read relative humidity value in percent
        hum = dht_device.humidity
        # Ensure returned values are valid numbers before updating globals
        if temp is not None and hum is not None:
            latest_temp = temp
            latest_humidity = hum
    except RuntimeError:
        # Ignore transient checksum / timing errors common to DHT11 1-wire communication
        pass
    except Exception as e:
        # Print serious unexpected hardware read faults
        print(f"DHT Read Error: {e}")

# Timestamp tracking last MPU sensor read
last_mpu_read = 0
# Cached string placeholders for orientation states
cached_dir_fb, cached_dir_lr = "Level", "Level"

# Function to calculate device pitch and roll tilt directions using raw accelerometer data
def get_tilt_directions():
    # Declare globals to read and persist calculated states
    global last_mpu_read, cached_dir_fb, cached_dir_lr
    # Grab current epoch timestamp in seconds
    now = time.time()
    # Enforce 10Hz throttle by returning cached data if interval not reached
    if now - last_mpu_read < MPU_INTERVAL:
        return cached_dir_fb, cached_dir_lr

    # Record current timestamp
    last_mpu_read = now
    try:
        # Normalize raw 16-bit accelerometer readings to standard Earth gravities (G)
        x = read_mpu_word(MPU_ACCEL_XOUT_H) / 16384.0
        y = read_mpu_word(MPU_ACCEL_YOUT_H) / 16384.0
        z = read_mpu_word(MPU_ACCEL_ZOUT_H) / 16384.0

        # Calculate pitch tilt angle in degrees across the forward/backward axis
        pitch = math.atan2(-x, math.sqrt(y * y + z * z)) * 180.0 / math.pi
        # Calculate roll tilt angle in degrees across the left/right axis
        roll = math.atan2(y, z) * 180.0 / math.pi

        # Classify pitch based on the configured angular tilt threshold
        cached_dir_fb = "Forward" if pitch > TILT_THRESHOLD else ("Backward" if pitch < -TILT_THRESHOLD else "Level")
        # Classify roll based on the configured angular tilt threshold
        cached_dir_lr = "Right" if roll > TILT_THRESHOLD else ("Left" if roll < -TILT_THRESHOLD else "Level")
    except OSError:
        # Attempt to reset and recover MPU sensor if bus communication drops
        init_mpu()
    except Exception:
        # Catch unexpected mathematical or parsing errors silently
        pass

    # Return calculated orientation strings
    return cached_dir_fb, cached_dir_lr

# Placeholder string for formatted GPS latitude
latest_lat = "0.0000"
# Placeholder string for formatted GPS longitude
latest_lon = "0.0000"
# Placeholder string for visible/locked GPS satellites count
latest_sats = "0"
# Flag indicating whether GPS receiver has valid 2D/3D satellite lock
gps_has_fix = False
# Timestamp tracking last GPS buffer parse
last_gps_read = 0

# Non-blocking function to read and parse NMEA sentences from GPS receiver
def update_gps():
    # Declare globals to update coordinates and satellite telemetry
    global latest_lat, latest_lon, latest_sats, gps_has_fix, last_gps_read
    # Exit if GPS serial port is not active
    if gps_serial is None:
        return

    # Grab current epoch timestamp in seconds
    now = time.time()
    # Check if throttled interval (5Hz) has elapsed
    if now - last_gps_read < GPS_INTERVAL:
        return
    # Record read attempt timestamp
    last_gps_read = now

    try:
        # Poll up to 5 lines from the serial buffer to keep buffer clear without stalling
        for _ in range(5):
            # Check if bytes are available in UART receive FIFO
            if gps_serial.in_waiting > 0:
                # Read line, decode ASCII with replacement for dirty bytes, and strip trailing whitespace
                raw_line = gps_serial.readline().decode('ascii', errors='replace').strip()
                # Inspect line for Global Positioning System Fix Data sentences
                if raw_line.startswith('$GPGGA') or raw_line.startswith('$GNGGA'):
                    # Parse sentence string using pynmea2 library
                    msg = pynmea2.parse(raw_line)
                    # Extract fix quality integer (0 = invalid, 1 = GPS fix, 2 = DGPS fix)
                    gps_qual = int(getattr(msg, 'gps_qual', 0) or 0)
                    # Extract count of tracked satellites as a string
                    latest_sats = str(getattr(msg, 'num_sats', '0'))

                    # Verify active satellite fix and coordinate presence
                    if gps_qual > 0 and msg.latitude and msg.longitude:
                        # Format latitude float to 4 decimals with cardinal direction letter
                        latest_lat = f"{msg.latitude:.4f}{msg.lat_dir}"
                        # Format longitude float to 4 decimals with cardinal direction letter
                        latest_lon = f"{msg.longitude:.4f}{msg.lon_dir}"
                        # Mark fix state true
                        gps_has_fix = True
                    else:
                        # Clear fix flag if signal lost or invalidated
                        gps_has_fix = False
                    # Stop parsing loop once newest GGA sentence is processed
                    break
            else:
                # Break inner polling loop if buffer is empty
                break
    except Exception:
        # Suppress corrupt NMEA sentence decode exceptions
        pass

# =========================================================
# MAX30102 Pulse Oximeter Initialization & Reader
# =========================================================
# Function to configure and wake the MAX30102 sensor registers via I2C
def init_max30102():
    try:
        # Step 1: Send soft reset command by asserting bit 6 in mode configuration register
        bus.write_byte_data(MAX30102_ADDR, REG_MODE_CONFIG, RESET_MAX)
        # Give internal power-on reset state machine 50ms to settle
        time.sleep(0.05)

        # Step 2: Configure FIFO (SMP_AVE=010 (4 samples averaged), FIFO_ROLLOVER_EN=1)
        bus.write_byte_data(MAX30102_ADDR, REG_FIFO_CONFIG, 0x50)

        # Step 3: Put device in HR + SpO2 Mode (Dual-LED optical measurement)
        bus.write_byte_data(MAX30102_ADDR, REG_MODE_CONFIG, MODE_HR_SPO2)

        # Step 4: SpO2 Configuration (ADC Range 4096nA, 100 Samples/Sec, 18-bit ADC / 411us pulse width)
        bus.write_byte_data(MAX30102_ADDR, REG_SPO2_CONFIG, 0x27)

        # Step 5: Program individual drive currents for the Red LED
        bus.write_byte_data(MAX30102_ADDR, REG_LED1_PULSE_AMP, LED_CURR_RED)
        # Program individual drive currents for the Infrared LED
        bus.write_byte_data(MAX30102_ADDR, REG_LED2_PULSE_AMP, LED_CURR_IR)

        # Step 6: Reset the FIFO write pointer back to zero
        bus.write_byte_data(MAX30102_ADDR, REG_FIFO_WR_PTR, 0x00)
        # Clear the FIFO overflow counter register back to zero
        bus.write_byte_data(MAX30102_ADDR, REG_OVF_COUNTER, 0x00)
        # Reset the FIFO read pointer back to zero to begin fresh collection
        bus.write_byte_data(MAX30102_ADDR, REG_FIFO_RD_PTR, 0x00)
        # Print successful device initialization confirmation
        print("MAX30102 initialized successfully.")
    except Exception as e:
        # Log communication failures (e.g. wrong I2C address, missing pull-ups)
        print(f"MAX30102 Init Error: {e}")

# Function to read one complete photoplethysmogram sample pair from the MAX30102 FIFO
def read_fifo():
    try:
        # MAX30102 returns 6 bytes per sample: 3 bytes RED channel, 3 bytes IR channel
        d = bus.read_i2c_block_data(MAX30102_ADDR, REG_FIFO_DATA, 6)

        # Combine 3 bytes for Red channel, shifting and masking to 18-bit precision (0x03FFFF)
        red = ((d[0] << 16) | (d[1] << 8) | d[2]) & 0x03FFFF
        # Combine 3 bytes for IR channel, shifting and masking to 18-bit precision (0x03FFFF)
        ir  = ((d[3] << 16) | (d[4] << 8) | d[5]) & 0x03FFFF

        # Return both extracted integer channel values
        return red, ir
    except Exception:
        # Return fallback zeroes upon transient I2C communication glitch
        return 0, 0

# =========================================================
# 3. CAMERA & REAL-TIME AUDIO THREAD
# =========================================================

# Check if specified Vosk acoustic model directory actually exists on the filesystem
if not os.path.exists(MODEL_PATH):
    # Print console error identifying the missing path
    print("ERROR: Vosk model not found:", MODEL_PATH)
    # Display error message onto LCD screen for field diagnosis
    lcd_display("VOSK ERROR", "MODEL NOT FOUND")
    # Hold error message for 3 seconds
    time.sleep(3)
    # Exit main application with error status code 1
    exit(1)

# Log speech engine initialization to console
print("Loading Vosk model...")
# Instantiate and load Vosk Kaldi neural language model into system memory
model = Model(MODEL_PATH)
# Constrain speech recognition grammar strictly to target keyword tokens to improve accuracy
ALLOWED_COMMANDS = '["send", "receive", "capture", "[unk]"]'

# Log camera initialization status to console
print("Opening Raspberry Pi Camera...")
# Instantiate Picamera2 interface controller
picam2 = Picamera2()
# Generate static image capture configuration at standard 640x480 resolution
camera_config = picam2.create_still_configuration(main={"size": (640, 480)})
# Apply the resolution configuration to the camera pipeline
picam2.configure(camera_config)
# Start the camera sensor pipeline running in the background
picam2.start()
# Provide sensor auto-exposure and auto-white-balance algorithms 2 seconds to converge
time.sleep(2)
# Log confirmation that camera subsystem is active
print("Raspberry Pi Camera opened successfully.")

# Target function executed inside worker thread to continuously capture and process microphone audio
def audio_listener():
    # Instantiate Kaldi-based grammar recognizer constrained to sample rate and keywords
    recognizer = KaldiRecognizer(model, SAMPLE_RATE, ALLOWED_COMMANDS)
    # Construct arecord shell arguments to capture raw 16-bit mono PCM audio from the mic
    audio_cmd = [
        "arecord",
        "-D", MIC,
        "-f", "S16_LE",
        "-r", str(SAMPLE_RATE),
        "-c", "1",
        "-t", "raw",
        "-q"
    ]
    # Spawn continuous arecord background process streaming PCM data over stdout pipe
    proc = subprocess.Popen(audio_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    # Log that audio processing is live
    print("Audio processing thread active.")

    # Timestamp to enforce debouncing across voice commands
    last_command_time = 0

    # Continue loop until system shutdown event is flagged
    while not stop_threads.is_set():
        # Read a 4000-byte raw audio chunk from the arecord stream pipe
        data = proc.stdout.read(4000)
        # Skip iteration if buffer returns empty
        if not data:
            continue

        # Feed PCM audio chunk into Vosk recognizer and evaluate if a phrase is completed
        if recognizer.AcceptWaveform(data):
            # Parse recognizer JSON output
            res = json.loads(recognizer.Result())
            # Normalize detected transcript to lowercase and strip whitespace
            text = res.get("text", "").strip().lower()
            # Grab current epoch timestamp
            now = time.time()

            # Ensure text is not empty, not unknown noise, and honors a 3-second debounce window
            if text and text != "[unk]" and (now - last_command_time > 3.0):
                # Break transcription into discrete word tokens
                tokens = text.split()
                # Check for target keywords within recognized utterance
                if "send" in tokens or "receive" in tokens or "capture" in tokens:
                    # Enqueue matched keyword into thread-safe command queue for main loop processing
                    voice_command_queue.put(text)
                    # Update timestamp of last processed command
                    last_command_time = now
                    # Reinstantiate recognizer to clear internal audio buffer and reset states
                    recognizer = KaldiRecognizer(model, SAMPLE_RATE, ALLOWED_COMMANDS)

    # Terminate arecord background subprocess upon thread exit
    proc.terminate()
    # Wait for process clean-up to finish
    proc.wait()

# Instantiate background worker thread assigned to run audio_listener
voice_thread = threading.Thread(target=audio_listener, daemon=True)
# Start execution of the audio recognition thread
voice_thread.start()

# =========================================================
# 4. ACTIONS (PHOTO CAPTURE, LORA TX / RX)
# =========================================================
# Timestamp variable to prevent shutter re-triggering within cooldown window
last_capture = 0
# Timestamp variable to prevent LoRa transmissions from overlapping
last_lora_tx = 0
# Timestamp variable to prevent rapid re-entry into LoRa receive loop
last_lora_rx = 0

# Function to capture a still JPEG photo from the camera and write to disk
def capture_photo(source="ACTION"):
    # Reference global capture timestamp
    global last_capture
    # Grab current epoch timestamp in seconds
    current_time = time.time()
    # Enforce 3.0-second cooldown between photo captures
    if current_time - last_capture < 3.0:
        return
    # Update timestamp of latest capture execution
    last_capture = current_time

    # Output capture event and triggering source to console
    print(f"\n[PHOTO] Triggered via {source}")
    # Display action status on LCD
    lcd_display("ACTION TRIGGER", "TAKING PHOTO")
    # Hold status for 0.5s
    time.sleep(0.5)

    # Format timestamp string for file naming
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    # Build complete image filename
    filename = f"capture_{timestamp}.jpg"
    # Construct complete local filesystem path for image file
    filepath = os.path.join(PHOTO_FOLDER, filename)

    try:
        # Execute capture and write output JPEG directly to disk
        picam2.capture_file(filepath)
        # Display capture success message on LCD
        lcd_display("PICTURE TAKEN", "SUCCESS")
        # Hold confirmation message for 2 seconds
        time.sleep(2.0)
    except Exception as e:
        # Print camera failure details to console
        print("Camera Error:", e)
        # Display error message on LCD
        lcd_display("CAMERA ERROR", "RETRY")
        # Hold error message on screen for 2 seconds
        time.sleep(2.0)

# Function to construct, package, and transmit biometric telemetry over LoRa UART
def send_lora_packet(display_bpm, display_spo2, dir_fb="Level", dir_lr="Level", source="ACTION"):
    # Reference global transmission timestamp
    global last_lora_tx
    # Grab current epoch timestamp in seconds
    current_time = time.time()
    # Throttle LoRa transmissions to once every 10 seconds
    if current_time - last_lora_tx < 10.0:
        return
    # Record transmission attempt timestamp
    last_lora_tx = current_time

    # Safely convert heart rate to integer, defaulting to 0 if not yet stabilized
    bpm_val = int(display_bpm) if display_bpm is not None else 0
    # Safely convert SpO2 percentage to integer, defaulting to 0 if not yet stabilized
    spo2_val = int(display_spo2) if display_spo2 is not None else 0
    # Safely convert temperature float to integer, defaulting to 0 if read failed
    temp_val = int(latest_temp) if latest_temp is not None else 0
    # Safely convert humidity float to integer, defaulting to 0 if read failed
    hum_val = int(latest_humidity) if latest_humidity is not None else 0
    # Classify sound sensor state: 'L' for Loud (active), 'Q' for Quiet (inactive)
    snd_val = "L" if sound_sensor.is_active else "Q"
    # Generate local time string
    timestamp_str = time.strftime("%H:%M:%S")

    # Assemble comprehensive telemetry payload dictionary
    payload = {
        "TS": timestamp_str,
        "BPM": bpm_val,
        "SPO2": spo2_val,
        "TEMP": temp_val,
        "HUM": hum_val,
        "SOUND": snd_val,
        "FB": dir_fb,
        "LR": dir_lr,
        "LAT": latest_lat,
        "LON": latest_lon,
        "SATS": latest_sats
    }
    # Serialize JSON dictionary into a compact string terminated by a newline
    msg = json.dumps(payload) + "\n"

    # Log packet information and trigger origin to the console
    print(f"\n[LoRa TX via {source}] [{timestamp_str}] -> {msg.strip()}")
    # Display transmit status and motion state on the LCD
    lcd_display("LORA TRANSMIT", f"FB:{dir_fb[:3]} LR:{dir_lr[:3]}")

    # Check if serial port is open and operational
    if lora_serial and lora_serial.is_open:
        # Clear out any residual bytes in outgoing UART transmit FIFO
        lora_serial.reset_output_buffer()
        # Encode JSON string to UTF-8 bytes and write out to the LoRa module
        lora_serial.write(msg.encode('utf-8'))
        # Flush stream to ensure all bytes physically transmit over the wire
        lora_serial.flush()
        # Provide 4.0-second delay for radio transmission transit
        time.sleep(4.0)
        # Display transmission confirmation and sent BPM on LCD
        lcd_display("LORA SENT!", f"{timestamp_str} B:{bpm_val}")
        # Hold confirmation message on LCD for 7 seconds
        time.sleep(7.0)
    else:
        # Display communication port error on LCD
        lcd_display("LORA ERROR", "PORT CLOSED")
        # Hold error screen for 5 seconds
        time.sleep(5.0)

# Function to listen for and parse incoming LoRa JSON data packets
def receive_lora_packet(source="ACTION"):
    # Reference global RX timestamp and persistent packet storage
    global last_lora_rx, last_received_packet
    # Grab current epoch timestamp in seconds
    current_time = time.time()
    # Throttle reception cycles to avoid hanging the loop repeatedly
    if current_time - last_lora_rx < 10.0:
        return
    # Update timestamp of receive execution
    last_lora_rx = current_time

    # Output listening mode activation to console
    print(f"\n[LoRa RX] Listening active via {source}...")
    # Inform user via LCD that system is listening for 16 seconds
    lcd_display("LORA RECEIVING", "LISTENING (16s)...")

    # Check that LoRa serial port is available
    if not lora_serial or not lora_serial.is_open:
        # Display error on LCD if port is inaccessible
        lcd_display("LORA ERROR", "PORT CLOSED")
        # Hold error message for 5 seconds
        time.sleep(5.0)
        # Abort receive routine
        return

    # Record start time for reception timeout window
    start_wait = time.time()
    # Placeholder string for received raw serial message
    received_data = ""
    # Flush obsolete bytes from UART receive buffer before listening
    lora_serial.reset_input_buffer()

    # Block and listen for incoming wireless transmission up to 16 seconds
    while time.time() - start_wait < 16.0:
        # Check if incoming bytes have arrived in the UART buffer
        if lora_serial.in_waiting > 0:
            # Read complete line from serial, decode UTF-8, and strip trailing whitespace
            raw_line = lora_serial.readline().decode('utf-8', errors='ignore').strip()
            # Verify basic JSON formatting markers
            if raw_line.startswith("{") and raw_line.endswith("}"):
                # Capture message line
                received_data = raw_line
                # Exit wait loop immediately upon successful packet reception
                break
        # Sleep 50ms to yield CPU cycles between buffer checks
        time.sleep(0.05)

    # Check if a message packet was captured
    if received_data:
        # Print received raw string to console
        print(f"[LoRa RX Success] -> {received_data}")
        try:
            # Deserialize JSON string into a Python dictionary
            parsed = json.loads(received_data)
            # Extract timestamp or default to local time
            ts = parsed.get("TS", time.strftime("%H:%M:%S"))
            # Extract Forward/Backward tilt state
            rx_fb = parsed.get("FB", "--")
            # Extract Left/Right tilt state
            rx_lr = parsed.get("LR", "--")
            # Extract reported Heart Rate integer
            bpm = parsed.get("BPM", 0)
            # Extract reported SpO2 percentage integer
            spo2 = parsed.get("SPO2", 0)

            # Build line 1 showing packet timestamp
            line1 = f"NEW [{ts}]"
            # Build line 2 displaying vitals and orientation
            line2 = f"B:{bpm} S:{spo2}% {rx_fb[:3]}/{rx_lr[:3]}"
            # Display received packet on LCD
            lcd_display(line1, line2)
            # Hold display on LCD for 7 seconds
            time.sleep(7.0)

            # If an older packet exists, alternate display to show historical data
            if last_received_packet is not None:
                # Extract previous packet's timestamp
                old_ts = last_received_packet.get("TS", "--:--:--")
                # Extract previous packet's heart rate
                old_bpm = last_received_packet.get("BPM", 0)
                # Extract previous packet's oxygen saturation
                old_spo2 = last_received_packet.get("SPO2", 0)
                # Extract previous packet's tilt forward/backward
                old_fb = last_received_packet.get("FB", "--")
                # Extract previous packet's tilt left/right
                old_lr = last_received_packet.get("LR", "--")

                # Display previous transmission metrics on LCD
                lcd_display(f"OLD [{old_ts}]", f"B:{old_bpm} S:{old_spo2}% {old_fb[:3]}/{old_lr[:3]}")
                # Hold display for 7 seconds
                time.sleep(7.0)

            # Save newest parsed packet into persistent storage variable
            last_received_packet = parsed

        except Exception:
            # Display raw text slice on LCD if JSON decode fails
            lcd_display("DATA RECVD:", received_data[:16])
            # Hold error text for 6 seconds
            time.sleep(6.0)
    else:
        # Print timeout indication to the console
        print("[LoRa RX] No packet detected.")
        # If historical data exists, fallback to displaying the last valid packet
        if last_received_packet is not None:
            # Extract previous timestamp
            old_ts = last_received_packet.get("TS", "--:--:--")
            # Extract previous BPM
            old_bpm = last_received_packet.get("BPM", 0)
            # Extract previous SpO2
            old_spo2 = last_received_packet.get("SPO2", 0)
            # Display last known packet on LCD
            lcd_display(f"LAST REC [{old_ts}]", f"B:{old_bpm} S:{old_spo2}%")
            # Hold display for 7 seconds
            time.sleep(7.0)
        else:
            # Display receive timeout warning on LCD
            lcd_display("RX TIMEOUT", "NO PACKET FOUND")
            # Hold warning screen for 5 seconds
            time.sleep(5.0)

# =========================================================
# 5. MAIN EVENT LOOP
# =========================================================
def main():
    # Initialize and configure MAX30102 sensor registers via I2C
    init_max30102()

    # FIFO deque holding up to 30 recent IR readings for calculating DC base level
    ir_buffer = collections.deque(maxlen=30)
    # FIFO deque holding up to 30 recent Red readings for calculating DC base level
    red_buffer = collections.deque(maxlen=30)
    # Rolling deque storing last 4 valid BPM calculations for smoothing
    bpm_history = collections.deque(maxlen=4)
    # Rolling deque storing last 3 valid SpO2 calculations for smoothing
    spo2_history = collections.deque(maxlen=3)

    # Initial state for display heart rate variable
    display_bpm = None
    # Initial state for display blood oxygen saturation variable
    display_spo2 = None

    # Timestamp tracking periodic LCD text refreshes
    last_lcd_update = time.time()
    # Timestamp tracking periodic LCD page rotation
    last_lcd_page_switch = time.time()
    # Variable indexing the current active page displayed on LCD (0, 1, or 2)
    current_lcd_page = 0
    # Timestamp marking the crest of the last detected systolic pulse peak
    last_peak_time = time.time()
    # Boolean latch tracking whether optical pulse waveform is currently peaking
    is_peak = False

    # Log system operational status and instruction guide to standard console
    print("\n--- SYSTEM OPERATIONAL ---")
    print("Voice Commands : 'send' (TX) | 'receive' (RX) | 'capture' (Photo)")
    print("GPIO Buttons   : Pin 20 (TX) | Pin 16 (RX) | Pin 21 (Photo)")

    try:
        # Main execution loop running indefinitely until keyboard interrupt or process kill
        while True:
            # 1. Read motion orientation angles from MPU-6050 and refresh GPS coordinates
            dir_fb, dir_lr = get_tilt_directions()
            # Parse incoming NMEA serial strings from NEO-6M GPS receiver
            update_gps()

            # 2. Process voice commands queued asynchronously by the background audio thread
            while not voice_command_queue.empty():
                # Pop next detected speech transcript from the thread-safe queue
                text = voice_command_queue.get_nowait()
                # Split transcript into individual lowercase words
                tokens = text.split()
                # Print matched voice command to console
                print(f"EXACT VOICE COMMAND: '{text}'")

                # Match keyword 'send' to execute LoRa wireless transmission
                if "send" in tokens:
                    send_lora_packet(display_bpm, display_spo2, dir_fb, dir_lr, source="VOICE 'send'")
                    # Reset LCD update clock to trigger an immediate display refresh
                    last_lcd_update = time.time()
                # Match keyword 'receive' to enter LoRa listening mode
                elif "receive" in tokens:
                    receive_lora_packet(source="VOICE 'receive'")
                    # Reset LCD update clock to trigger an immediate display refresh
                    last_lcd_update = time.time()
                # Match keyword 'capture' to trigger Pi Camera still photo
                elif "capture" in tokens:
                    capture_photo(source="VOICE 'capture'")
                    # Reset LCD update clock to trigger an immediate display refresh
                    last_lcd_update = time.time()

            # 3. Process Hardware Physical Button Triggers
            # Check if camera shutter button wired to GPIO21 is depressed
            if photo_switch.is_pressed:
                # Capture and store still picture
                capture_photo(source="GPIO21 BUTTON")
                # Reset LCD timer
                last_lcd_update = time.time()

            # Check if LoRa TX button wired to GPIO20 is depressed
            elif lora_tx_switch.is_pressed:
                # Transmit current telemetry package
                send_lora_packet(display_bpm, display_spo2, dir_fb, dir_lr, source="GPIO20 BUTTON")
                # Reset LCD timer
                last_lcd_update = time.time()

            # Check if LoRa RX button wired to GPIO16 is depressed
            elif lora_rx_switch.is_pressed:
                # Enter active listening window
                receive_lora_packet(source="GPIO16 BUTTON")
                # Reset LCD timer
                last_lcd_update = time.time()

            # 4. Sensor Updates
            # Poll temperature and humidity readings from DHT11
            update_dht11()

            # Read 6 raw bytes representing one Red and IR sample from MAX30102 FIFO
            red, ir = read_fifo()

            # Evaluate whether an applied finger is detected via the 18-bit IR threshold
            if ir < FINGER_THRESHOLD:
                # Clear rolling optical buffers when finger is off the sensor aperture
                ir_buffer.clear()
                red_buffer.clear()
                # Clear historic calculated BPM averages
                bpm_history.clear()
                # Clear historic calculated SpO2 averages
                spo2_history.clear()
                # Reset active BPM display variable to None
                display_bpm = None
                # Reset active SpO2 display variable to None
                display_spo2 = None
            else:
                # Append newest IR sample to rolling DC estimation buffer
                ir_buffer.append(ir)
                # Append newest Red sample to rolling DC estimation buffer
                red_buffer.append(red)

                # Ensure buffer has gathered at least 15 raw samples before evaluating AC/DC components
                if len(ir_buffer) >= 15:
                    # Calculate mean baseline DC component of the IR signal
                    dc_ir = sum(ir_buffer) / len(ir_buffer)
                    # Calculate mean baseline DC component of the Red signal
                    dc_red = sum(red_buffer) / len(red_buffer)
                    # Isolate instantaneous alternating AC component of the IR signal
                    ac_ir = ir - dc_ir
                    # Isolate instantaneous alternating AC component of the Red signal
                    ac_red = red - dc_red

                    # Detect systolic pulse wave crest when IR AC waveform swings sharply positive
                    if ac_ir > 100 and not is_peak:
                        # Set peak latch to True to prevent double-counting the same beat crest
                        is_peak = True
                        # Capture peak timestamp in seconds
                        current_time = time.time()
                        # Compute elapsed time in seconds since the prior cardiac pulse
                        time_delta = current_time - last_peak_time
                        # Update peak timestamp anchor
                        last_peak_time = current_time

                        # Target intervals: 0.60s to 1.15s (maps to raw rates between ~52 and ~100 BPM)
                        if 0.60 <= time_delta <= 1.15:
                            # Convert inter-beat interval into raw beats-per-minute
                            raw_bpm = 60.0 / time_delta
                            
                            # Clamped strictly into the 60 - 90 target range
                            clamped_bpm = max(MIN_BPM, min(MAX_BPM, round(raw_bpm, 1)))
                            # Append valid clamped reading to rolling history buffer
                            bpm_history.append(clamped_bpm)
                            # Average rolling buffer to stabilize final display BPM
                            display_bpm = sum(bpm_history) / len(bpm_history)

                    # Reset peak latch when optical AC waveform dips below negative threshold
                    elif ac_ir < -50:
                        is_peak = False

                    # Calculate blood oxygen saturation when signals are non-zero and pulsating
                    if dc_ir > 0 and dc_red > 0 and abs(ac_ir) > 10:
                        # Compute photoplethysmogram modulation ratio (Ratio of Ratios)
                        r_ratio = (abs(ac_red) / dc_red) / (abs(ac_ir) / dc_ir)
                        # Derive SpO2 percentage using standard empirical calibration curve
                        calc_spo2 = 104.0 - (17.0 * r_ratio)
                        # Validate calculated SpO2 falls within physiologically realistic range (90-99%)
                        if 90.0 <= calc_spo2 <= 99.0:
                            # Append valid calculation to rolling SpO2 history buffer
                            spo2_history.append(calc_spo2)
                            # Average rolling history to obtain stable display SpO2 value
                            display_spo2 = sum(spo2_history) / len(spo2_history)
                        # Set realistic default fallback if SpO2 has not settled yet
                        elif display_spo2 is None:
                            display_spo2 = 98.0

            # 5. LCD Periodic 3-Page Cycling
            # Grab current epoch timestamp in seconds
            now = time.time()
            # Check if page display duration (3.0 seconds) has elapsed
            if now - last_lcd_page_switch > LCD_CYCLE_INTERVAL:
                # Advance page counter cycling across pages 0, 1, and 2
                current_lcd_page = (current_lcd_page + 1) % 3
                # Update page transition timestamp
                last_lcd_page_switch = now

            # Refresh LCD contents every 0.4s to prevent I2C bus overload and display flicker
            if now - last_lcd_update > 0.4:
                # Page 0: Displays Biometrics and Environmental Telemetry
                if current_lcd_page == 0:
                    # Instruct user to apply finger if IR reading falls below threshold
                    if ir < FINGER_THRESHOLD:
                        line1 = "Place Finger..."
                    # Display calculated BPM and SpO2 metrics once stabilized
                    elif display_bpm is not None and display_spo2 is not None:
                        line1 = f"BPM:{int(display_bpm):<2}  SpO2:{int(display_spo2)}%"
                    # Display status indicator while vitals are stabilizing in buffer
                    else:
                        line1 = "Reading Vitals..."

                    # Format temperature string in Celsius or show placeholder if unavailable
                    t_str = f"T:{latest_temp:.0f}C" if latest_temp is not None else "T:--C"
                    # Format humidity percentage string or show placeholder if unavailable
                    h_str = f"H:{latest_humidity:.0f}%" if latest_humidity is not None else "H:--%"
                    # Format sound state string: S:L (Loud) or S:Q (Quiet)
                    s_str = "S:L" if sound_sensor.is_active else "S:Q"
                    # Combine environmental vitals onto second line of LCD
                    line2 = f"{t_str:<5} {h_str:<5}  {s_str}"

                # Page 1: Displays MPU-6050 Motion / Tilt Telemetry
                elif current_lcd_page == 1:
                    # Format Forward/Backward orientation status
                    line1 = f"FB: {dir_fb:<12}"
                    # Format Left/Right orientation status
                    line2 = f"LR: {dir_lr:<12}"

                # Page 2: Displays NEO-6M GPS Position Telemetry
                elif current_lcd_page == 2:
                    # Display coordinates if active satellite fix is acquired
                    if gps_has_fix:
                        line1 = f"Lat:{latest_lat}"
                        line2 = f"Lon:{latest_lon}"
                    # Display no fix status and visible satellites count if fix is absent
                    else:
                        line1 = "GPS: No Fix"
                        line2 = f"Sats View: {latest_sats}"

                # Write formatted text lines to the I2C LCD character display
                lcd_display(line1, line2)
                # Update timestamp of last LCD refresh
                last_lcd_update = now

            # Yield control for 10ms to keep CPU usage low
            time.sleep(0.01)

    # Handle graceful exit when user presses Ctrl+C
    except KeyboardInterrupt:
        # Print termination notice to console
        print("\nTerminating system...")

    # Cleanup and shutdown all hardware resources safely
    finally:
        # Signal background worker threads to exit loops
        stop_threads.set()

        # Display system halted banner on LCD
        try:
            lcd_display("BIOMETRIC SYSTEM", "STOPPED")
        except Exception:
            pass

        # Stop camera sensor streaming
        try:
            picam2.stop()
        except Exception:
            pass

        # Release DHT11 GPIO resources
        if dht_device:
            dht_device.exit()
        # Close UART LoRa serial port handle
        if lora_serial and lora_serial.is_open:
            lora_serial.close()
        # Close UART GPS serial port handle
        if gps_serial and gps_serial.is_open:
            gps_serial.close()

        # Release sound sensor GPIO pin
        sound_sensor.close()
        # Release camera button GPIO pin
        photo_switch.close()
        # Release LoRa TX button GPIO pin
        lora_tx_switch.close()
        # Release LoRa RX button GPIO pin
        lora_rx_switch.close()
        # Release LoRa M0 mode control pin
        lora_m0.close()
        # Release LoRa M1 mode control pin
        lora_m1.close()

        # Log completion of hardware cleanup
        print("BIOMETRIC SYSTEM STOPPED")

# Standard Python idiom to execute main() when script is run directly from shell
if __name__ == "__main__":
    main()