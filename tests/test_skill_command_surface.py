#!/usr/bin/env python3
"""Every `scripts/*.py` command a SKILL.md quotes must actually exist (issue #31).

The skills drive the scripts by quoting command lines verbatim:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cache.py" status --ids-only
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/config.py" get subtitle_source
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/to_srt.py" <id> --preview-sources

Nothing tied the two ends together. Rename a flag, drop a subcommand, and every
test stays green while the skill breaks at the moment a user runs it — which is
the worst place to find out, because by then it is not a test failure, it is
someone's indexing run.

**What this checks: existence, not execution.** The quoted lines carry
placeholders (`<id>`, `"<name>"`) and heredocs, and several need network and a
login. Running them is impossible here and always will be. So this parses out
the subcommand and the long flags and asks argparse whether they exist. Keeping
that boundary is the point — the obvious "improvement" of actually executing
them is what would make this untestable in CI.

**What it does NOT cover, measured rather than assumed.** Only flags written
inside a quoted invocation. A flag mentioned in prose — `plaud-index/SKILL.md`
line 52 says "Keep the id set (`--ids-only`)" without ever quoting the command
— is invisible here, and renaming it leaves this file green. That was checked,
not guessed: an acid run renaming `--ids-only` did not go red.

Extending to backticked flags was considered and rejected. The skills backtick
plenty of flags that are not ours (`--days` and `--since` are plaud-index's own
arguments; `--block` and `--polished` belong to the Plaud CLI), so the check
would have to know which flags to claim, and getting that wrong turns a guard
into noise. The honest scope is quoted invocations; prose stays a human
responsibility.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py" <args>`, following `\`
# line continuations.
#
# Stopping at the first newline was the first draft, and it under-scanned
# silently: the longest invocation in plaud-index/SKILL.md puts `--cutoff`,
# `--prev-last` and `--anomaly-seen` on continuation lines, so the scan saw
# `should-stop-paging \` and nothing else. Renaming `--anomaly-seen` left this
# whole file green — a coverage hole in the thing built to close coverage holes.
INVOCATION = re.compile(
    r'python3\s+"?\$\{CLAUDE_PLUGIN_ROOT\}/scripts/(?P<script>[a-z_]+\.py)"?\s+'
    r'(?P<rest>(?:[^\n]*\\\n)*[^\n]*)'
)
# A long flag, stopping before `=` so `--cwd=x` still names `--cwd`.
LONG_FLAG = re.compile(r'(?<![\w-])(--[a-z][a-z0-9-]*)')
# A bare word is a subcommand only in first position and only if it is a plain
# word — `<id>`, `"$tmp"` and `$VAR` are arguments, not subcommands.
SUBCOMMAND = re.compile(r'^([a-z][a-z0-9-]*)(?:\s|$)')


def _quoted_invocations() -> list[tuple[pathlib.Path, str, str]]:
    """Every (skill file, script name, argument string) quoted across skills/."""
    found = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        for m in INVOCATION.finditer(text):
            # Flatten continuations, then drop anything downstream of a pipe —
            # those flags belong to the next command, not this one.
            rest = m.group("rest").replace("\\\n", " ").split("|")[0]
            found.append((skill_md, m.group("script"), " ".join(rest.split())))
    return found


def _help_text(script: str, *sub: str) -> str:
    out = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *sub, "--help"],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout + out.stderr


# argparse prints the subcommand choices as `{a,b,c}` in the usage line. Read
# THAT, not the whole help text.
#
# Searching the whole text was the first draft, and it was wrong in the exact
# way this file exists to catch: `cache.py`'s module docstring lists its own
# subcommands, and argparse prints the docstring as the description — so a
# renamed subcommand still "existed" because its old name was sitting in the
# prose two paragraphs down. Renaming `mark-full-sweep` left the test green.
# Checking a proxy for the property instead of the property.
CHOICES = re.compile(r"\{([a-z0-9,_-]+)\}")

# Same hazard for flags: a flag named in a description line is not a flag the
# parser accepts. argparse puts real options in their own indented column.
def _option_line(flag: str) -> re.Pattern:
    return re.compile(rf"(?m)^\s{{2,}}(?:-\w,\s+)?{re.escape(flag)}\b")


def _subcommands(script: str) -> set[str]:
    m = CHOICES.search(_help_text(script))
    return set(m.group(1).split(",")) if m else set()


class TestQuotedCommandsExist(unittest.TestCase):
    def test_there_is_something_to_check(self):
        """A regex that silently matches nothing would make every test below pass."""
        invocations = _quoted_invocations()
        self.assertGreaterEqual(
            len(invocations), 5,
            "the invocation regex found almost nothing — it has probably drifted "
            "from how the skills actually write these commands, and an empty scan "
            "reports success",
        )

    def test_every_quoted_script_exists(self):
        for skill_md, script, _rest in _quoted_invocations():
            with self.subTest(skill=skill_md.parent.name, script=script):
                self.assertTrue(
                    (SCRIPTS_DIR / script).is_file(),
                    f"{skill_md.relative_to(REPO_ROOT)} calls scripts/{script}, which is gone",
                )

    def test_every_quoted_subcommand_exists(self):
        for skill_md, script, rest in _quoted_invocations():
            m = SUBCOMMAND.match(rest)
            if not m:
                continue
            sub = m.group(1)
            with self.subTest(skill=skill_md.parent.name, script=script, sub=sub):
                offered = _subcommands(script)
                if not offered:
                    continue  # no subparsers on this script
                self.assertIn(
                    sub, offered,
                    f"{skill_md.relative_to(REPO_ROOT)} calls `{script} {sub}`, but "
                    f"argparse offers only {sorted(offered)}",
                )

    def test_every_quoted_long_flag_exists(self):
        for skill_md, script, rest in _quoted_invocations():
            m = SUBCOMMAND.match(rest)
            sub = [m.group(1)] if m else []
            flags = set(LONG_FLAG.findall(rest))
            if not flags:
                continue
            help_text = _help_text(script, *sub)
            if "--help" not in help_text:
                continue  # that subcommand did not parse; the subcommand test covers it
            for flag in sorted(flags):
                with self.subTest(skill=skill_md.parent.name, script=script,
                                  sub=" ".join(sub), flag=flag):
                    self.assertRegex(
                        help_text, _option_line(flag),
                        f"{skill_md.relative_to(REPO_ROOT)} passes `{flag}` to "
                        f"`{script} {' '.join(sub)}`, which does not accept it "
                        f"(a mention in the description does not count — that is "
                        f"how the first draft of this test missed a renamed subcommand)",
                    )
