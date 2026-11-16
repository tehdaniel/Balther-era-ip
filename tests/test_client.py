"""End-to-end test of BalterClient against a local fake account server and relay."""

from __future__ import annotations

import asyncio
import ipaddress
import unittest

from pybalter import BalterAuthError, BalterClient, protocol as p

USER, PASSWORD = "user@example.com", "secret"
SERIAL = "TEO00000TESTTEST0001"
TOKEN, SECRET = b"@tokentokentoken", b"secretsecretsecr"
KEYFRAME = p.H264_START + b"\x67sps" + p.H264_START + b"\x68pps" + p.H264_START + b"\x65idr"
PFRAME = p.H264_START + b"\x41slice"


async def _read(reader: asyncio.StreamReader) -> p.Message:
    magic = await reader.readexactly(2)
    assert magic == p.FRAME_MAGIC
    length = int.from_bytes(await reader.readexactly(2), "big")
    return p.parse_payload(await reader.readexactly(length))


def _reply(msg_class: int, cmd: int, body: bytes) -> bytes:
    header = bytearray(p.HEADER_LEN)
    header[0], header[1] = p.MSG_CONTROL, msg_class
    header[2:4] = cmd.to_bytes(2, "big")
    return p.frame(bytes(header) + body)


def media_frame(kind: int, data: bytes) -> bytes:
    """Media frame with the 37 byte header layout seen in captures."""
    header = bytearray(p.MEDIA_HEADER_LEN)
    header[0] = p.MSG_MEDIA
    header[18] = kind
    header[24:28] = p.u32(1_763_228_638)
    header[33:37] = p.u32(len(data))
    return p.frame(bytes(header) + data)


