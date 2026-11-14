"""Tests for the parts that can be proven without an emulator.

Run:  python tests/test_core.py

The bot's raid loop needs CoC running, so it is not tested here. Everything
that *decides* something -- jitter geometry, delay distribution, port
arithmetic, task scheduling, plan replay -- is pure logic, and that is what
this file exercises.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import emulator as EMU
import tasks as T
from humanize import Humanizer

PASS = 0
FAIL = 0


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok   {name}")
    except AssertionError as exc:
        FAIL += 1
        print(f"  FAIL {name} :: {exc}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  FAIL {name} :: {type(exc).__name__}: {exc}")


def eq(a, b, msg=""):
    assert a == b, f"{a!r} != {b!r} {msg}"


# ---------------------------------------------------------------- emulator

def test_profiles_exist():
    for key in ("ldplayer", "mumu", "bluestacks", "memu", "nox", "androidstudio", "generic"):
        p = EMU.profile(key)
        eq(p["label"] in ("", None), False, f"{key} has no label")
        assert isinstance(p["adb_names"], list) and p["adb_names"]


def test_ldplayer_ports_step_by_two():
    ports = EMU.expected_ports("ldplayer", count=3)
    eq(ports[0], 5555)
    eq(ports[1], 5557)
    eq(ports[2], 5559)


def test_mumu_ports_step_by_32():
    ports = EMU.expected_ports("mumu", count=3)
    eq(ports[0], 16384)
    eq(ports[1], 16416)
    eq(ports[2], 16448)


def test_bluestacks_ports_step_by_ten():
    ports = EMU.expected_ports("bluestacks", count=3)
    eq(ports[:3], [5555, 5565, 5575])


def test_unknown_profile_falls_back():
    eq(EMU.profile("nope")["label"], EMU.profile("generic")["label"])


def test_serial_guessing():
    eq(EMU.emulator_of_serial("127.0.0.1:5555"), "ldplayer")
    eq(EMU.emulator_of_serial("127.0.0.1:16384"), "mumu")
    eq(EMU.emulator_of_serial("127.0.0.1:62001"), "nox")
    eq(EMU.emulator_of_serial("emulator-5554"), "androidstudio")
    eq(EMU.emulator_of_serial("127.0.0.1:21503"), "memu")


def test_avd_has_no_ports_to_scan():
    eq(EMU.expected_ports("androidstudio"), [])
    eq(EMU.expected_ports("waydroid"), [])


# ---------------------------------------------------------------- humanize

def test_jitter_stays_inside_radius():
    h = Humanizer(jitter_px=8, seed=1)
    for _ in range(2000):
        x, y = h.jitter_point(500, 400)
        dist = math.hypot(x - 500, y - 400)
        assert dist <= 8.6, f"jitter escaped radius: {dist}"


def test_jitter_is_disabled_cleanly():
    h = Humanizer(enabled=False, jitter_px=20, seed=1)
    eq(h.jitter_point(500, 400), (500, 400))


def test_jitter_actually_moves():
    h = Humanizer(jitter_px=6, seed=2)
    seen = {h.jitter_point(500, 400) for _ in range(50)}
    assert len(seen) > 10, f"jitter is not random enough: {len(seen)} distinct points"


def test_delay_median_is_the_base():
    h = Humanizer(delay_sigma=0.3, seed=3)
    xs = [h.delay(1.0) for _ in range(4000)]
    median = statistics.median(xs)
    assert 0.9 <= median <= 1.1, f"median {median:.3f} is not near 1.0"


def test_delay_is_not_a_metronome():
    h = Humanizer(delay_sigma=0.28, seed=4)
    xs = [h.delay(0.5) for _ in range(500)]
    assert len(set(round(x, 4) for x in xs)) > 400, "delays look quantised"
    stdev = statistics.pstdev(xs)
    assert stdev > 0.05, f"spread too small: {stdev:.3f}"


def test_delay_is_bounded():
    h = Humanizer(delay_sigma=0.9, seed=5)
    for _ in range(2000):
        v = h.delay(2.0)
        assert 0.7 <= v <= 6.0, f"outlier {v}"


def test_disabled_humanizer_is_exact():
    h = Humanizer(enabled=False)
    eq(h.delay(1.5), 1.5)
    eq(h.maybe_pause(), 0.0)


def test_pause_chance_is_respected():
    h = Humanizer(pause_chance=0.25, seed=6)
    hits = sum(1 for _ in range(4000) if h.maybe_pause() > 0)
    rate = hits / 4000
    assert 0.20 <= rate <= 0.30, f"pause rate {rate:.2f} off target 0.25"


def test_edge_point_stays_on_the_rect():
    h = Humanizer(seed=7)
    rect = (100, 200, 400, 300)
    for _ in range(500):
        x, y = h.edge_point(rect, inset=20)
        assert 100 <= x <= 500 and 200 <= y <= 500, f"outside rect: {(x, y)}"


def test_edge_point_uses_more_than_one_edge():
    h = Humanizer(seed=8)
    rect = (0, 0, 300, 300)
    edges = set()
    for _ in range(200):
        x, y = h.edge_point(rect, inset=10)
        if y <= 12:
            edges.add("top")
        elif y >= 288:
            edges.add("bottom")
        elif x <= 12:
            edges.add("left")
        else:
            edges.add("right")
    eq(len(edges), 4, f"only hit {edges}")


# ---------------------------------------------------------------- tasks

class FakeWindow:
    win_width = 1280
    win_height = 720

    def capture_window(self):
        return "FRAME"


class FakeAuto:
    def __init__(self):
        self.clicks = []
        self.keys = []

    def click(self, x, y, radius=None):
        self.clicks.append((x, y, radius))

    def send_key(self, key):
        self.keys.append(key)


class FakeAdb:
    def __init__(self):
        self.swipes = []

    def swipe(self, *a, **kw):
        self.swipes.append((a, kw))
        return True


def make_ctx(controls=None, settings=None):
    return T.TaskContext(
        adb=FakeAdb(),
        window=FakeWindow(),
        auto=FakeAuto(),
        humanizer=Humanizer(seed=9),
        controls=controls or {},
        settings=settings if settings is not None else {},
        log=lambda *_: None,
    )


def test_collect_unavailable_without_templates_or_spots():
    ctx = make_ctx()
    ok, why = T.CollectTask().available(ctx)
    eq(ok, False)
    assert "template" in why


def test_collect_available_with_recorded_spots():
    ctx = make_ctx(settings={"collect_spots": [{"x": 0.2, "y": 0.3}]})
    ok, why = T.CollectTask().available(ctx)
    eq(ok, True)
    assert "1" in why


def test_collect_taps_every_recorded_spot():
    ctx = make_ctx(settings={"collect_spots": [{"x": 0.2, "y": 0.3}, {"x": 0.7, "y": 0.8}]})
    result = T.CollectTask().run(ctx)
    eq(result.ran, True)
    eq(len(ctx.auto.clicks), 2)
    # Normalised spots must land on the right pixels.
    eq(ctx.auto.clicks[0][0], int(0.2 * 1280))
    eq(ctx.auto.clicks[0][1], int(0.3 * 720))


def test_train_needs_calibration():
    ok, why = T.TrainTask().available(make_ctx())
    eq(ok, False)
    assert "calibrate" in why


def test_train_taps_the_troop_button_n_times():
    controls = {
        "army_open": {"x": 0.05, "y": 0.9},
        "troop_pick": {"x": 0.30, "y": 0.80},
        "army_close": {"x": 0.93, "y": 0.12},
    }
    ctx = make_ctx(controls=controls, settings={"train_clicks": 4})
    result = T.TrainTask().run(ctx)
    eq(result.ran, True)
    # open + 4 picks + close
    eq(len(ctx.auto.clicks), 6)


def test_train_stops_when_the_queue_is_full():
    """Two identical panel captures in a row mean the tap did nothing."""
    try:
        import numpy as np
    except ImportError:
        return  # needs numpy; the shipped build has it, this box does not

    class FrozenWindow:
        win_width = 1280
        win_height = 720

        def __init__(self):
            self.frame = np.full((720, 1280, 3), 90, dtype=np.uint8)

        def capture_window(self):
            return self.frame

    controls = {"army_open": {"x": 0.05, "y": 0.9},
                "troop_pick": {"x": 0.30, "y": 0.80},
                "army_close": {"x": 0.93, "y": 0.12}}
    ctx = make_ctx(controls=controls)
    ctx.window = FrozenWindow()

    result = T.TrainTask(clicks=8).run(ctx)
    eq(result.ran, True)
    assert "full" in result.detail, result.detail
    # open + two taps (the check runs on the second) + close
    eq(len(ctx.auto.clicks), 4)


def test_train_keeps_tapping_while_the_panel_changes():
    try:
        import numpy as np
    except ImportError:
        return

    class MovingWindow:
        win_width = 1280
        win_height = 720

        def __init__(self):
            self.n = 0

        def capture_window(self):
            self.n += 1
            return np.full((720, 1280, 3), (self.n * 7) % 255, dtype=np.uint8)

    controls = {"army_open": {"x": 0.05, "y": 0.9},
                "troop_pick": {"x": 0.30, "y": 0.80},
                "army_close": {"x": 0.93, "y": 0.12}}
    ctx = make_ctx(controls=controls)
    ctx.window = MovingWindow()

    result = T.TrainTask(clicks=4).run(ctx)
    eq(result.ran, True)
    assert "full" not in result.detail
    eq(len(ctx.auto.clicks), 6)  # open + 4 + close


def test_upgrade_requires_a_plan():
    ok, why = T.UpgradeTask().available(make_ctx())
    eq(ok, False)
    assert "plan" in why


def test_upgrade_replays_a_plan_and_advances():
    tmp = tempfile.mkdtemp()
    T.PLAN_DIR = tmp
    with open(os.path.join(tmp, "upgrade_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"steps": [
            {"label": "lab", "actions": [
                {"type": "tap", "x": 0.5, "y": 0.5, "after": 0},
                {"type": "wait", "s": 0},
                {"type": "tap", "x": 0.8, "y": 0.8, "after": 0},
            ]},
            {"label": "camp", "actions": [{"type": "tap", "x": 0.1, "y": 0.1, "after": 0}]},
        ]}, f)

    ctx = make_ctx()
    task = T.UpgradeTask()

    ok, why = task.available(ctx)
    eq(ok, True)
    assert "1/2" in why

    r1 = task.run(ctx)
    eq(r1.ran, True)
    eq(len(ctx.auto.clicks), 2)

    ok, why = task.available(ctx)
    assert "2/2" in why

    r2 = task.run(ctx)
    eq(r2.ran, True)
    eq(len(ctx.auto.clicks), 3)

    ok, why = task.available(ctx)
    eq(ok, False)
    assert "finished" in why


def test_upgrade_reports_stop_mid_step():
    tmp = tempfile.mkdtemp()
    T.PLAN_DIR = tmp
    with open(os.path.join(tmp, "upgrade_plan.json"), "w", encoding="utf-8") as f:
        json.dump({"steps": [{"label": "x", "actions": [
            {"type": "tap", "x": 0.5, "y": 0.5}, {"type": "tap", "x": 0.6, "y": 0.6}]}]}, f)

    import threading
    ev = threading.Event()
    ev.set()
    ctx = make_ctx()
    ctx.stop_event = ev
    r = T.UpgradeTask().run(ctx)
    eq(r.ran, False)
    eq(len(ctx.auto.clicks), 0)


# ---------------------------------------------------------------- planner

from planner import (Candidate, Cost, accept, choose, classify, find_box_with,
                     find_name_box, normalize, read_cost, read_resources)


def test_classify_known_names():
    eq(classify(["Army Camp"])[0], "Army Camp")
    eq(classify(["Laboratory"])[1], 1)
    eq(classify(["Town Hall"])[0], "Town Hall")
    eq(classify(["Town Hall"])[1], 1)
    eq(classify(["Cannon"])[1], 6)
    eq(classify(["Wall"])[1], 9)


def test_classify_longest_alias_wins():
    # "elixir storage" is a substring of "dark elixir storage"; the specific one
    # has to win or dark storage gets ranked as a normal storage.
    eq(classify(["Dark Elixir Storage"])[0], "Dark Elixir Storage")
    eq(classify(["Multi-Archer Tower"])[0], "Multi-Archer Tower")
    eq(classify(["Dark Barracks"])[0], "Dark Barracks")


def test_classify_ignores_noise():
    eq(classify(["Level 5"])[0], None)
    eq(classify(["1,200,000"])[0], None)
    eq(classify([])[0], None)


def test_normalize_strips_ocr_punctuation():
    eq(normalize("  Army   Camp!! "), "army camp")
    eq(normalize("X-Bow"), "x bow")


def test_read_cost_picks_the_price_not_the_level():
    boxes = [("Cannon", (10, 10, 90, 30), 0.9),
             ("Level 5", (10, 40, 80, 60), 0.9),
             ("12,500", (10, 70, 90, 90), 0.9),
             ("650,000", (10, 100, 120, 120), 0.9)]
    cost = read_cost(boxes)
    eq(cost.amount, 650000)


def test_read_cost_understands_suffixes():
    eq(read_cost([("1.2M", (0, 0, 1, 1), 0.9)]).amount, 1200000)
    eq(read_cost([("850K", (0, 0, 1, 1), 0.9)]).amount, 850000)


def test_read_cost_names_the_resource():
    boxes = [("Dark Elixir", (0, 0, 10, 10), 0.9), ("180,000", (0, 20, 10, 30), 0.9)]
    eq(read_cost(boxes).resource, "dark")


def test_read_cost_empty():
    eq(read_cost([]).amount, 0)
    eq(bool(read_cost([])), False)


def test_find_name_box_prefers_the_longest_title():
    boxes = [("Cannon", (0, 0, 50, 20), 0.9),
             ("Archer Tower", (0, 40, 90, 60), 0.9),
             ("Upgrade", (0, 200, 60, 220), 0.9)]
    named = find_name_box(boxes)
    eq(named[0], "Archer Tower")


def test_find_box_with_matches_case_insensitively():
    boxes = [("UPGRADE", (10, 300, 110, 330), 0.9)]
    eq(find_box_with(boxes, ["upgrade"]), (10, 300, 110, 330))


def test_read_resources_from_hud():
    boxes = [("Gold 1,500,000", (10, 10, 200, 30), 0.9),
             ("Elixir 900,000", (10, 40, 200, 60), 0.9),
             ("Dark 12,000", (10, 70, 200, 90), 0.9),
             ("Upgrade", (10, 600, 100, 620), 0.9)]
    res = read_resources(boxes, 720)
    eq(res["gold"], 1500000)
    eq(res["elixir"], 900000)
    eq(res["dark"], 12000)


def test_read_resources_ignores_the_lower_half():
    boxes = [("Gold 500,000", (10, 600, 200, 620), 0.9)]
    eq(read_resources(boxes, 720), {})


def test_accept_rejects_walls_by_default():
    c = Candidate("Wall", 9, Cost(500000, "gold"))
    ok, why = accept(c, None, {"allow_walls": False, "min_tier": 1, "max_tier": 7})
    eq(ok, False)
    assert "wall" in why


def test_accept_allows_walls_when_asked():
    c = Candidate("Wall", 9, Cost(500000, "gold"))
    ok, _why = accept(c, None, {"allow_walls": True, "min_tier": 1, "max_tier": 9})
    eq(ok, True)


def test_accept_checks_affordability():
    c = Candidate("Army Camp", 1, Cost(4000000, "elixir"))
    ok, why = accept(c, {"elixir": 1000000}, {"allow_walls": False, "max_tier": 7})
    eq(ok, False)
    assert "needs" in why


def test_accept_respects_cost_cap():
    c = Candidate("Inferno Tower", 5, Cost(9000000, "gold"))
    cfg = {"allow_walls": False, "max_tier": 7, "max_cost": {"gold": 5000000}}
    ok, why = accept(c, None, cfg)
    eq(ok, False)
    assert "cap" in why


def test_choose_prefers_offence_over_defence():
    cands = [Candidate("Cannon", 6, Cost(900000, "gold")),
             Candidate("Army Camp", 1, Cost(4500000, "elixir"))]
    pick, why = choose(cands, None, {"allow_walls": False, "max_tier": 7})
    eq(pick.name, "Army Camp")
    assert "Army Camp" in why


def test_choose_skips_unaffordable_and_takes_next():
    cands = [Candidate("Army Camp", 1, Cost(4500000, "elixir")),
             Candidate("Cannon", 6, Cost(900000, "gold"))]
    pick, _why = choose(cands, {"elixir": 100000, "gold": 2000000},
                        {"allow_walls": False, "max_tier": 7})
    eq(pick.name, "Cannon")


def test_choose_reports_when_everything_is_rejected():
    cands = [Candidate("Wall", 9, Cost(1000000, "gold"))]
    pick, why = choose(cands, None, {"allow_walls": False, "max_tier": 7})
    eq(pick, None)
    assert "rejected" in why


def test_choose_empty_list():
    pick, why = choose([], None, {"allow_walls": False})
    eq(pick, None)
    eq(why, "nothing to upgrade")


def test_auto_upgrade_task_needs_ocr():
    ctx = make_ctx()
    ctx.vision = None
    ok, why = T.AutoUpgradeTask().available(ctx)
    eq(ok, False)
    assert "OCR" in why


def test_auto_upgrade_task_is_available_with_vision():
    class FakeVision:
        def read_text(self, frame):
            return []
    ctx = make_ctx()
    ctx.vision = FakeVision()
    ok, why = T.AutoUpgradeTask().available(ctx)
    eq(ok, True)
    assert "live" in why


def test_auto_upgrade_reports_dry_run_in_availability():
    class FakeVision:
        def read_text(self, frame):
            return []
    ctx = make_ctx(settings={"auto_upgrade": {"dry_run": True}})
    ctx.vision = FakeVision()
    ok, why = T.AutoUpgradeTask().available(ctx)
    eq(ok, True)
    assert "dry run" in why


# ---------------------------------------------------------------- automode

from automode import AutoDirector, Snapshot


def test_auto_disabled_by_default():
    eq(AutoDirector({}).enabled, False)


def test_auto_enabled_from_settings():
    d = AutoDirector({"auto_mode": {"enabled": True}})
    eq(d.enabled, True)


def test_auto_upgrade_started_means_farm():
    d = AutoDirector({"auto_mode": {"enabled": True}})
    phase, why = d.decide(Snapshot(upgraded=True, candidates=3))
    eq(phase, "farm")
    assert "builders busy" in why


def test_auto_picks_upgrade_when_candidates_exist():
    d = AutoDirector({"auto_mode": {"enabled": True}})
    phase, _why = d.decide(Snapshot(candidates=2))
    eq(phase, "upgrade")


def test_auto_farms_when_nothing_to_upgrade():
    d = AutoDirector({"auto_mode": {"enabled": True}})
    phase, why = d.decide(Snapshot(candidates=0))
    eq(phase, "farm")
    assert "nothing to upgrade" in why


def test_auto_holds_when_storages_full():
    d = AutoDirector({
        "auto_mode": {
            "enabled": True,
            "caps": {"gold": 20_000_000, "elixir": 20_000_000, "dark": 400_000},
        }
    })
    phase, why = d.decide(Snapshot(candidates=0,
                                   resources={"gold": 20_000_000,
                                              "elixir": 19_950_000}))
    eq(phase, "hold")
    assert "full" in why


def test_auto_does_not_hold_below_cap():
    d = AutoDirector({
        "auto_mode": {
            "enabled": True,
            "caps": {"gold": 20_000_000, "elixir": 20_000_000},
        }
    })
    phase, _why = d.decide(Snapshot(candidates=0,
                                    resources={"gold": 12_000_000,
                                               "elixir": 19_999_999}))
    eq(phase, "farm")


def test_auto_hold_falls_back_to_stall_when_resources_unknown():
    d = AutoDirector({"auto_mode": {"enabled": True, "stall_raids": 3}})
    for _ in range(3):
        d.note_raid(0, 0, 0)
    phase, _why = d.decide(Snapshot(candidates=0, resources={}))
    eq(phase, "hold")


def test_auto_stall_resets_on_gain():
    d = AutoDirector({"auto_mode": {"enabled": True, "stall_raids": 3}})
    d.note_raid(0, 0, 0)
    d.note_raid(0, 0, 0)
    d.note_raid(5000, 0, 0)
    eq(d.stall_raids, 0)
    phase, _why = d.decide(Snapshot(candidates=0, resources={}))
    eq(phase, "farm")


def test_auto_upgrade_resets_stall():
    d = AutoDirector({"auto_mode": {"enabled": True, "stall_raids": 2}})
    d.note_raid(0, 0, 0)
    d.note_raid(0, 0, 0)
    d.decide(Snapshot(upgraded=True))
    eq(d.stall_raids, 0)


def test_auto_hold_deadline():
    now = [0.0]
    d = AutoDirector({"auto_mode": {"enabled": True, "hold_max_min": 60}},
                     clock=lambda: now[0])
    eq(d.hold_deadline_passed(), False)   # starts the clock
    now[0] = 1800
    eq(d.hold_deadline_passed(), False)
    now[0] = 3700
    eq(d.hold_deadline_passed(), True)


def test_auto_hold_check_interval_has_a_floor():
    d = AutoDirector({"auto_mode": {"enabled": True, "hold_check_s": 5}})
    eq(d.hold_check_interval(), 60.0)


# ---------------------------------------------------------------- scheduler

def test_scheduler_orders_by_priority():
    s = T.Scheduler()
    s.configure({
        "upgrade": {"enabled": False},
        "collect": {"enabled": True, "priority": 20},
        "train": {"enabled": True, "priority": 5},
    })
    order = [i.key for i in s.due()]
    eq(order, ["train", "collect"])


def test_scheduler_skips_unavailable_tasks_without_spinning():
    s = T.Scheduler()
    s.configure({"collect": {"enabled": True, "interval": 60, "priority": 1}})
    ctx = make_ctx()          # no templates, no spots -> unavailable
    first = s.maybe_run(ctx)
    eq(first, None)
    item = s.items["collect"]
    assert item.last, "unavailability was not recorded"
    # It must not be due again immediately.
    eq(s.due(), [])


def test_scheduler_runs_available_task_and_backs_off():
    s = T.Scheduler()
    s.configure({"collect": {"enabled": True, "interval": 30, "priority": 1}})
    ctx = make_ctx(settings={"collect_spots": [{"x": 0.5, "y": 0.5}]})
    result = s.maybe_run(ctx)
    assert result is not None and result.ran
    eq(s.items["collect"].runs, 1)
    eq(s.due(), [])


def test_scheduler_interval_respects_minimum():
    s = T.Scheduler()
    s.configure({"collect": {"enabled": True, "interval": 1}})
    assert s.items["collect"].interval >= 15.0


# ---------------------------------------------------------------- run

# Explicit registry: filtering globals() by substring grouped
# `test_upgrade_reports_stop_mid_step` under "ports" once, which silently ran it
# out of order. A list cannot make that mistake.
GROUPS = [
    ("emulator profiles", [
        test_profiles_exist,
        test_ldplayer_ports_step_by_two,
        test_mumu_ports_step_by_32,
        test_bluestacks_ports_step_by_ten,
        test_unknown_profile_falls_back,
        test_serial_guessing,
        test_avd_has_no_ports_to_scan,
    ]),
    ("humanize", [
        test_jitter_stays_inside_radius,
        test_jitter_is_disabled_cleanly,
        test_jitter_actually_moves,
        test_delay_median_is_the_base,
        test_delay_is_not_a_metronome,
        test_delay_is_bounded,
        test_disabled_humanizer_is_exact,
        test_pause_chance_is_respected,
        test_edge_point_stays_on_the_rect,
        test_edge_point_uses_more_than_one_edge,
    ]),
    ("tasks", [
        test_collect_unavailable_without_templates_or_spots,
        test_collect_available_with_recorded_spots,
        test_collect_taps_every_recorded_spot,
        test_train_needs_calibration,
        test_train_taps_the_troop_button_n_times,
        test_train_stops_when_the_queue_is_full,
        test_train_keeps_tapping_while_the_panel_changes,
        test_upgrade_requires_a_plan,
        test_upgrade_replays_a_plan_and_advances,
        test_upgrade_reports_stop_mid_step,
    ]),
    ("planner", [
        test_classify_known_names,
        test_classify_longest_alias_wins,
        test_classify_ignores_noise,
        test_normalize_strips_ocr_punctuation,
        test_read_cost_picks_the_price_not_the_level,
        test_read_cost_understands_suffixes,
        test_read_cost_names_the_resource,
        test_read_cost_empty,
        test_find_name_box_prefers_the_longest_title,
        test_find_box_with_matches_case_insensitively,
        test_read_resources_from_hud,
        test_read_resources_ignores_the_lower_half,
        test_accept_rejects_walls_by_default,
        test_accept_allows_walls_when_asked,
        test_accept_checks_affordability,
        test_accept_respects_cost_cap,
        test_choose_prefers_offence_over_defence,
        test_choose_skips_unaffordable_and_takes_next,
        test_choose_reports_when_everything_is_rejected,
        test_choose_empty_list,
        test_auto_upgrade_task_needs_ocr,
        test_auto_upgrade_task_is_available_with_vision,
        test_auto_upgrade_reports_dry_run_in_availability,
    ]),
    ("automode", [
        test_auto_disabled_by_default,
        test_auto_enabled_from_settings,
        test_auto_upgrade_started_means_farm,
        test_auto_picks_upgrade_when_candidates_exist,
        test_auto_farms_when_nothing_to_upgrade,
        test_auto_holds_when_storages_full,
        test_auto_does_not_hold_below_cap,
        test_auto_hold_falls_back_to_stall_when_resources_unknown,
        test_auto_stall_resets_on_gain,
        test_auto_upgrade_resets_stall,
        test_auto_hold_deadline,
        test_auto_hold_check_interval_has_a_floor,
    ]),
    ("scheduler", [
        test_scheduler_orders_by_priority,
        test_scheduler_skips_unavailable_tasks_without_spinning,
        test_scheduler_runs_available_task_and_backs_off,
        test_scheduler_interval_respects_minimum,
    ]),
]

for group, fns in GROUPS:
    print(group)
    for fn in fns:
        check(fn.__name__.replace("test_", "", 1), fn)

print()
print(f"{PASS} checks passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
