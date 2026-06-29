"""Pick the best short-clip segments from a transcript.

Uses a local OpenAI-compatible LLM (Ollama / LM Studio) when reachable,
otherwise falls back to a simple heuristic so the tool always produces output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

from .util import log

SYSTEM_PROMPT = (
    "You are an expert short-form video editor for TikTok, Reels and YouTube "
    "Shorts. You are given a timestamped transcript of a long video. Select the "
    "self-contained moments that would make the best standalone vertical clips: "
    "a strong hook in the first seconds, a complete thought, and something "
    "surprising, emotional, funny or insightful. Avoid mid-sentence cuts."
)


@dataclass
class LLM:
    base_url: str
    model: str
    label: str


# ----------------------------------------------------------------------------
# LLM discovery
# ----------------------------------------------------------------------------
def discover_llm(choice: str = "auto", model: str | None = None) -> LLM | None:
    """Return a reachable LLM endpoint, or None to use the heuristic.

    choice: 'auto' | 'none' | 'ollama' | 'lmstudio' | a full base_url.
    """
    if choice == "none":
        return None

    candidates: list[tuple[str, str]] = []
    if choice in ("auto", "ollama"):
        candidates.append(("ollama", "http://localhost:11434/v1"))
    if choice in ("auto", "lmstudio"):
        candidates.append(("lmstudio", "http://localhost:1234/v1"))
    if choice.startswith("http"):
        candidates.append(("custom", choice.rstrip("/")))

    for label, base in candidates:
        try:
            r = requests.get(f"{base}/models", timeout=2)
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]
        except Exception:
            continue
        if not ids:
            continue
        picked = model or _prefer_model(ids)
        log(f"Using local LLM via {label}: {picked}")
        return LLM(base_url=base, model=picked, label=label)

    log("No local LLM reachable -> falling back to heuristic selection.")
    return None


def _prefer_model(ids: list[str]) -> str:
    """Prefer a capable instruct chat model when several are installed."""
    ranked = ("llama3.1", "llama3", "qwen2.5", "qwen", "mistral", "gemma2", "phi")
    for key in ranked:
        for mid in ids:
            if key in mid.lower():
                return mid
    return ids[0]


# ----------------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------------
def analyze(
    transcript: dict,
    title: str,
    n: int = 3,
    min_dur: float = 15.0,
    max_dur: float = 60.0,
    llm: LLM | None = None,
) -> list[dict]:
    segments = transcript["segments"]
    total = transcript["duration"]
    if not segments:
        return []

    raw: list[dict] = []
    if llm is not None:
        try:
            raw = _llm_pick(segments, title, n, min_dur, max_dur, llm)
        except Exception as exc:  # never let a flaky local model kill the run
            log(f"LLM selection failed ({exc}); using heuristic instead.")
    if not raw:
        raw = _heuristic_pick(segments, n, min_dur, max_dur)

    clips = _finalize(raw, transcript, n, min_dur, max_dur, total)
    return clips


# ----------------------------------------------------------------------------
# LLM path
# ----------------------------------------------------------------------------
def _llm_pick(segments, title, n, min_dur, max_dur, llm: LLM) -> list[dict]:
    chunks = list(_chunk(segments, max_chars=14000))
    candidates: list[dict] = []
    for idx, chunk in enumerate(chunks, 1):
        log(f"LLM analysing transcript block {idx}/{len(chunks)} "
            f"(this can take a minute on a long video)...")
        body = "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in chunk)
        want = max(n, 4)
        user = (
            f'Video title: "{title}"\n\n'
            f"Transcript (each line is [start_seconds] text):\n{body}\n\n"
            f"Select up to {want} clips. Each clip must last between "
            f"{int(min_dur)} and {int(max_dur)} seconds. Respond ONLY with a JSON "
            'array, no prose. Each item:\n'
            '{"start": <sec>, "end": <sec>, "title": "<catchy <=60 chars>", '
            '"score": <0-100 virality>, "reason": "<short why>", '
            '"hashtags": ["#tag", ...]}'
        )
        candidates.extend(_chat_json(llm, user))
    return candidates


def _chat_json(llm: LLM, user: str) -> list[dict]:
    payload = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "stream": False,
    }
    r = requests.post(
        f"{llm.base_url}/chat/completions", json=payload, timeout=600
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return _parse_json_array(content)


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict) and "start" in d and "end" in d]


# ----------------------------------------------------------------------------
# Heuristic path (no LLM)
# ----------------------------------------------------------------------------
HOOK_WORDS = re.compile(
    r"\b(secret|jamais|toujours|incroyable|erreur|argent|pourquoi|comment|"
    r"never|always|secret|money|mistake|why|how|best|worst|huge|crazy)\b", re.I
)


def _heuristic_pick(segments, n, min_dur, max_dur) -> list[dict]:
    """Build windows at sentence boundaries and score them simply."""
    windows = []
    i = 0
    while i < len(segments):
        start = segments[i]["start"]
        j = i
        while j < len(segments) and segments[j]["end"] - start < max_dur:
            if segments[j]["end"] - start >= min_dur:
                break
            j += 1
        end = segments[min(j, len(segments) - 1)]["end"]
        if end - start >= min_dur:
            text = " ".join(s["text"] for s in segments[i : j + 1])
            wps = len(text.split()) / max(end - start, 1)
            score = 40 + min(40, wps * 8) + (10 if "?" in text else 0)
            score += 10 if HOOK_WORDS.search(text) else 0
            windows.append({
                "start": start, "end": end,
                "title": text[:55].strip(), "score": int(min(score, 95)),
                "reason": "Auto-selected (no LLM): dense, self-contained segment.",
                "hashtags": ["#shorts", "#clip"],
            })
        i = j + 1
    windows.sort(key=lambda w: w["score"], reverse=True)
    return windows


# ----------------------------------------------------------------------------
# Shared post-processing
# ----------------------------------------------------------------------------
def _chunk(segments, max_chars: int):
    chunk, size = [], 0
    for s in segments:
        chunk.append(s)
        size += len(s["text"]) + 12
        if size >= max_chars:
            yield chunk
            chunk, size = [], 0
    if chunk:
        yield chunk


def _finalize(raw, transcript, n, min_dur, max_dur, total) -> list[dict]:
    """Snap to word boundaries, enforce duration, drop overlaps, rank, trim."""
    words = transcript["words"]
    cleaned = []
    for c in raw:
        try:
            start = max(0.0, float(c["start"]))
            end = min(total, float(c["end"]))
        except (TypeError, ValueError, KeyError):
            continue
        if end <= start:
            continue
        start, end = _snap(start, end, words, min_dur, max_dur, total)
        cleaned.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(end - start, 2),
            "title": str(c.get("title") or "Clip").strip()[:80],
            "score": int(c.get("score", 50)),
            "reason": str(c.get("reason", "")).strip(),
            "hashtags": [str(h) for h in (c.get("hashtags") or [])][:6],
        })

    cleaned.sort(key=lambda c: c["score"], reverse=True)
    picked: list[dict] = []
    for c in cleaned:
        if all(c["end"] <= p["start"] or c["start"] >= p["end"] for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    picked.sort(key=lambda c: c["start"])
    return picked


def _snap(start, end, words, min_dur, max_dur, total):
    """Nudge boundaries to the nearest word edges and clamp the duration."""
    if words:
        starts = [w["start"] for w in words]
        ends = [w["end"] for w in words]
        start = min(starts, key=lambda t: abs(t - start))
        end = min(ends, key=lambda t: abs(t - end))
    if end - start < min_dur:
        end = min(total, start + min_dur)
    if end - start > max_dur:
        end = start + max_dur
    return start, end
