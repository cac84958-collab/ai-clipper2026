"""The end-to-end clip pipeline as one reusable call (used by the CLI and web)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from . import analyze, download, render, transcribe
from .util import log, slugify


def run(
    source: str,
    *,
    out_root: str = "output",
    n: int = 3,
    min_dur: float = 15.0,
    max_dur: float = 60.0,
    model: str = "small",
    lang: str | None = None,
    layout: str = "track",
    captions: bool = True,
    llm: str = "auto",
    llm_model: str | None = None,
    keep_temp: bool = False,
) -> dict:
    """Run download -> transcribe -> select -> render and write clips.json.

    Returns {"title", "out_dir", "clips"}. `clips` is empty if nothing usable.
    """
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="clipper-")

    try:
        src = download.fetch(source, workdir)
        project = slugify(src["title"])
        out_dir = os.path.join(out_root, project)
        os.makedirs(out_dir, exist_ok=True)
        log(f"Source: {src['title']!r}")

        tr = transcribe.transcribe(src["video"], workdir, model_size=model, lang=lang)
        if not tr["segments"]:
            log("No speech detected; nothing to clip.")
            return {"title": src["title"], "out_dir": out_dir, "clips": []}

        llm_obj = analyze.discover_llm(llm, llm_model)
        clips = analyze.analyze(
            tr, src["title"], n=n, min_dur=min_dur, max_dur=max_dur, llm=llm_obj
        )
        if not clips:
            log("Could not identify any suitable clip.")
            return {"title": src["title"], "out_dir": out_dir, "clips": []}
        log(f"Selected {len(clips)} clip(s).")

        for i, clip in enumerate(clips, 1):
            path = render.render_clip(
                src["video"], clip, tr["words"], out_dir, i,
                layout=layout, captions=captions,
            )
            clip["file"] = os.path.basename(path)

        with open(os.path.join(out_dir, "clips.json"), "w", encoding="utf-8") as fh:
            json.dump(clips, fh, ensure_ascii=False, indent=2)
        return {"title": src["title"], "out_dir": out_dir, "clips": clips}

    finally:
        if not keep_temp:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            log(f"Kept working files in {workdir}")
