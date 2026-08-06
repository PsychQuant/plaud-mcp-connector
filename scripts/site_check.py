#!/usr/bin/env python3
"""Pre-deploy gate for site/.

`site/README.md` states four positioning rules as "requirements, not style
notes". Prose requirements hold only as long as the next person reads them, and
this repo already shipped one page whose install commands did not work (#7).
Everything mechanical about those rules lives here so `make site-deploy` cannot
publish a page that breaks them.

What it cannot check is stated as such: whether a domain "reads as official" is
a judgement, so ambiguous names warn and ask a human rather than pretending.

    site_check.py --site site --readme README.md [--domain example.com]

Exit 0 when nothing is at error level, 1 otherwise.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import pathlib
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser

# Plaud's own identity, which this page must not borrow.
BRAND_COLOUR = "8f53ed"
BRAND_TYPEFACE = "jokker"

# Second-level names that read as Plaud's own even to a careful reader.
BLOCKED_LABELS = frozenset(
    {"plaud", "plaudai", "plaudmcp", "getplaud", "plaudapp", "myplaud", "plaudnote"}
)

# Elements the browser fetches. Under `default-src 'none'` with only
# `style-src`/`script-src 'unsafe-inline'` and `img-src data:`, anything here
# that is not a data: URI is silently blocked at runtime.
FETCHED = {
    "img": "src", "iframe": "src", "frame": "src", "embed": "src",
    "object": "data", "audio": "src", "video": "src", "source": "src", "track": "src",
}
# rel values that make a <link> a fetch rather than metadata.
FETCHING_REL = frozenset({"stylesheet", "icon", "shortcut", "preload", "prefetch", "manifest"})


@dataclass(frozen=True)
class Finding:
    rule: str
    level: str  # "error" | "warn"
    message: str


def strip_comments(source: str) -> str:
    """Drop HTML and CSS comments.

    Needed because the page documents the rules it follows: a CSS comment names
    Plaud's typeface and purple to explain why neither is used. Grepping the raw
    source would read that explanation as a violation of the thing it explains.
    Non-greedy on purpose — a greedy match would swallow everything between two
    separate comments.
    """
    without_html = re.sub(r"<!--.*?-->", " ", source, flags=re.DOTALL)
    return re.sub(r"/\*.*?\*/", " ", without_html, flags=re.DOTALL)


def check_brand_isolation(source: str) -> list[Finding]:
    body = strip_comments(source).lower()
    found = []
    if BRAND_COLOUR in body:
        found.append(Finding("brand-isolation", "error",
                             f"Plaud's brand purple #{BRAND_COLOUR.upper()} is used in the page"))
    if BRAND_TYPEFACE in body:
        found.append(Finding("brand-isolation", "error",
                             "Plaud's typeface (Jokker) is referenced in the page"))
    return found


def _element(source: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}\b.*?</{tag}>", source, flags=re.DOTALL | re.IGNORECASE)
    return m.group(0) if m else None


def check_independence_notice(source: str) -> list[Finding]:
    """Rule 2: the notice lives in the header AND the footer, not one or other."""
    found = []
    for tag in ("header", "footer"):
        block = _element(source, tag)
        if block is None:
            found.append(Finding("independence-notice", "error", f"no <{tag}> element on the page"))
        elif "not affiliated" not in block.lower():
            found.append(Finding("independence-notice", "error",
                                 f"<{tag}> does not carry the 'not affiliated' notice"))
    return found


def check_official_docs_link(source: str) -> list[Finding]:
    """Rule 4: a reader looking for Plaud's own integration must be sent to it."""
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)', source, flags=re.IGNORECASE)
    if any("docs.plaud.ai" in h for h in hrefs):
        return []
    return [Finding("official-docs-link", "error",
                    "no link to docs.plaud.ai — readers wanting the official integration "
                    "have nowhere to go")]


