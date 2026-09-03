#!/usr/bin/env python3
"""Child process for `TestSegmentMatchingHasOneEntryPoint` (#57).

Not a test module — `unittest discover` only collects `test*.py`. The parent
test spawns this once per path, hands it a JSON spec (a file path in
argv[1]; nothing else is accepted), and reads a JSON report from the file
its stdout was redirected to. It only ever loads the repository's own
`scripts/to_srt.py`: a spec naming any other path is refused, because a
tracked, directly runnable file whose job is to `exec_module` a path from a
JSON blob is a gadget the first time something globs `tests/*.py`.

It is a separate PROCESS, not a helper function, because three things the
in-process version could not have are had at once: a HARD TIMEOUT (a regex
that has gone quadratic cannot be interrupted from inside the interpreter —
`_sre` does not check for signals — but a child can be killed), `cli_env`
ISOLATION (this process IS the CLI, so `config.load_config()` reads the
pinned, absent path), and a SPY on the chokepoint proving the pathological
line reached `_match_segment` on the path being timed.

WHAT IS MEASURED, AND AGAINST WHAT — the round-4 lessons.

  * CPU time (`time.process_time`), not wall time. Round 4 showed the wall
    clock guard going red on correct code with four idle processes on an
    18-core machine: descheduling was counted as backtracking.
  * Sizes are byte-equalised: `n` is divided by the string's bytes per
    character, so a U+3000 tail is 3.2 MB at the top size like a space tail
    is, not 6.5 MB. At 6.5 MB the discriminating signal was the memory
    system — `split`, the `rstrip("\\r")` comprehension and `rstrip()` each
    allocating and scanning it — which grows faster than 4x per 4x and was
    reported as a quadratic.
  * Every path-level shape is measured ALTERNATELY with a CONTROL line of
    the same length and representation that the pattern rejects at its first
    character (`"x" + line[1:]`): the same copies, the same cache misses, the
    same load, no matcher work. The path criterion is on the EXCESS over
    the control (`judge()` below), so what the memory system does to both
    cancels, and the pattern's own linear cost — 60 bounded speaker attempts
    on one shape, ≈ 200 ms per 3.2 MB — is judged for growth, not for size.
  * The three growth sizes are measured round-robin inside each rep, and a
    shape that fails the criterion is measured once more with more reps
    before it is called (`growth_series` and the retry below say why: the
    cores are not all the same speed, and CPU time cannot tell).
  * The matcher itself is timed on its own path (`_match_segment` on a
    pre-built string): no allocation, no I/O, a clean signal with an
    absolute linearity bound. Round 4 found the previous family never let
    the pattern see a string longer than 34 characters — every member
    stripped to its prefix — so a pattern regression on lines that SURVIVE
    the strip (a lifted speaker bound, 11 s per 128 KB line) was invisible.
    The survivor shapes (`a` + whitespace + `:`, a long text, a long mixed
    text) exist for that.

Per-rep content is DISTINCT (the run is `n + rep` long), so an `lru_cache`
on the helper cannot make `min()` see only cache hits.

Protocol: progress goes to stderr one line per shape, so when the parent
kills this process on timeout the last line names the shape it died on.
Exit 0 with a full report; exit 2 with a partial report the moment a
ceiling or a growth criterion fails, so a gross quadratic is red after the
3 200-character stage — about a second — and a slow one after its first
shape rather than its hundredth.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import time
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "to_srt.py"


def _load(script: str):
    if pathlib.Path(script).resolve() != SCRIPT:
        raise SystemExit(f"refusing to load {script!r}: this probe only loads {SCRIPT}")
    spec = importlib.util.spec_from_file_location("to_srt", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bytes_per_char(s: str) -> int:
    """CPython's compact representation: 1, 2 or 4 bytes per code point."""
    top = max(map(ord, s), default=0)
    return 1 if top <= 0xFF else 2 if top <= 0xFFFF else 4


