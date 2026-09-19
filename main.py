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
from wearable.peripherals.bluetooth import BluetoothManager
from wearable.ui.status import print_audio_status
from wearable.ui.terminal import display_on_terminal
from wearable.system.actions import parse_action

print = display_on_terminal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    BUTTON_PHOTO, BUTTON_LORA_TX, BUTTON_LORA_RX,
    LORA_COOLDOWN, CAMERA_COOLDOWN
)

# Menu entry format: key -> (display label, module import path or None for special actions)
MENU = {
    "1":  ("DHT11 Temp/Humidity Sensor", "wearable.sensors.dht"),
    "2":  ("Sound Sensor",               "wearable.sensors.sound"),
    "3":  ("MPU6050 Motion Sensor",      "wearable.sensors.motion"),
    "4":  ("MAX30102 Vitals Sensor",     "wearable.sensors.vitals"),
    "5":  ("GPS Receiver",               "wearable.sensors.gps"),
    "6":  ("Camera",                     "wearable.peripherals.camera"),
    "7":  ("LoRa Radio",                 "wearable.communications.lora_radio"),
    "8":  ("LCD Display",                "wearable.ui.display"),
    "9":  ("Text-to-Speech",             "wearable.ui.tts"),
    "10": ("Speech-to-Text",             "wearable.ui.stt"),
    "11": ("Bluetooth Manager",          "wearable.peripherals.bluetooth"),
    "12": ("LoRa Chat Mode",             "wearable.communications.chat"),
    "13": ("Full Integrated System",     None),
    "14": ("Run Secure Mesh Payload Tests", "tests.integration.test_mesh"),
    "15": ("Run Ascon-XOF Tests", "tests.unit.crypto.test_ascon"),
    "16": ("Provision Pi From Central Server", "wearable.communications.provisioning_client"),
    "17": ("Sync Gateway Queue", "wearable.communications.gateway_sync"),
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
            from wearable.system.full_system import run_full_system
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
