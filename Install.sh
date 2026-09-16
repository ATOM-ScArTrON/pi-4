#!/bin/bash
set -e

echo "=== 1. Updating System & Installing All OS-Level Dependencies ==="
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    python3-venv \
    i2c-tools \
    libgpiod-dev \
    gpiod \
    bluez \
    pipewire \
    pipewire-bin \
    espeak-ng \
    speech-dispatcher \
    swig \
    liblgpio-dev \
    libcap-dev \
    python3-picamera2 \
    python3-libcamera \
    libcamera-apps-lite \
    direnv

echo "=== 1.5. Enabling Hardware Interfaces (I2C & Serial) ==="
# Programmatically enable I2C, Serial hardware and UART interfaces
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_serial_hw 0

CONFIG_FILE="/boot/firmware/config.txt"
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="/boot/config.txt"
fi

if ! grep -q "dtoverlay=uart3" "$CONFIG_FILE"; then
    echo "Enabling UART3 hardware overlay..."
    echo "dtoverlay=uart3" | sudo tee -a "$CONFIG_FILE" > /dev/null
fi

echo "=== 2. Creating Virtual Environment (with System Site Packages) ==="
if [ ! -d "venv" ]; then
    python3 -m venv --system-site-packages venv
fi

# Explicitly force the system package flag to true to guarantee portability
if [ -f "venv/pyvenv.cfg" ]; then
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' venv/pyvenv.cfg
fi

source venv/bin/activate

echo "=== 3. Upgrading Python Packaging Tools ==="
pip install --upgrade pip setuptools wheel

echo "=== 4. Installing Python Dependencies ==="
pip install --no-cache-dir \
    gpiozero \
    smbus2 \
    pyserial \
    pynmea2 \
    adafruit-blinka \
    adafruit-circuitpython-dht \
    RPLCD \
    vosk 

echo "=== 5. Configuring Direnv Automation ==="
cat << 'EOF' > .envrc
# Explicitly activate the venv path without calling python macros
if [ -d "venv" ]; then
    export VIRTUAL_ENV="$PWD/venv"
    PATH_add "$VIRTUAL_ENV/bin"
fi
EOF

# Inject hook into bashrc if missing
if ! grep -q "direnv hook bash" ~/.bashrc 2>/dev/null; then
    echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
fi

# Force allow for both the current user running the script and root
direnv allow . 2>/dev/null || true
if [ -n "$SUDO_USER" ]; then
    sudo -u "$SUDO_USER" direnv allow . 2>/dev/null || true
fi

echo "=== 6. Generating .gitignore ==="
cat << 'EOF' > .gitignore
# Python Virtual Environment and Cache
venv/
__pycache__/
*.py[cod]
*$py.class

# Direnv local security state
.direnv/
.envrc

# Vosk Speech Models
vosk-model-*/

# Media and Test Artifacts
*.wav
*.jpg
*.png
*.jpeg

# OS Generated Files
.DS_Store
Thumbs.db
EOF

echo "=== 7. Cleaning Up Redundant Artifacts ==="
rm -f dht_s_bt.py earbuds.py gps.py loratxrx.py nbm.py test.wav
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "--------------------------------------------------"
echo "Setup Complete! To apply hardware interface changes,"
echo "please reboot your Pi using: sudo reboot"
echo "--------------------------------------------------"