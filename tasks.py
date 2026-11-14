"""The task layer: what the bot does besides raiding.

The original bot had exactly one behaviour -- the goblin farm loop -- and the
moment you wanted a collector emptied or a builder started you had to stop the
bot and tap it yourself. This module turns those into first-class tasks that
the farm loop calls between raids.

Four tasks ship here, chosen because each one is *verifiable*: the bot can see
whether it worked, instead of tapping into the void and hoping.

``collect``   finds the collector bubbles with template matching and taps them.
              No template yet? It falls back to a list of normalised spots you
              record once, and skips cleanly if neither exists.
``train``     opens the army panel and queues troops by tapping a calibrated
              troop button N times.
``upgrade``   replays a recorded plan: a list of taps that walks the base and
              starts the next upgrade. Anything account-specific (which
              building, in which order) lives in the plan file, not in code.
``autoupgrade`` finds the green upgrade bubbles, reads the card with OCR, and
              takes the one the tier table in :mod:`planner` rates highest that
              it can afford -- no recording, no thinking.

Every task returns a :class:`TaskResult` so the caller can log what actually
happened. A task that could not run says why; it never pretends to have run.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import templates as TPL
import planner as PLANNER

PLAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plans")


# --------------------------------------------------------------------------
# Context and results
# --------------------------------------------------------------------------

@dataclass
class TaskResult:
    ran: bool
    detail: str = ""

    def __bool__(self) -> bool:  # so `if task.run(ctx):` reads naturally
        return self.ran


@dataclass
class TaskContext:
    """Everything a task is allowed to touch.

    Passed explicitly rather than reached for as globals, so a task can be
    unit-tested against a fake ADB without a running emulator.
    """
    adb: object
    window: object
    auto: object
    humanizer: object
    controls: dict
    settings: dict
    log: object = print
    stop_event: object = None
    vision: object = None

    def stopped(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def capture(self):
        return self.window.capture_window()

    def size(self) -> tuple[int, int]:
        return (self.window.win_width or 0, self.window.win_height or 0)

    # -- input helpers shared by every task ------------------------------

    def tap(self, x: float, y: float, radius: int | None = None) -> None:
        # The jitter lives in AutomationController so there is exactly one place
        # that decides where a tap lands; doing it here as well would double it.
        self.auto.click(int(x), int(y), radius=radius)

    def tap_control(self, name: str, radius: int = 8) -> None:
        norm = self.controls.get(name)
        if not norm or "x" not in norm:
            raise KeyError(name)
        w, h = self.size()
        self.tap(norm["x"] * w, norm["y"] * h, radius=radius)

    def wait(self, seconds: float) -> None:
        self.humanizer.sleep(seconds, stop_check=self.stopped)


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------

class Task:
    key = "task"
    label = "Task"
    default_priority = 50
    default_interval = 300.0     # seconds between runs
    default_enabled = False

    def available(self, ctx: TaskContext) -> tuple[bool, str]:
        return True, ""

    def run(self, ctx: TaskContext) -> TaskResult:  # pragma: no cover - abstract
        raise NotImplementedError


# --------------------------------------------------------------------------
# Collect resources
# --------------------------------------------------------------------------

class CollectTask(Task):
    key = "collect"
    label = "Collect resources"
    default_priority = 20
    default_interval = 180.0
    default_enabled = True

    def __init__(self, template_names: list[str] | None = None):
        # Several names so a fresh install can drop in collect.png, or the
        # gold/elixir/de variants separately.
        self.template_names = template_names or ["collect", "collect_gold", "collect_elixir", "collect_dark"]

    def available(self, ctx: TaskContext) -> tuple[bool, str]:
        if not TPL.load_set("collect*"):
            spots = ctx.settings.get("collect_spots") or []
            if spots:
                return True, f"{len(spots)} recorded spots"
            return False, "no templates/collect*.png and no recorded spots"
        return True, ""

    def run(self, ctx: TaskContext) -> TaskResult:
        frame = ctx.capture()
        if frame is None:
            return TaskResult(False, "capture failed")

        tapped = 0
        names = set(self.template_names)
        for name, tpl in TPL.load_set("collect*"):
            if names and name not in names and not name.startswith("collect"):
                continue
            for box in TPL.find_all(frame, tpl, threshold=0.80):
                if ctx.stopped():
                    return TaskResult(tapped > 0, f"stopped after {tapped} taps")
                cx, cy = TPL.centre(box)
                ctx.tap(cx, cy, radius=max(3, min(box[2], box[3]) // 4))
                tapped += 1
                ctx.wait(0.22)

        if tapped:
            return TaskResult(True, f"collected {tapped} collector(s)")

        # Fallback: hand-recorded spots, used when the user has no template yet.
        spots = ctx.settings.get("collect_spots") or []
        for spot in spots:
            if ctx.stopped():
                break
            w, h = ctx.size()
            ctx.tap(float(spot.get("x", 0)) * w, float(spot.get("y", 0)) * h, radius=10)
            tapped += 1
            ctx.wait(0.25)

        if tapped:
            return TaskResult(True, f"tapped {tapped} recorded spot(s)")
        return TaskResult(False, "no collector found on screen")


# --------------------------------------------------------------------------
# Train troops
# --------------------------------------------------------------------------

class TrainTask(Task):
    key = "train"
    label = "Train troops"
    default_priority = 30
    default_interval = 240.0
    default_enabled = False

    # Controls this task needs; calibrate.py offers them all.
    REQUIRED = ("army_open", "troop_pick", "army_close")

    def __init__(self, clicks: int | None = None):
        self.clicks = clicks

    def available(self, ctx: TaskContext) -> tuple[bool, str]:
        missing = [n for n in self.REQUIRED if n not in ctx.controls]
        if missing:
            return False, "calibrate: " + ", ".join(missing)
        return True, ""

    @staticmethod
    def _signature(frame):
        """A cheap fingerprint of the training panel, for change detection.

        Downsampled on purpose: the queue counter animates, the layout does not,
        and we only care whether tapping the troop still did something.
        """
        try:
            import numpy as np
        except ImportError:
            return None
        if frame is None:
            return None
        try:
            h, w = frame.shape[:2]
        except Exception:
            return None
        if h < 8 or w < 8:
            return None
        step_y = max(1, h // 36)
        step_x = max(1, w // 64)
        small = frame[::step_y, ::step_x]
        return np.asarray(small, dtype="int16")

    @staticmethod
    def _same(a, b, tolerance: float = 4.0) -> bool:
        if a is None or b is None or getattr(a, "shape", None) != getattr(b, "shape", None):
            return False
        try:
            return float(abs(a - b).mean()) < tolerance
        except Exception:
            return False

    def run(self, ctx: TaskContext) -> TaskResult:
        clicks = self.clicks
        if clicks is None:
            clicks = int(ctx.settings.get("train_clicks", 5))
        clicks = max(1, min(40, int(clicks)))

        try:
            ctx.tap_control("army_open")
        except KeyError:
            return TaskResult(False, "army_open not calibrated")
        ctx.wait(1.4)

        # Tapping a full queue does nothing but moves the finger around, which
        # looks worse than stopping. Compare the panel every second tap and bail
        # out once it has stopped changing.
        previous = self._signature(ctx.capture())
        queued = 0
        stopped_early = False

        for i in range(clicks):
            if ctx.stopped():
                break
            try:
                ctx.tap_control("troop_pick", radius=10)
            except KeyError:
                return TaskResult(False, "troop_pick not calibrated")
            ctx.wait(0.35)
            queued += 1

            if i % 2 == 1:
                current = self._signature(ctx.capture())
                if previous is not None and self._same(previous, current):
                    stopped_early = True
                    break
                previous = current

        try:
            ctx.tap_control("army_close")
            ctx.wait(0.6)
        except KeyError:
            # Closing is nice-to-have; BACK gets out of the panel too.
            try:
                ctx.auto.send_key("back")
            except Exception:
                pass

        if queued == 0:
            return TaskResult(False, "nothing queued")
        suffix = " (queue was already full)" if stopped_early else ""
        return TaskResult(True, f"queued {queued} troop batch(es){suffix}")


# --------------------------------------------------------------------------
# Upgrade (recorded plan)
# --------------------------------------------------------------------------

class UpgradeTask(Task):
    key = "upgrade"
    label = "Upgrade buildings"
    default_priority = 10        # highest: builders idle is wasted time
    default_interval = 900.0
    default_enabled = False

    def __init__(self, plan_name: str = "upgrade_plan"):
        self.plan_name = plan_name

    def plan_path(self) -> str:
        return os.path.join(PLAN_DIR, f"{self.plan_name}.json")

    def load_plan(self) -> dict:
        try:
            with open(self.plan_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def available(self, ctx: TaskContext) -> tuple[bool, str]:
        plan = self.load_plan()
        steps = plan.get("steps") or []
        if not steps:
            return False, f"no plan at plans/{self.plan_name}.json (run record_plan.py)"
        idx = int((ctx.settings.get("upgrade_state") or {}).get("index", 0))
        if idx >= len(steps):
            return False, f"plan finished ({len(steps)} steps)"
        return True, f"step {idx + 1}/{len(steps)}"

    def run(self, ctx: TaskContext) -> TaskResult:
        plan = self.load_plan()
        steps = plan.get("steps") or []
        state = ctx.settings.setdefault("upgrade_state", {})
        idx = int(state.get("index", 0))
        if idx >= len(steps):
            return TaskResult(False, "plan finished")

        step = steps[idx]
        label = step.get("label") or f"step {idx + 1}"
        actions = step.get("actions") or []
        if not actions:
            state["index"] = idx + 1
            return TaskResult(False, f"{label}: empty step, skipped")

        w, h = ctx.size()
        done = 0
        for act in actions:
            if ctx.stopped():
                return TaskResult(done > 0, f"{label}: stopped mid-step")
            kind = act.get("type")
            if kind == "tap":
                ctx.tap(float(act.get("x", 0)) * w, float(act.get("y", 0)) * h,
                        radius=int(act.get("radius", 10)))
                done += 1
            elif kind == "wait":
                ctx.wait(float(act.get("s", 1.0)))
            elif kind == "back":
                try:
                    ctx.auto.send_key("back")
                except Exception:
                    pass
                done += 1
            elif kind == "swipe":
                # Plans record swipes for panels that need scrolling.
                x1 = float(act.get("x1", 0.5)) * w
                y1 = float(act.get("y1", 0.7)) * h
                x2 = float(act.get("x2", 0.5)) * w
                y2 = float(act.get("y2", 0.3)) * h
                try:
                    ctx.adb.swipe(int(x1), int(y1), int(x2), int(y2),
                                  int(act.get("ms", 320)))
                except Exception:
                    pass
                done += 1
            else:
                continue
            ctx.wait(float(act.get("after", 0.6)))

        state["index"] = idx + 1
        return TaskResult(True, f"{label}: done ({done} action(s))")


# --------------------------------------------------------------------------
# Smart upgrade: the bot decides what to upgrade
# --------------------------------------------------------------------------

class AutoUpgradeTask(Task):
    """Finds an upgradeable building, reads its card, and takes the best one.

    This exists because "record every upgrade by hand" is a worse product than
    "let the bot pick". The decision table lives in :mod:`planner`; this class
    only moves the mouse and reads the screen.
    """

    key = "autoupgrade"
    label = "Smart upgrade"
    default_priority = 11
    default_interval = 900.0
    default_enabled = False

    def _cfg(self, ctx: TaskContext) -> dict:
        cfg = dict(PLANNER.DEFAULT_CFG)
        cfg.update(ctx.settings.get("auto_upgrade") or {})
        return cfg

    def available(self, ctx: TaskContext) -> tuple[bool, str]:
        if ctx.vision is None or not hasattr(ctx.vision, "read_text"):
            return False, "OCR isn't available in this build"
        if ctx.settings.get("upgrade_state", {}).get("finished"):
            return False, "all upgradeable buildings are done"
        cfg = self._cfg(ctx)
        mode = "dry run" if cfg.get("dry_run") else "live"
        return True, f"picks the best card itself ({mode})"

    def _back(self, ctx: TaskContext):
        try:
            ctx.auto.send_key("back")
        except Exception:
            pass

    def _read(self, ctx: TaskContext):
        frame = ctx.capture()
        if frame is None:
            return None, []
        return frame, ctx.vision.read_text(frame)

    def run(self, ctx: TaskContext) -> TaskResult:
        cfg = self._cfg(ctx)
        dry = bool(cfg.get("dry_run"))

        if PLANNER.cv2 is None:
            return TaskResult(False, "OpenCV missing — pip install -r requirements.txt")

        frame = ctx.capture()
        if frame is None:
            return TaskResult(False, "no frame -- is the village on screen?")

        template = None
        try:
            template = TPL.load(os.path.join(TPL.TEMPLATE_DIR, "upgrade_arrow.png"))
        except Exception:
            template = None

        spots = PLANNER.find_indicators(frame, template)
        if not spots:
            return TaskResult(
                False,
                "no upgrade bubbles -- a builder may be busy, or nothing is affordable",
            )
        ctx.log(f"Smart upgrade: {len(spots)} candidate(s) on screen")

        tried = 0
        rejected: list[str] = []
        for rect in spots:
            if ctx.stopped() or tried >= int(cfg.get("max_tries", 4)):
                break
            tried += 1
            cx = rect[0] + rect[2] / 2
            cy = rect[1] + rect[3] / 2
            ctx.tap(cx, cy, radius=max(4, min(12, rect[2] // 3)))
            ctx.wait(1.1)

            _frame, boxes = self._read(ctx)
            named = PLANNER.find_name_box(boxes)
            if named is None:
                ctx.log("Smart upgrade: card text not recognised, closing")
                self._back(ctx)
                ctx.wait(0.4)
                continue

            name, tier, _rect = named
            cost = PLANNER.read_cost(boxes)
            cand = PLANNER.Candidate(name=name, tier=tier, cost=cost)
            resources = PLANNER.read_resources(boxes, ctx.size()[1]) or None

            ok, why = PLANNER.accept(cand, resources, cfg)
            if not ok:
                rejected.append(f"{name}: {why}")
                ctx.log(f"Smart upgrade: skip {name} -- {why}")
                self._back(ctx)
                ctx.wait(0.4)
                continue

            if dry:
                ctx.log(f"Smart upgrade [dry run] would take {cand.describe()}")
                self._back(ctx)
                ctx.wait(0.4)
                return TaskResult(True, f"dry run: would upgrade {cand.describe()}")

            button = PLANNER.find_box_with(boxes, ["upgrade", "improve", "upgrade now"])
            if button is None:
                ctx.log(f"Smart upgrade: {name} has no upgrade button (maxed?)")
                self._back(ctx)
                ctx.wait(0.4)
                continue

            bx = (button[0] + button[2]) / 2
            by = (button[1] + button[3]) / 2
            ctx.tap(bx, by, radius=6)
            ctx.wait(1.0)

            # Second pass: the confirm dialog repeats the word "upgrade".
            _f2, boxes2 = self._read(ctx)
            confirm = PLANNER.find_box_with(boxes2, ["upgrade", "yes", "confirm", "ok"])
            if confirm is not None:
                ctx.tap((confirm[0] + confirm[2]) / 2, (confirm[1] + confirm[3]) / 2,
                        radius=6)
                ctx.wait(1.2)

            ctx.log(f"Smart upgrade: started {cand.describe()}")
            return TaskResult(True, f"upgrading {name}")

        reason = "nothing suitable" + (f" -- {'; '.join(rejected[:3])}" if rejected else "")
        return TaskResult(False, reason)


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------

TASK_CLASSES = {
    "collect": CollectTask,
    "train": TrainTask,
    "upgrade": UpgradeTask,
    "autoupgrade": AutoUpgradeTask,
}


@dataclass
class Scheduled:
    task: Task
    enabled: bool
    priority: int
    interval: float
    next_due: float = 0.0
    runs: int = 0
    last: str = ""

    @property
    def key(self) -> str:
        return self.task.key


class Scheduler:
    """Runs at most one task per call, highest priority first.

    Deliberately not a loop of its own: the farm loop owns the emulator, and a
    second thread tapping the same screen would collide with it. The farm loop
    calls `maybe_run()` at a natural pause (after returning home, before the
    next raid) and gets back either a result or None.
    """

    def __init__(self, humanizer=None, clock=time.time):
        self.items: dict[str, Scheduled] = {}
        self.humanizer = humanizer
        self.clock = clock
        for key, cls in TASK_CLASSES.items():
            task = cls()
            self.items[key] = Scheduled(
                task=task,
                enabled=task.default_enabled,
                priority=task.default_priority,
                interval=task.default_interval,
            )

    # -- configuration ---------------------------------------------------

    def configure(self, cfg: dict) -> None:
        """Apply settings.json -> "tasks"."""
        for key, item in self.items.items():
            entry = (cfg or {}).get(key) or {}
            if "enabled" in entry:
                item.enabled = bool(entry["enabled"])
            if "priority" in entry:
                item.priority = int(entry["priority"])
            if "interval" in entry:
                item.interval = max(15.0, float(entry["interval"]))

    def due(self) -> list[Scheduled]:
        now = self.clock()
        ready = [i for i in self.items.values() if i.enabled and i.next_due <= now]
        ready.sort(key=lambda i: (i.priority, i.next_due))
        return ready

    def next_due_in(self) -> float | None:
        now = self.clock()
        pending = [i.next_due - now for i in self.items.values() if i.enabled]
        return max(0.0, min(pending)) if pending else None

    def maybe_run(self, ctx: TaskContext) -> TaskResult | None:
        """Run the single most urgent ready task, or return None."""
        for item in self.due():
            ok, why = item.task.available(ctx)
            if not ok:
                item.last = why
                item.next_due = self.clock() + min(120.0, item.interval)
                ctx.log(f"{item.task.label}: skipped -- {why}")
                continue

            try:
                result = item.task.run(ctx)
            except Exception as exc:                      # a task must never kill the farm
                result = TaskResult(False, f"error: {exc}")
                ctx.log(f"{item.task.label}: {result.detail}")

            item.runs += 1
            item.last = result.detail
            # Back off on a no-op so a task that cannot find anything does not
            # spin every loop pass.
            backoff = item.interval if result.ran else min(item.interval, 90.0)
            item.next_due = self.clock() + backoff
            return result
        return None

    def snapshot(self) -> list[dict]:
        now = self.clock()
        out = []
        for item in sorted(self.items.values(), key=lambda i: i.priority):
            out.append({
                "key": item.key,
                "label": item.task.label,
                "enabled": item.enabled,
                "priority": item.priority,
                "interval": item.interval,
                "runs": item.runs,
                "last": item.last,
                "in": max(0.0, item.next_due - now),
            })
        return out


def ensure_plan_dir() -> str:
    os.makedirs(PLAN_DIR, exist_ok=True)
    return PLAN_DIR
