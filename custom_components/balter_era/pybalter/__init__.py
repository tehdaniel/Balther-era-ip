"""Minimal client for the Balter ERA IP door intercom cloud.

Kept free of Home Assistant imports so it can be used and tested on its own.
"""

from .client import DEFAULT_HOST, DEFAULT_PORT, BalterClient
from .exceptions import (
    BalterAuthError,
    BalterConnectionError,
    BalterError,
    BalterProtocolError,
)
from .protocol import DOOR_1, DOOR_2, DeviceRecord

# BalterPushListener lives in .push and needs the firebase-messaging package.

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DOOR_1",
    "DOOR_2",
    "BalterAuthError",
    "BalterClient",
    "BalterConnectionError",
    "BalterError",
    "BalterProtocolError",
    "DeviceRecord",
]
