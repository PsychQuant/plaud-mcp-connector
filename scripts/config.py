#!/usr/bin/env python3
"""User preferences for this plugin.

What belongs here is a **preference** — a choice where more than one answer is
correct and which one is right depends on what you are doing. Subtitles from the
cleaned-up transcript or the verbatim one is the founding example: clean reads
better on screen, verbatim keeps the disfluency that qualitative work measures.

What does NOT belong here is a decision that has a right answer. Making one of
those configurable does not add flexibility; it ships a way to be wrong and
calls it a feature. The test to apply before adding a key: can you write down
who picks the other value, in what situation, and why they are right? If yes it
is a preference. If no, it is something nobody has decided yet.

Config lives at ``~/.plaud-connector/config.json`` — beside the cache, not
inside it. A cache is disposable and gets rebuilt; preferences are not, and
should survive someone clearing the cache to fix an indexing problem.
``PLAUD_CONFIG`` overrides the whole path, which is also how the tests avoid
reading (or overwriting) the developer's real preferences.

Every failure here degrades to the default and keeps going. A preference that
cannot be read is a smaller problem than the subtitle file you did not get. But
nothing degrades *silently*: a typo'd key that quietly does nothing is worse
than either alternative, because you go on believing the setting is in effect.
"""

import argparse
import copy
import json
import os
import pathlib
import sys

DEFAULTS = {
    "subtitle_source": "polished",
    "srt_line_limits": {"latin": 42, "cjk": 20},
}

SUBTITLE_SOURCES = ("polished", "verbatim")


def _warn(message: str) -> None:
    print(f"config: {message}", file=sys.stderr)


def config_path() -> pathlib.Path:
    """Where preferences live. ``PLAUD_CONFIG`` overrides it entirely."""
    override = os.environ.get("PLAUD_CONFIG")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".plaud-connector" / "config.json"


def _read_raw(path: pathlib.Path) -> dict:
    """The file's contents as a dict, or ``{}`` when it cannot be used.

    Distinguishes the two "no settings" cases on stderr, because they call for
    different responses: an absent file is the normal state and says nothing,
    while a file that exists and cannot be parsed means someone wrote settings
    that are not taking effect.
    """
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"could not read {path} ({exc}) — using defaults")
        return {}
    if not isinstance(loaded, dict):
        _warn(f"{path} is not a JSON object — using defaults")
        return {}
    return loaded


def _resolve_subtitle_source(value) -> str:
    if value in SUBTITLE_SOURCES:
        return value
    _warn(
        f"subtitle_source={value!r} is not one of {', '.join(SUBTITLE_SOURCES)} "
        f"— using {DEFAULTS['subtitle_source']!r}"
    )
    return DEFAULTS["subtitle_source"]


def _resolve_line_limits(value) -> dict:
    """Merge onto the defaults so setting one script keeps the other.

    Replacing wholesale would mean writing ``{"cjk": 14}`` silently deletes the
    Latin limit — the setting you did not mention disappears, and the wrapping
    you did not ask to change, changes.
    """
    limits = copy.deepcopy(DEFAULTS["srt_line_limits"])
    if not isinstance(value, dict):
        _warn(f"srt_line_limits={value!r} is not an object — using defaults")
        return limits
    for script, limit in value.items():
        if script not in limits:
            _warn(
                f"srt_line_limits has no {script!r} — known scripts: "
                f"{', '.join(sorted(limits))}"
            )
            continue
        # bool is an int subclass, and a True limit is a typo, not a width.
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            _warn(f"srt_line_limits[{script!r}]={limit!r} is not a positive integer "
                  f"— using {limits[script]}")
            continue
        limits[script] = limit
    return limits


