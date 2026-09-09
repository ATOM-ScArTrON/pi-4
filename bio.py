import collections
import json
import math
import os
import subprocess
import time
import serial
import pynmea2

import adafruit_dht
import board
from gpiozero import Button, DigitalInputDevice, OutputDevice
from mpu6050 import mpu6050
from picamera2 import Picamera2
from RPLCD.i2c import CharLCD
import smbus
from vosk import KaldiRecognizer, Model

# =========================================================
# 1. SETTINGS & HARDWARE ADDRESSES
# =========================================================
MODEL_PATH = "/home/pi/vosk-model-small-en-us-0.15"
PHOTO_FOLDER = "/home/pi/captured_photos"
SAMPLE_RATE = 16000
MIC = "default"

I2C_BUS = 1
MAX30102_ADDR = 0x57
MPU6050_ADDR = 0x68
LCD_ADDRESS = 0x27

# Sensor & Switch Pins
DHT_PIN = board.D17       # GPIO4  (Physical Pin 11)
SOUND_PIN = 26          # GPIO17 (Physical Pin 37)
PHOTO_SWITCH_PIN = 21    # GPIO21 (Physical Pin 40)
LORA_TX_PIN = 20         # GPIO20 (Physical Pin 38)
LORA_RX_PIN = 16         # GPIO16 (Physical Pin 36)

# LoRa HAT Pins (Primary Serial /dev/serial0)
LORA_M0_PIN = 22         # GPIO22
LORA_M1_PIN = 23         # GPIO23
LORA_UART_PORT = "/dev/serial0"
LORA_BAUD = 9600

# GPS NEO-6M Serial Port (UART3 on GPIO4/5 -> /dev/ttyAMA1)
GPS_UART_PORT = "/dev/ttyAMA1"
GPS_BAUD = 9600

DHT_INTERVAL = 2.5
MPU_INTERVAL = 0.1       # Throttle to 10Hz to prevent I2C bus flooding
TILT_THRESHOLD = 5.0
LCD_CYCLE_INTERVAL = 3.0
MIN_BPM = 55.0
MAX_BPM = 85.0

# MAX30102 Registers
REG_INTR_STATUS_1 = 0x00
REG_FIFO_WR_PTR   = 0x04
REG_OVF_COUNTER   = 0x05
REG_FIFO_RD_PTR   = 0x06
REG_FIFO_DATA     = 0x07
REG_FIFO_CONFIG   = 0x08
REG_MODE_CONFIG   = 0x09
REG_SPO2_CONFIG   = 0x0A
REG_LED1_PA       = 0x0C
REG_LED2_PA       = 0x0D

os.makedirs(PHOTO_FOLDER, exist_ok=True)

# =========================================================
# 2. HARDWARE INITIALIZATION
# =========================================================

bus = smbus.SMBus(I2C_BUS)
print("Starting I2C LCD...")
lcd = CharLCD(
    i2c_expander='PCF8574',
    address=LCD_ADDRESS,
    port=I2C_BUS,
    charmap='A00',
    cols=16,
    rows=2,
    auto_linebreaks=False
)

def lcd_display(line1="", line2=""):
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(line1[:16].ljust(16))
    lcd.cursor_pos = (1, 0)
    lcd.write_string(line2[:16].ljust(16))

# --- BIOMETRIC SYSTEM BOOT BANNER ---
print("Displaying Biometric System boot banner...")
lcd_display("BIOMETRIC SYSTEM", "INITIALIZING...")
time.sleep(2.5)

# MPU-6050 Motion Sensor
print("Initializing MPU6050 Sensor...")
try:
    mpu_sensor = mpu6050(MPU6050_ADDR)
except Exception as e:
    print(f"MPU6050 Init Error: {e}")
    mpu_sensor = None

# Sensors & Trigger Switches
sound_sensor = DigitalInputDevice(SOUND_PIN, pull_up=False)
photo_switch = Button(PHOTO_SWITCH_PIN, pull_up=True, bounce_time=0.3, hold_time=0.05)
lora_tx_switch = Button(LORA_TX_PIN, pull_up=True, bounce_time=0.3, hold_time=0.05)
lora_rx_switch = Button(LORA_RX_PIN, pull_up=True, bounce_time=0.3, hold_time=0.05)

