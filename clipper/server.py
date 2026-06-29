"""Local web UI for ai-clipper: paste a URL or upload a video -> get clips.

A single background worker runs jobs one at a time (whisper is CPU-heavy), while
the browser polls /api/jobs/{id} for live logs and, when done, the clip cards.

Run it:
    uv run python -m clipper.server          # then open http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import queue
import threading
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from .util import set_log_sink

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_ROOT = os.path.join(ROOT, "output")
UPLOAD_DIR = os.path.join(ROOT, "web_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@dataclass
class Job:
    id: str
    source_label: str
    params: dict
    status: str = "queued"          # queued | running | done | error
    logs: list[str] = field(default_factory=list)
    title: str = ""
    out_dir: str | None = None
    clips: list[dict] = field(default_factory=list)
    error: str | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "source": self.source_label,
            "title": self.title,
            "logs": list(self.logs),
            "clips": [
                {
                    "file": c.get("file"),
                    "title": c.get("title"),
                    "score": c.get("score"),
                    "duration": c.get("duration"),
                    "hashtags": c.get("hashtags", []),
                    "reason": c.get("reason", ""),
                }
                for c in self.clips
            ],
            "error": self.error,
        }


JOBS: dict[str, Job] = {}
WORK: "queue.Queue[str]" = queue.Queue()


def _worker() -> None:
    while True:
        job_id = WORK.get()
        job = JOBS.get(job_id)
        if job is None:
            continue
        job.status = "running"
        set_log_sink(job.logs.append)
        try:
            p = job.params
            result = pipeline.run(
                p["source"],
                out_root=OUT_ROOT,
                n=p["n"],
                min_dur=p["min_dur"],
                max_dur=p["max_dur"],
                model=p["model"],
                lang=p["lang"],
                layout=p["layout"],
                captions=p["captions"],
                llm=p["llm"],
                llm_model=p["llm_model"],
            )
            job.title = result["title"]
            job.out_dir = result["out_dir"]
            job.clips = result["clips"]
            job.status = "done"
            if not job.clips:
                job.error = "Aucun clip exploitable (pas de parole détectée ?)."
        except Exception as exc:  # surface the failure to the UI
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.logs.append(f"[error] {job.error}")
        finally:
            set_log_sink(None)


app = FastAPI(title="ai-clipper")
threading.Thread(target=_worker, daemon=True).start()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/health")
def health() -> dict:
    """Tell the UI whether a local LLM is reachable."""
    from . import analyze
    llm = analyze.discover_llm("auto")
    return {"llm": llm.label if llm else None, "model": llm.model if llm else None}


@app.post("/api/jobs")
async def create_job(
    url: str = Form(""),
    n: int = Form(3),
    min_dur: float = Form(15.0),
    max_dur: float = Form(60.0),
    model: str = Form("small"),
    lang: str = Form(""),
    layout: str = Form("track"),
    captions: bool = Form(True),
    use_llm: bool = Form(True),
    llm_model: str = Form(""),
    file: UploadFile | None = File(None),
) -> JSONResponse:
    if file is not None and file.filename:
        # keep the original name (clean project title) under a unique subdir
        sub = os.path.join(UPLOAD_DIR, uuid.uuid4().hex)
        os.makedirs(sub, exist_ok=True)
        dest = os.path.join(sub, os.path.basename(file.filename))
        with open(dest, "wb") as fh:
            while chunk := await file.read(1 << 20):
                fh.write(chunk)
        source, label = dest, file.filename
    elif url.strip():
        source, label = url.strip(), url.strip()
    else:
        raise HTTPException(400, "Donne une URL ou un fichier.")

    job = Job(
        id=uuid.uuid4().hex[:12],
        source_label=label,
        params={
            "source": source,
            "n": max(1, min(int(n), 10)),
            "min_dur": float(min_dur),
            "max_dur": float(max_dur),
            "model": model,
            "lang": lang.strip() or None,
            "layout": layout,
            "captions": bool(captions),
            "llm": "auto" if use_llm else "none",
            "llm_model": llm_model.strip() or None,
        },
    )
    JOBS[job.id] = job
    WORK.put(job.id)
    return JSONResponse({"id": job.id})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job inconnu")
    return job.public()


@app.get("/api/jobs/{job_id}/file/{name}")
def get_file(job_id: str, name: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or not job.out_dir:
        raise HTTPException(404, "job inconnu")
    path = os.path.join(job.out_dir, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(404, "fichier inconnu")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(name))


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
