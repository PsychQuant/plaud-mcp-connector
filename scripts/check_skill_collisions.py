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
  (`installed_plugins.json`, each entry's `installPath`), minus any plugin whose
  `enabledPlugins` entry in the user's settings.json is false. The cache directory is
  NOT the source of truth: it keeps every version ever downloaded and plugins that were
  fetched once and never installed.
* If the default install record does not exist (an unusual layout, or `--plugins-dir`
  pointed somewhere else) it falls back to the newest cached version of every plugin
  and says so on stderr: "cache only". That answer is a guess, not a statement about
  what is loaded. "Newest" is numeric for dotted versions ("1.10.0" beats "1.9.0"); a
  plugin versioned by hash has no order to compare, so its most recently modified
  directory is used.
* An install record or settings file that EXISTS but cannot be understood is an error
  (exit 2), never a quiet fallback and never a clean result. A record named explicitly
  with --installed-file that cannot be read is the same.
* Our own installed copy is skipped, by the name in `.claude-plugin/plugin.json`.
* Also reports two of OUR skills declaring the same name.
* The clean line says how many installed plugins were actually compared, so a scan
  that compared nothing is visible as such.

Known limits (each makes the result over-report or under-cover, never silently clean):
* Scope is ignored: a plugin installed for another project is compared as if it were
  loaded here. This over-reports.
* Only the user's settings.json is read for enabled state; a project's
  .claude/settings.json or settings.local.json is not merged, and a plugin with no
  `enabledPlugins` entry counts as enabled.
* NOT scanned: `commands/` of other plugins, skill paths a plugin redirects with a
  custom manifest, and skills under ~/.claude/skills or any project's .claude/skills.
  A clash with one of those would not be reported (#80).
* Our own plugin is skipped by NAME only, whatever marketplace it came from, so a
  same-named plugin from another marketplace is not compared.
* A run that compared 0 plugins (every recorded install path gone, or all switched off)
  still exits 0. The count is in the clean line; the exit status does not carry it.

Names read from the install record or cache are printed with control, format and
line-separator characters replaced, because they are chosen by other plugins' authors
and this goes to a terminal.

Exit status: 0 clean (or nothing installed to compare against), 1 collision found,
2 the check could not run or could not read its input. A crash never exits 1, because
1 means "collision found" to anything gating on it.

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
import unicodedata

DEFAULT_PLUGINS_DIR = pathlib.Path.home() / ".claude" / "plugins" / "cache"
OWN_PLUGIN_FALLBACK = "plaud-mcp-connector"
SEMVER = re.compile(r"^\d+(\.\d+)*$")
COMMENT = re.compile(r"\s#")


class InputError(Exception):
    """An input file exists (or was named) but cannot be used. Exit status 2."""


def printable(text: str) -> str:
    """Replace control (Cc), format (Cf, includes bidi marks) and line/paragraph
    separator (Zl, Zp) characters, none of which belong in one line of terminal output."""
    return "".join(
        "?" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text
    )


def parse_name_value(raw: str) -> str | None:
    """The value of a frontmatter `name:` line: quotes removed, trailing comment cut.

    A value that is nothing but a comment is no value at all."""
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw[0] in "\"'":
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else None
    return COMMENT.split(raw, 1)[0].strip() or None


def frontmatter_name(skill_md: pathlib.Path) -> str | None:
    """`name:` from the YAML frontmatter only — it also appears in prose further down."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # One unreadable file in somebody else's plugin must not end the whole check;
        # without a parsable name the skill is known by its directory name.
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


ABSENT = object()   # a default file that is not there; distinct from a file whose JSON is `null`


def load_json(path: pathlib.Path, what: str, *, explicit: bool):
    """Parsed JSON, or ABSENT when a DEFAULT file simply is not there.

    A file that was named explicitly, or that exists but is unreadable or not JSON, is an
    InputError: guessing past it would turn "could not read" into "nothing to report"."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if explicit:
            raise InputError(f"{what} {path} does not exist")
        return ABSENT
    except OSError as exc:
        raise InputError(f"{what} {path} could not be read: {exc.strerror or exc}")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InputError(f"{what} {path} is not valid JSON: {exc}")


def validate_install_record(record, source: pathlib.Path) -> dict:
    """The `plugins` map of a recognized install record, or InputError.

    Recognized = {"plugins": {"name@marketplace": [ {"installPath": "<str>", ...}, ... ]}}.
    Anything else, including a future schema, must not be mistaken for "nothing installed"."""
    plugins = record.get("plugins") if isinstance(record, dict) else None
    if not isinstance(plugins, dict):
        raise InputError(f"install record {source} has no `plugins` map; its layout is not one this check understands")
    for key, entries in plugins.items():
        if not isinstance(entries, list) or not all(
            isinstance(e, dict) and isinstance(e.get("installPath"), str) and e["installPath"].strip()
            for e in entries
        ):
            raise InputError(
                f"install record {source}: entry `{printable(str(key))}` is not a list of objects with a non-empty string installPath"
            )
    return plugins


def disabled_plugins(settings) -> set[str]:
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    if not isinstance(enabled, dict):
        return set()
    return {k for k, v in enabled.items() if v is False}


def installed_skills(plugins: dict, disabled: set[str], skip_plugin: str, stats: dict):
    """Yield (name, label) for every skill of every installed, not-disabled plugin.

    `stats["compared"]` counts plugins whose skills directory was read; plugins whose
    installPath is gone are listed in `stats["missing"]` instead of vanishing."""
    for key, entries in sorted(plugins.items()):
        plugin = key.partition("@")[0]
        if plugin == skip_plugin or key in disabled:
            continue
        read_any = False
        for entry in entries:
            install_path = pathlib.Path(entry["installPath"])
            if not install_path.is_dir():
                continue
            read_any = True
            label = printable(f"{key} {entry.get('version', '?')}")
            for name in skills_in(install_path / "skills"):
                yield name, label
        if read_any:
            stats["compared"] += 1
        else:
            stats["missing"].append(printable(key))


def cached_skills(plugins_dir: pathlib.Path, disabled: set[str], skip_plugin: str, stats: dict):
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
            if plugin.name == skip_plugin or f"{plugin.name}@{marketplace.name}" in disabled:
                continue
            version = newest_version(plugin)
            if version is None:
                continue
            stats["compared"] += 1
            label = printable(f"{marketplace.name}/{plugin.name} {version.name}")
            for name in skills_in(version / "skills"):
                yield name, label


def run(args) -> int:
    ours = skills_in(args.repo / "skills")
    if not (args.repo / "skills").is_dir():
        print(f"error: {args.repo}/skills is not a directory", file=sys.stderr)
        return 2

    collisions = []
    for name, dirs in sorted(ours.items()):
        if len(dirs) > 1:
            collisions.append(f"`{name}` is declared by two of our skills: {', '.join(dirs)}")

    default_installed, default_settings = default_claude_paths(args.plugins_dir)
    installed_path = args.installed_file or default_installed
    settings_path = args.settings or default_settings

    record = (
        load_json(installed_path, "install record", explicit=args.installed_file is not None)
        if installed_path else ABSENT
    )
    settings = (
        load_json(settings_path, "settings file", explicit=args.settings is not None)
        if settings_path else ABSENT
    )
    if settings is ABSENT or settings is None:
        settings = {}
    disabled = disabled_plugins(settings)
    own = own_plugin_name(args.repo)
    stats = {"compared": 0, "missing": []}

    if record is not ABSENT:
        found = installed_skills(validate_install_record(record, installed_path), disabled, own, stats)
        basis = "installed"
    elif args.plugins_dir.is_dir():
        print("cache only: no install record found, so this reports the newest cached version "
              "of every plugin, which may not be what is loaded", file=sys.stderr)
        found = cached_skills(args.plugins_dir, disabled, own, stats)
        basis = "cached"
    else:
        print(f"nothing to compare: no install record and {args.plugins_dir} does not exist", file=sys.stderr)
        found = iter(())
        basis = "installed"

    seen = set()
    for name, where in found:
        if name in ours and (name, where) not in seen:
            seen.add((name, where))
            collisions.append(f"`{name}` is also shipped by {where}")

    for key in stats["missing"]:
        print(f"note: {key} is recorded as installed but its installPath is missing; not compared",
              file=sys.stderr)

    if collisions:
        print(f"{len(collisions)} name collision(s):")
        for line in collisions:
            print(f"  - {line}")
        return 1
    print(f"no collisions among {len(ours)} skill name(s); compared against "
          f"{stats['compared']} {basis} plugin(s)")
    return 0


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
    try:
        return run(args)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a gate must not die with the collision exit code
        print(f"error: unexpected failure ({type(exc).__name__}): {printable(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
