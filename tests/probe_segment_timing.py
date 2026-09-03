#!/usr/bin/env python3
"""Child process for `TestSegmentMatchingHasOneEntryPoint` (#57).

Not a test module — `unittest discover` only collects `test*.py`. The parent
test spawns this once per reachable path, hands it a JSON spec (a file path
in argv[1]; stdin when run by hand), and reads a JSON report from stdout.
It is a separate PROCESS, not a helper function, because three things the
in-process version could not have are had at once:

  * a HARD TIMEOUT. A regex that has gone quadratic cannot be interrupted
    from inside the interpreter — `_sre` does not check for signals — but a
    child can be killed. Round 3 pointed out that the old ceiling assertion
    ran only after the function it was timing had returned, so a regression
    bad enough would stall CI to the job limit instead of failing in 100 ms.
  * `cli_env` ISOLATION. The old in-process `_cli_ms` was the one CLI
    exercise in the test file that went around `cli_env`, so the developer's
    real config file sat inside the timed region. The parent passes
    `cli_env(...)` as this process's environment; this process IS the CLI,
    so `config.load_config()` reads the pinned, absent path.
  * a SPY on the chokepoint. Every path is proven to have handed the
    pathological line to `_match_segment`. The old CLI test swallowed
    `SystemExit` and could not tell a timed parse from an early argparse
    exit — Codex's round-3 finding.

Per-rep content is DISTINCT (the tail is `n + rep` long). Round 3's Codex
lens showed that `min()` over identical inputs lets an `lru_cache` on the
helper hide any amount of first-call work.

Progress goes to stderr one line per shape, so when the parent kills this
process on timeout the last line names the shape it died on. Exit 0 with a
full report; exit 2 with a partial report the moment a ceiling or the
growth criterion fails, so a gross quadratic is red after the 3 200-character
stage — about a second — and a slow one after its first shape.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import time
from unittest import mock


def _load(script: str):
    spec = importlib.util.spec_from_file_location("to_srt", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            spec = json.load(fh)
    else:
        spec = json.load(sys.stdin)
    to_srt = _load(spec["script"])
    path = spec["path"]

    seen = {"max": 0, "calls": 0}
    real = to_srt._match_segment

    def spy(line: str):
        seen["calls"] += 1
        if len(line) > seen["max"]:
            seen["max"] = len(line)
        return real(line)

    to_srt._match_segment = spy

    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)

        def run_once(prefix: str, tail: str, n: int, rep: int) -> tuple[float, dict | None]:
            line = prefix + tail * (n + rep)
            if path == "lib":
                t0 = time.perf_counter()
                to_srt.parse_transcript(line + "\n")
                return time.perf_counter() - t0, None
            if path == "segments":
                t0 = time.perf_counter()
                to_srt.parse_segments(line + "\n")
                return time.perf_counter() - t0, None
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
            out = work / "out.srt"
            out.unlink(missing_ok=True)
            argv = ["to_srt.py", "--file", str(src), "-o", str(out)]
            stdout, stderr = io.StringIO(), io.StringIO()
            code = 0
            with mock.patch.object(sys, "argv", argv), \
                 contextlib.redirect_stdout(stdout), \
                 contextlib.redirect_stderr(stderr):
                t0 = time.perf_counter()
                try:
                    to_srt.main()
                except SystemExit as e:
                    # `sys.exit(str)` is status 1 and the interpreter would
                    # have printed the string to stderr; caught here, it
                    # would vanish, so print it where the parent looks.
                    if isinstance(e.code, int):
                        code = e.code
                    else:
                        code = 1
                        if e.code is not None:
                            stderr.write(str(e.code))
                dt = time.perf_counter() - t0
            return dt, {"code": code, "wrote": out.exists(),
                        "stdout": stdout.getvalue()[:200],
                        "stderr": stderr.getvalue()[:300]}

        def best_ms(prefix: str, tail: str, n: int, reps: int) -> tuple[float, dict | None]:
            best, last = float("inf"), None
            for rep in range(reps):
                dt, last = run_once(prefix, tail, n, rep)
                best = min(best, dt)
            return best * 1000, last

        report = {"path": path, "shapes": [], "failed": None}
        for prefix in spec["prefixes"]:
            for tail in spec["tails"]:
                seen["max"], seen["calls"] = 0, 0
                shape = {"prefix": prefix, "tail": tail, "ceiling": {}, "growth": {},
                         "spy_max": 0, "spy_calls": 0, "exit": None}
                report["shapes"].append(shape)
                for n, limit in spec["ceilings"]:
                    ms, _ = best_ms(prefix, tail, n, spec["ceiling_reps"])
                    shape["ceiling"][str(n)] = ms
                    if ms >= limit:
                        report["failed"] = {"prefix": prefix, "tail": tail,
                                            "stage": "ceiling", "n": n, "ms": ms}
                        print(json.dumps(report))
                        return 2
                for n in spec["growth"]:
                    ms, last = best_ms(prefix, tail, n, spec["growth_reps"])
                    shape["growth"][str(n)] = ms
                    shape["exit"] = last
                shape["spy_max"], shape["spy_calls"] = seen["max"], seen["calls"]
                # The parent judges every shape of a full report; this only
                # stops the run at the first shape that has already failed,
                # so a slow quadratic is red after ONE shape, not after 100.
                t = [shape["growth"][str(n)] for n in spec["growth"]]
                d1, d2 = t[1] - t[0], t[2] - t[1]
                if d2 > 8 * d1 + spec["slack_ms"]:
                    report["failed"] = {"prefix": prefix, "tail": tail, "stage": "growth",
                                        "times": t, "ratio": d2 / max(d1, 1e-9)}
                    print(json.dumps(report))
                    return 2
                print(f"{path} {prefix!r} + {tail!r}: "
                      + " ".join(f"{k}={v:.3f}" for k, v in shape["growth"].items()),
                      file=sys.stderr, flush=True)
        print(json.dumps(report))
        return 0


if __name__ == "__main__":
    sys.exit(main())
