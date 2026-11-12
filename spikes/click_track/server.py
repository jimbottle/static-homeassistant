"""Serve the click track the way the real station stream will be served.

Throwaway measurement code for the Phase 0 spike. Nothing here is reused in
the integration, and nothing of Static's is in it.

Each connection gets an endless FLAC stream that:

- starts at the click for the moment of the request plus a lead (?lead=1.0),
  so a player that starts sounding `lead` seconds after asking is on time;
- reports no duration, so a player treats it as live radio;
- is produced as fast as the reader takes it, never paced to the clock, so a
  player that wants 3 s buffered before starting gets it at once.

Every connection is logged as JSON lines (when it opened, what asked, how many
bytes and stream-seconds it took, when it closed). That log shows probes,
read-ahead and reconnects without a microphone. It holds the addresses of
whatever connected, so it is written under spikes/out/, which is not tracked.

Run on a machine the player can reach:

    uv run python spikes/click_track/server.py --host 0.0.0.0 --port 8765

Needs ffmpeg on PATH (it does the FLAC encoding).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import clicks  # noqa: E402

CHUNK_SAMPLES = clicks.SAMPLE_RATE // 10  # 100 ms of audio per write
LOG_LOCK = threading.Lock()
LOG_FILE: Path | None = None
NEXT_ID = iter(range(1, 1_000_000))


def log(**fields: object) -> None:
    line = json.dumps({"at": round(time.time(), 3), **fields})
    with LOG_LOCK:
        print(line, flush=True)
        if LOG_FILE is not None:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # the body ends when the connection does

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # replaced by the JSON log

    def _common(self) -> tuple[int, float]:
        conn = next(NEXT_ID)
        query = parse_qs(urlparse(self.path).query)
        lead = float(query.get("lead", ["0"])[0])
        log(
            event="open",
            conn=conn,
            method=self.command,
            path=self.path,
            peer=self.client_address[0],
            agent=self.headers.get("User-Agent", ""),
            range=self.headers.get("Range"),
            icy=self.headers.get("Icy-MetaData"),
            lead=lead,
        )
        self.send_response(200)
        self.send_header("Content-Type", "audio/flac")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        return conn, lead

    def do_HEAD(self) -> None:
        conn, _ = self._common()
        log(event="close", conn=conn, bytes=0, stream_s=0.0, open_s=0.0, why="HEAD")

    def do_GET(self) -> None:
        opened = time.time()
        conn, lead = self._common()
        start_time = opened + lead  # sample 0 sounds at this wall-clock instant
        encoder = subprocess.Popen(  # noqa: S603
            [  # noqa: S607
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(clicks.SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
                "-c:a", "flac", "-compression_level", "0", "-f", "flac", "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        assert encoder.stdin and encoder.stdout
        fed = {"samples": 0}

        def feed() -> None:
            try:
                while True:
                    encoder.stdin.write(clicks.pcm(start_time, fed["samples"], CHUNK_SAMPLES))
                    fed["samples"] += CHUNK_SAMPLES
            except (BrokenPipeError, ValueError, OSError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        sent, why, last_note = 0, "reader closed", opened
        try:
            while True:
                data = encoder.stdout.read1(65536)
                if not data:
                    why = "encoder ended"
                    break
                self.wfile.write(data)
                sent += len(data)
                now = time.time()
                if now - last_note >= 5:
                    last_note = now
                    log(event="progress", conn=conn, bytes=sent,
                        fed_s=round(fed["samples"] / clicks.SAMPLE_RATE, 1),
                        open_s=round(now - opened, 1))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            encoder.kill()
            encoder.wait()
            log(event="close", conn=conn, bytes=sent,
                fed_s=round(fed["samples"] / clicks.SAMPLE_RATE, 1),
                open_s=round(time.time() - opened, 1), why=why)


def main() -> None:
    global LOG_FILE  # noqa: PLW0603
    parser = argparse.ArgumentParser(description="Click-track server for the Phase 0 spike")
    parser.add_argument("--host", required=True, help="address to listen on; there is no default")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", type=Path, default=Path(__file__).parent.parent / "out" / "connections.jsonl")
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE = args.log
    log(event="listening", host=args.host, port=args.port)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
