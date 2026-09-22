"""Full hardware coordinator for the wearable edge node."""

import sys
import time
import queue
import threading
from gpiozero import Button
from wearable.ui.status import print_audio_status
from wearable.ui.terminal import display_on_terminal
from wearable.system.actions import parse_action
from wearable.system.session_manager import SessionManager
from wearable.communications.lora_radio import make_packet_handler, send_selected_payloads
from config import BUTTON_PHOTO, BUTTON_LORA_TX, BUTTON_LORA_RX, LORA_COOLDOWN

print = display_on_terminal

# Every continuous-kind sensor this coordinator keeps running in the
# background at all times, via SessionManager -- same activation path
# (and same module_registry payload extractors) that the individual
# launchers use, instead of a separate hand-rolled instantiation here.
BACKGROUND_SENSORS = ("dht", "sound", "motion", "vitals", "gps")


def run_full_system():
    """The all-sensors-at-once coordinator loop, now built on SessionManager.

    Every sensor is activated as a SessionManager background module (so it's
    polled the same way, and its payload is extracted the same way, as when
    launched individually), and outgoing sends transmit one packet per active
    source via send_selected_payloads() instead of bundling everything into
    a single hand-built TEL packet.
    """
    from wearable.ui.display import Display
    from wearable.sensors.gps import GPSReceiver
    from wearable.ui.stt import SpeechToText
    from wearable.ui.tts import TextToSpeech

    print("==================================================")
    print("    Starting Wearable Edge Monitoring System      ")
    print("==================================================")

    lcd = Display()

    # Constructed directly, rather than through SessionManager.activate(),
    # so this exact instance can be threaded into EpochClock via
    # SessionManager(gps=...) *and* polled as the "gps" background module --
    # SessionManager reuses it instead of opening a second serial connection
    # to the same port.
    gps = GPSReceiver()

    sm = SessionManager(primary="lora", lcd=lcd, gps=gps)
    lora = sm.get("lora")
    lora.debug = False

    for name in BACKGROUND_SENSORS:
        sm.activate(name, quiet=True)
    sm.activate("camera", quiet=True)
    sm.activate("bluetooth", quiet=True)

    dht = sm.get("dht")
    sound = sm.get("sound")
    motion = sm.get("motion")
    vitals = sm.get("vitals")
    camera = sm.get("camera")
    bt = sm.get("bluetooth")

    stt = SpeechToText()
    tts = TextToSpeech()

    btn_photo = Button(BUTTON_PHOTO, pull_up=True, bounce_time=0.3)
    btn_tx = Button(BUTTON_LORA_TX, pull_up=True, bounce_time=0.3)
    btn_rx = Button(BUTTON_LORA_RX, pull_up=True, bounce_time=0.3)

    input_queue = queue.Queue()

    def keyboard_listener():
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                text = line.strip()
                if text:
                    input_queue.put(text)
            except Exception:
                break

    threading.Thread(target=keyboard_listener, daemon=True).start()

    _on_packet = make_packet_handler(lcd, tts)
    lora.start_listener(on_packet_received=_on_packet)
    stt.start()

    tts.speak("Edge monitoring station online.")
    lcd.show_banner("SYSTEM READY", "ALL SENSORS ON", duration=2.0)

    last_lora_tx_time = 0
    last_gateway_sync = 0

    def trigger_photo(source="MANUAL"):
        # CameraManager.capture_photo() already enforces its own cooldown --
        # no need to duplicate that bookkeeping here.
        lcd.show_banner("TAKING PHOTO", "PLEASE WAIT...", duration=2.0)
        path = camera.capture_photo(source=source)
        if path:
            lcd.show_banner("PICTURE TAKEN", "SUCCESS", duration=2.0)
            tts.speak("Photo captured.")
        else:
            lcd.show_banner("CAMERA ERROR", "RETRY", duration=2.0)

    def trigger_lora_send(source="MANUAL"):
        nonlocal last_lora_tx_time
        now = time.time()
        if now - last_lora_tx_time < LORA_COOLDOWN:
            return
        last_lora_tx_time = now

        sources = sm.payload_sources(include_action=False)  # never auto-send camera/IMG
        if not sources:
            print("[LoRa Send] No active data sources ready to send.")
            lcd.show_banner("LORA TX", "NOTHING READY", duration=2.0)
            return

        print(f"\n[LoRa TX Attempt]: Source={source} | Sending={sources}")
        lcd.show_banner("LORA TX", f"{len(sources)} READING(S)", duration=2.0)
        send_selected_payloads(sm, lora, sources, lcd=lcd, tts=tts)

    def check_startup_health():
        checks = [
            (dht is None or not dht.dht_device,         "DHT11 FAILED",    "CHECK WIRING"),
            (motion is None or not motion.is_connected, "MPU6050 FAILED",  "CHECK WIRING"),
            (vitals is None or not vitals.is_connected, "MAX30102 FAILED", "CHECK WIRING"),
            (not gps.ser,                                "GPS FAILED",      "CHECK PORT"),
            (camera is None or not camera.picam2,        "CAMERA FAILED",   "CHECK RIBBON"),
            (not lora.ser,                                "LORA FAILED",     "CHECK PORT"),
            (stt.model is None,                           "STT FAILED",      "NO MODEL"),
            (tts.engine == tts.DUMMY_ENGINE,              "TTS FAILED",      "NO ENGINE"),
        ]
        failures = [(l1, l2) for cond, l1, l2 in checks if cond]

        if not failures:
            print("[Startup Health] All subsystem checks passed.")
            lcd.log("ALL SYSTEMS OK", "", duration=2.0)
            return

        print("[Startup Health] Failures detected:")
        for l1, l2 in failures:
            print(f"  - {l1}: {l2}")
            lcd.log(l1, l2, duration=2.0)
            time.sleep(2.2)

    check_startup_health()

    bt.autoconnect_async(on_result=lambda ok, name: tts.speak(
        f"Connected to {name}." if ok else "No headset found, using onboard audio."
    ))

    lcd.log("SYSTEM OPERATIONAL", "READY", duration=2.0)
    print("\n--- SYSTEM OPERATIONAL ---")
    print("Triggers (Speak or Type): 'click'/'capture'/'photo' | 'send' | 'receive'")
    print("Hardware Pins          : Photo (Pin 21) | TX (Pin 20) | RX (Pin 16)\n")

    def _sensor_status():
        return {
            "DHT11":    dht is not None and dht.dht_device is not None,
            "MPU6050":  motion is not None and motion.is_connected,
            "MAX30102": vitals is not None and vitals.is_connected,
            "GPS":      gps.ser is not None,
            "Camera":   camera is not None and camera.picam2 is not None,
            "LoRa":     lora.ser is not None,
        }

    def _handle_command(text, source):
        """Session-level commands (exit/status/menu/switch/activate-by-name)
        route through SessionManager first, same as the LoRa Radio and Chat
        launchers -- then anything left over falls back to this coordinator's
        own capture/send/receive/mute triggers."""
        result = sm.route_command(text, source=source)
        if result == "EXIT":
            return "EXIT"
        if result == "STATUS":
            print_audio_status(tts, stt, lcd)
            bt_state = bt.name if bt.is_connected() else "none"
            failed = [name for name, ok in _sensor_status().items() if not ok]
            print(f"[Status] BT = {bt_state} | Sensors down: {', '.join(failed) if failed else 'none'}")
            lcd.log("BT:" + bt_state[:12].upper(), "DOWN:" + (",".join(failed)[:11] if failed else "NONE"), duration=2.0)
            return None
        if result is not None:
            return None  # other session command (activate/switch/menu) handled
        
        action_text = text.strip()[1:].strip() if source == "TYPED" and text.strip().startswith("/") else text
        action = parse_action(action_text)
        print(f"\n[{source} TRIGGER]: '{text}' -> Action: {action}")

        if action == "MUTE_TTS":
            tts.mute()
            lcd.show_banner("TTS", "MUTED", duration=1.5)
        elif action == "UNMUTE_TTS":
            tts.unmute()
            lcd.show_banner("TTS", "UNMUTED", duration=1.5)
        elif action == "MUTE_STT":
            stt.mute()
            lcd.show_banner("STT", "MUTED", duration=1.5)
        elif action == "UNMUTE_STT":
            stt.unmute()
            lcd.show_banner("STT", "UNMUTED", duration=1.5)
        elif action == "CAPTURE":
            trigger_photo(source=f"{source} '{text}'")
        elif action == "SEND":
            trigger_lora_send(source=f"{source} '{text}'")
        elif action == "RECEIVE":
            lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)
            tts.speak("Listening for incoming messages.")
        elif action == "STATUS":
            print_audio_status(tts, stt, lcd)
        else:
            lcd.show_banner(f"{source} TXT", text[:16], duration=3.0)
            from wearable.communications.chat import send_chat_message
            send_chat_message(lora, text, f"FULL SYSTEM {source}")
        return None

    try:
        while True:
            # dht/sound/motion/vitals/gps are polled by SessionManager's own
            # background threads now -- no manual .update() calls needed here.
            bt.is_connected()  # BT has no SessionManager thread; poll it directly

            if time.time() - last_gateway_sync >= 5.0:
                last_gateway_sync = time.time()
                try:
                    synced = lora.sync_gateway()
                    if synced:
                        print(f"[Gateway] Synchronized {synced} queued records.")
                except Exception as exc:
                    print(f"[Gateway] Sync unavailable: {exc}")

            raw_voice_cmd = stt.get_command()
            try:
                raw_typed_cmd = input_queue.get_nowait()
            except queue.Empty:
                raw_typed_cmd = None

            if raw_voice_cmd and _handle_command(raw_voice_cmd, "VOICE") == "EXIT":
                break
            if raw_typed_cmd and _handle_command(raw_typed_cmd, "TYPED") == "EXIT":
                break

            if btn_photo.is_pressed:
                trigger_photo(source="BUTTON Pin 21")
            elif btn_tx.is_pressed:
                trigger_lora_send(source="BUTTON Pin 20")
            elif btn_rx.is_pressed:
                lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)

            lcd.update_cyclic(vitals=vitals, dht=dht, motion=motion, gps=gps, sound=sound)
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopping system...")

    finally:
        print("Cleaning up resources...")
        stt.stop()
        sm.shutdown()  # closes dht/sound/motion/vitals/gps/camera/bluetooth/lora
        btn_photo.close()
        btn_tx.close()
        btn_rx.close()
        lcd.show_banner("SYSTEM STOPPED", "GOODBYE", duration=1.5)
        time.sleep(1.0)
        lcd.close()
        print("Hardware shutdown cleanly complete.")