#!/bin/bash
# Tell the user when this plugin cannot update itself.
#
# Claude Code ships third-party marketplaces with auto-update DISABLED (its own
# docs say so: "Third-party and local development marketplaces have auto-update
# disabled by default"). An install left alone therefore stays on the version it
# was installed at — including versions with the search bugs fixed in #8 and #15,
# which return "no match" for words that were spoken.
#
# The cruel part is that the stale copy is the thing that would have to tell you.
# So it tells you: this notice is the plugin reporting its own inability to
# update.
#
# WHY IT ONLY TELLS YOU
#
# The state lives in Claude Code's `known_marketplaces.json` as a per-marketplace
# `autoUpdate` boolean, so flipping it is mechanically trivial. It is still not
# ours to flip: that file is another program's runtime state, that program
# rewrites it (there is a .bak beside it), and the documented way to change it is
# its own UI. Writing to it would be an unsupported poke at someone else's
# internals that works until the moment it silently doesn't.
#
# Environment (test seams):
#   PLAUD_KNOWN_MARKETPLACES        path to known_marketplaces.json
#   PLAUD_CONNECTOR_NO_UPDATE_CHECK set to 1 to silence
set -u

[ "${PLAUD_CONNECTOR_NO_UPDATE_CHECK:-}" = "1" ] && exit 0

KNOWN="${PLAUD_KNOWN_MARKETPLACES:-$HOME/.claude/plugins/known_marketplaces.json}"
[ -f "$KNOWN" ] || exit 0

MARKETPLACE="plaud-mcp-connector"

# Three outcomes: "off" (entry exists, auto-update not on), "on", or "" for
# anything we cannot read — not installed, malformed file, no python. Silence on
# "" is deliberate: a notice fired on a guess is a notice people learn to skip.
STATE=$(python3 - "$KNOWN" "$MARKETPLACE" <<'PY' 2>/dev/null
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    entry = data.get(sys.argv[2]) if isinstance(data, dict) else None
    if isinstance(entry, dict):
        # A missing key is the real failing shape — an entry written by an
        # install that was never toggled has no `autoUpdate` field at all,
        # rather than `false`.
        print("on" if entry.get("autoUpdate") is True else "off")
except Exception:
    pass
PY
)

[ "$STATE" = "off" ] || exit 0

cat <<'EOF'
⬆️  plaud-mcp-connector: auto-update is OFF for this marketplace
   Claude Code disables auto-update for third-party marketplaces by default, so
   this install stays on the version you installed it at — including versions
   whose search silently reported "no match" for words that were said (#8, #15).

   Turn it on:  /plugin  →  Marketplaces  →  plaud-mcp-connector  →  Enable auto-update
   (silence this with PLAUD_CONNECTOR_NO_UPDATE_CHECK=1)
EOF
exit 0
