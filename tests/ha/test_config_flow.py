"""Adding the integration from Home Assistant's UI."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL
from custom_components.static_radio.const import DOMAIN


async def test_the_flow_shows_the_notice_then_creates_one_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, schedule_text: str
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)

    shown = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert shown["type"] is FlowResultType.FORM
    assert shown["step_id"] == "user"
    assert shown["description_placeholders"] == {"static_url": "https://staticradio.app"}
    assert aioclient_mock.call_count == 0  # showing the form asks Static for nothing

    done = await hass.config_entries.flow.async_configure(shown["flow_id"], {})
    await hass.async_block_till_done()
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert "unofficial" in done["title"].lower()
    assert done["data"] == {}
    assert aioclient_mock.call_count == 1  # and setting it up asks once


async def test_a_second_entry_is_refused(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
