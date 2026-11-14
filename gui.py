"""The window.

Design notes, because the previous revision was rejected as ugly and the reason
matters:

* **No ttk widgets on the main surfaces.** ttk is themed by the OS on Windows and
  by whatever `clam` decides on Linux; either way it fights the palette. Every
  visible surface here is drawn by :mod:`ui_kit`, so the app looks the same on
  both platforms.
* **Hierarchy over decoration.** One accent colour, three text weights, and size
  doing the rest. Cards are separated by background contrast and a 1px border,
  not by shadows Tk cannot render anyway.
* **The rail carries navigation**, so the content area is not boxed in by a tab
  strip that steals vertical space from the log.

Threading contract is unchanged: the bot puts dicts on a queue, `_poll_queue`
drains it on the main thread, and no callback ever touches a bot object off the
GUI thread.
"""

from __future__ import annotations

import os
import queue
import time
import tkinter as tk

import emulator as EMU
from settings import save_settings
from ui_kit import (BG, SURFACE, SURFACE_2, SURFACE_3, BORDER, TEXT, MUTED, FAINT,
                    ACCENT, GREEN, RED, GOLD, PURPLE, CYAN, Card, Button, Entry,
                    Toggle, NavRail, Pill, Select, icon, f, monof)

PAGES = [("grid", "Farm"), ("check", "Tasks"), ("sliders", "Advanced")]

APP_DIR = os.path.dirname(os.path.abspath(__file__))


class _TabsShim:
    """`gui.tabs.select(i)` used to be a ttk.Notebook. Keep the call working."""

    def __init__(self, gui):
        self._gui = gui

    def select(self, index=None):
        if index is not None:
            self._gui.show_page(int(index))
        return self._gui.page


