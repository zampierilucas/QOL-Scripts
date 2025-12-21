import time
import re
import screen_brightness_control as sbc
from win32gui import GetWindowText, GetForegroundWindow
from win32api import GetMonitorInfo, MonitorFromWindow
from win32con import MONITOR_DEFAULTTONEAREST
import os
import logging
import base64
import pystray
from PIL import Image
import json
from threading import Thread
import tkinter as tk
from tkinter import ttk
import appdirs
import pathlib
import signal
import win32gui
import argparse
import sv_ttk
import darkdetect
import pywinstyles
import sys
import webbrowser
import io
import shutil
import winshell
from win32com.client import Dispatch
from lcu_driver import Connector
import asyncio
from _version import VERSION

PROGRAM_NAME = "QOL-Scripts"
CONFIG_DIR = pathlib.Path(appdirs.user_config_dir(PROGRAM_NAME))
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Disable "unverified HTTPS request" warnings
import urllib3
from urllib3.exceptions import InsecureRequestWarning
urllib3.disable_warnings(InsecureRequestWarning)

# ----------------------------------
# 1) LOGGING SETUP
# ----------------------------------
def setup_logging(debug=False):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    # Suppress specific warning logs
    logging.getLogger("screen_brightness_control").setLevel(logging.ERROR)
    return logger

# ----------------------------------
# 2) BRIGHTNESS UTILS
# ----------------------------------
def set_brightness_side_monitors(brightness, monitor_ids):
    """
    Sets the brightness for any side monitors configured in settings
    """
    for monitor_id in monitor_ids:
        try:
            current_brightness = sbc.get_brightness(display=monitor_id)[0]
            if current_brightness != brightness:
                sbc.set_brightness(brightness, display=monitor_id)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Set brightness to {brightness} for monitor ID {monitor_id}")
            else:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Brightness for monitor ID {monitor_id} is already {brightness}, no change needed")
        except Exception as e:
            logger.error(f"Failed to set brightness for monitor ID {monitor_id}: {e}")

def get_focused_monitor_info():
    """
    Get information about the monitor containing the focused window
    Returns the monitor's device name or None if not found
    """
    try:
        # Get the focused window handle
        focused_window = GetForegroundWindow()
        if not focused_window:
            logger.debug("No focused window found")
            return None

        # Get the monitor handle for the focused window
        monitor_handle = MonitorFromWindow(focused_window, MONITOR_DEFAULTTONEAREST)
        if not monitor_handle:
            logger.debug("No monitor handle found for focused window")
            return None

        # Get monitor info
        monitor_info = GetMonitorInfo(monitor_handle)
        if not monitor_info:
            logger.debug("No monitor info found")
            return None

        # Extract device name from monitor info
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
        # Get focused monitor device name (e.g., \\.\DISPLAY1, \\.\DISPLAY2)
        focused_device = get_focused_monitor_info()
        if not focused_device:
            logger.debug("Cannot detect focused monitor, returning empty list")
            return []

        # Extract display number from device name (e.g., \\.\DISPLAY1 -> 0, \\.\DISPLAY2 -> 1)
        # Windows display numbering starts at 1, but array indices start at 0
        try:
            match = re.search(r'DISPLAY(\d+)', focused_device)
            if match:
                focused_display_index = int(match.group(1)) - 1  # Convert to 0-based index
                logger.debug(f"Focused display index: {focused_display_index}")
            else:
                logger.debug(f"Could not parse display number from: {focused_device}")
                focused_display_index = 0  # Default to first monitor
        except Exception as e:
            logger.error(f"Error parsing display index: {e}")
            focused_display_index = 0

        # Get all monitors info
        monitors_info = sbc.list_monitors_info()
        logger.debug(f"Total monitors found: {len(monitors_info)}")

        non_focused_serials = []

        for idx, info in enumerate(monitors_info):
            serial = info.get('serial', '')
            logger.debug(f"Monitor {idx}: serial={serial}, name={info.get('name', 'Unknown')}")

            # Skip the focused monitor
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

