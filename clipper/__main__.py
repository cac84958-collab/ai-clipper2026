"""CLI: turn a long video (file or URL) into vertical short clips.

Usage:
    uv run python -m clipper <video-or-url> [options]
"""
from __future__ import annotations

import argparse
import sys

from . import pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clipper",
        description="Local OpusClip-style clip generator (file or YouTube URL).",
    )
    p.add_argument("source", help="path to a video file OR a YouTube/web URL")
    p.add_argument("-n", "--clips", type=int, default=3, help="number of clips")
    p.add_argument("--min", type=float, default=15.0, help="min clip seconds")
    p.add_argument("--max", type=float, default=60.0, help="max clip seconds")
    p.add_argument("--model", default="small",
                   help="whisper size: tiny|base|small|medium|large-v3")
    p.add_argument("--lang", default=None, help="force language, e.g. fr (auto if unset)")
    p.add_argument("--layout", choices=["track", "crop", "blur"], default="track",
                   help="track = AI reframe follows the speaker (9:16); "
                        "crop = fixed center crop; blur = blurred background")
    p.add_argument("--no-captions", action="store_true", help="disable subtitles")
    p.add_argument("--llm", default="auto",
                   help="auto|none|ollama|lmstudio|<base_url>")
    p.add_argument("--llm-model", default=None, help="force a local model name")
    p.add_argument("--out", default="output", help="output directory")
    p.add_argument("--keep-temp", action="store_true", help="keep working files")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    result = pipeline.run(
        args.source,
        out_root=args.out,
        n=args.clips,
        min_dur=args.min,
        max_dur=args.max,
        model=args.model,
        lang=args.lang,
        layout=args.layout,
        captions=not args.no_captions,
        llm=args.llm,
        llm_model=args.llm_model,
        keep_temp=args.keep_temp,
    )
    if not result["clips"]:
        return 1
    _summary(result["out_dir"], result["clips"])
    return 0


def _summary(out_dir: str, clips: list[dict]) -> None:
    print("\n" + "=" * 60)
    print(f"Done. {len(clips)} clip(s) in {out_dir}")
    print("=" * 60)
    for i, c in enumerate(clips, 1):
        print(f"\n#{i}  [score {c['score']}/100]  {c['duration']:.0f}s  -> {c['file']}")
        print(f"    {c['title']}")
        if c.get("hashtags"):
            print(f"    {' '.join(c['hashtags'])}")


if __name__ == "__main__":
    sys.exit(main())
