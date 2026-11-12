"""Setup from Home Assistant's UI.

There is nothing to enter yet: Static's interface is public and needs no
account. The one step says what is being added, and that it is unofficial.
Nothing is fetched here, so adding the integration costs Static one schedule
request, made at setup, and not two.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, STATIC_URL, TITLE


class StaticConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add the integration. The manifest allows only one entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the notice, then create the entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", description_placeholders={"static_url": STATIC_URL}
            )
        return self.async_create_entry(title=TITLE, data={})
