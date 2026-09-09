#!/bin/bash
sudo apt-get update
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install python3-pip
sudo pip3 install adafruit-blinka
sudo pip3 install adafruit-circuitpython-mcp3xxx
sudo apt-get install build-essential python-dev python3-smbus git
cd ~
git clone https://github.com/adafruit/Adafruit_Python_MCP3008.git
cd Adafruit_Python_MCP3008
sudo python3 setup.py install
sudo pip3 install Adafruit_DHT
sudo pip install Adafruit_DHT
sudo pip install pad4pi
sudo apt-get install python3-dev python3-pip
sudo pip3 install mfrc522
python3 -m venv ~/mcp3008-demo-venv
source ~/mcp3008-demo-venv/bin/activate
pip install adafruit-circuitpython-mcp3xxx
sudo apt update
sudo apt install -y python3-smbus i2c-tools
i2cdetect -y 1
sudo apt update
sudo apt install -y python3-smbus i2c-tools
pip3 install RPLCD
sudo apt-get install python3-serial python3-rpi.gpio
pip3 install max30102
pip3 install git+https://github.com
wget https://githubusercontent.com
pip3 install max30100
pip3 install RPLCD
sudo apt update
sudo apt install python3-pip portaudio19-dev libasound2-dev
pip3 install vosk sounddevice opencv-python
pip3 install --user vosk sounddevice opencv-python
sudo apt install v4l-utils
pip3 uninstall opencv-python numpy -y
sudo apt update
python3 -m pip install --user vosk
sudo apt install python3-opencv python3-pyaudio portaudio19-dev
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
pip3 install vosk
sudo apt-get install python3-serial python3-rpi.gpio
python3 -m pip install vosk
pip3 install vosk sounddevice opencv-python
sudo apt install python3-pip portaudio19-dev libasound2-dev
sudo apt install python3-opencv
python3 -c "import cv2; print(cv2.__version__)"
python3 -c "import vosk; print('VOSK OK')"
sudo apt install python3-sounddevice
pip3 install --user sounddevice
sudo apt-get update
sudo apt-get install python3-smbus i2c-tools
pip install mpu6050-raspberrypi
sudo apt update
sudo apt install -y python3-gps gpsd gpsd-clients

cd ~
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
rm vosk-model-small-en-us-0.15.zip
pip install --break-system-packages \
    pyserial \
    pynmea2 \
    RPLCD \
    smbus2 \
    adafruit-circuitpython-dht \
    gpiozero \
    mpu6050-raspberrypi \
    vosk
sudo apt update && sudo apt install -y \
    python3-pip \
    python3-smbus \
    i2c-tools \
    alsa-utils \
    libgpiod2 \
    libcap-dev \
    libcamera-dev \
    python3-libcamera \
    python3-picamera2 \
    wget \
    unzip



