#!/usr/bin/env python3
"""Mark cues that look like an ASR engine filling silence or noise with text (#62).

Speech recognisers do not output "nothing". Given a stretch of silence, music or
room noise they produce something plausible: a three-character "中文。" held
across forty seconds, a subtitle credit learned from their training data, or a
phrase repeated until the decoder gives up. Left in a subtitle file these read
as things somebody said.

This module marks them. It never decides that nobody spoke: a flagged cue is
kept, with its words and its timing unchanged and a marker before the words,
unless the caller asks for `strip=True`. Three signals, in this order, first
match wins:

  low_cps       more than 15 seconds on screen at under 0.3 characters a second.
                Needs a real end time; cues without one are counted, not guessed.
  ghost_phrase  the WHOLE cue is on the phrase list (asr_ghost_phrases.txt), after
                case, width, spaces and punctuation are ignored.
  repetition    a unit of 3-12 characters repeated back to back three or more
                times. A unit that is one character, or has no letter or digit in
                it, is laughter or punctuation and does not count.

The thresholds come from one recording where they separated a decoder holding a
token across silence (0.05-0.07 characters a second) from slow real speech
(0.55 and up), with a wide gap between the two. That is one recording of one
kind of meeting: how many ghost cues the lists miss, and how many real cues the
thresholds catch, has not been measured on other material.

Detection lives here, away from `to_srt.py`, so the large and heavily guarded
subtitle file changes by a few lines and this can be tested on its own.
"""

import dataclasses
import pathlib
import unicodedata
from collections import namedtuple

SHIPPED_PHRASES = pathlib.Path(__file__).resolve().parent / "asr_ghost_phrases.txt"

LOW_CPS_MIN_SECONDS = 15.0     # strictly more than
LOW_CPS_MAX_RATE = 0.3         # strictly less than, in visible characters a second

REPEAT_MIN_UNIT = 3
REPEAT_MAX_UNIT = 12
# Five, not the issue's three. On 3,667 real cues every unit found at three or four
# copies was ordinary emphasis (a word said three times, with its comma), none had five;
# the archived script's prose also says five where its code says three; and the issue's own
# example loop is five. A marker in the subtitles is not free.
REPEAT_MIN_COPIES = 5
# `--file` bounds neither the size of a file nor the length of a line, so one cue
# can be as long as the file. The scan below is linear in what it looks at; this
# bounds what that is. A decoder loop fills the cue, so one that starts after
# twenty thousand characters has already filled twenty thousand.
REPEAT_WINDOW = 20_000
# A phrase can only match a WHOLE cue, so a cue much longer than any phrase cannot be one,
# and folding it is pure cost: NFKC turns U+FDFA into 18 characters, and a 6 MB cue of it
# took 7.5 s and 2.6 GB. Longer than this, the phrase check is skipped.
PHRASE_TEXT_CAP = 1000

SIGNALS = ("low_cps", "ghost_phrase", "repetition")
LABEL = "疑似雜音/沉默幻覺"

Flag = namedtuple("Flag", "signal detail")

# What `to_srt.sanitise` removes outright. Kept in step by hand: importing it
# would make this module depend on the file that depends on it.
_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})


class PhraseListError(ValueError):
    """The phrase list could not be read. The message is complete and names the file."""


@dataclasses.dataclass(frozen=True)
class Report:
    total: int          # segments looked at
    flagged: int        # segments a signal matched, whether kept or removed
    stripped: int       # segments removed (equals `flagged` under strip, else 0)
    unevaluable: int    # visible segments with no real end, so no rate check
    by_signal: dict     # signal name -> count


def visible_length(text: str) -> int:
    """Characters that display: not whitespace and not a code point the tool strips.

    Close to the notion `build_cues` uses to decide a cue is empty, so that a cue it
    removes as empty is not also reported as flagged. Not identical: `sanitise` keeps the
    joiners and direction marks that this treats as invisible, so a long cue of nothing but
    one of them is kept and not flagged.
    """
    return sum(1 for ch in text
               if not ch.isspace() and unicodedata.category(ch) not in _INVISIBLE)


