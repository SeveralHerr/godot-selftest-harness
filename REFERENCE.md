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

   Every installed `.gd` also gets a `.uid` sidecar minted offline, using the same
   encoding `new-uid` implements — the scaffolder used to copy the script alone, and
   because the default `uid_check_ignore` covers exactly the paths it writes to, lint
   reported `UIDs: OK` over the hole (`moving-in:G-004`). An existing sidecar is
   **never** rewritten: a uid is an identity, and churning one breaks every scene that
   references the script. The sidecars are deliberately absent from
   `.harness_manifest.json` for the same reason — a recorded hash would let a later
   refresh judge the project's own identity "pristine" and clobber it. Projects
   declaring a Godot older than 4.4 are skipped, since uids arrive in 4.4.

> Note: if the Godot editor has the project open while you scaffold, close and
> reopen it so the editor picks up (and doesn't clobber) the edited
> `project.godot`.

### Installing without an LLM (`scaffold_install.py full`)

Since 0.20.0 the whole install is one command with no slash command in the loop
(gh#9 / H-047):

```bash
python <plugin>/tools/scaffold_install.py full --project <game> [--set key=value ...] \
    [--no-hook] [--hook-python python3]
```

It is the **one definition of "installed"** — files (with pristine-hash backups and
`.uid` minting) → `devtools_config.json` merge (with `main_scene` detected from
`run/main_scene`) → `devtools_ext/` (never overwrites `commands.gd`) → `test/` seed
(only if empty) → `CLAUDE.md` merge → `log-devtools.md` seed/refresh → `Stop` hook →
`DevTools` autoload, **appended last** in `[autoload]` so a project's own autoloads are
ready before the extension registers. `/scaffold-godot-harness` calls it in step 3 and
`check_templates.py` builds its scratch project through it, so the installer the check
exercises is the installer users get. Before it, the slash command (prose), the check
(its own copy) and every benchmark rig each defined "installed" for themselves — and the
autoload-ordering rule existed only as a sentence, which is how the first rig put
`DevTools` first. What it leaves to the slash command: `hud_layer_name` detection (pass
`--set`), the Godot binary (`config --set godot_bin=...`), and the import + lint smoke
check. Idempotent — a second run changes no byte, and refuses (rather than overwrites) a
malformed `.claude/settings.json`.

**It says what it IS before touching anything (0.33.0, gh#32).** The first line is
`[version] fresh install of X` / `already at X - this is a same-version refresh, not an
upgrade` / `upgrade Y -> X` / `DOWNGRADE Y -> X`, from the plugin root's `plugin.json`
against the project's `_scaffold_defaults.harness_version` (or the installed
`# harness-version:` stamp on a pre-record install). A downgrade is **refused with exit
2 and nothing written** unless `--allow-downgrade` is passed: a file matching
`harness_history.json` is pristine and overwritten without a `.bak`, so a backwards
refresh is silent by construction — and the skill loads from a plugin cache pinned at
one version, which is exactly how a stale cache meets a newer vendored harness. `full`
ends with `[full] harness: <transition>`, the one line the per-file output never gave.
`files` mode applies the same guard.

`config --set` no longer reverts a scaffold-owned key it was **not** passed (0.20.0,
gh#7): each call proposes the shipped default for every owned key, and a second call
used to reset the `godot_bin` the first had just detected back to `""`. Owned keys now
change only on an explicit `--set`, or when the harness version has moved on since the
record was written — and even then never from a real value to an empty default.

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
(`python tools/devtools.py harness-version --client`), so a gap logged before an upgrade is
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

Three properties fall out of this and all of them matter:

- **`id` makes a crossed reply detectable.** The client refuses a reply stamped for
  a different request rather than returning it. A response with no `id` key at all is
  accepted, so a newer client still works against an older game build.
- **Deleting the command file on pickup is the liveness signal.** If the file is still
  there ~2 s later, nothing is polling that directory — the game is dead, or the client
  is polling the wrong `user://`. That is why a dead game now fails in seconds rather
  than at the end of a 30–60 s timeout. **One exception**: a command that arrives while
  a handler is running is deliberately left on disk (below), so the client only calls
  the bus dead when there is *also* no handler in flight — no breadcrumb file, or an
  owner heartbeat older than 5 s.
- **The bridge is strictly one command at a time, by deferral rather than by luck**
  (0.16.0). `_process` polls without `await`, so an awaiting handler — `step_time`,
  `input_tap`, any project verb that yields — hands control straight back to the poll.
  A re-entrancy guard makes the next tick leave an arriving command *on disk* until the
  current handler returns, instead of dispatching a second handler into the same scene
  tree and racing both replies onto the one result file. The deferred command runs on
  the tick after the first one finishes; nothing is dropped. A handler that never
  returns (a runtime error inside an `await` never resumes) releases the guard after
  300 s with a loud log line naming the verb, so a wedged handler cannot silently make
  the bus deaf until restart.

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

# Elsewhere: a static-utility class with no node ever carrying its script self-reports
# into `scripts-seen`/`reach` from its own real entry points, not from here. DevTools
# is an autoload, so it is reachable by name from anywhere, static context included:
#   class_name Music extends RefCounted
#   static func crossfade(...) -> void:
#       DevTools.mark_script_reached("res://game/music.gd")
#       # ...the real crossfade logic...

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
| `uid_check_ignore` | Array | `["res://addons/", "res://tools/"]` | Path prefixes exempt from the missing-`.uid`-sidecar warning. The defaults cover the files the scaffolder copies in. The original reason — that it could not mint a valid `.uid` — no longer holds: `new-uid` mints them offline and the scaffolder now does so for every `.gd` it installs. The default stays because this same array also gates the class-cache, compile, shader and string-ref passes, so widening it runs all four across the addon. Narrow it per-pass, not here. |
| `name_check_extra_types` | Array | `[]` | Type names `tools/name_check.py` should accept without proof — the escape hatch for classes a GDExtension registers at runtime, which `--dump-extension-api` cannot see. Leave empty until the checker reports a false positive; every name added here is a name it will never again tell you is missing. |
| `name_check_ignore` | Array | `[]` | Path prefixes exempt from `name_check.py` findings. Generated or vendored-but-not-plugin code goes here. Vendored addons (an `addons/<name>/` holding a `plugin.cfg`) and `.gdignore` directories are already exempt without configuration. |
| `reach_aliases` | Object | `{}` | Credits a script reach can never observe to the observed script(s) that vouch for it: `{"world/tile_path_finder.gd": ["world/tile_scenes/bone_worker.gd"]}`. A `RefCounted` or `Resource` held as a plain field is never any node's script, so no amount of exercising it registers — and a permanently deflated reach number teaches readers to ignore the field. Credited files land in a **separate bucket** (`+N by alias`), never folded into `reached`: it is a claim your config makes, shown so a reader can disbelieve it. A voucher that was itself not reached credits nothing. |
| `reach_headless_dirs` | Array | `["tools/"]` | Directories whose scripts only ever run under `godot --headless --script`. They cannot be any node's `script`, so reach scores them `headless_tools` (a sub-list of `not_applicable`) instead of counting them as misses — otherwise `lint_project.gd` and `run_tests.gd` are charged as unreached by the runs that just executed them. Matching is on whole path segments, so `tools/` never swallows `toolsy/`. Set to `[]` if your `tools/` genuinely holds game code. `addons/` is not covered here by design: `dev_tools.gd` is the autoload and resolves through `reached_implicit`. |
| `fps_min` | int | `30` | Minimum acceptable FPS for `/verify` performance gate. |
| `min_control_gap` | number | `0` | Minimum pixels between two **non-overlapping** interactive Controls before `validate_ui` / `findings` report `controls_touching` (0.34.0, plant-tower-defense:G-046). `0` keeps the old behaviour: `Rect2.intersects` is false for boxes sharing an edge, so "not overlapping" passed for "touching", and a Back button flush under a row read as broken while every gate said clean. Set to your design's gutter (e.g. `8`); overlapping pairs stay `interactive_overlap` and are never double-reported. |
| `orphan_max` | int | `0` | Max tolerated **absolute** orphan nodes. Kept for compatibility only — `0` is unreachable in a real project (a fresh launch reports dozens). Gate on `orphan_growth_max` instead. |
| `orphan_growth_max` | int | `20` | Max tolerated growth in orphan nodes vs. the startup baseline. This is the number that means "this change leaks". |
| `safe_area_inset` | Object | `{left:0, top:0, right:0, bottom:0}` | Pixels trimmed off each viewport edge before `validate_ui` judges on-screen-ness. All-zero disables the check. |
| `main_scene` | String | `""` | Main scene path (detected from `run/main_scene`). |
| `entry_hook` | Object | `{ "node_path": "", "method": "" }` | Node/method the harness calls **automatically, once, shortly after launch** to reach a testable game state (0.30.0, gh#29 — before this it was accepted and read by nothing). Both keys required together; leave both `""` for "not configured". Outcome rides on every `ping` reply as `entry_hook_status`: `not_configured`, `fired`, or an error naming what went wrong (`node not found: …`, `no such method: …`) — never silent. `ping`'s CLI print shows it too. See the "entry_hook / entry_points" section below. |
| `entry_points` | Object | `{}` | Named alternates to `entry_hook`, each `{node_path, method, scene, args, match}` (`node_path`/`method` required, rest optional) — fired **on demand**, not automatically, via the `fire-entry-point NAME` verb. `match` is read only by `/verify`'s own workflow (`commands/verify.md`), not by this core: an agent compares it against the diff to pick a runtime path for a changed boss/shop/level script instead of only a code read. |
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
reachable_ui, findings, mouse_move, reload, first_frame, pause, unpause, look_at,
fire_entry_point
```

Notable behaviors:

- **A `--script` instance is passive on the bus** (0.22.0, moving-in:G-018/G-025).
  `lint_project.gd`, `run_tests.gd`, `eval.gd` and `capture.gd` all bring the
  autoload up; before 0.22.0 its `_ready()` deleted the "stale" owner/command/result
  files and wrote its own owner record — so a headless lint in one session deleted a
  colleague's in-flight command and then, once the runner exited, refused their next
  `launch` for 30 s with a dead pid. Now an instance started by `godot --script` (or
  `-s`) registers its handlers (a test may call them in-process) and never touches a
  bus file, never polls, never heartbeats. `is_bus_active()` says which kind you have.
- **`performance` measures FPS over a window** (0.22.0, moving-in:G-021 twice, G-020).
  `{"frames": N}` (default 30, `0` = the old one-shot read) samples wall-clock frame
  times and reports `fps` (the mean), `fps_min`, `fps_max`, `fps_samples`,
  `fps_window_sec`, `fps_instant`, and `fps_settling: true` when the two halves of the
  window disagree by more than 15% — which is what "the renderer has not settled after
  that change" looks like from inside one window. A single `get_frames_per_second()`
  read straight after a quality-preset switch produced `HIGH 110 / MEDIUM 50 / LOW 105`
  and nearly reported the slowest preset as the fastest. `findings` uses the window too.
- **`performance`'s `fps_max` carries a headless caveat** (H-060). A headless frame does
  no rendering work and waits on nothing, so `fps_max` reads five figures (58823 from a
  17µs frame) — real, but not a ceiling a player would ever see. `fps_max_trustworthy` /
  `fps_max_caveat` name it, the same convention as `geometry_trustworthy` /
  `geometry_caveat`; `fps` (mean) and `fps_min` are unaffected and remain the numbers a
  gate reads.
- **`performance` reports in-tree node growth** (0.22.0, moving-in:G-030). A node
  parented under a live node is never an orphan, so a UI that adds a layer per visit
  reports orphan growth `+0` forever. `node_baseline` / `node_growth` are sampled and
  reset alongside the orphan baseline; `{"by_type": true}` adds `node_types_delta`
  (`{class: ±N}` since the baseline) — "something accumulates" becomes "3 more
  CanvasLayers".
- **`set_game_speed` refuses a scale below 0.01** (0.22.0, moving-in:G-019). A time
  scale of 0 stops the game while the bus keeps answering well-formed stale values;
  `0.04` used to echo as `1.0 -> 0.0` from a 1-dp print. The reply names the floor;
  the client prints three decimals.
- **`validate_ui` / `findings` report `ui_escapes_panel`** (0.37.0,
  plant-tower-defense:G-048b): a visible Control whose CENTRE sits on a sibling
  `Panel`/`PanelContainer` but whose box hangs off it. A legend row hanging 34px past
  a pause card passed every gate, because the row's *parent* is the screen, not the
  card, and "inside its parent" was trivially true; `ui_text_trimmed` measured text
  against a `size` that had already been clamped. The centre rule keeps a neighbouring
  HUD element that merely brushes an edge out of the report, and a Control that
  *contains* the panel (a backdrop) is skipped. Siblings only — the parent case is
  `ui_overflow`. Stage 5 plants a Panel + escaping Label and an inside one; the stock
  fixture must report none.
- **`run_tests.py` prints `Engine errors: N ERROR: line(s) emitted`** (0.37.0, gh#35 /
  moving-in:G-058) — plain `ERROR:` lines (which is how `push_error` prints), counted
  and quoted, **never gated**: the reporter measured a clean baseline of exactly two
  legitimate ones, so zero is not the threshold; the number is there to be seen
  *moving*. `SCRIPT ERROR` / `USER SCRIPT ERROR` stay in the gated bucket. Also prints
  **`user:// writes: N file(s) changed by the suite`** (plant-tower-defense:G-048c) —
  four tests staged scores through a real `record_score()` → `_save()` and destroyed
  both high scores across two runs while every one restored the in-memory values.
  Advisory, but a suite that writes a file no test named a path for is driving
  production state.
- **`run_tests.gd` refuses an argument it does not know** (0.37.0,
  plant-tower-defense:G-049): `-- --select test_x` used to run the whole suite under
  `Selected: 491 of 491 (no selector)`; now exit 2, naming the flag and the three it
  takes.
- **Every Python client reconfigures stdout/stderr to UTF-8 with `errors="replace"`**
  (0.37.0, gh#34 / plant-tower-defense:G-048): on a default Windows console (`cp1252`)
  any verb echoing a `← Back` caption or an arrow key legend died with
  `UnicodeEncodeError`, and the traceback read as "this verb is broken on this node".
  A glyph the console cannot render prints `?` and the verb finishes.
- **`quit` names every `user://` file the run changed, created or deleted** (0.36.0,
  gh#33) — always on, no flag: `launch` records size+mtime of the top-level `user://`
  files, `quit` diffs and prints `user://: this run wrote the developer's REAL user
  data … changed: highscore.save`, or `no file changed`. The bridge's own files are
  excluded. This is the half of gh#33 that turns a silent mutation into a named one; a
  save a live pass altered had leaked into the *headless* suite two runs later and read
  as an unrelated failure. `--snapshot-userstate` (below) is the half that puts it back.
  The `--isolated` launch line now says the failure, not the fact.
- **`run_tests.py` exits 2 when one results file holds two runs** (0.39.0,
  plant-tower-defense:G-051b). `run_tests.gd` prints `Run: <id> pid <n> started` first
  and `Run: <id> pid <n> finished` after `Total:` (the id is also in `--json` output as
  `run_id`/`pid`); the wrapper refuses a capture with more than one distinct id — or,
  for a half-refreshed install whose runner predates the id, more than one `Total:`
  line — naming both pids: "the tallies above are a MIXTURE. Nothing was verified."
  A suite moved to the background and stopped left its Godot child alive, still
  appending to the `.devtools/tests.log` a later run had truncated; one file carried
  `519/519` and `516/519 | Failed: 3` with per-test times inflated 30x, and nothing
  said the denominators belonged to different runs.
- **`import_check.py` sweeps stranded `<asset>.import*.tmp` files** (0.39.0,
  plant-tower-defense:G-044, 7th sighting) on a crash retry and on the final failure
  path, naming what it removed. They are the engine's write-then-rename temporaries
  for `.import` metadata; a crash mid-write leaves them beside the assets, where they
  show up as untracked in `git status` and invite being committed. Never touches
  `.godot/`.
- **`pause` names what keeps running** (0.40.0, moving-in:G-061). A paused tree is
  not a frozen one: any node the project set to `PROCESS_MODE_ALWAYS` — and a HUD,
  where ALWAYS is mandatory or the pause menu cannot draw itself, is exactly what a
  caller pauses to measure — keeps processing. `RewardCard` read `visible: true` then
  `visible: false` one command later on a tree `ping` insisted was paused, and it read
  as a bus fault. The reply now says `Tree paused; N node(s) are PROCESS_MODE_ALWAYS
  (K set explicitly: /root/…) and keep processing on a paused tree - set-game-speed
  0.01 slows them where pause cannot`; `data.always_count` / `always_roots`. Stage 5
  plants one ALWAYS node and asserts count 1, its path, and the pointer.
- **`run_tests.gd` names the TEST that wrote `user://`** (0.40.0, gh#39 /
  plant-tower-defense:G-052): a stat + md5 of the files under `user://` per test
  method (bridge files excluded), printed after `Suite:` as
  `user:// writes by test: N write(s) to M file(s)` then `highscore.save <-
  test_quitting_a_run… (test_selftest.gd) [content changed]` — or `[rewritten
  identically]`, `[created]`, `[deleted]`. `--json` carries `user_writes`. The
  per-run line from `run_tests.py` said "highscore.save changed" and stopped there;
  recovering the test cost a hand-instrumented `_save()` and a 535-test run.
- **`_T.assert_ne(actual, unexpected, context)`** (0.40.0, gh#39 /
  plant-tower-defense:G-053) — "did the guard actually move this", reported with the
  value (`Expected anything but user://highscore.save, got exactly that`) instead of
  `assert_false(a == b)`'s `Expected false but got true` with both values interpolated
  by hand.
- **`verify_ledger.py reach` says it is file-level, and names the changed functions**
  (0.40.0, gh#38 / moving-in:G-060). `reached 1/1 changed file(s)` was true of a run in
  which the changed `_process()` body provably ran zero times (`_search_left: 12`,
  its initial value): the game LOADED the file. The worktree line now reads `reached
  1/1 changed file(s) [file-level: loaded, not lines-executed]`, and `reach` prints an
  advisory block — `changed function(s) in reached file(s) - N … unpack_ui.gd:
  _process, <top-level>` — from `git diff -U0` intersected with the enclosing `func`;
  it never gates and never claims execution. It leaves the reader one `get-state` /
  `run-method` from the answer instead of a 1/1 that reads stronger than it is.
- **`verb-usage`** (0.46.0, H-027) — reads the game's own `user://devtools_log.jsonl`
  (every `Executing: <verb>` line the bridge wrote, across every session that shared
  the log) and prints a count per verb, most-used first, `generic` / `project` told
  apart by the installed scripts' `register_command()` names, then `generic verbs never
  called here: N of M (…)`. Never opens the bus; `--log FILE` reads another log,
  `--json` for the raw counts. The question is the harness's own — which of the ~57
  generic verbs earn their place — and at ship time the two live projects answered:
  8,295 + 4,653 commands, `run_method` / `get_state` / `set_state` / `get_node_bounds`
  / `ping` / `scene_tree` / `find_nodes` the top seven on both, 22 of 57 generic verbs
  never called on one of them (`pause`, `contained_in`, `input_sequence`, `curve`, …).
  Nothing was trimmed on that evidence — every generic verb has a gap behind it — but
  the number is now one command away instead of a hand-written parser.
- **Findings baselines live in the project's `.devtools/`, not `user://`** (0.45.0,
  gh#48 / moving-in:G-066). `findings --baseline-write` and `validate-ui
  --baseline-write` write `res://.devtools/ui_findings_baseline.json` and
  `signal_findings_baseline.json` (created on demand; committable — the same
  argument as `verify-runs.jsonl`); reads prefer `.devtools/` and fall back to the
  legacy `user://` copy so every existing install still reads its own. The verdict of
  an adjudication now travels with the evidence: a fresh clone, a second developer or
  CI used to see eleven accepted findings as eleven new ones. `--baseline-dir DIR`
  (bus arg `baseline_dir`) pins another location; an exported build whose `res://` is
  read-only falls back to `user://` and the reply's `baseline_path` says which. Stage 5
  asserts both files land under the scratch project's `.devtools/`.
- **`upstream_gaps.py` reads a gap id from the Gap title, and `status:` from the
  wrapped paragraph** (0.45.0, gh#47.2 / moving-in:G-065). A repeat sighting written as
  `- Gap: **[G-025] …** - status: fixed (RECONCILED …) | **seen: 3**` used to mint an
  `auto-` id — one gap seen twice arrived as two new gaps, and a fixed sighting arrived
  as open. The pool output now says `(id read from the Gap title)` or `(minted: no id
  anywhere in the entry)`, so a correct dedupe and a guess no longer print the same.
- **`scaffold_install.py version --project .`, and the installer's version reads all
  three records** (0.45.0, gh#47.1). `version` prints the `[version]` transition, the
  version of the command body / plugin root itself, and the newest harness on the
  machine — exit 3 `STALE COMMAND BODY` when the loaded skill is behind the cache (every
  path in it interpolates the older root), exit 2 on a would-be downgrade. `full` /
  `files` have refused a downgrade since 0.33.0 (gh#32); the project's version is now
  the NEWEST of `_scaffold_defaults.harness_version`, `.harness_manifest.json` and the
  installed stamps, so the command's manual pre-flight and the installer cannot
  disagree. `/scaffold-godot-harness` step 1.4b runs `version` first.
- **`harness-version --client` names the project's own gaps fixed in releases it does
  not have** (0.44.0, gh#45). When a newer harness is on the machine the staleness
  block says `N release(s) behind` (from the newest root's `harness_history.json`) and
  then reads the project's `log-devtools.md` for open `G-NNN` ids and the newest
  templates for `<project>:G-NNN` credits (project = `config/name` and the directory
  name), printing `N gap(s) this project filed (G-…) are credited as fixed in releases
  it does not have` and, separately, the open ids already credited in the templates it
  *runs* (`the fix is installed; the log's status line is what is stale`). A version
  number was a nag on a verb almost nothing calls; `/verify` Phase 0 now runs it and
  quotes it. On the two live projects at ship time: plant 5 + 12, moving-in 1 + 24.
- **`verify_ledger.py record` says when a row was recorded AFTER the commit** (0.44.0,
  gh#44 / plant-tower-defense:G-057). Reach is the diff intersected with what the game
  loaded; after `git commit` the diff is empty by construction, and the row asserted
  the benign reading (`a real zero: every changed file is excused`). When the working
  tree holds no `.gd`/`.tscn` change but `HEAD~1..HEAD` touched some, `record` prints
  `This row was almost certainly recorded AFTER the commit, which destroys reach`,
  names the files, and writes `reach.post_commit_suspected: [files]` into the row so
  it can be told apart afterwards; `reach` prints the same `POST-COMMIT?` note.
- **`record` names unknown `run.json` keys with the nearest known one, and `record
  --schema` prints the key set** (0.44.0, gh#46 / plant-tower-defense:G-058). A row
  was written with `checks: []` from a `run.json` that carried the evidence under
  `phase4`, and the tool then warned that the evidence was missing — both halves
  present in one invocation and never meeting. Now: `run.json: ignoring unknown key
  'phase4' (did you mean 'checks'?) - it is NOT in the row`, via a fixed known-key set
  plus aliases (`phase4`/`evidence` → `checks`, `notes` → `expected`, `phases` →
  `runtime`) and `difflib`; a `checks[]` entry with `check` but no `name` is called
  out too.
- **`run_tests.gd` clamps physics catch-up to one tick per frame, and the per-test
  `user://` walk is top-level only** (0.43.0, gh#43 — a deterministic segfault on a real
  suite that 0.38.0 passed). Godot catches up on lost real time by running up to
  `max_physics_steps_per_frame` (default 8) physics ticks in one process frame, so
  ANY slow synchronous step in the runner — 0.40.0's recursive md5 walk of `user://`
  (178 files / 11 MB on the reporting project: `screenshots/`, `shader_cache/`,
  `logs/`), a slow `setup()`, a slow test before — changed how many `_physics_process`
  calls the next `instantiate_scene()`'s two settle frames delivered. A pest the test
  had parked on its last safe leg advanced, escaped, freed itself, and the typed array
  built after the `await` held a freed object. The runner, not the test, had changed.
  Now `Engine.max_physics_steps_per_frame = 1` for the whole run (two settle frames
  are two ticks, whatever the wall clock says; a test wanting N ticks awaits N
  `physics_frame`s as before), the walk reads only top-level `user://` files (the
  engine's subdirectories are never a save) with a 256 KB md5 cap, and stage 4 plants
  the mechanism: a 400 ms stall under 8 catch-up steps delivers >2 ticks in the settle
  frames; under the clamp ≤2. The crash itself did not reproduce on the maintainer's
  machine (554/554 twice, once with the reporter's real 11 MB `user://` copied in);
  the mechanism did, and it is the only thing in the 0.38.0→0.42.0 runner diff that
  can free a node.
- **`interactive_overlap` skips a control inert by both channels** (0.43.0, gh#42 /
  plant-tower-defense:G-055): a `Button` at `FOCUS_NONE` + `MOUSE_FILTER_IGNORE` can be
  reached neither by Tab nor by pointer, and making a covered layer inert is the
  standard fix for the hazard this check exists to find — so the check fired hardest
  at projects that had already fixed it, and the only way to quiet it was a baseline
  that would also hide a genuine overlap arriving later at the same pair. The finding
  text now says `both reachable (focusable or clickable)`. Stage 5 overlaps the A/B
  pair, makes B inert (no finding), restores one channel (finding again).
- **`upstream_gaps.py --triage` / `--mark-unverified ID…`** (0.42.0, H-069). `--triage`
  lists a log's open pooled gaps by project, oldest first, flagging `STALE` any logged
  against a harness more than `--older-than N` (default 15) minor releases behind this
  one; an unknown `harness:` prints `?` and is never flagged (unknown is not old);
  harness-native `H-NNN` are listed, never flagged. `--mark-unverified` rewrites the
  *named* pooled gaps to `status: unverified | stale-since: <version>` — a third state
  meaning "logged against templates since rewritten and not re-checked", which is not a
  claim of fixed; the id stays known to the dedupe so re-pooling does not re-append it.
  Explicit ids on purpose: an age-based bulk mark was built first and would have
  relabelled real, still-wanted requests. The first `--triage` run found three gaps
  whose fixing release had said "closes" in prose while the status line stayed `open`.
- **`upstream_gaps.py` carries `dup-of: gh#NN` and prints open gaps by source**
  (0.41.0, H-044 / H-028). A project that filed its gap upstream says so on its id line
  (`filed upstream: gh#40`); the pooled entry now names the issue it duplicates, so a
  release closes both at once instead of one twice. Every run ends with `open gaps in
  log-devtools.md by source: gather 40, harness 22, … (85 total)` — the concentration
  a single project carries is stated, not implied.
- **`_T.assert_margin(values, threshold, margin, recorded, context)`** (0.36.0,
  moving-in:G-057) — the threshold-margin gate a project had hand-rolled three times:
  sweep a corpus, one number per item; fail on any item newly within `margin` of the
  threshold, any recorded near-the-line value that moved, and any stale record. Returns
  every violation on one line. Stage 4 plants a passing recorded set and a new
  near-the-line item that must be refused.
- **`quit` says what it did with the snapshot on EVERY exit path, and the default
  patterns cover more than `*.save`** (0.41.0, moving-in:G-063 / plant-tower-defense:G-054
  2nd sighting). Two holes in 0.40.0's default: the survivor branch (`pid N is STILL
  ALIVE`, with or without `--kill`) exited without restoring and without a line — a
  session whose game lingered a few seconds kept the run's writes with the flag armed;
  and `quit` recommended `--snapshot-userstate` in the same reply that proved it would
  not have helped (`changed: settings.cfg` against patterns `*.save`). Now: the default
  globs are `*.save *.sav *.cfg *.dat *.json *.tres *.res *.bin` (the bridge's own
  files — owner, baselines, `findings_last.json` — are never in the set, or a
  mid-session `findings --baseline-write` would be undone on quit); `quit` prints one
  of `restored N …`, `no snapshot to restore (none was taken at launch)`, `snapshot …
  kept, NOT restored (--no-snapshot-userstate)`, or on a survivor `snapshot KEPT, not
  restored - pid(s) N still alive and may write user:// on exit. Once gone:
  restore-userstate` (restoring under a live game would be undone by its exit-time
  save); and the write report appends `NOT covered by the snapshot patterns (…) and so
  NOT restored: settings.cfg - relaunch with --snapshot-userstate … *.cfg to cover them`.
- **`launch` restores `user://*.save` on `quit` BY DEFAULT** (0.40.0, gh#40 /
  plant-tower-defense:G-054). Two projects on one day had a bridge session persist
  into the developer's real save through a verb that behaved correctly (`capture()`
  rebinding a key; `bank_score()`), and the damage surfaced twenty minutes later as
  five unrelated-looking headless failures. The launch line now says `N file(s) …
  will be RESTORED on quit`; **`--no-snapshot-userstate`** keeps a run's writes (a
  playtest whose save is the point — the copy is still taken and `restore-userstate`
  can still revert); `--snapshot-userstate GLOB…` widens what is restored beyond
  `*.save`. The `quit` report says **`rewritten identically`** for a file whose bytes
  match and whose mtime moved (gh#39) — a writer ran and the values happened to
  match, which is a different bug from a value overwritten.
- **A `user://` snapshot is taken on EVERY `launch`** (0.39.0, plant-tower-defense:G-050;
  0.39.0's `--snapshot-userstate` opt-in became 0.40.0's default). Before this, the report
  that a run had overwritten a developer's campaign best arrived at `quit`, when the
  previous value existed nowhere. Now `launch` copies `*.save` under
  `.devtools/userstate_snapshot/` (the previous launch's copy is kept as
  `userstate_snapshot_prev/`), names the files at risk on the launch line, and
  **`restore-userstate`** puts them back after the fact; `quit`'s "this run wrote …"
  line names the copy. Without the flag nothing is reverted automatically — a
  legitimate run keeps its save; the copy is a recovery point.
- **`launch --snapshot-userstate [GLOB ...]` / `quit` restore** (0.35.0,
  plant-tower-defense:G-047). `--isolated` isolates the bus and only the bus, so a
  live check that presses a key whose handler calls `_save()` writes the developer's
  real `user://highscore.save`, and putting it back was a discipline nothing enforced
  — a crash mid-check skipped it. The flag copies every `user://` file matching the
  globs (default `*.save`) under `.devtools/userstate_snapshot/` before the game
  starts; `quit` copies them back and **removes files the run created** under those
  globs, so "did not exist before" holds after; a snapshot left by a game that died is
  restored by the next `launch` before anything else. Needs no `user://` isolation.
- **`scene-tree` ends with `N node(s) in this subtree`** on stderr (0.35.0,
  moving-in:G-056) — each node prints a `name` and a `path` line, so `| grep -c` over
  the JSON reads one node as two, i.e. as exactly the duplicate a reader was testing
  for. The denominator is the house style; JSON on stdout stays parseable.
- **`import_check.py` retries a crashing `--import` while it still makes progress**
  (0.35.0, plant-tower-defense:G-044, fifth sighting) — the observed failure needed
  two retries and the cap was one. Now up to 4 attempts, each allowed only if the
  previous crash grew `.godot/imported` or moved the last `reimport | file` line; a
  crash that gained nothing twice running is reported, not retried forever.
- **`verify_ledger.py stats` says "N alias credit(s) across M distinct file(s)"**
  (0.39.0, moving-in:G-059) for the alias, implicit and base-class lines — the old
  "138 file(s) credited by reach_aliases" was five files credited 51+50+21+8+8 times,
  and cost a project a kanban entry, a todo and a re-audit before the noun was
  noticed. The comparison against the observed count stays (both cumulative).
- **`harness-version --client` never opens the bus** (0.34.0, moving-in:G-055) — the
  log-entry format wants the installed version on every turn, most of them with no
  game running, and the bus-first path printed a `game not running` warning before
  the answer every single time, training the reader to skip it. `--client` prints
  the client's and the installed addon's versions from disk, `Game: not asked`, and
  the machine-staleness line, exit 0 when they agree.
- **`run_tests.py` prints `Declared: N assertion call site(s) …; M executed`**
  (0.34.0, moving-in:G-054 / gh#27), advisory, suite level, skipped under
  `--filter`/`--file`. Counted with `coverage_check.py`'s word-bounded `_T.assert*(`
  pattern over comment/string-blanked source. It cannot know which sites a loop runs
  twice, so it is a number to read, not a gate — but the reporter measured
  written-vs-executed at 4/2, 2/1, 2/1 for three aborting methods against 2/2 for the
  one genuine pass, and that separation is what the line is for. On a real 248-test
  suite: `Declared: 2276 … 7962 executed (loops or helpers run some sites more than
  once)`.
- **`import_check.py` says what a crashed import left behind** (0.34.0,
  plant-tower-defense:G-044, fourth sighting): on a non-zero exit with no findings it
  reports how many finished artifacts `.godot/imported` gained across the run, how
  many `.tmp` files it holds, and the last `[ N% ] reimport | file` line — the asset
  the crash happened on. When it gained nothing, it names the legal way out: the
  import cache is keyed on `res://` paths, identical across worktrees of one project,
  so a sibling checkout's `.godot/imported`, `uid_cache.bin` and
  `scene_groups_cache.cfg` seed this one. The single automatic retry (0.28.0) stays;
  this covers the case it does not fix.
- **`set_state` rebuilds a JSON array as the property's own typed Array** (0.32.0,
  plant-tower-defense:G-019). Assigning a plain `Array` to an `Array[StringName]`
  (or any typed array) property is a silent no-op in GDScript; the read-back already
  caught that as `set had no effect`, but the write is what the caller wanted. The
  value is now reconstructed with the target's element type (a String becomes a
  StringName), reported as `coerced: true`, and an element that cannot convert fails
  the whole write with a count — never a partial apply.
- **`findings` / `validate_ui` keep the full records of the last NON-CLEAN run** at
  `user://findings_last.json` (0.32.0, plant-tower-defense:G-030) — `{verb, count,
  iso_time, process_frame, tree_paused, current_scene, records}` — and print the path
  whenever the count is non-zero. A finding that fires once on a transient frame used
  to leave nothing to investigate with: the verb re-run seconds later was a different
  frame and said `[OK]`, and the only record was a count in a line already truncated.
  A clean run writes nothing, so the file is always the most recent run that had
  something to say (its timestamp tells you which).
- **`harness-version` names the versions this MACHINE can offer** (0.32.0, H-064):
  the plugin Claude Code is running from (`$CLAUDE_PLUGIN_ROOT`), the plugin cache
  (`~/.claude/plugins/installed_plugins.json`) and the marketplace clone, and says
  when any of them is newer than the project's install. A project stays on the
  version it was scaffolded with until someone re-runs `/scaffold-godot-harness`, and
  nothing used to say a newer one was already sitting on disk — two real projects
  ran 0.21.0 and 0.25.0 through a day in which 0.26.0–0.31.0 shipped, and about half
  the gaps their logs pooled upstream had been fixed releases earlier. Every value is
  read from a file; nothing asks the network.
- **`pause` / `unpause` set `SceneTree.paused` directly** (0.25.0, gh#26) — the thing
  `set_game_speed`'s own refusal message names but nothing implemented until now. The
  bridge autoload runs `PROCESS_MODE_ALWAYS`, so it keeps answering on a paused tree
  (`ping`'s `paused` field already relies on this). Meant for catching a sub-second
  effect that races the bus round-trip — a fade, a hit-flash, a cooldown tween:
  trigger it, poll for the moment you want, `pause`, then take as long as you like
  over `sample-pixels`/`screenshot`/`node-bounds` without racing anything, `unpause`
  to resume. Idempotent either direction; the reply's `was_paused` says what state it
  found.
- **`entry_hook` / `entry_points` actually fire, and say so** (0.30.0, gh#29). Before
  this, `entry_hook` accepted a value, validated fine, and was read by nothing — a
  config key that *looks* configured is the harness's own worst failure mode applied
  to its own config, and the natural symptom (a launched session still sitting on a
  title screen) reads as a broken game, not an unwired setting. `entry_hook`
  (`{node_path, method}`, both required together) fires **automatically, once,
  shortly after launch** — polled rather than fired on a fixed frame delay, since
  autoloads run before the main scene is instantiated and the target usually does
  not exist yet at `_ready()`. Gives up after 10s and reports `node not found` if it
  never resolves, so a typo is an error, not permanent silence. Outcome rides on
  every `ping` reply as `entry_hook_status` (`not_configured` / `fired` / an error
  string) and `entry_hook_result` (the method's own return value); `ping`'s CLI
  print surfaces it too. `entry_points` (same shape plus optional `scene` and
  `args`) are named alternates reached **on demand** via `fire-entry-point NAME`,
  not fired automatically — `/verify` picks one whose `match` substrings hit the
  diff (see `commands/verify.md`), so a change to a boss/shop/level script gets a
  runtime path instead of only a code read. An entry with `scene` set switches to
  it first via `change_scene_to_file()` (polling for the target node the same way,
  since the new scene is deferred to the end of the frame) and reports
  `scene_changed: true`. Neither mechanism guards against being fired twice — that
  is the target method's own job, the same way a project verb should already guard
  a non-idempotent action, and the workaround this report shipped with
  (`"already in play, nothing to dismiss"`) is the pattern to copy.
- **`raycast` is 2D or 3D by arity** (0.22.0, moving-in:G-023): `[x,y]` queries the 2D
  space, `[x,y,z]` the 3D space (`layer_names/3d_physics`), `data.space` says which.
  A 2D query on a tree whose only colliders are `CollisionObject3D`s is **refused**
  naming `--from X,Y,Z` — before, it answered `clear` in a 3D project with the
  inside-a-shape caveat attached, which sent a session hunting a geometry bug that
  did not exist. Mixed arity is refused.
- **`validate_ui`'s baseline survives auto-name renumbering** (0.22.0,
  moving-in:G-031 twice). Keys normalise `@VBoxContainer@465` to `@VBoxContainer`;
  the counter is a per-process allocation order, and an unrelated commit inserting one
  sibling earlier in the scene renumbered every runtime-built row and re-presented 30
  accepted findings as NEW on a diff that touched no UI. Multiplicity is kept: three
  accepted rows under one key stay accepted, a fourth is NEW. Baselines written before
  0.22.0 are normalised on read and stay valid.
- **`mouse_move`** (0.22.0, moving-in:G-029) dispatches a real `InputEventMouseMotion`
  through `Input.parse_input_event`: `{"relative": [dx, dy], "steps": N, "position":
  [x, y], "buttons": mask}` — `steps` splits the delta into one event per frame, so a
  look handler that clamps per event sees them individually. Data: `relative, steps,
  position, mouse_mode`. Mouse-look was the one input a first-person game could not be
  tested with; `run-method _unhandled_input` with a JSON event was silently a no-op.
  When `mouse_mode` is `captured`, your physical mouse is a second input source on the
  same camera between commands (moving-in:G-024) — release it first if a read must be
  stable.
- **`reload`** (0.22.0, moving-in:G-033) re-reads `{"path": "res://…"}` from disk into
  the running game with `CACHE_MODE_REPLACE`. A text resource is re-parsed into the
  cached instance; a shader/binary/texture loader builds a new object, so the verb
  copies its stored properties onto the cached one — either way every node already
  holding that resource sees the edit without a relaunch. Data: `path,
  resource_class, was_cached, holders_updated, properties_copied`. `was_cached: false`
  means nothing held it and nothing changed on screen.
- **`ping` and the owner file carry `project_path`** (0.22.0, plant-tower-defense:G-018)
  — where the answering game's `res://` is on disk. A sibling git worktree has the same
  project name, so the same `user://` and the same bus; its game overwrites the owner
  record with its own pid, after which every reply's pid matches the owner and the
  survivor check cannot see it — errors arrive as `no Game in the tree`, i.e. as bugs
  in your own scene, on a game that is not yours. The client compares the owner's
  `project_path` to its `--path` before writing any command and raises
  `ForeignInstanceError` naming both checkouts; `launch` says the same in its refusal;
  `ping` prints the path and flags a mismatch.
- **`sample_pixels` states its frame** (0.22.0, moving-in:G-032): `origin: "top-left"`,
  `space: "srgb_0_1"`, `image_size`, `same_image_as_screenshot: true` — it crops the
  same root-viewport image with the same `Rect2i` that `screenshot --region` uses. If
  the two ever disagree, the reply now carries enough to say in which coordinate space.

- **`run.json` `tier` is a field of its own, and `/verify` has tier (f) Tooling-only**
  (0.50.0, plant-tower-defense:G-060 second sighting). A session wrote `tier` into every
  `run.json` and five releases read it nowhere (0.48.0 even aliased it to `runtime`,
  which was wrong — it is the triage decision, not runtime evidence); it is now stored
  on the row and `stats` prints `by Phase 0.5 tier: full N, headless-only M, …`, the
  number that says how often the full run is the right call. Tier (f): a project-owned
  `tools/*.py` checker that no GDScript imports fits none of (a)–(e) — no Godot phase can
  speak to it; run its own tests / self-check and record `tier: tooling`.
- **`sample-pixels --expect` / `--points`** (0.49.0, gh#49 / plant-tower-defense:G-059).
  The verb could describe a region and never assert a colour was in it: a 3.6 px pip
  inside a 5x5 box is a few percent and `dominant` will never name it however plainly
  it is drawn, so "did this cue get drawn" was a squint with extra steps that could not
  be written into a test. `--expect RRGGBB[,…] [--tolerance N]` (per 8-bit channel,
  default 8) counts the sampled pixels within tolerance of each colour game-side (the
  image is already in hand; nothing new crosses the bus) and the client exits 1 when a
  named colour appears zero times: `expected #ffc500 (tol 8): 0 px (0.0%) <- ABSENT`;
  `--points X,Y;X,Y` samples exactly those pixels (a cue is a point) and reports each
  one's colour. Data: `expected[{color,count,fraction,absent}]`, `absent`, `points`,
  `tolerance`. A game older than 0.49.0 answers without `expected` and the client exits
  2, never 0. Argument errors are refused by name *before* the framebuffer check, so
  the headless contract table proves the parsing; the counting was proved live,
  windowed, on a plant copy: `expected #192121: 11857 px (49.4%)`, `#ff00ff: 0 px <-
  ABSENT`, exit 1; two named points read back at 100%.
- **`run.json` `kind: "experiment"`, `/verify` tier (e)** (0.49.0, gh#50 /
  plant-tower-defense:G-060). Phase 0.5 classified a run by its diff, and a session
  whose diff is its *output* — a recipe learned by driving the live game — read as
  tier (a) "nothing to verify, no ledger row, overkill". A fifth row: **classify by
  what the run is for**; tier (e) records the row with `kind: experiment`, `record`
  implies `--no-reach` and stamps `reach_note`, the reach downgrade never applies, and
  the verdict is judged on what was established (`found`); `stats` counts experiments
  separately.
- **`record` prints its denominator every run, and `fixed-upstream` is a status**
  (0.48.0, plant-tower-defense:G-058 third sighting). `run.json: read K of N supplied
  key(s) (…); ignored: …; defaulted: verdict -> unknown` — `difflib` catches a key near
  a real one; the denominator catches the rest, including the silent `verdict: unknown`
  that turned a clean run into a wrong row on a project pinned at 0.38.0. Flat gate
  numbers (`lint_exit`, `tests_total`, `assertions`, …) alias to their nested homes.
  The project's log format gained `status: fixed-upstream: X.Y.Z` — fixed in a release
  the project does not run yet, still open *here* until the refresh (the state the
  reporter said the ledger had no word for); `upstream_gaps.py` skips it like `fixed`
  and reads a title id even inside backticks. `/verify` Phase 0 also runs the
  *plugin's* `devtools.py harness-version --client -p .`, because a project's own client
  can be too old to know how to compare.
- **`batch`** (0.47.0) runs several verbs in ONE round trip. Every bus call costs a
  command-file write, up to a 100 ms poll and a result-file read; `verb-usage` on the
  two live projects showed ~13k calls, most of them `get_state` / `set_state` /
  `run_method` / `get_node_bounds` in tight read-modify-read runs. `batch --json-items
  '[{"action":"set-state","args":{...}},{"action":"get-state","args":{...}}]'` (or
  `--file cmds.json`, or stdin) dispatches each item through the same registry —
  project verbs included, hyphens accepted — awaits it, and returns every item's full
  envelope in order: `data.results[{action, success, message, data}]`, `count`,
  `succeeded`, `failed` (indices), `stopped_at`. A failed item does not stop the batch
  unless `--stop-on-error`; the reply's own `success` means *all* succeeded and the
  client exits 1 otherwise, printing one line per item. `batch` and `quit` are refused
  inside a batch; the cap is 200 items. Stage 5 runs a five-item batch (a hyphenated
  verb, an unknown verb, a nested batch) and asserts the indices and the stop.
- **`findings`** runs every live check at once — `ui_layout`, `ui_reachable`,
  `signal_unconnected`, `performance`, `scene_validation` — and returns one flat
  findings list with no assertions from the project. `data`:
  `findings[{source, code, severity, path, message}]`, `counts`, `checks_run`,
  `checks_skipped[{check, reason}]`, `viewport`, `baseline_in_use`, `new_count`,
  `pre_existing_count`, and (0.40.0) `signal_baseline_in_use`, `signal_new_count`,
  `signal_pre_existing_count`, `signal_baseline_path`, `signal_baseline_written`.

  It reuses each verb's own implementation rather than re-deriving it, so it can
  never disagree with `validate_ui` / `reachable_ui` / `performance` /
  `validate_all` about the same scene, and a fix to one is a fix here too. It
  respects the `user://ui_findings_baseline.json` NEW/PRE split: pre-existing UI
  findings are excluded from the list and reported as a count, never silently
  dropped.

  `signal_unconnected` reports only signals a *script* declares
  (`Script.get_script_signal_list()`), never the engine built-ins every `Node`
  inherits — those are legitimately unconnected almost everywhere and would bury
  the real findings. **One finding per (script, signal), with a node count**
  (0.40.0, gh#41 / moving-in:G-062): a signal declared once on a script instanced
  24 times used to print 24 lines differing only by a node index — 57 lines for 11
  facts, and a report that was permanently red and permanently unread. The finding
  carries `script`, `signal`, `nodes` and up to 20 `paths`; the message ends
  `(24 node(s))`. **It has a baseline of its own**, `user://signal_findings_baseline.json`,
  keyed on the (script, signal) pair and not on a node path (a per-instance key would
  need 24 entries for one decision and re-present them all as NEW the day a box is
  added): `findings --baseline-write` accepts the current pairs — a signal left
  unconnected on purpose (an outward API, an emit the project documents leaving
  dangling) is accepted once — and only a NEW pair gates; `--no-baseline` re-reports
  everything. Stage 5 instances one emitter script three times and asserts one
  finding with `nodes=3`, then the accept / exclude / re-report round trip.

  **`checks_run` / `checks_skipped` are load-bearing.** A consolidated report is
  the easiest place in the whole system for a check to quietly vanish from, so it
  carries its own denominator: a check that could not run is named with a reason,
  and the client prints `N findings across K of M checks`. A check that ran and
  found nothing is `counts[id] = 0`, not an absent key — absent means it did not
  run, and the two must never read alike. Sub-conditions skipped inside a check
  that *did* run carry a dotted id (`performance.orphan_growth`) and do not count
  toward M, so the denominator stays arithmetically true.

  Exit codes: `0` clean, `1` gating findings, `2` could not run — including a
  reply missing a required `data` key, which is reported as unreadable rather
  than as a result.

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
  `root` and `limit` (default 200) narrow the answer. **`class` matches a script
  `class_name` too, subclasses included** (0.21.0, gh#15.2): the node's script and its
  base-script chain are compared by `get_global_name()`, so `--class Pest` finds the six
  live `Pest` nodes that report `type: Node2D`, and `--class Plant` finds every plant. A
  `class` that names neither an engine class nor a registered `class_name` **fails**
  (naming the project's script classes) rather than returning a clean zero — and when
  the selector itself matched nothing, the empty result says so instead of diagnosing a
  `where` predicate that never ran. `clear_nodes` shares the matcher and the refusal.
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
- **To observe a state with a lifetime shorter than a few bus round-trips — a confirm
  window, a combo timer, an i-frame, a hitstop — freeze it first with
  `set-game-speed 0` (clamped to `0.0`), then read it, then `set-game-speed 1.0`**
  (plant-tower-defense:G-022). A 4-second confirm window read as already-expired
  twice across two ~1s calls; freezing first is the standard technique, and it is the
  inverse of `step_time`: that one advances time deterministically, this one holds it
  still while you look.
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
- **A test that aborts mid-method, having already run a real assertion, is reported
  `[PASS]`** (0.27.0, gh#27 / moving-in:G-050, reported independently by two projects
  the same day) — a third failure in the same family, and the one `[VACUOUS]`
  structurally cannot catch, because `[VACUOUS]` only fires on *zero* assertions and
  an abort mid-method has usually already run some. Godot coerces the aborted
  coroutine's return value to the declared type's default — `""` for a `-> String`
  test — indistinguishable from a genuine pass by the return value alone; a real run
  measured `ALL TESTS PASSED` over a test whose body raised
  `SCRIPT ERROR: Invalid operands 'float' and 'Nil' in operator '+'`. GDScript cannot
  observe its own process's stderr after the fact, so no fix inside `run_tests.gd`
  itself is possible — see `tools/run_tests.py` below.
- **`validate_ui`** flags `ui_outside_safe_area` when `safe_area_inset` is configured
  — for overlays (a CRT shader, a notch, a rounded corner) that eat the viewport edges
  without any validator knowing. The check is skipped entirely when the inset is
  all-zero, so it adds no findings to an existing project.
- **`validate_ui`'s `ui_text_overflow` measures a Label per line** (0.21.0, gh#15.1).
  `get_string_size()` lays a string holding `
` out as one line, so a two-line banner
  that rendered perfectly inside its box measured 1052px against 896px and gated a run;
  the only workaround was a baseline entry that would also have hidden a *genuine*
  overflow of that Label forever. The widest single line is what the box has to hold;
  the message says `(widest of N lines)` when it applies. Autowrapping Labels are still
  skipped, as before. A Label with `clip_text` or a `text_overrun_behavior` is not
  spilling past its box, it is **trimming** the string — the player reads a cut readout
  and the fix is room or a shorter string — so that case is now `ui_text_trimmed`
  (plant:G-017); the two used to share one code and were triaged by eye. A baseline
  written before 0.21.0 that held such a Label under `ui_text_overflow` will show it as
  NEW once under the new code — re-run `--baseline-write` after checking it.
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
- **`raycast`** casts through the 2D physics space (or the 3D one — see above):
  `{from, to, mask, areas, exclude}`
  in, `{clear, collider, collider_class, position, normal, mask, mask_names, space}` out. The
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
  frame: `{controls: [{path, type, text, rect, on_screen, scroll_reachable,
  scroll_container, blocked_by, kind}], count, reachable, scroll_reachable, viewport}`.
  **A Control inside a `ScrollContainer`** (0.21.0, gh#16) is `on_screen` only where
  the container's own on-screen rect shows it — a row clipped by its container no
  longer reads as hittable — and a row past the fold that lies within the scrolled
  content's extent is `scroll_reachable`, which `findings` **counts and reports but
  never gates** (a shop list taller than the viewport used to fail `findings` forever
  with no baseline to accept it). A Control qualifies when it is effectively visible, has a
  non-zero on-screen rect, does not ignore the mouse, and is either a `BaseButton` or
  carries a `gui_input` connection. `blocked_by` names a **later** sibling whose rect
  covers this one's centre and which stops input — the full-rect `MOUSE_FILTER_STOP`
  overlay that silently eats a button. Unreachable controls are **listed with a reason**,
  not omitted, so the answer is diffable. `validate_ui` cannot cover this: an unreachable
  panel is not a layout fault, so it correctly reported 0 issues for a feature that had a
  key binding, a desktop button, a passing suite and no way in on a phone. Diff this verb
  between `set-feature --touchscreen true` and `false` and that class of bug names itself.
- **`first_frame`** (CLI: `first-frame`; 0.23.0, H-059) answers "what IS the screen showing", which none of
  the other checks do — `findings` asks "is anything wrong", `reachable_ui` asks "can a
  finger hit this ONE control". Returns `{tree_paused, cursor_mode,
  visible_canvas_layers: [{path, layer, name}] (paint order, back to front, hidden ones
  excluded), topmost_control: {path, type, text, rect} (the single Control everything
  else is drawn under — an empty dict if none is on screen), viewport, geometry_trustworthy,
  geometry_caveat}`. `topmost_control` needs no z-index or occlusion math: Godot paints
  children after parents and a later sibling after an earlier one, so the last visible,
  on-screen Control a depth-first walk finds is the last one painted, by construction.
  `cursor_mode` is `Input.get_mouse_mode()` by name (`VISIBLE` / `HIDDEN` / `CAPTURED` /
  `CONFINED` / `CONFINED_HIDDEN`).
- **`screenshot`** takes `region: [x,y,w,h]`, `hide: [node paths]` and
  `hide_group: [group names]`, and reports the applied `region` and the `hidden` paths.
  The crop and the hiding happen game-side inside one command, so a store capture is
  reproducible from the command line instead of being two `set_state` calls plus a
  separate PIL crop. Each node's **previous** visibility is remembered, so an
  already-hidden node is not "restored" into view, and visibility is restored on every
  failure path too — this verb cannot leave a HUD switched off. **`--hide` accepts a
  `CanvasLayer`** (0.20.0, gh#5) — the node nearly every HUD, pause menu and overlay is
  rooted in, and which is a `Node`, not a `CanvasItem`; before, those were silently
  dropped and the "hidden" capture had the HUD in it. A `--hide` / `--hide-group` that
  names **nothing it can hide is an error** (`success: false`, no file written, each
  path explained: missing, or a class with no `visible`) rather than a warning beside a
  capture that ignored the flag.
- **`ping` reports `bus_dir` and `user_dir` separately**, always. When they differ the
  bus is isolated and saves/screenshots are not; when they match nothing is isolated.
  `launch --isolated` previously claimed an isolation it did not have and nothing could
  contradict it, so this is a read rather than an assumption.
- **`get_node_bounds` works on any `CanvasItem`**, not just a `Control`. It used to
  answer `Node is not a Control`, so every visual check on a game object meant
  rebuilding the camera transform by hand. Every rect comes from
  `get_global_transform_with_canvas()` — the same transform the renderer uses, so it
  accounts for the camera, every ancestor's scale and the `CanvasLayer` — applied to
  whatever extent the node can report (a `Control`'s `size`, a `Sprite2D`'s texture
  rect, a `CollisionShape2D`'s shape, a `TileMapLayer`'s used rect).
  `data.size_source` names which of those produced the size; see the sharp edge on
  `0x0`.
- **Every screen-position check measures in screen space, not `CanvasLayer` space**
  (0.17.0). `Control.get_global_rect()` stops at the `CanvasLayer`, so a HUD on a
  layer with a `scale` — an ordinary way to get resolution independence — used to
  report rects in layer units while the viewport was measured in pixels. On one real
  project that was **51 of 51 `ui_overflow` findings false**, and `reachable-ui`
  called visible, clickable buttons `OFF-SCREEN`. `validate-ui`, `reachable-ui`,
  `node-bounds`, `ui-snapshot` and `ui-snapshot-diff` all now transform through the
  canvas. `get-state` reports both: `transform.global_rect` is the Control's own
  layer-space answer, `transform.screen_rect` is where it lands.
- **Headless runs measure against the project's *designed* viewport.** Headless has
  no window, so `root.size` is `64x64` and every `Control` wider than 64px would
  "extend past viewport". The UI verbs fall back to
  `display/window/size/viewport_width`/`_height`, which is what a windowed run of the
  same project reports.
- **Headless geometry is flagged as headless** (0.20.0, H-051). The fallback above
  fixes the *reference rect*; it cannot fix a node the **game** positions from the
  window size. `get_window().size` is `64x64` headless, so a panel centred with
  `(get_window().size - size) / 2` on an 800×600 design sits at `(-368,-268)` headless
  and at `(0,0)` for a player — reproduced exactly. That number reached a published
  report as "the whole end-of-run screen off-viewport" before a windowed screenshot
  overturned it. So `get_node_bounds`, `get_ui_snapshot`, `validate_ui`,
  `reachable_ui` and `findings` all carry `geometry_trustworthy: bool` and
  `geometry_caveat: String` (empty when windowed); `findings` additionally stamps
  `caveat` on each *geometry-code* finding (`ui_overflow`, `ui_negative_pos`,
  `ui_outside_safe_area`, `interactive_overlap`, `container_layout_drift`, and any
  `unreachable_ui` that is off-screen), never on `ui_transparent` / `small_tap_target`
  / text overflow, which are the same headless or windowed. The client prints the
  caveat next to the numbers. Headless verdicts still gate — CI lives there — but an
  off-viewport verdict measured headless is a fact about the headless run until it is
  confirmed windowed.
- **`performance` says when the tree is paused** (0.20.0, gh#6). The bridge is
  `PROCESS_MODE_ALWAYS`, so it answers on a paused tree with a plausible FPS for a game
  that is not stepping — a whole `/verify` phase validated a title-screen pause as
  healthy on those numbers. The reply carries `tree_paused`, the message says `PAUSED`,
  and the client prints `TREE IS PAUSED` above the metrics. `/verify` Phase 2 now
  gates on `ping`'s `tree is PAUSED` before Phase 3 runs.
- **`set-state` writes through a dotted path** (0.17.0), the same one `get-state`
  reads through. `--property environment.ambient_light_energy` resolves the container
  and writes the leaf, which is how every knob in a lighting rig, a material or a sky
  is reachable — they all live one level in. Note this mutates the **Resource**: a
  material shared by several nodes changes for all of them. A component of a built-in
  struct (`size.x`) is refused, naming the call that works (`--property size`).
- **Verbs are accepted hyphenated or underscored** (0.17.0). Handlers register with
  underscores, the CLI and the docs use hyphens, and the sequence-step dispatcher
  already normalized between them — so `cmd light-get` failed while the identical
  step inside a sequence worked. An action that matches nothing now suggests the
  nearest registered verb instead of a bare `Unknown action`.
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
set-game-speed, pause, unpause, wait-frames, clear-nodes, validate-ui, ui-snapshot,
node-bounds, save-ui-baseline, ui-snapshot-diff, tilemap-cells,
tilemap-region, scripts-seen, canvas-scale, set-resolution,
find-nodes, press, raycast, sample-pixels, reachable-ui, aabb, look-at, new-uid,
mouse-move, reload, first-frame, fire-entry-point, project-settings, contained-in,
restore-userstate, verb-usage, batch
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
- `aabb --node PATH` — the merged **world-space** AABB of a 3D node's geometry:
  `min`, `max`, `size`, `center`, plus `top_y` ("what do I rest something on") and
  `bottom_y` ("is it sunk into the floor"). This is `node-bounds`' 3D counterpart —
  `node-bounds` answers *where on screen*, `aabb` answers *where in the world*. It
  merges `GeometryInstance3D` descendants and reports both what it merged
  (`merged`) and what it skipped and why (`excluded`); see the sharp edge below,
  because what it excludes is the whole point.

- `get-state --node PATH --property NAME` — repeatable. Without it a single `Label`
  read returns ~120 keys, which is why every assertion used to be piped through an
  ad-hoc filter. A name that doesn't exist is reported explicitly rather than silently
  omitted, so a typo can't look like a missing value. A **dotted** name walks into
  Resources and Dictionaries: `--property texture.region`,
  `--property slot_data.item.name` — and, since 0.32.0 (H-046), into a built-in struct's
  named components: `--property position.x`, `modulate.a`, `size.y`, `global_transform.origin`
  read what the caller plainly means instead of refusing (`get-state`) or printing
  `null` (`find-nodes`). Writes are still whole-value: `set-state --property position
  --value 12,7`, never `position.x`.
- `find-nodes [--class C] [--group G] [--method M] [--where NAME=VALUE] [--property NAME]
  [--call METHOD] [--root PATH] [--limit N]` — `--where` and `--property` are repeatable
  and accept dotted paths (`--where slot_data.item.name='Iron Bar'`). Use it instead of a
  `scene-tree` dump plus a `get-state` per child when the question is "which of these
  is the one". `--call METHOD` (0.32.0, plant-tower-defense:G-005) calls a zero-argument
  method on every hit and prints the result beside the path (`get_hp()=12`), so a node
  whose auto-generated name changes every launch can be identified and read in one
  round-trip; a missing method lands in `call_errors`, it never aborts the reply. A
  `--property` the resolver cannot read prints `<unresolved: reason>` instead of a bare
  `null` (0.32.0, H-046) — `null` alone was indistinguishable from a property that holds
  null.
- `contained-in --node PATH --within PATH` (0.37.0, plant-tower-defense:G-048b) — is
  one Control's screen box inside another's? Exit 1 with the per-side overhang
  (`hangs off by 40px right`) when it is not; `control_centre_inside` says whether it
  visibly *belongs* on that panel. The question three screens in one project each
  answered with a bespoke test, and the read a `ui_escapes_panel` finding is checked
  against.
- `project-settings [--filter PREFIX] [--name KEY ...] [--json]` (0.32.0,
  dave-game:G-003) — `ProjectSettings` as the **running** game sees them. A value
  written into `project.godot` that never applied (a typo'd key, an editor overwrite,
  a setting the engine ignores at runtime) had no gate at all: lint and `validate-all`
  reported clean while the game rendered on the stock clear colour, and the only
  detection was opening a PNG. `--name KEY` is exact and repeatable; a key no setting
  has exits 1 and is listed under `missing`. `get-state` cannot answer this because
  `ProjectSettings` is not a node.
- `step-time --seconds N [--hold ACTION] [--then-pause]` — `--then-pause` (0.32.0,
  plant-tower-defense:G-016) sets `SceneTree.paused = true` the moment the step lands
  (lifting a pre-existing pause for the step itself), so a short-lived state can be
  stepped *into* and then read at leisure: without it every step + read pair costs
  unbounded ambient game time on top of the seconds requested, because the tree keeps
  running between the reply and the next command. The reply's `elapsed_wall_ms` (printed
  as `Wall clock:`) is the real time the step took, so "what I advanced" and "what
  actually passed" are both visible. `unpause` resumes. Note the verb's own limit still
  holds — nothing here is a manual tick; the tree is NOT paused *during* the step (the
  0.31.0 CLI help said it was, and was wrong).
- `press --node PATH [--toggle BOOL]` — emit `pressed` on the button at, or directly
  under, `PATH`.
- `raycast --from X,Y[,Z] --to X,Y[,Z] [--mask N] [--areas] [--exclude NODE]` — two
  components query the 2D space, three the 3D space; `--exclude` is
  repeatable; without `--mask` every layer is tested.
- `sample-pixels [--rect X,Y,W,H | --points X,Y;X,Y] [--expect RRGGBB,... [--tolerance N]]` — mean / dominant colour over a screen rect or exactly the named points; with `--expect` an **assertion** (exit 1 when a colour appears zero times within the tolerance)
  (default: the whole viewport).
- `mouse-move --relative DX,DY [--steps N] [--position X,Y] [--buttons MASK]` — a
  real `InputEventMouseMotion` (mouse-look); prints the mouse mode and warns when
  the cursor is captured.
- `reload res://path` — re-read an edited resource into the running game; exits 1
  if the cached instance could not be updated in place.
- `reachable-ui` — no flags. Prints every interactive Control with its rect, marking
  each `OFF-SCREEN`, `SCROLL TO REACH (inside <ScrollContainer>)` or `BLOCKED BY <path>`
  rather than dropping it. Run it once per device profile
  (`set-feature --touchscreen true|false`) and diff.
- `curve --node PATH --method NAME --from N --to N [--step N] [--args JSON]
  [--arg-index N]` — the series a pure method produces over an integer range.
- `scene-tree [--depth N] [--root PATH] [--property NAME]` — `--root` lists one subtree
  instead of the whole scene (a deep UI subtree otherwise truncates); `--property` is
  repeatable and reports that property on every node.
- `screenshot [--filename F] [--region X,Y,W,H] [--hide NODE] [--hide-group GROUP]` —
  `--hide` / `--hide-group` are repeatable and accept a `CanvasLayer` as well as any
  `CanvasItem`; the crop and the hiding happen game-side in one command, and previous
  visibility is always restored. Naming nothing hideable **exits 1 with no file**.
- `quit [--wait S] [--kill]` — sends the verb, then confirms the owner pid actually exited (with
  a short grace beyond `--wait`), exit 1 only for a real survivor. It then **sweeps
  `.devtools/launched.jsonl`** (0.21.0, gh#14.1): every pid this project ever launched —
  the `_console.exe` wrapper that spawned the engine, and an engine from an earlier
  launch that stopped polling without exiting — is checked alive-and-same-start-time
  and listed; `--kill` terminates exactly those. Never by image name (other sessions run
  games on this machine); a recycled pid whose creation time does not match the record
  is left alone — including the pid that was readable at launch time and is not
  readable at all now (0.25.0, gh#24): that is the same recycling, one layer earlier,
  where Windows reused the number for a process this tool cannot even query. Only a
  genuinely unverifiable survivor (never readable, not a recycling signature) is
  reported without moving the exit code — `verified: null` never fails the run, only
  `verified: true` does, so the code stays worth reading. On Windows the
  liveness check uses `OpenProcess`/`GetExitCodeProcess` (0.20.0, gh#6): `os.kill(pid, 0)`
  raises `WinError 87` for a **dead** pid, which read as "alive", so `quit` on Windows
  used to warn `STILL ALIVE` after every wait and name a pid `tasklist` no longer had.
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
- `performance [--frames N] [--by-type] [--reset-baseline]` — FPS is a mean over N
  frames (game default 30; `0` = one instantaneous read) printed as `mean … min … max …
  n=… (…s)` and marked `STILL SETTLING` when the halves disagree; `--by-type` prints
  which node classes grew or shrank since the baseline; `--reset-baseline`
  re-baselines the orphan AND node counts (see below). The reply
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
    follow-up command — a bus that never answered exits 1 instead, because a
    follow-up command that would not work is worse than none. **On that timeout it
    leads with any Godot `ERROR:` line found in EITHER captured log** (0.31.0, gh#31)
    — `launch_stdout.log` as well as `launch_stderr.log`, because Godot's own
    destination for a startup-abort message (a native `OS.alert()` dialog: `Main
    scene's path could not be resolved from UID. Make sure the project is imported
    first. Aborting.`, from an `--import` that printed a clean-looking completion but
    never wrote `uid_cache.bin`) is not reliably stderr. Before this only stderr was
    tailed, so that exact line sat unread in the other log while the generic 20s
    timeout read identically to the unrelated gh#28 missing-separator bug, and
    diagnosing it took a PowerShell screen-scrape of the alert window. It does not
    move `user://`; the output labels that line `SHARED`, and the sharp edge below
    says why.
  - Everything after a bare `--` is forwarded to the Godot command line, so a run
    needing an engine flag no longer has to re-implement launching:
    `devtools.py launch --no-mute -- --write-movie out/frame.png --fixed-fps 30`.
  - **A `--devtools-session`/`--devtools-busdir` token in that passthrough is now
    wired correctly, not silently dropped** (0.28.0, gh#28). Those two only ever
    reach the addon via `OS.get_cmdline_user_args()` — everything after Godot's
    *own* `--` — so `launch -- --devtools-session X` used to append them straight
    onto the engine command line with no `--` ahead of them at all: two
    unrecognized top-level tokens, silently ignored, and every later
    `ping --session X` timed out reading exactly like a crashed game. `launch`
    now also adopts `X` for its own owner-conflict pre-check and post-launch
    poll, so a bare passthrough session behaves identically to `--isolated` /
    the top-level `--session` flag rather than being the one spelling that
    silently did nothing.
  - `--no-mute` opts out of `--mute`: a `--write-movie` run records the audio bus, and
    a muted run captures silence.
  - `launch` **refuses to join a bus a live pid already owns** — two instances
    answering one bus is silent corruption — naming the pid and session in the error.
    A *dead* owner file is cleared rather than obeyed, since that is the normal
    post-crash state. `--allow-second-instance` overrides the refusal.
  - `--no-wait` returns as soon as the process is spawned, without proving the bus
    answers.
- `quit [--exit-code N] [--wait SECONDS] [--kill]` — sends the quit and then **waits for the pid
  in the owner file to actually go** (default 10 s), exiting **1** on a survivor with
  the kill line to run — on Windows **both** `Stop-Process -Force -Id` (PowerShell) and
  `taskkill /F /PID` (cmd.exe only: through Git-Bash/MSYS the `/F` is rewritten to a
  phantom `F:/` path and it fails with `Invalid argument/option - 'F:/'`, gh#12).
  `launch` writes every pid it starts, and the pid the bus answers with, to
  `.devtools/launched.jsonl`; `launch` warns about earlier ones still alive
  (`--kill-survivors` clears them) and `quit` sweeps them after the owner exits.
  `quit` was not reliably fatal: three separate
  times the old process was still alive after a relaunch (once at 1.4 GB), and the only
  symptom was verbs returning empty output while `ping` said `No response` — which
  reads as *no* game rather than as *two*.
- `list-commands --offline` — no running game: statically parse `register_command(`
  names from the installed core and the config's extension script, labeled
  generic/project (a text scan, not runtime truth). **Both modes print each verb's
  arg keys** (`place_plant  args: plant, x, y`, 0.21.0, gh#14.2), scanned from the
  handler body's `args.get("k")` / `args["k"]` / `args.has("k")` — a key not listed is
  silently ignored by that verb, which is how a guessed `cell` planted the default at
  the default cell and reported success. `--json` in online mode now returns
  `{"actions": [...], "args": {verb: [keys]}}` (was a bare list).
- `set-feature --query` — read the current feature-flag values without writing.

Two subcommands make project verbs first-class without touching the CLI:

- `cmd <action> [--args JSON]` — sends `{action: <action>, args: <parsed json>}`
  verbatim, so any project-registered verb is reachable:

  ```bash
  python tools/devtools.py cmd spawn_enemy --args '{"count": 3}'
  ```

- `list-commands` — sends `{action: "list_commands"}` and prints the discovered
  verbs (generic + project).
- `harness-version [--client]` — prints the installed revision game-side and client-side; `--client` reads disk only and never opens the bus. Exits 1
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

**A shared `user://` is still shared, and there is no supported way to isolate it**
(gh#28). Separate buses fix the crossing, not the rest of the directory: screenshots,
UI baselines and save files still collide, and `--headless --import` still races on a
single `.godot/` class cache. Godot has **no `--user-data-dir` engine flag and honours
no `GODOT_USERDATA` environment variable** — setting one before launching Godot does
nothing to where the engine writes; only `devtools.py` reads it, and only to decide
where *it* polls. Setting `GODOT_USERDATA` (or `--userdata`) without also changing
where the *game* writes just makes the client poll an empty directory while the game
writes to the real one — the exact silent-timeout shape `launch -- --devtools-session`
takes when it is not wired correctly (see below).

The only way to actually move an instance's `user://` is
`application/config/use_custom_user_dir=true` **and** `custom_user_dir_name` in a
**per-worker copy of `project.godot`** — both keys; the name alone is silently ignored
and the copy keeps writing the shared directory (a 0.37.0 probe rewrote a developer's
real save that way; H-067). Heavier than an env var, but it is the one mechanism Godot
itself honours. `launch` now says when the resolved `user://` was last used by a game
from a different checkout. Set `GODOT_USERDATA` / `--userdata` to match wherever that custom dir
resolves to, so the client polls the same place the game actually writes:

```bash
# project.godot in this worker's copy sets custom_user_dir_name="run-a"
godot --path . -- --devtools-session a &
python tools/devtools.py --session a --userdata /path/to/run-a's/resolved/userdir ping
```

Without a per-worker `project.godot`, treat `user://` as shared and serialize anything
that touches it (screenshots, imports, save files) across parallel instances. Use
`--session` alone when instances only need separate buses; add the custom-user-dir
setup when they must not share `user://` at all.

### Userdata directory resolution

The CLI must poll the same `user://` directory the game writes to. **This resolution
is entirely client-side** — Godot itself is never told any of this; both entries below
only change where `devtools.py` looks, so they only help once something ELSE (usually
`custom_user_dir_name` in a per-worker `project.godot`) has actually moved where the
game writes. It resolves that directory in priority order:

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
| `--no-orphans` | Skip the orphan scan (on by default since 0.21.0; `--find-orphans` is accepted as a no-op). Advisory; never fails. |
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

The **orphan scan** covers a failure both other gates miss: a system with passing unit
tests and no caller anywhere in the game. Lint checks UIDs and scenes, the test runner
green-lights orphaned code, and both report clean. It is a heuristic — signal
callbacks, `call()`-by-name, and `@export` hooks produce false positives — so it is
advisory and never gates. It **runs by default** (0.21.0, gh#11) and prints
`Orphans: N of M public function(s) across S script(s) have no live reference` as a
denominator, because opt-in meant the default gate passed on a method nothing could
ever call and nothing in the output said the check existed — the same reason the
string-reference scan below is always on. `--no-orphans` turns it off.

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
| `as_precedence` | error | `x as Type == y` (or `!=`) with no parentheses (0.31.0, moving-in:G-053). GDScript's `as` binds *looser* than `==`, so this parses as `x as (Type == y)` — a cast to a bool — and is a hard parse error that took a real suite from green to 48 failures on one line. The parenthesised `(x as Type) == y` is never wrong and never flagged. Only fires when the token after `as` is PascalCase (a Type shape); a lowercase operand is left alone rather than guessed at. Zero false positives across three real projects. |

**A clean run is not a compile, and the tool now says so itself.** Every run that ends
with zero errors prints a `NOT COVERED:` line, and `--json` carries the same text under
a `not_covered` key. The gap is type inference: `var kids := root.get_children()` where
`root` is typed only `Node` is a hard parse error, and every name in that line resolves,
so this checker passes it. Deciding it would need method resolution order and return
types — i.e. opening the project, the one thing this gate exists not to do. It matters
because this is the only concurrency-safe gate, so it is the only one a fan-out agent is
allowed to run: two agents in one real session shipped code that did not compile behind a
clean `name_check` (`moving-in:G-009`). The line is suppressed when there are errors to
report, so it never buries a real finding.

**`--require-compile FILE [FILE ...]` closes the type-inference gap for named files,
without giving up parallelism (0.26.0, gh#20.1 / plant-tower-defense:G-025).** The only
way this tool ever launches Godot, and only for the files named: one
`godot --check-only --script res://…` per file, which does compile function and const
bodies. Verified read-only against `.godot/` — no import, no cache write, single or
concurrent — so a fan-out agent can check its own changed file(s) alongside siblings
still mid-task, `import_check.py`, or a running game, without contending for the same
lock everything else in this project shares. Findings land as `compile_error` next to
the static ones; a file that compiles clean is named in `compiled OK:` rather than
silently passing. **Two real costs, both confirmed directly rather than assumed:**
`--check-only` reads the *existing* class cache to resolve a `class_name` from another
file — it does not build one — so on a project that has never been imported at all, a
file referencing a sibling's `class_name` false-positives `Could not find type`. Needs
one prior `--import` or launch (this project's own, or any earlier one in the same
checkout); after that, the cache is shared read-only and every agent's
`--require-compile` sees it. Second, unrelated to imports: **`--check-only` on an
isolated file does not resolve an autoload SINGLETON by its global name at all**, even
after a prior import, even with the autoload correctly declared in `project.godot`
(0.29.0, discovered building gh#30's fix — a two-line repro: an autoload script plus a
second script calling it by name, `--check-only` on the second reports
`Identifier not found` regardless of import state). This project's own `DevTools`
autoload is exactly this shape, so `--require-compile` on **any file that calls
`DevTools.<verb>(...)` by the bare global name** false-positives a compile error on
code that runs correctly. Not fixable from this side — it is `--check-only`'s own
parse-time scope, narrower than a full project launch's. Workaround where it matters:
`get_node("/root/DevTools").call("method_name", ...)` resolves fine under
`--check-only` (a runtime string lookup, not a static identifier), at the cost of
losing static argument/return checking on that call site.

Exit codes follow the harness convention: `0` clean, `1` findings that count (errors,
plus warnings under `--strict`), `2` could not run. Flags: `-p/--project`, `--json`,
`--strict`, `--only <prefix>` (repeatable — filters the *report* while still scanning
the whole project, so cross-file names still resolve; this is what a fan-out agent
wants), `--no-strings`, `--baseline-write <p>` / `--baseline <p>` (same
`file|rule|subject` key format as `lint_project.gd`, so only NEW findings gate on a
project with a backlog), `--refresh-api`, `--force-refresh`, `--api <path>`,
`--require-api`, `--require-compile <file...>`, `--godot`.

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

### `tools/coverage_check.py` — what your checks never ask

Every other gate answers *"did the checks pass"*. This one answers *"which questions
do the checks ask at all"* — the only thing a green run structurally cannot tell you.
A hand-rolled suite is incapable of noticing what it forgot to check, and
`70 checks, 0 failures` reads identically over a working screen and a broken one.

It enumerates the eight defect classes this harness knows about from real Godot
failures — `ui_layout`, `ui_reachable`, `signal_unconnected`, `orphan_growth`,
`input_path`, `scene_validation`, `shader_compile`, `name_resolution` — counts the
project's `_T.assert_*` call sites, and for each class prints either `UNCHECKED` with
the cheapest command that would cover it, or `COVERED` with **the file:line and token
that convinced it**.

**The evidence line is the design.** A checker whose only possible output is "all
covered" is indistinguishable from one that is not running — this repo has been burned
by that three times ([H-035]). Printing the matched line is what lets a reader falsify
a COVERED in ten seconds.

Four statuses, because "something asked this once" is not the same claim as "the suite
asks this every run":

| Status | Means |
|---|---|
| `COVERED` | A check in this project's `test_dir` asks the question. |
| `COVERED (gate)` | An always-run tool covers it — and only while that tool is actually installed. A project without `tools/name_check.py` correctly reports `name_resolution` UNCHECKED rather than inheriting a gate it does not have. |
| `COVERED (session)` | Seen only in `.devtools/verify-runs.jsonl` or a `devtools_log*.jsonl`. Printed with its timestamp, because a past run is an observation, not a standing check. |
| `UNCHECKED` | Nothing exercises it. |

**A filesystem sweep counts as strong evidence for `scene_validation`, distinct from a
literal (gh#21).** `load("res://x.tscn")` was the only strong token; a test that walks
`res://`, filters on `.tscn`, and loads whatever it finds scored UNCHECKED, even though
it is stronger than any hardcoded literal — it can't go stale and it covers scenes
added after it was written. Recognized by `ends_with(".tscn")` / `match("*.tscn")`
alongside a `load(`/`ResourceLoader.load(` call in the same file, and reported with its
own evidence line ("sweeps res:// for .tscn and loads what it finds") so it reads as a
distinct, stronger claim rather than a disguised literal.

**Weak evidence never promotes a class.** `ui.size` without `instantiate_ui` (a Control
outside a tree reports `0x0`, so that assertion asserts the failure mode itself),
`PackedScene` without a `res://….tscn` literal (the shipped example test packs one in
code, which would mark every scaffolded project covered on day one), `connect(` without
`is_connected`, `InputEventKey` without a delivery call — and **anything inside the
methods the harness itself seeds** into `test_selftest.gd`. That last one matters more
than it sounds: the seed genuinely calls `_T.instantiate_ui()` and asserts `ui.size`, on
a two-node HUD it builds in code, so crediting it marked every freshly scaffolded
project `ui_layout`-covered on day one — two real projects were reported covered off
`test_example.gd:42` before it was fixed. The rule keys on the enclosing *method*, not
the file, so a project that adds its own tests to the seeded file gets full credit for
them. Weak matches are printed
underneath the UNCHECKED verdict with the reason they fell short — a false UNCHECKED
costs a reader ten seconds, a false COVERED is the entire bug the tool exists to catch.
Source is scanned with comments and string bodies blanked, so a token named in a
docstring cannot fake coverage.

**Like `name_check.py` it never opens the project** — no `godot`, no `--import`, no
`.godot/` write — so N agents can run it at once and a never-imported worktree can run
it at all.

Advisory by design: **exit `0` always**, `--strict` exits `1` on any UNCHECKED class,
`2` means it could not run. Flags: `--json`, `--only ID`, `--test-dir DIR`,
`--config PATH`, `--project DIR`.

A covered class means the question is asked. It never means the answer was right, and
the eight classes are the failures this harness has been burned by — not every way a
game can break. Coverage here is a floor, never a pass; the tool prints that itself as
a `NOT COVERED:` line on every run.

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

**`Import OK` is a narrower claim than it reads (gh#23).** `--import` only registers
global class names; it never evaluates a function or `const` body, so a `const`
initializer that calls a method (not a constant expression) passes `--import` clean and
is only caught by `lint_project.gd`'s real compile. The success line now says exactly
that — `Import OK: the class cache regenerated and godot --import's N line(s) of output
contain no [...]` followed by a `NOT COVERED:` line naming what `--import` structurally
cannot see — rather than reading as a compile verdict it isn't. `/verify` still runs
lint alongside import in every tier that reaches Phase 1, so nothing ships broken on
this gap; the fix is honesty about which gate actually caught it.

**A crash with no parse-error text is retried once, transparently** (0.29.0,
plant-tower-defense:G-044). `godot --import` segfaulted on its first call of a real
session (exit 139, mid-reimport of vendored audio) and imported clean on an immediate
retry with nothing else changed — the harder-failure sibling of the shared `.godot/`
class-cache contention this project already knows about under concurrent load. A crash
is distinguishable from a genuine parse failure by exactly the thing this tool already
scans for: a nonzero exit with **no** recognizable `SCRIPT ERROR`/`Parse Error`/etc. in
the output. `import_check.py` retries that specific shape once, prints a `Note:` saying
so, and only then treats a repeat as unverified. A run with real findings is never
retried — retrying broken code fixes nothing, and could reorder a cascade's line
numbers between attempts.

### `tools/run_tests.py`

`run_tests.gd`'s own PASS/FAIL tally is fooled by a test that aborts mid-method: Godot
coerces the aborted coroutine's return to the declared type's default (`""` for a
`-> String` test), which is byte-for-byte identical to a genuine pass, and `[VACUOUS]`
only fires on *zero* assertions — an abort that already ran some is the more dangerous
case (0.27.0, gh#27 / moving-in:G-050). GDScript cannot observe its own process's
stderr after the fact, so no fix inside `run_tests.gd` is possible; the signal has to
come from outside the process, same reasoning as `import_check.py` wrapping `--import`.

```bash
python tools/run_tests.py                       # the whole suite
python tools/run_tests.py -- --filter foo        # passthrough to run_tests.gd
python tools/run_tests.py -- --file test_x.gd
python tools/run_tests.py --json                 # this wrapper's own verdict
```

Runs the suite as a subprocess, captures stdout+stderr together (in order, to
`.devtools/tests.log`) exactly as `run_tests.gd` printed them, then scans for
`SCRIPT ERROR` / `USER SCRIPT ERROR` lines — deliberately narrower than
`import_check.py`'s signal set, since `Parse Error` / `Failed to load script` /
`Compilation failed` are load-time signals that tool already owns. Reports
`Errors: N emitted during the suite` next to the runner's own denominators, and
**a nonzero count overrides a reported-clean exit** — the entire point — while never
downgrading a genuine `run_tests.gd` exit `2` (could not run at all). Args after `--`
pass straight through to `run_tests.gd`, so `--filter`/`--file`/its own `--json` all
still work. Exit codes follow the harness convention: `0` clean, `1` the suite failed
or emitted an error under a reported pass, `2` could not run. Binary resolution and
flags match `import_check.py`: `-p/--project`, `--godot`, `--json`, `--timeout`.

### Standalone runners: `tools/eval.gd` and `tools/capture.gd`

Both are shipped and installed, and neither needs the bridge or a running game — they
open the project, do one thing, and exit. Useful in a fresh worktree, on a project that
has never launched, and when the question is about one scene rather than a session.

**`eval.gd`** evaluates a single Godot `Expression` against the project:

```bash
godot --headless --path . --script res://tools/eval.gd -- --expr "Balance.xp_for_level(3)"
```

Global classes (`class_name`) bind to their loaded scripts, so constants and static
functions resolve, and autoloads are reachable by node path after the runner awaits one
frame. Live world state is *not* in scope — this opens the project, it does not play the
game; anything depending on a running scene belongs on the bridge (`get-state`,
`run-method`). Exit `0` result printed / `1` parse or execute failure / `2` no `--expr`.

**`capture.gd`** writes one scene to a PNG:

```bash
godot --path . --script res://tools/capture.gd -- --scene res://ui/hud.tscn --out shot.png
```

Note the absent `--headless`, and it is the whole sharp edge. Headless Godot loads the
dummy rendering driver, `root.get_texture()` returns **null**, and a tool that did not
check would write a blank or zero-byte file and report success — a picture of nothing is
indistinguishable from a picture of a broken scene, and it would be believed. So a
headless run exits `2` and names the fix rather than producing a file. A real display is
required; on CI that means a virtual one (`xvfb-run` and friends).

| Flag | Purpose |
|---|---|
| `--scene <res://path>` | Scene to capture. Default: the project's main scene. |
| `--out <path>` | Output PNG. `res://` and `user://` resolve. Default: `user://screenshots/capture_<scene>_<timestamp>.png`. |
| `--frames N` | Frames to step before capturing (default `3`). |
| `--size WxH` | Resize the window first, e.g. `--size 1920x1080`. |
| `--fail-on-uniform` | Exit `1` if the capture is a single flat colour. |

**`--frames` is not a formality.** Two is the floor for anything `Control`-shaped: the
first frame is where `@onready` runs and containers get their size, so a capture taken
earlier is a correctly-rendered picture of an unfinished layout — which looks like a
layout bug and isn't one. Raise it for tweens, particles, or an animation you want
settled.

Every run prints scene, dimensions, frames stepped, **distinct colours sampled** and the
output path. That colour count is the point: a flat image is what a broken scene, a
capture taken too early, and a working solid-colour splash all produce, so the run says
so out loud (`WARNING: the capture is a single flat colour`) instead of reporting a
written file and stopping there. It stays exit `0` by default because a solid scene is
legal; `--fail-on-uniform` makes it gate for callers using the capture as evidence that
something drew.

Exit `0` captured / `1` ran but produced nothing usable (scene missing or
uninstantiable, save failed, flat under `--fail-on-uniform`) / `2` could not run
(headless, malformed flag).

This does not replace the bridge's `screenshot` verb, which photographs a **live
session** mid-play — with its input state, spawned entities and elapsed time — and can
crop, hide nodes, and be driven between other verbs. `capture.gd` answers the different
question: what does this scene look like on its own.

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

**`--about PATH` narrows the denominator for a fan-out session (gh#20.2).** Reach's
default denominator is the whole dirty tree (`git status`), which is right for a solo
session and wrong once several subagents edit disjoint files at once: a run gets
graded on a sibling's still-uncommitted file (a false miss), or — the inverse, and more
dangerous — silently credited for one it never touched. Pass `--about` (repeatable)
naming the file(s) *this* run actually set out to verify; the denominator becomes that
set intersected with what actually changed, and anything outside it is neither reached
nor unreached — it isn't this run's business. A path named that never shows up in the
changed set prints a warning (typo, or a file that was never saved) rather than
silently doing nothing.

`record` derives everything it can — timestamp, sha, branch, changed files — and takes
only runner exit codes, Phase 4 check results, and duration from the caller. The split
is deliberate: a run can misreport its own checks, but not whether it touched the diff.

**Reach has three states, not two.** A ratio (`reached 1/4`), a *real* zero (git is
present and reports nothing changed — a fact), and **unavailable** (there is no git
repository at all, so there is no changed set to score against). The third used to print
as `reached 0/0`, which reads as "nothing to check" when the truth is "cannot tell", and
the row went into the ledger carrying a denominator it never had (`moving-in:G-003`). In
the recorded row this is `changed: null` plus an explicit `changed_unavailable: true`,
with every reach bucket `null` — `null` being this file's established shape for "could
not tell", so older readers already skip the row rather than scoring it as a clean
sweep. `stats` counts those runs separately instead of blaming them for having no
snapshot. Reach `unavailable` never drives an `insufficient` verdict: that verdict is a
claim about the run, and this is a statement about the checkout.

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
- **Base classes** (0.21.0, gh#15.3) land in `reached_base` with the observed
  descendant in `reached_base_via`, printed inline (`+1 as base class: X extended by
  Y`). A node reports only the script attached to it, never its ancestry, so a base
  class whose only live instances were subclasses scored NOT reached while its `_draw()`
  ran every frame — systematic, in every project, and the only escape was an alias that
  turns an observation into a claim. Reach now walks each observed script's `extends`
  line statically (a quoted `res://` path, or a `class_name` resolved through
  `.godot/global_script_class_cache.cfg` — or a scan for `class_name` when the project
  was never imported) and credits every ancestor in the changed set. A static fact about
  the files, needing no config; still a distinct bucket so it stays auditable.
- **A static-utility script can self-report into `reached` directly** (0.29.0, gh#30 /
  plant-tower-defense:G-014). `class_name Music extends RefCounted` with only static
  entry points is never itself a node's `script` — the identical shape as
  `reached_base`'s problem, one level further out, where no live node carries the
  script at all, so no amount of walking the tree or the `extends` graph can find it.
  Call `DevTools.mark_script_reached(<its own res:// path>)` once from each real entry
  point; it writes straight into the same `_scripts_seen` dictionary `scripts-seen`
  already reports and `reach` already reads as `reached` — an observation, not a
  declaration, unlike `reach_aliases` — so it needs no config and chains through
  nothing. See "The registry-extension pattern" above for where to call it from.

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

### `test_dir` is the home for the checks a session writes

An agent asked to verify a change writes a selftest whether or not the project has
one — that instinct is robust enough to survive removing every prompt that asks for
it. The failure is not that the checks are bad; it is that they are written into a
scratch script or the transcript, thrown away at the end of the session, and written
again from scratch by the next one. A project accumulates nothing.

So `test_dir` (`res://test/unit` by default) is the documented destination, the
scaffolder seeds it as **`test_selftest.gd`** — named and headed as *this project's
selftest, extend it* rather than *example, delete me* — and `/verify` Phase 1 re-runs
everything in it on every change. A check promoted there costs one edit and is then
re-run for the life of the project.

Every run prints the suite as a standing quantity:

```
  Assertions: 61 executed
  Suite: 4 test script(s) in res://test/unit
```

That pair is the inherited-coverage reading. A project stuck at `Suite: 1` after
twenty sessions is one where every session wrote a throwaway; the number is there to
make that visible rather than inferable. `--json` carries it as `test_files` and
`test_dir`.

The split is by **what the check needs**, not by how important it is: anything that
needs a live playing game — real input over time, physics, a tween landing, a scene
mid-transition — stays a `/verify` Phase 4 bridge check. Everything else (pure logic,
resources, data tables, and any layout `instantiate_ui` can resolve) belongs in
`test_dir`. `/verify` Phase 4 Step 5 makes that decision an explicit, reported step.

Installs predating 0.19.0 keep their `test_example.gd`; the scaffolder only seeds a
test dir that is missing or empty, so nothing is renamed or clobbered under a refresh.

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

**The size that lands can be larger than the size the code assigned** (0.39.0,
plant-tower-defense:G-051). Every line above is about `size` being too small without
`instantiate_ui`; the trap on the other side is that after the settle frames a
Control's `size` is clamped **up** to `get_combined_minimum_size()`, and a Label's
minimum is its font: `heading.size = Vector2(720, 40)` under a 30px font lands as
`(720, 42)`, so `assert_eq(heading.size, Vector2(720, 40))` is asserting the theme's
font metrics as much as the code's layout — and goes red the next time the heading
font moves. `_T.assert_box(control, Rect2(pos, size), context)` is the assertion that
is actually true of every text-bearing Control: position exact, and per axis
`size == max(assigned, combined minimum)`; on failure it names the axis and says
"assigned 40 clamped up to combined minimum 42". Stage 4 plants a 30px Label assigned
`(200, 10)`, proves exact equality fails, and asserts `assert_box` passes it and refuses
a moved position.

**`set_physics_process(false)` before `add_child()` does not stick** (0.39.0,
plant-tower-defense:G-050b). Godot re-enables physics/process at `NOTIFICATION_READY`
for any script declaring `_physics_process`/`_process`, so a node quiesced before
hosting has already ticked by the first assertion (a `Dandelion` had fired a seed).
`_T.quiesce(node, physics := true, process := true, recursive := false)` **after**
`instantiate_scene()` is the call that holds; on a node not yet in a tree it warns and
does nothing rather than being silently undone. It holds from the moment it is called —
the two settle frames have already run and a physics tick lands in them, so a cooldown
or spawn counter that must be pristine is reset by the test after `quiesce()`, not
assumed. Stage 4 hosts two tickers — one disabled before hosting (must tick: that is
the mechanism; it ticked 4 times), one `quiesce()`d after (0 further ticks).

`_T.text_width(label: Label) -> float` (gh#20 / plant-tower-defense:G-033) measures the
widest line of a Label's text under its own resolved theme font. Use it, not
`get_minimum_size()`, when a test asks "does this text fit its box" — `get_minimum_size()`
returns ~1px on any Label with `clip_text` or a non-default `text_overrun_behavior`, so
the obvious width assertion passes unconditionally on exactly the labels worth checking.
Same measurement `dev_tools.gd`'s `ui_text_trimmed` finding already does internally,
exposed here so a test can do it too.

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

Two verdicts that used to point at the wrong cause (0.20.0):

- **A selected script that fails to compile is blamed on the compile, not the
  selector** (gh#10). It never reaches discovery, so it contributes 0 to `M` and the
  selector "matches nothing"; the verdict used to be three lines of `--filter`/`--file`
  syntax advice while the parse error sat 60 lines up. It now reads
  `SELECTED NOTHING - 1 selected test script(s) FAILED TO COMPILE ... Fix the parse error
  first: res://test/unit/<file>.gd`, and the JSON carries `selected_load_failures`.
- **A never-imported project is refused, not passed** (H-029). With no
  `.godot/global_script_class_cache.cfg` no `class_name` resolves, so a test whose first
  statement uses one aborts on a runtime error — which is a pass to this runner. It now
  exits `2` up front with one line naming `godot --headless --path . --import`.

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
- **`press` fires `pressed`; it does not move the mouse.** (plant-tower-defense:G-020)
  A tooltip already open stays open and renders over whatever the press created,
  because a real click cancels the tooltip as part of the mouse event and the
  bridge's emit does not. A screenshot taken straight after `press` can therefore
  contain a popup a player would never see. If the picture matters, `mouse-move` onto
  the button first, or take the screenshot a frame after moving the pointer away.
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
- **`node-bounds` on a non-`Control` derives the extent, and `0x0` means "unknown".**
  The rect is always the canvas transform applied to whatever the node can report
  about itself. When it can report nothing, the rect is a correct origin with a
  **zero size** — "where on screen is this" answered, "how big is it" not. That is not
  the same claim as "this node is zero-sized", and `data.size_source` is what tells
  them apart, and it now names the transform outright rather than paraphrasing it
  (`get_global_transform_with_canvas() x Control.size (screen space)`,
  `… x Sprite2D texture rect (screen space)`, `… x origin only (this class reports
  no extent)`). `data.canvas_scale` carries the accumulated canvas scale alongside
  the rect, so a layer-scale problem is diagnosable from this one call instead of a
  second trip through `canvas-scale`.
- **`aabb` excludes `Light3D`, and that exclusion is load-bearing.** `Light3D` is a
  `VisualInstance3D`, so a naive geometry walk includes it — and an `OmniLight3D`'s
  AABB is a cube of *twice its range*, not its visible size. A ceiling lamp with a
  light child measured 7.2 x 7.2 units instead of 0.2 in a real project, which
  silently corrupted every "what is the top of this" computed from it. `aabb` also
  skips zero-size `GeometryInstance3D`s (a mesh-less `MeshInstance3D`, an unbuilt
  CSG) rather than letting their origin drag the box, and non-geometry
  `VisualInstance3D`s such as `Decal` and `ReflectionProbe`. Everything skipped is
  listed in `data.excluded` with its reason, so a suspiciously small `merged_count`
  is traceable rather than mysterious. Children *under* an excluded node are still
  walked — a mesh parented to a lamp is still geometry.
- **`aabb` on a node with no geometry FAILS.** It does not return a zero box. A zero
  AABB at the origin is indistinguishable from a real measurement of a small object
  at the origin, and that is exactly the kind of plausible-looking wrong number this
  harness exists to refuse. `GPUParticles3D` is merged rather than excluded: it is
  genuinely geometry, but its AABB is a *visibility* volume and is often deliberately
  generous, so read `merged` if a particle-bearing node measures large.
- **A rotated node's `aabb` encloses the footprint, it is not the footprint.** The box
  is axis-aligned by definition; `data.node_transform.axis_aligned` is `false` when
  the node is rotated off-axis, and the CLI says so on the rotation line. Comparing
  two enclosing boxes overstates overlap for anything turned off the grid.
- **`look-at` orients, it never moves anything** (0.26.0, moving-in:G-044,
  seen twice the same day). Framing a fixture for a screenshot used to mean guessing
  a heading in degrees — four blind attempts (a wall, a hallway, the kitchen) on one
  real run. `look-at --node PATH [--from-node PATH] [--up X,Y,Z]` calls
  `Node3D.look_at()` on `--from-node` (default: `get_viewport().get_camera_3d()`, the
  active camera — no project knowledge needed to find "the camera") toward the
  target's world-space AABB centre (the same measurement `aabb` reports), falling
  back to the target's own `global_position` when it has no geometry to merge (a
  spawn marker, an empty `Node3D`). Deliberately does not reposition `--from-node` —
  "a sensible standoff" is exactly the kind of guess this verb exists to remove, and
  the reported blocker was always heading, never position. Refuses a 2D target or a
  2D `--from-node` outright: `look_at()` has no 2D equivalent.
- **A wedged handler still costs the caller its timeout.** GDScript has no catchable
  exception: a runtime error raised by *project* code reacting to a verb (a setter, a
  signal, an `Area` `body_entered`) kills the handler before it can reply, and the
  game survives, so the verb looks selectively broken while every later verb answers
  normally. The client says so and points at stderr; the dispatch watchdog releases
  the bus and now writes a failure reply rather than only a log line. `[SCRIPT ERROR]`
  on the game's stderr is what names the line.
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
  each other's commands *before the game ever sees them* — a second client's write
  overwrites the first's command file — and request ids make the resulting crossed
  reply an error rather than silent corruption without making it safe. Drive one bus
  from **one** client at a time, and give genuinely parallel instances separate buses
  with `--session` (above). What the 0.16.0 re-entrancy guard fixes is the *game* side
  of this: whatever commands do reach the game are now served strictly one at a time,
  so a second client (or a client that gave up on a slow verb and sent another
  command) can no longer get two handlers running in the same scene tree. That is a
  narrower guarantee than a safe multi-client bus, and it is not one.
- **A slow verb blocks the bus for its whole duration** (0.16.0). This is the intended
  consequence of the guard above, but it changes what a client-side timeout means:
  the command you sent may never have started. `devtools.py` now says which case it
  hit — "was never picked up, but the game is ALIVE and busy: it is still inside
  `<verb>`" — and withdraws the queued command rather than leaving it to fire minutes
  later against changed state. `step_time` is capped at 60 s for this reason.
