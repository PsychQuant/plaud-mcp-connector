#!/usr/bin/env python3
"""The #57 mutation ledger: the changes to `scripts/to_srt.py` that
`TestSegmentMatchingHasOneEntryPoint` (and `TestThereIsOnlyOneParse`) must
turn red, and the one that must stay green.

Not a test module — `unittest discover` only collects `test*.py` — and it
refuses to run on this repository: it edits `scripts/to_srt.py`, so it takes
a COPY of the tree (`rsync -a --exclude .git <repo>/ <copy>/`) and mutates
that. Round 7 found the ledger existing only in the implementer's scratch
directory, so a reader could neither re-run it nor see which mutants it did
NOT contain — and the one it did not contain (a quadratic in `build_cues`'
accumulation) was that round's HIGH.

    python3 tests/mutants_57.py <copy-of-the-repo> [M-prefix ...]

Each mutant is a function from the source text to the mutated source text;
`sub` asserts its anchor occurs exactly once, so a mutant that no longer
applies fails loudly instead of testing nothing.
"""
from __future__ import annotations

import os
import ast
import pathlib
import pwd
import re
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
RET = "    return SEGMENT.match(line.rstrip())\n"
ZPAT = ('_Z = re.compile(\n    rf"^\\[\\s*(?P<ts>{_STAMP})\\s*(?:-(?P<end>[^\\]]*))?\\]\\s*"\n'
        '    r"(?:(?P<speaker>[^:\\[\\]]{1,60}?)\\s*:\\s*)?"\n    r"(?P<text>.*\\S)\\s*$"\n)\n')
COPYPAT = ('    _COPY = re.compile(r"^\\[\\s*(?P<ts>(?:\\d{1,2}:[0-5]\\d:[0-5]\\d|\\d{1,4}:[0-5]\\d)(?:[.,]\\d{1,3})?)"\n'
           '                       r"\\s*(?:-(?P<end>[^\\]]*))?\\]\\s*(?:(?P<speaker>[^:\\[\\]]{1,60}?)\\s*:\\s*)?"\n'
           '                       r"(?P<text>.*\\S)\\s*$")\n'
           '    mine = []\n    for l in body.split("\\n"):\n        m = _COPY.match(l)\n        if m:\n'
           '            mine.append({"start": 0.0, "end": None, "speaker": (m.group("speaker") or "").strip(),\n'
           '                         "text": m.group("text").strip()})\n')
PARSE_RET = "    cues, _, _, _ = parse_transcript(body)\n    return cues\n"


def sub(s: str, old: str, new: str, n: int = 1) -> str:
    assert s.count(old) == n, (old[:60], s.count(old))
    return s.replace(old, new)


