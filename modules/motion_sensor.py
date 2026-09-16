"""
MPU-6050 6-axis accelerometer driver using smbus2.
"""
import time
import math
from modules.terminal import display_on_terminal

print = display_on_terminal
from smbus2 import SMBus
from config import I2C_BUS, MPU6050_ADDR, TILT_THRESHOLD, MPU_INTERVAL

RAD_TO_DEG = 180.0 / math.pi
ACCEL_SCALE = 1.0 / 16384.0
LCD_REFRESH_INTERVAL = 0.5  # throttle LCD writes independent of terminal print rate

class MotionSensor:
    def __init__(self, bus_num=I2C_BUS, addr=MPU6050_ADDR, threshold=TILT_THRESHOLD):
        self.bus_num, self.addr, self.threshold = bus_num, addr, threshold
        self.bus = None
        self.dir_fb = self.dir_lr = "Level"
        self.pitch = self.roll = self.last_read = 0.0
        self.is_connected = False
        self._init_sensor()

    def _init_sensor(self):
        try:
            self.bus = SMBus(self.bus_num)
            self.bus.write_byte_data(self.addr, 0x6B, 0x00)
            time.sleep(0.05)
            self.is_connected = True
        except Exception as e:
            print(f"[MPU6050 Init Warning]: {e}")
            self.is_connected = False

    def _read_word(self, reg):
        val = (self.bus.read_byte_data(self.addr, reg) << 8) | self.bus.read_byte_data(self.addr, reg + 1)
        return val - 65536 if val > 32767 else val

    def update(self):
        now = time.time()
        if not self.bus or not self.is_connected or now - self.last_read < MPU_INTERVAL:
            return self.dir_fb, self.dir_lr
            
        self.last_read = now
        try:
            ax, ay, az = [self._read_word(reg) * ACCEL_SCALE for reg in (0x3B, 0x3D, 0x3F)]
            self.pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * RAD_TO_DEG
            self.roll = math.atan2(ay, az) * RAD_TO_DEG

            self.dir_fb = "Forward" if self.pitch > self.threshold else ("Backward" if self.pitch < -self.threshold else "Level")
            self.dir_lr = "Right" if self.roll > self.threshold else ("Left" if self.roll < -self.threshold else "Level")
        except OSError:
            self._init_sensor()
        except Exception:
            pass

        return self.dir_fb, self.dir_lr

    def get_telemetry(self):
        return {"FB": self.dir_fb, "LR": self.dir_lr, "PITCH": round(self.pitch, 1), "ROLL": round(self.roll, 1)}

    def close(self):
        if self.bus:
            try: self.bus.close()
            except Exception: pass


def run_standalone(lcd=None):
    """Continuously print live pitch/roll/tilt direction until Ctrl+C. Mirrors state to LCD (throttled)."""
    sensor = MotionSensor()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    print(f"[MPU6050] Connected: {sensor.is_connected}. Tilt the device. Press Ctrl+C to stop.\n")
    last_lcd_update = 0
    try:
        while True:
            sensor.update()
            print(f"Pitch:{sensor.pitch:6.1f}  Roll:{sensor.roll:6.1f}   FB:{sensor.dir_fb:<8} LR:{sensor.dir_lr:<8}", end="\r")

            now = time.time()
            if now - last_lcd_update >= LCD_REFRESH_INTERVAL:
                last_lcd_update = now
                lcd.log(f"FB: {sensor.dir_fb:<12}", f"LR: {sensor.dir_lr:<12}", duration=LCD_REFRESH_INTERVAL)

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sensor.close()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()
