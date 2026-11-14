"""Making the input look like a person's.

A bot that taps the exact centre of every button, on a perfect metronome, is
trivial to separate from a human on the server side: no human hits (643, 1871)
four hundred times in a row, and no human waits 500ms +/- 0 ms between taps.
This module is the single place that decides "where" and "how long", so every
caller gets the same treatment instead of each one inventing its own jitter.

Two rules drive every number here:

* **Coordinates jitter inside the target, never outside it.** A button is a
  rectangle; a tap 40px off the edge is a miss, and a miss is a lost raid.
  `jitter_point` therefore takes an explicit radius that callers derive from
  the element they are hitting.
* **Delays are log-normal, not uniform.** Human inter-action times are right
  skewed: mostly short, occasionally long. `uniform(0.4, 0.6)` is a rectangle
  and its histogram is a tell all by itself.
"""

from __future__ import annotations

import math
import random
import time


class Humanizer:
    """Deterministic when seeded, random otherwise."""

    def __init__(
        self,
        enabled: bool = True,
        jitter_px: int = 6,
        delay_sigma: float = 0.28,
        pause_chance: float = 0.03,
        pause_range: tuple[float, float] = (0.8, 2.6),
        seed: int | None = None,
    ):
        self.enabled = bool(enabled)
        self.jitter_px = max(0, int(jitter_px))
        self.delay_sigma = max(0.0, min(0.9, float(delay_sigma)))
        self.pause_chance = max(0.0, min(0.5, float(pause_chance)))
        self.pause_range = pause_range
        self._rng = random.Random(seed)

    # ---- coordinates ----------------------------------------------------

    def jitter_point(self, x: float, y: float, radius: int | None = None) -> tuple[int, int]:
        """A point inside a disc of `radius` centred on (x, y).

        Uniform-in-disc rather than uniform-in-square: a square distribution
        puts more mass in the corners, which is measurably not how a finger
        lands on a button.
        """
        r = self.jitter_px if radius is None else max(0, int(radius))
        if not self.enabled or r <= 0:
            return int(round(x)), int(round(y))

        # sqrt(u) gives uniform density over the disc's area.
        angle = self._rng.uniform(0.0, 2.0 * math.pi)
        dist = r * math.sqrt(self._rng.random())
        return int(round(x + math.cos(angle) * dist)), int(round(y + math.sin(angle) * dist))

    def edge_point(self, rect: tuple[int, int, int, int], edge: str | None = None,
                   inset: int = 24) -> tuple[int, int]:
        """A point along one edge of `rect` = (x, y, w, h).

        Troop deployment along a straight, evenly spaced line is the classic
        "deploy signature". Picking a random edge and a random spot along it
        keeps the mass of the army in the same place while breaking the line.
        """
        x, y, w, h = rect
        edges = ["top", "bottom", "left", "right"]
        e = edge if edge in edges else self._rng.choice(edges)

        inset = max(0, min(inset, min(w, h) // 3))
        if e == "top":
            return (self._rng.randint(x + inset, x + max(inset, w - inset)), y + inset)
        if e == "bottom":
            return (self._rng.randint(x + inset, x + max(inset, w - inset)), y + h - inset)
        if e == "left":
            return (x + inset, self._rng.randint(y + inset, y + max(inset, h - inset)))
        return (x + w - inset, self._rng.randint(y + inset, y + max(inset, h - inset)))

    def cluster_points(self, rect: tuple[int, int, int, int], count: int,
                       spread: int = 90) -> list[tuple[int, int]]:
        """`count` points around one anchor, for dumping a group of troops.

        Used when a wave should land in a *roughly* one place but not in a
        perfect grid.
        """
        x, y, w, h = rect
        cx, cy = x + w / 2.0, y + h / 2.0
        pts = []
        for _ in range(max(0, count)):
            a = self._rng.uniform(0, 2 * math.pi)
            d = spread * math.sqrt(self._rng.random())
            pts.append((int(cx + math.cos(a) * d), int(cy + math.sin(a) * d)))
        return pts

    # ---- timing ---------------------------------------------------------

    def delay(self, base: float, sigma: float | None = None) -> float:
        """Log-normal delay with median `base` and shape `sigma`."""
        base = max(0.0, float(base))
        if not self.enabled or base == 0.0:
            return base
        s = self.delay_sigma if sigma is None else max(0.0, min(0.9, float(sigma)))
        if s == 0.0:
            return base
        # lognormal's median is exp(mu), so mu = ln(base) makes the median exact.
        value = self._rng.lognormvariate(math.log(base), s)
        # Never return something absurd: a 12x outlier would stall a raid.
        return max(base * 0.35, min(base * 3.0, value))

    def maybe_pause(self) -> float:
        """Occasionally a person stops and looks at the screen."""
        if not self.enabled or self.pause_chance <= 0:
            return 0.0
        if self._rng.random() >= self.pause_chance:
            return 0.0
        lo, hi = self.pause_range
        return self._rng.uniform(lo, hi)

    def sleep(self, base: float, stop_check=None, tick: float = 0.1) -> float:
        """Sleep for a jittered `base`, returning the seconds actually slept.

        `stop_check` is polled every `tick` so a Stop press still lands during a
        long wait instead of after it.
        """
        total = self.delay(base) + self.maybe_pause()
        deadline = time.time() + total
        while time.time() < deadline:
            if stop_check is not None and stop_check():
                break
            time.sleep(min(tick, max(0.0, deadline - time.time())))
        return total

    # ---- taps -----------------------------------------------------------

    def anticipation(self) -> float:
        """Small pre-tap hesitation, the way a finger hovers before landing."""
        if not self.enabled:
            return 0.0
        return self._rng.uniform(0.02, 0.16)


def converge(values: list[float], target: float, rate: float = 0.35) -> list[float]:
    """Nudge a list of measured values toward `target`.

    Used by the timing tuner: if every raid is finishing early the waits are
    too long, and shrinking them by a fraction each session converges without
    the oscillation a full jump would cause.
    """
    if not values:
        return []
    return [v + (target - v) * rate for v in values]
