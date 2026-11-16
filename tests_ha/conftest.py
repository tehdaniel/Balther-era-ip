"""Fixtures for Home Assistant integration tests."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.balter_era.pybalter import DeviceRecord

DEVICE = DeviceRecord(device_id=0x5678, serial="TEO00000TESTTEST0001", name="Home", firmware="5.0.0.80")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load integrations from custom_components."""


@pytest.fixture(scope="session")
def h264_frames() -> list[bytes]:
    """A short synthetic H.264 Annex B stream, split into access units."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-frames:v", "10", "-c:v", "libx264", "-bsf:v", "h264_mp4toannexb",
         "-x264-params", "keyint=5", "-f", "h264", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    start = b"\x00\x00\x00\x01"
    frames, current = [], b""
    for nal in raw.split(start)[1:]:
        current += start + nal
        if nal[0] & 0x1F in (1, 5):  # slice ends an access unit
            frames.append(current)
            current = b""
    return frames


@pytest.fixture
def mock_push() -> Generator[MagicMock]:
    """Replace the Firebase listener."""
    with patch(
        "custom_components.balter_era.pybalter.push.BalterPushListener", autospec=True
    ) as listener_cls:
        listener_cls.return_value.async_start.return_value = "fcm-token"
        yield listener_cls


@pytest.fixture
def mock_client(h264_frames: list[bytes], mock_push: MagicMock) -> Generator[AsyncMock]:
    """Replace the cloud client everywhere."""
    with patch(
        "custom_components.balter_era.BalterClient", autospec=True
    ) as client_cls:
        client = client_cls.return_value
        client.async_get_devices.return_value = [DEVICE]
        client.async_validate.return_value = [DEVICE]
        client.async_snapshot.return_value = h264_frames[0]

        async def _stream(_device: DeviceRecord) -> AsyncIterator[bytes]:
            for frame in h264_frames:
                yield frame

        client.async_stream = MagicMock(side_effect=_stream)
        yield client
