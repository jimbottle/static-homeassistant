# Spikes

Throwaway measurement code. Nothing under this folder is reused in the
integration, and nothing here fetches, plays or converts Static's audio.

## click_track: can a speaker hold the live moment?

Static's stations are live: everyone hears the same moment. A speaker starts
sounding some time after it is handed a stream. If that delay is steady, a
fixed lead covers it; if it varies from one tune to the next by more than the
tolerance, nothing does. This spike measures the delay and its spread before
any real stream code is written.

- `clicks.py` defines the sound: a click on every wall-clock second, a longer
  beep on every tenth second and every minute.
- `server.py` serves it as an endless FLAC stream, the way the real station
  stream will be served: each connection starts at the live click plus a lead,
  reports no duration, and is sent as fast as it is read. It logs every
  connection.
- `analyze.py` reads a recording and prints how far what was heard is from the
  clock.

- `run.py` drives the whole thing unattended: it starts the server, asks Home
  Assistant to play the stream, records the room, measures each tune and
  writes one line per tune to `spikes/out/results.jsonl`.

### Running the whole spike

One machine does everything, so it has to sit within a couple of metres of the
speaker, on the home network, plugged in and awake, for about three hours. The
room should be quiet and nobody should use the speaker meanwhile.

Put the house's details in an untracked `.env.spike` at the repo root (the
names are listed at the top of `run.py`; none has a default), then:

```bash
uv run python spikes/click_track/run.py check   # silent: clocks, reachability, microphone
uv run python spikes/click_track/run.py night   # everything, unattended
```

`night` runs a silent pass on each arm, then 20 audible tunes on each, then two
hours on the arm that passed. A single arm or the two-hour run can be run alone
(`arm A`, `arm A --silent`, `soak A`), and `--dry-run` prints the calls without
making any. The speaker's volume is set for each pass and put back afterwards.

An arm passes when every tune is measured, at least 18 of 20 start within 1 s
of the arm's usual delay, and none is more than 2 s from it. The usual delay
is the lead a real stream would use; it is the spread that a lead cannot fix.

### Running the pieces by hand

```bash
# 1. On a machine the player can reach, with an accurate clock:
uv run python spikes/click_track/server.py --host 0.0.0.0 --port 8765

# 2. Tune the player to http://<that machine>:8765/click.flac?lead=1.0

# 3. Record the speaker on the same machine into spikes/out/, noting the
#    wall-clock time of the first sample, for at least 12 s (one ten-second
#    beep), then:
uv run python spikes/click_track/analyze.py spikes/out/recording.wav --started-at <epoch seconds>
```

Recordings pick up the room, so they stay in `spikes/out/`, which is ignored,
as is any audio file anywhere under `spikes/`.

One machine serves and records, so one clock times both. Record that clock's
offset from true time before each run; it is the floor on what the measurement
can show.

### What the log shows without a microphone

`spikes/out/connections.jsonl` (not tracked) has one line when a connection
opens, one every 5 s, and one when it closes, with the bytes sent and the
seconds of stream generated. From it: how many connections one tune makes
(probes), how long each stays open, and whether a resume opens a new
connection or continues an old one.

It does not show how far ahead a player reads. `fed_s` counts audio handed to
the encoder, and `bytes` counts bytes the operating system accepted; the pipes
and socket buffers between them hold many seconds of a stream that is mostly
silence and compresses to almost nothing. Read-ahead has to come from the
recording, not from this log.

### Self-check

Served and fetched on one machine with curl, decoded and analysed: the offset
read back was -0.010 s (the time between curl starting and the server
accepting), with 3 s of connection taking 7,276 s of stream, which confirms
the stream is not paced. `ffprobe` reports the duration as N/A.
