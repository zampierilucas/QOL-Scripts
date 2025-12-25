import json
import pathlib
import appdirs

from brightness import clean_window_title

PROGRAM_NAME = "QOL-Scripts"
CONFIG_DIR = pathlib.Path(appdirs.user_config_dir(PROGRAM_NAME))
CONFIG_FILE = CONFIG_DIR / "settings.json"


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
        "auto_pick_enabled": True,
        "auto_lock_enabled": True,
        "dim_all_except_focused": False,
        "default_champions": {
            "top": {"primary": None, "secondary": None},
            "jungle": {"primary": None, "secondary": None},
            "middle": {"primary": None, "secondary": None},
            "bottom": {"primary": None, "secondary": None},
            "utility": {"primary": None, "secondary": None}
        }
    }

    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.load_settings()

    def load_settings(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                self.data = json.load(f)
                updated = False
                for key, default_value in self.DEFAULT_SETTINGS.items():
                    if key not in self.data:
                        self.data[key] = default_value
                        updated = True
                if "games_to_dimm" in self.data:
                    self.data["games_to_dimm"] = sorted(
                        [clean_window_title(g) for g in self.data["games_to_dimm"] if clean_window_title(g)],
                        key=str.lower
                    )
                # Migrate old champion format (single ID) to new format (primary/secondary)
                if "default_champions" in self.data:
                    for role, value in self.data["default_champions"].items():
                        if not isinstance(value, dict):
                            # Old format: just a champion ID
                            self.data["default_champions"][role] = {
                                "primary": value,
                                "secondary": None
                            }
                            updated = True
                if updated:
                    self.save_settings()
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = self.DEFAULT_SETTINGS.copy()
            self.save_settings()

    def save_settings(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.data, f, indent=4)
