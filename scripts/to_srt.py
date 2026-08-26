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
from collections import Counter
import importlib.util
import os
import pathlib
import re
import unicodedata
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
# The two whitespace runs used to be separated only by an OPTIONAL bracket,
# so on a line of leading spaces with no digits after them the engine tried
# every split point and re-scanned the tail each time: 0.86s at 16k spaces,
# and this runs over EVERY dropped line. Putting the second run inside the
# bracket group means it executes at most once. Verified linear to 64k and
# identical on 3020 shapes including 3000 random ones.
CUE_SHAPED = re.compile(r"^[﻿\s]*(?:[\[(][﻿\s]*)?\d+\s*:\s*\d+")


# Which characters may not reach an output stream.
#
# Two rounds drew this as a list of ranges and both were wrong, in opposite
# directions AT THE SAME TIME. Round 14 widened it to `\u200b-\u200f` and took
# ZWNJ and ZWJ with it — U+200C is the difference between two Persian spellings
# and U+200D is what makes an emoji family one glyph — so the class deleted
# meaning; and it still passed the Tag block (U+E0000–E007F, the standard
# invisible-text smuggling vector), the Arabic letter mark and the word joiner,
# which is what it was drawn to stop. A range list is a positive enumeration,
# and a positive enumeration of a hostile input space always has a next member.
#
# So the RULE is negative — every character whose Unicode general category is a
# control, format, surrogate, private-use, unassigned, or line/paragraph
# separator — and the EXEMPTIONS are a short closed list of characters that
# carry text meaning.
#
# The asymmetry is the whole design. An exemption list that goes stale fails
# LOUD: a meaningful format character missing from it is removed AND COUNTED,
# so the loss is reported like every other loss here. A strip list that goes
# stale fails SILENT: the next hostile character simply passes. Being wrong in
# the direction that reports itself is what makes it safe to be wrong.
# `Cn` is NOT in this set, though it was for one round. `Cn` is not a property
# of a character — it is a property of the running interpreter's Unicode
# tables. `unicodedata.unidata_version` is 15.1 on Python 3.13, 14.0 on 3.10,
# so a letter assigned in Unicode 16.0 is `Cn` here and a real letter on the
# next machine. It was deleting Todhri letters and 16.0 emoji outright, and on
# 3.10/3.11 it would delete CJK Extension H and I — a whole block of Chinese.
#
# Nor is this set complete, and it cannot be made complete: variation
# selectors are `Mn`, and so is every combining mark in half the world's
# scripts, so widening by category would delete more than it protects. What
# makes an incomplete rule safe here is not coverage but the COUNT — anything
# it removes is reported, so being wrong is visible rather than silent. That
# is the same argument as the exemption list below, in the other direction.
_STRIP_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})
# `\t` is exempt HERE and normalised by `collapse_runs` instead. It was
# exempt here alone for one round, on the grounds that "a tab is layout, not
# addressing" — and `wrap_cue_text`, a hundred lines further down, deleted it
# anyway via `text.split()`, silently, and only for cues over the line limit.
# Two halves of one file disagreeing about one character, the losing half
# unable to say so.
#
# Deleting it here instead is worse, not better: a tab is a SEPARATOR, so
# removing it joins the words on either side — `word\tafter` became
# `wordafter`, which is a new word nobody said. Whitespace belongs to the
# function that understands whitespace, and that function counts what it
# changes. The two halves agree by one deferring to the other.
_STRIP_KEEP = frozenset(
    "\t"                # whitespace — `collapse_runs` normalises it, and counts it
    "\u200c\u200d"      # ZWNJ / ZWJ — orthographic; also emoji sequences
    "\u200e\u200f")     # LRM / RLM — the standard, non-overriding marks


