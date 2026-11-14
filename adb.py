"""ADB-based background interaction with any Android emulator.

Coordinates are guest pixels (the resolution reported by ``wm size``), which
makes them independent of where the emulator window sits on the desktop and of
whether it is visible at all.

Changes from the LDPlayer-only original:

* the adb binary and the port list come from an emulator profile (see
  :mod:`emulator`), so MuMu, BlueStacks, MEmu, Nox, an Android Studio AVD or a
  hand-entered host:port all work;
* a serial may be an AVD ("emulator-5554"), which must not be passed to
  ``adb connect``;
* ``swipe`` exists, because recorded upgrade plans need to scroll panels;
* a failed tap is retried once after reconnecting, instead of surfacing as an
  exception in the middle of a raid.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time

import emulator as EMU

# Decoding the PNG that `screencap` returns is the only thing OpenCV is used for
# here. Keeping the import optional means the module (and the recorders) can be
# imported on a machine without the ML stack, and a missing dependency surfaces
# as one readable line instead of an ImportError traceback at startup.
try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None
    np = None

MISSING_CV2 = "OpenCV is not installed - run: pip install -r requirements.txt"


class EmulatorAdb:
    """Thin subprocess wrapper around ``adb`` talking to one emulator instance."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5555, adb_path: str = "",
                 emulator: str = "ldplayer", serial: str = "", auto_scan: bool = True):
        self.host = host or "127.0.0.1"
        self.port = int(port or 5555)
        self.emulator = (emulator or "generic").lower()
        self.adb = adb_path or EMU.find_adb(emulator=self.emulator)
        # An explicit serial wins: that is how an AVD or a remote device is
        # addressed, and neither has a meaningful host:port.
        self.serial = serial or f"{self.host}:{self.port}"
        self.auto_scan = auto_scan
        self.last_error = ""
        self._connected = False
        self._res = (0, 0)

    # ---- state ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def _is_tcp(self, serial: str) -> bool:
        return ":" in (serial or "")

    def connect(self, timeout: float = 15.0) -> bool:
        """Connect and query the guest resolution. Returns bool."""
        if self.adb is None:
            self.last_error = "adb not found (install platform-tools or point at the emulator's own adb)"
            return False

        candidates = [self.serial]
        if self.auto_scan and self._is_tcp(self.serial):
            for port in EMU.expected_ports(self.emulator):
                serial = f"{self.host}:{port}"
                if serial not in candidates:
                    candidates.append(serial)

        for index, serial in enumerate(candidates):
            if self._is_tcp(serial):
                # A refused `adb connect` costs seconds per port; probe the TCP
                # port first so a dead instance fails fast. The first candidate
                # still gets patience, because adbd may simply be booting.
                if index > 0 and not EMU.port_open(self.host, int(serial.rsplit(":", 1)[1])):
                    continue
                self._run_on(serial, ["connect", serial], timeout=8)
            deadline = time.time() + (timeout if index == 0 else 1.0)
            while time.time() < deadline:
                if self._is_device(serial):
                    self.serial = serial
                    if self._is_tcp(serial):
                        self.port = int(serial.rsplit(":", 1)[1])
                    self._connected = True
                    self._refresh_resolution()
                    return True
                time.sleep(0.2)

        self.last_error = (
            f"no adb device for {self.serial}"
            + (f" (scanned {self.emulator} ports)" if self.auto_scan else "")
        )
        return False

    def _is_device(self, serial: str) -> bool:
        p = self._run_raw(["devices"], timeout=8)
        if p is None or p.returncode != 0:
            return False
        for line in p.stdout.decode("utf-8", "ignore").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == serial and parts[1].strip() == "device":
                return True
        return False

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    # ---- commands ------------------------------------------------------

    def _run_raw(self, args, timeout: float = 30.0):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run([self.adb] + args, capture_output=True,
                                  timeout=timeout, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = f"adb {' '.join(args)}: {exc}"
            return None

    def _run_on(self, serial: str, args, timeout: float = 30.0):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run([self.adb, "-s", serial] + args, capture_output=True,
                                  timeout=timeout, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = f"adb {' '.join(args)}: {exc}"
            return None

    def _run(self, args, timeout: float = 30.0):
        return self._run_on(self.serial, args, timeout=timeout)

    def _refresh_resolution(self) -> None:
        p = self._run(["shell", "wm", "size"], timeout=10)
        if p is None or p.returncode != 0:
            return
        for word in p.stdout.decode("utf-8", "ignore").replace(":", " ").split():
            if "x" in word and word.replace("x", "").isdigit():
                try:
                    w, h = (int(v) for v in word.split("x"))
                    self._res = (w, h)
                except ValueError:
                    pass
                return

    def resolution(self) -> tuple[int, int]:
        if self._res == (0, 0):
            self._refresh_resolution()
        return self._res

    def screencap(self):
        """Capture the guest framebuffer as a BGR numpy image, or None."""
        if not self._connected:
            return None
        if cv2 is None:
            self.last_error = MISSING_CV2
            return None
        p = self._run(["exec-out", "screencap", "-p"], timeout=30)
        if p is None or p.returncode != 0 or not p.stdout:
            self.last_error = "screencap failed"
            return None
        frame = cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            self.last_error = "screencap decode failed"
            return None
        return frame

    def tap(self, x: float, y: float, retry: bool = True) -> bool:
        if not self._connected:
            return False
        p = self._run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=10)
        if p is not None and p.returncode == 0:
            return True
        if retry:
            # One reconnect covers the common case: the emulator dropped the
            # adb connection while the bot was idle between raids.
            self._connected = False
            if self.connect(timeout=5):
                return self.tap(x, y, retry=False)
        return False

    def swipe(self, x1: float, y1: float, x2: float, y2: float, ms: int = 300) -> bool:
        if not self._connected:
            return False
        p = self._run(
            ["shell", "input", "swipe", str(int(x1)), str(int(y1)),
             str(int(x2)), str(int(y2)), str(max(50, int(ms)))],
            timeout=10,
        )
        return p is not None and p.returncode == 0

    def long_press(self, x: float, y: float, ms: int = 700) -> bool:
        """Implemented as a stationary swipe: `input tap` has no duration."""
        return self.swipe(x, y, x, y, ms=ms)

    def keyevent(self, keycode: int) -> bool:
        if not self._connected:
            return False
        p = self._run(["shell", "input", "keyevent", str(int(keycode))], timeout=10)
        return p is not None and p.returncode == 0

    def package_running(self, package: str) -> bool | None:
        """Is `package` in the foreground? None when it cannot be determined."""
        p = self._run(["shell", "dumpsys", "window"], timeout=12)
        if p is None or p.returncode != 0:
            return None
        text = p.stdout.decode("utf-8", "ignore")
        for line in text.splitlines():
            if "mCurrentFocus" in line:
                return package in line
        return None

    def info(self) -> dict:
        w, h = self.resolution()
        return {
            "serial": self.serial,
            "emulator": self.emulator,
            "adb": os.path.basename(self.adb) if self.adb else None,
            "size": f"{w}x{h}",
            "connected": self._connected,
        }
