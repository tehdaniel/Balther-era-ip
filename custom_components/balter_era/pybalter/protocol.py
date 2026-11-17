"""Wire format of the Balter ERA IP cloud protocol ("DongSDK").

Reverse engineered from the Balter ERA IP Android app (libMobClientSDK.so)
and packet captures; see docs/PROTOCOL.md for the full description.

Every message is framed as ``02 02 <u16 length> <payload>``.  Control
payloads start with a fixed 45 byte header (type, class, command, routing
fields) followed by a command specific body.  All integers are big endian.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import struct
from dataclasses import dataclass

from .exceptions import BalterProtocolError

FRAME_MAGIC = b"\x02\x02"
KEEPALIVE = b"\x02\x01"
HEADER_LEN = 45

MSG_MEDIA = 0x01
MSG_CONTROL = 0x02
CLASS_ACCOUNT = 0xD6
CLASS_VIEW = 0xCA

# Account channel (TCP, port 3478 on the Balter server).
ACC_DEVICE_LIST_REQUEST = 0x000D
ACC_DEVICE_LIST = 0x000E
ACC_LOGIN = 0x0020
ACC_LOGIN_REPLY = 0x0021
ACC_STATUS_REQUEST = 0x0025
ACC_STATUS = 0x0026  # also pushed by the server; must be acknowledged
ACC_STATUS_ACK = 0x0103
ACC_CONNECT_REQUEST = 0x0027
ACC_CONNECT_INFO = 0x0028
ACC_CHALLENGE_REQUEST = 0x0030
ACC_CHALLENGE = 0x0031
ACC_SET_PUSH = 0x004D

# Push registration (InfoPush constants of the SDK).
PUSH_DELETE = 0
PUSH_FORCE_ADD = 1
PUSH_OS_GOOGLE = 13
PUSH_LANGUAGE_ENGLISH = 2

# Media frames: 37 byte header, then the payload.
MEDIA_HEADER_LEN = 37
MEDIA_VIDEO_KEY = 0x91
MEDIA_AUDIO = 0x21
H264_START = b"\x00\x00\x00\x01"

# View session (TCP, port 5439 on the relay named in ACC_CONNECT_INFO).
VIEW_LOGIN = 0x0001
VIEW_CHALLENGE = 0x0002
VIEW_AUTH = 0x0003
VIEW_AUTH_OK = 0x0004
VIEW_STREAM_START = 0x0011
VIEW_STREAM_STOP = 0x0012
VIEW_STATUS = 0x0021
VIEW_STREAM_STOPPED = 0x0022
VIEW_DOOR_OPEN = 0x007A
VIEW_DOOR_OPEN_ACK = 0x008A

STREAM_VIDEO = 0x16
STREAM_AUDIO = 0x14
STREAM_TALK = 0x02

# Door relay indices as used by the app (buttons "lock 1" and "lock 2").
DOOR_1 = 1
DOOR_2 = 101

# Constants copied verbatim from captured app traffic.
_VIEW_CONN_TYPE = 0x17
_DEVICE_CLASS = 0x12
_CLIENT_TYPE = 0x03000001
_PUSH_OPR = 2
_ROUTE = b"\x00\x01\x00\x01"

_U32 = struct.Struct(">I")


@dataclass(frozen=True)
class Message:
    """One decoded frame."""

    msg_type: int
    msg_class: int
    cmd: int
    header: bytes
    body: bytes


@dataclass(frozen=True)
class LoginResult:
    """Identifiers handed out by the account server after login."""

    user_id: int
    session_id: int


@dataclass(frozen=True)
class DeviceRecord:
    """A door system as listed by the account server."""

    device_id: int
    serial: str
    name: str
    firmware: str | None

    @property
    def view_id(self) -> int:
        """Identifier used to address the device on the relay."""
        return serial_to_view_id(self.serial)


@dataclass(frozen=True)
class ConnectInfo:
    """Where and how to open a view session for one device."""

    relay_host: str
    relay_port: int
    token: bytes
    secret: bytes


def u32(value: int) -> bytes:
    """Encode an unsigned 32 bit big endian integer."""
    return _U32.pack(value & 0xFFFFFFFF)


def read_u32(data: bytes, offset: int) -> int:
    """Decode an unsigned 32 bit big endian integer."""
    return _U32.unpack_from(data, offset)[0]


def varstr(value: bytes) -> bytes:
    """Encode a length prefixed string (one length byte, no terminator)."""
    if len(value) > 255:
        raise ValueError("string too long")
    return bytes([len(value)]) + value


def pad(value: bytes, size: int) -> bytes:
    """Zero pad (or truncate) to a fixed size field."""
    return value[:size].ljust(size, b"\x00")


def frame(payload: bytes) -> bytes:
    """Wrap a payload into a ``02 02 <len>`` frame."""
    return FRAME_MAGIC + struct.pack(">H", len(payload)) + payload


def parse_payload(payload: bytes) -> Message:
    """Decode the payload of one frame."""
    if len(payload) < 2:
        raise BalterProtocolError("frame too short")
    if payload[0] != MSG_CONTROL:
        return Message(payload[0], payload[1], 0, b"", payload)
    if len(payload) < HEADER_LEN:
        raise BalterProtocolError("control header too short")
    return Message(
        MSG_CONTROL,
        payload[1],
        int.from_bytes(payload[2:4], "big"),
        payload[:HEADER_LEN],
        payload[HEADER_LEN:],
    )


def account_header(cmd: int) -> bytes:
    """Header of a client to server message on the account channel."""
    header = bytearray(HEADER_LEN)
    header[0] = MSG_CONTROL
    header[1] = CLASS_ACCOUNT
    header[2:4] = cmd.to_bytes(2, "big")
    header[21:25] = _ROUTE
    header[42] = 0x01
    return bytes(header)


def view_header(
    cmd: int, *, session_id: int, view_id: int, conn_id: int, user_id: int
) -> bytes:
    """Header of a client to relay message in a view session."""
    header = bytearray(HEADER_LEN)
    header[0] = MSG_CONTROL
    header[1] = CLASS_VIEW
    header[2:4] = cmd.to_bytes(2, "big")
    header[4:8] = b"\x04\x00\x02\x00"
    header[9:13] = u32(session_id)
    header[13:17] = u32(view_id)
    header[21:25] = _ROUTE
    header[27:29] = (conn_id & 0xFFFF).to_bytes(2, "big")
    header[29:33] = u32(user_id)
    return bytes(header)


def _md5(data: bytes) -> bytes:
    return hashlib.md5(data).digest()  # noqa: S324 - mandated by the protocol


def trim_username(username: str) -> bytes:
    """The app silently truncates user names to 31 characters."""
    return username.encode()[:31]


def account_digest(username: str, password: str, challenge: bytes) -> bytes:
    """Response to the account challenge: MD5(user + MD5(password) + challenge)."""
    return _md5(trim_username(username) + _md5(password.encode()) + challenge[:16])


def view_digest(token: bytes, secret: bytes, challenge: bytes) -> bytes:
    """Response to the view challenge.

    MD5(MD5(pad32(token)) + MD5(pad32(secret)) + challenge), with the two
    session tokens from ``ACC_CONNECT_INFO``.
    """
    return _md5(_md5(pad(token, 32)) + _md5(pad(secret, 32)) + challenge[:16])


def _serial_char_value(char: str) -> int:
    code = ord(char)
    if 48 <= code <= 57:
        return code - 48
    if 65 <= code <= 90:
        return code - 55
    if 97 <= code <= 122:
        return code - 87
    return 0


_SERIAL_DIGITS = ((2, 12), (5, 34), (3, 10), (6, 21), (7, 30), (4, 25), (1, 4))
_VIEW_ID_LIMIT = 0x071C71C7  # (2**32 - 1) // 36


def serial_to_view_id(serial: str) -> int:
    """Port of ``CamSNToID`` from the native SDK (base 36 with offsets)."""
    if len(serial) < 8:
        raise ValueError("serial number too short")
    digits = [(_serial_char_value(serial[i]) + off) % 36 for i, off in _SERIAL_DIGITS]
    result = 0
    for digit in digits[:-1]:
        result = result * 36 + digit
    # Same overflow guard as the native code: the result must fit in 32 bits.
    if result > _VIEW_ID_LIMIT or (result == _VIEW_ID_LIMIT and digits[-1] > 3):
        return 0
    return result * 36 + digits[-1]


# --- account channel -------------------------------------------------------


def build_challenge_request() -> bytes:
    """Ask the account server for a login challenge."""
    return frame(account_header(ACC_CHALLENGE_REQUEST))


def build_login(
    username: str, password: str, challenge: bytes, *, known_user_id: int = 0
) -> bytes:
    """Login message (layout of ``CDServerSend::SendCmd_Login``)."""
    body = (
        varstr(trim_username(username))
        + account_digest(username, password, challenge)
        + u32(0)
        + u32(0)
        + bytes([_PUSH_OPR])
        + u32(known_user_id)
        + u32(_CLIENT_TYPE)
        + b"\x00"  # push OS
        + b"\x00"  # language
        + varstr(b"")  # push token
        + u32(1)
        + b"\x00"
    )
    return frame(account_header(ACC_LOGIN) + body)


def build_device_list_request(user_id: int) -> bytes:
    """Ask for the devices bound to the account."""
    return frame(account_header(ACC_DEVICE_LIST_REQUEST) + u32(user_id))


def build_connect_request(login: LoginResult, device_id: int) -> bytes:
    """Ask for relay address and session tokens of one device."""
    body = (
        u32(0)
        + b"\x01"
        + u32(login.user_id)
        + u32(login.session_id)
        + u32(device_id)
        + bytes(19)
    )
    return frame(account_header(ACC_CONNECT_REQUEST) + body)


STATUS_ONLINE = 6
STATUS_OFFLINE = 7


@dataclass(frozen=True)
class DeviceStatus:
    """One entry of an ``ACC_STATUS`` message."""

    device_id: int
    status: int

    @property
    def is_alarm(self) -> bool:
        """The SDK reports every status except online/offline as an alarm (call)."""
        return self.status not in (STATUS_ONLINE, STATUS_OFFLINE)


def build_status_request(login: LoginResult, device_ids: list[int]) -> bytes:
    """Ask for the status of devices (the app does this after login)."""
    body = u32(login.user_id) + u32(login.session_id) + u32(len(device_ids))
    body += b"".join(u32(device_id) for device_id in device_ids)
    return frame(account_header(ACC_STATUS_REQUEST) + body)


def build_status_ack() -> bytes:
    """Acknowledge an ``ACC_STATUS`` message (``SendCmd_GetDeviceStatusRepACK``)."""
    return frame(account_header(ACC_STATUS_ACK))


def parse_status(body: bytes) -> list[DeviceStatus]:
    """Parse ``user_id, session, count, count x (device_id, status), ...``."""
    if len(body) < 12:
        raise BalterProtocolError("status message too short")
    count = read_u32(body, 8)
    if len(body) < 12 + count * 8:
        raise BalterProtocolError("status message truncated")
    return [
        DeviceStatus(read_u32(body, 12 + i * 8), read_u32(body, 16 + i * 8))
        for i in range(count)
    ]


def build_set_push(user_id: int, operation: int, token: str) -> bytes:
    """Register (``PUSH_FORCE_ADD``) or remove (``PUSH_DELETE``) a push token.

    Layout of ``CDServerSend::SendCmd_SetPushInfoEx``: the new push info
    followed by the "old" one, which the app leaves empty.
    """
    body = (
        bytes([operation])
        + u32(user_id)
        + u32(_CLIENT_TYPE)
        + bytes([PUSH_LANGUAGE_ENGLISH])
        + u32(2)
        + b"\x01"
        + bytes([PUSH_OS_GOOGLE])
        + varstr(token.encode())
        + b"\x00"
        + b"\x00"  # old push OS
        + varstr(b"")  # old token
    )
    return frame(account_header(ACC_SET_PUSH) + body)


def parse_challenge(body: bytes) -> bytes:
    """Extract the 16 byte challenge."""
    if len(body) < 16:
        raise BalterProtocolError("challenge too short")
    return body[:16]


def parse_login_reply(body: bytes) -> LoginResult:
    """Extract user and session id from a successful login reply."""
    if len(body) < 16:
        raise BalterProtocolError("login reply too short")
    return LoginResult(user_id=read_u32(body, 0), session_id=read_u32(body, 12))


_FIRMWARE = re.compile(rb"\d+\.\d+\.\d+\.\d+")


def parse_device_list(body: bytes) -> list[DeviceRecord]:
    """Parse the device list.

    Only the first record's layout is known (all captures contain a single
    door system), so further records are ignored for now.
    """
    if len(body) < 12:
        raise BalterProtocolError("device list too short")
    if read_u32(body, 8) == 0:
        return []
    offset = 12
    if len(body) < offset + 33:
        raise BalterProtocolError("device record too short")
    device_id = read_u32(body, offset)
    serial = body[offset + 12 : offset + 32].split(b"\x00", 1)[0].decode("ascii", "replace")
    name_len = body[offset + 32]
    name = body[offset + 33 : offset + 33 + name_len].decode("utf-8", "replace")
    firmware_match = _FIRMWARE.search(body, offset + 33 + name_len)
    firmware = firmware_match.group().decode() if firmware_match else None
    return [DeviceRecord(device_id, serial, name, firmware)]


def parse_connect_info(body: bytes) -> ConnectInfo:
    """Extract relay address and the two session tokens (last 46 bytes)."""
    if len(body) < 46:
        raise BalterProtocolError("connect info too short")
    relay_host = str(ipaddress.IPv4Address(body[-46:-42]))
    relay_port = int.from_bytes(body[-42:-40], "big")
    return ConnectInfo(relay_host, relay_port, body[-32:-16], body[-16:])


# --- view session ----------------------------------------------------------


@dataclass
class ViewContext:
    """Routing fields shared by all messages of one view session."""

    view_id: int
    user_id: int
    conn_id: int
    session_id: int = 0

    def header(self, cmd: int) -> bytes:
        return view_header(
            cmd,
            session_id=self.session_id,
            view_id=self.view_id,
            conn_id=self.conn_id,
            user_id=self.user_id,
        )


def build_view_login(ctx: ViewContext, token: bytes) -> bytes:
    """First message of a view session, presenting the public token."""
    body = (
        pad(token, 32)
        + b"\x08"
        + u32(_VIEW_CONN_TYPE)
        + u32(0)
        + u32(ctx.view_id)
        + u32(_DEVICE_CLASS)
    )
    return frame(ctx.header(VIEW_LOGIN) + body)


def build_view_auth(ctx: ViewContext, token: bytes, secret: bytes, challenge: bytes) -> bytes:
    """Answer the relay's challenge."""
    return frame(ctx.header(VIEW_AUTH) + view_digest(token, secret, challenge))


