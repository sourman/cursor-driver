# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A set of standalone CLI scripts (bash / Node / Python) that drive the **running**
Cursor editor programmatically — reading and changing its live runtime state from
outside the app, through Cursor's own code, with no clicking and no restart-per-change.
The worked example flips the "use my own OpenAI key" setting, but the method is general
for anything Cursor keeps in its reactive store.

There is **no build step, no test suite, no linter, and no dependencies to install.**
The Node scripts (`bin/cursor-cdp`, `bin/cursor-netmon`) use Node 22+ globals only
(`fetch`, `WebSocket`); the Python scripts use the stdlib. `bin/*` scripts are executable
and invoked directly. `bin/cursor-openai-toggle` shells out to a `cursor-cdp` it expects
on `PATH` (default `~/.local/bin/cursor-cdp`), so for the toggle to work the `bin/`
scripts must be installed to `~/.local/bin` or equivalent.

## Running the tools

```sh
bin/cursor-cdp targets                     # list CDP page/worker targets
bin/cursor-cdp contexts                    # list execution contexts (main + isolated worlds)
bin/cursor-cdp eval '<js expr>'            # Runtime.evaluate in the workbench (default world)
bin/cursor-cdp --context isolated eval 'typeof define'   # eval in a specific world
bin/cursor-cdp eval-file <path>            # eval expression loaded from a file

bin/cursor-glass-patch                     # patch Glass bundle + clear v8 cache (idempotent)
bin/cursor-glass-patch --restart           # ...and fully relaunch Cursor to apply

bin/cursor-openai-toggle --status          # read live + disk values
bin/cursor-openai-toggle                   # live toggle (default)
bin/cursor-openai-toggle --on|--off        # live set
bin/cursor-openai-toggle --disk [--on|--off]   # edit state.vscdb directly (only sticks if Cursor is QUIT)
bin/cursor-openai-toggle --restore FILE    # restore a saved backup blob to state.vscdb

bin/cursor-netmon [seconds=60]             # capture renderer network traffic over CDP

bin/cursor-byok-trace                      # dump live BYOK routing per model (read-only)
bin/cursor-byok-trace --watch [secs=30]    # hook renderer fetch/WS, log custom-endpoint traffic
bin/cursor-byok-trace --clear              # remove previously installed --watch hooks

sudo tools/memscan.py [marker ...]         # scan Cursor process memory for strings (read-only; root)
python3 tools/grepctx.py <bundle> <regex>  # grep w/ char-window context for minified bundles
```


The CDP port defaults to `9223` and is overridable via the `CURSOR_CDP_PORT` env var
(read by `cursor-cdp` and `cursor-netmon`).

## Architecture — the big picture

These scripts exist because of how Cursor stores state. Understanding the split is
required before changing anything here:

- **Cursor is an Electron app** (a VS Code fork). The **renderer** is the UI — a React
  app called *Glass*. The **main process** is the server; it owns app state and persists
  it to SQLite at `~/.config/Cursor/User/globalStorage/state.vscdb`.
- **A setting lives in two places at once:** an object in the renderer's memory **and** a
  row in `state.vscdb`. Editing the DB while Cursor runs is futile — the renderer writes
  its in-memory copy back over your edit within seconds. To change a setting and have it
  stick, you must call **the renderer's own code.**
- **There is no local HTTP API and no socket.** Client and server talk over Electron's
  in-process IPC. The only way in from outside is the **Chrome DevTools Protocol (CDP)**:
  attach to the renderer (launched with `--remote-debugging-port=9223`, 127.0.0.1 only)
  and run JavaScript in it.

### The hook + patch loop

`reactiveStorageService.setApplicationUserPersistentStorage(key, value)` reads and writes
the setting — but that service object is trapped inside a closure, unreachable from
outside. So `bin/cursor-glass-patch` injects one line into its constructor in Cursor's
**Glass bundle** (`globalThis.__cursorRS = this`), handing out a global reference. From
then on, `bin/cursor-cdp` / `bin/cursor-openai-toggle` call `globalThis.__cursorRS`
methods over CDP, which update the in-memory store **and** persist to `state.vscdb`
through Cursor's own code.

