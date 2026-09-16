"""
Text-to-Speech (TTS) engine wrapper for the Raspberry Pi.
Supports Piper neural voice, espeak-ng, and system speech dispatcher over PipeWire/ALSA.
"""

import os
import shutil
import threading
import subprocess
from modules.terminal import display_on_terminal

print = display_on_terminal

class TextToSpeech:
    DUMMY_ENGINE = "dummy"

    def __init__(self):
        self.engine = self._detect_engine()
        self.enabled = True
        print(f"[TTS] Engine active: {self.engine}")

    def _detect_engine(self):
        if shutil.which("piper"):
            return "piper"
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        if shutil.which("espeak"):
            return "espeak"
        if shutil.which("spd-say"):
            return "spd-say"
        return self.DUMMY_ENGINE

    def speak(self, text, block=False):
        if not text or not self.enabled:
            return

        if block:
            self._do_speak(text)
        else:
            threading.Thread(target=self._do_speak, args=(text,), daemon=True).start()

    def mute(self):
        self.enabled = False
        print("[TTS] Muted.")

    def unmute(self):
        self.enabled = True
        print("[TTS] Unmuted.")

    def _do_speak(self, text):
        clean_text = text.replace('"', '').replace("'", "")
        print(f"[TTS Voice]: \"{clean_text}\"")

        if self.engine in ("espeak-ng", "espeak"):
            subprocess.run([self.engine, "-s", "150", clean_text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif self.engine == "spd-say":
            subprocess.run(["spd-say", clean_text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif self.engine == "piper":
            piper_proc = subprocess.Popen(
                ["piper", "--output-raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            pw_proc = subprocess.Popen(
                ["pw-play", "--rate", "22050", "--channels", "1", "--format", "s16", "-"],
                stdin=piper_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            piper_proc.stdin.write(f"{clean_text}\n".encode("utf-8"))
            piper_proc.stdin.close()
            piper_proc.stdout.close()
            pw_proc.wait()

    def synthesize_to_wav(self, text, output_file):
        clean_text = text.replace('"', '').replace("'", "")
        if self.engine in ("espeak-ng", "espeak"):
            subprocess.run([self.engine, "-w", output_file, clean_text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.exists(output_file)
        return False

    def estimate_duration(self, text):
        """Estimates speech playback duration in seconds."""
        words = len(text.split())
        # Average rate: ~2.5 words/sec, minimum floor of 1.5 seconds
        return max(1.5, words / 2.5)


def run_standalone(lcd=None):
    """Type text and have it spoken aloud. Type 'exit' or Ctrl+C to quit. Mirrors spoken text to LCD."""
    tts = TextToSpeech()

    own_lcd = lcd is None
    if own_lcd:
        from modules.display import Display
        lcd = Display()

    lcd.log("TTS READY", f"ENGINE:{tts.engine}", duration=2.0)
    print("Type text + Enter to speak it. Type 'exit' to quit.\n")
    try:
        while True:
            text = input("> ")
            if text.strip().lower() == "exit":
                break
            
            # Run LCD animation in background thread while TTS speaks
            lcd_thread = threading.Thread(
                target=lcd.log, 
                args=("SPEAKING:", text, 2.5), 
                daemon=True
            )
            lcd_thread.start()
            
            # Speak on main thread concurrently
            tts.speak(text, block=True)
            
    except KeyboardInterrupt:
        pass
    print("\nStopped.")
    if own_lcd:
        lcd.close()


if __name__ == "__main__":
    run_standalone()
