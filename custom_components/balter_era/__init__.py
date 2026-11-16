"""The Balter ERA IP door intercom integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .pybalter import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    BalterAuthError,
    BalterClient,
    BalterError,
    DeviceRecord,
)

PLATFORMS: list[Platform] = [Platform.BUTTON]


@dataclass
class BalterData:
    """Runtime data of one config entry."""

    client: BalterClient
    devices: list[DeviceRecord]


type BalterConfigEntry = ConfigEntry[BalterData]


def create_client(data: dict) -> BalterClient:
    """Build a client from config entry data."""
    return BalterClient(
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        host=data.get(CONF_HOST, DEFAULT_HOST),
        port=data.get(CONF_PORT, DEFAULT_PORT),
    )


async def async_setup_entry(hass: HomeAssistant, entry: BalterConfigEntry) -> bool:
    """Set up Balter ERA IP from a config entry."""
    client = create_client(dict(entry.data))
    try:
        devices = await client.async_get_devices()
    except BalterAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BalterError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = BalterData(client=client, devices=devices)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BalterConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
