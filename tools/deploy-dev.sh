#!/usr/bin/env bash
# Copies the integration to a development Home Assistant over SSH, into
# <config>/custom_components/static_radio/, replacing what is there. For
# development only; a release is installed through HACS.
#
# Which machine is a local setting and has no default. Set it in the
# environment, or in an untracked .env.deploy file at the repo root:
#
#   STATIC_HA_SSH=<ssh host or alias>
#   STATIC_HA_CONFIG=/config          # optional; this is the default
#
# It writes nothing else on that machine and restarts nothing. Home Assistant
# loads new Python only on a restart of Core, which takes the assistant down
# for the household: that is a separate step, and one to ask about first.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.deploy ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.deploy
  set +a
fi
HOST="${STATIC_HA_SSH:?set STATIC_HA_SSH (in the environment or .env.deploy) to the SSH host}"
CONFIG="${STATIC_HA_CONFIG:-/config}"
case "$CONFIG" in /*) ;; *) echo "deploy-dev.sh: STATIC_HA_CONFIG must be an absolute path" >&2; exit 2 ;; esac
DEST="$CONFIG/custom_components/static_radio"

VERSION="$(python3 -c 'import json;print(json.load(open("custom_components/static_radio/manifest.json"))["version"])')"
REVISION="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- custom_components || echo '+local')"

# Unpack beside the destination, then swap, so Home Assistant never sees half
# a copy. COPYFILE_DISABLE keeps macOS's ._ files out of the archive.
COPYFILE_DISABLE=1 tar -C custom_components --exclude '__pycache__' -czf - static_radio |
  ssh "$HOST" "set -e
    mkdir -p '$CONFIG/custom_components'
    rm -rf '$DEST.new'
    mkdir '$DEST.new'
    tar -xzf - -C '$DEST.new' --strip-components 1
    rm -rf '$DEST.old'
    if [ -d '$DEST' ]; then mv '$DEST' '$DEST.old'; fi
    mv '$DEST.new' '$DEST'
    rm -rf '$DEST.old'
    ls '$DEST' | wc -l"

echo "deployed static_radio $VERSION ($REVISION) to $DEST"
echo "Home Assistant loads it on the next Core restart. Ask before restarting."
