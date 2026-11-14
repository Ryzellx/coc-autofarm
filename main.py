"""
Entry point: composes all modules and starts the Tkinter GUI.
"""

print("COC Auto-Farm: starting...", flush=True)

import logging
import os
import queue
import sys
import tkinter as tk
from tkinter import messagebox, ttk

# Optional: the F12 emergency stop. On Linux `keyboard` needs root and fails,
# so a missing/broken module must not stop the app from starting.
try:
    import keyboard
except Exception:  # pragma: no cover - platform dependent
    keyboard = None

from config import AppConfig
from adb import EmulatorAdb
from logger import setup_logging
from window_manager import WindowManager
from settings import load_settings, save_settings
from vision import VisionManager
from automation import AutomationController
from bot import CocBot
from gui import BotGUI
from humanize import Humanizer
from tasks import Scheduler
from automode import AutoDirector
from notify import Notifier
from utils import configure_runtime_env, resource_path

logger = logging.getLogger(__name__)

# Kept so the top-level crash handler can reuse the existing root window
# instead of creating a second one.
_root = None


def _selftest():
    """
    Import every heavy dependency and report what is available.

    Run with ``CocAutoFarm.exe --selftest`` to verify a build on a machine
    without an emulator attached. Exits with 0 when everything imported.
    """
    configure_runtime_env()

    checks = [
        ("cv2", "cv2"),
        ("numpy", "numpy"),
        ("PIL", "PIL"),
        ("keyboard", "keyboard"),
        ("torch", "torch"),
        ("torchvision", "torchvision"),
        ("ultralytics", "ultralytics"),
        ("easyocr", "easyocr"),
    ]
    failures = []
    for label, module in checks:
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", "?")
            print(f"  OK   {label:<12} {version}")
        except Exception as e:
            failures.append(f"{label}: {type(e).__name__}: {e}")
            print(f"  FAIL {label:<12} {type(e).__name__}: {e}")

    try:
        from ultralytics import YOLO
        from config import AppConfig
        from utils import resource_path

        model = resource_path(AppConfig().model_path)
        if not os.path.exists(model):
            failures.append(f"YOLO model missing: {model}")
            print(f"  FAIL yolo model   not found: {model}")
        else:
            YOLO(model)
            print(f"  OK   yolo model   {model}")
    except Exception as e:
        failures.append(f"ultralytics/YOLO: {type(e).__name__}: {e}")
        print(f"  FAIL yolo model   {type(e).__name__}: {e}")

    print()
    if failures:
        print(f"SELFTEST FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELFTEST PASSED")
    return 0


def _show_splash(root, message):
    """Small 'starting' window shown while the ADB connection is attempted."""
    win = tk.Toplevel(root)
    win.title("COC Auto-Farm")
    win.resizable(False, False)
    win.transient(root)

    frame = ttk.Frame(win, padding=20)
    frame.pack()
    ttk.Label(frame, text="COC Auto-Farm", font=("Segoe UI", 12, "bold")).pack(
        pady=(0, 10)
    )
    label = ttk.Label(frame, text=message)
    label.pack()

    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.update_idletasks()
    return win, label


def main():
    setup_logging()
    logger.info("COC Auto-Farm starting")

    configure_runtime_env()

    config = AppConfig()

    # The YOLO model ships inside the bundle (read-only); from source it is
    # next to the project files.
    model_full_path = resource_path(config.model_path)
    if os.path.exists(model_full_path):
        config.model_path = model_full_path

    settings = load_settings()
    notifier = Notifier(settings.get("notifications"))

    # Root is kept hidden until the emulator is reachable; error dialogs
    # (and the GUI) still work while it is withdrawn.
    root = tk.Tk()
    root.withdraw()
    global _root
    _root = root

    splash, splash_label = _show_splash(root, "Starting...")

    def step(text):
        splash_label.config(text=text)
        splash.update()

    adb = EmulatorAdb(
        host=settings.get("adb_host", config.adb_host),
        port=settings.get("adb_port", config.adb_port),
        adb_path=settings.get("adb_path", config.adb_path),
        emulator=settings.get("emulator", config.emulator),
        serial=settings.get("adb_serial", ""),
    )
    step("Connecting to the emulator over ADB...")
    splash.update()
    if not adb.connect():
        logger.error(f"ADB connect failed: {adb.last_error}")
        # Process is about to exit, so send synchronously (text only: ADB is down).
        notifier.notify(
            "COC Auto-Farm: ADB error",
            "Could not connect to the emulator via ADB. "
            f"Details: {adb.last_error}",
            blocking=True,
            priority="high",
            tags="warning",
        )
        splash.destroy()
        messagebox.showerror(
            "ADB Error",
            "Could not connect to the emulator via ADB.\n\n"
            "Check that the emulator is running and that ADB debugging is on "
            "(each emulator puts that switch somewhere different; the Emulator "
            "dropdown in the main window shows the right menu path).\n\n"
            f"Tried: {adb.serial}\n"
            f"Details: {adb.last_error}",
        )
        sys.exit(1)

    step("Loading components...")
    splash.update()

    window = WindowManager(adb=adb)
    vision = VisionManager(config)
    hum_cfg = settings.get("humanize") or {}
    humanizer = Humanizer(
        enabled=bool(hum_cfg.get("enabled", config.humanize_enabled)),
        jitter_px=int(hum_cfg.get("jitter_px", config.jitter_px)),
        delay_sigma=float(hum_cfg.get("delay_sigma", config.delay_sigma)),
        pause_chance=float(hum_cfg.get("pause_chance", config.pause_chance)),
    )
    automation = AutomationController(adb=adb, humanizer=humanizer)
    controls = settings.get("controls") or {}
    waits = settings.get("waits") or {}
    recovery = settings.get("recovery") or {}
    attack = settings.get("attack") or {}

    scheduler = Scheduler(humanizer=humanizer)
    scheduler.configure(settings.get("tasks") or {})

    director = AutoDirector(settings)

    msg_queue = queue.Queue()
    bot = CocBot(
        config, window, vision, automation, msg_queue,
        controls=controls, waits=waits, notifier=notifier,
        recovery=recovery, attack=attack,
        humanizer=humanizer, scheduler=scheduler, settings=settings,
        director=director,
    )

    splash.destroy()
    app = BotGUI(root, bot, msg_queue, settings=settings, adb=adb)
    root.deiconify()

    # Emergency keyboard stop
    if keyboard is not None:
        try:
            keyboard.add_hotkey("f12", bot.stop)
        except Exception as e:
            logger.warning(f"Could not register F12 hotkey: {e}")

    root.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        detail = traceback.format_exc()
        logger.critical("Fatal error:\n%s", detail)
        try:
            if _root is None:
                _root = tk.Tk()
                _root.withdraw()
            messagebox.showerror(
                "COC Auto-Farm crashed",
                "An unexpected error occurred.\n\n"
                f"{detail[-1500:]}\n\n"
                "The full traceback was written to the log file.",
            )
        except Exception:
            pass
        sys.exit(1)
