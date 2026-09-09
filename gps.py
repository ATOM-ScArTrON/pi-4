import time
import serial
import pynmea2
from RPLCD.i2c import CharLCD

# ----------------------------------------------------
# 1. LCD Configuration (0x27 confirmed from your scan)
# ----------------------------------------------------
lcd = CharLCD(
    i2c_expander='PCF8574',
    address=0x27,
    port=1,
    cols=16,
    rows=2,
    dotsize=8
)

def update_lcd(line1, line2=""):
    """Safely updates both lines of the 16x2 LCD."""
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(line1[:16])
    if line2:
        lcd.cursor_pos = (1, 0)
        lcd.write_string(line2[:16])

# Initial boot display
update_lcd("GPS Receiver", "Initializing...")
time.sleep(2)

# ----------------------------------------------------
# 2. UART Serial Setup for NEO-6M
# ----------------------------------------------------
SERIAL_PORT = "/dev/ttyAMA3"
BAUD_RATE = 9600

try:
    ser = serial.Serial(SERIAL_PORT, baudrate=BAUD_RATE, timeout=1.0)
except Exception as e:
    update_lcd("Serial Error:", "Check UART/Pins")
    print(f"Error opening serial port: {e}")
    exit(1)

update_lcd("Waiting for", "GPS Lock...")

# ----------------------------------------------------
# 3. Main Loop: Read & Parse NMEA Sentences
# ----------------------------------------------------
try:
    while True:
        try:
            line = ser.readline().decode('ascii', errors='replace').strip()

            # We parse GPGGA / GNGGA for fix quality and coordinates
            if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                msg = pynmea2.parse(line)
                
                # GPS Quality: 0 = No Fix, 1 = GPS Fix, 2 = DGPS Fix
                gps_qual = int(getattr(msg, 'gps_qual', 0) or 0)
                num_sats = getattr(msg, 'num_sats', '0')

                if gps_qual > 0 and msg.latitude and msg.longitude:
                    # Valid Fix Acquired
                    lat_str = f"Lat:{msg.latitude:.4f}{msg.lat_dir}"
                    lon_str = f"Lon:{msg.longitude:.4f}{msg.lon_dir}"
                    
                    update_lcd(lat_str, lon_str)
                    print(f"Fix OK | Sats: {num_sats} | {lat_str} | {lon_str}")
                else:
                    # Searching for satellites
                    update_lcd("Searching Fix...", f"Sats in view:{num_sats}")

        except pynmea2.ParseError:
            continue
        except UnicodeDecodeError:
            continue

        time.sleep(0.5)

except KeyboardInterrupt:
    lcd.clear()
    ser.close()
    print("\nScript stopped by user.")
