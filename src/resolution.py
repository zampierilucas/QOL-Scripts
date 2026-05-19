import logging

import win32api
import win32con

logger = logging.getLogger(__name__)


def get_supported_resolutions():
    """Primary display's supported (width, height) pairs, largest first."""
    modes = set()
    i = 0
    while True:
        try:
            dm = win32api.EnumDisplaySettings(None, i)
            modes.add((int(dm.PelsWidth), int(dm.PelsHeight)))
            i += 1
        except Exception:
            break
    return sorted(modes, reverse=True)


def get_current_resolution():
    """Primary display (width, height), or (None, None) on error."""
    try:
        dm = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
        return int(dm.PelsWidth), int(dm.PelsHeight)
    except Exception as e:
        logger.error(f"Failed to get primary display resolution: {e}")
        return None, None


def set_resolution(width, height):
    """Set the primary display to the best-refresh valid mode for that size."""
    try:
        # Pick the highest-refresh enumerated mode for the target dimensions so
        # ChangeDisplaySettings never gets DISP_CHANGE_BADMODE from a mismatched
        # frequency.
        best_dm = None
        i = 0
        while True:
            try:
                dm = win32api.EnumDisplaySettings(None, i)
                if int(dm.PelsWidth) == width and int(dm.PelsHeight) == height:
                    if (best_dm is None
                            or dm.DisplayFrequency > best_dm.DisplayFrequency):
                        best_dm = dm
                i += 1
            except Exception:
                break
        if best_dm is None:
            logger.error(f"Unsupported resolution {width}x{height}")
            return False
        result = win32api.ChangeDisplaySettings(best_dm, 0)
        if result == win32con.DISP_CHANGE_SUCCESSFUL:
            logger.info(f"Primary display resolution set to {width}x{height}")
            return True
        logger.error(f"ChangeDisplaySettings returned error code {result}")
        return False
    except Exception as e:
        logger.error(f"Failed to set resolution: {e}")
        return False


class CS2ResolutionSwitcher:
    """Switches primary display resolution when CS2 starts; restores on exit."""

    def __init__(self, settings):
        self.settings = settings
        self._original_resolution = None

    def stop(self):
        self.on_cs2_stop()

    def _enabled(self):
        return self.settings.data.get("cs2_resolution_enabled", False)

    def on_cs2_start(self):
        if not self._enabled():
            return
        w = self.settings.data.get("cs2_resolution_width", 1280)
        h = self.settings.data.get("cs2_resolution_height", 960)
        self._original_resolution = get_current_resolution()
        logger.info(f"CS2 started — switching primary display to {w}x{h}")
        set_resolution(w, h)

    def on_cs2_stop(self):
        if self._original_resolution and self._original_resolution[0]:
            w, h = self._original_resolution
            logger.info(f"CS2 closed — restoring primary display to {w}x{h}")
            set_resolution(w, h)
        self._original_resolution = None