def build_line(kind: str, prefix: str, tail: str, n: int) -> str:
    if kind == "tail":          # the issue's class: nothing after the prefix but whitespace
        return prefix + tail * n
    if kind == "colon":         # survives the strip; forces the speaker group to backtrack
        return prefix + "a" + tail * n + ":"
    if kind == "text":          # survives the strip; a long text group
        return prefix + "x" * n
    if kind == "mixed":         # survives the strip; text with internal whitespace runs
        return prefix + ("x" + tail) * (n // 2)
    raise SystemExit(f"unknown shape kind {kind!r}")


def control_line(line: str) -> str:
    """Same length, same representation, rejected by `^\\[` at character 0."""
    return "x" + line[1:]


def judge(spec: dict, path: str, p: list[float], c: list[float]) -> dict | None:
    """The growth criteria, shared verbatim by this child (fail fast) and the
    parent test (which re-judges every shape of a full report).

    `p` and `c` are the shape's and its control's CPU milliseconds at the
    three growth sizes. What is judged is the EXCESS `p - c`: the work the
    shape triggers that its same-length, same-representation control does
    not. The copies, the cache misses and the load are in both and cancel;
    the pattern's own cost — 60 bounded speaker attempts on the `a` + ws + `:`
    shape, a scan on the long-text shapes, a `rstrip` on the tail shapes — is
    what remains, and it must grow linearly: its increment at the top size
    may be at most K_GROWTH x its increment at the middle size, plus a slack
    of SLACK_MS and half the control's own top increment (the tail shapes'
    excess is a few tenths of a millisecond, below the noise a bare slack
    could absorb). On the `matcher` path there is no control — the helper on
    a pre-built string is allocation-free CPU work — so its own increments
    are the signal. The control carries the loose uniform bound: a quadratic
    that hits every line equally shows there, and nowhere else.
    """
    # Increments are clamped at zero before they scale a bound: a first
    # measurement that is a few hundred microseconds slower than the second
    # (measured; CPU time is not immune to a cold cache) would otherwise turn
    # the bound negative and report a ratio of a billion.
    dp1, dp2 = max(p[1] - p[0], 0.0), p[2] - p[1]
    k, s = spec["k_growth"], spec["slack_ms"]
    if path == "matcher":
        if dp2 > k * dp1 + s:
            return {"stage": "matcher", "times": p, "ratio": dp2 / max(dp1, 1e-9),
                    "admitted": (k - 4) * dp1 + s}
        return None
    dc1, dc2 = max(c[1] - c[0], 0.0), c[2] - c[1]
    e = [pi - ci for pi, ci in zip(p, c)]
    de1, de2 = max(e[1] - e[0], 0.0), e[2] - e[1]
    allowance = s + max(dc2, 0.0) / 2
    if de2 > k * de1 + allowance:
        return {"stage": "excess", "times": p, "control": c, "excess": e,
                "ratio": de2 / max(de1, 1e-9), "admitted": (k - 4) * de1 + allowance}
    # The uniform bound's allowance grows with the control's absolute cost at
    # the middle size: when a fixed cost dominates, the increments are noise
    # around zero and a bare slack would fire on correct code.
    if dc2 > spec["k_uniform"] * dc1 + spec["uniform_slack_ms"] + 2 * c[1]:
        return {"stage": "uniform", "control": c, "ratio": dc2 / max(dc1, 1e-9)}
    return None


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_segment_timing.py <spec.json>")
    with open(sys.argv[1], encoding="utf-8") as fh:
        spec = json.load(fh)
    to_srt = _load(spec["script"])
    path = spec["path"]

    seen = {"max": 0}
    real = to_srt._match_segment

    def spy(line: str):
        if len(line) > seen["max"]:
            seen["max"] = len(line)
        return real(line)

    to_srt._match_segment = spy
    cache = pathlib.Path(to_srt.CACHE_DIR)     # pinned by the parent's cli_env
    work = pathlib.Path(spec["work"])           # scratch OUTSIDE the cache dir

    def run_path(line: str) -> tuple[float, dict | None]:
        """CPU seconds for one pass of `line` through `path`, plus exit info."""
        if path == "matcher":
            # The helper itself, called directly so no spy frame sits inside
            # the timed region; the line demonstrably reached it, so record
            # the length the spy would have.
            seen["max"] = max(seen["max"], len(line))
            t0 = time.process_time()
            real(line)
            return time.process_time() - t0, None
        if path == "lib":
            t0 = time.process_time()
            to_srt.parse_transcript(line + "\n")
            return time.process_time() - t0, None
        if path == "segments":
            t0 = time.process_time()
            to_srt.parse_segments(line + "\n")
            return time.process_time() - t0, None
        out = work / "out.srt"
        out.unlink(missing_ok=True)
        if path == "cli-preview":
            # `--preview-sources` → `differing_sample` → `_cue_lines` → the
            # parser, on BOTH the transcript and its polished twin; both drop
            # the line, so the comparison is refused (exit 3) — after parsing.
            (cache / "polish").mkdir(exist_ok=True)
            (cache / "rec.md").write_text("---\ntitle: x\n---\n" + line + "\n[00:10] S: hello\n",
                                          encoding="utf-8")
            (cache / "polish" / "rec.md").write_text(line + "\n[00:10] S: hello\n",
                                                     encoding="utf-8")
            argv = ["to_srt.py", "rec", "--preview-sources"]
        else:
            if path == "cli-header":
                text = "---\ntitle: x\n" + line + "\n---\n[00:10] S: hello\n"
            elif path == "cli-zero":
                text = line + "\n"
            elif path == "cli-body":
                text = "[00:10] S: hello\n" + line + "\n"
            else:
                raise SystemExit(f"unknown path {path!r}")
            src = work / "in.md"
            src.write_text(text, encoding="utf-8")
            argv = ["to_srt.py", "--file", str(src), "-o", str(out)]
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", argv), \
             contextlib.redirect_stdout(stdout), \
             contextlib.redirect_stderr(stderr):
            t0 = time.process_time()
            try:
                to_srt.main()
            except SystemExit as e:
                # `sys.exit(str)` is status 1 and the interpreter would have
                # printed the string; caught here it would vanish, so print
                # it where the parent looks.
                if isinstance(e.code, int):
                    code = e.code
                else:
                    code = 1
                    if e.code is not None:
                        stderr.write(str(e.code))
            dt = time.process_time() - t0
        return dt, {"code": code, "wrote": out.exists(),
                    "stdout": stdout.getvalue()[:200], "stderr": stderr.getvalue()[:300]}

    def best_pair(kind, prefix, tail, n, reps) -> tuple[float, float, dict | None]:
        """min CPU ms over `reps` distinct lines, shape and control alternating."""
        bp, bc, last = float("inf"), float("inf"), None
        for rep in range(reps):
            line = build_line(kind, prefix, tail, n + rep)
            dc, _ = run_path(control_line(line))
            dp, last = run_path(line)
            bp, bc = min(bp, dp), min(bc, dc)
        return bp * 1000, bc * 1000, last

    def growth_series(kind, prefix, tail, sizes, reps):
        """The three growth sizes measured ROUND-ROBIN inside each rep, then
        `min` per size. A child stays on one core for milliseconds at a time
        and Apple silicon's efficiency cores run this code at half speed, so
        three reps of one size, then three of the next, put the sizes on
        different cores and turned a linear helper into a 10.8x increment
        ratio. Interleaving keeps all three sizes under the same conditions
        within a rep; `min` over reps then picks the rep on the fast core.
        """
        bp, bc = [float("inf")] * len(sizes), [float("inf")] * len(sizes)
        last = None
        for rep in range(reps):
            for i, n in enumerate(sizes):
                line = build_line(kind, prefix, tail, n + rep)
                dc, _ = run_path(control_line(line))
                dp, last = run_path(line)
                bp[i], bc[i] = min(bp[i], dp), min(bc[i], dc)
        return [x * 1000 for x in bp], [x * 1000 for x in bc], last

    def fail(report, **why):
        report["failed"] = why
        print(json.dumps(report))
        return 2

    report = {"path": path, "shapes": [], "failed": None}
    for kind, prefix, tail in spec["shapes"]:
        seen["max"] = 0
        bpc = bytes_per_char(prefix + tail)
        shape = {"kind": kind, "prefix": prefix, "tail": tail, "bpc": bpc,
                 "ceiling": {}, "growth": {}, "spy_max": 0, "exit": None, "retried": False}
        report["shapes"].append(shape)
        label = f"{path} {kind} {prefix!r} + {tail!r}"
        for n, limit in spec["ceilings"]:
            ms, _, _ = best_pair(kind, prefix, tail, n, spec["ceiling_reps"])
            shape["ceiling"][str(n)] = ms
            if ms >= limit:
                return fail(report, label=label, stage="ceiling", n=n, ms=ms)
        # Warm both the shape and its control once at the first growth size
        # and discard it: the first pass through a path pays one-time costs
        # (a cold page cache for the scratch file, a first `config` read)
        # that would land in the first increment and turn it negative.
        best_pair(kind, prefix, tail, spec["growth"][0] // bpc, 1)
        sizes = [n // bpc for n in spec["growth"]]
        p, c, last = growth_series(kind, prefix, tail, sizes, spec["growth_reps"])
        verdict = judge(spec, path, p, c)
        if verdict is not None:
            # Measure the shape ONCE MORE, with more reps, before calling it.
            # Under load (nine busy cores of eighteen) a linear helper measured
            # 10.2x once in three runs: the top-size sample is the longest and
            # the likeliest to straddle a migration to a slower core. A real
            # quadratic fails the second measurement exactly as it failed the
            # first; a scheduling artefact does not repeat. The numbers kept
            # in the report are the ones the verdict was reached on.
            p, c, last = growth_series(kind, prefix, tail, sizes, spec["growth_reps"] + 2)
            verdict = judge(spec, path, p, c)
            shape["retried"] = True
        for n, mp, mc in zip(spec["growth"], p, c):
            shape["growth"][str(n)] = [mp, mc]
        shape["exit"] = last
        shape["spy_max"] = seen["max"]
        if verdict is not None:
            return fail(report, label=label, **verdict)
        print(f"{label}: " + " ".join(f"{n}={mp:.3f}/{mc:.3f}" for n, (mp, mc)
                                      in shape["growth"].items()),
              file=sys.stderr, flush=True)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
