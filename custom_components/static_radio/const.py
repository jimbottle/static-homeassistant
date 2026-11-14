"""Names shared by the Home Assistant side of the integration."""

from __future__ import annotations

DOMAIN = "static_radio"

# What a listener sees in Home Assistant. "unofficial" stays in it: Static
# agreed to this build on that condition.
TITLE = "Static Radio (unofficial)"

# Credited and linked wherever the integration introduces itself.
STATIC_URL = "https://staticradio.app"

# Static's guide for developers: the authority on how a player must behave.
DEVELOPER_GUIDE_URL = "https://staticradio.app/developers"

# Options, set from the integration's Configure screen.
CONF_SPEAKERS = "speakers"  # media player entity ids that can be tuned to Static
CONF_LEADS_MS = "leads_ms"  # entity id -> how far ahead of live to start, in ms
CONF_NOTIFY_SERVICE = "notify_service"  # the notify action a Support link is sent with
CONF_ANNOUNCE = "announce"  # whether a stream opens with the station's name, spoken

# How far ahead of the live moment a stream starts, to cover the time a
# speaker takes to make sound. Static's reference player uses 1.0 s. Measured
# here on a Voice Preview Edition: 1.33 s through Music Assistant (20 tunes
# within 0.08 s of each other), 0.28 s on its own ESPHome player.
DEFAULT_LEAD_MS = 1300
LEAD_MAX_MS = 5000
