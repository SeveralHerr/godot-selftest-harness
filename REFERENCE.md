# godot-selftest-harness — reference manual

Every verb, flag, config key and sharp edge. Start at [README.md](README.md) if you
just want to know what this is.

A game-agnostic **self-testing harness for Godot 4.x**. It lets you (and Claude
Code) drive, inspect, and validate a running Godot game entirely from the
command line — no manual clicking in the editor.

This project was inspired by
https://github.com/cleak/tea-leaves and adapts the same runtime-driven testing
concepts for Godot. It combines three things:

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


> Command blocks say `python`. On systems where only `python3` exists, use that; on Windows, probe by *executing* (`python -c ""`) — the Store's `python3` alias passes `command -v` and then refuses to run.

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
`/scaffold-godot-harness`, and this reference rather than restating them.

## The devtools log

Scaffolding installs `log-devtools.md` at the project root and a matching rule in
`CLAUDE.md`. Every entry has **two required halves**: whether running the harness was
worth it, and what was missing from it. An honest "no gaps this turn" line counts for
the second half — it is what makes an absent gap distinguishable from a forgotten log.

```markdown
## 2026-07-25 — Animate the HUD orb losing a hit point

- Value: **warranted** — the tween landed at the wrong scale and only the running game said so.
  - Expected: whether `orb.scale` actually returns to 1.0 after the hit animation.
  - Got: `get-state --property scale` read `0.85` at rest; `data.transform` confirmed it.
  - Cheaper: nothing. Reading the file is what produced the wrong belief in the first place.

- Gap: **`get-state` has no `--property` filter**, despite the cheat-sheet listing it.
  `devtools.py: error: unrecognized arguments: --property scale` — so every read
  dumped ~200 properties and had to be grepped.
  - Improvement: add `--property` (repeatable) to `get-state`.
```

### Why value is recorded, not only gaps

A log that asks only "what was missing?" can only ever answer "add more harness." It has
no vocabulary for *this task didn't need the tool* — so a harness that is the wrong
choice for half the changes it runs on would generate a tidy stream of feature requests
and never once suggest being used less. The `Value:` block is the half that can say so.

| Verdict | Means | What it should change |
|---|---|---|
| `warranted` | Runtime produced a claim the diff could not. Name it specifically. | Nothing — the harness worked. |
| `overkill` | Everything passed and confirmed what was already known. | Invoke `/verify` less for this shape of change. |
| `insufficient` | It ran but couldn't reach or assert what mattered. Reach decides this, not impression. | File the gap. |
| `inconclusive` | Aborted, or too small to judge. | Nothing. Don't inflate to `warranted`. |

This is a self-report about the tool's own usefulness, and self-reports of that kind bias
one way. Four things push back, and none of them make it objective — they make it harder
to inflate without noticing:

1. **The prediction is written first.** `/verify` Phase 4 Step 1 records what runtime
   should reveal that the diff cannot, *before* any test runs; Phase 6 copies it in
   verbatim. "It was useful" is easy to write after any run that passed and much harder
   with a prediction already on the page that the run merely confirmed.
2. **`Cheaper:` demands a concrete alternative** — "reading `ore_vein.gd:40-52`", "lint
   alone, 4s", "nothing, this needed the running game". "Probably still worth it" is not
   an answer to the question that was asked.
3. **The verdict is cross-checked twice, mechanically.** `verify_ledger.py record`
   downgrades a `warranted` whose changed files were never loaded to `insufficient`, and
   one whose `found` list is empty to `overkill`. Both keep the original under
   `value_reported` so the disagreement stays auditable.
4. **`Found:` is asked separately from `Value:`**, because they fail differently: the
   verdict is an impression formed at the end of a run, while `found` is a list of things
   that either happened or didn't. A bug surfaced and fixed mid-run belongs here — it is
   invisible in every other field.
5. **It is a countable enum, not prose**, so `stats` can say "31% of runs were overkill"
   — a sentence no amount of narrative in a log will ever produce.

`overkill` is the verdict that will be under-reported, because a run that passed feels
like a run that helped. `stats` says so out loud when a long stretch contains none, and
the collected `Cheaper:` lines are the real product: if "reading the file" appears thirty
times, that is a finding about *when* to reach for the harness, which no amount of
feature work on it would have surfaced.

The gaps half is the harness's improvement pipeline, not bookkeeping. Nearly every capability
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
(`python tools/devtools.py harness-version`), so a gap logged before an upgrade is
distinguishable from a regression after one.

### Getting gaps upstream

A logged gap only becomes a fix once it reaches this repo, and for one full release the
only transport was a human pasting text between two repositories — a transport that never
ran. `tools/upstream_gaps.py` (installed into every project) is that transport:

