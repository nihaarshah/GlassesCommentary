"""Whisper transcription with word timestamps (runs in the GPU image)."""

from __future__ import annotations

_MODEL = None


def transcribe_file(path: str, model_name: str) -> dict:
    global _MODEL
    from faster_whisper import WhisperModel

    if _MODEL is None:
        _MODEL = WhisperModel(model_name, device="cuda", compute_type="float16")
    segments, info = _MODEL.transcribe(path, word_timestamps=True, vad_filter=True, language=None)
    out = []
    for s in segments:
        out.append(
            {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
                "words": [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word} for w in (s.words or [])],
            }
        )
    return {"language": info.language, "duration": info.duration, "segments": out}
