# godot-selftest-harness

A game-agnostic **self-testing harness for Godot 4.x**. It lets you (and Claude
Code) drive, inspect, and validate a running Godot game entirely from the
command line — no manual clicking in the editor. 

This project was inspired by
https://github.com/cleak/tea-leaves and adapts the same runtime-driven testing
concepts for Godot. It combines three things:I

1. A **file-based DevTools bridge** — an autoload that reads command files from
   `user://` and writes back results, so any process (a Python CLI, a shell
   script, Claude Code) can control the game.
2. **Headless lint and test runners** — `lint_project.gd` (UID + scene-config
   linting, duplicate `ext_resource`/`sub_resource` id detection, baseline-delta
   mode) and `run_tests.gd` (unit tests) that run without a display.
3. A diff-aware **`/verify`** workflow that ties lint, tests, and a live runtime
   smoke test into a single pre-commit gate.

The harness itself contains **no game-specific logic**. Each project registers
its own debug verbs through a small, documented registry extension, so the core
stays reusable across projects.

## Why

Godot games are hard to test from CI or from an agent: the interesting behavior
only exists at runtime, inside a running scene tree. This harness exposes that
runtime over a simple file bus and a Python CLI, so you can spawn entities,
inject input, read node state, snapshot the UI, measure FPS, and assert on all
of it from scripts — and Claude Code can do the same to check its own changes.

## Requirements

- **Godot 4.x** (4.6+ recommended). The scaffolder aborts on 3.x.
- **Python 3** (standard library only) for the CLI client.
- macOS / Linux / Windows. Godot-binary auto-detection is macOS-biased; other
  platforms set `GODOT_BIN` (see below).

## Install

1. Add this plugin to Claude Code (via your marketplace / plugin config).
2. Open a Godot 4.x project directory and run:

   ```
   /scaffold-godot-harness
   ```

   This copies the addon, tools, a registry-extension stub, and seed tests into
   the project; writes `devtools_config.json` with values detected from
   `project.godot`; adds the `DevTools` autoload; and runs a headless smoke
   check. See `commands/scaffold-godot-harness.md` for the exact steps.

