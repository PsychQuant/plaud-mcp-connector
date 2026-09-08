"""Tests for scripts/to_srt.py (issue #4).

Subtitle timing is the kind of thing that looks right and is off by a second, so
these pin the arithmetic and the edge cases rather than smoke-testing that it
produces *some* output.
"""

import importlib.util
import io
import itertools
import json
import os
import pathlib
import ast
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "to_srt.py"


def cli_env(cache: pathlib.Path, **extra: str) -> dict:
    """Environment for running to_srt.py under test.

    Always pins PLAUD_CONFIG, not just PLAUD_CACHE_DIR. Once the CLI started
    reading preferences (#29), every subprocess test silently inherited whatever
    the developer had configured — and one #22 test, which asserts the polish is
    used, fails outright for anyone who has chosen verbatim. It passed here only
    because this machine had no config file.

    Isolation lives in this one helper rather than in each test's env dict so
    that forgetting it is not possible: the next person to add a CLI test gets
    it by using the same door everyone else uses.
    """
    return {
        **os.environ,
        "PLAUD_CACHE_DIR": str(cache),
        # Inside the per-test cache dir, which is a fresh tempdir — so it is
        # both absent by default and impossible to share between tests.
        "PLAUD_CONFIG": str(cache / "test-config.json"),
        **extra,
    }


_spec = importlib.util.spec_from_file_location("to_srt", SCRIPT)
to_srt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(to_srt)

# The #57 timing probe: its `judge()` is the growth criterion, imported so the
# parent re-judges every shape with the SAME function the child failed fast on.
_probe_spec = importlib.util.spec_from_file_location(
    "probe_segment_timing", REPO / "tests" / "probe_segment_timing.py")
_probe = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(_probe)


# Every number the tool prints, extracted by the pattern that prints it.
#
# Round 10 built a ledger parser for `wrote N cues (...)` and claimed the numbers
# were "compared as integers on every shape that prints one". They were compared
# on ONE shape. The zero-cue exit and the header warning were never parsed by
# anything, so `+40` on either left the suite green — round 9's finding fixed on
# one surface of three and declared fixed everywhere.
#
# Anything that prints a count goes in here, and every assertion about a count
# goes through it. A test suite that cannot detect a `+40` is not evidence.
_NUMBER_PATTERNS = {
    "cues":       r"wrote (\d+) cues",
    "header":     r"\((?:[^)]*?, )?(\d+) header",
    "dropped":    r"(\d+) content line\(s\) dropped",
    "header_ate": r"— (\d+) of them would have parsed as cues",
    "header_all": r"⚠ (\d+) line\(s\) in .* were taken as the",
    "zero_body":  r"\n(\d+) content line\(s\) and \d+ header",
    "zero_head":  r"\n\d+ content line\(s\) and (\d+) header",
    "warn_bad":   r"⚠ (\d+) of \d+ content lines",
    "warn_all":   r"⚠ \d+ of (\d+) content lines",
    # The four `differing_sample` refusals and the zero-cue `stamped` branch.
    # Round 12 declared "every count" while these five had neither a pattern
    # nor any mention in tests/ — `grep` for their sentences returned nothing.
    "zero_stamped":      r"\. (\d+) of the content lines DID carry",
    "refusal_dropped":   r"transcript dropped (\d+) line\(s\)",
    "refusal_polished":  r"different cue counts \((\d+) polished",
    "refusal_verbatim":  r"different cue counts \(\d+ polished, (\d+) verbatim",
    "refusal_ambiguous": r"uses more than once \((\d+) such\)",
    "header_odd":        r"— (\d+) of them are not `key: value` lines",
    # Round 16. `lost_ends` and the stripped-character count reached stderr and
    # stopped; `refusal_dropped`'s sentence was reworded to name both sides.
    "lost_ends":         r"(\d+) declared end\(s\) discarded",
    "stripped_ledger":   r"(\d+) char\(s\) removed",
    "stripped_warn":     r"⚠ (\d+) character\(s\) in the cue text were",
    "refusal_drop_a":    r"transcripts? dropped (\d+)",
    "refusal_drop_b":    r"transcripts dropped \d+ and (\d+)",
    "refusal_header_odd": r"header holds (\d+) line\(s\)",
    "refusal_also_dropped": r"transcript also dropped (\d+) line\(s\)",
    "corrections":       r"(\d+) cue end\(s\) corrected",
    "more_ends":         r"and (\d+) more declared end\(s\)",
    "more_trims":        r"and (\d+) more cue end\(s\) corrected",
    "emptied_ledger":    r"(\d+) empty cue\(s\) removed",
    "emptied_warn":      r"⚠ (\d+) cue\(s\) held nothing but",
    "preview_altered":   r"⚠ (\d+) character\(s\) in the two lines above",
    "preview_more":      r"…\(\+(\d+) more characters",
    "shape_digits":      r"opens with '[^']*?d\{(\d+)\}",
    "shape_spaces":      r"opens with '[^']*?s\{(\d+)\}",
}


def numbers_in(text: str) -> dict:
    """Parse every count the tool can print. Absent keys are simply absent."""
    got = {}
    for name, pattern in _NUMBER_PATTERNS.items():
        m = re.search(pattern, text)
        if m:
            got[name] = int(m.group(1))
    return got



class TestTimestampParsing(unittest.TestCase):
    def test_accepts_the_forms_the_cache_actually_contains(self) -> None:
        cases = {
            "00:00:00": 0.0,
            "00:01:30": 90.0,
            "1:02:03": 3723.0,
            "02:03": 123.0,            # MM:SS short form
            "00:00:01.250": 1.25,
            "00:00:01,250": 1.25,      # comma decimal, as some tools emit
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertAlmostEqual(to_srt.parse_timestamp(raw), want, places=3)

    def test_rejects_garbage(self) -> None:
        for raw in ("", "abc", "1", "1:2:3:4"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    to_srt.parse_timestamp(raw)

    def test_rejects_what_its_docstring_says_it_rejects(self) -> None:
        """"Raises ValueError on anything else" was not true, and #53 is that gap.

        The function split on `:` and converted, so a malformed stamp became a
        plausible-looking NUMBER rather than an error: `00:412` came back as
        412.0 seconds. Round 1 and round 2 both closed this at the CALL SITES
        by checking the shape before converting, which fixed every reachable
        path and left the function itself still contradicting its own
        docstring — so #53's title claim stayed literally true while its
        symptom was gone. Validation living in two places, neither of which is
        the function that promises it, is how the two halves drifted apart in
        the first place.
        """
        for raw in ("00:412", "99:99", "12:99:99", "10000:00", "1:2", "00:1"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    to_srt.parse_timestamp(raw)


class TestTimestampFormatting(unittest.TestCase):
    def test_renders_srt_shape(self) -> None:
        self.assertEqual(to_srt.format_timestamp(0), "00:00:00,000")
        self.assertEqual(to_srt.format_timestamp(90.5), "00:01:30,500")
        self.assertEqual(to_srt.format_timestamp(3723.25), "01:02:03,250")

    def test_negative_clamps_instead_of_wrapping(self) -> None:
        # Wrapping would silently produce 23:59:59 and put the cue at the end of
        # a 24-hour timeline, which players accept and humans never find.
        self.assertEqual(to_srt.format_timestamp(-5), "00:00:00,000")

    def test_rounds_rather_than_truncates(self) -> None:
        self.assertEqual(to_srt.format_timestamp(1.0006), "00:00:01,001")


class TestSegmentParsing(unittest.TestCase):
    def test_extracts_timestamp_speaker_and_text(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] Speaker 1: hello there\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 5.0)
        self.assertEqual(segs[0]["speaker"], "Speaker 1")
        self.assertEqual(segs[0]["text"], "hello there")

    def test_speaker_is_optional(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] no speaker label here\n")
        self.assertEqual(segs[0]["speaker"], "")
        self.assertEqual(segs[0]["text"], "no speaker label here")

    def test_text_containing_colons_survives(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] Ann: the ratio is 3:1 and rising\n")
        self.assertEqual(segs[0]["speaker"], "Ann")
        self.assertEqual(segs[0]["text"], "the ratio is 3:1 and rising")

    def test_non_segment_lines_are_dropped(self) -> None:
        body = "some prose\n\n[00:00:05] A: real line\nrandom prose\n"
        self.assertEqual(len(to_srt.parse_segments(body)), 1)

    def test_frontmatter_is_stripped_before_parsing(self) -> None:
        raw = "---\nid: rec1\ncomplete: true\n---\n\n[00:00:05] A: hi\n"
        self.assertEqual(len(to_srt.parse_segments(to_srt.strip_frontmatter(raw))), 1)

    def test_cjk_text_survives(self) -> None:
        segs = to_srt.parse_segments("[00:01:02] 講者一: 我們把預算拆成兩期\n")
        self.assertEqual(segs[0]["speaker"], "講者一")
        self.assertEqual(segs[0]["text"], "我們把預算拆成兩期")

    # --- ranged form (#40) ---------------------------------------------
    #
    # `plaud-index`'s CLI fast path writes `[start - end]`, and this parser
    # accepted only `[start]`, so every recording indexed the cheap way — the
    # way the README recommends — produced no subtitles at all. Every fixture
    # in this file used the other producer's shape, so the suite stayed green
    # through it.
    #
    # Fixtures here are synthetic: real shape, invented words. The recordings
    # that exposed this are other people's speech.

    def test_ranged_form_parses_and_keeps_the_end(self) -> None:
        segs = to_srt.parse_segments("[01:01 - 01:55] Speaker 1: hello there\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 61.0)
        self.assertEqual(segs[0]["end"], 115.0)
        self.assertEqual(segs[0]["speaker"], "Speaker 1")
        self.assertEqual(segs[0]["text"], "hello there")

    def test_ranged_form_in_full_hms(self) -> None:
        segs = to_srt.parse_segments("[00:01:01 - 00:01:55] A: x\n")
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (61.0, 115.0))

    def test_point_form_reports_no_end(self) -> None:
        """The other producer's shape must keep behaving exactly as before."""
        segs = to_srt.parse_segments("[00:00:05] Speaker 1: hello there\n")
        self.assertIsNone(segs[0]["end"])

    def test_an_unparseable_end_loses_the_end_not_the_line(self) -> None:
        """Dropping the segment would lose speech over a timing detail."""
        segs = to_srt.parse_segments("[01:01 - notatime] A: the words still matter\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 61.0)
        self.assertIsNone(segs[0]["end"])
        self.assertEqual(segs[0]["text"], "the words still matter")


class TestEveryBracketLineBecomesACue(unittest.TestCase):
    """The comparison test: parsed cue count MUST equal `[`-leading line count.

    This is deliberately not a test for one regex. #40 was "the CLI's bracket
    shape does not parse"; #50 was "the same shape parses until the minute
    field passes 99". Both are instances of one thing — the cache grew a line
    shape the parser did not follow — and both produced a syntactically valid
    SRT that was silently short. A test written against either specific shape
    would not have caught the other.

    An independent oracle matters here. Counting lines that start with `[` does
    not reuse `to_srt`'s parser, so this does not have the circularity #42
    raises about the cache contract test judging itself.
    """

    def _fixture(self, *lines: str) -> str:
        return "---\nid: abc123\ncomplete: true\n---\n\n" + "\n".join(lines) + "\n"

    def test_no_bracket_line_is_silently_dropped(self) -> None:
        body = self._fixture(
            "[00:00 - 00:12] Speaker 1: first minute",
            "[99:58 - 99:59] Speaker 1: the last line under the 100-minute mark",
            "[100:05 - 100:31] Speaker 1: the first line past 99 minutes",
            "[446:12 - 446:40] Speaker 1: seven hours in",
        )
        stripped = to_srt.strip_frontmatter(body)
        bracket_lines = sum(1 for line in stripped.splitlines() if line.startswith("["))
        # A FLOOR, so the comparison cannot pass as 0 == 0. Both sides run
        # through `strip_frontmatter` first, and that shared step is round 4's
        # blind spot in miniature: replace its body with `return ""` and the
        # assertion below goes green on an empty file while claiming every
        # bracket line became a cue. The independent oracle stops being
        # independent the moment the thing it counts can be zeroed by the code
        # under test — which is the #42 circularity this class's own docstring
        # says it avoids.
        self.assertEqual(4, bracket_lines,
                         "the fixture's own bracket lines went missing, so the "
                         "comparison below would compare nothing to nothing")
        segments = to_srt.parse_segments(stripped)
        self.assertEqual(
            bracket_lines, len(segments),
            f"{bracket_lines - len(segments)} line(s) starting with '[' produced no cue. "
            f"A file can lose most of its transcript this way and still emit a "
            f"syntactically valid SRT — #50 lost four fifths of a 7.4-hour recording "
            f"(281 segments in, 57 cues out) with "
            f"no error, no warning, and continuous timecodes.")

    def test_the_point_form_counts_too(self) -> None:
        """The MCP producer writes `[start]` with no end (#40). Same rule."""
        body = self._fixture(
            "[00:30] Speaker 1: point form, two-digit",
            "[132:07] Speaker 1: point form, three-digit",
        )
        stripped = to_srt.strip_frontmatter(body)
        bracket_lines = sum(1 for line in stripped.splitlines() if line.startswith("["))
        # The same floor its sibling got, for the same reason and two lines
        # after the comment explaining it — zeroing `strip_frontmatter` makes
        # this pass as 0 == 0 while claiming every bracket line became a cue.
        # A fix applied to one of two identical constructs is half a fix.
        self.assertEqual(2, bracket_lines,
                         "the fixture's own bracket lines went missing, so the "
                         "comparison below would compare nothing to nothing")
        self.assertEqual(bracket_lines, len(to_srt.parse_segments(stripped)))


class TestTheBoundAppliesToBothEndsAndBothShapes(unittest.TestCase):
    r"""Verify round 1 found the bound was enforced on a quarter of what it claimed.

    `_STAMP` governs the `ts` group only. The `end` group is `[^\]]*?` and goes
    straight to `parse_timestamp`, which has no width limit — so `10000:00` as
    an END sailed through while the same string as a START was rejected. And
    `_STAMP` is ONE pattern serving TWO shapes: `MM:SS` (total minutes, which
    is what #50 is about) and `HH:MM:SS` (literal hours). Widening it to four
    digits for the first silently widened the hours field of the second to
    9999 hours — 416 days — where two digits had rejected it before.

    The comment justifying the bound said an unbounded class "would trade a
    silent-drop bug for a silent-accept one at the same site". It was right,
    and it only checked one of the two shapes it was describing.
    """

    def test_a_five_digit_end_does_not_become_a_timestamp(self):
        segs = to_srt.parse_segments("[100:05 - 10000:00] Speaker 1: five-digit end")
        self.assertEqual(1, len(segs), "the line itself must survive — a malformed end "
                                       "costs the timing, not the words")
        self.assertIsNone(segs[0]["end"],
                          "a five-digit end was accepted as a real time. The contract "
                          "says five digits is malformed and stays rejected; it was "
                          "only ever true of starts")

    def test_a_well_formed_end_still_parses(self):
        segs = to_srt.parse_segments("[100:05 - 100:31] Speaker 1: fine")
        self.assertAlmostEqual(6031.0, segs[0]["end"])

    def test_a_malformed_end_keeps_the_words(self):
        """The existing trade, restated as a test rather than a comment."""
        for bad in ("banana", "00:412", "10000:00", ""):
            with self.subTest(end=bad):
                segs = to_srt.parse_segments(f"[00:10 - {bad}] Speaker 1: the words")
                self.assertEqual(1, len(segs), f"end={bad!r} cost the whole line")
                self.assertEqual("the words", segs[0]["text"])

    def test_seconds_must_be_exactly_two_digits(self):
        """`00:412` became 412.0 — a plausible-looking wrong number (#53's substance)."""
        segs = to_srt.parse_segments("[00:10 - 00:412] Speaker 1: three-digit seconds")
        self.assertIsNone(segs[0]["end"],
                          "`00:412` was converted to a number instead of refused. "
                          "Silently wrong beats silently absent for danger")

    def test_the_hours_field_is_not_four_digits(self):
        """`\\d{1,4}` was meant for TOTAL MINUTES. On the HH:MM:SS shape it is hours."""
        self.assertEqual(
            [], to_srt.parse_segments("[1234:05:06] Speaker 1: 51 days in"),
            "a four-digit HOURS field parsed as 4442706s (51.4 days). The bound was "
            "justified as `9999:59 is about seven days`, which is the minutes reading; "
            "the same four digits on hours is 416 days")

    def test_the_ordinary_hour_forms_still_parse(self):
        for line, want in (("[00:01:01] S: a", 61.0),
                           ("[12:30:00] S: b", 45000.0),
                           ("[1:02:03.250] S: c", 3723.25)):
            with self.subTest(line=line):
                segs = to_srt.parse_segments(line)
                self.assertEqual(1, len(segs), f"{line} stopped parsing")
                self.assertAlmostEqual(want, segs[0]["start"])

    def test_total_minutes_still_reach_four_digits(self):
        segs = to_srt.parse_segments("[1440:00] Speaker 1: a day of total minutes")
        self.assertEqual(1, len(segs))
        self.assertAlmostEqual(86400.0, segs[0]["start"])


class TestTheGuardDoesNotShareTheParsersAssumption(unittest.TestCase):
    r"""The denominator must be LOOSER than the parser, or it cannot see the gap.

    The guard counted `line.startswith("[")` — the same column-0 anchor
    `SEGMENT`'s `^\[` requires. Any drop whose cause also breaks that anchor
    left both the numerator and the denominator, so `unparsed` stayed 0 and
    nothing was said. One leading space was enough to reproduce #50's exact
    signature: an SRT that looks complete, no error, no warning.
    """

    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "abc123"],
                                  capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stderr

    def test_an_indented_bracket_line_is_counted_and_warned_about(self):
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00 - 00:12] S: parses\n"
                " [00:13 - 00:20] S: one leading space, silently dropped\n"
                "[00:21 - 00:30] S: parses\n")
        _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "an indented bracket line vanished without a word. The guard "
                      "shares the parser's line-start assumption, so the drop leaves "
                      "the denominator too and `unparsed` stays 0")

    def test_a_clean_file_is_still_quiet(self):
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00 - 00:12] S: parses\n\n[00:13 - 00:20] S: also parses\n")
        _, err = self._run(body)
        self.assertNotIn("did not parse", err, f"warned about a clean file: {err!r}")


class TestTheDenominatorSharesNoAssumptionWithTheParser(unittest.TestCase):
    """Round 2: `lstrip()` NARROWED the shared assumption instead of removing it.

    Round 1 caught the denominator counting `startswith("[")` — the parser's own
    column-0 anchor — so a drop caused by breaking that anchor left numerator and
    denominator together. The repair moved to `lstrip().startswith("[")`, which
    sees the indent class and still requires a leading `[`, exactly as `SEGMENT`
    does. Anything cue-shaped without that bracket escapes both again, and #50's
    whole signature came back: three cue-shaped lines in, two cues out, exit 0,
    stderr empty.

    A denominator is only honest if it shares NO gate with the parser. This one
    asks a single question the parser never asks: does the line carry a
    timestamp at all?
    """

    def _run(self, body: str, name: str = "abc123.md") -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / name).write_text(body, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), name[:-3]],
                                  capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stdout, proc.stderr

    def test_a_cue_shaped_line_without_a_bracket_is_counted(self):
        """#50's signature, reproduced through a prefix the bracket test cannot see."""
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00 - 00:12] S: parses\n"
                "(00:13 - 00:20) S: paren form, silently dropped\n"
                "[00:21 - 00:30] S: parses\n")
        _, _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "a cue-shaped line vanished without a word because the "
                      "denominator still required a leading '[' — the same gate "
                      "SEGMENT applies, one character narrower than round 1")

    def test_a_byte_order_mark_does_not_hide_a_drop(self):
        """`str.lstrip()` does not strip a BOM, so it hid the line from BOTH sides.

        The assertion is on the OUTCOME — no line lost — and not on the warning,
        because the warning is only the second-best answer here. Reading the file
        as `utf-8-sig` consumes the mark and the line simply parses, which beats
        parsing one of two and saying so. An earlier draft of this test demanded
        the warning, and would have failed the better fix.
        """
        body = ("\ufeff[00:00 - 00:12] S: a BOM defeats SEGMENT's column-zero anchor\n"
                "[00:13 - 00:20] S: parses\n")
        _, out, err = self._run(body)
        self.assertNotIn("did not parse", err, f"a BOM cost a line: {err!r}")
        # Without `-o` the CLI writes the SRT itself to stdout, so count cues there.
        self.assertEqual(2, out.count(" --> "),
                         f"the BOM line vanished — round 1's B3 reached through a "
                         f"different invisible prefix: {out!r}")

    def test_an_annotation_line_is_reported_under_the_negative_count(self):
        """Deliberate behaviour change in round 4, recorded rather than hidden.

        Under the positive denominator `[laughter]` was excluded, on the
        argument that it was never meant to be a cue and warning about it cries
        wolf. The negative denominator counts it, and on reflection that is the
        right way round: `cache.py`'s contract is ONE SEGMENT PER LINE, so an
        annotation line is a line the contract does not permit. Naming it is a
        contract question surfacing, which is what this warning is for.

        The trade only works because the advice distinguishes the cases — a
        line with no recognisable timestamp gets told it may be prose, not told
        to go grow the timestamp contract.
        """
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] S: hello\n"
                "[laughter]\n"
                "[00:10] S: bye\n")
        _, _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "an annotation line is a content line that produced no cue; "
                      "the negative count must see it")
        self.assertIn("no recognisable timestamp", err,
                      f"the advice sent an annotation line to the timestamp "
                      f"contract, which cannot help it: {err!r}")

    def test_the_denominator_is_not_bounded_where_the_parser_is(self):
        """A drift PAST the parser's bound must be visible, not agreed-upon."""
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] S: parses\n"
                "[10000:00] S: five digits — past the contract's bound\n")
        _, _, err = self._run(body)
        self.assertIn("did not parse", err,
                      "a five-digit minute field was invisible to the denominator "
                      "AND the parser, so they agreed silently — which is the one "
                      "thing this count exists to prevent")


class TestTheDenominatorEnumeratesNothing(unittest.TestCase):
    r"""Round 4: the denominator was inverted, because widening it kept failing.

    Rounds 1, 2 and 3 each widened a POSITIVE test — "does this line look like
    a cue?" — and each time the next reviewer found a shape outside it:

        round 1  startswith("[")             an indented line escapes both
        round 2  lstrip().startswith("[")    a `(` or a BOM escapes both
        round 3  ^[BOM\s]*[\[(]?\s*\d+:\d+   a bullet, a blockquote, a
                                             numbered list, a fullwidth or an
                                             angle bracket escapes both

    A positive test must enumerate what counts. The enumeration is finite and
    producer drift is not, so there is always a next shape. The question is now
    NEGATIVE — did this non-blank line fail to become a cue? — which enumerates
    nothing and therefore cannot have a blind spot of this kind.

    The cost is the mirror image: prose lines inside the body would count as
    drops. That cost is measured rather than assumed. `cache.py put` is the only
    producer, it writes YAML frontmatter and then one segment per line, and on
    all nine real cache files every non-blank line after `strip_frontmatter` is
    a cue line — so the false-positive count on real data today is zero.
    """

    def _run(self, *body_lines: str) -> tuple[int, str, str]:
        """A fixture in the REAL producer's shape — YAML frontmatter, then cues."""
        body = ("---\nid: abc123\nname: \"t\"\ncomplete: true\n---\n\n"
                + "\n".join(body_lines) + "\n")
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            out = cache / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out)],
                capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stdout, proc.stderr

    def test_every_prefix_round_three_missed_is_counted(self):
        """The five shapes that reproduced #50's signature at 0ed3501."""
        for label, line in (
                ("markdown bullet",   "- [00:13 - 00:20] S: x"),
                ("blockquote",        "> [00:13 - 00:20] S: x"),
                ("numbered list",     "1. [00:13 - 00:20] S: x"),
                ("fullwidth bracket", "【00:13 - 00:20】S: x"),
                ("angle bracket",     "<00:13 - 00:20> S: x")):
            with self.subTest(prefix=label):
                _, out, err = self._run("[00:00] S: ok", line, "[00:30] S: ok")
                self.assertIn("did not parse", err,
                              f"{label} left numerator and denominator together — "
                              f"#50's signature, the shape a positive test misses")
                self.assertIn("dropped", out.lower(),
                              f"{label}: stdout reported success with no caveat")

    def test_a_shape_nobody_has_thought_of_is_counted(self):
        """The point of a negative test: it needs no entry for this line.

        If this test ever needs editing to add a new shape, the denominator has
        silently gone back to being a positive one.
        """
        for line in ("\u2063\u2063[00:13] S: invisible separators",
                     "\N{RIGHT-TO-LEFT OVERRIDE}[00:13] S: bidi",
                     "\t\t|00:13 - 00:20| S: pipe delimiters",
                     "00:13\u3000S: ideographic space, no bracket at all",
                     "…[00:13] S: leading ellipsis"):
            with self.subTest(line=line[:20]):
                _, out, err = self._run("[00:00] S: ok", line, "[00:30] S: ok")
                self.assertIn("did not parse", err,
                              f"a line the parser dropped was invisible to the "
                              f"count: {line[:30]!r}")

    def test_blank_lines_and_frontmatter_stay_quiet(self):
        """The silence that IS deliberate must survive the inversion."""
        _, out, err = self._run("[00:00] S: ok", "", "   ", "[00:30] S: ok")
        self.assertNotIn("did not parse", err, f"warned about blank lines: {err!r}")
        self.assertNotIn("dropped", out.lower(), f"stdout caveat on a clean file: {out!r}")

    def test_the_real_corpus_shape_is_quiet(self):
        """Nine real cache files produce no warning; this is that shape."""
        _, out, err = self._run(
            "[00:01 - 00:27] Speaker 1: a line",
            "[00:37 - 01:00] Speaker 2: another",
            "[446:12 - 446:40] Speaker 1: seven hours in")
        self.assertNotIn("did not parse", err, f"false positive: {err!r}")


class TestTheCountComesFromOnePass(unittest.TestCase):
    r"""Round 5: stop deriving the count twice.

    Four rounds tried to make a SECOND derivation of the input agree with the
    parser's. Each time the two derivations shared a step, and the shared step
    was the blind spot:

        round 1  startswith("[")           the column-0 anchor
        round 2  lstrip().startswith("[")  still requires a leading `[`
        round 3  ^[BOM\s]*[\[(]?\s*\d+:\d+  the stamp must begin the line
        round 4  strip_frontmatter(raw) on BOTH sides — a body opening with
                 `---` lost cues from the numerator AND the denominator

    That is not bad luck. Computing one quantity twice from one source makes
    every transformation before the split a shared gate, and the next one is
    only ever found by someone looking for it.

    So the parser reports its own skips. It already visits every line and
    already decides which become cues; returning both halves of that decision
    leaves no second traversal to desynchronise and no preprocessing to share.
    """

    def _run(self, body: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            out = cache / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out)],
                capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stdout, proc.stderr

    def test_a_bare_body_opening_with_a_delimiter_keeps_its_cues(self):
        """Round 4's blind spot, on the source it actually affects.

        This fixture is a POLISH body — no frontmatter, because `cache.py`
        writes none for that kind — so nothing here may be consumed as one.
        """
        cues, skipped, front, _ = to_srt.parse_transcript(
            "---\n[00:00] S: one\n[00:10] S: two\n---\n[00:20] S: three\n",
            expect_frontmatter=False)
        self.assertEqual([], front)
        self.assertEqual(3, len(cues),
                         f"a bare body whose first line is `---` lost cues: {cues!r}")
        self.assertEqual(2, len(skipped), "the two delimiters are content here")

    def test_real_frontmatter_is_still_stripped(self):
        body = ("---\nid: abc123\nname: \"x\"\ncomplete: true\n---\n\n"
                "[00:00] S: a\n")
        self.assertNotIn("id: abc123", to_srt.strip_frontmatter(body),
                         "a well-formed frontmatter block stopped being stripped")

    def test_the_skipped_lines_come_from_the_same_pass_as_the_cues(self):
        """The property itself: one traversal, both halves."""
        cues, skipped, _, _ = to_srt.parse_transcript(
            "[00:00] S: kept\n"
            "- [00:10] S: bullet\n"
            "not a cue at all\n"
            "\n"
            "[00:20] S: kept\n")
        self.assertEqual(2, len(cues))
        self.assertEqual(2, len(skipped),
                         f"blank lines must not count and dropped lines must: {skipped!r}")

    def test_no_second_derivation_exists_in_main(self):
        """A mechanical check: main must not re-split the body to count.

        This is the regression that would undo the design. If someone later
        recomputes a denominator from the text, the shared-gate class comes
        straight back, and it comes back silently.
        """
        src = SCRIPT.read_text(encoding="utf-8")
        main_src = src[src.index("def main("):]
        self.assertNotIn(".splitlines()", main_src,
                         "main() splits the body again to count. The count must "
                         "come from the parse that already visited every line — "
                         "any second traversal can go blind where the first does")


