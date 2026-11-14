"""Decides what to upgrade, so the player does not have to.

The hard part of auto-upgrading is not tapping -- it is knowing *what* you are
tapping. Three pieces solve that without hard-coded coordinates:

1. **Find the candidates.** The game puts a green arrow bubble over every
   building it will let you upgrade. Those bubbles are matched either against a
   captured template or, with no template at all, by a colour-and-shape mask.
2. **Read the card.** After the tap, EasyOCR reads the popup. The building name
   is matched against a vocabulary of real CoC names, and the price is pulled
   from the same text -- so the button does not need a fixed position.
3. **Rank.** A tier table (offence first, walls last) decides which of the
   affordable options is worth the builder.

Everything in this module is pure logic over data, which is why it is the part
that gets unit tests instead of "trust me".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None
    np = None

from utils import parse_loot_number

# --------------------------------------------------------------------------
# What is worth upgrading
# --------------------------------------------------------------------------
# tier 1 is the best use of a builder. The order is the usual advice: offence
# and heroes before defence, storages early enough that loot stops overflowing,
# walls never (they eat everything and give nothing back early).
#
# Each entry: (canonical name, tier, [aliases as OCR might read them])
TIERS: list[tuple[str, int, list[str]]] = [
    # --- offence: what actually wins attacks -------------------------------
    ("Town Hall", 1, ["town hall", "townhall"]),
    ("Laboratory", 1, ["laboratory", "lab"]),
    ("Army Camp", 1, ["army camp"]),
    ("Barracks", 1, ["barracks"]),
    ("Dark Barracks", 1, ["dark barracks"]),
    ("Spell Factory", 1, ["spell factory"]),
    ("Dark Spell Factory", 1, ["dark spell factory"]),
    ("Clan Castle", 1, ["clan castle"]),
    ("Workshop", 1, ["workshop"]),
    ("Pet House", 1, ["pet house"]),
    ("Blacksmith", 1, ["blacksmith"]),
    # --- heroes ------------------------------------------------------------
    ("Barbarian King", 2, ["barbarian king"]),
    ("Archer Queen", 2, ["archer queen"]),
    ("Grand Warden", 2, ["grand warden"]),
    ("Royal Champion", 2, ["royal champion"]),
    ("Minion Prince", 2, ["minion prince"]),
    # --- storages: loot you cannot hold is loot you lose --------------------
    ("Gold Storage", 3, ["gold storage"]),
    ("Elixir Storage", 3, ["elixir storage"]),
    ("Dark Elixir Storage", 3, ["dark elixir storage"]),
    # --- collectors: only worth it when they are the bottleneck ------------
    ("Gold Mine", 4, ["gold mine"]),
    ("Elixir Collector", 4, ["elixir collector"]),
    ("Dark Elixir Drill", 4, ["dark elixir drill"]),
    # --- heavy defence ------------------------------------------------------
    ("Inferno Tower", 5, ["inferno tower"]),
    ("Eagle Artillery", 5, ["eagle artillery"]),
    ("Scattershot", 5, ["scattershot", "scatter shot"]),
    ("Monolith", 5, ["monolith"]),
    ("X-Bow", 5, ["x-bow", "xbow", "x bow"]),
    ("Multi-Archer Tower", 5, ["multi-archer", "multi archer"]),
    ("Ricochet Cannon", 5, ["ricochet cannon"]),
    ("Multi-Gear Tower", 5, ["multi-gear", "multi gear"]),
    ("Firespitter", 5, ["firespitter", "fire spitter"]),
    # --- standard defence ---------------------------------------------------
    ("Air Defense", 6, ["air defense", "air defence"]),
    ("Wizard Tower", 6, ["wizard tower"]),
    ("Hidden Tesla", 6, ["hidden tesla", "tesla"]),
    ("Bomb Tower", 6, ["bomb tower"]),
    ("Archer Tower", 6, ["archer tower"]),
    ("Cannon", 6, ["cannon"]),
    ("Mortar", 6, ["mortar"]),
    # --- traps --------------------------------------------------------------
    ("Bomb", 7, ["bomb"]),
    ("Spring Trap", 7, ["spring trap"]),
    ("Air Bomb", 7, ["air bomb"]),
    ("Giant Bomb", 7, ["giant bomb"]),
    ("Seeking Air Mine", 7, ["seeking air mine"]),
    ("Skeleton Trap", 7, ["skeleton trap"]),
    # --- walls: last, and off by default -----------------------------------
    ("Wall", 9, ["wall", "walls"]),
]

# Aliases sorted longest-first so "dark elixir storage" wins over "elixir
# storage", and "multi archer tower" wins over "archer tower".
_LOOKUP: list[tuple[str, str, int]] = sorted(
    ((alias, canon, tier) for canon, tier, aliases in TIERS for alias in aliases),
    key=lambda row: len(row[0]),
    reverse=True,
)

RESOURCE_WORDS = {
    # "dark" first: it qualifies elixir, and "dark elixir" must not be read as
    # plain elixir -- they are different currencies with different caps.
    "dark": "dark",
    "elixir": "elixir",
    "gold": "gold",
    "gems": "gems",
    "gem": "gems",
}


def normalize(text: str) -> str:
    """Lowercase, keep letters and single spaces: OCR punctuation is noise."""
    cleaned = re.sub(r"[^a-z ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def classify(texts) -> tuple[str | None, int | None]:
    """Map a page of OCR lines to a building name and its tier."""
    best: tuple[int, str, int] | None = None
    for raw in texts:
        norm = normalize(raw)
        if not norm:
            continue
        for alias, canon, tier in _LOOKUP:
            if alias in norm:
                if best is None or len(alias) > best[0]:
                    best = (len(alias), canon, tier)
                break
    if best is None:
        return None, None
    return best[1], best[2]


def find_name_box(boxes) -> tuple[str, int, tuple[int, int, int, int]] | None:
    """Pick the OCR box most likely to be the card title, plus its rect."""
    best = None
    for text, rect, _conf in boxes:
        norm = normalize(text)
        if not norm or len(norm) > 40:
            continue
        canon, tier = classify([text])
        if canon is None:
            continue
        key = len(norm)
        if best is None or key > best[0]:
            best = (key, canon, tier, rect)
    if best is None:
        return None
    return best[1], best[2], best[3]


def find_box_with(boxes, words, max_y: int | None = None) -> tuple[int, int, int, int] | None:
    """First OCR box whose text contains any of `words` (case-insensitive)."""
    for text, rect, _conf in boxes:
        norm = normalize(text)
        if max_y is not None and rect[1] > max_y:
            continue
        for word in words:
            if word in norm:
                return rect
    return None


@dataclass
class Cost:
    amount: int = 0
    resource: str = "unknown"

    def __bool__(self):
        return self.amount > 0


def read_cost(boxes) -> Cost:
    """Find the price on the card.

    The price is the biggest number sitting near the Upgrade button; other
    numbers on the card are level, hitpoints or damage. Anchoring on the button
    instead of taking the largest number on the page is what stops a maxed
    building's hitpoints from being read as its cost.
    """
    resource = "unknown"
    for text, _rect, _conf in boxes:
        norm = normalize(text)
        hit = next((kind for word, kind in RESOURCE_WORDS.items() if word in norm), None)
        if hit:
            resource = hit
            break

    numbers: list[tuple[int, tuple[int, int, int, int]]] = []
    for text, rect, _conf in boxes:
        for token in re.findall(r"[\d][\d.,]*\s*[kmb]?", (text or "").lower()):
            value = parse_loot_number(token)
            amount = int(value) if value.isdigit() else 0
            # A price is at least four digits; smaller numbers on the card are
            # levels, hitpoints or the "level 5" label.
            if amount >= 1000:
                numbers.append((amount, rect))

    if not numbers:
        return Cost(0, resource)

    button = find_box_with(boxes, ["upgrade"])
    if button is not None:
        button_cy = (button[1] + button[3]) / 2
        reach = max(40.0, (button[3] - button[1]) * 3.5)
        near = [pair for pair in numbers
                if abs((pair[1][1] + pair[1][3]) / 2 - button_cy) <= reach]
        if near:
            return Cost(max(near, key=lambda pair: pair[0])[0], resource)

    return Cost(max(numbers, key=lambda pair: pair[0])[0], resource)


@dataclass
class Candidate:
    name: str
    tier: int
    cost: Cost
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    meta: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Higher is better. Tier dominates; a cheaper card wins a tie."""
        base = (10 - self.tier) * 100.0
        if self.cost.amount:
            base -= min(40.0, self.cost.amount / 250_000.0)
        return base

    def describe(self) -> str:
        price = f"{self.cost.amount:,} {self.cost.resource}" if self.cost else "price unknown"
        return f"{self.name} (tier {self.tier}, {price})"


