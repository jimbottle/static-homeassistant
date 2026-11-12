"""Which station a request means, and what to say when the station changes.

Static's dial is the list of stations in the schedule, in sort_index order.
This file answers three questions for anything that tunes (voice, a
dashboard, a dial): which station is this name, which station is one step
along the dial, and what is said aloud when the station changes.

Two rules from the owner are kept here:

- A station is asked for by Static's own name for it and by nothing else. The
  names come from the schedule; this file invents none.
- Whenever the station changes, the reply says which station it now is, using
  Static's name exactly as Static writes it.

It imports nothing from Home Assistant and plays nothing. The part that starts
a station playing is not built yet.
"""

from __future__ import annotations

from collections.abc import Sequence

from .schedule import Station


def find_station(stations: Sequence[Station], said: str) -> Station | None:
    """The station with this name. Only Static's own names match.

    Those are the station's display name, the same name as it appears on the
    dial in capitals, and its id. Case is ignored, since speech has none.
    """
    wanted = said.strip().casefold()
    for station in stations:
        names = (station.display_name, station.u_tag, station.station_id)
        if wanted in (name.casefold() for name in names):
            return station
    return None


def step(stations: Sequence[Station], current_id: str | None, by: int) -> Station | None:
    """The station `by` places along the dial from the current one.

    The dial has two ends and does not wrap: stepping past an end gives None,
    and the caller says so. With nothing tuned yet, or a station that has left
    the dial, any step lands on the first station.
    """
    if not stations:
        return None
    index = next((i for i, s in enumerate(stations) if s.station_id == current_id), None)
    if index is None:
        return stations[0]
    target = index + by
    if target < 0 or target >= len(stations):
        return None
    return stations[target]


def tuned_reply(station: Station) -> str:
    """Said whenever the station changes: which station it is now."""
    return f"Playing Static {station.display_name}."


def end_of_dial_reply(station: Station, by: int) -> str:
    """Said when a step would run off the end of the dial. The station stays as it is."""
    end = "last" if by > 0 else "first"
    return f"Static {station.display_name} is the {end} station on the dial."


def unknown_station_reply(stations: Sequence[Station]) -> str:
    """Said when the name asked for is not one of Static's, listing the ones that are."""
    names = [station.display_name for station in stations]
    if not names:
        return "Static has no stations on the dial right now."
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Static's stations are {listed}."
