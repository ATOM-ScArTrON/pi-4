"""
Text-to-Speech (TTS) engine wrapper for the Raspberry Pi.
Supports Piper neural voice, espeak-ng, and system speech dispatcher over PipeWire/ALSA.
"""

import os
import shutil
import threading
import subprocess

class TextToSpeech:
    def __init__(self):
        self.engine = self._detect_engine()
        print(f"[TTS] Engine active: {self.engine}")

    def _detect_engine(self):
        """Detects available system TTS binaries."""
        if shutil.which("piper"):
            return "piper"
        elif shutil.which("espeak-ng"):
            return "espeak-ng"
        elif shutil.which("espeak"):
            return "espeak"
        elif shutil.which("spd-say"):
            return "spd-say"
        else:
            return "dummy"

    def speak(self, text, block=False):
        """Speaks text out loud over connected earbuds or audio output."""
        if not text:
            return

        if block:
            self._do_speak(text)
        else:
            threading.Thread(target=self._do_speak, args=(text,), daemon=True).start()

    def _do_speak(self, text):
        clean_text = text.replace('"', '').replace("'", "")
        print(f"[TTS Voice]: \"{clean_text}\"")

        if self.engine == "espeak-ng" or self.engine == "espeak":
            cmd = [self.engine, "-s", "150", clean_text]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif self.engine == "spd-say":
            subprocess.run(["spd-say", clean_text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif self.engine == "piper":
            # Piper converts text to wav stream, piped to pw-play
            piper_cmd = f"echo '{clean_text}' | piper --output-raw | pw-play --rate 22050 --channels 1 --format s16 -"
            subprocess.run(piper_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        else:
            # Dummy fallback (console simulated speech)
            pass

    def synthesize_to_wav(self, text, output_file):
        """Synthesizes text into a local WAV audio file."""
        clean_text = text.replace('"', '').replace("'", "")
        if self.engine in ["espeak-ng", "espeak"]:
            cmd = [self.engine, "-w", output_file, clean_text]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.exists(output_file)
        return False

if __name__ == "__main__":
    print("Testing TextToSpeech module...")
    tts = TextToSpeech()
    tts.speak("Biometric monitoring station ready.", block=True)
    tts.speak("Heart rate is seventy two beats per minute.", block=True)
    print("TTS test complete.")