class BotGUI:
    def __init__(self, root: tk.Tk, bot, msg_queue: queue.Queue,
                 settings: dict | None = None, adb=None):
        self.root = root
        self.bot = bot
        self.msg_queue = msg_queue
        self.settings = settings if settings is not None else {}
        self.adb = adb
        self.started_at = 0.0
        self.raids = 0
        self.page = 0
        self._bars: dict[str, tk.Canvas] = {}

        self.root.title("COC Auto-Farm")
        self.root.geometry("1140x880")
        self.root.minsize(1000, 800)
        self.root.configure(bg=BG)

        self._build()
        self.tabs = _TabsShim(self)
        self.show_page(0)
        self._poll_queue()

    # ------------------------------------------------------------- shell

    def _build(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()

        main = tk.Frame(self.root, bg=BG)
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self.rail = NavRail(main, PAGES, command=self.show_page, bg="#0d1219")
        self.rail.grid(row=0, column=0, sticky="ns")

        content = tk.Frame(main, bg=BG)
        content.grid(row=0, column=1, sticky="nsew", padx=(18, 18), pady=(16, 12))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=3)
        content.rowconfigure(1, weight=1, minsize=180)

        self.page_host = tk.Frame(content, bg=BG)
        self.page_host.grid(row=0, column=0, sticky="nsew")
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)

        self.pages = [
            self._build_farm_page(self.page_host),
            self._build_tasks_page(self.page_host),
            self._build_advanced_page(self.page_host),
        ]
        for p in self.pages:
            p.grid(row=0, column=0, sticky="nsew")

        self._build_activity(content)

    def _build_header(self):
        head = tk.Frame(self.root, bg=SURFACE, height=74)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_propagate(False)
        head.columnconfigure(1, weight=1)

        brand = tk.Frame(head, bg=SURFACE)
        brand.grid(row=0, column=0, padx=(22, 0), pady=14, sticky="w")
        tk.Label(brand, text="COC", bg=SURFACE, fg="#ffffff",
                 font=f(16, "bold")).pack(side="left")
        tk.Label(brand, text=" AUTO-FARM", bg=SURFACE, fg=ACCENT,
                 font=f(16, "bold")).pack(side="left")
        tk.Label(brand, text="   farm automation for Clash of Clans", bg=SURFACE,
                 fg=FAINT, font=f(9)).pack(side="left", pady=(4, 0))

        right = tk.Frame(head, bg=SURFACE)
        right.grid(row=0, column=2, padx=22, sticky="e")

        self.emulator_var = tk.StringVar(value=self._profile_label())
        Select(right, [EMU.profile(k)["label"] for k in EMU.profile_keys()],
               self.emulator_var, command=self._on_emulator_change,
               width=176, bg=SURFACE).pack(side="left", padx=(0, 10))

        self.serial_var = tk.StringVar(value=self._current_serial())
        Entry(right, self.serial_var, width=160, bg=SURFACE).pack(
            side="left", padx=(0, 10))

        Button(right, "Connect", command=self._on_connect,
               kind="primary", height=34, width=104, bg=SURFACE).pack(
            side="left", padx=(0, 14))

        self.status_pill = Pill(right, "connected", GREEN, bg=SURFACE)
        self.status_pill.pack(side="left")

        tk.Frame(self.root, bg=BORDER, height=1).grid(row=0, column=0, sticky="sew")

    def _build_activity(self, parent):
        card = Card(parent, radius=14, padding=14, bg=BG, autofit=False)
        card.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        card.body.columnconfigure(0, weight=1)
        card.body.rowconfigure(1, weight=1)

        head = tk.Frame(card.body, bg=SURFACE)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="ACTIVITY", bg=SURFACE, fg=FAINT,
                 font=f(9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(head, textvariable=self.status_var, bg=SURFACE, fg=MUTED,
                 font=f(9)).grid(row=0, column=1, sticky="e")

        box = tk.Frame(card.body, bg=SURFACE)
        box.grid(row=1, column=0, sticky="nsew", pady=(0, 3))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.log_text = tk.Text(box, height=8, wrap="none", bg=SURFACE, fg="#b9c2d0",
                                bd=0, relief="flat", highlightthickness=0,
                                font=monof(9), insertbackground=TEXT,
                                padx=0, pady=0, state="disabled", cursor="arrow")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(box, command=self.log_text.yview, width=8, bd=0,
                          relief="flat", bg=SURFACE, troughcolor=SURFACE,
                          activebackground=SURFACE_3, highlightthickness=0)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        self.log_text.tag_configure("t", foreground=FAINT)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("note", foreground=ACCENT)
        self.log_text.tag_configure("dim", foreground=MUTED)

    # -------------------------------------------------------------- pages

    def _page(self, parent):
        frame = tk.Frame(parent, bg=BG)
        frame.columnconfigure(0, weight=1)
        return frame

    def _section(self, parent, text, row, pady=(0, 8)):
        tk.Label(parent, text=text.upper(), bg=BG, fg=FAINT,
                 font=f(8, "bold")).grid(row=row, column=0, sticky="w", pady=pady)

    # ---- farm ----------------------------------------------------------

    def _build_farm_page(self, parent):
        page = self._page(parent)

        # ---- auto mode banner ------------------------------------------
        auto_cfg = self.settings.get("auto_mode") or {}
        self.auto_var = tk.BooleanVar(value=bool(auto_cfg.get("enabled", False)))

        banner = Card(page, radius=14, padding=14, bg=BG)
        banner.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        row = tk.Frame(banner.body, bg=SURFACE)
        row.pack(fill="x")

        text = tk.Frame(row, bg=SURFACE)
        text.pack(side="left")
        tk.Label(text, text="AUTO MODE", bg=SURFACE, fg=ACCENT,
                 font=f(11, "bold")).pack(anchor="w")
        tk.Label(text, text="upgrade everything to max · farm when broke · "
                            "stop when the storages are full",
                 bg=SURFACE, fg=MUTED, font=f(9)).pack(anchor="w")

        right = tk.Frame(row, bg=SURFACE)
        right.pack(side="right")
        self.auto_state_var = tk.StringVar(value="off")
        tk.Label(right, textvariable=self.auto_state_var, bg=SURFACE, fg=FAINT,
                 font=f(9)).pack(side="left", padx=(0, 10))
        Toggle(right, self.auto_var, command=self._on_auto_toggle,
               bg=SURFACE).pack(side="left")

        self._section(page, "session", 1)
        stats = tk.Frame(page, bg=BG)
        stats.grid(row=2, column=0, sticky="ew")
        for c in range(4):
            stats.columnconfigure(c, weight=1, uniform="stat")

        self.stat_vars = {}
        for i, (key, label, color) in enumerate([
            ("raids", "RAIDS", TEXT),
            ("gold", "GOLD", GOLD),
            ("elixir", "ELIXIR", PURPLE),
            ("uptime", "UPTIME", CYAN),
        ]):
            self._stat_card(stats, i, key, label, color)

        self._section(page, "attack thresholds", 3, pady=(18, 8))

        cards = tk.Frame(page, bg=BG)
        cards.grid(row=4, column=0, sticky="new")
        cards.columnconfigure(0, weight=1, uniform="thr")
        cards.columnconfigure(1, weight=1, uniform="thr")

        self.min_gold = tk.StringVar(value=str(self._num("min_gold", 500000)))
        self.min_elixir = tk.StringVar(value=str(self._num("min_elixir", 50000)))
        self.min_dark = tk.StringVar(value=str(self._num("min_dark", 0)))
        self.goal_gold = tk.StringVar(value=str(self._num("goal_gold", 20000000)))
        self.goal_elixir = tk.StringVar(value=str(self._num("goal_elixir", 20000000)))
        self.goal_dark = tk.StringVar(value=str(self._num("goal_dark", 0)))

        self._threshold_card(
            cards, 0, "Attack when above", "skip a base below any of these",
            [(GOLD, "Gold", self.min_gold), (PURPLE, "Elixir", self.min_elixir),
             (CYAN, "Dark elixir", self.min_dark)],
        )
        self._threshold_card(
            cards, 1, "Stop after farming", "ignored in auto mode — it stops when full",
            [(GOLD, "Gold", self.goal_gold), (PURPLE, "Elixir", self.goal_elixir),
             (CYAN, "Dark elixir", self.goal_dark)],
        )

        bar = tk.Frame(page, bg=BG)
        bar.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        bar.columnconfigure(3, weight=1)

        self.start_btn = Button(bar, "Start farm", command=self._on_start,
                                kind="success", icon="play", height=44, width=176, bg=BG)
        self.start_btn.grid(row=0, column=0, padx=(0, 10))

        self.stop_btn = Button(bar, "Stop", command=self._on_stop,
                               kind="ghost", icon="stop", height=44, width=124, bg=BG)
        self.stop_btn.grid(row=0, column=1, padx=(0, 10))
        self.stop_btn.set_enabled(False)

        self.calibrate_btn = Button(bar, "Calibrate", command=self._open_calibrate,
                                    kind="outline", height=44, width=134, bg=BG)
        self.calibrate_btn.grid(row=0, column=2)

        self.status_var = tk.StringVar(value="idle")

        self.totals_var = tk.StringVar(value="")
        tk.Label(page, textvariable=self.totals_var, bg=BG, fg=FAINT,
                 font=f(9)).grid(row=6, column=0, sticky="w", pady=(10, 0))

        page.rowconfigure(7, weight=1)
        self._sync_auto_label()
        return page

    def _stat_card(self, parent, col, key, label, color):
        card = Card(parent, radius=13, padding=14, bg=BG, min_height=96)
        card.grid(row=0, column=col, sticky="nsew",
                  padx=(0 if col == 0 else 6, 0 if col == 3 else 6))

        var = tk.StringVar(value="0")
        self.stat_vars[key] = var
        tk.Label(card.body, text=label, bg=SURFACE, fg=FAINT,
                 font=f(8, "bold")).pack(anchor="w")
        tk.Label(card.body, textvariable=var, bg=SURFACE, fg=color,
                 font=f(21, "bold")).pack(anchor="w", pady=(1, 7))

        bar = tk.Canvas(card.body, height=4, bg=SURFACE, highlightthickness=0, bd=0)
        bar.pack(fill="x")
        self._bars[key] = bar
        bar.bind("<Configure>", lambda _e, b=bar, c=color: self._draw_bar(b, c, 0.0))

    def _draw_bar(self, canvas, color, frac):
        canvas.delete("all")
        w = canvas.winfo_width()
        if w < 2:
            return
        canvas.create_rectangle(0, 1, w, 4, fill=SURFACE_3, outline="")
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            canvas.create_rectangle(0, 1, int(w * frac), 4, fill=color, outline="")

    def _threshold_card(self, parent, col, title, subtitle, rows):
        card = Card(parent, radius=14, padding=16, bg=BG)
        card.grid(row=0, column=col, sticky="nsew",
                  padx=(0 if col == 0 else 7, 7 if col == 0 else 0))

        head = tk.Frame(card.body, bg=SURFACE)
        head.pack(fill="x", pady=(0, 12))
        tk.Label(head, text=title, bg=SURFACE, fg=TEXT,
                 font=f(11, "bold")).pack(anchor="w")
        tk.Label(head, text=subtitle, bg=SURFACE, fg=FAINT,
                 font=f(8)).pack(anchor="w")

        for color, label, var in rows:
            row = tk.Frame(card.body, bg=SURFACE)
            row.pack(fill="x", pady=4)
            dot = tk.Canvas(row, width=10, height=10, bg=SURFACE,
                            highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(0, 9))
            icon(dot, "dot", 5, 5, color)
            tk.Label(row, text=label, bg=SURFACE, fg=MUTED,
                     font=f(10)).pack(side="left")
            Entry(row, var, width=132, height=32, bg=SURFACE,
                  justify="right").pack(side="right")

    # ---- tasks ---------------------------------------------------------

    def _build_tasks_page(self, parent):
        page = self._page(parent)

        tk.Label(page, text="Between raids", bg=BG, fg=TEXT,
                 font=f(15, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(page, text="Runs in the gap after Return Home, highest priority first.",
                 bg=BG, fg=FAINT, font=f(9)).grid(row=1, column=0, sticky="w",
                                                  pady=(3, 14))

        card = Card(page, radius=14, padding=0, bg=BG)
        card.grid(row=2, column=0, sticky="new")
        self.task_frame = card.body
        self.task_frame.columnconfigure(1, weight=1)

        head = tk.Frame(card.body, bg=SURFACE)
        head.grid(row=0, column=0, columnspan=5, sticky="ew")
        head.columnconfigure(1, weight=1)
        for c, text, anchor in ((1, "TASK", "w"), (2, "PRIORITY", "center"),
                                (3, "EVERY", "center"), (4, "STATE", "w")):
            tk.Label(head, text=text, bg=SURFACE, fg=FAINT, font=f(8, "bold"),
                     anchor=anchor).grid(row=0, column=c, sticky="ew",
                                         padx=(0 if c == 1 else 10, 0), pady=(14, 10))

        self.task_rows = {}
        tasks_cfg = self.settings.get("tasks") or {}
        for i, (key, label, hint) in enumerate([
            ("upgrade", "Upgrade buildings", "replays plans/upgrade_plan.json"),
            ("autoupgrade", "Smart upgrade", "picks the best building itself"),
            ("collect", "Collect resources", "templates/collect*.png"),
            ("train", "Train troops", "queues at the barracks · auto mode turns it on"),
        ], start=1):
            cfg = tasks_cfg.get(key) or {}
            var = tk.BooleanVar(value=bool(cfg.get("enabled", key == "collect")))
            prio = tk.StringVar(value=str(cfg.get("priority", {"upgrade": 10, "autoupgrade": 11, "collect": 20, "train": 30}[key])))
            every = tk.StringVar(value=str(int(cfg.get("interval", {"upgrade": 900, "autoupgrade": 900, "collect": 180, "train": 240}[key]))))

            if i > 1:
                tk.Frame(card.body, bg=BORDER, height=1).grid(
                    row=i * 2 - 1, column=0, columnspan=5, sticky="ew")

            row = tk.Frame(card.body, bg=SURFACE)
            row.grid(row=i * 2, column=0, columnspan=5, sticky="ew")
            row.columnconfigure(1, weight=1)

            toggle = Toggle(row, var, command=self._save_tasks, bg=SURFACE)
            toggle.grid(row=0, column=0, padx=(16, 14), pady=13)

            info = tk.Frame(row, bg=SURFACE)
            info.grid(row=0, column=1, sticky="w")
            tk.Label(info, text=label, bg=SURFACE, fg=TEXT,
                     font=f(10, "bold")).pack(anchor="w")
            tk.Label(info, text=hint, bg=SURFACE, fg=FAINT,
                     font=f(8)).pack(anchor="w")

            Entry(row, prio, width=58, height=30, bg=SURFACE,
                  justify="center").grid(row=0, column=2, padx=(10, 0))
            Entry(row, every, width=76, height=30, bg=SURFACE,
                  justify="center").grid(row=0, column=3, padx=(10, 0))

            state = tk.Label(row, text="idle", bg=SURFACE, fg=FAINT,
                             font=f(9), anchor="w", width=20)
            state.grid(row=0, column=4, sticky="w", padx=(10, 16))

            self.task_rows[key] = {"enabled": var, "priority": prio,
                                   "interval": every, "state": state, "toggle": toggle}

        tk.Frame(card.body, bg=SURFACE, height=4).grid(row=99, column=0, columnspan=5)

        tk.Label(page, bg=BG, fg=FAINT, font=f(8), justify="left",
                 text="Collect needs templates/collect*.png (capture_template.py) or recorded spots.\n"
                      "Upgrade needs plans/upgrade_plan.json (record_plan.py).\n"
                      "Train needs army_open / troop_pick / army_close from calibrate.py — auto mode "
                      "switches it on by itself.\n"
                      "A task that cannot run says why, then stays quiet until its interval."
                 ).grid(row=3, column=0, sticky="w", pady=(12, 0))

        page.rowconfigure(4, weight=1)
        return page

    # ---- advanced ------------------------------------------------------

    def _build_advanced_page(self, parent):
        page = self._page(parent)
        page.columnconfigure(0, weight=1, uniform="adv")
        page.columnconfigure(1, weight=1, uniform="adv")

        hum_cfg = self.settings.get("humanize") or {}
        self.human_enabled = tk.BooleanVar(value=bool(hum_cfg.get("enabled", True)))
        self.jitter_var = tk.StringVar(value=str(hum_cfg.get("jitter_px", 6)))
        self.sigma_var = tk.StringVar(value=str(hum_cfg.get("delay_sigma", 0.28)))
        self.pause_var = tk.StringVar(value=str(hum_cfg.get("pause_chance", 0.03)))

        hum = Card(page, radius=14, padding=16, bg=BG)
        hum.grid(row=0, column=0, sticky="new", padx=(0, 7))
        self._card_head(hum.body, "HUMANISATION", "shapes the input so it is not a metronome")

        head = tk.Frame(hum.body, bg=SURFACE)
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="Enabled", bg=SURFACE, fg=TEXT,
                 font=f(10)).pack(side="left")
        Toggle(head, self.human_enabled, bg=SURFACE).pack(side="right")

        self._adv_field(hum.body, "Tap jitter", "px inside each button", self.jitter_var)
        self._adv_field(hum.body, "Delay spread", "0 fixed · 0.28 subtle", self.sigma_var)
        self._adv_field(hum.body, "Idle pauses", "0.03 = 3% of actions", self.pause_var)

        atk_cfg = self.settings.get("attack") or {}
        self.drain_var = tk.StringVar(value=str(atk_cfg.get("drain_ratio", 0.8)))
        self.limit_var = tk.StringVar(value=str(atk_cfg.get("time_limit", 30)))

        atk = Card(page, radius=14, padding=16, bg=BG)
        atk.grid(row=0, column=1, sticky="new", padx=(7, 0))
        self._card_head(atk.body, "ATTACK", "what counts as a good enough base")
        self._adv_field(atk.body, "Drain target", "fraction of providers to empty",
                        self.drain_var)
        self._adv_field(atk.body, "Time limit", "seconds per raid before surrender",
                        self.limit_var)

        self.host_var = tk.StringVar(value=self.settings.get("adb_host", "127.0.0.1"))
        self.port_var = tk.StringVar(value=str(self.settings.get("adb_port", 5555)))
        self.adbpath_var = tk.StringVar(value=self.settings.get("adb_path", ""))

        auto_cfg = self.settings.get("auto_upgrade") or {}
        self.dry_run_var = tk.BooleanVar(value=bool(auto_cfg.get("dry_run", False)))
        self.walls_var = tk.BooleanVar(value=bool(auto_cfg.get("allow_walls", False)))
        self.tries_var = tk.StringVar(value=str(auto_cfg.get("max_tries", 4)))

        smart = Card(page, radius=14, padding=16, bg=BG)
        smart.grid(row=1, column=0, sticky="ew", pady=(14, 0), padx=(0, 7))
        self._card_head(smart.body, "SMART UPGRADE",
                        "lets the bot choose what to upgrade")

        self._adv_row_widget(
            smart.body, "Dry run", "log the pick, tap nothing",
            lambda p: Toggle(p, self.dry_run_var, command=self._save_auto, bg=SURFACE),
        )
        self._adv_row_widget(
            smart.body, "Allow walls", "off: walls eat loot",
            lambda p: Toggle(p, self.walls_var, command=self._save_auto, bg=SURFACE),
        )
        self._adv_row_widget(
            smart.body, "Cards to inspect", "before giving up",
            lambda p: Entry(p, self.tries_var, width=76, height=32, bg=SURFACE,
                           justify="right"),
        )

        caps = (self.settings.get("auto_mode") or {}).get("caps") or {}
        self.cap_gold = tk.StringVar(value=str(caps.get("gold", 20_000_000)))
        self.cap_elixir = tk.StringVar(value=str(caps.get("elixir", 20_000_000)))
        self.cap_dark = tk.StringVar(value=str(caps.get("dark", 400_000)))

        def _caps_row(parent):
            box = tk.Frame(parent, bg=SURFACE)
            for var, width, colour in ((self.cap_gold, 92, GOLD),
                                       (self.cap_elixir, 92, PURPLE),
                                       (self.cap_dark, 74, CYAN)):
                cell = tk.Frame(box, bg=SURFACE)
                cell.pack(side="left", padx=(8, 0))
                tk.Label(cell, text="●", bg=SURFACE, fg=colour,
                         font=f(8)).pack(side="left", padx=(0, 3))
                Entry(cell, var, width=width, height=30, bg=SURFACE,
                      justify="right").pack(side="left")
            return box

        self._adv_row_widget(
            smart.body, "Storages full at", "auto mode stops farming here",
            _caps_row,
        )

        conn = Card(page, radius=14, padding=16, bg=BG)
        conn.grid(row=1, column=1, sticky="ew", pady=(14, 0), padx=(7, 0))
        self._card_head(conn.body, "CONNECTION", "how to reach the emulator")

        top = tk.Frame(conn.body, bg=SURFACE)
        top.pack(fill="x")
        for label, var, width in (("Host", self.host_var, 132), ("Port", self.port_var, 80)):
            cell = tk.Frame(top, bg=SURFACE)
            cell.pack(side="left", padx=(0, 12))
            tk.Label(cell, text=label, bg=SURFACE, fg=FAINT,
                     font=f(8)).pack(anchor="w", pady=(0, 4))
            Entry(cell, var, width=width, height=32, bg=SURFACE).pack(anchor="w")

        cell = tk.Frame(conn.body, bg=SURFACE)
        cell.pack(fill="x", pady=(8, 0))
        tk.Label(cell, text="adb path (empty = auto-detect)", bg=SURFACE, fg=FAINT,
                 font=f(8)).pack(anchor="w", pady=(0, 4))
        Entry(cell, self.adbpath_var, width=300, height=32, bg=SURFACE).pack(anchor="w")

        page.rowconfigure(2, weight=1)
        return page

    def _card_head(self, parent, title, subtitle):
        tk.Label(parent, text=title, bg=SURFACE, fg=FAINT,
                 font=f(8, "bold")).pack(anchor="w")
        tk.Label(parent, text=subtitle, bg=SURFACE, fg=MUTED,
                 font=f(9)).pack(anchor="w", pady=(0, 12))

    def _adv_field(self, parent, title, subtitle, var):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=5)
        text = tk.Frame(row, bg=SURFACE)
        text.pack(side="left")
        tk.Label(text, text=title, bg=SURFACE, fg=TEXT, font=f(10)).pack(anchor="w")
        tk.Label(text, text=subtitle, bg=SURFACE, fg=FAINT,
                 font=f(8)).pack(anchor="w")
        Entry(row, var, width=96, height=32, bg=SURFACE,
              justify="right").pack(side="right")

    def _adv_row_widget(self, parent, title, subtitle, factory):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=5)
        text = tk.Frame(row, bg=SURFACE)
        text.pack(side="left")
        tk.Label(text, text=title, bg=SURFACE, fg=TEXT, font=f(10)).pack(anchor="w")
        tk.Label(text, text=subtitle, bg=SURFACE, fg=FAINT,
                 font=f(8)).pack(anchor="w")
        factory(row).pack(side="right")

    def _save_auto(self):
        cfg = {
            "dry_run": bool(self.dry_run_var.get()),
            "allow_walls": bool(self.walls_var.get()),
            "max_tries": max(1, min(12, self._as_int(self.tries_var.get(), 4))),
        }
        self.settings["auto_upgrade"] = cfg

        auto = self.settings.setdefault("auto_mode", {})
        auto["caps"] = {
            "gold": max(0, self._as_int(self.cap_gold.get(), 20_000_000)),
            "elixir": max(0, self._as_int(self.cap_elixir.get(), 20_000_000)),
            "dark": max(0, self._as_int(self.cap_dark.get(), 400_000)),
        }
        save_settings(self.settings)

    # ------------------------------------------------------------ helpers

    def _num(self, key, default):
        try:
            return int(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default

    def _profile_label(self):
        return EMU.profile(self.settings.get("emulator") or "ldplayer")["label"]

    def _profile_key(self):
        label = self.emulator_var.get()
        for k in EMU.profile_keys():
            if EMU.profile(k)["label"] == label:
                return k
        return "ldplayer"

    def _current_serial(self):
        return self.settings.get("adb_serial") or (
            f"{self.settings.get('adb_host', '127.0.0.1')}:"
            f"{self.settings.get('adb_port', 5555)}"
        )

    def show_page(self, index: int):
        index = max(0, min(len(self.pages) - 1, int(index)))
        self.page = index
        self.pages[index].tkraise()
        if getattr(self, "rail", None):
            self.rail._active = index
            self.rail._draw()

    # ------------------------------------------------------------ actions

    def _on_emulator_change(self, _value=None):
        key = self._profile_key()
        ports = EMU.expected_ports(key)
        if ports:
            self.serial_var.set(f"127.0.0.1:{ports[0]}")
        self.settings["emulator"] = key
        self._log(f"{EMU.profile(key)['label']} · {EMU.profile(key)['hint']}", "dim")

    def _on_connect(self):
        serial = self.serial_var.get().strip()
        self.settings["emulator"] = self._profile_key()
        self.settings["adb_serial"] = serial
        if ":" in serial:
            host, _, port = serial.rpartition(":")
            self.settings["adb_host"] = host or "127.0.0.1"
            try:
                self.settings["adb_port"] = int(port)
            except ValueError:
                pass
        if hasattr(self, "adbpath_var"):
            self.settings["adb_path"] = self.adbpath_var.get().strip()
        save_settings(self.settings)

        if self.adb is None:
            self._log("connect: no adb handle in this build", "err")
            return

        self.status_pill.set("connecting", GOLD)
        self.status_var.set(f"connecting to {serial} …")
        self.root.update_idletasks()

        self.adb.host = self.settings.get("adb_host", "127.0.0.1")
        self.adb.port = self.settings.get("adb_port", 5555)
        self.adb.serial = serial
        self.adb.emulator = self._profile_key()

        if self.adb.connect(timeout=6):
            w, h = self.adb.resolution()
            self.status_pill.set("connected", GREEN)
            self._log(f"connected {serial} · {w}x{h}", "ok")
            self.status_var.set("connected")
        else:
            self.status_pill.set("offline", RED)
            self._log(f"connect failed: {self.adb.last_error}", "err")
            self.status_var.set("not connected")

    def _save_tasks(self):
        cfg = {}
        for key, row in self.task_rows.items():
            cfg[key] = {
                "enabled": bool(row["enabled"].get()),
                "priority": self._as_int(row["priority"].get(), 50),
                "interval": max(15, self._as_int(row["interval"].get(), 300)),
            }
        self.settings["tasks"] = cfg
        save_settings(self.settings)
        scheduler = getattr(self.bot, "scheduler", None)
        if scheduler is not None:
            scheduler.configure(cfg)

    def _on_auto_toggle(self):
        cfg = self.settings.setdefault("auto_mode", {})
        cfg["enabled"] = bool(self.auto_var.get())
        save_settings(self.settings)
        self._sync_auto_label()

    def _sync_auto_label(self):
        if not hasattr(self, "start_btn"):
            return
        if self.auto_var.get():
            self.auto_state_var.set("armed")
            self.start_btn.set_text("Start auto")
        else:
            self.auto_state_var.set("off")
            self.start_btn.set_text("Start farm")

    def _on_start(self):
        missing = self._missing_controls()
        if missing:
            self._log(f"cannot start — {len(missing)} button(s) not calibrated: "
                      + ", ".join(missing[:5]), "err")
            self.status_var.set("calibrate first")
            from tkinter import messagebox
            if messagebox.askyesno(
                "Calibrate first",
                "Button positions are missing:\n\n"
                + "\n".join(f"  · {name}" for name in missing)
                + "\n\nOpen the calibration window now?\n"
                  "Click each listed button in the screenshot, then press Save.",
            ):
                self._open_calibrate()
            return

        targets = (self._as_int(self.min_gold.get(), 0),
                   self._as_int(self.min_elixir.get(), 0),
                   self._as_int(self.min_dark.get(), 0))
        goals = (self._as_int(self.goal_gold.get(), 0),
                 self._as_int(self.goal_elixir.get(), 0),
                 self._as_int(self.goal_dark.get(), 0))

        auto = bool(self.auto_var.get())
        if auto:
            # Auto mode decides when to stop; the manual goal check would fight
            # it, so park the goals out of reach.
            goals = (10 ** 12, 10 ** 12, 10 ** 12)
        self.settings.setdefault("auto_mode", {})["enabled"] = auto

        self.settings.update({
            "min_gold": targets[0], "min_elixir": targets[1], "min_dark": targets[2],
            "goal_gold": goals[0], "goal_elixir": goals[1], "goal_dark": goals[2],
            "humanize": {
                "enabled": bool(self.human_enabled.get()),
                "jitter_px": self._as_int(self.jitter_var.get(), 6),
                "delay_sigma": self._as_float(self.sigma_var.get(), 0.28),
                "pause_chance": self._as_float(self.pause_var.get(), 0.03),
            },
            "attack": {
                "drain_ratio": self._as_float(self.drain_var.get(), 0.8),
                "time_limit": self._as_float(self.limit_var.get(), 30),
            },
        })
        save_settings(self.settings)
        self._apply_runtime()
        self._save_auto()

        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self.status_var.set("starting…")
        self.status_pill.set("running", ACCENT)
        self.started_at = time.time()
        self.raids = 0
        self.bot.start(targets, goals)
        # bot.start() refuses to run when the controls are missing, and the
        # buttons above would otherwise stay stuck in the "running" state.
        self.root.after(400, self._verify_start)

    def _verify_start(self):
        if self.status_var.get() not in ("starting…", "running"):
            return
        running = False
        try:
            running = bool(self.bot.is_running())
        except Exception:
            running = True
        if running:
            return
        self.start_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        self.status_var.set("not started — calibrate first")
        self.status_pill.set("idle", GREEN)
        self.started_at = 0.0

    # ---- calibration hand-off -------------------------------------------

    REQUIRED_CONTROLS = (
        "attack_btn_home", "find_btn", "nxt_btn", "attack_btn", "s_goblin",
        "surr", "surr_conf", "return_home", "retry_btn_1", "retry_btn_2",
        "retry_btn_3",
    )

    def _missing_controls(self) -> list[str]:
        controls = self.settings.get("controls") or {}
        return [name for name in self.REQUIRED_CONTROLS if name not in controls]

    def _open_calibrate(self):
        """Launch calibrate.py and reload the result when it closes."""
        import subprocess
        import sys as _sys

        if getattr(_sys, "frozen", False):
            script = os.path.join(APP_DIR, "calibrate.exe")
            cmd = [script]
        else:
            script = os.path.join(APP_DIR, "calibrate.py")
            cmd = [_sys.executable, script]

        if not os.path.exists(script):
            self._log(f"calibrate not found next to the app ({APP_DIR})", "err")
            return

        try:
            proc = subprocess.Popen(cmd, cwd=APP_DIR)
        except Exception as exc:  # noqa: BLE001
            self._log(f"could not open calibrate: {exc}", "err")
            return

        self._log("calibrate opened — click every button, then Save", "note")
        self._wait_calibrate(proc)

    def _wait_calibrate(self, proc):
        if proc.poll() is None:
            self.root.after(600, lambda: self._wait_calibrate(proc))
            return
        self._reload_settings()

    def _reload_settings(self):
        """Re-read settings.json after calibration and hand it to the bot."""
        from settings import load_settings

        fresh = load_settings()
        if not fresh:
            self._log("settings.json is still empty", "err")
            return
        self.settings.clear()
        self.settings.update(fresh)

        controls = fresh.get("controls") or {}
        try:
            self.bot.controls = controls
            self.bot.waits.update({k: float(v) for k, v in (fresh.get("waits") or {}).items()})
        except Exception:
            pass
        if getattr(self.bot, "scheduler", None) is not None:
            self.bot.scheduler.configure(fresh.get("tasks") or {})

        missing = self._missing_controls()
        if missing:
            self._log(f"reloaded — still missing {len(missing)} button(s): "
                      + ", ".join(missing[:4]), "err")
        else:
            self._log(f"calibration reloaded — {len(controls)} controls ready", "ok")

    def _apply_runtime(self):
        """Push the Advanced page into the live objects, not just into settings."""
        hum = getattr(self.bot, "humanizer", None)
        if hum is not None:
            hum.enabled = bool(self.human_enabled.get())
            hum.jitter_px = self._as_int(self.jitter_var.get(), 6)
            hum.delay_sigma = self._as_float(self.sigma_var.get(), 0.28)
            hum.pause_chance = self._as_float(self.pause_var.get(), 0.03)
        atk = getattr(self.bot, "attack", None)
        if isinstance(atk, dict):
            atk["drain_ratio"] = self._as_float(self.drain_var.get(), 0.8)
            atk["time_limit"] = self._as_float(self.limit_var.get(), 30)
        self._save_tasks()

    def _on_stop(self):
        self.stop_btn.set_enabled(False)
        self.status_var.set("stopping…")
        self.bot.stop()

    @staticmethod
    def _as_int(value, default):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # --------------------------------------------------------------- log

    def _log(self, text: str, tag: str | None = None):
        self.log_text.config(state="normal")
        self.log_text.insert("end", time.strftime("%H:%M:%S  "), "t")
        self.log_text.insert("end", f"{text}\n", tag or ())
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                if not isinstance(msg, dict):
                    continue
                self._handle(msg)
        except queue.Empty:
            pass
        finally:
            if self.started_at and self.status_var.get() in ("running", "loading", "farming"):
                mins, secs = divmod(int(time.time() - self.started_at), 60)
                self.stat_vars["uptime"].set(f"{mins}:{secs:02d}")
            self.root.after(120, self._poll_queue)

    def _handle(self, msg):
        mtype = msg.get("type")

        if mtype == "log":
            text = msg.get("text", "")
            low = text.lower()
            tag = "err" if ("error" in low or "failed" in low) else (
                "ok" if low.startswith("battle ended") else None)
            self._log(text, tag)
            if low.startswith("battle ended"):
                self.raids += 1
                self.stat_vars["raids"].set(str(self.raids))
                self._draw_bar(self._bars["raids"], TEXT, min(1.0, self.raids / 50))

        elif mtype == "status":
            state = msg.get("state", "unknown")
            self.status_var.set(state)
            if state in ("stopped", "error"):
                self.start_btn.set_enabled(True)
                self.stop_btn.set_enabled(False)
                self.status_pill.set("idle" if state == "stopped" else "error",
                                     GREEN if state == "stopped" else RED)
            elif state == "running":
                self.start_btn.set_enabled(False)
                self.stop_btn.set_enabled(True)
                self.status_pill.set("running", ACCENT)

        elif mtype == "auto":
            phase = msg.get("phase", "")
            self.auto_state_var.set(phase or "auto")
            self.status_var.set(f"auto · {phase} — {msg.get('text', '')}")

        elif mtype == "alert":
            from tkinter import messagebox
            messagebox.showinfo("Bot alert", msg.get("text", ""))

        elif mtype == "totals":
            data = msg.get("data", {}) or {}
            g = int(data.get("gold", 0) or 0)
            e = int(data.get("elixir", 0) or 0)
            d = int(data.get("delixir", 0) or 0)
            self.stat_vars["gold"].set(_short(g))
            self.stat_vars["elixir"].set(_short(e))
            self.totals_var.set(f"Gold {g:,}    Elixir {e:,}    Dark {d:,}")
            self._draw_bar(self._bars["gold"], GOLD, g / max(1, self._num("goal_gold", 1)))
            self._draw_bar(self._bars["elixir"], PURPLE, e / max(1, self._num("goal_elixir", 1)))

        elif mtype == "tasks":
            self._render_tasks(msg.get("data") or [])

    def _render_tasks(self, rows):
        for row in rows:
            ui = self.task_rows.get(row.get("key"))
            if not ui:
                continue
            if not row.get("enabled"):
                ui["state"].configure(text="off", fg=FAINT)
                continue
            last = row.get("last") or ""
            nxt = int(row.get("in", 0))
            if last:
                ui["state"].configure(text=f"{last} · {nxt}s", fg=MUTED)
            else:
                ui["state"].configure(text=f"waiting · {nxt}s", fg=FAINT)


def _short(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return str(value)