# Waveshare LoRa HAT Control
lora_m0 = OutputDevice(LORA_M0_PIN, active_high=True, initial_value=False)
lora_m1 = OutputDevice(LORA_M1_PIN, active_high=True, initial_value=False)

# LoRa Serial Port
try:
    lora_serial = serial.Serial(LORA_UART_PORT, baudrate=LORA_BAUD, timeout=0.1)
    lora_serial.reset_input_buffer()
    lora_serial.reset_output_buffer()
    print("LoRa Serial connected on", LORA_UART_PORT)
except Exception as e:
    print(f"LoRa Serial Error: {e}")
    lora_serial = None

# GPS NEO-6M Serial Port
try:
    gps_serial = serial.Serial(GPS_UART_PORT, baudrate=GPS_BAUD, timeout=0.05)
    print("NEO-6M GPS connected on", GPS_UART_PORT)
except Exception as e:
    print(f"GPS Serial Error: {e}")
    gps_serial = None

# DHT11 Sensor
print("Initializing DHT11 Sensor...")
try:
    dht_device = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)
except Exception as e:
    print(f"DHT11 Init Warning: {e}")
    dht_device = None

latest_temp = None
latest_humidity = None
last_dht_read = 0

def update_dht11():
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

# MPU-6050 Motion Calculation (Non-blocking cache)
last_mpu_read = 0
cached_pitch, cached_roll = 0.0, 0.0
cached_dir_fb, cached_dir_lr = "Level", "Level"

def get_tilt_angles():
    global last_mpu_read, cached_pitch, cached_roll, cached_dir_fb, cached_dir_lr
    now = time.time()
    if now - last_mpu_read < MPU_INTERVAL or mpu_sensor is None:
        return cached_pitch, cached_roll, cached_dir_fb, cached_dir_lr

    last_mpu_read = now
    try:
        accel_data = mpu_sensor.get_accel_data()
        x, y, z = accel_data['x'], accel_data['y'], accel_data['z']

        cached_pitch = math.atan2(-x, math.sqrt(y * y + z * z)) * 180.0 / math.pi
        cached_roll = math.atan2(y, z) * 180.0 / math.pi

        cached_dir_fb = "Forward" if cached_pitch > TILT_THRESHOLD else ("Backward" if cached_pitch < -TILT_THRESHOLD else "Level")
        cached_dir_lr = "Right" if cached_roll > TILT_THRESHOLD else ("Left" if cached_roll < -TILT_THRESHOLD else "Level")
    except Exception:
        pass

    return cached_pitch, cached_roll, cached_dir_fb, cached_dir_lr

# GPS Global State & Non-blocking Reader
latest_lat = "0.0000"
latest_lon = "0.0000"
latest_sats = "0"
gps_has_fix = False

def update_gps():
    global latest_lat, latest_lon, latest_sats, gps_has_fix
    if gps_serial is None:
        return

    try:
        while gps_serial.in_waiting > 0:
            raw_line = gps_serial.readline().decode('ascii', errors='replace').strip()
            if raw_line.startswith('$GPGGA') or raw_line.startswith('$GNGGA'):
                msg = pynmea2.parse(raw_line)
                gps_qual = int(getattr(msg, 'gps_qual', 0) or 0)
                latest_sats = str(getattr(msg, 'num_sats', '0'))

                if gps_qual > 0 and msg.latitude and msg.longitude:
                    latest_lat = f"{msg.latitude:.4f}{msg.lat_dir}"
                    latest_lon = f"{msg.longitude:.4f}{msg.lon_dir}"
                    gps_has_fix = True
                else:
                    gps_has_fix = False
    except Exception:
        pass

# MAX30102 Heart Rate Sensor Init
def init_max30102():
    try:
        bus.write_byte_data(MAX30102_ADDR, REG_MODE_CONFIG, 0x40)
        time.sleep(0.05)
        bus.write_byte_data(MAX30102_ADDR, REG_FIFO_CONFIG, 0x20)
        bus.write_byte_data(MAX30102_ADDR, REG_MODE_CONFIG, 0x03)
        bus.write_byte_data(MAX30102_ADDR, REG_SPO2_CONFIG, 0x27)
        bus.write_byte_data(MAX30102_ADDR, REG_LED1_PA, 0x3F)
        bus.write_byte_data(MAX30102_ADDR, REG_LED2_PA, 0x3F)
        bus.write_byte_data(MAX30102_ADDR, REG_FIFO_WR_PTR, 0x00)
        bus.write_byte_data(MAX30102_ADDR, REG_OVF_COUNTER, 0x00)
        bus.write_byte_data(MAX30102_ADDR, REG_FIFO_RD_PTR, 0x00)
        print("MAX30102 initialized successfully.")
    except Exception as e:
        print(f"MAX30102 Init Error: {e}")

