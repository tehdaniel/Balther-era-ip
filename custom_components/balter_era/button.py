"""Buttons for Balter ERA IP: door openers and image refresh."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BalterConfigEntry, async_refresh_image
from .const import DOMAIN
from .entity import BalterEntity
from .pybalter import DOOR_1, DOOR_2, BalterError, DeviceRecord

PARALLEL_UPDATES = 1

DOORS: tuple[tuple[str, int], ...] = (("door_1", DOOR_1), ("door_2", DOOR_2))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create door and refresh buttons per device."""
    entities: list[ButtonEntity] = []
    for device in entry.runtime_data.devices:
        entities.extend(BalterDoorButton(entry, device, key, door) for key, door in DOORS)
        entities.append(BalterRefreshImageButton(entry, device))
    async_add_entities(entities)


class BalterDoorButton(BalterEntity, ButtonEntity):
    """Triggers one door relay."""

    def __init__(
        self, entry: BalterConfigEntry, device: DeviceRecord, key: str, door: int
    ) -> None:
        super().__init__(entry, device, key)
        self._door = door
        self._attr_translation_key = key

    async def async_press(self) -> None:
        """Open the door."""
        try:
            await self._entry.runtime_data.client.async_open_door(self._device, self._door)
        except BalterError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="open_failed",
                translation_placeholders={"error": str(err)},
            ) from err


class BalterRefreshImageButton(BalterEntity, ButtonEntity):
    """Fetches a new still image (wakes up the outdoor station)."""

    _attr_translation_key = "refresh_image"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: BalterConfigEntry, device: DeviceRecord) -> None:
        super().__init__(entry, device, "refresh_image")

    async def async_press(self) -> None:
        """Take a snapshot."""
        try:
            await async_refresh_image(self.hass, self._entry, self._device)
        except BalterError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="image_failed",
                translation_placeholders={"error": str(err)},
            ) from err
