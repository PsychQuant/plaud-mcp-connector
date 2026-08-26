"""The issue's assertion, over the real cache, with no network.

#50 asks for it 對每個 cache 檔 — for EVERY cache file — and the evidence
offered for the fix was a corpus measurement: 281/281, 77/77, 1106 segments
with 1106 ends and zero drops. Nothing in `tests/` checked those numbers. The
only per-file check over real producer output is `test_cache_line_format_live`,
which needs `PLAUD_LIVE_TESTS=1` and a login, fetches the twenty most recent
recordings and stops after three — and #50's shape only appears past 99
minutes, so the check most likely to matter was the one least likely to run.

This walks whatever is already on disk. It needs no network, no login and no
fixture, and it skips when the directory is absent, so it is free for everyone
and load-bearing for anyone who has ever indexed a recording.

It reads third-party speech and must never emit any. Failures report counts and
`shape_of` output only — the same rule the tool itself follows, for the same
reason: a CI log and a terminal scrollback are both places words end up.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("to_srt", REPO / "scripts" / "to_srt.py")
to_srt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(to_srt)
_live = importlib.util.spec_from_file_location(
    "live", REPO / "tests" / "test_cache_line_format_live.py")
_live_mod = importlib.util.module_from_spec(_live)
_live.loader.exec_module(_live_mod)
content_lines = _live_mod.content_lines

CACHE = pathlib.Path.home() / ".plaud-connector" / "cache"


class TestEveryLocalCacheFileParsesWhole(unittest.TestCase):
    def _files(self) -> list[pathlib.Path]:
        if not CACHE.is_dir():
            self.skipTest(f"no local cache at {CACHE}")
        found = sorted(CACHE.glob("*.md")) + sorted((CACHE / "polish").glob("*.md"))
        if not found:
            self.skipTest(f"local cache at {CACHE} holds no transcripts")
        return found

    def test_no_content_line_is_silently_dropped(self):
        """The issue's assertion, per file. An aggregate would hide one bad file."""
        for path in self._files():
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                cues, dropped, _, _ = to_srt.parse_transcript(
                    text, expect_frontmatter=path.parent == CACHE)
                self.assertEqual(
                    len(content_lines(text)), len(cues),
                    f"{len(dropped)} content line(s) did not become cues; the "
                    f"first is {to_srt.shape_of(dropped[0]) if dropped else '—'}")

    def test_the_corpus_is_large_enough_to_mean_something(self):
        """A green run over three short files is not the evidence #50 asked for.

        Without this, the class above passes on an empty-ish cache and reads as
        'the real corpus is fine' — which is how the live test's `break` at
        three recordings came to stand for a claim about all of them.
        """
        total = sum(len(content_lines(p.read_text(encoding="utf-8-sig", errors="replace")))
                    for p in self._files())
        self.assertGreater(
            total, 100,
            f"only {total} content lines on disk — this run does not support "
            f"any claim about the corpus; index a recording or read the number "
            f"as 'untested' rather than 'passing'")

    def test_a_long_recording_is_among_them(self):
        """#50 lives past the 100-minute mark. A corpus that stops before it
        cannot see the bug, and a green run would say otherwise."""
        longest = 0.0
        for path in self._files():
            cues, _, _, _ = to_srt.parse_transcript(
                path.read_text(encoding="utf-8-sig", errors="replace"),
                expect_frontmatter=path.parent == CACHE)
            if cues:
                longest = max(longest, cues[-1]["start"])
        if longest < 100 * 60:
            self.skipTest(
                f"the longest local recording reaches {longest / 60:.0f} minutes, "
                f"short of the 100-minute mark where #50 appears — this corpus "
                f"cannot confirm the fix")
        self.assertGreaterEqual(longest, 100 * 60)


if __name__ == "__main__":
    unittest.main()
