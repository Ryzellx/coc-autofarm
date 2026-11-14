"""Emulator profiles and discovery.

Every Android emulator exposes the same ADB interface but differs in three
things: which adb binary ships with it, which TCP port the guest listens on, and
how that port is offset for the second, third, ... instance. Hard-coding
LDPlayer's 5555/5557 was the single biggest reason this bot could only ever talk
to one emulator.

The port arithmetic below is taken from each vendor's own multi-instance
behaviour and is deliberately conservative: a profile lists the ports it knows,
and `discover()` still falls back to a `adb devices` scan for anything
unexpected (an Android Studio AVD shows up as `emulator-5554`, not as a TCP
port at all).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import socket
import subprocess
import sys

# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

EMULATORS = {
    "ldplayer": {
        "label": "LDPlayer 9",
        "adb_names": ["adb.exe", "adb"],
        "ports": [5555, 5557, 5559, 5561, 5563, 5565, 5567, 5569],
        "port_step": 2,
        "processes": ["dnplayer.exe", "LdVBoxHeadless.exe"],
        "hint": "Settings -> Others -> ADB local connection = ON",
    },
    "mumu": {
        "label": "MuMu Player 12",
        "adb_names": ["adb.exe", "adb"],
        # MuMu 12 starts at 16384 and steps by 32 per instance.
        "ports": [16384, 16416, 16448, 16480, 16512],
        "port_step": 32,
        "processes": ["MuMuPlayer.exe", "NemuPlayer.exe"],
        "hint": "Settings -> Advanced -> ADB debugging = ON",
    },
    "bluestacks": {
        "label": "BlueStacks 5",
        "adb_names": ["HD-Adb.exe", "adb.exe", "adb"],
        # Rvc64 (Android 11) images start at 5555 and step by 10.
        "ports": [5555, 5565, 5575, 5585, 5595],
        "port_step": 10,
        "processes": ["HD-Player.exe", "BlueStacksAppplayer.exe"],
        "hint": "Settings -> Advanced -> Android Debug Bridge = ON",
    },
    "memu": {
        "label": "MEmu Play",
        "adb_names": ["adb.exe", "adb"],
        "ports": [21503, 21513, 21523, 21533],
        "port_step": 10,
        "processes": ["MEmu.exe", "MEmuHeadless.exe"],
        "hint": "Settings -> ADB = ON",
    },
    "nox": {
        "label": "NoxPlayer",
        "adb_names": ["adb.exe", "adb", "nox_adb.exe"],
        "ports": [62001, 62025, 62026, 62027],
        "port_step": 1,
        "processes": ["Nox.exe", "NoxVMHandle.exe"],
        "hint": "Settings -> General -> Root/ADB = ON",
    },
    "genymotion": {
        "label": "Genymotion",
        "adb_names": ["adb.exe", "adb"],
        "ports": [6555, 6556, 6557],
        "port_step": 1,
        "processes": ["genymotion.exe", "player.exe"],
        "hint": "Virtual device must be started from Genymotion first",
    },
    "androidstudio": {
        "label": "Android Studio AVD",
        "adb_names": ["adb.exe", "adb"],
        # AVDs are USB-style serials, not TCP ports. discovery handles them.
        "ports": [],
        "port_step": 0,
        "processes": ["qemu-system-x86_64.exe", "qemu-system-x86_64"],
        "hint": "Start the AVD from Device Manager",
    },
    "waydroid": {
        "label": "Waydroid (Linux)",
        "adb_names": ["adb"],
        "ports": [],
        "port_step": 0,
        "processes": ["waydroid"],
        "hint": "sudo waydroid shell  (then: adb connect 192.168.240.112:5555)",
    },
    "generic": {
        "label": "Other / manual",
        "adb_names": ["adb.exe", "adb"],
        "ports": [5555],
        "port_step": 0,
        "processes": [],
        "hint": "Enter the ADB host:port shown by your emulator",
    },
}

# Order shown in the GUI: the ones people actually use for CoC first.
PROFILE_ORDER = ["ldplayer", "mumu", "bluestacks", "memu", "nox", "androidstudio", "genymotion", "waydroid", "generic"]


def profile(key: str) -> dict:
    return EMULATORS.get((key or "").lower(), EMULATORS["generic"])


def profile_keys() -> list[str]:
    return [k for k in PROFILE_ORDER if k in EMULATORS]


def expected_ports(key: str, count: int = 8) -> list[int]:
    """Ports this emulator is likely to be listening on, in probe order."""
    p = profile(key)
    ports = list(p.get("ports") or [])
    step = p.get("port_step") or 0
    if step and ports:
        base = ports[0]
        for i in range(1, count):
            candidate = base + step * i
            if candidate not in ports:
                ports.append(candidate)
    return ports


# --------------------------------------------------------------------------
# adb binary discovery
# --------------------------------------------------------------------------

def _platform_tools_adb() -> str | None:
    exe = "adb.exe" if os.name == "nt" else "adb"
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        base = os.environ.get(var)
        if base:
            candidate = os.path.join(base, "platform-tools", exe)
            if os.path.isfile(candidate):
                return candidate
    return None


def _vendor_adb(key: str) -> str | None:
    """Look for the adb that ships inside a vendor install directory."""
    p = profile(key)
    if os.name == "nt":
        roots = [f"{d}:\\" for d in "CDEF" if os.path.exists(f"{d}:\\")]
        patterns = {
            "ldplayer": ["LDPlayer*\\*\\adb.exe", "LDPlayer9\\adb.exe", "LDPlayer\\adb.exe"],
            "mumu": ["*MuMu*\\*\\adb.exe", "Netease\\MuMuPlayer-12.0\\shell\\adb.exe"],
            "bluestacks": ["BlueStacks_nxt\\HD-Adb.exe", "BlueStacks_msi5\\*\\adb.exe", "*BlueStacks*\\HD-Adb.exe"],
            "memu": ["*MEmu*\\adb.exe", "Microvirt\\MEmu\\adb.exe"],
            "nox": ["*Nox*\\nox_adb.exe", "Nox\\bin\\nox_adb.exe"],
        }.get(key, [])
        for root in roots:
            for pat in patterns:
                hits = glob.glob(os.path.join(root, pat))
                if hits:
                    return hits[0]
        return None

    # Linux/macOS: vendors mostly do not ship adb; rely on PATH.
    for name in p.get("adb_names", []):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_adb(preferred: str = "", emulator: str = "generic") -> str | None:
    """Resolve an adb binary, preferring an explicit path, then the vendor's."""
    if preferred and os.path.isfile(preferred):
        return preferred
    for name in ("adb", "adb.exe"):
        found = shutil.which(name)
        if found:
            return found
    return _platform_tools_adb() or _vendor_adb(emulator)


