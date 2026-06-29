"""Small shared helpers: ffmpeg/ffprobe wrappers, formatting, logging."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time

# Per-thread log sink so the web server can stream a job's progress while the
# CLI keeps printing to stdout. Thread-local => concurrent jobs never mix logs.
_local = threading.local()


def set_log_sink(fn) -> None:
    """Route log() lines for the *current thread* to fn (or None to clear)."""
    _local.sink = fn


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    # sink first so the web UI gets the line even if the console can't print it
    sink = getattr(_local, "sink", None)
    if sink is not None:
        try:
            sink(line)
        except Exception:
            pass
    # Windows consoles default to cp1252; emojis in video titles would crash
    # print(). Down-convert to whatever the console can actually encode.
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)
    except Exception:
        pass


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a command, raising with captured output on failure."""
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
        raise RuntimeError(
            f"command failed ({proc.returncode}): {cmd[0]} ...\n" + "\n".join(tail)
        )
    return proc


def probe_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe."""
    proc = run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", path,
        ]
    )
    return float(json.loads(proc.stdout)["format"]["duration"])


def probe_dims(path: str) -> tuple[int, int]:
    """Return (width, height) of the first video stream via ffprobe."""
    proc = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path,
    ])
    st = json.loads(proc.stdout)["streams"][0]
    return int(st["width"]), int(st["height"])


def extract_wav(video_path: str, wav_path: str) -> str:
    """Extract 16kHz mono PCM wav (what whisper wants) from any media."""
    run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path,
    ])
    return wav_path


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source.strip(), re.I))


def slugify(text: str, maxlen: int = 50) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.U).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return (text[:maxlen].strip("-")) or "clip"


def fmt_ts(seconds: float) -> str:
    """Seconds -> H:MM:SS.cs for ASS timestamps.

    NOTE: on arrondit d'abord les centisecondes à 2 chiffres max (0-99).
    Si l'arrondi donne 100, on incrémente la seconde (évite '07.100').
    """
    total_cs = int(round(seconds * 100))   # centisecondes totales entières
    cs = total_cs % 100
    s  = (total_cs // 100) % 60
    m  = (total_cs // 6000) % 60
    h  =  total_cs // 360000
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"
