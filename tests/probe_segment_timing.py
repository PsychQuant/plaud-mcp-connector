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
incident exactly. Round 7 found the replacement — the root named in the
spec, refused when it is or contains the home directory or the repository —
accepting every directory INSIDE the home directory, `~/.plaud-connector`
included, so the incident was one level deeper than the refusal. The root
must now carry a marker the parent test wrote with the nonce the spec
names, may not contain the default cache directory, and every write, mkdir
and unlink resolves symlinks before it acts (checked, then done: the two
are not one atomic step, which the parent's fresh private directory makes
moot and a hand-run against a shared directory does not). A tracked,
directly runnable file that writes and deletes paths from a JSON blob is a
gadget; the refusals narrow what it can reach to directories this test
suite created.

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
    tell). The warm-up runs BEFORE the smallest ceiling, at a size no rep
    re-uses: round 6 found the tightest absolute bound in the class taking
    the one cold sample, and round 7 found the warm-up building the very
    string rep 0 then measured, which a memoizing helper would have served
    from cache.
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
    LINE COUNT (`many`: n characters of short lines that are dropped, kept
    as cues, or kept with a lost end — round 6 found every fixture a single
    line, so a quadratic in the per-line bookkeeping cost 7 s per 0.8 MB of
    ordinary markdown with everything green; round 7 found the cue count
    never reaching `build_cues`, and the same block used as the control, so
    the axis was judged by the loose uniform bound alone). A `many` shape's
    control is ONE line of the same length: the per-line work is in the
    shape only, and the excess criterion judges it.

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
import pwd
import re
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


MANY_SECONDS = 200_000      # `many` timestamps count DOWN from here, one a line
MANY_TEXTS = 1_000_000      # and their texts cycle through this many


def many_prefix(template: str, i: int) -> str:
    """Line `i` of a `many` block: the template with its timestamp and its
    text filled in, always at the SAME width so the block's length arithmetic
    stays exact. `HH:MM:SS` is 8 characters for every second `_STAMP` admits
    (two-digit hours), and the text counter is zero-padded."""
    s = MANY_SECONDS - i
    if s < 0 or i >= MANY_TEXTS:
        raise SystemExit(f"`many` line {i} runs past the distinct timestamps or texts "
                         f"this block can carry ({MANY_SECONDS}, {MANY_TEXTS})")
    return template.format(t=f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}", i=i)


_MANY_LINES: dict[str, list[str]] = {}


def many_prefixes(template: str, count: int) -> list[str]:
    """The first `count` filled prefixes for this template, built once.
    A `many` block is up to 164 000 lines and every rep rebuilds it, so
    formatting each line every time cost seconds of the class's wall time
    for values that never change. The cache is per template and grows only
    upwards; nothing here depends on the block's LENGTH, which is what
    differs between reps."""
    have = _MANY_LINES.setdefault(template, [])
    while len(have) < count:
        have.append(many_prefix(template, len(have)))
    return have


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
        # is the prefix and a short tail, so it strips to the prefix and
        # takes the branch a `tail` shape takes.
        #
        # EVERY line is well formed, including the last. The remainder used
        # to be a bare `xxx…` fragment, which the parser drops — and one
        # dropped line on each side is enough for `differing_sample` to
        # refuse before it reaches the per-cue walk this block exists to
        # time, so round 9 found that walk running at most twice in the whole
        # family. The final line absorbs the remainder as extra tail instead,
        # which keeps the total length exactly `n` (so reps still differ)
        # with nothing for the parser to drop.
        #
        # And EVERY line differs from every other, in both fields a cue has.
        # Round 10 found the axis running at cardinality one: `unit * whole`
        # made 156 038 cues carrying one timestamp and one text, so every
        # per-cue cost that is superlinear only when the cues DIFFER read as
        # linear. Three such changes passed the whole family — a dedup scan in
        # `build_cues`, an order-preserving rewrite of `differing_sample`'s
        # `ambiguous` set, and a duplicate-timestamp counter on the main
        # conversion path — while costing 8 s on 40 000 real cues. Real
        # transcripts are the opposite: their timestamps are nearly all
        # distinct, and the family had picked the degenerate side.
        #
        # The timestamps count DOWN, one second a line. Distinct is what the
        # cardinality needs; DESCENDING is what keeps `build_cues`' clamp
        # firing on every cue (`end = nxt` lands at or before `start`), which
        # is the per-cue `warnings` list the ledger's M34 mutates. Ascending
        # timestamps would have closed one hole by opening another.
        template, prefix = prefix, many_prefix(prefix, 0)
        unit_len = len(prefix) + 8 + 1
        floor = len(prefix) + 1              # the shortest well-formed line
        whole, rest = divmod(n, unit_len)
        if rest and rest < floor:            # borrow a unit so the last line fits
            whole, rest = whole - 1, rest + unit_len
        if whole < 1:
            raise SystemExit(f"`many` needs at least {unit_len + floor} characters, got {n}")
        filled = many_prefixes(template, whole + (1 if rest else 0))
        pad = tail * 8 + "\n"
        out = [line + pad for line in filled[:whole]]
        if rest:
            out.append(filled[whole] + tail * (rest - floor) + "\n")
        return "".join(out)
    raise SystemExit(f"unknown shape kind {kind!r}")


