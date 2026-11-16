"""Receive doorbell notifications the same way the app does (Firebase push).

Requires the ``firebase-messaging`` package.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import firebase
from .client import BalterClient

_LOGGER = logging.getLogger(__name__)

PushCallback = Callable[[dict[str, Any]], None]
CredentialsCallback = Callable[[dict[str, Any]], None]


class BalterPushListener:
    """Registers an FCM token with Balter and forwards incoming pushes."""

    def __init__(
        self,
        client: BalterClient,
        on_push: PushCallback,
        *,
        credentials: dict[str, Any] | None = None,
        on_credentials: CredentialsCallback | None = None,
        http_session: Any = None,
    ) -> None:
        # Imported lazily so pybalter itself stays dependency free.
        from firebase_messaging import FcmPushClient, FcmRegisterConfig  # noqa: PLC0415

        self._client = client
        self._on_push = on_push
        self._seen: list[str] = []
        self.token: str | None = None
        self._fcm = FcmPushClient(
            self._handle,
            FcmRegisterConfig(
                project_id=firebase.PROJECT_ID,
                app_id=firebase.APP_ID,
                api_key=firebase.API_KEY,
                messaging_sender_id=firebase.SENDER_ID,
            ),
            credentials,
            on_credentials,
            http_client_session=http_session,
        )

    async def async_start(self) -> str:
        """Register with Firebase and Balter, then start listening."""
        self.token = await self._fcm.checkin_or_register()
        await self._client.async_set_push_token(self.token)
        await self._fcm.start()
        _LOGGER.debug("listening for doorbell pushes")
        return self.token

    async def async_stop(self) -> None:
        """Stop listening (the token stays registered)."""
        await self._fcm.stop()

    async def async_unregister(self) -> None:
        """Remove the token from the Balter account."""
        if self.token:
            await self._client.async_set_push_token(self.token, register=False)

    def _handle(self, notification: dict[str, Any], persistent_id: str, _ctx: Any) -> None:
        if persistent_id in self._seen:
            return
        self._seen.append(persistent_id)
        del self._seen[:-100]
        _LOGGER.debug("push received: %s", notification)
        self._on_push(notification)
