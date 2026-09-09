import collections
import json
import os
import subprocess
import time
import serial

import adafruit_dht
import board
from gpiozero import Button, DigitalInputDevice, OutputDevice
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
LCD_ADDRESS = 0x27

# Sensor & Switch Pins
DHT_PIN = board.D4       # GPIO4  (Physical Pin 7)
SOUND_PIN = 17           # GPIO17 (Physical Pin 11)
PHOTO_SWITCH_PIN = 21    # GPIO21 (Physical Pin 40) - Photo capture
LORA_TX_PIN = 20         # GPIO20 (Physical Pin 38) - LoRa Send Trigger

# LoRa HAT Pins (Waveshare standard pinout)
LORA_M0_PIN = 22         # GPIO22
LORA_M1_PIN = 23         # GPIO23
LORA_UART_PORT = "/dev/serial0"
LORA_BAUD = 9600

DHT_INTERVAL = 2.5
MIN_BPM = 55.0
MAX_BPM = 85.0

# MAX30102 Register Addresses
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

# I2C Bus & LCD
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

# Sensors & Trigger Switches
sound_sensor = DigitalInputDevice(SOUND_PIN, pull_up=False)
photo_switch = Button(PHOTO_SWITCH_PIN, pull_up=True, bounce_time=0.1)
lora_tx_switch = Button(LORA_TX_PIN, pull_up=True, bounce_time=0.1)

# Waveshare LoRa HAT Control (Set M0=0, M1=0 for Transparent Mode)
lora_m0 = OutputDevice(LORA_M0_PIN, active_high=True, initial_value=False)
lora_m1 = OutputDevice(LORA_M1_PIN, active_high=True, initial_value=False)

try:
    lora_serial = serial.Serial(LORA_UART_PORT, baudrate=LORA_BAUD, timeout=1)
    print("Waveshare LoRa HAT Serial (868MHz) connected on", LORA_UART_PORT)
except Exception as e:
    print(f"LoRa Serial Error: {e}")
    lora_serial = None

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

# MAX30102 Initialization
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
recognizer = KaldiRecognizer(model, SAMPLE_RATE)
print("Vosk model loaded successfully.")

print("Opening Raspberry Pi Camera...")
picam2 = Picamera2()
camera_config = picam2.create_still_configuration(main={"size": (640, 480)})
picam2.configure(camera_config)
picam2.start()
time.sleep(2)
print("Raspberry Pi Camera opened successfully.")

# Start Microphone
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
# 4. ACTIONS (PHOTO CAPTURE & LORA TRANSMISSION)
# =========================================================
last_capture = 0
last_lora_tx = 0

def capture_photo(source="VOICE"):
    global last_capture, recognizer
    current_time = time.time()
    if current_time - last_capture < 3:
        return

    last_capture = current_time
    print(f"\n[PHOTO] Triggered via {source}")
    lcd_display("COMMAND RECEIVED", "TAKING PHOTO")
    time.sleep(1)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"capture_{timestamp}.jpg"
    filepath = os.path.join(PHOTO_FOLDER, filename)

    lcd_display("TAKING PICTURE", "PLEASE WAIT...")
    try:
        picam2.capture_file(filepath)
        lcd_display("PICTURE TAKEN", "SUCCESS")
        time.sleep(1.5)
    except Exception as e:
        print("Camera Error:", e)
        lcd_display("CAMERA ERROR", "TRY AGAIN")
        time.sleep(1.5)

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)

def send_lora_packet(display_bpm, display_spo2, source="TRIGGER"):
    global last_lora_tx
    current_time = time.time()
    if current_time - last_lora_tx < 2:
        return
    last_lora_tx = current_time

    bpm_val = int(display_bpm) if display_bpm is not None else 0
    spo2_val = int(display_spo2) if display_spo2 is not None else 0
    temp_val = int(latest_temp) if latest_temp is not None else 0
    hum_val = int(latest_humidity) if latest_humidity is not None else 0
    snd_val = "L" if sound_sensor.is_active else "Q"

    # Formatted packet payload: JSON string followed by newline delimiter
    payload = {
        "BPM": bpm_val,
        "SPO2": spo2_val,
        "TEMP": temp_val,
        "HUM": hum_val,
        "SOUND": snd_val
    }
    msg = json.dumps(payload) + "\n"

    print(f"\n[LoRa TX via {source}] -> {msg.strip()}")
    lcd_display("LORA SENDING...", f"B:{bpm_val} S:{spo2_val}%")

    if lora_serial and lora_serial.is_open:
        lora_serial.write(msg.encode('utf-8'))
        lora_serial.flush()
        time.sleep(1.0)
        lcd_display("LORA SENT!", "SUCCESSFUL")
        time.sleep(1.0)
    else:
        lcd_display("LORA ERROR", "PORT CLOSED")
        time.sleep(1.5)

