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

Cache lives in $PLAUD_CACHE_DIR, default ~/.plaud-connector/cache.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
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
        print(f"incomplete: {len(incomplete)} (will be re-fetched on the next plaud-index run)")
    if recs:
        dates = sorted(r.get("created_at", "") for r in recs.values() if r.get("created_at"))
        if dates:
            print(f"range     : {dates[0][:10]} → {dates[-1][:10]}")
        total = sum(r.get("chars", 0) for r in recs.values())
        print(f"transcript: {total:,} characters indexed")


def cmd_put(args) -> None:
    rec_id = _safe_id(args.id)
    body = sys.stdin.read()
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


def cmd_search(args) -> None:
    if not CACHE_DIR.exists() or not any(CACHE_DIR.glob("*.md")):
        sys.exit("error: cache is empty — run the plaud-index skill first")

    man = _load_manifest()["recordings"]

    if _have_rg():
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if not args.case_sensitive:
            cmd.append("--ignore-case")
        cmd += [args.pattern, str(CACHE_DIR)]
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
        cmd += ["--include=*.md", args.pattern, str(CACHE_DIR)]

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
        # A hit inside proofread/ is corrected text, not what the recording
        # literally contains. Marking it keeps a caller from quoting it as
        # verbatim speech — the whole point of proofreading is that the two
        # differ, so the difference has to stay visible.
        corrected = "proofread" in path.parts
        hits.setdefault(rec_id, []).append((parts[2].strip(), corrected))

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
        for ln, corrected in lines[: args.max_lines]:
            tag = " [corrected]" if corrected else ""
            print(f"   │ {ln[:200]}{tag}")
        if len(lines) > args.max_lines:
            print(f"   │ … {len(lines) - args.max_lines} more")
        if any(c for _, c in lines):
            print("   ℹ [corrected] lines come from a proofread copy — quote them as "
                  "corrected text, not as what was said verbatim")
        print()


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
    p.add_argument("--last-cursor", dest="last_cursor", default=None,
                   help="the last next_cursor seen, verbatim, even when it looked empty — "
                        "lets this tool check the --complete claim and resume later")
    p.set_defaults(func=cmd_put)

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
