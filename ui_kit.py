"""Small widget kit drawn with Pillow, because Tk looks like 2009 otherwise.

Tkinter has no rounded corners, no anti-aliasing and no hover states, so every
"modern" Tk app ends up looking like a spreadsheet. The trick that fixes it:
render each surface once with Pillow at 4x and downsample, then place real
widgets on top of that image. Costs a few milliseconds per card, gives clean
edges at any DPI.

Everything here is optional in the sense that a missing Pillow degrades to a
sharp-cornered Canvas shape instead of crashing -- the app should still run on a
machine where the ML stack failed to install.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

try:
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except ImportError:  # pragma: no cover - optional dependency
    HAVE_PIL = False

# ------------------------------------------------------------------ theme
BG = "#0b0e13"
SURFACE = "#12161f"
SURFACE_2 = "#1a202b"
SURFACE_3 = "#222a37"
BORDER = "#222b39"
BORDER_HI = "#2f3a4c"

TEXT = "#e9edf4"
MUTED = "#7e8899"
FAINT = "#4e5766"

ACCENT = "#5b8cff"
ACCENT_DK = "#3f6fe0"
GREEN = "#2fcf7a"
GREEN_DK = "#25a862"
RED = "#ef5b5b"
RED_DK = "#c94848"
GOLD = "#f5b942"
PURPLE = "#b07cf5"
CYAN = "#4fd6e8"


def pick_font(candidates: list[str], fallback: str = "DejaVu Sans") -> str:
    try:
        available = set(tkfont.families())
    except Exception:  # pragma: no cover
        return fallback
    for name in candidates:
        if name in available:
            return name
    return fallback


UI = pick_font(["Segoe UI Variable", "Segoe UI", "Inter", "SF Pro Text", "DejaVu Sans"])
MONO = pick_font(["Cascadia Mono", "Consolas", "SF Mono", "DejaVu Sans Mono"])


def f(size: int, weight: str = "normal"):
    return (UI, size, weight)


def monof(size: int, weight: str = "normal"):
    return (MONO, size, weight)


# ------------------------------------------------------------------ shapes

_PIX_CACHE: dict[tuple, object] = {}


def rounded_pixmap(w: int, h: int, radius: int, fill: str, bg: str,
                   border: str | None = None, border_w: int = 1,
                   ss: int = 4):
    """An anti-aliased rounded rectangle, pre-composited on `bg`."""
    w, h = max(1, int(w)), max(1, int(h))
    key = (w, h, radius, fill, bg, border, border_w)
    hit = _PIX_CACHE.get(key)
    if hit is not None:
        return hit

    if not HAVE_PIL:
        photo = None
    else:
        W, H = w * ss, h * ss
        img = Image.new("RGBA", (W, H), bg)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            [0, 0, W - 1, H - 1], radius=radius * ss, fill=fill,
            outline=border, width=max(1, border_w * ss) if border else 0,
        )
        img = img.resize((w, h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)

    if len(_PIX_CACHE) > 400:  # a resize storm would otherwise leak
        _PIX_CACHE.clear()
    _PIX_CACHE[key] = (photo, w, h)
    return (photo, w, h)


class Card(tk.Canvas):
    """Rounded surface that sizes itself to whatever you pack inside.

    `self.body` is the frame to put widgets in. The canvas grows when the body
    does, which is what makes a grid of cards behave without hard-coded heights.
    """

    def __init__(self, parent, radius=14, fill=SURFACE, border=BORDER,
                 padding=16, bg=None, min_height=0, autofit=True, **kw):
        self._bg = bg or parent.cget("bg")
        self._radius = radius
        self._fill = fill
        self._border = border
        self._pad = padding
        self._min_h = min_height
        self._autofit = autofit
        super().__init__(parent, bg=self._bg, highlightthickness=0, bd=0,
                         height=min_height or 1, **kw)
        self.body = tk.Frame(self, bg=fill)
        self._win = self.create_window(padding, padding, anchor="nw", window=self.body)
        self.body.bind("<Configure>", lambda _e: self.after_idle(self._fit))
        self.bind("<Configure>", self._redraw)
        self.after_idle(self._fit)

    def _fit(self):
        """Grow to the body's requested height.

        Asking the *requested* height, not the mapped height, is what breaks the
        deadlock: a 1px-tall canvas clips its window item, so a body that sizes
        itself from the canvas never reports a size at all and every card pins
        at 1px.
        """
        if not self._autofit:
            return
        try:
            need = max(self._min_h, self.body.winfo_reqheight() + 2 * self._pad)
        except tk.TclError:
            return
        if abs(int(self.cget("height")) - need) > 1:
            self.configure(height=need)

    def _redraw(self, event=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        photo, pw, ph = rounded_pixmap(w, h, self._radius, self._fill, self._bg,
                                       self._border)
        self.delete("card")
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo, tags="card")
            self._img = photo
        else:
            self.create_rectangle(1, 1, w - 1, h - 1, fill=self._fill,
                                  outline=self._border, tags="card")
        self.tag_lower("card")

        # The window item is widened to the card, and grown to fill it only when
        # the card was stretched by its parent (a log panel, say). Otherwise it
        # keeps its natural height and the card hugs the content.
        try:
            natural = self.body.winfo_reqheight()
        except tk.TclError:
            natural = 0
        fill_h = max(0, h - 2 * self._pad)
        if not self._autofit or fill_h > natural + 2:
            self.itemconfig(self._win, width=max(1, w - 2 * self._pad),
                            height=max(1, fill_h))
        else:
            self.itemconfig(self._win, width=max(1, w - 2 * self._pad))


class Button(tk.Canvas):
    """Rounded button with hover and press states."""

    def __init__(self, parent, text, command=None, kind="primary", width=None,
                 height=38, radius=10, bg=None, icon=None):
        self._bg = bg or parent.cget("bg")
        self._enabled = True
        self._command = command
        self._kind = kind
        self._radius = radius
        self._icon = icon
        self._text = text
        self._hover = False

        palette = {
            "primary": (ACCENT, ACCENT_DK, "#ffffff"),
            "success": (GREEN, GREEN_DK, "#04140b"),
            "danger": (RED, RED_DK, "#ffffff"),
            "ghost": (SURFACE_2, SURFACE_3, TEXT),
            "outline": (SURFACE, SURFACE_2, MUTED),
        }
        self._fill, self._fill_hi, self._fg = palette.get(kind, palette["primary"])

        size = tkfont.Font(font=f(10, "bold"))
        text_w = size.measure(text) if text else 0
        w = width or max(96, text_w + (58 if icon else 40))
        super().__init__(parent, width=w, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._dw, self._dh = w, height
        self._draw()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._resize(e.width, e.height))

    def _resize(self, w, h):
        if (w, h) != (self._dw, self._dh) and w > 2 and h > 2:
            self._dw, self._dh = w, h
            self._draw()

    def _draw(self):
        if not self._enabled:
            fill, fg = SURFACE_2, FAINT
        elif self._hover:
            fill, fg = self._fill_hi, self._fg
        else:
            fill, fg = self._fill, self._fg

        photo, _, _ = rounded_pixmap(self._dw, self._dh, self._radius, fill, self._bg)
        self.delete("all")
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo)
            self._img = photo
        else:
            self.create_rectangle(0, 0, self._dw, self._dh, fill=fill, outline="")

        cx = self._dw // 2
        if self._icon:
            self._draw_icon(12, self._dh // 2, fg)
            self.create_text(38, self._dh // 2, text=self._text, fill=fg,
                             font=f(10, "bold"), anchor="w")
        else:
            self.create_text(cx, self._dh // 2, text=self._text, fill=fg,
                             font=f(10, "bold"))

    def _draw_icon(self, x, cy, color):
        if self._icon == "play":
            self.create_polygon(x, cy - 5, x, cy + 5, x + 9, cy,
                                fill=color, outline="")
        elif self._icon == "stop":
            self.create_rectangle(x, cy - 5, x + 9, cy + 4, fill=color, outline="")
        elif self._icon == "link":
            self.create_oval(x, cy - 4, x + 8, cy + 4, outline=color, width=2)
            self.create_oval(x + 4, cy - 7, x + 12, cy + 1, outline=color, width=2)

    def _enter(self, _e):
        if self._enabled:
            self._hover = True
            self._draw()

    def _leave(self, _e):
        self._hover = False
        self._draw()

    def _press(self, _e):
        if self._enabled:
            self.move("all", 0, 1)

    def _release(self, _e):
        if not self._enabled:
            return
        self._draw()
        if self._command:
            self._command()

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def set_text(self, text: str):
        self._text = text
        self._draw()


class Entry(tk.Canvas):
    """A tk.Entry wearing a rounded background."""

    def __init__(self, parent, textvariable=None, width=140, height=34,
                 radius=8, bg=None, justify="left", placeholder=""):
        self._bg = bg or parent.cget("bg")
        self._dw, self._dh = width, height
        super().__init__(parent, width=width, height=height, bg=self._bg,
                         highlightthickness=0, bd=0)
        self.var = textvariable or tk.StringVar()
        self._placeholder = placeholder

        self.entry = tk.Entry(
            self, textvariable=self.var, bd=0, relief="flat",
            bg=SURFACE_2, fg=TEXT, insertbackground=ACCENT,
            justify=justify, font=f(10),
            highlightthickness=0, selectbackground=ACCENT, selectforeground="#fff",
        )
        self._win = self.create_window(11, height // 2, anchor="w",
                                       window=self.entry, width=width - 22)
        self._focus = False
        self.entry.bind("<FocusIn>", self._in)
        self.entry.bind("<FocusOut>", self._out)
        self.bind("<Configure>", self._resize)
        self._draw()

    def _resize(self, e):
        if e.width > 2 and e.height > 2 and (e.width, e.height) != (self._dw, self._dh):
            self._dw, self._dh = e.width, e.height
            self.itemconfig(self._win, width=max(1, e.width - 22),
                            height=max(1, e.height - 8))
            self._draw()

    def _in(self, _e):
        self._focus = True
        self._draw()

    def _out(self, _e):
        self._focus = False
        self._draw()

    def _draw(self):
        border = ACCENT if self._focus else BORDER
        photo, _, _ = rounded_pixmap(self._dw, self._dh, 8, SURFACE_2, self._bg,
                                     border=border)
        self.delete("bg")
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo, tags="bg")
            self._img = photo
        else:
            self.create_rectangle(0, 0, self._dw, self._dh, fill=SURFACE_2,
                                  outline=border, tags="bg")
        self.tag_lower("bg")

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(str(value))


class Toggle(tk.Canvas):
    """44x24 switch. Tk has no such widget and a checkbox kills the look."""

    W, H = 44, 24

    def __init__(self, parent, variable: tk.BooleanVar, command=None, bg=None):
        self._bg = bg or parent.cget("bg")
        super().__init__(parent, width=self.W, height=self.H, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self._command = command
        self._pos = 1.0 if self.var.get() else 0.0
        self._draw()
        self.bind("<Button-1>", self._click)
        self.var.trace_add("write", lambda *_: self._animate())

    def _click(self, _e):
        self.var.set(not self.var.get())
        if self._command:
            self._command()

    def _animate(self):
        target = 1.0 if self.var.get() else 0.0
        steps = 5

        def step(i=1):
            t = i / steps
            self._pos += (target - self._pos) * 0.55 if i == 1 else (target - self._pos) * 0.4
            self._draw()
            if i < steps:
                self.after(16, lambda: step(i + 1))
            else:
                self._pos = target
                self._draw()

        step()

    def _draw(self):
        self.delete("all")
        on = self._pos > 0.5
        track = ACCENT if on else SURFACE_3
        photo, _, _ = rounded_pixmap(self.W, self.H, self.H // 2, track, self._bg)
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo)
            self._img = photo
        else:
            self.create_oval(0, 0, self.H, self.H, fill=track, outline="")
        knob_x = 4 + self._pos * (self.W - self.H)
        self.create_oval(knob_x, 4, knob_x + 16, 20, fill="#ffffff", outline="")


def icon(canvas: tk.Canvas, kind: str, cx: int, cy: int, color: str, size: int = 9):
    """Geometric icons, drawn rather than typed: no font ever goes tofu."""
    s = size
    if kind == "grid":
        for dx in (-1, 1):
            for dy in (-1, 1):
                canvas.create_rectangle(cx + dx * s - 4, cy + dy * s - 4,
                                        cx + dx * s + 4, cy + dy * s + 4,
                                        outline=color, width=2)
    elif kind == "check":
        canvas.create_line(cx - s, cy, cx - 3, cy + s - 3, cx + s, cy - s + 1,
                           fill=color, width=2, capstyle="round", joinstyle="round")
        canvas.create_line(cx - s - 2, cy - 1, cx + s + 2, cy - 1,
                           fill=color, width=0)
    elif kind == "sliders":
        for i, (y, knob) in enumerate(((cy - s, -4), (cy, 5), (cy + s, -1))):
            canvas.create_line(cx - s, y, cx + s, y, fill=color, width=2,
                               capstyle="round")
            canvas.create_oval(cx + knob - 3, y - 3, cx + knob + 3, y + 3,
                               fill=color, outline="")
    elif kind == "dot":
        canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, outline="")
    elif kind == "chevron":
        canvas.create_line(cx - 4, cy - 2, cx, cy + 2, cx + 4, cy - 2,
                           fill=color, width=2, capstyle="round")


class Select(tk.Canvas):
    """Dropdown button. A ttk.Combobox cannot be themed past a certain point."""

    def __init__(self, parent, values: list[str], variable: tk.StringVar,
                 command=None, width=190, height=34, bg=None):
        self._bg = bg or parent.cget("bg")
        self._dw, self._dh = width, height
        super().__init__(parent, width=width, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.values = values
        self.var = variable
        self._command = command
        self._hover = False
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE_2, fg=TEXT,
                            activebackground=ACCENT, activeforeground="#ffffff",
                            bd=0, font=f(10), relief="flat")
        self._rebuild_menu()
        self.bind("<Button-1>", self._open)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _rebuild_menu(self):
        self.menu.delete(0, "end")
        for v in self.values:
            self.menu.add_command(label=v, command=lambda v=v: self._pick(v))

    def _pick(self, value):
        self.var.set(value)
        if self._command:
            self._command(value)

    def _open(self, _e):
        try:
            self.menu.tk_popup(self.winfo_rootx(),
                               self.winfo_rooty() + self._dh + 4)
        finally:
            self.menu.grab_release()

    def _set_hover(self, on):
        self._hover = on
        self._draw()

    def _draw(self):
        border = BORDER_HI if self._hover else BORDER
        photo, _, _ = rounded_pixmap(self._dw, self._dh, 9, SURFACE_2, self._bg, border)
        self.delete("all")
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo)
            self._img = photo
        else:
            self.create_rectangle(0, 0, self._dw, self._dh, fill=SURFACE_2, outline=border)
        self.create_text(12, self._dh // 2, text=self.var.get(), fill=TEXT,
                         font=f(10), anchor="w")
        icon(self, "chevron", self._dw - 14, self._dh // 2, MUTED)


class NavRail(tk.Canvas):
    """Vertical rail with geometric icons; the active item gets an accent pill."""

    ITEM_H = 62

    def __init__(self, parent, items: list[tuple[str, str]], command, width=86,
                 bg="#0d1219"):
        self._items = items
        self._command = command
        self._active = 0
        self._hover = -1
        self._dw = width
        self._top = 104
        h = self._top + self.ITEM_H * len(items) + 20
        super().__init__(parent, width=width, height=h, bg=bg,
                         highlightthickness=0, bd=0)
        self._bg = bg
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(-1))
        self._draw()

    def _hit(self, y):
        i = int((y - self._top) // self.ITEM_H)
        return i if 0 <= i < len(self._items) else -1

    def _click(self, e):
        i = self._hit(e.y)
        if i >= 0:
            self.select(i)

    def _motion(self, e):
        self._set_hover(self._hit(e.y))

    def _set_hover(self, i):
        if i != self._hover:
            self._hover = i
            self._draw()

    def select(self, i):
        if 0 <= i < len(self._items):
            self._active = i
            self._draw()
            self._command(i)

    def _draw(self):
        self.delete("all")
        # brand mark
        cx = self._dw // 2
        photo, _, _ = rounded_pixmap(40, 40, 12, ACCENT, self._bg)
        if photo is not None:
            self.create_image(cx - 20, 26, anchor="nw", image=photo)
            self._img = photo
        else:
            self.create_rectangle(cx - 20, 26, cx + 20, 66, fill=ACCENT, outline="")
        self.create_text(cx, 46, text="C", fill="#ffffff", font=f(16, "bold"))
        self.create_text(cx, 78, text="AUTO", fill=FAINT, font=f(7, "bold"))

        for i, (key, label) in enumerate(self._items):
            top = self._top + i * self.ITEM_H
            active = i == self._active
            hovered = i == self._hover

            if active or hovered:
                fill = SURFACE_2 if active else SURFACE
                photo, _, _ = rounded_pixmap(self._dw - 16, self.ITEM_H - 12, 11,
                                             fill, self._bg)
                if photo is not None:
                    self.create_image(8, top + 6, anchor="nw", image=photo)
                    self._img = photo
                else:
                    self.create_rectangle(8, top + 6, self._dw - 8, top + self.ITEM_H - 6,
                                          fill=fill, outline="")

            color = TEXT if active else (MUTED if hovered else FAINT)
            icon(self, key, cx, top + 20, ACCENT if active else color, 8)
            self.create_text(cx, top + 42, text=label, fill=ACCENT if active else color,
                             font=f(8, "bold" if active else "normal"))

            if active:
                photo, _, _ = rounded_pixmap(3, 26, 2, ACCENT, self._bg)
                if photo is not None:
                    self.create_image(0, top + 18, anchor="nw", image=photo)
                    self._img = photo
                else:
                    self.create_rectangle(0, top + 18, 3, top + 44, fill=ACCENT, outline="")


class Pill(tk.Canvas):
    """Small status chip: dot + text, coloured."""

    def __init__(self, parent, text="", color=GREEN, bg=None, height=26):
        self._bg = bg or parent.cget("bg")
        self._color = color
        self._text = text
        self._dh = height
        super().__init__(parent, height=height, bg=self._bg, highlightthickness=0, bd=0)
        self._draw()

    def set(self, text=None, color=None):
        if text is not None:
            self._text = text
        if color is not None:
            self._color = color
        self._draw()

    def _draw(self):
        self.delete("all")
        font = tkfont.Font(font=f(9, "bold"))
        w = font.measure(self._text) + 40
        self.configure(width=w)
        photo, _, _ = rounded_pixmap(w, self._dh, self._dh // 2, SURFACE_2, self._bg,
                                    border=BORDER)
        if photo is not None:
            self.create_image(0, 0, anchor="nw", image=photo)
            self._img = photo
        else:
            self.create_rectangle(0, 0, w, self._dh, fill=SURFACE_2, outline=BORDER)
        cy = self._dh // 2
        self.create_oval(13, cy - 3, 19, cy + 3, fill=self._color, outline="")
        self.create_text(26, cy, text=self._text, fill=TEXT, font=f(9, "bold"), anchor="w")


class Divider(tk.Frame):
    def __init__(self, parent, bg=BORDER, **kw):
        super().__init__(parent, bg=bg, height=1, **kw)
