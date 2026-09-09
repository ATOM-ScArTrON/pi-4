import time import serial import pynmea2 from 
RPLCD.i2c import CharLCD
# --------------------------------------------------------- 
# 1. LCD Setup (Address confirmed 0x27) 
# ---------------------------------------------------------
LCD_ADDRESS = 0x27 I2C_PORT = 1 lcd = CharLCD( 
    i2c_expander='PCF8574', 
    address=LCD_ADDRESS, port=I2C_PORT, 
    charmap='A00', cols=16, rows=2, 
    auto_linebreaks=False
) def lcd_display(line1="", line2=""): 
    lcd.clear() lcd.cursor_pos = (0, 0) 
    lcd.write_string(line1[:16].ljust(16)) 
    lcd.cursor_pos = (1, 0) 
    lcd.write_string(line2[:16].ljust(16))
# Boot Banner
lcd_display("GPS TEST MODULE", "STARTING...") 
time.sleep(2)
# --------------------------------------------------------- 
# 2. Serial UART Setup for GPS 
# ---------------------------------------------------------
GPS_PORT = "/dev/ttyAMA3" BAUD_RATE = 9600 
try:
    gps_serial = serial.Serial(GPS_PORT, 
    baudrate=BAUD_RATE, timeout=0.5) 
    print(f"Connected to GPS on {GPS_PORT}")
except Exception as e: print(f"Serial Error: 
    {e}") lcd_display("SERIAL ERROR", "CHECK 
    PORT/PINS") exit(1)
lcd_display("WAITING FOR GPS", "SEARCHING 
SATS..")
# --------------------------------------------------------- 
# 3. Reading and Displaying Data 
# ---------------------------------------------------------
last_lcd_update = time.time() raw_received = 
False try:
    while True: if gps_serial.in_waiting > 0: 
            try:
                line = 
                gps_serial.readline().decode('ascii', 
                errors='replace').strip() if 
                line.startswith('$'):
                    raw_received = True
                # Parse GGA sentence for 
                # coordinates & satellite 
                # count
                if line.startswith('$GPGGA') 
                or line.startswith('$GNGGA'):
                    msg = pynmea2.parse(line) 
                    gps_qual = 
                    int(getattr(msg, 
                    'gps_qual', 0) or 0) sats 
                    = getattr(msg, 'num_sats', 
                    '0') if time.time() - 
                    last_lcd_update > 0.5:
                        if gps_qual > 0 and 
                        msg.latitude and 
                        msg.longitude:
                            # Fix locked
                            lat_str = 
                            f"Lat:{msg.latitude:.4f}{msg.lat_dir}" 
                            lon_str = 
                            f"Lon:{msg.longitude:.4f}{msg.lon_dir}" 
                            lcd_display(lat_str, 
                            lon_str) 
                            print(f"[FIX 
                            ACQUIRED] 
                            {lat_str} | 
                            {lon_str} | Sats: 
                            {sats}")
                        else:
                            # Searching for 
                            # satellites
                            lcd_display("NO 
                            FIX YET...", 
                            f"SATS IN 
                            VIEW:{sats}") 
                            print(f"[NO FIX] 
                            Satellites in 
                            view: {sats}")
                        last_lcd_update = 
                        time.time()
            except pynmea2.ParseError: 
                continue
            except UnicodeDecodeError: 
                continue
        # If after 5 seconds not a single '$' 
        # character has been read
        if not raw_received and (time.time() - 
        last_lcd_update > 5.0):
            lcd_display("NO GPS DATA", "CHECK 
            PIN 29 TX") print("Warning: No 
            NMEA data received. Check TX 
            connection to Pin 29.") 
            last_lcd_update = time.time()
        time.sleep(0.01) except 
KeyboardInterrupt:
    lcd_display("GPS MODULE", "STOPPED") 
    gps_serial.close() print("\nTest 
    terminated.")
