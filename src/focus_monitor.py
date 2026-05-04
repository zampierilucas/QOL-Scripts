import ctypes
import logging
import threading
from ctypes import wintypes
from threading import Thread

from win32_window_monitor import (
    init_com, set_win_event_hook, get_window_title, HookEvent
)

from brightness import clean_window_title

logger = logging.getLogger(__name__)


def _run_message_loop():
    """Run WIN32 message loop until WM_QUIT is received.

    Workaround for win32-window-monitor bug using TranslateMessageW
    which doesn't exist (should be TranslateMessage).
    """
    user32 = ctypes.windll.user32
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


# Transient windows that briefly steal focus during alt-tab / desktop switching.
# We ignore these so they don't trip per-feature dedup or restore "default" state.
_TRANSIENT_TITLES = {'Task Switching', 'DesktopWindowXamlSource'}


class FocusMonitor:
    """Single daemon that watches Windows foreground/focus events and publishes
    the current focused-window title.

    Features don't register callbacks here — they call ``subscribe()`` and run
    their own thread that blocks on ``Subscription.wait()``. Each feature
    reacts to focus changes independently, so a slow consumer (e.g. a
    blocking ``screen_brightness_control`` retry loop) can't delay others.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._latest = None       # last cleaned title published
        self._raw_latest = None   # last raw title (for dedup)
        self._version = 0         # bumped on every publish; subscribers track this
        self._stopped = False

        self._thread_id = None
        self._hooks = []
        self._thread = None
        self._running = False

    def subscribe(self):
        """Return a Subscription handle. The subscriber is responsible for
        calling ``wait()`` from its own thread."""
        return Subscription(self)

    def get_focused(self):
        """Return the most recently published cleaned title (or None)."""
        with self._cond:
            return self._latest

    def reset(self):
        """Invalidate the dedup baseline so the next focus event publishes
        even if the focused window hasn't actually changed. Call after a
        settings save so consumers re-apply on the next focus change."""
        with self._cond:
            self._raw_latest = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._loop, daemon=True, name="focus-monitor")
        self._thread.start()
        logger.info("Focus monitor thread started")

    def stop(self):
        if not self._running:
            return
        self._running = False
        with self._cond:
            self._stopped = True
            self._cond.notify_all()  # wake all subscribers so they can exit
        if self._thread_id:
            WM_QUIT = 0x0012
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _on_event(self, _hook_handle, _event_id, _hwnd, _id_object,
                  _id_child, _event_thread_id, _event_time_ms):
        try:
            # Always read the current foreground window — EVENT_OBJECT_FOCUS can
            # fire for child controls whose hwnd isn't a top-level window.
            foreground_hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not foreground_hwnd:
                return
            focused = get_window_title(foreground_hwnd)
            if not focused or focused in _TRANSIENT_TITLES:
                return
            with self._cond:
                if focused == self._raw_latest:
                    return
                self._raw_latest = focused
                self._latest = clean_window_title(focused)
                self._version += 1
                self._cond.notify_all()
            logger.debug(f"Focus changed to: '{focused}'")
        except Exception as e:
            logger.error(f"Error in focus event: {e}")

    def _loop(self):
        try:
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            with init_com():
                for event in (HookEvent.SYSTEM_FOREGROUND,
                              HookEvent.SYSTEM_MINIMIZEEND,
                              HookEvent.OBJECT_FOCUS,
                              HookEvent.SYSTEM_SWITCHEND):
                    self._hooks.append(set_win_event_hook(self._on_event, event))
                logger.debug("Focus monitor hooks registered")
                _run_message_loop()
        except Exception as e:
            logger.error(f"Error in focus monitor loop: {e}")
        finally:
            for hook in self._hooks:
                if hook:
                    try:
                        hook.unhook()
                    except Exception:
                        pass
            self._hooks.clear()


class Subscription:
    """Per-consumer cursor over the FocusMonitor's published state.

    ``wait()`` blocks until the publisher's version moves past the
    subscriber's local cursor (or the monitor stops). It returns the latest
    cleaned title — intermediate updates that arrived while the subscriber
    was busy are coalesced into "latest", so consumers naturally skip stale
    work. Returns None when the monitor has been stopped.
    """

    def __init__(self, monitor: FocusMonitor):
        self._monitor = monitor
        self._last_seen_version = 0

    def wait(self) -> str | None:
        with self._monitor._cond:
            while (self._last_seen_version == self._monitor._version
                   and not self._monitor._stopped):
                self._monitor._cond.wait()
            if self._monitor._stopped:
                return None
            self._last_seen_version = self._monitor._version
            return self._monitor._latest
