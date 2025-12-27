# QOL-Scripts

## Overview
A Windows system tray application that provides quality-of-life automation for gaming:
- League of Legends automation (auto accept, auto pick, auto lock)
- Automatic monitor brightness adjustments when playing games

## Features

### League of Legends
- **Auto Accept** - Automatically accepts match queue pop-ups via the game's API
- **Auto Pick** - Automatically hovers your configured champion based on assigned role (supports primary and secondary picks per role)
- **Auto Lock** - Automatically locks in your champion when the timer is below 5 seconds

### Monitor Dimming
- Automatically dims secondary monitors when a configured game is in focus
- Restores brightness when the game loses focus
- Configurable brightness levels (high/low)
- Option to dim all monitors except the focused one

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
   - Toggle **Dimming** on/off
   - Open **Settings** to configure monitors, games, brightness levels, and default champions
   - View **About** for repository link
   - **Exit** the application
3. When an update is available, an "Update available" button appears in the menu

## Contributing
Feel free to submit issues or pull requests on GitHub.

## License
This project is MIT-Licensed. See the LICENSE file for details.
