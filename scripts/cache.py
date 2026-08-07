#!/usr/bin/env python3
"""Local transcript cache for plaud-mcp-connector.

The official Plaud MCP matches `query` against recording NAMES only, over the
newest 500 recordings. This cache lands transcript BODIES on disk so they can be
searched in full, with no embedding service and no extra dependencies.

Subcommands
    status            what is already cached (so indexing only fetches new ids)
    put               write one recording's transcript (body on stdin)
    search <pattern>  full-text search across cached transcripts
    show <id>         print one cached transcript
    should-stop-paging  may an incremental listing stop here? (page on stdin)

Cache lives in $PLAUD_CACHE_DIR, default ~/.plaud-connector/cache.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

# How far back a listing keeps paging past everything already cached. A
# recording can reach the cloud later than the one before it, so "newer than
# the newest we hold" is not the same as "not yet seen". The two costs are not
# comparable: one extra page is one API call, while a missed recording is
# never reported, never counted, and never findable again — so this is a day
# rather than an hour, and it is subtracted, never added.
LIST_SAFETY_MARGIN = timedelta(days=1)

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


def _parse_api_time(raw) -> datetime | None:
    """One `created_at` as the API wrote it, or None if it cannot be read.

    Every timestamp compared here comes from `list_files` on both sides — the
    one stored in the manifest and the one on the page being walked. That is
    the whole reason no timezone handling appears in this module: the API's
    timestamps carry no offset, and the moment one side of the comparison is a
    local clock instead, a UTC+8 reader silently shifts every recording eight
    hours older. The official CLI's `plaud recent` does exactly that
    (`new Date(created_at)` against `Date.now()`), which is the defect this
    code exists to not inherit.

    A timestamp with no offset is read as **UTC**, not as local time. That is
    measured rather than assumed: one recording's `start_at` reads
    `...T02:01:34` while the name Plaud generated for it reads `10:01:34`, an
    eight-hour offset on a UTC+8 machine. Reading it as local time instead is
    the official CLI's bug — and leaving it naive would make a `Z`-suffixed
    timestamp incomparable with a bare one, since Python refuses to order
    offset-aware against offset-naive and the run would die rather than answer.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        when = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        # TypeError as well as ValueError: this function's whole promise is
        # "None if it cannot be read", and an exception escaping it would take
        # down an indexing run over one bad manifest entry. The isinstance
        # check above covers the known route in; this is the backstop for the
        # ones nobody thought of.
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def list_cutoff(man: dict) -> str | None:
    """Where a fresh listing may stop paging, or None when it may not.

    None is the honest answer for a first index and for a cache whose every
    entry is half-fetched: there is no point to stop at, so the caller walks
    the whole library. Returning some default timestamp instead would turn a
    first index into "the most recent page only", which looks like success.

    Only `complete` records count. A half-fetched one is the weakest possible
    evidence that everything up to its date was listed, and excluding it moves
    the cutoff older — which pages more, at a cost of one page.
    """
    times = [
        t for rec in man.get("recordings", {}).values()
        if _is_complete(rec) and (t := _parse_api_time(rec.get("created_at"))) is not None
    ]
    if not times:
        return None
    # Emitted without the offset, matching the shape `list_files` sends. The
    # cutoff crosses a shell boundary as a string and comes back through
    # `--cutoff`, so its shape is part of the contract — and anything read back
    # in with no offset is treated as UTC, which is what it is.
    edge = (max(times) - LIST_SAFETY_MARGIN).astimezone(timezone.utc)
    return edge.replace(tzinfo=None).isoformat()


def page_verdict(dates: list, cutoff: str | None) -> tuple:
    """Does this page of `list_files` end the walk? Returns (stop, reason).

    Three separate ways to answer "keep going", each covering a case where
    stopping would be wrong in a way nobody would notice:

    - **An empty page.** `all([])` is True, so the naive test reads no results
      as "everything here is old".
    - **A page that is not sorted newest-first.** The early exit only works
      because `list_files` returns descending `created_at`. That is observed,
      not promised — if it ever sorted by `start_at` (a recording's own time
      rather than its upload time) the walk would stop short in silence.
    - **A timestamp that will not parse.** Unknown is not old.

    Every uncertain case resolves to "keep paging": slower, and correct.
    """
    if cutoff is None:
        return False, "continue: no cutoff — nothing cached to stop at, walk the whole library"
    if not dates:
        return False, "continue: empty page — no entries to judge, so nothing here says stop"

    parsed = []
    for raw in dates:
        when = _parse_api_time(raw)
        if when is None:
            return False, f"continue: cannot read the timestamp {raw!r} — unknown is not old"
        parsed.append(when)

    if any(a < b for a, b in zip(parsed, parsed[1:])):
        return False, ("continue: this page is not descending — list_files is expected to "
                       "return newest-first, and an early exit is only safe if it does")

    edge = _parse_api_time(cutoff)
    if edge is None:
        return False, f"continue: cannot read the cutoff {cutoff!r}"

    newer = [raw for raw, when in zip(dates, parsed) if when >= edge]
    if newer:
        return False, f"continue: {newer[0]} is not older than {cutoff}"
    return True, f"stop: all {len(dates)} entries on this page are older than {cutoff}"