# =========================================================
# 5. MAIN TRANSMITTER LOOP
# =========================================================
def main():
    global recognizer
    init_max30102()

    ir_buffer = collections.deque(maxlen=30)
    red_buffer = collections.deque(maxlen=30)
    bpm_history = collections.deque(maxlen=3)
    spo2_history = collections.deque(maxlen=3)

    display_bpm = None
    display_spo2 = None

    last_lcd_update = time.time()
    last_peak_time = time.time()
    is_peak = False

    print("\nPi 1 Ready: GPIO20/Voice 'GO' sends LoRa | GPIO21/Voice 'YES' captures Photo")

    try:
        while True:
            # --- 1. Check Hardware Switch Triggers ---
            if photo_switch.is_pressed:
                capture_photo(source="GPIO21 SWITCH")
                last_lcd_update = time.time()

            if lora_tx_switch.is_pressed:
                send_lora_packet(display_bpm, display_spo2, source="GPIO20 SWITCH")
                last_lcd_update = time.time()

            # --- 2. Vosk Audio Processing ("YES", "CAPTURE", "GO") ---
            audio_data = audio.stdout.read(1600)
            if audio_data:
                if recognizer.AcceptWaveform(audio_data):
                    res = json.loads(recognizer.Result())
                    text = res.get("text", "").lower().strip()
                    if text:
                        print("HEARD:", text)

                    if "go" in text:
                        send_lora_packet(display_bpm, display_spo2, source="VOICE 'GO'")
                        last_lcd_update = time.time()
                    elif "capture" in text or "yes" in text:
                        capture_photo(source="VOICE: " + text)
                        last_lcd_update = time.time()
                else:
                    pres = json.loads(recognizer.PartialResult())
                    ptext = pres.get("partial", "").lower().strip()
                    if "go" in ptext:
                        send_lora_packet(display_bpm, display_spo2, source="VOICE 'GO' (PARTIAL)")
                        last_lcd_update = time.time()
                        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
                    elif "capture" in ptext or "yes" in ptext:
                        capture_photo(source="VOICE (PARTIAL): " + ptext)
                        last_lcd_update = time.time()

            # --- 3. Sensor Readings ---
            update_dht11()

            # MAX30102 Read & Processing
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

            # --- 4. LCD Update Loop ---
            if time.time() - last_lcd_update > 0.4:
                if ir < 20000:
                    line1 = "Place Finger..."
                elif display_bpm is not None and display_spo2 is not None:
                    line1 = f"BPM:{int(display_bpm):<2}  SpO2:{int(display_spo2)}%"
                else:
                    line1 = "Reading..."

                t_str = f"T:{latest_temp:.0f}C" if latest_temp is not None else "T:--C"
                h_str = f"H:{latest_humidity:.0f}%" if latest_humidity is not None else "H:--%"
                s_str = "S:L" if sound_sensor.is_active else "S:Q"
                line2 = f"{t_str:<5} {h_str:<5}  {s_str}"

                lcd_display(line1, line2)
                last_lcd_update = time.time()

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopping Pi 1 TX node...")

    finally:
        try:
            audio.terminate()
            audio.wait(timeout=2)
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass
        if dht_device:
            dht_device.exit()
        if lora_serial and lora_serial.is_open:
            lora_serial.close()
        sound_sensor.close()
        photo_switch.close()
        lora_tx_switch.close()
        lora_m0.close()
        lora_m1.close()
        lcd.clear()
        print("Pi 1 Cleanly Stopped.")

if __name__ == "__main__":
    main()