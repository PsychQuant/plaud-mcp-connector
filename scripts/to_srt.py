#!/usr/bin/env python3
"""Turn a cached transcript into SubRip (.srt) subtitles.

The official Plaud MCP returns transcript text and nothing else — there is no
subtitle export anywhere in its seven tools, and none in the CLI. Anyone cutting
video, subtitling a lecture, or captioning a recorded class has to build the
timing themselves. This does that, from the cache `plaud-index` already wrote, so
it needs no network, no auth, and no re-fetch.

Input is the cache's one-segment-per-line form:

    [00:12:03] Speaker 1: and then we moved the deadline

Timestamps accept H:MM:SS, MM:SS, and an optional .mmm / ,mmm fraction. In the
two-field form the leading number is TOTAL MINUTES, so it passes two digits at
100 and reads `446:12` seven hours in; seconds are always 00-59. Recordings
longer than 99 minutes were silently truncated before v0.10.1 (#50). The full
contract lives in scripts/cache.py.

If some lines cannot be parsed the rest are still written and the count is
reported beside the cue count — on stdout with `-o`, and on stderr without it,
because there stdout is the subtitle file itself. The shapes go to stderr either
way. Exit stays 0: the file was written and is usable, so a caller checking only
the exit status must read the cue line, which says how many were dropped.

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
# `446:12` at seven hours. `\d{1,2}` here cost #50 — a 7.4-hour transcript went
# in with 281 segments and came out with 57, dropped silently, into an SRT that
# was syntactically perfect.
#
# `parse_timestamp` never had the problem: it does `int(minutes) * 60` with no
# width assumption, and the END of a ranged line goes straight there without
# passing through this pattern — which is why ends already accepted three
# digits while starts did not. This was the only gate.
#
# Bounded at four, not `\d+`. Total-minute stamps are bounded by how long a
# recording can be (`9999:59` is about seven days); an unbounded class would
# swap a silent-drop bug for a silent-accept one at the same line.
# TWO shapes, and they need DIFFERENT bounds, because the leading field means
# different things in each:
#
#   HH:MM:SS   leading field is literal HOURS      -> 1-2 digits
#   MM:SS      leading field is TOTAL MINUTES      -> up to 4 digits (#50)
#
# One pattern with one bound was the mistake verify round 1 caught. Widening to
# four digits for the total-minute form silently widened the HOURS field of the
# other form to 9999 hours — 416 days — where two digits had rejected it. The
# justification written here ("9999:59 is about seven days") was the MINUTES
# reading, applied without checking to the shape where the same four digits
# mean something 60x larger. That is exactly the silent-accept this bound was
# chosen to prevent, introduced by the bound itself.
#
# Three-part alternative first, so `12:30:00` is read as hours and not as
# `12:30` with a stray tail.
#
# The seconds field — and the MINUTES field of the three-part form — are
# `[0-5]\d`, a MAGNITUDE bound and not a width one. Round 2 caught the
# difference: the round-1 repair bounded how many digits each field may hold
# and never asked what they meant, so `[9999:99]` produced 600039.0. Round 1
# had named 600000.0 as "the exact value the contract text promised could not
# exist"; the repair moved the bound and left the class of defect standing. A
# field of 99 seconds is not a seconds field, and counting its digits will
# never say so.
_STAMP = r"(?:\d{1,2}:[0-5]\d:[0-5]\d|\d{1,4}:[0-5]\d)(?:[.,]\d{1,3})?"

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


# The same shape as a start, for validating an end before converting it.
# Anchored, not merely used with `fullmatch`: unanchored, a later `.search()`
# would accept `100:05` inside any surrounding junk and the check would pass
# silently. The anchors make the pattern say what it means on its own.
_STAMP_ONLY = re.compile(rf"\A(?:{_STAMP})\Z")


# A hint for the WARNING'S ADVICE. Not the denominator — see `main()`.
#
# Three rounds were spent widening a positive test used as the denominator, and
# each round a reviewer found a shape outside it:
#
#   round 1  `startswith("[")`              an indented line escapes both sides
#   round 2  `lstrip().startswith("[")`     a `(` or a BOM escapes both
#   round 3  `^[﻿\s]*[\[(]?\s*\d+:\d+`  a bullet, blockquote, numbered
#                                           list, fullwidth or angle bracket
#                                           escapes both
#
# Every one of those was the same failure: a drop whose cause also broke the
# gate the counter shared with `SEGMENT` left the numerator AND the denominator
# together, `unparsed` stayed 0, and #50's signature came back — cue-shaped
# lines in, fewer cues out, exit 0, stderr silent.
#
# A positive test has to enumerate what counts. The enumeration is finite and
# producer drift is not, so there is always a next shape. Widening it a fourth
# time would have failed the same way. The COUNT is negative now. This pattern
# survives only to pick which advice the warning gives, where being wrong costs
# a sentence rather than a silent truncation.
CUE_SHAPED = re.compile(r"^[﻿\s]*[\[(]?\s*\d+\s*:\s*\d+")


# Anything that could move a cursor, clear a line, or set a colour. Stripped
# rather than escaped: the point is that untrusted transcript text must not be
# able to address the terminal at all.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def shape_of(line: str) -> str:
    """Describe a line without quoting it.

    Recordings are other people's speech, and a terminal scrollback is as much
    a place words end up as a CI log. `tests/test_cache_line_format_live.py`
    has forbidden quoting a transcript line in a failure message since it was
    written; the warning added for #50 quoted ninety raw characters anyway,
    which published somebody's words and let embedded control codes rewrite the
    terminal — `\\x1b[1A\\x1b[2K` erases the line reporting the problem.

    No digit from the line survives. Round 2 replaced ninety raw characters
    with an opener matched by `[\\d:.,\\s-]*`, which runs from the start of the
    line and consumes digits without bound — and in a transcript the digits ARE
    the sensitive part: account numbers, ID numbers, phone numbers, amounts,
    dates. `99999:00 4111-1111-1111-1111 …` reported the card number in full.
    Trading one leak for a narrower-looking one is not closing it.

    What survives is the SHAPE: how many digits, in what arrangement. `d{5}`
    says as much as `99999` about which form the producer wrote and nothing at
    all about the value. Round 1 asked that the offending line be NAMED so a
    false positive stays diagnosable; naming its shape does that, and naming
    its contents was never what made it diagnosable.
    """
    head = re.match(r"^[﻿\s]*[^\w\s]*\s*[\d:.,\s-]*", line)
    opener = _CONTROL.sub("", head.group(0)) if head else ""
    redacted = re.sub(r"\d+", lambda m: f"d{{{len(m.group(0))}}}", opener)
    return f"{len(line)} chars, opens with {redacted!r}"


def parse_timestamp(raw: str) -> float:
    """'1:02:03.250' / '02:03' → seconds. Raises ValueError on anything else.

    The shape check is HERE, in the function that promises it. It used to be
    absent: this split on `:` and converted, so anything numeric came back as a
    plausible-looking number — `00:412` as 412.0 seconds, `10000:00` as
    600000.0 — while the docstring said those raise. That is #53.

    Both earlier repairs put the check at a CALL SITE instead, once for the
    start and once for the end. Each closed its own path and left this function
    still lying, so the same validation lived in two places and neither was the
    one a reader would look at. Splitting a promise from the code that keeps it
    is how the two halves came apart to begin with.
    """
    if not _STAMP_ONLY.fullmatch(raw):
        raise ValueError(f"unrecognised timestamp: {raw!r}")
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


DELIM = "---"


def _frontmatter_span(lines: list[str]) -> int:
    """How many leading lines are the frontmatter block. Position only.

    No test of what the lines look like. Round 5 asked whether each one matched
    `key: value` and ate `Alice: [00:00] opening statement` — speaker-labelled
    dialogue carrying a timestamp — while, when the test failed, keeping the
    block and letting `[00:01] metadata` render as a subtitle. Both directions
    of that came from the same mistake: deciding a REGION by the SHAPE of its
    contents.

    A block is delimiter to delimiter or it is not a block. Unterminated means
    there is none, and the `---` is then ordinary content that the counter will
    report rather than swallow.
    """
    if not lines or lines[0].strip() != DELIM:
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() == DELIM:
            return i + 1
    return 0


def strip_frontmatter(text: str) -> str:
    """Kept for callers that only want the body. Prefer `parse_transcript`.

    This exists because other modules and tests call it. It cannot report what
    it consumed, which is precisely why the count must not be built on it — for
    four rounds it was, and for four rounds the count went blind wherever this
    function did.
    """
    lines = text.splitlines(keepends=True)
    return "".join(lines[_frontmatter_span([l.rstrip("\n") for l in lines]):])


def parse_transcript(text: str, *, expect_frontmatter: bool = False
                     ) -> tuple[list[dict], list[str], list[str]]:
    """One pass over the body. Returns (cues, lines that produced no cue).

    Both halves come from the SAME traversal, and that is the whole point.

    Four rounds of #50 tried to make a second derivation of the input agree
    with this function's: count the `[`-leading lines, then the lstripped ones,
    then the timestamp-shaped ones, then the non-blank ones. Every version
    shared a step with the parser, and the shared step was where both went
    blind together — `unparsed` stayed 0 and a short file reported success.
    Round 4's shared step was `strip_frontmatter`, which is not even in this
    file's control.

    Computing one quantity twice from one source makes every transformation
    before the split a shared gate. There is no fourth pattern to try; there is
    a different shape of answer, which is to stop deriving it twice. This
    function already visits every line and already decides which ones become
    cues. Returning what it skipped alongside what it kept means there is no
    second traversal to desynchronise, no preprocessing to share, and no
    positive test to enumerate.

    Blank lines are not skips — they are not content and never were.
    """
    all_lines = text.splitlines()
    span = _frontmatter_span(all_lines) if expect_frontmatter else 0
    front, body_lines = all_lines[:span], all_lines[span:]

    cues: list[dict] = []
    skipped: list[str] = []
    for line in body_lines:
        m = SEGMENT.match(line)
        if m:
            try:
                start = parse_timestamp(m.group("ts"))
            except ValueError:
                skipped.append(line)      # matched the shape, failed the value
                continue
            raw_end = m.group("end")
            end: float | None = None
            if raw_end:
                # One check, in `parse_timestamp`, and the trade stated on the
                # branch that makes it: a malformed END costs the timing and
                # keeps the words, because losing somebody's speech over a
                # timing detail is the worse trade.
                try:
                    end = parse_timestamp(raw_end)
                except ValueError:
                    end = None
            cues.append({
                "start": start,
                "end": end,
                "speaker": (m.group("speaker") or "").strip(),
                "text": m.group("text").strip(),
            })
        elif line.strip():
            skipped.append(line)
    return cues, skipped, front


def parse_segments(body: str) -> list[dict]:
    """The cues only, for callers that genuinely do not need the skips.

    A thin delegate on purpose. Writing the loop twice would recreate the exact
    hazard `parse_transcript` exists to remove: two derivations of one quantity
    that can drift apart silently, which is what cost #50 four rounds. Anything
    that needs to know what was dropped must call `parse_transcript` — the count
    and the cues have to come from the same traversal or neither can be trusted.
    """
    cues, _, _ = parse_transcript(body)
    return cues


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
        trimmed = clamped = False
        if own is not None:
            end = own
            if nxt is not None and end > nxt:
                end = nxt
                trimmed = True
        elif nxt is not None:
            end = nxt
        else:
            end = seg["start"] + tail_seconds
        if end <= seg["start"]:
            end = seg["start"] + min_duration
            clamped = True
        # Reported AFTER both corrections, naming the end that was actually
        # written. The earlier version reported inside the trim branch and named
        # `nxt`, so whenever the clamp then moved the end the message described a
        # value that never existed.
        #
        # BOTH corrections report. Only the trim did, and on the nine real cache
        # files the trim fires zero times while the clamp fires thirty — ten of
        # them in #50's own recording. The correction that alters output was the
        # silent one, which is the failure this function's docstring argues
        # against: a correction nobody can see is one nobody can judge.
        if warnings is not None and (trimmed or clamped):
            what = ("trimmed" if trimmed and not clamped else
                    "clamped" if clamped and not trimmed else
                    "trimmed and then clamped")
            why = ("it ends after the next cue starts" if trimmed and not clamped else
                   "it would otherwise have zero or negative length" if clamped and not trimmed
                   else "it ends after the next cue starts, which left it with no length")
            warnings.append(
                f"cue at {format_timestamp(seg['start'])} {what} to "
                f"{format_timestamp(end)} — {why}")
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


def _cue_lines(path: pathlib.Path) -> tuple[list[tuple[float, str]], int]:
    """A file's cues as (start, text), and how many lines produced none.

    The drop count is returned, not discarded, because the caller pairs two of
    these BY INDEX and a drop on either side shifts every later pair. Round 3
    found that through a BOM and round 4 fixed the BOM, leaving the class: any
    drop cause at all still mis-pairs, and `--preview-sources` returns before
    `main`'s counter exists, so nothing said so.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return [], 0
    # Same "the caller knows" rule as `main`: only the top-level transcript
    # carries frontmatter, so only it may have a block consumed.
    cues, dropped, _ = parse_transcript(
        path.read_text(encoding="utf-8-sig"),
        expect_frontmatter=path.parent == CACHE_DIR)
    # The START comes back with the text. Round 5 returned text alone, so the
    # caller pairing two of these had nothing to check alignment WITH — and a
    # guard that only knew about parser drops could not see two clean files
    # whose moments simply differ. The timestamps were always available; not
    # returning them was the whole gap.
    return [(c["start"], t) for c, t in zip(cues, [x["text"] for x in build_cues(cues)])], len(dropped)


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

    A fourth None case, and the reason it is here: **either side dropped a
    line**. `zip` stopping at the shorter list does NOT rescue an index shift —
    lose line 1 of one side and every remaining pair is two different moments
    shown as one sentence written two ways. The earlier docstring claimed the
    zip handled it; it handles a length difference, not a displacement. Since
    the operator quotes this pair to the user and stores the answer as a
    preference, a fabricated comparison would become persisted configuration,
    so refusing is the only honest option.
    """
    polished, polish_drops = _cue_lines(CACHE_DIR / "polish" / f"{rec_id}.md")
    verbatim, verbatim_drops = _cue_lines(CACHE_DIR / f"{rec_id}.md")
    if not polished or not verbatim:
        return None
    if polish_drops or verbatim_drops:
        which = "polished" if polish_drops else "verbatim"
        n = polish_drops or verbatim_drops
        print(f"⚠ refusing to compare: the {which} transcript dropped {n} line(s), "
              f"so the two sides no longer line up and any pair shown would be two "
              f"different moments. Fix the shape first — run to_srt on the file to "
              f"see which lines, or see the contract in scripts/cache.py (#50).",
              file=sys.stderr)
        return None
    # Aligned by START TIME, not by index. Plaud returns both blocks with the
    # same segments and timings (94 against 94, measured in #22), so normally
    # every start matches — but "normally" is what four rounds of this issue
    # were about. Round 5 paired by position and checked only for parser drops,
    # which cannot see two clean files whose moments differ: it offered 00:20
    # against 00:10 as one sentence written two ways, and the operator stores
    # that answer as a preference. Comparing at equal starts makes a
    # misalignment unable to produce a pair at all.
    by_start = {start: text for start, text in verbatim}
    for start, tidy in polished:
        raw = by_start.get(start)
        if raw is not None and tidy != raw:
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
        # An arbitrary path, so the kind is unknown. Allow a block, and let the
        # count report its size — an unknown region that is visible is a
        # different thing from one that is guessed at silently.
        expect_front = True
    else:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.id or ""):
            sys.exit(f"error: refusing unsafe recording id: {args.id!r}")
        path = subtitle_source(args.id, prefer=prefer)
        # THE CALLER KNOWS. `cache.py` writes frontmatter for `--kind
        # transcript` and writes polish, summary and outline as bare bodies
        # (`cache.py:465`), and `subtitle_source` just decided which of those it
        # handed back. Nothing here has to infer what it is looking at from the
        # shape of the text, which is the guess that failed in rounds 1, 2, 3
        # and 5. A polish file's first line is content even when it is `---`.
        expect_front = path.parent == CACHE_DIR

    if not path.is_file():
        sys.exit(f"error: {path} not found — run the plaud-index skill first")

    # `utf-8-sig`, so a byte-order mark is consumed rather than left on line 1
    # where it defeats `SEGMENT`'s `^\[`. This matters most for polish files:
    # `cache.py` writes frontmatter only for `--kind transcript`, so on a polish
    # file a leading BOM lands on the FIRST CUE rather than harmlessly on `---`.
    raw = path.read_text(encoding="utf-8-sig")

    # ONE pass. `parse_transcript` visits every line once and hands back both
    # what became a cue and what did not, so there is no second derivation to
    # go blind where the first does. Four rounds of #50 were spent making a
    # separate count agree with this one; each version shared a step with the
    # parser, and the shared step was the blind spot. See `parse_transcript`.
    segments, dropped, front = parse_transcript(raw, expect_frontmatter=expect_front)
    unparsed = len(dropped)

    if not segments:
        stamped = [l for l in dropped if CUE_SHAPED.match(l)]
        detail = (f"\n{len(stamped)} line(s) DID carry a timestamp and none of "
                  f"them parsed; the first is {shape_of(stamped[0])}."
                  if stamped else
                  f"\n{len(dropped)} content line(s) were present and none "
                  f"carried a recognisable timestamp, so this is most likely a "
                  f"recording without them rather than a shape problem.")
        sys.exit(
            f"error: no lines in {path} looked like segments.\n"
            f"Expected '[00:12:03] Speaker 1: ...' or '[01:01 - 01:55] Speaker 1: ...'."
            f"{detail}\n"
            f"Either the recording genuinely has no timestamps, or it was cached in "
            f"a third shape neither of those covers — see the line-format contract "
            f"in scripts/cache.py. The earlier wording named only the first "
            f"possibility and sent people looking at the wrong one (#40)."
        )

    # A file that parses PARTLY is what both guards were blind to. The silent
    # drop is right for blank lines; the guard above fires only at ZERO. #50
    # parsed 20% of a file — silent by design, and quiet at the guard because
    # the list was not empty. "All or nothing" was an assumption nobody wrote
    # down.
    if unparsed > 0:
        first_bad = dropped[0]
        # The COUNT enumerates nothing; the ADVICE is allowed to guess, because
        # guessing wrong here costs a sentence rather than a silent truncation.
        # Three causes, three different things to do. Sending an indented line
        # to "grow the contract" was advice that could never work — `SEGMENT`
        # reads from column zero, so no contract text makes leading whitespace
        # parse — and a warning nobody can clear gets ignored, which is how
        # #50's silence comes back.
        if first_bad[:1].isspace():
            remedy = ("The line is indented, and the parser reads from column "
                      "zero — no contract change makes it parse. Unindent it "
                      "at the producer.")
        elif CUE_SHAPED.match(first_bad):
            remedy = ("It carries a timestamp, so this is probably a shape the "
                      "contract does not cover yet: measure it and add it to "
                      "scripts/cache.py (#50).")
        else:
            remedy = ("It carries no recognisable timestamp. Either the "
                      "producer wrote prose into the body — which the "
                      "line-format contract does not allow — or it has started "
                      "writing a shape this cannot even recognise as a time.")
        # `!r`, not raw and not `shape_of`. The filename is attacker-controlled
        # through `--file`, and `\x1b[1A\x1b[2K` in it erases the very line
        # reporting the problem — the same capability the transcript sanitiser
        # exists for, through the channel left open beside it. What actually
        # stops a control character addressing the terminal here is the repr's
        # escaping, not the character-class strip; `shape_of` would hide the
        # name entirely and leave nobody able to say WHICH file.
        print(f"⚠ {unparsed} of {unparsed + len(segments)} content lines in "
              f"{path.name!r} "
              f"did not parse as segments — those words are missing from the "
              f"subtitles, which will otherwise look complete.\n"
              f"  first one: {shape_of(first_bad)}\n"
              f"  {remedy}", file=sys.stderr)

    # A truncated cache would yield subtitles that just stop mid-recording, with
    # nothing in the .srt to say why. Say it here instead.
    #
    # Read from the TRANSCRIPT, not from whatever file we are subtitling. The
    # completeness flag lives in the transcript's frontmatter, and `cache.py`
    # writes polish, summary and outline as bare bodies with none (`cache.py:465`)
    # — while `subtitle_source` PREFERS the polish. So testing `raw` meant testing
    # a string that could never appear in the file being read: on the default
    # path this warning was structurally unable to fire, which is the same shape
    # of silent truncation the rest of this issue is about.
    completeness_source = raw
    if not args.file and args.id:
        transcript = CACHE_DIR / f"{args.id}.md"
        if transcript.is_file() and transcript != path:
            completeness_source = transcript.read_text(encoding="utf-8-sig")[:400]
    if "complete: false" in completeness_source[:400]:
        print(f"⚠ {path.name!r} is marked incomplete — these subtitles cover only the "
              f"part that was fetched. Re-run plaud-index first.", file=sys.stderr)

    # `build_cues` has always appended its trim corrections "when a list is
    # passed", and this call never passed one — so in the only shipped path the
    # branch was unreachable and the docstring described an intention. A tool
    # whose subject is "partial loss must not be silent" had a second silent
    # correction in the function it feeds.
    trims: list[str] = []
    srt = render_srt(build_cues(segments,
                                show_speaker=not args.no_speaker,
                                tail_seconds=args.tail_seconds,
                                warnings=trims),
                     limits=cfg["srt_line_limits"])
    for note in trims:
        print(f"⚠ {note}", file=sys.stderr)

    # "content line(s)", not "timestamped line(s)". The count is of lines that
    # did not become cues, and after the inversion most of them carry no
    # timestamp at all — round 4 shipped stdout calling them timestamped while
    # stderr, in the same run, said they carried none.
    note = f" ({unparsed} content line(s) dropped — see stderr)" if unparsed > 0 else ""
    if args.output:
        pathlib.Path(args.output).write_text(srt)
        # The drop count rides along with the cue count, on the same stream, in
        # the same sentence. #50 opens with a script reporting "6 succeeded /
        # 0 failed" over files that were four-fifths empty: the number was
        # plausible and alone. A warning on stderr does not fix that for
        # anything reading only the success line — so the success line carries
        # its own caveat.
        print(f"wrote {len(segments)} cues{note} → {args.output}")
    else:
        # Streaming: stdout IS the subtitle file, so there is no success line to
        # attach anything to and writing one would corrupt the .srt. The count
        # goes to stderr instead — which round 4 missed entirely, having put the
        # guarantee on the `-o` path, tested only that path, and then written
        # the promise into the module docstring without a condition. A caller
        # doing `to_srt.py id > out.srt` got a short file, exit 0 and silence.
        if note:
            print(f"wrote {len(segments)} cues to stdout{note}", file=sys.stderr)
        sys.stdout.write(srt)


if __name__ == "__main__":
    main()
