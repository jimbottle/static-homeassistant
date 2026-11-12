"""Setup and options from Home Assistant's UI.

Adding the integration asks for nothing: Static's interface is public and
needs no account. The one step says what is being added, and that it is
unofficial. Nothing is fetched here, so adding the integration costs Static
one schedule request, made at setup, and not two.

The Configure screen holds the choices that belong to a home:

- which media players can be tuned to Static;
- for each of them, the lead: how far ahead of the live moment a stream is
  started, to cover the time that speaker takes to make sound;
- which phone a show's Support link is sent to when a listener asks for it.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_LEADS_MS,
    CONF_NOTIFY_SERVICE,
    CONF_SPEAKERS,
    DEFAULT_LEAD_MS,
    DOMAIN,
    LEAD_MAX_MS,
    STATIC_URL,
    TITLE,
)

NO_PHONE = "none"  # the choice that means "send Support links nowhere"
PHONE_PREFIX = "mobile_app_"  # how the Home Assistant app names a phone's notify action


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> StaticOptionsFlow:
        """The Configure screen."""
        return StaticOptionsFlow()


class StaticOptionsFlow(OptionsFlowWithReload):
    """Speakers and the phone first, then a lead for each speaker chosen."""

    def __init__(self) -> None:
        self._chosen: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which speakers, and which phone."""
        options = self.config_entry.options
        if user_input is not None:
            self._chosen = {CONF_SPEAKERS: list(user_input.get(CONF_SPEAKERS, []))}
            phone = user_input.get(CONF_NOTIFY_SERVICE, NO_PHONE)
            if phone != NO_PHONE:
                self._chosen[CONF_NOTIFY_SERVICE] = phone
            if self._chosen[CONF_SPEAKERS]:
                return await self.async_step_leads()
            return self.async_create_entry(data={**self._chosen, CONF_LEADS_MS: {}})

        # Only phones with the Home Assistant app: other notify actions take
        # different fields, and would not be "your phone" in the reply.
        apps = sorted(
            name
            for name in self.hass.services.async_services_for_domain("notify")
            if name.startswith(PHONE_PREFIX)
        )
        phones = [NO_PHONE, *apps]
        current_phone = options.get(CONF_NOTIFY_SERVICE, NO_PHONE)
        if current_phone not in phones:
            phones.append(current_phone)  # keep a choice whose phone is offline just now
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SPEAKERS, default=list(options.get(CONF_SPEAKERS, []))
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player", multiple=True)
                ),
                vol.Optional(CONF_NOTIFY_SERVICE, default=current_phone): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=phones,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_NOTIFY_SERVICE,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_leads(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A lead for each speaker, in milliseconds."""
        speakers: list[str] = self._chosen[CONF_SPEAKERS]
        if user_input is not None:
            leads = {speaker: int(user_input[speaker]) for speaker in speakers}
            return self.async_create_entry(data={**self._chosen, CONF_LEADS_MS: leads})

        known: dict[str, int] = self.config_entry.options.get(CONF_LEADS_MS, {})
        schema = vol.Schema(
            {
                vol.Required(
                    speaker, default=known.get(speaker, DEFAULT_LEAD_MS)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=LEAD_MAX_MS,
                        step=50,
                        unit_of_measurement="ms",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
                for speaker in speakers
            }
        )
        return self.async_show_form(step_id="leads", data_schema=schema)
