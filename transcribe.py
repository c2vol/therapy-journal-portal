"""Turns your recorded voice into text, entirely on your computer.

It uses Whisper, an open speech-recognition model, through the faster-whisper
library. The first time you record, the model (about 150 MB for "base.en") is
downloaded once from Hugging Face. After that, transcription works offline and
your audio never leaves your computer. The audio is kept in memory only long
enough to transcribe it, and is never saved to disk.

Set WHISPER_MODEL to "small.en" for better accuracy (slower), or "tiny.en" for
speed. Drop the ".en" if you speak other languages.
"""

import io
import os
import threading

MODEL_NAME = os.environ.get("WHISPER_MODEL", "base.en")

_model = None
_lock = threading.Lock()


class TranscriptionError(Exception):
    pass


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel  # imported lazily: it's slow to load
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        return _model


def transcribe(audio: bytes) -> str:
    if not audio:
        raise TranscriptionError("No audio was recorded.")
    try:
        segments, _info = _get_model().transcribe(io.BytesIO(audio), vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:  # model download or audio decoding problems
        raise TranscriptionError(f"Couldn't transcribe the recording ({type(e).__name__}).") from e
    return text
