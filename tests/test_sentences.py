"""The English sentence pack, matched offline with Home Assistant's own matcher.

hassil is the library Home Assistant uses to match what was said against
sentence files, pinned here to the version Core 2026.7.2 ships. Three things
are held:

- every phrase the README promises reaches its intent, with the right station;
- every sentence the pack can produce contains the word "static", so it cannot
  capture another integration's or a household's own phrases;
- a list of everyday radio, music, transport and timer phrases matches nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hassil
import pytest
import yaml

from custom_components.static_radio.schedule import parse_schedule

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "custom_components/static_radio/sentences/en.yaml"
FIXTURE = ROOT / "tests/fixtures/schedule-wear.2026-11-11.json"

INTENT_NAMES = {
    "StaticPlay",
    "StaticTune",
    "StaticNext",
    "StaticPrevious",
    "StaticWhatsOn",
    "StaticSendSupportLink",
}


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture(scope="module")
def pack(raw: dict[str, Any]) -> hassil.Intents:
    return hassil.Intents.from_dict(raw)


def _match(pack: hassil.Intents, said: str) -> tuple[str, dict[str, Any]] | None:
    result = hassil.recognize(said, pack)
    if result is None:
        return None
    return result.intent.name, {name: e.value for name, e in result.entities.items()}


@pytest.mark.parametrize(
    ("said", "intent", "slots"),
    [
        # Written the way a speech recognizer hands them over: capitals, a period.
        ("Play Static.", "StaticPlay", {}),
        ("play static radio", "StaticPlay", {}),
        ("Put on Static, please.", "StaticPlay", {}),
        ("listen to static", "StaticPlay", {}),
        ("Play Static on the speaker.", "StaticPlay", {}),
        ("Play Static News.", "StaticTune", {"station": "News"}),
        ("Tune Static to Weird.", "StaticTune", {"station": "Weird"}),
        ("switch static to nature", "StaticTune", {"station": "Nature"}),
        ("Play the sleep station on Static.", "StaticTune", {"station": "Sleep"}),
        ("change to make on static radio", "StaticTune", {"station": "Make"}),
        ("Play Static Sheila B.", "StaticTune", {"station": "SHEILA B"}),
        ("play static sheila bee", "StaticTune", {"station": "SHEILA B"}),
        ("tune static to sheila", "StaticTune", {"station": "SHEILA B"}),
        ("play static learning", "StaticTune", {"station": "Learn"}),
        ("Next Static station.", "StaticNext", {}),
        ("go to the next static station", "StaticNext", {}),
        ("Previous Static station.", "StaticPrevious", {}),
        ("go back one static station", "StaticPrevious", {}),
        ("What's on Static?", "StaticWhatsOn", {}),
        ("what is playing on static radio", "StaticWhatsOn", {}),
        ("whats static playing", "StaticWhatsOn", {}),
        ("What show is this on Static?", "StaticWhatsOn", {}),
        ("Send me the Static support link.", "StaticSendSupportLink", {}),
        ("send the support link for static", "StaticSendSupportLink", {}),
        ("How can I support this Static show?", "StaticSendSupportLink", {}),
    ],
)
def test_what_a_listener_says_reaches_its_intent(
    pack: hassil.Intents, said: str, intent: str, slots: dict[str, str]
) -> None:
    assert _match(pack, said) == (intent, slots)


@pytest.mark.parametrize(
    "said",
    [
        # Radio phrases a household is likely to have already.
        "next station",
        "previous station",
        "change the station",
        "play the radio",
        "play the news",
        "play the news station",
        "play the classical station",
        "play jazz on the radio",
        "play the radio station nature",
        "turn on the radio",
        "what's on the radio",
        # Music.
        "play jazz",
        "play nature sounds",
        "play news",
        "play sleep music",
        "play weird al",
        "play work playlist",
        "play the album static",
        "play static by the band static",
        "play my playlist",
        "play some music",
        "what's playing",
        "what song is this",
        # Transport and volume: Static adds none of its own.
        "pause",
        "stop",
        "resume",
        "stop the music",
        "next",
        "next track",
        "skip",
        "skip this",
        "previous track",
        "go back",
        "pause static",
        "stop static",
        "resume static",
        "skip this static episode",
        "rewind static",
        "fast forward static",
        "turn it up",
        "set the volume to 40",
        # Timers and the rest of the house.
        "set a timer for 5 minutes",
        "cancel the timer",
        "turn on the lights",
        "what's the weather",
        "what's the temperature",
        "send me the link",
        # The word alone, and near misses a recognizer might write.
        "static",
        "there's static on the line",
        "play statics",
        "play ecstatic",
        # Stations that are not on the dial are not guessed at.
        "play static jazz",
        "tune static to 91.9",
    ],
)
def test_everyday_phrases_are_left_alone(pack: hassil.Intents, said: str) -> None:
    assert _match(pack, said) is None


def test_every_sentence_the_pack_can_produce_says_static(pack: hassil.Intents) -> None:
    produced = list(hassil.sample_intents(pack, exclude_sentences_with_wildcards=False))
    assert len(produced) > 100
    assert {intent for intent, _text in produced} == INTENT_NAMES
    unanchored = [
        text
        for _intent, text in produced
        if "static" not in text.lower().split() and "static's" not in text.lower().split()
    ]
    assert unanchored == []


def test_the_pack_has_no_wildcard_and_only_prefixed_lists(raw: dict[str, Any]) -> None:
    assert set(raw["intents"]) == INTENT_NAMES
    assert set(raw["lists"]) == {"static_station"}
    assert all(name.startswith("static") for name in raw["expansion_rules"])
    assert "wildcard" not in raw["lists"]["static_station"]
    assert "range" not in raw["lists"]["static_station"]


def test_nothing_in_the_pack_pauses_seeks_or_skips_within_a_show(pack: hassil.Intents) -> None:
    forbidden = {"pause", "resume", "rewind", "seek", "skip", "forward", "episode", "restart"}
    produced = [text.lower() for _intent, text in hassil.sample_intents(pack)]
    # "skip to the next static station" moves along the dial, which is tuning.
    offenders = [
        text for text in produced if forbidden & set(text.split()) and "next static" not in text
    ]
    assert offenders == []


def test_the_station_list_is_the_dial(raw: dict[str, Any]) -> None:
    schedule = parse_schedule(json.loads(FIXTURE.read_text(encoding="utf-8")))
    on_the_dial = {station.display_name for station in schedule.stations}
    in_the_pack = {value["out"] for value in raw["lists"]["static_station"]["values"]}
    assert in_the_pack == on_the_dial


def test_each_station_can_be_asked_for_by_its_own_name(pack: hassil.Intents) -> None:
    schedule = parse_schedule(json.loads(FIXTURE.read_text(encoding="utf-8")))
    for station in schedule.stations:
        said = f"play static {station.display_name}"
        assert _match(pack, said) == ("StaticTune", {"station": station.display_name}), said