def _plugin_commands(text: str) -> set[str]:
    """Pull `/plugin …` lines out of markdown or HTML.

    Tags come off before anything else: on the page the first command shares its
    line with `<pre><code>` and the last one with `</code></pre>`, so matching
    the raw line both misses the opening command and glues closing tags onto the
    final one. Unescaping happens after, or an escaped `&lt;script&gt;` in prose
    would turn into a tag and be stripped as one.

    Each tag becomes a newline rather than nothing: two adjacent code blocks sit
    on one source line, and deleting the tags between them would splice the last
    command of the first into the first command of the second — inventing a
    command that appears in neither file and blocking the deploy over it.
    """
    plain = html_mod.unescape(re.sub(r"<[^>]+>", "\n", text))
    return {
        " ".join(line.strip().split())
        for line in plain.splitlines()
        if line.strip().startswith("/plugin ")
    }


def check_install_commands(site_html: str, readme: str) -> list[Finding]:
    """The page's install commands must be the README's, verbatim.

    #7 shipped `/plugin install plaud-mcp-connector` without the marketplace
    suffix. It parsed, it read fine, and it did not work.
    """
    on_page, in_readme = _plugin_commands(site_html), _plugin_commands(readme)
    found = []
    for missing in sorted(in_readme - on_page):
        found.append(Finding("install-commands", "error",
                             f"README documents a command the page omits: {missing}"))
    for extra in sorted(on_page - in_readme):
        found.append(Finding("install-commands", "error",
                             f"page shows a command the README does not: {extra}"))
    return found


