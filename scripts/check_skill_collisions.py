#!/usr/bin/env python3
"""Report skill names this plugin shares with other installed plugins (#76).

Skills from every enabled plugin land in one `/` menu. When two plugins ship a skill
with the same name, the user's command is ambiguous and which one answers is not
something either plugin controls. That cannot be checked statically — it depends on
what else is installed on a given machine — so this reads the install record and
compares.

What it compares, and what it deliberately does not:

* OUR names come from `skills/*/SKILL.md` in this repo (frontmatter `name:`, falling
  back to the directory name). Maintainer skills under `.claude/skills/` are not
  installed with the plugin, so they cannot collide and are not scanned.
* THEIR names come from the plugins Claude Code records as installed
  (`installed_plugins.json`, each entry's `installPath`), minus any plugin the user
  settings explicitly disable. The cache directory is NOT the source of truth: it
  keeps every version ever downloaded and plugins that were fetched once and never
  installed, so scanning it reports plugins that are not loaded and can miss ones
  that are.
* If no install record is found (an unusual layout, or `--plugins-dir` pointed
  somewhere else) it falls back to the newest cached version of every plugin and says
  so on stderr: "cache only". That answer is a guess, not a statement about what is
  loaded. "Newest" is numeric for dotted versions ("1.10.0" beats "1.9.0"); a plugin
  versioned by hash has no order to compare, so its most recently modified directory
  is used.
* Our own installed copy is skipped, by the name in `.claude-plugin/plugin.json`.
* Also reports two of OUR skills declaring the same name.
* NOT scanned: `commands/` of other plugins, skill paths a plugin redirects with a
  custom manifest, and skills under ~/.claude/skills or any project's
  .claude/skills. A clash with one of those would not be reported.

Names read from the cache are printed with control characters replaced, because the
directory names are chosen by other plugins' authors and this goes to a terminal.

Exit status: 0 clean (or nothing installed to compare against), 1 collision found,
2 the repo has no skills/ directory to read.

Usage:
    check_skill_collisions.py [--repo PATH] [--plugins-dir PATH]
                              [--installed-file PATH] [--settings PATH]
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
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def printable(text: str) -> str:
    return CONTROL.sub("?", text)


def parse_name_value(raw: str) -> str | None:
    """The value of a frontmatter `name:` line: quotes removed, trailing comment cut."""
    raw = raw.strip()
    if not raw:
        return None
    if raw[0] in "\"'":
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else None
    return raw.split(" #", 1)[0].strip() or None


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
    match = re.search(r"^name:[ \t]*(.*)$", front, re.MULTILINE)
    return parse_name_value(match.group(1)) if match else None


def skills_in(skills_dir: pathlib.Path) -> dict[str, list[str]]:
    """{declared name: [directory names]} for every skill under `skills_dir`."""
    found: dict[str, list[str]] = {}
    try:
        children = sorted(p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return found
    for d in children:
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            continue
        found.setdefault(frontmatter_name(skill_md) or d.name, []).append(d.name)
    return found


def newest_version(plugin_dir: pathlib.Path) -> pathlib.Path | None:
    try:
        versions = [p for p in plugin_dir.iterdir() if p.is_dir()]
    except OSError:
        return None
    if not versions:
        return None
    dotted = [p for p in versions if SEMVER.match(p.name)]
    if dotted:
        return max(dotted, key=lambda p: tuple(int(x) for x in p.name.split(".")))

    def mtime(p: pathlib.Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return max(versions, key=mtime)


def own_plugin_name(repo: pathlib.Path) -> str:
    try:
        manifest = json.loads((repo / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return manifest.get("name") or OWN_PLUGIN_FALLBACK
    except (OSError, ValueError):
        return OWN_PLUGIN_FALLBACK


def default_claude_paths(plugins_dir: pathlib.Path):
    """(installed_plugins.json, settings.json) next to a standard ~/.claude layout, else (None, None).

    Only derived when `plugins_dir` is `<something>/plugins/<dir>`, so pointing the
    script at an arbitrary directory never picks up an unrelated file by accident."""
    if plugins_dir.parent.name == "plugins":
        return plugins_dir.parent / "installed_plugins.json", plugins_dir.parent.parent / "settings.json"
    return None, None


def read_json(path: pathlib.Path | None):
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def installed_skills(record: dict, disabled: set[str], skip_plugin: str):
    """Yield (name, label) for every skill of every installed, not-disabled plugin."""
    for key, entries in sorted((record.get("plugins") or {}).items()):
        plugin, _, marketplace = key.partition("@")
        if plugin == skip_plugin or key in disabled:
            continue
        for entry in entries if isinstance(entries, list) else []:
            install_path = entry.get("installPath")
            if not install_path:
                continue
            label = printable(f"{key} {entry.get('version', '?')}")
            for name in skills_in(pathlib.Path(install_path) / "skills"):
                yield name, label


def cached_skills(plugins_dir: pathlib.Path, skip_plugin: str):
    """Fallback: yield (name, label) for the newest cached version of every plugin."""
    try:
        marketplaces = sorted(p for p in plugins_dir.iterdir() if p.is_dir())
    except OSError:
        return
    for marketplace in marketplaces:
        try:
            plugins = sorted(p for p in marketplace.iterdir() if p.is_dir())
        except OSError:
            continue
        for plugin in plugins:
            if plugin.name == skip_plugin:
                continue
            version = newest_version(plugin)
            if version is None:
                continue
            label = printable(f"{marketplace.name}/{plugin.name} {version.name}")
            for name in skills_in(version / "skills"):
                yield name, label


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent)
    ap.add_argument(
        "--plugins-dir",
        type=pathlib.Path,
        default=pathlib.Path(os.environ.get("PLAUD_COLLISION_PLUGINS_DIR", DEFAULT_PLUGINS_DIR)),
    )
    ap.add_argument("--installed-file", type=pathlib.Path, default=None,
                    help="installed_plugins.json (default: next to a standard ~/.claude/plugins/cache)")
    ap.add_argument("--settings", type=pathlib.Path, default=None,
                    help="settings.json whose enabledPlugins can switch a plugin off")
    args = ap.parse_args(argv)

    ours = skills_in(args.repo / "skills")
    if not (args.repo / "skills").is_dir():
        print(f"error: {args.repo}/skills is not a directory", file=sys.stderr)
        return 2

    collisions = []
    for name, dirs in sorted(ours.items()):
        if len(dirs) > 1:
            collisions.append(f"`{name}` is declared by two of our skills: {', '.join(dirs)}")

    default_installed, default_settings = default_claude_paths(args.plugins_dir)
    record = read_json(args.installed_file or default_installed)
    settings = read_json(args.settings or default_settings) or {}
    disabled = {k for k, v in (settings.get("enabledPlugins") or {}).items() if v is False}
    own = own_plugin_name(args.repo)

    if isinstance(record, dict):
        found = installed_skills(record, disabled, own)
    elif args.plugins_dir.is_dir():
        print("cache only: no install record found, so this reports the newest cached version "
              "of every plugin, which may not be what is loaded", file=sys.stderr)
        found = cached_skills(args.plugins_dir, own)
    else:
        print(f"nothing to compare: no install record and {args.plugins_dir} does not exist", file=sys.stderr)
        found = iter(())

    for name, where in found:
        if name in ours:
            collisions.append(f"`{name}` is also shipped by {where}")

    if collisions:
        print(f"{len(collisions)} name collision(s):")
        for line in collisions:
            print(f"  - {line}")
        return 1
    print(f"no collisions among {len(ours)} skill name(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
