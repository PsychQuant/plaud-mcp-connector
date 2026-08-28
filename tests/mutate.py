#!/usr/bin/env python3
"""Change a printed value without touching the print, and see who notices.

Round 14 certified "every count the tool prints is compared as an integer"
with a sweep that rewrote each interpolation from `{x}` to `{x+40}`. That edit
changes the expression TEXT, which is what `TestNoValueReachesAStreamUnchecked`
watches — so the registry test fired at every site by construction and the
sweep turned red everywhere while proving nothing about any value assertion.

So this rebinds the underlying NAME on its own line, immediately before the
statement that prints it, and leaves the f-string alone.

Round 17 found four defects in the round-16 version of this file, all of them
the same family it exists to detect, and all of them fixed here:

  * The exclusion of the self-referential test was DEAD CODE. It compared
    `line.split()[1]` — the bare method name — against the CLASS name, which
    never appears in that token. Had the registry test ever gone red, every
    site would have scored "caught" and the run would have printed
    `every claim bites` over a measurement of nothing. It matches on the
    dotted path now.
  * A CRASH scored as a catch. The injection was applied to every lowercase
    name in the expression, including `len`, `str`, `path` and `args`;
    rebinding `len` inside a function makes it a local for the whole function,
    so the tool dies on the first `len(...)` and 80 tests fail — none of them
    because a number moved. Crashes are their own bucket now and never count
    as coverage.
  * The survivor partition asked "not in CHECKED" rather than "in UNCHECKED",
    so a site in NEITHER registry printed as `registered UNCHECKED — expected`.
    Unregistered survivors are a third bucket and a defect.
  * It mutated the TRACKED WORKING TREE and restored in a `finally`, which
    does not survive a kill and does not isolate concurrent readers. Two
    reviewers reported measurements corrupted by it mid-run — which is round
    15's own finding, reproduced by the tool written in response to it. It
    works in a throwaway copy now, and the tree is never touched.

    python3 tests/mutate.py            # every name a printed value depends on
    python3 tests/mutate.py --quiet    # only the sites nothing catches

What it still cannot do, said here rather than discovered later: the crash
probe runs one fixture, so it sees a crash only on the paths that fixture
reaches; sites whose mutation breaks a branch it does not visit are caught by
a breadth heuristic instead, and reported as a heuristic. Every count it
prints is a lower bound on the holes, never an upper bound on the coverage.
"""
from __future__ import annotations

import ast
import builtins
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
REGISTRY_TEST = "TestNoValueReachesAStreamUnchecked"
INJECT = "{name} = ({name} * 3) if hasattr({name}, '__len__') else ({name} + 40)"
# For a `holder['key']` site: rebuild the holder with that one value moved.
INJECT_SUB = ("{holder} = {{**{holder}, {key!r}: "
              "(({holder}[{key!r}] * 3) if hasattr({holder}[{key!r}], '__len__') "
              "else ({holder}[{key!r}] + 40))}}")
# Failing this many tests is not what catching one wrong number looks like.
BROAD = 25

# Names that are not local values. Rebinding one of these does not change a
# printed number, it breaks the program — and a broken program fails tests for
# a reason that has nothing to do with whether anyone checks the number.
NOT_A_LOCAL = set(dir(builtins)) | {
    "argparse", "ast", "importlib", "os", "pathlib", "re", "shutil",
    "subprocess", "sys", "tempfile", "unicodedata", "config", "to_srt",
    "args", "self", "cls",
}


