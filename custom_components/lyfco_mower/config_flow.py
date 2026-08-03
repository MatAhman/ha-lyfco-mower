"""Config flow for Lyfco mower."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import DEFAULT_NAME, DOMAIN
from .protocol import LyfcoError, LyfcoMowerClient


class LyfcoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a mower by its local IPv4 address or hostname."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not host:
                errors[CONF_HOST] = "invalid_host"
            else:
                client = LyfcoMowerClient(host)
                try:
                    await client.async_get_status()
                except LyfcoError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(host.lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"{DEFAULT_NAME} ({host})",
                        data={CONF_HOST: host},
                    )
                finally:
                    await client.async_close()

        schema = vol.Schema(
            {vol.Required(CONF_HOST, default="192.168.1.48"): str}
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
