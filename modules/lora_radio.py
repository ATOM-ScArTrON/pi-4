"""
Waveshare LoRa HAT (SX1262/SX1268) UART radio driver.
Handles non-blocking sending and receiving of Vitals, Text, Images, and Audio.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import queue
import threading
import serial
from gpiozero import OutputDevice
from config import LORA_PORT, LORA_BAUD, LORA_M0_PIN, LORA_M1_PIN
from modules.lora_protocol import LoRaProtocol, LoRaAssembler

class LoRaRadio:
    def __init__(self, port=LORA_PORT, baudrate=LORA_BAUD):
        self.port = port
        self.baudrate = baudrate
        self.ser = None

        self.m0 = None
        self.m1 = None

        self.assembler = LoRaAssembler()
        self.rx_queue = queue.Queue()
        self.last_received = None

        self.listener_thread = None
        self.stop_event = threading.Event()
        self.rx_callback = None

        self._init_hardware()

    def _init_hardware(self):
        try:
            # Set transparent mode (M0=0, M1=0)
            self.m0 = OutputDevice(LORA_M0_PIN, active_high=True, initial_value=False)
            self.m1 = OutputDevice(LORA_M1_PIN, active_high=True, initial_value=False)
        except Exception as e:
            print(f"[LoRa GPIO Init Warning]: {e}")

        try:
            self.ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=0.1)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            print(f"[LoRa] Serial active on {self.port}")
        except Exception as e:
            print(f"[LoRa Serial Warning]: {e}")
            self.ser = None

    def start_listener(self, on_packet_received=None):
        """Starts background thread to listen for incoming wireless transmissions."""
        if not self.ser or self.listener_thread:
            return

        self.rx_callback = on_packet_received
        self.stop_event.clear()
        self.listener_thread = threading.Thread(target=self._rx_worker, daemon=True)
        self.listener_thread.start()
        print("[LoRa] Background RX listener active.")

    def _rx_worker(self):
        while not self.stop_event.is_set():
            if self.ser and self.ser.is_open:
                try:
                    if self.ser.in_waiting > 0:
                        raw_line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                        if raw_line:
                            payload = self.assembler.process_packet(raw_line)
                            if payload:
                                self.last_received = payload
                                self.rx_queue.put(payload)
                                if self.rx_callback:
                                    self.rx_callback(payload)
                except Exception:
                    pass
            time.sleep(0.02)

    def send_packets(self, packets, delay_between=0.08):
        """Transmits a list of packet strings out over the LoRa UART."""
        if not self.ser or not self.ser.is_open:
            print("[LoRa Error]: Serial port not open.")
            return False

        try:
            for p in packets:
                self.ser.write(p.encode("utf-8"))
                self.ser.flush()
                if len(packets) > 1:
                    time.sleep(delay_between)
            return True
        except Exception as e:
            print(f"[LoRa TX Error]: {e}")
            return False

    def send_vitals(self, vitals_dict):
        pkts = LoRaProtocol.encode_vitals(vitals_dict)
        print(f"[LoRa TX] Sending vitals: {pkts[0].strip()}")
        return self.send_packets(pkts)

    def send_text(self, text):
        pkts = LoRaProtocol.encode_text(text)
        print(f"[LoRa TX] Sending text message in {len(pkts)} packet(s): '{text}'")
        return self.send_packets(pkts)

    def send_image_thumbnail(self, jpeg_bytes):
        pkts = LoRaProtocol.encode_binary(jpeg_bytes, data_type="IMG")
        print(f"[LoRa TX] Sending image thumbnail in {len(pkts)} chunks ({len(jpeg_bytes)} bytes)...")
        return self.send_packets(pkts)

    def send_audio_clip(self, audio_bytes):
        pkts = LoRaProtocol.encode_binary(audio_bytes, data_type="AUD")
        print(f"[LoRa TX] Sending audio clip in {len(pkts)} chunks ({len(audio_bytes)} bytes)...")
        return self.send_packets(pkts)

    def get_received(self):
        """Pops next received payload from the queue, or None."""
        try:
            return self.rx_queue.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        self.stop_event.set()
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        if self.m0:
            self.m0.close()
        if self.m1:
            self.m1.close()

if __name__ == "__main__":
    print("Testing LoRaRadio driver...")
    radio = LoRaRadio()
    if radio.ser:
        print("Sending test vitals packet...")
        radio.send_vitals({"BPM": 72, "SPO2": 98, "TEMP": 29, "HUM": 57})
        time.sleep(1)
    radio.close()
    print("LoRaRadio test complete.")

