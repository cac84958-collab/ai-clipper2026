"""Resolve the input source to a local video file (yt-dlp for URLs)."""
from __future__ import annotations

import glob
import json
import os
import sys

from .util import is_url, log, run


def fetch(source: str, workdir: str) -> dict:
    """Return {"video": path, "title": str}. Downloads if source is a URL."""
    if not is_url(source):
        if not os.path.isfile(source):
            raise FileNotFoundError(f"input file not found: {source}")
        title = os.path.splitext(os.path.basename(source))[0]
        return {"video": source, "title": title}

    log(f"Downloading via yt-dlp: {source}")
    out_tmpl = os.path.join(workdir, "source.%(ext)s")
    run([
        sys.executable, "-m", "yt_dlp",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-playlist",
        "-o", out_tmpl,
        source,
    ])

    videos = [
        f for f in glob.glob(os.path.join(workdir, "source.*"))
        if os.path.splitext(f)[1].lower() in (".mp4", ".mkv", ".webm", ".mov")
    ]
    if not videos:
        raise RuntimeError("yt-dlp did not produce a video file")
    video = videos[0]

    title = os.path.splitext(os.path.basename(video))[0]
    info_path = os.path.join(workdir, "source.info.json")
    if os.path.isfile(info_path):
        with open(info_path, encoding="utf-8") as fh:
            title = json.load(fh).get("title", title)
    return {"video": video, "title": title}
