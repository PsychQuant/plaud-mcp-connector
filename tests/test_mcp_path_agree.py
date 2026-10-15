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


def _section(skill: str, start: str, end: str) -> str:
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
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

    def test_the_plaud_download_json_example_shows_a_range_not_a_start_only_stamp(self):
        sec = download_mcp_section()
        example = re.search(r'"body":\s*"(<[^>]*>)"', sec)
        self.assertIsNotNone(example, "the JSON example with a body placeholder is gone")
        self.assertRegex(
            example.group(1), r"\[[^\]]* - [^\]]*\]",
            "the body example must show a range line; a start-only stamp there is what #67 was")


if __name__ == "__main__":
    unittest.main()
