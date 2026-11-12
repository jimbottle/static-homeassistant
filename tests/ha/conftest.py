"""Shared setup for the tests that run inside the Home Assistant test harness.

Nothing here reaches the network: every request is answered by the harness's
mock, from the saved schedule fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.static_radio.const import DOMAIN, TITLE

SCHEDULE_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures/schedule-wear.2026-11-11.json"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations: None) -> None:
    """Let the harness load integrations from custom_components/."""


@pytest.fixture
def schedule_text() -> str:
    return SCHEDULE_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    config_entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={})
    config_entry.add_to_hass(hass)
    return config_entry
