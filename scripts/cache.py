#!/usr/bin/env python3
"""Local transcript cache for plaud-mcp-connector.

The official Plaud MCP matches `query` against recording NAMES only, over the
newest 500 recordings. This cache lands transcript BODIES on disk so they can be
searched in full, with no embedding service and no extra dependencies.

Subcommands
    status            what is already cached (so a download can skip whole recordings)
    put               write one recording's transcript (body on stdin)
    search <pattern>  full-text search across cached transcripts
    show <id>         print one cached transcript
    find              resolve a recording by NAME (not full text) -> id, date, state

Cache lives in $PLAUD_CACHE_DIR, default ~/.plaud-connector/cache.

## The line format a producer must write (#40)

`put` takes whatever is on stdin. It does not parse, validate, or normalise a
single line — deliberately, so any source can land content here. That makes
this the only place a producer is told what the shape has to be, and
`tests/test_cache_line_format.py` the only place it is enforced.

For `--kind transcript` and `--kind polish`, one segment per line. The ranged
form carries a real end time; the point form does not, and `to_srt` has to infer
one from where the next segment starts, which leaves the last segment with a
guess. Both are accepted; a producer that has end times should keep them.

Written as a grammar rather than as examples, because prose about field widths
turned out to be readable three incompatible ways at once (#50, twice).

**This grammar is what a producer MUST WRITE, not what the parser tolerates.**
The two differ on purpose: `to_srt` is lenient where strictness would cost
somebody's words. Without that sentence, a reader diffing this against
`SEGMENT` reads every mismatch as the defect #50 was.

    stamp   := ( HH ":" MM ":" SS | TOTALMIN ":" SS ) frac?    [1:02:03.500]
    HH      := 1-2 digits, literal hours
    TOTALMIN:= 1-4 digits, TOTAL minutes not minutes-within-an-hour  [446:12]
    MM, SS  := exactly 2 digits, 00-59
    frac    := ( "." | "," ) 1-3 digits
    line    := "[" stamp ( " - " stamp )? "]" ( speaker ":" )? text
    speaker := optional, 1-60 chars, no colon or bracket

The tolerances are enumerated in `TOLERATED` / `NOT_TOLERATED` in
`tests/test_cache_line_format.py`, not here: a prose list drifts from the parser
the way this paragraph's ancestors did, and a table the suite runs cannot.

Three things the grammar says and earlier prose did not. The bound is on
**magnitude, not digit count** — `[9999:99]` has four legal minute digits and
two legal seconds digits and is still malformed. It applies at **both ends of a
range**, and the leading field means different things in the two forms: four
digits of TOTALMIN is seven days, four digits of HH is 416. And a malformed
**end** costs the timing, not the line, while a malformed **start** costs the
line, because without one there is nowhere to put the words.

**An incomplete contract is not a smaller contract, it is a wrong one.** This
was written from short recordings, so `MM` looked like two digits and `to_srt`
was built to match, while the producer had been writing three for as long as
anyone recorded past 99 minutes. The parser was correct by these words and
silently dropped four fifths of a 7.4-hour transcript — 281 segments in, 57 cues
out — with continuous timecodes and no error (#50). A shape not written here is
that bug again, not a small omission.

**Closed on purpose**, and closed against measurement: both forms are ones a
shipped producer emits, measured 2026-08-09 (MCP path writes the point form,
CLI the ranged one). A third goes here only once something real emits it, never
because it seems reasonable (#40). Shapes the parser accepts but nothing has
been measured emitting are pinned separately, in `BOUND_PINS` in the contract
test.

`--kind outline` and `--kind summary` are **not covered**. Their line shapes
have not been measured, and writing down an unmeasured shape as if it were
known is the mistake #36 was about.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone

CACHE_DIR = pathlib.Path(
    os.environ.get("PLAUD_CACHE_DIR", pathlib.Path.home() / ".plaud-connector" / "cache")
)
MANIFEST = CACHE_DIR / "manifest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except json.JSONDecodeError:
            print(f"warn: {MANIFEST} is corrupt — starting a fresh manifest", file=sys.stderr)
    return {"version": "1", "recordings": {}}


def _save_manifest(data: dict) -> None:
    """Atomic: a crash mid-write must not corrupt the manifest."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, MANIFEST)