def read_fifo():
    try:
        d = bus.read_i2c_block_data(MAX30102_ADDR, REG_FIFO_DATA, 6)
        red = (d[0] << 16 | d[1] << 8 | d[2]) & 0x03FFFF
        ir  = (d[3] << 16 | d[4] << 8 | d[5]) & 0x03FFFF
        return red, ir
    except Exception:
        return 0, 0

# =========================================================
# 3. VOSK & CAMERA INITIALIZATION
# =========================================================

if not os.path.exists(MODEL_PATH):
    print("ERROR: Vosk model not found:", MODEL_PATH)
    lcd_display("VOSK ERROR", "MODEL NOT FOUND")
    time.sleep(3)
    lcd.clear()
    exit(1)

print("Loading Vosk model...")
model = Model(MODEL_PATH)
ALLOWED_COMMANDS = '["send", "receive", "come", "capture", "yes", "go", "[unk]"]'
recognizer = KaldiRecognizer(model, SAMPLE_RATE, ALLOWED_COMMANDS)
print("Vosk model initialized with constrained grammar.")

print("Opening Raspberry Pi Camera...")
picam2 = Picamera2()
camera_config = picam2.create_still_configuration(main={"size": (640, 480)})
picam2.configure(camera_config)
picam2.start()
time.sleep(2)
print("Raspberry Pi Camera opened successfully.")

print("Starting Microphone...")
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

# =========================================================
# 4. ACTIONS (PHOTO CAPTURE, LORA TX / RX)
# =========================================================
last_capture = 0
last_lora_tx = 0
last_lora_rx = 0

def reset_audio_stream():
    global recognizer
    recognizer = KaldiRecognizer(model, SAMPLE_RATE, ALLOWED_COMMANDS)

def capture_photo(source="ACTION"):
    global last_capture
    current_time = time.time()
    if current_time - last_capture < 3.0:
        return
    last_capture = current_time

    print(f"\n[PHOTO] Triggered via {source}")
    lcd_display("ACTION TRIGGER", "TAKING PHOTO")
    time.sleep(0.5)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"capture_{timestamp}.jpg"
    filepath = os.path.join(PHOTO_FOLDER, filename)

    try:
        picam2.capture_file(filepath)
        lcd_display("PICTURE TAKEN", "SUCCESS")
        time.sleep(1.0)
    except Exception as e:
        print("Camera Error:", e)
        lcd_display("CAMERA ERROR", "RETRY")
        time.sleep(1.0)

    reset_audio_stream()

def send_lora_packet(display_bpm, display_spo2, pitch=0.0, roll=0.0, source="ACTION"):
    global last_lora_tx
    current_time = time.time()
    if current_time - last_lora_tx < 2.5:
        return
    last_lora_tx = current_time

    bpm_val = int(display_bpm) if display_bpm is not None else 0
    spo2_val = int(display_spo2) if display_spo2 is not None else 0
    temp_val = int(latest_temp) if latest_temp is not None else 0
    hum_val = int(latest_humidity) if latest_humidity is not None else 0
    snd_val = "L" if sound_sensor.is_active else "Q"

    payload = {
        "BPM": bpm_val,
        "SPO2": spo2_val,
        "TEMP": temp_val,
        "HUM": hum_val,
        "SOUND": snd_val,
        "PITCH": round(pitch, 1),
        "ROLL": round(roll, 1),
        "LAT": latest_lat,
        "LON": latest_lon,
        "SATS": latest_sats
    }
    msg = json.dumps(payload) + "\n"

    print(f"\n[LoRa TX via {source}] -> {msg.strip()}")
    lcd_display("LORA TRANSMIT", f"B:{bpm_val} S:{spo2_val}%")

    if lora_serial and lora_serial.is_open:
        lora_serial.reset_output_buffer()
        lora_serial.write(msg.encode('utf-8'))
        lora_serial.flush()
        time.sleep(0.8)
        lcd_display("LORA SENT!", "SUCCESS")
        time.sleep(0.8)
    else:
        lcd_display("LORA ERROR", "PORT CLOSED")
        time.sleep(1.2)

    reset_audio_stream()

