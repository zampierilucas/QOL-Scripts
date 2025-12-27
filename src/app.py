import time
import os
import sys
import logging
import base64
import io
import signal
import shutil
import webbrowser
import tomllib
import pathlib
from threading import Thread
from packaging import version as pkg_version

import requests
import pystray
from PIL import Image
from win32_window_monitor import (
    init_com, set_win_event_hook, get_window_title, HookEvent
)
import ctypes
from ctypes import wintypes
import winshell
from win32com.client import Dispatch

from settings import Settings, PROGRAM_NAME


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


from brightness import (
    set_brightness_side_monitors, get_all_monitor_serials_except_focused,
    clean_window_title, init_monitors_cache, get_all_monitor_serials
)
from lol import LoLAutoAccept, LoLAutoPick, SharedLCUConnector
from settings_window import SettingsWindow

try:
    from _version import VERSION as _BUNDLED_VERSION
except ImportError:
    _BUNDLED_VERSION = None


def _get_version():
    """Get version from pyproject.toml (dev) or _version.py (compiled exe)"""
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    if pyproject.exists():
        try:
            with open(pyproject, "rb") as f:
                return tomllib.load(f)["project"]["version"]
        except Exception:
            pass
    return _BUNDLED_VERSION or "unknown"


VERSION = _get_version()
GITHUB_REPO = "zampierilucas/QOL-Scripts"
UPDATE_CHECK_INTERVAL = 4 * 60 * 60  # 4 hours in seconds

logger = logging.getLogger(__name__)