```bash
# from the project, pushing its open gaps up
python tools/upstream_gaps.py log-devtools.md \
    --into /path/to/godot-selftest-harness/log-devtools.md

# from the harness repo, pulling from several projects at once
python tools/upstream_gaps.py ../game-a/log-devtools.md ../game-b/log-devtools.md
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
weaker check. Since 0.7.0 it also requires today's entry to carry a `- Value:` verdict
(`"log_check_value": false` turns that off): the value half is the one that can say the
harness wasn't needed, which makes it the half a rushed session drops, and its absence is
invisible because a log full of gap entries looks diligent. It is written in Python rather than shell so it behaves
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
| `name_check_extra_types` | Array | `[]` | Type names `tools/name_check.py` should accept without proof — the escape hatch for classes a GDExtension registers at runtime, which `--dump-extension-api` cannot see. Leave empty until the checker reports a false positive; every name added here is a name it will never again tell you is missing. |
| `name_check_ignore` | Array | `[]` | Path prefixes exempt from `name_check.py` findings. Generated or vendored-but-not-plugin code goes here. Vendored addons (an `addons/<name>/` holding a `plugin.cfg`) and `.gdignore` directories are already exempt without configuration. |
| `reach_aliases` | Object | `{}` | Credits a script reach can never observe to the observed script(s) that vouch for it: `{"world/tile_path_finder.gd": ["world/tile_scenes/bone_worker.gd"]}`. A `RefCounted` or `Resource` held as a plain field is never any node's script, so no amount of exercising it registers — and a permanently deflated reach number teaches readers to ignore the field. Credited files land in a **separate bucket** (`+N by alias`), never folded into `reached`: it is a claim your config makes, shown so a reader can disbelieve it. A voucher that was itself not reached credits nothing. |
| `reach_headless_dirs` | Array | `["tools/"]` | Directories whose scripts only ever run under `godot --headless --script`. They cannot be any node's `script`, so reach scores them `headless_tools` (a sub-list of `not_applicable`) instead of counting them as misses — otherwise `lint_project.gd` and `run_tests.gd` are charged as unreached by the runs that just executed them. Matching is on whole path segments, so `tools/` never swallows `toolsy/`. Set to `[]` if your `tools/` genuinely holds game code. `addons/` is not covered here by design: `dev_tools.gd` is the autoload and resolves through `reached_implicit`. |
| `fps_min` | int | `30` | Minimum acceptable FPS for `/verify` performance gate. |
| `orphan_max` | int | `0` | Max tolerated **absolute** orphan nodes. Kept for compatibility only — `0` is unreachable in a real project (a fresh launch reports dozens). Gate on `orphan_growth_max` instead. |
| `orphan_growth_max` | int | `20` | Max tolerated growth in orphan nodes vs. the startup baseline. This is the number that means "this change leaks". |
| `safe_area_inset` | Object | `{left:0, top:0, right:0, bottom:0}` | Pixels trimmed off each viewport edge before `validate_ui` judges on-screen-ness. All-zero disables the check. |
| `main_scene` | String | `""` | Main scene path (detected from `run/main_scene`). |
| `entry_hook` | Object | `{ "node_path": "", "method": "" }` | Optional node/method the harness calls to reach a testable game state. |
| `entry_points` | Object | `{}` | Named alternate entry points, each `{scene, node_path, method, args, match}`. `/verify` picks the one whose `match` substrings hit the diff, so a change to a boss/shop/level script has a runtime path instead of only a code read. |
| `godot_version` | String | `""` | Engine version the scaffolder resolved (`X.Y.Z`, from `godot --version`). `name_check.py` compares its cached API index against this and warns on a mismatch — without it, an index built by another engine is used silently, resolving classes the project's engine may not have. |
| `mute` | bool | `true` | Prefer launching muted during automated runs. |
| `log_files` | Array | `["log-devtools.md"]` | Files the `Stop` hook expects to change alongside code. |
| `log_check_globs` | Array | `[]` | Extra path substrings the `Stop` hook counts as "code". |
| `log_check_block` | bool | `false` | `true` makes the logging reminder a blocking `Stop` instead of advisory. |
| `log_check_dated_entry` | bool | `true` | The `Stop` hook requires an entry heading carrying **today's date**. `false` falls back to "the file changed at all", which any stray byte satisfies. |
| `log_check_value` | bool | `true` | Today's entry must also carry a `- Value: <warranted\|overkill\|insufficient\|inconclusive>` verdict. `false` requires only the dated entry. |

## Generic commands

The core keeps these engine-generic verbs (bus `action` strings, underscored):

```
ping, screenshot, scene_tree, validate_scene, validate_all, get_state,
set_state, run_method, curve, performance, quit, input_press, input_release,
input_tap, input_clear, input_actions, input_sequence, input_key, input_state,
set_game_speed, wait_frames, step_time, clear_nodes, validate_ui,
get_ui_snapshot, get_node_bounds, save_ui_baseline, ui_snapshot_diff,
list_commands, touch_press, touch_release, touch_drag, touch_clear, touch_list,
set_feature, tilemap_cells, tilemap_region, scripts_seen, canvas_scale,
set_resolution, harness_version, find_nodes, press, raycast, sample_pixels,
reachable_ui
```

Notable behaviors:

- **`clear_nodes`** is a generic replacement for game-specific "clear" verbs. It
  accepts one selector — `{"group": <name>}`, `{"method": <method_name>}`, or
  `{"class": <ClassName>}` — and frees matching descendants of the current
  scene, returning `{"count": n, "via": ..., "skipped": [...]}`. With **no selector** it
  returns `success: false` with a helpful message; it never blindly frees the tree.
  `{"via_method": <name>, "via_args": [...]}` calls that method on each match instead of
  `queue_free()`, because `queue_free()` is the wrong removal for any node whose removal
  has game meaning: a freed enemy drops nothing, pays no xp, never increments the run
  counter and never fires the `died` connection a boss is listening on — a test built on
  it proves the nodes are gone and nothing else. Matches that lack the method are listed
  in `data.skipped` and make the command fail rather than being quietly `queue_free`d.
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
- **`scene_tree`** serializes each node as `{name, type, path, script, scene_file}`
  plus type-specific extras and `children`. `script` is the node's `res://` script path
  (`""` when it has none or has a built-in one) and `scene_file` is set on the root of
  an instanced scene. Both keys are **always present, possibly empty** — a missing key
  and "no script" must not look alike. They are what make `/verify`'s reach measurement
  a set intersection against the diff instead of a self-assessment, and they also save
  a round of guessing when mapping a changed `.gd` to the node path that runs it.
  `{"root": <path>}` starts the walk at any node instead of the current scene — a
  whole-scene snapshot hits the depth limit before it reaches a deep UI subtree, so the
  buttons a panel had just built could not be enumerated at all. `{"properties": [...]}`
  reports the named properties on every serialized node; `find_nodes` usually answers
  the same question more directly.
- **`find_nodes`** matches by `class` / `group` / `method` plus `where` (a
  property → expected-value map, dotted paths allowed) and returns
  `{nodes: [{path, name, type, properties}], count, truncated}`. It is `clear_nodes`'
  selector pointed at reading instead of freeing. Identifying the Elite among an
  `EnemySpawner`'s `@CharacterBody2D@385`, `@CharacterBody2D@388`, … children otherwise
  cost one `get_state` round trip per child — and by the time the loop finished, the
  engine had rotated the auto-names, so a path that answered 20 seconds earlier 404'd.
  A node missing a `where` property is a **non-match, never an error**: the predicate is
  meant to run across a heterogeneous subtree. `properties` (extra keys to report),
  `root` and `limit` (default 200) narrow the answer.
- **`get_state`** takes an optional `properties` array (the CLI's repeatable
  `--property`). Names that don't exist come back in `data["missing"]` rather than
  being silently dropped. `data["transform"]` is **always** present — see the sharp
  edge below for why the property dump alone is not trustworthy for layout. A **dotted**
  name walks into Resources and Dictionaries: `texture.region`, `slot_data.item.name`.
  Plain `--property texture` answers `<AtlasTexture#-92233719…>` — an object id — while
  *which picture* lives one hop down, and there is no node path for a sub-resource, so
  every Resource-shaped question used to need a bespoke project verb. A hop off a plain
  value fails into `missing` with a reason naming the segment that ran out, so it can
  never be read as "the value is null".
