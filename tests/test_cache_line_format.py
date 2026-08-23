#!/usr/bin/env python3
"""The cache line format, as a contract rather than a habit (#40).

`cache.py put` writes whatever reaches its stdin — no parse, no validation, no
normalisation. That is deliberate (any source can land content), and it is why
the shape was never written down anywhere and drifted without anyone noticing:

    plaud-index, MCP path   →  [00:12:03] Speaker 1: …      point
    plaud-index, CLI path   →  [01:01 - 01:55] Speaker 1: … range

`to_srt` accepted only the first. So every recording indexed through the CLI —
the path the README calls "strongly recommended for large libraries" — silently
produced no subtitles, for as long as both paths existed. The whole suite was
green throughout, because every fixture in `test_to_srt.py` used the shape the
*other* producer emits.

The prose contract lives in `scripts/cache.py`'s module docstring, where a
producer calling `put` will meet it. This file is the part with teeth: prose
cannot fail a build.

## Scope, stated rather than assumed

`--kind transcript` and `--kind polish` only. Both forms here were **measured**
on 2026-08-09 against a real recording; neither was inferred from documentation.

`outline` and `summary` are **out of scope** and deliberately untested. Their
line shapes have not been measured, and pinning an unmeasured shape would
assert something nobody checked — the mistake #36 was about. When someone
measures them, they belong here; not before.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _load_to_srt():
    """Import the consumer that actually cares about the format.

    The contract is only real if the thing reading the cache honours it, so
    the assertions below run against `to_srt`'s own parser rather than a
    reimplementation. A copy of the regex here could agree with itself while
    production disagreed.

    **What that leaves uncovered, stated rather than implied (#42):** using
    the production parser as the judge makes this file blind in the other
    direction. If a producer starts writing a third shape and the parser is
    widened to accept it, both sides are self-consistent and every assertion
    here still passes. That is #40's own shape — `plaud-index`'s CLI path
    wrote ranges, `to_srt` read points, and the suite was green for months.

    So this file checks that the parser honours the two measured forms. It
    does **not** check that producers still emit only those.
    `tests/test_cache_line_format_live.py` is the half that does, by running
    the real producer; it is opt-in, because the only non-circular check needs
    the real thing.
    """
    spec = importlib.util.spec_from_file_location(
        "_to_srt_contract", REPO / "scripts" / "to_srt.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_to_srt_contract", mod)
    spec.loader.exec_module(mod)
    return mod


to_srt = _load_to_srt()

# Both shapes a shipped producer emits. Synthetic text — real recordings are
# other people's speech and do not belong in a repository.
IN_CONTRACT = [
    ("point, mm:ss",       "[01:01] Speaker 1: a line of words",        61.0, None),
    ("point, hh:mm:ss",    "[00:01:01] Speaker 1: a line of words",     61.0, None),
    ("range, mm:ss",       "[01:01 - 01:55] Speaker 1: a line",         61.0, 115.0),
    ("range, hh:mm:ss",    "[00:01:01 - 00:01:55] Speaker 1: a line",   61.0, 115.0),
    # The minute field is TOTAL minutes, so it leaves two digits at 100 (#50).
    # This was in the cache and shipping for as long as the CLI has written
    # long recordings — the contract simply never said so, which is why the
    # table above could look complete while a 7.4-hour transcript lost four
    # fifths of its segments (281 in, 57 out).
    ("point, mmm:ss",      "[100:05] Speaker 1: a line",              6005.0, None),
    ("range, mmm:ss",      "[100:05 - 100:31] Speaker 1: a line",     6005.0, 6031.0),
]

# Shapes the parser accepts that NO producer has been measured emitting. They
# are pinned so the boundary cannot move unnoticed — not offered as evidence
# that anything writes them.
#
# The distinction is the same one `scripts/cache.py` makes three paragraphs
# into its contract: "A third would go here only after something real emits it,
# never because it seems reasonable." A four-digit row went into IN_CONTRACT
# during #50 under a heading that reads "Both shapes a shipped producer emits",
# and #50's own measurements contain no four-digit minute field — the longest
# is `446:12`. That promotes a conservative implementation bound into a
# contract backed by a measurement that was never taken, and the next person
# wanting to tighten the bound would have to overturn a test that presents
# itself as a record of reality. An over-extended contract is a wrong contract
# in the same way an incomplete one is.
BOUND_PINS = [
    ("range, four-digit",  "[1440:00 - 1440:30] Speaker 1: a day in", 86400.0, 86430.0),
    ("max minutes",        "[9999:59] Speaker 1: the upper bound",   599999.0, None),
    ("fractional seconds", "[01:01.500] Speaker 1: a line",             61.5, None),
    ("one-digit hours",    "[1:02:03] Speaker 1: a line",             3723.0, None),
    ("no speaker",         "[01:01] a line with no speaker label",      61.0, None),
]

# Shapes no producer emits. Listed so that "the contract is two forms" is a
# fact the tests can fail on, not a claim in a comment.
OUT_OF_CONTRACT = [
    ("no brackets",        "01:01 Speaker 1: a line"),
    ("parenthesised",      "(01:01) Speaker 1: a line"),
    ("trailing timestamp", "Speaker 1: a line [01:01]"),
    ("prose",              "Speaker 1 said something at one minute in"),
    # Five digits is not a long meeting, it is a malformed line. The widening
    # in #50 is bounded at four on purpose — an unbounded minute field would
    # trade a silent-drop bug for a silent-accept one at the same site.
    ("five-digit minutes", "[99999:00] Speaker 1: not a recording"),
    # Verify round 1: the four-digit bound was enforced on a range's START
    # only, and the same widening had leaked into the HOURS field of the
    # three-part form. Both are contract statements, so both are pinned here.
    ("four-digit hours",   "[1234:05:06] Speaker 1: 51 days is not a recording"),
    ("three-digit seconds", "[00:412] Speaker 1: seconds are exactly two digits"),
]


class TestTheTwoAcceptedForms(unittest.TestCase):
    def test_the_measured_table_holds_both_forms_of_both_shapes(self):
        """A count, so a row cannot quietly vanish from the table.

        The rewrite in #50 made the assertions table-driven and, in doing so,
        dropped the implicit "two forms x two time-shapes" count the hardcoded
        version had. A table-driven test that never checks the table's own size
        passes just as green with a row deleted.
        """
        labels = [label for label, *_ in IN_CONTRACT]
        self.assertEqual(6, len(labels), f"IN_CONTRACT changed size: {labels}")
        self.assertEqual(3, sum("range" in l for l in labels), f"ranges: {labels}")
        self.assertEqual(3, sum("point" in l for l in labels), f"points: {labels}")

    def test_each_in_contract_form_parses_with_the_right_times(self):
        for label, line, start, end in IN_CONTRACT + BOUND_PINS:
            with self.subTest(form=label):
                segs = to_srt.parse_segments(line + "\n")
                self.assertEqual(len(segs), 1, f"{label} did not parse")
                self.assertEqual(segs[0]["start"], start)
                self.assertEqual(segs[0]["end"], end)

    def test_only_the_ranged_forms_carry_an_end(self):
        """The difference between the two forms is the whole point.

        A range gives `to_srt` a real end; a point leaves it inferring one
        from the next segment and guessing outright for the last.
        """
        # Derived from IN_CONTRACT rather than hardcoded, so adding a row to the
        # table does not require editing an assertion that has nothing to do
        # with it. The hardcoded `[115.0, 115.0]` broke the moment #50 added
        # the total-minute rows — a table-driven test whose expectations were
        # not table-driven.
        for label, line, _, expected_end in IN_CONTRACT:
            with self.subTest(form=label):
                end = to_srt.parse_segments(line + "\n")[0]["end"]
                if "range" in label:
                    self.assertIsNotNone(end, f"{label} is a range but carries no end")
                    self.assertEqual(expected_end, end)
                else:
                    self.assertIsNone(end, f"{label} is a point but carries an end")


class TestTheListIsClosed(unittest.TestCase):
    def test_out_of_contract_shapes_do_not_parse(self):
        """Not a wish — the failure the contract is meant to make loud.

        A third shape reaching the cache should break here rather than
        surface months later as "subtitles are empty for some recordings".
        """
        for label, line in OUT_OF_CONTRACT:
            with self.subTest(form=label):
                self.assertEqual(
                    [], to_srt.parse_segments(line + "\n"),
                    f"{label!r} parsed, so the accepted set is wider than the "
                    f"contract in scripts/cache.py claims. Either a producer "
                    f"now emits it — then measure it and add it to both — or "
                    f"the parser drifted (#40).",
                )


class TestTheContractIsWrittenDownWhereProducersLook(unittest.TestCase):
    def test_cache_py_documents_the_format(self):
        """Prose has no teeth, but its absence has consequences.

        `put` is the call a producer makes; if the shape is not stated there,
        the next producer guesses — which is how the two forms diverged.
        """
        doc = (REPO / "scripts" / "cache.py").read_text(encoding="utf-8")
        head = doc.split('"""')[1] if '"""' in doc else ""
        for needed in ("line format", "transcript", "polish"):
            with self.subTest(mentions=needed):
                self.assertIn(needed, head.lower(),
                              "cache.py's module docstring no longer states the "
                              "line format a producer must write (#40)")

        # #50: the docstring showed `MM:SS` and `HH:MM:SS` and nothing else, so
        # a reader concluded — correctly, from what was written — that two
        # digits was the whole story. The producer had been writing three for
        # as long as recordings ran past 99 minutes. An incomplete contract is
        # not a smaller contract; it is a wrong one, and it made the parser
        # look right while it dropped four fifths of a transcript.
        self.assertIn("total minutes", head.lower(),
                      "cache.py does not say the minute field is TOTAL minutes, so "
                      "nothing tells a reader it can exceed two digits (#50)")
        # The bound is a contract statement, so the contract has to say what it
        # bounds. "five is malformed" alone read three ways too widely at once.
        self.assertIn("both ends of a range", head,
                      "cache.py states a five-digit bound without saying it applies to "
                      "a range's end too — it did not, and 10000:00 became 600000.0")
        self.assertIn("literal hours", head,
                      "cache.py does not distinguish the HH field from the MM field, so "
                      "the four-digit bound reads as covering hours (416 days)")

        self.assertIn("446:12", head,
                      "cache.py has no worked example of a minute field past 99 — the "
                      "shape is the one that silently truncated real transcripts, and "
                      "prose without the example is what let it stay invisible (#50)")

    def test_the_out_of_scope_kinds_are_named_as_such(self):
        """Silence would read as coverage."""
        head = (REPO / "scripts" / "cache.py").read_text(encoding="utf-8").split('"""')[1]
        self.assertIn("outline", head.lower())
        self.assertIn("summary", head.lower())
