"""Asyncio client for the Balter ERA IP cloud."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Callable, Iterable

from . import protocol as p
from .exceptions import (
    BalterAuthError,
    BalterConnectionError,
    BalterError,
    BalterProtocolError,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "159.69.151.111"
DEFAULT_PORT = 3478
DEFAULT_TIMEOUT = 10.0
# The outdoor station sits on a 2-wire bus and is powered up when a stream
# starts; the first video frame arrives ~3.5 s later.  Door commands sent
# before that are acknowledged by the relay but ignored by the station.
DEFAULT_WAKE_TIMEOUT = 15.0
DEFAULT_HOLD_TIME = 2.0
KEEPALIVE_INTERVAL = 10.0


def _media(msg: p.Message) -> p.MediaFrame | None:
    """Decode a media message; malformed frames are skipped."""
    if msg.msg_type != p.MSG_MEDIA:
        return None
    try:
        return p.parse_media(msg.body)
    except BalterProtocolError:
        _LOGGER.debug("skipping malformed media frame (%d bytes)", len(msg.body))
        return None


class _Connection:
    """One framed TCP connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def open(cls, host: str, port: int, timeout: float) -> _Connection:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        except (OSError, TimeoutError) as err:
            raise BalterConnectionError(f"cannot connect to {host}:{port}: {err}") from err
        return cls(reader, writer)

    async def send(self, data: bytes) -> None:
        try:
            self._writer.write(data)
            await self._writer.drain()
        except OSError as err:
            raise BalterConnectionError(f"send failed: {err}") from err

    async def read(self) -> p.Message:
        """Read the next frame, skipping keepalives."""
        while (msg := await self.read_frame()) is None:
            pass
        return msg

    async def read_frame(self) -> p.Message | None:
        """Read the next frame; None for a keepalive."""
        try:
            magic = await self._reader.readexactly(2)
            if magic == p.KEEPALIVE:
                return None
            if magic != p.FRAME_MAGIC:
                raise BalterProtocolError(f"unexpected frame start {magic.hex()}")
            length = int.from_bytes(await self._reader.readexactly(2), "big")
            return p.parse_payload(await self._reader.readexactly(length))
        except asyncio.IncompleteReadError as err:
            raise BalterConnectionError("connection closed by server") from err
        except OSError as err:
            raise BalterConnectionError(f"receive failed: {err}") from err

    async def expect(self, cmds: Iterable[int], timeout: float) -> p.Message:
        """Wait for a control message with one of ``cmds``; drop everything else."""
        wanted = set(cmds)

        async def _wait() -> p.Message:
            while True:
                msg = await self.read()
                if msg.msg_type == p.MSG_CONTROL and msg.cmd in wanted:
                    return msg
                if msg.msg_type == p.MSG_CONTROL:
                    _LOGGER.debug("ignoring cmd 0x%04x (%d bytes)", msg.cmd, len(msg.body))

        try:
            return await asyncio.wait_for(_wait(), timeout)
        except TimeoutError as err:
            names = ", ".join(f"0x{cmd:04x}" for cmd in sorted(wanted))
            raise BalterProtocolError(f"timeout waiting for {names}") from err

    async def next_media(
        self, timeout: float, accept: Callable[[p.MediaFrame], bool]
    ) -> p.MediaFrame | None:
        """Return the first media frame accepted by ``accept``, None on timeout."""

        async def _wait() -> p.MediaFrame:
            while True:
                frame = _media(await self.read())
                if frame and accept(frame):
                    return frame

        try:
            return await asyncio.wait_for(_wait(), timeout)
        except TimeoutError:
            return None

    async def log_incoming(self, duration: float) -> None:
        """Log control messages for a while (protocol exploration)."""

        async def _drain() -> None:
            while True:
                msg = await self.read()
                if msg.msg_type == p.MSG_CONTROL:
                    _LOGGER.debug("received cmd 0x%04x %s", msg.cmd, msg.body[:32].hex())

        with contextlib.suppress(BalterError, TimeoutError):
            await asyncio.wait_for(_drain(), duration)

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()