def _plan(tree: ast.AST) -> tuple[list, list]:
    """(mutatable, unmutatable) — one entry per (function, name, statement)."""
    # A module-level function is not a value. Rebinding `format_timestamp`
    # makes it a local and the tool dies on the first call, which the round-21
    # `UNMEASURED` verdict correctly refused to score as coverage — but the
    # right answer is to mutate the VALUE at that site instead of giving up.
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    parent, func = {}, {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                func[id(child)] = node.name

    todo, skipped = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        stmt = node
        while id(stmt) in parent and not isinstance(stmt, ast.stmt):
            stmt = parent[id(stmt)]
        if not isinstance(stmt, ast.stmt):
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expr = ast.unparse(value.value)
            # A name used ONLY as `name[...]` is a container, not a value.
            # Rebinding it replaces the dict and the tool dies on the next
            # lookup — round 21's `UNMEASURED` verdict caught four sites in
            # exactly that state. The value is the subscript, so that is what
            # gets moved.
            holders = {n.value.id for n in ast.walk(value.value)
                       if isinstance(n, ast.Subscript)
                       and isinstance(n.value, ast.Name)}
            names = sorted({n.id for n in ast.walk(value.value)
                            if isinstance(n, ast.Name)
                            and n.id.islower()
                            and n.id not in NOT_A_LOCAL
                            and n.id not in defined
                            and n.id not in holders})
            # `format_timestamp(cue['start'])` has no rebindable value name —
            # only a function and a dict. The VALUE is the subscript, so
            # mutate that: `cue = {**cue, 'start': cue['start'] + 40}` changes
            # the number the site prints and leaves everything else standing.
            subs = sorted({(n.value.id, n.slice.value)
                           for n in ast.walk(value.value)
                           if isinstance(n, ast.Subscript)
                           and isinstance(n.value, ast.Name)
                           and isinstance(n.slice, ast.Constant)
                           and isinstance(n.slice.value, str)})
            if subs:
                for holder, key in subs:
                    todo.append((func.get(id(node), "?"), expr,
                                 f"{holder}[{key!r}]", stmt.lineno,
                                 stmt.col_offset))
            if not names and not subs:
                skipped.append((func.get(id(node), "?"), expr,
                                "no rebindable local name in the expression"))
                continue
            for name in names:
                todo.append((func.get(id(node), "?"), expr, name,
                             stmt.lineno, stmt.col_offset))
    return todo, skipped


class Sandbox:
    """A throwaway copy. The tracked tree is never written to.

    `finally`-based restore of the real file was the round-16 design; it does
    not survive SIGKILL, it re-read its baseline from the live file so one
    interrupted iteration pinned a mutation permanently, and anything else
    reading the repo during a sweep read injected code.
    """

    def __enter__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="idd-mutate-"))
        self.root = self.dir / "repo"
        # Copied file by file, tolerating files that vanish mid-walk. The
        # working tree is LIVE — other tools write scratch state into it — and
        # `shutil.copytree` aborts the whole copy when one entry disappears
        # between the listing and the read, which is exactly what happened
        # (`.remember/tmp/capture-alive.<pid>`). Enumerating what to copy
        # instead would be a list that goes stale the next time the repo grows
        # a directory, which is the failure this whole file is about.
        skip = {".git", "__pycache__", ".claude", "node_modules", ".remember",
                ".pytest_cache", ".venv"}
        for src in REPO.rglob("*"):
            rel = src.relative_to(REPO)
            if any(part in skip for part in rel.parts):
                continue
            dst = self.root / rel
            try:
                if src.is_dir():
                    dst.mkdir(parents=True, exist_ok=True)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            except (FileNotFoundError, OSError):
                continue          # it went away, or it is a live socket
        subprocess.run(["git", "init", "-q", "."], cwd=self.root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=x",
                        "commit", "-qm", "baseline"], cwd=self.root, check=True)
        self.script = self.root / "scripts" / "to_srt.py"
        self.pristine = self.script.read_text(encoding="utf-8")
        # The probe file exercises every branch that PRINTS, because a probe
        # that only runs the happy path cannot see a crash in the branch the
        # mutation is in — the first draft ran a two-cue file and reported
        # `path` as fine while `path + 40` raises. One header oddity, one
        # dropped line, one discarded end, one control character, one
        # duplicate start, one over-long cue.
        # A SECOND cache, for the `--preview-sources` path. The single probe
        # below never runs it, so every crash in `differing_sample` and
        # `_cue_lines` — a whole subsystem, and the one whose output an
        # operator quotes to a user — was invisible to the classifier and its
        # sites were scored as covered. A probe that visits one code path
        # gives a clean bill about the paths it did not visit.
        self.preview_cache = self.root / "previewcache"
        (self.preview_cache / "polish").mkdir(parents=True)
        (self.preview_cache / "p1.md").write_text(
            "---\nid: p1\n---\n[00:00] S: um the budget\n[00:10] S: and then\n",
            encoding="utf-8")
        (self.preview_cache / "polish" / "p1.md").write_text(
            "[00:00] S: the budget\n[00:10] S: and then\n", encoding="utf-8")
        self.probe_file = self.root / "probe.md"
        self.probe_file.write_text(
            "---\n"
            "id: probe\n"
            "somebody speaking inside the header\n"
            "---\n"
            "[00:01] S: one\x07two\n"
            "[00:10 - 00:412] S: a declared end that cannot be read\n"
            "[00:20] S: " + "long " * 30 + "\n"
            "[00:20] S: the same start twice\n"
            "[9999:99] S: a line that cannot parse at all\n",
            encoding="utf-8")
        return self

    def still_runs(self) -> str | None:
        """Does the mutated tool run at all? A reason, or None if it is fine.

        Detecting a crash by counting `ERROR:` lines does not work here and
        the round-18 first draft got it wrong: nearly every test in this suite
        drives the CLI through `subprocess`, so a tool that dies produces
        WRONG OUTPUT, which unittest reports as `FAIL`. Rebinding `len` inside
        `main` scored 77 clean value-failures that way — indistinguishable, by
        that method, from 77 assertions catching a wrong number.

        So ask the tool directly instead of inferring from the suite.
        """
        runs = [
            ([str(self.script), "--file", str(self.probe_file),
              "-o", str(self.root / "probe.srt")], {}, (0,)),
            # The preview path. Exit 3 is a refusal and a normal outcome here,
            # so both 0 and 3 are healthy; anything else, or a traceback, is
            # the tool dying.
            ([str(self.script), "p1", "--preview-sources"],
             {"PLAUD_CACHE_DIR": str(self.preview_cache),
              "PLAUD_CONFIG": str(self.root / "probe-config.json")}, (0, 3)),
        ]
        for argv, extra, ok in runs:
            proc = subprocess.run(
                [sys.executable, "-B", *argv], capture_output=True, text=True,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **extra))
            if "Traceback (most recent call last)" in proc.stderr:
                first = [l for l in proc.stderr.splitlines()
                         if l and not l[0].isspace()]
                return first[-1][:80] if first else "traceback"
            if proc.returncode not in ok:
                return f"{argv[1]}: exit {proc.returncode}"
        return None

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run(self, source: str | None = None) -> tuple[list, list]:
        """(value failures, crashes) for a given source, or the baseline."""
        self.script.write_text(source if source is not None else self.pristine,
                               encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover",
             "-s", str(self.root / "tests"), "-q"],
            capture_output=True, text=True, cwd=self.root, env=env)
        fails, errors = [], []
        for line in proc.stderr.splitlines():
            if not line.startswith(("FAIL: ", "ERROR: ")):
                continue
            # The DOTTED PATH, not the bare method name — the class name lives
            # only inside the parentheses, and comparing against the method
            # name is why the registry exclusion never once fired.
            path = line.split("(")[-1].rstrip(")") if "(" in line else line
            (errors if line.startswith("ERROR: ") else fails).append(path)
        return ([f for f in fails if REGISTRY_TEST not in f],
                [e for e in errors if REGISTRY_TEST not in e])