- **Node paths round-trip.** Godot auto-names an unnamed node `@Label@249`, `scene_tree`
  prints exactly that, and feeding the printed path back used to answer `Node not found`
  — a node the harness had just listed was unaddressable. `_resolve_node` now falls back
  to a literal per-segment descent, so any path the harness printed resolves.
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
- **The bridge keeps polling while the tree is paused.** The DevTools autoload runs
  `PROCESS_MODE_ALWAYS`, so pause menus, settings screens, death screens and
  level-complete screens are reachable — they were not before 0.12.0, and they are the
  UI most worth verifying. `ping` reports `tree is PAUSED` so a reply is never mistaken
  for an unpaused game. Before this, pausing produced *"that process is STILL ALIVE, so
  it is running but not polling THIS directory"*, which reads as a `--userdata` problem
  and cost the project that hit it a debugging cycle on the wrong thing.
- **The owner file carries `last_poll_unix`, and that is the liveness test.** A pid is a
  proxy and it failed both ways in the field: Windows recycled a dead Godot's pid onto
  an unrelated process, so `launch` refused to start against a bus nobody owned; and a
  live-but-paused owner was indistinguishable from a healthy one. A heartbeat is the
  fact itself — `launch` now blocks only on an owner that is alive *and* polling, and
  the messages say which.
- **`validate_ui` splits findings `NEW` vs `PRE-EXISTING`** against
  `user://ui_findings_baseline.json`, and only NEW ones fail the check —
  `--baseline-write` accepts the current set, `--no-baseline` ignores it. Keyed on
  (rule, node path), never on the message, which carries rects and alphas that move
  every frame. This exists because two projects independently hit a finding that was
  *correct and permanent*: a popup resting at alpha 0 between pops, and a diegetic HUD
  whose screen position is wherever the player happens to be standing. Both had to
  ignore `validate-ui` wholesale, which is the same as not having it.
- **`run_tests.gd` steps autoloads into readiness before discovery.** In `--script`
  mode Godot parents autoloads to `root` but does not step the tree, so `_ready()` has
  not run: an autoload that builds its data there answers **every test** with an empty
  collection, while lint reports every script compiled clean. Worse, the runner awaits
  inside tests, so the first test that happened to await a frame flushed the
  notifications and every test after it saw different data than every test before —
  suite behaviour depended on test order. One awaited frame up front makes it
  deterministic; `Autoloads: N of M ready` is the receipt.
- **A test that executes none of its own assertions is `[VACUOUS]`, not a pass.** The
  companion failure to the one above: a test that loops over a collection and asserts
  inside the loop is satisfied by an *empty* collection. Three such tests passed
  against an empty autoload in the project that found this. The runner counts
  `_T.assert_*` calls per method and only makes the call when the method's own source
  contains one, so a project that hand-rolls its failure strings is never accused.
- **`validate_ui`** flags `ui_outside_safe_area` when `safe_area_inset` is configured
  — for overlays (a CRT shader, a notch, a rounded corner) that eat the viewport edges
  without any validator knowing. The check is skipped entirely when the inset is
  all-zero, so it adds no findings to an existing project.
- **`run_method` and `set_state` coerce JSON arguments to the declared types.** JSON
  cannot carry a `Vector2`, so a `func take_vec(v: Vector2)` used to be uncallable over
  the bus. `[x, y]` / `{"x": .., "y": ..}` / `"x,y"` / `"(x,y)"` (and 3-component /
  `r,g,b,a` forms) become `Vector2`/`Vector2i`/`Vector3`/`Vector3i`/`Color` per the
  declared parameter type (from `get_method_list()`) or the property's current type;
  int/float/bool/String conversions are handled too. An impossible coercion **fails the
  command** naming both types *and the forms it would have accepted* — the method is
  never called with a silently-wrong argument. `run_method` also retries a bare node
  path under `/root` and echoes the path it used in `data.node_path`. `set_state`
  **reads the property back** after the write and fails with "set had no effect"
  (reporting `written` vs `read_back`) when a typo'd property or a clamping setter
  swallowed the value.
- **`run_method` reports `returned_null` and `declared_return`.** A `-> void` that ran
  perfectly and a `-> int` that aborted on a runtime error both come back as
  `result: null`, and GDScript hands the bridge no exception to tell them apart. What
  *is* knowable is what the method declared, so the reply says it: `declared_return` is
  a `type_string()` — `"Nil"` for a `-> void` or an untyped func, `""` when the method
  is absent from `get_method_list()` and nothing can be claimed.
- **`curve`** calls a pure method over an integer range and returns the series:
  `{node_path, method, from, to, step, args, arg_index}` in, `{points: [{input, value}],
  min, max, sum}` out (`sum`/`min`/`max` are `null` for a non-numeric series).
  Calibrating a difficulty ramp — `size_for_day`, `cost_for_parcel`, `xp_for_level`
  across 20+ days — otherwise meant hand-evaluating the formula in prose arithmetic
  every time anyone touched the constants; the game already knows those numbers and
  nothing could ask it for more than one at a time. `arg_index` chooses which parameter
  the swept value fills, so a method whose day argument is second is still sweepable.
- **`press`** emits `pressed` on the nearest `BaseButton` at, or one level under, a node
  path — a container path is what `scene_tree` hands you. Panels used to be verified one
  layer below what ships: the callables (`_on_strike`) were driven through `run_method`,
  which proves the *actions* and not the *buttons*, and a mis-wired `pressed.connect`
  shipped green. A **disabled** button is refused rather than pressed, because a real
  press would do nothing either and emitting anyway manufactures a state no player can
  reach. `{"toggle": bool}` sets `button_pressed` first on a `toggle_mode` button. Data:
  `node_path, type, disabled, button_pressed`.
- **`raycast`** casts through the 2D physics space: `{from, to, mask, areas, exclude}`
  in, `{clear, collider, collider_class, position, normal, mask, mask_names}` out. The
  harness could say what tiles are where and where a node is, but nothing could say what
  a given `collision_mask` would actually *hit*, which is the only form the question
  takes once a project has more than one physics layer — so every project wrote its own
  `los_probe`. `mask_names` resolves the mask against the project's own
  `layer_names/2d_physics/*` (unnamed bits report as `layer_N`, so no bit is silently
  dropped) because the whole bug class here is a number nobody can read. See the sharp
  edge on rays that start inside a shape.
- **`sample_pixels`** summarizes the rendered frame over a rect: `{rect, pixels, mean,
  brightest, darkest, dominant, dominant_share}`, `dominant` being the most common colour
  after quantising to 5 bits per channel. Asserting "this sprite renders blue" previously
  meant an inline zlib/PNG reader — ~30 lines of scanline defiltering that every visual
  assertion would have to carry. This is the same capture path `screenshot` uses,
  summarized instead of saved.