class _SubresourceScanner(HTMLParser):
    """Collects fetches the CSP would block. Anchors are navigation, not fetches."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "style":
            self._in_style = True
        elif tag == "script" and "src" in a:
            # script-src lists 'unsafe-inline' and no host, not even 'self'.
            self.problems.append(f"<script src={a['src']!r}> — inline scripts only")
        elif tag == "link":
            rel = set(a.get("rel", "").lower().split())
            href = a.get("href", "")
            if rel & FETCHING_REL and not href.startswith("data:"):
                self.problems.append(f"<link rel={a.get('rel')!r} href={href!r}>")
        elif tag == "form":
            self.problems.append("<form> — form-action is 'none'")
        elif tag in FETCHED:
            value = a.get(FETCHED[tag], "")
            if value and not value.startswith("data:"):
                self.problems.append(f"<{tag} {FETCHED[tag]}={value!r}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if not self._in_style:
            return
        for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", data):
            if not url.strip().startswith("data:"):
                self.problems.append(f"CSS url({url.strip()!r}) — font-src/img-src do not allow it")


def check_external_subresources(source: str) -> list[Finding]:
    """CSP and markup must agree, or the page fails silently in production.

    A blocked subresource does not error visibly: the page renders without it.
    That is the same shape as every other bug this repo has hit — all gates
    green, content wrong.
    """
    scanner = _SubresourceScanner()
    scanner.feed(source)
    return [Finding("blocked-subresource", "error", f"the CSP blocks {p}") for p in scanner.problems]


def check_vercel_config(text: str) -> list[Finding]:
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as exc:
        return [Finding("vercel-config", "error", f"vercel.json is not valid JSON: {exc}")]

    values = [
        h.get("value", "")
        for rule in cfg.get("headers", [])
        for h in rule.get("headers", [])
        if h.get("key", "").lower() == "content-security-policy"
    ]
    if not values:
        return [Finding("vercel-config", "error", "vercel.json sets no Content-Security-Policy")]
    if not any("default-src 'none'" in v for v in values):
        return [Finding("vercel-config", "error",
                        "the CSP no longer starts from default-src 'none'")]
    return []


def _host(value: str) -> str:
    return re.sub(r"^[a-z]+://", "", value.strip().lower()).split("/")[0]


def classify_domain(domain: str, accepted: str = "") -> tuple[str, str]:
    """Rule 3: the domain must not read as official. Returns (verdict, reason).

    "block" for names that are unambiguously Plaud's own shape, "warn" for names
    that merely contain the word — whether those read as official is a judgement
    a string comparison has no business making, so it is handed back to a human.

    `accepted` records the one name a human has already looked at and cleared.
    It downgrades a warning on exactly that host and nothing else, and it cannot
    touch a block — otherwise the record would become a way to wave through
    plaud.ai itself, which is the single thing the block list exists to stop.
    A gate that re-asks a settled question is one people learn to click past,
    and a gate people ignore is worse than none because it still looks like cover.
    """
    host = _host(domain)
    if not host:
        return "ok", "no custom domain — Vercel's own hostname is used"

    labels = [re.sub(r"[^a-z0-9]", "", part) for part in host.split(".")]
    if any(label in BLOCKED_LABELS for label in labels):
        return "block", (f"{host} reads as Plaud's own domain "
                         f"(site/README.md rule 3 names plaud-mcp.com as the example)")
    if any("plaud" in label for label in labels):
        if accepted and host == _host(accepted):
            return "ok", (f"{host} contains 'plaud' but was reviewed and accepted as not "
                          f"readable as official (see site/README.md rule 3)")
        return "warn", (f"{host} contains 'plaud' — check it cannot be mistaken for official "
                        f"before deploying; a string comparison cannot decide this")
    return "ok", f"{host} does not resemble plaud.ai"


def run_checks(site_dir: pathlib.Path, readme_path: pathlib.Path,
               domain: str = "", accepted: str = "") -> list[Finding]:
    site_dir, readme_path = pathlib.Path(site_dir), pathlib.Path(readme_path)
    found: list[Finding] = []

    index = site_dir / "index.html"
    if not index.is_file():
        return [Finding("missing-file", "error", f"{index} does not exist")]
    source = index.read_text()

    if readme_path.is_file():
        found += check_install_commands(source, readme_path.read_text())
    else:
        found.append(Finding("missing-file", "error", f"{readme_path} does not exist"))

    vercel = site_dir / "vercel.json"
    if vercel.is_file():
        found += check_vercel_config(vercel.read_text())
    else:
        found.append(Finding("missing-file", "error", f"{vercel} does not exist"))

    found += check_brand_isolation(source)
    found += check_independence_notice(source)
    found += check_official_docs_link(source)
    found += check_external_subresources(source)
    found += check_language_tagging(source)
    found += check_table_headers(source)
    found += check_aria_tabs(source)
    found += check_contrast(source)
    found += check_dead_selectors(source)
    found += check_i18n_completeness(source)
    found += check_i18n_positioning(source)

    verdict, reason = classify_domain(domain, accepted)
    if verdict == "block":
        found.append(Finding("domain", "error", reason))
    elif verdict == "warn":
        found.append(Finding("domain", "warn", reason))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="site")
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--domain", default="", help="the domain this will be served from, if custom")
    ap.add_argument("--accept-domain", dest="accept_domain", default="",
                    help="a plaud-containing domain a human has reviewed and cleared; "
                         "downgrades the warning for that exact host only, never a block")
    args = ap.parse_args()

    findings = run_checks(pathlib.Path(args.site), pathlib.Path(args.readme),
                          args.domain, args.accept_domain)
    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]

    for f in errors:
        print(f"✗ {f.rule}: {f.message}")
    for f in warns:
        print(f"⚠ {f.rule}: {f.message}")

    if errors:
        print(f"\n{len(errors)} blocking issue(s) — not deploying")
        return 1
    print(f"site checks pass{' (with warnings above)' if warns else ''}")
    return 0



# ---------------------------------------------------------------------------
# Accessibility properties a machine can decide. Added after an independent
# review found all four of these on the page after it was already public.
# ---------------------------------------------------------------------------

CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "source", "track", "wbr"})


class _LangScanner(HTMLParser):
    """Tracks the nearest `lang` in scope while walking the document."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self._stack: list[tuple[str, str | None]] = []
        self._skip = 0

    def _lang(self) -> str:
        for _, lang in reversed(self._stack):
            if lang:
                return lang
        return ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag in VOID:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        self._stack.append((tag, a.get("lang")))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        # Pop back to the matching open tag. Unbalanced markup must not leave a
        # stale lang in scope for the rest of the document.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._skip or not CJK.search(data):
            return
        lang = self._lang()
        if not lang or lang.lower().startswith("en"):
            self.problems.append(
                f"{data.strip()[:30]!r} is CJK text under lang={lang or 'unset'!r}"
            )


