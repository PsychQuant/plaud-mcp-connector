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
import importlib.util
import os
import pathlib
import re
import sys

# These scripts are run directly (`python3 scripts/to_srt.py`), never imported as
# a package, so a plain `import config` would resolve against the caller's cwd —
# or not at all. Load it by path, from beside this file.
_cfg_spec = importlib.util.spec_from_file_location(
    "plaud_config", pathlib.Path(__file__).resolve().parent / "config.py"
)
config = importlib.util.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(config)

CACHE_DIR = pathlib.Path(
    os.environ.get("PLAUD_CACHE_DIR", pathlib.Path.home() / ".plaud-connector" / "cache")
)

# [00:12:03] Speaker 1: text   /   [12:03.500] text   /   [446:12] text
#
# The minute field takes up to FOUR digits, because Plaud's CLI writes TOTAL
# minutes: a recording passes 99 minutes and the field becomes `100:05`, then
# `446:12` at seven hours. `\d{1,2}` here cost #50 — 86% of a 7.4-hour
# transcript, dropped silently, into an SRT that was syntactically perfect.
#
# `parse_timestamp` never had the problem: it does `int(minutes) * 60` with no
# width assumption, and the END of a ranged line goes straight there without
# passing through this pattern — which is why ends already accepted three
# digits while starts did not. This was the only gate.
#
# Bounded at four, not `\d+`. Total-minute stamps are bounded by how long a
# recording can be (`9999:59` is about seven days); an unbounded class would
# swap a silent-drop bug for a silent-accept one at the same line.
_STAMP = r"\d{1,4}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"

