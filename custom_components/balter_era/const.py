"""Constants for the Balter ERA IP integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "balter_era"
MANUFACTURER: Final = "Balter"
MODEL: Final = "ERA IP"

CONF_FCM_CREDENTIALS: Final = "fcm_credentials"
CONF_RING_ENTITY: Final = "ring_entity"
CONF_CLOUD_RING: Final = "cloud_ring"


def signal_ring(entry_id: str, serial: str) -> str:
    """Dispatcher signal fired when the doorbell rings."""
    return f"{DOMAIN}_{entry_id}_{serial}_ring"


def signal_image(entry_id: str, serial: str) -> str:
    """Dispatcher signal fired when a new still image is available."""
    return f"{DOMAIN}_{entry_id}_{serial}_image"