> Note: if the Godot editor has the project open while you scaffold, close and
> reopen it so the editor picks up (and doesn't clobber) the edited
> `project.godot`.

### Project `CLAUDE.md`

Scaffolding also creates or updates the project's `CLAUDE.md` with a delimited
harness section — bounded by `<!-- BEGIN godot-selftest-harness -->` and
`<!-- END godot-selftest-harness -->` — so Claude Code in that project knows the
harness exists and how to drive it. Re-scaffolding refreshes the section in place
(matched by those markers), so it's idempotent and never duplicated; content
outside the markers is left untouched, and an existing `CLAUDE.md` without them
just gets the section appended. The section is intentionally lean and
reference-style because `CLAUDE.md` is always-on context — it points at `/verify`,
`/scaffold-godot-harness`, and this README rather than restating them.

## The devtools gaps log

Scaffolding installs `log-devtools.md` at the project root and a matching rule in
`CLAUDE.md`: **at the end of every response, append an entry naming any gap in
`/verify` or the devtools that would have helped with the task, plus a suggested
improvement for each.** An honest "no gaps this turn" line counts — it is what
makes an absent gap distinguishable from a forgotten log.

```markdown
## 2026-07-25 — Animate the HUD orb losing a hit point

- Gap: **`get-state` has no `--property` filter**, despite the cheat-sheet listing it.
  `devtools.py: error: unrecognized arguments: --property scale` — so every read
  dumped ~200 properties and had to be grepped.
  - Improvement: add `--property` (repeatable) to `get-state`.
```

This is the harness's improvement pipeline, not bookkeeping. Nearly every capability
here beyond the first version — the status provider, node-path normalization, the
property filter, `step-time`, the touch verbs, the orphan baseline — exists because a
session wrote down what it couldn't do at the moment it couldn't do it. That evidence
is perishable: once a workaround is found, the friction that forced it is forgotten by
the next turn. Entries that quote real output are the ones that later become features.

Each gap carries a status line, which is what makes the file answerable later:

```markdown
  - [G-007] status: open | seen: 2 | harness: 0.4.0
```

Ids are stable and never reused. `status:` is `open`, `fixed` (plus `fixed-in: X.Y.Z`) or
`wontfix`; a gap whose fix shipped only in part stays **open**. `seen:` is bumped when a
gap bites again — a `seen: 3` is the strongest signal this file can produce, and three
separately-worded entries are the weakest. `harness:` records the installed version
(`python3 tools/devtools.py harness-version`), so a gap logged before an upgrade is
distinguishable from a regression after one.

### Getting gaps upstream

A logged gap only becomes a fix once it reaches this repo, and for one full release the
only transport was a human pasting text between two repositories — a transport that never
ran. `tools/upstream_gaps.py` (installed into every project) is that transport:

```bash
# from the project, pushing its open gaps up
python3 tools/upstream_gaps.py log-devtools.md \
    --into /path/to/godot-selftest-harness/log-devtools.md

# from the harness repo, pulling from several projects at once
python3 tools/upstream_gaps.py ../game-a/log-devtools.md ../game-b/log-devtools.md
```

It is deliberately boring: no PR, no review step, no filtering by importance. Open gaps
are appended verbatim, deduped by id, and `seen:` is bumped when an id reappears. Ids are
qualified with the project name on the way up (`gather:G-007`) because a `G-007` exists in
every project's log; a gap with no id at all still travels, under a stable `auto-<hash>`
derived from its text. Repeat sightings within one source log collapse into the highest
`seen:` count rather than becoming two entries. The source is never modified, nothing is
ever deleted from the destination, and a second run on unchanged input is a no-op. Flags:
`--project NAME`, `--include-fixed`, `--dry-run`.

A `Stop` hook (`tools/check_devtools_log.py`, wired into `.claude/settings.json`)
prints a reminder when a session changes Godot code without adding an entry **dated
today** to the log. It reads the file's `## ` headings rather than its git status, because
"the file changed" is satisfied by any stray byte — a log whose newest entry was three
weeks old used to pass, which is exactly the decay the hook exists to catch. It is
advisory by default — set `"log_check_block": true` in `devtools_config.json` to make
it a blocking `Stop` instead, or `"log_check_dated_entry": false` to fall back to the
weaker check. It is written in Python rather than shell so it behaves
identically on Windows, macOS, and Linux, and it always exits 0: a reminder must never
break a session.

## The file-based DevTools bridge

The `DevTools` autoload globalizes the `user://` paths, then polls a command
file (`user://devtools_commands.json`) every ~100 ms. When a JSON command
appears it **deletes the file**, dispatches on the `action` string to a registered
handler, writes the handler's result to a results file
(`user://devtools_results.json`), and appends a structured line to a JSONL log. The
Python client writes commands and reads results against the same directory. This is a
single-request-at-a-time bus: one command file, one result file, no concurrent clients.

The wire contract:

```jsonc
// user://devtools_commands.json
{ "id": "9f2c1a4b7de0", "action": "get_state", "args": { ... } }

// user://devtools_results.json
{ "id": "9f2c1a4b7de0",        // echoes the request; "" if the request carried none
  "action": "get_state", "success": true, "message": "...", "data": { ... },
  "timestamp": 1785592028.4, "status": { ... } }   // "status" only if a provider is registered
```

Two properties fall out of this and both matter:

- **`id` makes a crossed reply detectable.** The client refuses a reply stamped for
  a different request rather than returning it. A response with no `id` key at all is
  accepted, so a newer client still works against an older game build.
- **Deleting the command file on pickup is the liveness signal.** If the file is still
  there ~2 s later, nothing is polling that directory — the game is dead, or the client
  is polling the wrong `user://`. That is why a dead game now fails in seconds rather
  than at the end of a 30–60 s timeout.

## The registry-extension pattern

Generic verbs live in the core. **Project-specific** verbs live in
`res://devtools_ext/commands.gd`, which the core loads by path after registering
all generic handlers. The extension is a `RefCounted` that implements
`register_commands(dev)`:

```gdscript
extends RefCounted

func register_commands(dev: Node) -> void:
    # dev.register_command(action: String, handler: Callable) -> void
    # last-writer-wins: registering an existing action overrides the generic one.
    dev.register_command("spawn_enemy", _spawn_enemy)
    dev.register_command("get_score", _get_score)

    # dev.register_status_provider(provider: Callable) -> void
    # The returned Dictionary is merged into EVERY response as "status".
    dev.register_status_provider(_status)

func _status(_args: Dictionary) -> Dictionary:
    # Keep this tiny — it rides on every single reply.
    var p := dev.get_tree().get_first_node_in_group("player")
    return { "player": "absent" if p == null else ("dead" if p.is_dead else "alive") }

func _spawn_enemy(args: Dictionary) -> Dictionary:
    var count := int(args.get("count", 1))
    # ...operate on the running scene tree...
    return { "success": true, "message": "spawned %d" % count, "data": { "count": count } }

func _get_score(_args: Dictionary) -> Dictionary:
    var score := 0  # read from your game state
    return { "success": true, "message": "ok", "data": { "score": score } }
```

**Every handler must return exactly** `{ "success": bool, "message": String,
"data": Dictionary }` — the core and the Python client both depend on this
shape.

**A setter verb must leave the game in a state the game itself can reach.** A verb that
writes one half of an invariant pair is a latent trap: a `set_combo` that set the count
but not the combo window produced a state where `combo_fraction()` stayed 0 forever.
That was harmless while the HUD drew the count unconditionally, and became a verb that
silently tested nothing the moment the readout started fading on that timer. The same
applies to restoring a value without clearing the flag that guards it — setting health
back to full on a dead entity leaves `is_dead` true and the state machine in its death
state, so the run stays frozen and unrescuable short of a relaunch.

Core `_ready()` order is deliberate:

1. Globalize `user://` paths.
2. Load `devtools_config.json`.
3. Register **all generic handlers**.
4. Load + instantiate the extension and call `register_commands(self)`.
5. Clear stale command/result files from a previous run.

Because the extension is registered **after** the generic handlers, a project
can override a generic verb by registering the same action name.

## `devtools_config.json` schema

Lives at `res://addons/godot_selftest/devtools_config.json`.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `validator_script` | String | `res://addons/godot_selftest/scene_validator.gd` | Scene validator loaded by path. |
| `extension_script` | String | `res://devtools_ext/commands.gd` | Project command registry extension. |
| `hud_layer_name` | String | `HUD` | CanvasLayer name used by UI snapshot/validation verbs. |
| `test_dir` | String | `res://test/unit` | Directory the test runner scans for `test_*.gd`. |
| `scan_root` | String | `res://` | Root for scene/UID scanning. |
| `uid_check_ignore` | Array | `["res://addons/", "res://tools/"]` | Path prefixes exempt from the missing-`.uid`-sidecar warning. The defaults cover the files the scaffolder copies in — it can't generate a valid `.uid` (ids are engine-assigned), and a gate that cries wolf on install day gets ignored. |
| `fps_min` | int | `30` | Minimum acceptable FPS for `/verify` performance gate. |
| `orphan_max` | int | `0` | Max tolerated **absolute** orphan nodes. Kept for compatibility only — `0` is unreachable in a real project (a fresh launch reports dozens). Gate on `orphan_growth_max` instead. |
| `orphan_growth_max` | int | `20` | Max tolerated growth in orphan nodes vs. the startup baseline. This is the number that means "this change leaks". |
| `safe_area_inset` | Object | `{left:0, top:0, right:0, bottom:0}` | Pixels trimmed off each viewport edge before `validate_ui` judges on-screen-ness. All-zero disables the check. |
| `main_scene` | String | `""` | Main scene path (detected from `run/main_scene`). |
| `entry_hook` | Object | `{ "node_path": "", "method": "" }` | Optional node/method the harness calls to reach a testable game state. |
| `entry_points` | Object | `{}` | Named alternate entry points, each `{scene, node_path, method, args, match}`. `/verify` picks the one whose `match` substrings hit the diff, so a change to a boss/shop/level script has a runtime path instead of only a code read. |
| `mute` | bool | `true` | Prefer launching muted during automated runs. |
| `log_files` | Array | `["log-devtools.md"]` | Files the `Stop` hook expects to change alongside code. |
| `log_check_globs` | Array | `[]` | Extra path substrings the `Stop` hook counts as "code". |
| `log_check_block` | bool | `false` | `true` makes the logging reminder a blocking `Stop` instead of advisory. |
| `log_check_dated_entry` | bool | `true` | The `Stop` hook requires an entry heading carrying **today's date**. `false` falls back to "the file changed at all", which any stray byte satisfies. |

## Generic commands

The core keeps these engine-generic verbs (bus `action` strings, underscored):

```
ping, screenshot, scene_tree, validate_scene, validate_all, get_state,
set_state, run_method, performance, quit, input_press, input_release,
input_tap, input_clear, input_actions, input_sequence, set_game_speed,
wait_frames, step_time, clear_nodes, validate_ui, get_ui_snapshot,
get_node_bounds, save_ui_baseline, ui_snapshot_diff, list_commands,
touch_press, touch_release, touch_drag, touch_clear, touch_list, set_feature,
harness_version
```

Notable behaviors:

- **`clear_nodes`** is a generic replacement for game-specific "clear" verbs. It
  accepts one selector — `{"group": <name>}`, `{"method": <method_name>}`, or
  `{"class": <ClassName>}` — and frees matching descendants of the current
  scene, returning `{"count": n}`. With **no selector** it returns
  `success: false` with a helpful message; it never blindly frees the tree.
- **`list_commands`** returns `{"success": true, ..., "data": {"actions":
  [sorted handler names]}}` so `/verify` and the Python client can discover the
  project-registered verbs.
- **`harness_version`** returns `{"harness_version", "handlers",
  "extension_loaded", "config_path"}` — the revision the installed files were copied
  from. `list_commands` shows the verbs but never the revision, so deciding whether a
  refresh was a no-op or a real upgrade used to mean diffing template files against this
  repo by hand. The CLI prints the game's version *and* its own and **exits 1 when they
  differ**, which is what makes a half-refreshed install (new client, old autoload)
  visible instead of mysterious. Every copied file also carries a
  `# harness-version: X.Y.Z` header stamp, and `lint_project.gd` prints the version in
  its header, so a lint result or a logged gap can always name the version it came from.
- **`get_state`** takes an optional `properties` array (the CLI's repeatable
  `--property`). Names that don't exist come back in `data["missing"]` rather than
  being silently dropped. `data["transform"]` is **always** present — see the sharp
  edge below for why the property dump alone is not trustworthy for layout.
- **`step_time`** advances the running game by roughly N game-seconds with
  `Engine.time_scale` pinned to 1.0. Read the sharp edge below before trusting it.
- **`touch_press` / `touch_release` / `touch_drag` / `touch_clear` / `touch_list`**
  dispatch real `InputEventScreenTouch` / `InputEventScreenDrag` events, so multi-touch
  paths are exercisable. Malformed positions are rejected rather than read as the
  origin, a drag from an unheld index is an error rather than a drag from `(0,0)`, and
  held touches are released by `input_clear` and on exit so a run can't leave phantom
  fingers down.
- **`set_feature`** takes `{"touchscreen": bool}` and calls
  `Input.set_emulate_touch_from_mouse()`, which does flip
  `DisplayServer.is_touchscreen_available()` (verified on 4.7.1, headless and
  windowed). This is what makes touch UI visible on a desktop build — it otherwise
  hides itself and every screenshot needs manual `visible` overrides. Two caveats: it
  is a live query, not a signal, so a `Control` that read availability in its own
  `_ready()` won't re-evaluate (set the flag before the scene loads); and it is real
  emulation, so mouse input also arrives as touch.
- **`validate_ui`** flags `ui_outside_safe_area` when `safe_area_inset` is configured
  — for overlays (a CRT shader, a notch, a rounded corner) that eat the viewport edges
  without any validator knowing. The check is skipped entirely when the inset is
  all-zero, so it adds no findings to an existing project.

Game-specific verbs from earlier FlexCoins-era builds (`spawn_coin`,
`spawn_coin_on_catcher`, `get_active_coins`, `set_upgrade_levels`,
`reset_session`, `get_catcher_state`, `validate_ui_interactive`, and the
`COIN_TYPE_MAP` constant) have been **removed from the core** — they now belong
in a project's `commands.gd` extension.

## Python CLI (`tools/devtools.py`)

Generic hyphenated subcommands mirror the bus verbs:

```
ping, screenshot, scene-tree, validate, validate-all, get-state, set-state,
run-method, performance, quit, logs, harness-version,
input <press|release|tap|clear|list|sequence>,
touch <press|release|drag|clear|list>, set-feature, step-time,
set-game-speed, wait-frames, clear-nodes, validate-ui, ui-snapshot,
node-bounds, save-ui-baseline, ui-snapshot-diff
```

Notable flags:

- `get-state --node PATH --property NAME` — repeatable. Without it a single `Label`
  read returns ~120 keys, which is why every assertion used to be piped through an
  ad-hoc filter. A name that doesn't exist is reported explicitly rather than silently
  omitted, so a typo can't look like a missing value.
- `performance --reset-baseline` — re-baseline the orphan count (see below).
- `--no-precheck` — global; skip the ~2 s "is the game alive" check and wait the full
  timeout.

Two subcommands make project verbs first-class without touching the CLI:

- `cmd <action> [--args JSON]` — sends `{action: <action>, args: <parsed json>}`
  verbatim, so any project-registered verb is reachable:

  ```bash
  python3 tools/devtools.py cmd spawn_enemy --args '{"count": 3}'
  ```

- `list-commands` — sends `{action: "list_commands"}` and prints the discovered
  verbs (generic + project).
- `harness-version` — prints the installed revision game-side and client-side. Exits 1
  if they disagree, or if the running build predates the verb entirely (which names the
  fix: re-run `/scaffold-godot-harness`). Use it to fill the `harness:` field when
  logging a gap.

### Parallel verification (`--session`)

The bridge is one command file and one result file in one `user://` directory, so a
second running instance answers the first client's commands and neither notices. In
practice that means concurrent agents cannot verify at runtime at all — every one of them
has to be forbidden from launching the game, and a single owner does each check serially.

`--session <id>` splices the id into the bus filenames
(`devtools_commands_<id>.json`, …) on **both** halves, so instances stop crossing:

```bash
# each instance owns a bus
godot --path . --mute -- --devtools-session a &
godot --path . --mute -- --devtools-session b &

python3 tools/devtools.py --session a ping   # DevTools is running (…, session: a)
python3 tools/devtools.py --session b ping
```

Game-side the id comes from `-- --devtools-session <id>` or the `GODOT_DEVTOOLS_SESSION`
environment variable (the flag wins); client-side from `--session/-S` or the same
variable. With no session the filenames are exactly what they always were, so nothing
about existing single-instance usage changes. `ping` and `harness-version` report the
session they answered on, so "which instance is this?" is answerable rather than assumed.

**A shared `user://` is still shared.** Separate buses fix the crossing, not the rest of
the directory: screenshots, UI baselines and save files still collide, and
`--headless --import` still races on a single `.godot/` class cache. For real isolation,
give each instance its own userdata directory too:

```bash
GODOT_USERDATA=/tmp/run-a godot --path . -- --devtools-session a &
python3 tools/devtools.py --session a --userdata /tmp/run-a ping
```

or set `application/config/use_custom_user_dir` + `custom_user_dir_name` in a per-worker
copy of `project.godot`. Use `--session` when instances share a directory; use both when
they must not share anything.

### Userdata directory resolution

The CLI must poll the same `user://` directory the game writes to. It resolves
that directory in priority order:

1. `--userdata <path>` CLI flag.
2. `GODOT_USERDATA` environment variable.
3. `application/config/use_custom_user_dir` + `custom_user_dir_name` from
   `project.godot` (if a custom user dir is configured).
4. The default per-platform Godot location:
   - macOS: `~/Library/Application Support/Godot/app_userdata/<config-name>/`
   - Linux: `~/.local/share/godot/app_userdata/<config-name>/`
   - Windows: `%APPDATA%\Godot\app_userdata\<config-name>\`

## `/verify` workflow

`/verify` (shipped by this plugin) is the pre-commit gate. In summary it:

1. Runs the **headless linter** (`tools/lint_project.gd`) over changed/all scenes.
2. Runs the **headless unit tests** (`tools/run_tests.gd`).

Both runners call `quit(n)` with their own finding count, so the exit code means what
it says: `0` pass, `1` findings, `2` the runner itself could not run. Without that, the
code is Godot's — which reports leaked RIDs/ObjectDB instances at shutdown and returns
`1` on a perfectly clean project, making any CI gate keyed off it read pure noise.

The linter's flags (pass after `--`):

| Flag | Purpose |
|---|---|
| `--strict` | Warnings fail the run too (default: errors only). |
| `--baseline-write PATH` | Write the current finding set to `PATH` and exit 0. |
| `--baseline PATH` | Group findings into `NEW` (drives the exit code) and `PRE-EXISTING`. |
| `--find-orphans` | Warn on public functions whose only outside callers are tests. Advisory; never fails. |

Baseline keys are `file|rule|subject` with no line numbers, so a finding survives
unrelated edits to the same file. This exists because deciding whether a warning is
repo debt or something you just caused otherwise means hand-checking `git log` per
file — the same "is this noise mine?" problem that made an absolute orphan threshold
useless.

The UID pass has two halves. It validates that every `uid=` in a `.tscn`/`.tres` still
resolves to the same resource, **and** that every `.gd` under `scan_root` / `test_dir`
actually has a `.uid` sidecar. The second half exists because the first only inspects
sidecars that already exist: a script created outside the editor — which is every script
an agent writes — had no sidecar at all and lint happily reported `UIDs: OK`. The
omission then surfaced at review time, or as a broken reference on someone else's
machine. Missing sidecars are **warnings** (so they don't fail an existing project until
you pass `--strict`), `uid_check_ignore` exempts paths, and the check stands down
entirely if no `.gd` in the project has a sidecar — Godot only started writing them in
4.4, and flagging every file in a 4.3 project would be noise, not a finding.

`--find-orphans` covers a failure both other gates miss: a system with passing unit
tests and no caller anywhere in the game. Lint checks UIDs and scenes, the test runner
green-lights orphaned code, and both report clean. It is a heuristic — signal
callbacks, `call()`-by-name, and `@export` hooks produce false positives — so it is
opt-in and advisory.

### Headless UI tests

Headless Godot renders nothing and pumps no frames on its own, so a `Control` under
test reports `size == (0, 0)` and its `@onready` vars never initialize — layout is
simply not assertable by default. The runner exposes a helper pair for this:

```gdscript
func test_hud_fills_the_viewport() -> String:
    var ui: Control = await _T.instantiate_ui("res://scenes/hud.tscn", Vector2i(640, 360))
    var err: String = _T.assert_eq(ui.size, Vector2(640, 360), "hud fills the viewport")
    _T.free_ui(ui)
    return err
```

`instantiate_ui` accepts a `PackedScene`, a `res://` path, or an already-built `Node`;
it hosts the scene in a `SubViewport` of the given size, then awaits two `process_frame`s
so anchors resolve and `@onready` runs. `free_ui` frees the host immediately (not
`queue_free`) so nothing lands in the orphan count that `/verify` watches. Test methods
may now `await`; synchronous tests are unaffected.

This makes layout, anchors, and container sorting assertable. It does **not** make
pixels assertable — nothing is rendered. Visual regressions still need a running game
and a screenshot.

The test runner's flags (pass after `--`):

| Flag | Purpose |
|---|---|
| `--filter NAME` | Run tests whose **method name or test script filename** contains `NAME` (case-insensitive). |
| `--file NAME` | Run one test script: a bare name (`test_player`), a filename (`test_player.gd`), or any path substring. Combines with `--filter` via AND. |
| `--json` | Emit the full result dictionary, including `discovered`, `selected`, `filter`, `file`, `selection_error`. |

**A selection that matches nothing is exit 2, not a pass.** `--filter` used to match method
names only, so `--filter spawner` against a brand-new `test_enemy_spawner.gd` selected
nothing, skipped the entire suite, and printed `Total: 0 | Passed: 0 | Failed: 0` with exit
`0` — byte-identical to a clean run for anything grepping the exit code. Two agents in one
session shipped work on the strength of that output. The runner now matches filenames too,
reports `Selected: N of M discovered` whenever a selector is in play, and treats a
zero-selection (or discovering no test scripts at all) as "nothing was verified".

### What the test runner cannot catch

GDScript has no exception handling, and a runtime error inside a test method aborts
only that method, returning the declared type's default value — `""` for a `-> String`
test, which is exactly what a pass looks like. The runner cannot distinguish the two.
**Check stderr**: `[ERR]` / `[SCRIPT ERROR]` lines are the only evidence. There is also
no watchdog, so a test awaiting a signal that never fires will hang the run.
3. Launches the game (muted per config), waits, and **`ping`s** the bridge.
4. Runs **`validate-all`** and **`validate-ui`**, checks **`performance`**
   against `fps_min` / `orphan_max`.
5. Optionally runs a **sequence** (`test/sequences/*.json`) and can call
   `list-commands` to exercise project verbs.
6. **`quit`s** cleanly and reports pass/fail.

Run it after any script/scene/gameplay change, before committing.

## Sharp edges / risks

- **A frozen session answers every query with well-formed zeros.** Once the thing
  under test is dead or paused, nothing moves and nothing changes — which reads
  identically to a genuine clean result, so a broken run gets mistaken for a
  passing one. Register a status provider (above) so liveness rides on every
  response, and give yourself a verb that can *undo* the dead state. Restoring a
  health value is rarely enough: the death flag and state machine outlive it.
- **One in-flight command at a time.** The bridge is still a single command/result
  file pair — it is not concurrent. What changed is that a collision is now *loud*:
  the client stamps each request with an id, the game echoes it, and a reply
  carrying someone else's id is refused rather than returned. A second thread
  polling while you sample now fails with `Crossed replies: …` instead of handing
  back another request's data (which used to surface as a baffling missing key).
  Serialize your calls regardless — detection is not concurrency.
- **`get_state`'s property dump is not the whole node.** Godot clears the storage
  usage flag on `position`/`rotation`/`scale`/`pivot_offset` for **container
  children**, and Controls expose `offset_transform_scale` (which stays `1.0` while
  the node visibly scales). So a scale animation on a `VBoxContainer` child is
  invisible to a property dump while working perfectly on screen — verified on 4.7.1,
  where the same `Label` reports usage `6` under a plain `Control` and
  `READ_ONLY|EDITOR` under a `VBoxContainer`. Assert on `data.transform`, which is
  always present and read directly off the node.
- **`step_time` does not actually step.** GDScript cannot tick the SceneTree, so the
  verb runs the game at normal speed and returns when enough time has passed; the tree
  is neither paused nor stepped. What it does buy you is real: `Engine.time_scale` is
  pinned to 1.0 for the duration (so a leftover `set-game-speed 0.05` can't silently
  stretch the interval), and physics time is exact. Process-driven tweens —
  Godot's `Tween` default — land within about one frame. Compare the returned
  `process_seconds` against `seconds_requested` rather than assuming, and use
  `TWEEN_PROCESS_PHYSICS` when the sample point actually matters.
- **Registry-extension lifetime.** The core must keep a live reference to the
  instantiated extension (`var _extension`). If it lets the `RefCounted` go out
  of scope, the bound `Callable`s are freed and every project verb silently
  fails. Don't register from a throwaway local.
- **Autoload ordering.** Load `DevTools` **after** any game autoloads its
  handlers call. Godot initializes autoloads top-to-bottom; if DevTools loads
  first, an extension handler that touches `GameState` (or similar) at
  registration or first call can hit a not-yet-ready singleton. The scaffolder
  appends `DevTools` last for this reason.
- **Userdata path drift.** A custom `user://` (via `use_custom_user_dir` /
  `custom_user_dir_name`), a sandboxed/exported build, or a differently-named
  build can move the directory the game actually writes to. This now fails fast:
  the client fails in ~2 s with `game not running` (naming the directory it polled)
  instead of blocking for the full timeout, because the autoload deletes the
  command file the instant it reads it — a file still sitting there means nothing
  is polling that directory. Note that a *wrong userdata dir* is indistinguishable
  from a *dead game* by this signal, which is why the error says both. `--no-precheck`
  disables it.
- **Godot binary detection is heuristic and macOS-biased.** It checks
  `GODOT_BIN`, then the standard macOS app path, then `which godot`. On Linux /
  Windows (or with a non-standard install) set `GODOT_BIN` to the executable.
- **`python3` exists on Windows and does not run.** Windows ships a Microsoft Store
  *App execution alias* stub at `python3.exe`, so `command -v python3` succeeds and
  then every invocation fails with `Python was not found; run without arguments to
  install from the Microsoft Store…`. Probe interpreters by **executing** them
  (`"$c" -c "import sys"`), never by existence. The scaffolder does this when wiring
  the `Stop` hook; do the same in any script you add.
- **The Godot binary on Windows may write nothing to the console.** The non-console
  build produces no stdout in PowerShell, so a headless lint/test run looks like a
  silent success. Redirect to a file and read it back rather than trusting an empty
  console.
- **`class_name` collisions.** Addon classes are namespaced (e.g.
  `GodotSelftestSceneValidator`) so they never clash with a project's own class
  names; the core loads them by path, so the `class_name` is convenience only.
  Keep new addon classes namespaced.
- **Single-client file bus.** The bridge is one command file / one result file
  with no locking. Concurrent clients on the **same** bus still race and clobber
  each other's commands; request ids make the resulting crossed reply an error rather
  than silent corruption, but they do not make it safe. Drive one bus from **one**
  client at a time, and give genuinely parallel instances separate buses with
  `--session` (above).
