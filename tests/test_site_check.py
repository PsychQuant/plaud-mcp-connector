#!/usr/bin/env python3
"""Tests for scripts/site_check.py — the gate that stands between site/ and Vercel.

Written before the implementation. Each check has a passing case, a failing case,
and the edge case that would make a naive implementation wrong.

The point of this gate is that site/README.md states four positioning rules as
"requirements, not style notes" — and prose requirements are only as good as the
last person who read them. Everything mechanical about them lives here instead.
"""
import json
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import site_check  # noqa: E402


def ids(findings) -> set:
    return {f.rule for f in findings}


# --------------------------------------------------------------------------
# strip_comments — the reason the brand check needs it is subtle, see below
# --------------------------------------------------------------------------
class TestStripComments(unittest.TestCase):
    def test_removes_html_comment(self):
        self.assertNotIn("secret", site_check.strip_comments("<p>a</p><!-- secret -->"))

    def test_removes_css_comment(self):
        self.assertNotIn("secret", site_check.strip_comments("a { /* secret */ color: red }"))

    def test_keeps_real_content(self):
        out = site_check.strip_comments("<p>keep</p><!-- drop -->")
        self.assertIn("keep", out)

    def test_two_comments_do_not_swallow_the_middle(self):
        """A greedy regex would eat `keep` along with both comments."""
        out = site_check.strip_comments("<!-- a -->keep<!-- b -->")
        self.assertIn("keep", out)

    def test_multiline_comment(self):
        out = site_check.strip_comments("x\n<!--\n secret\n-->\ny")
        self.assertNotIn("secret", out)
        self.assertIn("y", out)


# --------------------------------------------------------------------------
# Rule 1 — no Plaud logo, brand colour, or typeface
# --------------------------------------------------------------------------
class TestBrandIsolation(unittest.TestCase):
    def test_clean_page_passes(self):
        self.assertEqual([], site_check.check_brand_isolation("<p>hello</p>"))

    def test_brand_purple_is_flagged(self):
        self.assertIn("brand-isolation", ids(site_check.check_brand_isolation("a{color:#8F53ED}")))

    def test_brand_purple_is_case_insensitive(self):
        self.assertIn("brand-isolation", ids(site_check.check_brand_isolation("a{color:#8f53ed}")))

    def test_brand_typeface_is_flagged(self):
        self.assertIn("brand-isolation", ids(site_check.check_brand_isolation("a{font-family:Jokker}")))

    def test_naming_the_brand_in_a_comment_is_not_using_it(self):
        """The real page has a CSS comment saying "they use Jokker and #8F53ED"
        precisely to explain why this page does NOT. A checker that greps the raw
        source flags our own documentation of the rule as a violation of it."""
        src = "/* Deliberately not theirs: they use Jokker and #8F53ED. */ a{color:#1f6f5c}"
        self.assertEqual([], site_check.check_brand_isolation(src))

    def test_html_comment_mentioning_brand_is_also_exempt(self):
        self.assertEqual([], site_check.check_brand_isolation("<!-- not #8F53ED --><p>x</p>"))


# --------------------------------------------------------------------------
# Rule 2 — independence notice in the header AND the footer, not one or other
# --------------------------------------------------------------------------
BOTH = "<header>Independent · not affiliated with Plaud Inc.</header><footer>not affiliated with them</footer>"


class TestIndependenceNotice(unittest.TestCase):
    def test_both_present_passes(self):
        self.assertEqual([], site_check.check_independence_notice(BOTH))

    def test_missing_from_footer_is_flagged(self):
        src = "<header>not affiliated</header><footer>just links</footer>"
        self.assertIn("independence-notice", ids(site_check.check_independence_notice(src)))

    def test_missing_from_header_is_flagged(self):
        src = "<header>a plugin</header><footer>not affiliated</footer>"
        self.assertIn("independence-notice", ids(site_check.check_independence_notice(src)))

    def test_present_only_in_body_does_not_count(self):
        """Buried mid-page is exactly the failure this rule exists to prevent."""
        src = "<header>a</header><main>not affiliated</main><footer>b</footer>"
        self.assertIn("independence-notice", ids(site_check.check_independence_notice(src)))

    def test_missing_footer_element_entirely_is_flagged(self):
        self.assertIn("independence-notice",
                      ids(site_check.check_independence_notice("<header>not affiliated</header>")))


# --------------------------------------------------------------------------
# Rule 4 — the official integration is linked, so readers can leave for it
# --------------------------------------------------------------------------
class TestOfficialDocsLink(unittest.TestCase):
    def test_link_present_passes(self):
        self.assertEqual([], site_check.check_official_docs_link('<a href="https://docs.plaud.ai/x">d</a>'))

    def test_no_link_is_flagged(self):
        self.assertIn("official-docs-link", ids(site_check.check_official_docs_link("<p>no links</p>")))


