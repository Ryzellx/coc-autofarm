"""Static configuration.

Anything a user might want to change at runtime lives in ``settings.json``
instead (see :mod:`settings`); this dataclass holds what a developer pins:
model paths, YOLO class names, and the defaults a fresh install starts from.
"""

from dataclasses import dataclass, field


@dataclass
class AppConfig:
    model_path: str = "models/best.pt"
    conf: float = 0.5
    # None = auto-detect (use CUDA when available). Set True/False to force.
    gpu: bool | None = None
    max_retries: int = 4

    # ---- emulator ------------------------------------------------------
    # Which profile's adb/ports to use. See emulator.py for the full list.
    emulator: str = "ldplayer"
    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    # Empty = auto-detect (PATH / platform-tools / the vendor's own adb).
    adb_path: str = ""
    # An explicit serial wins over host:port; required for Android Studio AVDs
    # ("emulator-5554") and for a physically attached device.
    adb_serial: str = ""

    # ---- YOLO class labels used across the bot -------------------------
    ATTACK_BTN_HOME: str = "attack_btn_home"
    FIND_BTN: str = "find_btn"
    ATTACK_BTN: str = "attack_btn"

    # Resource text labels (top bar during search)
    GOLD: str = "gold"
    ELIXIR: str = "elixir"
    D_ELIXIR: str = "d_elixir"

    # Base layout labels
    S_GOBLIN: str = "s_goblin"
    ELIXIRS: str = "elixirs"
    NXT_BTN: str = "nxt_btn"
    GOLDS: str = "golds"
    D_ELIXIRS: str = "d_elixirs"

    # Post-battle / surrender labels
    SURR_BTN: str = "surr"
    SURR_CONF: str = "surr_conf"

    # Scan-failure recovery: spots tapped (one each) before retrying
    RETRY_BTN_1: str = "retry_btn_1"
    RETRY_BTN_2: str = "retry_btn_2"
    RETRY_BTN_3: str = "retry_btn_3"

    POST_GOLD: str = "post_gold"
    POST_ELIXIR: str = "post_elixir"
    POST_D_ELIXIR: str = "post_d_elixir"
    RETURN_HOME: str = "return_home"

    # ---- humanisation defaults (settings.json overrides these) ---------
    jitter_px: int = 6
    delay_sigma: float = 0.28
    pause_chance: float = 0.03
    humanize_enabled: bool = True


# Defaults for the tasks layer. Kept here so both the GUI and the bot read the
# same numbers; settings.json -> "tasks" overrides them per user.
DEFAULT_TASKS = {
    "upgrade": {"enabled": False, "priority": 10, "interval": 900.0},
    "collect": {"enabled": True, "priority": 20, "interval": 180.0},
    "train": {"enabled": False, "priority": 30, "interval": 420.0},
}
