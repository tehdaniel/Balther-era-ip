"""Base entity for Balter ERA IP."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import BalterConfigEntry
from .const import DOMAIN, MANUFACTURER, MODEL
from .pybalter import DeviceRecord


class BalterEntity(Entity):
    """An entity belonging to one door system."""

    _attr_has_entity_name = True

    def __init__(self, entry: BalterConfigEntry, device: DeviceRecord, key: str) -> None:
        self._entry = entry
        self._device = device
        self._attr_unique_id = f"{device.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
            name=device.name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            serial_number=device.serial,
            sw_version=device.firmware,
        )
