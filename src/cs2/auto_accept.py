import logging
import time
import ctypes
from ctypes import wintypes
from threading import Thread

import win32api
import win32con

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# Per-pixel GetPixel on the desktop DC forces a DWM readback each call (~17ms),
# so scanning thousands of points that way takes ~a minute. Instead we BitBlt
# the region once into a memory DC and scan the resulting buffer in-process.
_SRCCOPY = 0x00CC0020
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]

CS2_WINDOW_TITLE = "Counter-Strike 2"
# Safety net cooldown — only triggers if session_id is missing for some reason.
# Primary dedup is per-session-id (set by CS2 for each new banner).
ACCEPT_COOLDOWN_SEC = 5.0

# The CS2 ready-check "Accept" button is a solid green (measured (54,183,82)).
# We LOCATE it by scanning for that green rather than assuming a fixed position
# (the popup location varies with resolution/UI scale), click its centre, then
# stop. CS2's console log (-condebug) does NOT record the local accept, so this
# screen-side signal is the only reliable "we succeeded" cue.
ACCEPT_GREEN = (54, 183, 82)


def _is_accept_green(r: int, g: int, b: int) -> bool:
    """Hue-dominance test: green clearly the strongest channel. Robust to
    digital-vibrance saturation boosts / gamma shifts, and won't match the
    beige menu behind the popup."""
    return g >= 70 and (g - r) >= 35 and (g - b) >= 35


# Scan a generous central band where the ready-check popup lives (fractions of
# the client rect). Coarse step keeps it light — the button is a large solid
# block, so a step this size still lands many hits on it.
SCAN_X0, SCAN_X1 = 0.20, 0.80
SCAN_Y0, SCAN_Y1 = 0.25, 0.75
SCAN_STEP_PX = 22
MIN_GREEN_HITS = 12          # need a big solid cluster, not stray/animated menu green
# The button is compact; reject green scattered across the scene (menu foliage).
MAX_CLUSTER_SPAN = 0.35      # green bounding box must fit within this fraction of the scan band

CLICK_THROTTLE_SEC = 0.5      # min gap between clicks
POLL_INTERVAL_SEC = 0.1       # how often we re-scan (a scan is ~16ms)
MAX_ACCEPT_SECONDS = 10.0     # overall cap on the attempt
MAX_LOCATED_CLICKS = 2        # once we've clicked the located button this many times, we're done
DETECT_DEADLINE_SEC = 4.0     # if not located by now, fall back to a few blind clicks
MAX_BLIND_CLICKS = 3          # minimal fallback so accepts still land if the scan fails
ACCEPT_BUTTON_Y_RATIO = 0.42  # blind-fallback vertical position (centre-ish)