# Two bracket forms, because two producers write into this cache and they do
# not agree (#40): Plaud's MCP path emits `[start]`, its CLI emits
# `[start - end]`. The end group is optional so the point form parses exactly
# as it always did — `end` simply comes back None.
#
# The end is matched loosely (`[^\]]*`) rather than as another timestamp, so a
# malformed end costs the timing and not the line. Requiring a well-formed end
# here would make the whole segment stop matching, and losing somebody's words
# over a timing detail is the worse trade.
SEGMENT = re.compile(
    rf"^\[\s*(?P<ts>{_STAMP})\s*(?:-\s*(?P<end>[^\]]*?)\s*)?\]\s*"
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
        raw_end = m.group("end")
        end: float | None = None
        if raw_end:
            try:
                end = parse_timestamp(raw_end)
            except ValueError:
                end = None      # keep the words, lose only the timing
        out.append({
            "start": start,
            "end": end,
            "speaker": (m.group("speaker") or "").strip(),
            "text": m.group("text").strip(),
        })
    return out


def build_cues(segments: list[dict], *, show_speaker: bool = True,
               tail_seconds: float = 4.0, min_duration: float = 0.5,
               warnings: list[str] | None = None) -> list[dict]:
    """Give every segment an end time.

    When the segment carries its own end — the ranged cache form (#40) — that
    is used. Otherwise a cue ends where the next one starts, which is the only
    other signal available, and the last cue falls back to `tail_seconds`: a
    guess, labelled as one. Before ranges reached this function every last cue
    in every subtitle file was that guess.

    A real end past the next segment's start is pulled back to it. Overlapping
    cues are valid SRT but players disagree about them, so accepting ranges
    stays a pure gain rather than a change in how output behaves. Those pulls
    are appended to `warnings` when a list is passed — a correction nobody can
    see is one nobody can judge.

    Out-of-order or duplicate timestamps would otherwise produce a negative-length
    cue that players reject outright; those get `min_duration` instead so the line
    still shows rather than silently vanishing.
    """
    cues = []
    for i, seg in enumerate(segments):
        nxt = segments[i + 1]["start"] if i + 1 < len(segments) else None
        own = seg.get("end")
        if own is not None:
            end = own
            if nxt is not None and end > nxt:
                if warnings is not None:
                    warnings.append(
                        f"segment at {format_timestamp(seg['start'])} ends after the "
                        f"next one starts; trimmed to {format_timestamp(nxt)}")
                end = nxt
        elif nxt is not None:
            end = nxt
        else:
            end = seg["start"] + tail_seconds
        if end <= seg["start"]:
            end = seg["start"] + min_duration
        text = seg["text"]
        if show_speaker and seg["speaker"]:
            text = f"{seg['speaker']}: {text}"
        cues.append({"start": seg["start"], "end": end, "text": text})
    return cues


# Readability conventions differ by script, and one number for all of them is
# wrong twice over: 42 characters is a long-but-standard Latin line and roughly
# double what a CJK line should carry, because each glyph is full-width.
LINE_LIMITS = {"latin": 42, "cjk": 20}

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_THAI = re.compile(r"[\u0e00-\u0e7f]")


def detect_script(text: str) -> str:
    """Which convention this line should follow: "latin", "cjk", or "thai".

    Decided by which script carries most of the line, not by first character —
    a Chinese sentence containing an English term is still a Chinese line, and
    breaking it at 42 characters would put twice the readable width on screen.
    """
    cjk = len(_CJK.findall(text))
    thai = len(_THAI.findall(text))
    latin = sum(1 for ch in text if ch.isalpha() and ord(ch) < 0x0250)
    if thai > cjk and thai > latin:
        return "thai"
    if cjk >= latin and cjk > 0:
        return "cjk"
    return "latin"


def wrap_cue_text(text: str, limits: dict | None = None) -> str:
    """Break one cue into readable lines for its script.

    `limits` defaults to `LINE_LIMITS` rather than being read from the module
    directly, so a caller can pass configured widths without mutating global
    state — a mutated global would leak between tests and between recordings in
    the same run.

    Thai is returned untouched on purpose. It has no word spaces and correct
    breaking needs a segmenter this plugin does not carry; breaking mid-word
    would be worse than a long line, and guessing a break point is exactly the
    kind of plausible-looking wrongness that is hard to notice later.
    """
    script = detect_script(text)
    if script == "thai":
        return text
    limit = (limits or LINE_LIMITS)[script]
    if len(text) <= limit:
        return text

    if script == "cjk":
        # No word spaces to break on, so break by width. Any position is a legal
        # break in a script that does not separate words.
        return "\n".join(text[i:i + limit] for i in range(0, len(text), limit))

    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def render_srt(cues: list[dict], limits: dict | None = None) -> str:
    blocks = []
    for n, cue in enumerate(cues, start=1):
        blocks.append(
            f"{n}\n"
            f"{format_timestamp(cue['start'])} --> {format_timestamp(cue['end'])}\n"
            f"{wrap_cue_text(cue['text'], limits)}\n"
        )
    return "\n".join(blocks)


def subtitle_source(rec_id: str, prefer: str = "polished") -> pathlib.Path:
    """Which cached file this recording's subtitles should come from.

    `polished` (the default) takes Plaud's filler-thinned version when there is
    one — same segments, same timings, so the timeline does not shift. Nobody
    reads "呃" on screen.

    `verbatim` takes the raw transcript even when a polish exists. That is not a
    worse choice, it is a different job: qualitative and conversation-analytic
    work treats disfluency as data, and hesitation is exactly what gets thinned
    away. Which one is right depends on what the subtitles are for, which is why
    this is a preference rather than a decision (see `scripts/config.py`).

    Search is not configurable and deliberately so — it stays on the raw
    transcript, because search answers "what was said" and polish is the same
    speech reworded.

    An empty polish file is ignored rather than preferred — a zero-byte source
    would produce an empty subtitle file, which is the failure shape that reads
    as success.
    """
    if prefer != "verbatim":
        polished = CACHE_DIR / "polish" / f"{rec_id}.md"
        if polished.is_file() and polished.stat().st_size > 0:
            return polished
    return CACHE_DIR / f"{rec_id}.md"


def _cue_lines(path: pathlib.Path) -> list[str]:
    """A file's segments rendered the way a cue would read, speaker included."""
    if not path.is_file() or path.stat().st_size == 0:
        return []
    segments = parse_segments(strip_frontmatter(path.read_text()))
    return [c["text"] for c in build_cues(segments)]


def differing_sample(rec_id: str) -> dict | None:
    """The same line both ways, or None when there is nothing to choose between.

    This exists to make the question answerable. "Polished or verbatim?" asked
    in the abstract cannot be answered by someone who has not seen either; asked
    beside one real line of their own recording rendered both ways, it answers
    itself. The failure was never that users could not decide — it was that the
    question had no content in it.

    Returns None in three cases, all of which mean *do not ask*:
      - no polish for this recording — there is no choice
      - polish is empty — same
      - the two versions are identical — a recording with no fillers to thin.
        Asking anyway would present two identical lines and demand a preference
        between them.

    Pairs by position: Plaud returns both blocks with the same segment count and
    the same timings (94 against 94, measured in #22), so index alignment holds.
    If a future release breaks that, the zip below simply stops at the shorter
    one rather than pairing lines that are not the same moment.
    """
    polished = _cue_lines(CACHE_DIR / "polish" / f"{rec_id}.md")
    verbatim = _cue_lines(CACHE_DIR / f"{rec_id}.md")
    if not polished or not verbatim:
        return None
    for tidy, raw in zip(polished, verbatim):
        if tidy != raw:
            return {"polished": tidy, "verbatim": raw}
    return None


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
    ap.add_argument("--source", choices=("polished", "verbatim"),
                    help="which transcript to subtitle from, just this once "
                         "(overrides PLAUD_SUBTITLE_SOURCE and the config file)")
    ap.add_argument("--preview-sources", action="store_true",
                    help="print one line rendered both ways so a caller can ask "
                         "which is wanted; exits 3 when there is nothing to choose")
    args = ap.parse_args()

    cfg = config.load_config()

    # Intent beats environment beats stored preference beats default. The
    # one-off flag has to win, or "just this once" would mean editing a file.
    prefer = (args.source
              or os.environ.get("PLAUD_SUBTITLE_SOURCE")
              or cfg["subtitle_source"])
    if prefer not in config.SUBTITLE_SOURCES:
        print(f"config: PLAUD_SUBTITLE_SOURCE={prefer!r} is not one of "
              f"{', '.join(config.SUBTITLE_SOURCES)} — using "
              f"{config.DEFAULTS['subtitle_source']!r}", file=sys.stderr)
        prefer = config.DEFAULTS["subtitle_source"]

    if args.preview_sources:
        if args.file:
            sys.exit("error: --preview-sources needs a recording id, not --file")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.id or ""):
            sys.exit(f"error: refusing unsafe recording id: {args.id!r}")
        sample = differing_sample(args.id)
        if sample is None:
            # Exit 3, not 0-with-empty-output. A caller branching on empty stdout
            # would be indistinguishable from a caller branching on success, and
            # would go on to offer a choice between two things it never found.
            sys.exit(3)
        print(f"polished: {sample['polished']}")
        print(f"verbatim: {sample['verbatim']}")
        return

    if args.file:
        path = pathlib.Path(args.file)
    else:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.id or ""):
            sys.exit(f"error: refusing unsafe recording id: {args.id!r}")
        path = subtitle_source(args.id, prefer=prefer)

    if not path.is_file():
        sys.exit(f"error: {path} not found — run the plaud-index skill first")

    raw = path.read_text()
    segments = parse_segments(strip_frontmatter(raw))
    if not segments:
        sys.exit(
            f"error: no lines in {path} looked like segments.\n"
            f"Expected '[00:12:03] Speaker 1: ...' or '[01:01 - 01:55] Speaker 1: ...'.\n"
            f"Either the recording genuinely has no timestamps, or it was cached in "
            f"a third shape neither of those covers — see the line-format contract "
            f"in scripts/cache.py. The earlier wording named only the first "
            f"possibility and sent people looking at the wrong one (#40)."
        )

    # A truncated cache would yield subtitles that just stop mid-recording, with
    # nothing in the .srt to say why. Say it here instead.
    if "complete: false" in raw[:400]:
        print(f"⚠ {path.name} is marked incomplete — these subtitles cover only the "
              f"part that was fetched. Re-run plaud-index first.", file=sys.stderr)

    srt = render_srt(build_cues(segments,
                                show_speaker=not args.no_speaker,
                                tail_seconds=args.tail_seconds),
                     limits=cfg["srt_line_limits"])
    if args.output:
        pathlib.Path(args.output).write_text(srt)
        print(f"wrote {len(segments)} cues → {args.output}")
    else:
        sys.stdout.write(srt)


if __name__ == "__main__":
    main()