# --------------------------------------------------------------------------
# Runtime discovery
# --------------------------------------------------------------------------

def port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def adb_devices(adb: str) -> list[str]:
    """Serials currently listed as `device` (not `offline`/`unauthorized`)."""
    if not adb:
        return []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, timeout=8,
                             creationflags=flags).stdout.decode("utf-8", "ignore")
    except (OSError, subprocess.TimeoutExpired):
        return []
    serials = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] != "List" and parts[1] == "device":
            serials.append(parts[0])
    return serials


def running_emulator_processes() -> list[str]:
    """Names of emulator processes running right now (best effort, no psutil)."""
    names = []
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist"], capture_output=True, timeout=8).stdout
            text = out.decode("utf-8", "ignore").lower()
            for key, p in EMULATORS.items():
                if any(proc.lower() in text for proc in p.get("processes", [])):
                    names.append(key)
        else:
            out = subprocess.run(["ps", "-eo", "comm"], capture_output=True, timeout=8).stdout
            text = out.decode("utf-8", "ignore").lower()
            for key, p in EMULATORS.items():
                if any(proc.lower() in text for proc in p.get("processes", [])):
                    names.append(key)
    except (OSError, subprocess.TimeoutExpired):
        pass
    # de-duplicate, keep profile order
    return [k for k in PROFILE_ORDER if k in names]


def discover(adb: str, emulator: str = "generic", host: str = "127.0.0.1") -> list[dict]:
    """Everything the GUI needs to offer a sensible default.

    Returns a list of candidates: {"serial", "kind", "label"} where kind is
    "tcp" or "usb". Already-connected devices come first because they need no
    probing.
    """
    found = []
    for serial in adb_devices(adb):
        found.append({"serial": serial, "kind": "usb", "label": serial})

    seen = {f["serial"] for f in found}
    for port in expected_ports(emulator):
        serial = f"{host}:{port}"
        if serial in seen:
            continue
        if port_open(host, port):
            found.append({"serial": serial, "kind": "tcp", "label": serial})
    return found


def emulator_of_serial(serial: str) -> str:
    """Best-effort guess at which emulator a serial belongs to.

    Port 5555 is genuinely ambiguous -- it is LDPlayer's first instance and also
    BlueStacks' -- so it is reported as LDPlayer, which is what CoC users run.
    BlueStacks is only claimed for the ports that are uniquely its own
    (5565, 5575, ...), which is what `port_step: 10` produces.
    """
    if re.match(r"emulator-\d+", serial or ""):
        return "androidstudio"
    if ":" not in (serial or ""):
        return "generic"
    try:
        port = int(serial.rsplit(":", 1)[1])
    except ValueError:
        return "generic"
    if 16384 <= port <= 16600:
        return "mumu"
    if 62000 <= port <= 62100:
        return "nox"
    if 21500 <= port <= 21600:
        return "memu"
    if port in (6555, 6556, 6557):
        return "genymotion"
    if port > 5555 and port % 10 == 5 and (port - 5555) % 10 == 0:
        return "bluestacks"
    if 5555 <= port <= 5600:
        return "ldplayer"
    return "generic"
