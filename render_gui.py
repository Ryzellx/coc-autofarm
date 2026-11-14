"""Render the real GUI against a stub bot, for screenshots and layout checks."""
import os
import queue
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import BotGUI
from humanize import Humanizer
from tasks import Scheduler


class StubAdb:
    serial = "127.0.0.1:5555"
    last_error = ""

    def resolution(self):
        return (1280, 720)

    def connect(self, timeout=6):
        return True


class StubBot:
    def __init__(self):
        self.humanizer = Humanizer()
        self.scheduler = Scheduler()
        self.attack = {"drain_ratio": 0.8, "time_limit": 30}
        self.started = None
        self.stopped = False

    def start(self, targets, goals):
        self.started = (targets, goals)

    def stop(self):
        self.stopped = True


LOG = [
    "connected 127.0.0.1:5555 (1280x720)",
    "Models ready",
    "tap attack_btn_home at (1187, 655) [0.927, 0.910] wait 0.52s",
    "search: Gold 640K / Elixir 601K / Dark 3.4K  -> above threshold",
    "attack: 46 goblins deployed along east edge",
    "battle ended: +741K Gold  +702K Elixir  +3.9K Dark",
    "Collect resources: collected 6 collector(s)",
    "Upgrade buildings: skipped -- no plan at plans/upgrade_plan.json",
    "tap attack_btn_home at (1191, 651) [0.927, 0.910] wait 0.48s",
]


def main():
    root = tk.Tk()
    q = queue.Queue()
    bot = StubBot()
    settings = {
        "min_gold": 500000, "min_elixir": 50000, "min_dark": 0,
        "goal_gold": 20000000, "goal_elixir": 20000000, "goal_dark": 0,
        "emulator": "ldplayer", "adb_host": "127.0.0.1", "adb_port": 5555,
        "tasks": {
            "upgrade": {"enabled": True, "priority": 10, "interval": 900},
            "collect": {"enabled": True, "priority": 20, "interval": 180},
            "train": {"enabled": False, "priority": 30, "interval": 240},
        },
    }
    gui = BotGUI(root, bot, q, settings=settings, adb=StubAdb())

    # Pretend a session has been running for a while, so the screenshot shows a
    # realistic state instead of a fresh idle window.
    import time as _t
    gui.started_at = _t.time() - 754
    gui.raids = 7
    gui.stat_vars["raids"].set("7")
    gui._draw_bar(gui._bars["raids"], "#e9edf4", 7 / 50)

    q.put({"type": "status", "state": "running"})
    for line in LOG:
        q.put({"type": "log", "text": line})
    q.put({"type": "totals", "data": {"gold": 1253000, "elixir": 1201000, "delixir": 6010}})
    q.put({"type": "tasks", "data": [
        {"key": "upgrade", "label": "Upgrade buildings", "enabled": True, "priority": 10,
         "interval": 900, "runs": 0, "last": "skipped -- no plan", "in": 880},
        {"key": "collect", "label": "Collect resources", "enabled": True, "priority": 20,
         "interval": 180, "runs": 3, "last": "collected 6 collector(s)", "in": 142},
        {"key": "train", "label": "Train troops", "enabled": False, "priority": 30,
         "interval": 420, "runs": 0, "last": "", "in": 0},
    ]})

    tab = os.environ.get("SHOT_TAB")
    if tab is not None:
        def _pick():
            try:
                gui.tabs.select(int(tab))
            except Exception:
                pass
        root.after(400, _pick)

    root.mainloop()


if __name__ == "__main__":
    main()