class TestTheWarningDoesNotPublishSomebodysWords(unittest.TestCase):
    """`tests/test_cache_line_format_live.py` already forbids this for CI logs.

    Its reason — "Recordings are other people's speech; a CI log is a
    publication" — applies at least as strongly to a terminal, and `shape_of`
    already existed to describe a line without quoting it. The warning added for
    #50 quoted 90 raw characters instead, which both publishes third-party
    speech and lets embedded control codes rewrite the terminal.
    """

    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPT), "abc123"],
                                  capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stderr

    def test_the_warning_never_quotes_the_transcript(self):
        secret = "something somebody actually said"
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] S: fine\n"
                f"[99999:00] S: {secret}\n")
        _, err = self._run(body)
        self.assertIn("did not parse", err, "precondition: the warning must fire")
        self.assertNotIn(secret, err,
                         "the warning printed a transcript line verbatim. This repo "
                         "already has shape_of() and a test forbidding exactly this")


    def test_no_digit_from_the_transcript_survives(self):
        r"""Round 3: the leak moved rather than closed.

        Round 2 replaced "ninety raw characters" with an opener matched by
        `[\d:.,\s-]*`, which runs from the start of the line and eats digits,
        dots, commas, hyphens and spaces without bound. In a transcript the
        numbers ARE the sensitive part — account numbers, ID numbers, phone
        numbers, amounts, dates — and every one of them is inside that class.
        The test that was supposed to catch this passed only because its
        fixture contained a `]`, which happens to fall outside the class and
        stop the match.

        So the rule is now the strong one: no digit from the line survives into
        the description. The SHAPE does — how many digits, in what arrangement
        — because that is what identifies the form without revealing the value.
        """
        for line, secret in (
                ("99999:00 4111-1111-1111-1111 is the account", "4111"),
                ("0912-345-678 called about it", "0912"),
                ("99999:00 1990.03.14 身分證 A123456789", "1990"),
                ("[99999:00] S: the sum was 8,750,000", "8,750")):
            with self.subTest(line=line[:24]):
                out = to_srt.shape_of(line)
                self.assertNotIn(secret, out,
                                 f"shape_of leaked {secret!r} from {line[:30]!r}: {out!r}")

    def test_the_description_still_identifies_the_shape(self):
        """Redaction that reveals nothing is useless — round 1 asked for naming."""
        a = to_srt.shape_of("[99999:00] S: x")
        b = to_srt.shape_of("- [00:13 - 00:20] S: x")
        c = to_srt.shape_of("plain prose with no timestamp at all")
        self.assertNotEqual(a, b, "two different shapes described identically")
        self.assertNotEqual(b, c, "two different shapes described identically")
        for out in (a, b, c):
            self.assertIn("chars", out, f"length dropped from the description: {out!r}")

    def test_control_codes_never_reach_the_terminal(self):
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] S: fine\n"
                "[99999:00] S: benign\x1b[2K\x1b[1A\x1b[2K forged\n")
        _, err = self._run(body)
        self.assertNotIn("\x1b", err,
                         "an ANSI escape from untrusted transcript text reached "
                         "stderr, where \\x1b[1A\\x1b[2K erases the very line "
                         "reporting the problem")


class TestTheBoundIsOnMagnitudeNotJustDigitCount(unittest.TestCase):
    """Round 2: the gate counted digits and never looked at what they meant.

    Round 1's B1 named `600000.0` as "the exact value the contract text added by
    this branch promised could not exist". The repair bounded field WIDTH, so
    `[9999:99]` produced 600039.0 — past the very number that was the point. A
    seconds field of 99 is not a seconds field.
    """

    def test_seconds_above_fifty_nine_are_not_a_timestamp(self):
        for line in ("[99:99] S: x", "[12:99:99] S: x", "[9999:99] S: x"):
            with self.subTest(line=line):
                self.assertEqual(
                    [], to_srt.parse_segments(line),
                    f"{line!r} parsed. Sixty-plus seconds is malformed, and "
                    f"accepting it is the silent-accept this bound exists to stop")

    def test_the_middle_field_of_the_three_part_form_is_also_bounded(self):
        self.assertEqual([], to_srt.parse_segments("[12:60:00] S: x"),
                         "sixty minutes in the HH:MM:SS form parsed")

    def test_a_bad_seconds_field_at_the_end_loses_the_timing_not_the_words(self):
        segs = to_srt.parse_segments("[00:10 - 00:99] Speaker 1: the words")
        self.assertEqual(1, len(segs), "the line must survive")
        self.assertIsNone(segs[0]["end"], "an end of 99 seconds was accepted as real")

    def test_the_shapes_that_must_still_parse(self):
        for line, want in (("[99:59] S: x", 5999.0),
                           ("[12:59:59] S: x", 46799.0),
                           ("[9999:59] S: x", 599999.0),
                           ("[1:02:03] S: x", 3723.0),
                           ("[00:10.500] S: x", 10.5)):
            with self.subTest(line=line):
                segs = to_srt.parse_segments(line)
                self.assertEqual(1, len(segs), f"{line!r} stopped parsing")
                self.assertAlmostEqual(want, segs[0]["start"])


class TestEveryCorrectionReachesSomebody(unittest.TestCase):
    """`build_cues` makes TWO corrections. Round 3 found only one reported.

    Its docstring argues the case — "a correction nobody can see is one nobody
    can judge" — and round 2 wired `main()` to surface the TRIM. Measured across
    all nine real cache files afterwards:

        trims                    0
        clamps to min_duration  30      (ten of them in #50's own 7.4-hour file)

    So the correction that was wired up and tested never happens on real data,
    and the one that happens thirty times stayed silent. A test named "every
    correction reaches somebody" asserted a property the code did not have.

    The trim message was also reporting a value that never existed: it named
    `nxt` as the new end, but when the clamp then fired the end actually written
    was `start + min_duration`.
    """

    def _stderr(self, *lines: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n" + "\n".join(lines) + "\n",
                encoding="utf-8")
            return subprocess.run([sys.executable, str(SCRIPT), "abc123"],
                                  capture_output=True, text=True,
                                  env=cli_env(cache)).stderr

    def test_a_trimmed_cue_is_reported(self):
        err = self._stderr("[00:00 - 09:59] S: a long declared end",
                           "[00:30 - 00:40] S: the next cue starts long before it")
        self.assertIn("trim", err.lower(),
                      f"a declared end was pulled back by nine minutes in silence: {err!r}")

    def test_a_clamped_cue_is_reported(self):
        """The correction that actually happens: 30 times on the real corpus."""
        err = self._stderr("[00:10 - 00:10] S: zero-length declared range",
                           "[00:10 - 00:20] S: same start as the one before")
        self.assertIn("clamp", err.lower(),
                      f"a cue was silently given a synthetic duration: {err!r}")

    def test_the_reported_end_is_the_one_actually_written(self):
        """A correction that misreports itself is not a correction anyone can judge.

        This was `assertNotIn("00:00:10,000", err)` for three rounds, which
        excludes ONE wrong answer and requires no right one — so rebinding
        `end` to anything else left it green while the registry entry naming
        this test read as coverage. Exactly `header_odd`'s shape, and found the
        same way it should have been: by `tests/mutate.py`, not by reading.
        """
        err = self._stderr("[00:20 - 09:59] S: declared end runs long",
                           "[00:10 - 00:30] S: and the next cue starts BEFORE it")
        self.assertIn("cue at 00:00:20,000 trimmed and then clamped to "
                      "00:00:20,500", err,
                      f"the correction must name the end it actually wrote: {err!r}")
        self.assertNotIn("00:00:10,000", err,
                         f"the message named the next cue's start as the new end, but "
                         f"the clamp then moved it — so the value reported was never "
                         f"written: {err!r}")

    def test_a_clean_file_reports_no_correction(self):
        err = self._stderr("[00:00 - 00:10] S: fine", "[00:20 - 00:30] S: fine")
        for word in ("trim", "clamp"):
            self.assertNotIn(word, err.lower(), f"corrected a clean file: {err!r}")


class TestPreviewSourcesRefusesToPairAfterADrop(unittest.TestCase):
    """A drop on either side makes the comparison a fabrication.

    `differing_sample` pairs polished against verbatim BY INDEX. Its docstring
    says a broken alignment is handled — "the zip below simply stops at the
    shorter one rather than pairing lines that are not the same moment" — but
    `zip` truncating does not undo an index SHIFT: lose line 1 of one side and
    every later pair is two different moments presented as one sentence written
    two ways.

    Round 3 found this through a BOM. Round 4 fixed the BOM and left the class:
    any drop cause at all — an out-of-contract shape, a bullet, a `---` body —
    still shifts the pairing, and `--preview-sources` returns before the drop
    counter exists, so there is not even a warning.

    It matters beyond one odd line: per SKILL.md the operator quotes those two
    lines to the user and then PERSISTS the answer with `config.py set
    subtitle_source`. A fabricated comparison becomes a stored preference.
    """

    def _preview(self, polish_body: str, verbatim_cues: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n" + verbatim_cues,
                encoding="utf-8")
            # polish is written by cache.py as a BARE body — no frontmatter
            (cache / "polish" / "abc123.md").write_text(polish_body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_it_pairs_the_same_moment_when_both_sides_parse_cleanly(self):
        """Round 5: the drop guard could not see this, because there is no drop.

        `_cue_lines` threw the start times away, so nothing downstream could
        check that index N on one side was the same moment as index N on the
        other — even though the timestamps were right there. Equal cue counts
        and zero drops were enough to offer 00:20 against 00:10 as "the same
        line both ways", and the operator stores that answer as a preference.
        """
        proc = self._preview(
            "[00:00] S: first\n[00:20] S: third\n[00:30] S: fourth\n",
            "[00:00] S: first\n[00:10] S: second\n[00:30] S: fourth\n")
        self.assertNotIn("second", proc.stdout,
                         f"00:20 was paired against 00:10 and offered as one "
                         f"sentence written two ways: {proc.stdout!r}")
        self.assertNotIn("third", proc.stdout,
                         f"same pair, other side: {proc.stdout!r}")

    def test_a_drop_on_one_side_refuses_rather_than_mis_pairs(self):
        """The polish side loses its first line to an out-of-contract shape."""
        proc = self._preview(
            "[00:99] S: so the budget\n[00:10] S: we split it in two\n[00:20] S: agreed\n",
            "[00:00] S: um so the budget\n[00:10] S: we split it in two\n[00:20] S: agreed\n")
        self.assertNotIn("we split it in two", proc.stdout,
                         f"a dropped line shifted the pairing and two different "
                         f"moments were offered as the same sentence: {proc.stdout!r}")
        self.assertIn("drop", (proc.stdout + proc.stderr).lower(),
                      f"the preview neither paired correctly nor said why it "
                      f"could not: out={proc.stdout!r} err={proc.stderr!r}")

    def test_a_bom_on_a_polish_file_does_not_shift_the_pairing(self):
        cues = "[00:00] S: first\n[00:10] S: second\n[00:20] S: third\n"
        proc = self._preview("\ufeff" + cues, cues)
        self.assertEqual(3, proc.returncode,
                         f"identical sources must report no choice to make: "
                         f"rc={proc.returncode} out={proc.stdout!r}")

    def test_a_genuine_difference_is_still_reported(self):
        proc = self._preview("[00:00] S: thinned\n", "[00:00] S: um, thinned\n")
        self.assertEqual(0, proc.returncode,
                         f"a real difference stopped being reported: "
                         f"rc={proc.returncode} out={proc.stdout!r}")


class TestTheCueCountCarriesItsOwnCaveat(unittest.TestCase):
    """#50's harm was a plausible number reported as success.

    The story in the issue is a script reporting "6 succeeded / 0 failed" over
    files that were four-fifths empty. Round 4 put the drop count on the stdout
    success line — and only on the `-o` path, while writing the guarantee into
    the module docstring unconditionally. Without `-o` the SRT itself IS stdout,
    so there is no success line to carry anything and the promise was false on
    the default invocation.
    """

    def _run(self, *args: str, body: str | None = None) -> subprocess.CompletedProcess:
        body = body or ("---\nid: abc123\ncomplete: true\n---\n\n"
                        "[00:00] S: kept\n[99999:00] S: dropped\n")
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123",
                 *[a.replace("{d}", str(cache)) for a in args]],
                capture_output=True, text=True, env=cli_env(cache))

    def test_stdout_says_lines_were_dropped(self):
        proc = self._run("-o", "{d}/o.srt")
        self.assertIn("dropped", proc.stdout.lower(),
                      f"stdout said {proc.stdout.strip()!r} with no hint that a "
                      f"line was lost — the same shape as the '6 succeeded / 0 "
                      f"failed' report #50 opens with")

    def test_streaming_mode_still_says_it(self):
        """No `-o`: stdout is the subtitle file, so the count must go to stderr."""
        proc = self._run()
        self.assertNotIn("dropped", proc.stdout.lower(),
                         "the caveat was written into the .srt itself")
        self.assertIn("dropped", proc.stderr.lower(),
                      f"streaming mode reported nothing at all: a caller doing "
                      f"`to_srt.py id > out.srt` gets a short file, exit 0 and "
                      f"silence — #50's exact shape. stderr={proc.stderr!r}")

    def test_the_words_on_the_two_streams_agree(self):
        """Round 4 said 'timestamped line(s) dropped' for a line with no timestamp."""
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] S: ok\nthis line is prose, no timestamp at all\n")
        proc = self._run("-o", "{d}/o.srt", body=body)
        self.assertNotIn("timestamped line", proc.stdout,
                         f"stdout calls the dropped line timestamped while stderr "
                         f"says it carries none — same run:\n"
                         f"  stdout: {proc.stdout.strip()!r}\n"
                         f"  stderr: {proc.stderr.strip()!r}")

    def test_a_clean_file_gets_no_caveat_on_either_stream(self):
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] S: a\n[00:10] S: b\n")
        proc = self._run("-o", "{d}/o.srt", body=body)
        self.assertNotIn("dropped", proc.stdout.lower())
        self.assertNotIn("dropped", proc.stderr.lower())


class TestPartialDropIsLoud(unittest.TestCase):
    """A file that parses PARTLY is the case both guards were blind to.

    The parse drops non-matching lines silently, and that is right for blank
    lines. (It was long justified by a `Subject:`-style header as well — no
    producer in this repo writes one; `cache.py put` emits YAML frontmatter.
    The justification outlived whatever it was written for.) The caller then
    guards on `if not segments`, which fires only
    at ZERO. #50 parsed 20% of one file: the drop was silent by design and the
    guard was quiet because the list was not empty. "All or nothing" was an
    assumption nobody wrote down, and partial parsing fell straight through it.

    So the signal belongs on the line count, not on the parser: a line that
    starts with `[` was meant to be a cue, and if it did not become one, say so.
    """

    def _run(self, body: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123"],
                capture_output=True, text=True, env=cli_env(cache))
            return proc.returncode, proc.stderr

    def test_a_partially_parsed_file_warns(self) -> None:
        # These carry a TIMESTAMP and still fail to parse, which is the class the
        # count is about. The first version of this fixture used `[bogus shape]`
        # and `[also bogus]` — bracketed lines with no digits in them. Those are
        # annotations, not lost cues, and counting them was the false positive
        # that made this warning fire on any transcript containing `[laughter]`
        # (verify round 2).
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00 - 00:12] Speaker 1: parses\n"
                "[99999:00] Speaker 1: five-digit minutes, out of contract\n"
                "[00:99] Speaker 1: ninety-nine seconds, out of contract\n")
        _, err = self._run(body)
        # NOT `assertIn("2", err)` — the fixture filename `abc123.md` is printed in
        # the warning and contains a "2", so that assertion passed on the filename
        # and never tested the count at all (verify round 1).
        self.assertRegex(err, r"\b2 of 3 content lines\b",
                         f"the warning does not state how many of how many were "
                         f"dropped: {err!r}")
        # Named by SHAPE, and the shape carries no digit VALUES either (round 3).
        # Round 1 asked that the line be named so a false positive stays
        # diagnosable; round 2 named it by printing ninety raw characters of
        # speech; round 3 found the replacement still echoed unbounded digit
        # runs, which is where account and ID numbers live. `d{5}` identifies
        # the form and reveals no value.
        self.assertRegex(err, r"opens with '\[d\{5\}",
                         f"the warning does not identify the first offending line's "
                         f"shape, so a false positive would be undiagnosable: {err!r}")

    def test_a_fully_parsed_file_stays_quiet(self) -> None:
        """The header and blank lines must not trip it — that silence is deliberate."""
        body = ("---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00 - 00:12] Speaker 1: parses\n"
                "\n"
                "[00:13 - 00:20] Speaker 1: also parses\n")
        _, err = self._run(body)
        self.assertNotIn("did not parse", err,
                         f"warned about a clean file — header/blank lines must stay silent: {err!r}")


class TestMinutesPastNinetyNine(unittest.TestCase):
    """Plaud's CLI writes TOTAL minutes, so 100 minutes in the field is `100:05`.

    `parse_timestamp` already computed `int(minutes) * 60` with no width
    assumption — the arithmetic was never wrong. `_STAMP` was the only gate.
    """

    def test_three_digit_minutes_parse(self) -> None:
        segs = to_srt.parse_segments("[100:05 - 100:31] Speaker 1: past the hour and a half")
        self.assertEqual(1, len(segs), "a three-digit minute field was dropped")
        self.assertAlmostEqual(6005.0, segs[0]["start"])
        self.assertAlmostEqual(6031.0, segs[0]["end"])

    def test_seven_hours_in(self) -> None:
        segs = to_srt.parse_segments("[446:12] Speaker 1: 7.4 hours")
        self.assertEqual(1, len(segs))
        self.assertAlmostEqual(446 * 60 + 12, segs[0]["start"])

    def test_two_digit_minutes_still_parse(self) -> None:
        """The widening must not cost the common case."""
        segs = to_srt.parse_segments("[07:25 - 07:31] Speaker 1: normal")
        self.assertEqual(1, len(segs))
        self.assertAlmostEqual(445.0, segs[0]["start"])

    def test_the_widening_is_bounded(self) -> None:
        """`\\d{1,4}` not `\\d+` — an unbounded class trades one silent bug for another.

        9999:59 is about seven days, past any real recording. Five digits is not
        a long meeting, it is a malformed line, and it should stay rejected.
        """
        self.assertEqual([], to_srt.parse_segments("[99999:00] Speaker 1: not a recording"))


class TestCueBuilding(unittest.TestCase):
    def _segs(self, *starts: float) -> list[dict]:
        return [{"start": s, "speaker": "A", "text": f"line {i}"}
                for i, s in enumerate(starts)]

    def test_a_cue_ends_where_the_next_begins(self) -> None:
        cues = to_srt.build_cues(self._segs(0.0, 5.0, 12.0))
        self.assertEqual(cues[0]["end"], 5.0)
        self.assertEqual(cues[1]["end"], 12.0)

    def test_final_cue_uses_the_tail_estimate(self) -> None:
        cues = to_srt.build_cues(self._segs(0.0, 10.0), tail_seconds=3.0)
        self.assertEqual(cues[-1]["end"], 13.0)

    def test_out_of_order_timestamps_get_a_positive_duration(self) -> None:
        # A zero- or negative-length cue is rejected by players, so the line would
        # disappear entirely rather than merely being mistimed.
        cues = to_srt.build_cues(self._segs(10.0, 4.0), min_duration=0.5)
        self.assertGreater(cues[0]["end"], cues[0]["start"])
        self.assertEqual(cues[0]["end"], 10.5)

    def test_duplicate_timestamps_also_get_a_positive_duration(self) -> None:
        cues = to_srt.build_cues(self._segs(7.0, 7.0), min_duration=0.5)
        self.assertEqual(cues[0]["end"], 7.5)

    def test_speaker_prefix_can_be_omitted(self) -> None:
        with_speaker = to_srt.build_cues(self._segs(0.0))
        without = to_srt.build_cues(self._segs(0.0), show_speaker=False)
        self.assertTrue(with_speaker[0]["text"].startswith("A: "))
        self.assertFalse(without[0]["text"].startswith("A: "))

    # --- real end times (#40) ------------------------------------------
    #
    # Until a producer supplied end times there was nothing to use, and the
    # docstring above says as much: the next segment's start is "the only
    # honest signal the transcript carries", and the last cue gets a guess.
    # The ranged form carries the real thing, so the guess can stop.

    def _ranged(self, *pairs: tuple) -> list[dict]:
        return [{"start": s, "end": e, "speaker": "A", "text": f"line {i}"}
                for i, (s, e) in enumerate(pairs)]

    def test_a_real_end_is_used_instead_of_the_next_start(self) -> None:
        cues = to_srt.build_cues(self._ranged((0.0, 3.0), (5.0, 9.0)))
        self.assertEqual(cues[0]["end"], 3.0)   # not 5.0

    def test_the_last_cue_stops_being_a_guess(self) -> None:
        """The tail estimate exists because nothing better was available."""
        cues = to_srt.build_cues(self._ranged((0.0, 3.0), (5.0, 9.0)),
                                 tail_seconds=4.0)
        self.assertEqual(cues[-1]["end"], 9.0)  # not 5.0 + 4.0

    def test_segments_without_an_end_still_use_the_old_rules(self) -> None:
        mixed = [{"start": 0.0, "end": None, "speaker": "A", "text": "x"},
                 {"start": 5.0, "end": 9.0, "speaker": "A", "text": "y"}]
        cues = to_srt.build_cues(mixed)
        self.assertEqual(cues[0]["end"], 5.0)   # inferred from the next start
        self.assertEqual(cues[1]["end"], 9.0)   # real

    def test_an_end_past_the_next_start_is_clamped(self) -> None:
        """Overlapping cues are legal SRT and players disagree about them.

        Accepting ranges should be a pure gain, not a change in how the
        output behaves, so an overlap is pulled back to where the next cue
        begins — exactly where the inferred value would have put it.
        """
        cues = to_srt.build_cues(self._ranged((0.0, 7.0), (5.0, 9.0)))
        self.assertEqual(cues[0]["end"], 5.0)

    def test_clamping_is_reported_rather_than_silent(self) -> None:
        notes: list[str] = []
        to_srt.build_cues(self._ranged((0.0, 7.0), (5.0, 9.0)), warnings=notes)
        self.assertTrue(notes, "an overlap was corrected and nothing said so")

    def test_nothing_is_reported_when_no_clamp_happens(self) -> None:
        notes: list[str] = []
        to_srt.build_cues(self._ranged((0.0, 3.0), (5.0, 9.0)), warnings=notes)
        self.assertEqual([], notes)

    def test_a_backwards_range_still_gets_a_positive_duration(self) -> None:
        cues = to_srt.build_cues(self._ranged((10.0, 4.0)), min_duration=0.5)
        self.assertEqual(cues[0]["end"], 10.5)


class TestRender(unittest.TestCase):
    def test_produces_wellformed_srt(self) -> None:
        cues = [{"start": 0.0, "end": 2.5, "text": "first"},
                {"start": 2.5, "end": 4.0, "text": "second"}]
        out = to_srt.render_srt(cues)
        self.assertEqual(
            out,
            "1\n00:00:00,000 --> 00:00:02,500\nfirst\n\n"
            "2\n00:00:02,500 --> 00:00:04,000\nsecond\n",
        )

    def test_indices_start_at_one_and_increment(self) -> None:
        cues = [{"start": float(i), "end": i + 1.0, "text": "x"} for i in range(3)]
        self.assertEqual(
            [ln for ln in to_srt.render_srt(cues).splitlines() if ln.isdigit()],
            ["1", "2", "3"],
        )


