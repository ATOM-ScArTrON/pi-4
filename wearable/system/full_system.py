def run_full_system():
    """The original all-sensors-at-once coordinator loop."""
    from wearable.ui.display import Display
    from wearable.sensors.dht import DHTSensor
    from wearable.sensors.sound import SoundSensor
    from wearable.sensors.motion import MotionSensor
    from wearable.sensors.vitals import VitalsSensor
    from wearable.sensors.gps import GPSReceiver
    from wearable.peripherals.camera import CameraManager
    from wearable.communications.lora_radio import LoRaRadio
    from wearable.ui.stt import SpeechToText
    from wearable.ui.tts import TextToSpeech

    print("==================================================")
    print("    Starting Wearable Edge Monitoring System      ")
    print("==================================================")

    lcd = Display()
    dht = DHTSensor()
    sound = SoundSensor()
    motion = MotionSensor()
    vitals = VitalsSensor()
    gps = GPSReceiver()
    camera = CameraManager()
    lora = LoRaRadio()
    stt = SpeechToText()
    tts = TextToSpeech()
    bt = BluetoothManager(lcd=lcd)

    btn_photo = Button(BUTTON_PHOTO, pull_up=True, bounce_time=0.3)
    btn_tx = Button(BUTTON_LORA_TX, pull_up=True, bounce_time=0.3)
    btn_rx = Button(BUTTON_LORA_RX, pull_up=True, bounce_time=0.3)

    # Queue for non-blocking keyboard inputs
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

    kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
    kb_thread.start()

    def handle_incoming_lora(payload):
        p_type = payload.get("type")
        print(f"\n[LoRa RX Packet Received]: Type={p_type}")

        if p_type == "VITALS":
            d = payload.get("data", {})
            bpm = d.get("BPM", 0)
            spo2 = d.get("SPO2", 0)
            ts = d.get("TS", "")
            lcd.show_banner(f"RX VIT [{ts}]", f"B:{bpm} S:{spo2}%", duration=4.0)
            tts.speak(f"Received vitals: heart rate {bpm}, oxygen {spo2} percent")

        elif p_type == "TEXT":
            msg = payload.get("text", "")
            lcd.show_banner("MSG RECEIVED", msg[:16], duration=5.0)
            tts.speak(f"Incoming message: {msg}")

        elif p_type == "IMAGE":
            lcd.show_banner("IMAGE RECEIVED", "SAVED TO DISK", duration=4.0)
            tts.speak("Incoming image received and saved to disk.")

        elif p_type == "AUDIO":
            lcd.show_banner("AUDIO RECEIVED", "SAVED TO DISK", duration=4.0)
            tts.speak("Incoming audio note received.")

    lora.start_listener(on_packet_received=handle_incoming_lora)
    stt.start()

    tts.speak("Edge monitoring station online.")
    lcd.show_banner("SYSTEM READY", "ALL SENSORS ON", duration=2.0)

    last_lora_tx_time = 0
    last_camera_time = 0
    last_gateway_sync = 0

    def trigger_photo(source="MANUAL"):
        nonlocal last_camera_time
        now = time.time()
        if now - last_camera_time < CAMERA_COOLDOWN:
            return
        last_camera_time = now

        lcd.show_banner("TAKING PHOTO", "PLEASE WAIT...", duration=2.0)
        path = camera.capture_photo(source=source)
        if path:
            lcd.show_banner("PICTURE TAKEN", "SUCCESS", duration=2.0)
            tts.speak("Photo captured.")
        else:
            lcd.show_banner("CAMERA ERROR", "RETRY", duration=2.0)

    def trigger_lora_tx(source="MANUAL"):
        nonlocal last_lora_tx_time
        now = time.time()
        if now - last_lora_tx_time < LORA_COOLDOWN:
            return
        last_lora_tx_time = now

        payload = {
            "BPM": vitals.bpm if vitals.bpm else 0,
            "SPO2": vitals.spo2 if vitals.spo2 else 0,
            "TEMP": dht.temp if dht.temp else 0,
            "HUM": dht.humidity if dht.humidity else 0,
            "SOUND": sound.status,
            "FB": motion.dir_fb,
            "LR": motion.dir_lr,
            "LAT": gps.lat,
            "LON": gps.lon,
            "SATS": gps.sats
        }
        print(f"\n[LoRa TX Packet Attempt]: Type=VITALS | Source={source} | Data={payload}")
        lcd.show_banner("LORA TX", f"B:{int(payload['BPM'])} S:{int(payload['SPO2'])}%", duration=2.5)
        success = lora.send_telemetry(payload)

        if success:
            print("[LoRa TX Packet Sent]: Type=VITALS")
            tts.speak("Telemetry transmitted.")
        else:
            print("[LoRa TX Failed]: Message send error.")
            lcd.show_banner("LORA TX", "FAILED", duration=2.5)
            tts.speak("Telemetry message failed to send.")

    # --- Startup Health Check: surface hardware/init failures on LCD ---
    def check_startup_health():
        checks = [
            (not dht.dht_device,      "DHT11 FAILED",    "CHECK WIRING"),
            (not motion.is_connected, "MPU6050 FAILED",  "CHECK WIRING"),
            (not vitals.is_connected, "MAX30102 FAILED", "CHECK WIRING"),
            (not gps.ser,             "GPS FAILED",      "CHECK PORT"),
            (not camera.picam2,       "CAMERA FAILED",   "CHECK RIBBON"),
            (not lora.ser,            "LORA FAILED",     "CHECK PORT"),
            (stt.model is None,       "STT FAILED",      "NO MODEL"),
            (tts.engine == tts.DUMMY_ENGINE, "TTS FAILED", "NO ENGINE"),
        ]
        failures = [(l1, l2) for cond, l1, l2 in checks if cond]

        if not failures:
            display_on_terminal("[Startup Health] All subsystem checks passed.")
            lcd.log("ALL SYSTEMS OK", "", duration=2.0)
            return

        display_on_terminal("[Startup Health] Failures detected:")
        for l1, l2 in failures:
            display_on_terminal(f"  - {l1}: {l2}")
            lcd.log(l1, l2, duration=2.0)
            time.sleep(2.2)

    check_startup_health()
    # --- End health check ---

    bt.autoconnect_async(on_result=lambda ok, name: tts.speak(
        f"Connected to {name}." if ok else "No headset found, using onboard audio."
    ))

    lcd.log("SYSTEM OPERATIONAL", "READY", duration=2.0)
    print("\n--- SYSTEM OPERATIONAL ---")
    print("Triggers (Speak or Type): 'click'/'capture'/'photo' | 'send' | 'receive'")
    print("Hardware Pins          : Photo (Pin 21) | TX (Pin 20) | RX (Pin 16)\n")

    try:
        while True:
            dht.update()
            gps.update()
            motion.update()
            vitals.update()
            bt.is_connected()  # polls BT status + re-routes audio if device changed

            if time.time() - last_gateway_sync >= 30.0:
                last_gateway_sync = time.time()
                try:
                    synced = lora.sync_gateway()
                    if synced:
                        print(f"[Gateway] Synchronized {synced} queued records.")
                except Exception as exc:
                    print(f"[Gateway] Sync unavailable: {exc}")

            # --- Process Voice & Typed Inputs ---
            raw_voice_cmd = stt.get_command()
            raw_typed_cmd = None
            try:
                raw_typed_cmd = input_queue.get_nowait()
            except queue.Empty:
                raw_typed_cmd = None

            # Handle Voice Command
            if raw_voice_cmd:
                action = parse_action(raw_voice_cmd)
                print(f"\n[VOICE TRIGGER]: '{raw_voice_cmd}' -> Action: {action}")

                if action == "MUTE_TTS":
                    tts.mute()
                    lcd.show_banner("TTS", "MUTED", duration = 1.5)
                elif action == "UNMUTE TTS":
                    tts.unmute()
                    lcd.show_banner("TTS", "UNMUTED", duration = 1.5)
                elif action == "MUTE STT":
                        stt.mute()
                        lcd.show_banner("STT", "MUTED", duration = 1.5)
                elif action == "UNMUTE STT":
                        stt.unmute()
                        lcd.show_banner("STT", "UNMUTED", duration = 1.5)
                elif action == "CAPTURE":
                    trigger_photo(source=f"VOICE '{raw_voice_cmd}'")
                elif action == "SEND":
                    trigger_lora_tx(source=f"VOICE '{raw_voice_cmd}'")
                elif action == "RECEIVE":
                    lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)
                    tts.speak("Listening for incoming messages.")
                elif action == "STATUS":
                    print_audio_status(tts, stt, lcd)
                    bt_state = bt.name if bt.is_connected() else "none"

                    sensor_states = {
                        "DHT11":    dht.dht_device is not None,
                        "MPU6050":  motion.is_connected,
                        "MAX30102": vitals.is_connected,
                        "GPS":      gps.ser is not None,
                        "Camera":   camera.picam2 is not None,
                        "LoRa":     lora.ser is not None,
                    }
                    failed = [name for name, ok in sensor_states.items() if not ok]
                    print(f"[Status] BT = {bt_state} | [Status] Sensors down: {', '.join(failed) if failed else 'none'}")
                    lcd.log("BT:" + bt_state[:12].upper(), "DOWN:" + (",".join(failed)[:11] if failed else "NONE"), duration=2.0)
                else:
                    lcd.show_banner("VOICE TXT", raw_voice_cmd[:16], duration=3.0)
                    from wearable.communications.chat import send_chat_message
                    send_chat_message(lora, raw_voice_cmd, "FULL SYSTEM VOICE")

            # Handle Typed Command
            if raw_typed_cmd:
                action = parse_action(raw_typed_cmd)
                print(f"\n[TYPED TRIGGER]: '{raw_typed_cmd}' -> Action: {action}")

                if action == "MUTE_TTS":
                    tts.mute()
                    lcd.show_banner("TTS", "MUTED", duration = 1.5)
                elif action == "UNMUTE TTS":
                    tts.unmute()
                    lcd.show_banner("TTS", "UNMUTED", duration = 1.5)
                elif action == "MUTE STT":
                        stt.mute()
                        lcd.show_banner("STT", "MUTED", duration = 1.5)
                elif action == "UNMUTE STT":
                        stt.unmute()
                        lcd.show_banner("STT", "UNMUTED", duration = 1.5)
                elif action == "CAPTURE":
                    trigger_photo(source=f"TYPED '{raw_typed_cmd}'")
                elif action == "SEND":
                    trigger_lora_tx(source=f"TYPED '{raw_typed_cmd}'")
                elif action == "RECEIVE":
                    lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)
                    tts.speak("Listening for incoming messages.")
                else:
                    lcd.show_banner("TYPED TXT", raw_typed_cmd[:16], duration=3.0)
                    from wearable.communications.chat import send_chat_message
                    send_chat_message(lora, raw_typed_cmd, "FULL SYSTEM TYPED")

            # --- Physical Button Triggers ---
            if btn_photo.is_pressed:
                trigger_photo(source="BUTTON Pin 21")
            elif btn_tx.is_pressed:
                trigger_lora_tx(source="BUTTON Pin 20")
            elif btn_rx.is_pressed:
                lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)

            lcd.update_cyclic(vitals=vitals, dht=dht, motion=motion, gps=gps, sound=sound)
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nStopping system...")

    finally:
        print("Cleaning up resources...")
        stt.stop()
        camera.close()
        lora.close()
        dht.close()
        sound.close()
        gps.close()
        btn_photo.close()
        btn_tx.close()
        btn_rx.close()
        lcd.show_banner("SYSTEM STOPPED", "GOODBYE", duration=1.5)
        time.sleep(1.0)
        lcd.close()
        print("Hardware shutdown cleanly complete.")


"""Full hardware coordinator for the wearable edge node."""

import os
import sys
import time
import queue
import threading
from gpiozero import Button
from wearable.peripherals.bluetooth import BluetoothManager
from wearable.ui.status import print_audio_status
from wearable.ui.terminal import display_on_terminal
from config import BUTTON_PHOTO, BUTTON_LORA_TX, BUTTON_LORA_RX, LORA_COOLDOWN, CAMERA_COOLDOWN

print = display_on_terminal


def parse_action(text):
    body = text.strip().lower()
    tokens = set(body.split())
    if body == "status":
        return "STATUS"
    if body in ("mute tts", "tts off", "voice off"):
        return "MUTE_TTS"
    if {"click", "capture", "photo", "picture", "snap"}.intersection(tokens):
        return "CAPTURE"
    if {"send", "transmit"}.intersection(tokens):
        return "SEND"
    if {"receive", "listen"}.intersection(tokens):
        return "RECEIVE"
    return "TEXT"
