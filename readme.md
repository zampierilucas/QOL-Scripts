# QOL-Scripts

## Overview
A Windows system tray application that provides quality-of-life automation for gaming:
- Auto-accept match queue pop-ups (League of Legends and CS2)
- League of Legends champion auto-pick / auto-lock
- Automatic monitor brightness adjustments when playing games
- Automatic NVIDIA digital vibrance switching per game

## Features

### Auto Accept
- Automatically accepts match queue pop-ups for **League of Legends** (via the game's API) and **CS2** (by monitoring the console log)
- CS2 requires `-condebug` in Steam launch options (automatically added if missing); a popup and tray indicator notify you if CS2 needs to be restarted to apply the flag

### League of Legends
- **Auto Pick** - Automatically hovers your configured champion based on assigned role (supports primary and secondary picks per role)
- **Auto Lock** - Automatically locks in your champion when the timer is below 5 seconds

### Monitor Dimming
- Automatically dims secondary monitors when a configured game is in focus
- Restores brightness when the game loses focus
- Configurable brightness levels (high/low)
- Option to dim all monitors except the focused one

### Digital Vibrance (NVIDIA)
- Automatically raises digital vibrance when a configured game is in focus and restores the default level when it loses focus
- Configurable game vs. default levels, on the same scale as the NVIDIA Control Panel slider
- Pick which displays the change applies to
- Requires an NVIDIA GPU

### General
- **System Tray** - Runs quietly in the background with easy access via tray icon
- **Update Notifications** - Checks for new releases and shows "Update available" button in tray menu
- **Run at Startup** - Option to automatically start with Windows
- **Settings GUI** - Theme-aware interface with searchable champion autocomplete

## Installation

### Option 1: Download Release (Recommended)
1. Download the latest `.exe` from [GitHub Releases](https://github.com/zampierilucas/QOL-Scripts/releases)
2. Run the executable

### Option 2: Run from Source
1. Install Python 3.11 or higher
2. Clone the repository and install dependencies:
   ```bash
   git clone https://github.com/zampierilucas/QOL-Scripts.git
   cd QOL-Scripts
   pip install .
   ```
3. Run the application:
   ```bash
   python src/main.py
   ```

## Usage
1. The application runs in the system tray
2. Right-click the tray icon to:
   - Toggle **LoL - Auto Accept** on/off
   - Toggle **LoL - Auto Pick** on/off
   - Toggle **LoL - Auto Lock** on/off
   - Toggle **CS2 - Auto Accept** on/off
   - Toggle **Dimming** on/off
   - Toggle **Digital Vibrance** on/off
   - Open **Settings** to configure monitors, games, brightness levels, vibrance levels/displays, and default champions
   - View **About** for repository link
   - **Exit** the application
3. When an update is available, an "Update available" button appears in the menu

## Contributing
Feel free to submit issues or pull requests on GitHub.

## License
This project is MIT-Licensed. See the LICENSE file for details.
