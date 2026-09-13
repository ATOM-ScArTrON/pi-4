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
from modules.status_utils import print_audio_status

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
    """Standalone LoRa runner. Primary module in a SessionManager session --
    other sensors can be pulled into the background on demand ("dht",
    "camera", etc, whole-utterance matched), 'send' opens a multi-select
    menu of active data sources to transmit, and free text/speech is sent
    as a chat message."""
    from modules.stt import SpeechToText
    from modules.tts import TextToSpeech
    from modules.session_manager import SessionManager
    from modules.lora_protocol import LoRaProtocol

    sm = SessionManager(primary="lora", lcd=lcd)
    lcd = sm.lcd
    radio = sm.get("lora")

    stt = SpeechToText()
    tts = TextToSpeech()

    if not radio or not radio.ser:
        print("[LoRa] Hardware not available - check init warning above.")
        lcd.log("LORA FAILED", "CHECK PORT", duration=3.0)
        tts.speak("LoRa initialization failed. Check port configuration.")
        sm.shutdown()
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
        elif p_type == "READING":
            sensor = payload.get("sensor", "?")
            d = payload.get("data", {})
            summary = ", ".join(f"{k}:{v}" for k, v in d.items() if k not in ("T", "TS"))
            lcd.log(f"RX {sensor}", summary[:16], duration=3.0)
            tts.speak(f"Received {sensor} reading: {summary}")
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

    def _send_text_message(text, source):
        """Transmit a chat-style text message, mirroring the outcome to
        terminal/LCD/TTS the same way _on_packet() does for RX."""
        print(f"\n[LoRa TX Attempt]: Source={source} | Text='{text}'")
        sent = radio.send_text(text)
        if sent:
            print(f"[LoRa TX Packet Sent]: Type=TEXT | Data={{'text': '{text}'}}")
            lcd.log("TX MSG", text[:16], duration=2.5)
            tts.speak(f"Message transmitted: {text}")
        else:
            print("[LoRa TX Failed]: Transmission error.")
            lcd.log("TX FAILED", text[:16], duration=2.5)
            tts.speak("Transmission failed.")
        return sent

    def _send_selected(selected):
        """Send one packet per chosen source -- keeps each transmission
        small and unambiguous rather than bundling everything into one
        packet (LoRa airtime is precious; chunked encoders already exist
        for the large IMG/AUD case)."""
        for name in selected:
            ptype, data = sm.build_payload(name)
            if ptype is None:
                print(f"[LoRa Send] '{name}' has no data ready yet -- skipped.")
                lcd.log(f"{name.upper()} SKIPPED", "NO DATA", duration=2.0)
                continue

            if isinstance(data, (bytes, bytearray)):
                packets = LoRaProtocol.encode_binary(data, ptype)
            else:
                packets = LoRaProtocol.encode_reading(ptype, data)

            print(f"\n[LoRa TX Attempt]: Type={ptype} | Source={name} | Data={data if not isinstance(data, (bytes, bytearray)) else f'<{len(data)} bytes>'}")
            sent = radio.send_packets(packets)
            if sent:
                print(f"[LoRa TX Packet Sent]: Type={ptype}")
                preview = str(data)[:16] if not isinstance(data, (bytes, bytearray)) else f"{len(data)}B"
                lcd.log(f"TX {ptype}", preview, duration=2.0)
                tts.speak(f"{name} data transmitted.")
            else:
                print(f"[LoRa TX Failed]: Type={ptype}")
                lcd.log(f"{ptype} TX FAILED", "", duration=2.0)
                tts.speak(f"{name} transmission failed.")
            time.sleep(0.3)

    def _wait_for_line(input_queue, timeout):
        """Blocks (without stealing stdin from the keyboard listener
        thread) until either a typed line or a spoken transcript arrives,
        or the timeout elapses. Returns the raw string, or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                return input_queue.get_nowait()
            except queue.Empty:
                pass
            if stt.model:
                spoken = stt.get_transcript(block=False)
                if spoken:
                    return spoken
            time.sleep(0.1)
        return None

    def _prompt_send_selection(input_queue, timeout=15.0):
        """LCD/terminal multi-select: list active payload sources, accept
        one line of comma/space-separated picks (numbers or names)."""
        sources = sm.payload_sources()
        if not sources:
            print("[LoRa Send] No active data sources to send.")
            lcd.log("SEND", "NOTHING ACTIVE", duration=2.5)
            tts.speak("No active data sources to send. Activate a sensor first.")
            return []

        print("\n[LoRa Send] Select source(s) to transmit (comma separated), or 'cancel':")
        for i, name in enumerate(sources, start=1):
            print(f"  [{i}] {name}")
        lcd.log("SELECT SOURCE", "SEE TERMINAL", duration=timeout)
        tts.speak("Which source should I send? " + ", ".join(sources))

        raw = _wait_for_line(input_queue, timeout)
        if not raw:
            print("[LoRa Send] Selection timed out.")
            lcd.log("SEND", "TIMED OUT", duration=2.0)
            return []

        body = raw.strip().lower().lstrip("/")
        if body in ("cancel", "none"):
            print("[LoRa Send] Cancelled.")
            return []

        picks = [p.strip() for p in body.replace(",", " ").split()]
        selected = []
        for p in picks:
            if p.isdigit() and 1 <= int(p) <= len(sources):
                selected.append(sources[int(p) - 1])
            elif p in sources:
                selected.append(p)

        if not selected:
            print(f"[LoRa Send] Didn't recognize selection: '{raw}'. Cancelled.")
            lcd.log("SEND", "NOT RECOGNIZED", duration=2.0)
        return selected

    def _make_confirm_fn(input_queue, timeout=10.0):
        def _confirm(from_name, to_name):
            print(f"\n[Session] Switch primary: {from_name} -> {to_name}? (y/n)")
            lcd.log("SWITCH TO", f"{to_name.upper()}? Y/N", duration=timeout)
            tts.speak(f"Switch to {to_name}? Say yes or no.")

            raw = _wait_for_line(input_queue, timeout)
            if not raw:
                print("[Session] Confirmation timed out -- switch cancelled.")
                return False
            answer = raw.strip().lower().lstrip("/")
            return answer in ("y", "yes")
        return _confirm

    def _parse_lora_command(text, source):
        """Whole-utterance match for LoRa's own domain commands (send /
        receive) -- same rule as SessionManager.route_command(): typed
        needs the '/' prefix, voice must match the entire utterance."""
        body = text.strip()
        if source == "TYPED":
            if not body.startswith("/"):
                return None
            body = body[1:].strip().lower()
        else:
            body = body.lower()
            if body.startswith("message "):
                return None
        if body in ("mute tts", "tts off"):
            return "MUTE_TTS"
        if body in ("unmute tts", "tts on"):
            return "UNMUTE_TTS"
        if body in ("mute stt", "stt off"):
            return "MUTE_STT"
        if body in ("unmute stt", "stt on"):
            return "UNMUTE_STT"
        if body in ("send", "transmit"):
            return "send"
        if body in ("receive", "listen"):
            return "receive"
        return None

    def _resolve_voice_escape(text, source):
        """The spoken 'message <content>' escape forces literal send,
        bypassing all command matching -- lets you say a reserved word
        as the actual message content when you need to."""
        if source != "VOICE":
            return None
        body = text.strip()
        if body.lower().startswith("message "):
            return body[len("message "):].strip()
        return None

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
    confirm_switch = _make_confirm_fn(input_queue)

    print(f"[LoRa] Active on {LORA_PORT}.")
    print("Commands: '/send' '/receive' (typed, need the / prefix) or say 'send'/'receive' (voice, whole phrase).")
    print("Say/type a module name ('dht', 'camera', ...) to activate it in the background.")
    print("Anything else typed/spoken is sent as a chat message. Press Ctrl+C to exit.\n")
    lcd.log("LORA READY", "VOICE / TYPE", duration=2.0)
    tts.speak("LoRa radio ready.")

    try:
        while True:
            source, text = None, None

            if stt.model:
                spoken = stt.get_transcript(block=False)
                if spoken:
                    source, text = "VOICE", spoken

            if not source and not input_queue.empty():
                source, text = "TYPED", input_queue.get_nowait()

            if text is not None:
                escaped = _resolve_voice_escape(text, source)
                if escaped is not None:
                    _send_text_message(escaped, source)
                else:
                    result = sm.route_command(text, source=source, confirm_fn=confirm_switch)   
                    if result == "EXIT":
                        break
                    elif result == "STATUS":
                        print_audio_status(tts, stt, lcd)
                    elif result is not None:
                        pass  # session command handled (activate/switch/status/menu)
                    else:
                        lora_cmd = _parse_lora_command(text, source)
                        if lora_cmd == "send":
                            selected = _prompt_send_selection(input_queue)
                            if selected:
                                _send_selected(selected)
                        elif lora_cmd == "receive":
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
                            _send_text_message(text, source)

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopping standalone LoRa...")
    finally:
        stt.stop()
        sm.shutdown()


if __name__ == "__main__":
    run_standalone()