def receive_lora_packet(source="ACTION"):
    global last_lora_rx
    current_time = time.time()
    if current_time - last_lora_rx < 2.5:
        return
    last_lora_rx = current_time

    print(f"\n[LoRa RX] Trigger active via {source}...")
    lcd_display("LORA RECEIVING", "LISTENING...")

    if not lora_serial or not lora_serial.is_open:
        lcd_display("LORA ERROR", "PORT CLOSED")
        time.sleep(1.2)
        reset_audio_stream()
        return

    start_wait = time.time()
    received_data = ""
    lora_serial.reset_input_buffer()

    while time.time() - start_wait < 3.0:
        if lora_serial.in_waiting > 0:
            raw_line = lora_serial.readline().decode('utf-8', errors='ignore').strip()
            if raw_line.startswith("{") and raw_line.endswith("}"):
                received_data = raw_line
                break
        time.sleep(0.05)

    if received_data:
        print(f"[LoRa RX Success] -> {received_data}")
        try:
            parsed = json.loads(received_data)
            line1 = f"B:{parsed.get('BPM', 0)} S:{parsed.get('SPO2', 0)}%"
            line2 = f"T:{parsed.get('TEMP', 0)}C H:{parsed.get('HUM', 0)}%"
            lcd_display(line1, line2)
        except Exception:
            lcd_display("DATA RECVD:", received_data[:16])
        time.sleep(2.5)
    else:
        print("[LoRa RX] No packet detected.")
        lcd_display("RX TIMEOUT", "NO PACKET FOUND")
        time.sleep(1.2)

    reset_audio_stream()