def sanitise(text: str) -> tuple[str, int]:
    """Strip what may not reach a stream. Returns the text and how many went.

    The count is not decoration. This issue's remedy is that content removed
    from a transcript must be audible, and characters are content: round 14
    added this filter as a security fix and forgot it was also a DELETION, so
    a cue could lose a joiner between the cache and the `.srt` with no number
    anywhere. That is the failure this branch exists to end, one unit down.

    `str.isprintable()` is false for exactly the categories above (plus
    non-space `Zs`, which is kept anyway), so the common case costs one C-level
    scan and the per-character walk runs only on text that has something in it.
    """
    if text.isprintable():
        return text, 0
    kept = [c for c in text
            if c in _STRIP_KEEP or unicodedata.category(c) not in _STRIP_CATEGORIES]
    return "".join(kept), len(text) - len(kept)


def collapse_runs(text: str) -> tuple[str, int]:
    """Each whitespace run becomes its FIRST character; the ends go entirely.

    Nothing is substituted — a run's survivor is a character that was already
    there — so `len(before) - len(after)` is an exact and complete account of
    what went, which is what lets the character-unit closure test close.

    This is here rather than in `wrap_cue_text` for two reasons the round it
    was written both learned the hard way. `wrap_cue_text` returned early for
    text under the line limit, so the same tab survived in a short cue and
    vanished from a long one — one file, two treatments of one character, no
    explanation anywhere. And its CJK branch breaks by width with no regard
    for content, so a run of twenty separators became a WHITESPACE-ONLY LINE
    inside the cue — which is SRT's cue terminator, so ffmpeg dropped
    everything after it while the tool reported `wrote N cues` and exited 0.
    That is this issue's own signature, one layer below the line ledger.

    Doing it here means the count lives with every other count, and
    `wrap_cue_text` receives text it cannot lose anything from.
    """
    out, changed = [], 0
    runs = list(re.finditer(r"\s+|\S+", text))
    for i, m in enumerate(runs):
        run = m.group(0)
        if not run[0].isspace():
            out.append(run)
            continue
        if i == 0 or i == len(runs) - 1:
            changed += len(run)          # leading and trailing go entirely
            continue
        # One plain space. A character is preserved only if it was already
        # exactly that, so a tab or an ideographic space counts as changed
        # even though the length does not move — a count that only tracks
        # length would call a substitution "nothing happened".
        out.append(" ")
        changed += len(run) - (1 if run[0] == " " else 0)
    return "".join(out), changed


