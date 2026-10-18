#!/usr/bin/env python3
"""The two skills that describe the MCP write path must describe the same line (#67).

`plaud-download` and `plaud-to-srt` both tell a model how to turn `get_transcript`
segments into the lines `cache.py put` stores. #65 corrected one of them: the segment's
`end_time` is kept, so a line is a range, `[HH:MM:SS.fff - HH:MM:SS.fff] Speaker N: …`.
The other kept showing the start-only form as the example to write, so the same step
produced different caches depending on which skill the model had loaded, and every
subtitle made from the start-only one ran on until the next one began.

What breaks, and what this catches: someone edits the line shape in one of the two
sections and not the other, or the `plaud-download` example goes back to a start-only
stamp. It does NOT show that a model follows the text; that was measured separately
(a model given each version of the section and the same synthetic response, output
compared with a hand-derived file) and is in the issue. This is a drift guard for the
one divergence that actually happened, not a test of the prose.
"""
from __future__ import annotations

import pathlib
import re
import unittest

SKILLS = pathlib.Path(__file__).resolve().parent.parent / "skills"

# The template both sections carry. Written once here; if it changes, both skills change.
RANGE_TEMPLATE = "[HH:MM:SS.fff - HH:MM:SS.fff] Speaker N:"


# The rules the two sections must both carry, not just the template. #67 showed the
# model diverges exactly where the text is silent: the millisecond conversion, what
# counts as "no end", and a line break inside a speaker or the content.
SHARED_RULES = {
    "the millisecond conversion": "`680` is\n`00:00:00.680`",
    "the line is one physical line": "A segment is one physical line",
    "what counts as no end": "absent, null, or not later than",
}


def _section(skill: str, start: str, end: str) -> str:
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    # A heading rename (#76 renamed three skills) must read as a failed
    # assertion that says which heading, not as a bare ValueError
    for marker in (start, end):
        assert marker in text, f"{skill}/SKILL.md no longer contains {marker!r}"
    a, b = text.index(start), text.index(end)
    assert a < b, f"{skill}: {start!r} must come before {end!r}"
    return text[a:b]


def download_mcp_section() -> str:
    return _section("plaud-download", "#### The MCP path", "### 4. Report")


def to_srt_mcp_section() -> str:
    return _section("plaud-to-srt", "**The MCP path**", "### 2. Pick the source")


class TestBothSkillsDescribeTheSameLine(unittest.TestCase):
    def test_plaud_download_names_the_range_template(self):
        self.assertIn(RANGE_TEMPLATE, download_mcp_section())

    def test_plaud_to_srt_names_the_range_template(self):
        self.assertIn(RANGE_TEMPLATE, to_srt_mcp_section())

    def test_both_sections_carry_the_same_rules_not_only_the_same_template(self):
        for skill, section in (("plaud-download", download_mcp_section()),
                               ("plaud-to-srt", to_srt_mcp_section())):
            # Line wrapping differs between the two files, so compare on words
            flat = " ".join(section.split())
            for what, needle in SHARED_RULES.items():
                with self.subTest(skill=skill, rule=what):
                    self.assertIn(" ".join(needle.split()), flat,
                                  f"{skill} no longer states {what}; the other skill does")

    def test_a_segment_with_no_end_is_written_start_only_in_both(self):
        for section in (download_mcp_section(), to_srt_mcp_section()):
            flat = " ".join(section.split())
            self.assertIn("is written start-only, `[HH:MM:SS.fff] Speaker N: <content>`", flat)

    def test_the_plaud_download_json_example_shows_a_range_not_a_start_only_stamp(self):
        sec = download_mcp_section()
        example = re.search(r'"body":\s*"(<[^>]*>)"', sec)
        self.assertIsNotNone(example, "the JSON example with a body placeholder is gone")
        self.assertRegex(
            example.group(1), r"\[[^\]]* - [^\]]*\]",
            "the body example must show a range line; a start-only stamp there is what #67 was")


class TestTheParserReadsExactlyWhatTheSkillsInstruct(unittest.TestCase):
    """The text tells a model to write millisecond range lines; the parser must read them.

    Everything above compares prose with prose. This is the other half: the lines the
    prose asks for, including the hour and ten-hour stamps and a segment with no end,
    go through the real parser and come out with the starts and ends that were meant.
    """

    def test_millisecond_ranges_hours_and_a_start_only_segment(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "to_srt_for_67", SKILLS.parent / "scripts" / "to_srt.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        body = (
            "[00:00:00.680 - 00:00:02.720] Speaker 1: first\n"
            "[01:00:00.250 - 01:00:04.000] Speaker 2: second\n"
            "[10:00:00.000] Speaker 1: third, no end\n"
        )
        segs, dropped, _front, lost = mod.parse_transcript(body)
        self.assertEqual([], dropped)
        self.assertEqual([], lost)
        self.assertEqual([(0.68, 2.72), (3600.25, 3604.0), (36000.0, None)],
                         [(s["start"], s["end"]) for s in segs])


if __name__ == "__main__":
    unittest.main()
