#!/usr/bin/env python3
"""Fetch ONE recording's transcript through the Plaud CLI and land it in the cache.

    python3 fetch_one.py --id <id> [--force] [--name N] [--created-at T] [--duration D]

Why this exists (#65): `plaud-to-srt` runs on the cache. Someone who wants one
recording's subtitles should not have to sync a date range first. This is the
one-recording fetch: the raw transcript and its polished twin.

CLI path only, on purpose. The MCP tools can only be called by the model, so a
script cannot drive them; the skill covers that path in prose and says what it
costs. The CLI path never puts the transcript through the model.

**Pass only `--id`.** The name, creation time and duration are read from
`plaud file <id>`. They are text from Plaud, and a recording's name can contain
anything — quotes, `$(...)`, backticks — so they must not be pasted into a shell
command line by whoever drives this script. The three flags exist as overrides.

Exit codes are a contract — the skill branches on them:

    0  cached, or already cached (complete, file present) and nothing fetched
    1  an unexpected error: a bug, not a state the skill should handle
    2  bad arguments, including an id that is not a plain recording id
    3  `plaud` is not on PATH               -> the skill falls back to the MCP
    4  nothing usable came back: the CLI answered with nothing, failed, or hung
       past PLAUD_FETCH_TIMEOUT seconds (default 300)   -> nothing was cached
    5  the CLI is not logged in             -> tell the user to run `plaud login`

Nothing is written to the cache until a non-empty transcript is in hand, so a
failed fetch — including a failed `--force` refresh — leaves whatever was cached
before exactly as it was. Everything is written through `cache.py put`, which
stays the cache's one writer, with a single exception: when a refresh replaces
the raw transcript but no new polished version can be fetched, an older polished
copy is removed, because `to_srt` prefers a polish file whenever one exists and
would otherwise pair stale text with the new transcript.

The record is written with `--single-fetch`: it was not reached by walking the
listing, so it must not move where an incremental `plaud-sync` may stop paging.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cache  # noqa: E402  (same directory; reuse its helpers, do not copy them)

EXIT_BAD_ARGS = 2
EXIT_NO_CLI = 3
EXIT_NOTHING = 4
EXIT_NOT_LOGGED_IN = 5

TIMEOUT = float(os.environ.get("PLAUD_FETCH_TIMEOUT", "300"))

# A recording id is `of_` and hex in practice. Held to a plain token that cannot
# start with `-`: it reaches the CLI's option parser, and `-o` or `--help` must
# not be readable as an option there.
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_DETAIL = re.compile(r"^\s*(name|created_at|duration):\s*(.*?)\s*$")
_DURATION = re.compile(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")


def _run(cmd: list[str], **kw) -> tuple[int, str, str]:
    """(returncode, stdout, stderr). A hang or a spawn failure is reported as a
    return code of -1 with the reason in stderr, never raised: an exception here
    would skip the cleanup that the caller's failure path owes the cache."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, **kw)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {TIMEOUT:g}s"
    except (OSError, UnicodeDecodeError) as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def _plaud_transcript(rec_id: str, *, polished: bool) -> tuple[int, str, str]:
    """`plaud transcript` into a temp file, as `plaud-sync` does. Returns
    (returncode, body, diagnostics)."""
    with tempfile.TemporaryDirectory(prefix="plaud-fetch-one-") as tmp:
        out = pathlib.Path(tmp) / "transcript.txt"
        cmd = ["plaud", "transcript", rec_id, *(["--polished"] if polished else []), "-o", str(out)]
        rc, so, se = _run(cmd)
        try:
            body = out.read_text(encoding="utf-8") if out.is_file() else ""
        except UnicodeDecodeError as exc:
            return -1, "", f"the CLI wrote something that is not UTF-8 text: {exc}"
        return rc, body, (se + so).strip()


def _duration_ms(text: str) -> str:
    """'1h02m03s' / '10m42s' / '45s' -> milliseconds as a string, '' if unreadable."""
    m = _DURATION.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return ""
    h, mi, s = (int(g or 0) for g in m.groups())
    return str((h * 3600 + mi * 60 + s) * 1000)


def _plaud_file(rec_id: str) -> tuple[int, dict, str]:
    """name / created_at / duration (ms) from `plaud file <id>`, plus diagnostics."""
    rc, so, se = _run(["plaud", "file", rec_id])
    found: dict = {}
    for line in _ANSI.sub("", so).splitlines():
        m = _DETAIL.match(line)
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2)
    if "duration" in found:
        found["duration"] = _duration_ms(found["duration"])
    return rc, found, (se + so).strip()