# ----------------------------------
# 3) LOL WEBSOCKET HANDLER
# ----------------------------------
class LoLAutoAccept:
    """
    WebSocket-based LoL match auto-accepter using lcu-driver.
    Runs in a separate thread and reacts instantly to ready-check events.
    """
    def __init__(self, settings):
        self.settings = settings
        self.connector = None
        self.loop = None
        self.running = False

    def start(self):
        """Start the WebSocket connector in a separate thread"""
        if not self.running:
            self.running = True
            thread = Thread(target=self._run_connector, daemon=True)
            thread.start()
            logger.info("LoL WebSocket auto-accept thread started")

    def _run_connector(self):
        """Run the connector (blocks until stopped)"""
        try:
            # Create a new event loop for this thread
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            # Create connector with the new event loop
            self.connector = Connector()

            # Register event handlers
            @self.connector.ready
            async def on_lcu_ready(connection):
                logger.info("LoL Client connected - WebSocket auto-accept is active")

            @self.connector.close
            async def on_lcu_close(connection):
                logger.info("LoL Client disconnected - WebSocket auto-accept stopped")

            @self.connector.ws.register('/lol-matchmaking/v1/ready-check', event_types=('CREATE',))
            async def on_ready_check(connection, event):
                # Only accept if auto-accept is enabled in settings
                if self.settings.data.get("auto_accept_enabled", True):
                    try:
                        logger.info("Ready check detected! Auto-accepting match via WebSocket...")
                        await connection.request('post', '/lol-matchmaking/v1/ready-check/accept')
                        logger.info("LoL match accepted successfully via WebSocket!")
                    except Exception as e:
                        logger.error(f"Failed to accept LoL match via WebSocket: {e}")
                else:
                    logger.debug("Ready check detected but auto-accept is disabled")

            # Start the connector (this blocks until stopped)
            self.connector.start()
        except Exception as e:
            logger.error(f"LoL WebSocket connector error: {e}")

    def stop(self):
        """Stop the WebSocket connector"""
        if self.running:
            self.running = False
            try:
                # Schedule the async stop on the connector's event loop
                if self.connector and self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.connector.stop(), self.loop)
                    # Give it a moment to clean up, then force stop the loop
                    self.loop.call_soon_threadsafe(self.loop.stop)
                logger.info("LoL WebSocket auto-accept stopped")
            except Exception as e:
                logger.error(f"Error stopping LoL WebSocket connector: {e}")

# ----------------------------------
# 4) MAIN LOOP
# ----------------------------------
class Settings:
    DEFAULT_SETTINGS = {
        "dimmable_monitors": [], 
        "monitor_brightness": {
            "high": 100,
            "low": 10
        },
        "games_to_dimm": [
            "Counter-Strike 2",
            "EscapeFromTarkov",
            "Hell Let Loose",
            "League of Legends (TM) Client"
        ],
        "dimming_enabled": True, 
        "auto_accept_enabled": True,
        "dim_all_except_focused": False
    }
    
    def __init__(self):
        # Create config directory if it doesn't exist
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.settings_file = CONFIG_FILE
        self.load_settings()

    def load_settings(self):
        try:
            with open(self.settings_file, 'r') as f:
                self.data = json.load(f)
                # Migrate any missing settings from defaults
                updated = False
                for key, default_value in self.DEFAULT_SETTINGS.items():
                    if key not in self.data:
                        self.data[key] = default_value
                        updated = True
                if updated:
                    self.save_settings()
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = self.DEFAULT_SETTINGS.copy()
            self.save_settings()

    def save_settings(self):
        with open(self.settings_file, 'w') as f:
            json.dump(self.data, f, indent=4)

