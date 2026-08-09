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
    production disagreed — which is the shape of the bug this file exists for.
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
]

# Shapes no producer emits. Listed so that "the contract is two forms" is a
# fact the tests can fail on, not a claim in a comment.
OUT_OF_CONTRACT = [
    ("no brackets",        "01:01 Speaker 1: a line"),
    ("parenthesised",      "(01:01) Speaker 1: a line"),
    ("trailing timestamp", "Speaker 1: a line [01:01]"),
    ("prose",              "Speaker 1 said something at one minute in"),
]


class TestTheTwoAcceptedForms(unittest.TestCase):
    def test_each_in_contract_form_parses_with_the_right_times(self):
        for label, line, start, end in IN_CONTRACT:
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
        ends = {label: to_srt.parse_segments(line + "\n")[0]["end"]
                for label, line, _, _ in IN_CONTRACT}
        self.assertEqual([e for k, e in ends.items() if "range" in k], [115.0, 115.0])
        self.assertEqual([e for k, e in ends.items() if "point" in k], [None, None])


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

    def test_the_out_of_scope_kinds_are_named_as_such(self):
        """Silence would read as coverage."""
        head = (REPO / "scripts" / "cache.py").read_text(encoding="utf-8").split('"""')[1]
        self.assertIn("outline", head.lower())
        self.assertIn("summary", head.lower())
