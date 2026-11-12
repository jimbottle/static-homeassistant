#!/usr/bin/env bash
# Builds the release zip: dist/static_radio-<version>.zip, holding
# custom_components/static_radio/ and nothing else, so it can be unpacked
# straight into a Home Assistant config directory. release.yml runs this on a
# version tag and attaches what it finds in dist/; CI's build job runs it on
# every change so a broken package is caught before a release.
#
# The version comes from the integration's manifest.json, the one place it is
# kept. The zip is the same byte for byte for the same files: entries are
# sorted and carry a fixed date.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path("custom_components/static_radio")
# Only what Home Assistant loads. Anything else in the folder stops the build,
# so nothing gets into a release by being left lying there.
ALLOWED = {".py", ".json", ".yaml", ".png", ".svg"}
FIXED_DATE = (2026, 1, 1, 0, 0, 0)

version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
files = sorted(
    p for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts
)
strays = [str(p) for p in files if p.suffix not in ALLOWED]
if strays:
    sys.exit("dist.sh: not packaging unexpected files: " + ", ".join(strays))
if not files:
    sys.exit("dist.sh: nothing to package")

out = pathlib.Path("dist") / f"static_radio-{version}.zip"
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        info = zipfile.ZipInfo(path.as_posix(), FIXED_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, path.read_bytes())

print(f"{out}: {len(files)} files, {out.stat().st_size} bytes")
PY
