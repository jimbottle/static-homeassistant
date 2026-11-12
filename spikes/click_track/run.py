"""Drive the spike by itself: tune the speaker, record the room, measure, repeat.

    uv run python spikes/click_track/run.py check          # nothing audible
    uv run python spikes/click_track/run.py arm A --silent # volume zero, logs only
    uv run python spikes/click_track/run.py arm A          # 20 audible tunes
    uv run python spikes/click_track/run.py soak A         # two hours on one arm
    uv run python spikes/click_track/run.py night          # the whole spike, unattended

This machine does three jobs at once, so one clock times all of them: it
serves the click track (server.py, started here), it asks Home Assistant to
play it, and it records the room with its microphone. Each tune is measured
with the stream's lead at zero, so the number that comes out is the speaker's
own start delay: how long after the live moment the sound is heard.

The three arms are the three ways a stream can reach the speaker:

  A  the Music Assistant player, with music_assistant.play_media
  B  the speaker's own ESPHome player, through Home Assistant's ffmpeg proxy
  C  the same player with bypass_proxy, so the speaker fetches the stream itself

Everything about the house comes from the environment or an untracked
.env.spike file at the repo root, and nothing here has a default for it:

  SPIKE_SSH              ssh host of the Home Assistant machine (its SSH add-on)
  SPIKE_MA_PLAYER        entity id of the speaker's Music Assistant player
  SPIKE_ESPHOME_PLAYER   entity id of the speaker's own ESPHome player
  SPIKE_HOST             this machine's address on the home network
  SPIKE_MIC              ffmpeg avfoundation audio device index (default 0)
  SPIKE_VOLUME           volume for the audible passes, 0..1 (default 0.25)
  SPIKE_PIPELINE_LOG     optional: path on that machine of the voice pipeline's
                         run log; if set, a run refuses to start within ten
                         minutes of the last real use of the speaker

Home Assistant is called from its own machine over ssh, with the token that
machine's SSH add-on already holds. No credential is read, stored or printed
here. Nothing is installed or changed there except the speaker's volume, which
is put back, and what the speaker is playing, which is stopped at the end.

Results go to spikes/out/ (untracked): results.jsonl, one line per tune, and
the recordings, which hold the room's sound and never leave this machine.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import statistics
import subprocess
import sys
import threading
import time
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
import clicks  # noqa: E402

ROOT = HERE.parent.parent
OUT = HERE.parent / "out"
PORT = 8765
RATE = 48000  # recording sample rate
TUNE_S = 26  # long enough to hear two ten-second beeps after a slow start
REST_S = 6  # quiet between tunes, so one tune's tail is not the next one's start
TUNES = 20
SOAK_S = 2 * 3600
SOAK_SAMPLE_EVERY_S = 300
QUIET_BEFORE_S = 600  # the house rule: nothing audible within ten minutes of real use
PASS_WITHIN_S = 1.0
PASS_AT_LEAST = 18
WORST_ALLOWED_S = 2.0


def load_env() -> None:
    """Read .env.spike into the environment, without overriding what is already set."""
    path = ROOT / ".env.spike"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"run.py: set {name} (in the environment or .env.spike)")
    return value


# -- talking to Home Assistant, from its own machine ---------------------------


class House:
    """Home Assistant's API, called on its own machine so no token leaves it."""

    def __init__(self, ssh_host: str, dry_run: bool = False) -> None:
        self.ssh_host = ssh_host
        self.dry_run = dry_run

    def _run(self, remote: str, stdin: str = "") -> str:
        if self.dry_run:
            print(f"  [dry run] ssh: {remote[:100]}")
            return ""
        try:
            done = subprocess.run(  # noqa: S603
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.ssh_host, remote],  # noqa: S607
                input=stdin,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            raise RuntimeError("ssh timed out after 60 s") from err
        if done.returncode != 0:
            raise RuntimeError(f"ssh failed: {done.stderr.strip()[:300]}")
        return done.stdout

    def call(self, service: str, data: dict[str, object]) -> None:
        """Call a service, for example media_player.play_media."""
        domain, name = service.split(".", 1)
        remote = (
            'curl -sS -f -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" '
            '-H "Content-Type: application/json" --data-binary @- '
            f"http://supervisor/core/api/services/{shlex.quote(domain)}/{shlex.quote(name)}"
        )
        self._run(remote, json.dumps(data))

    def state(self, entity_id: str) -> dict[str, object]:
        """An entity's state. A failed read is an empty state, never an exception:
        one dropped connection must not end a night's run."""
        remote = (
            'curl -sS -f -H "Authorization: Bearer $SUPERVISOR_TOKEN" '
            f"http://supervisor/core/api/states/{shlex.quote(entity_id)}"
        )
        empty: dict[str, object] = {"state": "unknown", "attributes": {}}
        try:
            out = self._run(remote)
            return json.loads(out) if out else empty
        except (RuntimeError, ValueError) as err:
            return empty | {"error": str(err)[:200]}

    def clock(self) -> float | None:
        """That machine's clock, as epoch seconds, read as near to now as ssh allows."""
        # Python, not date: that machine's date prints whole seconds only.
        out = self._run('python3 -c "import time; print(time.time())"')
        return float(out) if out.strip() else None

    def seconds_since_last_use(self, log_path: str) -> float | None:
        """How long ago the voice assistant was last really used, from its run log."""
        try:
            out = self._run(f"tail -1 {shlex.quote(log_path)}")
            stamp = datetime.fromisoformat(json.loads(out)["ts"])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            return (datetime.now(UTC) - stamp).total_seconds()
        except (RuntimeError, ValueError, KeyError, TypeError):
            return None  # the caller refuses to make a sound when it cannot tell

    def can_fetch(self, url: str) -> bool:
        """Whether that machine can reach the click server on this one."""
        out = self._run(f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}' -I {shlex.quote(url)}")
        return out.strip() == "200"