DEFAULT_CFG = {
    "allow_walls": False,
    "min_tier": 1,
    "max_tier": 7,
    "max_cost": {},          # e.g. {"gold": 8000000}
    "dry_run": False,
    "max_tries": 4,
}


def accept(cand: Candidate, resources: dict | None, cfg: dict) -> tuple[bool, str]:
    """Should this candidate get the builder?"""
    if not cfg.get("allow_walls", False) and cand.name == "Wall":
        return False, "walls are disabled"
    if cand.tier < cfg.get("min_tier", 1):
        return False, f"tier {cand.tier} below minimum"
    if cand.tier > cfg.get("max_tier", 7):
        return False, f"tier {cand.tier} above maximum"

    cap = (cfg.get("max_cost") or {}).get(cand.cost.resource)
    if cap and cand.cost.amount > cap:
        return False, f"cost {cand.cost.amount:,} over cap {cap:,}"

    if resources and cand.cost.amount and cand.cost.resource in resources:
        have = resources.get(cand.cost.resource) or 0
        if have and cand.cost.amount > have:
            return False, f"needs {cand.cost.amount:,} {cand.cost.resource}, have {have:,}"

    return True, "accepted"


def choose(candidates: list[Candidate], resources: dict | None, cfg: dict) -> tuple[Candidate | None, str]:
    """Best candidate, or None with a reason that says why nothing was picked."""
    if not candidates:
        return None, "nothing to upgrade"
    rejected: list[str] = []
    for cand in sorted(candidates, key=lambda c: c.score, reverse=True):
        ok, why = accept(cand, resources, cfg)
        if ok:
            return cand, f"{cand.describe()} — {why}"
        rejected.append(f"{cand.name}: {why}")
    return None, "all rejected — " + "; ".join(rejected[:4])


