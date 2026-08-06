#!/bin/bash
# Staleness check for plaud-mcp-connector and the upstream packages it sits on.
#
# WHY THIS IS NOT check-mcp.sh
#
# The sibling plugins (che-ical-mcp, che-apple-mail-mcp) ship a hook that curls
# GitHub's /releases/latest, downloads a binary, and registers it. Copying that
# here would produce a hook that finds nothing: what this plugin runs is an npm
# package, and npm packages have no GitHub release to read. Three different
# things need three different lookups:
#
#   @plaud-ai/mcp   npm ls -g / npx cache   vs  npm view @plaud-ai/mcp version
#   @plaud-ai/cli   plaud version           vs  npm view @plaud-ai/cli version
#   this plugin     .claude-plugin/plugin.json  vs  GitHub latest release
#
# And `@latest` in .mcp.json is not the same as "always current" — npx caches, so
# a stale copy can keep running for days. That is exactly what this check exists
# to surface.
#
# WHY IT ONLY TELLS YOU
#
# che-ical-mcp's hook auto-installs because it installs *its own* binary. These
# are someone else's npm packages. Installing third-party software on a user's
# machine without asking is not ours to decide, so this prints the command and
# stops. Same shape as the CheZoteroMCP notice you already see at startup.
#
# Failure is always silent: a version check must never be the reason a session
# cannot start.

set -uo pipefail   # deliberately NOT -e; every probe below may legitimately fail

STATE_DIR="${PLAUD_CONNECTOR_STATE:-$HOME/.plaud-connector}"
STAMP="$STATE_DIR/.update-check"
INTERVAL_SECONDS=86400        # once a day; two npm lookups cost ~1.3s
REPO="PsychQuant/plaud-mcp-connector"

# Opt out entirely.
[ "${PLAUD_CONNECTOR_NO_UPDATE_CHECK:-}" = "1" ] && exit 0

# Rate-limit: skip if we looked recently.
if [ -f "$STAMP" ]; then
    last=$(cat "$STAMP" 2>/dev/null || echo 0)
    now=$(date +%s)
    case "$last" in
        ''|*[!0-9]*) last=0 ;;    # corrupt stamp → treat as never checked
    esac
    [ $(( now - last )) -lt "$INTERVAL_SECONDS" ] && exit 0
fi

command -v npm >/dev/null 2>&1 || exit 0   # no npm, nothing to compare against

mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
date +%s > "$STAMP" 2>/dev/null || true    # stamp first: a slow probe must not
                                           # re-trigger on the next session

notices=()

# --- official MCP ----------------------------------------------------------
mcp_latest=$(npm view @plaud-ai/mcp version 2>/dev/null | tr -d '[:space:]')
if [ -n "$mcp_latest" ]; then
    mcp_local=$(npm ls -g @plaud-ai/mcp --depth=0 --json 2>/dev/null \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("dependencies") or {}).get("@plaud-ai/mcp",{}).get("version",""))' 2>/dev/null)
    if [ -n "$mcp_local" ] && [ "$mcp_local" != "$mcp_latest" ]; then
        notices+=("@plaud-ai/mcp  $mcp_local → $mcp_latest    npm install -g @plaud-ai/mcp@latest")
    fi
    # Not globally installed is the normal case — .mcp.json runs it through npx.
    # Say nothing: npx resolves @latest itself, and nagging about a package the
    # user never chose to install is noise.
fi

# --- official CLI (only if the user actually has it) -----------------------
if command -v plaud >/dev/null 2>&1; then
    cli_latest=$(npm view @plaud-ai/cli version 2>/dev/null | tr -d '[:space:]')
    cli_local=$(plaud version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ -n "$cli_latest" ] && [ -n "$cli_local" ] && [ "$cli_local" != "$cli_latest" ]; then
        notices+=("@plaud-ai/cli  $cli_local → $cli_latest    npm install -g @plaud-ai/cli@latest")
    fi
fi

# --- this plugin -----------------------------------------------------------
if command -v gh >/dev/null 2>&1; then
    plugin_local=$(python3 -c "
import json,sys
try:
    print(json.load(open('${CLAUDE_PLUGIN_ROOT:-.}/.claude-plugin/plugin.json'))['version'])
except Exception:
    pass" 2>/dev/null)
    plugin_latest=$(gh release view --repo "$REPO" --json tagName --jq '.tagName' 2>/dev/null | sed 's/^v//')
    if [ -n "$plugin_local" ] && [ -n "$plugin_latest" ] && [ "$plugin_local" != "$plugin_latest" ]; then
        notices+=("plaud-mcp-connector  $plugin_local → $plugin_latest    /plugin update plaud-mcp-connector@plaud-mcp-connector")
    fi
fi

if [ ${#notices[@]} -gt 0 ]; then
    echo "⬆️  plaud-mcp-connector: updates available"
    for n in "${notices[@]}"; do
        echo "   $n"
    done
    echo "   (silence this with PLAUD_CONNECTOR_NO_UPDATE_CHECK=1)"
fi

exit 0
