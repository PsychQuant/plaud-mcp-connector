#!/usr/bin/env python3
r"""Parse a flat transcript string scraped from the Plaud `.transcript-module`
DOM node into SRT — the third-layer fallback when both the Performance API and
the file-detail API path fail to yield a `trans_result` URL.

Why this exists
---------------
Plaud moved the API bearer token out of `localStorage.pld_tokenstr` into an
in-memory store (Vuex/Pinia) that JS can't read — so `/file/detail/{hash}`
returns `{"status":-3900,"msg":"invalid auth header"}` and the Performance API
never sees a `trans_result` request because the transcript renders straight
from IndexedDB cache. The rendered transcript text IS still in the DOM though,
so we scrape `.transcript-module` textContent and reconstruct SRT here.

Input shape
-----------
The DOM textContent is flat — segments are concatenated with no delimiters
other than the inline timestamp + speaker label that prefixes each one:

    00:00:49Speaker 1我會儘快趕回來啦。...00:00:56Speaker 2不要緊...

So the segment boundary is the regex `(\d{2}:\d{2}:\d{2})(Speaker \d+)`. End
time of each segment = start time of the next (last segment gets +5s).

Usage
-----
    safari-browser js "var m=document.querySelector('.transcript-module'); m?m.textContent.trim():''" --url <hash> \
      | python3 dom_transcript_to_srt.py "/path/out.srt"

    # or from a file:
    python3 dom_transcript_to_srt.py "/path/out.srt" --input /tmp/flat.txt
"""
import argparse
import re
import sys

# Boundary marker: HH:MM:SS immediately followed by "Speaker N" (Plaud renders
# them glued together in textContent). Tolerate optional whitespace between.
SEG_RE = re.compile(r"(\d{2}:\d{2}:\d{2})\s*(Speaker\s*\d+)")

# UI chrome that leaks into textContent at the tail of the transcript pane.
# Plaud's feedback widget ("satisfied / unsatisfied") and mind-map labels.
TAIL_NOISE_RE = re.compile(r"\s*(滿意\s*不滿意|心智圖|Satisfied\s*Unsatisfied)\s*$")


def parse_segments(flat: str):
    """Return list of (start "HH:MM:SS", speaker, text) tuples."""
    flat = TAIL_NOISE_RE.sub("", flat.strip())
    parts = SEG_RE.split(flat)
    # parts = [preamble, ts1, spk1, text1, ts2, spk2, text2, ...]
    segs = []
    i = 1
    while i + 1 < len(parts):
        ts = parts[i].strip()
        spk = re.sub(r"\s+", " ", parts[i + 1]).strip()
        txt = (parts[i + 2] if i + 2 < len(parts) else "").strip()
        if txt:  # drop empty segments (e.g. a stray timestamp with no words)
            segs.append((ts, spk, txt))
        i += 3
    return segs


def _plus_seconds(hhmmss: str, secs: int) -> str:
    h, m, s = map(int, hhmmss.split(":"))
    s += secs
    m += s // 60
    s %= 60
    h += m // 60
    m %= 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def to_srt(segs) -> str:
    lines = []
    for n, (ts, spk, txt) in enumerate(segs, 1):
        end = segs[n][0] if n < len(segs) else _plus_seconds(ts, 5)
        lines.append(f"{n}\n{ts},000 --> {end},000\n[{spk}] {txt}\n")
    return "\n".join(lines) + "\n" if lines else ""


def main():
    ap = argparse.ArgumentParser(description="Plaud DOM transcript → SRT fallback")
    ap.add_argument("output", help="output .srt path")
    ap.add_argument("--input", help="flat transcript file (default: stdin)")
    args = ap.parse_args()

    flat = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    flat = flat.strip()
    if not flat or flat in ("NONE", "NO_MODULE", "still running"):
        print("✗ no transcript text on stdin/input — is the transcript tab loaded?", file=sys.stderr)
        sys.exit(1)

    segs = parse_segments(flat)
    if not segs:
        # No timestamp markers — dump the raw text as a single cue so the
        # content is at least preserved rather than silently lost.
        print("⚠ no HH:MM:SS+Speaker markers found; writing single-cue fallback", file=sys.stderr)
        open(args.output, "w", encoding="utf-8").write(
            f"1\n00:00:00,000 --> 00:00:05,000\n{flat}\n"
        )
        sys.exit(0)

    open(args.output, "w", encoding="utf-8").write(to_srt(segs))
    print(f"✓ {len(segs)} segments → {args.output}")


if __name__ == "__main__":
    main()