# --------------------------------------------------------------------------
# Finding the green arrow bubbles
# --------------------------------------------------------------------------

def _dedupe(boxes, min_gap: int = 24):
    out: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda b: b[4] if len(b) > 4 else 0, reverse=True):
        x, y, w, h = box[:4]
        cx, cy = x + w / 2, y + h / 2
        if all(abs(cx - (o[0] + o[2] / 2)) > min_gap or abs(cy - (o[1] + o[3] / 2)) > min_gap
               for o in out):
            out.append((x, y, w, h))
    return out


def find_indicators(frame, template=None, threshold: float = 0.80,
                    max_hits: int = 12) -> list[tuple[int, int, int, int]]:
    """Green upgrade bubbles on the village, as (x, y, w, h) rects.

    A captured template is used when one exists; otherwise the colour mask is
    the fallback, which is why this works on a fresh install with no setup.
    """
    if cv2 is None or frame is None or getattr(frame, "size", 0) == 0:
        return []

    if template is not None:
        import templates as TPL
        hits = TPL.find_all(frame, template, threshold=threshold, max_hits=max_hits)
        if hits:
            return _dedupe(hits)

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # The bubble is a saturated, bright green. Two bands because the highlight
    # on the icon pushes some pixels toward yellow.
    mask = cv2.inRange(hsv, (40, 110, 110), (85, 255, 255))
    mask |= cv2.inRange(hsv, (30, 140, 150), (45, 255, 255))

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = frame.shape[:2]
    total = float(width * height)
    found: list[tuple[int, int, int, int]] = []

    for contour in contours:
        area = cv2.contourArea(contour)
        if not (40 <= area <= total * 0.01):
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w < 10 or h < 10:
            continue
        aspect = w / float(h)
        if not 0.65 <= aspect <= 1.5:
            continue
        # A ring reads as a bubble; a big flat green area (grass, a level bar)
        # does not.
        found.append((x, y, w, h))

    return _dedupe(found)[:max_hits]


def read_resources(boxes, height: int) -> dict:
    """Resource totals from the top HUD, best effort.

    Returns {} when the OCR pass did not see the bar, which callers treat as
    "unknown" rather than "zero" -- the game is then the authority on whether
    an upgrade is affordable.
    """
    out: dict[str, int] = {}
    for text, rect, _conf in boxes:
        if rect[1] > height * 0.22:
            continue
        norm = normalize(text)
        kind = None
        for word, k in RESOURCE_WORDS.items():
            if word in norm:
                kind = k
                break
        if kind is None:
            continue
        digits = re.findall(r"\d[\d.,]*\s*[kmb]?", text.lower())
        for token in digits:
            value = parse_loot_number(token)
            if value.isdigit() and int(value) > 0:
                out[kind] = int(value)
                break
    return out
