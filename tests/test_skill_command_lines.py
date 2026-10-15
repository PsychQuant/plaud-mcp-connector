#!/usr/bin/env python3
"""What the skills let a model put on a command line (#69).

Text that came from Plaud (recording names, ids, transcript lines, cursors) is chosen, one
step removed, by whoever spoke in a recording, and a shell has already parsed a command line
before any script can look at it. A script therefore cannot defend against it: the only
defences are to keep the text off the command line, or to check it before the line is written.
The skills are documents that instruct a model, so what can be enforced here is that the rules
are written down and that the command lines they show carry nothing from Plaud that needs
quoting. This file pins that and says so plainly: it does not show that a model obeys the rules.

Each test names the break it catches:

* a skill that shows an id in a shell block without ever saying how to check the id first;
* a recording name back on a command line (the old `to_srt.py <id> -o "<name>.srt"`);
* `cache.py put` called with a flag that carries Plaud text (`--name`, `--last-cursor`, ...);
* a fixed, predictable `/tmp/plaud-<id>.txt` path;
* the MCP path losing one of the instructions earlier verify rounds found missing from it;
* the two false claims about the audio download that #84 records, coming back.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILLS = sorted((REPO / "skills").glob("*/SKILL.md"))

FENCE = re.compile(r"```(?P<lang>[A-Za-z]*)\n(?P<body>.*?)```", re.S)
ID_PLACEHOLDER = re.compile(r"<(?:file_)?id>")
# The sentence every skill with an id in a shell block must carry before its first such block.
# Whitespace-tolerant because these files are hard-wrapped and the sentence breaks across lines.
ID_RULE = re.compile(r"`of_`\s+followed\s+by\s+hexadecimal")


def shell_blocks(text: str):
    """(start offset, body) of every fenced shell block."""
    return [(m.start(), m.group("body")) for m in FENCE.finditer(text)
            if m.group("lang") in ("bash", "sh", "shell")]


def logical_lines(body: str):
    """Lines of a shell block with backslash continuations joined."""
    return re.sub(r"\\\n\s*", " ", body).splitlines()


class TestIdRule(unittest.TestCase):
    def test_there_are_skills_to_check(self):
        self.assertGreaterEqual(len(SKILLS), 6)

    def test_every_skill_that_puts_an_id_in_a_shell_block_states_the_rule_first(self):
        for skill in SKILLS:
            with self.subTest(skill=skill.parent.name):
                text = skill.read_text(encoding="utf-8")
                first = next((start for start, body in shell_blocks(text)
                              if ID_PLACEHOLDER.search(body)), None)
                if first is None:
                    continue
                self.assertRegex(
                    text[:first], ID_RULE,
                    f"{skill.parent.name}: a shell block uses an id and no earlier text says an id "
                    f"is `of_` followed by hexadecimal characters, to be checked before it is used")


class TestNothingFromPlaudNeedsQuoting(unittest.TestCase):
    def test_no_shell_block_carries_a_recording_name_placeholder(self):
        for skill in SKILLS:
            with self.subTest(skill=skill.parent.name):
                for _start, body in shell_blocks(skill.read_text(encoding="utf-8")):
                    self.assertNotRegex(body, r"<(?:recording )?name>",
                                        "a recording name belongs in a file the script names, not on a command line")

    def test_cache_put_lines_carry_only_id_kind_and_json(self):
        allowed = {"--id", "--kind", "--json"}
        for skill in SKILLS:
            with self.subTest(skill=skill.parent.name):
                for _start, body in shell_blocks(skill.read_text(encoding="utf-8")):
                    for line in logical_lines(body):
                        # Only the `put` command itself: a line can chain other commands
                        # (`plaud transcript ... --block outline && ... put ...`), and their
                        # flags are not ours to police here.
                        put = re.search(r"cache\.py\"?\s+put\b[^&|;]*", line)
                        if not put:
                            continue
                        flags = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", put.group(0)))
                        self.assertLessEqual(flags, allowed, f"put with a flag that carries Plaud text: {line}")

    def test_no_shell_block_uses_a_fixed_tmp_path(self):
        for skill in SKILLS:
            with self.subTest(skill=skill.parent.name):
                for _start, body in shell_blocks(skill.read_text(encoding="utf-8")):
                    self.assertNotIn("/tmp/plaud-", body, "use mktemp: a fixed path is predictable and shared")


def section(skill: str, start: str, end: str) -> str:
    text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)]


class TestTheMcpPathsKeepTheirInstructions(unittest.TestCase):
    SECTIONS = {
        "plaud-download": ("#### The MCP path", "### 4. Report"),
        "plaud-to-srt": ("**The MCP path**", "### 2. Pick the source"),
    }

    def test_both_write_through_a_file_and_put_json(self):
        for skill, (a, b) in self.SECTIONS.items():
            with self.subTest(skill=skill):
                sec = section(skill, a, b)
                self.assertIn("Write tool", sec)
                self.assertIn('--json "$HOME/.plaud-connector/incoming/<id>.json"', sec)
                self.assertRegex(sec, r"command line")

    def test_plaud_to_srt_writes_in_the_order_that_never_leaves_a_stale_polish(self):
        sec = section("plaud-to-srt", *self.SECTIONS["plaud-to-srt"])
        a, b, c = (sec.index(x) for x in
                   ("Remove any older polished copy", "Write the raw transcript", "Write the polished version"))
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_plaud_download_writes_the_cache_once_after_the_loop(self):
        self.assertIn("Write the cache **once**", section("plaud-download", *self.SECTIONS["plaud-download"]))


class TestTheAudioClaimsFromIssue84(unittest.TestCase):
    """#84: the audio is Ogg for most recordings and MP3 for some, and the signed link carries
    X-Amz-Expires=3600. The skill hard-coded .mp3 and said 24 hours, in five places."""

    FALSE_CLAIMS = (
        "expires 24 hours after it is issued",
        "the URL dies in 24h",
        "**expires in 24 hours**",
        "Presigned S3 URL, expires in 24 hours",
    )

    def test_no_shell_block_hard_codes_mp3(self):
        text = (REPO / "skills" / "plaud-download-audio" / "SKILL.md").read_text(encoding="utf-8")
        for _start, body in shell_blocks(text):
            self.assertNotIn(".mp3", body)

    def test_the_24_hour_claim_is_gone_from_every_place_it_was(self):
        for rel in ("README.md", "docs/official-surface.md", "skills/plaud-download-audio/SKILL.md"):
            text = (REPO / rel).read_text(encoding="utf-8")
            for claim in self.FALSE_CLAIMS:
                with self.subTest(file=rel, claim=claim):
                    self.assertNotIn(claim, text)


if __name__ == "__main__":
    unittest.main()
