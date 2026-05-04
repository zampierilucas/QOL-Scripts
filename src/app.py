import time
import os
import sys
import tempfile
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
import ctypes
from ctypes import wintypes
import winshell
from win32com.client import Dispatch

from settings import Settings, PROGRAM_NAME

from brightness import (
    set_brightness_side_monitors, init_monitors_cache, get_all_monitor_serials,
    BrightnessFocusConsumer
)
from vibrance import init_nvapi, set_vibrance, VibranceFocusConsumer
from focus_monitor import FocusMonitor
from lol import LoLAutoAccept, LoLAutoPick, SharedLCUConnector
from cs2 import CS2AutoAccept, CS2ConsoleWatcher
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
UPDATE_CHECK_INTERVAL = 30 * 60  # 30 minutes in seconds

logger = logging.getLogger(__name__)


def _enable_dark_menus_process():
    """Opt the PROCESS into dark-mode capability via undocumented uxtheme ordinals.
    Per-window opt-in via _enable_dark_menus_window is also required."""
    try:
        uxtheme = ctypes.WinDLL("uxtheme.dll")
        # Ordinals must be passed as integers, not "#N" strings, when using WINFUNCTYPE
        SetPreferredAppMode = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int)((135, uxtheme))
        RefreshImmersiveColorPolicyState = ctypes.WINFUNCTYPE(None)((104, uxtheme))
        SetPreferredAppMode(1)  # AllowDark — follow system theme
        RefreshImmersiveColorPolicyState()
        logger.debug("Process dark-mode capability enabled")
    except Exception as e:
        logger.debug(f"Could not set process dark-mode: {e}")


def _enable_dark_menus_window(hwnd):
    """Opt a specific HWND into dark-mode rendering. Each HWND that owns or
    hosts a popup menu must be opted in individually."""
    if not hwnd:
        return
    try:
        uxtheme = ctypes.WinDLL("uxtheme.dll")
        AllowDarkModeForWindow = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.BOOL
        )((133, uxtheme))
        AllowDarkModeForWindow(hwnd, True)
        # Apply DarkMode_Explorer visual style so menus inherit dark theming
        ctypes.windll.uxtheme.SetWindowTheme(hwnd, "DarkMode_Explorer", None)
        # Force theme refresh
        WM_THEMECHANGED = 0x031A
        ctypes.windll.user32.SendMessageW(hwnd, WM_THEMECHANGED, 0, 0)
    except Exception as e:
        logger.debug(f"Could not enable dark mode for hwnd {hwnd}: {e}")