def control_line(kind: str, line: str) -> str:
    """Same length, same representation, rejected by `^\\[` at character 0.
    For the short-line kinds the control is also ONE line — the block's
    newlines become spaces — so the line count, their axis, is in the shape
    alone (round 7: the same block as control left one line's work in the
    excess and the uniform bound as the only judge)."""
    if kind in SHORT_LINES:
        line = line.replace("\n", " ")
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
    `many` shapes have a one-line control, so their per-line bookkeeping is
    the excess and is judged by the excess bound like any other shape's work
    (round 7 measured the uniform bound alone admitting 290–670 ms of
    line-count quadratic on them).
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


MARKER = ".probe-sandbox"     # written by the parent test into the root it created


def sandbox_root(spec: dict) -> pathlib.Path:
    """The one directory this child may write under. Named by the parent in
    the spec — not derived from this process's environment, which the same
    caller controls — and accepted only when it carries the marker file the
    parent wrote with the spec's nonce. Refused outright when it is, or
    contains, the user's home, this repository, or the default cache
    directory (round 7: the round-6 refusals accepted every directory under
    the home directory, `~/.plaud-connector` included).

    What the marker is and is not (round 10 found both sentences it replaces
    overstated): the nonce lives in the spec, so whoever writes a spec by
    hand can also write the marker. It separates a directory whoever ran this
    invited from one already holding somebody else's files — not "a directory
    this test suite created" from "one a hand-written spec points at". What
    keeps a hand-written spec off the rest of the disk is the refusal list
    above plus the fact that every name this child writes is derived from the
    nonce; the marker only stops it from landing in an occupied directory."""
    root = pathlib.Path(spec["sandbox"]).resolve()
    # The containment rules come FIRST, before the directory is required to
    # exist: they are pure path arithmetic, and being guarded is the stronger
    # objection. Round 10 asserted the refusal REASON and put the `is_dir()`
    # check ahead of it, so on any machine without `~/.plaud-connector` — a
    # fresh checkout, a CI runner, a new developer — the cache fixtures were
    # refused for not existing and the assertion failed. Nothing in the suite
    # creates that directory: every test pins `PLAUD_CACHE_DIR`.
    # From the password database, not `$HOME` — round 8 found the home and
    # cache refusals reading a variable the same caller sets, which is round
    # 6's `TMPDIR` finding one variable over, and the parent's test computing
    # its expectations from the same variable so the two agreed by
    # construction.
    home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    # The repository and the cache are refused in BOTH directions — a root
    # that contains one, and a root inside one — because nothing legitimate
    # puts scratch there (round 8: a repo subdirectory and the cache one
    # level below the guarded name both passed).
    for what, guarded in (("this repository", SCRIPT.parent.parent),
                          ("the default cache directory", home / ".plaud-connector")):
        if guarded.is_relative_to(root) or root.is_relative_to(guarded):
            raise SystemExit(f"refusing to run: sandbox {root} is inside, or contains, {what}")
    # The HOME directory is refused one way only: as itself or as something
    # the root contains. Round 9 made this bidirectional too and broke every
    # machine whose `TMPDIR` resolves under the home directory — the parent's
    # sandbox is a `TemporaryDirectory`, so all eleven children refused to
    # start and blamed the sandbox. Scratch lives under the home directory on
    # ordinary machines. (Round 10 cited `tests/mutants_57.py` as the
    # precedent for this; round 10's own review pointed out that file has no
    # marker at all — its equivalent is a `.git` check nobody can forge into
    # absence — so the analogy was false and is withdrawn.)
    #
    # What is left under the home directory once this rule is one-directional:
    # every writable directory except the repository, `~/.plaud-connector` and
    # the home directory itself. The marker below narrows that to directories
    # whoever ran this invited, and the nonce-derived filenames keep the child
    # from colliding with anything already there — but a hand-written spec can
    # write its own marker, so this rule, not the marker, is what bounds the
    # damage. That is the residue of accepting `TMPDIR` under the home
    # directory, and it is stated here rather than papered over.
    if home == root or home.is_relative_to(root):
        raise SystemExit(f"refusing to run: sandbox {root} is, or contains, the home directory")
    if not root.is_dir():
        raise SystemExit(f"refusing to run: sandbox {root} is not a directory")
    try:
        stamp = (root / MARKER).read_text(encoding="utf-8")
    except OSError:
        raise SystemExit(f"refusing to run: sandbox {root} carries no {MARKER} marker — "
                         f"not a directory the parent test created") from None
    if not spec.get("nonce") or stamp != spec["nonce"]:
        raise SystemExit(f"refusing to run: the {MARKER} marker in {root} does not match the spec")
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

    def guard(p: pathlib.Path, verb: str) -> None:
        # Checked, then acted on — not one atomic step. The parent's fresh,
        # private sandbox makes the gap moot; a hand-run against a shared
        # directory does not, and the module docstring says so.
        if p.is_symlink() or not _contained(p, root):
            raise SystemExit(f"refusing to {verb} {p}: a symlink, or outside the sandbox")

    def write(p: pathlib.Path, text: str) -> None:
        guard(p, "write")
        p.write_text(text, encoding="utf-8")

    def mkdir(p: pathlib.Path) -> None:
        guard(p, "create")
        p.mkdir(exist_ok=True)

    # Every file this child writes carries the run's nonce, so a collision
    # with real data is impossible whatever the root turns out to be — the
    # refusals decide WHERE it may write, this decides that what it writes
    # cannot be something somebody already had (round 8: fixed names).
    # Per RUN and per PATH: the cache directory is shared by every child, so
    # a name that varied only by run had the two `--preview-sources` children
    # writing the same file at the same time.
    rec = f"probe-{spec['nonce'][:8]}-{path}"
    stray = io.StringIO()       # anything the library paths print (see `timed`)

    seen = {"role": "shape", "calls": 0, "longest": dict.fromkeys(ROLES, 0),
            "calls_max": dict.fromkeys(ROLES, 0), "cues_in": dict.fromkeys(ROLES, 0),
            "parsed_cues": dict.fromkeys(ROLES, 0), "parsed_lost": dict.fromkeys(ROLES, 0),
            "parsed_skipped": dict.fromkeys(ROLES, 0), "cue_chars": dict.fromkeys(ROLES, 0),
            "cues_out": dict.fromkeys(ROLES, 0), "srt_bytes": dict.fromkeys(ROLES, 0),
            "cue_chars_in": dict.fromkeys(ROLES, 0)}
    real = to_srt._match_segment

    def spy(line: str):
        seen["calls"] += 1
        if len(line) > seen["longest"][seen["role"]]:
            seen["longest"][seen["role"]] = len(line)
        return real(line)

    to_srt._match_segment = spy
    real_build = to_srt.build_cues

    def build_spy(segments, **kwargs):
        # The longest cue list `build_cues` was handed, per role. `main` and
        # `_cue_lines` both call it, and `--preview-sources` prints no ledger
        # at all, so the count has to be observed rather than read back out
        # of the CLI's output (round 8: the cue count was in the fixture and
        # in no assertion).
        if len(segments) > seen["cues_in"][seen["role"]]:
            seen["cues_in"][seen["role"]] = len(segments)
        # And what it RETURNED: how many cues, and the longest cue text.
        # Round 9 showed a silent `if len(seg["text"]) > 1_000_000: continue`
        # at the head of `build_cues` passing every test and running FASTER,
        # because the survivor shapes' whole job — pushing one multi-megabyte
        # cue through collapse, wrap and the writer — was simply not done any
        # more. Round 10 answered that by measuring the ARGUMENT, which the
        # cap does not touch, and the same mutant passed again, faster again.
        # The observation has to be downstream of the thing being observed.
        # Both ends of it. The argument says the characters ARRIVED; the
        # return says they were not dropped on the way through. Neither
        # implies the other: a `colon` survivor arrives 4 000 characters long
        # and leaves 3, because its text IS the whitespace run `collapse_runs`
        # exists to collapse — so the arrival number is the one that can be
        # asserted on every survivor, and the return number the one a cap
        # inside `build_cues` cannot fake.
        arrived = max((len(seg["text"]) for seg in segments), default=0)
        if arrived > seen["cue_chars_in"][seen["role"]]:
            seen["cue_chars_in"][seen["role"]] = arrived
        built = real_build(segments, **kwargs)
        if len(built) > seen["cues_out"][seen["role"]]:
            seen["cues_out"][seen["role"]] = len(built)
        longest = max((len(cue["text"]) for cue in built), default=0)
        if longest > seen["cue_chars"][seen["role"]]:
            seen["cue_chars"][seen["role"]] = longest
        return built

    to_srt.build_cues = build_spy
    real_parse = to_srt.parse_transcript

    def parse_spy(text, **kwargs):
        # The parser's own two growing lists, per role — the analogue of the
        # `build_cues` count for the paths that stop at the parser.
        cues, skipped, front, lost = real_parse(text, **kwargs)
        for key, value in (("parsed_cues", cues), ("parsed_lost", lost),
                           ("parsed_skipped", skipped)):
            if len(value) > seen[key][seen["role"]]:
                seen[key][seen["role"]] = len(value)
        return cues, skipped, front, lost

    to_srt.parse_transcript = parse_spy
    recorder = _Recorder(to_srt.SEGMENT)
    to_srt.SEGMENT = recorder                   # the helper looks it up at call time

    def beat(what: str) -> None:
        """One line per STAGE, not per shape. The parent's stall detector
        watches this stream, so its bound has to cover the longest gap
        between two lines — and a whole shape is six ceiling sizes plus a
        growth series plus, on a miss, a second growth series, which on the
        calibration machine is ~17 s and on a slow one is that times the
        machine. Round 10 printed once per shape and set the bound at 300 s,
        which is 18x of that, below the 23x its own comment measured for
        efficiency cores times load. Printing per stage makes the gap one
        measurement instead of a dozen, which is what lets the bound be
        generous in wall-clock terms and still tight in multiples."""
        print(what, file=sys.stderr, flush=True)

    def timed(line: str) -> tuple[float, dict | None]:
        """CPU seconds for one pass of `line` through `path`, plus exit info."""
        # The library paths get the same redirect the CLI path has always
        # had, set up OUTSIDE the timed region. Two reasons, both found in
        # round 10: this process's stdout IS the report channel, so one
        # `print` anywhere under `parse_transcript` would make the parent say
        # "probe exited 0 without a report" and point at the wrong thing;
        # and this process's stderr is what the parent's stall detector
        # watches for growth, so anything written there from inside a shape
        # would let a stuck child look like a live one until HARD_CAP.
        # Whatever lands in the sink is reported rather than swallowed.
        if path.startswith("matcher"):
            # The helper through the spy — one Python frame inside the timed
            # region, so the spy's count and length are measured, not stated.
            with contextlib.redirect_stdout(stray), contextlib.redirect_stderr(stray):
                t0 = time.process_time()
                spy(line)
                dt = time.process_time() - t0
            return dt, None
        if path.startswith("lib"):
            with contextlib.redirect_stdout(stray), contextlib.redirect_stderr(stray):
                t0 = time.process_time()
                to_srt.parse_transcript(line + "\n")
                dt = time.process_time() - t0
            return dt, None
        if path == "segments":
            with contextlib.redirect_stdout(stray), contextlib.redirect_stderr(stray):
                t0 = time.process_time()
                to_srt.parse_segments(line + "\n")
                dt = time.process_time() - t0
            return dt, None
        out = work / f"{rec}.srt"
        guard(out, "unlink")
        out.unlink(missing_ok=True)
        if path.startswith("cli-preview"):
            # `--preview-sources` → `differing_sample` → `_cue_lines` → the
            # parser, on BOTH the transcript and its polished twin. A line
            # that yields no cue is a drop on both sides; one that yields a
            # cue yields the same cue on both. Either way the comparison is
            # refused (exit 3) — after both files were parsed.
            mkdir(cache / "polish")
            write(cache / f"{rec}.md", "---\ntitle: x\n---\n" + line + "\n[00:10] S: hello\n")
            write(cache / "polish" / f"{rec}.md", line + "\n[00:10] S: hello\n")
            argv = ["to_srt.py", rec, "--preview-sources"]
        else:
            if path == "cli-header":
                text = "---\ntitle: x\n" + line + "\n---\n[00:10] S: hello\n"
            elif path == "cli-zero":
                text = line + "\n"
            elif path.startswith("cli-body"):
                text = "[00:10] S: hello\n" + line + "\n"
            else:
                raise SystemExit(f"unknown path {path!r}")
            src = work / f"{rec}.md"
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
        # The CLI's own ledger, parsed rather than pattern-matched by the
        # parent: `stdout` and `stderr` are truncated for diagnostics, and
        # round 8 found the cue count present in the fixture but asserted
        # nowhere — the numbers have to survive the trip in full.
        both = stdout.getvalue() + "\n" + stderr.getvalue()
        ledger = {}
        for key, pattern in (("cues", r"wrote (\d+) cues"),
                             ("dropped", r"(\d+) content line\(s\) dropped"),
                             ("lost_ends", r"(\d+) declared end\(s\) discarded"),
                             ("zero_dropped", r"(\d+) content line\(s\) and \d+ header"),
                             ("zero_stamped", r"(\d+) of the content lines DID carry"),
                             ("header", r"\((\d+) header[,)]")):
            m = re.search(pattern, both)
            ledger[key] = int(m.group(1)) if m else 0
        # The SIZE of what was written — the one number downstream of
        # `wrap_cue_text` and `render_srt`, and outside the timed region.
        # Round 10 found both of them capping silently with all 649 tests
        # green: `text[:1_000_000]` in the wrap deleted 2.2 MB of a user's
        # sentence, and `cues[:1000]` in the writer left 1 000 of 156 039
        # cues in a file whose success line still read `wrote 156039 cues`.
        # Neither touches anything `build_cues` returns, so nothing upstream
        # can see them.
        if out.exists():
            size = out.stat().st_size
            if size > seen["srt_bytes"][seen["role"]]:
                seen["srt_bytes"][seen["role"]] = size
        return dt, {"code": code, "wrote": out.exists(), "ledger": ledger,
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
            dc, _ = run_path(control_line(kind, line), "control")
            dp, last = run_path(line, "shape")
            bp, bc = min(bp, dp), min(bc, dc)
        return bp * 1000, bc * 1000, last

    def ceiling_ms(kind, prefix, tail, n, reps, bpc, skip=0) -> float:
        # Shape only: a ceiling is an absolute bound on the shape's own cost,
        # so the control has nothing to say here — measuring it doubled the
        # cheapest stage's cost for nothing. Byte-equalised like the growth
        # sizes (the docstring said sizes were, and this stage was not), and
        # offset off the growth grid: `GROWTH`'s two smaller sizes are also
        # ceiling sizes, so rep 0 of each built the string the ceiling had
        # just built and a memoizing helper could serve it (round 8).
        # `skip`: the re-measure starts PAST the reps the first measurement
        # built. It used to restart at rep 0, so its first line was the very
        # string the first pass had just measured — and a memoizing helper
        # (the ledger's M3, `lru_cache` on `_match_segment`) served it in no
        # time, `min` took that, and a shape that had just measured 361 ms
        # against a 100 ms ceiling "passed" its re-measure and went on to
        # spend 118 s on the next size. Round 11's per-stage progress lines
        # are what made this visible: under one line per shape it looked
        # like a stall and was killed as one, which is red for the wrong
        # reason.
        return min(run_path(build_line(kind, prefix, tail, n // bpc + 10 + rep), "shape")[0]
                   for rep in range(skip, skip + reps)) * 1000

    def growth_series(kind, prefix, tail, sizes, reps, skip=0):
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
        for rep in range(skip, skip + reps):    # `skip`: see `ceiling_ms`
            beat(f"{label}: growth rep {rep + 1}/{skip + reps}")
            for i, n in enumerate(sizes):
                line = build_line(kind, prefix, tail, n + rep)
                dc, _ = run_path(control_line(kind, line), "control")
                dp, last = run_path(line, "shape")
                bp[i], bc[i] = min(bp[i], dp), min(bc[i], dc)
        return [x * 1000 for x in bp], [x * 1000 for x in bc], last

    def fail(report, **why):
        report["failed"] = why
        print(json.dumps(report))
        return 2

    report = {"path": path, "shapes": [], "failed": None, "stray": ""}
    for kind, prefix, tail in spec["shapes"]:
        recorder.reset()
        seen["longest"], seen["calls_max"] = dict.fromkeys(ROLES, 0), dict.fromkeys(ROLES, 0)
        seen["cues_in"] = dict.fromkeys(ROLES, 0)
        seen["parsed_cues"] = dict.fromkeys(ROLES, 0)
        seen["parsed_lost"] = dict.fromkeys(ROLES, 0)
        seen["parsed_skipped"] = dict.fromkeys(ROLES, 0)
        seen["cue_chars"] = dict.fromkeys(ROLES, 0)
        seen["cues_out"] = dict.fromkeys(ROLES, 0)
        seen["cue_chars_in"] = dict.fromkeys(ROLES, 0)
        seen["srt_bytes"] = dict.fromkeys(ROLES, 0)
        # Bytes per character of the line as BUILT, not of its parts: a `cjk`
        # text is two bytes a character on a one-byte prefix and tail.
        bpc = bytes_per_char(build_line(kind, prefix, tail, 256))
        shape = {"kind": kind, "prefix": prefix, "tail": tail, "bpc": bpc,
                 "ceiling": {}, "growth": {}, "exit": None, "retried": False,
                 "spy_max": 0, "pattern_max": 0, "spy_calls": 0,
                 "control_spy_max": 0, "control_pattern_max": 0, "control_spy_calls": 0,
                 "cues_in": 0, "control_cues_in": 0, "parsed_cues": 0, "parsed_lost": 0,
                 "parsed_skipped": 0, "cue_chars": 0, "cues_out": 0, "srt_bytes": 0,
                 "cue_chars_in": 0}
        report["shapes"].append(shape)
        label = f"{path} {kind} {prefix!r} + {tail!r}"
        # Warm the path once, at the SMALLEST size, and discard it: the first
        # pass through a path pays one-time costs (a cold page cache for the
        # scratch file, a first `config` read) that would otherwise land in
        # the smallest ceiling — the tightest absolute bound in the class. At
        # the smallest size so that a cubic still dies here in milliseconds:
        # the ceilings exist so the growth sizes are never reached by one.
        # And at a size no rep re-uses (rep r builds `n + r`, the retry adds
        # two reps), so a memoizing helper cannot serve rep 0 from here.
        first = spec["ceilings"][0][0]
        best_pair(kind, prefix, tail, first // bpc + spec["ceiling_reps"] + 20, 1)
        for n, limit in spec["ceilings"]:
            ms = ceiling_ms(kind, prefix, tail, n, spec["ceiling_reps"], bpc)
            beat(f"{label}: ceiling n={n} {ms:.1f} ms")
            if ms >= limit:
                # Once more, with more reps, before calling it — the same
                # treatment the growth stage gets; a real quadratic misses a
                # 100x-headroom ceiling the second time as it did the first.
                ms = ceiling_ms(kind, prefix, tail, n, spec["ceiling_reps"] + 2, bpc,
                                skip=spec["ceiling_reps"])
                shape["retried"] = True
            shape["ceiling"][str(n)] = ms
            if ms >= limit:
                return fail(report, label=label, stage="ceiling", n=n, ms=ms)
        # Warm the first growth size for BOTH roles: the ceilings ran the
        # shape there but never its control, and a cold control at the first
        # size would shrink `dc1` and tighten the uniform bound.
        best_pair(kind, prefix, tail, spec["growth"][0] // bpc + 2 * spec["growth_reps"] + 20, 1)
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
            p, c, last = growth_series(kind, prefix, tail, sizes, spec["growth_reps"] + 4,
                                       skip=spec["growth_reps"])
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
        shape["cues_in"], shape["control_cues_in"] = (seen["cues_in"]["shape"],
                                                      seen["cues_in"]["control"])
        shape["parsed_cues"] = seen["parsed_cues"]["shape"]
        shape["parsed_lost"] = seen["parsed_lost"]["shape"]
        shape["parsed_skipped"] = seen["parsed_skipped"]["shape"]
        shape["cue_chars"] = seen["cue_chars"]["shape"]
        shape["cues_out"] = seen["cues_out"]["shape"]
        shape["cue_chars_in"] = seen["cue_chars_in"]["shape"]
        shape["srt_bytes"] = seen["srt_bytes"]["shape"]
        if verdict is not None:
            report["stray"] = stray.getvalue()[:500]
            return fail(report, label=label, **verdict)
        print(f"{label}: " + " ".join(f"{n}={mp:.3f}/{mc:.3f}" for n, (mp, mc)
                                      in shape["growth"].items())
              + f" pattern_max={shape['pattern_max']}/{shape['control_pattern_max']}",
              file=sys.stderr, flush=True)
    report["stray"] = stray.getvalue()[:500]
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
