"""The Balter ERA IP door intercom integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_FCM_CREDENTIALS, signal_image, signal_ring
from .media import h264_to_jpeg
from .pybalter import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    BalterAuthError,
    BalterClient,
    BalterError,
    DeviceRecord,
)
from .pybalter.protocol import DeviceStatus

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.CAMERA, Platform.EVENT]


@dataclass
class BalterData:
    """Runtime data of one config entry."""

    client: BalterClient
    devices: list[DeviceRecord]
    images: dict[str, bytes] = field(default_factory=dict)
    push: Any = None
    refreshing: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    in_call: set[int] = field(default_factory=set)
    last_ring: dict[str, float] = field(default_factory=dict)


RING_DEDUPE_SECONDS = 30.0


@callback
def async_fire_ring(
    hass: HomeAssistant,
    entry: BalterConfigEntry,
    device: DeviceRecord,
    payload: dict[str, Any],
) -> None:
    """Fire the ring signal once, even if both channels report the same call."""
    data = entry.runtime_data
    now = hass.loop.time()
    if now - data.last_ring.get(device.serial, -RING_DEDUPE_SECONDS) < RING_DEDUPE_SECONDS:
        _LOGGER.debug("Ignoring duplicate ring report: %s", payload)
        return
    data.last_ring[device.serial] = now
    async_dispatcher_send(hass, signal_ring(entry.entry_id, device.serial), payload)


type BalterConfigEntry = ConfigEntry[BalterData]


def create_client(data: dict) -> BalterClient:
    """Build a client from config entry data."""
    return BalterClient(
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        host=data.get(CONF_HOST, DEFAULT_HOST),
        port=data.get(CONF_PORT, DEFAULT_PORT),
    )


async def async_refresh_image(
    hass: HomeAssistant, entry: BalterConfigEntry, device: DeviceRecord
) -> None:
    """Fetch a new still image; concurrent requests share one fetch."""
    data = entry.runtime_data
    task = data.refreshing.get(device.serial)
    if task is None or task.done():

        async def _fetch() -> None:
            keyframe = await data.client.async_snapshot(device)
            data.images[device.serial] = await h264_to_jpeg(
                get_ffmpeg_manager(hass).binary, keyframe
            )
            async_dispatcher_send(hass, signal_image(entry.entry_id, device.serial))

        task = hass.async_create_task(_fetch(), eager_start=False)
        data.refreshing[device.serial] = task
    await asyncio.shield(task)


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
    entry.async_create_background_task(
        hass, _async_start_push(hass, entry), f"{entry.title} doorbell push"
    )
    entry.async_create_background_task(
        hass, _async_watch(hass, entry), f"{entry.title} account session"
    )
    return True


async def _async_watch(hass: HomeAssistant, entry: BalterConfigEntry) -> None:
    """Keep an account session open; the server reports calls on it."""
    data = entry.runtime_data
    by_id = {device.device_id: device for device in data.devices}

    @callback
    def _on_status(status: DeviceStatus) -> None:
        device = by_id.get(status.device_id)
        if device is None:
            return
        if not status.is_alarm:
            data.in_call.discard(status.device_id)
            return
        if status.device_id in data.in_call:
            return  # same call reported again
        data.in_call.add(status.device_id)
        async_fire_ring(hass, entry, device, {"source": "account", "status": status.status})

    try:
        await data.client.async_watch_forever(list(by_id), _on_status)
    except BalterAuthError:
        entry.async_start_reauth(hass)


async def _async_start_push(hass: HomeAssistant, entry: BalterConfigEntry) -> None:
    """Start receiving doorbell notifications; failures only disable rings."""
    from .pybalter.push import BalterPushListener  # noqa: PLC0415

    data = entry.runtime_data

    @callback
    def _store_credentials(credentials: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_FCM_CREDENTIALS: credentials}
        )

    @callback
    def _on_push(payload: dict[str, Any]) -> None:
        _LOGGER.debug("Doorbell push: %s", payload)
        for device in data.devices:
            async_fire_ring(hass, entry, device, {"source": "push", **payload})

    listener = BalterPushListener(
        data.client,
        _on_push,
        credentials=entry.data.get(CONF_FCM_CREDENTIALS),
        on_credentials=lambda creds: hass.loop.call_soon_threadsafe(
            _store_credentials, creds
        ),
        http_session=async_get_clientsession(hass),
    )
    try:
        await listener.async_start()
    except Exception:  # noqa: BLE001 - optional feature, never break setup
        _LOGGER.warning(
            "Doorbell notifications are not available", exc_info=True
        )
        return
    data.push = listener


async def async_unload_entry(hass: HomeAssistant, entry: BalterConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.runtime_data.push:
        await entry.runtime_data.push.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BalterConfigEntry) -> None:
    """Remove our push token from the Balter account."""
    credentials = entry.data.get(CONF_FCM_CREDENTIALS) or {}
    token = credentials.get("fcm", {}).get("registration", {}).get("token")
    if not token:
        return
    try:
        await create_client(dict(entry.data)).async_set_push_token(token, register=False)
    except BalterError as err:
        _LOGGER.debug("Could not remove push token: %s", err)
