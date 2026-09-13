"""
Waveshare LoRa HAT (SX1262/SX1268) UART radio driver.
"""
import sys
import time
import queue
import threading
import serial
from gpiozero import OutputDevice
from config import LORA_PORT, LORA_BAUD, LORA_M0_PIN, LORA_M1_PIN
from modules.lora_protocol import LoRaProtocol, LoRaAssembler

class LoRaRadio:
    def __init__(self, port=LORA_PORT, baudrate=LORA_BAUD):
        self.ser = self.m0 = self.m1 = self.listener_thread = None
        self.assembler = LoRaAssembler()
        self.rx_queue = queue.Queue()
        self.stop_event = threading.Event()
        
        try:
            self.m0 = OutputDevice(LORA_M0_PIN, active_high=True, initial_value=False)
            self.m1 = OutputDevice(LORA_M1_PIN, active_high=True, initial_value=False)
            self.ser = serial.Serial(port, baudrate=baudrate, timeout=0.1)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception as e:
            print(f"[LoRa Init Warning]: {e}")

    def start_listener(self, on_packet_received=None):
        if not self.ser or self.listener_thread: return
        self.stop_event.clear()
        self.listener_thread = threading.Thread(target=self._rx_worker, args=(on_packet_received,), daemon=True)
        self.listener_thread.start()

    def _rx_worker(self, callback):
        while not self.stop_event.is_set():
            if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                try:
                    for raw_line in self.ser:
                        payload = self.assembler.process_packet(raw_line.decode("utf-8", errors="ignore"))
                        if payload:
                            self.rx_queue.put(payload)
                            if callback: callback(payload)
                except Exception: pass
            time.sleep(0.02)

    def send_packets(self, packets, delay_between=0.08):
        if not self.ser or not self.ser.is_open: return False
        try:
            for p in packets:
                self.ser.write(p.encode("utf-8"))
                self.ser.flush()
                if len(packets) > 1: time.sleep(delay_between)
            return True
        except Exception as e:
            print(f"[LoRa TX Error]: {e}")
            return False

    def send_vitals(self, vitals_dict):
        return self.send_packets(LoRaProtocol.encode_vitals(vitals_dict))

    def send_text(self, text):
        return self.send_packets(LoRaProtocol.encode_text(text))

    def send_image_thumbnail(self, jpeg_bytes):
        return self.send_packets(LoRaProtocol.encode_binary(jpeg_bytes, "IMG"))

    def send_audio_clip(self, audio_bytes):
        return self.send_packets(LoRaProtocol.encode_binary(audio_bytes, "AUD"))

    def get_received(self):
        try: return self.rx_queue.get_nowait()
        except queue.Empty: return None

    def close(self):
        self.stop_event.set()
        if self.ser and self.ser.is_open: self.ser.close()
        if self.m0: self.m0.close()
        if self.m1: self.m1.close()


def run_standalone(lcd=None):
    """Standalone LoRa runner: Listens for incoming packets, Voice triggers, and Typed messages with TTS audio."""
    from modules.stt import SpeechToText
    from modules.tts import TextToSpeech

    radio = LoRaRadio()
    stt = SpeechToText()
    tts = TextToSpeech()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    if not radio.ser:
        print("[LoRa] Hardware not available - check init warning above.")
        lcd.log("LORA FAILED", "CHECK PORT", duration=3.0)
        tts.speak("LoRa initialization failed. Check port configuration.")
        if own_lcd:
            lcd.close()
        return

    def _on_packet(payload):
        p_type = payload.get("type")
        print(f"\n[LoRa RX Packet Received]: Type={p_type} | Data={payload}")
        if p_type == "VITALS":
            d = payload.get("data", {})
            bpm = d.get("BPM", 0)
            spo2 = d.get("SPO2", 0)
            lcd.log(f"RX B:{bpm}", f"S:{spo2}%", duration=3.0)
            tts.speak(f"Received vitals: heart rate {bpm}, oxygen {spo2} percent")
        elif p_type == "TEXT":
            msg = str(payload.get("text", ""))
            lcd.log("RX MSG", msg[:16], duration=3.0)
            tts.speak(f"Incoming message: {msg}")
        elif p_type == "IMAGE":
            lcd.log("RX IMAGE", "SAVED TO DISK", duration=3.0)
            tts.speak("Incoming image received and saved to disk.")
        elif p_type == "AUDIO":
            lcd.log("RX AUDIO", "SAVED TO DISK", duration=3.0)
            tts.speak("Incoming audio note received.")
        else:
            lcd.log("LORA RX", str(p_type), duration=2.0)
            tts.speak(f"Received transmission of type {p_type}")

    radio.start_listener(on_packet_received=_on_packet)
    if stt.model:
        stt.start()

    input_queue = queue.Queue()
    def keyboard_listener():
        while True:
            try:
                line = sys.stdin.readline()
                if not line: break
                input_queue.put(line.strip())
            except Exception:
                break

    threading.Thread(target=keyboard_listener, daemon=True).start()

    print(f"[LoRa] Active on {LORA_PORT}.")
    print("Triggers (Voice or Terminal): 'send', 'receive', or speak/type any phrase to transmit.")
    print("Press Ctrl+C to exit.\n")
    lcd.log("LORA READY", "VOICE / TYPE", duration=2.0)
    tts.speak("LoRa radio ready.")

    try:
        while True:
            source, text = None, None

            if stt.model:
                cmd = stt.get_command()
                if cmd:
                    source, text = "VOICE", cmd

            if not source and not input_queue.empty():
                source, text = "TYPED", input_queue.get_nowait()

            if text is not None:
                lower_text = text.lower()
                tokens = set(lower_text.split())

                if {"send", "transmit"}.intersection(tokens):
                    print(f"\n[{source} COMMAND]: 'send' -> Transmitting ping packet")
                    lcd.log("LORA TX", "SENDING PING...", duration=1.5)
                    sent = radio.send_text("PING")
                    print("[LoRa Output]: Broadcast success." if sent else "[LoRa Output]: Transmission failed.")
                    lcd.log("LORA TX", "SENT" if sent else "FAILED", duration=2.5)
                    tts.speak("Ping message transmitted." if sent else "Transmission failed.")

                elif {"receive", "listen"}.intersection(tokens):
                    print(f"\n[{source} COMMAND]: 'receive' -> Checking buffer")
                    payload = radio.get_received()
                    if payload:
                        print(f"[LoRa Output]: Unread Packet -> {payload}")
                        lcd.log("LORA RX", str(payload.get("type")), duration=2.5)
                        tts.speak(f"Unread {payload.get('type')} packet retrieved.")
                    else:
                        print("[LoRa Output]: Queue empty. Listening over the air...")
                        lcd.log("LORA RX", "NO UNREAD MSGS", duration=2.5)
                        tts.speak("No unread packets. Listening for incoming signals.")

                else:
                    print(f"\n[{source} COMMAND]: Transmitting text -> '{text}'")
                    lcd.log("LORA TX", text[:16], duration=1.5)
                    sent = radio.send_text(text)
                    print("[LoRa Output]: Broadcast success." if sent else "[LoRa Output]: Transmission failed.")
                    lcd.log("LORA TX STATUS", "SENT" if sent else "FAILED", duration=2.5)
                    tts.speak(f"Message transmitted: {text}" if sent else "Transmission failed.")

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopping standalone LoRa...")
    finally:
        stt.stop()
        radio.close()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()