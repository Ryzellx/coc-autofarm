class AutomationController:
    """
    Wrapper for tapping and key events, injected straight into the Android
    guest via ADB (guest-pixel coordinates, works with the window
    backgrounded). There is no desktop fallback.
    """

    def __init__(self, adb, humanizer=None):
        """Initialize automation controller.

        `humanizer` is optional so scripts and tests can drive the raw input
        path; when present, every tap is jittered inside its target.
        """
        if adb is None:
            raise ValueError("AutomationController requires a connected EmulatorAdb (ADB-only mode)")
        self.adb = adb
        self.humanizer = humanizer

    def click(self, x, y=None, radius=None):
        """
        Tap at coordinates (guest pixels). A single (x, y) tuple may also
        be passed.

        `radius` is the jitter budget in pixels; callers pass something
        derived from the element they are hitting so the tap cannot land
        outside the button.
        """
        if y is None:
            x, y = x
        if self.humanizer is not None:
            x, y = self.humanizer.jitter_point(x, y, radius=radius)
        if not self.adb.tap(int(x), int(y)):
            raise RuntimeError("ADB tap failed")

    def swipe(self, x1, y1, x2, y2, ms=300):
        if not self.adb.swipe(x1, y1, x2, y2, ms=ms):
            raise RuntimeError("ADB swipe failed")

    def send_key(self, key):
        """
        Send a key event to the guest via ADB.

        Args:
            key: key name (e.g., 'f12', 'enter', 'space')
        """
        keycode = _KEYEVENT_MAP.get(key)
        if keycode is None:
            raise ValueError(f"Unsupported key for ADB: {key!r}")
        if not self.adb.keyevent(keycode):
            raise RuntimeError(f"ADB keyevent failed for {key!r}")


_KEYEVENT_MAP = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "space": 62,
    "escape": 111,
}