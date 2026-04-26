import argparse
import logging
import ctypes

# Silence noisy loggers before importing modules that use them
logging.getLogger("PIL").setLevel(logging.WARNING)
logging.getLogger("screen_brightness_control").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.WARNING)

import urllib3
from urllib3.exceptions import InsecureRequestWarning

from app import QOLApp

_MUTEX_NAME = "QOLScripts_SingleInstance"


def _acquire_single_instance_mutex():
    """Returns a mutex handle if this is the first instance, None if already running."""
    mutex = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 0xB7:  # ERROR_ALREADY_EXISTS
        return None
    return mutex

# Disable "unverified HTTPS request" warnings
urllib3.disable_warnings(InsecureRequestWarning)


def setup_logging(debug=False):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s'
    )
    logging.getLogger("screen_brightness_control").setLevel(logging.ERROR)
    logging.getLogger("lcu-driver").setLevel(logging.INFO)
    logging.getLogger("PIL").setLevel(logging.WARNING)


if __name__ == "__main__":
    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        logging.warning("QOL-Scripts is already running. Exiting.")
        raise SystemExit(0)

    parser = argparse.ArgumentParser(description="QOL-Scripts")
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    setup_logging(args.debug)

    app = QOLApp()
    try:
        app.run()
    except KeyboardInterrupt:
        logging.debug("Received Ctrl+C. Shutting down...")
        app.stop()
