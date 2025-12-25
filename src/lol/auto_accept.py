import logging
import asyncio
from threading import Thread
from lcu_driver import Connector

logger = logging.getLogger(__name__)


class LoLAutoAccept:
    """
    WebSocket-based LoL match auto-accepter using lcu-driver.
    Runs in a separate thread and reacts instantly to ready-check events.
    """
    def __init__(self, settings):
        self.settings = settings
        self.connector = None
        self.loop = None
        self.running = False
        self.accepted_this_check = False

    def start(self):
        """Start the WebSocket connector in a separate thread"""
        if not self.running:
            self.running = True
            thread = Thread(target=self._run_connector, daemon=True)
            thread.start()
            logger.info("LoL WebSocket auto-accept thread started")

    def _run_connector(self):
        """Run the connector (blocks until stopped)"""
        try:
            logger.debug("Auto-accept connector thread starting...")
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            self.connector = Connector()
            logger.debug("Auto-accept Connector created, registering handlers...")

            @self.connector.ready
            async def on_lcu_ready(connection):
                logger.info("LoL Client connected - WebSocket auto-accept is active")

            @self.connector.close
            async def on_lcu_close(connection):
                logger.info("LoL Client disconnected - WebSocket auto-accept stopped")

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

            logger.debug("Auto-accept handlers registered, calling connector.start()...")
            self.connector.start()
            logger.debug("Auto-accept connector.start() returned")
        except Exception as e:
            logger.error(f"LoL WebSocket connector error: {e}", exc_info=True)

    def stop(self):
        """Stop the WebSocket connector"""
        if self.running:
            self.running = False
            try:
                if self.connector and self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.connector.stop(), self.loop)
                    self.loop.call_soon_threadsafe(self.loop.stop)
                logger.info("LoL WebSocket auto-accept stopped")
            except Exception as e:
                logger.error(f"Error stopping LoL WebSocket connector: {e}")
