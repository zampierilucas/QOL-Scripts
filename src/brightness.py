import re
import logging
import screen_brightness_control as sbc
from win32gui import GetForegroundWindow
from win32api import GetMonitorInfo, MonitorFromWindow
from win32con import MONITOR_DEFAULTTONEAREST

logger = logging.getLogger(__name__)


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
    """
    Sets the brightness for any side monitors configured in settings
    """
    changed = []
    unchanged = []
    for monitor_id in monitor_ids:
        try:
            current_brightness = sbc.get_brightness(display=monitor_id)[0]
            if current_brightness != brightness:
                sbc.set_brightness(brightness, display=monitor_id)
                changed.append(monitor_id)
            else:
                unchanged.append(monitor_id)
        except Exception as e:
            logger.error(f"Failed to set brightness for monitor ID {monitor_id}: {e}")
    if logger.isEnabledFor(logging.DEBUG):
        if changed:
            logger.debug(f"Set brightness to {brightness} for: {', '.join(changed)}")
        if unchanged:
            logger.debug(f"Brightness already {brightness} for: {', '.join(unchanged)}")


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


def get_all_monitor_serials_except_focused():
    """
    Get all monitor serials except the one containing the focused window
    """
    try:
        focused_device = get_focused_monitor_info()
        if not focused_device:
            logger.debug("Cannot detect focused monitor, returning empty list")
            return []

        match = re.search(r'DISPLAY(\d+)', focused_device)
        if match:
            focused_display_index = int(match.group(1)) - 1
            logger.debug(f"Focused display index: {focused_display_index}")
        else:
            logger.debug(f"Could not parse display number from: {focused_device}")
            focused_display_index = 0

        monitors_info = sbc.list_monitors_info()
        logger.debug(f"Total monitors found: {len(monitors_info)}")

        non_focused_serials = []

        for idx, info in enumerate(monitors_info):
            serial = info.get('serial', '')
            logger.debug(f"Monitor {idx}: serial={serial}, name={info.get('name', 'Unknown')}")

            if idx != focused_display_index:
                non_focused_serials.append(serial)
                logger.debug("  -> Adding to dim list")
            else:
                logger.debug("  -> Skipping (focused monitor)")

        logger.debug(f"Monitors to dim (serials): {non_focused_serials}")
        return non_focused_serials

    except Exception as e:
        logger.error(f"Error getting non-focused monitors: {e}")
        return []
