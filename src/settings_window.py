import sys
import logging
import tkinter as tk
from tkinter import ttk
import screen_brightness_control as sbc
import win32gui
import sv_ttk
import darkdetect
import pywinstyles

from lol.lcu_api import LCUApi
from brightness import clean_window_title

logger = logging.getLogger(__name__)

ROLE_DISPLAY_NAMES = {
    "top": "Top",
    "jungle": "Jungle",
    "middle": "Mid",
    "bottom": "ADC",
    "utility": "Support"
}


def apply_theme_to_titlebar(root):
    version = sys.getwindowsversion()

    if version.major == 10 and version.build >= 22000:
        pywinstyles.change_header_color(root, "#1c1c1c" if sv_ttk.get_theme() == "dark" else "#fafafa")
    elif version.major == 10:
        pywinstyles.apply_style(root, "dark" if sv_ttk.get_theme() == "dark" else "normal")
        root.wm_attributes("-alpha", 0.99)
        root.wm_attributes("-alpha", 1)


class SettingsWindow:
    def __init__(self, settings, app=None):
        self.settings = settings
        self.app = app
        self.root = tk.Tk()
        self.root.title("QOL Settings")
        self.root.geometry("420x1000")
        self.root.minsize(380, 1000)
        self.root.maxsize(600, 1000)
        sv_ttk.set_theme(darkdetect.theme())
        self.monitor_vars = {}
        self.games_list = sorted(self.settings.data["games_to_dimm"], key=str.lower)

        self.lcu_api = LCUApi()
        self.owned_champions = self.lcu_api.get_owned_champions() if self.lcu_api.is_connected() else {}
        self.champion_id_to_name = {v: k for k, v in self.owned_champions.items()}
        self.champion_vars = {}

        self.create_widgets()
        apply_theme_to_titlebar(self.root)

    def get_running_programs(self):
        """Get list of running program window titles (filters out tool/overlay windows)"""
        programs = set()
        excluded = {"QOL Settings", "Add Game"}

        def enum_windows_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            ex_style = win32gui.GetWindowLong(hwnd, -20)
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

        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 400) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 450) // 2
        dialog.geometry(f"+{x}+{y}")

        main_frame = ttk.Frame(dialog, padding=15)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text="Select from running programs:").pack(anchor="w")

        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill="both", expand=True, pady=(5, 10))

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        programs_list = tk.Listbox(list_frame, height=15, yscrollcommand=scrollbar.set, selectmode="extended")
        programs_list.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=programs_list.yview)

        for program in self.get_running_programs():
            programs_list.insert(tk.END, program)

        ttk.Separator(main_frame, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(main_frame, text="Or enter manually:").pack(anchor="w")
        manual_entry = ttk.Entry(main_frame)
        manual_entry.pack(fill="x", pady=(5, 10))

        def add_selected():
            added = False
            for idx in programs_list.curselection():
                game = clean_window_title(programs_list.get(idx))
                if game and game not in self.games_list:
                    self.games_list.append(game)
                    added = True
            manual = clean_window_title(manual_entry.get())
            if manual and manual not in self.games_list:
                self.games_list.append(manual)
                added = True
            if added:
                self.games_list.sort(key=str.lower)
                self.refresh_games_listbox()
            dialog.destroy()

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=(5, 0))
        ttk.Button(btn_frame, text="Add Selected", command=add_selected).pack(side="right")

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
        selected.reverse()
        for idx in selected:
            del self.games_list[idx]
        self.refresh_games_listbox()

    def create_widgets(self):
        main_container = ttk.Frame(self.root)
        main_container.pack(fill="both", expand=True, padx=15, pady=15)

        # Monitors Frame
        monitors_frame = ttk.LabelFrame(main_container, text="Dimmable Monitors", padding=10)
        monitors_frame.pack(fill="x", pady=(0, 10))

        try:
            monitors_info = sbc.list_monitors_info()
            logger.debug(f"Found {len(monitors_info)} monitors")

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

        list_container = ttk.Frame(games_frame)
        list_container.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")

        self.games_listbox = tk.Listbox(list_container, height=6, selectmode="extended", yscrollcommand=scrollbar.set)
        self.games_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.games_listbox.yview)

        for game in self.games_list:
            self.games_listbox.insert(tk.END, game)

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

        # Champion selection section
        champs_frame = ttk.LabelFrame(main_container, text="Default Champions per Role", padding=10)
        champs_frame.pack(fill="x", pady=(0, 10))

        if self.owned_champions:
            champion_names = ["None"] + sorted(self.owned_champions.keys())

            # Header row
            header_frame = ttk.Frame(champs_frame)
            header_frame.pack(fill="x", pady=(0, 5))
            ttk.Label(header_frame, text="Role", width=8).pack(side="left")
            ttk.Label(header_frame, text="Primary", width=15).pack(side="left", padx=(5, 0))
            ttk.Label(header_frame, text="Secondary", width=15).pack(side="left", padx=(5, 0))

            for role_key, role_display in ROLE_DISPLAY_NAMES.items():
                role_frame = ttk.Frame(champs_frame)
                role_frame.pack(fill="x", pady=2)

                ttk.Label(role_frame, text=f"{role_display}:", width=8).pack(side="left")

                # Get current settings (handle both old and new format)
                role_data = self.settings.data.get("default_champions", {}).get(role_key, {})
                if isinstance(role_data, dict):
                    primary_id = role_data.get('primary')
                    secondary_id = role_data.get('secondary')
                else:
                    # Old format
                    primary_id = role_data
                    secondary_id = None

                # Primary champion
                primary_var = tk.StringVar()
                if primary_id and primary_id in self.champion_id_to_name:
                    primary_var.set(self.champion_id_to_name[primary_id])
                else:
                    primary_var.set("None")

                primary_combo = ttk.Combobox(
                    role_frame,
                    textvariable=primary_var,
                    values=champion_names,
                    state="readonly",
                    width=13
                )
                primary_combo.pack(side="left", padx=(5, 0))

                # Secondary champion
                secondary_var = tk.StringVar()
                if secondary_id and secondary_id in self.champion_id_to_name:
                    secondary_var.set(self.champion_id_to_name[secondary_id])
                else:
                    secondary_var.set("None")

                secondary_combo = ttk.Combobox(
                    role_frame,
                    textvariable=secondary_var,
                    values=champion_names,
                    state="readonly",
                    width=13
                )
                secondary_combo.pack(side="left", padx=(5, 0))

                self.champion_vars[role_key] = {
                    'primary': primary_var,
                    'secondary': secondary_var
                }
        else:
            ttk.Label(
                champs_frame,
                text="Start the League Client to configure champions",
                foreground="gray"
            ).pack(anchor="w")

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

            self.settings.data["games_to_dimm"] = sorted(
                [clean_window_title(g) for g in self.games_list if clean_window_title(g)],
                key=str.lower
            )
            logger.debug(f"Saved games_to_dimm: {self.settings.data['games_to_dimm']}")

            self.settings.data["dim_all_except_focused"] = self.dim_all_except_focused_var.get()

            if self.champion_vars:
                default_champions = {}
                for role_key, vars_dict in self.champion_vars.items():
                    primary_name = vars_dict['primary'].get()
                    secondary_name = vars_dict['secondary'].get()

                    primary_id = None
                    if primary_name and primary_name != "None":
                        primary_id = self.owned_champions.get(primary_name)

                    secondary_id = None
                    if secondary_name and secondary_name != "None":
                        secondary_id = self.owned_champions.get(secondary_name)

                    default_champions[role_key] = {
                        'primary': primary_id,
                        'secondary': secondary_id
                    }
                self.settings.data["default_champions"] = default_champions

            if self.app:
                startup_enabled = self.app.is_startup_enabled()
                startup_wanted = self.startup_var.get()
                if startup_enabled != startup_wanted:
                    self.app.toggle_startup(None, None)

            self.settings.save_settings()
            if self.app:
                self.app.last_brightness_state = None
                self.app.last_focused_window = None
            self.root.destroy()
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
