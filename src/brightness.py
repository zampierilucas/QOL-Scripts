import re
import logging
import screen_brightness_control as sbc
from threading import Lock, Thread
from win32gui import GetForegroundWindow
from win32api import EnumDisplayDevices, GetMonitorInfo, MonitorFromWindow
from win32con import MONITOR_DEFAULTTONEAREST

logger = logging.getLogger(__name__)

# Module-level monitor cache (populated once at startup)
_monitors_cache = None


def init_monitors_cache():
    """Initialize the monitor cache. Call once at startup."""
    global _monitors_cache
    _monitors_cache = sbc.list_monitors_info()
    logger.info(f"Cached {len(_monitors_cache)} monitors")
    return _monitors_cache


def get_cached_monitors():
    """Get cached monitor info. Returns empty list if not initialized."""
    if _monitors_cache is None:
        logger.warning("Monitor cache not initialized, initializing now")
        return init_monitors_cache()
    return _monitors_cache


def get_all_monitor_serials():
    """Get all monitor serials from cache."""
    return [info.get('serial') for info in get_cached_monitors()]


def clean_window_title(title):
    """
    Remove invisible Unicode characters from window titles.
    Some games use zero-width spaces and other invisible chars in their titles.
    """
    invisible_chars = [
        '\ufeff',  # BOM / Zero-width no-break space
        '\u200b',  # Zero-width space
        '\u200c',  # Zero-width non-joiner
        '\u200d',  # Zero-width joiner
        '\u2005',  # Four-per-em space
        '\u2004',  # Three-per-em space
        '\u2003',  # Em space
        '\u2002',  # En space
        '\u00a0',  # Non-breaking space
        '\u2060',  # Word joiner
        '\u180e',  # Mongolian vowel separator
    ]
    for char in invisible_chars:
        title = title.replace(char, '')
    return title.strip()


def set_brightness_side_monitors(brightness, monitor_ids):
    """Set brightness for the specified monitors.

    Skips falsy ids — passing display=None to sbc would target ALL monitors,
    causing unflagged monitors (like the main display) to dim unintentionally."""
    for monitor_id in monitor_ids:
        if not monitor_id:
            logger.debug("Skipping monitor with empty id (would target all monitors)")
            continue
        try:
            sbc.set_brightness(brightness, display=monitor_id)
        except Exception as e:
            logger.error(f"Failed to set brightness for {monitor_id}: {e}")


def get_focused_monitor_info():
    """
    Get information about the monitor containing the focused window
    Returns the monitor's device name or None if not found
    """
    try:
        focused_window = GetForegroundWindow()
        if not focused_window:
            logger.debug("No focused window found")
            return None

        monitor_handle = MonitorFromWindow(focused_window, MONITOR_DEFAULTTONEAREST)
        if not monitor_handle:
            logger.debug("No monitor handle found for focused window")
            return None

        monitor_info = GetMonitorInfo(monitor_handle)
        if not monitor_info:
            logger.debug("No monitor info found")
            return None

        device_name = monitor_info.get('Device', '')
        logger.debug(f"Focused monitor device: {device_name}")
        return device_name

    except Exception as e:
        logger.error(f"Error getting focused monitor info: {e}")
        return None


def get_focused_monitor_uid():
    """UID of the focused window's monitor — matches sbc's 'uid' info field."""
    device_name = get_focused_monitor_info()
    if not device_name:
        return None
    try:
        # flag 1 (EDD_GET_DEVICE_INTERFACE_NAME) populates DeviceID
        for adaptor_index in range(5):
            try:
                device = EnumDisplayDevices(device_name, adaptor_index, 1)
            except Exception:
                continue
            match = re.search(r'UID(\d+)', device.DeviceID)
            if match:
                return match.group(1)
        logger.debug(f"No device UID found for {device_name}")
        return None
    except Exception as e:
        logger.error(f"Error resolving focused monitor UID: {e}")
        return None


def get_all_monitor_serials_except_focused():
    """All monitor serials except the focused one; [] if it can't be identified."""
    try:
        focused_uid = get_focused_monitor_uid()
        if focused_uid is None:
            logger.debug("Cannot identify focused monitor, returning empty list")
            return []

        monitors_info = get_cached_monitors()
        if not any(info.get('uid') == focused_uid for info in monitors_info):
            logger.debug(f"Focused monitor UID {focused_uid} not in monitor list, returning empty list")
            return []

        non_focused_serials = [
            info.get('serial', '') for info in monitors_info
            if info.get('uid') != focused_uid
        ]
        logger.debug(f"Monitors to dim (serials): {non_focused_serials}")
        return non_focused_serials

    except Exception as e:
        logger.error(f"Error getting non-focused monitors: {e}")
        return []


class BrightnessFocusConsumer:
    """Daemon thread that subscribes to a FocusMonitor and dims/restores
    monitors when a tracked game gains/loses focus.

    Owns its own thread so its (potentially slow) sbc calls don't delay
    other focus consumers."""

    def __init__(self, settings, focus_monitor):
        self.settings = settings
        self.focus_monitor = focus_monitor
        self._thread = None
        self._running = False
        # serials we dimmed and have not yet restored
        self._dimmed = set()
        self._dimmed_lock = Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._run, daemon=True, name="brightness-focus")
        self._thread.start()

    def stop(self):
        # FocusMonitor.stop() will notify_all and our wait() returns None.
        self._running = False

    def restore_dimmed(self):
        """Restore every monitor this consumer dimmed to 'high'."""
        with self._dimmed_lock:
            dimmed = list(self._dimmed)
            self._dimmed.clear()
        if dimmed:
            logger.debug(f"Restoring previously dimmed monitors: {dimmed}")
            set_brightness_side_monitors(self.settings.data["monitor_brightness"]["high"], dimmed)

    def _run(self):
        sub = self.focus_monitor.subscribe()
        while self._running:
            title = sub.wait()
            if title is None:
                return  # monitor stopped
            try:
                self._apply(title)
            except Exception as e:
                logger.error(f"Brightness consumer error: {e}")

    def _apply(self, focused_title: str):
        if not self.settings.data["dimming_enabled"]:
            return
        games_list = self.settings.data['games_to_dimm']
        is_game_focused = focused_title in games_list
        dim_all_mode = self.settings.data["dim_all_except_focused"]
        brightness_settings = self.settings.data["monitor_brightness"]

        if is_game_focused:
            monitors_to_dim = (get_all_monitor_serials_except_focused()
                               if dim_all_mode
                               else self.settings.data["dimmable_monitors"])
            target = {m for m in monitors_to_dim if m}
            with self._dimmed_lock:
                no_longer_dimmed = self._dimmed - target
                newly_dimmed = target - self._dimmed
            if no_longer_dimmed:
                logger.debug(f"Restoring monitors that left the dim set: {sorted(no_longer_dimmed)}")
                set_brightness_side_monitors(brightness_settings["high"], no_longer_dimmed)
            if newly_dimmed:
                logger.debug(f"Game focused - dimming monitors: {sorted(newly_dimmed)}")
                set_brightness_side_monitors(brightness_settings["low"], newly_dimmed)
            with self._dimmed_lock:
                self._dimmed -= no_longer_dimmed
                self._dimmed |= newly_dimmed
        else:
            # only what we dimmed — other monitors may be manually adjusted
            self.restore_dimmed()
