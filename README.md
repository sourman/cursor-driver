# cursor-driver

Tools and notes for driving the Cursor editor programmatically: reading and
changing its live runtime state from outside the app, through its own code, no
clicking, no restarting per change.

The worked example flips the "use my own OpenAI key" setting live. But the
method is not specific to that. It works for anything Cursor keeps in its
reactive store, which is most of its settings.

Linux is the path of least resistance here. The ideas port to macOS and
Windows, the paths and launch flags do not.

## What you are dealing with

Cursor is an Electron app on top of VS Code.

- The renderer is the UI. Cursor's UI is a React app called Glass.
- The main process is the server. It owns the app state and writes it to a
  SQLite file at `~/.config/Cursor/User/globalStorage/state.vscdb`.
- A setting lives in two places at once: an object in the renderer's memory, and
  a row in state.vscdb.

That split is the whole game. Edit state.vscdb while Cursor runs and the
renderer writes its in-memory copy back over your edit. Your change is gone in
seconds. To change a setting and have it stick, you call the renderer's own
code.

There is no local HTTP API and no socket to talk to. Client and server talk over
Electron's in-process IPC. So the way in from outside is the Chrome DevTools
Protocol (CDP): attach to the renderer and run JavaScript in it.

## Setup, once

Three things.

1. Open the CDP port on Cursor.
2. Patch Cursor's Glass bundle to expose the live service you want to call.
3. Clear Cursor's v8 bytecode cache, or it runs the old unpatched code.

Then restart Cursor. It will say the installation looks corrupt. That is just
its integrity check noticing the edit. Dismiss it. It comes back every launch
while the patch is in place.

`bin/cursor-glass-patch` does steps 2 and 3 and checks step 1.

### 1. Open the CDP port

Add `--remote-debugging-port=9223` to how you launch Cursor. On Linux, put a
user-level override at `~/.local/share/applications/cursor.desktop` that adds
the flag to the Exec line. It binds to 127.0.0.1 only.

9222 was taken by Chrome on my machine, so 9223. Pick any free port.

### 2. The patch

The setting we want is read and written by a method on Cursor's
reactive-storage service:

```js
reactiveStorageService.setApplicationUserPersistentStorage(key, value)
```

That service object is trapped inside a closure. From outside you cannot get a
reference to it. So we add one line to its constructor that hands us the
reference:

```js
globalThis.__cursorRS = this
```

The file to patch is:

```
/usr/share/cursor/resources/app/out/vs/workbench/workbench.glass.main.js
```

Not `workbench.desktop.main.js`. That one is legacy. The Glass renderer never
loads it. The Glass bundle loads through a dynamic import, so it does not show
up when you list the page's loaded resources. That detail cost me a while. Do
not fall into the same hole.

Run `bin/cursor-glass-patch`. It is idempotent and keeps a `.orig` backup.

### 3. Clear the cache

```sh
rm -rf ~/.config/Cursor/CachedData/* ~/.config/Cursor/Code\ Cache/*
```

The patch script does this. Without it the stale compiled bytecode wins and your
source edit does nothing. This is the part that will make you think the patch
failed when it did not.

## Driving it

`bin/cursor-cdp` is a small dependency-free CDP client. It needs Node 22 or
newer (it uses the built-in `fetch` and `WebSocket`). It runs JavaScript in
Cursor's renderer.

```sh
bin/cursor-cdp targets                                  # list pages/workers
bin/cursor-cdp eval 'document.title'                    # run JS in the workbench
bin/cursor-cdp --context isolated eval 'typeof define'  # run JS in the isolated world
bin/cursor-cdp contexts                                 # list execution contexts
```

Once the patch is in, read the setting through the hook:

```sh
bin/cursor-cdp eval 'globalThis.__cursorRS.applicationUserPersistentStorage.useOpenAIKey'
```

Flip it through the real handler:

```sh
bin/cursor-cdp eval 'globalThis.__cursorRS.setApplicationUserPersistentStorage("useOpenAIKey", true)'
```

That last call updates the in-memory store and persists to state.vscdb, through
Cursor's own code. It sticks. No click, no restart.

## The example tool

`bin/cursor-openai-toggle` wraps the above into a friendly toggle. It needs
Python 3. It reads the live value, flips it, and confirms. It also has a
`--disk` mode that edits state.vscdb directly, which only sticks while Cursor is
quit.

```sh
bin/cursor-openai-toggle --status   # show live and disk values
bin/cursor-openai-toggle            # toggle
bin/cursor-openai-toggle --on
bin/cursor-openai-toggle --off
```

## Finding your own hook

This is not specific to the OpenAI key. To drive some other piece of Cursor
state:

1. Find the setter. Grep the Glass bundle for the field name or for a label you
   saw in the UI. You are looking for a call shaped like
   `someService.setSomething("yourKey", value)`, or a method on a service.
2. Find where that service is constructed, and inject `globalThis.__x = this` to
   grab the instance. The constructor is a safe spot because it always runs.
3. Clear the cache, restart, and call your hook through cursor-cdp.

The two recon tools in `tools/` are how the map got drawn. Neither is required to
use the driver.

- `tools/memscan.py` reads a process's memory for a string. Run as root. It is
  how we confirmed a value was live in RAM, and which process held it. Useful
  for "is this string even loaded."
- `bin/cursor-netmon` captures the renderer's network traffic over CDP for N
  seconds. It is how we learned that flipping a setting fires no network request
  at all, which is what pointed us back to in-process state.

## Caveats

- A Cursor update overwrites the patched file. Re-run `cursor-glass-patch` (and
  restart). The `.orig` backup is preserved across runs.
- The "installation appears corrupt" notice is expected while patched. Dismiss
  it.
- CDP listens on 127.0.0.1. Any local process can drive your editor. That is the
  point, and also the risk. Mind what you run.
- This reaches into Cursor's internals. Cursor versions move things. If a script
  breaks, the bundle layout probably shifted. Re-derive from the field name and
  the steps above.

## License

MIT. See [LICENSE](LICENSE).