def normalise(text: str) -> str:
    """Case, width, spaces and punctuation folded away, for whole-cue comparison."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded
                   if not ch.isspace() and unicodedata.category(ch)[0] not in "PCZ")


def _read_phrases(path: pathlib.Path, what: str) -> set:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        why = getattr(exc, "strerror", None) or type(exc).__name__
        raise PhraseListError(
            f"error: cannot read the {what} ghost-phrase list {str(path)!r}: {why}") from exc
    phrases = set()
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        folded = normalise(line)
        # An entry with nothing left would equal every punctuation-only cue
        if folded:
            phrases.add(folded)
    return phrases


def load_phrases(extra: pathlib.Path | str | None = None) -> frozenset:
    """The shipped list, plus the entries of `extra` when one is given."""
    phrases = _read_phrases(SHIPPED_PHRASES, "shipped")
    if extra is not None:
        phrases |= _read_phrases(pathlib.Path(extra), "extra")
    return frozenset(phrases)


def _repetition(text: str):
    """`(unit length, copies)` of the first unit repeated back to back, else None.

    The archived script compared every window with the next at every offset: its worst
    case is quadratic, and a long cue of one repeating character is where it shows. This is
    linear. Call a position "same" when the character equals the one `unit` places later;
    a stretch repeating a unit k times is then a run of at least (k-1)*unit consecutive
    "same" positions, and finding runs is one pass.
    """
    s = text[:REPEAT_WINDOW]
    n = len(s)
    for unit in range(REPEAT_MIN_UNIT, REPEAT_MAX_UNIT + 1):
        if n < unit * REPEAT_MIN_COPIES:
            break
        need = unit * (REPEAT_MIN_COPIES - 1)
        same = [a == b for a, b in zip(s, s[unit:])]
        i, end = 0, len(same)
        while i < end:
            if not same[i]:
                i += 1
                continue
            j = i
            while j < end and same[j]:
                j += 1
            if j - i >= need:
                chunk = s[i:i + unit]
                # Every window of a periodic stretch is a rotation of the same unit, so
                # checking the first is checking all of them. A decoder loop repeats a
                # phrase; a unit with fewer than three distinct letters is laughter or an
                # interjection ('哈 哈 哈', 'ha ha ha', 'no no no', 'ok ok ok'), however
                # it is spaced, or punctuation. That also means a two-character word
                # repeated is never reported as a four-character unit.
                if len({ch for ch in chunk if ch.isalnum()}) >= 3:
                    return unit, (j - i + unit) // unit
            i = j
    return None


def detect(text: str, duration, phrases) -> Flag | None:
    """The first signal that matches, or None.

    `duration` is how long the cue is on screen in seconds, or None when there
    is no real end to measure it from.
    """
    n = visible_length(text)
    if n == 0:
        return None
    if duration is not None:
        # To the millisecond, the resolution of the stamps: the difference of two floats
        # is not always the number it looks like (15.000000000000455), and "more than 15
        # seconds" is a strict line.
        duration = round(duration, 3)
        if duration > LOW_CPS_MIN_SECONDS:
            rate = n / duration
            if rate < LOW_CPS_MAX_RATE:
                # Cut, not rounded: 0.2996 shown as 0.30 reads as not under the line
                return Flag("low_cps",
                            f"{int(rate * 100) / 100:.2f} chars/s over {int(duration)}s")
    if len(text) <= PHRASE_TEXT_CAP and normalise(text) in phrases:
        return Flag("ghost_phrase", "")
    found = _repetition(text)
    if found:
        unit, copies = found
        return Flag("repetition", f"{unit} characters × {copies} in a row")
    return None


def label(flag: Flag) -> str:
    if flag.detail:
        return f"{LABEL} [{flag.signal}: {flag.detail}]"
    return f"{LABEL} [{flag.signal}]"


def bracket(marker: str) -> str:
    """The marker as it is written into a subtitle: on a line of its own, so that wrapping
    the words cannot cut it (at twenty characters a line it was sliced through "characters")."""
    return f"({marker})"


def flag_segments(segments: list, *, phrases, strip: bool = False):
    """`(segments, Report)`. The input is not modified.

    A flagged segment gains a `flag` key holding its label. Under `strip` it is
    removed instead, and the cue before it is held to the removed segment's start
    whenever it has no end of its own or an end that runs past that start: with no end a
    cue runs to the next one, and an end past the next cue is pulled back to it by
    `build_cues`. Without this the cue before a removed ghost would take over the span
    the ghost had occupied.

    The rate is measured over the time the viewer sees, which is the declared
    end or the next cue's start, whichever comes first. That is the same pull-back
    `build_cues` applies to an end that runs past the next cue.
    """
    out = []
    by_signal = {name: 0 for name in SIGNALS}
    unevaluable = stripped = 0
    for i, seg in enumerate(segments):
        text = seg["text"]
        end = seg.get("end")
        duration = None
        if end is not None:
            effective = end
            if i + 1 < len(segments) and segments[i + 1]["start"] < effective:
                effective = segments[i + 1]["start"]
            duration = effective - seg["start"]
        elif visible_length(text) > 0:
            unevaluable += 1
        flag = detect(text, duration, phrases)
        if flag is None:
            out.append(seg)
            continue
        by_signal[flag.signal] += 1
        if strip:
            stripped += 1
            if out:
                prev_end = out[-1].get("end")
                if prev_end is None or prev_end > seg["start"]:
                    out[-1] = {**out[-1], "end": seg["start"]}
            continue
        out.append({**seg, "flag": label(flag)})
    report = Report(total=len(segments), flagged=sum(by_signal.values()),
                    stripped=stripped, unevaluable=unevaluable, by_signal=by_signal)
    return out, report


def summary_lines(report: Report) -> list:
    """What to tell the person on stderr. Counts only, never the words."""
    lines = []
    if report.flagged:
        kinds = ", ".join(f"{name}: {report.by_signal[name]}"
                          for name in SIGNALS if report.by_signal.get(name))
        if report.stripped:
            lines.append(
                f"⚠ {report.stripped} cue(s) removed as suspected ASR noise ({kinds}). "
                f"The cues around them keep the times they had; --no-flag turns the check off.")
        else:
            lines.append(
                f"⚠ {report.flagged} cue(s) flagged as suspected ASR noise ({kinds}). "
                f"They are kept, with a marker before the words; "
                f"--strip-hallucinations drops them and --no-flag turns the check off.")
    # Said only when SOME segments have an end and some do not. A transcript with
    # no ends at all is the point form the MCP path writes: a known kind from a
    # known writer, and `to_srt.py` is quiet about those on a clean run (a test
    # holds it to that). A mix is the unusual case, where a reader could take
    # "nothing flagged" to mean every cue was checked for silence.
    if 0 < report.unevaluable < report.total:
        lines.append(
            f"note: the silence check (low_cps) could not run on {report.unevaluable} "
            f"of {report.total} segment(s): they have no end time to measure from.")
    return lines


def ledger_clauses(report: Report) -> list:
    """The clause for the success line, which a caller reading only stdout sees.

    Only a removal belongs there. A flagged cue is kept, nothing is lost, and a batch
    caller that reads any parenthetical as "something was lost" (#50) would be told so
    about every recording that merely had a marker."""
    if report.stripped:
        return [f"{report.stripped} cue(s) removed as suspected ASR noise — see stderr"]
    return []
