"""Tests for scripts/to_srt.py (issue #4).

Subtitle timing is the kind of thing that looks right and is off by a second, so
these pin the arithmetic and the edge cases rather than smoke-testing that it
produces *some* output.
"""

import importlib.util
import io
import json
import os
import pathlib
import ast
import re
import subprocess
import sys
import tempfile
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
    "stripped_ledger":   r"(\d+) invisible char\(s\) removed",
    "stripped_warn":     r"⚠ (\d+) invisible character\(s\) were removed",
    "refusal_drop_a":    r"transcripts? dropped (\d+)",
    "refusal_drop_b":    r"transcripts dropped \d+ and (\d+)",
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
    """Round 4: the denominator was inverted, because widening it kept failing.

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
    """Round 5: stop deriving the count twice.

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
        """Round 3: the leak moved rather than closed.

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
        Latin wraps AT a space, so the newline replaces one — compare tokens.
        CJK wraps BETWEEN characters with no space involved, so replacing the
        newline with a space would invent one — compare the raw string."""
        for text in ["a b c " * 30, "hello"]:
            self.assertEqual(text.split(), to_srt.wrap_cue_text(text).replace("\n", " ").split(), text)
        for text in ["預算" * 40, "よろしく" * 20]:
            self.assertEqual(text, to_srt.wrap_cue_text(text).replace("\n", ""), text)

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

    def test_the_wrapper_has_no_loop_of_its_own(self):
        src = SCRIPT.read_text(encoding="utf-8")
        body = src[src.index("def parse_segments("):src.index("def build_cues(")]
        self.assertNotIn("SEGMENT.match", body,
                         "parse_segments matches lines itself instead of "
                         "delegating — that is a second parse, and second "
                         "derivations are what this issue kept failing on")


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
        for body in ("---\nid: a\n---\n\n[00:00] S: x\nprose\n",
                     "[00:00] S: x\n---\n[00:10] S: y\n",
                     "---\nid: a\n[00:00] S: in header\n---\n[00:10] S: out\n",
                     "no header at all\n[00:00] S: x\n"):
            for expect in (True, False):
                with self.subTest(body=body[:20], expect_frontmatter=expect):
                    cues, dropped, front, _ = to_srt.parse_transcript(
                        body, expect_frontmatter=expect)
                    non_blank = [l for l in body.splitlines() if l.strip()]
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
                if proc.returncode != 3:
                    continue          # this case produced a pair; not a refusal
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
        ("main", "invisible char(s) removed — see stderr", "stripped_chars"):
            "numbers_in: stripped_ledger",
        ("main", "of the content lines DID carry a timestamp and", "len(stamped)"):
            "numbers_in: zero_stamped",
        ("main", "wrote cues to stdout", "len(segments)"):
            "numbers_in: cues, streaming",
        ("main", "wrote cues →", "len(segments)"):
            "numbers_in: cues, -o",
        ("main", "— of them are not `key: value` lines, so this ", "len(odd)"):
            "numbers_in: header_odd",
        ("main", "— of them would have parsed as cues (first: )", "len(ate)"):
            "numbers_in: header_ate",
        ("main", "⚠ invisible character(s) were removed from the", "stripped_chars"):
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
        ("shape_of", "d{}", "j - i"): "numbers_in: shape_digits",
        ("shape_of", "s{}", "j - i"): "numbers_in: shape_spaces",
    }

    # Carries no value an assertion could be wrong about → why that is fine.
    # Being in this bucket is a claim, not a default: it says the interpolation
    # is text whose exact content no caller computes with.
    UNCHECKED = {
        ("<module>", "\\A(?:)\\Z", "_STAMP"):                     "regex assembly, not output",
        ("<module>", "^\\[\\s*(?P<ts>)\\s*(?:-\\s*(?P<end>[^\\]]*?)\\s*)?\\", "_STAMP"): "regex assembly",
        ("_refuse", "⚠ no source comparison to show:", "why"):     "the cause sentence; each caller's wording is tested",
        ("build_cues", ":", "speaker"):                            "the speaker label itself",
        ("build_cues", ":", "text"):                               "the cue words themselves",
        ("build_cues", "cue at to —", "what"):                     "which correction, tested by name",
        ("build_cues", "cue at to —", "why"):                      "the reason clause, tested by name",
        ("differing_sample", ".md", "rec_id"):                     "a path fragment",
        ("differing_sample", "the line(s), so the two sides no longer line u", "which"): "the composed side-name clause",
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
        ("main", "polished:", "sanitise(sample['polished'])[0]"):   "the sampled line itself",
        ("main", "verbatim:", "sanitise(sample['verbatim'])[0]"):   "the sampled line itself",
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

        consumed = set()
        for node in module.body:
            if is_own_registry(node) or is_pattern_table(node):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    consumed.add(child.value)

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
