"""Checks on the ESPHome dial overlay, read as YAML with no ESPHome installed.

`esphome/static-dial.yaml` changes the stock Voice PE firmware as little as it
can, so that it stays a small file on top of a 1,900-line one. These tests
hold it to that: it is pinned to the stock release, it reaches into the stock
config by four ids only, it boots with stock behaviour, and it keeps Wi-Fi
details out of the repo. The full compile is run by hand with ESPHome (see the
file's opening comment); it needs the network and minutes, so it is not here.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "esphome" / "static-dial.yaml"

STOCK_RELEASE = "26.9.0"
# The commit the 26.9.0 tag points to (docs/phase0-results.md).
STOCK_COMMIT = "2644f4c794271d735d182ca7ebf899ed46164f4e"


@dataclass(frozen=True)
class Tagged:
    """An ESPHome tag such as !extend, !remove, !secret or !lambda."""

    tag: str
    value: Any


class _Loader(yaml.SafeLoader):  # type: ignore[misc]  # PyYAML ships no stubs
    pass


def _tagged(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Tagged:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node) or None
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        assert isinstance(node, yaml.MappingNode)
        value = loader.construct_mapping(node, deep=True)
    return Tagged("!" + suffix, value)


_Loader.add_multi_constructor("!", _tagged)


def _load() -> dict[str, Any]:
    data = yaml.load(OVERLAY.read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506
    assert isinstance(data, dict)
    return data


def _walk(node: Any) -> list[Any]:
    out = [node]
    if isinstance(node, dict):
        for value in node.values():
            out += _walk(value)
    elif isinstance(node, list):
        for value in node:
            out += _walk(value)
    elif isinstance(node, Tagged):
        out += _walk(node.value)
    return out


def _ids(config: dict[str, Any]) -> list[Any]:
    return [
        item["id"]
        for key in ("globals", "number", "light", "sensor", "event", "switch", "script")
        for item in config.get(key, [])
    ]


def test_opens_with_a_plain_language_comment() -> None:
    first = OVERLAY.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# ")
    assert "unofficial" in first


def test_stock_config_is_the_release_tag() -> None:
    package = _load()["packages"]["voice_pe"]
    assert package["url"] == "https://github.com/esphome/home-assistant-voice-pe"
    assert package["ref"] == STOCK_RELEASE
    assert package["files"] == ["home-assistant-voice.yaml"]


def test_voice_kit_is_pinned_to_the_release_commit() -> None:
    (entry,) = _load()["external_components"]
    assert entry["components"] == ["voice_kit"]
    assert entry["source"]["ref"] == STOCK_COMMIT
    assert re.fullmatch(r"[0-9a-f]{40}", entry["source"]["ref"])


def test_build_command_names_the_release_that_built_the_stock_image() -> None:
    assert "esphome==2026.6.0 compile" in OVERLAY.read_text(encoding="utf-8")


def test_reaches_into_stock_by_four_ids_only() -> None:
    config = _load()
    touched = {
        item_id.value
        for item_id in _ids(config)
        if isinstance(item_id, Tagged) and item_id.tag == "!extend"
    }
    assert touched == {
        "dial",
        "button_press_event",
        "voice_assistant_leds",  # one more effect for the ring
        "control_leds_dial_touched",  # which effect a turn shows
    }
    removed_items = [
        item_id
        for item_id in _ids(config)
        if isinstance(item_id, Tagged) and item_id.tag == "!remove"
    ]
    assert removed_items == []


def test_replaces_only_the_dial_actions() -> None:
    first, second = _load()["sensor"]
    assert first["on_clockwise"] == Tagged("!remove", None)
    assert first["on_anticlockwise"] == Tagged("!remove", None)
    assert set(first) == set(second) == {"id", "on_clockwise", "on_anticlockwise"}
    for key, clockwise in (("on_clockwise", True), ("on_anticlockwise", False)):
        (action,) = second[key]
        assert action == {"script.execute": {"id": "static_dial_turn", "clockwise": clockwise}}


def test_new_ids_are_static_ones() -> None:
    new = {item_id for item_id in _ids(_load()) if isinstance(item_id, str)}
    assert new == {
        "static_dial",
        "static_dial_hold_to_tune",
        "static_dial_turn",
        "static_dial_show",
        "static_dial_station",
        "static_tuning",
    }


def test_adds_the_two_dial_event_types_and_fires_only_those() -> None:
    config = _load()
    (event,) = config["event"]
    assert event["event_types"] == ["dial_clockwise", "dial_anticlockwise"]
    fired = [
        node["event.trigger"]
        for node in _walk(config["script"])
        if isinstance(node, dict) and "event.trigger" in node
    ]
    assert len(fired) == 1
    assert fired[0]["id"] == "button_press_event"
    lam = fired[0]["event_type"]
    assert isinstance(lam, Tagged) and lam.tag == "!lambda"
    assert set(re.findall(r'"(\w+)"', lam.value)) == set(event["event_types"])


def test_boots_with_stock_behaviour() -> None:
    switches = {s["id"]: s for s in _load()["switch"]}
    assert switches["static_dial"]["name"] == "Static dial"
    assert switches["static_dial"]["restore_mode"] == "ALWAYS_OFF"
    reverse = switches["static_dial_hold_to_tune"]
    assert reverse["restore_mode"] == "RESTORE_DEFAULT_OFF"
    assert reverse["entity_category"] == "config"


def test_switch_off_runs_the_three_stock_dial_scripts() -> None:
    script = next(s for s in _load()["script"] if s["id"] == "static_dial_turn")
    (step,) = script["then"]
    stock_branch = step["if"]["else"]
    called = {
        node["script.execute"]["id"]
        for node in _walk(stock_branch)
        if isinstance(node, dict) and "script.execute" in node
    }
    assert called == {"control_volume", "control_group_volume", "control_hue"}


def test_wifi_comes_from_an_uncommitted_secrets_file() -> None:
    wifi = _load()["wifi"]
    assert wifi["ssid"] == Tagged("!secret", "wifi_ssid")
    assert wifi["password"] == Tagged("!secret", "wifi_password")
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "esphome/secrets.yaml" in ignored
    example = yaml.safe_load(
        (REPO / "esphome" / "secrets.example.yaml").read_text(encoding="utf-8")
    )
    assert set(example) == {"wifi_ssid", "wifi_password"}


def test_a_turn_that_tunes_shows_the_station_and_any_other_shows_the_volume() -> None:
    config = _load()
    (ring,) = config["light"]
    assert [effect["addressable_lambda"]["name"] for effect in ring["effects"]] == [
        "Static Station"
    ]
    removed, shown = (
        s for s in config["script"] if s["id"] == Tagged("!extend", "control_leds_dial_touched")
    )
    assert removed["then"] == Tagged("!remove", None)
    (step,) = shown["then"]
    effect = step["light.turn_on"]["effect"]
    assert isinstance(effect, Tagged) and effect.tag == "!lambda"
    assert set(re.findall(r'"([\w ]+)"', effect.value)) == {"Static Station", "Volume Display"}
    place = next(n for n in config["number"] if n["id"] == "static_dial_station")
    assert (place["min_value"], place["max_value"]) == (0, 12)  # the ring has 12 LEDs
