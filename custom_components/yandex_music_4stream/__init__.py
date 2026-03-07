"""Yandex Music for 4STREAM integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_YANDEX_TOKEN, DOMAIN, PROXY_PORT
from .proxy import StreamProxy
from .yandex_client import YandexMusicClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["media_player"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Yandex Music 4STREAM from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create shared Yandex Music client
    ym_client = YandexMusicClient(entry.data[CONF_YANDEX_TOKEN])
    try:
        await ym_client.authenticate()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to connect to Yandex Music: {err}") from err

    # Share a single proxy across all config entries
    if "_proxy" not in hass.data[DOMAIN]:
        proxy = StreamProxy(port=PROXY_PORT)
        await proxy.start()
        hass.data[DOMAIN]["_proxy"] = proxy
        hass.data[DOMAIN]["_proxy_refs"] = 0

    hass.data[DOMAIN]["_proxy_refs"] += 1

    hass.data[DOMAIN][entry.entry_id] = {
        "ym_client": ym_client,
        "proxy": hass.data[DOMAIN]["_proxy"],
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.data[DOMAIN]["_proxy_refs"] -= 1
        if hass.data[DOMAIN]["_proxy_refs"] <= 0:
            await hass.data[DOMAIN]["_proxy"].stop()
            del hass.data[DOMAIN]["_proxy"]
            del hass.data[DOMAIN]["_proxy_refs"]
    return unload_ok
