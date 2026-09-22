"""Shared command parsing for the wearable launchers."""


def parse_action(text):
    """Standardize keyword matching for voice and typed inputs."""
    body = text.strip().lower()
    tokens = set(body.split())
    if body == "status":
        return "STATUS"
    if body in ("mute tts", "tts off", "voice off"):
        return "MUTE_TTS"
    if body in ("unmute tts", "tts on"):
        return "UNMUTE_TTS"
    if body in ("mute stt", "stt off"):
        return "MUTE_STT"
    if body in ("unmute stt", "stt on"):
        return "UNMUTE_STT"
    if {"click", "capture", "photo", "picture", "snap"}.intersection(tokens):
        return "CAPTURE"
    if {"send", "transmit"}.intersection(tokens):
        return "SEND"
    if {"receive", "listen"}.intersection(tokens):
        return "RECEIVE"
    return "TEXT"
