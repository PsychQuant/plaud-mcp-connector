#!/usr/bin/env python3
"""Which closed issues are missing a closing summary — and which only look like it.

IDD's `--audit-closes` marks a closed issue as missing its summary when no
comment *starts with* the literal `## Closing Summary`. That is the documented
contract, and on this repo it was right about 32 issues and wrong about 11:

    ## Closing summary   ×10   lowercase s — the summary is there
    summary mid-comment  × 1   posted inside the Implementation Complete body

A guard wrong a quarter of the time gets ignored, and the ignoring is the
damage: the eleven false alarms hide any twelfth that is real. Worse, IDD's
`/idd-close --retroactive` uses the same marker as its precondition, so acting
on those alarms would have posted ten duplicate summaries.

So this splits the verdict four ways instead of two. Only `MISSING` means work
is owed; `CASING` and `MID_COMMENT` mean the content exists and the *marker*
does not match, which is a different repair with a different cost.

Classification is a pure function over comment bodies — no network — so the
four cases are unit-testable. `gh` only appears in `main`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from enum import Enum

CANONICAL = "## Closing Summary"
# Any casing, at the start of some line. `\s*` after `##` because `##Closing`
# happens; `[ \t]*` before it because a summary indented under a list item is
# still a summary.
#
# No `\b` after `summary`. The first draft had one, an acid run showed no test
# covered it, and looking at what it actually did: `_` is a word character, so
# `## Closing Summary_v2` — a plausible way to head a revised summary — failed
# to match and the issue read as MISSING. The boundary was not merely untested,
# it was wrong in the direction that matters.
ANY_HEADING = re.compile(r"(?mi)^[ \t]*##\s*closing\s+summary")


class Verdict(str, Enum):
    OWN_COMMENT = "own-comment"   # conforming: its own comment, canonical casing
    CASING = "casing"             # own comment, heading cased differently
    MID_COMMENT = "mid-comment"   # heading present, but not at the comment's start
    MISSING = "missing"           # no closing summary anywhere


def classify(comment_bodies: list[str]) -> Verdict:
    """The verdict for one closed issue, given every comment body on it.

    Order matters and is not arbitrary: an issue can hold several comments,
    and the best one wins. A repo that has already normalised one summary
    should not still be reported for an older non-conforming one.
    """
    if any(b.startswith(CANONICAL) for b in comment_bodies):
        return Verdict.OWN_COMMENT
    for b in comment_bodies:
        head = b.split("\n", 1)[0]
        if ANY_HEADING.match(head):
            return Verdict.CASING
    if any(ANY_HEADING.search(b) for b in comment_bodies):
        return Verdict.MID_COMMENT
    return Verdict.MISSING


def fetch(repo: str, limit: int) -> list[dict]:
    out = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "closed",
         "--limit", str(limit), "--json", "number,title,comments"],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--only", choices=[v.value for v in Verdict],
                    help="print just this class, one issue number per line")
    args = ap.parse_args()

    rows = fetch(args.repo, args.limit)
    if not rows:
        # An empty scan and a clean repo look identical. Say which this is.
        print(f"no closed issues returned for {args.repo} — nothing scanned, "
              f"which is not the same as nothing wrong", file=sys.stderr)
        return 2

    buckets: dict[Verdict, list[dict]] = {v: [] for v in Verdict}
    for r in rows:
        buckets[classify([c["body"] for c in r["comments"]])].append(r)

    if args.only:
        for r in sorted(buckets[Verdict(args.only)], key=lambda x: -x["number"]):
            print(r["number"])
        return 0

    print(f"scanned {len(rows)} closed issue(s) in {args.repo}\n")
    for v, note in [
        (Verdict.OWN_COMMENT, "conforming"),
        (Verdict.CASING, "summary present — heading cased differently"),
        (Verdict.MID_COMMENT, "summary present — not at the comment's start"),
        (Verdict.MISSING, "no closing summary — the only class that owes work"),
    ]:
        rs = buckets[v]
        print(f"{v.value:<12} {len(rs):>3}   {note}")
        for r in sorted(rs, key=lambda x: -x["number"]):
            if v is not Verdict.OWN_COMMENT:
                print(f"                   #{r['number']}  {r['title'][:56]}")
    return 1 if buckets[Verdict.MISSING] else 0


if __name__ == "__main__":
    sys.exit(main())
