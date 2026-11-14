"""Auto mode: one button that runs the whole progression.

Manual mode makes the player choose: farm, or upgrade, or stop. This module
makes that choice every cycle, from three facts the bot can actually observe:

* did an upgrade just start     -> a builder is busy now, go farm
* are there upgradeable cards   -> take one
* are the storages full         -> stop farming, there is nothing to gain

"Full" is decided two ways, because neither alone is reliable: the configured
storage caps when the HUD was readable, and a stall counter (consecutive raids
that gained nothing) when it was not. A bot that keeps raiding a full storage
wastes hours and looks exactly like a bot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

UPGRADE = "upgrade"
FARM = "farm"
HOLD = "hold"
DONE = "done"

DEFAULTS = {
    "enabled": False,
    # What "full" means, in raw units. The stall detector covers a wrong number.
    "caps": {"gold": 20_000_000, "elixir": 20_000_000, "dark": 400_000},
    "full_ratio": 0.995,
    # Raids that gained nothing before we call the storages full.
    "stall_raids": 4,
    # How long to sit on a full base before giving up entirely.
    "hold_max_min": 180,
    # How often to peek for a free builder while holding.
    "hold_check_s": 600,
    # Auto mode turns troop training on; this is how often it re-queues.
    "train_interval": 240,
}


@dataclass
class Snapshot:
    """What the director is allowed to look at."""
    upgraded: bool = False       # an upgrade started this cycle
    candidates: int = 0          # upgradeable cards seen (0 = unknown/none)
    resources: dict = field(default_factory=dict)
    workers_free: int | None = None


class AutoDirector:
    """Chooses UPGRADE / FARM / HOLD / DONE once per raid cycle."""

    def __init__(self, settings: dict | None = None, clock=time.time):
        self.settings = settings if settings is not None else {}
        self.clock = clock
        self.stall_raids = 0
        self.hold_started: float | None = None
        self.cycles = 0
        self.last_reason = ""
        self.last_phase = ""

    # -- configuration ---------------------------------------------------

    @property
    def cfg(self) -> dict:
        merged = dict(DEFAULTS)
        merged.update(self.settings.get("auto_mode") or {})
        return merged

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", False))

    @property
    def caps(self) -> dict:
        return self.cfg.get("caps") or {}

    # -- observation -----------------------------------------------------

    def note_raid(self, gold: int = 0, elixir: int = 0, dark: int = 0) -> None:
        """Called after every completed raid with what it actually gained."""
        if gold <= 0 and elixir <= 0 and dark <= 0:
            self.stall_raids += 1
        else:
            self.stall_raids = 0

    def is_full(self, resources: dict | None = None) -> bool:
        """Storages full, by measurement if possible, by stall otherwise."""
        ratio = float(self.cfg.get("full_ratio", 0.995))
        caps = self.caps

        if resources and caps:
            checks = []
            for key in ("gold", "elixir"):
                cap = caps.get(key)
                have = resources.get(key)
                if cap and have:
                    checks.append(have >= cap * ratio)
            if checks:
                return all(checks)

        return self.stall_raids >= int(self.cfg.get("stall_raids", 4))

    # -- decision --------------------------------------------------------

    def decide(self, snap: Snapshot) -> tuple[str, str]:
        cfg = self.cfg

        if snap.upgraded:
            self.stall_raids = 0
            self.hold_started = None
            return FARM, "upgrade started — builders busy, back to farming"

        if snap.workers_free is not None and snap.workers_free == 0 and not snap.candidates:
            # No builder and nothing on screen to tap: nowhere to go but farm.
            if self.is_full(snap.resources):
                return HOLD, "no free builder and storages are full"
            return FARM, "no free builder — farming"

        if snap.candidates > 0:
            return UPGRADE, f"{snap.candidates} upgrade candidate(s) on screen"

        if self.is_full(snap.resources):
            return HOLD, "storages full and nothing upgradeable"

        return FARM, "nothing to upgrade — farming"

    # -- holding ---------------------------------------------------------

    def hold_deadline_passed(self) -> bool:
        """True once waiting has stopped being worth it."""
        if self.hold_started is None:
            self.hold_started = self.clock()
            return False
        limit = float(self.cfg.get("hold_max_min", 180)) * 60
        return (self.clock() - self.hold_started) >= limit

    def hold_check_interval(self) -> float:
        return max(60.0, float(self.cfg.get("hold_check_s", 600)))

    def reset_hold(self) -> None:
        self.hold_started = None

    def describe(self) -> str:
        return f"{self.last_phase or 'idle'} — {self.last_reason or 'waiting'}"
