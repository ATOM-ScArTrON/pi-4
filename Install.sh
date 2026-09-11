#!/bin/bash
set -e

echo "Updating system dependencies..."
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    python3-venv \
    portaudio19-dev \
    libatlas-base-dev \
    i2c-tools

# Create virtual environment if missing
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

echo "Upgrading package manager..."
pip install --upgrade pip setuptools wheel

echo "Installing active Python dependencies..."
pip install --no-cache-dir \
    RPi.GPIO \
    gpiozero \
    spidev \
    smbus2 \
    pyserial \
    pynmea2 \
    adafruit-circuitpython-dht \
    adafruit-circuitpython-rfm9x \
    adafruit-circuitpython-ssd1306 \
    Pillow \
    PyAudio \
    SpeechRecognition \
    gTTS \
    pyttsx3

echo "Cleaning up redundant artifacts..."
rm -f dht_s_bt.py earbuds.py gps.py loratxrx.py nbm.py test.wav
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "Installation finished successfully."