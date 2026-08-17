# Upstream report — `login` binds a fixed machine-wide port (8199)

**Status: drafted, NOT sent — and it should not be sent as-is by anyone, human or
agent, without the owner of this repo deciding to.** No issue tracker is declared
for the MCP package: as measured on 2026-08-17 against version 0.3.8, its
`package.json` has no `repository`, `bugs` or `homepage` key at all (the keys are
absent, not set to null), and the `Plaud-AI` GitHub organisation publishes no
repository for the MCP server. Local tracking:
[`#44`](https://github.com/PsychQuant/plaud-mcp-connector/issues/44).

Measured against **0.3.8** of the MCP package (the `dist/` as published to npm),
cross-checked against **0.3.7**. Line references are relative to the package root
and are only valid for those two versions — the `dist/` filenames carry content
hashes and change every build, so reproduce with a pinned
`npx -y @plaud-ai/mcp@0.3.8`, not with `@latest`.

Environment for everything below: macOS, Node 20+, several MCP client sessions
running concurrently on one machine.

---

## Summary

The OAuth callback listener binds a hardcoded machine-wide port, **8199**, with no
environment override on the stdio path and no fallback. The deployment model the
package itself recommends — `npx -y @plaud-ai/mcp@latest` from an MCP client's
config — starts **one process per client session**, and those processes are
long-lived. Any two `login` calls whose two-minute authorisation windows overlap
therefore collide, and the second fails with a message that does not tell the user
where to look.

Measured on one developer machine: **20 concurrent MCP server processes**, the
oldest four days old, none of them orphaned (`ppid != 1` for all 20; each one's
parent chain leads back to a distinct live client).

## Four places bind or assume 8199, not one

`grep -rn 8199 dist/` on 0.3.8:

| Location | What it is |
|---|---|
| `dist/index.js:36` | `var CALLBACK_PORT = 8199;` — the stdio `login` tool |
| `dist/install-RTIXREYV.js:466` | same constant, the `install` subcommand's own login flow |
| `dist/server-VJAFEGJ6.js:1171` | same constant, HTTP mode |
| `dist/chunk-EY5K2UXG.js:28` | `redirectUri: "http://localhost:8199/auth/callback"` |

**The HTTP-mode one behaves differently from the other two and matters most.**
`dist/server-VJAFEGJ6.js:1547-1562` binds it at startup and never closes it:

```js
if (!process.env.PLAUD_CALLBACK_URL) {
  const callbackApp = express();
  callbackApp.get(CALLBACK_PATH, (req, res) => { /* … */ });
  createServer(callbackApp).listen(CALLBACK_PORT, "localhost", () => { /* … */ });
}
```

So `plaud-mcp http` holds 8199 for the whole life of the process. That is not a
two-minute window — it is a permanent holder, and it makes "just wait for the
other window to close" wrong whenever the holder happens to be an HTTP-mode
server.

The CLI is a fifth binder, in a different package: `@plaud-ai/cli` (installed
globally as `plaud`) has the same `redirectUri` and its own
`var CALLBACK_PORT = 8199` at `dist/index.js:20948`. This matters for the wording
of ask 2 below.

## What we could not establish, and are not claiming

Two things we initially believed and then could not support. Recording both so
nobody re-derives them:

**1. We do not know whether the timeout path leaks the listener.** The code says
it should not: `dist/index.js:37` sets `LOGIN_TIMEOUT_MS = 12e4`, and the timeout
branch (`dist/chunk-5NWKLF3V.js:393-395` → `finalize` at `:407-428`) calls
`server.closeAllConnections?.()` and `server.close()`. That function is identical
in 0.3.7 (`dist/chunk-OYI4PXYP.js`).

But the one runtime observation we have points the other way. The report that
opened `#44` recorded, after a real login timeout:

> `lsof -nP -i :8199` still showed the same node process in LISTEN — still there
> 40+ hours later.

We have not reproduced that, and we cannot explain it from the code. Our own
current measurement is the opposite: with 20 MCP server processes alive,
`lsof -nP -iTCP:8199 -sTCP:LISTEN` returns empty. The honest state is **not
"there is no leak"** — it is: *the code path we can read closes the listener; a
single field observation says otherwise; the two are unreconciled.* A stuck
listener would change the advice below completely, so it is worth resolving.

**2. We do not know whether the OAuth client registration pins the port.**
`redirectUri` is hardcoded in the source, but that only tells us what the client
sends — not what the authorization server would accept. An OAuth client can have
several redirect URIs registered. We have not tested whether changing the port and
re-registering works, so we make no claim about it; that half is for Plaud to
confirm.

(An earlier draft argued that `redirectUri` was the *only* field in that `CONFIG`
object without a `process.env` fallback and read the asymmetry as deliberate. That
was wrong: `tokenFile: "tokens-mcp.json"` on the very next line, `:29`, is equally
hardcoded. The asymmetry does not exist, so it is not evidence of anything.)

## Reproduction

Two MCP clients — or two sessions of one client — on the same machine, each
running the server through `npx`:

1. In session A, call `login`. The callback server binds `localhost:8199` and a
   browser opens.
2. Before authorising, call `login` in session B, any time inside A's two-minute
   window.
3. Session B fails with:

   > Failed to start callback server: port 8199 is in use — another `plaud login`
   > may still be running. Wait a few seconds and retry.

4. Retrying inside the window fails identically. B cannot authenticate until A's
   window closes.

A related observation on the same code path: if a stale authorisation page is
submitted after its flow has ended, and some other session's callback server is
listening, the `state` will not match `expectedState`. The request falls through to
`respondNeutral()` (`dist/chunk-5NWKLF3V.js:359-360` → `:403`), which serves:

> Continue authorization in the original window.

That sends the user back to a window whose flow is already gone.

## What we are asking for

1. **Make the callback port movable on the stdio path.** HTTP mode already reads
   `PLAUD_HTTP_PORT` and `PLAUD_CALLBACK_URL` (`dist/server-VJAFEGJ6.js:1169`,
   `:1173`); the stdio path has no equivalent — `dist/index.js` reads no
   environment variable at all. An override there, and/or a fallback search when
   8199 is taken, would remove the collision. If the registered redirect URI
   constrains which ports are usable, that part is yours to change; a
   loopback-with-any-port registration, as
   [RFC 8252 §7.3](https://datatracker.ietf.org/doc/html/rfc8252#section-7.3)
   describes, exists for exactly this situation. Note that §7.3 is written for
   loopback **IP literals** (`127.0.0.1`, `[::1]`), whereas the current value uses
   the hostname `localhost`, so adopting it would mean changing the host as well
   as freeing the port.

2. **Say which process is holding the port.** The current message names
   "another `plaud login`", which is one real possibility — the CLI binds the same
   port — but not the usual one on a machine running MCP clients, where the holder
   is normally another client session's server process, or an HTTP-mode server
   holding it permanently. The message also suggests waiting "a few seconds"; the
   window is up to two minutes, and if the holder is an HTTP-mode server, waiting
   never helps. Reporting the holder's PID, or at least naming the three
   possibilities, would let the user act.

3. **Tell the user when a flow has expired.** On `state` mismatch, something that
   says the authorisation flow is no longer valid and a fresh `login` is needed
   would beat the current redirect-to-nowhere. One caveat worth weighing on your
   side: a `state` mismatch is also what a CSRF attempt looks like, so the
   endpoint should not confirm to an arbitrary caller which states are live. A
   generic "this authorisation link is no longer valid" avoids both problems.

Asks 2 and 3 are self-contained and do not depend on ask 1.

## What this costs users today

The workaround that circulates is to find the process holding the port and kill
it. `lsof -nP -iTCP:8199 -sTCP:LISTEN` does identify the holder unambiguously, so
this is doable — but killing an MCP server drops that client session's connection,
and it does not reconnect on its own. Waiting out the other session's window is
better *when the holder is a login flow*; it does not help at all when the holder
is an HTTP-mode server, because that one never lets go. Nothing in the current
message lets the user tell those cases apart.