class QOLApp:
    def __init__(self):
        self.settings = Settings()

        # Update checker state (must be before create_tray_icon)
        self.update_available = False
        self.latest_version = None

        self.create_tray_icon()
        self.running = True
        self.install_dir = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', PROGRAM_NAME)
        self.startup_shortcut = os.path.join(winshell.startup(), f"{PROGRAM_NAME}.lnk")
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        self.last_focused_window = None
        self.last_brightness_state = None

        # Create shared LCU connector with handlers
        self.lol_auto_accept = LoLAutoAccept(self.settings)
        self.lol_auto_pick = LoLAutoPick(self.settings)
        self.lcu_connector = SharedLCUConnector()
        self.lcu_connector.register_handler(self.lol_auto_accept)
        self.lcu_connector.register_handler(self.lol_auto_pick)
        self.lcu_connector.register_close_callback(lambda _: self.lol_auto_accept.on_disconnect())
        self.lcu_connector.register_close_callback(lambda _: self.lol_auto_pick.on_disconnect())

        self.settings_requested = False
        self.settings_window = None
        self._focus_monitor_thread_id = None

        try:
            logger.info("Initializing monitor cache and setting brightness to 100%")
            init_monitors_cache()
            set_brightness_side_monitors(100, get_all_monitor_serials())
        except Exception as e:
            logger.error(f"Failed to initialize monitors on startup: {e}")

    def signal_handler(self, signum, frame):
        logger.debug("Received signal to terminate. Cleaning up...")
        self.stop()

    def is_startup_enabled(self):
        """Check if app is registered to run at startup"""
        return os.path.exists(self.startup_shortcut)

    def get_executable_path(self):
        """Get the path to the current executable"""
        if getattr(sys, 'frozen', False):
            return sys.executable
        else:
            return os.path.abspath(sys.argv[0])

    def get_installed_exe_path(self):
        """Get path to the installed executable"""
        exe_name = os.path.basename(self.get_executable_path())
        return os.path.join(self.install_dir, exe_name)

    def toggle_startup(self, icon, item):
        """Toggle startup by installing app and managing shortcut"""
        try:
            if self.is_startup_enabled():
                os.remove(self.startup_shortcut)
                logger.info("Removed from startup")
            else:
                if not getattr(sys, 'frozen', False):
                    logger.warning("Startup installation is only available when running from the compiled .exe")
                    return

                os.makedirs(self.install_dir, exist_ok=True)

                current_exe = self.get_executable_path()
                installed_exe = self.get_installed_exe_path()

                if not os.path.exists(installed_exe) or os.path.getmtime(current_exe) > os.path.getmtime(installed_exe):
                    shutil.copy2(current_exe, installed_exe)
                    logger.info(f"Installed to: {installed_exe}")

                shell = Dispatch('WScript.Shell')
                shortcut = shell.CreateShortCut(self.startup_shortcut)
                shortcut.Targetpath = installed_exe
                shortcut.WorkingDirectory = self.install_dir
                shortcut.IconLocation = installed_exe
                shortcut.save()
                logger.info(f"Added to startup: {installed_exe}")
        except Exception as e:
            logger.error(f"Failed to toggle startup: {e}")

    def check_for_updates(self):
        """Check GitHub releases for a newer version."""
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            latest_tag = data.get("tag_name", "").lstrip("v")
            if not latest_tag:
                return

            current = pkg_version.parse(VERSION)
            latest = pkg_version.parse(latest_tag)

            if latest > current:
                self.latest_version = latest_tag
                self.update_available = True
                logger.info(f"Update available: v{latest_tag} (current: v{VERSION})")
                self._rebuild_menu()
            else:
                logger.debug(f"No update available (current: v{VERSION}, latest: v{latest_tag})")
        except Exception as e:
            logger.debug(f"Failed to check for updates: {e}")

    def _update_check_loop(self):
        """Background loop to periodically check for updates."""
        while self.running:
            self.check_for_updates()
            for _ in range(UPDATE_CHECK_INTERVAL):
                if not self.running:
                    break
                time.sleep(1)

    def _open_releases(self, icon=None, item=None):
        """Open the GitHub releases page."""
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _rebuild_menu(self):
        """Rebuild the tray menu (used when update becomes available)."""
        self._build_menu()
        self.icon.menu = self._menu

    def create_tray_icon(self):
        try:
            icon_base64 = "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"
            icon_data = base64.b64decode(icon_base64)
            self._icon_image = Image.open(io.BytesIO(icon_data))
        except Exception as e:
            logger.error(f"Failed to load icon: {e}")
            self._icon_image = Image.new('RGB', (64, 64), color='red')

        self._build_menu()
        self.icon = pystray.Icon(
            PROGRAM_NAME,
            self._icon_image,
            PROGRAM_NAME,
            menu=self._menu
        )

    def _build_menu(self):
        """Build the tray menu items."""
        def check_dimming(item):
            return self.settings.data["dimming_enabled"]

        def check_auto_accept(item):
            return self.settings.data["auto_accept_enabled"]

        def check_auto_pick(item):
            return self.settings.data["auto_pick_enabled"]

        def check_auto_lock(item):
            return self.settings.data.get("auto_lock_enabled", True)

        def toggle_dimming(icon, item):
            self.settings.data["dimming_enabled"] = not self.settings.data["dimming_enabled"]
            self.settings.save_settings()
            if not self.settings.data["dimming_enabled"]:
                set_brightness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )

        def toggle_auto_accept(icon, item):
            self.settings.data["auto_accept_enabled"] = not self.settings.data["auto_accept_enabled"]
            self.settings.save_settings()

        def toggle_auto_pick(icon, item):
            self.settings.data["auto_pick_enabled"] = not self.settings.data["auto_pick_enabled"]
            self.settings.save_settings()

        def toggle_auto_lock(icon, item):
            self.settings.data["auto_lock_enabled"] = not self.settings.data.get("auto_lock_enabled", True)
            self.settings.save_settings()

        def open_about(icon, item):
            webbrowser.open("https://github.com/zampierilucas/QOL-Scripts")

        menu_items = [
            pystray.MenuItem(f"{PROGRAM_NAME} v{VERSION}", None, enabled=False),
        ]

        # Add update available button if there's a new version
        if self.update_available and self.latest_version:
            menu_items.append(
                pystray.MenuItem(
                    f"Update available: v{self.latest_version}",
                    self._open_releases
                )
            )

        menu_items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "LoL - Auto Accept",
                toggle_auto_accept,
                checked=check_auto_accept
            ),
            pystray.MenuItem(
                "LoL - Auto Pick",
                toggle_auto_pick,
                checked=check_auto_pick
            ),
            pystray.MenuItem(
                "LoL - Auto Lock",
                toggle_auto_lock,
                checked=check_auto_lock
            ),
            pystray.MenuItem(
                "Dimming",
                toggle_dimming,
                checked=check_dimming
            ),
            pystray.MenuItem("Settings", self.show_settings),
            pystray.MenuItem("About", open_about),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.stop)
        ])

        self._menu = pystray.Menu(*menu_items)

    def show_settings(self, icon=None, item=None):
        self.settings_requested = True

    def stop(self, icon=None, item=None):
        if self.running:
            self.running = False
            if self.settings_window:
                try:
                    self.settings_window.root.destroy()
                except Exception:
                    pass
            self.lcu_connector.stop()
            # Stop the focus monitor thread by posting WM_QUIT to its message loop
            if self._focus_monitor_thread_id:
                WM_QUIT = 0x0012
                ctypes.windll.user32.PostThreadMessageW(
                    self._focus_monitor_thread_id, WM_QUIT, 0, 0
                )
            self.icon.stop()
            if self.settings.data["dimming_enabled"]:
                set_brightness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )

    def run(self):
        self.lcu_connector.start()
        Thread(target=self._focus_monitor_loop, daemon=True).start()
        Thread(target=self._update_check_loop, daemon=True).start()
        Thread(target=self.icon.run, daemon=True).start()

        while self.running:
            if self.settings_requested:
                self.settings_requested = False
                self.settings_window = SettingsWindow(self.settings, app=self)
                self.settings_window.root.mainloop()
                self.settings_window = None
            time.sleep(0.1)

    def _on_foreground_change(self, win_event_hook_handle, event_id: int,
                                hwnd: wintypes.HWND, id_object: wintypes.LONG,
                                id_child: wintypes.LONG, event_thread_id: wintypes.DWORD,
                                event_time_ms: wintypes.DWORD):
        """Callback for foreground window change events."""
        try:
            focused = get_window_title(hwnd)

            if focused != self.last_focused_window:
                self.last_focused_window = focused
                logger.debug(f"Focus changed to: '{focused}'")

                if self.settings.data["dimming_enabled"]:
                    games_list = self.settings.data['games_to_dimm']
                    cleaned_focused = clean_window_title(focused)
                    is_game_focused = cleaned_focused in games_list
                    brightness_state = (is_game_focused, self.settings.data["dim_all_except_focused"])

                    if brightness_state != self.last_brightness_state:
                        self.last_brightness_state = brightness_state

                        dim_all_mode = self.settings.data["dim_all_except_focused"]
                        brightness_settings = self.settings.data["monitor_brightness"]

                        if is_game_focused:
                            monitors_to_dim = (get_all_monitor_serials_except_focused()
                                               if dim_all_mode
                                               else self.settings.data["dimmable_monitors"])
                            logger.debug(f"Game focused - dimming monitors: {monitors_to_dim}")
                            set_brightness_side_monitors(brightness_settings["low"], monitors_to_dim)
                        else:
                            logger.debug("Game unfocused - restoring all monitors")
                            set_brightness_side_monitors(brightness_settings["high"], get_all_monitor_serials())
        except Exception as e:
            logger.error(f"Error in foreground change handler: {e}")

    def _focus_monitor_loop(self):
        """Run the Windows event hook message loop for focus monitoring."""
        try:
            self._focus_monitor_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            with init_com():
                self._foreground_hook = set_win_event_hook(
                    self._on_foreground_change,
                    HookEvent.SYSTEM_FOREGROUND
                )
                self._minimize_end_hook = set_win_event_hook(
                    self._on_foreground_change,
                    HookEvent.SYSTEM_MINIMIZEEND
                )
                self._object_focus_hook = set_win_event_hook(
                    self._on_foreground_change,
                    HookEvent.OBJECT_FOCUS
                )
                logger.debug("Focus monitor hooks registered")
                _run_message_loop()
        except Exception as e:
            logger.error(f"Error in focus monitor loop: {e}")
        finally:
            if hasattr(self, '_foreground_hook') and self._foreground_hook:
                self._foreground_hook.unhook()
            if hasattr(self, '_minimize_end_hook') and self._minimize_end_hook:
                self._minimize_end_hook.unhook()
            if hasattr(self, '_object_focus_hook') and self._object_focus_hook:
                self._object_focus_hook.unhook()