class QOLApp:
    def __init__(self):
        self.settings = Settings()

        # Update checker state (must be before create_tray_icon)
        self.update_available = False
        self.latest_version = None
        self.latest_download_url = None
        self.latest_download_size = 0

        _enable_dark_menus_process()
        self.create_tray_icon()
        self.running = True
        self.install_dir = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', PROGRAM_NAME)
        self.startup_shortcut = os.path.join(winshell.startup(), f"{PROGRAM_NAME}.lnk")
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        # Create shared LCU connector with handlers
        self.lol_auto_accept = LoLAutoAccept(self.settings)
        self.lol_auto_pick = LoLAutoPick(self.settings)
        self.lcu_connector = SharedLCUConnector()
        self.lcu_connector.register_handler(self.lol_auto_accept)
        self.lcu_connector.register_handler(self.lol_auto_pick)
        self.lcu_connector.register_close_callback(lambda _: self.lol_auto_accept.on_disconnect())
        self.lcu_connector.register_close_callback(lambda _: self.lol_auto_pick.on_disconnect())

        # Create CS2 console watcher with auto-accept handler
        self.cs2_auto_accept = CS2AutoAccept(self.settings)
        self.cs2_watcher = CS2ConsoleWatcher()
        self.cs2_watcher.register_callback(self.cs2_auto_accept.on_match_found)
        self.cs2_watcher.register_condebug_missing_callback(self._on_condebug_missing)

        # Shared focus monitor: one daemon publishes the focused window;
        # each feature has its own consumer thread that subscribes and reacts
        # independently.
        self.focus_monitor = FocusMonitor()
        self.brightness_consumer = BrightnessFocusConsumer(self.settings, self.focus_monitor)
        self.vibrance_consumer = VibranceFocusConsumer(self.settings, self.focus_monitor)

        self.settings_requested = False
        self.settings_window = None

        try:
            logger.info("Initializing monitor cache and setting brightness to 100%")
            init_monitors_cache()
            set_brightness_side_monitors(100, get_all_monitor_serials())
        except Exception as e:
            logger.error(f"Failed to initialize monitors on startup: {e}")

        try:
            init_nvapi()
        except Exception as e:
            logger.error(f"Failed to initialize NVAPI: {e}")

    def signal_handler(self, _signum, _frame):
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

    def toggle_startup(self, _icon, _item):
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
                assets = data.get("assets", [])
                exe_asset = next((a for a in assets if a["name"].endswith(".exe")), None)
                self.latest_download_url = exe_asset["browser_download_url"] if exe_asset else None
                self.latest_download_size = exe_asset.get("size", 0) if exe_asset else 0
                logger.info(f"Update available: v{latest_tag} (current: v{VERSION})")
                if getattr(sys, 'frozen', False) and self.settings.data.get("auto_update_enabled", False):
                    logger.info("Auto-update enabled, applying update silently")
                    self._do_update()
                    return
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

    def _refresh_updates(self, _icon=None, _item=None):
        """Manually trigger an update check from the tray menu."""
        Thread(target=self.check_for_updates, daemon=True).start()

    def _do_update(self, _icon=None, _item=None):
        """Download the new exe and relaunch via a swap script."""
        if not self.latest_download_url:
            logger.warning("No download URL available for update")
            return
        Thread(target=self._run_update, daemon=True).start()

    def _run_update(self):
        try:
            installed_exe = self.get_installed_exe_path()
            logger.info(f"Downloading update from {self.latest_download_url}")

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".exe", prefix="QOL-update-")
            os.close(tmp_fd)

            with requests.get(self.latest_download_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                # Trust the github API asset size if present, otherwise fall back to Content-Length
                expected_size = self.latest_download_size or int(r.headers.get("Content-Length", 0))
                bytes_written = 0
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
                        bytes_written += len(chunk)

            # Defense: if the download was truncated mid-stream, the resulting exe
            # boots into PyInstaller bootstrap and crashes ("base_library.zip not
            # found in _MEI*"). Verify the size before letting the swap script run.
            if expected_size and bytes_written != expected_size:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                logger.error(
                    f"Update download incomplete: got {bytes_written} bytes, "
                    f"expected {expected_size}. Aborting swap so the running "
                    f"version is not replaced with a corrupted exe."
                )
                return

            bat_fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="QOL-update-")
            pid = os.getpid()
            bat_script = (
                f"@echo off\n"
                f":wait\n"
                f"tasklist /fi \"PID eq {pid}\" 2>nul | find \"{pid}\" >nul\n"
                f"if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n"
                f"move /y \"{tmp_path}\" \"{installed_exe}\"\n"
                f"start \"\" \"{installed_exe}\"\n"
                f"del \"%~f0\"\n"
            )
            with os.fdopen(bat_fd, "w") as f:
                f.write(bat_script)

            logger.info("Launching update script and exiting")
            os.startfile(bat_path)
            self.stop()
        except Exception as e:
            logger.error(f"Update failed: {e}")

    def _on_condebug_missing(self, was_fixed: bool):
        """Called when CS2 is running but -condebug is not set."""
        Thread(target=self._show_condebug_popup, args=(was_fixed,), daemon=True).start()

    def _show_condebug_popup(self, was_fixed: bool):
        if was_fixed:
            title = "QOL-Scripts: restart CS2"
            msg = "CS2 auto-accept needs '-condebug' in Steam launch options.\n\nIt has been added automatically. Please restart CS2 for it to take effect."
        else:
            title = "QOL-Scripts: action required"
            msg = "CS2 auto-accept needs '-condebug' in Steam launch options.\n\nCould not auto-add it. Right-click CS2 in Steam → Properties → Launch Options, add '-condebug', then restart CS2."
        MB_OK = 0x0
        MB_ICONINFORMATION = 0x40
        ctypes.windll.user32.MessageBoxW(0, msg, title, MB_OK | MB_ICONINFORMATION)

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
        def check_auto_update(_item):
            return self.settings.data.get("auto_update_enabled", False)

        def toggle_auto_update(_icon, _item):
            self.settings.data["auto_update_enabled"] = not self.settings.data.get("auto_update_enabled", False)
            self.settings.save_settings()

        def check_dimming(_item):
            return self.settings.data["dimming_enabled"]

        def check_auto_accept(_item):
            return self.settings.data["auto_accept_enabled"]

        def check_auto_pick(_item):
            return self.settings.data["auto_pick_enabled"]

        def check_cs2_auto_accept(_item):
            return self.settings.data.get("cs2_auto_accept_enabled", True)

        def check_auto_lock(_item):
            return self.settings.data.get("auto_lock_enabled", True)

        def toggle_dimming(_icon, _item):
            self.settings.data["dimming_enabled"] = not self.settings.data["dimming_enabled"]
            self.settings.save_settings()
            if not self.settings.data["dimming_enabled"]:
                set_brightness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )

        def toggle_auto_accept(_icon, _item):
            self.settings.data["auto_accept_enabled"] = not self.settings.data["auto_accept_enabled"]
            self.settings.save_settings()

        def toggle_auto_pick(_icon, _item):
            self.settings.data["auto_pick_enabled"] = not self.settings.data["auto_pick_enabled"]
            self.settings.save_settings()

        def toggle_cs2_auto_accept(_icon, _item):
            self.settings.data["cs2_auto_accept_enabled"] = not self.settings.data.get("cs2_auto_accept_enabled", True)
            self.settings.save_settings()

        def check_vibrance(_item):
            return self.settings.data.get("vibrance_enabled", False)

        def toggle_vibrance(_icon, _item):
            self.settings.data["vibrance_enabled"] = not self.settings.data.get("vibrance_enabled", False)
            self.settings.save_settings()
            if not self.settings.data["vibrance_enabled"]:
                vibrance_displays = self.settings.data.get("vibrance_displays", []) or None
                set_vibrance(self.settings.data.get("vibrance_default_level", 50), vibrance_displays)
            else:
                self.focus_monitor.reset()

        def toggle_auto_lock(_icon, _item):
            self.settings.data["auto_lock_enabled"] = not self.settings.data.get("auto_lock_enabled", True)
            self.settings.save_settings()

        def open_about(_icon, _item):
            webbrowser.open("https://github.com/zampierilucas/QOL-Scripts")

        # Single dual-function version row: ↻ to check for updates, or ⬇ to apply one
        can_self_update = (self.update_available and self.latest_version
                           and getattr(sys, 'frozen', False) and self.latest_download_url)
        if can_self_update:
            version_action = self._do_update
            version_label = f"{PROGRAM_NAME} v{VERSION} → v{self.latest_version} ⬇"
        else:
            version_action = self._refresh_updates
            version_label = f"{PROGRAM_NAME} v{VERSION} ↻"

        lol_submenu = pystray.Menu(
            pystray.MenuItem("Auto Accept", toggle_auto_accept, checked=check_auto_accept),
            pystray.MenuItem("Auto Pick", toggle_auto_pick, checked=check_auto_pick),
            pystray.MenuItem("Auto Lock", toggle_auto_lock, checked=check_auto_lock),
        )

        menu_items = [
            pystray.MenuItem(version_label, version_action),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("LoL", lol_submenu),
            pystray.MenuItem(
                "CS2 - Auto Accept",
                toggle_cs2_auto_accept,
                checked=check_cs2_auto_accept
            ),
            pystray.MenuItem(
                "Dimming",
                toggle_dimming,
                checked=check_dimming
            ),
            pystray.MenuItem(
                "Digital Vibrance",
                toggle_vibrance,
                checked=check_vibrance
            ),
            pystray.MenuItem("Auto Update", toggle_auto_update, checked=check_auto_update),
            pystray.MenuItem("Settings", self.show_settings),
            pystray.MenuItem("About", open_about),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.stop),
        ]

        self._menu = pystray.Menu(*menu_items)

    def show_settings(self, _icon=None, _item=None):
        self.settings_requested = True

    def stop(self, _icon=None, _item=None):
        if self.running:
            self.running = False
            if self.settings_window:
                try:
                    # Schedule destroy on the main thread to avoid tkinter threading issues
                    self.settings_window.root.after(0, self.settings_window.root.destroy)
                except Exception:
                    pass
            self.lcu_connector.stop()
            self.cs2_watcher.stop()
            self.brightness_consumer.stop()
            self.vibrance_consumer.stop()
            self.focus_monitor.stop()
            self.icon.stop()
            if self.settings.data["dimming_enabled"]:
                set_brightness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )
            if self.settings.data.get("vibrance_enabled", False):
                vibrance_displays = self.settings.data.get("vibrance_displays", []) or None
                set_vibrance(self.settings.data.get("vibrance_default_level", 50), vibrance_displays)

    def run(self):
        self.lcu_connector.start()
        self.cs2_watcher.start()
        self.focus_monitor.start()
        self.brightness_consumer.start()
        self.vibrance_consumer.start()
        Thread(target=self._update_check_loop, daemon=True).start()
        Thread(target=self.icon.run, daemon=True).start()
        Thread(target=self._enable_dark_menus_when_ready, daemon=True).start()

        while self.running:
            if self.settings_requested:
                self.settings_requested = False
                self.settings_window = SettingsWindow(self.settings, app=self)
                self.settings_window.root.mainloop()
                self.settings_window = None
            time.sleep(0.1)

    def _enable_dark_menus_when_ready(self):
        """Wait for pystray to create its HWNDs, then opt them into dark mode.
        pystray creates two windows: _hwnd (foreground when menu shown) and
        _menu_hwnd (the popup-menu owner). Both need the opt-in."""
        for _ in range(50):  # up to ~5 seconds
            hwnd = getattr(self.icon, "_hwnd", None)
            menu_hwnd = getattr(self.icon, "_menu_hwnd", None)
            if hwnd and menu_hwnd:
                _enable_dark_menus_window(hwnd)
                _enable_dark_menus_window(menu_hwnd)
                logger.debug(f"Dark menu opt-in applied to hwnd={hwnd} menu_hwnd={menu_hwnd}")
                return
            time.sleep(0.1)
        logger.debug("pystray HWNDs never appeared; skipping dark menu opt-in")