# --------------------------------------------------------------------------
# "Claims must match what ships" — the #7 regression, mechanised
# --------------------------------------------------------------------------
README_CMDS = """Install:

    /plugin marketplace add PsychQuant/plaud-mcp-connector
    /plugin install plaud-mcp-connector@plaud-mcp-connector
"""


class TestInstallCommands(unittest.TestCase):
    def test_identical_commands_pass(self):
        site = "<pre><code>/plugin marketplace add PsychQuant/plaud-mcp-connector\n" \
               "/plugin install plaud-mcp-connector@plaud-mcp-connector</code></pre>"
        self.assertEqual([], site_check.check_install_commands(site, README_CMDS))

    def test_site_missing_the_marketplace_suffix_is_flagged(self):
        """This is issue #7 verbatim: the install line was published without
        `@plaud-mcp-connector` and simply did not work."""
        site = "<pre><code>/plugin marketplace add PsychQuant/plaud-mcp-connector\n" \
               "/plugin install plaud-mcp-connector</code></pre>"
        self.assertIn("install-commands", ids(site_check.check_install_commands(site, README_CMDS)))

    def test_site_command_absent_from_readme_is_flagged(self):
        site = "<pre><code>/plugin install something-else</code></pre>"
        self.assertIn("install-commands", ids(site_check.check_install_commands(site, README_CMDS)))

    def test_repetition_on_the_page_is_fine(self):
        """The real page prints the same two commands twice, hero and install tab."""
        block = "/plugin marketplace add PsychQuant/plaud-mcp-connector\n" \
                "/plugin install plaud-mcp-connector@plaud-mcp-connector"
        site = f"<pre><code>{block}</code></pre><pre><code>{block}</code></pre>"
        self.assertEqual([], site_check.check_install_commands(site, README_CMDS))


# --------------------------------------------------------------------------
# CSP and markup must agree, or the page fails silently in production
# --------------------------------------------------------------------------
class TestExternalSubresources(unittest.TestCase):
    def test_inline_only_passes(self):
        src = "<style>a{color:red}</style><script>var x=1</script>"
        self.assertEqual([], site_check.check_external_subresources(src))

    def test_external_script_is_flagged(self):
        src = '<script src="https://cdn.example.com/a.js"></script>'
        self.assertIn("blocked-subresource", ids(site_check.check_external_subresources(src)))

    def test_relative_script_is_also_flagged(self):
        """`script-src 'unsafe-inline'` lists no host and not 'self', so even a
        same-origin file is blocked. Relative paths look safe and are not."""
        self.assertIn("blocked-subresource",
                      ids(site_check.check_external_subresources('<script src="app.js"></script>')))

    def test_external_stylesheet_is_flagged(self):
        src = '<link rel="stylesheet" href="https://fonts.googleapis.com/css">'
        self.assertIn("blocked-subresource", ids(site_check.check_external_subresources(src)))

    def test_remote_image_is_flagged(self):
        self.assertIn("blocked-subresource",
                      ids(site_check.check_external_subresources('<img src="https://x/y.png">')))

    def test_data_uri_image_passes(self):
        self.assertEqual([], site_check.check_external_subresources('<img src="data:image/png;base64,AA">'))

    def test_data_uri_favicon_passes(self):
        src = '<link rel="icon" href="data:image/svg+xml,<svg/>">'
        self.assertEqual([], site_check.check_external_subresources(src))

    def test_anchor_href_is_not_a_subresource(self):
        """Navigation is not a fetch. Flagging <a> would make every outbound link
        an error, including the docs.plaud.ai link rule 4 requires."""
        self.assertEqual([], site_check.check_external_subresources('<a href="https://github.com">x</a>'))

    def test_iframe_is_flagged(self):
        self.assertIn("blocked-subresource",
                      ids(site_check.check_external_subresources('<iframe src="https://x"></iframe>')))

    def test_css_url_reference_is_flagged(self):
        """font-src is not granted, so @font-face url() silently fails to load."""
        src = "<style>@font-face{src:url(https://x/f.woff2)}</style>"
        self.assertIn("blocked-subresource", ids(site_check.check_external_subresources(src)))

    def test_css_data_uri_passes(self):
        src = "<style>a{background:url(data:image/png;base64,AA)}</style>"
        self.assertEqual([], site_check.check_external_subresources(src))


# --------------------------------------------------------------------------
# vercel.json must stay valid and keep the CSP it promises
# --------------------------------------------------------------------------
GOOD_VERCEL = json.dumps({
    "headers": [{"source": "/(.*)", "headers": [
        {"key": "Content-Security-Policy", "value": "default-src 'none'; style-src 'unsafe-inline'"}
    ]}]
})


