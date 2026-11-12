"""Keeps the pure-Python tests runnable where Home Assistant is not installed.

The timeline, MP3 seek and API modules import nothing from Home Assistant, but
they live in the integration's package, whose __init__.py does. Without Home
Assistant, the package is registered here as an empty shell, so
`custom_components.static_radio.schedule` loads under its one real name and
__init__.py is never run. The tests in tests/ha/ need the real thing and are
left out. scripts/verify.sh proves this by running the pure tests in an
environment that has no Home Assistant in it.
"""

import importlib.util
import sys
import types
from pathlib import Path

collect_ignore: list[str] = []

if importlib.util.find_spec("homeassistant") is None:
    collect_ignore.append("ha")
    _dir = Path(__file__).resolve().parent.parent / "custom_components" / "static_radio"
    _shell = types.ModuleType("custom_components.static_radio")
    _shell.__path__ = [str(_dir)]
    sys.modules["custom_components.static_radio"] = _shell
