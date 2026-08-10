#!/usr/bin/env python3
"""The four verdicts, one test each — because two of them were found the hard way.

Two ad-hoc audits of this repo were run an hour apart. The first used
`startswith("## Closing Summary")` and reported eleven issues as missing their
summary. The second split out "heading present but mid-comment" and reported
ten. Both numbers were wrong: all eleven had a summary, and the remaining ten
differed only in the casing of one letter.

The pattern is the same both times — a classifier with fewer buckets than the
data has shapes, run once, believed. So the classifier now has its shapes
written down as tests, and adding a fifth means adding a test for it.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from audit_closing_summaries import CANONICAL, Verdict, classify  # noqa: E402

DIAGNOSIS = "## Diagnosis\n\n### Root Cause\nsomething"
IC = "## Implementation Complete\n\n### Checklist\n\n- [x] done"
SUMMARY = f"{CANONICAL}\n\n### Problem\n\nthe thing that was wrong"


class TestTheFourVerdicts(unittest.TestCase):
    def test_own_comment_is_the_conforming_shape(self):
        self.assertIs(Verdict.OWN_COMMENT, classify([DIAGNOSIS, IC, SUMMARY]))

    def test_a_differently_cased_heading_is_not_missing(self):
        """Ten of this repo's forty-three closed issues are exactly this."""
        lower = SUMMARY.replace(CANONICAL, "## Closing summary")
        self.assertIs(Verdict.CASING, classify([DIAGNOSIS, lower]))

    def test_a_summary_inside_another_comment_is_not_missing(self):
        """#41: posted below the Implementation Complete, separated by ---."""
        merged = f"{IC}\n\n---\n\n{SUMMARY}"
        self.assertIs(Verdict.MID_COMMENT, classify([DIAGNOSIS, merged]))

    def test_missing_is_reserved_for_actually_missing(self):
        self.assertIs(Verdict.MISSING, classify([DIAGNOSIS, IC]))

    def test_no_comments_at_all_is_missing(self):
        self.assertIs(Verdict.MISSING, classify([]))


class TestPrecedence(unittest.TestCase):
    """Which comment wins when an issue holds more than one summary shape."""

    def test_a_conforming_comment_outranks_a_miscased_one(self):
        """After normalising one summary, an older one must not re-flag it."""
        lower = SUMMARY.replace(CANONICAL, "## Closing summary")
        self.assertIs(Verdict.OWN_COMMENT, classify([lower, SUMMARY]))
        self.assertIs(Verdict.OWN_COMMENT, classify([SUMMARY, lower]),
                      "verdict depended on comment order")

    def test_own_comment_outranks_mid_comment(self):
        merged = f"{IC}\n\n---\n\n{SUMMARY}"
        self.assertIs(Verdict.OWN_COMMENT, classify([merged, SUMMARY]))

    def test_casing_outranks_mid_comment(self):
        """A miscased heading is one edit from conforming; mid-comment is more."""
        lower = SUMMARY.replace(CANONICAL, "## Closing summary")
        merged = f"{IC}\n\n---\n\n{SUMMARY}"
        self.assertIs(Verdict.CASING, classify([merged, lower]))


class TestHeadingRecognition(unittest.TestCase):
    def test_spacing_and_case_variants_are_recognised(self):
        for head in ("## closing summary", "##  Closing  Summary",
                     "## CLOSING SUMMARY", "  ## Closing Summary"):
            with self.subTest(head=head):
                self.assertIn(classify([f"{head}\n\nbody"]),
                              (Verdict.OWN_COMMENT, Verdict.CASING),
                              f"{head!r} was not recognised as a summary heading")

    def test_a_versioned_heading_in_canonical_case_already_conforms(self):
        """`## Closing Summary_v2` satisfies the upstream prefix marker as-is.

        Worth pinning because it is counter-intuitive: the audit contract is
        `startswith("## Closing Summary")`, so anything appended after the
        phrase still conforms. This class needs no repair.
        """
        self.assertIs(Verdict.OWN_COMMENT,
                      classify(["## Closing Summary_v2\n\nrevised"]))

    def test_a_versioned_heading_in_other_case_is_casing_not_missing(self):
        """The case a trailing `\\b` silently broke.

        Lowercase means the canonical prefix does not match, so the verdict
        rests on `ANY_HEADING`. Underscore is a word character, so
        `summary\\b` refused it and the issue read as MISSING while a summary
        sat right there. Found by acid — no test covered the boundary, and
        inspecting what it did showed it was wrong, not merely unproven.
        """
        self.assertIs(Verdict.CASING,
                      classify(["## closing summary_v2\n\nrevised"]))

    def test_a_deeper_heading_is_not_a_closing_summary(self):
        """`### Closing Summary` nested under something else is a subsection."""
        self.assertIs(Verdict.MISSING,
                      classify([f"{IC}\n\n### Closing Summary of the sub-part\n\nx"]))

    def test_the_words_in_prose_are_not_a_heading(self):
        self.assertIs(
            Verdict.MISSING,
            classify([f"{IC}\n\nI will write the closing summary tomorrow."]))
