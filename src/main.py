import pyautogui
import time
import screen_brightness_control as sbc
from win32gui import GetWindowText, GetForegroundWindow, MonitorFromWindow
from win32api import GetMonitorInfo
from win32con import WM_INPUTLANGCHANGEREQUEST, MONITOR_DEFAULTTONEAREST
import os
import logging
import requests
from requests.auth import HTTPBasicAuth
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
import tempfile
import argparse
import sv_ttk
import darkdetect
import pywinstyles, sys
import webbrowser
import winreg
import io
import shutil
import winshell
from win32com.client import Dispatch

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
# 2) CS ACCEPT-IMAGE / UTILS
encoded_accept_cs = "iVBORw0KGgoAAAANSUhEUgAAABIAAAASCAYAAABWzo5XAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAABdaVRYdFNuaXBNZXRhZGF0YQAAAAAAeyJjbGlwUG9pbnRzIjpbeyJ4IjowLCJ5IjowfSx7IngiOjE4LCJ5IjowfSx7IngiOjE4LCJ5IjoxOH0seyJ4IjowLCJ5IjoxOH1dfYQwdLwAAAAmSURBVDhPYzTbHvSfgQqACUpTDEYNIgxGDSIMRg0iDEYNIgQYGADd0QJi+65e0gAAAABJRU5ErkJggg=="

def accept_match(img_base64):
    """
    Accept a match by:
      1) Decoding base64-encoded image into PIL Image
      2) Locating the image on screen
      3) Clicking if found
    """
    try:
        # Decode base64 directly to bytes
        img_data = base64.b64decode(img_base64)
        # Create PIL Image directly from bytes
        img = Image.open(io.BytesIO(img_data))  
        
        accept_btn = None
        # accept_btn = pyautogui.locateCenterOnScreen(img, confidence=0.999)
        
        if accept_btn is not None:
            # pyautogui.click(accept_btn)
            # pyautogui.move(0, 300)
            logger.debug("Match accepted via image recognition (CS).")
        else:
            logger.debug("CS accept button not found on screen.")
            
    except Exception as e:
        logger.error(f"Error in accept_match: {str(e)}")

def set_brigtness_side_monitors(brightness, monitor_ids):
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
            return None
            
        # Get the monitor handle for the focused window
        monitor_handle = MonitorFromWindow(focused_window, MONITOR_DEFAULTTONEAREST)
        if not monitor_handle:
            return None
            
        # Get monitor info
        monitor_info = GetMonitorInfo(monitor_handle)
        if not monitor_info:
            return None
            
        # Extract device name from monitor info
        device_name = monitor_info.get('Device', '')
        return device_name
        
    except Exception as e:
        logger.error(f"Error getting focused monitor info: {e}")
        return None

def get_all_monitor_serials_except_focused():
    """
    Get all monitor serials except the one containing the focused window
    """
    try:
        # Get focused monitor device name
        focused_device = get_focused_monitor_info()
        if not focused_device:
            # If we can't detect focused monitor, return all configured monitors
            return []
            
        # Get all monitors info
        monitors_info = sbc.list_monitors_info()
        non_focused_serials = []
        
        for info in monitors_info:
            # Try to match by device name or other identifiers
            serial = info.get('serial', '')
            # Note: screen_brightness_control might not provide device name directly
            # We'll need to use index matching as fallback
            non_focused_serials.append(serial)
            
        # For now, return all except the first one (primary) as a simple implementation
        # This needs refinement based on actual monitor detection capabilities
        return non_focused_serials[1:] if len(non_focused_serials) > 1 else []
        
    except Exception as e:
        logger.error(f"Error getting non-focused monitors: {e}")
        return []

# ----------------------------------
# 4) LCU (LEAGUE CLIENT) HELPER
# ----------------------------------
def league_client_is_open():
    """
    Returns True if the League lockfile exists, indicating the client is running.
    """
    lockfile_path = r"C:\Riot Games\League of Legends\lockfile"  # Adjust if needed
    return os.path.exists(lockfile_path)

def accept_lol_via_lcu():
    """
    Accept a League of Legends match via the local League Client API (LCU).
    This function:
      1) Reads the 'lockfile' to obtain port & credentials
      2) Sends a POST to /lol-matchmaking/v1/ready-check/accept
    """
    lockfile_path = r"C:\Riot Games\League of Legends\lockfile"  # adjust if needed

    # lockfile must exist if league_client_is_open() is True
    with open(lockfile_path, "r") as lf:
        content = lf.read().strip().split(':')

    if len(content) != 5:
        raise ValueError(f"Lockfile did not have the expected format. Got: {content}")

    process_name, pid, port, password, protocol = content
    url = f"{protocol}://127.0.0.1:{port}/lol-matchmaking/v1/ready-check/accept"
    auth = HTTPBasicAuth('riot', password)

    logger.debug("Attempting to accept LoL match via local client API...")
    response = requests.post(url, auth=auth, verify=False)

    if response.status_code in (200, 201, 204):
        logger.debug("LoL match accepted successfully (LCU).")
    else:
        logger.debug(
            f"LoL match accept call returned status {response.status_code}. "
            f"Response text: {response.text}"
        )