def header_oddities(lines: list[str]) -> list[str]:
    """Header lines that are neither a delimiter, a blank, nor `key: value`.

    Negative by construction, and used by both callers that need it. `main`
    asks it whether a block is the shape `cache.py` writes; `_cue_lines` asks
    it how much a header may have swallowed, where it replaced a
    `SEGMENT.match` gate — a gate the parser also applies is blind exactly
    where the parser is, which is the construct round 8 removed from `main`
    for this reason and which survived here until round 16.
    """
    return [l for l in lines
            if l.strip() and l.strip() != "---"
            and not re.match(r"^[A-Za-z_][\w-]*\s*:", l)]


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
    opener = sanitise(head.group(0))[0] if head else ""
    out: list[str] = []
    i = 0
    while i < len(opener):
        ch = opener[i]
        if ch.isdigit():
            j = i
            while j < len(opener) and opener[j].isdigit():
                j += 1
            out.append(f"d{{{j - i}}}")
        elif ch.isspace():
            j = i
            while j < len(opener) and opener[j].isspace():
                j += 1
            out.append(" " if j - i == 1 else f"s{{{j - i}}}")
        elif ch.isascii() and not ch.isalnum():
            out.append(ch)
            j = i + 1
        else:
            out.append("p")
            j = i + 1
        i = j
    shape = "".join(out)
    if len(shape) > _SHAPE_CAP:
        shape = shape[:_SHAPE_CAP - 1] + "\u2026"
    return f"{len(line)} chars, opens with {shape!r}"


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
                     ) -> tuple[list[dict], list[str], list[str], list[str]]:
    """One pass over the body.

    Returns `(cues, lines that produced no cue, header lines, lines whose
    declared end could not be read)`. The docstring said "(cues, lines that
    produced no cue)" for eight rounds while the signature said four — and the
    two it left out are precisely the ones the ledger, the header warning and
    the corpus test are built on. A reader trusting the prose would not know
    the discarded ends were available at all, which is how the corpus test
    came to check two of the three numbers its own docstring named.

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
    lost_ends: list[str] = []
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
                    # A discarded end is a loss too — smaller than a lost line,
                    # and until round 8 it landed in no bucket at all: the cue
                    # counted as a cue, the line was not dropped, and a time the
                    # producer wrote was thrown away in silence while
                    # `build_cues` invented a replacement from the next start.
                    lost_ends.append(line)
            cues.append({
                "start": start,
                "end": end,
                "speaker": (m.group("speaker") or "").strip(),
                "text": m.group("text").strip(),
            })
        elif line.strip():
            skipped.append(line)
    return cues, skipped, front, lost_ends


def parse_segments(body: str) -> list[dict]:
    """The cues only, for callers that genuinely do not need the skips.

    A thin delegate on purpose. Writing the loop twice would recreate the exact
    hazard `parse_transcript` exists to remove: two derivations of one quantity
    that can drift apart silently, which is what cost #50 four rounds. Anything
    that needs to know what was dropped must call `parse_transcript` — the count
    and the cues have to come from the same traversal or neither can be trusted.
    """
    cues, _, _, _ = parse_transcript(body)
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
        # Sanitised HERE, where every cue passes, rather than only at the two
        # diagnostic outlets. `_CONTROL` was applied to `shape_of` and to
        # `--preview-sources` and the normal conversion path was left raw, so a
        # cue that parses perfectly and contains `\x1b[2K\x1b[1A` reached stdout
        # untouched when streaming — and stdout IS the terminal without `-o`,
        # which the module docstring describes as ordinary usage. The comment
        # that reasoned about this considered the `.srt` file and stopped one
        # case short, in the same commit that fixed the streaming ledger.
        #
        # The `.srt` gets the same treatment: a subtitle has no legitimate use
        # for a cursor movement, and a file that carries one is a file that
        # attacks whatever displays it next.
        text, gone = sanitise(seg["text"])
        text, collapsed = collapse_runs(text)
        gone += collapsed
        if show_speaker and seg["speaker"]:
            speaker, gone_s = sanitise(seg["speaker"])
            speaker, collapsed_s = collapse_runs(speaker)
            text = f"{speaker}: {text}"
            gone += gone_s + collapsed_s
        cues.append({"start": seg["start"], "end": end, "text": text,
                     "stripped": gone})
    return cues


# Readability conventions differ by script, and one number for all of them is
# wrong twice over: 42 characters is a long-but-standard Latin line and roughly
# double what a CJK line should carry, because each glyph is full-width.
_SHAPE_CAP = 48

# How many examples a per-cue warning shows before it says "and N more".
_SAMPLE = 3

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

    # `text.split()` here — splitting on ANY whitespace and discarding it —
    # is what deleted tabs and collapsed repeated spaces with no count. The
    # text arrives collapsed now, so splitting on the plain space is lossless:
    # a non-breaking space is simply carried inside its token, making one line
    # slightly longer rather than making a character disappear.
    lines, current = [], ""
    for word in (w for w in text.split(" ") if w):
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
    """A file's cues as (start, text), and how many lines it lost.

    "Lost" counts a line the parser refused AND a cue-shaped line the header
    swallowed. Both remove content from one side of a comparison that pairs the
    two sides positionally, so both make a pair a statement about two different
    moments — which the operator then quotes to the user and stores as a
    preference.

    The drop count is returned, not discarded, because the caller pairs two of
    these BY INDEX and a drop on either side shifts every later pair. Round 3
    found that through a BOM and round 4 fixed the BOM, leaving the class: any
    drop cause at all still mis-pairs, and `--preview-sources` returns before
    `main`'s counter exists, so nothing said so.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return [], 0, 0
    # Same "the caller knows" rule as `main`: only the top-level transcript
    # carries frontmatter, so only it may have a block consumed.
    cues, dropped, front, _ = parse_transcript(
        path.read_text(encoding="utf-8-sig"),
        expect_frontmatter=path.parent == CACHE_DIR)
    # `front` was discarded here through round 7, so a header that had eaten a
    # legitimate cue was invisible to the comparison even after `main` learned to
    # report it. One root cause, two exits: fixing the first and not the second
    # is the shape of half this issue's history.
    header_ate = header_oddities(front)
    # The START comes back with the text. Round 5 returned text alone, so the
    # caller pairing two of these had nothing to check alignment WITH — and a
    # guard that only knew about parser drops could not see two clean files
    # whose moments simply differ. The timestamps were always available; not
    # returning them was the whole gap.
    # The two counts stay SEPARATE. Adding them meant a header holding one
    # note said "the verbatim transcript dropped 1 line(s)" — the transcript
    # dropped nothing, and the user was sent to look for a parse failure that
    # is not there. Both still refuse the comparison, and now each says which
    # of the two it is.
    return ([(c["start"], t)
             for c, t in zip(cues, [x["text"] for x in build_cues(cues)])],
            len(dropped), len(header_ate))


