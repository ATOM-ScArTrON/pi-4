"""
MPU-6050 6-axis accelerometer and orientation sensor driver using direct I2C smbus.
Calculates Pitch/Roll angles and Forward/Backward/Left/Right directions without external dependencies.
"""

import time
import math
import smbus
from config import I2C_BUS, MPU6050_ADDR, TILT_THRESHOLD, MPU_INTERVAL

# MPU-6050 Registers
MPU_PWR_MGMT_1   = 0x6B
MPU_ACCEL_XOUT_H = 0x3B
MPU_ACCEL_YOUT_H = 0x3D
MPU_ACCEL_ZOUT_H = 0x3F

class MotionSensor:
    def __init__(self, bus_num=I2C_BUS, addr=MPU6050_ADDR, threshold=TILT_THRESHOLD):
        self.bus_num = bus_num
        self.addr = addr
        self.threshold = threshold
        self.bus = None
        self.dir_fb = "Level"
        self.dir_lr = "Level"
        self.pitch = 0.0
        self.roll = 0.0
        self.last_read = 0
        self.is_connected = False
        self._init_sensor()

    def _init_sensor(self):
        try:
            self.bus = smbus.SMBus(self.bus_num)
            # Wake MPU-6050 from sleep mode
            self.bus.write_byte_data(self.addr, MPU_PWR_MGMT_1, 0x00)
            time.sleep(0.05)
            self.is_connected = True
        except Exception as e:
            print(f"[MPU6050 Init Warning]: {e}")
            self.is_connected = False

    def _read_word(self, reg):
        high = self.bus.read_byte_data(self.addr, reg)
        low = self.bus.read_byte_data(self.addr, reg + 1)
        val = (high << 8) | low
        if val > 32767:
            val -= 65536
        return val

    def update(self):
        """Polls accelerometer at throttled rate (10Hz). Updates pitch, roll, dir_fb, dir_lr."""
        if not self.bus or not self.is_connected:
            return self.dir_fb, self.dir_lr

        now = time.time()
        if now - self.last_read < MPU_INTERVAL:
            return self.dir_fb, self.dir_lr
        self.last_read = now

        try:
            # 16384 LSB/g sensitivity for +/- 2g range
            ax = self._read_word(MPU_ACCEL_XOUT_H) / 16384.0
            ay = self._read_word(MPU_ACCEL_YOUT_H) / 16384.0
            az = self._read_word(MPU_ACCEL_ZOUT_H) / 16384.0

            # Pitch (Forward / Backward)
            self.pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az)) * 180.0 / math.pi
            # Roll (Left / Right)
            self.roll = math.atan2(ay, az) * 180.0 / math.pi

            self.dir_fb = "Forward" if self.pitch > self.threshold else ("Backward" if self.pitch < -self.threshold else "Level")
            self.dir_lr = "Right" if self.roll > self.threshold else ("Left" if self.roll < -self.threshold else "Level")

        except OSError:
            # Attempt to re-initialize on transient I2C drop
            self._init_sensor()
        except Exception:
            pass

        return self.dir_fb, self.dir_lr

    def get_telemetry(self):
        return {
            "FB": self.dir_fb,
            "LR": self.dir_lr,
            "PITCH": round(self.pitch, 1),
            "ROLL": round(self.roll, 1)
        }

if __name__ == "__main__":
    print("Testing MotionSensor module...")
    motion = MotionSensor()
    for _ in range(15):
        fb, lr = motion.update()
        print(f"Orientation: FB={fb:<8} LR={lr:<8} Pitch={motion.pitch:5.1f}° Roll={motion.roll:5.1f}°")
        time.sleep(0.2)
    print("MotionSensor test complete.")

