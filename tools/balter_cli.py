"""Test the Balter cloud connection without Home Assistant.

    python tools/balter_cli.py USERNAME devices
    python tools/balter_cli.py USERNAME open --door 2 -v
    python tools/balter_cli.py USERNAME snapshot --out bild.jpg
    python tools/balter_cli.py USERNAME listen

The password is asked for interactively. Add -v for protocol debug output.
"snapshot" needs ffmpeg on the PATH for JPEG output (otherwise .h264 is saved),
"listen" uses the account session and Firebase push (pip install firebase-messaging);
use --no-push to skip Firebase.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "balter_era"))

from pybalter import DEFAULT_HOST, DEFAULT_PORT, DOOR_1, DOOR_2, BalterClient  # noqa: E402

DOORS = {1: DOOR_1, 2: DOOR_2}
FCM_FILE = Path("balter_fcm_credentials.json")


async def snapshot(client: BalterClient, device, out: Path) -> None:
    keyframe = await client.async_snapshot(device)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and out.suffix.lower() in (".jpg", ".jpeg"):
        subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-f", "h264", "-i", "pipe:0",
             "-frames:v", "1", str(out)],
            input=keyframe, check=True,
        )
    else:
        out = out.with_suffix(".h264")
        out.write_bytes(keyframe)
    print(f"Bild gespeichert: {out}")


async def listen(client: BalterClient, devices: list, use_push: bool) -> None:
    """Listen on both channels: the account session and (optionally) Firebase."""
    stamp = lambda: datetime.now().strftime("%H:%M:%S")  # noqa: E731

    def on_status(status) -> None:
        kind = "KLINGELN/ALARM" if status.is_alarm else "Status"
        print(f"{stamp()} {kind}: Gerät 0x{status.device_id:x} Status {status.status}", flush=True)

    def on_message(msg) -> None:
        print(f"{stamp()} KONTO: cmd 0x{msg.cmd:04x} {msg.body.hex()}", flush=True)

    listener = None
    if use_push:
        from pybalter.push import BalterPushListener  # noqa: PLC0415

        def on_push(payload: dict) -> None:
            print(f"{stamp()} PUSH:", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)

        credentials = json.loads(FCM_FILE.read_text()) if FCM_FILE.exists() else None
        listener = BalterPushListener(
            client,
            on_push,
            credentials=credentials,
            on_credentials=lambda creds: FCM_FILE.write_text(json.dumps(creds)),
        )
        await listener.async_start()
    print("Warte auf Klingeln ... (Strg+C beendet)", flush=True)
    try:
        await client.async_watch_forever(
            [device.device_id for device in devices], on_status, on_message=on_message
        )
    finally:
        if listener:
            await listener.async_stop()


async def main(args: argparse.Namespace) -> None:
    client = BalterClient(
        args.username, getpass.getpass("Balter-Passwort: "), host=args.host, port=args.port
    )
    devices = await client.async_get_devices()
    for index, device in enumerate(devices):
        print(f"[{index}] {device.name}  Firmware {device.firmware}")
    device = devices[args.device]
    if args.command == "open":
        await client.async_open_door(device, DOORS[args.door])
        print(f"Tür {args.door} geöffnet.")
    elif args.command == "snapshot":
        await snapshot(client, device, Path(args.out))
    elif args.command == "listen":
        await listen(client, devices, not args.no_push)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username")
    parser.add_argument("command", choices=("devices", "open", "snapshot", "listen"))
    parser.add_argument("--door", type=int, choices=(1, 2), default=2)
    parser.add_argument("--device", type=int, default=0, help="Index aus 'devices'")
    parser.add_argument("--out", default="balter_snapshot.jpg")
    parser.add_argument("--no-push", action="store_true", help="listen: nur Konto-Verbindung, ohne Firebase")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-v", "--verbose", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if arguments.verbose else logging.WARNING)
    try:
        asyncio.run(main(arguments))
    except KeyboardInterrupt:
        pass
