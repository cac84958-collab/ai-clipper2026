"""Render each selected clip: cut, reframe to 9:16, burn animated captions."""
from __future__ import annotations

import os
import shutil

from .util import fmt_ts, log, run, slugify

FONT_FILE = os.path.join(os.path.dirname(__file__), "assets", "Montserrat-ExtraBold.ttf")
FONT_NAME = "Montserrat ExtraBold"

# TikTok / CapCut style : mot actif en jaune vif, texte blanc MAJUSCULES,
# outline noir très épais, positionné en bas de cadre. ASS couleurs = &HAABBGGRR.
_YELLOW = "&H0000FFFF&"   # jaune vif pour le mot en cours (karaoke highlight)
_WHITE  = "&H00FFFFFF&"   # blanc pour les mots inactifs
_BLACK  = "&H00000000&"   # noir pour l'outline
_BACK   = "&HC0000000&"   # noir semi-transparent (drop shadow discret)

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{FONT_NAME},115,{_WHITE},{_YELLOW},{_BLACK},{_BACK},-1,0,0,0,100,100,2,0,1,9,0,2,60,60,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _esc(word: str) -> str:
    return word.replace("\\", "").replace("{", "(").replace("}", ")")


# Tous les caractères apostrophe possibles (ASCII, Unicode typographique, backtick)
_APOSTROPHES = set("'\u2019\u02bc\u0060\u2018\u02b9")


def _merge_elisions(words: list[dict]) -> list[dict]:
    """Glue French elisions back together: Whisper splits "c'est" into "c" + "'est".

    On fusionne le token courant avec le précédent quand :
    - Le token courant commence par une apostrophe (ex: 'est → c'est)
    - Le token précédent se termine par une apostrophe (ex: c' → c'est)
    - Le token courant est une ponctuation seule (, . ! ? ;) — on la colle
    """
    out: list[dict] = []
    for w in words:
        tok = w["word"].strip()
        if not tok:
            continue
        if out:
            prev = out[-1]
            # Apostrophe en début du token courant OU en fin du précédent
            starts_with_apos = tok[0] in _APOSTROPHES
            ends_with_apos   = prev["word"][-1] in _APOSTROPHES
            # Ponctuation seule qui doit coller au mot précédent
            is_punct_only    = tok in (",", ".", "!", "?", ";", ":", "...")
            if starts_with_apos or ends_with_apos or is_punct_only:
                prev["word"] += tok
                prev["end"]   = w["end"]
                continue
        out.append({"start": w["start"], "end": w["end"], "word": tok})
    return out


def _can_break_before(word: str) -> bool:
    """Renvoie True si on peut commencer un nouveau groupe AVANT ce mot.

    On ne coupe jamais juste avant une apostrophe (sinon on recrée le bug
    "c" → "'est de croiser").
    """
    return word and word[0] not in _APOSTROPHES


def _group_words(in_clip, group_size, max_gap):
    """Découpe les mots en phrases courtes en respectant :
    - La taille max (group_size mots)
    - Les pauses de parole (max_gap secondes)
    - L'interdiction de couper AVANT une apostrophe
    """
    groups, cur = [], []
    for w in in_clip:
        if cur and _can_break_before(w["word"]):
            # On coupe si : taille max atteinte OU pause de parole
            if len(cur) >= group_size or w["start"] - cur[-1]["end"] > max_gap:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], clip_start: float, clip_end: float,
              group_size: int = 4, max_gap: float = 0.5) -> str:
    """Sous-titres style TikTok/CapCut : phrases courtes en MAJUSCULES, mot
    actif surligné en jaune, les autres en blanc. Un Dialogue par mot parlé.

    group_size=4 → 4 mots max par ligne (meilleure lisibilité sur vertical)
    max_gap=0.5 → on coupe aussi en cas de pause ≥ 0.5 s
    """
    in_clip = [w for w in words if w["start"] >= clip_start - 0.05
               and w["end"] <= clip_end + 0.05]
    in_clip = _merge_elisions(in_clip)
    lines = [ASS_HEADER]
    for group in _group_words(in_clip, group_size, max_gap):
        toks = [_esc(w["word"]).upper() for w in group]
        for i in range(len(group)):
            start = group[i]["start"] - clip_start
            end = (group[i + 1]["start"] - clip_start) if i + 1 < len(group) \
                else (group[i]["end"] - clip_start)
            if end <= start:
                end = start + 0.05
            # Mot actif = jaune, mots inactifs = blanc
            parts = [
                f"{{\\1c{_YELLOW}}}{t}{{\\1c{_WHITE}}}" if j == i else t
                for j, t in enumerate(toks)
            ]
            lines.append(
                f"Dialogue: 0,{fmt_ts(start)},{fmt_ts(end)},Pop,,0,0,0,,{' '.join(parts)}"
            )
    return "\n".join(lines) + "\n"


