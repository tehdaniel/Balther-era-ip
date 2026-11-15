"""Door opener buttons for Balter ERA IP."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BalterConfigEntry
from .const import DOMAIN, MANUFACTURER, MODEL
from .pybalter import DOOR_1, DOOR_2, BalterClient, BalterError, DeviceRecord

PARALLEL_UPDATES = 1

DOORS: tuple[tuple[str, int], ...] = (("door_1", DOOR_1), ("door_2", DOOR_2))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one button per door relay and device."""
    client = entry.runtime_data.client
    async_add_entities(
        BalterDoorButton(client, device, key, door)
        for device in entry.runtime_data.devices
        for key, door in DOORS
    )


class BalterDoorButton(ButtonEntity):
    """Triggers one door relay."""

    _attr_has_entity_name = True

    def __init__(
        self, client: BalterClient, device: DeviceRecord, key: str, door: int
    ) -> None:
        self._client = client
        self._device = device
        self._door = door
        self._attr_translation_key = key
        self._attr_unique_id = f"{device.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
            name=device.name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=device.serial,
            sw_version=device.firmware,
        )

    async def async_press(self) -> None:
        """Open the door."""
        try:
            await self._client.async_open_door(self._device, self._door)
        except BalterError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="open_failed",
                translation_placeholders={"error": str(err)},
            ) from err