class TestSuiteIsolation(unittest.TestCase):
    """Guards the guard.

    Before #29 the CLI read no preferences, so pinning only PLAUD_CACHE_DIR was
    complete isolation. The moment it started reading a config file, every
    subprocess test quietly began depending on the developer's own settings —
    and #22's `test_the_cli_actually_uses_the_preference`, which asserts the
    polish is used, fails for anyone who has chosen verbatim.

    It passed review because the machine it ran on had no config file. That is
    the shape worth guarding: a test that only passes because of something
    absent from this particular machine.
    """

    def test_cli_env_pins_the_config_path(self) -> None:
        env = cli_env(pathlib.Path("/tmp/whatever"))
        self.assertIn("PLAUD_CONFIG", env)
        self.assertTrue(env["PLAUD_CONFIG"].startswith("/tmp/whatever"),
                        "config must land inside the per-test cache dir")

    def test_cli_env_overrides_an_inherited_config(self) -> None:
        with mock.patch.dict(os.environ, {"PLAUD_CONFIG": "/home/dev/real.json"}):
            env = cli_env(pathlib.Path("/tmp/whatever"))
        self.assertNotEqual("/home/dev/real.json", env["PLAUD_CONFIG"])


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="srt-test-")
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)

    def _run(self, *args: str, cache=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True,
                              env=cli_env(cache or self.cache))

    def _write(self, rec_id: str, body: str, front: str = "") -> None:
        (self.cache / f"{rec_id}.md").write_text(front + body)

    def test_converts_a_cached_recording(self) -> None:
        self._write("rec1", "[00:00:01] A: hello\n[00:00:04] B: world\n")
        p = self._run("rec1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("00:00:01,000 --> 00:00:04,000", p.stdout)
        self.assertIn("A: hello", p.stdout)

    def test_missing_recording_fails_with_a_useful_message(self) -> None:
        p = self._run("nope")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not found", p.stderr)

    def test_refuses_a_traversing_id(self) -> None:
        p = self._run("../../etc/passwd")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unsafe recording id", p.stderr)

    def test_transcript_without_timestamps_fails_loudly(self) -> None:
        # Emitting an empty .srt would look like success and produce a video with
        # no subtitles and no explanation.
        #
        # Asserts the properties the message must have, not its wording. The
        # earlier version pinned the literal "no timestamped segments", so
        # #40 — which found that exact phrasing sends people to debug the
        # wrong thing — could not correct it without a test failing for a
        # reason unrelated to behaviour.
        self._write("plain", "just prose, no timestamps at all\n")
        p = self._run("plain")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("error", p.stderr.lower())
        self.assertIn("plain", p.stderr)          # names the file it read
        self.assertIn("[00:12:03]", p.stderr)     # shows an accepted shape

    def test_the_failure_names_both_accepted_shapes(self) -> None:
        """A reader who cached the ranged form must see it listed (#40).

        Naming only the point form is what made this failure read as "your
        recording has no timestamps" when the real cause was a second, equally
        valid shape the parser did not yet accept.
        """
        self._write("plain2", "just prose, no timestamps at all\n")
        p = self._run("plain2")
        self.assertIn(" - ", p.stderr, "the ranged form is not shown as accepted")

    def test_incomplete_cache_warns_on_stderr_but_still_converts(self) -> None:
        self._write("part", "[00:00:01] A: half a transcript\n",
                    front="---\nid: part\ncomplete: false\n---\n\n")
        p = self._run("part")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("marked incomplete", p.stderr)
        self.assertIn("00:00:01,000", p.stdout)

    def test_output_flag_writes_a_file(self) -> None:
        self._write("rec1", "[00:00:01] A: hello\n")
        out = self.cache / "out.srt"
        p = self._run("rec1", "-o", str(out))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("1 cues", p.stdout)
        self.assertIn("A: hello", out.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------
# Language-dependent line length (#14)
#
# Subtitle readability conventions are not universal. Latin scripts run ~42
# characters per line; CJK is roughly half that in character count because each
# glyph is full-width. Emitting one unwrapped line regardless of script is the
# same mistake in both directions — unreadably long for Latin, and wrong by a
# factor of two for CJK.
# --------------------------------------------------------------------------
class TestScriptDetection(unittest.TestCase):
    def test_latin_text(self):
        self.assertEqual("latin", to_srt.detect_script("we should split the budget"))

    def test_chinese_text(self):
        self.assertEqual("cjk", to_srt.detect_script("我們應該把預算拆成兩期"))

    def test_japanese_kana(self):
        self.assertEqual("cjk", to_srt.detect_script("よろしくお願いします"))

    def test_thai_text(self):
        self.assertEqual("thai", to_srt.detect_script("การประชุมเรื่องงบประมาณ"))

    def test_mixed_text_follows_the_majority(self):
        """A Chinese sentence with an English term in it is still a Chinese line."""
        self.assertEqual("cjk", to_srt.detect_script("那就先把 budget 拆成兩期比較好"))

    def test_empty_text_defaults_to_latin(self):
        self.assertEqual("latin", to_srt.detect_script(""))


class TestWrapCueText(unittest.TestCase):
    def test_short_latin_line_is_untouched(self):
        self.assertEqual("hello there", to_srt.wrap_cue_text("hello there"))

    def test_long_latin_wraps_on_spaces(self):
        text = "we agreed to split the budget across two quarters and revisit it in March"
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        for line in out.split("\n"):
            self.assertLessEqual(len(line), 42, line)
        self.assertEqual(text.split(), out.replace("\n", " ").split())

    def test_latin_never_splits_a_word(self):
        out = to_srt.wrap_cue_text("supercalifragilistic " * 4)
        for line in out.split("\n"):
            for word in line.split():
                self.assertIn(word, "supercalifragilistic")

    def test_cjk_uses_a_shorter_limit(self):
        """Full-width glyphs take about twice the space per character."""
        text = "我們應該把預算拆成兩期然後在三月的時候重新檢視這件事情比較妥當"
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        for line in out.split("\n"):
            self.assertLessEqual(len(line), 20, line)

    def test_cjk_wraps_without_spaces(self):
        """CJK has no word spaces — wrapping on spaces would never fire."""
        text = "預算" * 30
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        self.assertEqual(text, out.replace("\n", ""))

    def test_thai_is_left_unwrapped_and_that_is_deliberate(self):
        """Thai has no word spaces and needs a segmenter to break correctly.
        Breaking mid-word is worse than a long line, so it is left alone rather
        than broken wrongly."""
        text = "การประชุม" * 12
        self.assertEqual(text, to_srt.wrap_cue_text(text))

    def test_no_content_is_lost_for_any_script(self):
        """Checked per script, because "same content" means different things.
        Latin wraps AT a space, so the newline replaces one. CJK wraps BETWEEN
        characters with no space involved, so replacing the newline with a
        space would invent one.

        EXACT comparisons. This asserted
        `text.split() == wrap(text).replace("\n", " ").split()` for four
        rounds — `.split()` is the transformation under test, applied to the
        REFERENCE as well as the output, so whitespace collapse and tab
        deletion were invisible to it by construction and it could not fail on
        the loss its own name promises to catch. The CJK half two lines below
        was already exact and would have gone red immediately; the weaker
        oracle sat on precisely the branch where the loss happened.
        """
        # Across configured widths, not just the default — the same gap the
        # blank-line guard had, and the same reason: `srt_line_limits` is a
        # setting, so one column count is one sample of it.
        widths = [None, {"latin": 10, "cjk": 4}, {"latin": 100, "cjk": 60},
                  {"latin": 1, "cjk": 1}]
        for limits in widths:
            for text in ["a b c " * 30, "hello", "a\u00a0b " * 20]:
                text = text.strip()
                self.assertEqual(
                    text, to_srt.wrap_cue_text(text, limits).replace("\n", " "),
                    f"the latin wrap lost or altered something at {limits}: "
                    f"{text!r}")
            for text in ["預算" * 40, "よろしく" * 20, "你好 世界"]:
                self.assertEqual(
                    text, to_srt.wrap_cue_text(text, limits).replace("\n", ""),
                    f"the CJK wrap lost or altered something at {limits}: "
                    f"{text!r}")

    def test_the_wrap_cannot_produce_a_blank_line_inside_a_cue(self):
        """A blank line is SRT's cue terminator, so one inside a cue truncates
        it in every player. The CJK branch breaks by width with no regard for
        content, so a run of `limit` separators became exactly that — and
        ffmpeg dropped everything after it while the tool said `wrote N cues`
        and exited 0. #50's own signature, one layer below the line ledger."""
        # CONFIGURED widths too. `srt_line_limits` is a documented setting,
        # and passing no `limits` meant this verified one column count out of
        # every value a user can put in their config — the guard tested the
        # default and was named for the property.
        widths = [None, {"latin": 10, "cjk": 4}, {"latin": 100, "cjk": 60},
                  {"latin": 1, "cjk": 1}]
        for raw in ["你好" + "\u3000" * 44 + "世界", "a" + " " * 90 + "b",
                    "\u3000" * 60, "預算" * 40]:
          for limits in widths:
            collapsed, _ = to_srt.collapse_runs(to_srt.sanitise(raw)[0])
            if not collapsed:
                continue
            for line in to_srt.wrap_cue_text(collapsed, limits).split("\n"):
                self.assertTrue(
                    line.strip(),
                    f"a whitespace-only line inside the cue terminates it: "
                    f"{raw[:20]!r} at {limits} -> "
                    f"{to_srt.wrap_cue_text(collapsed, limits)!r}")

    def test_wrapping_is_applied_when_rendering(self):
        cues = [{"start": 0.0, "end": 4.0,
                 "text": "we agreed to split the budget across two quarters and revisit in March"}]
        out = to_srt.render_srt(cues)
        body = [l for l in out.splitlines() if l and "-->" not in l and not l.strip().isdigit()]
        self.assertGreater(len(body), 1, out)
        for line in body:
            self.assertLessEqual(len(line), 42, line)


# --------------------------------------------------------------------------
# Subtitle source preference (#22)
#
# Plaud returns two versions of the same speech with identical segments and
# timings: raw, and a filler-thinned polish. Subtitles want the tidy one —
# nobody reads "呃" on screen — while search keeps the raw one, because search
# answers "what was said".
# --------------------------------------------------------------------------
class TestSubtitleSource(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_polish_is_preferred_when_present(self):
        self._write("rec_a.md", "[00:00:01] Speaker 1: raw words\n")
        self._write("polish/rec_a.md", "[00:00:01] Speaker 1: tidy words\n")
        self.assertEqual(self.cache / "polish" / "rec_a.md", to_srt.subtitle_source("rec_a"))

    def test_transcript_is_used_when_no_polish(self):
        self._write("rec_b.md", "[00:00:01] Speaker 1: raw words\n")
        self.assertEqual(self.cache / "rec_b.md", to_srt.subtitle_source("rec_b"))

    def test_missing_recording_returns_the_transcript_path(self):
        """So the caller's own 'not cached' error still fires, with the path the
        user expects to see named in it."""
        self.assertEqual(self.cache / "rec_c.md", to_srt.subtitle_source("rec_c"))

    def test_empty_polish_file_is_not_preferred(self):
        """A zero-byte polish would silently produce an empty subtitle file —
        the failure that looks like success."""
        self._write("rec_d.md", "[00:00:01] Speaker 1: raw words\n")
        self._write("polish/rec_d.md", "")
        self.assertEqual(self.cache / "rec_d.md", to_srt.subtitle_source("rec_d"))

    def test_the_cli_actually_uses_the_preference(self):
        """Testing `subtitle_source` alone proves the part works, not that it is
        wired in — deleting the call from main() left every other test green.
        Exercise the command, not the helper."""
        self._write("rec_e.md", "[00:00:01] Speaker 1: 呃 那個 raw wording\n")
        self._write("polish/rec_e.md", "[00:00:01] Speaker 1: tidy wording\n")
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "to_srt.py"), "rec_e"],
            capture_output=True, text=True, env=cli_env(self.cache),
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)
        self.assertNotIn("raw wording", out.stdout)

    def test_the_cli_falls_back_when_no_polish(self):
        self._write("rec_f.md", "[00:00:01] Speaker 1: only the raw exists\n")
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "to_srt.py"), "rec_f"],
            capture_output=True, text=True, env=cli_env(self.cache),
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("only the raw exists", out.stdout)


# --------------------------------------------------------------------------
# Subtitle source as a preference, not a decision (#29)
#
# #22 established the split — subtitles from the polish, search from the raw —
# and hardcoded it. Which one your *subtitles* use is a preference, though:
# clean reads better on screen, verbatim keeps the disfluency that qualitative
# work measures. Both answers are right; which one depends on the work.
#
# Search is NOT configurable and deliberately so: polish is the same speech
# reworded, so searching it returns sentences nobody said. That is #28's
# unanswered labelling question, not a preference.
# --------------------------------------------------------------------------
class TestSourcePreference(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def _both(self, rec_id: str) -> None:
        self._write(f"{rec_id}.md", "[00:00:01] Speaker 1: 呃 那個 就是 raw wording\n")
        self._write(f"polish/{rec_id}.md", "[00:00:01] Speaker 1: tidy wording\n")

    def test_polished_is_still_the_default(self) -> None:
        """The default must not move — an existing user with no config file has
        to see exactly what they saw yesterday."""
        self._both("r1")
        self.assertEqual(self.cache / "polish" / "r1.md", to_srt.subtitle_source("r1"))

    def test_verbatim_takes_the_raw_transcript_even_though_polish_exists(self) -> None:
        self._both("r2")
        self.assertEqual(self.cache / "r2.md",
                         to_srt.subtitle_source("r2", prefer="verbatim"))

    def test_verbatim_with_no_polish_is_still_the_raw_transcript(self) -> None:
        self._write("r3.md", "[00:00:01] Speaker 1: only raw\n")
        self.assertEqual(self.cache / "r3.md",
                         to_srt.subtitle_source("r3", prefer="verbatim"))


class TestDifferingSample(unittest.TestCase):
    """The pair of lines that makes the question answerable.

    Asking "polished or verbatim?" in the abstract is not answerable — the user
    has not seen either. Asking it beside the same line rendered both ways is.
    That is the whole reason this function exists.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> None:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_returns_the_first_segment_that_actually_differs(self) -> None:
        """Not the first segment — the first *differing* one. A recording whose
        opening line has no fillers would otherwise preview two identical lines
        and demonstrate nothing."""
        self._write("s1.md",
                    "[00:00:01] A: same opening\n[00:00:05] A: 呃 那個 the budget\n")
        self._write("polish/s1.md",
                    "[00:00:01] A: same opening\n[00:00:05] A: the budget\n")
        sample = to_srt.differing_sample("s1")
        self.assertIsNotNone(sample)
        self.assertIn("呃 那個 the budget", sample["verbatim"])
        self.assertEqual("A: the budget", sample["polished"])

    def test_returns_none_when_there_is_no_polish(self) -> None:
        self._write("s2.md", "[00:00:01] A: only raw\n")
        self.assertIsNone(to_srt.differing_sample("s2"))

    def test_returns_none_when_the_two_versions_are_identical(self) -> None:
        """The fifth state the issue's table did not list. A recording with no
        fillers to thin has nothing to choose between — asking anyway would be
        a question with no information in it."""
        text = "[00:00:01] A: nothing to thin here\n"
        self._write("s3.md", text)
        self._write("polish/s3.md", text)
        self.assertIsNone(to_srt.differing_sample("s3"))

    def test_returns_none_when_polish_is_empty(self) -> None:
        self._write("s4.md", "[00:00:01] A: raw\n")
        self._write("polish/s4.md", "")
        self.assertIsNone(to_srt.differing_sample("s4"))


class TestConfigurableLineLimits(unittest.TestCase):
    def test_default_limits_still_apply_when_none_passed(self) -> None:
        """The existing single-argument call sites must keep working unchanged."""
        text = "一二三四五六七八九十一二三四五六七八九十一二三"
        self.assertEqual(to_srt.wrap_cue_text(text),
                         to_srt.wrap_cue_text(text, limits=to_srt.LINE_LIMITS))

    def test_a_narrower_cjk_limit_wraps_sooner(self) -> None:
        text = "一二三四五六七八九十一二"      # 12 chars: under the default 20
        self.assertNotIn("\n", to_srt.wrap_cue_text(text))
        wrapped = to_srt.wrap_cue_text(text, limits={"latin": 42, "cjk": 5})
        self.assertIn("\n", wrapped)
        self.assertTrue(all(len(line) <= 5 for line in wrapped.split("\n")))

    def test_a_narrower_latin_limit_wraps_sooner(self) -> None:
        text = "the quick brown fox jumps over the lazy dog"
        wrapped = to_srt.wrap_cue_text(text, limits={"latin": 12, "cjk": 20})
        self.assertTrue(all(len(line) <= 12 for line in wrapped.split("\n")
                            if " " in line or len(line) <= 12))

    def test_render_srt_threads_the_limits_through(self) -> None:
        """Testing wrap_cue_text alone proves the part works, not that render_srt
        hands it anything — the #22 lesson, applied one layer up."""
        cues = [{"start": 0.0, "end": 2.0, "text": "一二三四五六七八九十"}]
        self.assertIn("\n", to_srt.render_srt(cues, limits={"latin": 42, "cjk": 3}))


class TestSourcePreferenceCLI(unittest.TestCase):
    """The wiring, not the parts.

    #22 shipped a passing test for `subtitle_source()` while `main()` could have
    stopped calling it entirely without a single test going red. Everything here
    runs the actual command.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        self.config = self.cache / "config.json"

    def _write(self, rel: str, text: str) -> None:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _both(self, rec_id: str) -> None:
        self._write(f"{rec_id}.md", "[00:00:01] Speaker 1: 呃 raw wording\n")
        self._write(f"polish/{rec_id}.md", "[00:00:01] Speaker 1: tidy wording\n")

    def _run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
            env=cli_env(self.cache, PLAUD_CONFIG=str(self.config), **env_extra))

    def _set_config(self, **keys) -> None:
        self.config.write_text(json.dumps(keys), encoding="utf-8")

    def test_config_file_preference_reaches_the_output(self) -> None:
        self._both("c1")
        self._set_config(subtitle_source="verbatim")
        out = self._run("c1")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)
        self.assertNotIn("tidy wording", out.stdout)

    def test_flag_beats_config_file(self) -> None:
        self._both("c2")
        self._set_config(subtitle_source="verbatim")
        out = self._run("c2", "--source", "polished")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_env_beats_config_file(self) -> None:
        self._both("c3")
        self._set_config(subtitle_source="polished")
        out = self._run("c3", PLAUD_SUBTITLE_SOURCE="verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)

    def test_flag_beats_env(self) -> None:
        self._both("c4")
        out = self._run("c4", "--source", "polished", PLAUD_SUBTITLE_SOURCE="verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_no_config_still_means_polished(self) -> None:
        self._both("c5")
        out = self._run("c5")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_line_limits_from_config_reach_the_output(self) -> None:
        self._write("c6.md", "[00:00:01] A: 一二三四五六七八九十\n")
        self._set_config(srt_line_limits={"cjk": 3})
        out = self._run("c6", "--no-speaker")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("一二三\n", out.stdout)

    def test_preview_sources_prints_both_and_exits_zero(self) -> None:
        self._both("c7")
        out = self._run("c7", "--preview-sources")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)
        self.assertIn("tidy wording", out.stdout)

    def test_preview_sources_exits_3_when_there_is_no_choice(self) -> None:
        """Exit 3, not exit 0 with empty output.

        An empty stdout that exits 0 is indistinguishable from success, and a
        caller branching on it would ask the user to choose between two things
        it never found.
        """
        self._write("c8.md", "[00:00:01] A: only raw\n")
        out = self._run("c8", "--preview-sources")
        self.assertEqual(3, out.returncode)

    def test_preview_sources_exits_3_when_versions_are_identical(self) -> None:
        text = "[00:00:01] A: nothing to thin\n"
        self._write("c9.md", text)
        self._write("polish/c9.md", text)
        out = self._run("c9", "--preview-sources")
        self.assertEqual(3, out.returncode)

    def test_unknown_config_key_warns_but_subtitles_still_come_out(self) -> None:
        """A typo costs you the preference, never the work."""
        self._both("c10")
        self._set_config(subtitle_soruce="verbatim")
        out = self._run("c10")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("subtitle_soruce", out.stderr)
        self.assertIn("tidy wording", out.stdout, "the typo must not silently take effect")


class TestTheAdviceHintIsPinned(unittest.TestCase):
    """`CUE_SHAPED` picks which advice the warning gives. Nothing tested it.

    Round 4 demoted it from denominator to hint, and the tests that had pinned
    it went with the denominator they were really about. Setting it to a pattern
    that never matches left all 527 tests green — so the three-way advice could
    silently collapse to one branch and no run would say so.

    Round 3 recorded a DA finding of this shape as "did not reproduce", and that
    verdict was correct against round-3 code. Round 4's fix made it true. A
    "did not reproduce" is a statement about the code at that moment, not a
    property that survives the next commit.
    """

    def _advice(self, bad_line: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n"
                f"[00:00] S: ok\n{bad_line}\n", encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt")],
                capture_output=True, text=True, env=cli_env(cache)).stderr

    def test_a_timestamped_line_is_sent_to_the_contract(self):
        err = self._advice("[99999:00] S: five-digit minutes")
        self.assertIn("carries a timestamp", err,
                      f"a line that DOES carry a timestamp was not recognised as "
                      f"one, so the advice sent it to the wrong place: {err!r}")

    def test_a_line_with_no_timestamp_is_not(self):
        err = self._advice("just some prose in the body")
        self.assertIn("no recognisable timestamp", err,
                      f"a prose line was told to go grow the timestamp contract: {err!r}")

    def test_an_indented_line_gets_the_only_advice_that_works(self):
        err = self._advice("  [00:10] S: indented")
        self.assertIn("column", err,
                      f"an indented line was sent to 'grow the contract', which "
                      f"cannot ever make it parse: {err!r}")


class TestTheIncompleteGuardCanFireOnTheDefaultSource(unittest.TestCase):
    """It structurally could not — the same failure class as this PR's subject.

    `subtitle_source` prefers `polish/<id>.md`, and `cache.py:465` writes polish,
    summary and outline as a bare body with NO frontmatter. So `"complete: false"
    in raw[:400]` tested a string that can never appear in the file actually
    being read: a recording indexed incompletely produced subtitles that stop
    partway, with the one warning built to explain that silence unable to fire.
    """

    def test_an_incomplete_recording_warns_even_when_polish_is_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: false\n---\n\n[00:00] S: x\n",
                encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(
                "[00:00] S: x\n", encoding="utf-8")
            err = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt")],
                capture_output=True, text=True, env=cli_env(cache)).stderr
        self.assertIn("incomplete", err,
                      f"the transcript is marked incomplete and the subtitles come "
                      f"from the polish beside it, which carries no frontmatter to "
                      f"say so — the warning could not fire: {err!r}")


class TestThereIsOnlyOneParse(unittest.TestCase):
    """`parse_segments` must not become a second implementation.

    Two derivations of one quantity drifting apart is the hazard that cost this
    issue four rounds. A convenience wrapper is fine; a copy of the loop is the
    same trap in a new place, and it would drift the moment somebody fixes a
    bug in one and not the other.
    """

    CASES = [
        "[00:00] S: a\n[00:10] S: b\n",
        "[00:00] S: a\nnot a cue\n\n[00:20] S: c\n",
        "- [00:10] S: bullet\n",
        "[99999:00] S: out of contract\n",
        "[00:10 - banana] S: malformed end\n",
        "",
        "\n\n\n",
    ]

    def test_the_wrapper_returns_exactly_the_pass_result(self):
        for body in self.CASES:
            with self.subTest(body=body[:24]):
                self.assertEqual(to_srt.parse_transcript(body)[0],
                                 to_srt.parse_segments(body),
                                 "parse_segments and parse_transcript disagree — "
                                 "they are two implementations now")

    def test_the_wrapper_is_exactly_the_one_pass(self):
        """Behavioural since #57 round 2, after two structural versions fell;
        tightened in round 3 after the behavioural one fell too.

        Version 1 grepped the body for `SEGMENT.match`; #57 moved every match
        behind a helper and the needle went dead. Version 2 forbade loop NODES
        and required a call to `parse_transcript` — a reviewer wrote a full
        second derivation with `map`/`filter`, called `parse_transcript` for
        nothing, and stayed green. Version 3 substituted a sentinel LIST and
        asserted identity of the returned object — a reviewer parsed the body
        a second time with a copied pattern, wrote the answer INTO that list
        (`cues[:] = mine`), and returned the very sentinel: 8.9 s at 12 800
        characters, 646 tests green. Identity of the container is not identity
        of the contents.

        So the sentinel is now a tuple of read-only mappings — nothing can be
        written into it, only replaced, and the round-3 attack dies on the
        slice assignment with a TypeError before any assertion runs. That
        immutability is what does the work (round 5 pointed out that a
        per-element identity loop AFTER the container check could never
        fail, so it is gone); the identity check on the container and on the
        text the one pass received are what remain. A
        derivation that runs and DISCARDS its result is not visible here; it
        is visible to the timing family in `TestSegmentMatchingHasOneEntryPoint`,
        which times `parse_segments` as one of its seven paths.
        """
        body = "[00:10] S: x\n[00:20] S: y\n"
        cue = types.MappingProxyType
        sentinel = (cue({"start": 0.0, "end": None, "speaker": "", "text": "x"}),
                    cue({"start": 10.0, "end": None, "speaker": "", "text": "y"}))
        with mock.patch.object(to_srt, "parse_transcript",
                               return_value=(sentinel, [], [], [])) as one_pass, \
             mock.patch.object(to_srt, "_match_segment",
                               side_effect=AssertionError(
                                   "parse_segments consulted the matcher itself — "
                                   "that is a second derivation")):
            got = to_srt.parse_segments(body)
        one_pass.assert_called_once_with(body)
        self.assertIs(body, one_pass.call_args.args[0],
                      "the text handed to the one pass is not the caller's object — "
                      "something pre-processed it first")
        self.assertIs(sentinel, got,
                      "parse_segments returned something other than the very "
                      "object parse_transcript produced — a copy, a re-parse, or "
                      "a merge is a second derivation")


class TestFrontmatterIsDecidedByPositionAndByKind(unittest.TestCase):
    """Round 6: the last look-like test goes.

    Every failure in this series was a shape question. Does this line look like
    a cue (rounds 1-3). Does this block look like frontmatter (round 5). A shape
    question has to enumerate, the enumeration is finite, and producer drift is
    not — so there is always a next shape, and it is always found by someone
    else.

    Two facts replace the guess, and neither is a shape:

      WHICH KIND OF FILE.  `cache.py` writes frontmatter for `--kind transcript`
        and writes polish, summary and outline as bare bodies (`cache.py:465`).
        `subtitle_source` knows which one it handed back. The caller does not
        have to infer what it is looking at.
      WHERE THE LINES ARE.  When a frontmatter block is expected and the first
        line is a delimiter, the block runs to the next delimiter — whatever
        those lines contain. Position, not resemblance.

    Round 5 asked instead whether each line looked like `key: value`, which ate
    `Alice: [00:00] opening statement` — speaker-labelled dialogue carrying a
    timestamp, the most likely thing a drifting producer writes — and, when the
    check failed, kept the block and rendered `[00:01] metadata` into the
    subtitles as if somebody had said it.

    Nothing is invisible either way: the frontmatter line count comes back from
    the same call, so a misjudged region is reportable rather than silent.
    """

    FRONT = "---\nid: abc123\nname: \"x\"\ncomplete: true\n---\n\n"

    def test_a_bare_body_keeps_lines_that_look_like_frontmatter(self):
        """Polish files have no frontmatter, so nothing may be consumed as it."""
        cues, skipped, front, _ = to_srt.parse_transcript(
            "---\nAlice: [00:00] opening statement\n---\n[00:20] S: real\n",
            expect_frontmatter=False)
        self.assertEqual([], front, "a bare body had lines eaten as frontmatter")
        self.assertEqual(1, len(cues))
        self.assertEqual(3, len(skipped),
                         f"the two delimiters and the dialogue line are content "
                         f"here and must be counted: {skipped!r}")

    def test_a_transcript_block_is_taken_whole_whatever_it_contains(self):
        """By position. `Alice:` and `[00:01]` alike — the region is the region."""
        cues, skipped, front, _ = to_srt.parse_transcript(
            "---\nAlice: [00:00] opening\n[00:01] metadata\n---\n[00:20] S: real\n",
            expect_frontmatter=True)
        self.assertEqual(4, len(front),
                         f"the frontmatter region is delimiter-to-delimiter: {front!r}")
        self.assertEqual(1, len(cues),
                         f"a line INSIDE the frontmatter became a subtitle — round 5 "
                         f"turned silent deletion into silent fabrication: {cues!r}")

    def test_the_frontmatter_count_is_never_invisible(self):
        """Whatever the region turns out to be, the caller is told its size."""
        _, _, front, _ = to_srt.parse_transcript(self.FRONT + "[00:00] S: a\n",
                                              expect_frontmatter=True)
        self.assertEqual(5, len(front), f"{front!r}")

    def test_an_unterminated_delimiter_is_content_not_frontmatter(self):
        cues, skipped, front, _ = to_srt.parse_transcript(
            "---\nid: abc\n[00:00] S: a\n", expect_frontmatter=True)
        self.assertEqual([], front, "a block with no closing delimiter was consumed")
        self.assertEqual(1, len(cues))
        self.assertEqual(2, len(skipped))

    def test_the_default_source_no_longer_loses_speaker_labelled_dialogue(self):
        """Round 5's blocking repro, through the CLI, on the preferred source."""
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(self.FRONT + "[00:00] S: x\n",
                                             encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(
                "---\nAlice: [00:00] opening statement\nBob: [00:10] response\n"
                "---\n[00:20] S: final line\n", encoding="utf-8")
            out = cache / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out)],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertIn("did not parse", proc.stderr,
                      f"speaker-labelled dialogue carrying timestamps vanished "
                      f"from the cues AND the count: out={proc.stdout!r} "
                      f"err={proc.stderr!r}")

    def test_no_shape_test_decides_the_region(self):
        """Mechanical: the region must not be chosen by what the lines look like."""
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("_FRONT_LINE", src,
                         "a pattern for what frontmatter LOOKS LIKE is back. That "
                         "is the construct that failed in rounds 1, 2, 3 and 5; "
                         "the region is decided by kind and position now")


