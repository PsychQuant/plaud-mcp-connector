#!/usr/bin/env python3
"""Tests for the Makefile — specifically that deploying cannot skip the gate.

Written before the Makefile exists.

Every assertion here runs `make -n` (dry run) or a target that touches nothing
outward-facing. A test that actually deployed would publish a page every time
the suite ran, which is the opposite of what a test is for.
"""
import os
import pathlib
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
MAKEFILE = REPO / "Makefile"


def make(*args: str) -> subprocess.CompletedProcess:
    """Run make in an environment this test controls.

    MAKEFLAGS propagates command-line variables to sub-makes, so running the
    suite through `make site-prod CONFIRM=1` handed CONFIRM=1 to the very
    invocations that exist to prove an unconfirmed run is refused — and they
    passed by inheriting the confirmation they were meant to lack. A test whose
    premise is "no CONFIRM was given" has to guarantee that, not assume it.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "CONFIRM")}
    return subprocess.run(
        ["make", "-C", str(REPO), *args], capture_output=True, text=True, timeout=120, env=env
    )


@unittest.skipIf(shutil.which("make") is None, "make not installed")
class MakefileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MAKEFILE.is_file():
            self.fail("Makefile does not exist")

    def dry(self, target: str, *extra: str) -> str:
        p = make("-n", target, *extra)
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        return p.stdout

    def order(self, text: str, first: str, second: str) -> None:
        i, j = text.find(first), text.find(second)
        self.assertNotEqual(-1, i, f"{first!r} never runs:\n{text}")
        self.assertNotEqual(-1, j, f"{second!r} never runs:\n{text}")
        self.assertLess(i, j, f"{first!r} must run before {second!r}:\n{text}")


class TestTargetsExist(MakefileTestCase):
    def test_help_is_the_default_goal(self):
        """Bare `make` must not deploy anything."""
        p = make()
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertNotIn("vercel deploy", p.stdout)

    def test_help_lists_the_deploy_targets(self):
        out = make("help").stdout
        for target in ("site-check", "site-preview", "site-prod"):
            self.assertIn(target, out)

    def test_test_target_runs_the_suite(self):
        self.assertIn("unittest", self.dry("test"))

    def test_site_check_runs_the_checker(self):
        self.assertIn("site_check.py", self.dry("site-check"))


class TestDeployIsGated(MakefileTestCase):
    """The whole point of the Makefile: you cannot publish past a failing gate."""

    # Match "vercel deploy", not "vercel": a target that merely probes for the
    # CLI (`command -v vercel`) also contains the bare word, and matching that
    # would test string placement rather than whether the deploy is gated.
    def test_preview_runs_the_checker_before_vercel(self):
        self.order(self.dry("site-preview"), "site_check.py", "vercel deploy")

    def test_preview_runs_the_test_suite_before_vercel(self):
        self.order(self.dry("site-preview"), "unittest", "vercel deploy")

    def test_prod_runs_the_checker_before_vercel(self):
        self.order(self.dry("site-prod", "CONFIRM=1"), "site_check.py", "vercel deploy")

    def test_prod_names_the_production_target_explicitly(self):
        """`--prod` is only shorthand for this. Both targets name the environment
        outright so neither depends on what an unspecified target resolves to."""
        self.assertIn("--target=production", self.dry("site-prod", "CONFIRM=1"))

    def test_preview_names_the_preview_target_explicitly(self):
        """Absence of `--prod` does not mean preview, and this cost a real one.

        The first version of this target ran a bare `vercel deploy` and this test
        asserted only that `--prod` was absent. It passed. The deploy went to
        production anyway — Vercel resolved the unspecified target to production
        for a project with no connected Git repository — and aliased the public
        hostname. The test verified the absence of a string; the property that
        mattered was which environment the deploy lands in, which no absent flag
        can express. Name the target.
        """
        out = self.dry("site-preview")
        self.assertIn("--target=preview", out)
        self.assertNotIn("--prod", out)

    def test_prod_and_preview_do_not_share_a_target(self):
        self.assertNotIn("--target=preview", self.dry("site-prod", "CONFIRM=1"))

    def test_domain_is_passed_through_to_the_checker(self):
        """Rule 3 is a property of the deploy target, not of any file, so it can
        only be checked if the domain reaches the checker."""
        out = self.dry("site-check", "DOMAIN=example.com")
        self.assertIn("example.com", out)


class TestProductionNeedsConfirmation(MakefileTestCase):
    """Publishing is outward-facing and hard to take back."""

    def test_bare_prod_refuses(self):
        # Non-zero, not a specific code: a recipe exiting 1 makes GNU make exit
        # 2. Asserting 1 would be testing make's reporting convention, not the
        # property that matters — that it refuses.
        p = make("_confirm-prod")
        self.assertNotEqual(0, p.returncode, p.stdout)

    def test_refusal_says_how_to_proceed(self):
        p = make("_confirm-prod")
        self.assertIn("CONFIRM=1", p.stdout + p.stderr)

    def test_refusal_survives_an_ambient_confirmation(self):
        """Guards the harness fix: inject the exact leak that broke this before —
        a MAKEFLAGS carrying CONFIRM=1, as `make site-prod CONFIRM=1` produces —
        and require the refusal to still hold. Asserting on os.environ instead
        would only restate whichever environment the suite happens to run in."""
        original = os.environ.get("MAKEFLAGS")
        os.environ["MAKEFLAGS"] = " -- CONFIRM=1"
        try:
            p = make("_confirm-prod")
            self.assertNotEqual(0, p.returncode, "ambient CONFIRM leaked into the subprocess")
        finally:
            if original is None:
                del os.environ["MAKEFLAGS"]
            else:
                os.environ["MAKEFLAGS"] = original

    def test_confirmed_prod_proceeds(self):
        p = make("_confirm-prod", "CONFIRM=1")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)

    def test_prod_actually_depends_on_the_guard(self):
        """Without this, deleting _confirm-prod from site-prod's prerequisites
        leaves every other test in this file green — the guard would still work
        when invoked directly and never run when it mattered.

        Checked under -n so nothing executes: asserting it by running site-prod
        for real would, on a broken guard, proceed to deploy.
        """
        self.assertIn("awkward to take back", self.dry("site-prod", "CONFIRM=1"))

    def test_preview_needs_no_confirmation(self):
        """A preview URL is not the public site; gating it would only train the
        user to type CONFIRM=1 without reading it."""
        self.assertNotIn("_confirm-prod", self.dry("site-preview"))


class TestRealHostnameIsChecked(MakefileTestCase):
    """The gate used to default DOMAIN to empty, so it reported 'no custom
    domain' and never looked at the hostname the site is actually served on."""

    HOST = "plaud-mcp-connector.vercel.app"

    def test_the_serving_hostname_is_the_default(self):
        """Bound to --domain, not merely present in the command line: the
        --accept-domain flag carries the same hostname, so asserting the bare
        string passed even with DOMAIN emptied back out. Same false pass as
        `assertNotIn("--prod")` — checking that a string exists somewhere
        instead of that it occupies the position that gives it meaning."""
        self.assertIn(f'--domain "{self.HOST}"', self.dry("site-check"))

    def test_the_reviewed_acceptance_is_passed_too(self):
        self.assertIn(f'--accept-domain "{self.HOST}"', self.dry("site-check"))

    def test_default_check_is_silent_about_the_domain(self):
        """The judgement is recorded, so it must not be re-raised every run."""
        p = make("site-check")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        self.assertNotIn("⚠", p.stdout)

    def test_a_different_plaud_domain_still_warns(self):
        """The acceptance covers one reviewed name, not the word 'plaud'."""
        p = make("site-check", "DOMAIN=plaud-notes.example.com")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        self.assertIn("⚠", p.stdout)


class TestCheckerActuallyRuns(MakefileTestCase):
    def test_site_check_passes_on_the_real_site(self):
        p = make("site-check")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)

    def test_site_check_fails_on_a_blocked_domain(self):
        p = make("site-check", "DOMAIN=plaud-mcp.com")
        self.assertNotEqual(0, p.returncode, "make must propagate the checker's failure")
        self.assertIn("domain", p.stdout.lower())


if __name__ == "__main__":
    unittest.main()