def check_language_tagging(source: str) -> list[Finding]:
    """WCAG 3.1.2. A passage in another language needs its own `lang`.

    Without it a screen reader keeps applying English pronunciation rules to Han
    characters and produces noise — on this page that would silently break the
    one example demonstrating that you can search in your own language.
    """
    scanner = _LangScanner()
    scanner.feed(source)
    return [Finding("language-tagging", "error", f"missing lang: {p}") for p in scanner.problems]


class _TableScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict] = []
        self._cell: dict | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "table":
            self.tables.append({"has_head": False, "rows": [], "section": None})
        elif not self.tables:
            return
        elif tag in ("thead", "tbody"):
            self.tables[-1]["section"] = tag
            if tag == "thead":
                self.tables[-1]["has_head"] = True
        elif tag == "tr":
            self.tables[-1]["rows"].append({"section": self.tables[-1]["section"], "cells": []})
        elif tag in ("th", "td") and self.tables[-1]["rows"]:
            self._cell = {"tag": tag, "scope": a.get("scope", ""), "text": ""}
            self.tables[-1]["rows"][-1]["cells"].append(self._cell)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td"):
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"] += data


def check_table_headers(source: str) -> list[Finding]:
    """WCAG 1.3.1 (Level A): the header/data relationship must be programmatic.

    A row label that is a bold `<td>` looks like a header and announces as data,
    so cell-by-cell navigation loses the row context entirely.
    """
    scanner = _TableScanner()
    scanner.feed(source)
    found = []
    for n, table in enumerate(scanner.tables, 1):
        for row in table["rows"]:
            for cell in row["cells"]:
                # An empty corner cell carries no header meaning and needs no scope.
                if cell["tag"] == "th" and cell["text"].strip() and not cell["scope"]:
                    found.append(Finding("table-headers", "error",
                                         f"table {n}: <th>{cell['text'].strip()[:20]}</th> has no scope"))
            if table["has_head"] and row["section"] == "tbody" and row["cells"]:
                first = row["cells"][0]
                if first["tag"] == "td":
                    found.append(Finding("table-headers", "error",
                                         f"table {n}: row label {first['text'].strip()[:20]!r} is a "
                                         f"<td>; a labelled row needs <th scope=\"row\">"))
    return found


def check_aria_tabs(source: str) -> list[Finding]:
    """role=tab announces a keyboard contract: arrow keys move between tabs and
    only the selected tab is in the Tab order. Claiming the role without
    honouring it is worse than not claiming it — it tells the user arrow keys
    work, and then they do not."""
    if 'role="tab"' not in source and "role='tab'" not in source:
        return []
    found = []
    if "keydown" not in source:
        found.append(Finding("aria-tabs", "error",
                             "role=tab is used but no keydown handler exists — arrow keys do nothing"))
    if "tabindex" not in source.lower():
        found.append(Finding("aria-tabs", "error",
                             "role=tab is used without roving tabindex — every tab stays in the Tab order"))
    return found


