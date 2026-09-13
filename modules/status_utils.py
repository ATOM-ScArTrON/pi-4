"""
Shared status-printing helpers used across run_standalone() loops
(main.py's full system, lora_radio.py, chat_mode.py) so each module
doesn't hand-roll its own copy of the same TTS/STT status printout.
"""

def print_audio_status(tts, stt, lcd=None):
    """Prints TTS/STT mute state to terminal and mirrors it to the LCD.
    Returns (tts_state, stt_state) in case a caller wants to log/reuse them."""
    tts_state = "muted" if not tts.enabled else "on"
    stt_state = "muted" if not stt.enabled else "on"

    print(f"[Audio] TTS={tts_state} | STT={stt_state}")
    if lcd:
        lcd.log("TTS:" + tts_state.upper(), "STT:" + stt_state.upper(), duration=2.0)

    return tts_state, stt_state