- **`reachable_ui`** returns every Control a finger or cursor could actually hit this
  frame: `{controls: [{path, type, text, rect, on_screen, blocked_by, kind}], count,
  reachable, viewport}`. A Control qualifies when it is effectively visible, has a
  non-zero on-screen rect, does not ignore the mouse, and is either a `BaseButton` or
  carries a `gui_input` connection. `blocked_by` names a **later** sibling whose rect
  covers this one's centre and which stops input — the full-rect `MOUSE_FILTER_STOP`
  overlay that silently eats a button. Unreachable controls are **listed with a reason**,
  not omitted, so the answer is diffable. `validate_ui` cannot cover this: an unreachable
  panel is not a layout fault, so it correctly reported 0 issues for a feature that had a
  key binding, a desktop button, a passing suite and no way in on a phone. Diff this verb
  between `set-feature --touchscreen true` and `false` and that class of bug names itself.
- **`screenshot`** takes `region: [x,y,w,h]`, `hide: [node paths]` and
  `hide_group: [group names]`, and reports the applied `region` and the `hidden` paths.
  The crop and the hiding happen game-side inside one command, so a store capture is
  reproducible from the command line instead of being two `set_state` calls plus a
  separate PIL crop. Each node's **previous** visibility is remembered, so an
  already-hidden node is not "restored" into view, and visibility is restored on every
  failure path too — this verb cannot leave a HUD switched off.
- **`ping` reports `bus_dir` and `user_dir` separately**, always. When they differ the
  bus is isolated and saves/screenshots are not; when they match nothing is isolated.
  `launch --isolated` previously claimed an isolation it did not have and nothing could
  contradict it, so this is a read rather than an assumption.
- **`get_node_bounds` works on any `CanvasItem`**, not just a `Control`. It used to
  answer `Node is not a Control`, so every visual check on a game object meant
  rebuilding the camera transform by hand. For a `Control` the rect is
  `get_global_rect()`; for anything else it is `get_global_transform_with_canvas()` —
  the same transform the renderer uses, so it accounts for the camera, every ancestor's
  scale and the `CanvasLayer` — applied to whatever extent the node can report (a
  `Sprite2D`'s texture rect, a `CollisionShape2D`'s shape, a `TileMapLayer`'s used
  rect). `data.size_source` names which of those produced the size; see the sharp edge
  on `0x0`.
- **`canvas_scale` also reports `canvas_layer` and `canvas_layer_path`.** A
  `CanvasModulate` tints exactly one canvas, so "will that tint reach this node" is a
  question about `CanvasLayer` ancestry — previously answered by grepping the `.tscn`
  for every `type="CanvasLayer"` and hand-walking parents, since `scene_tree` reports
  `script` and `scene_file` and nothing about canvases. `0` / `""` mean the root
  viewport canvas, which is a real answer, not a missing one.
- **`input_key`** dispatches a raw `InputEventKey` by OS keycode name (`{"key": "E"}`,
  `"LEFT"`, `"SPACE"`; optional `count`, `hold_frames`) with both `keycode` and
  `physical_keycode` set — the only way to reach game code that reads key events
  directly instead of actions. The release always lands on a later frame than the
  press. **`input_tap`** now does the same (release on the next frame, or after
  `seconds`), replies only after the release, and reports `pressed_during` /
  `pressed_after` so a tap that never registered is visible.
- **`input_state`** returns `{action: {pressed, strength}}` for the named actions
  (`{"actions": [...]}`) or every non-`ui_` action — the polled state the game is
  actually seeing, not an inference from the last simulated event.
- **`step_time`** accepts `{"hold": "<action>"}`: the action is re-asserted pressed on
  every stepped frame and released at the end (`data.held_action`), collapsing the
  press/step/release three-round-trip dance.