The full end-to-end requirement for the live path (documented in each script's header):
1. CDP port open on Cursor (`~/.local/share/applications/cursor.desktop` Exec line),
2. the Glass patch applied (`__cursorRS` present),
3. v8 bytecode cache cleared (else stale compiled bytecode wins and the patch silently does nothing),
4. Cursor restarted so the patched bundle loads.

### `bin/cursor-cdp` — the generic driver

A minimal CDP client: opens a WebSocket to a page target's `webSocketDebuggerUrl`,
routes id-matched replies to promises, and buffers async events. `targets`/`contexts`
are introspection; `eval`/`eval-file` are `Runtime.evaluate` with `returnByValue`,
`awaitPromise` (default on), and `userGesture: true`. Everything else in the repo that
talks to Cursor routes through this one script (or duplicates its WebSocket pattern, as
`cursor-netmon` does for streaming).

### `bin/cursor-openai-toggle` — live vs disk

Two modes mirror the two-places-at-once split: **live** (default) drives the real handler
over CDP via `__cursorRS` so the change sticks; **`--disk`** rewrites the JSON blob inside
the `state.vscdb` `ItemTable` row for key `BLOB_KEY` directly (regex on the
`"useOpenAIKey": true|false` field), which only survives while Cursor is quit. The disk
path makes timestamped `.bak-*.json` backups and supports `--restore`.

### Recon tools (`tools/`, `bin/cursor-netmon`)

Not required to use the driver — they're how the map was drawn. `memscan.py` walks
`/proc/<pid>/maps` + `/proc/<pid>/mem` of Cursor's descendant processes to confirm whether
a string is live in RAM (and which process holds it); `cursor-netmon` captures renderer
network traffic to confirm whether a UI action fires any request at all.

## Critical gotchas (these will cost you hours)

- **Patch the Glass bundle, not the desktop one.** The target file is
  `workbench.glass.main.js`, **not** `workbench.desktop.main.js` (legacy; the Glass
  renderer never loads it). The Glass bundle loads via dynamic import, so it does **not**
  appear in CDP's resource list — that's the trap. The path is overridable via
  `CURSOR_GLASS_JS`.
- **Clear the v8 bytecode cache** (`~/.config/Cursor/CachedData/*`, `Code Cache/*`) every
  patch, or the stale compiled bytecode overrides your source edit. `cursor-glass-patch`
  does this; a manual edit will not.
- **The "installation appears corrupt" notice is expected** while patched — dismiss it; it
  returns every launch.
- **A Cursor upgrade overwrites the patched file.** Re-run `cursor-glass-patch` and restart
  (`--restart`). The `.orig` backup is preserved across runs and upgrades.
- **The patch anchor is `this.setApplicationUserPersistentStorage=` and must occur exactly
  once.** If `cursor-glass-patch` reports it 0 or >1 times, Cursor's bundle layout shifted
  — re-derive the patch rather than forcing it.
- **CDP listens on 127.0.0.1 only**, so any local process can drive the editor — both the
  point and the risk.

## Extending to other Cursor state

The pattern is not specific to the OpenAI key. To drive some other piece of state:
1. Find the setter — grep the Glass bundle for the field name or a UI label; you're
   looking for `someService.setSomething("yourKey", value)`.
2. Find where that service is constructed and inject `globalThis.__x = this` (constructor
   is the safe spot — it always runs).
3. Clear the cache, restart, call the hook through `cursor-cdp`.

This reaches into Cursor internals, which move between versions. If a script breaks, the
bundle layout probably shifted — re-derive from the field name and the steps above.

## Known finding: BYOK breaks Cursor's own models (composer-2.5 / grok)

Turning on "use my own OpenAI key" poisons Cursor's proprietary models: the
routing function `fHy` (workbench.glass.main.js ~L12006) has a **default branch**
that classifies *any* non-`claude-`/non-`gemini-` model as `openai` (custom key)
when the global `useOpenAIKey` is on — so `composer-2.5`→`grok-composer-2.5`
gets a custom key attached and Cursor's backend rejects it with *"This model
does not support custom API keys."* Full write-up + suggested fixes:
`docs/composer-byok-bug-report.md`. Inspect/confirm live with
`bin/cursor-byok-trace`. Note `fHy` never consults `availableAPIKeyModels`
(only written, never read for routing), and `getUseApiKeyForModel` collapses to
the global flag for the whole default category — so there is no real per-model
opt-out for OpenAI-family models.
