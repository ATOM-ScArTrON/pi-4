#!/usr/bin/env python3
"""
Main central coordinator for the Raspberry Pi 4 wearable edge node.
Integrates STT, TTS, LoRa multi-data telemetry, sensors, camera, and LCD display.
"""

import os
import sys
import time
from gpiozero import Button

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    BUTTON_PHOTO, BUTTON_LORA_TX, BUTTON_LORA_RX,
    LORA_COOLDOWN, CAMERA_COOLDOWN
)
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
from modules.bluetooth_manager import BluetoothManager

def main():
    print("==================================================")
    print("    Starting Wearable Edge Monitoring System      ")
    print("==================================================")

    # 1. Initialize Subsystems
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
    bt = BluetoothManager()

    # 2. Hardware Buttons (Active Low with software debounce)
    btn_photo = Button(BUTTON_PHOTO, pull_up=True, bounce_time=0.3)
    btn_tx = Button(BUTTON_LORA_TX, pull_up=True, bounce_time=0.3)
    btn_rx = Button(BUTTON_LORA_RX, pull_up=True, bounce_time=0.3)

    # 3. Handle Incoming LoRa Telemetry Packets
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
            path = payload.get("path", "")
            lcd.show_banner("IMAGE RECEIVED", "SAVED TO DISK", duration=4.0)
            tts.speak("Incoming image received and saved to disk.")

        elif p_type == "AUDIO":
            path = payload.get("path", "")
            lcd.show_banner("AUDIO RECEIVED", "SAVED TO DISK", duration=4.0)
            tts.speak("Incoming audio note received.")

    # Start asynchronous listeners
    lora.start_listener(on_packet_received=handle_incoming_lora)
    stt.start()

    # Voice startup announcement
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
        lcd.show_banner("LORA TX", f"B:{int(payload['BPM'])} S:{int(payload['SPO2'])}%", duration=2.5)
        success = lora.send_vitals(payload)
        if success:
            tts.speak("Telemetry transmitted.")

    print("\n--- SYSTEM OPERATIONAL ---")
    print("Voice Triggers: 'capture' / 'send' / 'receive'")
    print("Hardware Pins : Photo (Pin 21) | TX (Pin 20) | RX (Pin 16)\n")

    try:
        while True:
            # 1. Non-blocking sensor polling
            dht.update()
            gps.update()
            motion.update()
            vitals.update()

            # Attach sound status to dht for display convenience
            dht.sound_status = sound.status

            # 2. Process Voice Commands from STT
            cmd = stt.get_command()
            if cmd == "capture":
                print("\n[VOICE TRIGGER]: 'capture'")
                trigger_photo(source="VOICE 'capture'")
            elif cmd == "send":
                print("\n[VOICE TRIGGER]: 'send'")
                trigger_lora_tx(source="VOICE 'send'")
            elif cmd == "receive":
                print("\n[VOICE TRIGGER]: 'receive'")
                lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)
            elif cmd:
                # Transcribed arbitrary speech - can be forwarded over LoRa as a text message!
                print(f"\n[VOICE MESSAGE]: '{cmd}'")
                lcd.show_banner("VOICE TXT", cmd[:16], duration=3.0)
                lora.send_text(cmd)

            # 3. Hardware Button Triggers
            if btn_photo.is_pressed:
                trigger_photo(source="BUTTON Pin 21")
            elif btn_tx.is_pressed:
                trigger_lora_tx(source="BUTTON Pin 20")
            elif btn_rx.is_pressed:
                lcd.show_banner("LORA RX", "LISTENING...", duration=3.0)

            # 4. Refresh cyclical LCD dashboard
            lcd.update_cyclic(vitals=vitals, dht=dht, motion=motion, gps=gps)

            # 5. Low-latency sleep to yield CPU cycles
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

if __name__ == "__main__":
    main()

