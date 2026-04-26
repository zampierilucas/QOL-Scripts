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
# Safety net cooldown — only triggers if session_id is missing for some reason.
# Primary dedup is per-session-id (set by CS2 for each new banner).
ACCEPT_COOLDOWN_SEC = 5.0

ACCEPT_BUTTON_X_RATIO = 0.5
ACCEPT_BUTTON_Y_RATIO = 0.42

# Click for ~3 seconds total to better catch the banner across UI transitions.
CLICK_RETRIES = 6
CLICK_RETRY_DELAY_SEC = 0.5


class CS2AutoAccept:
    def __init__(self, settings):
        self.settings = settings
        self._last_accept_time = 0.0
        self._last_session_id = None

    def on_match_found(self, session_id=None):
        if not self.settings.data.get("cs2_auto_accept_enabled", True):
            logger.debug("CS2 match found but auto-accept is disabled")
            return

        # Primary dedup: don't fire twice for the same banner reservation.
        if session_id and session_id == self._last_session_id:
            logger.debug(f"CS2 already accepted session {session_id}, skipping")
            return

        # Secondary dedup: short time-based cooldown as a safety net.
        now = time.time()
        if now - self._last_accept_time < ACCEPT_COOLDOWN_SEC:
            logger.debug("CS2 accept cooldown active, skipping")
            return

        self._last_session_id = session_id
        self._last_accept_time = now
        logger.info(f"CS2 accepting match (session {session_id})")
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

        # Click repeatedly across a ~3s window. Defensive against the banner
        # taking longer than expected to render or losing focus mid-attempt.
        for _ in range(CLICK_RETRIES):
            # Re-assert foreground each loop so the click lands on CS2 even if
            # the user briefly clicks elsewhere
            user32.SetForegroundWindow(hwnd)
            self._click_at(click_x, click_y)
            time.sleep(CLICK_RETRY_DELAY_SEC)

        logger.info(f"CS2 match auto-accepted (clicked {CLICK_RETRIES}x at {click_x}, {click_y})")

    def _click_at(self, x: int, y: int):
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
