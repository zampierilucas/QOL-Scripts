import logging
import asyncio
from abc import ABC, abstractmethod
from threading import Thread
from lcu_driver import Connector

logger = logging.getLogger(__name__)


class LCUConnectorBase(ABC):
    """
    Base class for WebSocket-based LCU connectors.
    Handles the common connector lifecycle (start/stop) and thread management.
    """

    def __init__(self, settings, name: str):
        self.settings = settings
        self.name = name
        self.connector = None
        self.loop = None
        self.running = False

    def start(self):
        """Start the WebSocket connector in a separate thread"""
        if not self.running:
            self.running = True
            thread = Thread(target=self._run_connector, daemon=True)
            thread.start()
            logger.info(f"LoL WebSocket {self.name} thread started")

    def _run_connector(self):
        """Run the connector (blocks until stopped)"""
        try:
            logger.debug(f"{self.name} connector thread starting...")
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.connector = Connector()

            @self.connector.ready
            async def on_lcu_ready(connection):
                logger.info(f"LoL Client connected - WebSocket {self.name} is active")

            @self.connector.close
            async def on_lcu_close(connection):
                logger.info(f"LoL Client disconnected - WebSocket {self.name} stopped")

            self._register_handlers()

            logger.debug(f"{self.name} handlers registered, calling connector.start()...")
            self.connector.start()
            logger.debug(f"{self.name} connector.start() returned")
        except Exception as e:
            logger.error(f"LoL WebSocket {self.name} connector error: {e}", exc_info=True)

    @abstractmethod
    def _register_handlers(self):
        """Register WebSocket event handlers. Must be implemented by subclasses."""
        pass

    def stop(self):
        """Stop the WebSocket connector"""
        self._on_stop()
        if self.running:
            self.running = False
            try:
                if self.connector and self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.connector.stop(), self.loop)
                    self.loop.call_soon_threadsafe(self.loop.stop)
                logger.info(f"LoL WebSocket {self.name} stopped")
            except Exception as e:
                logger.error(f"Error stopping LoL WebSocket {self.name} connector: {e}")

    def _on_stop(self):
        """Hook for subclasses to perform cleanup before stopping. Override if needed."""
        pass