class FakeCloud:
    """Implements just enough of the server side to drive the client."""

    def __init__(self) -> None:
        self.doors: list[int] = []
        self.push: list[bytes] = []
        self.relay_port = 0

    async def account(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        challenge = b"0123456789abcdef"
        try:
            while True:
                msg = await _read(reader)
                if msg.cmd == p.ACC_CHALLENGE_REQUEST:
                    writer.write(p.KEEPALIVE)  # must be skipped by the client
                    writer.write(_reply(p.CLASS_ACCOUNT, p.ACC_CHALLENGE, challenge))
                elif msg.cmd == p.ACC_LOGIN:
                    name = msg.body[1 : 1 + msg.body[0]].decode()
                    digest = msg.body[1 + msg.body[0] : 17 + msg.body[0]]
                    if digest != p.account_digest(USER, PASSWORD, challenge) or name != USER:
                        break
                    body = p.u32(0x1234) + p.u32(1) + p.u32(5) + p.u32(0xABCDEF) + bytes(8)
                    writer.write(_reply(p.CLASS_ACCOUNT, p.ACC_LOGIN_REPLY, body))
                elif msg.cmd == p.ACC_DEVICE_LIST_REQUEST:
                    body = (
                        p.u32(0x1234) + p.u32(5) + p.u32(1) + p.u32(0x5678) + p.u32(1)
                        + p.u32(0) + SERIAL.encode() + p.varstr(b"Home") + bytes(18)
                        + b"5.0.0.80\x00"
                    )
                    writer.write(_reply(p.CLASS_ACCOUNT, p.ACC_DEVICE_LIST, body))
                elif msg.cmd == p.ACC_CONNECT_REQUEST:
                    assert p.read_u32(msg.body, 13) == 0x5678
                    body = (
                        bytes(40) + ipaddress.IPv4Address("127.0.0.1").packed
                        + self.relay_port.to_bytes(2, "big") * 2 + bytes(5) + b"\x03"
                        + TOKEN + SECRET
                    )
                    writer.write(_reply(p.CLASS_ACCOUNT, p.ACC_CONNECT_INFO, body))
                elif msg.cmd == p.ACC_SET_PUSH:
                    self.push.append(msg.body)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()

    @staticmethod
    async def _wake(writer: asyncio.StreamWriter, state: dict[str, bool]) -> None:
        """Outdoor station powers up and starts sending video."""
        await asyncio.sleep(0.3)
        state["awake"] = True
        writer.write(p.frame(b"\x01\x00broken"))  # malformed, must be skipped
        writer.write(media_frame(p.MEDIA_AUDIO, b"\x21" * 33))
        writer.write(media_frame(p.MEDIA_VIDEO_KEY, KEYFRAME))
        for _ in range(3):
            await asyncio.sleep(0.01)
            writer.write(media_frame(0x90, PFRAME))
        await writer.drain()

    async def relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        challenge = b"fedcba9876543210"
        view_id = p.serial_to_view_id(SERIAL)
        state = {"awake": False}
        try:
            while True:
                msg = await _read(reader)
                assert p.read_u32(msg.header, 13) == view_id
                if msg.cmd == p.VIEW_LOGIN:
                    assert msg.body[:16] == TOKEN
                    writer.write(_reply(p.CLASS_VIEW, p.VIEW_CHALLENGE, challenge))
                elif msg.cmd == p.VIEW_AUTH:
                    if msg.body != p.view_digest(TOKEN, SECRET, challenge):
                        break
                    writer.write(_reply(p.CLASS_VIEW, p.VIEW_AUTH_OK, p.u32(0x169178)))
                elif msg.cmd == p.VIEW_STREAM_START:
                    assert p.read_u32(msg.header, 9) == 0x169178
                    writer.write(_reply(p.CLASS_VIEW, p.VIEW_STATUS, p.u32(view_id)))
                    asyncio.get_running_loop().create_task(self._wake(writer, state))
                elif msg.cmd == p.VIEW_DOOR_OPEN:
                    # Like the real relay: always acknowledged, but the
                    # station only acts once it is awake.
                    if state["awake"]:
                        self.doors.append(p.read_u32(msg.body, 4))
                    writer.write(_reply(p.CLASS_VIEW, p.VIEW_DOOR_OPEN_ACK, p.u32(view_id)))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.cloud = FakeCloud()
        self.account = await asyncio.start_server(self.cloud.account, "127.0.0.1", 0)
        self.relay = await asyncio.start_server(self.cloud.relay, "127.0.0.1", 0)
        self.cloud.relay_port = self.relay.sockets[0].getsockname()[1]
        self.port = self.account.sockets[0].getsockname()[1]

    async def asyncTearDown(self) -> None:
        for server in (self.account, self.relay):
            server.close()
            await server.wait_closed()

    def client(self, password: str = PASSWORD) -> BalterClient:
        return BalterClient(
            USER, password, host="127.0.0.1", port=self.port, timeout=2, hold_time=0.1
        )

    async def test_devices(self) -> None:
        (device,) = await self.client().async_get_devices()
        self.assertEqual((device.serial, device.name, device.firmware), (SERIAL, "Home", "5.0.0.80"))

    async def test_open_both_doors(self) -> None:
        client = self.client()
        (device,) = await client.async_get_devices()
        await client.async_open_door(device, p.DOOR_1)
        await client.async_open_door(device, p.DOOR_2)
        self.assertEqual(self.cloud.doors, [p.DOOR_1, p.DOOR_2])

    async def test_wrong_password(self) -> None:
        with self.assertRaises(BalterAuthError):
            await self.client("wrong").async_get_devices()

    async def test_snapshot_returns_keyframe(self) -> None:
        client = self.client()
        (device,) = await client.async_get_devices()
        self.assertEqual(await client.async_snapshot(device), KEYFRAME)

    async def test_stream_yields_video_only(self) -> None:
        client = self.client()
        (device,) = await client.async_get_devices()
        frames = []
        async for frame in client.async_stream(device):
            frames.append(frame)
            if len(frames) == 3:
                break
        self.assertEqual(frames, [KEYFRAME, PFRAME, PFRAME])

    async def test_push_registration(self) -> None:
        client = self.client()
        await client.async_set_push_token("fcm-token")
        await client.async_set_push_token("fcm-token", register=False)
        added, removed = self.cloud.push
        self.assertEqual(added[0], p.PUSH_FORCE_ADD)
        self.assertEqual(removed[0], p.PUSH_DELETE)
        self.assertEqual(p.read_u32(added, 1), 0x1234)
        self.assertEqual(added[15], p.PUSH_OS_GOOGLE)
        self.assertEqual(added[16 : 17 + added[16]], p.varstr(b"fcm-token"))


if __name__ == "__main__":
    unittest.main()
