<!-- BEGIN godot-selftest-harness -->
## Self-Test Harness (godot-selftest-harness)

This project ships a **self-test harness**. Two things it does that a test suite you
write cannot: it knows a checklist of ways Godot games break and applies it with no
assertions from you, and it reports on *your* checks — which classes of defect this
project never asks about. Under that sits a bridge for driving the running game, and
a `/verify` gate. It is game-agnostic; project specifics come from the config below.

### START HERE: the findings report

```bash
python tools/devtools.py findings          # needs a running game; add --no-scenes to skip the slow scene pass
python tools/coverage_check.py             # static, no game, no engine, safe in parallel
```

`findings` runs every zero-config check at once against the live tree — offscreen and
zero-size Controls, unreachable/blocked interactive Controls, signals a script declares
and nothing connects, orphan growth and FPS against config thresholds, and scene
validation across `scan_root` — and returns one flat findings list. Each check calls
the same implementation the standalone verb does, so `findings` can never disagree
with `validate-ui` / `reachable-ui` / `performance` / `validate-all` about a scene.

**Read the denominator it prints** — `N finding(s) across K of M checks` — and read the
skipped checks: a consolidated report is the easiest place for a check to vanish from,
so one that could not run is named with a reason instead of disappearing into a clean
result. A check that ran and found nothing is a `0` in the `By check:` line, not an
absent one. UI findings are split NEW vs PRE against `user://ui_findings_baseline.json`
and only NEW ones gate. Exit `0` clean, `1` gating findings, `2` could not run — which
includes a reply missing a key, reported as unreadable rather than as a result.

`coverage_check.py` answers the other question — not *did the checks pass* but *which
questions do the checks ask at all*. A suite asserting 70 things that never once reads
a Control's screen rect prints exactly what a thorough one prints. It names the defect
classes nothing in this project exercises (`ui_layout`, `ui_reachable`,
`signal_unconnected`, `orphan_growth`, `input_path`, `scene_validation`,
`shader_compile`, `name_resolution`) and, for every class it calls covered, prints the
**file:line and token** that convinced it — read that, don't trust the verdict.
`COVERED (gate)` means an installed tool covers it; `COVERED (session)` means a past run
asked once, which is an observation, not a standing check. Advisory: exit 0 always,
`--strict` exits 1 on any unchecked class. **Coverage here is a floor, never a pass** —
a covered class means the question is asked, not that the answer was right.

When one of these fires, use the bridge below to reproduce and fix the specific case.

### Where the checks you write live

**`res://test/unit/test_selftest.gd` — add to it, don't start a new file beside it.**

When you verify a change, put the resulting checks there. `/verify` re-runs everything
in `test_dir` on every subsequent change, so a check written there is inherited by the
next session; the same check written into a scratch script or the transcript is worth
one run. Every test run prints `Suite: N test script(s) in <dir>` next to
`Assertions: M executed` — that pair is how much checking previous sessions left you.

Split by **what the check needs**: anything requiring a live playing game (real input
over time, physics, a tween landing, a scene mid-transition) stays a `/verify` Phase 4
bridge check. Everything else — pure logic, resources, data tables, and any layout
`_T.instantiate_ui` can resolve — belongs in `test_dir`.

**Writing them.** Alongside `_T.assert_*`, use
`await _T.instantiate_ui(scene, Vector2i(w, h))` / `_T.free_ui(node)` for anything
`Control`-shaped: headless pumps no frames, so without it `size` stays `(0, 0)` and
`@onready` vars never initialize. Test methods may `await`. **Always read stderr** — a
runtime error inside a test aborts only that method and returns `""` for a `-> String`
test, which is identical to a pass. `[ERR]` lines are the only signal.

**Testing "does this text fit its box"?** `Label.get_minimum_size()` returns ~1px on
any Label with `clip_text` or a non-default `text_overrun_behavior` — it reports the
clip stub, not the text, so the obvious width assertion passes unconditionally on
exactly the labels that need it checked. Use `_T.text_width(label) -> float` instead;
it measures through the label's own resolved theme font.