def _vfilter(layout: str) -> str:
    """Filtergraph that turns any input into a 1080x1920 vertical frame."""
    if layout == "blur":
        return (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=24:2[bg];"
            "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    # default: center-crop to 9:16 then scale up
    return "crop=ih*9/16:ih,scale=1080:1920"


def _track_chain(source_video: str, clip: dict, out_dir: str, name: str) -> str | None:
    """AI reframe: a sendcmd-driven crop that pans to follow the speaker.

    Returns the filtergraph string, or None to fall back to a center crop.
    """
    from . import reframe
    from .util import probe_dims

    w, h = probe_dims(source_video)
    traj = reframe.track(source_video, clip["start"], clip["end"], w, h)
    if not traj:
        return None

    cw, ch = reframe.crop_dims(w, h)
    cmd_name = f"{name}.cmd"
    init_x = traj[0][1]
    with open(os.path.join(out_dir, cmd_name), "w", encoding="utf-8") as fh:
        fh.write("".join(f"{t:.3f} crop x {x:.1f};\n" for t, x in traj))
    # run with cwd=out_dir so sendcmd/subtitles get bare filenames
    return (
        f"sendcmd=f={cmd_name},"
        f"crop=w={cw}:h={ch}:x={init_x:.1f}:y=0,"
        f"scale=1080:1920"
    )


def _ffmpeg_cmd(source_video, start, dur, chain, use_complex, out_name) -> list[str]:
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", source_video, "-t", f"{dur:.3f}"]
    cmd += (["-filter_complex", chain] if use_complex else ["-vf", chain])
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out_name,
    ]
    return cmd


def render_clip(
    source_video: str,
    clip: dict,
    words: list[dict],
    out_dir: str,
    index: int,
    layout: str = "track",
    captions: bool = True,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    name = f"{index:02d}-{slugify(clip['title'])}"
    ass_name = f"{name}.ass"
    out_name = f"{name}.mp4"
    start, dur = clip["start"], clip["duration"]

    cap_suffix = ""
    if captions:
        ass_text = build_ass(words, clip["start"], clip["end"])
        with open(os.path.join(out_dir, ass_name), "w", encoding="utf-8") as fh:
            fh.write(ass_text)
        # copy the bundled font next to the .ass so libass loads it (sidesteps
        # Windows path escaping in the subtitles filter; cwd is out_dir)
        if os.path.isfile(FONT_FILE):
            dst = os.path.join(out_dir, os.path.basename(FONT_FILE))
            if not os.path.isfile(dst):
                shutil.copyfile(FONT_FILE, dst)
        cap_suffix = f",subtitles={ass_name}:fontsdir=."

    track_chain = _track_chain(source_video, clip, out_dir, name) if layout == "track" else None
    if track_chain is not None:
        chain, use_complex, mode = track_chain + cap_suffix, False, "AI reframe"
    else:
        base = "blur" if layout == "blur" else "crop"
        chain, use_complex, mode = _vfilter(base) + cap_suffix, base == "blur", base

    log(f"Rendering clip {index} ({mode}): {clip['title'][:50]!r} ({dur:.0f}s)")
    cmd = _ffmpeg_cmd(source_video, start, dur, chain, use_complex, out_name)
    try:
        run(cmd, cwd=out_dir)
    except RuntimeError:
        if track_chain is None:
            raise
        log("AI reframe render failed -> retrying with center crop.")
        chain = _vfilter("crop") + cap_suffix
        run(_ffmpeg_cmd(source_video, start, dur, chain, False, out_name), cwd=out_dir)
    return os.path.join(out_dir, out_name)