class BalterClient:
    """Talks to the Balter account server and its media relay."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        wake_timeout: float = DEFAULT_WAKE_TIMEOUT,
        hold_time: float = DEFAULT_HOLD_TIME,
    ) -> None:
        self._username = username
        self._password = password
        self._host = host
        self._port = port
        self._timeout = timeout
        self._wake_timeout = wake_timeout
        self._hold_time = hold_time
        self._user_id = 0
        self._lock = asyncio.Lock()

    # --- account channel ---------------------------------------------------

    @contextlib.asynccontextmanager
    async def _account(self) -> AsyncIterator[tuple[_Connection, p.LoginResult]]:
        """Open the account channel and log in."""
        conn = await _Connection.open(self._host, self._port, self._timeout)
        try:
            await conn.send(p.build_challenge_request())
            challenge = p.parse_challenge(
                (await conn.expect({p.ACC_CHALLENGE}, self._timeout)).body
            )
            await conn.send(
                p.build_login(
                    self._username,
                    self._password,
                    challenge,
                    known_user_id=self._user_id,
                )
            )
            try:
                reply = await conn.expect({p.ACC_LOGIN_REPLY}, self._timeout)
                login = p.parse_login_reply(reply.body)
            except BalterError as err:
                raise BalterAuthError(f"login rejected: {err}") from err
            if login.user_id == 0:
                raise BalterAuthError("login rejected")
            self._user_id = login.user_id
            _LOGGER.debug("logged in, session 0x%x", login.session_id)
            yield conn, login
        finally:
            await conn.close()

    async def async_validate(self) -> list[p.DeviceRecord]:
        """Log in and return the devices; raises on bad credentials."""
        return await self.async_get_devices()

    async def async_get_devices(self) -> list[p.DeviceRecord]:
        """Return all door systems bound to the account."""
        async with self._lock, self._account() as (conn, login):
            await conn.send(p.build_device_list_request(login.user_id))
            reply = await conn.expect({p.ACC_DEVICE_LIST}, self._timeout)
            return p.parse_device_list(reply.body)

    async def async_set_push_token(self, token: str, *, register: bool = True) -> None:
        """Register (or remove) an FCM token for doorbell notifications."""
        operation = p.PUSH_FORCE_ADD if register else p.PUSH_DELETE
        async with self._lock, self._account() as (conn, login):
            await conn.send(p.build_set_push(login.user_id, operation, token))
            # The reply format is not known yet; log what comes back.
            await conn.log_incoming(2.0)
        _LOGGER.debug("push token %s", "registered" if register else "removed")

    async def async_watch(
        self,
        device_ids: list[int],
        on_status: Callable[[p.DeviceStatus], None],
        *,
        on_message: Callable[[p.Message], None] | None = None,
    ) -> None:
        """Hold an account session open and report device status pushes.

        Like the app, the server pushes ``ACC_STATUS`` while a session is
        open; any status other than online/offline is a call (doorbell).
        Returns only by raising (connection lost) or when cancelled.
        """
        async with self._account() as (conn, login):
            await conn.send(p.build_status_request(login, device_ids))
            pinger = asyncio.get_running_loop().create_task(self._keepalive(conn))
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            conn.read_frame(), 3 * KEEPALIVE_INTERVAL
                        )
                    except TimeoutError as err:
                        raise BalterConnectionError("account session went silent") from err
                    if msg is None or msg.msg_type != p.MSG_CONTROL:
                        continue
                    _LOGGER.debug("account push cmd 0x%04x %s", msg.cmd, msg.body.hex())
                    if on_message:
                        on_message(msg)
                    if msg.cmd == p.ACC_STATUS:
                        await conn.send(p.build_status_ack())
                        for status in p.parse_status(msg.body):
                            on_status(status)
            finally:
                pinger.cancel()
                with contextlib.suppress(asyncio.CancelledError, BalterError):
                    await pinger

    async def async_watch_forever(
        self,
        device_ids: list[int],
        on_status: Callable[[p.DeviceStatus], None],
        *,
        on_message: Callable[[p.Message], None] | None = None,
        max_delay: float = 300.0,
    ) -> None:
        """``async_watch`` with reconnects; stops only on auth errors or cancel."""
        delay = 5.0
        while True:
            loop = asyncio.get_running_loop()
            started = loop.time()
            try:
                await self.async_watch(device_ids, on_status, on_message=on_message)
            except BalterAuthError:
                raise
            except BalterError as err:
                if loop.time() - started > 60:
                    delay = 5.0
                _LOGGER.debug("account session lost (%s), reconnecting in %.0f s", err, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

    # --- view session ------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _view(
        self, device: p.DeviceRecord, *, keepalive: bool = False
    ) -> AsyncIterator[tuple[_Connection, p.ViewContext]]:
        """Log in, ask for relay tokens and open an authenticated view session."""
        async with self._account() as (account, login):
            await account.send(p.build_connect_request(login, device.device_id))
            info = p.parse_connect_info(
                (await account.expect({p.ACC_CONNECT_INFO}, self._timeout)).body
            )
            _LOGGER.debug("relay %s:%d", info.relay_host, info.relay_port)
            view = await _Connection.open(info.relay_host, info.relay_port, self._timeout)
            pinger = (
                asyncio.get_running_loop().create_task(self._keepalive(account))
                if keepalive
                else None
            )
            try:
                ctx = p.ViewContext(
                    view_id=device.view_id,
                    user_id=login.user_id,
                    conn_id=secrets.randbits(16),
                )
                await self._view_login(view, ctx, info)
                yield view, ctx
            finally:
                if pinger:
                    pinger.cancel()
                    with contextlib.suppress(asyncio.CancelledError, BalterError):
                        await pinger
                await view.close()

    @staticmethod
    async def _keepalive(account: _Connection) -> None:
        """Keep the account session alive during long streams, like the app."""
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            await account.send(p.KEEPALIVE)

    async def _view_login(
        self, view: _Connection, ctx: p.ViewContext, info: p.ConnectInfo
    ) -> None:
        await view.send(p.build_view_login(ctx, info.token))
        challenge = p.parse_challenge(
            (await view.expect({p.VIEW_CHALLENGE}, self._timeout)).body
        )
        await view.send(p.build_view_auth(ctx, info.token, info.secret, challenge))
        try:
            reply = await view.expect({p.VIEW_AUTH_OK}, self._timeout)
        except BalterError as err:
            raise BalterAuthError(f"relay rejected session: {err}") from err
        ctx.session_id = p.parse_view_session_id(reply.body)

    async def _start_video(self, view: _Connection, ctx: p.ViewContext) -> p.MediaFrame | None:
        """Start video and wait until the outdoor station delivers a frame."""
        await view.send(p.build_stream_start(ctx, p.STREAM_VIDEO))
        loop = asyncio.get_running_loop()
        started = loop.time()
        frame = await view.next_media(self._wake_timeout, lambda f: f.is_video)
        if frame:
            _LOGGER.debug("station awake, first video after %.1f s", loop.time() - started)
        else:
            _LOGGER.warning("no video from the station within %.0f s", self._wake_timeout)
        return frame

    async def async_open_door(
        self, device: p.DeviceRecord, door: int, *, start_video: bool = True
    ) -> None:
        """Trigger a door relay of ``device``.

        Like the app, the video stream is started first and the command is
        only sent once the outdoor station delivers video, i.e. is awake.
        """
        async with self._lock, self._view(device) as (view, ctx):
            if start_video:
                await self._start_video(view, ctx)
            await view.send(p.build_door_open(ctx, door))
            await view.expect({p.VIEW_DOOR_OPEN_ACK}, self._timeout)
            _LOGGER.debug("door %d acknowledged by relay", door)
            # Keep the session up briefly so the station can execute it.
            await view.log_incoming(self._hold_time)
            await view.send(p.build_stream_stop(ctx))

    async def async_snapshot(self, device: p.DeviceRecord) -> bytes:
        """Return one H.264 keyframe (SPS, PPS, IDR; Annex B) from the camera."""
        async with self._lock, self._view(device) as (view, ctx):
            first = await self._start_video(view, ctx)
            frame = first if first and first.is_keyframe else None
            if frame is None:
                frame = await view.next_media(self._wake_timeout, lambda f: f.is_keyframe)
            with contextlib.suppress(BalterError):
                await view.send(p.build_stream_stop(ctx))
        if frame is None:
            raise BalterProtocolError("no keyframe received from the camera")
        return frame.data

    async def async_stream(self, device: p.DeviceRecord) -> AsyncIterator[bytes]:
        """Yield H.264 video frames (Annex B) until the consumer stops iterating."""
        async with self._view(device, keepalive=True) as (view, ctx):
            await view.send(p.build_stream_start(ctx, p.STREAM_VIDEO))
            try:
                while True:
                    frame = _media(await view.read())
                    if frame and frame.is_video:
                        yield frame.data
            finally:
                with contextlib.suppress(BalterError):
                    await view.send(p.build_stream_stop(ctx))