def _find_accept_button(ox: int, oy: int, w: int, h: int):
    """BitBlt the central band of the CS2 client area, then scan the buffer for
    the green Accept button. Returns (screen_x, screen_y, green_hits, mean_rgb)
    of the green cluster's centroid, or None. mean_rgb is logged so we can tell
    the accept button's green apart from other green UI (e.g. the post-accept
    checkmark panel) when tuning."""
    rx, ry = ox + int(w * SCAN_X0), oy + int(h * SCAN_Y0)
    rw, rh = int(w * (SCAN_X1 - SCAN_X0)), int(h * (SCAN_Y1 - SCAN_Y0))
    if rw <= 0 or rh <= 0:
        return None

    screen = user32.GetDC(0)
    if not screen:
        return None
    memdc = gdi32.CreateCompatibleDC(screen)
    hbm = gdi32.CreateCompatibleBitmap(screen, rw, rh)
    old = gdi32.SelectObject(memdc, hbm)
    try:
        if not gdi32.BitBlt(memdc, 0, 0, rw, rh, screen, rx, ry, _SRCCOPY):
            return None
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = rw
        bmi.biHeight = -rh   # negative → top-down rows
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf = (ctypes.c_ubyte * (rw * rh * 4))()
        if gdi32.GetDIBits(memdc, hbm, 0, rh, buf, ctypes.byref(bmi), 0) == 0:
            return None

        stride = rw * 4
        step = SCAN_STEP_PX
        sum_x = sum_y = hits = 0
        sum_r = sum_g = sum_b = 0
        min_x = min_y = 1 << 30
        max_x = max_y = -1
        y = 0
        while y < rh:
            row = y * stride
            x = 0
            while x < rw:
                i = row + x * 4        # buffer is BGRA
                r, g, b = buf[i + 2], buf[i + 1], buf[i]
                if _is_accept_green(r, g, b):
                    sum_x += x
                    sum_y += y
                    sum_r += r
                    sum_g += g
                    sum_b += b
                    hits += 1
                    min_x = min(min_x, x)
                    max_x = max(max_x, x)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y)
                x += step
            y += step
        if hits < MIN_GREEN_HITS:
            return None
        # Reject green scattered across the scene (animated menu foliage): the
        # accept button's green must be a compact block.
        if (max_x - min_x) > MAX_CLUSTER_SPAN * rw or (max_y - min_y) > MAX_CLUSTER_SPAN * rh:
            return None
        mean_rgb = (sum_r // hits, sum_g // hits, sum_b // hits)
        return (rx + sum_x // hits, ry + sum_y // hits, hits, mean_rgb)
    finally:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(0, screen)


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
        hwnd = user32.FindWindowW(None, CS2_WINDOW_TITLE)
        if not hwnd:
            logger.warning("CS2 window not found for auto-accept")
            return

        # Foreground immediately — no blind pre-sleep. The scan loop below only
        # clicks once the green button is actually rendered, so it naturally
        # waits out both the foreground transition and the banner fade-in.
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        blind_x = origin.x + w // 2
        blind_y = origin.y + int(h * ACCEPT_BUTTON_Y_RATIO)

        def click(x, y):
            # never click unless CS2 really is foreground — SetForegroundWindow
            # can be refused, and the click would land in the user's active app
            if user32.GetForegroundWindow() != hwnd:
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.05)
                if user32.GetForegroundWindow() != hwnd:
                    logger.debug("CS2 not foreground; skipping click")
                    return False
            self._click_at(x, y)
            return True

        start = time.time()
        clicks = 0          # clicks on the located button
        blind_clicks = 0    # fallback clicks when never located
        saw_button = False
        last_click = 0.0

        while time.time() - start < MAX_ACCEPT_SECONDS:
            found = _find_accept_button(origin.x, origin.y, w, h)
            now = time.time()

            if found:
                bx, by, hits, rgb = found
                if not saw_button:
                    logger.info(
                        f"CS2 accept button located at ({bx},{by}) "
                        f"[{hits} green hits, mean_rgb={rgb} #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}]"
                    )
                saw_button = True
                if now - last_click >= CLICK_THROTTLE_SEC and click(bx, by):
                    clicks += 1
                    last_click = now
                    if clicks >= MAX_LOCATED_CLICKS:
                        # First click accepts; a second is insurance. Done —
                        # don't keep clicking (that's the behaviour we're fixing).
                        logger.info(
                            f"CS2 accepted — clicked located button {clicks}x at ({bx},{by}), "
                            f"last mean_rgb={rgb}"
                        )
                        return
            elif saw_button and clicks > 0:
                # Button was there, we clicked, now it's gone → accepted. Stop.
                logger.info(f"CS2 accept button gone after {clicks} click(s) — accepted")
                return
            elif (now - start >= DETECT_DEADLINE_SEC
                  and blind_clicks < MAX_BLIND_CLICKS
                  and now - last_click >= CLICK_THROTTLE_SEC):
                # blind fallback when the button was never located
                if click(blind_x, blind_y):
                    blind_clicks += 1
                    last_click = now

            time.sleep(POLL_INTERVAL_SEC)

        logger.info(
            f"CS2 accept finished — {clicks} located click(s), "
            f"{blind_clicks} blind, button_detected={saw_button}"
        )

    def _click_at(self, x: int, y: int):
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