class TestVercelConfig(unittest.TestCase):
    def test_valid_config_passes(self):
        self.assertEqual([], site_check.check_vercel_config(GOOD_VERCEL))

    def test_invalid_json_is_flagged(self):
        self.assertIn("vercel-config", ids(site_check.check_vercel_config("{not json")))

    def test_missing_csp_is_flagged(self):
        cfg = json.dumps({"headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Content-Type-Options", "value": "nosniff"}]}]})
        self.assertIn("vercel-config", ids(site_check.check_vercel_config(cfg)))

    def test_csp_without_default_src_none_is_flagged(self):
        cfg = json.dumps({"headers": [{"source": "/(.*)", "headers": [
            {"key": "Content-Security-Policy", "value": "default-src *"}]}]})
        self.assertIn("vercel-config", ids(site_check.check_vercel_config(cfg)))

    def test_header_key_match_is_case_insensitive(self):
        cfg = json.dumps({"headers": [{"source": "/(.*)", "headers": [
            {"key": "content-security-policy", "value": "default-src 'none'"}]}]})
        self.assertEqual([], site_check.check_vercel_config(cfg))


# --------------------------------------------------------------------------
# Rule 3 — the domain must not read as official
# --------------------------------------------------------------------------
class TestClassifyDomain(unittest.TestCase):
    def test_no_custom_domain_is_ok(self):
        self.assertEqual("ok", site_check.classify_domain("")[0])

    def test_unrelated_domain_is_ok(self):
        self.assertEqual("ok", site_check.classify_domain("che-tools.dev")[0])

    def test_plaud_ai_itself_is_blocked(self):
        self.assertEqual("block", site_check.classify_domain("plaud.ai")[0])

    def test_plaud_mcp_com_is_blocked(self):
        """site/README.md names this one explicitly as reading official."""
        self.assertEqual("block", site_check.classify_domain("plaud-mcp.com")[0])

    def test_squashed_spelling_is_blocked(self):
        self.assertEqual("block", site_check.classify_domain("plaudai.com")[0])

    def test_get_plaud_is_blocked(self):
        self.assertEqual("block", site_check.classify_domain("getplaud.com")[0])

    def test_ambiguous_third_party_name_warns_rather_than_blocks(self):
        """A machine cannot decide whether this reads as official. Say so instead
        of pretending to know — the deploy asks a human."""
        self.assertEqual("warn", site_check.classify_domain("plaud-mcp-connector.vercel.app")[0])

    def test_subdomain_of_plaud_ai_is_blocked(self):
        self.assertEqual("block", site_check.classify_domain("docs.plaud.ai")[0])

    def test_classification_is_case_insensitive(self):
        self.assertEqual("block", site_check.classify_domain("PLAUD-MCP.com")[0])

    def test_every_verdict_carries_a_reason(self):
        for d in ("", "che-tools.dev", "plaud.ai", "plaud-x.vercel.app"):
            self.assertTrue(site_check.classify_domain(d)[1].strip(), f"no reason for {d!r}")


# --------------------------------------------------------------------------
# The real page must pass. This is the check that would have caught #7.
# --------------------------------------------------------------------------
class TestRealSite(unittest.TestCase):
    def test_shipping_site_has_no_errors(self):
        findings = site_check.run_checks(REPO / "site", REPO / "README.md")
        errors = [f for f in findings if f.level == "error"]
        self.assertEqual([], errors, "\n".join(f"{f.rule}: {f.message}" for f in errors))


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "site_check.py"), *args],
            capture_output=True, text=True,
        )

    def test_exits_zero_on_the_real_site(self):
        p = self._run("--site", str(REPO / "site"), "--readme", str(REPO / "README.md"))
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)

    def test_exits_nonzero_when_a_rule_is_broken(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            broken = pathlib.Path(d)
            (broken / "index.html").write_text('<script src="https://cdn/x.js"></script>')
            (broken / "vercel.json").write_text(GOOD_VERCEL)
            p = self._run("--site", str(broken), "--readme", str(REPO / "README.md"))
            self.assertEqual(1, p.returncode)
            self.assertIn("blocked-subresource", p.stdout + p.stderr)

    def test_blocked_domain_fails_the_run(self):
        p = self._run("--site", str(REPO / "site"), "--readme", str(REPO / "README.md"),
                      "--domain", "plaud-mcp.com")
        self.assertEqual(1, p.returncode)

    def test_missing_site_dir_is_an_error_not_a_crash(self):
        p = self._run("--site", "/nonexistent/site", "--readme", str(REPO / "README.md"))
        self.assertEqual(1, p.returncode)
        self.assertNotIn("Traceback", p.stderr)


if __name__ == "__main__":
    unittest.main()
