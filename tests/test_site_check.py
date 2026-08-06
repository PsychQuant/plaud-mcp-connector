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


# --------------------------------------------------------------------------
# Accessibility properties that a machine can actually decide.
# Added after an independent review found all four on the live page.
# --------------------------------------------------------------------------
class TestLanguageTagging(unittest.TestCase):
    """WCAG 3.1.2: a passage in another language needs its own lang, or a screen
    reader pronounces Han characters with English rules and produces noise."""

    def test_english_page_passes(self):
        self.assertEqual([], site_check.check_language_tagging('<html lang="en"><p>hello</p></html>'))

    def test_untagged_chinese_is_flagged(self):
        src = '<html lang="en"><p>哪次會議談到預算</p></html>'
        self.assertIn("language-tagging", ids(site_check.check_language_tagging(src)))

    def test_tagged_chinese_passes(self):
        src = '<html lang="en"><p lang="zh-Hant">哪次會議談到預算</p></html>'
        self.assertEqual([], site_check.check_language_tagging(src))

    def test_lang_inherits_from_an_ancestor(self):
        src = '<html lang="en"><div lang="zh-Hant"><p>哪次會議</p></div></html>'
        self.assertEqual([], site_check.check_language_tagging(src))

    def test_lang_scope_ends_with_the_element(self):
        """A naive stack that never pops would treat the trailing English as zh."""
        src = '<html lang="en"><div lang="zh-Hant"><p>會議</p></div><p>後面的中文</p></html>'
        self.assertIn("language-tagging", ids(site_check.check_language_tagging(src)))

    def test_japanese_kana_also_counts(self):
        self.assertIn("language-tagging",
                      ids(site_check.check_language_tagging('<html lang="en"><p>ひらがな</p></html>')))


class TestTableHeaders(unittest.TestCase):
    """WCAG 1.3.1 (Level A): the header/data relationship must be programmatic,
    not just bold text."""

    def test_scoped_table_passes(self):
        src = ('<table><thead><tr><th></th><th scope="col">A</th></tr></thead>'
               '<tbody><tr><th scope="row">Search</th><td>x</td></tr></tbody></table>')
        self.assertEqual([], site_check.check_table_headers(src))

    def test_row_label_as_td_is_flagged(self):
        src = ('<table><thead><tr><th scope="col">A</th></tr></thead>'
               '<tbody><tr><td>Search</td><td>x</td></tr></tbody></table>')
        self.assertIn("table-headers", ids(site_check.check_table_headers(src)))

    def test_th_without_scope_is_flagged(self):
        src = '<table><tr><th>A</th><td>x</td></tr></table>'
        self.assertIn("table-headers", ids(site_check.check_table_headers(src)))

    def test_page_without_tables_passes(self):
        self.assertEqual([], site_check.check_table_headers("<p>no tables here</p>"))


class TestAriaTabs(unittest.TestCase):
    """role=tab announces a keyboard contract. Claiming it without honouring it
    is worse than not claiming it: it tells the user arrow keys work."""

    def test_no_tabs_passes(self):
        self.assertEqual([], site_check.check_aria_tabs("<p>x</p>"))

    def test_tabs_without_keydown_are_flagged(self):
        src = '<div role="tablist"><button role="tab"></button></div><script>a.onclick=1</script>'
        self.assertIn("aria-tabs", ids(site_check.check_aria_tabs(src)))

    def test_tabs_with_keydown_and_roving_tabindex_pass(self):
        src = ('<div role="tablist"><button role="tab" tabindex="0"></button></div>'
               '<script>t.addEventListener("keydown", f); t.tabIndex = -1</script>')
        self.assertEqual([], site_check.check_aria_tabs(src))

    def test_keydown_without_roving_tabindex_is_still_flagged(self):
        src = '<div role="tablist"><button role="tab"></button></div><script>x.addEventListener("keydown",f)</script>'
        self.assertIn("aria-tabs", ids(site_check.check_aria_tabs(src)))


