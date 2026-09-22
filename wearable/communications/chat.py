"""
LoRa Chat Mode -- continuous bidirectional text messaging between two
wearable nodes.

Deliberately different from the general LoRa module (modules/lora_radio.py):
there, 'send' opens a menu to pick sensor telemetry to transmit, and free
text is just one option among several. Here, there's no menu step at all --
anything you say or type that isn't a reserved command is transmitted the
moment it's captured, and every incoming TEXT packet is spoken immediately.
It's meant to feel like a live conversation, not a control surface, so
sensor/telemetry sending is deliberately NOT available in this mode --
back out to the regular LoRa module for that.
"""
import sys
import time
import queue
import threading
from wearable.ui.status import print_audio_status
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal


def send_chat_message(radio, text, source="CHAT"):
    """Send a chat payload through the chat-mode boundary."""
    print(f"\n[Chat TX ({source})]: {text}")
    return radio.send_text(text)


def run_standalone(lcd=None):
    from wearable.ui.stt import SpeechToText
    from wearable.ui.tts import TextToSpeech
    from wearable.sensors.gps import GPSReceiver
    from wearable.system.session_manager import SessionManager

    # deferred-changes.md §2.1: construct a live GPS receiver and thread it
    # into SessionManager (which threads it into LoRaRadio -> EpochClock) so
    # a solid outdoor fix narrows the pseudo-ID lookup window instead of
    # this launcher permanently running the degraded/monotonic fallback.
    # GPSReceiver() already no-ops safely (has_fix stays False) if the
    # hardware/serial port isn't present.
    gps = GPSReceiver()

    # Reuses SessionManager purely for clean LoRa + LCD setup/teardown --
    # chat mode does NOT expose SessionManager's activate/switch commands,
    # to keep this a focused channel rather than the general control surface.
    sm = SessionManager(primary="lora", lcd=lcd, gps=gps)
    # Also activate "gps" as a background module so SessionManager actually
    # polls .update() on it -- otherwise has_fix would never advance past
    # its initial False, even with the receiver constructed above.
    sm.activate("gps", quiet=True)
    lcd = sm.lcd
    radio = sm.get("lora")

    stt = SpeechToText()
    tts = TextToSpeech()

    if not radio or not radio.ser:
        print("[Chat] LoRa hardware not available - check init warning above.")
        lcd.log("CHAT FAILED", "CHECK LORA", duration=3.0)
        tts.speak("Chat mode unavailable. LoRa initialization failed.")
        sm.shutdown()
        return

    # --- Echo guard -----------------------------------------------------
    # While TTS is actively speaking (reading out an incoming message, or
    # confirming an outgoing one), the mic can pick up this device's own
    # speaker output. Without a guard, that gets transcribed and could be
    # re-transmitted as a duplicate message, or misread as a new command.
    # `speaking` gates STT consumption: while set, any transcript that
    # accumulates is drained and discarded rather than acted on.
    speaking = threading.Event()

    def _speak(text):
        speaking.set()
        try:
            tts.speak(text, block=True)
        finally:
            time.sleep(0.3)  # let room echo/audio tail settle before resuming
            speaking.clear()

    # --- RX ---------------------------------------------------------------

    def _on_packet(payload):
        p_type = payload.get("type")
        if p_type == "TEXT":
            msg = str(payload.get("text", ""))
            print(f"\n[Chat RX]: {msg}")
            lcd.log("THEM:", msg[:16], duration=3.0)
            _speak(msg)
        else:
            # A peer running the regular LoRa module might still send
            # sensor/image packets even while this side is in chat mode --
            # note it, don't try to "speak" a photo.
            print(f"\n[Chat RX]: non-text packet ignored (Type={p_type})")

    # --- TX ---------------------------------------------------------------

    def _send(text, source):
        sent = send_chat_message(radio, text, source)
        if sent:
            lcd.log("YOU:", text[:16], duration=2.5)
        else:
            print("[Chat TX Failed]: Message send error.")
            lcd.log("SEND FAILED", text[:16], duration=2.5)
            _speak("Message failed to send.")
        return sent

    # --- Command parsing ---------------------------------------------------
    # Same prefix/whole-utterance rules as the rest of the app, scoped to
    # just 'exit' -- chat mode intentionally doesn't expose module
    # activation or switching.

    def _parse_command(text, source):
        body = text.strip()
        if source == "TYPED":
            if not body.startswith("/"):
                return None
            body = body[1:].strip().lower()
        else:
            body = body.lower()
            if body.startswith("message "):
                return None  # spoken escape, handled by the caller first
        if body in ("mute tts", "tts off"):
            return "MUTE_TTS"
        if body in ("unmute tts", "tts on"):
            return "UNMUTE_TTS"
        if body in ("mute stt", "stt off"):
            return "MUTE_STT"
        if body in ("unmute stt", "stt on"):
            return "UNMUTE_STT"
        if body in ("exit", "quit"):
            return "EXIT"
        if body == "status":
            return "STATUS"
        return None

    def _resolve_voice_escape(text, source):
        """Spoken 'message <content>' forces literal send, bypassing
        command matching -- lets you say 'exit' as an actual message."""
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

    print("[Chat] Live LoRa chat. Type or speak freely -- it sends immediately.")
    print("Type '/exit' or say 'exit' (whole phrase) to leave. To literally")
    print("send the word 'exit' as a message, say 'message exit'.\n")
    lcd.log("CHAT MODE", "LIVE", duration=2.0)
    _speak("Chat mode active.")

    try:
        while True:
            source, text = None, None

            if stt.model:
                if speaking.is_set():
                    # Drain anything captured while we were talking, but
                    # don't act on it -- this is the echo guard in effect.
                    stt.get_transcript(block=False)
                else:
                    spoken = stt.get_transcript(block=False)
                    if spoken:
                        source, text = "VOICE", spoken

            if not source and not input_queue.empty():
                source, text = "TYPED", input_queue.get_nowait()

            if text is not None:
                escaped = _resolve_voice_escape(text, source)
                if escaped is not None:
                    _send(escaped, source)
                else:
                    cmd = _parse_command(text, source)
                    if cmd == "EXIT":
                        break
                    elif cmd == "STATUS":
                        print_audio_status(tts, stt, lcd)
                    elif cmd == "MUTE_TTS":
                        tts.mute()
                    elif cmd == "UNMUTE_TTS":
                        tts.unmute()
                    elif cmd == "MUTE_STT":
                        stt.mute()
                    elif cmd == "UNMUTE_STT":
                        stt.unmute()
                    else:
                        cmd = _parse_command(text, source)
                        if cmd == "EXIT":
                            break
                        elif cmd == "STATUS":
                            print_audio_status(tts, stt, lcd)
                        elif cmd == "MUTE_TTS":
                            tts.mute()
                        elif cmd == "UNMUTE_TTS":
                            tts.unmute()
                        elif cmd == "MUTE_STT":
                            stt.mute()
                        elif cmd == "UNMUTE_STT":
                            stt.unmute()
                        else:
                            _send(text, source)

                        time.sleep(0.05)

                    time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nExiting chat mode...")
    finally:
        stt.stop()
        sm.shutdown()


if __name__ == "__main__":
    run_standalone()