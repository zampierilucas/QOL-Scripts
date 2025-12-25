import os
import logging
import requests

logger = logging.getLogger(__name__)


class LCUApi:
    """Synchronous LCU API client for fetching data from the League Client"""

    LOCKFILE_PATHS = [
        os.path.expandvars(r"%LOCALAPPDATA%\Riot Games\League of Legends\lockfile"),
        os.path.expandvars(r"C:\Riot Games\League of Legends\lockfile"),
    ]

    def __init__(self):
        self.base_url = None
        self.auth = None
        self._connect()

    def _connect(self):
        """Try to connect to the LCU by reading the lockfile"""
        lockfile_path = None
        for path in self.LOCKFILE_PATHS:
            if os.path.exists(path):
                lockfile_path = path
                break

        if not lockfile_path:
            logger.debug("LCU lockfile not found")
            return

        try:
            with open(lockfile_path, 'r') as f:
                content = f.read()
            parts = content.split(':')
            if len(parts) >= 5:
                port = parts[2]
                password = parts[3]
                self.base_url = f"https://127.0.0.1:{port}"
                self.auth = ('riot', password)
                logger.debug(f"LCU API connected on port {port}")
        except Exception as e:
            logger.error(f"Failed to read LCU lockfile: {e}")

    def is_connected(self):
        return self.base_url is not None

    def get(self, endpoint):
        """Make a GET request to the LCU API"""
        if not self.is_connected():
            return None
        try:
            response = requests.get(
                f"{self.base_url}{endpoint}",
                auth=self.auth,
                verify=False,
                timeout=5
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.debug(f"LCU API request failed: {e}")
        return None

    def get_owned_champions(self):
        """Fetch list of owned champions"""
        data = self.get('/lol-champions/v1/owned-champions-minimal')
        if data:
            return {champ['name']: champ['id'] for champ in data if champ.get('ownership', {}).get('owned', False)}
        return {}
