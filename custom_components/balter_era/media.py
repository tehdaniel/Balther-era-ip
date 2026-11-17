"""Turn the camera's raw H.264 into JPEG, MJPEG or an MPEG-TS stream with ffmpeg."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

MJPEG_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=ffmpeg"

# The relay sends a raw Annex B elementary stream without timestamps, starting
# with a keyframe (SPS/PPS), so ffmpeg can start immediately with a tiny probe.
_H264_INPUT = (
    "-probesize", "32768", "-analyzeduration", "0",
    "-use_wallclock_as_timestamps", "1", "-f", "h264", "-i", "pipe:0",
)
MJPEG_OUTPUT = ("-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-q:v", "5", "-f", "mpjpeg", "pipe:1")
MPEGTS_OUTPUT = ("-c:v", "copy", "-an", "-f", "mpegts", "-muxdelay", "0", "pipe:1")


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


class FfmpegPipe:
    """Feeds an H.264 frame iterator into ffmpeg and exposes its output."""

    def __init__(
        self, ffmpeg: str, frames: AsyncIterator[bytes], output: tuple[str, ...]
    ) -> None:
        self._ffmpeg = ffmpeg
        self._frames = frames
        self._output = output
        self._proc: asyncio.subprocess.Process | None = None
        self._feeder: asyncio.Task[None] | None = None

    async def start(self) -> asyncio.StreamReader:
        """Start ffmpeg; returns its output."""
        self._proc = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-loglevel", "error", *_H264_INPUT, *self._output,
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
            _LOGGER.exception("Video stream from the door station ended")
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


class StreamServer:
    """Serves the camera as MPEG-TS on a loopback TCP port.

    Home Assistant's stream integration and go2rtc open ``tcp://127.0.0.1:<port>``;
    every connection starts its own relay session (and wakes the outdoor
    station) and ends it when the client disconnects.
    """

    def __init__(self, ffmpeg: str, open_frames: Callable[[], AsyncIterator[bytes]]) -> None:
        self._ffmpeg = ffmpeg
        self._open_frames = open_frames
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()

    @property
    def url(self) -> str | None:
        """Stream URL, or None while not started."""
        if self._server is None or not self._server.sockets:
            return None
        return f"tcp://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def start(self) -> None:
        """Listen on a free loopback port."""
        self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task
        self._clients.add(task)
        pipe = FfmpegPipe(self._ffmpeg, self._open_frames(), MPEGTS_OUTPUT)
        try:
            output = await pipe.start()
            while data := await output.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self._clients.discard(task)
            await pipe.stop()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def stop(self) -> None:
        """Close the port and all running streams."""
        if self._server:
            self._server.close()
        for task in list(self._clients):
            task.cancel()
        for task in list(self._clients):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._server:
            await self._server.wait_closed()
            self._server = None
