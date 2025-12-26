import argparse
import logging

import urllib3
from urllib3.exceptions import InsecureRequestWarning

from app import QOLApp

# Disable "unverified HTTPS request" warnings
urllib3.disable_warnings(InsecureRequestWarning)


def setup_logging(debug=False):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s'
    )
    logging.getLogger("screen_brightness_control").setLevel(logging.ERROR)
    logging.getLogger("lcu-driver").setLevel(logging.INFO)


if __name__ == "__main__":
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
