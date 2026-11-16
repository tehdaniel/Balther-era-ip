"""Asyncio client for the Balter ERA IP cloud."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Iterable

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
        try:
            while True:
                magic = await self._reader.readexactly(2)
                if magic == p.KEEPALIVE:
                    continue
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

    async def wait_for_media(self, timeout: float) -> bool:
        """Wait for the first media frame; False if none arrived in time."""

        async def _wait() -> None:
            while (await self.read()).msg_type != p.MSG_MEDIA:
                pass

        try:
            await asyncio.wait_for(_wait(), timeout)
        except TimeoutError:
            return False
        return True

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

    async def async_open_door(
        self, device: p.DeviceRecord, door: int, *, start_video: bool = True
    ) -> None:
        """Trigger a door relay of ``device``.

        Like the app, the video stream is started first and the command is
        only sent once the outdoor station delivers video, i.e. is awake.
        """
        async with self._lock, self._account() as (account, login):
            await account.send(p.build_connect_request(login, device.device_id))
            info = p.parse_connect_info(
                (await account.expect({p.ACC_CONNECT_INFO}, self._timeout)).body
            )
            _LOGGER.debug("relay %s:%d", info.relay_host, info.relay_port)
            view = await _Connection.open(info.relay_host, info.relay_port, self._timeout)
            try:
                ctx = p.ViewContext(
                    view_id=device.view_id,
                    user_id=login.user_id,
                    conn_id=secrets.randbits(16),
                )
                await self._view_login(view, ctx, info)
                if start_video:
                    await view.send(p.build_stream_start(ctx, p.STREAM_VIDEO))
                    loop = asyncio.get_running_loop()
                    started = loop.time()
                    if await view.wait_for_media(self._wake_timeout):
                        _LOGGER.debug(
                            "station awake, first video after %.1f s", loop.time() - started
                        )
                    else:
                        _LOGGER.warning(
                            "no video within %.0f s, sending door command anyway",
                            self._wake_timeout,
                        )
                await view.send(p.build_door_open(ctx, door))
                await view.expect({p.VIEW_DOOR_OPEN_ACK}, self._timeout)
                _LOGGER.debug("door %d acknowledged by relay", door)
                # Keep the session up briefly so the station can execute it.
                with contextlib.suppress(BalterError, TimeoutError):
                    await asyncio.wait_for(self._drain(view), self._hold_time)
                await view.send(p.build_stream_stop(ctx))
            finally:
                await view.close()

    @staticmethod
    async def _drain(view: _Connection) -> None:
        """Consume incoming frames until cancelled."""
        while True:
            msg = await view.read()
            if msg.msg_type == p.MSG_CONTROL:
                _LOGGER.debug("after door command: cmd 0x%04x %s", msg.cmd, msg.body[:24].hex())

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
