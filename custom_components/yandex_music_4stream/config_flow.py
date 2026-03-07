"""Config flow for Yandex Music 4STREAM integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .arylic_client import ArylicClient
from .const import CONF_DEVICES, CONF_DEVICE_HOST, CONF_DEVICE_NAME, CONF_YANDEX_TOKEN, DOMAIN
from .yandex_client import YandexMusicClient

_LOGGER = logging.getLogger(__name__)


class YandexMusic4StreamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Yandex Music 4STREAM."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._yandex_token: str = ""
        self._account_name: str = ""
        self._devices: list[dict[str, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Yandex Music token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_YANDEX_TOKEN]
            try:
                client = YandexMusicClient(token)
                self._account_name = await client.authenticate()
                self._yandex_token = token
                return await self.async_step_device()
            except Exception:
                _LOGGER.exception("Failed to authenticate with Yandex Music")
                errors["base"] = "auth_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_YANDEX_TOKEN): str,
                }
            ),
            errors=errors,
            description_placeholders={"account": self._account_name},
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Add 4STREAM device by IP."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_DEVICE_HOST]
            try:
                client = ArylicClient(host)
                info = await client.get_device_info()
                self._devices.append(
                    {CONF_DEVICE_HOST: host, CONF_DEVICE_NAME: info.name}
                )
                return await self.async_step_confirm()
            except Exception:
                _LOGGER.exception("Failed to connect to device at %s", host)
                errors["base"] = "device_not_found"

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_HOST): str,
                }
            ),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Add more devices or finish."""
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_device()
            return self.async_create_entry(
                title=f"Yandex Music ({self._account_name})",
                data={
                    CONF_YANDEX_TOKEN: self._yandex_token,
                    CONF_DEVICES: self._devices,
                },
            )

        devices_str = ", ".join(d[CONF_DEVICE_NAME] for d in self._devices)

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("add_another", default=False): bool,
                }
            ),
            description_placeholders={
                "account": self._account_name,
                "devices": devices_str,
            },
        )
