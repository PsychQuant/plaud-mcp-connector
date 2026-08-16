# Upstream report — `login` binds a fixed machine-wide port (8199)

**Status: drafted, not yet filed.** `@plaud-ai/mcp` declares no issue tracker —
`repository`, `bugs` and `homepage` are all `null` in its `package.json`, and the
`Plaud-AI` GitHub organisation publishes no repository for the MCP server. This
file is the report, ready to paste into whatever channel turns out to be the right
one. Local tracking: [`#44`](https://github.com/PsychQuant/plaud-mcp-connector/issues/44).

Measured against **`@plaud-ai/mcp@0.3.8`** (`dist/` as published to npm),
cross-checked against `0.3.7`. Every line reference below is relative to the
package root.

---

## Summary

The stdio `login` tool binds its OAuth callback listener to a hardcoded
machine-wide port, **8199**, with no environment override and no fallback. The
deployment model the package itself recommends — `npx -y @plaud-ai/mcp@latest`
from an MCP client's config — starts **one process per client session**. On a
machine running several sessions, those processes coexist for days. Any two
`login` calls whose two-minute authorisation windows overlap therefore collide,
and the second one fails with an error message that points the user in the wrong
direction.

Measured on one developer machine: **20 concurrent `plaud-mcp` processes**, the
oldest four days old, every one of them a live child of a running client (no
orphans — `ppid != 1` for all 20).

## What is *not* the problem

We began from the hypothesis that the timeout path leaks the listener. **It does
not.** Recording this so the report does not send anyone down that path:

| Location | Code |
|---|---|
| `dist/index.js:37` | `var LOGIN_TIMEOUT_MS = 12e4;` |
| `dist/index.js:60-71` | `login` calls `runOAuthCallback({ port: CALLBACK_PORT, timeoutMs: LOGIN_TIMEOUT_MS, … })` |
| `dist/chunk-5NWKLF3V.js:393-395` | `setTimeout(() => finalize({ status: "timeout" }, true), timeoutMs)` — note the second argument, `immediate` |
| `dist/chunk-5NWKLF3V.js:407-427` | `finalize()` with `immediate` truthy calls `close()` straight away → `server.closeAllConnections?.()` (`:417`) and `server.close()` (`:420`) |

The function is byte-identical in `0.3.7` (`dist/chunk-OYI4PXYP.js`), so this is
not a regression either. And since Node's `server.close()` releases the listening
handle immediately — outstanding connections only delay the `'close'` event, not
the unbind — a listener still in `LISTEN` long after a timeout is inconsistent
with `close()` having run at all.

## The actual defect

| Location | Code |
|---|---|
| `dist/index.js:36` | `var CALLBACK_PORT = 8199;` |
| `dist/index.js` (whole file) | **no `process.env` read anywhere** — the stdio path offers no escape hatch for this port |
| `dist/chunk-EY5K2UXG.js:28` | `redirectUri: "http://localhost:8199/auth/callback"` |

In `chunk-EY5K2UXG.js`, the surrounding `CONFIG` object makes `clientId`,
`clientSecret`, `apiBase`, `authorizationUrl`, `tokenUrl` and `refreshUrl` all
overridable through `process.env`. `redirectUri` is the single exception. We read
that as deliberate rather than accidental: an OAuth provider compares
`redirect_uri` verbatim against the value registered for the client, so the port
is pinned at the registration, not in the code. **That constraint is why we cannot
send a patch** — see "What we are asking for" below.

There is a working example of the other shape inside the same package: the HTTP
server mode *does* let the operator move its ports
(`dist/server-VJAFEGJ6.js:1169` `PLAUD_HTTP_PORT`, `:1173` `PLAUD_CALLBACK_URL`).
The stdio path did not follow.

## Reproduction

Two MCP clients (or two sessions of one client) on the same machine, each running
the server through `npx -y @plaud-ai/mcp@latest`:

1. In session A, call `login`. The callback server binds `localhost:8199` and a
   browser opens.
2. Before authorising, call `login` in session B — any time inside A's two-minute
   window.
3. Session B fails:
   > `Failed to start callback server: port 8199 is in use — another `plaud login` may still be running. Wait a few seconds and retry.`
4. Retrying inside the window fails identically. B cannot authenticate until A's
   window closes.

A related observation, same code path: if a stale authorisation page is submitted
after its flow has ended, and some *other* session's callback server happens to be
listening, the `state` will not match `expectedState`. The request then falls
through to `respondNeutral()` (`dist/chunk-5NWKLF3V.js:359-360` → `:403`), which
serves `NEUTRAL_HTML` (`:318`):

> `Continue authorization in the original window.`

That is worse than silence — it directs the user back to a window whose flow is
already dead.

## What we are asking for

1. **Make the callback port movable.** An env override (mirroring `PLAUD_HTTP_PORT`)
   and/or a fallback search when 8199 is taken. This needs a change **on the OAuth
   client registration** too — either a set of candidate loopback ports, or
   loopback-with-any-port per [RFC 8252 §7.3](https://datatracker.ietf.org/doc/html/rfc8252#section-7.3),
   which exists for exactly this situation. Only Plaud can make that half of the
   change, which is why this is a report rather than a pull request.
2. **Rewrite the `EADDRINUSE` message** (`dist/chunk-5NWKLF3V.js:390`). Two things
   in it mislead. "another `plaud login` may still be running" reads as a second
   CLI invocation, when the holder is usually *another MCP client session's server
   process* — somewhere the user will not think to look. And "Wait a few seconds
   and retry" is the wrong order of magnitude for a two-minute window.
3. **Tell the user when a flow has expired.** On `state` mismatch, serve something
   that says the authorisation flow is no longer valid and a fresh `login` is
   needed, instead of `NEUTRAL_HTML`'s redirect-to-nowhere.

Items 2 and 3 are self-contained and do not depend on item 1.

## What this costs users today

The workaround that circulates is to find the process holding the port and kill
it. On a machine with 20 identically-named `plaud-mcp` processes — `ps` shows the
same `.bin/plaud-mcp` command line for every one of them — there is no reliable
way to tell which process belongs to which session, so this risks killing a
different session's server. Killing it also drops that session's MCP connection,
which does not reconnect on its own.

The correct workaround is simply to wait out the other session's two-minute
window, but nothing in the error message says so.

## Environment

- `@plaud-ai/mcp` 0.3.8 (also checked 0.3.7), launched via `npx -y @plaud-ai/mcp@latest`
- macOS
- Multiple concurrent MCP client sessions on one machine