def apply_theme_to_titlebar(root):
    version = sys.getwindowsversion()

    if version.major == 10 and version.build >= 22000:
        # Set the title bar color to the background color on Windows 11 for better appearance
        pywinstyles.change_header_color(root, "#1c1c1c" if sv_ttk.get_theme() == "dark" else "#fafafa")
    elif version.major == 10:
        pywinstyles.apply_style(root, "dark" if sv_ttk.get_theme() == "dark" else "normal")

        # A hacky way to update the title bar's color on Windows 10 (it doesn't update instantly like on Windows 11)
        root.wm_attributes("-alpha", 0.99)
        root.wm_attributes("-alpha", 1)

class SettingsWindow:
    def __init__(self, settings, app=None):
        self.settings = settings
        self.app = app  # Reference to AutoAccept for startup toggle
        self.root = tk.Tk()
        self.root.title("QOL Settings")
        self.root.geometry("420x680")
        self.root.minsize(380, 550)
        self.root.maxsize(600, 850)
        sv_ttk.set_theme(darkdetect.theme())
        self.monitor_vars = {}
        self.games_list = sorted(self.settings.data["games_to_dimm"], key=str.lower)
        self.create_widgets()
        apply_theme_to_titlebar(self.root)

    def get_running_programs(self):
        """Get list of running program window titles (filters out tool/overlay windows)"""
        programs = set()
        excluded = {"QOL Settings", "Add Game"}

        def enum_windows_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            # Skip tool windows (overlays, popups) unless they explicitly want taskbar presence
            ex_style = win32gui.GetWindowLong(hwnd, -20)  # GWL_EXSTYLE
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and len(title) > 1 and title not in excluded:
                programs.add(title)

        win32gui.EnumWindows(enum_windows_callback, None)
        return sorted(list(programs))

    def show_add_game_dialog(self):
        """Show modal dialog to add games from running programs"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Game")
        dialog.geometry("400x450")
        dialog.transient(self.root)
        dialog.grab_set()
        sv_ttk.set_theme(darkdetect.theme())
        apply_theme_to_titlebar(dialog)

        # Center dialog on parent
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 400) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 450) // 2
        dialog.geometry(f"+{x}+{y}")

        main_frame = ttk.Frame(dialog, padding=15)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text="Select from running programs:").pack(anchor="w")

        # Listbox with scrollbar
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill="both", expand=True, pady=(5, 10))

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        programs_list = tk.Listbox(list_frame, height=15, yscrollcommand=scrollbar.set, selectmode="extended")
        programs_list.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=programs_list.yview)

        for program in self.get_running_programs():
            programs_list.insert(tk.END, program)

        # Or enter manually
        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(main_frame, text="Or enter manually:").pack(anchor="w")
        manual_entry = ttk.Entry(main_frame)
        manual_entry.pack(fill="x", pady=(5, 10))

        def add_selected():
            added = False
            # Add from listbox selection
            for idx in programs_list.curselection():
                game = programs_list.get(idx)
                if game and game not in self.games_list:
                    self.games_list.append(game)
                    added = True
            # Add from manual entry
            manual = manual_entry.get().strip()
            if manual and manual not in self.games_list:
                self.games_list.append(manual)
                added = True
            if added:
                self.games_list.sort(key=str.lower)
                self.refresh_games_listbox()
            dialog.destroy()

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=(5, 0))
        ttk.Button(btn_frame, text="Add Selected", command=add_selected).pack(side="right")

        # Double-click to add
        def on_double_click(event):
            add_selected()
        programs_list.bind('<Double-Button-1>', on_double_click)

    def refresh_games_listbox(self):
        """Refresh the games listbox from internal list"""
        self.games_listbox.delete(0, tk.END)
        for game in self.games_list:
            self.games_listbox.insert(tk.END, game)

    def remove_selected_games(self):
        """Remove selected games from the list"""
        selected = list(self.games_listbox.curselection())
        selected.reverse()  # Remove from end to preserve indices
        for idx in selected:
            del self.games_list[idx]
        self.refresh_games_listbox()

    def create_widgets(self):
        # Main scrollable container
        main_container = ttk.Frame(self.root)
        main_container.pack(fill="both", expand=True, padx=15, pady=15)

        # Monitors Frame
        monitors_frame = ttk.LabelFrame(main_container, text="Dimmable Monitors", padding=10)
        monitors_frame.pack(fill="x", pady=(0, 10))

        try:
            monitors_info = sbc.list_monitors_info()
            logger.debug(f"Found {len(monitors_info)} monitors")

            for info in monitors_info:
                logger.debug(f"Monitor data: {info}")

            for index, info in enumerate(monitors_info):
                serial = info.get('serial', 'Unknown')
                name = info.get('name', 'Unknown')
                display_name = f"Monitor {index + 1}: {name}"

                logger.debug(f"Adding monitor: {display_name}")

                var = tk.BooleanVar()
                var.set(serial in self.settings.data["dimmable_monitors"])
                self.monitor_vars[serial] = var

                ttk.Checkbutton(
                    monitors_frame,
                    text=display_name,
                    variable=var
                ).pack(anchor="w", pady=2)

        except Exception as e:
            logger.error(f"Error setting up monitor checkboxes: {e}")
            ttk.Label(
                monitors_frame,
                text=f"Error loading monitors: {str(e)}",
                foreground="red"
            ).pack(fill="x", pady=5)

        # Brightness Frame
        brightness_frame = ttk.LabelFrame(main_container, text="Brightness Settings", padding=10)
        brightness_frame.pack(fill="x", pady=(0, 10))

        # High brightness with label
        high_frame = ttk.Frame(brightness_frame)
        high_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(high_frame, text="Focused:").pack(side="left")
        self.high_value_label = ttk.Label(high_frame, text="100%", width=5)
        self.high_value_label.pack(side="right")

        self.high_brightness_var = tk.IntVar(value=self.settings.data["monitor_brightness"]["high"])
        self.high_brightness = ttk.Scale(
            brightness_frame,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.high_brightness_var,
            command=lambda v: self.high_value_label.config(text=f"{int(float(v))}%")
        )
        self.high_brightness.pack(fill="x")
        self.high_value_label.config(text=f"{self.high_brightness_var.get()}%")

        # Low brightness with label
        low_frame = ttk.Frame(brightness_frame)
        low_frame.pack(fill="x", pady=(10, 5))
        ttk.Label(low_frame, text="Dimmed:").pack(side="left")
        self.low_value_label = ttk.Label(low_frame, text="10%", width=5)
        self.low_value_label.pack(side="right")

        self.low_brightness_var = tk.IntVar(value=self.settings.data["monitor_brightness"]["low"])
        self.low_brightness = ttk.Scale(
            brightness_frame,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.low_brightness_var,
            command=lambda v: self.low_value_label.config(text=f"{int(float(v))}%")
        )
        self.low_brightness.pack(fill="x")
        self.low_value_label.config(text=f"{self.low_brightness_var.get()}%")

        # Dimming mode selection
        self.dim_all_except_focused_var = tk.BooleanVar()
        self.dim_all_except_focused_var.set(self.settings.data.get("dim_all_except_focused", False))
        ttk.Checkbutton(
            brightness_frame,
            text="Dim all monitors except focused (experimental)",
            variable=self.dim_all_except_focused_var
        ).pack(anchor="w", pady=(10, 0))

        # Games section
        games_frame = ttk.LabelFrame(main_container, text="Games to Dim", padding=10)
        games_frame.pack(fill="both", expand=True, pady=(0, 10))

        # Games listbox with scrollbar
        list_container = ttk.Frame(games_frame)
        list_container.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")

        self.games_listbox = tk.Listbox(list_container, height=6, selectmode="extended", yscrollcommand=scrollbar.set)
        self.games_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.games_listbox.yview)

        # Populate games listbox
        for game in self.games_list:
            self.games_listbox.insert(tk.END, game)

        # Game buttons
        games_btn_frame = ttk.Frame(games_frame)
        games_btn_frame.pack(fill="x", pady=(10, 0))
        ttk.Button(
            games_btn_frame,
            text="Add...",
            command=self.show_add_game_dialog,
            width=12
        ).pack(side="left")
        ttk.Button(
            games_btn_frame,
            text="Remove",
            command=self.remove_selected_games,
            width=12
        ).pack(side="left", padx=(5, 0))

        # General section
        general_frame = ttk.LabelFrame(main_container, text="General", padding=10)
        general_frame.pack(fill="x", pady=(0, 10))

        self.startup_var = tk.BooleanVar()
        self.startup_var.set(self.app.is_startup_enabled() if self.app else False)
        ttk.Checkbutton(
            general_frame,
            text="Start on Windows startup",
            variable=self.startup_var
        ).pack(anchor="w")

        # Bottom buttons
        btn_frame = ttk.Frame(main_container)
        btn_frame.pack(fill="x", pady=(5, 0))

        ttk.Button(
            btn_frame,
            text="Save",
            command=self.save_settings,
            width=12
        ).pack(side="right")
        ttk.Button(
            btn_frame,
            text="Cancel",
            command=self.root.destroy,
            width=12
        ).pack(side="right", padx=(0, 5))

    def save_settings(self):
        """Save current settings and close window"""
        try:
            logger.debug(f"Current monitor vars: {self.monitor_vars}")

            selected_monitors = [
                serial for serial, var in self.monitor_vars.items()
                if var.get()
            ]
            logger.debug(f"Saving selected monitors: {selected_monitors}")
            self.settings.data["dimmable_monitors"] = selected_monitors

            self.settings.data["monitor_brightness"]["high"] = self.high_brightness_var.get()
            self.settings.data["monitor_brightness"]["low"] = self.low_brightness_var.get()

            self.settings.data["games_to_dimm"] = sorted([g.strip() for g in self.games_list if g.strip()], key=str.lower)

            self.settings.data["dim_all_except_focused"] = self.dim_all_except_focused_var.get()

            # Handle startup toggle
            if self.app:
                startup_enabled = self.app.is_startup_enabled()
                startup_wanted = self.startup_var.get()
                if startup_enabled != startup_wanted:
                    self.app.toggle_startup(None, None)

            self.settings.save_settings()
            self.root.destroy()
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

class AutoAccept:
    def __init__(self):
        self.settings = Settings()
        self.create_tray_icon()
        self.running = True
        self.install_dir = os.path.join(os.environ['LOCALAPPDATA'], 'Programs', PROGRAM_NAME)
        self.startup_shortcut = os.path.join(winshell.startup(), f"{PROGRAM_NAME}.lnk")
        # Add signal handler
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        # Cache for tracking state to avoid unnecessary operations
        self.last_focused_window = None
        self.last_brightness_state = None

        # Initialize LoL WebSocket auto-accepter
        self.lol_auto_accept = LoLAutoAccept(self.settings)

        # Flag for settings window (must be opened from main thread)
        self.settings_requested = False
        self.settings_window = None

        # Set all monitors to 100% brightness on startup
        try:
            logger.info("Setting all monitors to 100% brightness on startup")
            all_monitors = [info.get('serial') for info in sbc.list_monitors_info()]
            set_brightness_side_monitors(100, all_monitors)
        except Exception as e:
            logger.error(f"Failed to set all monitors to 100% on startup: {e}")

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

    def is_frozen(self):
        """Check if running from compiled exe (not .py file)"""
        return getattr(sys, 'frozen', False)

    def toggle_startup(self, icon, item):
        """Toggle startup by installing app and managing shortcut"""
        try:
            if self.is_startup_enabled():
                os.remove(self.startup_shortcut)
                logger.info("Removed from startup")
            else:
                # Only allow startup installation from compiled exe
                if not self.is_frozen():
                    logger.warning("Startup installation is only available when running from the compiled .exe")
                    return

                # Create install directory
                os.makedirs(self.install_dir, exist_ok=True)

                # Copy executable to install directory
                current_exe = self.get_executable_path()
                installed_exe = self.get_installed_exe_path()

                if not os.path.exists(installed_exe) or os.path.getmtime(current_exe) > os.path.getmtime(installed_exe):
                    shutil.copy2(current_exe, installed_exe)
                    logger.info(f"Installed to: {installed_exe}")

                # Create startup shortcut pointing to installed exe
                shell = Dispatch('WScript.Shell')
                shortcut = shell.CreateShortCut(self.startup_shortcut)
                shortcut.Targetpath = installed_exe
                shortcut.WorkingDirectory = self.install_dir
                shortcut.IconLocation = installed_exe
                shortcut.save()
                logger.info(f"Added to startup: {installed_exe}")
        except Exception as e:
            logger.error(f"Failed to toggle startup: {e}")

    def create_tray_icon(self):
        try:
            # Use base64 encoded icon instead of file
            icon_base64 = "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"
            icon_data = base64.b64decode(icon_base64)
            icon_image = Image.open(io.BytesIO(icon_data))
        except Exception as e:
            logger.error(f"Failed to load icon: {e}")
            # Fallback to default red square icon
            icon_image = Image.new('RGB', (64, 64), color='red')
        
        def check_dimming(item):
            return self.settings.data["dimming_enabled"]
        
        def check_auto_accept(item):
            return self.settings.data["auto_accept_enabled"]
        
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

        def open_about(icon, item):
            webbrowser.open("https://github.com/zampierilucas/QOL-Scripts")

        menu_items = [
            pystray.MenuItem(f"{PROGRAM_NAME} v{VERSION}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Auto Accept",
                toggle_auto_accept,
                checked=check_auto_accept
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
        ]

        menu = pystray.Menu(*menu_items)

        self.icon = pystray.Icon(
            PROGRAM_NAME,
            icon_image,
            PROGRAM_NAME,
            menu=menu
        )

    def show_settings(self, icon=None, item=None):
        # Signal main thread to open settings (Tkinter must run on main thread)
        self.settings_requested = True

    def stop(self, icon=None, item=None):
        if self.running:
            self.running = False
            # Close settings window if open
            if self.settings_window:
                try:
                    self.settings_window.root.destroy()
                except Exception:
                    pass
            # Stop LoL WebSocket connector
            self.lol_auto_accept.stop()
            self.icon.stop()
            # Ensure brightness is restored before exit
            if self.settings.data["dimming_enabled"]:
                set_brightness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )

    def run(self):
        # Start LoL WebSocket auto-accepter
        self.lol_auto_accept.start()
        # Start main loop for dimming (daemon so it doesn't block exit)
        Thread(target=self.main_loop, daemon=True).start()
        # Run pystray in background thread (so main thread can handle Tkinter)
        Thread(target=self.icon.run, daemon=True).start()

        # Main thread loop - handles Tkinter windows
        while self.running:
            if self.settings_requested:
                self.settings_requested = False
                self.settings_window = SettingsWindow(self.settings, app=self)
                self.settings_window.root.mainloop()
                self.settings_window = None
            time.sleep(0.1)

    def main_loop(self):
        while self.running:
            try:
                # Get focused window once per loop
                foreground_window = GetForegroundWindow()
                focused = GetWindowText(foreground_window)

                # Only process if focused window changed
                if focused != self.last_focused_window:
                    self.last_focused_window = focused
                    logger.debug(f"Focus changed to: '{focused}'")

                    # Handle dimming when focus changes
                    if self.settings.data["dimming_enabled"]:
                        is_game_focused = focused.strip() in self.settings.data['games_to_dimm']
                        brightness_state = (is_game_focused, self.settings.data["dim_all_except_focused"])

                        # Only change brightness if state changed
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
                                # Restore all monitors to high brightness
                                all_monitors = [info.get('serial') for info in sbc.list_monitors_info()]
                                logger.debug("Game unfocused - restoring all monitors")
                                set_brightness_side_monitors(brightness_settings["high"], all_monitors)

                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Error in main loop: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QOL-Scripts")
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    logger = setup_logging(args.debug)

    app = AutoAccept()
    try:
        app.run()
    except KeyboardInterrupt:
        logger.debug("Received Ctrl+C. Shutting down...")
        app.stop()
