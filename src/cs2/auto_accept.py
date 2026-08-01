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
user32.PrintWindow.restype = wintypes.BOOL
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]


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
ACCEPT_COOLDOWN_SEC = 5.0  # safety net when session_id is missing

# proven click spot — scales with the client rect; detection below only decides WHEN to click
ACCEPT_BUTTON_X_RATIO = 0.5
ACCEPT_BUTTON_Y_RATIO = 0.42


def _force_foreground(hwnd) -> bool:
    """Bring hwnd to foreground; the ALT nudge lifts the SetForegroundWindow restriction."""
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    win32api.keybd_event(0x12, 0, 0, 0)                          # ALT down
    win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)   # ALT up
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def _is_accept_green(r: int, g: int, b: int) -> bool:
    """Full-strength button green (54,183,82) only — mid-fade/blended greens must fail."""
    return g >= 140 and (g - r) >= 80 and (g - b) >= 80


# central band of the client rect where the ready-check popup lives
SCAN_X0, SCAN_X1 = 0.20, 0.80
SCAN_Y0, SCAN_Y1 = 0.25, 0.75
MIN_BUTTON_PTS = 20    # measured: button = 111 grid points (49 when half-covered), title/icons ≤ 42
MIN_BUTTON_FILL = 0.6  # measured: button region is 0.87 solid green, title lettering only 0.10

CLICK_THROTTLE_SEC = 0.5      # min gap between clicks
POLL_INTERVAL_SEC = 0.1       # re-scan rate while the button is on screen (a scan is ~16ms)
MAX_ACCEPT_SECONDS = 25.0     # ready-check lifetime — keep watching this long
WINDOW_CHECK_INTERVAL = 0.4   # slower rate while it isn't (PrintWindow is heavier)


def _grab_pixels(capture, cw: int, ch: int):
    """Capture cw×ch via capture(memdc, screen_dc) into a top-down BGRA buffer."""
    screen = user32.GetDC(0)
    if not screen:
        return None
    memdc = gdi32.CreateCompatibleDC(screen)
    hbm = gdi32.CreateCompatibleBitmap(screen, cw, ch)
    old = gdi32.SelectObject(memdc, hbm)
    try:
        if not capture(memdc, screen):
            return None
        gdi32.SelectObject(memdc, old)  # deselect before GetDIBits
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = cw
        bmi.biHeight = -ch   # negative → top-down rows
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf = (ctypes.c_ubyte * (cw * ch * 4))()
        if gdi32.GetDIBits(memdc, hbm, 0, ch, buf, ctypes.byref(bmi), 0) == 0:
            return None
        return buf
    finally:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(0, screen)


def _scan_band(buf, buf_w: int, x0: int, y0: int, x1: int, y1: int):
    """Find the button: the one big, mostly-solid green region (title/timer/checkmarks are not)."""
    stride = buf_w * 4
    step = max(8, (y1 - y0) // 39)   # 22px at 1728p — scales so grid density matches any resolution
    green = {}
    y = y0
    while y < y1:
        row = y * stride
        x = x0
        while x < x1:
            i = row + x * 4        # buffer is BGRA
            r, g, b = buf[i + 2], buf[i + 1], buf[i]
            if _is_accept_green(r, g, b):
                green[(x, y)] = (r, g, b)
            x += step
        y += step

    todo = set(green)
    best = None
    while todo:
        comp = [todo.pop()]
        for cx, cy in comp:        # comp grows while iterating — flood fill
            for n in ((cx - step, cy), (cx + step, cy), (cx, cy - step), (cx, cy + step)):
                if n in todo:
                    todo.remove(n)
                    comp.append(n)
        if len(comp) < MIN_BUTTON_PTS or (best and len(comp) <= best[0]):
            continue
        xs = sorted(p[0] for p in comp)
        ys = sorted(p[1] for p in comp)
        bw, bh = xs[-1] - xs[0] + step, ys[-1] - ys[0] + step
        if len(comp) * step * step >= MIN_BUTTON_FILL * bw * bh:
            best = (len(comp), xs[0] + bw // 2, ys[0] + bh // 2)
    if best is None:
        return None
    hits, bx, by = best
    mean = tuple(sum(green[p][k] for p in green) // len(green) for k in range(3))
    return (bx, by, hits, mean)


def _find_accept_button(ox: int, oy: int, w: int, h: int):
    """Find the button as visible on screen → (screen_x, screen_y, hits, mean_rgb)."""
    rx, ry = ox + int(w * SCAN_X0), oy + int(h * SCAN_Y0)
    rw, rh = int(w * (SCAN_X1 - SCAN_X0)), int(h * (SCAN_Y1 - SCAN_Y0))
    if rw <= 0 or rh <= 0:
        return None
    buf = _grab_pixels(
        lambda memdc, screen: gdi32.BitBlt(memdc, 0, 0, rw, rh, screen, rx, ry, _SRCCOPY),
        rw, rh,
    )
    if buf is None:
        return None
    found = _scan_band(buf, rw, 0, 0, rw, rh)
    if found is None:
        return None
    cx, cy, hits, rgb = found
    return (rx + cx, ry + cy, hits, rgb)


def _banner_in_window(hwnd, w: int, h: int) -> bool:
    """Banner still in CS2's own rendered content, even if covered on screen?"""
    if w <= 0 or h <= 0:
        return False
    # 1|2 = PW_CLIENTONLY | PW_RENDERFULLCONTENT
    buf = _grab_pixels(lambda memdc, screen: user32.PrintWindow(hwnd, memdc, 1 | 2), w, h)
    return buf is not None and _scan_band(buf, w,
                                          int(w * SCAN_X0), int(h * SCAN_Y0),
                                          int(w * SCAN_X1), int(h * SCAN_Y1)) is not None


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

        _force_foreground(hwnd)

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        click_x = origin.x + int(w * ACCEPT_BUTTON_X_RATIO)
        click_y = origin.y + int(h * ACCEPT_BUTTON_Y_RATIO)

        start = time.time()
        clicks = 0
        seen = False            # banner detected at least once
        waiting_logged = False
        last_click = 0.0

        while time.time() - start < MAX_ACCEPT_SECONDS:
            found = _find_accept_button(origin.x, origin.y, w, h)
            now = time.time()

            if found:
                bx, by, hits, rgb = found
                if not seen:
                    logger.info(f"CS2 accept button at ({bx},{by}) [{hits} pts, rgb={rgb}], clicking ({click_x},{click_y})")
                seen = True
                # still on screen = not accepted; click the fixed spot (CS2 foreground only) until gone
                if now - last_click >= CLICK_THROTTLE_SEC:
                    if user32.GetForegroundWindow() == hwnd or _force_foreground(hwnd):
                        self._click_at(click_x, click_y)
                        clicks += 1
                        last_click = now
                time.sleep(POLL_INTERVAL_SEC)
                continue

            if _banner_in_window(hwnd, w, h):
                # the game still shows it, just not on screen: covered or behind
                seen = True
                if user32.GetForegroundWindow() != hwnd:
                    _force_foreground(hwnd)
                elif not waiting_logged:
                    logger.info("CS2 accept button covered by an always-on-top window — waiting")
                    waiting_logged = True
            elif seen:
                logger.info(f"CS2 accepted — banner gone after {clicks} click(s)")
                return
            time.sleep(WINDOW_CHECK_INTERVAL)

        logger.info(f"CS2 accept gave up after {MAX_ACCEPT_SECONDS:.0f}s — {clicks} click(s), seen={seen}")

    def _click_at(self, x: int, y: int):
        # 50ms delays are required for CS2's menu to register the click — do not shorten
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
