#!/usr/bin/env python3
"""Child process for `TestSegmentMatchingHasOneEntryPoint` (#57).

Not a test module — `unittest discover` only collects `test*.py`. The parent
test spawns this once per path, hands it a JSON spec (a file path in
argv[1]; nothing else is accepted), and reads a JSON report from the file
its stdout was redirected to.

It refuses to run outside the parent's sandbox: it only loads the
repository's own `scripts/to_srt.py`; it only runs when `PLAUD_CACHE_DIR`
is set; and every path it writes or unlinks must RESOLVE under the sandbox
root the spec names — a directory the parent created — which may not be,
or contain, the user's home or this repository. Round 5 found that run by
hand it wrote its `--preview-sources` fixtures straight into
`~/.plaud-connector/cache`, replacing a real `rec.md`, and unlinked
`<work>/out.srt` at whatever path the JSON named. Round 6 found the refusal
that replaced that checked against `tempfile.gettempdir()` — against
`TMPDIR`, which the same caller sets — so `TMPDIR=$HOME` reproduced the
incident exactly. The root now comes from the spec and is checked against
what it must not be, not against this process's environment, and a write
follows no symlink out of it. A tracked, directly runnable file that writes
and deletes paths from a JSON blob is a gadget; the refusals are what make
it not one.

It is a separate PROCESS, not a helper function, because three things the
in-process version could not have are had at once: a HARD TIMEOUT (a regex
that has gone quadratic cannot be interrupted from inside the interpreter —
`_sre` does not check for signals — but a child can be killed), `cli_env`
ISOLATION (this process IS the CLI, so `config.load_config()` reads the
pinned, absent path), and a SPY on the chokepoint proving the pathological
line reached `_match_segment` on the path being timed.

WHAT IS MEASURED, AND AGAINST WHAT — the lessons of rounds 4 to 6.

  * CPU time (`time.process_time`), not wall time. Round 4 showed the wall
    clock guard going red on correct code with four idle processes on an
    18-core machine: descheduling was counted as backtracking.
  * Sizes are byte-equalised: `n` is divided by the built line's bytes per
    character, so a U+3000 tail or a CJK text is 3.2 MB at the top size like
    a space tail is, not 6.5 MB. At 6.5 MB the discriminating signal was the
    memory system — `split`, the `rstrip("\\r")` comprehension and
    `rstrip()` each allocating and scanning it — which grows faster than 4x
    per 4x and was reported as a quadratic.
  * Every shape on every path is measured ALTERNATELY with a CONTROL line of
    the same length and representation that the pattern rejects at its first
    character (`"x" + line[1:]`): the same copies, the same cache misses, the
    same load, no matcher work. The criterion is on the EXCESS over the
    control (`judge()` below), so what the memory system does to both
    cancels, and the pattern's own linear cost — 60 bounded speaker attempts
    on one shape, ≈ 200 ms per 3.2 MB — is judged for growth, not for size.
    The `matcher` path (the helper, through the spy, on a pre-built string;
    the strip's copy and the Match object are inside the timed region, one
    spy frame is, input construction is not) uses the same criterion.
  * The three growth sizes are measured round-robin inside each rep, and a
    shape that fails a ceiling or the growth criterion is measured once more
    with more reps before it is called (`growth_series` and the retry below
    say why: the cores are not all the same speed, and CPU time cannot
    tell). The warm-up runs BEFORE the smallest ceiling: round 6 found the
    tightest absolute bound in the class taking the one cold sample.
  * The pattern is wrapped in a recorder and the helper in a spy, and each
    keeps its maximum PER ROLE: the shape's runs and the control's runs are
    counted apart. Round 4 found the family never let the pattern see more
    than 34 characters; round 5 found nothing asserted otherwise; round 6
    found the assertion round 5 asked for satisfied by the CONTROL — the
    same length, and on every survivor kind it survives the strip too — so a
    helper that dropped the shape's own line on `^\\[` past any length kept
    the whole class green. The parent asserts on the shape's counters; the
    control's are reported beside them and held to the same bound, which
    pins the premise that the control does the same copies.
  * Shapes grow in FOUR regions, because each earlier family grew in one.
    After the closing bracket (`tail`, `colon`, `text`, `cjk`, `mixed`, and
    `run` — a whitespace run across `\\]\\s*` or the speaker colon's `\\s*`,
    which round 6 found no shape growing at the pattern); before it (`open`:
    no closing bracket at all, the #50/#55 cubic branch; `end`: a growing
    end group that does close; `lead`: a growing run after `[`); and in the
    LINE COUNT (`many`: n characters of short lines — round 6 found every
    fixture a single line, so a quadratic in the per-line bookkeeping cost
    7 s per 0.8 MB of ordinary markdown with everything green).

Per-rep content is DISTINCT (the run is `n + rep` long, and `mixed` adds a
character when `n` is odd so integer division cannot fold two reps into one
string), so an `lru_cache` on the helper cannot make `min()` see only cache
hits. The parent has a test that every shape at every size differs between
consecutive reps.

Protocol: progress goes to stderr one line per shape, so when the parent
kills this process on timeout the last line names the shape it died on.
Exit 0 with a full report; exit 2 with a partial report the moment a
ceiling or the growth criterion fails, so a gross quadratic is red after the
3 200-character stage — about a second — and a slow one after its first
shape rather than its hundredth.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import time
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "to_srt.py"

# Shape kinds. `tail` is the issue's class; the rest exist because a guard
# that grows only one region of the input guards only that region.
KINDS = ("tail", "colon", "text", "cjk", "mixed", "run", "open", "end", "lead", "many")
STRIPS_TO_PREFIX = ("tail",)            # the pattern receives only the prefix
SHORT_LINES = ("many",)                 # many short lines: the pattern never sees a long one
REACHES_PATTERN = tuple(k for k in KINDS if k not in STRIPS_TO_PREFIX + SHORT_LINES)


def _load(script: str):
    if pathlib.Path(script).resolve() != SCRIPT:
        raise SystemExit(f"refusing to load {script!r}: this probe only loads {SCRIPT}")
    spec = importlib.util.spec_from_file_location("to_srt", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bytes_per_char(s: str) -> int:
    """CPython's compact representation: 1, 2 or 4 bytes per code point.
    The parent skips the family test on other implementations."""
    top = max(map(ord, s), default=0)
    return 1 if top <= 0xFF else 2 if top <= 0xFFFF else 4


def build_line(kind: str, prefix: str, tail: str, n: int) -> str:
    if kind == "tail":          # nothing after the prefix but whitespace
        return prefix + tail * n
    if kind == "colon":         # survives the strip; forces the speaker group to backtrack
        return prefix + "a" + tail * n + ":"
    if kind == "text":          # survives the strip; a long text group
        return prefix + "x" * n
    if kind == "cjk":           # survives the strip; a long CJK text (the width-break wrap)
        return prefix + "字" * n
    if kind == "mixed":         # survives the strip; text with internal whitespace runs
        return prefix + ("x" + tail) * (n // 2) + ("x" if n % 2 else "")
    if kind in ("run", "open"):
        # One construction, two claims, chosen by the prefix. `run`: a
        # whitespace run the pattern must cross AFTER the closing bracket to
        # reach a one-character text — `\]\s*` on `[00:10]`, the speaker
        # colon's `\s*` on `[00:10] S:`. `open`: a prefix that never closes
        # its bracket, so the growth is inside `[^\]]*` / `\s*` BEFORE it.
        return prefix + tail * n + "x"
    if kind == "end":           # a growing end group that DOES close
        return prefix + tail * n + "] S: x"
    if kind == "lead":          # a growing run inside `\[\s*`
        return prefix + tail * n + "00:10] S: x"
    if kind == "many":
        # n characters of SHORT lines: the line count is the axis. Each line
        # is the prefix and a short tail, so it strips to the prefix and takes
        # the branch a `tail` shape takes, `n // len(unit)` times over; the
        # remainder is a final line, so consecutive reps still differ.
        unit = prefix + tail * 8 + "\n"
        return unit * (n // len(unit)) + "x" * (n % len(unit))
    raise SystemExit(f"unknown shape kind {kind!r}")


def control_line(line: str) -> str:
    """Same length, same representation, rejected by `^\\[` at character 0."""
    return "x" + line[1:]


ROLES = ("shape", "control")


class _Recorder:
    """Stands in for `SEGMENT` inside the child: records the longest string
    the pattern was actually handed, per role, and delegates everything
    else. One counter for both roles was round 6's finding — the control
    alone drove it to full length."""

    def __init__(self, pattern):
        self._pattern = pattern
        self.role = "shape"
        self.longest = dict.fromkeys(ROLES, 0)

    def reset(self):
        self.longest = dict.fromkeys(ROLES, 0)

    def match(self, string, *args, **kwargs):
        if len(string) > self.longest[self.role]:
            self.longest[self.role] = len(string)
        return self._pattern.match(string, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._pattern, name)


def judge(spec: dict, p: list[float], c: list[float]) -> dict | None:
    """The growth criterion, shared verbatim by this child (which stops at
    the first shape it fails) and the parent test (which runs it again on
    every shape of the report it accepts — the same function on the same
    numbers, so the two cannot disagree; the parent's own contributions are
    the report's completeness, the recorder and spy bounds, and the exit
    codes).

    `p` and `c` are the shape's and its control's CPU milliseconds at the
    three growth sizes. What is judged is the EXCESS `p - c`: the work the
    shape triggers that its same-length, same-representation control does
    not. The copies, the cache misses and the load are in both and cancel;
    the pattern's own cost is what remains, and it must grow linearly: its
    increment at the top size may be at most K_GROWTH x its increment at the
    middle size, plus a slack of SLACK_MS and half the control's own top
    increment (the tail shapes' excess is a few tenths of a millisecond,
    below the noise a bare slack could absorb). The control carries the
    loose uniform bound: a quadratic that hits every line equally shows
    there, and nowhere else — and its allowance grows with the control's
    absolute cost, so on a path with a large fixed cost (`cli-preview`) a
    uniform quadratic worth some tens of milliseconds at 3.2 MB passes
    (measured in round 6: ≈ 22 ms on that path's `tail`, ≈ 1 ms on
    `matcher`'s `colon`); that is the stated residue of this bound. The
    `many` shapes are judged by the uniform bound alone: their control is
    the same block with one line changed, so their excess is one line's work
    by construction, and the line count — their axis — is in both.
    """
    # Increments are clamped at zero before they scale a bound: a first
    # measurement a few hundred microseconds slower than the second
    # (measured; CPU time is not immune to a cold cache) would otherwise
    # turn the bound negative and report a ratio of a billion.
    k, s = spec["k_growth"], spec["slack_ms"]
    rc1, dc2 = c[1] - c[0], c[2] - c[1]
    e = [pi - ci for pi, ci in zip(p, c)]
    re1, de2 = e[1] - e[0], e[2] - e[1]
    dc1, de1 = max(rc1, 0.0), max(re1, 0.0)
    allowance = s + max(dc2, 0.0) / 2
    bound = k * de1 + allowance
    # `clear`: the miss is past TWICE the bound. That is what decides whether
    # the child re-measures before calling it — not the ratio, which is
    # meaningless when de1 is at the noise floor (a tail shape's excess is a
    # few microseconds, and 2.8 ms over 0 µs is "infinite"). And NEVER when
    # the first increment was clamped: a bound built on a zeroed increment is
    # the slack alone, and twice the slack is not evidence of anything —
    # round 6 found the clamp suppressing the retry in exactly the case it
    # exists to flag.
    if de2 > bound:
        return {"stage": "excess", "times": p, "control": c, "excess": e,
                "ratio": de2 / max(de1, 1e-9), "admitted": (k - 4) * de1 + allowance,
                "clear": de2 > 2 * bound and re1 > 0}
    ubound = spec["k_uniform"] * dc1 + spec["uniform_slack_ms"] + 2 * c[1]
    if dc2 > ubound:
        return {"stage": "uniform", "control": c, "ratio": dc2 / max(dc1, 1e-9),
                "clear": dc2 > 2 * ubound and rc1 > 0}
    return None


def _contained(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path` RESOLVES under `root` — symlinks followed, so a planted link
    cannot carry a write or an unlink out of the sandbox."""
    return path.resolve().is_relative_to(root)


def sandbox_root(spec: dict) -> pathlib.Path:
    """The one directory this child may write under. Named by the parent in
    the spec — not derived from this process's environment, which the same
    caller controls — and refused when it is, or contains, the user's home
    or this repository: the two places a hand-run with a careless spec
    would do the round-5 damage."""
    root = pathlib.Path(spec["sandbox"]).resolve()
    if not root.is_dir():
        raise SystemExit(f"refusing to run: sandbox {root} is not a directory")
    for what, guarded in (("the home directory", pathlib.Path.home().resolve()),
                          ("this repository", SCRIPT.parent.parent)):
        if guarded.is_relative_to(root):
            raise SystemExit(f"refusing to run: sandbox {root} contains {what}")
    return root


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_segment_timing.py <spec.json>  (run by the parent test)")
    with open(sys.argv[1], encoding="utf-8") as fh:
        spec = json.load(fh)
    if not os.environ.get("PLAUD_CACHE_DIR"):
        raise SystemExit("refusing to run: PLAUD_CACHE_DIR is not set, so the CLI would use "
                         "the real cache and the --preview-sources fixtures would overwrite it")
    root = sandbox_root(spec)
    to_srt = _load(spec["script"])
    path = spec["path"]
    cache = pathlib.Path(to_srt.CACHE_DIR)     # pinned by the parent's cli_env
    work = pathlib.Path(spec["work"])           # scratch OUTSIDE the cache dir
    for what, p in (("cache", cache), ("work", work)):
        if not _contained(p, root):
            raise SystemExit(f"refusing to run: {what} directory {p} is not under the "
                             f"sandbox {root}; this probe writes and unlinks there")

    def write(p: pathlib.Path, text: str) -> None:
        if p.is_symlink() or not _contained(p, root):
            raise SystemExit(f"refusing to write {p}: a symlink, or outside the sandbox")
        p.write_text(text, encoding="utf-8")

    seen = {"role": "shape", "calls": 0,
            "longest": dict.fromkeys(ROLES, 0), "calls_max": dict.fromkeys(ROLES, 0)}
    real = to_srt._match_segment

    def spy(line: str):
        seen["calls"] += 1
        if len(line) > seen["longest"][seen["role"]]:
            seen["longest"][seen["role"]] = len(line)
        return real(line)

    to_srt._match_segment = spy
    recorder = _Recorder(to_srt.SEGMENT)
    to_srt.SEGMENT = recorder                   # the helper looks it up at call time

    def timed(line: str) -> tuple[float, dict | None]:
        """CPU seconds for one pass of `line` through `path`, plus exit info."""
        if path.startswith("matcher"):
            # The helper through the spy — one Python frame inside the timed
            # region, so the spy's count and length are measured, not stated.
            t0 = time.process_time()
            spy(line)
            return time.process_time() - t0, None
        if path.startswith("lib"):
            t0 = time.process_time()
            to_srt.parse_transcript(line + "\n")
            return time.process_time() - t0, None
        if path == "segments":
            t0 = time.process_time()
            to_srt.parse_segments(line + "\n")
            return time.process_time() - t0, None
        out = work / "out.srt"
        if out.is_symlink() or not _contained(out, root):
            raise SystemExit(f"refusing to unlink {out}: a symlink, or outside the sandbox")
        out.unlink(missing_ok=True)
        if path == "cli-preview":
            # `--preview-sources` → `differing_sample` → `_cue_lines` → the
            # parser, on BOTH the transcript and its polished twin. A line
            # that yields no cue is a drop on both sides; one that yields a
            # cue yields the same cue on both. Either way the comparison is
            # refused (exit 3) — after both files were parsed.
            (cache / "polish").mkdir(exist_ok=True)
            write(cache / "rec.md", "---\ntitle: x\n---\n" + line + "\n[00:10] S: hello\n")
            write(cache / "polish" / "rec.md", line + "\n[00:10] S: hello\n")
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
            write(src, text)
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

    def run_path(line: str, role: str) -> tuple[float, dict | None]:
        """`timed()` with the spy and the recorder counting for `role`."""
        seen["role"] = recorder.role = role
        seen["calls"] = 0
        try:
            return timed(line)
        finally:
            seen["calls_max"][role] = max(seen["calls_max"][role], seen["calls"])

    def best_pair(kind, prefix, tail, n, reps) -> tuple[float, float, dict | None]:
        """min CPU ms over `reps` distinct lines, shape and control alternating."""
        bp, bc, last = float("inf"), float("inf"), None
        for rep in range(reps):
            line = build_line(kind, prefix, tail, n + rep)
            dc, _ = run_path(control_line(line), "control")
            dp, last = run_path(line, "shape")
            bp, bc = min(bp, dp), min(bc, dc)
        return bp * 1000, bc * 1000, last

    def ceiling_ms(kind, prefix, tail, n, reps) -> float:
        # Shape only: a ceiling is an absolute bound on the shape's own cost,
        # so the control has nothing to say here — measuring it doubled the
        # cheapest stage's cost for nothing.
        return min(run_path(build_line(kind, prefix, tail, n + rep), "shape")[0]
                   for rep in range(reps)) * 1000

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
                dc, _ = run_path(control_line(line), "control")
                dp, last = run_path(line, "shape")
                bp[i], bc[i] = min(bp[i], dp), min(bc[i], dc)
        return [x * 1000 for x in bp], [x * 1000 for x in bc], last

    def fail(report, **why):
        report["failed"] = why
        print(json.dumps(report))
        return 2

    report = {"path": path, "shapes": [], "failed": None}
    for kind, prefix, tail in spec["shapes"]:
        recorder.reset()
        seen["longest"], seen["calls_max"] = dict.fromkeys(ROLES, 0), dict.fromkeys(ROLES, 0)
        # Bytes per character of the line as BUILT, not of its parts: a `cjk`
        # text is two bytes a character on a one-byte prefix and tail.
        bpc = bytes_per_char(build_line(kind, prefix, tail, 256))
        shape = {"kind": kind, "prefix": prefix, "tail": tail, "bpc": bpc,
                 "ceiling": {}, "growth": {}, "exit": None, "retried": False,
                 "spy_max": 0, "pattern_max": 0, "spy_calls": 0,
                 "control_spy_max": 0, "control_pattern_max": 0, "control_spy_calls": 0}
        report["shapes"].append(shape)
        label = f"{path} {kind} {prefix!r} + {tail!r}"
        # Warm the path once, at the SMALLEST size, and discard it: the first
        # pass through a path pays one-time costs (a cold page cache for the
        # scratch file, a first `config` read) that would otherwise land in
        # the smallest ceiling — the tightest absolute bound in the class. At
        # the smallest size so that a cubic still dies here in milliseconds:
        # the ceilings exist so the growth sizes are never reached by one.
        first = spec["ceilings"][0][0]
        best_pair(kind, prefix, tail, first, 1)
        for n, limit in spec["ceilings"]:
            ms = ceiling_ms(kind, prefix, tail, n, spec["ceiling_reps"])
            if ms >= limit:
                # Once more, with more reps, before calling it — the same
                # treatment the growth stage gets; a real quadratic misses a
                # 100x-headroom ceiling the second time as it did the first.
                ms = ceiling_ms(kind, prefix, tail, n, spec["ceiling_reps"] + 2)
                shape["retried"] = True
            shape["ceiling"][str(n)] = ms
            if ms >= limit:
                return fail(report, label=label, stage="ceiling", n=n, ms=ms)
        # Warm the first growth size for BOTH roles: the ceilings ran the
        # shape there but never its control, and a cold control at the first
        # size would shrink `dc1` and tighten the uniform bound.
        best_pair(kind, prefix, tail, spec["growth"][0] // bpc, 1)
        sizes = [n // bpc for n in spec["growth"]]
        p, c, last = growth_series(kind, prefix, tail, sizes, spec["growth_reps"])
        verdict = judge(spec, p, c)
        if verdict is not None and not verdict["clear"]:
            # Measure the shape ONCE MORE, with more reps, before calling it —
            # but only when the miss is within a factor of two of the bound.
            # A miss past twice the bound is not scheduling noise (a linear
            # helper under nine busy cores missed by 1.3x, never 2x), and
            # re-measuring a real quadratic just doubled the time to red.
            # Under load (nine busy cores of eighteen) a linear helper measured
            # 10.2x once in three runs: the top-size sample is the longest and
            # the likeliest to straddle a migration to a slower core. A real
            # quadratic fails the second measurement exactly as it failed the
            # first; a scheduling artefact does not repeat. The numbers kept
            # in the report are the ones the verdict was reached on, and the
            # second verdict REPLACES the first — a shape is never called on
            # one measurement.
            p, c, last = growth_series(kind, prefix, tail, sizes, spec["growth_reps"] + 4)
            verdict = judge(spec, p, c)
            shape["retried"] = True
        for n, mp, mc in zip(spec["growth"], p, c):
            shape["growth"][str(n)] = [mp, mc]
        shape["exit"] = last
        shape["spy_max"], shape["pattern_max"] = seen["longest"]["shape"], recorder.longest["shape"]
        shape["control_spy_max"] = seen["longest"]["control"]
        shape["control_pattern_max"] = recorder.longest["control"]
        shape["spy_calls"], shape["control_spy_calls"] = (seen["calls_max"]["shape"],
                                                          seen["calls_max"]["control"])
        if verdict is not None:
            return fail(report, label=label, **verdict)
        print(f"{label}: " + " ".join(f"{n}={mp:.3f}/{mc:.3f}" for n, (mp, mc)
                                      in shape["growth"].items())
              + f" pattern_max={shape['pattern_max']}/{shape['control_pattern_max']}",
              file=sys.stderr, flush=True)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
