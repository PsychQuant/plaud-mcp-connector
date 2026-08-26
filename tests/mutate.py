#!/usr/bin/env python3
"""Change a printed value without touching the print, and see who notices.

Round 14 certified "every count the tool prints is compared as an integer" with
a sweep that rewrote each interpolation from `{x}` to `{x+40}`. That edit
changes the expression TEXT, which is precisely what
`TestNoValueReachesAStreamUnchecked` watches — so the registry test fired at
every site by construction and the sweep turned red everywhere while proving
nothing about any value assertion. One site (`len(odd)`) had no value assertion
at all and the sweep could not tell.

So this rebinds the underlying NAME on its own line, immediately before the
statement that prints it, and leaves the f-string alone. It then reports the
failure count with the registry test excluded, because the registry test is
about the shape of the source and not about the value.

It also prints what it could NOT mutate. A sweep that silently skips what it
cannot handle reads as exhaustive and is not — the same defect, one level up.

    python3 tests/mutate.py            # every name a printed value depends on
    python3 tests/mutate.py --quiet    # only the sites nothing catches
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "to_srt.py"
REGISTRY_TEST = "TestNoValueReachesAStreamUnchecked"
INJECT = "{name} = ({name} * 3) if hasattr({name}, '__len__') else ({name} + 40)"


def _plan(tree: ast.AST) -> tuple[list, list]:
    """(mutatable, unmutatable) — one entry per (function, name, statement)."""
    parent = {}
    func = {}
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
            names = sorted({n.id for n in ast.walk(value.value)
                            if isinstance(n, ast.Name) and n.id.islower()})
            if not names:
                skipped.append((func.get(id(node), "?"), expr,
                                "no plain local name to rebind"))
                continue
            for name in names:
                todo.append((func.get(id(node), "?"), expr, name,
                             stmt.lineno, stmt.col_offset))
    return todo, skipped


def _run_with(lines: list[str], at: int, col: int, name: str) -> tuple[int, list]:
    src = SCRIPT.read_text(encoding="utf-8")
    mutated = lines[:at - 1] + [" " * col + INJECT.format(name=name)] + lines[at - 1:]
    SCRIPT.write_text("\n".join(mutated), encoding="utf-8")
    try:
        # No bytecode cache. Python keys it on (mtime, size), and a mutation
        # that changes a value without changing the byte count — `\d{1,4}` to
        # `\d{1,2}`, say — restored inside the same clock second is invisible
        # to that key, so the next run silently executes the PREVIOUS source.
        # A measurement harness that can report a stale answer is the exact
        # instrument this branch keeps being betrayed by; caught here by a
        # revert that appeared not to take.
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                               "-s", str(REPO / "tests"), "-q"],
                              capture_output=True, text=True, cwd=REPO, env=env)
    finally:
        SCRIPT.write_text(src, encoding="utf-8")
    hits = [l.split()[1] for l in proc.stderr.splitlines()
            if l.startswith(("FAIL: ", "ERROR: "))]
    real = [h for h in hits if REGISTRY_TEST not in h]
    return len(real), real


def main() -> int:
    quiet = "--quiet" in sys.argv
    src = SCRIPT.read_text(encoding="utf-8")
    lines = src.split("\n")
    todo, skipped = _plan(ast.parse(src))

    # Partition against the registry. A survivor is only a DEFECT if something
    # claims to check it; a survivor registered as UNCHECKED is that claim
    # being kept. The two artefacts are maintained independently — the registry
    # is not derived from these results and these results do not read the
    # registry to decide what to mutate — so crossing them is a check, not the
    # self-reference this harness exists to replace.
    sys.path.insert(0, str(REPO / "tests"))
    import importlib
    registry = importlib.import_module("test_to_srt").TestNoValueReachesAStreamUnchecked
    checked = {(f, e) for f, _, e in registry.CHECKED}

    seen, survivors = set(), []
    for fn, expr, name, at, col in todo:
        key = (fn, name, at)
        if key in seen:
            continue
        seen.add(key)
        n, hits = _run_with(lines, at, col, name)
        if n == 0:
            survivors.append((fn, expr, name, at))
        if not quiet:
            mark = "  SURVIVES" if n == 0 else ""
            print(f"  {fn:18s} line {at:4d}  {name:16s} ({expr[:28]:28s}) "
                  f"-> {n:2d} failing{mark}")

    claimed = [s for s in survivors if (s[0], s[1]) in checked]
    declared = [s for s in survivors if (s[0], s[1]) not in checked]
    print(f"\n{len(seen)} mutations, {len(survivors)} survive "
          f"(the registry test is excluded from every count — it fires on a "
          f"changed expression, not a changed value, which is what made round "
          f"14's sweep unfalsifiable)")
    print(f"  {len(declared)} survive and are registered UNCHECKED — expected")
    for fn, expr, name, at in claimed:
        where = [v for k, v in registry.CHECKED.items() if (k[0], k[2]) == (fn, expr)]
        print(f"  DEFECT  {fn}:{at}  {expr}  (rebound `{name}`) — claimed "
              f"covered by {where}")
    if claimed:
        print("\n  NOTE: a survivor is 'uncaught' OR 'the mutation changed "
              "nothing that gets printed' — e.g. rebinding one argument of a "
              "min() whose other argument always wins. This harness cannot "
              "tell those apart; check each by hand rather than assuming the "
              "worse or the better one.")
    if skipped:
        print(f"\n{len(skipped)} site(s) this harness cannot mutate — NOT a "
              f"claim that they are covered:")
        for fn, expr, why in sorted(set(skipped)):
            print(f"  {fn:18s} {expr[:40]:40s} {why}")
    print(f"\nRESULT: {len(claimed)} registered-as-checked value(s) survive "
          f"mutation." + ("" if claimed else "  none — every claim bites."))
    return 1 if claimed else 0


if __name__ == "__main__":
    raise SystemExit(main())
