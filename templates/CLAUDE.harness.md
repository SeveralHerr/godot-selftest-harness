<!-- BEGIN godot-selftest-harness -->
## Self-Test Harness (godot-selftest-harness)

This project ships a **self-test harness**: a file-based DevTools bridge (control a
running game from the CLI), headless lint + unit-test runners (no game needed), and a
diff-aware **`/verify`** pre-commit gate. It is game-agnostic; project-specific behavior
is discovered at runtime or read from the config file below.

### DEVELOPMENT RULE (REQUIRED)
After **any** gameplay, script, or scene change, run **`/verify`** before considering the
work complete — don't wait for a commit request. It runs lint + tests, launches the game
muted, and asserts your actual diff at runtime (catching errors lint/tests can't).
Headless lint and unit tests need **no running game**; run them anytime:

```bash
python tools/name_check.py                                       # names only — no engine at all
godot --headless --path . --script res://tools/lint_project.gd   # UID + scene + dup-id lint
godot --headless --path . --script res://tools/run_tests.gd      # unit tests (test_dir)
godot --path . --script res://tools/capture.gd -- --scene res://ui/hud.tscn --out shot.png
```

**`capture.gd` screenshots one scene without the bridge or a running game** — note the
missing `--headless`. Headless has no renderer (`root.get_texture()` is null), so a
headless run exits `2` naming the fix rather than writing a blank PNG. Flags:
`--scene` (default: main scene), `--out`, `--frames N` (default 3 — **two is the floor**
for `Control`s, since `@onready` and container sizing happen on the first frame),
`--size WxH`, `--fail-on-uniform`. Every run prints the **distinct colours sampled**;
a `WARNING: single flat colour` means the scene may have drawn nothing. For a *live*
session mid-play use the bridge's `screenshot` verb instead.

**`name_check.py` is the only gate that is safe to run in parallel.** It resolves types,
`class_name`s, autoloads, `preload("res://…")` targets, engine classes/members and string
method names from source plus a cached engine API index — it opens no project and writes
nothing to `.godot/`, so N agents can run it at once on one checkout. It is also the only
Phase 1 gate that works in a **fresh worktree**: with no class cache, lint reports a
thousand `Identifier "X" not declared` errors and still exits `0`, and the test runner
prints `[PASS]` for tests whose first statement errored. Exit `0`/`1`/`2` as below. Flags:
`--only PREFIX` (report just your files; the whole project is still scanned so cross-file
names resolve), `--strict`, `--json`, `--baseline PATH` / `--baseline-write PATH`,
`--no-strings`. If it prints `engine index: NONE` the engine-name half was **skipped, not
passed** — run `python tools/name_check.py --refresh-api` once (Godot in a temp dir, no
project, safe alongside other agents); `--require-api` makes a missing index an exit `2`.

Exit codes (both): `0` pass, `1` findings, `2` **the runner couldn't run** — a `2` means you
verified nothing. Redirect to a file and read it back; the Windows Godot build often prints
nothing to the console, so a failed run looks like silent success.
Test flags (after `--`): `--filter NAME` (matches method name **or** test script filename),
`--file NAME` (one script; combines with `--filter` via AND), `--json`. **Every** run prints
`Selected: N of M discovered`, `Autoloads: N of M ready` and `Assertions: N executed` — read
those lines, not just the exit code. `Total: 0 | ALL TESTS PASSED` is this harness's worst
failure mode and those three denominators are what make a silent zero visible. A selector
matching nothing is exit `2` (`SELECTED NOTHING — …`), and a suite with no `test_*` methods is
exit `2` as well; neither is a pass.
A test that returns pass having executed **none** of its own `_T.assert_*` calls prints
`[VACUOUS]` and fails the run. The usual cause is a loop over a collection that was empty —
an empty collection satisfies every assertion inside a loop over it. Fix the data, not the
test. (`Autoloads:` exists because that empty collection is usually an autoload: `--script`
mode parents autoloads to root but does not step the tree, so `_ready()` had not run. The
runner awaits a frame first, so what you test is what ships.)
`UIDs: OK` covers both halves: no stale `uid=` reference **and** no `.gd` missing its
`.uid` sidecar. A script you just wrote outside the editor has none — commit the sidecar
Godot generates alongside the script.
Lint flags (after `--`): `--strict` (warnings fail), `--baseline-write PATH` /
`--baseline PATH` (split findings into `NEW` vs `PRE-EXISTING` so repo debt isn't re-triaged
by hand), `--find-orphans` (public functions called only from tests — advisory),
`--no-shaders` (skip the shader pass).
Lint also **compiles every `.gdshader` and every `Shader` embedded in a `.tres`** and prints
`Shaders: N of M compiled OK (X file, Y embedded)`. Read the denominator: `Shaders: none
found` means there are no shaders, not that shaders passed, and `.gdshaderinc` files are
reported as *skipped* (no `shader_type`, so they're checked through their includers). This is
the only gate for a broken shader — the scene holding one loads clean, lints clean and tests
green, and shows magenta only on screen.

**Writing tests.** Alongside `_T.assert_*`, use `await _T.instantiate_ui(scene, Vector2i(w, h))`
/ `_T.free_ui(node)` for anything `Control`-shaped: headless pumps no frames, so without it
`size` stays `(0, 0)` and `@onready` vars never initialize. Test methods may `await`.
**Always read stderr**: a runtime error inside a test aborts only that method and returns
`""` for a `-> String` test — identical to a pass. `[ERR]` lines are the only signal.

### DEVTOOLS LOG (REQUIRED)
At the end of **every** response, append an entry to `log-devtools.md` (create it if
missing). Two required halves: **was using the harness worth it**, and **what was
missing from it**. If nothing was missing, write one explicit "no gaps this turn" line —
that is what makes an absent gap distinguishable from a forgotten log. The `Value:`
block is required either way.

```markdown
## YYYY-MM-DD — <what this response did>

- Value: **<warranted|overkill|insufficient|inconclusive>** — <one sentence of why>
  - Expected: <what you predicted runtime would reveal, written before running it>
  - Got: <what it actually told you — quote the assertion, not "it passed">
  - Found: <what this run caught that reading the diff would not have, or "nothing">
  - Cheaper: <the cheapest thing that would have given the same confidence>

- Gap: **<what was missing>** — <the command run, the output it gave, the workaround used>
  - [G-001] status: open | seen: 1 | harness: 0.7.0
  - Improvement: <the smallest change that would have closed it>
```

`warranted` = runtime produced a claim the diff could not (name it). `overkill` =
everything passed and confirmed what was already known — renames, comments, pure
refactors, anything lint alone settled. `insufficient` = it ran but never reached or
asserted what mattered (**reach decides this, not your impression**); file the gap.
`inconclusive` = aborted or too small to judge.

**`overkill` is a useful entry, not an admission.** It is also the one that goes
unwritten, because a run that passed feels like a run that helped. `Cheaper:` must name
something concrete — "reading `player.gd:40-60`", "lint alone, 4s", "nothing, this needed
the running game". "Probably still worth it" is not an answer.

**`Found:` counts a bug you fixed mid-run.** Every other field describes how the run
*ended*, so a defect surfaced at minute four and repaired by minute six vanishes: the
checks end green, the runners end clean. Write it here or it is not recorded anywhere.
"nothing" is the honest answer for a run that confirmed what you already knew, and Phase 5
turns a `warranted` with nothing found into `overkill` automatically — so padding it buys
nothing.

The `[G-NNN]` line is required and is what makes the log answerable: ids are stable and
never reused, `status:` is `open`/`fixed`/`wontfix` (`fixed` adds `fixed-in: X.Y.Z`),
`harness:` comes from `python tools/devtools.py harness-version`. **Hitting a known gap
again bumps its `seen:` count** — don't file a second entry for it. `tools/upstream_gaps.py`
reads exactly these fields to pool open gaps into the harness repo.

Quote real output; a gap without evidence can't be acted on later. This log is the
harness's feedback channel — entries here are what get upstreamed into
`godot-selftest-harness` itself, so a gap logged here becomes a fixed feature for every
project using it. A `Stop` hook (`tools/check_devtools_log.py`, wired in
`.claude/settings.json`) prints a reminder when a session changes code without touching
the log; it is advisory, not a gate.

### THE VERIFY LEDGER
`/verify` Phase 5 appends one line per run to `.devtools/verify-runs.jsonl` — including
the clean ones, which is the point. The gaps log records what the harness couldn't do;
the ledger is the denominator it lacks.

The field worth reading is **reach**: computed by intersecting the diff against the
`script`/`scene_file` paths in a `scene-tree` snapshot, so it says whether a run actually
loaded the code it claimed to verify rather than asking the run to grade itself. A pass
on an unreached file is a statement about the diff, not the running game — report it that
way. Each row also carries the `value` verdict above and **`found`** — the list of what
the run caught, `[]` when it caught nothing — so both "how often was this overkill?" and
"how often did it tell me something?" are queries rather than reading exercises. A Phase 4
check that failed and was fixed keeps `"result": "fail"` with `"fixed_in_run": true`;
rewriting it green erases the run's own evidence. `python tools/verify_ledger.py stats`
reads the history back; `reach` computes reach alone without writing a row. Commit the
ledger.

### Command cheat-sheet (`python tools/devtools.py <verb>`)
Launch first: `godot --path . --mute &` then `sleep 5 && python tools/devtools.py ping`.

| Verb | Use |
|---|---|
| `ping` | Confirm the bridge is live — reports which session answered, plus `bus_dir` and `user_dir` separately, and `tree is PAUSED` when it is. **The bridge answers while paused** (`PROCESS_MODE_ALWAYS`), so pause menus / settings / death screens are verifiable like any other UI |
| `quit` | Shut the game down; waits for the process and **exits 1 if it survived**. A survivor answers the bus alongside the next instance, and the symptom is empty replies, not an error |
| `scene-tree [--root PATH] [--depth N]` | Discover root scene name + node paths (don't assume names). Each node carries `script` and `scene_file`, so a changed file maps to the node that runs it. `--root` lists a deep subtree that would otherwise truncate |
| `find-nodes [--class C\|--group G\|--method M] [--where N=V] [--property N] [--root PATH]` | Locate nodes by what they *are*, not where they sit. `--where` is repeatable and takes dotted paths (`--where slot_data.item.name='Iron Bar'`). Usually the right verb for identifying one node in a large tree |
| `get-state --node PATH [--property N ...]` | Read a node's properties. **Always pass `--property`** — an unfiltered `Label` is ~120 keys. Repeatable; dotted paths walk into Resources and Dictionaries (`texture.region`, `slot_data.item.name`); unknown names are reported, not dropped |
| `set-state --node PATH --property N --value V` | Set raw property (bypasses setters/signals) and print the read-back. Dotted paths write through, same as `get-state` reads through (`environment.ambient_light_energy`) — note that mutates the **Resource**, so a shared material changes for every node using it. Vectors take `[x,y]`, `x,y` or `(x,y)` — write `--value=-200,-296` with an `=` when it starts with `-`, or argparse reads it as a flag. A struct component (`size.x`) is refused; set the struct whole |
| `run-method --node PATH --method N --args "[...]" [--json]` | Call a method — preferred when a signal should fire. Reports `returned_null` + `declared_return`, so a `-> void` that ran is distinguishable from a call that aborted. `--json` for a pipeable envelope |
| `press --node PATH [--toggle BOOL]` | Emit `pressed` on the nearest `BaseButton` at or under PATH — a real button press with no screen coordinates to guess. A disabled button is reported, not silently "pressed" |
| `curve --node PATH --method M --from A --to B [--step N]` | Call a pure method over an integer range and get the whole series — a difficulty ramp as one read. Capped at **500 points** |
| `raycast --from X,Y --to X,Y [--mask N] [--areas]` | What a ray would hit, with collision-layer names resolved. A ray that **starts inside** a shape reports nothing |
| `sample-pixels [--rect X,Y,W,H]` | Mean + dominant colour over a screen rect — "is it still on fire?" as numbers rather than a PNG to open |
| `reachable-ui` | Every Control a finger/cursor could actually hit now; unreachable ones are listed `OFF-SCREEN` or `BLOCKED BY <path>`, not dropped. Diff it across `set-feature --touchscreen true\|false` to catch an affordance that exists on one device only — `validate-ui` reports 0 issues for that, correctly |
| `node-bounds PATH` | Exact **screen-space** position/size (deterministic layout ground truth). Ancestor `CanvasLayer` transforms are applied, so a HUD on a scaled layer reports where it renders, not layer units |
| `canvas-scale --node PATH` | Accumulated canvas scale + effective texture filter — the crisp/blurry question as one read |
| `set-resolution --size W,H` | Resize the window (honest read-back; headless may clamp) |
| `ui-snapshot` / `ui-snapshot-diff` / `save-ui-baseline` | Structured UI state vs baseline |
| `validate --scene S` / `validate-all` | One scene / every scene validation (expect 0 issues) |
| `validate-ui [--baseline-write] [--no-baseline]` | UI layout validation. Findings split `NEW` vs `PRE` against `user://ui_findings_baseline.json` (keyed on rule + node path); **only NEW ones fail**. `--baseline-write` accepts the current set — read them first, a blind write is deleting the check. A popup resting at alpha 0 or a world-space HUD is the case this exists for |
| `performance [--reset-baseline]` | FPS vs `fps_min`, orphan **growth** vs `orphan_growth_max` |
| `input press` `input release` `input tap` ACTION | Simulate input actions. `tap` releases on the NEXT frame and replies after the release, reporting `pressed_during`/`pressed_after` |
| `input clear` / `input list` / `input sequence FILE` | clear everything currently held / list the project's actions / replay a JSON sequence file |
| `input state [ACTION ...]` | Polled pressed/strength per action (all project actions when none named) — what the game is actually seeing |
| `key NAME [--count N] [--hold-frames N]` | Raw `InputEventKey` by OS keycode name (`E`, `LEFT`, `SPACE`) — for game code reading keys directly instead of actions |
| `touch press` `touch release` `touch drag` `--index N --pos X,Y` | Real `InputEventScreenTouch`/`Drag` — the only way to exercise multi-touch. A drag needs `--to`; `touch clear` / `touch list` cover the held points |
| `set-feature --touchscreen true` | Makes touch UI show itself on desktop (it hides when no touchscreen is reported). Set it **before** the scene loads. `--query` reads the flags without writing |
| `set-game-speed N` / `wait-frames N` | Speed up / advance N physics frames |
| `step-time --seconds N [--hold ACTION]` | Advance ~N game-seconds (**60 max** — longer waits are a `wait-frames` loop or `set-game-speed`) with `time_scale` pinned to 1.0. Physics exact; process tweens land ±1 frame — it does not pause and step the tree. `--hold` keeps an action pressed across the step and releases it at the end |
| `tilemap-cells --node PATH [--layer N] [--rect X,Y,W,H]` | Used cells with source/atlas ids as data (capped at 2000; pass `--rect`) — not a screenshot guess |
| `tilemap-region --node PATH --atlas X,Y [--layer N] [--source-id N]` | 4-neighbor connected components of matching cells, largest first — "is this island one landmass?" as data |
| `scripts-seen [--json]` | Every distinct script path that has entered the tree since launch; `--json` prints the full reply envelope |
| `launch [--isolated] [--no-mute] [--no-wait] [-- GODOT ARGS]` | Start the game detached (logs under `.devtools/`), waiting until the bus answers. `--isolated` = private session **and** bus dir, verified before the follow-up command prints. Everything after a bare `--` is forwarded to Godot (`-- --write-movie out/f.png --fixed-fps 30`, which needs `--no-mute`). It refuses a bus a live pid already owns unless `--allow-second-instance` |
| `clear-nodes --group G` (or `--method`/`--class`) `[--via-method NAME]` | Free matching nodes. `queue_free()` skips the game's own removal path, so a cleared enemy drops nothing and pays no xp — `--via-method die` runs it instead |
| `new-uid [--count N] [--write PATH]` | Emit a valid `uid://`, collision-checked against the project's existing sidecars. No game, no editor, no import — the way to give a script you just wrote its `.uid` |
| `screenshot [--region X,Y,W,H] [--hide NODE] [--hide-group G]` | Visual check only (`sleep 0.5`–`1` after a state change). Crop and hiding happen game-side inside one command, so a capture is reproducible and can't leave the HUD switched off |
| `list-commands` | Discover all registered verbs (generic + project). `--offline` statically parses the scripts when no game is running |
| `logs --tail N [--category C]` | Read the game's JSONL debug log directly (no bus call; works on a hung game) |
| `harness-version` | Installed harness revision (game + client). Read it once per session — it fills the `harness:` field on every gap you log. Exits 1 on a mismatch, which means a half-refreshed install |
| `cmd <verb> --args '{...}'` | Invoke any project-registered verb |

### Add project-specific debug verbs
Register domain verbs in `res://devtools_ext/commands.gd` (loaded after generic verbs,
last-writer-wins). Each handler returns exactly `{success:bool, message:String, data:Dictionary}`.

```gdscript
func register_commands(dev: Node) -> void:
    dev.register_command("spawn_enemy", func(args): 
        return {"success": true, "message": "ok", "data": {}})
```

Reach them from the CLI via `cmd spawn_enemy --args '{"count":3}'`; discover them via
`list-commands`. Use these for setup/trigger steps the generic primitives can't express.

**Attach liveness to every reply.** Register one status provider and its Dictionary is
merged into *every* response as `status` — the fact you need on every read and never
remember to ask for separately. Without it, a session that has silently died or frozen
keeps answering with well-formed zeros, which looks exactly like a clean pass.

```gdscript
    dev.register_status_provider(func(_args):
        var p = dev.get_tree().get_first_node_in_group("player")
        return {"player": "absent"} if p == null else {"player": "dead" if p.is_dead else "alive"})
```

Pair it with verbs that can *undo* the dead state (a `revive_player` that clears the
flag and leaves the death state, or a `god_mode` toggle). Restoring a health value is
usually not enough on its own — the death flag and state machine outlive it, so the
run stays frozen and unrescuable short of a relaunch.

**A setter verb must leave the game in a state the game itself can reach.** Writing one
half of an invariant pair is a latent trap — a `set_combo` that sets the count but not
the combo window tests nothing the moment the readout starts fading on that timer.

### Gotchas
- **One command at a time, enforced.** The bus is one command file / one result file.
  Requests carry an id the game echoes, so a crossed reply errors (`Crossed replies: …`)
  instead of silently returning another request's data — detection, not concurrency.
  A command sent while a handler is still running (`step_time`, `input_tap`, a project
  verb that awaits) waits on disk and runs when that handler returns; it is deferred,
  never dropped and never run alongside. So a timeout can now mean *your command never
  started* — the error says which, and naming the verb that is hogging the bus.
  For *parallel* instances give each its own bus: `launch --isolated`, or launch with
  `-- --devtools-session <id>` and call with `--session <id>`. That isolates the **bus
  only** — Godot has no switch for `user://`, so saves, screenshots, UI baselines and the
  `.godot/` import cache stay shared; `ping` reports `bus_dir` and `user_dir` separately so
  the difference is a read, not a guess. Add `GODOT_USERDATA` per instance to isolate fully.
  For parallel *validation* rather than parallel play, `python tools/name_check.py` needs
  none of this — it never opens the project.
- **`game not running` in ~2s** means a dead game *or* the wrong `user://` dir; the
  error can't tell them apart. Check `--userdata` before assuming a crash.
- **Assert transforms on `data.transform`, not the property dump.** Godot hides
  `position`/`scale`/`rotation` on container children, so a scale animation on a
  `VBoxContainer` child is invisible to a property read while working on screen.
- **A run that never changes is broken, not passing.** Check the `status` field.

### Config
`res://addons/godot_selftest/devtools_config.json` holds thresholds and hooks:
`fps_min`, `orphan_growth_max` (gate on this — `orphan_max: 0` is unreachable),
`safe_area_inset`, `mute`, `main_scene`, `entry_hook {node_path, method}` (advances past
a menu into the playable scene), `entry_points` (named alternates for scenes the default
hook can't reach), `test_dir`, `scan_root`, `hud_layer_name`, `name_check_extra_types`
(types a GDExtension registers at runtime, which the static checker cannot see) and
`name_check_ignore` (path prefixes it should skip).

### Token-aware
- Prefer `node-bounds` / `ui-snapshot` (compact, deterministic) over `screenshot`; only
  open a screenshot PNG when a genuine **visual** regression is suspected.
- `get-state` dumps ~120 keys for a `Label` — pass `--property NAME` (repeatable).
- Run `/verify` **inline**; don't wrap routine validation in subagents/workflows.
- Launch with `--mute` for automated testing.
- On Windows, probe Python by running it (`python3` may be a Store alias stub that
  exists and refuses to run).

### (Re)install
Run **`/scaffold-godot-harness`** to install or refresh the harness. Re-running it also
refreshes this very section in place (it never duplicates it).
<!-- END godot-selftest-harness -->