def differing_sample(rec_id: str) -> dict | None:
    """The same line both ways, or None with a REASON on stderr.

    This exists to make the question answerable. "Polished or verbatim?" asked
    in the abstract cannot be answered by someone who has not seen either; asked
    beside one real line of their own recording rendered both ways, it answers
    itself.

    Every refusal states its cause. The operator's checklist used to enumerate
    them — three, when there were six, and three of those returned None in
    silence, so the operator picked one of the listed causes and told the user
    something untrue. A closed list that has to be kept in step with the code
    drifts, and it drifted again in the same commit that grew it. So the list is
    gone: read stderr, it always says why.

    Two of the silent causes were defects rather than omissions. A dict keyed on
    `start` collapsed duplicate timestamps and paired two DIFFERENT segments —
    round 5's fabricated comparison through a new mechanism. And two clean files
    whose timelines merely differ shared no key at all, so nothing was found and
    None came back, which the table read as "the two versions are identical".

    Pairing walks both sequences and requires the starts to agree at each step.
    Position alone mis-pairs after a drop; a start lookup collapses duplicates;
    both together do neither.
    """
    def _refuse(why: str) -> None:
        print(f"⚠ no source comparison to show: {why}", file=sys.stderr)
        return None

    polished, polish_drops, polish_odd = _cue_lines(CACHE_DIR / "polish" / f"{rec_id}.md")
    verbatim, verbatim_drops, verbatim_odd = _cue_lines(CACHE_DIR / f"{rec_id}.md")

    if not (CACHE_DIR / "polish" / f"{rec_id}.md").is_file():
        return _refuse("this recording has no polished version, so there is "
                       "nothing to choose between.")
    if not polished:
        return _refuse("the polished file is empty or produced no cues, so there "
                       "is nothing to compare. Run to_srt on it to see why.")
    if not verbatim:
        return _refuse("the transcript produced no cues, so there is nothing to "
                       "compare. Run to_srt on it to see why.")
    if polish_odd or verbatim_odd:
        return _refuse(f"a header block holds {polish_odd + verbatim_odd} line(s) "
                       f"that are not `key: value`, so some of what the file "
                       f"says may have been taken as header and not read. The "
                       f"two sides cannot be lined up until that is settled — "
                       f"run to_srt on each to see the block.")
    if polish_drops or verbatim_drops:
        # Reporting one side's count when both dropped understates the loss and
        # points the user at one of two broken files. On a branch whose subject
        # is that a stated count which is wrong is worse than no count, a count
        # wrong by construction in a reachable case is worth two extra lines.
        which = ("polished and verbatim transcripts dropped "
                 f"{polish_drops} and {verbatim_drops}") if (
                     polish_drops and verbatim_drops) else (
                 f"polished transcript dropped {polish_drops}"
                 if polish_drops else f"verbatim transcript dropped {verbatim_drops}")
        return _refuse(f"the {which} line(s), so the two "
                       f"sides no longer line up and any pair shown would be two "
                       f"different moments. Fix the shape first — see the "
                       f"contract in scripts/cache.py (#50).")

    # Walk BOTH sequences in step and require the starts to agree. Position
    # alone mis-pairs after a drop (round 5); a `{start: text}` lookup collapses
    # duplicate timestamps and pairs different segments (round 6). Together they
    # do neither, and a timeline that has genuinely diverged is refused by name
    # instead of returning None in silence.
    # Equal starts prove the Nth cues share a timestamp. Inside a run of
    # duplicates that proves nothing about WHICH segment, so a displacement
    # within the group still pairs two different ones — round 6's fabricated
    # comparison surviving the round-7 repair. The test written for this used a
    # fixture whose sides were in the same order, so it could not fail on the
    # defect it named.
    # Ambiguous GROUPS are skipped; the rest still compare. Refusing the whole
    # file was correct in principle and wrong in scope: measured on the nine
    # real cache files, THREE have at least one duplicated start, so one
    # repeated timestamp in a 338-cue recording disabled the source-preference
    # flow for all of it. The fabricated pair that refusal prevents has never
    # been observed on real data — only constructed — and no answer to a
    # question that should have been asked is its own kind of wrong.
    ambiguous = {start for side in (polished, verbatim)
                 for start, n in Counter(s for s, _ in side).items() if n > 1}
    if len(polished) != len(verbatim):
        return _refuse(f"the two versions have different cue counts "
                       f"({len(polished)} polished, {len(verbatim)} verbatim), so "
                       f"they cannot be lined up sentence by sentence.")
    hidden = 0
    for (p_start, tidy), (v_start, raw) in zip(polished, verbatim):
        if p_start in ambiguous or v_start in ambiguous:
            # Same timestamp twice: cannot say which is which. Count the ones
            # that ACTUALLY differed — the refusal below used to fire on the
            # mere existence of a duplicate, so two identical files with one
            # repeated timestamp were told "every line that differs sits at an
            # ambiguous timestamp" when no line differed at all. A refusal
            # that describes a problem the file does not have sends somebody
            # to fix nothing.
            hidden += tidy != raw
            continue
        if p_start != v_start:
            return _refuse(f"the two versions diverge at "
                           f"{format_timestamp(min(p_start, v_start))} — one has a "
                           f"segment the other does not, so no pair after that "
                           f"point is the same moment.")
        if tidy != raw:
            return {"polished": tidy, "verbatim": raw}
    if hidden:
        return _refuse(f"every line that differs sits at a timestamp the file "
                       f"uses more than once ({len(ambiguous)} such), so there is "
                       f"no pair that can be shown as certainly the same moment. "
                       f"Everything unambiguous was identical.")
    return _refuse("the two versions are identical — there is no choice to "
                   "offer, and asking would present the same line twice.")


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
        # Sanitised. `_CONTROL`'s comment says untrusted transcript text must
        # not be able to address the terminal at all, and this was the one
        # outlet still handing it over raw — while the class added to close
        # those outlets checked only the PATH. It matters more here than in the
        # `.srt` (where escaping would corrupt the file): per SKILL.md the
        # operator quotes these two lines to the user and then stores the answer
        # as a preference, so forged content becomes persisted configuration.
        print(f"polished: {sanitise(sample['polished'])[0]}")
        print(f"verbatim: {sanitise(sample['verbatim'])[0]}")
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
        # `!r`, like the other outlets. `--file` makes this path
        # attacker-controlled and a control character in it erases or forges the
        # line reporting it. The comment beside the drop warning named this
        # channel and only the one sentence being written at the time was fixed
        # — one root cause, three exits, two of them left open for two rounds.
        sys.exit(f"error: {str(path)!r} not found — run the plaud-index skill first")

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
    segments, dropped, front, lost_ends = parse_transcript(
        raw, expect_frontmatter=expect_front)
    unparsed = len(dropped)
    header = [line for line in front if line.strip()]

    # ACCOUNTING, and it is NEGATIVE now.
    #
    # Round 7 made the header bucket reportable and then gated the report on
    # `SEGMENT.match` — so the header reported only the subset the parser
    # already accepts, and every shape the parser cannot read stayed invisible
    # to the very thing written to report the parser's blindness. That is the
    # construct rounds 1, 2 and 3 failed on, reintroduced inside its own fix,
    # and it swallowed the five prefixes named in `CUE_SHAPED`'s comment above.
    #
    # A COUNT needs no shape test. Cues, dropped and header are all stated, so
    # the three close the ledger against every non-blank line and a reader needs
    # to trust no judgement about what the consumed lines looked like.
    #
    # The sharper sentence survives on top of the number, because "some of what
    # the header ate was speech" is worth more than a bare count — but it is no
    # longer the only signal, so being wrong about it now costs precision rather
    # than silence.
    #
    # Quiet on the ordinary case: a cache transcript whose header `cache.py`
    # wrote itself, with nothing else wrong. Silence is allowed only where the
    # kind AND the writer are both known.
    if header:
        ate = [line for line in header if SEGMENT.match(line)]
        # Speak whenever the header is not the shape `cache.py` writes, not only
        # when its contents happen to be `SEGMENT`-shaped. An ordinary five-line
        # frontmatter and one that had swallowed speech printed the SAME ledger
        # and were both silent, so `(5 header)` carried no information: the
        # number was arithmetically right and told nobody anything.
        #
        # `cache.py` writes `---`, then `key: value` lines, then `---`. A header
        # holding anything else is a header worth a sentence — and that is a
        # statement about the block's SHAPE, which is safe to make here because
        # being wrong costs a sentence, while the ledger's count, which is not a
        # judgement, still closes underneath it.
        odd = header_oddities(header)
        # `args.file` used to force this too, on the grounds that a caller
        # passing an arbitrary path may not know the file has a header. But
        # the ledger already says `(N header)`, and SKILL.md now tells the
        # operator to relay EVERY `⚠` — so an ordinary transcript produced a
        # warning whose own text said nothing was wrong ("all of them are
        # `key: value` lines, which is what a header holds"), and the operator
        # was instructed to pass it on. A warning that fires when nothing is
        # wrong teaches its reader to ignore warnings.
        if ate or odd:
            # No conclusion in either branch. `ate` being empty means THE PARSER
            # DID NOT RECOGNISE THEM — it does not mean they were not content,
            # and the earlier wording said "which is what a header normally
            # holds" about lines that were speech. A wrong reassurance is worse
            # than silence: silence leaves the question open, "this is normal"
            # stops the reader looking. This branch has made that trade twice
            # (round 5 turned silent deletion into silent fabrication), and both
            # times by reading "not recognised" as "not content".
            if ate:
                detail = (f" — {len(ate)} of them would have parsed as cues "
                          f"(first: {shape_of(ate[0])})")
            elif odd:
                detail = (f" — {len(odd)} of them are not `key: value` lines, so "
                          f"this block is not the header `cache.py` writes "
                          f"(first: {shape_of(odd[0])})")
            else:
                detail = (" — all of them are `key: value` lines, which is what a "
                          "header holds")
            print(f"⚠ {len(header)} line(s) in {path.name!r} were taken as the "
                  f"file's header and not read{detail}.\n"
                  f"  The header is the block from the first '---' to the next "
                  f"one. If this file has no header, its first line should not "
                  f"be '---'.", file=sys.stderr)

    # A discarded end is a loss with no line attached to it, so it needs its own
    # sentence: the cue counts as a cue, the line is not dropped, and until now
    # a time the producer wrote was thrown away while `build_cues` invented a
    # replacement from the next start.
    # First few and a count, not one line per cue. This file's own convention
    # everywhere else is "the first one, plus how many"; here it printed one
    # line per affected cue, which on a long recording buries every other
    # warning and on a hostile file is unbounded output from untrusted input.
    for line in lost_ends[:_SAMPLE]:
        print(f"⚠ a declared end time was discarded and replaced with a guess: "
              f"{shape_of(line)}", file=sys.stderr)
    if len(lost_ends) > _SAMPLE:
        print(f"⚠ and {len(lost_ends) - _SAMPLE} more declared end(s) "
              f"discarded — the count on the success line is the whole total.",
              file=sys.stderr)

    if not segments:
        # The ledger, on the exit that never printed one. This branch used to
        # read `dropped` alone and announce "0 content line(s) were present" for
        # a file whose header had swallowed five of them, then name the wrong
        # hypothesis — "most likely a recording without timestamps" — for a file
        # whose problem is exactly a region one. It counts both buckets now, and
        # the hypothesis is conditional on there being no header to blame.
        stamped = [l for l in dropped if CUE_SHAPED.match(l)]
        counts = (f"\n{len(dropped)} content line(s) and {len(header)} header "
                  f"line(s) were present.")
        if stamped:
            why = (f" {len(stamped)} of the content lines DID carry a timestamp "
                   f"and none parsed; the first is {shape_of(stamped[0])}.")
        elif header:
            why = (f" The header is the block from the first '---' to the next "
                   f"one; if this file has no header, its first line should not "
                   f"be '---'. First header line: {shape_of(header[0])}.")
        elif dropped:
            # No conclusion. Round 10 made this conditional on `header`; the
            # condition it needed was "and nothing here is a cue in ANY shape",
            # which `CUE_SHAPED` cannot supply — it misses the markdown bullet,
            # the blockquote, the numbered list, the fullwidth and the angle
            # bracket, which are the five shapes this file's own comments
            # enumerate. A file that is 100% bullet-prefixed speech was told it
            # was "most likely a recording without timestamps".
            why = (f" None matched the rough timestamp hint, which misses several "
                   f"known shapes — so this may be a recording without timestamps "
                   f"OR a shape the contract does not cover. First line: "
                   f"{shape_of(dropped[0])}.")
        else:
            why = (" The file has no content lines at all, so there is nothing "
                   "to convert.")
        detail = counts + why
        sys.exit(
            f"error: no lines in {str(path)!r} looked like segments.\n"
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
            # Best-effort, and never fatal. This read was added to make the
            # incomplete warning reach the polish path; unguarded, one bad byte
            # in a file we are NOT converting killed a conversion that would
            # otherwise have succeeded — an unhandled traceback and no `.srt` at
            # all. A check that cannot fire is a defect; a check that takes the
            # whole command down with it is a worse one.
            try:
                with transcript.open(encoding="utf-8-sig", errors="replace") as fh:
                    completeness_source = fh.read(400)
            except OSError as exc:
                print(f"note: could not read {transcript.name!r} to check whether "
                      f"this recording was fully indexed ({exc.strerror}); the "
                      f"subtitles below are unaffected.", file=sys.stderr)
    if "complete: false" in completeness_source[:400]:
        print(f"⚠ {path.name!r} is marked incomplete — these subtitles cover only the "
              f"part that was fetched. Re-run plaud-index first.", file=sys.stderr)

    # `build_cues` has always appended its trim corrections "when a list is
    # passed", and this call never passed one — so in the only shipped path the
    # branch was unreachable and the docstring described an intention. A tool
    # whose subject is "partial loss must not be silent" had a second silent
    # correction in the function it feeds.
    trims: list[str] = []
    built = build_cues(segments,
                       show_speaker=not args.no_speaker,
                       tail_seconds=args.tail_seconds,
                       warnings=trims)
    # Characters removed from the words themselves. `build_cues` has stripped
    # them since round 14 and reported nothing, so a cue could lose a joiner
    # between the cache and the `.srt` with no number anywhere — a silent
    # partial loss, in the tool written to end silent partial loss.
    stripped_chars = sum(c["stripped"] for c in built)
    if stripped_chars:
        # It said "invisible" and "the words are otherwise unchanged" for one
        # round, while the rule it described was deleting assigned letters the
        # running Python had not heard of. Both claims were false about the
        # user's data, in the sentence whose job is to be true about it.
        print(f"⚠ {stripped_chars} character(s) were removed from the cue text: "
              f"control and format code points, which a subtitle has no use for "
              f"and which can address the terminal or hide text, and repeated "
              f"whitespace collapsed to one. Letters and punctuation are "
              f"untouched.", file=sys.stderr)
    srt = render_srt(built, limits=cfg["srt_line_limits"])
    for note in trims[:_SAMPLE]:
        print(f"⚠ {note}", file=sys.stderr)
    if len(trims) > _SAMPLE:
        print(f"⚠ and {len(trims) - _SAMPLE} more cue end(s) corrected — the "
              f"count on the success line is the whole total.", file=sys.stderr)

    # "content line(s)", not "timestamped line(s)". The count is of lines that
    # did not become cues, and after the inversion most of them carry no
    # timestamp at all — round 4 shipped stdout calling them timestamped while
    # stderr, in the same run, said they carried none.
    # The ledger, on the line a caller actually reads. Every non-blank input line
    # is a cue, a dropped line, or a header line, and all three are stated here
    # so the three add up without anyone having to trust a judgement about what
    # the header contained. Round 7 put the header behind a `SEGMENT.match` gate
    # and the shapes the parser cannot read went unmentioned again.
    # The first three are a LINE ledger and close: cues + dropped + header is
    # every non-blank input line. The rest are losses in other units, which is
    # why they are named rather than folded in — a discarded end time costs no
    # line and a stripped character costs no cue, so adding them to the same
    # sum would break the one arithmetic a caller can check.
    #
    # A discarded end reached stderr and stopped there, which left the ledger
    # clean for a file where the producer's own timings were thrown away and
    # replaced with guesses — and a batch harness reading only stdout is the
    # exact caller the ledger was built for (#50 opens with one).
    parts = []
    if header:
        parts.append(f"{len(header)} header")
    if unparsed > 0:
        parts.append(f"{unparsed} content line(s) dropped — see stderr")
    if lost_ends:
        parts.append(f"{len(lost_ends)} declared end(s) discarded — see stderr")
    if stripped_chars:
        parts.append(f"{stripped_chars} char(s) removed — see stderr")
    if trims:
        # A trim or a clamp REPLACES a time the producer wrote, which is the
        # same kind of loss as a discarded end and was the only one still kept
        # off the success line. On #50's own recording it is also the most
        # frequent: thirty of them across the local corpus, ten in that file.
        parts.append(f"{len(trims)} cue end(s) corrected — see stderr")
    note = f" ({', '.join(parts)})" if parts else ""
    if args.output:
        pathlib.Path(args.output).write_text(srt)
        # The drop count rides along with the cue count, on the same stream, in
        # the same sentence. #50 opens with a script reporting "6 succeeded /
        # 0 failed" over files that were four-fifths empty: the number was
        # plausible and alone. A warning on stderr does not fix that for
        # anything reading only the success line — so the success line carries
        # its own caveat.
        # The ledger is the line round 8 designated as the guarantee, and it
        # ended in a raw path: a control character in `-o` erases or forges the
        # guarantee itself.
        print(f"wrote {len(segments)} cues{note} → {str(args.output)!r}")
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
