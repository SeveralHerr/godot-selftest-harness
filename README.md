# godot-selftest-harness

A game-agnostic **self-testing harness for Godot 4.x**. It lets you (and Claude
Code) drive, inspect, and validate a running Godot game entirely from the
command line — no manual clicking in the editor. It combines three things:

1. A **file-based DevTools bridge** — an autoload that reads command files from
   `user://` and writes back results, so any process (a Python CLI, a shell
   script, Claude Code) can control the game.
2. **Headless lint and test runners** — `lint_project.gd` (UID + scene-config
   linting) and `run_tests.gd` (unit tests) that run without a display.
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

## The file-based DevTools bridge

The `DevTools` autoload globalizes the `user://` paths, then polls a command
file (`user://devtools_commands.json`) every ~100 ms. When a JSON command
appears it dispatches on the `action` string to a registered handler, writes the
handler's result to a results file (`user://devtools_results.json`), and appends
a structured line to a JSONL log. The Python client writes commands and reads
results against the same directory. This is a single-request-at-a-time bus: one
command file, one result file, no concurrent clients.

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
| `fps_min` | int | `30` | Minimum acceptable FPS for `/verify` performance gate. |
| `orphan_max` | int | `0` | Max tolerated orphan nodes. |
| `main_scene` | String | `""` | Main scene path (detected from `run/main_scene`). |
| `entry_hook` | Object | `{ "node_path": "", "method": "" }` | Optional node/method the harness calls to reach a testable game state. |
| `mute` | bool | `true` | Prefer launching muted during automated runs. |

## Generic commands

The core keeps these engine-generic verbs (bus `action` strings, underscored):

```
ping, screenshot, scene_tree, validate_scene, validate_all, get_state,
set_state, run_method, performance, quit, input_press, input_release,
input_tap, input_clear, input_actions, input_sequence, set_game_speed,
wait_frames, clear_nodes, validate_ui, get_ui_snapshot, get_node_bounds,
save_ui_baseline, ui_snapshot_diff, list_commands
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

Game-specific verbs from earlier FlexCoins-era builds (`spawn_coin`,
`spawn_coin_on_catcher`, `get_active_coins`, `set_upgrade_levels`,
`reset_session`, `get_catcher_state`, `validate_ui_interactive`, and the
`COIN_TYPE_MAP` constant) have been **removed from the core** — they now belong
in a project's `commands.gd` extension.

## Python CLI (`tools/devtools.py`)

Generic hyphenated subcommands mirror the bus verbs:

```
ping, screenshot, scene-tree, validate, validate-all, get-state, set-state,
run-method, performance, quit, logs,
input <press|release|tap|clear|list|sequence>,
set-game-speed, wait-frames, clear-nodes, validate-ui, ui-snapshot,
node-bounds, save-ui-baseline, ui-snapshot-diff
```

Two subcommands make project verbs first-class without touching the CLI:

- `cmd <action> [--args JSON]` — sends `{action: <action>, args: <parsed json>}`
  verbatim, so any project-registered verb is reachable:

  ```bash
  python3 tools/devtools.py cmd spawn_enemy --args '{"count": 3}'
  ```

- `list-commands` — sends `{action: "list_commands"}` and prints the discovered
  verbs (generic + project).

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
3. Launches the game (muted per config), waits, and **`ping`s** the bridge.
4. Runs **`validate-all`** and **`validate-ui`**, checks **`performance`**
   against `fps_min` / `orphan_max`.
5. Optionally runs a **sequence** (`test/sequences/*.json`) and can call
   `list-commands` to exercise project verbs.
6. **`quit`s** cleanly and reports pass/fail.

Run it after any script/scene/gameplay change, before committing.

## Sharp edges / risks

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
  build can move the directory the game actually writes to. If `ping` times out,
  the CLI is almost certainly polling the wrong folder — set `--userdata` or
  `GODOT_USERDATA` explicitly.
- **Godot binary detection is heuristic and macOS-biased.** It checks
  `GODOT_BIN`, then the standard macOS app path, then `which godot`. On Linux /
  Windows (or with a non-standard install) set `GODOT_BIN` to the executable.
- **`class_name` collisions.** Addon classes are namespaced (e.g.
  `GodotSelftestSceneValidator`) so they never clash with a project's own class
  names; the core loads them by path, so the `class_name` is convenience only.
  Keep new addon classes namespaced.
- **Single-client file bus.** The bridge is one command file / one result file
  with no locking. Concurrent clients race and clobber each other's
  commands/results. Drive the game from **one** client at a time.