def _put(rec_id: str, body: str, *args: str) -> tuple[int, str]:
    rc, _so, se = _run([sys.executable, str(HERE / "cache.py"), "put", "--id", rec_id, *args],
                       input=body)
    return rc, se.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="the recording id")
    ap.add_argument("--name", default="", help="override; normally read from `plaud file`")
    ap.add_argument("--created-at", default="", dest="created_at", help="override")
    ap.add_argument("--duration", default="", help="override")
    ap.add_argument("--force", action="store_true",
                    help="fetch again even if a complete copy is already cached")
    args = ap.parse_args()

    rec_id = args.id
    if not ID_RE.fullmatch(rec_id):
        print(f"refusing id {rec_id!r}: a recording id is letters, digits, '.', '_' and '-', "
              f"and does not start with '-'.", file=sys.stderr)
        return EXIT_BAD_ARGS

    existing = cache._load_manifest()["recordings"].get(rec_id)
    if not args.force and existing and cache._is_complete(existing):
        # The manifest alone is not proof: it can outlive the file it describes
        # (a hand-cleared cache, an interrupted write). No file, no copy.
        have = cache.CACHE_DIR / f"{rec_id}.md"
        if have.is_file() and have.stat().st_size > 0:
            print(f"{rec_id}: already cached and complete — nothing fetched "
                  f"(--force fetches it again)")
            return 0

    if shutil.which("plaud") is None:
        print("plaud CLI not found on PATH — fetch this recording through the MCP "
              "(get_transcript) instead. `npm install -g @plaud-ai/cli` installs the CLI.",
              file=sys.stderr)
        return EXIT_NO_CLI

    meta = {"name": args.name, "created_at": args.created_at, "duration": args.duration}
    if not all(meta.values()):
        frc, found, fdiag = _plaud_file(rec_id)
        if "AUTH_FAILED" in fdiag:
            print("the plaud CLI is not logged in — run `plaud login`. The CLI keeps its own "
                  "login, separate from the MCP's.", file=sys.stderr)
            return EXIT_NOT_LOGGED_IN
        if frc != 0:
            print(f"warning: could not read this recording's details ({fdiag or 'no output'}) — "
                  f"it is cached without a name unless an earlier copy had one.", file=sys.stderr)
        prior = {"name": (existing or {}).get("name", ""),
                 "created_at": (existing or {}).get("created_at", ""),
                 "duration": (existing or {}).get("duration_ms", "")}
        for key in meta:
            meta[key] = meta[key] or found.get(key, "") or prior[key]

    rc, raw, diag = _plaud_transcript(rec_id, polished=False)
    if "AUTH_FAILED" in diag:
        print("the plaud CLI is not logged in — run `plaud login`. The CLI keeps its own "
              "login, separate from the MCP's.", file=sys.stderr)
        return EXIT_NOT_LOGGED_IN
    if rc != 0 or not raw.strip():
        print(f"the plaud CLI returned no transcript for {rec_id} "
              f"(exit {rc}): {diag or 'empty output'}", file=sys.stderr)
        return EXIT_NOTHING

    prc, perr = _put(rec_id, raw, "--name", meta["name"], "--created-at", meta["created_at"],
                     "--duration", meta["duration"], "--complete", "true", "--pages", "1",
                     "--last-cursor", "", "--single-fetch")
    if prc != 0:
        print(perr or "cache.py put refused the transcript", file=sys.stderr)
        return EXIT_NOTHING

    rc2, polished, pdiag = _plaud_transcript(rec_id, polished=True)
    if rc2 == 0 and polished.strip():
        rc3, perr2 = _put(rec_id, polished, "--kind", "polish")
        if rc3 == 0:
            print(f"cached {rec_id}: raw transcript and polished version")
            return 0
        pdiag = perr2

    # `to_srt` prefers a polish file whenever one EXISTS. If this was a refresh, the
    # raw transcript was just replaced, so an older polish no longer matches it and
    # would put stale text on screen with no error. It is derived data in our own
    # cache, so remove it and let subtitles fall back to the raw transcript.
    stale = cache.CACHE_DIR / "polish" / f"{rec_id}.md"
    removed = ""
    if stale.is_file():
        stale.unlink()
        removed = " The older polished copy was removed because it no longer matched."
    print(f"warning: no polished version cached for {rec_id} ({pdiag or 'empty'}) — "
          f"subtitles will come from the raw transcript.{removed}", file=sys.stderr)
    print(f"cached {rec_id}: raw transcript only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
