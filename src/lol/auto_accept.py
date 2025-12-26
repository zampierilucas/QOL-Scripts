import logging
from .connector_base import LCUConnectorBase

logger = logging.getLogger(__name__)


class LoLAutoAccept(LCUConnectorBase):
    """
    WebSocket-based LoL match auto-accepter using lcu-driver.
    Runs in a separate thread and reacts instantly to ready-check events.
    """

    def __init__(self, settings):
        super().__init__(settings, "auto-accept")
        self.accepted_this_check = False

    def _register_handlers(self):
        """Register the ready-check event handler"""

        @self.connector.ws.register('/lol-matchmaking/v1/ready-check', event_types=('CREATE', 'UPDATE', 'DELETE'))
        async def on_ready_check(connection, event):
            # Reset flag when ready check ends
            if event.type == 'Delete':
                self.accepted_this_check = False
                return

            if self.accepted_this_check:
                return

            if self.settings.data.get("auto_accept_enabled", True):
                self.accepted_this_check = True
                try:
                    await connection.request('post', '/lol-matchmaking/v1/ready-check/accept')
                    logger.info("Match auto-accepted")
                except Exception as e:
                    self.accepted_this_check = False
                    logger.error(f"Failed to auto-accept match: {e}")
            else:
                logger.debug("Ready check detected but auto-accept is disabled")
