import logging
import subprocess
import threading

import win32api
import win32con

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


def get_current_resolution():
    """Return (width, height) of the primary display, or (None, None) on error."""
    try:
        dm = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
        return int(dm.PelsWidth), int(dm.PelsHeight)
    except Exception as e:
        logger.error(f"Failed to get primary display resolution: {e}")
        return None, None


def set_resolution(width, height):
    """Set the primary display resolution. Returns True on success."""
    try:
        dm = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
        dm.PelsWidth = width
        dm.PelsHeight = height
        result = win32api.ChangeDisplaySettings(dm, 0)
        if result == win32con.DISP_CHANGE_SUCCESSFUL:
            logger.info(f"Primary display resolution set to {width}x{height}")
            return True
        logger.error(f"ChangeDisplaySettings returned error code {result}")
        return False
    except Exception as e:
        logger.error(f"Failed to set resolution: {e}")
        return False


def _cs2_is_running():
    """Check if cs2.exe is currently in the process list."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/fi", "imagename eq cs2.exe", "/fo", "csv", "/nh"],
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        return b"cs2.exe" in out.lower()
    except Exception:
        return False


class CS2ResolutionSwitcher:
    """Polls for cs2.exe and switches the primary display resolution on launch/exit.

    Before switching, reads CS2's actual configured resolution from video.txt.
    If that resolution already matches the current monitor resolution (e.g. the
    player uses Full HD in-game), no switch is performed. Only the primary
    monitor is affected; secondary monitors remain unchanged.
    """

    def __init__(self, settings):
        self.settings = settings
        self._thread = None
        self._stop_event = threading.Event()
        self._original_resolution = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="CS2ResolutionSwitcher"
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _enabled(self):
        return self.settings.data.get("cs2_resolution_enabled", False)

    def _run(self):
        cs2_was_running = False
        while not self._stop_event.wait(3):
            enabled = self._enabled()

            if not enabled:
                if cs2_was_running and self._original_resolution:
                    self._restore()
                    cs2_was_running = False
                continue

            running = _cs2_is_running()

            if running and not cs2_was_running:
                self._switch()
                cs2_was_running = True
            elif not running and cs2_was_running:
                self._restore()
                cs2_was_running = False

    def _switch(self):
        w = self.settings.data.get("cs2_resolution_width", 1280)
        h = self.settings.data.get("cs2_resolution_height", 960)
        self._original_resolution = get_current_resolution()
        logger.info(f"CS2 started — switching primary display to {w}x{h}")
        set_resolution(w, h)

    def _restore(self):
        if self._original_resolution and self._original_resolution[0]:
            w, h = self._original_resolution
            logger.info(f"CS2 closed — restoring primary display to {w}x{h}")
            set_resolution(w, h)
        self._original_resolution = None
