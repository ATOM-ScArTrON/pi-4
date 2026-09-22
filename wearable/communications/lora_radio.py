"""
Waveshare LoRa HAT (SX1262/SX1268) UART radio driver.
"""
import sys
import time
import queue
import threading
import serial
from gpiozero import OutputDevice
from config import (LORA_PORT, LORA_BAUD, LORA_M0_PIN, LORA_M1_PIN, MESH_NONCE_FILE, MISSION_KEYSET_PATH, PEER_ID)
from wearable.communications.lora_protocol import LoRaProtocol, LoRaAssembler
from wearable.crypto.mesh_crypto import NonceManager
from wearable.system.epoch_clock import EpochClock
from wearable.ui.status import print_audio_status
from wearable.ui.terminal import display_on_terminal
from config import (GATEWAY_ENABLED, GATEWAY_QUEUE_PATH, DEVICE_ID, PROVISION_SERVER_URL, TLS_CA_FILE, TLS_CERT_FILE, TLS_KEY_FILE)
from config import LORA_DEBUG

print = display_on_terminal

class LoRaRadio:
    def __init__(self, port=LORA_PORT, baudrate=LORA_BAUD, peer_id=PEER_ID, gps_receiver=None):
        self.ser = self.m0 = self.m1 = self.listener_thread = None
        self.peer_id = peer_id
        self.keyset, self.broadcast_key, self.key_epoch, epoch_start_time = self._load_keyset()
        self.key = self.keyset.get(peer_id) or next(iter(self.keyset.values()), None)
        if self.key is None:
            raise RuntimeError(
                "No provisioned mission key is available. Run provisioning before starting LoRa."
            )
        self.protocol = LoRaProtocol(self.key, keyset=self.keyset, peer_id=peer_id,
                                     broadcast_key=self.broadcast_key,
                                     nonce_manager=NonceManager(MESH_NONCE_FILE),
                                     epoch_clock=EpochClock(epoch_start_time, gps=gps_receiver))
        self.assembler = LoRaAssembler(self.protocol)
        self.gateway_queue = None
        if GATEWAY_ENABLED:
            from wearable.communications.gateway_sync import GatewaySyncQueue
            self.gateway_queue = GatewaySyncQueue(GATEWAY_QUEUE_PATH)
        self._rx_buffer = bytearray()
        self.rx_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.gps_receiver = gps_receiver
        
        try:
            self.m0 = OutputDevice(LORA_M0_PIN, active_high=True, initial_value=False)
            self.m1 = OutputDevice(LORA_M1_PIN, active_high=True, initial_value=False)
            self.ser = serial.Serial(port, baudrate=baudrate, timeout=0.1)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception as e:
            print(f"[LoRa Init Warning]: {e}")

    @staticmethod
    def _load_keyset():
        try:
            import json
            with open(MISSION_KEYSET_PATH, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
            keyset = {name: bytes.fromhex(value) for name, value in payload.get("mission_keyset", {}).items()}
            broadcast = bytes.fromhex(payload.get("mission_broadcast_key", ""))
            if keyset and len(broadcast) == 16:
                epoch = payload.get("key_epoch", payload.get("mission_epoch_id", 0))
                if not isinstance(epoch, int) or epoch < 0:
                    raise ValueError("invalid key epoch")
                epoch_start_time = payload.get("epoch_start_time")
                if not isinstance(epoch_start_time, (int, float)) or epoch_start_time <= 0:
                    raise ValueError("invalid epoch_start_time")
                return keyset, broadcast, epoch, epoch_start_time
        except (OSError, KeyError, TypeError, ValueError) as exc:
            print(f"[LoRa Keyset Error] {type(exc).__name__}: {exc}")
            raise RuntimeError(
            f"No valid mission keyset found at {MISSION_KEYSET_PATH}. "
            "Provision this Pi before starting LoRa."
            ) from exc

    def start_listener(self, on_packet_received=None):
        if not self.ser or self.listener_thread: return
        self.stop_event.clear()
        self.listener_thread = threading.Thread(target=self._rx_worker, args=(on_packet_received,), daemon=True)
        self.listener_thread.start()
        print(f"[LoRa RX] Listener thread started on {self.ser.port}.")  # confirms step 1

    def _rx_worker(self, callback):
        while not self.stop_event.is_set():
            if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                try:
                    raw = self.ser.read(self.ser.in_waiting)
                    if LORA_DEBUG:
                        print(f"[LoRa RX DEBUG] {len(raw)} raw byte(s): {raw.hex()}")  # confirms step 2
                    self._rx_buffer.extend(raw)
                    while self._rx_buffer:
                        body_length = self._rx_buffer[0]
                        frame_length = body_length + 1
                        if body_length == 0 or body_length > 120:
                            del self._rx_buffer[0]
                            continue
                        if len(self._rx_buffer) < frame_length:
                            break
                        frame = bytes(self._rx_buffer[:frame_length])
                        del self._rx_buffer[:frame_length]
                        payload = self.assembler.process_frame(frame)
                        if payload:
                            if self.gateway_queue:
                                self.gateway_queue.enqueue(payload)
                            self.rx_queue.put(payload)
                            if callback:
                                callback(payload)
                        elif LORA_DEBUG:
                            print(f"[LoRa RX DEBUG] frame of {frame_length}B decoded to a raw byte count but process_frame() returned None -- rejected at crypto/replay/epoch layer")  # step 3
                except Exception as e:
                    if LORA_DEBUG:
                        print(f"[LoRa RX DEBUG] exception in rx_worker: {e}")
            time.sleep(0.02)
    def send_packets(self, packets, delay_between=0.08):
        if not self.ser or not self.ser.is_open: return False
        try:
            for p in packets:
                if not isinstance(p, (bytes, bytearray)):
                    raise TypeError("LoRa packets must be binary frames")
                self.ser.write(p)
                self.ser.flush()
                if len(packets) > 1: time.sleep(delay_between)
            return True
        except Exception as e:
            print(f"[LoRa TX Error]: {e}")
            return False

    def send_telemetry(self, telemetry_dict):
        return self.send_packets(self.protocol.encode_telemetry(telemetry_dict))

    def send_text(self, text):
        return self.send_packets(self.protocol.encode_text(text))

    def send_image_thumbnail(self, jpeg_bytes):
        return self.send_packets(self.protocol.encode_binary(jpeg_bytes, "IMG"))

    def send_audio_clip(self, audio_bytes):
        return self.send_packets(self.protocol.encode_binary(audio_bytes, "AUD"))

    def get_received(self):
        try: return self.rx_queue.get_nowait()
        except queue.Empty: return None

    def sync_gateway(self):
        if not self.gateway_queue:
            return 0
        return self.gateway_queue.sync_once(PROVISION_SERVER_URL, DEVICE_ID,
                                            TLS_CA_FILE, TLS_CERT_FILE, TLS_KEY_FILE)

    def close(self):
        self.stop_event.set()
        if self.ser and self.ser.is_open: self.ser.close()
        if self.m0: self.m0.close()
        if self.m1: self.m1.close()

def make_packet_handler(lcd, tts):
    """Shared incoming-LoRa-packet display/speech handler, used by every
    run_standalone() launcher that listens on the radio (LoRa Radio, Full
    System) so every packet type gets the same treatment everywhere instead
    of each launcher hand-rolling its own partial copy."""
    def _on_packet(payload):
        p_type = payload.get("type")
        print(f"\n[LoRa RX Packet Received]: Type={p_type} | Data={payload}")
        if p_type == "VIT":
            bpm = payload.get("BPM", 0)
            spo2 = payload.get("SPO2", 0)
            lcd.log(f"RX B:{bpm}", f"S:{spo2}%", duration=3.0)
            tts.speak(f"Received vitals: heart rate {bpm}, oxygen {spo2} percent")
        elif p_type in ("DHT", "SND", "MOT", "GPS"):
            summary = ", ".join(f"{k}:{v}" for k, v in payload.items() if k not in ("type", "timestamp"))
            lcd.log(f"RX {p_type}", summary[:16], duration=3.0)
            tts.speak(f"Received {p_type} reading: {summary}")
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
        elif p_type == "ALERT":
            msg = str(payload.get("data", ""))
            lcd.log("RX ALERT", msg[:16], duration=5.0)
            tts.speak(f"Emergency alert: {msg}")
        else:
            lcd.log("LORA RX", str(p_type), duration=2.0)
            tts.speak(f"Received message of type {p_type}")
    return _on_packet


def send_selected_payloads(sm, radio, selected, lcd=None, tts=None):
    """Send one packet per chosen active data source. Per-source detail
    stays terminal-only (dual-output rule); LCD/TTS only get a single
    combined outcome once the whole batch is done."""
    sent_count = failed_count = 0
    skipped = []

    for name in selected:
        ptype, data = sm.build_payload(name)
        if ptype is None:
            print(f"[LoRa Send] '{name}' has no data ready yet -- skipped.")
            skipped.append(name)
            continue

        if isinstance(data, (bytes, bytearray)):
            packets = radio.protocol.encode_binary(data, ptype)
        else:
            packets = radio.protocol.encode_reading(ptype, data)

        print(f"\n[LoRa TX Attempt]: Type={ptype} | Source={name} | "
              f"Data={data if not isinstance(data, (bytes, bytearray)) else f'<{len(data)} bytes>'}")
        sent = radio.send_packets(packets)
        if sent:
            print(f"[LoRa TX Packet Sent]: Type={ptype}")
            sent_count += 1
        else:
            print(f"[LoRa TX Failed]: Type={ptype}")
            failed_count += 1
        time.sleep(0.3)

    if skipped:
        print(f"[LoRa Send] Skipped (no data ready): {', '.join(skipped)}")

    if sent_count and not failed_count:
        line1, line2, speech = "DATA SENT", f"{sent_count} READING(S)", "Data transmitted."
    elif sent_count and failed_count:
        line1, line2 = "DATA PARTIAL", f"{sent_count} OK / {failed_count} FAIL"
        speech = f"Data sent, but {failed_count} failed."
    elif failed_count:
        line1, line2, speech = "DATA SEND FAILED", f"{failed_count} FAILED", "Data failed to send."
    else:
        line1, line2, speech = "DATA SEND", "NOTHING SENT", None

    if lcd:
        lcd.log(line1, line2, duration=2.0)
    if tts and speech:
        tts.speak(speech)

def run_standalone(lcd=None):
    """Standalone LoRa runner. Primary module in a SessionManager session --
    other sensors can be pulled into the background on demand ("dht",
    "camera", etc, whole-utterance matched), 'send' opens a multi-select
    menu of active data sources to transmit, and free text/speech is sent
    as a chat message."""
    from wearable.ui.stt import SpeechToText
    from wearable.ui.tts import TextToSpeech
    from wearable.system.session_manager import SessionManager

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

    _on_packet = make_packet_handler(lcd, tts)

    def _send_text_message(text, source):
        """Compatibility helper for the explicit chat-message escape."""
        from wearable.communications.chat import send_chat_message
        sent = send_chat_message(radio, text, source)
        if sent:
            print(f"[LoRa TX Packet Sent]: Type=TEXT | Data={{'text': '{text}'}}")
            lcd.log("TX MSG", text[:16], duration=2.5)
            tts.speak(f"Message transmitted: {text}")
        else:
            print("[LoRa TX Failed]: Message send error.")
            lcd.log("TX FAILED", text[:16], duration=2.5)
            tts.speak("Message failed to send.")
        return sent

    def _send_selected(selected):
        send_selected_payloads(sm, radio, selected, lcd=lcd, tts=tts)

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
    print("Use option 12 for chat messages. Press Ctrl+C to exit.\n")
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
                        elif lora_cmd == "MUTE_TTS":
                            tts.mute()
                        elif lora_cmd == "UNMUTE_TTS":
                            tts.unmute()
                        elif lora_cmd == "MUTE_STT":
                            stt.mute()
                        elif lora_cmd == "UNMUTE_STT":
                            stt.unmute()
                        else:
                            print("[LoRa] Unrecognized input. Use '/send', '/receive', or Chat Mode.")

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopping standalone LoRa...")
    finally:
        stt.stop()
        sm.shutdown()


if __name__ == "__main__":
    run_standalone()
