"""Template matching for the things YOLO cannot give us.

The shipped model detects bases, loot numbers and the fixed battle buttons. It
does *not* detect village furniture -- collectors, barracks, the upgrade
hammer, the "full" bubble -- because those sit in the village, not in a battle,
and retraining the model for them would tie every future release to a training
run.

Template matching covers that gap. A 40x40 PNG cropped once from a real
screenshot matches the same icon on every account, at every resolution (after
scaling to the guest's current size), with no model involved.
"""

from __future__ import annotations

import glob
import os

# OpenCV is only needed when a template is actually matched. Keeping the import
# optional lets the task layer (and its tests) run on a machine without the ML
# stack installed -- `pip install -r requirements.txt` is a 2 GB download and
# nobody should need it to check that the scheduler works.
try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None
    np = None

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def available() -> bool:
    return cv2 is not None and np is not None


def ensure_dir() -> str:
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    return TEMPLATE_DIR


def load(path: str) -> np.ndarray | None:
    if cv2 is None or not path or not os.path.isfile(path):
        return None
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    return img


def load_set(pattern: str = "*") -> list[tuple[str, np.ndarray]]:
    """Every template matching `pattern` in the templates directory."""
    if cv2 is None:
        return []
    out = []
    for path in sorted(glob.glob(os.path.join(ensure_dir(), pattern + ".png"))):
        img = load(path)
        if img is not None:
            out.append((os.path.basename(path)[:-4], img))
    return out


def _scaled(template: np.ndarray, frame_w: int) -> list[np.ndarray]:
    """Templates at a couple of sizes around the frame's own scale.

    CoC is rendered at whatever the emulator resolution is, so a template
    captured at 1280x720 has to be resized for a 1920x1080 guest. The scale
    factor is derived from the frame width, and a small band around it absorbs
    the fact that UI icons do not always scale perfectly with the framebuffer.
    """
    th, tw = template.shape[:2]
    if not tw or not th:
        return []
    base = frame_w / 1280.0
    variants = []
    for mult in (1.0, 0.9, 1.1):
        w = max(8, int(round(tw * base * mult)))
        h = max(8, int(round(th * base * mult)))
        if (w, h) == (tw, th):
            variants.append(template)
        else:
            interp = cv2.INTER_AREA if base * mult < 1 else cv2.INTER_CUBIC
            variants.append(cv2.resize(template, (w, h), interpolation=interp))
    return variants


def find_all(frame: np.ndarray, template: np.ndarray, threshold: float = 0.82,
             max_hits: int = 60) -> list[tuple[int, int, int, int]]:
    """All non-overlapping matches as (x, y, w, h), best first."""
    if cv2 is None or frame is None or template is None:
        return []
    frame_h, frame_w = frame.shape[:2]
    hits: list[tuple[float, tuple[int, int, int, int]]] = []

    for tpl in _scaled(template, frame_w):
        th, tw = tpl.shape[:2]
        if th > frame_h or tw > frame_w:
            continue
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        for y, x in zip(ys, xs):
            hits.append((float(res[y, x]), (int(x), int(y), tw, th)))

    hits.sort(key=lambda h: -h[0])
    kept: list[tuple[int, int, int, int]] = []
    for score, box in hits:
        x, y, w, h = box
        cx, cy = x + w / 2.0, y + h / 2.0
        # Suppress duplicates: the same icon found by two scale variants, or two
        # neighbouring peak pixels from one match.
        if any(abs(cx - (kx + kw / 2.0)) < w * 0.6 and abs(cy - (ky + kh / 2.0)) < h * 0.6
               for kx, ky, kw, kh in kept):
            continue
        kept.append(box)
        if len(kept) >= max_hits:
            break
    return kept


def find_best(frame: np.ndarray, template: np.ndarray, threshold: float = 0.82):
    hits = find_all(frame, template, threshold=threshold, max_hits=1)
    return hits[0] if hits else None


def centre(box: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, w, h = box
    return int(x + w / 2), int(y + h / 2)


def save_crop(frame: np.ndarray, box: tuple[int, int, int, int], name: str) -> str:
    """Write one crop into the templates dir (used by capture_template.py)."""
    if cv2 is None:
        raise RuntimeError("OpenCV is required to write templates")
    x, y, w, h = box
    path = os.path.join(ensure_dir(), f"{name}.png")
    cv2.imwrite(path, frame[y:y + h, x:x + w])
    return path