class TestContrast(unittest.TestCase):
    """WCAG 1.4.11: a border that is the only thing delineating an interactive
    control needs 3:1 against its background."""

    def test_ratio_of_black_on_white_is_21(self):
        self.assertAlmostEqual(21.0, site_check.contrast_ratio("#000000", "#ffffff"), places=1)

    def test_ratio_is_symmetric(self):
        self.assertAlmostEqual(site_check.contrast_ratio("#1f6f5c", "#fbfaf8"),
                               site_check.contrast_ratio("#fbfaf8", "#1f6f5c"), places=6)

    def test_identical_colours_are_one(self):
        self.assertAlmostEqual(1.0, site_check.contrast_ratio("#abcdef", "#abcdef"), places=6)

    def test_three_digit_hex_expands(self):
        self.assertAlmostEqual(21.0, site_check.contrast_ratio("#000", "#fff"), places=1)

    def test_low_contrast_body_text_is_flagged(self):
        css = ":root { --bg: #fbfaf8; --fg: #111111; --rule-strong: #e2ded6; --muted: #5f5d57; }"
        self.assertIn("contrast", ids(site_check.check_contrast(f"<style>{css}</style>")))

    def test_adequate_contrast_passes(self):
        css = ":root { --bg: #fbfaf8; --fg: #111111; --muted: #5f5d57; }"
        self.assertEqual([], site_check.check_contrast(f"<style>{css}</style>"))

    def test_dark_scheme_is_checked_too(self):
        css = (":root { --bg: #fbfaf8; --fg: #111111; --muted: #5f5d57; }"
               "@media (prefers-color-scheme: dark) { :root { --bg: #14140f; --fg: #eeeeee;"
               " --muted: #2e2d27; } }")
        found = site_check.check_contrast(f"<style>{css}</style>")
        self.assertIn("contrast", ids(found))
        self.assertTrue(any("dark" in f.message for f in found), [f.message for f in found])




# --------------------------------------------------------------------------
# A settled judgement should stop being asked.
#
# classify_domain warns on any name containing "plaud" because a string
# comparison cannot decide whether it reads as official. Once a human HAS
# decided, a gate that keeps asking is one people learn to click past — and a
# gate people ignore is worse than no gate, because it still looks like cover.
# --------------------------------------------------------------------------
class TestAcceptedDomain(unittest.TestCase):
    ACCEPTED = "plaud-mcp-connector.vercel.app"

    def test_accepting_the_reviewed_domain_clears_the_warning(self):
        verdict, reason = site_check.classify_domain(self.ACCEPTED, accepted=self.ACCEPTED)
        self.assertEqual("ok", verdict)
        self.assertIn("accepted", reason.lower())

    def test_without_acceptance_it_still_warns(self):
        self.assertEqual("warn", site_check.classify_domain(self.ACCEPTED)[0])

    def test_acceptance_cannot_override_a_block(self):
        """Otherwise the record becomes a way to wave through plaud.ai itself —
        the one thing the block list exists to stop."""
        for bad in ("plaud.ai", "plaud-mcp.com", "getplaud.com"):
            self.assertEqual("block", site_check.classify_domain(bad, accepted=bad)[0], bad)

    def test_acceptance_is_specific_to_the_domain_reviewed(self):
        """Accepting one name must not silence every other plaud-ish name."""
        self.assertEqual("warn", site_check.classify_domain("plaud-tools.vercel.app",
                                                            accepted=self.ACCEPTED)[0])

    def test_acceptance_normalises_case_and_scheme(self):
        for written in ("PLAUD-MCP-CONNECTOR.vercel.app", "https://plaud-mcp-connector.vercel.app/"):
            self.assertEqual("ok", site_check.classify_domain(written, accepted=self.ACCEPTED)[0], written)

    def test_unrelated_domain_is_unaffected_by_an_acceptance(self):
        self.assertEqual("ok", site_check.classify_domain("che-tools.dev", accepted=self.ACCEPTED)[0])

    def test_empty_acceptance_leaves_behaviour_unchanged(self):
        self.assertEqual("warn", site_check.classify_domain(self.ACCEPTED, accepted="")[0])

    def test_run_checks_threads_the_acceptance_through(self):
        findings = site_check.run_checks(REPO / "site", REPO / "README.md",
                                         self.ACCEPTED, accepted=self.ACCEPTED)
        self.assertEqual([], [f for f in findings if f.rule == "domain"])

    def test_run_checks_without_acceptance_still_warns(self):
        findings = site_check.run_checks(REPO / "site", REPO / "README.md", self.ACCEPTED)
        warns = [f for f in findings if f.rule == "domain"]
        self.assertEqual(1, len(warns))
        self.assertEqual("warn", warns[0].level)


class TestAcceptedDomainCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "site_check.py"),
             "--site", str(REPO / "site"), "--readme", str(REPO / "README.md"), *args],
            capture_output=True, text=True)

    def test_accepted_domain_produces_no_warning(self):
        p = self._run("--domain", "plaud-mcp-connector.vercel.app",
                      "--accept-domain", "plaud-mcp-connector.vercel.app")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        self.assertNotIn("⚠", p.stdout)

    def test_blocked_domain_is_not_rescued_by_accepting_it(self):
        p = self._run("--domain", "plaud-mcp.com", "--accept-domain", "plaud-mcp.com")
        self.assertEqual(1, p.returncode)


# --------------------------------------------------------------------------
# Multi-language gate (#14)
#
# The positioning rules are per-language properties, not page properties. Without
# these checks, adding languages would mean only the English version is ever
# verified — the independence notice could be missing in Japanese and every gate
# would still be green.
# --------------------------------------------------------------------------
I18N_OK = '''
<html lang="en">
<p data-i18n="hero.lede">x</p><p data-i18n="foot.independent">y</p>
<select id="lang"><option value="en">English</option><option value="ja">日本語</option></select>
<script>
const I18N = {
  en: {"hero.lede": "Search what was said", "foot.independent": "not affiliated with Plaud Inc."},
  ja: {"hero.lede": "話された内容を検索", "foot.independent": "Plaud Inc. とは提携していません"}
};
</script>
</html>'''


class TestI18nCompleteness(unittest.TestCase):
    def test_page_without_i18n_is_a_no_op(self):
        """The single-language page must keep passing unchanged."""
        self.assertEqual([], site_check.check_i18n_completeness("<p>plain page</p>"))

    def test_complete_translation_set_passes(self):
        self.assertEqual([], site_check.check_i18n_completeness(I18N_OK))

    def test_key_missing_from_one_language_is_flagged(self):
        """A half-translated page is the failure this exists to prevent."""
        broken = I18N_OK.replace('"hero.lede": "話された内容を検索", ', "")
        found = site_check.check_i18n_completeness(broken)
        self.assertIn("i18n-completeness", ids(found))
        self.assertTrue(any("ja" in f.message and "hero.lede" in f.message for f in found),
                        [f.message for f in found])

    def test_key_used_in_markup_but_absent_everywhere_is_flagged(self):
        broken = I18N_OK.replace('<p data-i18n="hero.lede">x</p>',
                                 '<p data-i18n="hero.lede">x</p><p data-i18n="ghost.key">z</p>')
        self.assertIn("i18n-completeness", ids(site_check.check_i18n_completeness(broken)))

    def test_dictionary_key_no_markup_uses_is_flagged(self):
        """Drift the other way: a translated string nothing renders is dead weight
        that hides the fact the markup lost its hook."""
        broken = I18N_OK.replace('"hero.lede": "Search what was said"',
                                 '"hero.lede": "Search what was said", "orphan.key": "nobody renders me"')
        self.assertIn("i18n-completeness", ids(site_check.check_i18n_completeness(broken)))

    def test_switcher_must_offer_exactly_the_translated_languages(self):
        """A language in the dictionary with no way to pick it is unreachable;
        an option with no dictionary renders an empty page."""
        broken = I18N_OK.replace('<option value="ja">日本語</option>', "")
        self.assertIn("i18n-completeness", ids(site_check.check_i18n_completeness(broken)))


class TestI18nPositioningPerLanguage(unittest.TestCase):
    """Rules 2 and 4 are per-language properties. English passing says nothing
    about Japanese."""

    def test_independence_notice_present_in_every_language_passes(self):
        self.assertEqual([], site_check.check_i18n_positioning(I18N_OK))

    def test_language_without_independence_notice_is_flagged(self):
        broken = I18N_OK.replace('"foot.independent": "Plaud Inc. とは提携していません"',
                                 '"foot.independent": "Plaud のプラグイン"')
        found = site_check.check_i18n_positioning(broken)
        self.assertIn("i18n-positioning", ids(found))
        self.assertTrue(any("ja" in f.message for f in found), [f.message for f in found])

    def test_page_without_i18n_is_a_no_op(self):
        self.assertEqual([], site_check.check_i18n_positioning("<p>plain</p>"))

    def test_install_commands_must_not_be_translated(self):
        """`/plugin install …` is a literal the user types. A translated copy in a
        dictionary would ship a command that does not exist."""
        broken = I18N_OK.replace('"hero.lede": "話された内容を検索"',
                                 '"hero.lede": "/plugin install プラウド"')
        self.assertIn("i18n-positioning", ids(site_check.check_i18n_positioning(broken)))


