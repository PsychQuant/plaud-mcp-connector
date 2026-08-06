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


def classify_domain(domain: str) -> tuple[str, str]:
    """Rule 3: the domain must not read as official. Returns (verdict, reason).

    "block" for names that are unambiguously Plaud's own shape, "warn" for names
    that merely contain the word — whether those read as official is a judgement
    a string comparison has no business making, so it is handed back to a human.
    """
    host = re.sub(r"^[a-z]+://", "", domain.strip().lower()).split("/")[0]
    if not host:
        return "ok", "no custom domain — Vercel's own hostname is used"

    labels = [re.sub(r"[^a-z0-9]", "", part) for part in host.split(".")]
    if any(label in BLOCKED_LABELS for label in labels):
        return "block", (f"{host} reads as Plaud's own domain "
                         f"(site/README.md rule 3 names plaud-mcp.com as the example)")
    if any("plaud" in label for label in labels):
        return "warn", (f"{host} contains 'plaud' — check it cannot be mistaken for official "
                        f"before deploying; a string comparison cannot decide this")
    return "ok", f"{host} does not resemble plaud.ai"


def run_checks(site_dir: pathlib.Path, readme_path: pathlib.Path,
               domain: str = "") -> list[Finding]:
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

    verdict, reason = classify_domain(domain)
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
    args = ap.parse_args()

    findings = run_checks(pathlib.Path(args.site), pathlib.Path(args.readme), args.domain)
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


if __name__ == "__main__":
    sys.exit(main())
