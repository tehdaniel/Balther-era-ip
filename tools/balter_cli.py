"""Test the Balter cloud connection without Home Assistant.

    python tools/balter_cli.py USERNAME devices
    python tools/balter_cli.py USERNAME open --door 2

The password is asked for interactively. Add -v for protocol debug output.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "balter_era"))

from pybalter import DEFAULT_HOST, DEFAULT_PORT, DOOR_1, DOOR_2, BalterClient  # noqa: E402

DOORS = {1: DOOR_1, 2: DOOR_2}


async def main(args: argparse.Namespace) -> None:
    client = BalterClient(
        args.username, getpass.getpass("Balter-Passwort: "), host=args.host, port=args.port
    )
    devices = await client.async_get_devices()
    for index, device in enumerate(devices):
        print(f"[{index}] {device.name}  Firmware {device.firmware}")
    if args.command == "open":
        await client.async_open_door(devices[args.device], DOORS[args.door])
        print(f"Tür {args.door} geöffnet.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username")
    parser.add_argument("command", choices=("devices", "open"))
    parser.add_argument("--door", type=int, choices=(1, 2), default=2)
    parser.add_argument("--device", type=int, default=0, help="Index aus 'devices'")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-v", "--verbose", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if arguments.verbose else logging.WARNING)
    asyncio.run(main(arguments))