MUTANTS = {
    "M0 baseline (must stay green)": lambda s: s,
    "M1 DA speaker-colon fold inside helper": lambda s: sub(s, RET,
        '    head, sep, rest = line.rpartition(":")\n    if sep and not rest.strip():\n'
        '        while rest and rest[-1].isspace():\n            rest = rest[:-1]\n        line = head + sep + rest\n' + RET),
    'M2 rstrip(" ") instead of rstrip()': lambda s: sub(s, RET, '    return SEGMENT.match(line.rstrip(" "))\n'),
    "M3 lru_cache + quadratic preprocess": lambda s: sub(sub(sub(s, "import re\n", "import re\nimport functools\n"),
        "def _match_segment(line: str) -> re.Match | None:",
        "@functools.lru_cache(maxsize=None)\ndef _match_segment(line: str) -> re.Match | None:"),
        RET, '    re.match(r"\\S+\\s*(?P<t>.*\\S)\\s*$", line)\n' + RET),
    "M4 zero-cue branch matches a copied pattern": lambda s: sub(sub(s, "_STAMP_ONLY = re.compile(", ZPAT + "_STAMP_ONLY = re.compile("),
        "stamped = [l for l in dropped if CUE_SHAPED.match(l)]", "stamped = [l for l in dropped if _Z.match(l) or CUE_SHAPED.match(l)]"),
    "M5 parse_segments second derivation written INTO the result (cues[:] = mine)": lambda s: sub(s,
        PARSE_RET, "    cues, _, _, _ = parse_transcript(body)\n" + COPYPAT + "    cues[:] = mine\n    return cues\n"),
    "M6 parse_segments second derivation that DISCARDS its result": lambda s: sub(s,
        PARSE_RET, "    cues, _, _, _ = parse_transcript(body)\n" + COPYPAT + "    return cues\n"),
    "M7 main exits 2 before parsing on --file": lambda s: sub(s, "    args = ap.parse_args()\n",
        "    args = ap.parse_args()\n    if args.file:\n        raise SystemExit(2)\n"),
    "M8 header ledger uses a copied pattern (round-2 bypass)": lambda s: sub(sub(s, "_STAMP_ONLY = re.compile(", ZPAT + "_STAMP_ONLY = re.compile("),
        "ate = [line for line in header if _match_segment(line)]", "ate = [line for line in header if _Z.match(line)]"),
    "M9 small-coefficient quadratic (passes the first ceilings)": lambda s: sub(s, RET,
        '    for i in range(0, len(line), 512):\n        line.count(" ", i)\n' + RET),
    "M10 full revert of the fix": lambda s: sub(s, RET, "    return SEGMENT.match(line)\n"),
    "M11 re.ASCII on SEGMENT": lambda s: sub(s, '    r"(?P<text>.*\\S)\\s*$"\n)\n', '    r"(?P<text>.*\\S)\\s*$", re.ASCII\n)\n'),
    "M12 helper drops NBSP lines": lambda s: sub(s, RET, '    if "\\xa0" in line:\n        return None\n' + RET),
    "M13 parse_segments returns element copies": lambda s: sub(s, PARSE_RET,
        "    cues, _, _, _ = parse_transcript(body)\n    return type(cues)(dict(c) for c in cues) if cues else cues\n"),
    "M14 speaker bound lifted {1,60}? -> {1,}?": lambda s: sub(s, "[^:\\[\\]]{1,60}?", "[^:\\[\\]]{1,}?"),
    "M15 rstrip with an explicit class": lambda s: sub(s, RET, '    return SEGMENT.match(line.rstrip(" \\t\\xa0\\u3000"))\n'),
    "M16 _cue_lines copied-pattern pre-check": lambda s: sub(sub(s, "_STAMP_ONLY = re.compile(", ZPAT + "_STAMP_ONLY = re.compile("),
        '    cues, dropped, front, _ = parse_transcript(\n        path.read_text(encoding="utf-8-sig"),',
        '    _shaped = sum(1 for _l in path.read_text(encoding="utf-8-sig").split("\\n") if _Z.match(_l.rstrip("\\r")))\n'
        '    cues, dropped, front, _ = parse_transcript(\n        path.read_text(encoding="utf-8-sig"),'),
    "M17 uniform quadratic in parse_transcript outside the helper": lambda s: sub(s, "        m = _match_segment(line)\n",
        '        for _i in range(0, len(line), 512):\n            line.count(" ", _i)\n        m = _match_segment(line)\n'),
    "M18 end group reverted to the pre-#50 lazy form (cubic on an unclosed bracket)": lambda s: sub(s,
        r'(?:-(?P<end>[^\]]*))?', r'(?:-\s*(?P<end>[^\]]*?)\s*)?'),
    "M19 ^[-keyed cap: drops every cue over 1 MB (round 6: recorder satisfied by the control)": lambda s: sub(s, RET,
        '    if line.startswith("[") and len(line) > 1_000_000:\n        return None\n' + RET),
    "M20 sanitise per-character walk goes quadratic (kept = kept + [c])": lambda s: sub(s,
        "    kept = [c for c in text\n            if c.isspace() or c in _STRIP_KEEP\n            or unicodedata.category(c) not in _STRIP_CATEGORIES]\n",
        "    kept = []\n    for c in text:\n        if c.isspace() or c in _STRIP_KEEP or unicodedata.category(c) not in _STRIP_CATEGORIES:\n            kept = kept + [c]\n"),
    "M21 wrap_cue_text CJK width-break goes quadratic (pieces = pieces + [chunk])": lambda s: sub(s,
        "                pieces.append(chunk)\n", "                pieces = pieces + [chunk]\n"),
    "M22 parse_transcript dropped-line bookkeeping goes quadratic (skipped = skipped + [line])": lambda s: sub(s,
        "        elif line.strip():\n            skipped.append(line)\n", "        elif line.strip():\n            skipped = skipped + [line]\n"),
    "M23 parse_transcript cue bookkeeping goes quadratic (cues = cues + [{...}])": lambda s: sub(sub(s,
        "            cues.append({\n", "            cues = cues + [{\n"),
        '                "text": m.group("text").strip(),\n            })\n', '                "text": m.group("text").strip(),\n            }]\n'),
    "M24 build_cues accumulation goes quadratic (round 7: the cue count never reached it)": lambda s: sub(s,
        '        cues.append({"start": seg["start"], "end": end, "text": text,\n'
        '                     "stripped": gone})\n',
        '        cues = cues + [{"start": seg["start"], "end": end, "text": text,\n'
        '                        "stripped": gone}]\n'),
    "M25 render_srt block accumulation goes quadratic (round 7: 5 s per 0.8 MB, all green)": lambda s: sub(sub(s,
        "        blocks.append(\n", "        blocks = blocks + [\n"),
        '        )\n    return "\\n".join(blocks)\n', '        ]\n    return "\\n".join(blocks)\n'),
    "M26 parse_transcript lost_ends bookkeeping goes quadratic (round 7: the third per-line list)": lambda s: sub(s,
        "                    lost_ends.append(line)\n", "                    lost_ends = lost_ends + [line]\n"),
    "M27 uniform line-count quadratic in parse_transcript (round 7: 6 s per 13 MB under the uniform bound alone)": lambda s: sub(s,
        '    all_lines = [l.rstrip("\\r") for l in text.split("\\n")]\n',
        '    all_lines = [l.rstrip("\\r") for l in text.split("\\n")]\n'
        '    _L, _j = len(all_lines), 0\n    for _ in range(_L * _L // 2000):\n        _j += 1\n'),

    # Round 8: seven per-line / per-cue walks the family reached only at
    # count 1-3, each demonstrated superlinear on `--file` or
    # `--preview-sources` with the whole suite green.
    "M28 header gate goes quadratic (the fix's own second call site, walked per header line)": lambda s: sub(s,
        "        ate = [line for line in header if _match_segment(line)]\n",
        "        ate = []\n        for _l in header:\n"
        "            if _match_segment(_l):\n                ate = ate + [_l]\n"),
    "M29 header list itself goes quadratic": lambda s: sub(s,
        '    header = [line for line in front if line.strip()]\n',
        '    header = []\n    for _l in front:\n        if _l.strip():\n            header = header + [_l]\n'),
    "M30 the no-cue exit's re-walk of dropped lines goes quadratic": lambda s: sub(s,
        "        stamped = [l for l in dropped if CUE_SHAPED.match(l)]\n",
        "        stamped = []\n        for _l in dropped:\n"
        "            if CUE_SHAPED.match(_l):\n                stamped = stamped + [_l]\n"),
    "M31 _cue_lines' per-cue pairing goes quadratic (--preview-sources only)": lambda s: sub(s,
        '    return ([(c["start"], b["text"], b["stripped"]) for c, b in zip(cues, built)],\n'
        '            len(dropped), len(header_ate))\n',
        '    _rows = []\n    for c, b in zip(cues, built):\n'
        '        _rows = _rows + [(c["start"], b["text"], b["stripped"])]\n'
        '    return (_rows, len(dropped), len(header_ate))\n'),
    "M32 collapse_runs accumulation goes quadratic (the heaviest shape's own dominant cost)": lambda s: sub(sub(s,
        "            out.append(run)\n            continue\n",
        "            out = out + [run]\n            continue\n"),
        '        out.append(" ")\n', '        out = out + [" "]\n'),
    "M33 wrap_cue_text's Latin branch goes quadratic (M21 mutates only the CJK one)": lambda s: sub(sub(s,
        "            lines.append(current)\n            current = word\n",
        "            lines = lines + [current]\n            current = word\n"),
        "    if current:\n        lines.append(current)\n",
        "    if current:\n        lines = lines + [current]\n"),
    "M34 build_cues' warnings list goes quadratic (grows with the cue count)": lambda s: sub(s,
        '            warnings.append(\n'
        '                f"cue at {format_timestamp(seg[\'start\'])} {what} to "\n'
        '                f"{format_timestamp(end)} — {why}")\n',
        '            warnings = warnings + [\n'
        '                f"cue at {format_timestamp(seg[\'start\'])} {what} to "\n'
        '                f"{format_timestamp(end)} — {why}"]\n'),

    # Round 9: three more walks the family reached only at count 1-3, and the
    # one the round-9 fixture itself could not reach because a remainder line
    # made `differing_sample` refuse before its own per-cue loop.
    "M35 differing_sample's per-cue pairing goes quadratic (--preview-sources, round 9)": lambda s: sub(s,
        "    for (p_start, tidy, p_alt), (v_start, raw, v_alt) in zip(polished, verbatim):\n",
        "    _paired = []\n"
        "    for (p_start, tidy, p_alt), (v_start, raw, v_alt) in zip(polished, verbatim):\n"
        "        _paired = _paired + [p_start]\n"),
    "M36 header_oddities goes quadratic (the third frontmatter walk)": lambda s: sub(s,
        '    return [l for l in lines\n'
        '            if l.strip() and l.strip() != "---"\n'
        '            and not re.match(r"^[A-Za-z_][\\w-]*\\s*:", l)]\n',
        '    _odd = []\n    for l in lines:\n'
        '        if l.strip() and l.strip() != "---" and not re.match(r"^[A-Za-z_][\\w-]*\\s*:", l):\n'
        '            _odd = _odd + [l]\n    return _odd\n'),
    "M37 _frontmatter_span's own scan goes quadratic": lambda s: sub(s,
        "    for i in range(1, len(lines)):\n",
        "    _walked = []\n    for i in range(1, len(lines)):\n        _walked = _walked + [i]\n"),
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    copy = pathlib.Path(argv[1]).resolve()
    # The same reasoning the probe's sandbox got over rounds 5-8, applied to
    # the other runnable file this suite ships: it rewrites `to_srt.py` and
    # deletes `<copy>/work`, so it must refuse anything that could be a live
    # working tree rather than the throwaway the docstring asks for.
    home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    # The repository in BOTH directions (a subdirectory of it is a live tree
    # too), the home directory and its ancestors. Not every path under the
    # home directory: a scratch copy usually lives there, and what actually
    # distinguishes a working tree from a throwaway is the `.git` below.
    if copy == REPO or REPO.is_relative_to(copy) or copy.is_relative_to(REPO):
        raise SystemExit(f"refusing to mutate {copy}: it is inside, or contains, this "
                         f"repository; give it an rsync copy outside it")
    if copy == home or home.is_relative_to(copy):
        raise SystemExit(f"refusing to mutate {copy}: it is, or contains, the home directory")
    if (copy / ".git").exists():
        raise SystemExit(f"refusing to mutate {copy}: it has a .git, so it is a working tree, "
                         f"not the throwaway copy this expects")
    if not (copy / "scripts" / "to_srt.py").is_file():
        raise SystemExit(f"{copy} has no scripts/to_srt.py")
    selected = argv[2:]
    base = copy / "base"
    if not base.is_dir():          # first run: keep a pristine copy beside the working tree
        base.mkdir()
        for name in ("scripts", "tests", "Makefile"):
            src = copy / name
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, base / name)
    results = []
    for name, mutate in MUTANTS.items():
        if selected and not any(name.startswith(x) for x in selected):
            continue
        work = copy / "work"
        # Not `ignore_errors`: a caller who mistyped the target should see
        # what could not be removed rather than nothing.
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(base, work)
        target = work / "scripts" / "to_srt.py"
        mutated = mutate(target.read_text(encoding="utf-8"))
        # A mutant that does not PARSE is red for the wrong reason — round 9
        # had one going red in 0.1 s on an unbalanced bracket, which says
        # nothing about the guard.
        ast.parse(mutated)
        target.write_text(mutated, encoding="utf-8")
        t0 = time.perf_counter()
        try:
            run = subprocess.run([sys.executable, "-m", "unittest",
                                  "tests.test_to_srt.TestSegmentMatchingHasOneEntryPoint",
                                  "tests.test_to_srt.TestThereIsOnlyOneParse"],
                                 cwd=work, capture_output=True, text=True, timeout=1200)
            out = run.stdout + run.stderr
            verdict = "GREEN" if run.returncode == 0 else "red"
        except subprocess.TimeoutExpired as exc:
            # A mutant that never finishes IS caught — that is the verdict
            # this ledger wants. Round 9 found the bare `timeout=` aborting
            # the whole run instead, so one slow mutant suppressed the
            # verdicts of every other one.
            out = "".join(x for x in (exc.stdout, exc.stderr) if isinstance(x, str))
            verdict = "red"
        elapsed = time.perf_counter() - t0
        first = re.search(r"AssertionError: (.*)", out)
        results.append((name, verdict))
        print(f"{verdict:5} {elapsed:6.1f}s  {name}\n      first: {(first.group(1) if first else '')[:230]}", flush=True)
    bad = [n for n, v in results if (v == "GREEN") != n.startswith("M0")]
    print(f"\n{len(results)} mutants; wrong verdicts: {bad or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