- **`tilemap_cells`** returns a TileMap/TileMapLayer's used cells as
  `[{x, y, source_id, atlas}]` (optionally clipped to `rect: [x,y,w,h]`, capped at 2000
  with `truncated: true`), and **`tilemap_region`** flood-fills 4-neighbor connected
  components among cells matching an `atlas: [x,y]` (and optional `source_id`),
  returning `[{cells, bounds}]` sorted largest-first — structural map questions ("is
  this island one landmass?") as data instead of a screenshot guess.
- **`scripts_seen`** returns every distinct script `resource_path` that has entered the
  tree since launch (seeded from the existing tree, then `node_added`): session-long
  reach ground truth that survives nodes dying between snapshots. The count also rides
  on `ping` as `data.scripts_seen`.
- **`set_feature`** with `{"query": true}` reports the current flag values without
  writing anything.
- **Every reply carries `pid` and `start_unix`**, and the autoload writes
  `user://devtools_owner<-session>.json` (`{pid, start_unix, project, session}`) at
  startup after clearing stale bus files. The Python client compares the reply's pid
  against the owner file and raises `ForeignInstanceError` when another instance is
  answering on the bus; the "game not running" precheck message names who last claimed
  the bus.

Game-specific verbs from earlier FlexCoins-era builds (`spawn_coin`,
`spawn_coin_on_catcher`, `get_active_coins`, `set_upgrade_levels`,
`reset_session`, `get_catcher_state`, `validate_ui_interactive`, and the
`COIN_TYPE_MAP` constant) have been **removed from the core** — they now belong
in a project's `commands.gd` extension.

## Python CLI (`tools/devtools.py`)

Generic hyphenated subcommands mirror the bus verbs:

```
ping, screenshot, scene-tree, validate, validate-all, get-state, set-state,
run-method, curve, performance, quit, logs, harness-version, launch,
input <press|release|tap|clear|list|sequence|state>, key,
touch <press|release|drag|clear|list>, set-feature, step-time,
set-game-speed, wait-frames, clear-nodes, validate-ui, ui-snapshot,
node-bounds, save-ui-baseline, ui-snapshot-diff, tilemap-cells,
tilemap-region, scripts-seen, canvas-scale, set-resolution,
find-nodes, press, raycast, sample-pixels, reachable-ui, new-uid
```

`new-uid` is the one subcommand that never touches the bus — see below.

Notable flags:

- `canvas-scale --node PATH` — a CanvasItem's ACCUMULATED canvas-transform scale
  (what the player's eye sees after every ancestor multiplies through) plus the
  effective texture filter and which node (or project setting) supplied it. The
  answer to every "why is this sprite blurry / enormous" question in one read;
  `get-state` cannot see it because containers hide position/scale.
- `set-resolution --size W,H` — resize the game window and read back what was
  actually applied; a headless or tiling environment that clamps the resize is
  reported as a failure, not "Resized".

- `get-state --node PATH --property NAME` — repeatable. Without it a single `Label`
  read returns ~120 keys, which is why every assertion used to be piped through an
  ad-hoc filter. A name that doesn't exist is reported explicitly rather than silently
  omitted, so a typo can't look like a missing value. A **dotted** name walks into
  Resources and Dictionaries: `--property texture.region`,
  `--property slot_data.item.name`.
- `find-nodes [--class C] [--group G] [--method M] [--where NAME=VALUE] [--property NAME]
  [--root PATH] [--limit N]` — `--where` and `--property` are repeatable and accept
  dotted paths (`--where slot_data.item.name='Iron Bar'`). Use it instead of a
  `scene-tree` dump plus a `get-state` per child when the question is "which of these
  is the one".
- `press --node PATH [--toggle BOOL]` — emit `pressed` on the button at, or directly
  under, `PATH`.
- `raycast --from X,Y --to X,Y [--mask N] [--areas] [--exclude NODE]` — `--exclude` is
  repeatable; without `--mask` every layer is tested.
- `sample-pixels [--rect X,Y,W,H]` — mean / dominant colour over a screen rect
  (default: the whole viewport).
- `reachable-ui` — no flags. Prints every interactive Control with its rect, marking
  each `OFF-SCREEN` or `BLOCKED BY <path>` rather than dropping it. Run it once per
  device profile (`set-feature --touchscreen true|false`) and diff.
- `curve --node PATH --method NAME --from N --to N [--step N] [--args JSON]
  [--arg-index N]` — the series a pure method produces over an integer range.
- `scene-tree [--depth N] [--root PATH] [--property NAME]` — `--root` lists one subtree
  instead of the whole scene (a deep UI subtree otherwise truncates); `--property` is
  repeatable and reports that property on every node.
- `screenshot [--filename F] [--region X,Y,W,H] [--hide NODE] [--hide-group GROUP]` —
  `--hide` / `--hide-group` are repeatable; the crop and the hiding happen game-side in
  one command, and previous visibility is always restored.
- `run-method --node PATH --method NAME [--args JSON] [--json]` — `--json` prints the
  full reply envelope (pipeable, like `cmd`), which is where `returned_null` and
  `declared_return` are readable.
- `clear-nodes --group/--method/--class SELECTOR [--via-method NAME] [--via-args JSON]`
  — `--via-method` calls the game's own removal path instead of `queue_free()`.
- `new-uid [--count N] [--write PATH]` — emit a correctly-encoded `uid://` string with
  **no game, no editor and no import**: a pure reimplementation of
  `ResourceUID.id_to_text()` (base 34, `a`–`z` then `0`–`9`; verified against the engine
  on 4.6.1 for eight ids including two 63-bit ones), collision-checked against every
  `.uid` sidecar in the project. An agent that isn't allowed to run Godot otherwise
  hand-writes a sidecar nobody can validate — and lint's `UIDs: OK` is only reachable
  from a tree that already compiles, which a fan-out doesn't have until every agent has
  landed. `--write` refuses to overwrite an existing sidecar, because changing a uid
  breaks every reference to it.
- `performance --reset-baseline` — re-baseline the orphan count (see below). The reply
  also carries `time_scale`, `devtools_set_speed` (the last `set-game-speed` value this
  session, `null` if never) and `orphan_baseline_age_frames`, so a reading taken under
  a leftover speed override or a stale baseline says so.
- `--no-precheck` — global; skip the ~2 s "is the game alive" check and wait the full
  timeout.
- `--json` — global (before the subcommand, e.g. `--json ping`): print every bus reply
  as the raw JSON envelope instead of the formatted view.
- `key NAME [--count N] [--hold-frames N]` — raw keyboard event by OS keycode name
  (`input_key`).
- `input state [ACTION ...]` — polled pressed/strength per action (`input_state`).
- `step-time --seconds N [--hold ACTION]` — hold an action across the whole step.
- `tilemap-cells --node PATH [--layer N] [--rect X,Y,W,H]` and
  `tilemap-region --node PATH --atlas X,Y [--layer N] [--source-id N]` — tilemap
  contents and connected components as data.
- `scripts-seen [--json]` — the script census; with `--json` the full reply envelope is
  printed (what `tools/verify_ledger.py` consumes from a redirect).
- `launch [--godot PATH] [--isolated] [--no-mute] [--no-wait]
  [--allow-second-instance] [-- GODOT ARGS]` — start the game detached with
  stdout/stderr under `.devtools/` (never a pipe — an unread pipe stalls Godot on
  Windows). Binary from `--godot`, `$GODOT_BIN`, or the config's `godot_bin`. Prints
  the PID.
  - `--isolated` gives the instance a fresh session id **and its own bus directory**
    (passed as `-- --devtools-busdir <dir>`, which the autoload honours), then proves
    that bus answers a `ping` **before** printing the `--session … --userdata …`
    follow-up command — a bus that never answered prints the stderr tail and exits 1
    instead, because a follow-up command that would not work is worse than none. It
    does not move `user://`; the output labels that line `SHARED`, and the sharp edge
    below says why.
  - Everything after a bare `--` is forwarded to the Godot command line, so a run
    needing an engine flag no longer has to re-implement launching:
    `devtools.py launch --no-mute -- --write-movie out/frame.png --fixed-fps 30`.
  - `--no-mute` opts out of `--mute`: a `--write-movie` run records the audio bus, and
    a muted run captures silence.
  - `launch` **refuses to join a bus a live pid already owns** — two instances
    answering one bus is silent corruption — naming the pid and session in the error.
    A *dead* owner file is cleared rather than obeyed, since that is the normal
    post-crash state. `--allow-second-instance` overrides the refusal.
  - `--no-wait` returns as soon as the process is spawned, without proving the bus
    answers.
- `quit [--exit-code N] [--wait SECONDS]` — sends the quit and then **waits for the pid
  in the owner file to actually go** (default 10 s), exiting **1** on a survivor with
  the `taskkill`/`kill -9` line to run. `quit` was not reliably fatal: three separate
  times the old process was still alive after a relaunch (once at 1.4 GB), and the only
  symptom was verbs returning empty output while `ping` said `No response` — which
  reads as *no* game rather than as *two*.
- `list-commands --offline` — no running game: statically parse `register_command(`
  names from the installed core and the config's extension script, labeled
  generic/project (a text scan, not runtime truth).
- `set-feature --query` — read the current feature-flag values without writing.

Two subcommands make project verbs first-class without touching the CLI:

- `cmd <action> [--args JSON]` — sends `{action: <action>, args: <parsed json>}`
  verbatim, so any project-registered verb is reachable:

  ```bash
  python tools/devtools.py cmd spawn_enemy --args '{"count": 3}'
  ```

- `list-commands` — sends `{action: "list_commands"}` and prints the discovered
  verbs (generic + project).
- `harness-version` — prints the installed revision game-side and client-side. Exits 1
  if they disagree, or if the running build predates the verb entirely (which names the
  fix: re-run `/scaffold-godot-harness`). Use it to fill the `harness:` field when
  logging a gap. **It answers with a cold bridge too**: with no game running it reports
  this client's version and the addon's `HARNESS_VERSION` read off disk, and marks the
  *running* build `unknown` rather than failing. Every gaps-log entry is written after
  the session is over — exactly when the bridge is down — so failing the whole verb made
  the field it exists to fill unfillable at the only moment anyone fills it, and the
  value got copied from a neighbouring entry instead. A disk-level mismatch still
  exits 1.

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

python tools/devtools.py --session a ping   # DevTools is running (…, session: a)
python tools/devtools.py --session b ping
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
python tools/devtools.py --session a --userdata /tmp/run-a ping
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
| `--no-shaders` | Skip the shader compile pass. |

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

The **global class cache** check runs before the compile pass, on purpose. Every
top-level `class_name` in the scanned scripts is compared against
`res://.godot/global_script_class_cache.cfg`; a class the cache doesn't know is an
`ERROR` (`class_cache_stale`), and it prints *above* the compile results it explains.
One missing entry — the normal state after a rebase, a pull, or a branch switch —
cascades into `Could not find type "X" in the current scope` in files nobody touched;
one such run produced 135 bogus parse errors. The fix is one line, and lint now names
it: `godot --headless --path . --import`. No cache file at all is a *different*
condition (a fresh clone that has never been imported) and gets one advisory line
(`class_cache_missing`), not one finding per class.

The **string-literal reference** check (`string_ref_unresolved`) flags the name inside
`has_method("x")` / `has_signal("x")` / `emit_signal("x")` / `call("x")` /
`call_deferred("x")` / `connect("x", …)` when no `func x` and no `signal x` exists
anywhere in the project and `ClassDB` knows no such engine member. It is the
`--find-orphans` scan pointed the other way — references that declare nowhere — and it
is always on, because the entire failure mode is that nobody knew to look. It is
**advisory** and always will be; see the sharp edge below for what it structurally
cannot see.

The **shader compile** pass (`shader_compile_failed`, `shader_embedded_compile_failed`)
compiles every `.gdshader` under `scan_root` plus every `Shader` embedded in a `.tres`.
A broken shader is a runtime-only failure that nothing else here sees: the scene holding
it loads fine, lint was clean, tests were green, and the magenta fallback only appears
when that scene is on screen and someone is looking.

The detection is not the obvious one, because the obvious one does not work. `load()` on
an unparseable shader still returns a `Shader` object — the same trap as `load()` on an
unparseable script — so load success proves nothing. Instead a sentinel uniform is
appended to the source and assigned to a bare `Shader`. Assigning `Shader.code` runs the
real `ShaderLanguage` parser even under `--headless` (the dummy rendering driver still
reports `Shader compilation failed`), and `RenderingServer.get_shader_parameter_list()`
returns `[]` for a shader that did not compile — so the sentinel coming back is the pass
signal. No `SubViewport`, no node, and no render is involved. If the sentinel name
already occurs in the source it is suffixed until unique, so a shader cannot collide its
way to green.

`#include` of a `.gdshaderinc` resolves normally from raw source, so an error inside an
include is caught through its includers. The `.gdshaderinc` files themselves declare no
`shader_type` and cannot compile standalone: they are counted and reported as **skipped**,
never as passed. Shaders embedded in a `.tscn` are **not** covered — only `.gdshader`
files and `.tres` resources. The summary line always names its denominators
(`Shaders: 41 of 42 compiled OK (29 file, 13 embedded)`), because `Shaders: OK` over a
project the pass never found a shader in is the report lying about what it looked at.

### On `gdlint`

`gdlint` (from `gdtoolkit`) is **deliberately not wired into `/verify`**, and the reason
is a measurement rather than a preference. Run against a real 169-file project it
produced **1,420 findings**: `class-definitions-order` 579, `max-line-length` 398,
`trailing-whitespace` 206, naming rules 173 — about 96% pure style. The
correctness-adjacent remainder was 33 findings (2.3%), none of them a bug.

That is the "gate that cries wolf on install day gets ignored" shape, it would be this
harness's first non-stdlib Python dependency, and `name_check.py` plus `lint_project.gd`
already cover the correctness ground `gdlint` does not. A project that wants enforced
formatting should adopt `gdlint` directly with its own `gdlintrc` — that is a house-style
decision, and it does not belong in a shared pre-commit gate that every installing
project inherits.

### `tools/name_check.py` — the gate that never opens the project

Every other gate in this harness goes through `.godot/`, which is a single-writer
resource: `--import` rewrites the import cache, and the headless runners open the
project and take the same locks. So N agents on one checkout cannot validate at the
same time, and the honest policy has been *agents never run Godot* — which serialises
the slowest part of the work behind one agent. A fresh git worktree is the same problem
at its worst: never imported, so lint reports a thousand `Identifier "X" not declared`
errors and still exits `0`, and the test runner prints `[PASS]` for tests whose first
statement errored.

Nearly all of those thousand lines are a **name** that did not resolve, and names are
decidable from source text plus a table of what the engine provides. `name_check.py`
resolves them from three inputs, none of which is `.godot/`:

1. the `.gd` files (`class_name`, inner classes, `const`, `enum`, `func`, `signal`, and
   the `extends` graph that puts a base class's members in scope),
2. `project.godot`'s `[autoload]` section,
3. an **engine API index** distilled from `godot --dump-extension-api`.

The third is what makes it safe under parallelism. `--dump-extension-api` runs in an
empty temp directory with *no project at all* — it opens no `.godot/`, takes no lock,
and is deterministic for a given engine build. So it is dumped once per engine version
per machine (~6s, 6.7 MB reduced to ~130 KB gzipped), cached under the user's cache dir
where **every clone and worktree shares it**, and after that the checker launches
nothing:

```bash
python tools/name_check.py --refresh-api    # once per engine version, per machine
python tools/name_check.py                  # every run after that, no engine
```

| Rule | Severity | What it means |
|---|---|---|
| `unknown_type` | error | An identifier in a type position (`var x: T`, `-> T`, `extends T`, `as T`, `is T`, `Array[T]`, `T.new()`) that matches no `class_name`, inner class, preloaded const, autoload, or engine class. |
| `duplicate_class_name` | error | Two files declare the same global class. Godot registers one; the others silently do not load. |
| `class_name_shadows_engine` | error | A `class_name` colliding with an engine class — Godot refuses to register it. |
| `missing_preload` | error | `preload("res://…")` pointing at a file that is not there. |
| `missing_extends_path` | error | `extends "res://…"` pointing at a file that is not there. |
| `missing_load` | warning | The same for `load()`, which can legitimately be conditional. |
| `unknown_member` | warning | `Known.member` where `Known` resolves and has no such member, walking both the project `extends` chain and the engine's. |
| `unknown_global_ref` | warning | A PascalCase identifier used as `Name.` / `Name(` that is declared nowhere. This is the `Identifier "Types" not declared` cascade, reported once per name per file instead of once per line. |
| `class_cache_stale` | warning | A `class_name` present in the source but absent from `.godot/global_script_class_cache.cfg` — read-only, so it stays safe with other agents running. Says the engine will disagree with your files until you import. |
| `string_ref_unresolved` | advisory | The name inside `has_method("x")` / `connect("x", …)` and friends, matching `lint_project.gd`'s rule of the same name. Advisory for the same structural reason. |

Exit codes follow the harness convention: `0` clean, `1` findings that count (errors,
plus warnings under `--strict`), `2` could not run. Flags: `-p/--project`, `--json`,
`--strict`, `--only <prefix>` (repeatable — filters the *report* while still scanning
the whole project, so cross-file names still resolve; this is what a fan-out agent
wants), `--no-strings`, `--baseline-write <p>` / `--baseline <p>` (same
`file|rule|subject` key format as `lint_project.gd`, so only NEW findings gate on a
project with a backlog), `--refresh-api`, `--force-refresh`, `--api <path>`,
`--require-api`, `--godot`.

**Two config keys, both empty by default.** `name_check_extra_types` whitelists types a
GDExtension registers at runtime, which the dump cannot see; `name_check_ignore`
exempts path prefixes. Leave both empty until the checker actually reports a false
positive — pre-filling them suppresses real findings.

**Sharp edges.** Without a cached index the engine-name checks are reported as
`SKIPPED`, *not* passed; `--require-api` turns that into an exit `2`. Member checks
stand down entirely for a name that is a local value binding, since its type is usually
unannotated and checking it against a same-named global would flag correct code.
`unknown_global_ref` deliberately ignores lowercase and `SCREAMING_CASE` identifiers: a
lowercase name could be an inherited property and a `SCREAMING_CASE` one an inherited
constant, and a gate that cries wolf gets switched off, which is worse than the gap.
And this is not a compiler — it does not type-check, evaluate, or see a name built at
runtime.

### `tools/import_check.py`

`godot --headless --path . --import` exits `0` whether or not the scripts it just
re-scanned compile. A real run:

```
import exit=0
SCRIPT ERROR: Parse Error: Cannot infer the type of "walk" variable
   at: GDScript::reload (res://player/player.gd:446)
ERROR: Failed to load script "res://items/items.gd" with error "Parse error".
```

"The class cache was regenerated" and "the project still parses" are the same exit
code, so a broken game reports as a clean import — and the tool you ran *to fix* a
`class_name` cascade is the one that told you it had succeeded. `import_check.py` runs
the same import, captures stdout and stderr to `.devtools/import.log`, scans it for the
signals Godot only prints on a real failure (`SCRIPT ERROR`, `Parse Error`,
`Failed to load script`, `Compilation failed` — literal, case-insensitive substrings,
each taken from captured output), and **quotes the findings back rather than counting
them**. Exit codes follow the harness convention: `0` clean, `1` the import ran and the
output contains parse/load errors, `2` couldn't run at all (no binary, no
`project.godot`, no captured output, a crash, or a timeout). Binary resolution matches
`devtools.py launch`: `--godot` → `$GODOT_BIN` → the config's `godot_bin`. Flags:
`-p/--project`, `--godot`, `--json`.

### The run ledger (`tools/verify_ledger.py`)

Phase 5 appends one row per `/verify` run to `.devtools/verify-runs.jsonl`, including
the runs where nothing went wrong. It exists because `log-devtools.md` has no
denominator: thirty gap entries say the harness was in the way thirty times, not
whether that was out of forty runs or four hundred, and only the ratio answers "is this
thing earning its keep?"

```bash
python tools/verify_ledger.py reach  --scene-tree tree.json   # dry run, writes nothing
python tools/verify_ledger.py record --scene-tree tree.json --run run.json
python tools/verify_ledger.py stats
```

`record` derives everything it can — timestamp, sha, branch, changed files — and takes
only runner exit codes, Phase 4 check results, and duration from the caller. The split
is deliberate: a run can misreport its own checks, but not whether it touched the diff.

**Reach is the field that matters.** `scene-tree` reports each node's `script` and
`scene_file` (0.6.0+), so reach is the intersection of a snapshot with `git diff` rather
than a self-assessment by the thing being measured. `--scene-tree` is repeatable and the
union is taken, because a node spawned mid-test is absent from an early capture and one
freed by the last test is absent from a late one.

A green run that never loaded the changed file is the failure this whole harness exists
to prevent, and a pass/fail summary cannot see it — both cases print "all checks
passed". `stats` breaks reach out per harness version, which is what tells you whether a
release improved anything or just felt like it. Files reach cannot speak to (`.cfg`,
shaders, `project.godot`) are recorded as `not_applicable` rather than counted as
misses; runs with no snapshot record reach as `null`, never as zero.

Two things that were being scored as misses no longer are — a metric that reports a file
as unreached when it demonstrably ran doesn't merely lose accuracy, it teaches its
readers to discount the number:

- **Scripts under `test_dir`** ran in Phase 1 and are structurally incapable of appearing
  in a scene-tree snapshot of a game session. Counting them in the denominator meant
  writing a test alongside a fix permanently capped the ratio below 100% — precisely the
  wrong incentive. They now join `not_applicable` (sub-list `test_scripts`). Excused, not
  credited: the ledger cannot see Phase 1's results, so it makes no claim they passed.
- **Scripts under `reach_headless_dirs`** (default `["tools/"]`) run only as
  `godot --headless --script res://tools/x.gd`. No node ever carries them as its
  `script`, so no amount of running them can register — `lint_project.gd` and
  `run_tests.gd` were being charged as misses by the very runs that had just executed
  them. Same bucket (`headless_tools`), same terms: excused, not credited. `addons/` is
  deliberately excluded from this rule — `dev_tools.gd` is the autoload and already
  resolves through `reached_implicit`.
- **Scripts credited by `reach_aliases`** land in `reached_alias`, with the voucher
  recorded in `reached_alias_via` and printed inline (`+1 by alias: X via Y`) so the
  claim is auditable rather than an anonymous bump. Never folded into `reached` — an
  alias is a project's declaration, not an observation, and the whole value of the field
  is that it doesn't blur the two. Aliases don't chain: a voucher that was itself only
  alias-credited credits nothing.

Each row also carries the run's `value` verdict, its `expected` prediction, and its
`cheaper_alternative` — the countable form of the log's `Value:` block, so "how often was
this overkill?" is a query rather than a reading exercise. `reach` exists as a separate
dry-run subcommand because the verdict depends on reach: a run that never loaded the
changed file is `insufficient` however well its checks went, and that has to be knowable
*before* the row is written.

**`found` is what the run caught** (0.10.0+), and it exists because every other field
describes the run's *end state*. A defect found four minutes in and fixed before Phase 5
leaves no trace: the checks are written green, the runners re-run clean, and the row is
identical to one where nothing was ever wrong. The first 52 rows recorded in anger showed
exactly that — 319 Phase 4 checks with not one `fail`, zero new lint findings, zero
failing tests, and 98% of runs grading themselves `warranted` on the strength of catches
that survived only as prose. Each entry is `{what, phase, static_would_have_caught}`, and
the three states are distinct on purpose: a non-empty list, `[]` meaning *this run found
nothing*, and `null` meaning unrecorded — which every pre-0.10.0 row is, permanently.
`stats` excludes those from the rate rather than scoring them as zeros.

That gives `value` its second mechanical gate. `record` already downgraded a `warranted`
whose changed files were never loaded to `insufficient`; a `warranted` with `found: []`
now becomes **`overkill`**, because confirming what was already known is the definition.
Both downgrades print on stderr. The point is that the one field grading the tool's own
usefulness is no longer graded solely by the tool.

Two related disciplines live in `/verify` rather than here, because no script can enforce
them: a Phase 4 check records its **first** observation with `fixed_in_run: true` rather
than being rewritten green, and `found: []` is to be recorded honestly rather than padded
to protect a verdict. An `overkill` row is a useful row.

`stats` reports the value mix, the time spent on runs judged overkill, the recent
`cheaper_alternative` lines, and the share of runs that caught anything — broken out by
whether lint and tests would have caught it too, which is the number that justifies
running a game at all. It also calls out a long stretch with no `overkill`, and a pile of
`warranted` rows with no `found` recorded, because both are what a log that flatters the
tool looks like.

Commit the ledger. Its value is entirely in being long.

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
and treats a zero-selection as "nothing was verified". So do two neighbouring cases:
discovering **no test scripts at all**, and discovering scripts that hold **no `test_*`
methods between them** — both exit `2`, because "the suite is empty" and "the suite
passed" are the same sentence otherwise.

`Selected: N of M discovered` is printed on **every** run, not only when a selector is in
play (it reads `(no selector)` then). A line that only appears under a filter is a line
nobody learns to read, and the number it carries is the one that distinguishes a real
pass from an empty one.

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
- **`launch --isolated` isolates the bus, and only the bus.** Godot resolves `user://`
  inside the engine and honours no flag for it, so screenshots, save files and UI
  baselines are still shared with every other instance. The previous version printed a
  temp `userdata:` path as though it had moved them; nothing ever wrote there, and the
  follow-up command it printed failed with `game not running`. The claim is now exactly
  as large as the behaviour, and `ping` reports `bus_dir` and `user_dir` separately so
  the difference is checkable rather than trusted. For real isolation give each instance
  its own userdata directory as well (see *Parallel verification* above).
- **`run_method`'s `returned_null` cannot tell "returned null" from "aborted".**
  GDScript raises nothing the bridge can catch, so a `-> void` that ran and a `-> int`
  that hit a runtime error are indistinguishable from the reply alone.
  `declared_return` says what the method *declares*, which is as far as the engine
  allows; `[ERR]` / `[SCRIPT ERROR]` on the game's stderr is still the only evidence of
  the abort.
- **A ray that starts inside a collision shape reports nothing.** That is the engine's
  behaviour and `raycast` inherits it — its `clear` message says so out loud — so five
  bisecting probes can all come back `clear` with a wall sitting between them. Start the
  ray outside the geometry you are asking about.
- **`node-bounds` on a non-`Control` derives the extent, and `0x0` means "unknown".** A
  `Control` reports its own `get_global_rect()`; anything else is the canvas transform
  applied to whatever the node can report about itself. When it can report nothing, the
  rect is a correct origin with a **zero size** — "where on screen is this" answered,
  "how big is it" not. That is not the same claim as "this node is zero-sized", and
  `data.size_source` is what tells them apart (`Control.get_global_rect`,
  `canvas transform x Sprite2D texture rect`, `… x origin only (this class reports no
  extent)`).
- **The unresolved-string-reference lint is advisory and structurally blind to common
  names.** It suppresses anything `ClassDB` knows on *any* engine class, so `open`,
  `close`, `start`, `stop`, `play` and `clear` can never be flagged — some engine class
  owns each of them, and suppressing engine names suppresses these too. It is also blind
  to names built by concatenation or held in variables (no finding, and no claim they
  were checked), to `cb.call("player")` on a `Callable` — where the literal is *data*,
  not a method name, and no text scan can tell the difference — and to GDExtension/C#
  members and built-in Variant methods, which live outside `ClassDB`. `call` is the
  honest reason the rule is advisory rather than an error: a noisy new ERROR would teach
  projects to stop reading lint output, which is worse than the gap.
- **`curve` is capped at 500 points and `step-time` at 60 game-seconds.** The bus serves
  one command at a time, so a typo'd range would wedge it for everyone. Both refuse with
  the cap named rather than truncating silently.
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
- **Reach proves loading, not exercising.** A changed script attached to a node that
  was in the tree counts as reached even if the specific function you edited never
  ran. It is a floor, not a coverage number — it can prove a run was blind, never that
  it was thorough. The stronger claim still comes from the Phase 4 assertions, which
  are self-reported. Reach also only speaks to `.gd` and `.tscn`; an autoload-only or
  `class_name`-only script that no node instantiates will read as unreached even when
  the tests genuinely drove it.
- **A skipped `/verify` leaves no trace.** The ledger records runs that happened, so
  if the messy changes are the ones that skip it, every rate is flattered and nothing
  in the file reveals the omission. Recording aborted runs (`verdict: "aborted"`)
  covers the case where verification was attempted and failed; it cannot cover the
  case where it was never attempted.
- **Single-client file bus.** The bridge is one command file / one result file
  with no locking. Concurrent clients on the **same** bus still race and clobber
  each other's commands; request ids make the resulting crossed reply an error rather
  than silent corruption, but they do not make it safe. Drive one bus from **one**
  client at a time, and give genuinely parallel instances separate buses with
  `--session` (above).
