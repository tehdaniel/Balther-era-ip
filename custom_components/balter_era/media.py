"""Turn the camera's raw H.264 into JPEG / MJPEG with ffmpeg."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

MJPEG_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=ffmpeg"


async def h264_to_jpeg(ffmpeg: str, data: bytes) -> bytes:
    """Decode one H.264 keyframe to JPEG."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-loglevel", "error", "-f", "h264", "-i", "pipe:0",
        "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    jpeg, err = await proc.communicate(data)
    if proc.returncode or not jpeg:
        raise HomeAssistantError(f"ffmpeg could not decode the image: {err.decode(errors='replace')}")
    return jpeg


class MjpegTranscoder:
    """Pipes an H.264 frame iterator through ffmpeg into an MJPEG stream."""

    def __init__(self, ffmpeg: str, frames: AsyncIterator[bytes]) -> None:
        self._ffmpeg = ffmpeg
        self._frames = frames
        self._proc: asyncio.subprocess.Process | None = None
        self._feeder: asyncio.Task[None] | None = None

    async def start(self) -> asyncio.StreamReader:
        """Start ffmpeg; returns its MJPEG output."""
        self._proc = await asyncio.create_subprocess_exec(
            # Small probe size: the first frame is a keyframe with SPS/PPS,
            # so ffmpeg can start right away instead of buffering seconds.
            self._ffmpeg, "-loglevel", "error",
            "-probesize", "32768", "-analyzeduration", "0",
            "-f", "h264", "-i", "pipe:0",
            "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-q:v", "5",
            "-f", "mpjpeg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._feeder = asyncio.get_running_loop().create_task(self._feed())
        assert self._proc.stdout
        return self._proc.stdout

    async def _feed(self) -> None:
        assert self._proc and self._proc.stdin
        try:
            async for frame in self._frames:
                self._proc.stdin.write(frame)
                await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            _LOGGER.exception("Live stream from the door station ended")
        finally:
            with contextlib.suppress(Exception):
                self._proc.stdin.close()

    async def stop(self) -> None:
        """Stop feeding and terminate ffmpeg."""
        if self._feeder:
            self._feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feeder
        with contextlib.suppress(Exception):
            await self._frames.aclose()  # type: ignore[attr-defined]
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
