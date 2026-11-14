"""Record an upgrade plan by clicking through it once.

Upgrading is the one thing that cannot be inferred: which building to level,
in which order, and through which menus is account-specific. So the bot does
not try to be clever -- it replays exactly what you show it.

    python record_plan.py

Click on the live screenshot to record a tap. The sequence is saved into
``plans/upgrade_plan.json``; each "New step" becomes one upgrade the bot will
perform, one per interval, until the plan runs out.
"""

from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

import cv2
from PIL import Image, ImageTk

import tasks as T
from adb import EmulatorAdb
from config import AppConfig
from settings import load_settings

BG = "#15161a"
FG = "#e6e9ef"
DIM = "#8b93a3"
ACCENT = "#4f8cff"
GREEN = "#35c47a"


class Recorder:
    def __init__(self, root, adb):
        self.root = root
        self.adb = adb
        self.frame = None
        self._photo = None
        self.scale = 1.0
        self.off_x = 0
        self.off_y = 0
        self.steps: list[dict] = [{"label": "upgrade 1", "actions": []}]
        self.current = 0
        self.live = tk.BooleanVar(value=True)

        root.title("Record upgrade plan")
        root.geometry("1180x760")
        root.configure(bg=BG)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        head = tk.Frame(root, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="Click the screenshot to record a tap", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 12)).grid(row=0, column=0, sticky="w")
        self.hint = tk.StringVar(value="")
        tk.Label(head, textvariable=self.hint, bg=BG, fg=DIM,
                 font=("Segoe UI", 9)).grid(row=0, column=1, sticky="e")

        body = tk.Frame(root, bg=BG)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=10)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, bg="#0b0d10", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Configure>", lambda _e: self.render())

        side = tk.Frame(body, bg=BG, width=300)
        side.grid(row=0, column=1, sticky="ns", padx=(12, 0))
        side.grid_propagate(False)

        tk.Label(side, text="PLAN", bg=BG, fg=DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(side, bg="#1b1f27", fg=FG, bd=0,
                                  highlightthickness=0, selectbackground=ACCENT,
                                  font=("Consolas", 9), height=22)
        self.listbox.pack(fill="both", expand=True, pady=(4, 8))

        for text, cmd, color in [
            ("New step", self.new_step, "#5f6368"),
            ("Add wait 2s", lambda: self.add_wait(2.0), "#5f6368"),
            ("Add BACK", self.add_back, "#5f6368"),
            ("Undo last", self.undo, "#a86418"),
            ("Save plan", self.save, GREEN),
        ]:
            tk.Button(side, text=text, command=cmd, bg=color, fg="white" if color != GREEN else "#08120c",
                      relief="flat", padx=10, pady=6, font=("Segoe UI", 9, "bold"),
                      cursor="hand2").pack(fill="x", pady=2)

        tk.Checkbutton(side, text="Live preview", variable=self.live, bg=BG, fg=DIM,
                       selectcolor="#1b1f27", activebackground=BG, bd=0,
                       highlightthickness=0).pack(anchor="w", pady=(8, 0))

        self.refresh()
        self.paint_list()
        self.tick()

    # ---- preview -------------------------------------------------------

    def refresh(self):
        frame = self.adb.screencap()
        if frame is not None:
            self.frame = frame
            self.render()

    def render(self):
        if self.frame is None:
            return
        cw = self.canvas.winfo_width() or 1
        ch = self.canvas.winfo_height() or 1
        h, w = self.frame.shape[:2]
        self.scale = min(cw / w, ch / h)
        dw, dh = int(w * self.scale), int(h * self.scale)
        self.off_x = (cw - dw) // 2
        self.off_y = (ch - dh) // 2
        rgb = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb).resize((dw, dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(pil)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)
        for sx, sy, label in self._dots():
            self.canvas.create_oval(sx - 9, sy - 9, sx + 9, sy + 9,
                                    outline=GREEN, width=2)
            self.canvas.create_text(sx + 14, sy - 12, text=label, fill=GREEN,
                                    font=("Segoe UI", 8, "bold"), anchor="w")

    def _dots(self):
        h, w = self.frame.shape[:2]
        out = []
        for i, act in enumerate(self.steps[self.current]["actions"]):
            if act.get("type") == "tap":
                x = self.off_x + act["x"] * w * self.scale
                y = self.off_y + act["y"] * h * self.scale
                out.append((x, y, str(i + 1)))
        return out

    def tick(self):
        if self.live.get():
            self.refresh()
        self.root.after(1200, self.tick)

    # ---- actions -------------------------------------------------------

    def on_click(self, e):
        if self.frame is None:
            return
        h, w = self.frame.shape[:2]
        x = (e.x - self.off_x) / self.scale / w
        y = (e.y - self.off_y) / self.scale / h
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return
        self.steps[self.current]["actions"].append(
            {"type": "tap", "x": round(x, 5), "y": round(y, 5), "radius": 10, "after": 0.9}
        )
        self.paint_list()

    def add_wait(self, seconds):
        self.steps[self.current]["actions"].append({"type": "wait", "s": seconds})
        self.paint_list()

    def add_back(self):
        self.steps[self.current]["actions"].append({"type": "back", "after": 0.8})
        self.paint_list()

    def undo(self):
        if self.steps[self.current]["actions"]:
            self.steps[self.current]["actions"].pop()
            self.paint_list()

    def new_step(self):
        n = len(self.steps) + 1
        label = simpledialog.askstring("Step name", "What does this step upgrade?",
                                       initialvalue=f"upgrade {n}", parent=self.root)
        self.steps.append({"label": label or f"upgrade {n}", "actions": []})
        self.current = len(self.steps) - 1
        self.paint_list()

    def paint_list(self):
        self.listbox.delete(0, tk.END)
        for si, step in enumerate(self.steps):
            marker = ">" if si == self.current else " "
            self.listbox.insert(tk.END, f"{marker} {si + 1}. {step['label']}")
            for ai, act in enumerate(step["actions"]):
                if act["type"] == "tap":
                    desc = f"      tap {act['x']:.3f},{act['y']:.3f}"
                elif act["type"] == "wait":
                    desc = f"      wait {act['s']}s"
                else:
                    desc = f"      {act['type']}"
                self.listbox.insert(tk.END, desc)
        self.hint.set(f"{len(self.steps)} step(s), step {self.current + 1} selected")

    def save(self):
        keep = [s for s in self.steps if s["actions"]]
        if not keep:
            messagebox.showwarning("Empty plan", "Record at least one tap first.")
            return
        os.makedirs(T.PLAN_DIR, exist_ok=True)
        path = os.path.join(T.PLAN_DIR, "upgrade_plan.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"steps": keep}, f, indent=2)
        messagebox.showinfo("Saved", f"Wrote {path}\n\n{len(keep)} step(s).\n"
                                     "Enable the Upgrade task in the GUI.")


def main():
    config = AppConfig()
    settings = load_settings()
    adb = EmulatorAdb(
        host=settings.get("adb_host", config.adb_host),
        port=settings.get("adb_port", config.adb_port),
        adb_path=settings.get("adb_path", config.adb_path),
        emulator=settings.get("emulator", config.emulator),
        serial=settings.get("adb_serial", ""),
    )
    if not adb.connect():
        tk.Tk().withdraw()
        messagebox.showerror("ADB Error", f"Could not connect.\n\n{adb.last_error}")
        sys.exit(1)
    root = tk.Tk()
    Recorder(root, adb)
    root.mainloop()


if __name__ == "__main__":
    main()
