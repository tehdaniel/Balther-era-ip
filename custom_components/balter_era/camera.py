"""Camera of the Balter ERA IP outdoor station.

Every access powers up the outdoor station, so still images are cached and
only refreshed on a ring, via the refresh button, or once when none exists
yet.  Opening the camera in the UI starts an MJPEG live view.
"""

from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BalterConfigEntry, async_refresh_image
from .const import signal_image
from .entity import BalterEntity
from .media import MJPEG_CONTENT_TYPE, MjpegTranscoder
from .pybalter import BalterError, DeviceRecord

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one camera per device."""
    async_add_entities(BalterCamera(entry, device) for device in entry.runtime_data.devices)


class BalterCamera(BalterEntity, Camera):
    """Outdoor station camera."""

    _attr_name = None

    def __init__(self, entry: BalterConfigEntry, device: DeviceRecord) -> None:
        BalterEntity.__init__(self, entry, device, "camera")
        Camera.__init__(self)
        self._initial_fetch_done = False

    async def async_added_to_hass(self) -> None:
        """Update the picture when a new image arrives."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_image(self._entry.entry_id, self._device.serial),
                self._on_new_image,
            )
        )

    @callback
    def _on_new_image(self) -> None:
        self.async_update_token()
        self.async_write_ha_state()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the cached still image (fetch once if there is none)."""
        images = self._entry.runtime_data.images
        if self._device.serial not in images and not self._initial_fetch_done:
            self._initial_fetch_done = True
            try:
                await async_refresh_image(self.hass, self._entry, self._device)
            except BalterError as err:
                _LOGGER.warning("Could not fetch camera image: %s", err)
        return images.get(self._device.serial)

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Live view: H.264 from the relay, transcoded to MJPEG by ffmpeg."""
        transcoder = MjpegTranscoder(
            get_ffmpeg_manager(self.hass).binary,
            self._entry.runtime_data.client.async_stream(self._device),
        )
        stream = await transcoder.start()
        try:
            return await async_aiohttp_proxy_stream(
                self.hass, request, stream, MJPEG_CONTENT_TYPE
            )
        finally:
            await transcoder.stop()
