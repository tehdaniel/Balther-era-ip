"""Fixtures for Home Assistant integration tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.balter_era.pybalter import DeviceRecord

DEVICE = DeviceRecord(device_id=0x5678, serial="TEO00000TESTTEST0001", name="Home", firmware="5.0.0.80")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load integrations from custom_components."""


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Replace the cloud client everywhere."""
    with patch(
        "custom_components.balter_era.BalterClient", autospec=True
    ) as client_cls:
        client = client_cls.return_value
        client.async_get_devices.return_value = [DEVICE]
        client.async_validate.return_value = [DEVICE]
        yield client
