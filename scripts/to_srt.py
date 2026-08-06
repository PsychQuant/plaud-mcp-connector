#!/usr/bin/env python3
"""Turn a cached transcript into SubRip (.srt) subtitles.

The official Plaud MCP returns transcript text and nothing else — there is no
subtitle export anywhere in its seven tools, and none in the CLI. Anyone cutting
video, subtitling a lecture, or captioning a recorded class has to build the
timing themselves. This does that, from the cache `plaud-index` already wrote, so
it needs no network, no auth, and no re-fetch.

Input is the cache's one-segment-per-line form:

    [00:12:03] Speaker 1: and then we moved the deadline

Timestamps accept H:MM:SS, MM:SS, and an optional .mmm / ,mmm fraction.

Usage:
    to_srt.py <recording-id> [-o out.srt] [--no-speaker] [--tail-seconds N]
    to_srt.py --file <path.md> [-o out.srt]
"""

import argparse
import os
import pathlib
import re
import sys

CACHE_DIR = pathlib.Path(
    os.environ.get("PLAUD_CACHE_DIR", pathlib.Path.home() / ".plaud-connector" / "cache")
)

# [00:12:03] Speaker 1: text   /   [12:03.500] text
SEGMENT = re.compile(
    r"^\[\s*(?P<ts>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\s*\]\s*"
    r"(?:(?P<speaker>[^:\[\]]{1,60}?)\s*:\s*)?"
    r"(?P<text>.*\S)\s*$"
)


def parse_timestamp(raw: str) -> float:
    """'1:02:03.250' / '02:03' → seconds. Raises ValueError on anything else."""
    stamp = raw.replace(",", ".")
    parts = stamp.split(":")
    if len(parts) == 2:                      # MM:SS — the common short form
        parts = ["0"] + parts
    if len(parts) != 3:
        raise ValueError(f"unrecognised timestamp: {raw!r}")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(seconds: float) -> str:
    """Seconds → SRT's HH:MM:SS,mmm. Negative clamps to zero rather than wrapping."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text if end == -1 else text[end + len("\n---\n"):]


def parse_segments(body: str) -> list[dict]:
    """Lines that look like segments become cues; everything else is dropped.

    Dropping silently is deliberate: cached files carry a `Subject:`-style header
    and blank lines, and warning about each one would bury the real problem —
    which is a file with *no* parseable segments at all (handled by the caller).
    """
    out: list[dict] = []
    for line in body.splitlines():
        m = SEGMENT.match(line)
        if not m:
            continue
        try:
            start = parse_timestamp(m.group("ts"))
        except ValueError:
            continue
        out.append({
            "start": start,
            "speaker": (m.group("speaker") or "").strip(),
            "text": m.group("text").strip(),
        })
    return out


def build_cues(segments: list[dict], *, show_speaker: bool = True,
               tail_seconds: float = 4.0, min_duration: float = 0.5) -> list[dict]:
    """Give every segment an end time.

    A cue ends when the next one starts — that is the only honest signal the
    transcript carries. The last cue has nothing after it, so it gets
    `tail_seconds`, which is a guess and labelled as one.

    Out-of-order or duplicate timestamps would otherwise produce a negative-length
    cue that players reject outright; those get `min_duration` instead so the line
    still shows rather than silently vanishing.
    """
    cues = []
    for i, seg in enumerate(segments):
        if i + 1 < len(segments):
            end = segments[i + 1]["start"]
            if end <= seg["start"]:
                end = seg["start"] + min_duration
        else:
            end = seg["start"] + tail_seconds
        text = seg["text"]
        if show_speaker and seg["speaker"]:
            text = f"{seg['speaker']}: {text}"
        cues.append({"start": seg["start"], "end": end, "text": text})
    return cues


def render_srt(cues: list[dict]) -> str:
    blocks = []
    for n, cue in enumerate(cues, start=1):
        blocks.append(
            f"{n}\n"
            f"{format_timestamp(cue['start'])} --> {format_timestamp(cue['end'])}\n"
            f"{cue['text']}\n"
        )
    return "\n".join(blocks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("id", nargs="?", help="recording id in the cache")
    src.add_argument("--file", help="read a transcript markdown file directly")
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    ap.add_argument("--no-speaker", action="store_true",
                    help="omit the speaker prefix from cue text")
    ap.add_argument("--tail-seconds", type=float, default=4.0,
                    help="duration for the final cue, which has no successor (default 4)")
    args = ap.parse_args()

    if args.file:
        path = pathlib.Path(args.file)
    else:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.id or ""):
            sys.exit(f"error: refusing unsafe recording id: {args.id!r}")
        path = CACHE_DIR / f"{args.id}.md"

    if not path.is_file():
        sys.exit(f"error: {path} not found — run the plaud-index skill first")

    raw = path.read_text()
    segments = parse_segments(strip_frontmatter(raw))
    if not segments:
        sys.exit(
            f"error: no timestamped segments in {path}.\n"
            f"Expected lines like '[00:12:03] Speaker 1: ...'. A transcript cached "
            f"without timestamps cannot become subtitles."
        )

    # A truncated cache would yield subtitles that just stop mid-recording, with
    # nothing in the .srt to say why. Say it here instead.
    if "complete: false" in raw[:400]:
        print(f"⚠ {path.name} is marked incomplete — these subtitles cover only the "
              f"part that was fetched. Re-run plaud-index first.", file=sys.stderr)

    srt = render_srt(build_cues(segments,
                                show_speaker=not args.no_speaker,
                                tail_seconds=args.tail_seconds))
    if args.output:
        pathlib.Path(args.output).write_text(srt)
        print(f"wrote {len(segments)} cues → {args.output}")
    else:
        sys.stdout.write(srt)


if __name__ == "__main__":
    main()