# =========================================================
# 5. MAIN EVENT LOOP
# =========================================================
def main():
    init_max30102()

    ir_buffer = collections.deque(maxlen=30)
    red_buffer = collections.deque(maxlen=30)
    bpm_history = collections.deque(maxlen=3)
    spo2_history = collections.deque(maxlen=3)

    display_bpm = None
    display_spo2 = None

    last_lcd_update = time.time()
    last_lcd_page_switch = time.time()
    current_lcd_page = 0  # 0: Vitals/Temp, 1: MPU6050, 2: GPS
    last_peak_time = time.time()
    is_peak = False

    print("\n--- SYSTEM OPERATIONAL ---")
    print("Voice Commands : 'send'/'go' (TX) | 'receive'/'come' (RX) | 'capture'/'yes' (Photo)")
    print("GPIO Buttons   : Pin 20 (TX) | Pin 16 (RX) | Pin 21 (Photo)")

    try:
        while True:
            # Read MPU-6050 angles (cached & throttled)
            pitch, roll, dir_fb, dir_lr = get_tilt_angles()

            # Non-blocking GPS read
            update_gps()

            # --- 1. Hardware Buttons ---
            if photo_switch.is_pressed:
                capture_photo(source="GPIO21 BUTTON")
                last_lcd_update = time.time()

            elif lora_tx_switch.is_pressed:
                send_lora_packet(display_bpm, display_spo2, pitch, roll, source="GPIO20 BUTTON")
                last_lcd_update = time.time()

            elif lora_rx_switch.is_pressed:
                receive_lora_packet(source="GPIO16 BUTTON")
                last_lcd_update = time.time()

            # --- 2. Vosk Audio Processing ---
            audio_data = audio.stdout.read(1600)
            if audio_data:
                if recognizer.AcceptWaveform(audio_data):
                    res = json.loads(recognizer.Result())
                    text = res.get("text", "").strip().lower()

                    if text and text != "[unk]":
                        print(f"VOICE COMMAND RECOGNIZED: '{text}'")
                        tokens = text.split()

                        if "send" in tokens or "go" in tokens:
                            send_lora_packet(display_bpm, display_spo2, pitch, roll, source=f"VOICE '{text}'")
                            last_lcd_update = time.time()
                        elif "receive" in tokens or "come" in tokens:
                            receive_lora_packet(source=f"VOICE '{text}'")
                            last_lcd_update = time.time()
                        elif "capture" in tokens or "yes" in tokens:
                            capture_photo(source=f"VOICE '{text}'")
                            last_lcd_update = time.time()

            # --- 3. Sensor Updates ---
            update_dht11()

            red, ir = read_fifo()
            if ir < 20000:
                ir_buffer.clear()
                red_buffer.clear()
                bpm_history.clear()
                spo2_history.clear()
                display_bpm = None
                display_spo2 = None
            else:
                ir_buffer.append(ir)
                red_buffer.append(red)

                if len(ir_buffer) >= 15:
                    dc_ir = sum(ir_buffer) / len(ir_buffer)
                    dc_red = sum(red_buffer) / len(red_buffer)
                    ac_ir = ir - dc_ir
                    ac_red = red - dc_red

                    if ac_ir > 60 and not is_peak:
                        is_peak = True
                        current_time = time.time()
                        time_delta = current_time - last_peak_time
                        last_peak_time = current_time

                        if 0.40 <= time_delta <= 1.40:
                            raw_bpm = 60.0 / time_delta
                            if 25.0 <= raw_bpm < 52.0:
                                raw_bpm *= 2
                            clamped_bpm = max(MIN_BPM, min(MAX_BPM, raw_bpm))
                            bpm_history.append(clamped_bpm)
                            display_bpm = sum(bpm_history) / len(bpm_history)
                    elif ac_ir < -30:
                        is_peak = False

                    if dc_ir > 0 and dc_red > 0 and abs(ac_ir) > 5:
                        r_ratio = (abs(ac_red) / dc_red) / (abs(ac_ir) / dc_ir)
                        calc_spo2 = 104.0 - (17.0 * r_ratio)
                        if 90.0 <= calc_spo2 <= 99.0:
                            spo2_history.append(calc_spo2)
                            display_spo2 = sum(spo2_history) / len(spo2_history)
                        elif display_spo2 is None:
                            display_spo2 = 98.0

            # --- 4. LCD Periodic 3-Page Cycling ---
            now = time.time()
            if now - last_lcd_page_switch > LCD_CYCLE_INTERVAL:
                current_lcd_page = (current_lcd_page + 1) % 3
                last_lcd_page_switch = now

            if now - last_lcd_update > 0.4:
                if current_lcd_page == 0:
                    # Page 0: Heart Rate, SpO2, Temp, Humidity, Sound
                    if ir < 20000:
                        line1 = "Place Finger..."
                    elif display_bpm is not None and display_spo2 is not None:
                        line1 = f"BPM:{int(display_bpm):<2}  SpO2:{int(display_spo2)}%"
                    else:
                        line1 = "Reading Vitals..."

                    t_str = f"T:{latest_temp:.0f}C" if latest_temp is not None else "T:--C"
                    h_str = f"H:{latest_humidity:.0f}%" if latest_humidity is not None else "H:--%"
                    s_str = "S:L" if sound_sensor.is_active else "S:Q"
                    line2 = f"{t_str:<5} {h_str:<5}  {s_str}"

                elif current_lcd_page == 1:
                    # Page 1: MPU-6050 Motion / Tilt Angles
                    line1 = f"P:{dir_fb:<7}{pitch:>6.1f}"
                    line2 = f"R:{dir_lr:<7}{roll:>6.1f}"

                elif current_lcd_page == 2:
                    # Page 2: NEO-6M GPS Position
                    if gps_has_fix:
                        line1 = f"Lat:{latest_lat}"
                        line2 = f"Lon:{latest_lon}"
                    else:
                        line1 = "GPS: No Fix"
                        line2 = f"Sats View: {latest_sats}"

                lcd_display(line1, line2)
                last_lcd_update = now

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nTerminating system...")

    finally:
        # 1. Print and hold the stopped message permanently on the LCD
        try:
            lcd_display("BIOMETRIC SYSTEM", "STOPPED")
        except Exception:
            pass

        # 2. Terminate background processes
        try:
            audio.terminate()
            audio.wait(timeout=1)
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass

        # 3. Release GPIOs and serial buses
        if dht_device:
            dht_device.exit()
        if lora_serial and lora_serial.is_open:
            lora_serial.close()
        if gps_serial and gps_serial.is_open:
            gps_serial.close()

        sound_sensor.close()
        photo_switch.close()
        lora_tx_switch.close()
        lora_rx_switch.close()
        lora_m0.close()
        lora_m1.close()

        print("BIOMETRIC SYSTEM STOPPED")

if __name__ == "__main__":
    main()
