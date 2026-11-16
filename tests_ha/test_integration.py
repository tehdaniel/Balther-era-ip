"""Tests for config flow and button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.balter_era.const import DOMAIN
from custom_components.balter_era.pybalter import (
    DOOR_1,
    DOOR_2,
    BalterAuthError,
    BalterConnectionError,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

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
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="user@example.com")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_buttons_open_doors(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    states = sorted(s.entity_id for s in hass.states.async_all("button"))
    assert len(states) == 2

    for entity_id, door in zip(states, (DOOR_1, DOOR_2), strict=True):
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
        mock_client.async_open_door.assert_awaited_with(DEVICE, door)


async def test_button_error(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    await _setup(hass)
    mock_client.async_open_door.side_effect = BalterConnectionError("down")
    entity_id = sorted(s.entity_id for s in hass.states.async_all("button"))[0]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)


async def test_setup_auth_failed_starts_reauth(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    mock_client.async_get_devices.side_effect = BalterAuthError("no")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
