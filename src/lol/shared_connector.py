import logging
import asyncio
import ctypes
import time
from threading import Thread
from lcu_driver import Connector

logger = logging.getLogger(__name__)

# Windows API for fast window detection
user32 = ctypes.windll.user32


def is_lol_client_running() -> bool:
    """
    Fast check if League Client is running using FindWindow.
    Much faster than psutil process iteration (~0.01ms vs ~1700ms).
    """
    # RCLIENT is the window class for LeagueClientUx
    hwnd = user32.FindWindowW("RCLIENT", None)
    return hwnd != 0


class SharedLCUConnector:
    """
    A single shared LCU connector that multiple handlers can register with.
    Uses fast FindWindow detection instead of slow psutil polling.
    """

    def __init__(self):
        self.connector = None
        self.loop = None
        self.running = False
        self._handlers = []
        self._ready_callbacks = []
        self._close_callbacks = []
        self._client_connected = False

    def register_handler(self, handler):
        """Register a handler that will be called to set up its WebSocket subscriptions."""
        self._handlers.append(handler)

    def register_ready_callback(self, callback):
        """Register a callback to be called when LCU connects."""
        self._ready_callbacks.append(callback)

    def register_close_callback(self, callback):
        """Register a callback to be called when LCU disconnects."""
        self._close_callbacks.append(callback)

    def start(self):
        """Start the shared connector in a separate thread."""
        if not self.running:
            self.running = True
            thread = Thread(target=self._run_connector_loop, daemon=True)
            thread.start()
            logger.info("Shared LCU connector thread started")

    def _run_connector_loop(self):
        """
        Main loop that waits for LoL client using fast FindWindow,
        then starts lcu-driver connector when client is detected.
        """
        while self.running:
            try:
                # Phase 1: Wait for LoL client using fast FindWindow check
                logger.debug("Waiting for LoL client (using FindWindow)...")
                while self.running and not is_lol_client_running():
                    time.sleep(2.0)  # Check every 2 seconds (very low CPU)

                if not self.running:
                    break

                logger.info("LoL client detected, starting connector...")

                # Phase 2: Start lcu-driver connector (will quickly find the process)
                self._run_connector()

                # Connector returned means client closed, loop back to wait
                if self.running:
                    logger.debug("Connector stopped, will wait for client again")
                    time.sleep(1.0)  # Brief pause before re-checking

            except Exception as e:
                logger.error(f"Error in connector loop: {e}", exc_info=True)
                if self.running:
                    time.sleep(5.0)  # Wait before retry on error

    def _run_connector(self):
        """Run the lcu-driver connector (blocks until client closes)."""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.connector = Connector()
            self._client_connected = False

            @self.connector.ready
            async def on_lcu_ready(connection):
                self._client_connected = True
                logger.info("LoL Client connected - shared connector active")
                for callback in self._ready_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(connection)
                        else:
                            callback(connection)
                    except Exception as e:
                        logger.error(f"Error in ready callback: {e}")

            @self.connector.close
            async def on_lcu_close(connection):
                self._client_connected = False
                logger.info("LoL Client disconnected")
                for callback in self._close_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(connection)
                        else:
                            callback(connection)
                    except Exception as e:
                        logger.error(f"Error in close callback: {e}")

            # Register all handlers
            for handler in self._handlers:
                try:
                    handler.register_ws_handlers(self.connector)
                except Exception as e:
                    logger.error(f"Error registering handler: {e}")

            logger.debug("Handlers registered, starting connector...")
            self.connector.start()  # Blocks until client closes

        except Exception as e:
            logger.error(f"Connector error: {e}", exc_info=True)
        finally:
            self.connector = None
            self.loop = None

    def stop(self):
        """Stop the shared connector."""
        if self.running:
            self.running = False
            try:
                if self.connector and self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.connector.stop(), self.loop)
                    self.loop.call_soon_threadsafe(self.loop.stop)
                logger.info("Shared LCU connector stopped")
            except Exception as e:
                logger.error(f"Error stopping shared connector: {e}")
