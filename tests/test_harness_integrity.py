#!/usr/bin/env python3
"""Tests about the test harness itself (issue #32).

A broken way of running the tests must not be mistakable for a failing test.

`python3 -m unittest tests.test_cache.SomeClass.some_test` used to die with
`ModuleNotFoundError` — because `tests/` was not a package — and unittest
reported that the way it reports everything else: a final line reading
`FAILED`. Anything deciding "did this go red?" by looking for that word got
`FAILED` on every run, whether or not the thing under test was broken.

That is not a hypothetical. An acid-test harness — inject a bug, check the
test goes red — reported eleven guards as protected when it had verified
none of them. Rerun with a working invocation, it immediately found a real
bug (`_parse_api_time` letting a `TypeError` escape on a non-string
`created_at`, which would kill an indexing run over one bad manifest entry).

The lesson generalises past this one file: **a verification tool that fails
in a way indistinguishable from the failure it is looking for cannot verify
anything.** So the property is pinned here rather than left to whoever next
notices.

Recursion note: every test here that shells out MUST target
`TestPackageMarker.test_tests_is_a_package`, which shells out to nothing.
Pointing a subprocess test at this module as a whole makes it re-run itself,
which is how the first draft of this file hung — an infinite fork bomb that
looked exactly like a slow test suite.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The one test in this file that spawns nothing. Every subprocess test below
# aims at this and only this — see the recursion note in the module docstring.
LEAF = "test_tests_is_a_package"


class TestPackageMarker(unittest.TestCase):
    def test_tests_is_a_package(self):
        """Stated directly, so the diff that removes it is obviously the cause.

        The subprocess tests below also catch this, but their failure output
        talks about return codes. This one names the file.
        """
        self.assertTrue(
            (REPO_ROOT / "tests" / "__init__.py").is_file(),
            "tests/__init__.py is what lets `python3 -m unittest tests.test_x.Y` "
            "resolve; without it that invocation reports FAILED for a reason "
            "that has nothing to do with the code under test",
        )


class TestInvocationForms(unittest.TestCase):
    """Both ways of naming a single test must work, and must be tellable apart."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "unittest", *args],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=60,
        )

    def test_dotted_module_path_resolves(self):
        """The form that used to ModuleNotFoundError.

        Delete `tests/__init__.py` and this goes red — which is the point.
        """
        out = self._run(f"tests.test_harness_integrity.TestPackageMarker.{LEAF}")
        self.assertNotIn("ModuleNotFoundError", out.stderr)
        self.assertIn("Ran 1 test", out.stderr)
        self.assertEqual(0, out.returncode, out.stderr)

    def test_discover_still_works_after_making_tests_a_package(self):
        """`discover` is what `make check` uses. Whatever makes the dotted form
        resolve must not cost us the form the suite actually runs on."""
        out = self._run("discover", "-s", "tests", "-p", "test_harness_integrity.py",
                        "-k", LEAF)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("Ran 1 test", out.stderr)

    def test_a_missing_test_name_is_distinguishable_from_a_failing_test(self):
        """The property that makes an acid harness trustworthy.

        A selector matching nothing must NOT look like a red test. It reports
        `Ran 0 tests`, so a harness that checks the run count cannot mistake a
        typo'd selector for a caught bug — which is exactly the mistake that
        made eleven acid results worthless.
        """
        out = self._run("discover", "-s", "tests", "-p", "test_harness_integrity.py",
                        "-k", "no_such_test_exists_anywhere")
        self.assertIn("Ran 0 tests", out.stderr)
        self.assertNotIn("Ran 1 test", out.stderr)
