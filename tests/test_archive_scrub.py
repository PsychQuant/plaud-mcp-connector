"""archive/plaud-transcriber/ is a snapshot of a plugin written under a
private-repo assumption, published here in a PUBLIC repo (#60).

The first archive commit was gated on a substring grep for e-mail and home
paths; verify found the tree it approved still carried the maintainer's
address spelled as a key-press sequence, four real recording ids, and
third-party names. Two fix-forward commits scrubbed those. Nothing in tests/
covered archive/ at the time — the repo's third-party-words doctrine
(test_to_srt.py) stopped at the shipped tree. This test extends it.

It pins SHAPES, not values: the scrubbed values must not be re-spelled here.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
ARCHIVE = REPO / "archive" / "plaud-transcriber"

# Placeholders the scrub wrote in: all-zero ids ending in a digit.
PLACEHOLDER_ID = re.compile(r"\b0{31}[0-9]\b")

SHAPES = {
    "literal e-mail or home path": re.compile(r"@gmail|@icloud|/Users/", re.I),
    # An address spelled one key at a time: `a+b+c+@+d+…`
    "key-press-encoded e-mail": re.compile(r"(?:\+[a-z0-9]){3,}\+@\+", re.I),
    # A real Plaud recording id (32 hex) that is not one of our placeholders.
    "32-hex recording id": re.compile(r"\b[0-9a-f]{32}\b"),
    # An exact calendar date next to a person placeholder: a schedule row.
    "date pinned to a person": re.compile(
        r"20\d\d-[01]\d-[0-3]\d[^\n]{0,40}(Student[A-Z]|Collaborator[A-Z])"
        r"|(Student[A-Z]|Collaborator[A-Z])[^\n]{0,40}20\d\d-[01]\d-[0-3]\d"),
}


def _texts():
    for p in sorted(ARCHIVE.rglob("*")):
        if p.is_file():
            yield p.relative_to(REPO), p.read_text(encoding="utf-8", errors="replace")


class TestArchivedSnapshotStaysScrubbed(unittest.TestCase):
    def test_archive_exists(self):
        self.assertTrue(ARCHIVE.is_dir(), "the #60 snapshot has moved — update ARCHIVE")

    def test_no_identifying_shapes(self):
        hits = []
        for rel, text in _texts():
            for label, rx in SHAPES.items():
                for m in rx.finditer(text):
                    if label == "32-hex recording id" and PLACEHOLDER_ID.match(m.group(0)):
                        continue
                    line = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{rel}:{line}: {label}")
        self.assertEqual(hits, [], "archived snapshot carries identifying shapes:\n  "
                         + "\n  ".join(hits))


if __name__ == "__main__":
    unittest.main()
