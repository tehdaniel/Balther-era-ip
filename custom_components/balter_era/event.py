"""Doorbell event for Balter ERA IP."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BalterConfigEntry, async_refresh_image
from .const import signal_ring
from .entity import BalterEntity
from .pybalter import BalterError, DeviceRecord

_LOGGER = logging.getLogger(__name__)

EVENT_RING = "ring"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the doorbell event per device."""
    async_add_entities(BalterDoorbell(entry, device) for device in entry.runtime_data.devices)


class BalterDoorbell(BalterEntity, EventEntity):
    """Fires when somebody rings at the outdoor station."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING]
    _attr_translation_key = "doorbell"

    def __init__(self, entry: BalterConfigEntry, device: DeviceRecord) -> None:
        super().__init__(entry, device, "doorbell")

    async def async_added_to_hass(self) -> None:
        """Listen for pushes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_ring(self._entry.entry_id, self._device.serial),
                self._on_ring,
            )
        )

    @callback
    def _on_ring(self, payload: dict[str, Any]) -> None:
        # The payload format is not documented; pass its scalar values on so
        # automations can use them.
        attributes = {
            str(key): value
            for key, value in payload.items()
            if isinstance(value, str | int | float | bool)
        }
        self._trigger_event(EVENT_RING, attributes)
        self.async_write_ha_state()
        self._entry.async_create_background_task(
            self.hass, self._refresh_image(), f"{self.entity_id} snapshot"
        )

    async def _refresh_image(self) -> None:
        try:
            await async_refresh_image(self.hass, self._entry, self._device)
        except BalterError as err:
            _LOGGER.warning("Could not fetch image after ring: %s", err)