def _safe_id(rec_id: str) -> str:
    """Recording ids come from the API; never let one escape the cache dir."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", rec_id or ""):
        sys.exit(f"error: refusing unsafe recording id: {rec_id!r}")
    return rec_id


def _is_cursor_exhausted(raw) -> bool:
    """Is this `next_cursor` value the API's way of saying 'no more pages'?

    This exists because `complete` would otherwise rest entirely on a judgement
    the indexing skill makes at runtime — "does this cursor look empty?" — which
    no test can reach. Putting the rule here makes it a testable function with
    one definition, so a caller claiming `--complete true` can be checked rather
    than believed.

    Treated as exhausted: None, "", whitespace, and the literal strings "null" /
    "none" / "undefined" (JSON serialisers and string coercion produce these).
    Anything else is a real cursor, however odd it looks — "0" and "false" are
    valid opaque cursors and must NOT be read as terminators.
    """
    if raw is None:
        return True
    text = str(raw).strip()
    return text == "" or text.lower() in {"null", "none", "undefined"}


def normalize_pattern(pattern: str) -> str:
    """Make a search pattern reach text in either Unicode normal form.

    `café` has two canonically-equivalent encodings — NFC (`U+00E9`) and NFD
    (`U+0065 U+0301`). grep and ripgrep both compare bytes, so one form silently
    fails to find the other and the answer is "no match" on a transcript that
    plainly contains the word.

    The fix lives here, at the query end, rather than in a migration: a cache
    written before this existed is a mixed bag, and rewriting it would also shift
    every `chars` count in the manifest — a second problem introduced to solve
    the first. Expanding the query reaches old and new entries alike.

    Normalization only affects precomposed/decomposed characters, never ASCII, so
    the caller's regex metacharacters survive untouched. When both forms are
    identical (any pure-ASCII pattern, and CJK, which does not decompose here) the
    pattern is returned as-is — wrapping it would alter a user's regex for nothing.
    """
    nfc = unicodedata.normalize("NFC", pattern)
    nfd = unicodedata.normalize("NFD", pattern)
    if nfc == nfd:
        return pattern
    # Plain `(a|b)` rather than a non-capturing group: BSD grep -E has no `(?:`.
    return f"({nfc}|{nfd})"


def _is_complete(rec: dict) -> bool:
    """A record with no `complete` key was written before paging existed, so its
    transcript may stop at the first page. Absent means incomplete — the opposite
    default to `cmd_put`, where absence means an old caller we still trust."""
    return rec.get("complete", False) is True


def cmd_status(args) -> None:
    man = _load_manifest()
    recs = man["recordings"]
    incomplete = [k for k, v in recs.items() if not _is_complete(v)]
    if args.ids_only:
        # Only fully-fetched ids count as "already cached". Anything partial is
        # omitted so the indexer's diff picks it back up — that is the whole
        # re-fetch mechanism, no --rebuild flag needed.
        print("\n".join(sorted(k for k, v in recs.items() if _is_complete(v))))
        return
    print(f"cache dir : {CACHE_DIR}")
    print(f"cached    : {len(recs)} recordings")
    if incomplete:
        print(f"incomplete: {len(incomplete)} (a download that covers them fetches them again)")
    if recs:
        dates = sorted(r.get("created_at", "") for r in recs.values() if r.get("created_at"))
        if dates:
            print(f"range     : {dates[0][:10]} → {dates[-1][:10]}")
        total = sum(r.get("chars", 0) for r in recs.values())
        print(f"transcript: {total:,} characters indexed")
        summarised = sum(1 for r in recs.values() if r.get("has_summary"))
        if summarised:
            print(f"summaries : {summarised} of {len(recs)} recordings")
        polished = sum(1 for r in recs.values() if r.get("has_polish"))
        if polished:
            print(f"polished  : {polished} of {len(recs)} recordings (subtitles only)")


def cmd_put(args) -> None:
    rec_id = _safe_id(args.id)
    # Converge new writes on one normal form so the cache stops accumulating
    # entropy. This does not make normalize_pattern redundant — entries written
    # before this existed are still whatever the API sent.
    body = unicodedata.normalize("NFC", sys.stdin.read())
    if not body.strip():
        sys.exit(f"error: empty transcript body for {rec_id} — refusing to cache a blank entry")

    # getattr, not args.complete: callers that build an argparse.Namespace by hand
    # (the test helpers do) never go through parse_args, so argparse defaults are
    # absent on the object. Reading the attribute directly would AttributeError.
    #
    # Absence here means "an older caller that predates paging" → trust it as
    # complete. That is NOT the same as a manifest record with no `complete` key,
    # which means "written before paging existed" → treat as incomplete. Same word,
    # opposite defaults, on purpose.
    # A summary belongs to a recording; it is not a second recording. It goes to
    # summaries/, leaves the transcript alone, and must not move `chars` or
    # `complete` — those describe the transcript and would start meaning nothing
    # if a summary could change them.
    kind = str(getattr(args, "kind", "transcript") or "transcript")
    if kind in ("summary", "polish", "outline"):
        man = _load_manifest()
        if rec_id not in man["recordings"]:
            sys.exit(f"error: {rec_id} has no cached transcript — refusing to write an "
                     f"orphan {kind} the manifest would never mention. Index it first.")
        subdir, flag = {
            "summary": ("summaries", "has_summary"),
            "polish": ("polish", "has_polish"),
            "outline": ("outline", "has_outline"),
        }[kind]
        d = CACHE_DIR / subdir
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{rec_id}.md"
        path.write_text(body.rstrip("\n") + "\n")
        man["recordings"][rec_id][flag] = True
        man["recordings"][rec_id][f"{kind}_indexed_at"] = _now()
        _save_manifest(man)
        print(f"cached {kind} for {rec_id} ({len(body):,} chars) → {path}")
        return

    claimed = str(getattr(args, "complete", "true")).lower() == "true"
    pages = int(getattr(args, "pages", 1) or 1)
    last_cursor = getattr(args, "last_cursor", None)

    # Verify the completeness claim instead of taking it on faith. If the caller
    # says "done" but hands back a cursor that is still live, the loop stopped
    # early — downgrade rather than error, so the transcript we did fetch is kept
    # and the next index run picks it up.
    complete = claimed
    warning = ""
    if claimed and last_cursor is not None and not _is_cursor_exhausted(last_cursor):
        complete = False
        warning = (
            f"\n⚠ {rec_id}: caller claimed --complete true but --last-cursor "
            f"{last_cursor!r} is not exhausted — recorded as INCOMPLETE. "
            f"The fetch loop stopped early; the next index run will resume it."
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    front = [
        "---",
        f"id: {rec_id}",
        f'name: "{(args.name or "").replace(chr(34), chr(39))}"',
        f"created_at: {args.created_at or ''}",
        f"duration_ms: {args.duration or ''}",
        f"complete: {'true' if complete else 'false'}",
        f"pages: {pages}",
        f"indexed_at: {_now()}",
        "---",
        "",
    ]
    path = CACHE_DIR / f"{rec_id}.md"
    path.write_text("\n".join(front) + body.rstrip("\n") + "\n")

    man = _load_manifest()
    man["recordings"][rec_id] = {
        "name": args.name or "",
        "created_at": args.created_at or "",
        "duration_ms": args.duration or "",
        "chars": len(body),
        "complete": complete,
        "pages": pages,
        # Kept so an interrupted fetch can resume from where it stopped instead of
        # replaying every page. Without it a recording longer than the page cap can
        # never finish: each run restarts at page 1, hits the cap, and gives up.
        "last_cursor": None if complete else last_cursor,
        "indexed_at": _now(),
    }
    _save_manifest(man)
    state = "complete" if complete else "INCOMPLETE"
    print(f"cached {rec_id} ({len(body):,} chars, {pages} page(s), {state}) → {path}{warning}")


def _have_rg() -> bool:
    return subprocess.run(["which", "rg"], capture_output=True).returncode == 0


# Directory name → what a hit found there actually is. Anything else is the
# transcript itself, which needs no label.
HIT_SOURCES = {"proofread": "corrected", "summaries": "summary"}

# Subdirectories held back from search. `polish/` is the same speech said more
# tidily — including it would return every line twice, once raw and once
# cleaned. That is not extra reach, it is halved signal.
#
# The contrast with `summaries/` is what makes the rule coherent: a summary is
# NEW content, so searching it finds things findable nowhere else. A polish is a
# rewording, so a term that appears only there was never actually said, and
# surfacing it as a hit would misrepresent the recording.
#
# `outline/` sits on the polish side by that same rule. Its TEXT is mostly a
# rewording of what was said; the one genuinely new thing it carries is a
# timestamp — and a timestamp is not something grep finds.
#
# The timestamp is also why a fourth `[outline]` tag would not have been enough.
# `[summary]` carries no locator, so nobody cites it as "at 12:03 they said X".
# An outline line does carry one, and it means something different: not "this
# was spoken here" but "the section starting here is about this". Same syntax,
# different relation. Searching outlines needs that answered first (#28).
SEARCH_EXCLUDED_DIRS = {"polish", "outline"}


def _hit_source(path: pathlib.Path) -> str:
    """What kind of text a hit came from: "corrected", "summary", or "" for the
    transcript. Empty string means "what was actually said" — the only case that
    needs no caveat."""
    for part in path.parts:
        if part in HIT_SOURCES:
            return HIT_SOURCES[part]
    return ""


def _scope_lines(man: dict) -> str:
    """What a search covered, so "no match" cannot be read as "never said".

    The cache holds only what the user chose to download. A search over it is a
    search over that part, and a bare "no match" is a wrong answer that looks like a
    correct one. These lines go out on EVERY path — hit or miss — because the miss is
    where the difference matters."""
    dates = sorted(r.get("created_at", "")[:10] for r in man.values() if r.get("created_at"))
    span = f"{dates[0]} → {dates[-1]}" if dates else "dates unknown"
    partial = sum(1 for r in man.values() if r.get("complete") is False)
    extra = f", {partial} incomplete" if partial else ""
    return (f"searched {len(man)} cached recordings, covering {span}{extra}\n"
            "(the cache holds only what has been downloaded — not necessarily everything in Plaud)")


def cmd_search(args) -> None:
    if not CACHE_DIR.exists() or not any(CACHE_DIR.glob("*.md")):
        sys.exit("error: cache is empty — run the plaud-download skill first")

    man = _load_manifest()["recordings"]

    if _have_rg():
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if not args.case_sensitive:
            cmd.append("--ignore-case")
        cmd += [normalize_pattern(args.pattern), str(CACHE_DIR)]
    else:
        # grep -r is POSIX-ubiquitous; ripgrep is only a speed upgrade here.
        # -E is REQUIRED, not cosmetic: BSD grep (macOS) defaults to basic regex,
        # where `|` is a LITERAL character. Without it, `search "預算|budget"`
        # silently reports no match on a transcript containing both — a wrong
        # answer that looks like a correct one. -E gives ERE, matching ripgrep's
        # alternation semantics so results don't depend on which binary is present.
        cmd = ["grep", "-rnE"]
        if not args.case_sensitive:
            cmd.append("-i")
        cmd += ["--include=*.md", normalize_pattern(args.pattern), str(CACHE_DIR)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        sys.exit(f"error: search failed: {proc.stderr.strip()}")

    hits: dict = {}
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path = pathlib.Path(parts[0])
        rec_id = path.stem
        if rec_id == "manifest":
            continue
        if SEARCH_EXCLUDED_DIRS & set(path.parts):
            continue
        # Which file a hit came from changes what it means. proofread/ holds
        # corrected text and summaries/ holds AI-written prose; neither is what
        # anybody actually said. Marking the difference keeps a caller from
        # quoting either as verbatim speech — and the reader cannot tell by
        # looking, which is exactly why the label has to be on the line.
        #
        # An enum rather than a second boolean: the sources are mutually
        # exclusive, and two booleans would admit a state ("corrected AND
        # summary") that no file can be in.
        source = _hit_source(path)
        hits.setdefault(rec_id, []).append((parts[2].strip(), source))

    if not hits:
        print(f"no match for {args.pattern!r} across {len(man)} cached recordings")
        print(_scope_lines(man))
        return

    ordered = sorted(
        hits.items(), key=lambda kv: man.get(kv[0], {}).get("created_at", ""), reverse=True
    )
    print(f"{sum(len(v) for v in hits.values())} matches in {len(hits)} recordings")
    print(_scope_lines(man))
    print()
    for rec_id, lines in ordered:
        meta = man.get(rec_id, {})
        print(f"── {meta.get('name') or '(unnamed)'}")
        print(f"   id {rec_id}  ·  {(meta.get('created_at') or '?')[:10]}  ·  {len(lines)} hit(s)")
        # Only for records the manifest knows about and marks incomplete. An .md
        # with no manifest entry at all is a different problem (lost manifest, not
        # a half-fetched transcript) and already shows as "(unnamed)" above —
        # labelling it "partially indexed" would point at the wrong cause.
        if rec_id in man and man[rec_id].get("complete") is False:
            print("   ⚠ partially indexed — more transcript may exist; download this recording again")
        for ln, source in lines[: args.max_lines]:
            tag = f" [{source}]" if source else ""
            print(f"   │ {ln[:200]}{tag}")
        if len(lines) > args.max_lines:
            print(f"   │ … {len(lines) - args.max_lines} more")
        present = {src for _, src in lines if src}
        if "corrected" in present:
            print("   ℹ [corrected] lines come from a proofread copy — quote them as "
                  "corrected text, not as what was said verbatim")
        if "summary" in present:
            print("   ℹ [summary] lines come from an AI-written summary — nobody said "
                  "these words; open the transcript for the actual wording")
        print()


def _human_duration(raw) -> str:
    """'642000' (ms) -> '10 min'; anything else is shown as it was stored."""
    text = str(raw or "").strip()
    return f"{int(text) // 60000} min" if text.isdigit() else text


def cmd_find(args) -> None:
    """List cached recordings whose NAME contains the query, newest first.

    Names only. `search` is a full-text search in which the name is merely the
    heading above the hits, so a recording whose talk mentions the words becomes
    a "match" for a name it does not have — and a skill that resolves "the
    recording the user named" from it delivers the wrong one (verify R1).

    Exit 3 when nothing matches, the same convention `config.py get` uses for
    "absent", so a caller branches on it instead of parsing prose.
    """
    # NFC on both sides, as `put` does for transcript bodies: `Café` typed as `Cafe`
    # plus a combining accent is the same name and must match.
    nfc = lambda t: unicodedata.normalize("NFC", str(t)).casefold()  # noqa: E731
    needle = nfc((args.name or "").strip())
    if not needle:
        sys.exit("error: find needs a non-empty name — an empty query would match every recording")
    recs = _load_manifest().get("recordings", {})
    hits = sorted(((k, v) for k, v in recs.items() if needle in nfc(v.get("name", ""))),
                  key=lambda kv: str(kv[1].get("created_at", "")), reverse=True)
    if not hits:
        print(f"no cached recording is named like {args.name.strip()!r}", file=sys.stderr)
        sys.exit(3)
    print(f"{len(hits)} cached recording(s) named like {args.name.strip()!r}:")
    for rec_id, rec in hits:
        state = "complete" if _is_complete(rec) else "INCOMPLETE"
        when = str(rec.get("created_at", ""))[:10] or "undated"
        print(f"  {rec_id}  ·  {rec.get('name', '')}  ·  {when}  ·  "
              f"{_human_duration(rec.get('duration_ms'))}  ·  {state}")


def cmd_show(args) -> None:
    rec_id = _safe_id(args.id)
    kind = str(getattr(args, "kind", "transcript") or "transcript")
    subdir = {"summary": "summaries", "polish": "polish", "outline": "outline"}.get(kind)
    path = (CACHE_DIR / subdir / f"{rec_id}.md") if subdir else (CACHE_DIR / f"{rec_id}.md")
    if not path.exists():
        sys.exit(f"error: {args.id} has no cached {kind} — run the plaud-download skill")
    sys.stdout.write(path.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="show what is cached")
    p.add_argument("--ids-only", action="store_true", help="print cached ids, one per line")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("put", help="cache one transcript (body on stdin)")
    p.add_argument("--id", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--created-at", default="", dest="created_at")
    p.add_argument("--duration", default="")
    p.add_argument("--complete", choices=["true", "false"], default="true",
                   help="did the fetch loop run to the end of the transcript?")
    p.add_argument("--pages", type=int, default=1,
                   help="how many get_transcript pages were concatenated")
    p.add_argument("--kind", choices=["transcript", "summary", "polish", "outline"],
                   default="transcript",
                   help="summary writes to summaries/ (searchable, labelled); polish and "
                        "outline write to polish/ and outline/ (NOT searchable — a polish is "
                        "the same speech reworded, an outline is AI-written structure whose "
                        "timestamps are section starts, not citations); all three leave the "
                        "transcript, chars, and completeness untouched")
    p.add_argument("--last-cursor", dest="last_cursor", default=None,
                   help="the last next_cursor seen, verbatim, even when it looked empty — "
                        "lets this tool check the --complete claim and resume later")
    p.set_defaults(func=cmd_put)

    p = sub.add_parser("search", help="full-text search cached transcripts")
    p.add_argument("pattern")
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--max-lines", type=int, default=5, help="context lines per recording")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("find", help="resolve a recording by NAME; exits 3 when none matches")
    p.add_argument("name", help="a case-insensitive substring of the recording's name")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("show", help="print one cached transcript, summary, polish or outline")
    p.add_argument("id")
    p.add_argument("--kind", choices=["transcript", "summary", "polish", "outline"],
                   default="transcript",
                   help="which cached view to print; a cache written but never read "
                        "saves nothing, which is what #28 was about")
    p.set_defaults(func=cmd_show)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
