import time
from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE
from RPLCD.i2c import CharLCD

# Give I2C bus time to stabilize after boot
time.sleep(2)

# Change 0x27 to your actual I2C address if different
lcd = CharLCD(i2c_expander='PCF8574', address=0x27, port=1, cols=16, rows=2, dotsize=8)

session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)

lcd.clear()
lcd.write_string("Waiting for GPS")
lcd.cursor_pos = (1, 0)
lcd.write_string("fix...")

print("Waiting for GPS fix...")

last_update = 0

while True:
    try:
        report = session.next()
        if report['class'] == 'TPV':
            if hasattr(report, 'lat') and hasattr(report, 'lon'):
                lat = report.lat
                lon = report.lon

                now = time.time()
                if now - last_update >= 1:
                    lcd.clear()
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string(f"Lat:{lat:.5f}")
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string(f"Lon:{lon:.5f}")
                    print(f"Latitude: {lat}, Longitude: {lon}")
                    last_update = now

    except KeyError:
        pass
    except StopIteration:
        session = None
        print("GPSD has terminated")
        break
    except KeyboardInterrupt:
        lcd.clear()
        lcd.write_string("Stopped")
        breakimport time
from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE
from RPLCD.i2c import CharLCD

# Give I2C bus time to stabilize after boot
time.sleep(2)

# Change 0x27 to your actual I2C address if different
lcd = CharLCD(i2c_expander='PCF8574', address=0x27, port=1, cols=16, rows=2, dotsize=8)

session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)

lcd.clear()
lcd.write_string("Waiting for GPS")
lcd.cursor_pos = (1, 0)
lcd.write_string("fix...")

print("Waiting for GPS fix...")

last_update = 0

while True:
    try:
        report = session.next()
        if report['class'] == 'TPV':
            if hasattr(report, 'lat') and hasattr(report, 'lon'):
                lat = report.lat
                lon = report.lon

                now = time.time()
                if now - last_update >= 1:
                    lcd.clear()
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string(f"Lat:{lat:.5f}")
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string(f"Lon:{lon:.5f}")
                    print(f"Latitude: {lat}, Longitude: {lon}")
                    last_update = now

    except KeyError:
        pass
    except StopIteration:
        session = None
        print("GPSD has terminated")
        break
    except KeyboardInterrupt:
        lcd.clear()
        lcd.write_string("Stopped")
        break
