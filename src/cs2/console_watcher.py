import logging
import os
import re
import time
import winreg
from threading import Thread

import ctypes

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

CS2_WINDOW_TITLE = "Counter-Strike 2"
CS2_APP_ID = "730"

# Console log pattern that indicates a match was found.
# CS2 logs: "[Client] CheckServerReservation: N @ =[A:1:SESSION_ID:PORT] (...)"
# Each new accept banner gets a new session id and starts at N=1; subsequent
# N=2, N=3 are status polls within the SAME banner. We dedup by session id so
# defective polling never re-fires the click and so consecutive new banners
# (e.g. when a player declines and a new match is found) both get clicked.
MATCH_FOUND_RE = re.compile(
    r"\[Client\] CheckServerReservation: 1 @ (=\[[^\]]+\])"
)


def _is_cs2_running() -> bool:
    hwnd = user32.FindWindowW(None, CS2_WINDOW_TITLE)
    return hwnd != 0


def _find_steam_path() -> str | None:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Valve\Steam"
        )
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        winreg.CloseKey(key)
        if os.path.isdir(path):
            return path
    except OSError:
        pass
    default = r"C:\Program Files (x86)\Steam"
    return default if os.path.isdir(default) else None


def _parse_library_folders(vdf_path: str) -> list[str]:
    paths = []
    if not os.path.exists(vdf_path):
        return paths
    with open(vdf_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith('"path"'):
                parts = line.split('"')
                if len(parts) >= 4:
                    path = parts[3]
                    steamapps = os.path.join(path, "steamapps")
                    if os.path.isdir(steamapps):
                        paths.append(steamapps)
    return paths


def _find_localconfig_path(steam_path: str) -> str | None:
    """Find the localconfig.vdf for the Steam account that owns CS2."""
    userdata = os.path.join(steam_path, "userdata")
    if not os.path.isdir(userdata):
        return None

    best_path = None
    best_mtime = 0
    for uid in os.listdir(userdata):
        candidate = os.path.join(userdata, uid, "config", "localconfig.vdf")
        if os.path.isfile(candidate):
            try:
                mtime = os.path.getmtime(candidate)
                content = open(candidate, encoding="utf-8", errors="replace").read()
                # Only consider accounts that have CS2 in their config
                if f'"{CS2_APP_ID}"' in content and mtime > best_mtime:
                    best_mtime = mtime
                    best_path = candidate
            except OSError:
                continue
    return best_path


def _has_condebug(steam_path: str) -> bool:
    """Return True if -condebug is already in CS2's Steam launch options."""
    config_path = _find_localconfig_path(steam_path)
    if not config_path:
        return False
    try:
        with open(config_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        apps_idx = content.find('"apps"')
        if apps_idx == -1:
            return False
        idx730 = content.find(f'"{CS2_APP_ID}"', apps_idx)
        if idx730 == -1:
            return False
        brace_open = content.find("{", idx730)
        if brace_open == -1:
            return False
        depth = 0
        block_end = brace_open
        for i in range(brace_open, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    block_end = i
                    break
        block = content[idx730:block_end]
        m = re.search(r'"LaunchOptions"\s+"([^"]*)"', block)
        if not m:
            return False
        return "-condebug" in m.group(1).split()
    except OSError:
        return False


def _ensure_condebug(steam_path: str) -> bool:
    """
    Add -condebug to CS2 launch options in Steam's localconfig.vdf if missing.
    Returns True if the file was modified, False if already set or not found.
    """
    config_path = _find_localconfig_path(steam_path)
    if not config_path:
        logger.warning("Could not find Steam localconfig.vdf to set -condebug")
        return False

    with open(config_path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Find the "730" app block inside the "apps" section
    apps_idx = content.find('"apps"')
    if apps_idx == -1:
        logger.warning("Could not find 'apps' section in localconfig.vdf")
        return False

    idx730 = content.find(f'"{CS2_APP_ID}"', apps_idx)
    if idx730 == -1:
        logger.warning("Could not find CS2 (730) block in localconfig.vdf")
        return False

    # Find the end of the 730 block (the closing brace at the right indent level)
    brace_open = content.find("{", idx730)
    if brace_open == -1:
        return False
    depth = 0
    block_end = brace_open
    for i in range(brace_open, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                block_end = i
                break

    block = content[idx730:block_end]

    if '"LaunchOptions"' in block:
        # Key exists — add -condebug if not already there
        def add_flag(m):
            opts = m.group(1)
            if "-condebug" in opts.split():
                return m.group(0)
            return m.group(0).replace(f'"{opts}"', f'"{(opts + " -condebug").strip()}"')

        new_block = re.sub(r'"LaunchOptions"\s+"([^"]*)"', add_flag, block)
        if new_block == block:
            logger.debug("-condebug already set in CS2 launch options")
            return False
        new_content = content[:idx730] + new_block + content[block_end:]
    else:
        # Key absent — insert before the closing brace, matching Steam's tab style
        insert = '\t\t\t\t\t\t"LaunchOptions"\t\t"-condebug"\n\t\t\t\t\t'
        new_content = content[:block_end] + insert + content[block_end:]

    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)
    os.replace(tmp_path, config_path)

    logger.info(f"Added -condebug to CS2 launch options in {config_path}")
    return True


def _find_cs2_path() -> str | None:
    steam_path = _find_steam_path()
    if not steam_path:
        logger.debug("Steam installation not found")
        return None

    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    library_paths = _parse_library_folders(vdf_path)
    library_paths.insert(0, os.path.join(steam_path, "steamapps"))

    for lib_path in library_paths:
        manifest = os.path.join(lib_path, f"appmanifest_{CS2_APP_ID}.acf")
        if os.path.exists(manifest):
            cs2_dir = os.path.join(
                lib_path, "common", "Counter-Strike Global Offensive"
            )
            if os.path.isdir(cs2_dir):
                return cs2_dir

    logger.debug("CS2 installation not found in any Steam library")
    return None


class CS2ConsoleWatcher:
    def __init__(self):
        self.running = False
        self._callbacks = []
        self._condebug_missing_callbacks = []
        self._cs2_path = None
        self._thread = None

    def register_callback(self, callback):
        self._callbacks.append(callback)

    def register_condebug_missing_callback(self, callback):
        """Called when CS2 is running but -condebug is not set (after auto-fix attempt)."""
        self._condebug_missing_callbacks.append(callback)

    def start(self):
        if not self.running:
            self.running = True
            self._thread = Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("CS2 console watcher thread started")

    def stop(self):
        self.running = False

    def _run_loop(self):
        while self.running:
            try:
                if not self._cs2_path:
                    self._cs2_path = _find_cs2_path()
                    if not self._cs2_path:
                        time.sleep(30)
                        continue
                    logger.info(f"CS2 found at: {self._cs2_path}")

                # Wait for CS2 to launch
                logger.debug("Waiting for CS2 to launch...")
                while self.running and not _is_cs2_running():
                    time.sleep(2.0)

                if not self.running:
                    break

                logger.info("CS2 detected, starting console log watcher...")
                self._tail_console_log()

                if self.running:
                    logger.debug("CS2 closed, will wait for it again")
                    time.sleep(1.0)

            except Exception as e:
                logger.error(f"Error in CS2 console watcher: {e}", exc_info=True)
                if self.running:
                    time.sleep(5.0)

    def _tail_console_log(self):
        log_path = os.path.join(
            self._cs2_path, "game", "csgo", "console.log"
        )

        # Check launch options directly — no guessing from file state
        steam_path = _find_steam_path()
        if steam_path and not _has_condebug(steam_path):
            logger.warning("CS2 launch options missing -condebug")
            fixed = _ensure_condebug(steam_path)
            for cb in self._condebug_missing_callbacks:
                try:
                    cb(fixed)
                except Exception as e:
                    logger.error(f"Error in condebug missing callback: {e}")
            # Wait for user to restart CS2 with the flag applied
            while self.running and _is_cs2_running() and not os.path.exists(log_path):
                time.sleep(2.0)

        # Wait for console.log to appear (it may take a moment after launch)
        while self.running and _is_cs2_running() and not os.path.exists(log_path):
            time.sleep(1.0)

        if not self.running or not _is_cs2_running():
            return

        logger.debug(f"Tailing console.log at: {log_path}")
        last_size = os.path.getsize(log_path)
        last_pos = last_size  # Start from end

        while self.running and _is_cs2_running():
            try:
                current_size = os.path.getsize(log_path)
            except OSError:
                time.sleep(0.5)
                continue

            # File was truncated/rewritten by CS2 — reset to beginning
            if current_size < last_pos:
                logger.debug("console.log was truncated, resetting position")
                last_pos = 0

            if current_size == last_pos:
                time.sleep(0.1)
                continue

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_pos)
                new_data = f.read()
                last_pos = f.tell()

            for line in new_data.splitlines():
                line = line.strip()
                if not line:
                    continue

                m = MATCH_FOUND_RE.search(line)
                if m:
                    session_id = m.group(1)
                    logger.info(f"CS2 match found: {line}")
                    self._notify_callbacks(session_id)
                    # No break — multiple distinct sessions in one chunk should
                    # all fire; CS2AutoAccept dedups by session_id.

    def _notify_callbacks(self, session_id):
        for cb in self._callbacks:
            try:
                cb(session_id)
            except Exception as e:
                logger.error(f"Error in CS2 match callback: {e}")
