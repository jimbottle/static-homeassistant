#!/usr/bin/env bash
# Generates the MP3 files the seek tests read: tones and noise made by ffmpeg,
# never episode audio. Each covers one way an MP3 says where second N is.
# Then asks Static's reference (t/mp3seek_test.pl in a clone of
# static-squeezebox) what it reads from each file and which byte it would seek
# to, and saves the answers so the tests run offline.
#
# Usage: tools/make_mp3_fixtures.sh /path/to/static-squeezebox
# Needs ffmpeg with libmp3lame, and perl. Neither is needed to run the tests.
set -euo pipefail
REF="${1:?usage: $0 /path/to/static-squeezebox}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT=tests/fixtures/mp3
mkdir -p "$OUT"

ff() { ffmpeg -hide_banner -loglevel error -y -fflags +bitexact "$@"; }
TONE=(-f lavfi -i "sine=frequency=440:duration=12")
# Noise that swells and fades, so a VBR encoder really varies its bitrate.
SWELL=(-f lavfi -i "aevalsrc=random(0)*pow(sin(PI*t/4)\,2):d=24:s=44100")
MP3=(-flags +bitexact -c:a libmp3lame -map_metadata -1)

# Constant bitrate, no Xing or Info header: the measured byte rate decides.
ff "${TONE[@]}" -ac 1 -ar 44100 "${MP3[@]}" -b:a 32k -write_xing 0 -id3v2_version 0 "$OUT/cbr-bare.mp3"
# Constant bitrate with an Info header: its frame count decides.
ff "${TONE[@]}" -ac 2 -ar 44100 "${MP3[@]}" -b:a 64k -id3v2_version 0 "$OUT/cbr-info.mp3"
# Variable bitrate with a Xing table of contents: the table decides.
ff "${SWELL[@]}" -ac 1 "${MP3[@]}" -q:a 6 -id3v2_version 0 "$OUT/vbr-xing.mp3"
# MPEG2 (22.05 kHz), mono: the other frame size and side-info length.
ff "${TONE[@]}" -ac 1 -ar 22050 "${MP3[@]}" -b:a 24k -write_xing 0 -id3v2_version 0 "$OUT/mpeg2-bare.mp3"
# A tag bigger than the first read, as cover art makes: the frames have to be
# fetched with a second request.
ff "${TONE[@]}" -ac 1 -ar 44100 -c:a libmp3lame -b:a 32k -write_xing 0 -id3v2_version 3 \
  -metadata "comment=$(head -c 40000 /dev/zero | tr '\0' 'x')" "$OUT/big-tag.mp3"

python3 - "$REF" "$OUT" <<'PY'
import json, os, re, subprocess, sys
ref, out = sys.argv[1], sys.argv[2]
rows = []
for name in sorted(f for f in os.listdir(out) if f.endswith(".mp3")):
    path = os.path.join(out, name)
    total = os.path.getsize(path)
    for seconds, duration in [(0, None), (0.5, None), (3, None), (7.25, None), (11, None),
                              (23.9, None), (500, None), (7.25, 24), (7.25, 12)]:
        args = ["perl", os.path.join(ref, "t/mp3seek_test.pl"), path, str(total), str(seconds)]
        if duration is not None:
            args.append(str(duration))
        text = subprocess.run(args, check=True, capture_output=True, text=True).stdout
        m = re.search(r"tag (\d+) start (\d+) kbps (\d+) srate (\d+) walked (\d+) "
                      r"byte_rate (\S+) vbr (\d) toc (\d) frames (\S+)", text)
        byte = int(re.search(r"byte for [\d.]+ s: (\d+)", text).group(1))
        rows.append({
            "file": name, "total": total, "seconds": seconds, "duration": duration,
            "tag": int(m[1]), "start": int(m[2]), "kbps": int(m[3]), "srate": int(m[4]),
            "walked": int(m[5]), "byte_rate": None if m[6] == "-" else m[6],
            "vbr": bool(int(m[7])), "toc": bool(int(m[8])),
            "frames": None if m[9] == "-" else int(m[9]), "byte": byte,
        })
with open(os.path.join(out, "seek-expected.json"), "w") as f:
    json.dump(rows, f, indent=1); f.write("\n")
print(f"{len(rows)} reference answers for {len(set(r['file'] for r in rows))} files")
PY
ls -la "$OUT"