class TestEveryLineEndsInExactlyOneReportableBucket(unittest.TestCase):
    """Round 7: the count was never the problem. The silence around it was.

    Six rounds removed something from the input before or beside the count, and
    every time the removed thing was reported to nobody. Look-like tests were
    one way to remove it; a position rule was another. Round 6's rule is right —
    the region is delimiter to delimiter, whatever it contains — and it still
    consumed lines that nothing ever mentioned, on the cache path as well as
    `--file`, which is #50's signature for the sixth time.

    So the missing sentence is an accounting one: every line of the input lands
    in exactly one of three buckets — header, cue, dropped — and every bucket is
    reportable. `front` was already returned and already unpacked; nothing read
    it.

    Note what this is NOT: checking whether the consumed lines *look* like
    frontmatter would be the look-like test again. Saying how many lines were
    consumed, and how many of them the parser would have accepted as cues, is a
    statement of fact about what was removed — not a rule for deciding what to
    remove.
    """

    def _run(self, body: str, *args: str, name: str = "abc123") -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / f"{name}.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), name, "-o", str(cache / "o.srt"), *args],
                capture_output=True, text=True, env=cli_env(cache))

    def test_cue_lines_inside_the_header_region_are_reported(self):
        """Round 6's blind spot, on the NORMAL cache path."""
        proc = self._run("---\n"
                         "id: abc123\n"
                         "complete: true\n"
                         "[00:00] S: eaten one\n"
                         "[00:10] S: eaten two\n"
                         "---\n"
                         "[00:20] S: kept\n")
        self.assertIn("header", (proc.stdout + proc.stderr).lower(),
                      f"two cue-shaped lines were consumed as a header and "
                      f"nothing said so: out={proc.stdout!r} err={proc.stderr!r}")
        self.assertEqual(2, numbers_in(proc.stderr)["header_ate"],
                         f"the count of consumed cue-shaped lines is wrong: "
                         f"{proc.stderr!r}")

    def test_an_ordinary_header_is_not_worth_a_word(self):
        """The accounting must not turn every clean run into a warning."""
        proc = self._run("---\nid: abc123\nname: \"x\"\ncomplete: true\n---\n\n"
                         "[00:00] S: a\n[00:10] S: b\n")
        self.assertEqual("", proc.stderr.strip(),
                         f"a normal file with normal frontmatter warned: {proc.stderr!r}")

    def test_the_buckets_add_up(self):
        """header + cues + dropped == every non-blank line. No fourth outcome."""
        # The last fixture carries a U+2028. The oracle below counts
        # `\n`-delimited lines because that is what the FORMAT means by a
        # line; `str.splitlines()` — which both the oracle and the parser used
        # to call — breaks on five more characters, so the two agreed with
        # each other and neither agreed with the file. Round 20 fixed the
        # parser; an oracle left on the old rule would now report a gap that
        # is not there, which is the same defect wearing the other sign.
        for body in ("---\nid: a\n---\n\n[00:00] S: x\nprose\n",
                     "[00:00] S: x\n---\n[00:10] S: y\n",
                     "---\nid: a\n[00:00] S: in header\n---\n[00:10] S: out\n",
                     "no header at all\n[00:00] S: x\n",
                     f"[00:00] S: one{chr(0x2028)}two\n[00:10] S: three\n"):
            for expect in (True, False):
                with self.subTest(body=body[:20], expect_frontmatter=expect):
                    cues, dropped, front, _ = to_srt.parse_transcript(
                        body, expect_frontmatter=expect)
                    non_blank = [l for l in body.split("\n") if l.strip()]
                    accounted = len(cues) + len(dropped) + len(
                        [l for l in front if l.strip()])
                    self.assertEqual(
                        len(non_blank), accounted,
                        f"{len(non_blank) - accounted} line(s) fell outside every "
                        f"bucket — that gap is where six rounds of silence lived")


class TestEveryPreviewRefusalNamesItsCause(unittest.TestCase):
    """Round 7: retire the enumeration instead of growing it.

    SKILL.md's table said exit 3 had three causes. `differing_sample` had at
    least six, three of them returning `None` in total silence — so the operator
    applied the table, picked one of the two silent listed causes, and told the
    user something that was not true. Growing a closed list is what round 6 did
    and it drifted again in the same commit.

    A list that must be kept in step with the code is the wrong shape. Every
    refusal states its own cause on stderr, and the table then needs no list at
    all: read stderr, it always says why. Same move as the negative denominator.

    Two of those silent causes were real defects, not just undocumented:

      - a dict keyed on `start` collapsed duplicate timestamps, so two DIFFERENT
        segments were offered as one line written two ways — round 5's
        fabricated comparison, through a new mechanism;
      - two clean files whose timelines simply differ shared no key at all, so
        the loop found nothing and returned `None`, which the table reads as
        "the two versions are identical".
    """

    def _preview(self, polish: str, verbatim: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n" + verbatim, encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_duplicate_start_times_do_not_fabricate_a_pair(self):
        proc = self._preview(
            "[00:00] S: polished FIRST\n[00:00] S: polished SECOND\n",
            "[00:00] S: verbatim FIRST\n[00:00] S: verbatim SECOND\n")
        self.assertNotIn("verbatim SECOND", proc.stdout,
                         f"a dict keyed on start collapsed the duplicates and "
                         f"paired two different segments: {proc.stdout!r}")

    def test_differing_timelines_say_so_rather_than_going_quiet(self):
        proc = self._preview("[00:01] S: polished A\n[00:11] S: polished B\n",
                             "[00:00] S: verbatim A\n[00:10] S: verbatim B\n")
        self.assertEqual(3, proc.returncode)
        # "stderr is non-empty" is not a check on the moment it names. The
        # timestamp is the whole content of this refusal — it is where the user
        # is told to look — and rebinding either side left this green.
        self.assertIn("diverge at 00:00:00,000", proc.stderr,
                      f"the refusal must name the earliest moment the two "
                      f"sides disagree: {proc.stderr!r}")
        # And from the other side. With only the case above, `min(p_start,
        # v_start)` always resolves to `v_start`, so mutating `p_start` cannot
        # change the printed value and the assertion is untestable rather than
        # passing — a distinction `tests/mutate.py` reports but cannot make for
        # you. Reversing which side is earlier makes both names load-bearing.
        rev = self._preview("[00:00] S: polished A\n[00:10] S: polished B\n",
                            "[00:01] S: verbatim A\n[00:11] S: verbatim B\n")
        self.assertIn("diverge at 00:00:00,000", rev.stderr,
                      f"with the polished side earlier, the refusal must still "
                      f"name the earliest moment: {rev.stderr!r}")
        self.assertTrue(proc.stderr.strip(),
                        "the two sources have different timelines and the tool "
                        "exited 3 in silence, which the operator's table reads "
                        "as 'the two versions are identical'")

    def test_every_exit_three_states_a_reason(self):
        """The property that replaces the list."""
        cases = {
            "no polish": (None, "[00:00] S: a\n"),
            "polish empty": ("", "[00:00] S: a\n"),
            "identical": ("[00:00] S: a\n", "[00:00] S: a\n"),
            "polish parses to nothing": ("not a cue at all\n", "[00:00] S: a\n"),
            "timelines differ": ("[00:01] S: a\n", "[00:00] S: b\n"),
            "one side dropped": ("[99999:00] S: a\n[00:00] S: b\n", "[00:00] S: b\n"),
        }
        for label, (polish, verbatim) in cases.items():
            with self.subTest(cause=label):
                with tempfile.TemporaryDirectory() as d:
                    cache = pathlib.Path(d)
                    (cache / "polish").mkdir()
                    (cache / "abc123.md").write_text(
                        "---\nid: abc123\ncomplete: true\n---\n\n" + verbatim,
                        encoding="utf-8")
                    if polish is not None:
                        (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
                    proc = subprocess.run(
                        [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                        capture_output=True, text=True, env=cli_env(cache))
                # `continue` here meant a case that STOPPED refusing was
                # silently dropped from the property — all six could turn into
                # pairs and this would still pass. Every case in the table is
                # chosen because it has nothing to offer; if one starts
                # offering something, that is the finding, not an exemption.
                self.assertEqual(
                    3, proc.returncode,
                    f"{label!r} no longer refuses — it returned "
                    f"{proc.returncode} with {proc.stdout!r}. Either the case "
                    f"is no longer causeless, or the tool now shows a pair it "
                    f"cannot justify; decide which rather than skipping it")
                self.assertTrue(
                    proc.stderr.strip(),
                    f"exit 3 for {label!r} with nothing on stderr. The operator "
                    f"cannot tell it apart from any other cause and will state "
                    f"one of them to the user")

    def test_a_cue_eaten_by_a_header_refuses_too(self):
        """`_cue_lines` threw `front` away, so the preview could not see this.

        One root cause, two exits: round 7 taught `main` to read the header and
        left the second call site unchanged. A transcript whose header swallowed
        a real cue has one fewer moment than the polish beside it, which is the
        same asymmetry a parser drop creates and the same fabricated pair at the
        end of it — quoted to the user, then stored as a preference.

        Caught by mutation, not by reading: setting `header_ate = []` left the
        whole suite green.
        """
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n"
                "[00:00] S: swallowed by the header\n"
                "---\n[00:10] S: um so the budget\n", encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(
                "[00:10] S: so the budget\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertEqual(3, proc.returncode,
                         f"a header ate a cue on one side and the comparison went "
                         f"ahead anyway: {proc.stdout!r}")
        self.assertTrue(proc.stderr.strip(), "refused without saying why")

    def test_a_real_difference_is_still_offered(self):
        proc = self._preview("[00:00] S: thinned\n", "[00:00] S: um, thinned\n")
        self.assertEqual(0, proc.returncode, f"{proc.stdout!r} {proc.stderr!r}")


class TestTheHeaderBucketIsReportedNegatively(unittest.TestCase):
    """Round 8: the reporter was a positive test, one region over.

    Round 7 made the header bucket reportable — but gated the report on
    `SEGMENT.match`, so the header reported only the subset the parser already
    accepts. Every shape the parser cannot read was invisible to the thing
    written to report the parser's blindness. That is the round-1/2/3 construct,
    reintroduced inside its own fix, and it swallowed the five prefixes this
    branch itself names.

    The size of the header needs no shape test. It is a number, it is always
    true, and stating it makes the ledger close: cues + dropped + header ==
    every non-blank line, with all three visible.
    """

    def _run(self, body: str, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt"), *args],
                capture_output=True, text=True, env=cli_env(cache))

    def test_the_five_prefixes_inside_a_header_are_accounted_for(self):
        """Round 7's CRITICAL, on the cache path."""
        proc = self._run("---\n"
                         "id: abc123\n"
                         "complete: true\n"
                         "- [00:00] S: eaten one\n"
                         "> [00:10] S: eaten two\n"
                         "1. [00:20] S: eaten three\n"
                         "---\n"
                         "[00:30] S: kept\n")
        combined = proc.stdout + proc.stderr
        self.assertIn("7 header", combined,
                      f"seven lines were consumed as a header, three of them "
                      f"speech, and neither stream states the region's size: "
                      f"out={proc.stdout!r} err={proc.stderr!r}")
        # The LEDGER is the guarantee: 1 cue + 0 dropped + 7 header accounts for
        # every line, so a reader can see that seven lines went somewhere and ask
        # why. The sharper "three of those were speech" does NOT fire here, and
        # cannot: `- [00:00] …` is not a shape any parser in this file accepts,
        # so calling it speech is a judgement, not a fact. Round 7 made that
        # judgement the ONLY signal and the whole loss went silent. It is a bonus
        # on top of the count now, and the count is what closes.
        self.assertIn("wrote 1 cues", proc.stdout)

    def test_a_line_the_parser_cannot_read_is_still_counted(self):
        """The gate was `SEGMENT.match`; this line fails it and is still content."""
        proc = self._run("---\nid: abc123\nAlice: [00:00] opening statement\n---\n"
                         "[00:20] S: kept\n")
        self.assertIn("header", (proc.stdout + proc.stderr).lower(),
                      f"round 5's exact fixture, consumed by a header, silent again: "
                      f"out={proc.stdout!r} err={proc.stderr!r}")

    def test_the_ledger_closes(self):
        """cues + dropped + header == every non-blank line, and all three shown."""
        proc = self._run("---\nid: abc123\nAlice: [00:00] eaten\n---\n"
                         "[00:20] S: kept\nprose line\n")
        # Exact integers. The first version asserted the SUBSTRING "3" for a
        # header that is 4, and passed because `abc123.md` contains a 3 — the
        # antipattern this very file documents as a round-1 finding, inside the
        # class written to retire it, under a docstring claiming it demonstrated
        # the sum. It demonstrated neither.
        self.assertEqual({"cues": 1, "header": 4, "dropped": 1},
                         numbers_in(proc.stdout),
                         f"ledger wrong: {proc.stdout!r}")

    def test_a_clean_run_still_reads_cleanly(self):
        """Accounting must not turn every success into a wall of numbers."""
        proc = self._run("---\nid: abc123\nname: \"x\"\ncomplete: true\n---\n\n"
                         "[00:00] S: a\n[00:10] S: b\n")
        self.assertEqual("", proc.stderr.strip(),
                         f"a normal file warned: {proc.stderr!r}")
        self.assertIn("wrote 2 cues", proc.stdout)


class TestADiscardedEndIsNotSilent(unittest.TestCase):
    """A range whose end will not parse loses a declared time and says nothing.

    The line survives — that trade is deliberate and right, words over timing.
    But the discarded end lands in no bucket: the cue is counted as a cue, the
    line is not dropped, and nothing mentions that a time the producer wrote was
    thrown away. `build_cues` then invents a replacement from the next cue's
    start, so the output carries a fabricated duration with no marker.
    """

    def test_a_malformed_end_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00 - banana] S: the declared end is gone\n"
                "[00:10] S: fine\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt")],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertIn("end", proc.stderr.lower(),
                      f"a declared end was discarded and replaced with a guess, "
                      f"silently: {proc.stderr!r}")


class TestTheLedgerNumbersAreTheOnesMeasured(unittest.TestCase):
    """Round 10: the guarantee's own arithmetic was unverified.

    Round 8 called the stdout line a ledger and said it was the guarantee. Three
    of its four numbers could be corrupted by +40 with the whole suite green, and
    the fourth — the one test that did assert a header count — asserted it as a
    SUBSTRING: `"47 header"` contains `"7 header"`, so it passed under corruption
    too.

    A substring assertion on a number is very nearly no assertion. Most of the
    numeric checks this branch accumulated over nine rounds have that shape: they
    assert a word appears, not that a value is right. Nothing was checking the
    checkers.

    These parse the ledger and compare exact integers, on every shape that prints
    one.
    """

    # Greedy to the LAST `)`. The first version stopped at the first one, which
    # is inside `content line(s)`, so the dropped count was silently cut off —
    # a parser that stops where it should not, verifying a parser that stopped
    # where it should not. Substring assertions never hit this class of bug
    # because they check almost nothing.
    LEDGER = re.compile(r"wrote (?P<cues>\d+) cues(?: to stdout)?"
                        r"(?: \((?P<parts>.*)\))?")

    def _ledger(self, out: str) -> dict:
        m = self.LEDGER.search(out)
        self.assertIsNotNone(m, f"no ledger line in {out!r}")
        got = {"cues": int(m.group("cues")), "header": 0, "dropped": 0}
        for part in (m.group("parts") or "").split(","):
            n = re.search(r"(\d+)", part)
            if not n:
                continue
            if "header" in part:
                got["header"] = int(n.group(1))
            elif "dropped" in part:
                got["dropped"] = int(n.group(1))
        return got

    def _run(self, body: str, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", *args],
                capture_output=True, text=True, env=cli_env(cache))

    CASES = [
        # body, cues, header, dropped
        # header counts NON-BLANK lines in the region, so the blank line after
        # the closing `---` belongs to nobody — which is right, and is why the
        # sum below is against non-blank lines too.
        ("---\nid: abc123\ncomplete: true\n---\n\n[00:00] S: a\n[00:10] S: b\n",
         2, 4, 0),
        ("---\nid: abc123\ncomplete: true\n---\n\n[00:00] S: a\nprose\n",
         1, 4, 1),
        ("---\nid: abc123\n- [00:00] S: eaten\n---\n[00:20] S: kept\n",
         1, 4, 0),
        ("[00:00] S: a\n[00:10] S: b\nprose\n",
         2, 0, 1),
    ]

    def test_the_numbers_are_exact_with_an_output_file(self):
        for body, cues, header, dropped in self.CASES:
            with self.subTest(body=body[:26]):
                with tempfile.TemporaryDirectory() as d:
                    cache = pathlib.Path(d)
                    (cache / "abc123.md").write_text(body, encoding="utf-8")
                    proc = subprocess.run(
                        [sys.executable, str(SCRIPT), "abc123",
                         "-o", str(cache / "o.srt")],
                        capture_output=True, text=True, env=cli_env(cache))
                self.assertEqual({"cues": cues, "header": header, "dropped": dropped},
                                 self._ledger(proc.stdout),
                                 f"ledger wrong: {proc.stdout!r}")

    def test_the_numbers_are_exact_when_streaming(self):
        """Streaming puts the ledger on stderr; it was pinned by nothing."""
        for body, cues, header, dropped in self.CASES:
            if not header and not dropped:
                continue          # nothing to print, and that is correct
            with self.subTest(body=body[:26]):
                proc = self._run(body)
                self.assertEqual({"cues": cues, "header": header, "dropped": dropped},
                                 self._ledger(proc.stderr),
                                 f"streaming ledger wrong: {proc.stderr!r}")

    def test_the_ledger_sums_to_every_non_blank_line(self):
        """The identity the ledger exists to make checkable, checked."""
        for body, *_ in self.CASES:
            with self.subTest(body=body[:26]):
                with tempfile.TemporaryDirectory() as d:
                    cache = pathlib.Path(d)
                    (cache / "abc123.md").write_text(body, encoding="utf-8")
                    proc = subprocess.run(
                        [sys.executable, str(SCRIPT), "abc123",
                         "-o", str(cache / "o.srt")],
                        capture_output=True, text=True, env=cli_env(cache))
                got = self._ledger(proc.stdout)
                self.assertEqual(
                    len([l for l in body.splitlines() if l.strip()]),
                    got["cues"] + got["header"] + got["dropped"],
                    f"the three numbers do not account for every line: {got} "
                    f"from {proc.stdout!r}")


class TestNoDiagnosticAssertsMoreThanItMeasured(unittest.TestCase):
    """Round 10: two messages said things that were false.

    Round 8 replaced silence with misinformation, twice, and both times through
    the same inference: `ate` being empty means THE PARSER DID NOT RECOGNISE
    THEM, and it was read as THEY WERE NOT CONTENT.

      - the zero-cue exit announced "0 content line(s) were present" for a file
        whose header had swallowed five, then named the wrong hypothesis;
      - the header sentence said "none of them look like cues, which is what a
        header normally holds" about lines that were speech.

    A wrong reassurance is worse than the silence it replaced: silence leaves
    the question open, "this is normal" stops the reader looking.
    """

    def _run(self, body: str, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt"),
                 *args], capture_output=True, text=True, env=cli_env(cache))

    ALL_SWALLOWED = ("---\nid: abc123\n"
                     "- [101:05] S: swallowed one\n"
                     "- [101:35] S: swallowed two\n"
                     "- [102:05] S: swallowed three\n"
                     "---\n")

    def test_the_zero_cue_exit_counts_the_header(self):
        proc = self._run(self.ALL_SWALLOWED)
        self.assertNotIn("0 content line(s) and 0 header", proc.stderr)
        self.assertIn("6 header line(s)", proc.stderr,
                      f"six lines were consumed as a header and the diagnostic "
                      f"does not mention the region: {proc.stderr!r}")

    def test_the_zero_cue_exit_does_not_guess_when_a_header_could_be_to_blame(self):
        proc = self._run(self.ALL_SWALLOWED)
        self.assertNotIn("most likely a recording without them", proc.stderr,
                         f"the diagnostic named the wrong hypothesis for a file "
                         f"whose problem is a region one: {proc.stderr!r}")

    def test_the_hypothesis_names_both_possibilities(self):
        """Round 11: it named ONE, and named it for a file that was all speech.

        `CUE_SHAPED` misses the markdown bullet, the blockquote, the numbered
        list, the fullwidth and the angle bracket — the five shapes this file's
        own comments enumerate — so "none matched" cannot support "there are no
        timestamps here". Two lines of bullet-prefixed speech were told they were
        probably a recording without timestamps.
        """
        proc = self._run("just some prose\nand more prose\n")
        self.assertIn("rough timestamp hint", proc.stderr,
                      f"the diagnostic states a conclusion its test cannot "
                      f"support: {proc.stderr!r}")
        self.assertIn("OR a shape the contract does not cover", proc.stderr,
                      f"only one of the two possibilities is named: {proc.stderr!r}")

    def test_bullet_prefixed_speech_is_not_called_untimestamped(self):
        """The case round 11 reproduced: 100% timestamped speech, misdiagnosed."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "bullets.md"
            f.write_text("- [00:00] S: bullet speech one\n"
                         "- [00:10] S: bullet speech two\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--file", str(f),
                 "-o", str(pathlib.Path(d) / "o.srt")],
                capture_output=True, text=True, env=cli_env(pathlib.Path(d)))
        self.assertNotIn("most likely a recording without them", proc.stderr,
                         f"two lines that both carry a timestamp were reported as "
                         f"probably having none: {proc.stderr!r}")

    def test_the_header_sentence_claims_nothing_about_speech(self):
        """`--file` is the shape where this sentence prints at all."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "drift.md"
            f.write_text("---\n- [00:00] S: bullet speech\n---\n[00:20] S: kept\n",
                         encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--file", str(f),
                 "-o", str(pathlib.Path(d) / "o.srt")],
                capture_output=True, text=True, env=cli_env(pathlib.Path(d)))
        self.assertNotIn("which is what a header normally holds", proc.stderr,
                         f"the sentence reassured about lines that are speech: "
                         f"{proc.stderr!r}")
        self.assertIn("not `key: value` lines", proc.stderr,
                      f"a bullet-prefixed line is not `key: value`, so the header "
                      f"is not the shape cache.py writes and the message should "
                      f"say which lines make it so: {proc.stderr!r}")


class TestNoPathReachesAStreamUnescaped(unittest.TestCase):
    """Round 10: the channel was named in a comment and fixed at one outlet.

    The drop warning's comment says the filename is attacker-controlled through
    `--file` and that `\x1b[1A\x1b[2K` in it erases the line reporting the
    problem. Three other outlets kept interpolating the path raw, including the
    stdout ledger — the line round 8 designated as the guarantee. One root
    cause, four exits, and only the one being written at the time was closed.
    """

    EVIL = "a\x1b[2K\x1b[1Ab\u202ec"

    def test_the_not_found_error(self):
        with tempfile.TemporaryDirectory() as d:
            missing = pathlib.Path(d) / f"{self.EVIL}.md"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--file", str(missing)],
                capture_output=True, text=True, env=cli_env(pathlib.Path(d)))
        self.assertNotIn("\x1b", proc.stderr, f"raw escape: {proc.stderr!r}")

    def test_the_zero_segment_error(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / f"{self.EVIL}.md"
            f.write_text("no timestamps here\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--file", str(f)],
                capture_output=True, text=True, env=cli_env(pathlib.Path(d)))
        self.assertNotIn("\x1b", proc.stderr, f"raw escape: {proc.stderr!r}")

    def test_the_stdout_ledger(self):
        """The line the ledger design calls the guarantee."""
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "ok.md"
            f.write_text("[00:00] S: x\n", encoding="utf-8")
            out = pathlib.Path(d) / f"{self.EVIL}.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--file", str(f), "-o", str(out)],
                capture_output=True, text=True, env=cli_env(pathlib.Path(d)))
        self.assertNotIn("\x1b", proc.stdout, f"raw escape: {proc.stdout!r}")