# -- recording the room --------------------------------------------------------


class Recording:
    """The microphone, recorded through ffmpeg, with the time of its first sample.

    ffmpeg hands over raw samples on a pipe. The wall-clock time of the first
    sample is not something it reports, so it is worked out: each chunk
    arrives no earlier than its samples were captured, so the smallest value
    of (arrival time - samples so far / rate) over the whole recording is the
    best estimate of when sample 0 was captured. Buffering can only make a
    chunk late, never early, which is why the smallest one is taken.
    """

    def __init__(self, mic: str, seconds: float) -> None:
        self.samples = array("h")
        self.started_at = float("inf")
        self._proc = subprocess.Popen(  # noqa: S603
            [  # noqa: S607
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "avfoundation", "-i", f":{mic}",
                "-t", f"{seconds}", "-ac", "1", "-ar", str(RATE),
                "-f", "s16le", "-flush_packets", "1", "-",
            ],  # fmt: skip
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        assert self._proc.stdout is not None  # noqa: S101
        leftover = b""
        while chunk := self._proc.stdout.read1(8192):
            arrived = time.time()
            data = leftover + chunk
            whole = len(data) - len(data) % 2
            self.samples.frombytes(data[:whole])
            leftover = data[whole:]
            self.started_at = min(self.started_at, arrived - len(self.samples) / RATE)

    def finish(self) -> None:
        self._thread.join(timeout=120)
        self._proc.wait(timeout=30)
        if not self.samples:
            err = self._proc.stderr.read().decode(errors="replace") if self._proc.stderr else ""
            raise RuntimeError(f"nothing recorded; is the microphone allowed? {err[:300]}")

    def save(self, path: Path) -> None:
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(self.samples.tobytes())


# -- measuring one recording ---------------------------------------------------


def measure(samples: array[int], started_at: float, asked_at: float) -> dict[str, object]:
    """How late the clicks were heard, from one recording of one tune.

    The stream is served with no lead, so a click can only be heard late,
    never early. The ten-second beeps give the delay as a remainder of ten
    seconds. The whole tens come from the play call: the stream cannot start
    before it was asked for, so the delay is never more than the time from the
    ask to the first sound, and the largest candidate that fits is taken. A
    start so slow that this guess could be a whole ten out shows up as a first
    sound many seconds after the ask, which is recorded beside it.
    """
    peak = max((abs(s) for s in samples), default=0) / 32768
    if peak < 0.004:  # noqa: PLR2004 - quieter than this is room noise, not a click
        return {"heard": False, "peak": round(peak, 4)}
    # Marks are found relative to the loudest thing in the room, so a quiet
    # speaker a couple of metres from the microphone still counts.
    threshold = max(0.003, peak * 0.35)
    found = analyze.marks(samples, RATE, threshold)
    # Only what was heard after the speaker was asked to play belongs to this tune.
    found = [(start, length) for start, length in found if started_at + start >= asked_at]
    rows = analyze.offsets(found, started_at)
    raw_tens = [o % 10 for kind, _, o in rows if kind in ("ten", "minute")]
    # Readings either side of a multiple of ten (9.95 and 0.05) are the same
    # delay: bring them all next to the first one before taking a middle.
    tens = [raw_tens[0] + ((o - raw_tens[0] + 5) % 10 - 5) for o in raw_tens] if raw_tens else []
    ticks = [o for kind, _, o in rows if kind == "click"]
    result: dict[str, object] = {
        "heard": bool(rows),
        "peak": round(peak, 4),
        "marks": len(rows),
        "first_sound_after_s": round(rows[0][1] - asked_at, 3) if rows else None,
    }
    if not tens:
        return result | {"note": "no ten-second beep heard"}
    whole = statistics.median(tens)
    near = [o + round(whole - o) for o in ticks] + tens
    first_sound = rows[0][1] - asked_at
    middle = statistics.median(near) % 10
    # The largest delay of the form middle + 10k that the play call allows.
    lift = middle + 10 * max(0, int((first_sound + 0.25 - middle) // 10)) - statistics.median(near)
    return result | {
        "delay_s": round(statistics.median(near) + lift, 3),
        "delay_min_s": round(min(near) + lift, 3),
        "delay_max_s": round(max(near) + lift, 3),
        "clicks": len(ticks),
        "beeps": len(tens),
    }


# -- the arms ------------------------------------------------------------------


class Spike:
    def __init__(self, args: argparse.Namespace) -> None:
        self.dry_run: bool = args.dry_run
        # A dry run prints the calls and waits for nothing.
        self.wait = (lambda _seconds: None) if self.dry_run else time.sleep
        self.house = House(need("SPIKE_SSH"), self.dry_run)
        self.ma_player = need("SPIKE_MA_PLAYER")
        self.esphome_player = need("SPIKE_ESPHOME_PLAYER")
        self.url = f"http://{need('SPIKE_HOST')}:{PORT}/click.flac?lead=0"
        self.mic = os.environ.get("SPIKE_MIC", "0")
        self.volume = float(os.environ.get("SPIKE_VOLUME", "0.25"))
        self.results = OUT / "results.jsonl"
        OUT.mkdir(exist_ok=True)
        # The household's own volume, read once before anything is changed and
        # put back once at the very end. Either entity may report it; an idle
        # Music Assistant player often does not.
        self.volume_before: float | None = None
        # Which players this run has commanded. The speaker's own ESPHome
        # player is only ever touched by arms B and C, restore included.
        self.touched: set[str] = set()

    def remember_volume(self) -> None:
        """Read the speaker's volume before the first pass, or refuse to start."""
        if self.dry_run:
            return
        for entity in (self.esphome_player, self.ma_player):
            attributes = self.house.state(entity).get("attributes", {})
            level = attributes.get("volume_level") if isinstance(attributes, dict) else None
            if isinstance(level, int | float):
                self.volume_before = float(level)
                self.record_result({"kind": "volume before", "level": level, "from": "speaker"})
                return
        raise SystemExit(
            "run.py: could not read the speaker's volume, so it could not be put back; not starting"
        )

    def restore_volume(self) -> None:
        """Put the household's volume back on each player this run commanded. Three tries each."""
        if self.volume_before is None:
            return
        for entity in sorted(self.touched):
            for _attempt in range(3):
                try:
                    self.house.call(
                        "media_player.volume_set",
                        {"entity_id": entity, "volume_level": self.volume_before},
                    )
                    break
                except RuntimeError:
                    time.sleep(5)
        after = self.house.state(self.esphome_player).get("attributes", {})
        level = after.get("volume_level") if isinstance(after, dict) else None
        self.record_result(
            {"kind": "volume after", "level": level, "wanted": self.volume_before}
        )

    def player(self, arm: str) -> str:
        return self.ma_player if arm == "A" else self.esphome_player

    def play(self, arm: str) -> None:
        if arm == "A":
            self.house.call(
                "music_assistant.play_media",
                {"entity_id": self.ma_player, "media_id": self.url, "media_type": "radio"},
            )
            return
        data: dict[str, object] = {
            "entity_id": self.esphome_player,
            "media_content_id": self.url,
            "media_content_type": "music",
        }
        if arm == "C":
            data["extra"] = {"bypass_proxy": True}
        self.house.call("media_player.play_media", data)

    def stop(self, arm: str) -> None:
        self.house.call("media_player.media_stop", {"entity_id": self.player(arm)})

    def record_result(self, row: dict[str, object]) -> None:
        row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **row}
        with self.results.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row) + "\n")
        print("  " + json.dumps({k: v for k, v in row.items() if k != "at"}))

    @contextlib.contextmanager
    def volume_set(self, arm: str, level: float):  # type: ignore[no-untyped-def]
        """Set the speaker's volume for a pass, and stop it playing when the pass ends.

        The household's own volume is put back once, at the end of the whole
        run, by restore_volume().
        """
        self.touched.add(self.player(arm))
        self.house.call(
            "media_player.volume_set", {"entity_id": self.player(arm), "volume_level": level}
        )
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                self.stop(arm)

    def clocks(self) -> dict[str, object]:
        """Both clocks against true time, and against each other."""
        sntp = subprocess.run(  # noqa: S603
            ["sntp", "-t", "3", "time.apple.com"],  # noqa: S607
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        before = time.time()
        theirs = self.house.clock()
        after = time.time()
        row: dict[str, object] = {"kind": "clocks", "this_machine_sntp": sntp.stdout.strip()[:60]}
        if theirs is not None:
            row["house_minus_this_s"] = round(theirs - (before + after) / 2, 3)
            row["read_took_s"] = round(after - before, 3)
        return row

    def preflight(self, audible: bool) -> None:
        """Everything that can be known before a sound is made."""
        if self.dry_run:
            return
        if not self.house.can_fetch(self.url):
            raise SystemExit("run.py: the Home Assistant machine cannot reach the click server")
        log_path = os.environ.get("SPIKE_PIPELINE_LOG")
        if audible and log_path:
            ago = self.house.seconds_since_last_use(log_path)
            if ago is None:
                raise SystemExit(
                    "run.py: could not read when the speaker was last used; "
                    "not making a sound without knowing"
                )
            if ago < QUIET_BEFORE_S:
                raise SystemExit(
                    f"run.py: the speaker was used {ago / 60:.0f} minutes ago; "
                    "wait until ten minutes have passed"
                )

    def tune(self, arm: str, number: int, silent: bool) -> dict[str, object]:
        """One tune: start recording, ask for the stream, listen, stop."""
        row: dict[str, object] = {"kind": "tune", "arm": arm, "n": number, "silent": silent}
        listen = not self.dry_run and not silent
        recording = Recording(self.mic, TUNE_S + 2) if listen else None
        self.wait(1.0)  # the microphone is running before the speaker is asked
        asked_at = time.time()
        try:
            self.play(arm)
        except RuntimeError as err:
            row["error"] = str(err)[:200]
        row["play_call_took_s"] = round(time.time() - asked_at, 3)
        self.wait(TUNE_S / 2)
        attributes = self.house.state(self.player(arm)).get("attributes", {})
        if isinstance(attributes, dict):
            row["reported_position_s"] = attributes.get("media_position")
            row["reported_at"] = attributes.get("media_position_updated_at")
        if recording is not None:
            recording.finish()
        else:
            self.wait(TUNE_S / 2)  # a silent tune lasts as long as an audible one
        with contextlib.suppress(RuntimeError):
            self.stop(arm)
        if recording is not None and not silent:
            # A silent pass has nothing to hear; its rows are the calls, the
            # reported positions and the server's connection log.
            row |= measure(recording.samples, recording.started_at, asked_at)
            recording.save(OUT / f"arm-{arm}-{number:02d}.wav")
        self.wait(REST_S)
        return row

    def arm(self, arm: str, tunes: int, silent: bool) -> list[dict[str, object]]:
        print(f"arm {arm}: {tunes} tunes, {'silent' if silent else 'audible'}")
        self.preflight(audible=not silent)
        self.record_result(self.clocks())
        rows = []
        with self.volume_set(arm, 0.0 if silent else self.volume):
            for number in range(1, tunes + 1):
                row = self.tune(arm, number, silent)
                self.record_result(row)
                rows.append(row)
        if not silent:
            self.record_result(verdict(arm, rows))
        return rows

    def soak(self, arm: str, seconds: int) -> None:
        """One long tune, sampled every few minutes: does the delay drift?"""
        print(f"soak on arm {arm}: {seconds / 3600:.1f} h")
        self.preflight(audible=True)
        self.record_result(self.clocks())
        delays: list[float] = []
        with self.volume_set(arm, self.volume):
            asked_at = time.time()
            try:
                self.play(arm)
            except RuntimeError as err:
                self.record_result({"kind": "soak", "arm": arm, "error": str(err)[:200]})
                return
            while time.time() - asked_at < seconds:
                time.sleep(SOAK_SAMPLE_EVERY_S if not self.dry_run else 0)
                if self.dry_run:
                    break
                try:
                    recording = Recording(self.mic, TUNE_S)
                    recording.finish()
                except RuntimeError as err:
                    self.record_result({"kind": "soak", "arm": arm, "error": str(err)[:200]})
                    continue
                # A long-running stream has no fresh play call to count tens
                # from, so its delay is read as a remainder of ten seconds and
                # only its change over the two hours is judged.
                row = measure(recording.samples, recording.started_at, recording.started_at)
                state = self.house.state(self.player(arm)).get("state")
                self.record_result(
                    {"kind": "soak", "arm": arm, "after_s": round(time.time() - asked_at),
                     "player_state": state, **row}
                )  # fmt: skip
                if isinstance(row.get("delay_s"), float):
                    delays.append(float(row["delay_s"]))  # type: ignore[arg-type]
        if delays:
            drift = max(delays) - min(delays)
            self.record_result(
                {"kind": "soak verdict", "arm": arm, "samples": len(delays),
                 "delay_first_s": delays[0], "delay_last_s": delays[-1],
                 "drift_s": round(drift, 3), "passes": drift <= PASS_WITHIN_S}
            )  # fmt: skip


def verdict(arm: str, rows: list[dict[str, object]]) -> dict[str, object]:
    """An arm against the bar: the spread of the delay is what a fixed lead cannot cover."""
    delays = [float(r["delay_s"]) for r in rows if isinstance(r.get("delay_s"), float)]  # type: ignore[arg-type]
    if not delays:
        return {"kind": "verdict", "arm": arm, "measured": 0, "passes": False}
    middle = statistics.median(delays)
    off = [abs(d - middle) for d in delays]
    within = sum(1 for o in off if o <= PASS_WITHIN_S)
    return {
        "kind": "verdict",
        "arm": arm,
        "measured": len(delays),
        "of": len(rows),
        "lead_to_use_s": round(middle, 3),
        "within_1s_of_it": within,
        "worst_s": round(max(off), 3),
        "delay_min_s": min(delays),
        "delay_max_s": max(delays),
        "passes": (
            len(delays) == len(rows)
            and within >= min(PASS_AT_LEAST, len(rows))
            and max(off) <= WORST_ALLOWED_S
        ),
    }


@contextlib.contextmanager
def click_server(dry_run: bool):  # type: ignore[no-untyped-def]
    """The click server, for as long as the run lasts, kept awake with it."""
    if dry_run:
        yield
        return
    server = subprocess.Popen(  # noqa: S603
        [sys.executable, str(HERE / "server.py"), "--host", "0.0.0.0", "--port", str(PORT)],  # noqa: S104
        stdout=(OUT / "server.out").open("a"),
        stderr=subprocess.STDOUT,
    )
    awake = subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])  # noqa: S603, S607
    time.sleep(1.5)
    try:
        yield
    finally:
        server.terminate()
        awake.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the click-track spike")
    parser.add_argument("--dry-run", action="store_true", help="print the calls, make none")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="clocks, reachability and the microphone; nothing audible")
    arm = sub.add_parser("arm", help="20 tunes on one arm")
    arm.add_argument("arm", choices=["A", "B", "C"])
    arm.add_argument("--silent", action="store_true", help="volume zero: logs and positions only")
    arm.add_argument("--tunes", type=int, default=TUNES)
    soak = sub.add_parser("soak", help="two hours on one arm")
    soak.add_argument("arm", choices=["A", "B", "C"])
    soak.add_argument("--seconds", type=int, default=SOAK_S)
    night = sub.add_parser("night", help="the whole spike, unattended")
    night.add_argument("--arms", default="ABC", help="which arms to run, for example A or ABC")
    args = parser.parse_args()

    load_env()
    spike = Spike(args)
    with click_server(args.dry_run):
        if args.command != "check":
            spike.remember_volume()
        try:
            run_command(spike, args)
        finally:
            spike.restore_volume()
    print(f"results: {spike.results}")