def main() -> int:
    quiet = "--quiet" in sys.argv
    src = (REPO / "scripts" / "to_srt.py").read_text(encoding="utf-8")
    lines = src.split("\n")
    todo, skipped = _plan(ast.parse(src))

    sys.path.insert(0, str(REPO / "tests"))
    import importlib
    registry = importlib.import_module("test_to_srt").TestNoValueReachesAStreamUnchecked
    checked = {(f, e) for f, _, e in registry.CHECKED}
    unchecked = {(f, e) for f, _, e in registry.UNCHECKED}

    with Sandbox() as box:
        base_fails, base_errors = box.run()
        # Environmental failures are SUBTRACTED and NAMED, not tolerated in
        # silence. A fresh `git init` in the copy does not reproduce the real
        # repo's tracked state, so one gitignore test fails here for a reason
        # that has nothing to do with any mutation. Letting that sit in the
        # baseline would make every site score one free "catch".
        baseline = set(base_fails) | set(base_errors)
        if baseline:
            print(f"{len(baseline)} test(s) already fail in the sandbox for "
                  f"environmental reasons and are excluded from every count "
                  f"below:")
            for t in sorted(baseline):
                print(f"  {t}")
            print()
        if box.still_runs() is not None:
            print(f"the unmutated tool does not run in the sandbox "
                  f"({box.still_runs()}) — nothing below would mean anything")
            return 2

        seen, survivors, crashed, broad = set(), [], [], []
        for fn, expr, name, at, col in todo:
            key = (fn, name, at)
            if key in seen:
                continue
            seen.add(key)
            if name.endswith("']"):
                holder, key = name.split("[", 1)
                line = INJECT_SUB.format(holder=holder, key=key[1:-2])
            else:
                line = INJECT.format(name=name)
            mutated = lines[:at - 1] + [" " * col + line] + lines[at - 1:]
            box.script.write_text("\n".join(mutated), encoding="utf-8")
            broke = box.still_runs()
            if broke is not None:
                # The tests will fail, and none of it is evidence: the program
                # died, no printed value ever moved.
                crashed.append((fn, expr, name, at, broke))
                if not quiet:
                    print(f"  {fn:18s} line {at:4d}  {name:16s} "
                          f"({expr[:28]:28s}) -> CRASHED ({broke[:40]})")
                continue
            fails, errors = box.run("\n".join(mutated))
            fails = [f for f in fails if f not in baseline]
            errors = [e for e in errors if e not in baseline]
            # A crash the probe could not reach still fails tests, and those
            # failures are not evidence. Breadth is the only signal left: a
            # value assertion catches a wrong number in the handful of tests
            # that read that message, while a dead tool fails everything. This
            # is a HEURISTIC and is reported as one — the alternative is to go
            # on counting crashes as coverage, which is the defect being fixed.
            if len(fails) >= BROAD:
                broad.append((fn, expr, name, at, len(fails)))
            if errors:
                crashed.append((fn, expr, name, at, f"{len(errors)} errors"))
                mark, n = "  CRASHED", len(fails)
            elif not fails:
                survivors.append((fn, expr, name, at))
                mark, n = "  SURVIVES", 0
            else:
                mark, n = "", len(fails)
            if not quiet:
                print(f"  {fn:18s} line {at:4d}  {name:16s} "
                      f"({expr[:28]:28s}) -> {n:2d} failing{mark}")

    claimed = [s for s in survivors if (s[0], s[1]) in checked]
    declared = [s for s in survivors if (s[0], s[1]) in unchecked]
    orphan = [s for s in survivors
              if (s[0], s[1]) not in checked and (s[0], s[1]) not in unchecked]

    print(f"\n{len(seen)} mutations, {len(survivors)} survive, "
          f"{len(crashed)} crashed (the registry test is excluded from every "
          f"count — it fires on a changed expression, not a changed value, "
          f"which is what made round 14's sweep unfalsifiable)")
    print(f"  {len(declared)} survive and are registered UNCHECKED — expected")
    for fn, expr, name, at in claimed:
        where = [v for k, v in registry.CHECKED.items() if (k[0], k[2]) == (fn, expr)]
        print(f"  DEFECT  {fn}:{at}  {expr}  (rebound `{name}`) — claimed "
              f"covered by {where}")
    for fn, expr, name, at in orphan:
        print(f"  DEFECT  {fn}:{at}  {expr}  (rebound `{name}`) — in NEITHER "
              f"registry, so nothing ever claimed or disclaimed it")
    if claimed or orphan:
        print("\n  NOTE: a survivor is 'uncaught' OR 'the mutation changed "
              "nothing that gets printed' — e.g. rebinding one argument of a "
              "min() whose other argument always wins. This harness cannot "
              "tell those apart; check each by hand rather than assuming the "
              "worse or the better one.")
    if crashed:
        print(f"\n{len(crashed)} mutation(s) CRASHED the tool rather than "
              f"changing a printed value. These are NOT evidence of coverage "
              f"— the tests failed because the program died:")
        for fn, expr, name, at, why in crashed:
            print(f"  {fn}:{at}  {expr}  (rebound `{name}`) — {why}")
    if broad:
        print(f"\n{len(broad)} mutation(s) failed {BROAD}+ tests. A wrong "
              f"number is read by a handful of tests; a dead tool fails "
              f"everything. These are flagged, not classified — the crash "
              f"probe only sees crashes on the paths its fixture reaches, so "
              f"verify each by hand before counting it as coverage:")
        for fn, expr, name, at, n in broad:
            print(f"  {fn}:{at}  {expr}  (rebound `{name}`) — {n} failing")
    if skipped:
        print(f"\n{len(skipped)} site(s) this harness cannot mutate — NOT a "
              f"claim that they are covered:")
        for fn, expr, why in sorted(set(skipped)):
            print(f"  {fn:18s} {expr[:40]:40s} {why}")

    # A CHECKED site whose mutation CRASHED was never measured. It is not a
    # survivor and it is not a catch — it is a hole, and printing
    # "every claim bites" over it makes an unmeasured site read exactly like a
    # verified one. That is this branch's whole subject with the instrument as
    # the subject instead of the tool.
    crashed_checked = [c for c in crashed if (c[0], c[1]) in checked]
    if crashed_checked:
        print(f"\n{len(crashed_checked)} registered-as-checked site(s) were "
              f"NOT MEASURED — their mutation killed the tool, so nothing was "
              f"learned about whether the claim holds:")
        for fn, expr, name, at, why in crashed_checked:
            print(f"  UNMEASURED  {fn}:{at}  {expr}  (rebound `{name}`) — {why}")

    bad = len(claimed) + len(orphan)
    print(f"\nRESULT: {bad} registered-as-checked or unregistered value(s) "
          f"survive mutation; {len(crashed_checked)} more were not measured at "
          f"all." + ("  Every claim that could be tested bites."
                     if not bad and not crashed_checked else ""))
    return 1 if (bad or crashed_checked) else 0


if __name__ == "__main__":
    raise SystemExit(main())
