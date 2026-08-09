#!/usr/bin/env python3
"""What under `.claude/` may reach a commit, and what may not (#41).

`.claude/` holds three unrelated kinds of thing, and they get opposite
treatment:

    .claude/skills/, settings.json  →  tracked. Repo content.
    .claude/.mail/                  →  ignored. Local mail state.
    .claude/.idd/                   →  ignored, EXCEPT local.json.

The `.idd/` line is the one that went wrong. It was written per-file
(`.claude/.idd/tree-lock`), so `attachments/` — added later by the IDD
plugin — was never covered. Nothing leaked, because no issue in this repo
has carried an attachment yet. But `process-attachments.sh` downloads
whatever an issue has, that is often somebody else's document, and IDD's
own rule is to keep every attachment. So "an issue with an attachment" is
the expected case, not the exotic one, and `git add -A` would have taken
it into a public repository.

These tests ask git rather than reading the file, because the question is
"is this path ignored", and only git answers that — patterns interact,
order matters, and a directory that is excluded cannot have its contents
re-included (which is why the rule needs `dir/*`, not `dir/`).
"""
from __future__ import annotations

import pathlib
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


def is_ignored(rel: str) -> bool:
    """Ask git, not the .gitignore text.

    `git check-ignore` applies the real precedence rules — repo .gitignore,
    .git/info/exclude, and the user's global excludesfile all at once. A
    regex over .gitignore would miss every one of those interactions.
    """
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel],
                       cwd=REPO, capture_output=True)
    return r.returncode == 0


class TestIddScratchIsNotCommittable(unittest.TestCase):
    MUST_BE_IGNORED = [
        (".claude/.idd/attachments/issue-1/some-third-party.docx",
         "issue attachments are other people's documents (#41)"),
        (".claude/.idd/attachments/issue-1/_manifest.json",
         "the manifest names those files and is re-derivable"),
        (".claude/.idd/tree-lock",
         "per-session lock state"),
        (".claude/.idd/anything-the-plugin-adds-later",
         "the point of the carve-out: cover what does not exist yet"),
    ]

    def test_idd_scratch_paths_are_ignored(self):
        for rel, why in self.MUST_BE_IGNORED:
            with self.subTest(path=rel):
                self.assertTrue(is_ignored(rel), f"{rel} is committable — {why}")


class TestWhatMustStayCommittable(unittest.TestCase):
    """A carve-out that swallows the wrong thing fails silently.

    `.claude/.idd/local.json` does not exist in this repo today, so a rule
    that wrongly excluded it would show no symptom here at all — the sibling
    bestASR repo has one, and this repo may grow one. That is exactly the
    kind of mistake worth a test: no feedback until much later, somewhere
    else.
    """

    MUST_NOT_BE_IGNORED = [
        (".claude/.idd/local.json",
         "the repo's IDD config — shared, belongs in version control"),
        (".claude/settings.json",
         "repo settings"),
        (".claude/skills/spectra-apply/SKILL.md",
         "Spectra skills are repo content, not scratch"),
    ]

    def test_repo_content_under_claude_is_not_ignored(self):
        for rel, why in self.MUST_NOT_BE_IGNORED:
            with self.subTest(path=rel):
                self.assertFalse(is_ignored(rel), f"{rel} is ignored — {why}")

    def test_the_tracked_skills_really_are_tracked(self):
        """Guards against the check above passing for the wrong reason.

        `is_ignored` returning False proves only that nothing excludes the
        path — an empty repo would pass just as well. This confirms the
        files it names are genuinely in the index.
        """
        out = subprocess.run(["git", "ls-files", ".claude/skills"],
                             cwd=REPO, capture_output=True, text=True).stdout
        self.assertGreater(len(out.strip().splitlines()), 5,
                           "no Spectra skills tracked — has .claude/skills moved?")