def cmd_status(args) -> None:
    man = _load_manifest()
    recs = man["recordings"]
    # getattr, not args.list_cutoff: cmd_status has callers that build a
    # Namespace by hand and predate this flag (the test helpers do, and so
    # would any older caller). Reading the attribute directly would turn an
    # unrelated status call into an AttributeError.
    if getattr(args, "list_cutoff", False):
        if getattr(args, "ids_only", False):
            sys.exit("error: --ids-only and --list-cutoff answer different questions "
                     "— pass one or the other, not both")
        cutoff = list_cutoff(man)
        if cutoff is None:
            # Exit 3, not an empty line: "nothing to stop at" has to be
            # distinguishable from "stop at the start of time", and a caller
            # reading stdout cannot tell those apart.
            sys.exit(3)
        print(cutoff)
        return
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
        print(f"incomplete: {len(incomplete)} (will be re-fetched on the next plaud-index run)")
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
    if kind in ("summary", "polish"):
        man = _load_manifest()
        if rec_id not in man["recordings"]:
            sys.exit(f"error: {rec_id} has no cached transcript — refusing to write an "
                     f"orphan {kind} the manifest would never mention. Index it first.")
        subdir, flag = ("summaries", "has_summary") if kind == "summary" else ("polish", "has_polish")
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
SEARCH_EXCLUDED_DIRS = {"polish"}


def _hit_source(path: pathlib.Path) -> str:
    """What kind of text a hit came from: "corrected", "summary", or "" for the
    transcript. Empty string means "what was actually said" — the only case that
    needs no caveat."""
    for part in path.parts:
        if part in HIT_SOURCES:
            return HIT_SOURCES[part]
    return ""


def cmd_search(args) -> None:
    if not CACHE_DIR.exists() or not any(CACHE_DIR.glob("*.md")):
        sys.exit("error: cache is empty — run the plaud-index skill first")

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
        return

    ordered = sorted(
        hits.items(), key=lambda kv: man.get(kv[0], {}).get("created_at", ""), reverse=True
    )
    print(f"{sum(len(v) for v in hits.values())} matches in {len(hits)} recordings\n")
    for rec_id, lines in ordered:
        meta = man.get(rec_id, {})
        print(f"── {meta.get('name') or '(unnamed)'}")
        print(f"   id {rec_id}  ·  {(meta.get('created_at') or '?')[:10]}  ·  {len(lines)} hit(s)")
        # Only for records the manifest knows about and marks incomplete. An .md
        # with no manifest entry at all is a different problem (lost manifest, not
        # a half-fetched transcript) and already shows as "(unnamed)" above —
        # labelling it "partially indexed" would point at the wrong cause.
        if rec_id in man and man[rec_id].get("complete") is False:
            print("   ⚠ partially indexed — more transcript may exist; re-run plaud-index")
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


def cmd_should_stop_paging(args) -> None:
    """Answer for one page of `list_files`, read from stdin, one date per line.

    A page is a list, so it arrives on stdin rather than as arguments. The
    verdict is a word on stdout rather than an exit code because the reason
    matters as much as the answer — a run that stopped early should be able to
    say why, and a run that kept going should be able to say what it saw.
    """
    # Blank lines are kept, not filtered. A page entry whose `created_at` is
    # empty arrives as a blank line, and dropping it would shorten the page —
    # turning "one entry we cannot read" into "an entry that was never there",
    # which is exactly how a page becomes wrongly all-old. An unreadable entry
    # must reach page_verdict so it can say "continue".
    dates = [ln.strip() for ln in sys.stdin.read().splitlines()]
    stop, reason = page_verdict(dates, args.cutoff)
    print(reason)
    # The answer is carried by BOTH the word and the exit code. Anyone wiring
    # this into a shell reaches for `if cmd; then` — that is the idiom — and
    # with exit 0 on both branches it would read as "stop" every time, which is
    # the single failure this whole change exists to prevent. Exit 3 rather
    # than 1 for "keep paging": 1 is what a crash looks like, and continuing to
    # page is not a failure.
    if not stop:
        sys.exit(3)


def cmd_show(args) -> None:
    path = CACHE_DIR / f"{_safe_id(args.id)}.md"
    if not path.exists():
        sys.exit(f"error: {args.id} is not cached — run the plaud-index skill")
    sys.stdout.write(path.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="show what is cached")
    p.add_argument("--ids-only", action="store_true", help="print cached ids, one per line")
    p.add_argument("--list-cutoff", action="store_true", dest="list_cutoff",
                   help="print the timestamp a fresh listing may stop paging at, safety "
                        "margin already applied; exit 3 when there is nothing to stop at "
                        "(first index) and the whole library must be walked")
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
    p.add_argument("--kind", choices=["transcript", "summary", "polish"], default="transcript",
                   help="summary writes to summaries/ (searchable, labelled); polish writes "
                        "to polish/ (NOT searchable — it is the same speech reworded); both "
                        "leave the transcript, chars, and completeness untouched")
    p.add_argument("--last-cursor", dest="last_cursor", default=None,
                   help="the last next_cursor seen, verbatim, even when it looked empty — "
                        "lets this tool check the --complete claim and resume later")
    p.set_defaults(func=cmd_put)

    p = sub.add_parser("should-stop-paging",
                       help="does this page of list_files end the walk? page on stdin, "
                            "one created_at per line")
    p.add_argument("--cutoff", required=True,
                   help="the value from `status --list-cutoff`")
    p.set_defaults(func=cmd_should_stop_paging)

    p = sub.add_parser("search", help="full-text search cached transcripts")
    p.add_argument("pattern")
    p.add_argument("--case-sensitive", action="store_true")
    p.add_argument("--max-lines", type=int, default=5, help="context lines per recording")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="print one cached transcript")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
