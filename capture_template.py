"""Crop a template out of a live screenshot.

The Collect task needs one small PNG per collector bubble. There is no way to
ship those with the repo -- they are copyrighted game art and they change with
every UI revamp -- so the user captures them once, locally:

    python capture_template.py

Drag a box around the bubble, type a name (default ``collect``), Save. Repeat
for gold/elixir/dark if their bubbles look different. The bot then finds them
by template matching at any resolution.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, simpledialog

import cv2
from PIL import Image, ImageTk

import templates as TPL
from adb import EmulatorAdb
from config import AppConfig
from settings import load_settings


class Cropper:
    def __init__(self, root, frame):
        self.root = root
        self.frame = frame
        self.start = None
        self.rect_id = None
        self._photo = None
        self.scale = 1.0
        self.off_x = 0
        self.off_y = 0

        root.title("Capture template - drag a box around the icon")
        root.configure(bg="#15161a")
        root.minsize(760, 560)
        self.canvas = tk.Canvas(root, bg="#0f1115", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Configure>", lambda _e: self.render())

        bar = tk.Frame(root, bg="#15161a")
        bar.pack(fill="x", padx=10, pady=10)
        self.hint = tk.StringVar(value="Drag a box around ONE collector bubble, then press Save.")
        tk.Label(bar, textvariable=self.hint, bg="#15161a", fg="#b6bcc6").pack(side="left")
        tk.Button(bar, text="Recapture", command=self.recapture, bg="#1a73e8", fg="white",
                  relief="flat", padx=14, pady=5).pack(side="right")
        tk.Button(bar, text="Save crop", command=self.save, bg="#188038", fg="white",
                  relief="flat", padx=14, pady=5).pack(side="right", padx=8)

        self.render()

    def render(self):
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

    def recapture(self):
        frame = self._adb.screencap()
        if frame is None:
            messagebox.showerror("Capture failed", self._adb.last_error)
            return
        self.frame = frame
        self.render()

    def _to_image(self, x, y):
        ix = (x - self.off_x) / self.scale
        iy = (y - self.off_y) / self.scale
        h, w = self.frame.shape[:2]
        return max(0, min(w - 1, int(ix))), max(0, min(h - 1, int(iy)))

    def on_press(self, e):
        self.start = (e.x, e.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                    outline="#35c47a", width=2)

    def on_drag(self, e):
        if not self.start or not self.rect_id:
            return
        self.canvas.coords(self.rect_id, self.start[0], self.start[1], e.x, e.y)

    def on_release(self, e):
        if not self.start:
            return
        x1, y1 = self._to_image(*self.start)
        x2, y2 = self._to_image(e.x, e.y)
        w, h = abs(x2 - x1), abs(y2 - y1)
        self.selection = (min(x1, x2), min(y1, y2), w, h) if w > 5 and h > 5 else None
        self.hint.set(
            f"Selection {w}x{h}px" if self.selection else "Selection too small -- drag a bigger box."
        )

    def save(self):
        sel = getattr(self, "selection", None)
        if not sel:
            messagebox.showwarning("Nothing selected", "Drag a box around the icon first.")
            return
        name = simpledialog.askstring("Template name", "File name (no .png):",
                                      initialvalue="collect", parent=self.root)
        if not name:
            return
        name = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
        if not name:
            return
        path = TPL.save_crop(self.frame, sel, name)
        messagebox.showinfo("Saved", f"Wrote {path}\n\nThe Collect task will use it now.")


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

    frame = adb.screencap()
    if frame is None:
        tk.Tk().withdraw()
        messagebox.showerror("Capture failed", adb.last_error)
        sys.exit(1)

    root = tk.Tk()
    app = Cropper(root, frame)
    app._adb = adb
    root.mainloop()


if __name__ == "__main__":
    main()