def _luminance(hex_colour: str) -> float:
    h = hex_colour.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2.x relative-contrast ratio. Symmetric, 1.0 to 21.0."""
    la, lb = _luminance(a), _luminance(b)
    lo, hi = sorted((la, lb))
    return (hi + 0.05) / (lo + 0.05)


# (custom property, minimum ratio against --bg, what it is)
# WCAG 1.4.11 governs the boundary of an interactive component. The install tabs
# were the page's only control and they are gone (#16 — the install is one
# sentence now), so `--rule-strong` and the check that it was actually referenced
# went with them rather than standing guard over nothing. Both come back with the
# language switcher (#14), which is the next real control.
CONTRAST_RULES = (
    ("--muted", 4.5, "secondary body text (WCAG 1.4.3)"),
)


def _palettes(source: str) -> dict[str, dict[str, str]]:
    """Pull the light and dark custom-property sets out of the stylesheet."""
    out: dict[str, dict[str, str]] = {}
    dark = re.search(r"prefers-color-scheme:\s*dark.*?:root\s*\{(.*?)\}", source, re.DOTALL)
    first = re.search(r":root\s*\{(.*?)\}", source, re.DOTALL)
    for name, match in (("light", first), ("dark", dark)):
        if match:
            out[name] = dict(re.findall(r"(--[a-z-]+)\s*:\s*(#[0-9a-fA-F]{3,6})", match.group(1)))
    return out


def check_contrast(source: str) -> list[Finding]:
    found = []
    for scheme, palette in _palettes(source).items():
        bg = palette.get("--bg")
        if not bg:
            continue
        for prop, minimum, what in CONTRAST_RULES:
            value = palette.get(prop)
            if not value:
                continue
            ratio = contrast_ratio(value, bg)
            if ratio < minimum:
                found.append(Finding("contrast", "error",
                                     f"{scheme}: {prop} {value} on --bg {bg} is {ratio:.2f}:1, "
                                     f"below {minimum}:1 for {what}"))
    return found


# ---------------------------------------------------------------------------
# Multi-language gate (#14)
#
# The positioning rules are per-language properties, not page properties. Check
# them per language or adding a language silently means "only English is
# verified" — the independence notice could vanish in Japanese with every gate
# still green.
# ---------------------------------------------------------------------------

# Each language's own way of saying the page is not Plaud's. Matching the English
# phrase everywhere would be a check that only English can pass.
NOT_AFFILIATED = {
    "en": ("not affiliated",),
    "zh-Hant": ("非 Plaud", "無關", "沒有隸屬", "獨立"),
    "ja": ("提携していません", "非公式", "独立"),
    "de": ("nicht verbunden", "unabhängig"),
    "es": ("no está afiliado", "no afiliado", "independiente"),
    "fr": ("sans affiliation", "non affilié", "indépendant"),
    "it": ("non affiliato", "indipendente"),
    "pt": ("não afiliado", "sem afiliação", "independente"),
    "nl": ("niet gelieerd", "onafhankelijk"),
}


def _i18n_tables(source: str) -> dict[str, dict[str, str]]:
    """Pull the inline `const I18N = {...}` dictionaries out of the page.

    Parsed rather than imported because the page must stay a single file with no
    build step; a JSON sidecar would be a fetch, and the CSP grants none.
    """
    block = re.search(r"const\s+I18N\s*=\s*\{(.*?)\n\};", source, re.DOTALL)
    if block is None:
        return {}
    tables: dict[str, dict[str, str]] = {}
    for lang, body in re.findall(r'([a-zA-Z-]+)\s*:\s*\{(.*?)\}', block.group(1), re.DOTALL):
        pairs = re.findall(r'"([^"]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
        tables[lang] = {k: v for k, v in pairs}
    return tables


def _markup_keys(source: str) -> set[str]:
    return set(re.findall(r'data-i18n="([^"]+)"', source))


def _switcher_languages(source: str) -> set[str]:
    block = re.search(r'<select[^>]*id="lang"[^>]*>(.*?)</select>', source, re.DOTALL)
    if block is None:
        return set()
    return set(re.findall(r'value="([^"]+)"', block.group(1)))


def check_i18n_completeness(source: str) -> list[Finding]:
    """Every rendered key exists in every language, and nothing drifts either way.

    A page is either fully translated into a language or does not offer it. There
    is no useful middle state: a missing key renders blank or falls back silently,
    and the reader cannot tell which.
    """
    tables = _i18n_tables(source)
    if not tables:
        return []
    found = []
    used = _markup_keys(source)
    all_keys = set().union(*(set(t) for t in tables.values()))

    for key in sorted(used - all_keys):
        found.append(Finding("i18n-completeness", "error",
                             f"markup renders data-i18n={key!r} but no language defines it"))
    for key in sorted(all_keys - used):
        found.append(Finding("i18n-completeness", "error",
                             f"{key!r} is translated but no markup renders it — "
                             f"either the hook was lost or the string is dead"))
    for lang in sorted(tables):
        for key in sorted((used & all_keys) - set(tables[lang])):
            found.append(Finding("i18n-completeness", "error",
                                 f"{lang} is missing {key!r} — a half-translated page"))

    switcher = _switcher_languages(source)
    if switcher:
        for lang in sorted(set(tables) - switcher):
            found.append(Finding("i18n-completeness", "error",
                                 f"{lang} is translated but the switcher cannot select it"))
        for lang in sorted(switcher - set(tables)):
            found.append(Finding("i18n-completeness", "error",
                                 f"the switcher offers {lang} but nothing is translated into it"))
    return found


def check_i18n_positioning(source: str) -> list[Finding]:
    """Rules 2 and 4, per language. English passing says nothing about Japanese."""
    tables = _i18n_tables(source)
    if not tables:
        return []
    found = []
    for lang in sorted(tables):
        blob = " ".join(tables[lang].values())
        # Install commands are literals the reader types. A translated copy would
        # ship a command that does not exist.
        if "/plugin " in blob:
            found.append(Finding("i18n-positioning", "error",
                                 f"{lang} has a '/plugin ...' command inside a translated "
                                 f"string — install commands are literals, never translated"))
        phrases = NOT_AFFILIATED.get(lang)
        if phrases is None:
            found.append(Finding("i18n-positioning", "error",
                                 f"{lang} has no independence phrasing registered in "
                                 f"NOT_AFFILIATED — add one before shipping the language"))
        elif not any(p in blob for p in phrases):
            found.append(Finding("i18n-positioning", "error",
                                 f"{lang} never states independence from Plaud "
                                 f"(expected one of: {', '.join(phrases)})"))
    return found



# ---------------------------------------------------------------------------
# Dead selectors (#16 fallout)
#
# Deleting the install tabs left the CSS and the JS behind, so
# `document.querySelector('[role="tablist"]')` returned null and the next line
# threw. Every existing check passed: they grep the source, and the source still
# contained the string. Structure intact, behaviour broken.
# ---------------------------------------------------------------------------

# Only these shapes are decidable by string matching. A compound or pseudo
# selector is not, and guessing would raise false alarms — which is how a rule
# gets ignored.
_ID = re.compile(r"^#([A-Za-z][\w-]*)$")
_CLASS = re.compile(r"^\.([A-Za-z][\w-]*)$")
_ATTR = re.compile(r'^\[([a-zA-Z-]+)=["\']([^"\']+)["\']\]$')


def check_dead_selectors(source: str) -> list[Finding]:
    """Every selector the page queries must match something the page renders."""
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", source, re.DOTALL)
    if not scripts:
        return []
    markup = re.sub(r"<script[^>]*>.*?</script>", " ", source, flags=re.DOTALL)

    found = []
    for js in scripts:
        # Back-reference the opening quote instead of excluding both kinds: a
        # selector is routinely `'[role="tab"]'`, and a class that rejects both
        # quote characters truncates at the first inner one — capturing `[role=`
        # and quietly checking nothing.
        for _q, raw in re.findall(r"querySelector(?:All)?\(\s*(['\"])(.*?)\1", js):
            sel = raw.strip()
            if m := _ID.match(sel):
                hit = re.search(rf'id=["\']{re.escape(m.group(1))}["\']', markup)
            elif m := _CLASS.match(sel):
                hit = re.search(rf'class=["\'][^"\']*\b{re.escape(m.group(1))}\b', markup)
            elif m := _ATTR.match(sel):
                hit = re.search(rf'{re.escape(m.group(1))}=["\']{re.escape(m.group(2))}["\']', markup)
            else:
                continue  # not decidable by string matching — say nothing
            if not hit:
                found.append(Finding("dead-selector", "error",
                                     f"the page queries {sel!r} but nothing in the markup "
                                     f"matches it — this throws at runtime"))
    return found

if __name__ == "__main__":
    sys.exit(main())
