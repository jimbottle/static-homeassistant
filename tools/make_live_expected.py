"""Regenerate tests/fixtures/live-expected.*.json from Static's own code.

The timeline tests compare schedule.py with two independent answers, saved by
this script so the tests run offline:

- the reference player: t/live_test.pl in a clone of static-squeezebox
- the liveNow() function published in Static's developer guide, run as written

Usage (needs perl and node; neither is needed to run the tests):

    python tools/make_live_expected.py \
        --schedule tests/fixtures/schedule-wear.2026-11-11.json \
        --reference /path/to/static-squeezebox \
        --guide /path/to/developers.md

The timestamps are chosen here with plain arithmetic on the raw file, not
with schedule.py, so the port is not checked against itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

GAP_MS = 1500


def timestamps(raw: dict[str, Any], station: dict[str, Any]) -> list[tuple[str, int]]:
    """The instants to check on one station, each with the case it covers."""
    epoch = int(datetime.fromisoformat(raw["daily_epoch_utc"]).timestamp() * 1000)
    eps = [e for e in station["episodes"] if e["durationInSeconds"] > 0]
    loop = station["loop_duration_seconds"] * 1000 + GAP_MS * len(eps)
    cases = [
        ("before the epoch", epoch - 12_345_678),
        ("inside the first 1.5 s gap", epoch + eps[0]["durationInSeconds"] * 1000 + 700),
        ("one ms before the loop wraps", epoch + loop - 1),
        ("at the loop wrap", epoch + loop),
        ("one ms after the loop wraps", epoch + loop + 1),
        ("mid-loop, three loops on", epoch + 3 * loop + 5_000_000),
        ("mid-episode", epoch + 40_000_000),
    ]
    start = 0
    for e in eps:
        if e.get("isBulletin"):
            cases.append(("past 30 s of a bulletin", epoch + start + 30_500))
            cases.append(("29 s into a bulletin", epoch + start + 29_000))
            break
        start += e["durationInSeconds"] * 1000 + GAP_MS
    return cases


def run_perl(reference: Path, schedule: Path, t_ms: int) -> dict[str, dict[str, Any]]:
    """t/live_test.pl prints station|audioUrl|offset_seconds|is_bulletin."""
    out = subprocess.run(  # noqa: S603
        ["perl", "t/live_test.pl", str(schedule.resolve()), str(t_ms)],  # noqa: S607
        cwd=reference,
        env={**os.environ, "T": str(reference / "t")},
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = {}
    for line in out.splitlines():
        station, rest = line.split("|", 1)
        url, offset_s, bulletin = rest.rsplit("|", 2)
        rows[station] = {"audioUrl": url, "offsetS": int(offset_s), "bulletin": int(bulletin)}
    return rows


def run_js(guide: Path, schedule: Path, asks: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Run the guide's liveNow(), exactly as published, on every (station, t)."""
    text = guide.read_text(encoding="utf-8")
    blocks = re.findall(r"```js\n(.*?)```", text, flags=re.DOTALL)
    source = next(b for b in blocks if "function liveNow(" in b)
    # Keep the constants and the function; drop the sample call that follows.
    source = source.split("// With the schedule from above:")[0]
    script = (
        source
        + """
const fs = require("fs");
const schedule = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const asks = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = asks.map(([station, t]) => {
  const l = liveNow(schedule, station, t);
  return { episodeId: l.episode.episodeId, audioUrl: l.episode.audioUrl,
           offsetMs: l.offsetMs, inGap: l.inGap, audioMs: l.audioMs,
           startedAtMs: l.startedAtMs, nextAtMs: l.nextAtMs };
});
console.log(JSON.stringify(out));
"""
    )
    with tempfile.TemporaryDirectory() as tmp:
        js = Path(tmp) / "live.js"
        js.write_text(script, encoding="utf-8")
        asks_file = Path(tmp) / "asks.json"
        asks_file.write_text(json.dumps(asks), encoding="utf-8")
        out = subprocess.run(  # noqa: S603
            ["node", str(js), str(schedule.resolve()), str(asks_file)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    result: list[dict[str, Any]] = json.loads(out)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--guide", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.schedule.read_text(encoding="utf-8"))
    asks: list[tuple[str, int]] = []
    names: list[str] = []
    for station_id, station in raw["categories"].items():
        for name, t_ms in timestamps(raw, station):
            asks.append((station_id, t_ms))
            names.append(name)

    js = run_js(args.guide, args.schedule, asks)
    rows = []
    for (station_id, t_ms), name, js_row in zip(asks, names, js, strict=True):
        perl_row = run_perl(args.reference, args.schedule, t_ms)[station_id]
        rows.append(
            {"station": station_id, "case": name, "t": t_ms, "perl": perl_row, "js": js_row}
        )

    stem = args.schedule.name.removeprefix("schedule-wear.").removesuffix(".json")
    target = args.schedule.parent / f"live-expected.{stem}.json"
    target.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    print(f"{target}: {len(rows)} cases across {len(raw['categories'])} stations")


if __name__ == "__main__":
    main()