def run_command(spike: Spike, args: argparse.Namespace) -> None:
    if args.command == "check":
        spike.preflight(audible=False)
        spike.record_result(spike.clocks())
        if not args.dry_run:
            room = Recording(spike.mic, 3)
            room.finish()
            peak = max(abs(s) for s in room.samples) / 32768
            spike.record_result({"kind": "microphone", "room_peak": round(peak, 4)})
    elif args.command == "arm":
        spike.arm(args.arm, args.tunes, args.silent)
    elif args.command == "soak":
        spike.soak(args.arm, args.seconds)
    else:
        passed: list[tuple[float, str]] = []
        for letter in args.arms:
            spike.arm(letter, TUNES, silent=True)
        for letter in args.arms:
            rows = spike.arm(letter, TUNES, silent=False)
            result = verdict(letter, rows)
            if result["passes"]:
                passed.append((float(result["worst_s"]), letter))  # type: ignore[arg-type]
        # The soak goes to the arm this house will use if it passed, else the steadiest.
        best = "A" if any(letter == "A" for _, letter in passed) else None
        if best is None and passed:
            best = min(passed)[1]
        if best is None:
            spike.record_result({"kind": "night", "note": "no arm passed; no soak run"})
        else:
            spike.soak(best, SOAK_S)


if __name__ == "__main__":
    main()


# clicks is imported so the analyser's idea of a click and this file's agree.
_ = clicks.SAMPLE_RATE
