"""Tests for the wire format.

The view-session vectors are one-time tokens taken from a real capture; they
are worthless after the session ended but prove the digest formula.
"""

from __future__ import annotations

import hashlib
import unittest

from pybalter import protocol as p


class DigestTests(unittest.TestCase):
    VIEW_VECTORS = (
        (b"@wlr4355rqtbobnz", b"fwlrguoqrqtbobnz", b"bggnrmlklggslpny", "0dc940ab8111e250497dc0679f5dca54"),
        (b"@tan5733bkmzvxrz", b"vtanhkwlbkmzvxrz", b"squwsqggjzneslql", "a7d3b5b7b7ada2ea9a988125849e6a74"),
        (b"@kic5817zdkqlrue", b"ckicjqtxzdkqlrue", b"mvqliwqfhslhjthe", "e9fbfc476a388782b0d7bcf9ab3797f8"),
    )

    def test_view_digest_matches_captures(self) -> None:
        for token, secret, challenge, expected in self.VIEW_VECTORS:
            self.assertEqual(p.view_digest(token, secret, challenge).hex(), expected)

    def test_account_digest_formula(self) -> None:
        challenge = b"abcdefghijklmnop"
        expected = hashlib.md5(
            b"user@example.com" + hashlib.md5(b"secret").digest() + challenge
        ).digest()
        self.assertEqual(p.account_digest("user@example.com", "secret", challenge), expected)

    def test_username_is_truncated_like_the_app(self) -> None:
        self.assertEqual(len(p.trim_username("x" * 40)), 31)


class SerialTests(unittest.TestCase):
    def test_serial_to_view_id_uses_positions_1_to_7(self) -> None:
        self.assertEqual(
            p.serial_to_view_id("XXO00000HIJ"), p.serial_to_view_id("YXO00000ZZZ")
        )

    def test_serial_to_view_id_base36(self) -> None:
        # Digit order is positions 2,5,3,6,7,4,1 with offsets 12,34,10,21,30,25,4.
        # "O" (24) at position 2 gives (24 + 12) % 36 == 0, "X" (33) at 1 gives 1.
        expected = 0
        for digit in (0, 34, 10, 21, 30, 25, 1):
            expected = expected * 36 + digit
        self.assertEqual(p.serial_to_view_id("XXO00000"), expected)

    def test_serial_to_view_id_overflow_returns_zero(self) -> None:
        # "0" at position 2 gives leading digit 12 -> exceeds 32 bits.
        self.assertEqual(p.serial_to_view_id("00000000"), 0)

    def test_lowercase_equals_uppercase(self) -> None:
        self.assertEqual(p.serial_to_view_id("xxo0abcd"), p.serial_to_view_id("XXO0ABCD"))


class FramingTests(unittest.TestCase):
    def test_account_header_layout(self) -> None:
        header = p.account_header(p.ACC_CHALLENGE_REQUEST)
        self.assertEqual(
            header.hex(),
            "02d60030" + "00" * 17 + "00010001" + "00" * 17 + "010000",
        )

    def test_view_frame_layout(self) -> None:
        ctx = p.ViewContext(view_id=0x102030, user_id=0x1234, conn_id=0xBEEF)
        route = "0010203000000000000100010000beef00001234" + "00" * 12
        self.assertEqual(
            p.build_view_login(ctx, b"@tan5733bkmzvxrz").hex(),
            "0202005e" "02ca000104000200" "0000000000" + route
            + "4074616e35373333626b6d7a7678727a" + "00" * 16
            + "08" "00000017" "00000000" "00102030" "00000012",
        )
        ctx.session_id = 0x169178
        self.assertEqual(
            p.build_door_open(ctx, p.DOOR_2).hex(),
            "02020035" "02ca007a04000200" "0000169178" + route + "0010203000000065",
        )

    def test_parse_roundtrip(self) -> None:
        data = p.build_device_list_request(0x1234)
        msg = p.parse_payload(data[4:])
        self.assertEqual(msg.cmd, p.ACC_DEVICE_LIST_REQUEST)
        self.assertEqual(msg.msg_class, p.CLASS_ACCOUNT)
        self.assertEqual(msg.body, bytes.fromhex("00001234"))


class ParserTests(unittest.TestCase):
    def test_parse_device_list(self) -> None:
        serial = b"TESTTESTTESTTEST0001"
        body = (
            p.u32(0x1234) + p.u32(5) + p.u32(1)
            + p.u32(0x5678) + p.u32(1) + p.u32(0)
            + serial + p.varstr(b"Home") + bytes(18) + b"\x03" + b"5.0.0.80\x00"
        )
        (device,) = p.parse_device_list(body)
        self.assertEqual(device.device_id, 0x5678)
        self.assertEqual(device.serial, serial.decode())
        self.assertEqual(device.name, "Home")
        self.assertEqual(device.firmware, "5.0.0.80")

    def test_parse_connect_info(self) -> None:
        body = (
            bytes(60) + b"\x00\x04" + bytes([159, 69, 151, 111]) + b"\x15\x3f\x15\x3f"
            + b"\x00\x00\x00\x00\x00\x03" + b"@" + b"a" * 15 + b"b" * 16
        )
        info = p.parse_connect_info(body)
        self.assertEqual((info.relay_host, info.relay_port), ("159.69.151.111", 5439))
        self.assertEqual(info.token, b"@" + b"a" * 15)
        self.assertEqual(info.secret, b"b" * 16)


if __name__ == "__main__":
    unittest.main()