# ----------------------------------
# 5) MAIN LOOP
# ----------------------------------
class Settings:
    DEFAULT_SETTINGS = {
        "dimmable_monitors": [], 
        "monitor_brightness": {
            "high": 100,
            "low": 10
        },
        "games_to_dimm": [
            "League of Legends (TM) Client",
            "EscapeFromTarkov",
            "Counter-Strike 2",
            "Hell Let Loose"
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
                # Migrate existing settings if missing
                if "dimming_enabled" not in self.data:
                    self.data["dimming_enabled"] = True
                if "auto_accept_enabled" not in self.data:  
                    self.data["auto_accept_enabled"] = True
                if "dim_all_except_focused" not in self.data:
                    self.data["dim_all_except_focused"] = False
                self.save_settings()
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = self.DEFAULT_SETTINGS
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
    def __init__(self, settings):
        self.settings = settings
        self.root = tk.Tk()
        self.root.title("QOL Settings")
        self.root.geometry("400x700")  # Reduced initial window size
        sv_ttk.set_theme(darkdetect.theme())
        self.monitor_vars = {}
        self.monitor_ids = {}  # Store mapping of display name to ID
        self.create_widgets()
        apply_theme_to_titlebar(self.root)  # Apply title bar theme

    def get_running_programs(self):
        """Get list of running program window titles"""
        programs = set()
        def enum_windows_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and len(title) > 1:  # Filter out empty or single char titles
                    programs.add(title)
        win32gui.EnumWindows(enum_windows_callback, None)
        return sorted(list(programs))

    def refresh_running_programs(self):
        """Refresh the list of running programs"""
        self.running_list.delete(0, tk.END)
        for program in self.get_running_programs():
            self.running_list.insert(tk.END, program)

    def create_unique_monitor_id(self, info):
        """Create a unique monitor ID from monitor properties"""
        # Use the 'serial' property if available, otherwise fall back to other properties
        monitor_properties = [
            str(info.get('serial', '')),
            str(info.get('name', '')),
            str(info.get('manufacturer', '')),
            str(info.get('index', ''))  
        ]
        return "|".join(filter(None, monitor_properties))

    def create_widgets(self):
        # Main container with vertical layout
        main_container = ttk.Frame(self.root)
        main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Top section for monitors and brightness
        top_section = ttk.Frame(main_container)
        top_section.pack(fill="x", expand=False)

        # Monitors Frame
        monitors_frame = ttk.LabelFrame(top_section, text="Dimmable Monitors", padding=10)
        monitors_frame.pack(fill="x", pady=(0, 5))
        
        try:
            monitors_info = sbc.list_monitors_info()
            logger.debug(f"Found {len(monitors_info)} monitors")
            
            # Print all monitor data
            for info in monitors_info:
                logger.debug(f"Monitor data: {info}")
            
            # Create checkbox for each monitor with unique ID
            for index, info in enumerate(monitors_info):
                # Create unique ID from monitor properties
                monitor_id = self.create_unique_monitor_id(info)
                serial = info.get('serial', 'Unknown')
                name = info.get('name', 'Unknown')
                
                display_name = (
                    f"Monitor {index + 1}: {name}"
                )
                
                logger.debug(f"Adding monitor: {display_name} with ID: {monitor_id}")
                
                var = tk.BooleanVar()
                var.set(serial in self.settings.data["dimmable_monitors"])
                
                self.monitor_vars[serial] = var
                self.monitor_ids[display_name] = serial
                
                frame = ttk.Frame(monitors_frame)
                frame.pack(fill="x", pady=5)
                
                ttk.Checkbutton(
                    frame, 
                    text=display_name,
                    variable=var
                ).pack(anchor="w")

        except Exception as e:
            logger.error(f"Error setting up monitor checkboxes: {e}")
            # Add a label to show the error
            ttk.Label(
                monitors_frame,
                text=f"Error loading monitors: {str(e)}",
                foreground="red"
            ).pack(fill="x", pady=5)

        # Brightness Frame
        brightness_frame = ttk.LabelFrame(top_section, text="Brightness Settings", padding=10)
        brightness_frame.pack(fill="x", pady=5)
        
        ttk.Label(brightness_frame, text="High:").pack()
        self.high_brightness = tk.Scale(brightness_frame, from_=0, to=100, orient="horizontal")
        self.high_brightness.set(self.settings.data["monitor_brightness"]["high"])
        self.high_brightness.pack(fill="x")
        
        ttk.Label(brightness_frame, text="Low:").pack()
        self.low_brightness = tk.Scale(brightness_frame, from_=0, to=100, orient="horizontal")
        self.low_brightness.set(self.settings.data["monitor_brightness"]["low"])
        self.low_brightness.pack(fill="x")
        
        # Dimming mode selection
        self.dim_all_except_focused_var = tk.BooleanVar()
        self.dim_all_except_focused_var.set(self.settings.data.get("dim_all_except_focused", False))
        ttk.Checkbutton(
            brightness_frame,
            text="Dim all monitors except focused (experimental)",
            variable=self.dim_all_except_focused_var
        ).pack(anchor="w", pady=(10, 0))

        # Games section
        games_section = ttk.Frame(main_container)
        games_section.pack(fill="both", expand=True, pady=5)

        # Games to Dim
        games_frame = ttk.LabelFrame(games_section, text="Games to Dim", padding=10)
        games_frame.pack(fill="both", expand=True)
        
        self.games_text = tk.Text(games_frame, height=5)  # Reduced height
        self.games_text.pack(fill="both", expand=True)
        self.games_text.insert("1.0", "\n".join(self.settings.data["games_to_dimm"]))

        # Running Programs (bottom section, initially hidden)
        running_section = ttk.Frame(main_container)
        running_section.pack(fill="x", expand=False, pady=5)
        
        running_frame = ttk.LabelFrame(running_section, text="Running Programs", padding=10)
        running_frame.pack(fill="x")
        
        list_frame = ttk.Frame(running_frame)
        list_frame.pack(fill="x")
        
        self.running_list = tk.Listbox(list_frame, height=6)  # Reduced height
        self.running_list.pack(side="left", fill="x", expand=True)
        
        button_frame = ttk.Frame(list_frame)
        button_frame.pack(side="right", padx=5)
        
        ttk.Label(
            button_frame,
            text="Double-click to add",
            font=("", 8)  # Smaller font
        ).pack()
        
        ttk.Button(
            button_frame,
            text="Refresh List",
            command=self.refresh_running_programs  # Now this method exists
        ).pack(pady=5)

        # Populate running programs
        for program in self.get_running_programs():
            self.running_list.insert(tk.END, program)
        
        def on_double_click(event):
            selection = self.running_list.curselection()
            if selection:
                program = self.running_list.get(selection[0])
                self.games_text.insert(tk.END, f"\n{program}")
        
        self.running_list.bind('<Double-Button-1>', on_double_click)

        # Save Button at bottom
        save_frame = ttk.Frame(self.root)
        save_frame.pack(fill="x", pady=10, padx=10)
        ttk.Button(
            save_frame,
            text="Save",
            command=self.save_settings
        ).pack(side="right")

    def toggle_running_programs(self):
        """Toggle the visibility of the running programs section"""
        if self.root.winfo_height() == 400:
            self.root.geometry("800x600")
        else:
            self.root.geometry("800x400")

    def save_settings(self):
        """Save current settings and close window"""
        try:
            # Log current state before saving
            logger.debug(f"Current monitor vars: {self.monitor_vars}")
            
            # Save selected monitor serials
            selected_monitors = [
                serial for serial, var in self.monitor_vars.items() 
                if var.get()
            ]
            logger.debug(f"Saving selected monitors: {selected_monitors}")
            self.settings.data["dimmable_monitors"] = selected_monitors

            # Save brightness values
            self.settings.data["monitor_brightness"]["high"] = self.high_brightness.get()
            self.settings.data["monitor_brightness"]["low"] = self.low_brightness.get()

            # Save games list
            self.settings.data["games_to_dimm"] = [
                x.strip() for x in self.games_text.get("1.0", "end-1c").split("\n") 
                if x.strip()
            ]
            
            # Save dimming mode setting
            self.settings.data["dim_all_except_focused"] = self.dim_all_except_focused_var.get()

            # Save to file and close window
            self.settings.save_settings()
            self.root.destroy()
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

class AutoAccept:
    def __init__(self):
        self.settings = Settings()
        self.create_tray_icon()
        self.running = True
        self.program_dir = os.path.join(os.environ['PROGRAMFILES'], PROGRAM_NAME)
        self.startup_dir = os.path.join(winshell.startup(), f"{PROGRAM_NAME}.lnk")
        # Add signal handler
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        logger.debug("Received signal to terminate. Cleaning up...")
        self.stop()

    def is_startup_enabled(self):
        """Check if app is registered to run at startup"""
        return os.path.exists(self.startup_dir)

    def create_shortcut(self, target_path):
        """Create a shortcut in the Windows Startup folder"""
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortCut(self.startup_dir)
        shortcut.Targetpath = target_path
        shortcut.WorkingDirectory = os.path.dirname(target_path)
        shortcut.IconLocation = target_path
        shortcut.save()

    def toggle_startup(self, icon, item):
        """Toggle startup by managing shortcut in Windows Startup folder"""
        try:
            if self.is_startup_enabled():
                # Remove from startup
                if os.path.exists(self.startup_dir):
                    os.remove(self.startup_dir)
                logger.debug("Removed from startup")
            else:
                # Create program directory if it doesn't exist
                os.makedirs(self.program_dir, exist_ok=True)
                
                # Get current executable path
                if getattr(sys, 'frozen', False):
                    # Running as compiled executable
                    current_exe = sys.executable
                else:
                    # Running as script
                    current_exe = os.path.abspath(sys.argv[0])
                
                # Define target path in program directory
                target_exe = os.path.join(self.program_dir, os.path.basename(current_exe))
                
                # Copy executable to program directory if not already there
                if not os.path.exists(target_exe) or os.path.getmtime(current_exe) > os.path.getmtime(target_exe):
                    shutil.copy2(current_exe, target_exe)
                
                # Create shortcut in startup folder
                self.create_shortcut(target_exe)
                logger.debug(f"Added to startup with path: {target_exe}")
        
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
                set_brigtness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )

        def toggle_auto_accept(icon, item):
            self.settings.data["auto_accept_enabled"] = not self.settings.data["auto_accept_enabled"]
            self.settings.save_settings()

        def open_about(icon, item):
            webbrowser.open("https://github.com/zampierilucas/QOL-Scripts")

        def check_startup(item):
            return self.is_startup_enabled()

        menu_items = [
            pystray.MenuItem(PROGRAM_NAME, None, enabled=False),
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
            pystray.MenuItem(
                "Start on Startup",
                self.toggle_startup,
                checked=check_startup
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

    def show_settings(self):
        settings_window = SettingsWindow(self.settings)
        settings_window.root.mainloop()

    def stop(self):
        if self.running:
            self.running = False
            self.icon.stop()
            # Ensure brightness is restored before exit
            if self.settings.data["dimming_enabled"]:
                set_brigtness_side_monitors(
                    self.settings.data["monitor_brightness"]["high"],
                    self.settings.data["dimmable_monitors"]
                )

    def run(self):
        Thread(target=self.main_loop).start()
        self.icon.run()

    def main_loop(self):
        while self.running:
            # Only check for matches if auto accept is enabled
            if self.settings.data["auto_accept_enabled"]:
                if league_client_is_open():
                    try:
                        accept_lol_via_lcu()
                    except Exception as e:
                        logger.exception("Error while attempting to accept LoL match via LCU.")

                foregroundWindow = GetForegroundWindow()
                focused = GetWindowText(foregroundWindow)

                if "Counter-Strike" in focused:
                    accept_match(encoded_accept_cs)

            # Handle dimming separately
            if self.settings.data["dimming_enabled"]:
                focused = GetWindowText(GetForegroundWindow())
                
                if self.settings.data["dim_all_except_focused"]:
                    # New mode: dim all monitors except the one with focused window
                    if focused in self.settings.data['games_to_dimm']:
                        # Get all monitor serials except the focused one
                        monitors_to_dim = get_all_monitor_serials_except_focused()
                        # Dim only non-focused monitors
                        set_brigtness_side_monitors(
                            self.settings.data["monitor_brightness"]["low"],
                            monitors_to_dim
                        )
                        # Keep focused monitor bright (if it's in dimmable list)
                        # Note: This is a simplified approach - might need refinement
                    else:
                        # Restore all monitors to high brightness
                        set_brigtness_side_monitors(
                            self.settings.data["monitor_brightness"]["high"],
                            self.settings.data["dimmable_monitors"]
                        )
                else:
                    # Original mode: dim configured monitors when game is focused
                    if focused in self.settings.data['games_to_dimm']:
                        set_brigtness_side_monitors(
                            self.settings.data["monitor_brightness"]["low"],
                            self.settings.data["dimmable_monitors"]
                        )
                    else:
                        set_brigtness_side_monitors(
                            self.settings.data["monitor_brightness"]["high"],
                            self.settings.data["dimmable_monitors"]
                        )

            time.sleep(1)

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
