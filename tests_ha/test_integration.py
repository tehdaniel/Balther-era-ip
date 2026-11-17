"""Tests for config flow and entities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.balter_era import async_fire_ring
from custom_components.balter_era.const import CONF_FCM_CREDENTIALS, DOMAIN, signal_ring
from custom_components.balter_era.pybalter import (
    DOOR_1,
    DOOR_2,
    BalterAuthError,
    BalterConnectionError,
)
from homeassistant import config_entries
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.setup import async_setup_component

from .conftest import DEVICE

USER_INPUT = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"}


async def test_user_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Balter ERA (Home)"
    assert result["result"].unique_id == "user@example.com"


@pytest.mark.parametrize(
    ("error", "key"),
    [(BalterAuthError("no"), "invalid_auth"), (BalterConnectionError("down"), "cannot_connect")],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_client: AsyncMock, error: Exception, key: str
) -> None:
    mock_client.async_validate.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "ffmpeg", {})
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="user@example.com")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE.serial}_{key}")
    assert entity_id
    return entity_id


async def test_door_buttons(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    for key, door in (("door_1", DOOR_1), ("door_2", DOOR_2)):
        await hass.services.async_call(
            "button", "press", {"entity_id": _entity(hass, "button", key)}, blocking=True
        )
        mock_client.async_open_door.assert_awaited_with(DEVICE, door)


async def test_button_error(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    await _setup(hass)
    mock_client.async_open_door.side_effect = BalterConnectionError("down")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": _entity(hass, "button", "door_1")}, blocking=True
        )


async def test_setup_auth_failed_starts_reauth(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    mock_client.async_get_devices.side_effect = BalterAuthError("no")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_push_listener_started(
    hass: HomeAssistant, mock_client: AsyncMock, mock_push: MagicMock
) -> None:
    await _setup(hass)
    mock_push.return_value.async_start.assert_awaited_once()
    on_credentials = mock_push.call_args.kwargs["on_credentials"]
    on_credentials({"fcm": {"registration": {"token": "fcm-token"}}})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_FCM_CREDENTIALS]["fcm"]["registration"]["token"] == "fcm-token"


async def test_push_failure_does_not_break_setup(
    hass: HomeAssistant, mock_client: AsyncMock, mock_push: MagicMock
) -> None:
    mock_push.return_value.async_start.side_effect = RuntimeError("firebase down")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED


async def test_ring_fires_event_and_refreshes_image(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    entry = await _setup(hass)
    event_id = _entity(hass, "event", "doorbell")
    assert hass.states.get(event_id).state == STATE_UNKNOWN

    async_dispatcher_send(hass, signal_ring(entry.entry_id, DEVICE.serial), {"type": "call", "n": 1})
    await hass.async_block_till_done()

    state = hass.states.get(event_id)
    assert state.state != STATE_UNKNOWN
    assert state.attributes["event_type"] == "ring"
    assert state.attributes["type"] == "call"
    mock_client.async_snapshot.assert_awaited_once_with(DEVICE)
    assert entry.runtime_data.images[DEVICE.serial].startswith(b"\xff\xd8")  # JPEG


async def test_camera_image_cached(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    await _setup(hass)
    camera_id = _entity(hass, "camera", "camera")
    first = await async_get_image(hass, camera_id)
    second = await async_get_image(hass, camera_id)
    assert first.content.startswith(b"\xff\xd8")
    assert first.content == second.content
    # Only one snapshot: further requests must not wake the outdoor station.
    mock_client.async_snapshot.assert_awaited_once()


async def test_refresh_button(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "button", "press", {"entity_id": _entity(hass, "button", "refresh_image")}, blocking=True
    )
    mock_client.async_snapshot.assert_awaited_once_with(DEVICE)


async def test_mjpeg_live_view(
    hass: HomeAssistant, mock_client: AsyncMock, hass_client
) -> None:
    await _setup(hass)
    camera_id = _entity(hass, "camera", "camera")
    client = await hass_client()
    response = await client.get(f"/api/camera_proxy_stream/{camera_id}")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace")
    body = await response.content.read()
    assert b"--ffmpeg" in body
    assert b"\xff\xd8" in body
    mock_client.async_stream.assert_called_once_with(DEVICE)


async def test_ring_from_account_session(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """A call reported on the account session fires one ring per call."""
    from custom_components.balter_era.pybalter.protocol import DeviceStatus

    started = asyncio.Event()
    callbacks: dict = {}

    async def _watch(device_ids, on_status, **_kwargs):
        callbacks["on_status"] = on_status
        started.set()
        await asyncio.Event().wait()

    mock_client.async_watch_forever.side_effect = _watch
    entry = await _setup(hass)
    await asyncio.wait_for(started.wait(), 2)
    on_status = callbacks["on_status"]
    event_id = _entity(hass, "event", "doorbell")

    on_status(DeviceStatus(DEVICE.device_id, 6))  # online: nothing
    await hass.async_block_till_done()
    assert hass.states.get(event_id).state == STATE_UNKNOWN

    on_status(DeviceStatus(DEVICE.device_id, 1))  # call
    await hass.async_block_till_done()
    first = hass.states.get(event_id)
    assert first.attributes["source"] == "account"
    assert first.attributes["status"] == 1

    # Same call reported again, and the same call arriving as Firebase push:
    on_status(DeviceStatus(DEVICE.device_id, 1))
    async_fire_ring(hass, entry, DEVICE, {"source": "push"})
    await hass.async_block_till_done()
    assert hass.states.get(event_id) == first

    # Call ends, a later call rings again (after the dedupe window).
    on_status(DeviceStatus(DEVICE.device_id, 6))
    entry.runtime_data.last_ring.clear()
    on_status(DeviceStatus(DEVICE.device_id, 1))
    await hass.async_block_till_done()
    assert hass.states.get(event_id).state != first.state