def load_config(path: pathlib.Path | None = None) -> dict:
    """Preferences with defaults filled in. Never raises, never returns partial."""
    path = path or config_path()
    raw = _read_raw(path)

    unknown = [k for k in raw if k not in DEFAULTS]
    if unknown:
        _warn(
            f"ignoring unknown key(s) in {path}: {', '.join(sorted(unknown))} — "
            f"valid keys are {', '.join(sorted(DEFAULTS))}"
        )

    return {
        "subtitle_source": _resolve_subtitle_source(
            raw.get("subtitle_source", DEFAULTS["subtitle_source"])
        ),
        "srt_line_limits": _resolve_line_limits(
            raw.get("srt_line_limits", DEFAULTS["srt_line_limits"])
        ),
    }


def save_preference(key: str, value, path: pathlib.Path | None = None) -> bool:
    """Record one preference. Returns whether it stuck.

    Callers should report a ``False`` rather than abort on it. Not being able to
    remember a choice is an inconvenience; refusing to do the work the user
    actually asked for, because of it, is the real failure.

    An existing file that cannot be parsed is replaced rather than treated as a
    reason to refuse — otherwise one bad character would leave you permanently
    unable to save anything.
    """
    path = path or config_path()
    existing = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError):
            _warn(f"{path} was unreadable — replacing it")

    updated = {**existing, key: value}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    except OSError as exc:
        _warn(f"could not save {key} to {path} ({exc}) — the preference will not persist")
        return False
    return True


def is_recorded(key: str, path: pathlib.Path | None = None) -> bool:
    """Whether this key was ever explicitly chosen, as opposed to defaulted.

    This distinction is the whole ask-once rule. ``load_config`` always returns
    a value, so it cannot tell a user who chose "polished" from a user who has
    never been asked — and asking the first one again, every time, is precisely
    the nagging the design is meant to avoid.
    """
    return key in _read_raw(path or config_path())


# ── Command line ──────────────────────────────────────────────────────────
# The question itself belongs to the skill, not here: AskUserQuestion is an
# agent tool and no Python process can call it. What Python owes the skill is
# the two things around the question — "has this been chosen before?" and
# "remember this answer" — as commands rather than as instructions to
# hand-write JSON, which works right up until it writes a stray comma.

def _cmd_get(args: argparse.Namespace) -> int:
    cfg = load_config()
    value = cfg.get(args.key)
    if value is None:
        print(f"unknown key: {args.key} — valid keys are {', '.join(sorted(DEFAULTS))}",
              file=sys.stderr)
        return 2
    print(json.dumps(value) if isinstance(value, dict) else value)
    # Exit 3 means "this is the default, nobody chose it" — a caller deciding
    # whether to ask needs that, and an exit code survives being piped.
    return 0 if is_recorded(args.key) else 3


def _cmd_set(args: argparse.Namespace) -> int:
    if args.key not in DEFAULTS:
        print(f"unknown key: {args.key} — valid keys are {', '.join(sorted(DEFAULTS))}",
              file=sys.stderr)
        return 2
    if args.key == "subtitle_source" and args.value not in SUBTITLE_SOURCES:
        print(f"invalid value {args.value!r} for subtitle_source — "
              f"choose one of {', '.join(SUBTITLE_SOURCES)}", file=sys.stderr)
        return 2
    # Validated before writing, not after: warning about a typo you have already
    # saved arrives too late to be worth anything.
    return 0 if save_preference(args.key, args.value) else 1


def _cmd_show(args: argparse.Namespace) -> int:
    cfg = load_config()
    path = config_path()
    print(f"config file: {path}"
          f"{'' if path.is_file() else '  (does not exist yet — showing defaults)'}")
    for key in sorted(cfg):
        marker = "" if is_recorded(key) else "   (default)"
        value = json.dumps(cfg[key], ensure_ascii=False) if isinstance(cfg[key], dict) else cfg[key]
        print(f"  {key}: {value}{marker}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Read and write plaud-connector preferences.")
    sub = ap.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="print a setting; exit 3 if it is only the default")
    p_get.add_argument("key")
    p_get.set_defaults(func=_cmd_get)

    p_set = sub.add_parser("set", help="record a preference")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_set.set_defaults(func=_cmd_set)

    p_show = sub.add_parser("show", help="print every setting and where they come from")
    p_show.set_defaults(func=_cmd_show)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
