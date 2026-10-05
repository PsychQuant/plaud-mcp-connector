#!/usr/bin/env python3
"""Report skill names this plugin shares with other installed plugins (#76).

Skills from every enabled plugin land in one `/` menu. When two plugins ship a skill
with the same name, the user's command is ambiguous and which one answers is not
something either plugin controls. That cannot be checked statically — it depends on
what else is installed on a given machine — so this reads the plugin cache and
compares.

What it compares, and what it deliberately does not:

* OUR names come from `skills/*/SKILL.md` in this repo (frontmatter `name:`, falling
  back to the directory name). Maintainer skills under `.claude/skills/` are not
  installed with the plugin, so they cannot collide and are not scanned.
* THEIR names come from the newest installed version of every other plugin under the
  cache (`<marketplace>/<plugin>/<version>/skills/*/SKILL.md`). Older versions on disk
  are ignored: Claude Code loads the newest, so an old copy is not a collision.
  "Newest" is numeric for dotted versions ("1.10.0" is newer than "1.9.0"); a plugin
  versioned by hash has no order to compare, so its most recently modified directory
  is used.
* Our own installed copy is skipped, by the name in `.claude-plugin/plugin.json`.
* Also reports two of OUR skills declaring the same name.

Exit status: 0 clean (or nothing installed to compare against), 1 collision found,
2 the repo has no skills/ directory to read.

Usage:
    check_skill_collisions.py [--repo PATH] [--plugins-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

DEFAULT_PLUGINS_DIR = pathlib.Path.home() / ".claude" / "plugins" / "cache"
OWN_PLUGIN_FALLBACK = "plaud-mcp-connector"
SEMVER = re.compile(r"^\d+(\.\d+)*$")


def frontmatter_name(skill_md: pathlib.Path) -> str | None:
    """`name:` from the YAML frontmatter only — it also appears in prose further down."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    front = text[3:end] if end != -1 else text
    match = re.search(r"^name:\s*(\S+)\s*$", front, re.MULTILINE)
    return match.group(1) if match else None


def skills_in(skills_dir: pathlib.Path) -> dict[str, list[str]]:
    """{declared name: [directory names]} for every skill under `skills_dir`."""
    found: dict[str, list[str]] = {}
    if not skills_dir.is_dir():
        return found
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            continue
        found.setdefault(frontmatter_name(skill_md) or d.name, []).append(d.name)
    return found


def newest_version(plugin_dir: pathlib.Path) -> pathlib.Path | None:
    versions = [p for p in plugin_dir.iterdir() if p.is_dir()]
    if not versions:
        return None
    dotted = [p for p in versions if SEMVER.match(p.name)]
    if dotted:
        return max(dotted, key=lambda p: tuple(int(x) for x in p.name.split(".")))
    return max(versions, key=lambda p: p.stat().st_mtime)


def own_plugin_name(repo: pathlib.Path) -> str:
    try:
        manifest = json.loads((repo / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return manifest.get("name") or OWN_PLUGIN_FALLBACK
    except (OSError, ValueError):
        return OWN_PLUGIN_FALLBACK


def installed_skills(plugins_dir: pathlib.Path, skip_plugin: str):
    """Yield (name, 'marketplace/plugin version') for the newest version of each plugin."""
    for marketplace in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        for plugin in sorted(p for p in marketplace.iterdir() if p.is_dir()):
            if plugin.name == skip_plugin:
                continue
            version = newest_version(plugin)
            if version is None:
                continue
            for name in skills_in(version / "skills"):
                yield name, f"{marketplace.name}/{plugin.name} {version.name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent)
    ap.add_argument(
        "--plugins-dir",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("PLAUD_COLLISION_PLUGINS_DIR", DEFAULT_PLUGINS_DIR)),
    )
    args = ap.parse_args(argv)

    ours = skills_in(args.repo / "skills")
    if not (args.repo / "skills").is_dir():
        print(f"error: {args.repo}/skills is not a directory", file=sys.stderr)
        return 2

    collisions = []
    for name, dirs in sorted(ours.items()):
        if len(dirs) > 1:
            collisions.append(f"`{name}` is declared by two of our skills: {', '.join(dirs)}")

    if args.plugins_dir.is_dir():
        for name, where in installed_skills(args.plugins_dir, own_plugin_name(args.repo)):
            if name in ours:
                collisions.append(f"`{name}` is also shipped by {where}")
    else:
        print(f"nothing to compare: {args.plugins_dir} does not exist", file=sys.stderr)

    if collisions:
        print(f"{len(collisions)} name collision(s):")
        for line in collisions:
            print(f"  - {line}")
        return 1
    print(f"no collisions among {len(ours)} skill name(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