# --------------------------------------------------------------------------
# Dead selectors (#16 fallout)
#
# Removing the install tabs left the CSS and the JS behind. The markup was gone,
# so `document.querySelector('[role="tablist"]')` returned null and the next line
# threw — a blank-behaving page whose source still greps clean. Every existing
# check passed. Structure intact, behaviour broken: the same shape as every other
# defect in this repo, and greppable checks are exactly what cannot see it.
# --------------------------------------------------------------------------
class TestDeadSelectors(unittest.TestCase):
    def test_selector_with_a_match_passes(self):
        src = '<div id="lang"></div><script>document.querySelector("#lang")</script>'
        self.assertEqual([], site_check.check_dead_selectors(src))

    def test_selector_with_no_match_is_flagged(self):
        src = '<p>nothing</p><script>document.querySelector(\'[role="tablist"]\')</script>'
        found = site_check.check_dead_selectors(src)
        self.assertIn("dead-selector", ids(found))
        self.assertTrue(any("tablist" in f.message for f in found), [f.message for f in found])

    def test_query_selector_all_is_checked_too(self):
        src = '<p>x</p><script>document.querySelectorAll(\'[role="tab"]\')</script>'
        self.assertIn("dead-selector", ids(site_check.check_dead_selectors(src)))

    def test_attribute_selector_matching_markup_passes(self):
        src = '<button role="tab"></button><script>document.querySelectorAll(\'[role="tab"]\')</script>'
        self.assertEqual([], site_check.check_dead_selectors(src))

    def test_class_selector_is_checked(self):
        src = '<p>x</p><script>document.querySelector(".tabs")</script>'
        self.assertIn("dead-selector", ids(site_check.check_dead_selectors(src)))

    def test_class_selector_present_passes(self):
        src = '<p class="tabs">x</p><script>document.querySelector(".tabs")</script>'
        self.assertEqual([], site_check.check_dead_selectors(src))

    def test_page_without_script_passes(self):
        self.assertEqual([], site_check.check_dead_selectors("<p>no script here</p>"))

    def test_selector_shapes_it_cannot_judge_are_left_alone(self):
        """Only id / class / [attr="value"] are decidable by string matching.
        A compound or pseudo selector is not, and guessing would produce false
        alarms that train the reader to ignore this rule."""
        src = '<p>x</p><script>document.querySelector("div > p:nth-child(2)")</script>'
        self.assertEqual([], site_check.check_dead_selectors(src))


class TestI18nQuotedLanguageKeys(unittest.TestCase):
    """`zh-Hant` cannot be a bare JS object key, so real pages quote it. A parser
    that only matches unquoted keys finds zero languages and every completeness
    check silently passes on a page it never read."""

    QUOTED = '''
<p data-i18n="k">x</p>
<select id="lang"><option value="en">E</option><option value="zh-Hant">中</option></select>
<script>
const I18N = {
  "en": {"k": "not affiliated with Plaud Inc."},
  "zh-Hant": {"k": "獨立專案，與 Plaud 無關"}
};
</script>'''

    def test_quoted_and_hyphenated_keys_are_parsed(self):
        self.assertEqual([], site_check.check_i18n_completeness(self.QUOTED))

    def test_positioning_reaches_the_hyphenated_language(self):
        self.assertEqual([], site_check.check_i18n_positioning(self.QUOTED))

    def test_a_missing_key_in_the_quoted_language_is_still_caught(self):
        broken = self.QUOTED.replace('"zh-Hant": {"k": "獨立專案，與 Plaud 無關"}', '"zh-Hant": {}')
        self.assertIn("i18n-completeness", ids(site_check.check_i18n_completeness(broken)))


class TestControlBorderToken(unittest.TestCase):
    """A token nothing references is not a fix — the same lesson the tab-pill
    version of this check taught, re-applied to the control that replaced them."""

    def test_switcher_using_the_strong_token_passes(self):
        self.assertEqual([], site_check.check_control_border_token(
            "<style>.langpick select { border: 1px solid var(--rule-strong); }</style>"))

    def test_switcher_falling_back_to_the_hairline_is_flagged(self):
        self.assertIn("contrast", ids(site_check.check_control_border_token(
            "<style>.langpick select { border: 1px solid var(--rule); }</style>")))

    def test_page_without_a_switcher_passes(self):
        self.assertEqual([], site_check.check_control_border_token("<p>no controls</p>"))
