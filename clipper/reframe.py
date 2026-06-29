"""AI Reframe: follow the subject when cropping to vertical 9:16.

Instead of a fixed center crop, detect faces (OpenCV Haar cascade) and, when
several people are on screen, pick the active speaker with a simple mouth-motion
heuristic. The result is a smoothed horizontal pan trajectory that the renderer
feeds to ffmpeg's `crop` filter via `sendcmd`.

Fully local / offline: the Haar cascade ships inside the opencv wheel; nothing is
downloaded at runtime. Degrades gracefully -- if OpenCV is missing, the source is
already 9:16, or no face is ever found, `track()` returns None and the caller
falls back to a static center crop.
"""
from __future__ import annotations

from .util import log


def crop_dims(src_w: int, src_h: int) -> tuple[int, int]:
    """9:16 crop window for a source frame: full height, narrower width (even px)."""
    cw = min(src_w, round(src_h * 9 / 16))
    ch = src_h
    return cw - (cw % 2), ch - (ch % 2)


def track(
    video_path: str,
    clip_start: float,
    clip_end: float,
    src_w: int,
    src_h: int,
    fps: float = 3.0,
) -> list[tuple[float, float]] | None:
    """Return crop keyframes [(t_rel, x_left), ...] for the clip, or None.

    t_rel is seconds from the clip start; x_left is the left edge of the 9:16
    crop window in source pixels. None means "can't track -> use center crop".
    """
    try:
        import cv2
    except Exception:
        log("OpenCV not installed -> static center crop.")
        return None

    cw, ch = crop_dims(src_w, src_h)
    if cw >= src_w:  # source is already 9:16 or taller; nothing to pan
        return None

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    if cascade.empty():
        log("Haar cascade unavailable -> static center crop.")
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    sample_every = max(1, int(round(src_fps / max(1.0, fps))))
    min_face = max(24, src_h // 16)
    cap.set(cv2.CAP_PROP_POS_MSEC, clip_start * 1000.0)

    raw: list[tuple[float, float | None]] = []
    prev_gray = None
    prev_center = src_w / 2.0
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t_abs = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t_abs > clip_end + 1e-3:
            break
        if i % sample_every == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5,
                minSize=(min_face, min_face),
            )
            cx = _choose_face(faces, gray, prev_gray, prev_center)
            if cx is not None:
                prev_center = cx
            raw.append((max(0.0, t_abs - clip_start), cx))
            prev_gray = gray
        i += 1
    cap.release()

    if not raw or all(cx is None for _, cx in raw):
        return None
    return _smooth(raw, cw, src_w)


def _choose_face(faces, gray, prev_gray, prev_center: float) -> float | None:
    """Horizontal center of the face to follow, or None if no face."""
    import cv2
    import numpy as np

    if len(faces) == 0:
        return None
    if len(faces) == 1 or prev_gray is None:
        x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
        return float(x + w / 2.0)

    # Several faces: the active speaker is the one whose mouth moves most.
    width = gray.shape[1]
    best_cx, best_score = None, -1.0
    for (x, y, w, h) in faces:
        my0, my1 = y + int(h * 0.55), y + h
        mx0, mx1 = x + int(w * 0.2), x + int(w * 0.8)
        a, b = gray[my0:my1, mx0:mx1], prev_gray[my0:my1, mx0:mx1]
        motion = float(np.mean(cv2.absdiff(a, b))) if a.size and a.shape == b.shape else 0.0
        cx = x + w / 2.0
        proximity = 1.0 / (1.0 + abs(cx - prev_center) / width)
        score = motion * 3.0 + (w * h) ** 0.5 * 0.02 + proximity * 2.0
        if score > best_score:
            best_score, best_cx = score, float(cx)
    return best_cx


def _smooth(raw, cw: int, src_w: int, alpha: float = 0.3):
    """Hold over gaps, exponentially smooth, clamp the window inside the frame."""
    centers, last = [], src_w / 2.0
    for t, cx in raw:
        if cx is None:
            cx = last
        last = cx
        centers.append((t, cx))

    out, sm = [], centers[0][1]
    for t, cx in centers:
        sm += alpha * (cx - sm)
        x_left = min(max(0.0, sm - cw / 2.0), float(src_w - cw))
        out.append((round(t, 3), round(x_left, 1)))
    return out