def build_stream_start(ctx: ViewContext, stream: int) -> bytes:
    """Start a media stream (video, audio or talk)."""
    body = u32(ctx.view_id) + u32(_DEVICE_CLASS) + u32(0) + bytes([stream]) + bytes(23)
    return frame(ctx.header(VIEW_STREAM_START) + body)


def build_stream_stop(ctx: ViewContext) -> bytes:
    """Stop all streams of this session."""
    return frame(ctx.header(VIEW_STREAM_STOP) + u32(ctx.view_id) + u32(0))


def build_door_open(ctx: ViewContext, door: int) -> bytes:
    """Trigger a door relay (``DOOR_1`` or ``DOOR_2``)."""
    return frame(ctx.header(VIEW_DOOR_OPEN) + u32(ctx.view_id) + u32(door))


@dataclass(frozen=True)
class MediaFrame:
    """One audio or video frame from the relay."""

    kind: int
    timestamp: int
    data: bytes

    @property
    def is_video(self) -> bool:
        """Video frames carry an H.264 Annex B elementary stream."""
        return self.data.startswith(H264_START)

    @property
    def is_keyframe(self) -> bool:
        """True if the frame contains an IDR slice (NAL type 5)."""
        return self.is_video and any(
            nal & 0x1F == 5 for nal in _nal_types(self.data)
        )


def _nal_types(data: bytes) -> list[int]:
    types = []
    index = data.find(H264_START)
    while index >= 0 and index + 4 < len(data):
        types.append(data[index + 4])
        index = data.find(H264_START, index + 4)
    return types


def parse_media(payload: bytes) -> MediaFrame:
    """Decode a media payload (``Message.body`` of a ``MSG_MEDIA`` message)."""
    if len(payload) < MEDIA_HEADER_LEN:
        raise BalterProtocolError("media frame too short")
    length = read_u32(payload, 33)
    data = payload[MEDIA_HEADER_LEN : MEDIA_HEADER_LEN + length]
    return MediaFrame(payload[18], read_u32(payload, 24), data)


def parse_view_session_id(body: bytes) -> int:
    """Session id assigned by the relay after successful authentication."""
    if len(body) < 4:
        raise BalterProtocolError("auth reply too short")
    return read_u32(body, 0)