class TestDuplicateStartsRefuseRatherThanGuess(unittest.TestCase):
    """Equal starts say nothing about WHICH segment inside a duplicate run.

    Round 7 paired by walking both sequences and requiring equal starts. That
    fixes a displacement between groups and does nothing within one: two cues at
    `00:00` on each side pair positionally, so a displacement inside the group
    still offers two different segments as one line written two ways. The test
    written for this used sides in the same order and could not fail on it.
    """

    def _preview(self, polish: str, verbatim: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n" + verbatim, encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_a_displacement_inside_a_duplicate_group_is_never_shown(self):
        """Ambiguous groups are skipped, so no pair from one can be offered."""
        proc = self._preview(
            "[00:00] S: polished FIRST\n[00:00] S: polished SECOND\n",
            "[00:00] S: verbatim SECOND\n[00:00] S: verbatim THIRD\n")
        self.assertEqual(3, proc.returncode,
                         f"two different segments were offered as one line "
                         f"written two ways: {proc.stdout!r}")
        self.assertIn("more than once", proc.stderr,
                      f"refused without naming the reason: {proc.stderr!r}")

    def test_an_unambiguous_difference_survives_a_duplicate_elsewhere(self):
        """Round 11: refusing the whole file cost 1 in 3 real recordings.

        One repeated timestamp in a 338-cue file disabled the source-preference
        flow for all of it — to prevent a fabricated pair that has never been
        observed outside a constructed fixture. The ambiguous group is skipped;
        everything else still compares.
        """
        proc = self._preview(
            "[00:00] S: dup A\n[00:00] S: dup B\n[00:10] S: thinned\n",
            "[00:00] S: dup A\n[00:00] S: dup B\n[00:10] S: um, thinned\n")
        self.assertEqual(0, proc.returncode,
                         f"a clean difference at 00:10 was thrown away because "
                         f"00:00 is duplicated: {proc.stderr!r}")
        self.assertIn("thinned", proc.stdout)
        self.assertNotIn("dup", proc.stdout,
                         f"a pair from the ambiguous group was shown: {proc.stdout!r}")

    def test_distinct_starts_still_compare(self):
        proc = self._preview("[00:00] S: thinned\n[00:10] S: b\n",
                             "[00:00] S: um, thinned\n[00:10] S: b\n")
        self.assertEqual(0, proc.returncode, f"{proc.stdout!r} {proc.stderr!r}")


class TestEveryPrintedCountIsChecked(unittest.TestCase):
    """The suite must be able to detect a `+40` on any number the tool prints.

    Round 10 pinned the `-o` ledger and said "every shape that prints one".
    Round 11 mutated the zero-cue exit and the header warning by +40 and the
    suite stayed green — the claim was true of one shape out of three.

    These cover the remaining two. The mutation matrix in the commit message is
    the evidence for the claim; this class is what makes the matrix red.
    """

    def _run(self, body: str, *args: str, cache_id: str = "abc123"):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / f"{cache_id}.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), cache_id, *args],
                capture_output=True, text=True, env=cli_env(cache))

    def test_the_zero_cue_exit_states_both_counts_exactly(self):
        proc = self._run("---\nid: abc123\n"
                         "- [101:05] S: one\n- [101:35] S: two\n"
                         "---\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(0, got.get("zero_body"), f"{proc.stderr!r}")
        self.assertEqual(5, got.get("zero_head"), f"{proc.stderr!r}")

    def test_the_zero_cue_exit_counts_a_body_with_no_header(self):
        proc = self._run("just prose\nand more prose\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(2, got.get("zero_body"), f"{proc.stderr!r}")
        self.assertEqual(0, got.get("zero_head"), f"{proc.stderr!r}")

    def test_the_header_warning_states_both_counts_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "drift.md"
            f.write_text("---\nid: x\n[00:00] S: eaten\n[00:10] S: also eaten\n"
                         "---\n[00:20] S: kept\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--file", str(f),
                 "-o", str(pathlib.Path(d) / "o.srt")],
                capture_output=True, text=True, env=cli_env(pathlib.Path(d)))
        got = numbers_in(proc.stderr)
        self.assertEqual(5, got.get("header_all"), f"{proc.stderr!r}")
        self.assertEqual(2, got.get("header_ate"), f"{proc.stderr!r}")

    def test_the_partial_drop_warning_states_both_counts_exactly(self):
        proc = self._run("---\nid: abc123\ncomplete: true\n---\n\n"
                         "[00:00] S: ok\nprose one\nprose two\n",
                         "-o", "/dev/null")
        got = numbers_in(proc.stderr)
        self.assertEqual(2, got.get("warn_bad"), f"{proc.stderr!r}")
        self.assertEqual(3, got.get("warn_all"), f"{proc.stderr!r}")


class TestNoValueReachesAStreamUnchecked(unittest.TestCase):
    """Every interpolation in the tool is registered, and every registration bites.

    Round 14 built this to end a specific loop: three times a claim that every
    printed value was checked had turned out to cover a subset, because the
    evidence was a mutation matrix built from the extractor's own key set — it
    enumerated what the extractor covered and could never find what it omitted.

    Round 15 found the guard had inherited the defect twice over.

    First, its DETECTOR was a positive enumeration. It considered only
    interpolations whose expression contained `len(`, `unparsed` or
    `format_timestamp`, or was exactly `n`; a new stderr print spelled any
    other way was added in silence, all tests green. That is the construct the
    drop counter took four rounds to retire, rebuilt inside the guard written
    to end it. It now takes EVERY `FormattedValue` in the module, with no
    filter on spelling and no reachability argument — the two buckets below are
    exhaustive, so adding anything forces a choice.

    Second, a registration named an EXTRACTOR KEY rather than an assertion, so
    an entry could point at nothing. `header_odd` had a pattern here and an
    entry here and no test read it: injecting `odd = odd * 3` left all 594
    tests green while the README said otherwise. Every `numbers_in:` entry is
    now required to name a key that some test actually consumes.

    And the sweep that certified round 14 could not have found either, because
    it mutated the interpolation TEXT (`{x}` → `{x+40}`) — which is exactly
    what this test watches, so this test fired at every site by construction:

        len(odd)+40    → 1 failing test, and it was this one
        len(ate)+40    → 3 failing,  1 of them this one
        len(header)+40 → 2 failing,  1 of them this one

    A green-to-red conversion at every site, proving nothing about any value
    assertion. `tests/mutate.py` exists so the honest version is a command
    rather than a memory: it changes values on their own line and reports the
    failure count with this test excluded.
    """

    # A value a test verifies → the assertion that fails when it is wrong.
    # `numbers_in: <key>` means the check goes through that extractor key, and
    # the key must be consumed by some test below.
    CHECKED = {
        ("build_cues", "cue at to —", "format_timestamp(end)"):
            "TestEveryCorrectionReachesSomebody.test_the_reported_end_is_the_one_actually_written",
        ("build_cues", "cue at to —", "format_timestamp(seg['start'])"):
            "TestEveryCorrectionReachesSomebody",
        ("differing_sample", "every line that differs sits at a timestamp th", "len(ambiguous)"):
            "numbers_in: refusal_ambiguous",
        ("differing_sample", "polished and verbatim transcripts dropped and", "polish_drops"):
            "numbers_in: refusal_drop_a",
        ("differing_sample", "polished and verbatim transcripts dropped and", "verbatim_drops"):
            "numbers_in: refusal_drop_b",
        ("differing_sample", "polished transcript dropped", "polish_drops"):
            "numbers_in: refusal_drop_a",
        ("differing_sample", "verbatim transcript dropped", "verbatim_drops"):
            "numbers_in: refusal_drop_a",
        ("differing_sample", "the two versions diverge at — one has a segmen", "format_timestamp(min(p_start, v_start))"):
            "TestEveryPreviewRefusalNamesItsCause.test_differing_timelines_say_so_rather_than_going_quiet",
        ("differing_sample", "the two versions have different cue counts ( p", "len(polished)"):
            "numbers_in: refusal_polished",
        ("differing_sample", "the two versions have different cue counts ( p", "len(verbatim)"):
            "numbers_in: refusal_verbatim",
        ("format_timestamp", "::,", "hours"):   "TestTimestampFormatting",
        ("format_timestamp", "::,", "minutes"): "TestTimestampFormatting",
        ("format_timestamp", "::,", "secs"):    "TestTimestampFormatting",
        ("format_timestamp", "::,", "millis"):  "TestTimestampFormatting",
        ("main", "content line(s) and header line(s) were presen", "len(dropped)"):
            "numbers_in: zero_body",
        ("main", "content line(s) and header line(s) were presen", "len(header)"):
            "numbers_in: zero_head",
        ("main", "content line(s) dropped — see stderr", "unparsed"):
            "numbers_in: dropped",
        ("main", "declared end(s) discarded — see stderr", "len(lost_ends)"):
            "numbers_in: lost_ends",
        ("main", "header", "len(header)"):
            "numbers_in: header (the ledger)",
        ("differing_sample", "The transcript also dropped line(s), which is ", "polish_drops + verbatim_drops"):
            "numbers_in: refusal_also_dropped",
        ("differing_sample", "the transcript's header holds line(s) that are", "verbatim_odd"):
            "numbers_in: refusal_header_odd",
        ("main", "char(s) removed — see stderr", "stripped_chars"):
            "numbers_in: stripped_ledger",
        ("main", "cue end(s) corrected — see stderr", "len(trims)"):
            "numbers_in: corrections",
        ("main", "empty cue(s) removed — see stderr", "emptied"):
            "numbers_in: emptied_ledger",
        ("main", "⚠ cue(s) held nothing but control or format ch", "emptied"):
            "numbers_in: emptied_warn",
        ("main", "⚠ character(s) in the two lines above were rem", "sample['altered']"):
            "numbers_in: preview_altered",
        ("main", "…(+ more characters, not shown)", "len(line) - _PREVIEW_CAP"):
            "numbers_in: preview_more",
        ("main", "⚠ and more declared end(s) discarded — the cou", "len(lost_ends) - _SAMPLE"):
            "numbers_in: more_ends",
        ("main", "⚠ and more cue end(s) corrected — the count on", "len(trims) - _SAMPLE"):
            "numbers_in: more_trims",
        ("main", "of the content lines DID carry a timestamp and", "len(stamped)"):
            "numbers_in: zero_stamped",
        ("main", "wrote cues to stdout", "len(built)"):
            "numbers_in: cues, streaming",
        ("main", "wrote cues →", "len(built)"):
            "numbers_in: cues, -o",
        ("main", "— of them are not `key: value` lines, so this ", "len(odd)"):
            "numbers_in: header_odd",
        ("main", "— of them would have parsed as cues (first: )", "len(ate)"):
            "numbers_in: header_ate",
        ("main", "⚠ character(s) in the cue text were removed or", "stripped_chars"):
            "numbers_in: stripped_warn",
        ("main", "⚠ line(s) in were taken as the file's header a", "len(header)"):
            "numbers_in: header_all",
        ("main", "⚠ of content lines in did not parse as segment", "unparsed"):
            "numbers_in: warn_bad",
        ("main", "⚠ of content lines in did not parse as segment", "unparsed + len(segments)"):
            "numbers_in: warn_all",
        ("render_srt", "-->", "format_timestamp(cue['end'])"):   "TestRender",
        ("render_srt", "-->", "format_timestamp(cue['start'])"): "TestRender",
        ("render_srt", "-->", "n"):                              "TestRender: cue numbering",
        ("shape_of", "chars, opens with", "len(line)"):
            "TestTheWarningDoesNotPublishSomebodysWords",
        # The two run widths. A heuristic sorting this census by "looks like a
        # count" put both of these in the other bucket — and they are the
        # redaction widths, the numbers round 2 introduced so that digits could
        # be described without being published. The classification is hand-made
        # for exactly this reason.
        ("_refuse", "⚠ no source comparison to show:", "why"):
            "TestEveryRefusalCauseIsPinned",
        ("shape_of", "d{}", "j - i"): "numbers_in: shape_digits",
        ("shape_of", "s{}", "j - i"): "numbers_in: shape_spaces",
    }

    # Carries no value an assertion could be wrong about → why that is fine.
    # Being in this bucket is a claim, not a default: it says the interpolation
    # is text whose exact content no caller computes with.
    UNCHECKED = {
        ("<module>", "\\A(?:)\\Z", "_STAMP"):                     "regex assembly, not output",
        ("<module>", "^\\[\\s*(?P<ts>)\\s*(?:-(?P<end>[^\\]]*))?\\]\\s*(?:", "_STAMP"): "regex assembly",

        ("build_cues", ":", "speaker"):                            "the speaker label itself",
        ("build_cues", ":", "text"):                               "the cue words themselves",
        ("build_cues", "cue at to —", "what"):                     "which correction, tested by name",
        ("build_cues", "cue at to —", "why"):                      "the reason clause, tested by name",
        ("differing_sample", ".md", "rec_id"):                     "a path fragment",
        ("differing_sample", "the line(s), so the two sides no longer line u", "which"): "the composed side-name clause",
        ("differing_sample", "the transcript's header holds line(s) that are", "also"): "the composed second-cause clause; the count inside it is checked",
        ("main", "()", "', '.join(parts)"):                        "the ledger clauses, each checked on its own",
        ("main", ".md", "args.id"):                                "a path fragment",
        ("main", "None matched the rough timestamp hint, which m", "shape_of(dropped[0])"): "a shape, and shape_of is tested",
        ("main", "The header is the block from the first '---' t", "shape_of(header[0])"):  "a shape",
        ("main", "config: PLAUD_SUBTITLE_SOURCE= is not one of —", "', '.join(config.SUBTITLE_SOURCES)"): "a constant list",
        ("main", "config: PLAUD_SUBTITLE_SOURCE= is not one of —", "config.DEFAULTS['subtitle_source']"): "a constant",
        ("main", "config: PLAUD_SUBTITLE_SOURCE= is not one of —", "prefer"): "echoes the bad value",
        ("main", "error: no lines in looked like segments. Expec", "detail"):   "the composed diagnostic, checked by its parts",
        ("main", "error: no lines in looked like segments. Expec", "str(path)"): "a path",
        ("main", "error: not found — run the plaud-index skill f", "str(path)"): "a path",
        ("main", "error: refusing unsafe recording id:", "args.id"):            "echoes the rejected id",
        ("main", "note: could not read to check whether this rec", "exc.strerror"): "an OS message",
        ("main", "note: could not read to check whether this rec", "transcript.name"): "a filename",
        ("main", "of the content lines DID carry a timestamp and", "shape_of(stamped[0])"): "a shape",
        ("main", ":", "label"):  "which of the two sources this line is",
        ("main", ":", "shown"):  "the sampled line itself, already capped",
        ("main", ":", "tail"):   "the composed overflow clause; its count is checked",
        ("main", "wrote cues to stdout", "note"):                   "the ledger, checked clause by clause",
        ("main", "wrote cues →", "note"):                           "the ledger, checked clause by clause",
        ("main", "wrote cues →", "str(args.output)"):               "the output path",
        ("main", "— of them are not `key: value` lines, so this ", "shape_of(odd[0])"): "a shape",
        ("main", "— of them would have parsed as cues (first: )", "shape_of(ate[0])"):  "a shape",
        ("main", "⚠", "note"):                                      "a trim/clamp sentence, tested by name",
        ("main", "⚠ a declared end time was discarded and replac", "shape_of(line)"): "a shape",
        ("main", "⚠ is marked incomplete — these subtitles cover", "path.name"):       "a filename",
        ("main", "⚠ line(s) in were taken as the file's header a", "detail"):          "the composed clause, checked by its parts",
        ("main", "⚠ line(s) in were taken as the file's header a", "path.name"):       "a filename",
        ("main", "⚠ of content lines in did not parse as segment", "path.name"):       "a filename",
        ("main", "⚠ of content lines in did not parse as segment", "remedy"):          "the advice clause, tested by name",
        ("main", "⚠ of content lines in did not parse as segment", "shape_of(first_bad)"): "a shape",
        ("parse_timestamp", "unrecognised timestamp:", "raw"):       "echoes the rejected stamp into an exception",
        ("render_srt", "-->", "wrap_cue_text(cue['text'], limits)"): "the wrapped words; wrapping is tested",
        ("shape_of", "\u2026", "shape[:_SHAPE_CAP - 1]"):            "the truncated shape, on its way into the line below",
        ("shape_of", "chars, opens with", "shape"):                  "the assembled shape string",
        ("subtitle_source", ".md", "rec_id"):                        "a path fragment",
        ("wrap_cue_text", "", "current"):                            "a partial line",
        ("wrap_cue_text", "", "word"):                               "a word",
    }

    @staticmethod
    def _sites():
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        owner = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    owner[id(child)] = node.name
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = " ".join("".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)).split())[:46]
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    found.add((owner.get(id(node), "<module>"), literal,
                               ast.unparse(value.value)))
        return found

    def test_every_interpolation_is_registered(self):
        found = self._sites()
        registered = set(self.CHECKED) | set(self.UNCHECKED)
        unregistered = found - registered
        self.assertEqual(
            set(), unregistered,
            f"{len(unregistered)} interpolation(s) reach a stream with nothing "
            f"recorded about them: {sorted(unregistered)}\n\n"
            f"Put each in CHECKED with the assertion that would fail if the "
            f"value were wrong — writing that assertion first if there is none "
            f"— or in UNCHECKED with the reason it carries no such value. "
            f"There is no third option and no filter on how the expression is "
            f"spelled: round 15 added a count spelled without `len(` and this "
            f"guard, which enumerated spellings, let it through.")
        stale = registered - found
        self.assertEqual(
            set(), stale,
            f"{len(stale)} registration(s) name a site that no longer exists: "
            f"{sorted(stale)}. A registry that outlives what it registers "
            f"starts granting coverage to nothing.")
        self.assertEqual(set(), set(self.CHECKED) & set(self.UNCHECKED),
                         "a site cannot be both checked and unchecked")

    def test_the_tool_builds_messages_only_with_f_strings(self):
        """What makes the census above exhaustive rather than merely large.

        The detector walks `ast.JoinedStr`. That is a positive enumeration of
        string-construction SYNTAX: a count printed with `.format()`, with `%`,
        or by concatenation is forced into neither bucket and is not reported
        as unmutatable either — the same shape as the expression-spelling
        filter this class was inverted to remove, one level up.

        Widening the detector to every way Python can build a string is the
        losing move; there is always another. Narrowing the LANGUAGE is not:
        the tool uses one construction, so walking that one is total. This
        test is what turns the census from a sample into a survey.

        Round 19 found it checked two of the three shapes its own docstring
        named. `"count: " + str(n)` walked past this test, past the registry
        (which reads f-strings), and past `mutate.py`'s unmutatable list —
        three guards blind to one `+`. `print("count:", n)` is worse still: it
        builds no string at all, so there is no interpolation to register, and
        the number reaches the user anyway.

        The rule is absolute rather than clever, and that costs something: a
        legitimate `+` on strings has to be written as an f-string too. One
        such line existed and was rewritten. Paying that is the point — a
        restriction with exceptions is an enumeration again, and the exception
        is where the next unregistered count will live.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format"):
                offenders.append(f"line {node.lineno}: .format()")
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
                    and isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, str)):
                offenders.append(f"line {node.lineno}: %-formatting")
            # CONCATENATION. The docstring below named three ways to build a
            # string and this checked two — `"count: " + str(n)` walked past
            # the language test, past the registry that only reads f-strings,
            # and past `mutate.py`'s unmutatable list, which is three guards
            # in a row blind to one `+`.
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and any(isinstance(side, ast.Constant)
                            and isinstance(side.value, str)
                            for side in (node.left, node.right))):
                offenders.append(f"line {node.lineno}: string concatenation")
            # MULTI-ARGUMENT print. `print("count:", n)` builds no string at
            # all — the interpolation the registry walks never exists, and the
            # number still reaches the user.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                    and len(node.args) > 1):
                offenders.append(f"line {node.lineno}: print() with "
                                 f"{len(node.args)} positional arguments")
        self.assertEqual(
            [], offenders,
            f"{offenders}\n\nMessages are built with f-strings here, and the "
            f"registry above walks f-strings. A message built any other way is "
            f"invisible to it — not flagged, not registered, not reported as "
            f"unmutatable. Use an f-string, or widen the detector and this "
            f"test together.")

    def test_every_registration_names_something_that_exists(self):
        """The teeth. An entry may not point at nothing.

        `header_odd` had a pattern, had an entry naming that pattern, and no
        test read it — so `odd * 3` was invisible while the registry read as
        covered. Naming an extractor key is not coverage; the key has to be
        consumed somewhere.
        """
        module = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))

        def is_own_registry(node):
            return isinstance(node, ast.ClassDef) and node.name == type(self).__name__

        def is_pattern_table(node):
            # `_NUMBER_PATTERNS` lives in the module body, so a naive walk
            # collects its own keys and every key looks consumed — a check
            # reading its own answer, which is the shape this whole class
            # exists to refuse. The table is where keys are DECLARED; being
            # declared is not being read.
            return (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_NUMBER_PATTERNS"
                            for t in node.targets))

        # READ, not merely present. Collecting every string constant meant a
        # key mentioned in any docstring counted as consumed, so the check
        # could be satisfied by prose. A `numbers_in` key is consumed when
        # something SUBSCRIPTS or `.get()`s it — that is the only way its
        # value is ever looked at.
        consumed = set()
        for node in module.body:
            if is_own_registry(node) or is_pattern_table(node):
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Subscript)
                        and isinstance(child.slice, ast.Constant)
                        and isinstance(child.slice.value, str)):
                    consumed.add(child.slice.value)
                elif (isinstance(child, ast.Call)
                      and isinstance(child.func, ast.Attribute)
                      and child.func.attr == "get"
                      and child.args
                      and isinstance(child.args[0], ast.Constant)
                      and isinstance(child.args[0].value, str)):
                    consumed.add(child.args[0].value)

        missing_key, missing_test, unused = [], [], []
        for site, where in self.CHECKED.items():
            if where.startswith("numbers_in:"):
                key = where.split(":", 1)[1].strip().split()[0].rstrip(",")
                if key not in _NUMBER_PATTERNS:
                    missing_key.append((site, key))
                elif key not in consumed:
                    unused.append((site, key))
            else:
                cls = where.split(":")[0].strip().split(".")[0]
                if cls not in globals():
                    missing_test.append((site, cls))
        self.assertEqual([], missing_key,
                         f"registration names a `numbers_in` key that is not in "
                         f"_NUMBER_PATTERNS: {missing_key}")
        self.assertEqual([], unused,
                         f"registration names a `numbers_in` key that NO TEST "
                         f"READS, so it grants coverage to nothing: {unused}. "
                         f"This is the exact state `header_odd` was in when a "
                         f"3x error in the printed count passed the suite.")
        self.assertEqual([], missing_test,
                         f"registration names a test class that does not exist: "
                         f"{missing_test}")


class TestRoundSixteensCountsAreCheckedToo(unittest.TestCase):
    """The ten counts round 15 found registered against nothing.

    `header_odd` is the one that mattered: it had a `_NUMBER_PATTERNS` entry
    and a registry entry naming that entry, and no test read it, so injecting
    `odd = odd * 3` left all 594 tests green while the README said every
    printed count was compared as an integer. The other nine are counts this
    round adds or rewords, held to the same standard before they ship rather
    than a round later.
    """

    def _run(self, body: str, *args: str, cache_id: str = "abc123"):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / f"{cache_id}.md").write_text(body, encoding="utf-8")
            out = cache / "o.srt"
            return subprocess.run(
                [sys.executable, str(SCRIPT), cache_id, "-o", str(out), *args],
                capture_output=True, text=True, env=cli_env(cache))

    def _preview(self, polish: str, verbatim: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n" + verbatim, encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_the_header_warning_states_the_odd_count_exactly(self):
        proc = self._run("---\nid: abc123\n"
                         "somebody was speaking here\n"
                         "and here too\n"
                         "---\n[00:01] S: one\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(2, got.get("header_odd"), f"{proc.stderr!r}")
        self.assertEqual(5, got.get("header_all"), f"{proc.stderr!r}")

    def test_an_ordinary_header_reports_no_oddities(self):
        """The other direction. A count that is always 2 is not a count."""
        proc = self._run("---\nid: abc123\ncomplete: true\n\n---\n"
                         "[00:01] S: one\n")
        self.assertNotIn("are not `key: value` lines", proc.stderr,
                         f"a blank line inside an ordinary header was counted "
                         f"as speech the header swallowed: {proc.stderr!r}")

    def test_discarded_ends_reach_the_stdout_ledger(self):
        proc = self._run("---\nid: abc123\n---\n"
                         "[00:01 - 00:412] S: one\n"
                         "[00:20 - 99:99] S: two\n"
                         "[00:40 - 00:45] S: three\n")
        self.assertEqual(2, numbers_in(proc.stdout).get("lost_ends"),
                         f"stdout: {proc.stdout!r}")

    def test_stripped_characters_are_counted_on_both_streams(self):
        proc = self._run("---\nid: abc123\n---\n"
                         "[00:01] S: one\x07two\x08three\n"
                         "[00:20] S: plain\n")
        self.assertEqual(2, numbers_in(proc.stdout).get("stripped_ledger"),
                         f"stdout: {proc.stdout!r}")
        self.assertEqual(2, numbers_in(proc.stderr).get("stripped_warn"),
                         f"stderr: {proc.stderr!r}")

    def test_a_clean_file_reports_no_stripping(self):
        proc = self._run("---\nid: abc123\n---\n[00:01] S: 你好 — ok?\n")
        self.assertNotIn("invisible", proc.stdout + proc.stderr,
                         f"{proc.stdout!r} {proc.stderr!r}")

    def test_when_both_sides_drop_the_refusal_names_both_counts(self):
        proc = self._preview(
            "[99999:00] S: a\n[88888:00] S: b\n[00:00] S: c\n",
            "[77777:00] S: a\n[00:00] S: c\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(3, proc.returncode)
        self.assertEqual(2, got.get("refusal_drop_a"), f"{proc.stderr!r}")
        self.assertEqual(1, got.get("refusal_drop_b"), f"{proc.stderr!r}")

    def test_when_one_side_drops_the_refusal_names_that_side(self):
        proc = self._preview("[99999:00] S: a\n[00:00] S: c\n",
                             "[00:00] S: c\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(1, got.get("refusal_drop_a"), f"{proc.stderr!r}")
        self.assertIsNone(got.get("refusal_drop_b"), f"{proc.stderr!r}")

    def test_the_shape_widths_are_the_widths(self):
        """`d{n}` and `s{n}` are the redaction. A wrong n publishes a wrong
        claim about the producer's format, and an unchecked n is how the
        digits themselves crept back in once already."""
        proc = self._run("---\nid: abc123\n---\n"
                         "[00:01] S: fine\n"
                         "   [12345:678] S: not a stamp\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(5, got.get("shape_digits"), f"{proc.stderr!r}")
        self.assertEqual(3, got.get("shape_spaces"), f"{proc.stderr!r}")


class TestTheRefusalCountsAreCheckedToo(unittest.TestCase):
    """The five counts round 12 declared covered and did not cover.

    Four are in `differing_sample`'s refusals and one is the `stamped` branch of
    the zero-cue diagnostic — the shape the README names by name. Mutating any
    of them by +40 left the whole suite green, and `grep` for their sentences in
    `tests/` returned nothing: not a wrong assertion, no assertion.
    """

    def _preview(self, polish: str, verbatim: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n" + verbatim, encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_the_drop_refusal_states_the_count(self):
        proc = self._preview("[99999:00] S: bad\n[00:00] S: a\n[00:10] S: b\n",
                             "[00:00] S: a\n[00:10] S: b\n")
        self.assertEqual(1, numbers_in(proc.stderr).get("refusal_dropped"),
                         f"{proc.stderr!r}")

    def test_the_cue_count_mismatch_states_both_counts(self):
        proc = self._preview("[00:00] S: a\n",
                             "[00:00] S: a\n[00:10] S: b\n[00:20] S: c\n")
        got = numbers_in(proc.stderr)
        self.assertEqual(1, got.get("refusal_polished"), f"{proc.stderr!r}")
        self.assertEqual(3, got.get("refusal_verbatim"), f"{proc.stderr!r}")

    def test_the_ambiguous_refusal_states_how_many(self):
        proc = self._preview("[00:00] S: a\n[00:00] S: b\n",
                             "[00:00] S: a\n[00:00] S: c\n")
        self.assertEqual(1, numbers_in(proc.stderr).get("refusal_ambiguous"),
                         f"{proc.stderr!r}")

    def test_the_zero_cue_stamped_branch_states_the_count(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n"
                "[99999:00] S: one\n[88888:00] S: two\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt")],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertEqual(2, numbers_in(proc.stderr).get("zero_stamped"),
                         f"a file with two timestamped lines that none parsed: "
                         f"{proc.stderr!r}")


class TestRoundTwelvesOwnFixesArePinned(unittest.TestCase):
    """Two of round 12's fixes were pinned by nothing.

    Reverting the `--preview-sources` control-character sanitisation — that
    commit's headline security fix — or the completeness-read crash guard left
    all 584 tests green. Round 12 spent itself building an apparatus to detect
    unpinned numbers and shipped two unpinned fixes in the same commit.

    Caught by mutation, which is now the third round running where mutation
    found what reading did not.
    """

    def test_the_preview_strips_control_characters(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n[00:00] S: um so the budget\n",
                encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(
                "[00:00] S: benign\x1b[2K\x1b[1A FORGED\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertEqual(0, proc.returncode, f"{proc.stderr!r}")
        self.assertNotIn("\x1b", proc.stdout,
                         f"the preview handed a control sequence to the terminal, "
                         f"and the operator quotes these lines to the user and "
                         f"stores the answer: {proc.stdout!r}")

    def test_an_unreadable_sibling_transcript_does_not_stop_the_conversion(self):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_bytes(
                b"---\nid: abc123\ncomplete: true\n---\n\n\xff\xfe bad bytes\n")
            (cache / "polish" / "abc123.md").write_text(
                "[00:00] S: polished line\n", encoding="utf-8")
            out = cache / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out)],
                capture_output=True, text=True, env=cli_env(cache))
            # Inside the `with`. The first version checked existence after the
            # tempdir had been removed and failed on its own cleanup.
            written = out.exists()
        self.assertNotIn("Traceback", proc.stderr,
                         f"one bad byte in a file we are NOT converting took the "
                         f"whole command down: {proc.stderr!r}")
        self.assertTrue(written,
                        "a valid polish file produced no .srt because the "
                        "completeness check could not read its sibling")


class TestControlCharactersNeverReachTheOutput(unittest.TestCase):
    """`_CONTROL` guarded two diagnostics and left the conversion path raw.

    A cue that parses perfectly and contains `\x1b[2K\x1b[1A` reached stdout
    untouched when streaming — and without `-o`, stdout IS the terminal, which
    the module docstring describes as ordinary usage. The comment that reasoned
    about this considered the `.srt` file and stopped one case short, in the
    same commit that fixed the streaming ledger.
    """

    BODY = ("---\nid: abc123\ncomplete: true\n---\n\n"
            "[00:00] S: benign\x1b[2K\x1b[1A forged status line\n"
            "[00:10] \u202eSpeaker: reversed\n")

    def _run(self, *args: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(self.BODY, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123",
                 *[a.replace("{d}", str(cache)) for a in args]],
                capture_output=True, text=True, env=cli_env(cache))
            wrote = (cache / "o.srt").read_text() if (cache / "o.srt").exists() else ""
            return proc, wrote

    def test_streaming_stdout_is_clean(self):
        proc, _ = self._run()
        self.assertNotIn("\x1b", proc.stdout, f"{proc.stdout!r}")
        self.assertNotIn("\u202e", proc.stdout, f"bidi override survived: {proc.stdout!r}")

    def test_the_srt_file_is_clean(self):
        _, wrote = self._run("-o", "{d}/o.srt")
        self.assertNotIn("\x1b", wrote, f"{wrote!r}")
        self.assertNotIn("\u202e", wrote,
                         f"a subtitle file that reorders what displays it: {wrote!r}")

    def test_ordinary_text_survives_intact(self):
        """The strip must not eat CJK, emoji or punctuation."""
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "---\nid: abc123\ncomplete: true\n---\n\n"
                "[00:00] 講者一: 我們把預算拆成兩期 — 好嗎？ 🎧\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123"],
                capture_output=True, text=True, env=cli_env(cache))
        # Newlines removed before comparing: long cues are WRAPPED (20 columns
        # for CJK), which is deliberate and unrelated. The first version of this
        # assertion read a line break as mangling.
        self.assertIn("我們把預算拆成兩期 — 好嗎？ 🎧",
                      proc.stdout.replace("\n", ""),
                      f"the sanitiser mangled ordinary text: {proc.stdout!r}")


class TestCueTextIsAccountedForCharacterByCharacter(unittest.TestCase):
    """The closure the line ledger has, at the unit the losses kept escaping to.

    `TestEveryLineEndsInExactlyOneReportableBucket` closes the accounting at
    the LINE unit: cues plus dropped plus header is every non-blank input
    line. There was no equivalent at the character unit, which is why the same
    defect landed twice one level down — round 15 found `sanitise` deleting
    joiners with no count, round 17 found `wrap_cue_text` deleting tabs and
    collapsing spaces with no count, both while the line ledger closed.

    The property is not "nothing changes" — normalising whitespace in a
    subtitle is correct. It is that **the count is zero if and only if nothing
    changed**, which is exactly what a silent transform violates and what no
    enumeration of transforms can go stale on.
    """

    def _cue(self, text: str, speaker: str = "") -> dict:
        return to_srt.build_cues(
            [{"start": 0.0, "end": None, "speaker": speaker, "text": text}])[0]

    def test_the_count_is_zero_exactly_when_the_text_is_unchanged(self):
        cases = [
            "plain words with single spaces",
            "你好世界",
            "emoji 👨‍👩‍👧 family",          # ZWJ is kept
            "می‌خواهم",                             # ZWNJ is kept
            "word\tafter-tab",
            "double  spaces",
            "  padded  ",
            "bell\x07inside",
            "ideographic　space",
            "a" + "　" * 44 + "b",
        ]
        for text in cases:
            with self.subTest(text=text[:24]):
                out = self._cue(text)
                unchanged = out["text"] == text
                self.assertEqual(
                    unchanged, out["stripped"] == 0,
                    f"the count and the text disagree about whether anything "
                    f"happened: {text!r} -> {out['text']!r}, "
                    f"stripped={out['stripped']}")

    def test_the_speakers_own_changes_are_counted_too(self):
        out = self._cue("words", speaker="spea\tker")
        self.assertEqual("spea ker: words", out["text"])
        self.assertEqual(1, out["stripped"],
                         "a change inside the speaker label is still a change "
                         "to the cue text the user reads")

    def test_no_separator_is_deleted_into_a_word_that_nobody_said(self):
        """Removing a tab rather than normalising it joins the words on either
        side. `word\\tafter` became `wordafter` for the length of one fix."""
        out = self._cue("word\tafter")
        self.assertEqual("word after", out["text"])


class TestEveryRefusalCauseIsPinned(unittest.TestCase):
    """All eight causes, because half of them were pinned by nothing.

    The registry carried this interpolation as UNCHECKED with the reason
    "the cause sentence; each caller's wording is tested" — a falsifiable
    claim, and false. Replacing four of the eight sentences with the word
    `banana` left all 607 tests green: the no-polish, polish-empty,
    no-cues and identical causes were read by no assertion at all.

    That matters more than a wording detail because SKILL.md stopped
    enumerating these causes in round 7 and now tells the operator to **read
    the sentence** and decide from it — relay it if it points at something the
    user should fix, stay quiet if it does not. The two branches named there as
    "stay quiet" (no polish, identical versions) were two of the four nobody
    checked. A closed list was replaced by a contract on sentences, and half
    the contract was unenforced.
    """

    def _preview(self, polish, verbatim, *, header="id: abc123"):
        """`header` is a parameter because one cause lives IN the header.

        It was easier to drop the header-oddity case from the table below than
        to reach it through a helper that always wrote its own frontmatter —
        and a case quietly missing from a table called "all eight causes" is
        the failure this whole class is about.
        """
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                f"---\n{header}\n---\n" + verbatim, encoding="utf-8")
            if polish is not None:
                (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    CAUSES = {
        "no polish":     (None, "[00:00] S: a\n", "has no polished version"),
        "polish empty":  ("", "[00:00] S: a\n", "empty or produced no cues"),
        "no cues":       ("[00:00] S: a\n", "nothing parseable here\n",
                          "produced no cues, so there is nothing to compare"),
        "identical":     ("[00:00] S: a\n", "[00:00] S: a\n",
                          "the two versions are identical"),
        "dropped":       ("[99999:00] S: a\n[00:00] S: b\n", "[00:00] S: b\n",
                          "transcript dropped"),
        "cue counts":    ("[00:00] S: a\n[00:10] S: b\n", "[00:00] S: a\n",
                          "different cue counts"),
        "diverge":       ("[00:01] S: a\n[00:11] S: b\n",
                          "[00:00] S: a\n[00:10] S: b\n", "diverge at"),
        "ambiguous":     ("[00:00] S: A\n[00:00] S: B\n",
                          "[00:00] S: a\n[00:00] S: b\n",
                          "uses more than once"),
    }

    #: The one cause that lives in the header rather than the body.
    HEADER_CAUSE = ("[00:00] S: a\n", "[00:00] S: a\n",
                    "id: abc123\n# a note somebody left in the header",
                    "header holds")

    def test_the_header_cause_says_its_own_thing(self):
        polish, verbatim, header, phrase = self.HEADER_CAUSE
        proc = self._preview(polish, verbatim, header=header)
        self.assertEqual(3, proc.returncode, proc.stderr)
        self.assertIn(phrase, proc.stderr, proc.stderr)

    def test_each_cause_says_its_own_thing(self):
        for label, (polish, verbatim, phrase) in self.CAUSES.items():
            with self.subTest(cause=label):
                proc = self._preview(polish, verbatim)
                self.assertEqual(3, proc.returncode,
                                 f"{label}: expected a refusal, got "
                                 f"{proc.returncode} / {proc.stdout!r}")
                # `assertIn` ONCE, not merely at all. Tripling the cause
                # string leaves every substring assertion green — the sentence
                # says the same thing three times and `in` cannot tell. Found
                # by `tests/mutate.py`, which is the third time on this branch
                # that an assertion turned out to test a property weaker than
                # the one its name claims.
                self.assertEqual(
                    1, proc.stderr.count(phrase),
                    f"{label}: expected the cause exactly once. SKILL.md tells "
                    f"the operator to decide whether to relay this from the "
                    f"sentence itself, so a sentence that describes the wrong "
                    f"cause — or says the right one three times — is a wrong "
                    f"instruction: {proc.stderr!r}")
                self.assertEqual(
                    1, proc.stderr.count("no source comparison to show"),
                    f"{label}: one refusal, one line: {proc.stderr!r}")

    def test_the_causes_are_distinguishable_from_each_other(self):
        """Eight sentences that all matched would pin nothing."""
        seen = {}
        cases = [(l, p, v, None) for l, (p, v, _) in self.CAUSES.items()]
        cases.append(("header oddity", self.HEADER_CAUSE[0],
                      self.HEADER_CAUSE[1], self.HEADER_CAUSE[2]))
        for label, polish, verbatim, header in cases:
            proc = (self._preview(polish, verbatim, header=header) if header
                    else self._preview(polish, verbatim))
            body = proc.stderr.split("show:", 1)[-1].strip()
            self.assertNotIn(body, seen,
                             f"{label} and {seen.get(body)} give the same "
                             f"sentence, so neither is pinned by it")
            seen[body] = label

    def test_an_identical_pair_is_not_blamed_on_a_repeated_timestamp(self):
        """Two identical files with one duplicate start said "every line that
        differs sits at a timestamp used more than once" — when no line
        differed. A refusal describing a problem the file does not have sends
        somebody to fix nothing."""
        proc = self._preview("[00:00] S: a\n[00:00] S: b\n",
                             "[00:00] S: a\n[00:00] S: b\n")
        self.assertIn("the two versions are identical", proc.stderr, proc.stderr)


class TestRoundEighteensCountsAreCheckedToo(unittest.TestCase):
    """The two counts this round adds, held to the standard before they ship."""

    def _run(self, body: str, *args: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o",
                 str(cache / "o.srt"), *args],
                capture_output=True, text=True, env=cli_env(cache))

    def test_corrections_reach_the_success_line_and_the_count_is_right(self):
        """A trim or a clamp REPLACES a time the producer wrote — the same
        kind of loss as a discarded end, and the only one still kept off the
        success line. On #50's own recording it is also the most frequent."""
        proc = self._run("---\nid: abc123\n---\n"
                         "[00:20 - 09:59] S: one\n"
                         "[00:10 - 00:30] S: two\n"
                         "[00:40 - 09:59] S: three\n"
                         "[00:45 - 00:50] S: four\n")
        self.assertEqual(2, numbers_in(proc.stdout).get("corrections"),
                         f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}")

    def test_a_file_needing_no_correction_says_nothing(self):
        proc = self._run("---\nid: abc123\n---\n"
                         "[00:00 - 00:05] S: one\n[00:10 - 00:15] S: two\n")
        self.assertNotIn("corrected", proc.stdout, proc.stdout)

    def test_the_header_oddity_refusal_counts_the_transcripts_header(self):
        """Named for the one side that can have a header.

        It was `..._counts_both_sides` while its own failure message explained
        that only one side is read — `cache.py` writes polish as a bare body,
        so `polish_odd` was structurally always zero and the refusal added it
        to make a two-sided sum out of one number. When a test's name and its
        message disagree, one of them is wrong; here it was the name.
        """
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\n# one note\n# two notes\n---\n[00:00] S: a\n",
                encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(
                "---\n# a third\n---\n[00:00] S: A\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))
        self.assertEqual(3, proc.returncode, proc.stderr)
        self.assertEqual(2, numbers_in(proc.stderr).get("refusal_header_odd"),
                         f"only the transcript's header is read for oddities — "
                         f"polish carries no frontmatter, so its `---` block is "
                         f"content: {proc.stderr!r}")


class TestTheSampledWarningsStateTheRemainder(unittest.TestCase):
    """`and N more` is a count like any other, and it is the one a reader
    subtracts. Round 18 capped two per-cue warnings at three examples; an
    off-by-one in the remainder makes the samples and the ledger disagree,
    which is worse than either alone."""

    def _run(self, body: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt")],
                capture_output=True, text=True, env=cli_env(cache))

    def test_the_remainder_plus_the_examples_is_the_ledger_total(self):
        body = "---\nid: abc123\n---\n" + "".join(
            f"[{i:02d}:00 - 00:412] S: line {i}\n" for i in range(1, 9))
        proc = self._run(body)
        total = numbers_in(proc.stdout).get("lost_ends")
        more = numbers_in(proc.stderr).get("more_ends")
        shown = proc.stderr.count("a declared end time was discarded")
        self.assertEqual(8, total, proc.stdout)
        self.assertEqual(3, shown, proc.stderr)
        self.assertEqual(5, more, proc.stderr)
        self.assertEqual(total - shown, more,
                         f"the examples and the remainder do not add up to the "
                         f"ledger: {shown} shown + {more} more != {total}")

    def test_corrections_are_sampled_the_same_way(self):
        rows = []
        for i in range(1, 7):
            rows.append(f"[{i * 10:02d}:00 - 99:00] S: long {i}")
            rows.append(f"[{i * 10:02d}:05 - {i * 10:02d}:09] S: next {i}")
        proc = self._run("---\nid: abc123\n---\n" + "\n".join(rows) + "\n")
        total = numbers_in(proc.stdout).get("corrections")
        more = numbers_in(proc.stderr).get("more_trims")
        shown = sum(1 for l in proc.stderr.splitlines() if l.startswith("⚠ cue at"))
        # The ABSOLUTE total as well as the relationship. `total - shown ==
        # more` is invariant under scaling both sides: tripling `trims` gives
        # 3T and 3T-3, the equation still balances, and the assertion stays
        # green while every number printed is wrong. A relational check needs
        # an anchor or it checks arithmetic rather than data.
        self.assertEqual(6, total, f"stdout: {proc.stdout!r}")
        self.assertEqual(3, shown, proc.stderr)
        self.assertEqual(total - shown, more,
                         f"{shown} shown + {more} more != {total}")


class TestRoundTwentysCountsAreCheckedToo(unittest.TestCase):
    """The four counts this round adds, pinned before they ship."""

    def _run(self, body: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(cache / "o.srt")],
                capture_output=True, text=True, env=cli_env(cache))

    def _preview(self, polish: str, verbatim: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                "---\nid: abc123\n---\n" + verbatim, encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_cues_that_hold_nothing_are_removed_and_counted(self):
        body = ("---\nid: abc123\n---\n"
                f"[00:01] S: {chr(0x200b)}{chr(0x7)}\n"
                "[00:10] S: real words\n"
                f"[00:20] S: {chr(0xfeff)}\n")
        proc = self._run(body)
        self.assertEqual(2, numbers_in(proc.stdout).get("emptied_ledger"),
                         f"stdout: {proc.stdout!r}")
        self.assertEqual(2, numbers_in(proc.stderr).get("emptied_warn"),
                         f"stderr: {proc.stderr!r}")
        self.assertEqual(1, numbers_in(proc.stdout).get("cues"), proc.stdout)

    def test_a_file_with_no_empty_cues_says_nothing(self):
        proc = self._run("---\nid: abc123\n---\n[00:01] S: words\n")
        self.assertNotIn("empty cue", proc.stdout + proc.stderr)

    def test_the_preview_says_when_it_altered_what_it_shows(self):
        proc = self._preview(f"[00:00] S: we{chr(0x9)}agreed\n",
                             "[00:00] S: um we agreed\n")
        self.assertEqual(1, numbers_in(proc.stderr).get("preview_altered"),
                         f"stderr: {proc.stderr!r}")

    def test_an_unaltered_preview_says_nothing(self):
        proc = self._preview("[00:00] S: we agreed\n", "[00:00] S: um we agreed\n")
        self.assertNotIn("before display", proc.stderr, proc.stderr)

    def test_the_preview_is_capped_and_states_the_remainder(self):
        long = "word " * 200
        proc = self._preview(f"[00:00] S: {long}\n", "[00:00] S: um different\n")
        more = numbers_in(proc.stdout).get("preview_more")
        self.assertIsNotNone(more, f"stdout: {proc.stdout[:200]!r}")
        shown = max(len(l) for l in proc.stdout.splitlines())
        self.assertLess(shown, 400,
                        f"the cap did not bound the line: {shown} chars")
        polished_line = [l for l in proc.stdout.splitlines()
                         if l.startswith("polished: ")][0]
        body = polished_line[len("polished: "):].split(" …(+")[0]
        self.assertEqual(len(body) + more, len(f"S: {long}".strip()),
                         "shown + remainder must be the whole line")


class TestTheClosureReachesTheFile(unittest.TestCase):
    """Count the cues in the `.srt`, which is what #50 actually asked for.

    The issue's requested regression test reads 「對每個 cache 檔，**`to_srt`
    產出的** cue 數必須等於該檔中以 `[` 開頭的行數」 — the count in the
    PRODUCED FILE. Every closure in this suite measured one layer earlier:
    `test_local_corpus` calls `parse_transcript` and never renders;
    `test_the_ledger_sums_to_every_non_blank_line` reads `wrote N cues` off
    stdout, which was `len(segments)`; the character-unit closure asserts on
    `build_cues(...)[0]`, before wrapping and before rendering.

    Round 19 asked what unit was left and the answer was the file, and every
    silent loss it found lived in exactly that unmeasured gap: a separator
    splitting one line into two cues, a cue that rendered as an empty block,
    the output written in the locale's encoding.

    So this reads the bytes back off disk. It is the only assertion here whose
    subject is the artifact rather than the tool's opinion of the artifact.
    """

    def _convert(self, body: str, *args: str) -> tuple:
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(body, encoding="utf-8")
            out = cache / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out), *args],
                capture_output=True, text=True, env=cli_env(cache))
            written = out.read_text(encoding="utf-8") if out.exists() else ""
        return proc, written

    @staticmethod
    def _cues_in(srt: str) -> int:
        return srt.count(" --> ")

    def test_the_success_line_counts_the_cues_the_file_holds(self):
        cases = {
            "ordinary": "[00:01] S: one\n[00:20] S: two\n",
            "past ninety-nine minutes":
                "[100:05] S: one\n[446:12] S: two\n[00:01] S: three\n",
            "a wordless cue among real ones":
                f"[00:01] S: {chr(0x200b)}\n[00:10] S: real\n",
            "a separator inside a cue":
                f"[00:01] S: before{chr(0x2028)}after\n[00:20] S: next\n",
            "a line that cannot parse":
                "[00:01] S: one\n[9999:99] S: bad\n[00:20] S: two\n",
        }
        for label, body in cases.items():
            with self.subTest(case=label):
                proc, srt = self._convert("---\nid: abc123\n---\n" + body)
                claimed = numbers_in(proc.stdout).get("cues")
                self.assertEqual(
                    self._cues_in(srt), claimed,
                    f"{label}: stdout claims {claimed} cues, the file holds "
                    f"{self._cues_in(srt)}. The success line is a claim about "
                    f"the file.\nstdout: {proc.stdout!r}")

    def test_no_cue_in_the_file_is_missing_its_text_line(self):
        """A block with a blank text line is SRT's cue terminator sitting
        inside a cue: some players show a blank flash, some read the file as
        malformed from there on."""
        proc, srt = self._convert(
            "---\nid: abc123\n---\n"
            f"[00:01] S: {chr(0x200b)}{chr(0x7)}\n[00:10] S: real words\n")
        for block in [b for b in srt.split("\n\n") if b.strip()]:
            lines = block.splitlines()
            self.assertGreaterEqual(len(lines), 3, f"short block: {block!r}")
            self.assertTrue("".join(lines[2:]).strip(),
                            f"a cue with no text reached the file: {block!r}")

    def test_every_output_write_names_its_encoding(self):
        """Checked in the SOURCE, because the runtime check cannot fail here.

        `write_text` with no encoding uses the locale's, while the cache is
        read as UTF-8 — so on a non-UTF-8 machine the subtitles are mojibake
        or the write raises, for a file reported as `wrote N cues`.

        The obvious test — run it under `LC_ALL=C` and read the file back —
        was written first and **passed with the fix reverted**. macOS forces
        `getpreferredencoding` to UTF-8, and PEP 538 coerces the C locale
        elsewhere, so the defect cannot reproduce on this platform at all. A
        test that cannot fail where it runs is not a test of the property; it
        passes, it counts, and it makes the path look guarded.

        So the property is asserted where it is decidable: no call that writes
        output may omit `encoding`.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        bare = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("write_text", "read_text", "open"):
                continue
            if not any(k.arg == "encoding" for k in node.keywords):
                bare.append(f"line {node.lineno}: .{node.func.attr}()")
        self.assertEqual(
            [], bare,
            f"{bare}\n\nEach of these uses the locale's encoding while the "
            f"cache is UTF-8. The mismatch is invisible on macOS and silent on "
            f"the machines where it is not.")

    def test_the_words_survive_a_round_trip(self):
        """Weaker than its neighbour above and honest about it: this cannot
        fail on macOS. It stays because it would catch a mangling that has
        nothing to do with the locale."""
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "abc123.md").write_text(
                "---\nid: abc123\n---\n[00:01] S: 講者說了什麼\n", encoding="utf-8")
            out = cache / "o.srt"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "-o", str(out)],
                capture_output=True, text=True, env=cli_env(cache))
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertIn("講者說了什麼", out.read_text(encoding="utf-8"),
                          "the words did not survive the write")

    def test_one_separator_does_not_become_two_subtitles(self):
        """`str.splitlines()` breaks on five characters this format does not
        use as line breaks. One of them inside a cue produced a second cue —
        a subtitle nobody said, with the ledger balancing and exit 0."""
        for cp in (0x2028, 0x2029, 0x85, 0x0B, 0x0C):
            with self.subTest(sep=hex(cp)):
                proc, srt = self._convert(
                    "---\nid: abc123\n---\n"
                    f"[00:00] S: before{chr(cp)}[00:05] S: fabricated\n")
                self.assertEqual(
                    1, self._cues_in(srt),
                    f"U+{cp:04X} split one transcript line into "
                    f"{self._cues_in(srt)} cues: {srt!r}")


class TestTwoCausesAtOnceAreBothNamed(unittest.TestCase):
    """A file with a bad header AND a dropped line said only one of them.

    The header refusal returned first and mentioned the header, so the user
    fixed that, re-ran, and met a drop the tool had already seen and not
    mentioned. Two problems in one file is not an occasion to pick one.
    """

    def _preview(self, polish: str, verbatim: str, header: str):
        with tempfile.TemporaryDirectory() as d:
            cache = pathlib.Path(d)
            (cache / "polish").mkdir()
            (cache / "abc123.md").write_text(
                f"---\n{header}\n---\n" + verbatim, encoding="utf-8")
            (cache / "polish" / "abc123.md").write_text(polish, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "abc123", "--preview-sources"],
                capture_output=True, text=True, env=cli_env(cache))

    def test_a_header_oddity_and_a_drop_are_both_reported(self):
        proc = self._preview(
            polish="[99999:00] S: unparseable\n[00:00] S: a\n",
            verbatim="[00:00] S: a\n",
            header="id: abc123\n# a note somebody left")
        got = numbers_in(proc.stderr)
        self.assertEqual(3, proc.returncode, proc.stderr)
        self.assertEqual(1, got.get("refusal_header_odd"), proc.stderr)
        self.assertEqual(1, got.get("refusal_also_dropped"),
                         f"the drop was seen and not mentioned: {proc.stderr!r}")

    def test_a_header_oddity_alone_does_not_invent_a_drop(self):
        proc = self._preview(polish="[00:00] S: a\n", verbatim="[00:00] S: a\n",
                             header="id: abc123\n# a note somebody left")
        self.assertIsNone(numbers_in(proc.stderr).get("refusal_also_dropped"),
                          f"a second cause was reported that is not there: "
                          f"{proc.stderr!r}")


class TestSegmentMatchingHasOneEntryPoint(unittest.TestCase):
    r"""#57: `SEGMENT` backtracks quadratically on a line it can start matching
    that then ends in whitespace with nothing for the text group to anchor on,
    and the repair is to strip before matching, in one helper. `str.rstrip()`
    and the pattern's `\s*$` discard the same characters (checked below over
    every code point, against the pattern's own flags), so no group changes.

    EIGHT ROUNDS, EIGHT GUARDS, EIGHT WAYS TO BE WRONG.

    Round 1 counted `SEGMENT.match(...)` calls: 7 of 8 second-call-site
    spellings passed. Round 2 counted Name LOADS of `SEGMENT`: a copy of the
    pattern text that never names it passed. Round 3 timed ONE input string
    per path: a fold keyed on a speaker colon, a `rstrip(" ")`, and a copied
    pattern in an untimed branch of `main` all passed. Round 4 timed a family
    of 100 strings on five paths — and every one STRIPPED TO ITS PREFIX
    before the pattern saw it, so a lifted speaker bound (`{1,60}?` →
    `{1,}?`) cost 11 s per 128 KB through `--file` with every guard green;
    the same round found a sixth path untimed and the wall-clock criterion
    red on correct code under load. Round 5 timed 263 shapes on seven paths,
    including lines that survive the strip — and every one of them grew
    AFTER the closing bracket, so reverting the end group to its pre-#50
    lazy form made a 2 KB line with no closing bracket cost 10.9 s, cubic,
    with everything green. Round 6 added shapes before the bracket and an
    assertion that the pattern received each survivor at full length — and
    the assertion was satisfied by the CONTROL line (the same length, and
    it survives the strip too), so a helper dropping every cue over 1 MB on
    `^\[` passed; the same round found every CLI survivor printable ASCII,
    so `sanitise`'s per-character walk and the CJK width-break were timed
    at length zero, and every fixture one line long, so a quadratic in the
    per-line bookkeeping cost 7 s per 0.8 MB with everything green. Round 7
    added the line-count axis — and its one CLI fixture produced a single
    cue, so `build_cues` and `render_srt` never saw the count and a
    quadratic in the SRT writer cost 5 s per 0.8 MB of ordinary markdown
    with everything green; its control was the same block, so only the
    loose uniform bound judged it and a line-count quadratic worth 6 s per
    13 MB passed; and its tab-mixed CLI survivor sat at 65–105% of a ceiling
    calibrated before the shape existed — round 4's false red, back. Round 8
    put the cue count in the FIXTURE and in no assertion: the three blocks
    became cues only through the leniency that reads a bare `S:` as the
    text, so one plausible parser tightening emptied all three and left
    `build_cues` and `render_srt` timed at one cue — a quadratic in the SRT
    writer then cost 33 s on a real 9 MB transcript with everything green —
    and its line-count axis reached the body only, leaving the frontmatter
    walk (this fix's own second call site), the no-cue exit's re-walk and
    `--preview-sources`' per-cue pairing timed at one to three, each
    demonstrated at 19-61 s per few MB. Each guard covered exactly the
    region its author was looking at.

    WHAT THIS CLASS GUARANTEES NOW.

    Seven paths in eleven children (the two heaviest tables are split in
    two; the line-count shapes on the `--file` body and `--preview-sources`
    paths have children of their own), in parallel, on CPU time, each shape measured against a
    same-length control the pattern rejects at character 0 — one line,
    always, so a `many` block's line count is in the shape alone (see
    `tests/probe_segment_timing.py` for the measurement design):

      matcher     `_match_segment` through the spy on a pre-built string —
                  the pattern's own cost plus the strip's copy;
      lib         `parse_transcript`;   segments  `parse_segments`;
      cli-header  `main --file` with the line in the frontmatter (header ledger);
      cli-zero    `main --file` with only that line (the no-cue error exit);
      cli-body    `main --file` with the line after a cue (dropped-line report);
      cli-body-2  the same branch, with blocks of short lines that are
                  dropped, kept as cues, or kept with a lost end;
      cli-preview-2 the `--preview-sources` branch with the same blocks, on
                  both files (`_cue_lines` walks the cues of each);
      cli-preview `main <id> --preview-sources` (`_cue_lines`, both files).

    `main <id>` (the cache-path conversion) reaches the same
    `parse_transcript` call in `main` as `--file` does, differing only in
    `expect_frontmatter`; it is not timed separately.

    Shapes grow in four regions. After the closing bracket: a whitespace
    tail (`tail`, the issue's class), and lines that SURVIVE the strip —
    `a` + whitespace + `:` (forces the speaker group to backtrack), a long
    Latin text, a long CJK text (`wrap_cue_text`'s width-break), a mixed
    text with internal runs (with a TAB on `cli-body`, the one CLI path
    whose survivors become cues, so the cue enters `sanitise`'s
    per-character walk and `collapse_runs`), and a run the pattern crosses after
    `]` or after the speaker colon (`run`). Before it: no closing bracket
    at all (`open`, the #50/#55 branch), a growing end group that does
    close (`end`), a growing run after `[` (`lead`). And the LINE COUNT
    (`many`: n characters of short lines that are dropped, kept as cues, or
    kept with a lost end — on every path that walks a per-line or per-cue
    list: `lib`, `segments`, the `--file` body (where the count reaches
    `build_cues` and `render_srt`), the frontmatter (where it reaches this
    fix's second call site), the no-cue exit (which re-walks the drops), and
    `--preview-sources` (which pairs the cues of two files). The
    cue-producing prefixes carry a word, so the count does not rest on the
    parser's leniency for a bare speaker label.)
    Prefixes are a spanning set of the pattern's branches (point / ranged /
    empty / malformed end; no / short / spaced speaker; inner bracket
    whitespace). Tails are the four classes the issue and round 3 named
    plus EVERY code point `str.splitlines()` breaks on that
    `parse_transcript` leaves inside a line — eight, derived by rule in a
    test below, not listed from memory. The full prefix × class product
    runs for the four named classes; the eight in-line classes run on the
    spanning prefixes only (the strip treats them alike; the full product
    was 168 further shapes of `rstrip`), and `mixed`, `run`, `end` and `lead` run
    on all twelve (`colon`, `open` and `many` do not; their tables say
    why). Which prefix grows which of the pattern's eight unbounded
    quantifiers, so the next reader need not re-derive it:

      `\[\s*`                    `lead` / `[`, `open` / `[`
      `\s*` after the stamp      `open` / `[00:10` — matcher only, two classes
      `(?P<end>[^\]]*)`          `end` / `[00:10 - `, `open` / `[00:10 -`
      `\]\s*`                    `run` / `[00:10]`
      `\s*` before the colon     `colon` (crossed in the failed speaker branch)
      `\s*` after the colon      `run` / `[00:10] S:`
      `.*` in the text group     `text`, `cjk`, `mixed`
      the trailing `\s*$`        `tail` — pre-strip only, by design

    The parent verifies the report is for the shapes it asked for,
    in order; that the pattern RECEIVED THE SHAPE'S line at full length on
    every kind that survives the strip, and nothing near the run's length
    on the ones that do not — counted apart from the control, whose
    counters are held to the same bound; that the line reached the helper
    on that path, and on `many` that every line did; what the run did WITH
    those lines, taken from the CLI's own ledger and from `build_cues` and
    the parser themselves — cues, dropped lines, discarded ends, header
    lines, each at least half the block; and each CLI exit code and printed
    marker. The breadth of the table — kinds, classes, prefixes, the
    per-path count blocks, the children by name, size — is asserted, not
    assumed.

    THE RESIDUE, AS NUMBERS — from the ten children's own reports on this
    machine, idle, and re-derived whenever the table changes — round 7 found
    the previous figures 4-25x low, round 8 found the list still missing a
    whole band and its stated ranges falsified by the class's own reports. The criterion (`judge()` in the probe, the
    same function for the child's fail-fast and the parent's pass over every
    shape) is on the EXCESS over the control: its increment at the top size
    may be at most `K_GROWTH` × its increment at the middle size, plus
    `SLACK_MS` and half the control's own top increment. A quadratic passes
    only if its extra cost at the top size is under `(K_GROWTH − 4) × de1 +
    SLACK_MS + dc2 / 2` — printed in the failure message for the shape at
    hand. Measured across all 322 shapes on an idle machine, that admits
    1–15 ms on the `tail` shapes and 2–6 ms on `run`; 100–130 ms at the top
    of `cjk`, `open` and `text`; 184–193 ms on `colon`, whose excess is the
    pattern's own 60 bounded speaker attempts (≈ 200 ms at 3.2 MB, linear);
    68–737 ms on the `many` blocks, whose excess is the per-line bookkeeping
    of 131–193 thousand lines; and 849 ms on the tab-mixed `cli-body`
    survivor — the largest, its excess being `collapse_runs` over 1.6
    million runs. Every band is listed because round 8 found the ~100 ms one
    missing from a list that read as complete. The control's uniform bound
    is looser: a quadratic that slows every line equally is caught only past
    `8 × dc1 + 1 ms + 2 × c[1]`, which admits ≈ 94 ms at 3.2 MB on
    `cli-preview` and ≈ 88 ms on `cli-header` (the largest fixed costs),
    30–55 ms on the other CLI exits and on `lib` and `segments`, and 12–26
    ms on `matcher`, `cli-body-2` and `cli-preview-2`. All of these move
    with the machine and the load — they are a range taken idle, not
    constants. Read those as COEFFICIENTS, not as costs:
    what is admitted is a QUADRATIC, so ≈ 730 ms at 3.28 MB is
    q ≈ 7e-11 ms/char², which is ≈ 5 s at 9.6 MB and ≈ 50 s at 30 MB — and
    `--file` bounds neither line length nor line count. A regression that
    stays under the criterion is the same defect class the issue is about
    with a smaller coefficient, reaching the issue's own five seconds about
    three times further out. That is the edge the next reader should reason
    from; "a fraction of a second per 3 MB", which this paragraph used to
    say, applied a linear unit to a superlinear residue.

    THE EDGE. The family is finite. A slow path keyed on a predicate false on
    every member — an adversary, not a regression — is outside any finite
    test; the defence there is that the chokepoint is one line a reviewer
    can read. The ceilings and slacks are absolute numbers calibrated on an
    18-core machine and are fail-fast heuristics, not the guarantee (each
    is re-measured once before it fails a shape); the growth criteria are
    relative and are. The family runs on CPython only (`bytes_per_char`
    assumes its compact strings). The eight in-line classes run on four of
    the twenty-five prefixes. The line-count blocks are short lines of two or
    three characters, so "many medium-sized cues" is a shape the family does
    not have. MEMORY is not measured at all — the criterion is CPU time by
    design, round 4's repair for false reds — while `collapse_runs`
    materialises one match object per run: the shipped code peaks around
    550 MB on one 3.2 MB line, and this class's own children peak on the
    order of a gigabyte together.
    `segments` runs the light survivor set, so four class variants of
    `colon` / `text` / `mixed` that earlier tables ran there now run on
    `matcher` only. A silent DROP keyed on a head predicate with a threshold
    above the longest line any test builds (≈ 3.3 MB) is outside both the
    family and the differential corpus — `pattern_max` is asserted from
    below only — so a cap at 4 MB passes; that is the finite-corpus edge of
    the drop test, and round 7 found it unstated.

    The structural checks remain as TRIPWIRES at the bottom: they fire in
    20 ms on the spellings a real author reaches for and name the line. They
    are not the guarantee, and their messages say so.
    """

    # ── guarantees ──────────────────────────────────────────────────────

    PROBE = REPO / "tests" / "probe_segment_timing.py"

    _STAMPS = ("00:10", "1:02:03.5")
    _ENDS = ("", " - 00:20", " - ", " - banana")
    _SPEAKERS = ("", "S: ", "Speaker Name: ")
    # `product(...)` is the OUTERMOST iterable, the one place a class body's
    # names are visible to a generator expression. A spanning subset of the
    # pattern's branches, not every string it admits.
    PREFIXES = tuple(f"[{s}{e}] {sp}"
                     for s, e, sp in itertools.product(_STAMPS, _ENDS, _SPEAKERS)
                     ) + ("[ 00:10 ] ",)
    # Four classes the issue and round 3 named, then EVERY whitespace code
    # point `str.splitlines()` breaks on, other than the two that never sit
    # mid-line in the parser's input: `\n` (the split) and `\r` (folded by
    # `read_text` before the parser; a stray mid-line `\r` from a library
    # caller strips like the other twelve) — eight,
    # derived by that rule in `test_the_shape_table_has_the_breadth_the_
    # docstring_claims`. Round 4 named three of them from memory (and
    # measured U+2028 at 3.1x the cost of a space); round 6 counted the rest.
    TAILS = (" ", "\t", "\u00a0", "\u3000",
             "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
    SPAN_TAILS = (" ", "\u2028")
    SPAN_PREFIXES = ("[00:10] ", "[00:10] S: ",
                     "[1:02:03.5 - 00:20] Speaker Name: ", "[00:10 - banana] S: ")
    # The no-cue exit needs prefixes that yield NO cue once stripped; a
    # speaker prefix strips to `[00:10] S:`, which the grammar reads as the
    # text "S:".
    ZERO_CUE_PREFIXES = ("[00:10] ", "[1:02:03.5 - 00:20] ", "[00:10 - banana] ", "[ 00:10 ] ")
    # Heads for the shapes that grow BEFORE the closing bracket: after a dash
    # (the end group), after a stamp, and right after `[`.
    OPEN_PREFIXES = ("[00:10 -", "[00:10 - ", "[00:10", "[")
    # An unclosed bracket makes the fixed pattern walk `[^\]]*` back one
    # character at a time — linear, but ~150 ms per 3.2 MB — so the `open`
    # shapes run two whitespace classes, not seven; `[^\]]*` does not care
    # which class it is walking back over.
    OPEN_TAILS = (" ", "\u2028")
    # Heads for the `run` shapes: a whitespace run the pattern crosses AFTER
    # the closing bracket (`\]\s*`) and after the speaker colon (`:\s*`) —
    # the two regions round 6 found no shape growing at the pattern.
    RUN_PREFIXES = ("[00:10]", "[00:10] S:")

    @staticmethod
    def _shapes(prefixes, tails, survivors=True, before=True):
        """The shape table for one path: (kind, prefix, tail) triples.

        `survivors` / `before` select the after-bracket survivors and the
        before-bracket shapes: True for the full set; "light" for one of
        each kind the path's code branches on (the CLI paths carry a 3.2 MB
        cue through sanitise, collapse and the SRT write on every survivor
        rep — 125–210 ms each at 3.2 MB, and ~1.5 s for the tab-mixed one,
        which is `collapse_runs` over 1.6 million whitespace runs on the
        fixed code — cProfile at 3.2 MB: `collapse_runs` 1.58 s,
        `wrap_cue_text` 0.35 s, `sanitise` 0.31 s; round 7 corrected the
        attribution — linear and the heaviest shape in the family);
        "classes" for the tail-DEPENDENT kinds only (a
        second table of more whitespace classes on fewer prefixes); False
        for none (the no-cue exit: a survivor yields a cue and the exit-1
        branch becomes unreachable, which is structural, not an omission —
        the pattern still sees those shapes on `matcher`).
        """
        cls = TestSegmentMatchingHasOneEntryPoint
        shapes = [("tail", p, t) for p in prefixes for t in tails]
        if survivors == "light":
            # Round 6: every CLI survivor was printable ASCII with plain
            # spaces, so `sanitise`'s per-character walk (entered by any
            # non-printable character — a TAB is one) and `wrap_cue_text`'s
            # CJK width-break were timed at length zero on every CLI path.
            shapes += [("colon", "[00:10] ", " "), ("text", "[00:10] S: ", " "),
                       ("cjk", "[00:10] S: ", " "), ("mixed", "[00:10] S: ", "\t")]
            shapes += [("run", p, " ") for p in cls.RUN_PREFIXES]
        elif survivors == "classes":
            shapes += [("mixed", "[00:10] S: ", t) for t in tails]
            shapes += [("run", p, t) for p in cls.RUN_PREFIXES for t in tails]
        elif survivors:
            # The `colon` shape is the pattern's most expensive linear case
            # (60 bounded speaker attempts: ≈ 200 ms per 3.2 MB on the fixed
            # code), so it is kept to two members; the speaker group accepts
            # every whitespace class alike, so the class matters less here
            # than the two stamp forms do.
            shapes += [("colon", "[00:10] ", " "), ("colon", "[1:02:03.5 - 00:20] ", " ")]
            shapes += [("text", "[00:10] S: ", " "), ("text", "[00:10 - 00:20] Speaker Name: ", " ")]
            shapes += [("cjk", "[00:10] S: ", " "), ("cjk", "[00:10] ", " ")]
            shapes += [("mixed", "[00:10] S: ", t) for t in tails]
            shapes += [("run", p, t) for p in cls.RUN_PREFIXES for t in tails]
        if before == "light":
            shapes += [("open", "[00:10 -", " "), ("end", "[00:10 - ", " "), ("lead", "[", " ")]
        elif before == "open":
            # The no-cue exit again: `end` and `lead` close their bracket and
            # yield a cue; `open` never closes it and yields none.
            shapes += [("open", "[00:10 -", " ")]
        elif before == "classes":
            shapes += [("end", "[00:10 - ", t) for t in tails]
            shapes += [("lead", "[", t) for t in tails]
        elif before:
            shapes += [("open", p, t) for p in cls.OPEN_PREFIXES for t in cls.OPEN_TAILS]
            shapes += [("end", "[00:10 - ", t) for t in tails]
            shapes += [("lead", "[", t) for t in tails]
        return shapes

    # Ceilings on the shape's own path time: fail-fast heuristics that bound
    # the next stage's worst case, not the guarantee. Growth on increments at
    # three sizes (byte-equalised in the probe); `min` of reps; reps differ.
    # The first ceiling is where a cubic dies: round 6's ledger found the
    # pre-#50 end group costing 45 s per measurement at 3 200, so the guard
    # was red only at the 120 s deadline; at 800 the fixed code's worst
    # shape (`cli-header mixed`) needs ~1.4 ms idle, ~2.1 ms on efficiency
    # cores. The last one is where a small-coefficient quadratic — one that
    # clears every earlier ceiling — is caught in seconds rather than at the
    # growth stage's deadline. The `--file` body path carries a 3.2 MB cue
    # through `collapse_runs` / `wrap_cue_text` / the SRT write, and its
    # tab-mixed survivor costs 260–420 ms at 819 200 on the fixed code —
    # round 7 found it at 65–105% of a 400 ms ceiling and red under load, the
    # false-red mode round 4 was built to remove — so that path has its own
    # table; every other shape is under 120 ms there. Headroom on the
    # binding shape is ~5x, not the "100x" an earlier comment claimed, and
    # a missed ceiling is re-measured once before it fails a shape.
    CEILINGS = ((800, 10.0), (3200, 25.0), (12800, 100.0), (51200, 100.0),
                (204800, 200.0), (819200, 400.0))
    CEILINGS_CUE = ((800, 10.0), (3200, 25.0), (12800, 100.0), (51200, 100.0),
                    (204800, 400.0), (819200, 1500.0))
    GROWTH = (204800, 819200, 3276800)
    CEILING_REPS, GROWTH_REPS = 1, 3   # one ceiling sample, re-measured on a miss; three growth reps
    K_GROWTH, K_UNIFORM = 8.0, 8.0
    SLACK_MS, UNIFORM_SLACK_MS = 0.5, 1.0
    # The hard bound for ALL children together, which run in parallel. The
    # fixed code needs ~13 s of wall on an idle 18-core machine and ~76 s
    # under background QoS (every child on an efficiency core) — round 8
    # measured both, against a comment that claimed ~8 s. A deadline is a
    # hang detector, so it is set where a hang is unambiguous rather than
    # where correct code is close: at 120 s the E-core figure had 1.6x.
    CHILD_TIMEOUT = 300.0

    # path → (shapes, expected exit code or None for a library call,
    #         a string the run must print or None)
    @classmethod
    def _paths(cls):
        # The full prefix x class product for the four classes the issue and
        # round 3 named, then the eight in-line classes on the spanning
        # prefixes: `mixed`, `run`, `end` and `lead` on all twelve classes, the
        # `tail` product itself on four (the strip treats them alike, and
        # the full product was 168 further shapes of rstrip). The two heaviest
        # tables are split into two children each, interleaved, so the wall
        # time is half — the machine has the cores.
        matcher = (cls._shapes(cls.PREFIXES, cls.TAILS[:4])
                   + cls._shapes(cls.SPAN_PREFIXES, cls.TAILS[4:], "classes", "classes"))
        lib = cls._shapes(cls.SPAN_PREFIXES, cls.TAILS[:4], before="light")
        # The line-count shapes: a block of lines the parser DROPS, one of
        # lines it keeps as cues, and one of cues whose end is malformed
        # (`lost_ends`, the third per-line list). Round 7 found only the
        # dropped block on a CLI path, so the cue count never reached
        # `build_cues` / `render_srt`, and the third list never grew anywhere.
        # The cue-producing prefixes carry a WORD (`x`): round 8's DA showed
        # `[00:10] S: ` producing cues only through the leniency that reads a
        # bare `S:` as the text — so one plausible parser tightening ("a bare
        # speaker label is not speech") emptied all three blocks at once and
        # took the whole cue-count axis with them, silently.
        dropped = ("many", "[00:10] ", " ")
        kept = ("many", "[00:10] S: x", " ")
        lost = ("many", "[00:10 - z] S: x", " ")
        return {
            "matcher-1":   (matcher[0::2], None, None),
            "matcher-2":   (matcher[1::2], None, None),
            # The full family runs on `matcher`; the path-level children exist
            # to catch what a PATH adds, which no prefix choice hides, so they
            # run spanning subsets.
            "lib-1":       (lib[0::2] + [dropped], None, None),
            "lib-2":       (lib[1::2] + [kept, lost], None, None),
            "segments":    (cls._shapes(cls.SPAN_PREFIXES, cls.SPAN_TAILS, "light", "light")
                            + [dropped, kept, lost], None, None),
            # Cue-producing before-bracket shapes (`end`, `lead`) push a 3.2 MB
            # cue through sanitise, collapse and the SRT write per rep; they
            # run on `lib` / `segments`, and the CLI exits get `open`.
            # The frontmatter is unbounded (`_frontmatter_span` reads to the
            # next `---`), and `main` walks it three times — the header list,
            # the `_match_segment` gate that is this fix's SECOND call site,
            # and `header_oddities`. Round 8 found all three timed at three
            # lines, so a quadratic in the gate cost 19 s per 3.5 MB, green.
            # The block must be CUE-SHAPED to reach the gate's body.
            "cli-header":  (cls._shapes(cls.SPAN_PREFIXES, cls.SPAN_TAILS, "light", "open")
                            + [kept],
                            0, "wrote "),
            # The no-cue exit re-reads every dropped line (`stamped = [l for
            # l in dropped if CUE_SHAPED.match(l)]`); round 8 found that walk
            # timed at one line, so a quadratic there cost 44 s per 1.6 MB.
            "cli-zero":    (cls._shapes(cls.ZERO_CUE_PREFIXES, cls.SPAN_TAILS, False, "open")
                            + [dropped],
                            1, "looked like segments"),
            "cli-body":    (cls._shapes(cls.SPAN_PREFIXES, cls.SPAN_TAILS, "light", "open"),
                            0, "wrote "),
            # The same `--file` body branch, in a child of its own: 160 000
            # cues through `build_cues` and `render_srt` per rep is the
            # heaviest work in the family.
            "cli-body-2":  ([dropped, kept, lost], 0, "wrote "),
            # A survivor yields the SAME cue in both files, so the comparison
            # is still refused (exit 3, same marker) — after both were parsed.
            "cli-preview": (cls._shapes(cls.ZERO_CUE_PREFIXES, cls.SPAN_TAILS, False, "open")
                            + [("text", "[00:10] S: ", " ")],
                            3, "no source comparison to show"),
            # `_cue_lines` and `differing_sample` are reachable from this
            # branch and nowhere else, and they walk the cues of BOTH files
            # positionally. Round 8 found them timed at two cues, so a
            # quadratic in the pairing cost 38 s per 3 MB, green.
            "cli-preview-2": ([dropped, kept], 3, "no source comparison to show"),
        }

    def _spec_constants(self):
        return {"k_growth": self.K_GROWTH, "k_uniform": self.K_UNIFORM,
                "slack_ms": self.SLACK_MS, "uniform_slack_ms": self.UNIFORM_SLACK_MS}

    # The paths that carry MEGABYTES OF CUE through `collapse_runs`,
    # `wrap_cue_text` and the SRT write — as one long survivor line
    # (`cli-body`, whose tab-mixed shape costs ~310 ms at 819 200) or as a
    # block of a hundred thousand short ones (`cli-body-2`, and
    # `cli-preview-2` twice over, once per file). They get the looser table.
    # Named rather than derived: the two ways to be cue-heavy do not share a
    # predicate, and round 9 measured a derivation from the `many` blocks
    # alone putting `cli-body` back at 78% of the ordinary ceiling — the
    # false red rounds 4, 7 and 8 each had to remove. Worst measured ratios
    # against the table each path gets: cli-body 21%, cli-preview-2 17%,
    # cli-body-2 16%, everything else at most 16%.
    CUE_HEAVY = ("cli-body", "cli-body-2", "cli-preview-2")

    @classmethod
    def _ceilings(cls, path):
        return cls.CEILINGS_CUE if path in cls.CUE_HEAVY else cls.CEILINGS

    def _spawn(self, path, shapes, sandbox, cache, scratch, nonce):
        """One child. Its spec, scratch files and captured streams live in
        `scratch`, never in `cache` — `cache` is the CLI's `PLAUD_CACHE_DIR`
        and must hold only what the CLI (and the `--preview-sources` fixture
        the probe writes for it) put there. Both are under `sandbox`, the one
        root the child may write beneath, named in its spec by THIS process
        (round 6: derived from `TMPDIR`, it was the caller's to move).
        Streams go to FILES, not pipes: a child that fills a 64 KB pipe while
        the parent waits on a sibling would hang and be reported as a
        quadratic. `nonce` is what the marker file in `sandbox` carries; the
        child refuses a root without it (round 7: a root INSIDE the home
        directory, `~/.plaud-connector` included, passed the refusals).
        """
        work = scratch / path
        work.mkdir()
        spec = json.dumps({
            "script": str(SCRIPT), "path": path, "shapes": shapes,
            "sandbox": str(sandbox), "nonce": nonce, "work": str(work),
            "ceilings": self._ceilings(path), "growth": self.GROWTH,
            "ceiling_reps": self.CEILING_REPS, "growth_reps": self.GROWTH_REPS,
            **self._spec_constants(),
        })
        (work / "spec.json").write_text(spec, encoding="utf-8")
        with open(work / "stdout.json", "w", encoding="utf-8") as out, \
             open(work / "stderr.txt", "w", encoding="utf-8") as err:
            return subprocess.Popen([sys.executable, str(self.PROBE), str(work / "spec.json")],
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    env=cli_env(cache))

    def test_every_rep_builds_a_distinct_line(self):
        """`min()` over reps only defeats a cache if the reps differ. Round 5
        found `mixed` folding `n` and `n + 1` into one string through integer
        division, so two of three reps were the same input."""
        for path, (shapes, _, _) in self._paths().items():
            for kind, prefix, tail in shapes:
                for n in (3200, 204800, 204801):
                    a, b = (_probe.build_line(kind, prefix, tail, n),
                            _probe.build_line(kind, prefix, tail, n + 1))
                    with self.subTest(path=path, kind=kind, prefix=prefix, tail=tail, n=n):
                        self.assertNotEqual(a, b, "consecutive reps build the same line")
                        control = _probe.control_line(kind, a)
                        self.assertNotEqual(a, control, "control equals the shape")
                        self.assertEqual(len(a), len(control))
                        if kind in _probe.SHORT_LINES:
                            self.assertNotIn("\n", control, "a short-line control must be ONE line")

    def test_the_shape_table_covers_every_kind_where_it_can(self):
        """The composition is asserted, not assumed: round 5 found the
        survivor shapes could be removed from the table with a three-line
        edit and the suite stayed green."""
        paths = self._paths()
        matcher = {k for p, (sh, _, _) in paths.items() if p.startswith("matcher")
                   for k, _, _ in sh}
        for kind in _probe.KINDS:
            if kind in _probe.SHORT_LINES:
                continue    # a block of lines; the helper takes one — asserted on lib/cli-body below
            with self.subTest(kind=kind):
                self.assertIn(kind, matcher, f"the full family on `matcher` has no {kind} shape")
        # Per PATH (the two halves of a split table are one path).
        merged = {}
        for p, (sh, _, _) in paths.items():
            merged.setdefault(p.rsplit("-", 1)[0] if p.rsplit("-", 1)[-1].isdigit() else p,
                              set()).update(k for k, _, _ in sh)
        for path, kinds in merged.items():
            with self.subTest(path=path):
                self.assertIn("tail", kinds)
                self.assertTrue(kinds & set(_probe.REACHES_PATTERN),
                                f"{path}: no shape lets the pattern see a long string")
                self.assertTrue({"open", "end", "lead"} & kinds,
                                f"{path}: no shape grows before the closing bracket")

    def test_the_shape_table_has_the_breadth_the_docstring_claims(self):
        """The other two axes, pinned. Round 6 cut `TAILS` to one class and
        `PREFIXES` to one form with the class green: breadth was asserted on
        the kind axis only, and the docstring, the README and the issue
        addendum were all false with nothing red. The whitespace classes are
        derived here by the rule that selects them; the prefixes and the
        table by size, so an edit that guts the table has to say so here."""
        # Every whitespace code point `str.splitlines()` breaks on, other
        # than the two that never sit mid-line in the parser's input: `\n`
        # is the split and `\r` is folded by `read_text` before the parser.
        inline = {chr(cp) for cp in range(0x110000)
                  if chr(cp).isspace() and cp not in (0x0A, 0x0D)
                  and len(("a" + chr(cp) + "b").splitlines()) == 2}
        self.assertEqual(8, len(inline))
        self.assertEqual(inline, set(self.TAILS[4:]),
                         "TAILS no longer carries every in-line line-boundary class")
        self.assertEqual((" ", "\t", "\u00a0", "\u3000"), self.TAILS[:4])
        self.assertEqual(12, len(set(self.TAILS)))
        self.assertEqual(25, len(set(self.PREFIXES)))
        self.assertLessEqual(set(self.SPAN_PREFIXES), set(self.PREFIXES))
        paths = self._paths()
        on_matcher = [sh for p in ("matcher-1", "matcher-2") for sh in paths[p][0]]
        tails = {(p, t) for k, p, t in on_matcher if k == "tail"}
        self.assertLessEqual(set(itertools.product(self.PREFIXES, self.TAILS[:4])), tails,
                             "the prefix x class product no longer all runs on matcher")
        self.assertLessEqual(set(itertools.product(self.SPAN_PREFIXES, self.TAILS)), tails,
                             "the spanning prefixes no longer run every class")
        for kind in ("mixed", "run", "end", "lead"):
            self.assertEqual(set(self.TAILS), {t for k, _, t in on_matcher if k == kind},
                             f"{kind} no longer runs every class on matcher")
        many = {pre for p, (sh, _, _) in paths.items() if p.startswith("cli-body")
                for k, pre, _ in sh if k == "many"}
        self.assertEqual({"[00:10] ", "[00:10] S: x", "[00:10 - z] S: x"}, many,
                         "the --file body path no longer grows dropped lines, cues AND lost "
                         "ends (round 7: only dropped lines, so build_cues never saw the count)")
        # Every path that walks a per-line or per-cue list gets the count
        # axis, and the cue-producing blocks carry a word rather than leaning
        # on the bare-label leniency (round 8, both findings).
        for path, want in (("cli-header", {"[00:10] S: x"}),
                           ("cli-zero", {"[00:10] "}),
                           ("cli-preview", {"[00:10] ", "[00:10] S: x"}),
                           ("segments", {"[00:10] ", "[00:10] S: x", "[00:10 - z] S: x"}),
                           ("lib", {"[00:10] ", "[00:10] S: x", "[00:10 - z] S: x"})):
            got = {pre for p, (sh, _, _) in paths.items() if p.startswith(path)
                   for k, pre, _ in sh if k == "many"}
            with self.subTest(path=path):
                self.assertEqual(want, got, f"{path}: the line count no longer grows here")
        self.assertLessEqual(set(self.CUE_HEAVY), set(paths),
                             "CUE_HEAVY names a path that does not exist")
        # The children, by name: round 7 found this the one axis nothing
        # asserted — a path could be deleted and its shapes redistributed
        # with the total unchanged.
        self.assertEqual({"matcher-1", "matcher-2", "lib-1", "lib-2", "segments", "cli-header",
                          "cli-zero", "cli-body", "cli-body-2", "cli-preview", "cli-preview-2"},
                         set(paths))
        self.assertEqual(322, sum(len(sh) for sh, _, _ in paths.values()),
                         "the family changed size — say so here, in the class docstring "
                         "and in the README")

    def test_the_probe_refuses_a_sandbox_that_is_not_its_own(self):
        """The refusals are what make a tracked, runnable, path-writing file
        not a gadget — so they are tested, not trusted. Round 5's incident
        wrote into the real cache; round 6 showed the refusal keyed on
        `TMPDIR`, which the same caller sets; round 7 showed every directory
        UNDER the home directory accepted; round 8 showed the same for the
        repository's own subdirectories and for the cache directory one level
        below the guarded name, and the home guard still read `$HOME` — the
        variable the same caller sets, which the test then read too, so the
        two agreed by construction. The guards come from the password
        database now, containment is checked in both directions, and the root
        must carry a marker naming this run.
        """
        import pwd
        home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        cache = home / ".plaud-connector"
        # Ancestors, the guarded directories themselves, and — the round-7
        # and round-8 halves — directories INSIDE them, marker or no marker.
        for bad in (home, home.parent, pathlib.Path("/"), REPO, REPO.parent,
                    cache, cache / "cache", home / "Documents", home / "Music",
                    REPO / "tests", REPO / "scripts"):
            with self.subTest(sandbox=str(bad)):
                with self.assertRaises(SystemExit):
                    _probe.sandbox_root({"sandbox": str(bad), "nonce": "n"})
        # A planted marker does not buy entry to a guarded tree.
        planted = REPO / "tests" / "_sandbox_probe_check"
        planted.mkdir(exist_ok=True)
        try:
            (planted / _probe.MARKER).write_text("n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                _probe.sandbox_root({"sandbox": str(planted), "nonce": "n"})
        finally:
            shutil.rmtree(planted, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d).resolve()
            if root.is_relative_to(home):
                self.skipTest("this machine's temp directory is inside the home directory, "
                              "which the probe refuses by design")
            with self.assertRaises(SystemExit):     # no marker: not ours
                _probe.sandbox_root({"sandbox": d, "nonce": "n"})
            (root / _probe.MARKER).write_text("n", encoding="utf-8")
            with self.assertRaises(SystemExit):     # a marker, but not this run's
                _probe.sandbox_root({"sandbox": d, "nonce": "other"})
            with self.assertRaises(SystemExit):     # no nonce offered at all
                _probe.sandbox_root({"sandbox": d})
            self.assertEqual(root, _probe.sandbox_root({"sandbox": d, "nonce": "n"}))
            with self.assertRaises(SystemExit):
                _probe.sandbox_root({"sandbox": str(root / "missing"), "nonce": "n"})
            inside = root / "in"
            inside.mkdir()
            self.assertTrue(_probe._contained(inside / "rec.md", root))
            (inside / "link").symlink_to(home)
            self.assertFalse(_probe._contained(inside / "link" / "rec.md", root),
                             "a write through a planted symlink resolved outside the sandbox")

    @unittest.skipUnless(sys.implementation.name == "cpython",
                         "the probe's byte-equalised sizes assume CPython's compact strings")
    def test_every_reachable_path_is_linear_across_the_input_family(self):
        """Eleven children over seven paths, all in parallel, judged here."""
        results = {}
        with tempfile.TemporaryDirectory() as sandbox_dir:
            sandbox = pathlib.Path(sandbox_dir).resolve()
            cache, scratch = sandbox / "cache", sandbox / "scratch"
            cache.mkdir()
            scratch.mkdir()
            nonce = secrets.token_hex(8)
            (sandbox / _probe.MARKER).write_text(nonce, encoding="utf-8")
            children = {}
            try:
                for path, (shapes, _, _) in self._paths().items():
                    children[path] = self._spawn(path, shapes, sandbox, cache, scratch, nonce)
                # ONE deadline for all of them: they run in parallel, so waiting
                # on each with its own timeout let a hung set take N x the bound.
                # And ONE verdict ends the round: the first child to exit 2 has
                # found a superlinear path, and the siblings — still grinding a
                # regressed helper towards the deadline — are killed, so the
                # time to red is the first child's, not the slowest's.
                deadline = time.monotonic() + self.CHILD_TIMEOUT
                pending, rcs, stopped = dict(children), {}, {}
                while pending and time.monotonic() < deadline:
                    for path, child in list(pending.items()):
                        if child.poll() is None:
                            continue
                        rcs[path] = child.returncode
                        del pending[path]
                        if child.returncode == 2:
                            for other, sibling in pending.items():
                                # A sibling that has ALSO finished keeps its
                                # report and its own code (round 6: a second
                                # exit 2 in the same poll was reported as a
                                # kill); one still running is killed.
                                if sibling.poll() is None:
                                    sibling.kill()
                                    sibling.wait()
                                    stopped[other] = path
                                else:
                                    rcs[other] = sibling.returncode
                            pending.clear()
                            break       # the snapshot list still holds the killed siblings
                    time.sleep(0.2)
                # Past the deadline: name EVERY child still running, not the
                # first in insertion order (round 6: the message blamed an
                # arbitrary sibling for the budget).
                still = sorted(pending)
                for path, child in pending.items():
                    child.kill()
                    child.wait()
                for path in children:
                    work = scratch / path
                    rc = rcs.get(path)
                    results[path] = (rc, (work / "stdout.json").read_text(encoding="utf-8"),
                                     (work / "stderr.txt").read_text(encoding="utf-8"),
                                     stopped.get(path) or (still if rc is None else None))
            finally:
                for child in children.values():
                    if child.poll() is None:
                        child.kill()
                        child.wait()

        for path, (rc, out, err, why) in results.items():
            shapes, want_exit, want_printed = self._paths()[path]
            if rc is None and isinstance(why, str) and results[why][0] == 2:
                continue        # killed because a sibling already found the regression
            with self.subTest(path=path):
                last = err.strip().splitlines()[-1] if err.strip() else "(no progress line)"
                budget = (f" (the budget shared by all children ran out with "
                          f"{', '.join(why)} still running)" if isinstance(why, list) else "")
                self.assertIsNotNone(
                    rc, f"{path}: the probe did not finish within {self.CHILD_TIMEOUT:.0f} s"
                        f"{budget} — a quadratic slow enough to pass the ceilings, or a "
                        f"hung child; last completed: {last}")
                try:
                    report = json.loads(out)
                except json.JSONDecodeError:
                    self.fail(f"{path}: probe exited {rc} without a report; stderr tail:\n"
                              + err[-1500:])
                if report["failed"]:
                    self._explain(report["failed"])
                self.assertEqual(0, rc, f"{path}: probe exited {rc}; stderr tail:\n" + err[-1500:])
                self._judge(path, shapes, report, want_exit, want_printed)

    def _explain(self, f):
        """The child stopped at the first failing shape; say which and why."""
        sizes = " → ".join(map(str, self.GROWTH))
        if f["stage"] == "ceiling":
            self.fail(f"{f['label']}: n={f['n']:,} took {f['ms']:.0f} ms of CPU — the #57 "
                      f"quadratic, or its cubic sibling; a {f['n']:,}-character line is a "
                      f"reachable DoS")
        if f["stage"] == "excess":
            p, c, e = f["times"], f["control"], f["excess"]
            self.fail(f"{f['label']}: the path cost {p[0]:.3f} → {p[1]:.3f} → {p[2]:.3f} ms, "
                      f"its same-length control {c[0]:.3f} → {c[1]:.3f} → {c[2]:.3f} ms, so "
                      f"the work only this shape triggers cost {e[0]:.3f} → {e[1]:.3f} → "
                      f"{e[2]:.3f} ms at n={sizes}; that excess grew {f['ratio']:.1f}x for 4x "
                      f"the input (linear ≈ 4x, quadratic ≈ 16x; up to {f['admitted']:.2f} ms "
                      f"of extra cost at the top size was admitted) — superlinear again on "
                      f"this path")
        c = f["control"]
        self.fail(f"{f['label']}: the CONTROL line — rejected at character 0 — cost "
                  f"{c[0]:.3f} → {c[1]:.3f} → {c[2]:.3f} ms at n={sizes} (increments "
                  f"{f['ratio']:.1f}x for 4x); something on this path is superlinear for "
                  f"every line, not just the pathological ones")

    def _judge(self, path, shapes, report, want_exit, want_printed):
        """What the parent adds to the child's verdict: the report is for the
        shapes that were asked for, in order (round 5: an empty report passed
        vacuously); the recorder and spy bounds; the exit codes and markers.
        The growth criterion is re-run on the stored numbers with the same
        function the child used — it cannot disagree, and is here so a probe
        that stopped calling `judge()` could not report green."""
        self.assertEqual(path, report["path"], "a report for a different path")
        self.assertEqual([tuple(s) for s in shapes],
                         [(sh["kind"], sh["prefix"], sh["tail"]) for sh in report["shapes"]],
                         f"{path}: the report does not cover the shapes that were asked for")
        for sh in report["shapes"]:
            label = f"{path} {sh['kind']} {sh['prefix']!r} + {sh['tail']!r}"
            # Sized from the parent's own build of the shape, not from the
            # child's report: `bpc` sizes both the measurement and the bar the
            # measurement is held to (round 7).
            bpc = _probe.bytes_per_char(_probe.build_line(sh["kind"], sh["prefix"], sh["tail"], 256))
            n_top = self.GROWTH[-1] // bpc
            small = self.GROWTH[0] // bpc
            lines = 1
            if sh["kind"] in _probe.SHORT_LINES:
                lines = _probe.build_line(sh["kind"], sh["prefix"], sh["tail"], n_top).count("\n")
            with self.subTest(shape=label):
                self.assertEqual(bpc, sh["bpc"], f"{label}: the child sized this shape at "
                                                  f"{sh['bpc']} bytes per character; the parent gets {bpc}")
                for n, limit in self._ceilings(path):
                    self.assertLess(sh["ceiling"][str(n)], limit,
                                    f"{label}: n={n} took {sh['ceiling'][str(n)]:.1f} ms")
                p = [sh["growth"][str(n)][0] for n in self.GROWTH]
                c = [sh["growth"][str(n)][1] for n in self.GROWTH]
                verdict = _probe.judge(self._spec_constants(), p, c)
                if verdict is not None:
                    self._explain({"label": label, **verdict})
                # The spy and the recorder, for the SHAPE's runs and, held to
                # the same bound, the control's (round 6: one counter for
                # both, and the control filled it). Every line of a `many`
                # block must reach the helper, and its control must be ONE
                # long line (round 7: the same block as control); on the other
                # kinds the one line must reach it, at full length.
                if sh["kind"] in _probe.SHORT_LINES:
                    # What the CLI's own ledger says it did with the block —
                    # round 8 found the fixture producing 163 840 cues and
                    # NOTHING asserting it, so an unrelated parser tightening
                    # took the cue count to 1 and left `build_cues` and
                    # `render_srt` timed at one cue with everything green.
                    # What this PATH does with a block of short lines, from
                    # the run's own ledger and from `build_cues` itself.
                    # Round 8 found the cue count present in the fixture and
                    # asserted nowhere, so an unrelated parser tightening took
                    # it to 1 with everything green.
                    want = lines // 2
                    led = (sh["exit"] or {}).get("ledger") or {}
                    # Whether this path is cue-heavy is decided by what the
                    # run actually did, not by its name: a block that reached
                    # `build_cues` in quantity belongs on the looser ceiling
                    # table, and round 9 measured the two ways of getting
                    # there (one huge cue, or a hundred thousand small ones)
                    # not sharing a predicate.
                    if sh["cues_in"] >= want:
                        self.assertIn(path, self.CUE_HEAVY,
                                      f"{label}: {sh['cues_in']} cues reached build_cues on a "
                                      f"path held to the ordinary ceilings")
                    if path == "cli-header":
                        # Inside the frontmatter: these lines become HEADER
                        # lines, and `main` walks that list three times.
                        self.assertGreaterEqual(
                            led.get("header", 0), want,
                            f"{label}: the run reported {led.get('header', 0)} header lines "
                            f"of {lines} — the block is meant to land inside the "
                            f"frontmatter, where the second call site walks it")
                    elif want_exit == 1:
                        self.assertGreaterEqual(
                            led.get("zero_dropped", 0), want,
                            f"{label}: the no-cue exit reported {led.get('zero_dropped', 0)} "
                            f"content lines of {lines} — the drop count is what this path "
                            f"re-walks")
                    elif sh["prefix"].endswith("x"):
                        # Observed, not read back out of the CLI's output:
                        # `--preview-sources` prints no ledger at all. The
                        # library paths stop at the parser, so their analogue
                        # is the parser's own lists.
                        self.assertGreaterEqual(
                            sh["parsed_cues"], want,
                            f"{label}: the parser returned {sh['parsed_cues']} cues from "
                            f"{lines} lines — these lines are supposed to BECOME cues")
                        if "-" in sh["prefix"]:
                            self.assertGreaterEqual(
                                sh["parsed_lost"], want,
                                f"{label}: the parser returned {sh['parsed_lost']} lost ends "
                                f"from {lines} lines — the third per-line list is not growing")
                        if want_exit is not None:
                            self.assertGreaterEqual(
                                sh["cues_in"], want,
                                f"{label}: build_cues was handed {sh['cues_in']} cues from "
                                f"{lines} lines — build_cues and render_srt are timed at one")
                        if want_exit == 0:
                            self.assertGreaterEqual(
                                led.get("cues", 0), want,
                                f"{label}: the run reported {led.get('cues', 0)} cues of "
                                f"{lines} lines")
                            if "-" in sh["prefix"]:
                                self.assertGreaterEqual(
                                    led.get("lost_ends", 0), want,
                                    f"{label}: {led.get('lost_ends', 0)} declared ends "
                                    f"discarded of {lines} — the lost_ends list is not growing")
                    else:
                        # The parser's own list, so this holds on the library
                        # paths and on `--preview-sources`, neither of which
                        # prints a dropped count.
                        self.assertGreaterEqual(
                            sh["parsed_skipped"], want,
                            f"{label}: the parser skipped {sh['parsed_skipped']} lines of "
                            f"{lines} — the dropped list is not growing")
                        if want_exit == 0:
                            self.assertGreaterEqual(
                                led.get("dropped", 0), want,
                                f"{label}: the run reported {led.get('dropped', 0)} dropped "
                                f"lines of {lines}")
                    self.assertGreaterEqual(
                        sh["spy_calls"], lines,
                        f"{label}: {sh['spy_calls']} of {lines} lines reached _match_segment "
                        f"on this path")
                    self.assertLess(sh["spy_max"], small,
                                    f"{label}: a {sh['spy_max']}-character line reached the "
                                    f"helper; these lines are meant to be short")
                    self.assertLess(sh["pattern_max"], small,
                                    f"{label}: the pattern received {sh['pattern_max']} "
                                    f"characters from a block of short lines")
                    # A small constant, not `lines // 2`: a one-line control
                    # reaches the helper once per line of its fixture, and
                    # `--preview-sources` parses two files (measured: 6).
                    self.assertLessEqual(sh["control_spy_calls"], 8,
                                         f"{label}: the control reached the helper "
                                         f"{sh['control_spy_calls']} times — it is meant to be "
                                         f"one line, so the line count is in the shape alone")
                    self.assertGreaterEqual(sh["control_spy_max"], n_top,
                                            f"{label}: the one-line control never reached the "
                                            f"helper at full length ({sh['control_spy_max']})")
                    self.assertGreaterEqual(sh["control_pattern_max"], small,
                                            f"{label}: the one-line control never reached the "
                                            f"pattern as a long line ({sh['control_pattern_max']})")
                for who in ("", "control_") if sh["kind"] not in _probe.SHORT_LINES else ():
                    role = "control" if who else "shape"
                    spy_max, pattern_max = sh[who + "spy_max"], sh[who + "pattern_max"]
                    self.assertGreaterEqual(
                        sh[who + "spy_calls"], lines,
                        f"{label}: {sh[who + 'spy_calls']} of {lines} lines reached "
                        f"_match_segment on this path ({role})")
                    self.assertGreaterEqual(
                        spy_max, len(sh["prefix"]) + n_top,
                        f"{label}: the pathological line never reached _match_segment on "
                        f"this path (longest {role} argument seen: {spy_max}) — it was "
                        f"matched, or skipped, somewhere else")
                    if sh["kind"] in _probe.STRIPS_TO_PREFIX:
                        # Below the SMALLEST growth size, not "only the prefix":
                        # the CLI fixtures carry a real cue line that the
                        # pattern legitimately sees.
                        self.assertLess(
                            pattern_max, small,
                            f"{label}: the pattern received {pattern_max} characters "
                            f"({role}) — the strip no longer removes this whitespace class")
                    else:
                        self.assertGreaterEqual(
                            pattern_max, n_top,
                            f"{label}: the pattern received at most {pattern_max} "
                            f"characters ({role}) — this shape is supposed to reach it at "
                            f"full length, or the guard is back to round 4's blindness")
                if want_exit is not None:
                    ex = sh["exit"]
                    self.assertEqual(want_exit, ex["code"],
                                     f"{label}: main exited {ex['code']}, expected "
                                     f"{want_exit}; stderr: {ex['stderr']!r}")
                    if path != "cli-preview":
                        self.assertEqual(want_exit == 0, ex["wrote"],
                                         f"{label}: output file "
                                         + ("missing" if want_exit == 0 else "written on an error exit"))
                    self.assertIn(want_printed, ex["stdout"] + ex["stderr"],
                                  f"{label}: the run did not take the branch this path "
                                  f"exists to time; stdout={ex['stdout']!r} stderr={ex['stderr']!r}")

    def test_the_helper_behaves_exactly_as_the_bare_pattern(self):
        """The helper is a chokepoint whose docstring is 35 lines of
        performance reasoning, which makes "one more small perf tweak" the
        likeliest next edit — and a `len(line) > 4096: return None` inside it
        passed every structural guard while dropping every real cue over 4 KB.

        Differential, on a corpus that includes what the contract tables do
        not: long accepted lines, accepted lines with a whitespace tail, and
        (since round 3) tails from the other whitespace classes, a bare CR,
        blank and whitespace-only lines, and bracket mistakes — so an
        `if "\\xa0" in line: return None` cannot hide either. Every group is
        compared by value AND by span: the strip must not move a boundary.
        The two largest cues are PAST the timing family's top size (3 276 800
        characters, or half that in two-byte text): round 6 showed the
        family's own recorder could not see a cap keyed on `^\\[` above the
        sizes it runs, so this corpus has to reach above them.
        `SEGMENT.match` is applied here directly — this is a test, outside the
        tripwire's scope — on inputs where it is fast.
        """
        corpus = [
            "[00:10-00:20] S: x", "[ 00:10 ] S: x", "[00:10 - banana] S: x",
            "[00:10:00 - 446:12] S: x", "[00:10] Speaker 1:",
            "[00:10] S: hello  world   ",          # tail, internal run kept
            "[00:10] S: " + "word " * 1000,        # 5 KB real cue
            "[00:10] S: " + "字" * 8000 + "  ",    # 8 KB non-ASCII cue
            "[00:10] S: " + "x" * 200000,          # 200 KB single cue
            "[00:10] S: " + "x" * 3_300_000,       # 3.3 MB: past the family's top size
            "[00:10] S: " + "字" * 1_700_000 + "  ",  # 3.4 MB of two-byte text, past it too
            "[00:10]", "[00:10]    ", "not a cue", "[99999:00] S: nope",
            "[00:10] S: x\r", "[00:10] S: x\u00a0", "[00:10] S: x  ",
            "[00:10] S: x\t\t", "[00:10] S: x\u3000", "[00:10] S: x \u00a0 y \u00a0",
            *("[00:10] S: x" + t + t for t in
              ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")),
            "[00:10] S:", "[00:10] :", "", "   ", "\u3000", "\r",
            "[00:10 S: no closing bracket", "[00:10]] S: double", "[[00:10] S: x",
            "[00:10 - ] S: empty end   ", "[00:10 -] S: x",
        ]
        for line in corpus:
            with self.subTest(line=repr(line[:24])):
                want = to_srt.SEGMENT.match(line)
                got = to_srt._match_segment(line)
                self.assertEqual(want is None, got is None,
                                 "helper and bare pattern disagree on WHETHER it matches")
                if want is not None:
                    self.assertEqual(want.groupdict(), got.groupdict(),
                                     "helper and bare pattern disagree on WHAT it captured")
                    self.assertEqual(want.groups(), got.groups(),
                                     "helper and bare pattern disagree on an unnamed capture")
                    self.assertEqual([want.span(g) for g in range(1, want.re.groups + 1)],
                                     [got.span(g) for g in range(1, got.re.groups + 1)],
                                     "a group boundary moved — the strip reached into a group")

    def test_rstrip_and_the_patterns_whitespace_class_agree_on_every_code_point(self):
        r"""The premise the whole fix rests on, pinned rather than remembered.

        "`\s*$` discards the same characters as `str.rstrip()`, so no captured
        group changes" was verified in review by enumerating every code point —
        but a claim that lives only in a verify comment is a claim the next
        interpreter upgrade can falsify in silence.

        Round 3 (Codex) pointed out that the first version of this test
        compared `str.isspace()` against a freshly compiled, flag-less `\s`:
        neither side was the thing the fix uses. This one calls `rstrip()` and
        compiles `\s` with `SEGMENT`'s OWN flags, so an `re.ASCII` added to
        the pattern — under which NBSP is `\S` to the pattern and whitespace
        to `rstrip()` — turns it red.
        """
        every = "".join(map(chr, range(0x110000)))
        pattern_eats = {m.start() for m in
                        re.finditer(r"\s", every, flags=to_srt.SEGMENT.flags)}
        rstrip_eats = {cp for cp in range(0x110000) if ("x" + chr(cp)).rstrip() == "x"}
        self.assertEqual(
            [], sorted(hex(cp) for cp in pattern_eats ^ rstrip_eats),
            "rstrip() and the pattern's \\s no longer strip the same characters — "
            "the strip is now changing captured groups, or dropping lines")

    def test_the_blank_line_is_still_rejected_after_stripping(self):
        """The contract the strip must not break: `"[00:10]    "` rstrips to
        `"[00:10]"`, which NOT_TOLERATED already rejects. Both spellings must
        still produce no cue, or the fix bought its speed by accepting a line
        the grammar forbids.
        """
        for line in ("[00:10]", "[00:10]    ", "[00:10] " + " " * 4000):
            with self.subTest(line=repr(line[:20])):
                self.assertEqual([], to_srt.parse_segments(line + "\n"),
                                 "a line whose text is blank produced a cue")

    def test_the_reported_line_keeps_its_original_trailing_whitespace(self):
        """Strip for MATCHING only — checked on BOTH ledger paths.

        - stripping at READ time is caught by the `[99999:00]` case, which
          fails `_STAMP` and never reaches the matcher; it lands in `skipped`
          via the `elif line.strip()` branch.
        - rebinding `line = line.rstrip()` AFTER a match is invisible to that
          case; the `banana` case matches, fails its end, and the RAW line
          must arrive in `lost_ends` intact.
        """
        raw = "[99999:00] S: out of contract   "
        _, skipped, _, _ = to_srt.parse_transcript(raw + "\n")
        self.assertEqual([raw], skipped, "the strip leaked into read time")
        raw = "[00:10 - banana] S: text   "
        cues, _, _, lost_ends = to_srt.parse_transcript(raw + "\n")
        self.assertEqual([raw], lost_ends, "the strip leaked out of the matcher")
        self.assertEqual("text", cues[0]["text"])

    # ── tripwires ───────────────────────────────────────────────────────
    #
    # Fast, named, and known to be incomplete. Each says what it cannot see.

    @staticmethod
    def _segment_reaches(tree):
        """Every way source text can reach SEGMENT that an AST can show:
        a Name load, an attribute `.SEGMENT`, or the string "SEGMENT" (the
        `globals()[...]` / `getattr(..., ...)` spellings). Scope is the
        qualified path, so a method named `_match_segment` inside a class is
        not the module helper.
        """
        out = []

        def visit(node, path):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                path = path + [node.name]
            elif isinstance(node, ast.Lambda):
                path = path + ["<lambda>"]
            if isinstance(node, ast.Name) and node.id == "SEGMENT" and isinstance(node.ctx, ast.Load):
                out.append((".".join(path) or "<module>", "Name", node.lineno))
            elif isinstance(node, ast.Attribute) and node.attr == "SEGMENT":
                out.append((".".join(path) or "<module>", "Attribute", node.lineno))
            elif isinstance(node, ast.Constant) and node.value == "SEGMENT":
                out.append((".".join(path) or "<module>", "str", node.lineno))
            for child in ast.iter_child_nodes(node):
                visit(child, path)

        visit(tree, [])
        return out

    def test_tripwire_segment_is_reached_from_one_place(self):
        """TRIPWIRE. Catches `.search`, an alias, `getattr`, `globals()`, and a
        plain second call site — the spellings a real author reaches for — in
        20 ms and names the line. It CANNOT see a re-compiled copy of the
        pattern text; the timing family above is what catches that.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        reaches = self._segment_reaches(tree)
        self.assertEqual(
            [("_match_segment", "Name")], [(scope, how) for scope, how, _ in reaches],
            "SEGMENT is reached from somewhere other than the helper — route it "
            "through _match_segment, or the strip is skipped on that path.\n  "
            + repr(reaches))
        helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_match_segment"]
        self.assertEqual(1, len(helpers), "exactly one module-level _match_segment")
        stores = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id == "SEGMENT"
                  and isinstance(n.ctx, ast.Store)]
        self.assertEqual(1, len(stores), "SEGMENT is assigned more than once: " + repr(stores))

    def test_tripwire_no_other_script_reaches_segment(self):
        """TRIPWIRE. `SEGMENT` is a public module name. Every other script,
        recursively, may not name it, attribute it, import it, or spell it as
        a string. Today nothing does (cache.py mentions it in prose only).
        """
        for path in sorted(SCRIPT.parent.rglob("*.py")):
            if path == SCRIPT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hits = [(how, ln) for _, how, ln in self._segment_reaches(tree)]
            hits += [("ImportFrom", n.lineno) for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom)
                     and any(a.name == "SEGMENT" for a in n.names)]
            with self.subTest(script=str(path.relative_to(SCRIPT.parent))):
                self.assertEqual([], hits, f"{path.name} reaches SEGMENT: {hits!r}")

    def test_tripwire_the_helper_strips_the_argument_it_matches(self):
        """TRIPWIRE. The single `SEGMENT.match` call's argument must be
        `<param>.rstrip()` with no arguments — `rstrip("\\n")` strips one
        character and walks every space. Shape only: the differential and
        timing tests above are what prove the helper works.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        helper = next(n for n in tree.body
                      if isinstance(n, ast.FunctionDef) and n.name == "_match_segment")
        param = helper.args.args[0].arg
        calls = [n for n in ast.walk(helper)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "match"
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "SEGMENT"]
        self.assertEqual(1, len(calls))
        arg = calls[0].args[0] if calls[0].args else None
        self.assertTrue(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute) and arg.func.attr == "rstrip"
            and isinstance(arg.func.value, ast.Name) and arg.func.value.id == param
            and not arg.args and not arg.keywords,
            f"the helper does not match `{param}.rstrip()` — got " + ast.dump(calls[0]))