### DEVELOPMENT RULE (REQUIRED)
After **any** gameplay, script, or scene change, run **`/verify`** before considering
the work complete — don't wait for a commit request. Headless gates need no running
game; run them anytime:

```bash
python tools/name_check.py                                       # names only — no engine at all
godot --headless --path . --script res://tools/lint_project.gd   # UID + scene + dup-id + shader lint
godot --headless --path . --script res://tools/run_tests.gd      # unit tests (test_dir)
godot --path . --script res://tools/capture.gd -- --scene res://ui/hud.tscn --out shot.png
```

**Lint flags** (after `--`): `--strict` (warnings fail the run), `--baseline-write PATH` /
`--baseline PATH` (split findings into `NEW` vs `PRE-EXISTING` against a saved snapshot —
the number that means "this change" rather than "all repo debt"), `--no-orphans` (skip the
advisory unreferenced-function pass, on by default since 0.21.0), `--no-shaders` (skip
compiling every `.gdshader` and embedded `Shader`).

**After adding a new `class_name` file (or a new `.tscn`/`.tres`), run
`godot --headless --path . --import` once before the next lint/test pass.** The
class cache is built by import; until it is, every script that references the new
class fails to compile with `Could not find type "X"` — and it cascades into files
you did not touch, reading like a broad regression. Lint's `stale class cache` hint
names the fix after the fact; this is the note that says to do it *before*
(`/verify` runs `import_check.py` for you; a bare lint does not). Also mint a
`.uid` for a new `.gd`: `python tools/devtools.py new-uid --write path/to/file.gd`.

**Exit codes are `0` pass / `1` findings / `2` the runner couldn't run.** A `2` means
you verified nothing — not that the code is clean. Redirect to a file and read it back;
the Windows Godot build often prints nothing to the console, so a failed run looks like
silent success.

**Read the denominators, not just the exit code.** `Total: 0 | ALL TESTS PASSED` is
this harness's worst failure mode. Every test run prints `Selected: N of M discovered`,
`Autoloads: N of M ready`, `Assertions: N executed` and `Suite: N test script(s)`; lint
prints `Shaders: N of M compiled OK` (`Shaders: none found` means there are none, not
that they passed), `UIDs: OK` (no stale `uid=` **and** no `.gd` missing its `.uid`
sidecar) and `Orphans: N of M public function(s) ... have no live reference` (advisory,
never gates — but a public method whose only caller is nothing is a feature that cannot
run; read the `WARN:` lines it prints, especially for a method you just added). A selector matching nothing, and a suite with no `test_*` methods, are both
exit `2`. A test returning pass having executed none of its own `_T.assert_*` calls
prints `[VACUOUS]` and fails — usually a loop over an empty collection, so fix the
data, not the test.

**`name_check.py` is the only gate safe to run in parallel** — it opens no project and
writes nothing to `.godot/`, so N agents can run it at once and it works in a fresh
worktree where lint reports a thousand bogus `not declared` errors and still exits `0`.
**But a clean `name_check` is not a compile.** It resolves names; it does not
type-check. `var kids := root.get_children()` on a bare `Node` is a hard parse error
that `name_check` reports clean, because every name in it resolves. Only
`import_check.py` and `lint_project.gd` see that class, and neither is parallel-safe.
If `name_check` was the only gate you could run, hand the work back saying that — not
"verified". It prints this itself as a `NOT COVERED:` line. If it prints
`engine index: NONE` the engine-name half was **skipped, not passed**; run
`python tools/name_check.py --refresh-api` once. `--require-compile FILE [FILE...]`
closes the gap for named files without losing parallelism — one `godot --check-only`
per file, verified read-only against `.godot/` — but needs the project imported once
already, or a cross-file `class_name` false-positives as unresolved.

**`capture.gd` must NOT be run headless** — note the missing `--headless` above.
Headless has no renderer, so it exits `2` naming the fix rather than writing a blank
PNG. Use `--frames N` (default 3; **two is the floor** for `Control`s). For a *live*
session mid-play use the bridge's `screenshot` verb instead.

Flags for all of these are in `REFERENCE.md`.

### The bus: driving the running game

