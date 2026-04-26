import logging
import time
import ctypes
from ctypes import wintypes
from threading import Thread

import win32api
import win32con

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

CS2_WINDOW_TITLE = "Counter-Strike 2"
ACCEPT_COOLDOWN_SEC = 10.0

ACCEPT_BUTTON_X_RATIO = 0.5
ACCEPT_BUTTON_Y_RATIO = 0.42


class CS2AutoAccept:
    def __init__(self, settings):
        self.settings = settings
        self._last_accept_time = 0.0

    def on_match_found(self):
        if not self.settings.data.get("cs2_auto_accept_enabled", True):
            logger.debug("CS2 match found but auto-accept is disabled")
            return

        now = time.time()
        if now - self._last_accept_time < ACCEPT_COOLDOWN_SEC:
            logger.debug("CS2 accept cooldown active, skipping")
            return

        self._last_accept_time = now
        Thread(target=self._accept_match, daemon=True).start()

    def _accept_match(self):
        # Wait for the accept banner to appear before clicking
        time.sleep(1.5)

        hwnd = user32.FindWindowW(None, CS2_WINDOW_TITLE)
        if not hwnd:
            logger.warning("CS2 window not found for auto-accept")
            return

        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.6)

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))

        point = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(point))

        width = rect.right - rect.left
        height = rect.bottom - rect.top

        click_x = point.x + int(width * ACCEPT_BUTTON_X_RATIO)
        click_y = point.y + int(height * ACCEPT_BUTTON_Y_RATIO)

        # Click 3 times with small gaps to ensure it registers
        for _ in range(3):
            self._click_at(click_x, click_y)
            time.sleep(0.3)

        logger.info(f"CS2 match auto-accepted (clicked at {click_x}, {click_y})")

    def _click_at(self, x: int, y: int):
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
