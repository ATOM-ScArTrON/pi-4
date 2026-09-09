"""
Speech-to-Text (STT) engine using Vosk neural speech model and PipeWire audio stream.
Continuously captures speech from Bluetooth earbuds or microphone and transcribes arbitrary speech.
"""

import os
import json
import time
import queue
import threading
import subprocess
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

        self._init_model()

    def _init_model(self):
        if not os.path.exists(self.model_path):
            print(f"[STT Error]: Vosk model not found at {self.model_path}")
            return
        try:
            print(f"[STT] Loading Vosk model from {self.model_path}...")
            self.model = Model(self.model_path)
            # Open recognition (transcribes arbitrary speech without restrictive grammar)
            self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
            print("[STT] Vosk model loaded successfully.")
        except Exception as e:
            print(f"[STT Init Error]: {e}")
            self.model = None

    def start(self):
        """Starts background PipeWire audio capture and STT processing thread."""
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
                if text and text != "[unk]":
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

    def get_transcript(self, block=False):
        """Returns the next transcribed sentence from the queue, or None."""
        try:
            return self.transcript_queue.get(block=block)
        except queue.Empty:
            return None

    def get_command(self):
        """Helper to match command keywords in newly transcribed sentences."""
        text = self.get_transcript(block=False)
        if not text:
            return None

        tokens = text.lower().split()
        now = time.time()
        if now - self.last_command_time < 2.0:
            return None

        if "send" in tokens or "transmit" in tokens:
            self.last_command_time = now
            return "send"
        elif "receive" in tokens or "listen" in tokens:
            self.last_command_time = now
            return "receive"
        elif "capture" in tokens or "photo" in tokens or "picture" in tokens or "yes" in tokens:
            self.last_command_time = now
            return "capture"

        return text  # Return full text if not a reserved command

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

if __name__ == "__main__":
    print("Testing SpeechToText module. Speak into microphone/earbuds:")
    stt = SpeechToText()
    stt.start()
    try:
        for _ in range(15):
            t = stt.get_transcript(block=False)
            if t:
                print(f">> Recognized: '{t}'")
            time.sleep(1.0)
    finally:
        stt.stop()
    print("STT test complete.")

