"""Local speech-to-text with word-level timestamps via faster-whisper."""
from __future__ import annotations

import os

from .util import extract_wav, log


def transcribe(
    video_path: str,
    workdir: str,
    model_size: str = "small",
    lang: str | None = None,
    compute_type: str = "int8",
) -> dict:
    """Return {language, duration, segments:[{start,end,text}], words:[{start,end,word}]}."""
    from faster_whisper import WhisperModel

    wav = extract_wav(video_path, os.path.join(workdir, "audio.wav"))

    log(f"Loading whisper model '{model_size}' (first run downloads it)...")
    model = WhisperModel(model_size, device="cpu", compute_type=compute_type)

    log("Transcribing (CPU, this can take a while on long videos)...")
    seg_iter, info = model.transcribe(
        wav, language=lang, word_timestamps=True, vad_filter=True
    )

    segments, words = [], []
    for seg in seg_iter:
        text = seg.text.strip()
        if not text:
            continue
        segments.append({"start": seg.start, "end": seg.end, "text": text})
        for w in seg.words or []:
            token = w.word.strip()
            if token:
                words.append({"start": w.start, "end": w.end, "word": token})

    log(f"Transcribed {len(segments)} segments ({info.language}, "
        f"{info.duration:.0f}s of audio).")
    return {
        "language": info.language,
        "duration": info.duration,
        "segments": segments,
        "words": words,
    }
