#!/usr/bin/env python3
"""
Menu-driven launcher for the Raspberry Pi 4 wearable edge node.

Run this file and pick a number to run any single sensor/module standalone
(each one loops and prints live readings until Ctrl+C), or pick the "Full
Integrated System" option to run everything together, exactly like the
original all-in-one main loop.
"""

import os
import sys
import time
import queue
import threading
import importlib
from gpiozero import Button
from modules.bluetooth_manager import BluetoothManager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    BUTTON_PHOTO, BUTTON_LORA_TX, BUTTON_LORA_RX,
    LORA_COOLDOWN, CAMERA_COOLDOWN
)

# Menu entry format: key -> (display label, module import path or None for special actions)
MENU = {
    "1":  ("DHT11 Temp/Humidity Sensor", "modules.dht_sensor"),
    "2":  ("Sound Sensor",               "modules.sound_sensor"),
    "3":  ("MPU6050 Motion Sensor",      "modules.motion_sensor"),
    "4":  ("MAX30102 Vitals Sensor",     "modules.vitals_sensor"),
    "5":  ("GPS Receiver",               "modules.gps_receiver"),
    "6":  ("Camera",                     "modules.camera"),
    "7":  ("LoRa Radio",                 "modules.lora_radio"),
    "8":  ("LCD Display",                "modules.display"),
    "9":  ("Text-to-Speech",             "modules.tts"),
    "10": ("Speech-to-Text",             "modules.stt"),
    "11": ("Bluetooth Manager",          "modules.bluetooth_manager"),
    "12": ("LoRa Chat Mode",             "modules.chat_mode"),
    "13": ("Full Integrated System",     None),
    "0":  ("Exit",                       None),
}


def print_menu():
    print("\n==================================================")
    print("   Wearable Edge Node - Module Launcher")
    print("==================================================")
    for key in sorted(MENU, key=lambda k: (len(k), k)):
        label, _ = MENU[key]
        print(f"  [{key:>2}] {label}")
    print("==================================================")


def run_module(module_path):
    """Import the chosen module and call its run_standalone() loop."""
    mod = importlib.import_module(module_path)
    if not hasattr(mod, "run_standalone"):
        print(f"[Error]: {module_path} has no run_standalone() function.")
        return
    mod.run_standalone()


def parse_action(text):
    """Standardize keyword matching for both voice and typed inputs."""
    tokens = set(text.lower().split())
    if {"click", "capture", "photo", "picture", "snap"}.intersection(tokens):
        return "CAPTURE"
    if {"send", "transmit"}.intersection(tokens):
        return "SEND"
    if {"receive", "listen"}.intersection(tokens):
        return "RECEIVE"
    return "TEXT"


def run_full_system():
    """The original all-sensors-at-once coordinator loop."""
    from modules.display import Display
    from modules.dht_sensor import DHTSensor
    from modules.sound_sensor import SoundSensor
    from modules.motion_sensor import MotionSensor
    from modules.vitals_sensor import VitalsSensor
    from modules.gps_receiver import GPSReceiver
    from modules.camera import CameraManager
    from modules.lora_radio import LoRaRadio
    from modules.stt import SpeechToText
    from modules.tts import TextToSpeech

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
        success = lora.send_vitals(payload)

        if success:
            print("[LoRa TX Packet Sent]: Type=VITALS")
            tts.speak("Telemetry transmitted.")
        else:
            print("[LoRa TX Failed]: Transmission error.")
            lcd.show_banner("LORA TX", "FAILED", duration=2.5)
            tts.speak("Telemetry transmission failed.")

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
            (tts.engine == "dummy",   "TTS FAILED",      "NO ENGINE"),
        ]
        failures = [(l1, l2) for cond, l1, l2 in checks if cond]

        if not failures:
            lcd.log("ALL SYSTEMS OK", "", duration=2.0)
            return

        for l1, l2 in failures:
            lcd.log(l1, l2, duration=2.0)
            time.sleep(2.2)

    check_startup_health()
    # --- End health check ---

    bt.autoconnect_async(on_result=lambda ok, name: tts.speak(
        f"Connected to {name}." if ok else "No headset found, using onboard audio."
    ))

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

            dht.sound_status = sound.status

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

                if action == "CAPTURE":
                    trigger_photo(source=f"VOICE '{raw_voice_cmd}'")
                elif action == "SEND":
                    trigger_lora_tx(source=f"VOICE '{raw_voice_cmd}'")
                elif action == "RECEIVE":
                    lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)
                    tts.speak("Listening for incoming transmissions.")
                else:
                    lcd.show_banner("VOICE TXT", raw_voice_cmd[:16], duration=3.0)
                    lora.send_text(raw_voice_cmd)

            # Handle Typed Command
            if raw_typed_cmd:
                action = parse_action(raw_typed_cmd)
                print(f"\n[TYPED TRIGGER]: '{raw_typed_cmd}' -> Action: {action}")

                if action == "CAPTURE":
                    trigger_photo(source=f"TYPED '{raw_typed_cmd}'")
                elif action == "SEND":
                    trigger_lora_tx(source=f"TYPED '{raw_typed_cmd}'")
                elif action == "RECEIVE":
                    lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)
                    tts.speak("Listening for incoming transmissions.")
                else:
                    lcd.show_banner("TYPED TXT", raw_typed_cmd[:16], duration=3.0)
                    lora.send_text(raw_typed_cmd)

            # --- Physical Button Triggers ---
            if btn_photo.is_pressed:
                trigger_photo(source="BUTTON Pin 21")
            elif btn_tx.is_pressed:
                trigger_lora_tx(source="BUTTON Pin 20")
            elif btn_rx.is_pressed:
                lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)

            lcd.update_cyclic(vitals=vitals, dht=dht, motion=motion, gps=gps)
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
        lcd.show_banner("SYSTEM STOPPED", "GOODBYE", duration=1.5)
        time.sleep(1.0)
        lcd.close()
        btn_photo.close()
        btn_tx.close()
        btn_rx.close()
        print("Hardware shutdown cleanly complete.")


def main():
    while True:
        print_menu()
        choice = input("\nSelect an option: ").strip()

        if choice not in MENU:
            print("Invalid option, please enter a number from the list.")
            continue

        label, module_path = MENU[choice]

        if choice == "0":
            print("Goodbye!")
            break

        elif choice == "13":
            run_full_system()

        else:
            print(f"\n--- Launching: {label} ---\n")
            try:
                run_module(module_path)
            except Exception as e:
                print(f"[Error running {label}]: {e}")

        input("\nPress Enter to return to the menu...")


if __name__ == "__main__":
    main()