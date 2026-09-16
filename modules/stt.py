import os
import json
import time
import queue
import threading
import subprocess
from modules.terminal import display_on_terminal

print = display_on_terminal
from vosk import Model, KaldiRecognizer
from config import VOSK_MODEL_PATH, SAMPLE_RATE, AUDIO_DEVICE

class SpeechToText:
    def __init__(self, model_path=VOSK_MODEL_PATH, sample_rate=SAMPLE_RATE, device=AUDIO_DEVICE):
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.device = device

        self.model = None
        self.recognizer = None
        self.proc = None
        self.thread = None
        self.stop_event = threading.Event()

        self.transcript_queue = queue.Queue()
        self.latest_partial = ""
        self.last_command_time = 0

        self.enabled = True

        self._init_model()

    def _init_model(self):
        if not os.path.exists(self.model_path):
            print(f"[STT Error]: Vosk model not found at {self.model_path}")
            return
        try:
            print(f"[STT] Loading Vosk model from {self.model_path}...")
            self.model = Model(self.model_path)
            self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
            print("[STT] Vosk model loaded successfully.")
        except Exception as e:
            print(f"[STT Init Error]: {e}")
            self.model = None

    def start(self):
        if not self.model or self.thread:
            return

        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        print("[STT] Speech-to-Text listener active.")

    def _worker(self):
        audio_cmd = [
            "pw-record",
            "--target", self.device,
            "--rate", str(self.sample_rate),
            "--channels", "1",
            "--format", "s16",
            "-"
        ]

        try:
            self.proc = subprocess.Popen(
                audio_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )
        except Exception as e:
            print(f"[STT Stream Error]: Failed to launch pw-record: {e}")
            return

        while not self.stop_event.is_set():
            data = self.proc.stdout.read(4000)
            if not data:
                time.sleep(0.01)
                continue

            if self.recognizer.AcceptWaveform(data):
                res = json.loads(self.recognizer.Result())
                text = res.get("text", "").strip()
                if text and text != "[unk]" and self.enabled:
                    print(f"\n[STT HEARD]: {text}")
                    self.transcript_queue.put(text)
            else:
                pres = json.loads(self.recognizer.PartialResult())
                ptext = pres.get("partial", "").strip()
                if ptext:
                    self.latest_partial = ptext

        try:
            self.proc.terminate()
            self.proc.wait(timeout=1)
        except Exception:
            pass

    def mute(self):
        self.enabled = False
        print("[STT] Muted.")

    def unmute(self):
        self.enabled = True
        print("[STT] Unmuted.")

    def get_transcript(self, block=False):
        try:
            return self.transcript_queue.get(block=block)
        except queue.Empty:
            return None

    def get_command(self):
        text = self.get_transcript(block=False)
        if not text:
            return None

        tokens = set(text.lower().split())
        now = time.time()
        
        if now - self.last_command_time < 2.0:
            return None

        if {"send", "transmit"}.intersection(tokens):
            self.last_command_time = now
            return "send"
        if {"receive", "listen"}.intersection(tokens):
            self.last_command_time = now
            return "receive"
        if {"capture", "photo", "picture", "yes"}.intersection(tokens):
            self.last_command_time = now
            return "capture"

        return text

    def stop(self):
        self.stop_event.set()
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None


def run_standalone(lcd=None):
    """Continuously transcribe speech and print it until Ctrl+C. Mirrors each heard phrase to LCD."""
    stt = SpeechToText()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    if not stt.model:
        print("[STT] Cannot start - Vosk model not loaded (see error above).")
        lcd.log("STT FAILED", "NO MODEL", duration=3.0)
        if own_lcd:
            lcd.close()
        return

    stt.start()
    print("[STT] Listening. Speak into the mic. Press Ctrl+C to stop.\n")
    lcd.log("STT READY", "LISTENING...", duration=2.0)
    try:
        while True:
            text = stt.get_transcript(block=True)
            if text:
                print(f"Heard: {text}")
                lcd.log("HEARD:", text[:16], duration=2.5)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stt.stop()
        if own_lcd:
            lcd.close()


if __name__ == "__main__":
    run_standalone()
