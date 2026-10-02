#!/usr/bin/env python3
"""Fetch ONE recording's transcript through the Plaud CLI and land it in the cache.

    python3 fetch_one.py --id <id> [--name N] [--created-at T] [--duration D] [--force]

Why this exists (#65): `plaud-to-srt` runs on the cache. Someone who wants one
recording's subtitles should not have to sync a date range first. This is the
one-recording fetch: the raw transcript and its polished twin, written through
`cache.py put` so there is still exactly one writer of the cache.

CLI path only, on purpose. The MCP tools can only be called by the model, so a
script cannot drive them; the skill covers that path in prose and says what it
costs. The CLI path never puts the transcript through the model.

Exit codes are a contract — the skill branches on them:

    0  cached, or already cached and complete (nothing fetched)
    3  `plaud` is not on PATH               -> the skill falls back to the MCP
    4  the CLI answered but gave nothing    -> nothing was cached
    5  the CLI is not logged in             -> tell the user to run `plaud login`

Nothing is written to the cache until a non-empty transcript is in hand, so a
failed fetch — including a failed `--force` refresh — leaves whatever was cached
before exactly as it was.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cache  # noqa: E402  (same directory; reuse its manifest helpers, do not copy them)

EXIT_NO_CLI = 3
EXIT_NOTHING = 4
EXIT_NOT_LOGGED_IN = 5


def _plaud_transcript(rec_id: str, *, polished: bool) -> tuple[int, str, str]:
    """Run `plaud transcript` into a temp file. Returns (returncode, body, diagnostics).

    Output goes to a file rather than a pipe, exactly as `plaud-sync` does it, so
    the text is the CLI's own `-o` output.
    """
    with tempfile.TemporaryDirectory(prefix="plaud-fetch-one-") as tmp:
        out = pathlib.Path(tmp) / "transcript.txt"
        cmd = ["plaud", "transcript", rec_id, *(["--polished"] if polished else []), "-o", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        body = out.read_text(encoding="utf-8") if out.is_file() else ""
        return proc.returncode, body, (proc.stderr + proc.stdout).strip()


def _put(rec_id: str, body: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE / "cache.py"), "put", "--id", rec_id, *args],
        input=body, capture_output=True, text=True, timeout=60)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="the recording id")
    ap.add_argument("--name", default="")
    ap.add_argument("--created-at", default="", dest="created_at")
    ap.add_argument("--duration", default="")
    ap.add_argument("--force", action="store_true",
                    help="fetch again even if a complete copy is already cached")
    args = ap.parse_args()

    rec_id = cache._safe_id(args.id)

    if not args.force:
        rec = cache._load_manifest()["recordings"].get(rec_id)
        if rec and cache._is_complete(rec):
            print(f"{rec_id}: already cached and complete — nothing fetched "
                  f"(--force fetches it again)")
            return 0

    if shutil.which("plaud") is None:
        print("plaud CLI not found on PATH — fetch this recording through the MCP "
              "(get_transcript) instead. `npm install -g @plaud-ai/cli` installs the CLI.",
              file=sys.stderr)
        return EXIT_NO_CLI

    rc, raw, diag = _plaud_transcript(rec_id, polished=False)
    if "AUTH_FAILED" in diag:
        print("the plaud CLI is not logged in — run `plaud login`. The CLI keeps its own "
              "login, separate from the MCP's.", file=sys.stderr)
        return EXIT_NOT_LOGGED_IN
    if rc != 0 or not raw.strip():
        print(f"the plaud CLI returned no transcript for {rec_id} "
              f"(exit {rc}): {diag or 'empty output'}", file=sys.stderr)
        return EXIT_NOTHING

    put = _put(rec_id, raw, "--name", args.name, "--created-at", args.created_at,
               "--duration", args.duration, "--complete", "true", "--pages", "1",
               "--last-cursor", "")
    if put.returncode != 0:
        print(put.stderr.strip() or "cache.py put refused the transcript", file=sys.stderr)
        return EXIT_NOTHING

    prc, polished, pdiag = _plaud_transcript(rec_id, polished=True)
    if prc == 0 and polished.strip():
        pput = _put(rec_id, polished, "--kind", "polish")
        if pput.returncode == 0:
            print(f"cached {rec_id}: raw transcript and polished version")
            return 0
        pdiag = pput.stderr.strip()
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
