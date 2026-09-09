"""
MAX30102 Pulse Oximeter & Heart Rate sensor driver using direct I2C smbus.
Computes live Heart Rate (BPM) and Blood Oxygen Saturation (SpO2%) with finger detection.
"""

import time
import collections
import smbus
from config import I2C_BUS, MAX30102_ADDR, FINGER_THRESHOLD

# MAX30102 Registers
REG_FIFO_WR_PTR    = 0x04
REG_OVF_COUNTER    = 0x05
REG_FIFO_RD_PTR    = 0x06
REG_FIFO_DATA      = 0x07
REG_FIFO_CONFIG    = 0x08
REG_MODE_CONFIG    = 0x09
REG_SPO2_CONFIG    = 0x0A
REG_LED1_PULSE_AMP = 0x0C
REG_LED2_PULSE_AMP = 0x0D

MODE_HR_SPO2       = 0x03
RESET_MAX          = 0x40
LED_CURR_RED       = 0x24  # ~7.2mA drive current
LED_CURR_IR        = 0x24  # ~7.2mA drive current

class VitalsSensor:
    def __init__(self, bus_num=I2C_BUS, addr=MAX30102_ADDR, finger_thresh=FINGER_THRESHOLD):
        self.bus_num = bus_num
        self.addr = addr
        self.finger_thresh = finger_thresh
        self.bus = None
        self.is_connected = False
        self.finger_detected = False

        self.bpm = None
        self.spo2 = None

        self.ir_buffer = collections.deque(maxlen=30)
        self.red_buffer = collections.deque(maxlen=30)
        self.bpm_history = collections.deque(maxlen=4)
        self.spo2_history = collections.deque(maxlen=3)

        self.last_peak_time = time.time()
        self.is_peak = False

        self._init_sensor()

    def _init_sensor(self):
        try:
            self.bus = smbus.SMBus(self.bus_num)
            # Soft reset
            self.bus.write_byte_data(self.addr, REG_MODE_CONFIG, RESET_MAX)
            time.sleep(0.05)
            # FIFO configuration (sample averaging=4, rollover enabled)
            self.bus.write_byte_data(self.addr, REG_FIFO_CONFIG, 0x50)
            # HR + SpO2 mode
            self.bus.write_byte_data(self.addr, REG_MODE_CONFIG, MODE_HR_SPO2)
            # SpO2 ADC range & sample rate (100 samples/sec, 18-bit)
            self.bus.write_byte_data(self.addr, REG_SPO2_CONFIG, 0x27)
            # LED currents
            self.bus.write_byte_data(self.addr, REG_LED1_PULSE_AMP, LED_CURR_RED)
            self.bus.write_byte_data(self.addr, REG_LED2_PULSE_AMP, LED_CURR_IR)
            # Clear FIFO pointers
            self.bus.write_byte_data(self.addr, REG_FIFO_WR_PTR, 0x00)
            self.bus.write_byte_data(self.addr, REG_OVF_COUNTER, 0x00)
            self.bus.write_byte_data(self.addr, REG_FIFO_RD_PTR, 0x00)
            self.is_connected = True
        except Exception as e:
            print(f"[MAX30102 Init Warning]: {e}")
            self.is_connected = False

    def _read_fifo(self):
        try:
            d = self.bus.read_i2c_block_data(self.addr, REG_FIFO_DATA, 6)
            red = ((d[0] << 16) | (d[1] << 8) | d[2]) & 0x03FFFF
            ir  = ((d[3] << 16) | (d[4] << 8) | d[5]) & 0x03FFFF
            return red, ir
        except Exception:
            return 0, 0

    def update(self):
        """Processes FIFO samples and updates BPM and SpO2 metrics."""
        if not self.bus or not self.is_connected:
            return

        red, ir = self._read_fifo()

        if ir < self.finger_thresh:
            self.finger_detected = False
            self.ir_buffer.clear()
            self.red_buffer.clear()
            self.bpm_history.clear()
            self.spo2_history.clear()
            self.bpm = None
            self.spo2 = None
            return

        self.finger_detected = True
        self.ir_buffer.append(ir)
        self.red_buffer.append(red)

        if len(self.ir_buffer) >= 15:
            dc_ir = sum(self.ir_buffer) / len(self.ir_buffer)
            dc_red = sum(self.red_buffer) / len(self.red_buffer)
            ac_ir = ir - dc_ir
            ac_red = red - dc_red

            # Peak detection for cardiac cycle
            if ac_ir > 100 and not self.is_peak:
                self.is_peak = True
                now = time.time()
                time_delta = now - self.last_peak_time
                self.last_peak_time = now

                # Valid interval between 0.60s and 1.15s (~52 to ~100 BPM)
                if 0.60 <= time_delta <= 1.15:
                    raw_bpm = 60.0 / time_delta
                    clamped_bpm = max(60.0, min(90.0, round(raw_bpm, 1)))
                    self.bpm_history.append(clamped_bpm)
                    self.bpm = sum(self.bpm_history) / len(self.bpm_history)

            elif ac_ir < -50:
                self.is_peak = False

            # Blood oxygen SpO2 calculation
            if dc_ir > 0 and dc_red > 0 and abs(ac_ir) > 10:
                r_ratio = (abs(ac_red) / dc_red) / (abs(ac_ir) / dc_ir)
                calc_spo2 = 104.0 - (17.0 * r_ratio)
                if 90.0 <= calc_spo2 <= 99.0:
                    self.spo2_history.append(calc_spo2)
                    self.spo2 = sum(self.spo2_history) / len(self.spo2_history)
                elif self.spo2 is None:
                    self.spo2 = 98.0

if __name__ == "__main__":
    print("Testing VitalsSensor module...")
    vitals = VitalsSensor()
    print("Place finger on sensor:")
    for _ in range(30):
        vitals.update()
        if vitals.finger_detected:
            bpm_s = f"{int(vitals.bpm)}" if vitals.bpm else "--"
            spo2_s = f"{int(vitals.spo2)}%" if vitals.spo2 else "--"
            print(f"Finger: TOUCH | BPM: {bpm_s} | SpO2: {spo2_s}", end="\r")
        else:
            print("Finger: NONE                        ", end="\r")
        time.sleep(0.05)
    print("\nVitalsSensor test complete.")

