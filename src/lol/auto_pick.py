import logging
import asyncio

logger = logging.getLogger(__name__)


class LoLAutoPick:
    """
    WebSocket-based LoL champion auto-picker.
    Automatically hovers the configured default champion based on assigned role.
    Auto-locks if timer is below threshold.
    """
    AUTO_LOCK_THRESHOLD_MS = 5000  # Lock champion if less than 5 seconds left

    def __init__(self, settings):
        self.settings = settings
        self.hovered_this_session = False
        self.locked_this_session = False
        self.lock_timer_task = None
        self.current_action_id = None
        self.current_connection = None

    def register_ws_handlers(self, connector):
        """Register the champion select event handler with the shared connector."""

        @connector.ws.register('/lol-champ-select/v1/session', event_types=('CREATE', 'UPDATE'))
        async def on_champ_select(connection, event):
            if not self.settings.data.get("auto_pick_enabled", True):
                logger.debug("Champion select event but auto-pick is disabled")
                return

            try:
                await self._handle_champ_select(connection, event)
            except Exception as e:
                logger.error(f"Error handling champion select: {e}")

    async def _handle_champ_select(self, connection, event):
        """Handle champion select session events"""
        data = event.data
        if not data:
            return

        if event.type == 'Create':
            self.hovered_this_session = False
            self.locked_this_session = False
            self._cancel_lock_timer()
            logger.info("Entered champion select")

        if self.locked_this_session:
            return

        local_cell_id = data.get('localPlayerCellId')
        if local_cell_id is None:
            return

        my_team = data.get('myTeam', [])
        assigned_position = None
        for player in my_team:
            if player.get('cellId') == local_cell_id:
                assigned_position = player.get('assignedPosition', '')
                break

        if not assigned_position:
            logger.debug("No assigned position found (might be blind pick)")
            return

        default_champions = self.settings.data.get('default_champions', {})
        role_champions = default_champions.get(assigned_position, {})
        primary_id = role_champions.get('primary')
        secondary_id = role_champions.get('secondary')

        if not primary_id and not secondary_id:
            logger.debug(f"No default champion configured for {assigned_position}")
            return

        # Get unavailable champions (banned or picked by others)
        unavailable = self._get_unavailable_champions(data, local_cell_id)

        # Choose champion: primary if available, else secondary
        if primary_id and primary_id not in unavailable:
            champion_id = primary_id
            logger.debug(f"Using primary champion {champion_id} for {assigned_position}")
        elif secondary_id and secondary_id not in unavailable:
            champion_id = secondary_id
            logger.debug(f"Primary unavailable, using secondary champion {champion_id} for {assigned_position}")
        else:
            logger.debug(f"Both primary and secondary champions unavailable for {assigned_position}")
            return

        actions = data.get('actions', [])
        my_pick_action = None
        for action_group in actions:
            for action in action_group:
                if (action.get('actorCellId') == local_cell_id and
                    action.get('type') == 'pick' and
                    not action.get('completed', False)):
                    my_pick_action = action
                    break
            if my_pick_action:
                break

        if not my_pick_action:
            logger.debug("No pending pick action found")
            return

        action_id = my_pick_action.get('id')
        current_champion = my_pick_action.get('championId', 0)
        is_our_turn = my_pick_action.get('isInProgress', False)
        # Get timer info
        timer = data.get('timer', {})
        time_left = timer.get('adjustedTimeLeftInPhase', 99999)
        logger.debug(f"Pick action: is_our_turn={is_our_turn}, champion={current_champion}, time_left={time_left}ms")

        # Hover champion if we haven't yet, it's our turn, and no champion is selected
        # Don't overwrite if user has already hovered/selected a champion manually
        if not self.hovered_this_session and is_our_turn and current_champion == 0:
            try:
                await connection.request(
                    'patch',
                    f'/lol-champ-select/v1/session/actions/{action_id}',
                    data={'championId': champion_id, 'completed': False}
                )
                self.hovered_this_session = True
                logger.info(f"Auto-hovered champion {champion_id} for {assigned_position}")
            except Exception as e:
                logger.error(f"Failed to hover champion: {e}")

        # Schedule auto-lock timer if it's our turn and we have a champion
        auto_lock_enabled = self.settings.data.get("auto_lock_enabled", True)
        if auto_lock_enabled and is_our_turn and current_champion > 0:
            # Store connection and action_id for the timer callback
            self.current_connection = connection
            self.current_action_id = action_id

            # Schedule lock timer if not already scheduled
            if self.lock_timer_task is None or self.lock_timer_task.done():
                delay_ms = time_left - self.AUTO_LOCK_THRESHOLD_MS
                if delay_ms > 0:
                    delay_sec = delay_ms / 1000.0
                    logger.debug(f"Scheduling auto-lock in {delay_sec:.1f}s")
                    self.lock_timer_task = asyncio.create_task(
                        self._auto_lock_after_delay(delay_sec, current_champion)
                    )
        elif not is_our_turn:
            # Not our turn anymore, cancel any pending timer
            self._cancel_lock_timer()

    def _get_unavailable_champions(self, data, local_cell_id):
        """Get set of champion IDs that are banned or picked by others"""
        unavailable = set()

        actions = data.get('actions', [])
        for action_group in actions:
            for action in action_group:
                if not action.get('completed', False):
                    continue

                champion_id = action.get('championId', 0)
                if champion_id == 0:
                    continue

                action_type = action.get('type', '')

                # All completed bans make champions unavailable
                if action_type == 'ban':
                    unavailable.add(champion_id)
                # Picks by others (not us) make champions unavailable
                elif action_type == 'pick' and action.get('actorCellId') != local_cell_id:
                    unavailable.add(champion_id)

        if unavailable:
            logger.debug(f"Unavailable champions: {unavailable}")

        return unavailable

    def _cancel_lock_timer(self):
        """Cancel any pending lock timer"""
        if self.lock_timer_task and not self.lock_timer_task.done():
            self.lock_timer_task.cancel()
            logger.debug("Cancelled auto-lock timer")
        self.lock_timer_task = None

    async def _auto_lock_after_delay(self, delay_sec, champion_id):
        """Wait for delay then lock the champion"""
        try:
            await asyncio.sleep(delay_sec)

            if self.locked_this_session:
                return

            if self.current_connection and self.current_action_id:
                await self.current_connection.request(
                    'patch',
                    f'/lol-champ-select/v1/session/actions/{self.current_action_id}',
                    data={'championId': champion_id, 'completed': True}
                )
                self.locked_this_session = True
                logger.info(f"Auto-locked champion {champion_id}")
        except asyncio.CancelledError:
            logger.debug("Auto-lock timer was cancelled")
        except Exception as e:
            logger.error(f"Failed to auto-lock champion: {e}")

    def on_disconnect(self):
        """Called when LCU disconnects."""
        self._cancel_lock_timer()
        self.hovered_this_session = False
        self.locked_this_session = False