Launch first: `python tools/devtools.py launch` (or `godot --path . --mute &` then
`sleep 5 && python tools/devtools.py ping`).

Measured across real sessions: 1192 verb calls used 25 of ~48 verbs, the top ten were
92% of all calls, and `get-state` alone was 44%. Those are below. **The rest are in
`REFERENCE.md`** — or run `list-commands`, which discovers generic and project verbs at
runtime and prints each verb's arg keys (`place_plant  args: plant, x, y` — a key not
listed is silently ignored; `--offline` parses the scripts statically with no game running).

| Verb | Use |
|---|---|
| `get-state --node PATH [--property N ...]` | Read a node's properties. **Always pass `--property`** — an unfiltered `Label` is ~120 keys. Repeatable; dotted paths walk into Resources and Dictionaries (`slot_data.item.name`); unknown names are reported, not dropped |
| `scene-tree [--root PATH] [--depth N]` | Discover root scene name + node paths (don't assume names). Each node carries `script` and `scene_file`, so a changed file maps to the node that runs it |
| `find-nodes [--class C\|--group G\|--method M] [--where N=V] [--property N]` | Locate nodes by what they *are*, not where they sit. `--class` takes a script `class_name` too (subclasses included) and **fails on a name that is neither**; `--where` is repeatable and takes dotted paths. Usually the right verb for identifying one node in a large tree |
| `run-method --node PATH --method N --args "[...]"` | Call a method — preferred over `set-state` when a signal should fire. Reports `returned_null` + `declared_return`, so a `-> void` that ran is distinguishable from a call that aborted |
| `set-state --node PATH --property N --value V` | Set raw property (bypasses setters/signals) and print the read-back. Dotted paths write through — note that mutates the **Resource**, so a shared material changes for every node using it. Write `--value=-200,-296` with an `=` when it starts with `-` |
| `node-bounds PATH` | Exact **screen-space** position/size — deterministic layout ground truth, ancestor `CanvasLayer` transforms applied. Prefer this over a screenshot |
| `press --node PATH` | Emit `pressed` on the nearest `BaseButton` at or under PATH — a real press with no screen coordinates to guess. A disabled button is reported, not silently "pressed" |
| `input press`/`release`/`tap` ACTION, `input state [ACTION ...]` | Simulate input actions; `state` polls what the game is actually seeing. `tap` releases on the NEXT frame and reports `pressed_during`/`pressed_after` |
| `screenshot [--region X,Y,W,H] [--hide NODE]` | Visual check only (`sleep 0.5`–`1` after a state change). Crop and hiding happen game-side, so a capture is reproducible |
| `ping` / `quit [--kill]` | Confirm the bridge is live (reports `bus_dir`, `user_dir`, and `tree is PAUSED`; **the bridge answers while paused**, so pause menus are verifiable) / shut down, **exiting 1 if the process survived** — or if any earlier launch of this project is still alive (`.devtools/launched.jsonl`); `--kill` terminates exactly those pids, never by image name. On Windows the printed fallback is `Stop-Process -Force -Id` (PowerShell) — `taskkill /F` through Git-Bash becomes `F:/` and fails |

Worth knowing exists, reach for `REFERENCE.md` when you need them — `validate-ui`,
`reachable-ui`, `performance`, `validate --scene`, `validate-all` (all folded into
`findings`, and worth calling alone only to re-check one thing after a fix);
`first-frame` (visible `CanvasLayer`s in paint order, the topmost on-screen Control,
paused state, cursor mode — "what IS the screen showing", not "is anything wrong");
`save-ui-baseline`, `ui-snapshot`, `ui-snapshot-diff` (structured UI state vs baseline);
`aabb` (3D world-space bounds, `top_y`/`bottom_y`), `node-bounds`' 3D counterpart;
`look-at --node PATH` (points the active Camera3D, or `--from-node`, at a target's
AABB centre — orientation only, never repositions);
`step-time`, `set-game-speed` (refuses a scale below 0.01 — that is a freeze, not a
speed; use `pause`/`unpause` for a real freeze), `wait-frames` (advance time
deterministically), `pause`/`unpause` (sets `SceneTree.paused` directly, bus keeps
answering — catch a sub-second effect, poll for the moment, pause, then inspect at
no rush);
`raycast --from X,Y[,Z] --to X,Y[,Z]` (2D or 3D by arity; refuses a 2D ray on a
3D-only tree), `sample-pixels`, `canvas-scale`, `set-resolution`;
`tilemap-cells`, `tilemap-region`; `curve` (a pure method over a range as one read);
`input clear`, `input list`, `input sequence FILE`, `key NAME`,
`mouse-move --relative DX,DY [--steps N]` (a real `InputEventMouseMotion` — the
only way to drive mouse-look; a captured cursor makes your physical mouse a second
input source between commands);
`reload res://path` (re-read an edited shader/.tres/texture into the running game —
holders see it, no relaunch);
`touch press`/`release`/`drag`/`clear`/`list` (the only way to exercise multi-touch);
`set-feature --touchscreen` (makes touch UI show itself on desktop — set it *before*
the scene loads); `clear-nodes --via-method` (free nodes through the game's own removal
path); `scripts-seen`, `new-uid`, `logs`, `harness-version`, `cmd <verb>`.

#### Gotchas
- **One command at a time, enforced.** One command file / one result file. Requests
  carry an id the game echoes, so a crossed reply errors instead of silently returning
  another request's data. A command sent mid-handler waits on disk and runs after —
  deferred, never dropped, never concurrent. So a timeout can mean *your command never
  started*; the error says which, naming the verb hogging the bus. For parallel
  instances use `launch --isolated`, which isolates the **bus only** — `user://` (saves,
  screenshots, UI baselines, `.godot/`) stays shared unless you also set `GODOT_USERDATA`.
- **`game not running` in ~2s** means a dead game *or* the wrong `user://` dir; the
  error can't tell them apart. Check `--userdata` before assuming a crash.
- **Assert transforms on `data.transform`, not the property dump.** Godot hides
  `position`/`scale`/`rotation` on container children, so a scale animation on a
  `VBoxContainer` child is invisible to a property read while working on screen.
- **A run that never changes is broken, not passing.** Check the `status` field.
- **`performance` FPS is a mean over a window** (`--frames N`, default 30, with min/max
  and `STILL SETTLING` when the halves disagree). Read after `wait-frames 60`+ past a
  settings change; a single frame's rate is not a measurement. Its `Total nodes …
  growth +N` is the leak signal the orphan count cannot see (in-tree accumulation);
  `--by-type` names which classes grew.
- **A headless gate never touches the bus.** `lint_project.gd` / `run_tests.gd` bring
  the autoload up passive: safe to run while another session drives this game.
- **A worktree sibling shares your bus.** Same project name → same `user://`. `ping`
  prints the answering game's `project` path and the client refuses to send to a game
  from another checkout — if you see `DIFFERENT checkout`, quit it or `launch --isolated`.

### Add project-specific debug verbs
Register domain verbs in `res://devtools_ext/commands.gd` (loaded after generic verbs,
last-writer-wins). Each handler returns exactly `{success:bool, message:String, data:Dictionary}`.

```gdscript
func register_commands(dev: Node) -> void:
    dev.register_command("spawn_enemy", func(args):
        return {"success": true, "message": "ok", "data": {}})
```

Reach them via `cmd spawn_enemy --args '{"count":3}'`. Use these for setup/trigger steps
the generic primitives can't express.

**Attach liveness to every reply.** Register one status provider and its Dictionary is
merged into *every* response as `status`. Without it, a session that has silently died
or frozen keeps answering with well-formed zeros, which looks exactly like a clean pass.

```gdscript
    dev.register_status_provider(func(_args):
        var p = dev.get_tree().get_first_node_in_group("player")
        return {"player": "absent"} if p == null else {"player": "dead" if p.is_dead else "alive"})
```

Pair it with verbs that can *undo* the dead state (a `revive_player` that clears the flag
and leaves the death state). Restoring a health value is usually not enough — the death
flag and state machine outlive it. And **a setter verb must leave the game in a state the
game itself can reach**: a `set_combo` that sets the count but not the combo window tests
nothing the moment the readout starts fading on that timer.

### DEVTOOLS LOG (REQUIRED)
At the end of **every** response, append an entry to `log-devtools.md`. Two required
halves: **was using the harness worth it**, and **what was missing from it**. If nothing
was missing, write one explicit "no gaps this turn" line — that is what makes an absent
gap distinguishable from a forgotten log. The `Value:` block is required either way.

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
everything passed and confirmed what was already known. `insufficient` = it ran but
never reached or asserted what mattered (**reach decides this, not your impression**);
file the gap. `inconclusive` = aborted or too small to judge.

**`overkill` is a useful entry, not an admission** — and it is the one that goes
unwritten, because a run that passed feels like a run that helped. `Cheaper:` must name
something concrete ("reading `player.gd:40-60`", "lint alone, 4s", "nothing, this needed
the running game"). **`Found:` counts a bug you fixed mid-run** — every other field
describes how the run *ended*, so a defect surfaced at minute four and repaired by minute
six vanishes otherwise. "nothing" is the honest answer for a run that confirmed what you
already knew.

The `[G-NNN]` line is required: ids are stable and never reused, `status:` is
`open`/`fixed`/`wontfix`, `harness:` comes from `python tools/devtools.py harness-version`.
**Hitting a known gap again bumps its `seen:` count** — don't file a second entry. Quote
real output; a gap without evidence can't be acted on. Entries here get upstreamed into
`godot-selftest-harness` itself, so a gap logged here becomes a fixed feature for every
project using it.

### The verify ledger
`/verify` Phase 5 appends one line per run to `.devtools/verify-runs.jsonl` — including
the clean ones, which is the point. The gaps log records what the harness couldn't do;
the ledger is the denominator it lacks.

The field worth reading is **reach**: the diff intersected against the `script`/
`scene_file` paths in a `scene-tree` snapshot, so it says whether a run actually loaded
the code it claimed to verify rather than asking the run to grade itself. A pass on an
unreached file is a statement about the diff, not the running game — report it that way.
Each row also carries the `value` verdict and **`found`** (`[]` when it caught nothing).
A Phase 4 check that failed and was fixed keeps `"result": "fail"` with
`"fixed_in_run": true`; rewriting it green erases the run's own evidence.
`python tools/verify_ledger.py stats` reads the history back. Commit the ledger.

### Config
`res://addons/godot_selftest/devtools_config.json` holds thresholds and hooks:
`fps_min`, `orphan_growth_max` (gate on this — `orphan_max: 0` is unreachable),
`safe_area_inset`, `mute`, `main_scene`, `entry_hook {node_path, method}` (advances past
a menu into the playable scene), `entry_points` (named alternates), `test_dir`,
`scan_root`, `hud_layer_name`, `name_check_extra_types` (types a GDExtension registers at
runtime, which the static checker cannot see) and `name_check_ignore` (path prefixes).

### Token-aware
- Prefer `findings` over a hand-built sweep of individual verbs; prefer `node-bounds` /
  `ui-snapshot` over `screenshot`. Only open a screenshot PNG when a genuine **visual**
  regression is suspected.
- **A `GEOMETRY CAVEAT` / `[HEADLESS geometry]` tag means the number is a headless
  measurement**: the window is 64×64 there, so anything the game positions from
  `get_window().size` sits off-viewport headless and centred for a player. Confirm an
  off-screen verdict windowed before reporting it as a defect.
- **`TREE IS PAUSED`** on `ping` / `performance` means every metric describes a game
  that is not stepping — unpause (or set `entry_hook`) before believing them.
- `get-state` dumps ~120 keys for a `Label` — pass `--property NAME` (repeatable).
- Run `/verify` **inline**; don't wrap routine validation in subagents/workflows.
- On Windows, probe Python by running it (`python3` may be a Store alias stub that
  exists and refuses to run).

### (Re)install
Run **`/scaffold-godot-harness`** to install or refresh the harness. Re-running it also
refreshes this very section in place (it never duplicates it).
<!-- END godot-selftest-harness -->
