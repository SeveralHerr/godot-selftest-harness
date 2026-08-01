---
description: Run runtime devtools verification on a Godot project (godot-selftest-harness). Use after modifying scripts, scenes, or gameplay before committing.
---

Run the Godot runtime verification workflow. Execute each phase sequentially, stopping on failures. This command is **generic** — it makes no assumptions about the game's node names, systems, or gameplay concepts. All project-specific behavior is discovered at runtime or read from `res://addons/godot_selftest/devtools_config.json`.

## Phase 0: Resolve Godot binary & config

**Resolve the Godot binary once, then use the resolved path everywhere below.** Do NOT hardcode a platform-specific path in the commands.

```bash
GODOT_BIN="${GODOT_BIN:-}"
if [ -z "$GODOT_BIN" ] && [ -x "/Applications/Godot.app/Contents/MacOS/Godot" ]; then
  GODOT_BIN="/Applications/Godot.app/Contents/MacOS/Godot"
fi
if [ -z "$GODOT_BIN" ]; then
  GODOT_BIN="$(command -v godot || true)"
fi
if [ -z "$GODOT_BIN" ]; then
  echo "ERROR: Godot binary not found. Set GODOT_BIN." >&2; exit 1
fi
echo "Using Godot: $GODOT_BIN"
```

Resolution order: `GODOT_BIN` env var → macOS default (`/Applications/Godot.app/Contents/MacOS/Godot`) → `which godot`. Because the shell does not persist between tool calls, re-run this resolution snippet (or inline it) in each Godot invocation below.

Read the config so you know the thresholds and hooks for this project:

```bash
python3 -c "import json; print(json.dumps(json.load(open('addons/godot_selftest/devtools_config.json')), indent=2))" 2>/dev/null || echo "{}"
```

Relevant keys: `fps_min` (default 30), `orphan_max` (default 0), `orphan_growth_max`, `mute` (default true), `main_scene`, `entry_hook` `{node_path, method}`, `entry_points` (see Phase 2), and `safe_area_inset`. If the file is missing, assume the defaults above.

**Python interpreter.** Every `python3 ...` below assumes a working `python3`. On Windows, `python3` often resolves to the Microsoft Store *App execution alias* stub, which exists but refuses to run. Probe by executing, not by `command -v`:

```bash
PY=""; for c in python3 python py; do "$c" -c "import sys" >/dev/null 2>&1 && { PY="$c"; break; }; done
[ -z "$PY" ] && { echo "ERROR: no working Python found." >&2; exit 1; }
echo "Using Python: $PY"
```

Substitute `$PY` for `python3` in every command below.

### Harness version

Read the installed revision once, up front — Phase 6 needs it for the `harness:` field of every gap it logs, and a version that has to be reconstructed afterwards never gets recorded at all:

```bash
grep -m1 'harness-version:' tools/lint_project.gd   # works without a running game
```

Once the game is up (Phase 2), `python3 tools/devtools.py harness-version` reports the addon's version and the client's. A non-zero exit there means the two halves are on different versions — a half-refreshed install — and the fix is to re-run `/scaffold-godot-harness`.

### Harness drift check

The installed harness can silently diverge from the plugin's templates — a project may have patched `dev_tools.gd` or `devtools.py` locally, or may be running a version predating fixes it now depends on. Either direction is a real hazard: a local patch is silently reverted by the next `/scaffold-godot-harness`, and a stale install means the pitfalls documented here don't match the code.

```bash
for f in addons/godot_selftest/dev_tools.gd addons/godot_selftest/scene_validator.gd \
         tools/devtools.py tools/lint_project.gd tools/run_tests.gd tools/check_devtools_log.py; do
  src="${CLAUDE_PLUGIN_ROOT}/templates/$f"
  [ -f "$src" ] && [ -f "$f" ] || continue
  cmp -s "$src" "$f" || echo "DRIFT: $f differs from the plugin template"
done
```

Report any drift in the summary, naming which side is ahead (`git log -1 --format=%cd -- <file>` on the project side vs the plugin's version). Do **not** auto-resolve it: if the project is ahead, the fix belongs upstream in the plugin; if the plugin is ahead, re-run `/scaffold-godot-harness`. Drift is a finding, not an error — continue the run.

## Phase 1: Headless Lint & Unit Tests (no game running)

Both runners write to stdout and set a meaningful exit code. **Redirect to a file and read it back** rather than trusting the console — the Godot binary on Windows is often the non-console build, which prints nothing to PowerShell, so a failed run looks like a silent success.

```bash
"$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd > lint.log 2>&1; echo "exit=$?"
cat lint.log
```

Exit-code contract (both runners): `0` = pass, `1` = findings (lint errors / failing tests) — stop and report them, `2` = **the runner itself could not run** (bad `devtools_config.json`, missing `test_dir`, unreadable `scan_root`) — stop with a different message, because a `2` means you verified nothing, not that the code is clean. The runners call `quit(n)` with their own counts, so the code is not polluted by Godot's leaked-RID-at-shutdown noise. The last line of lint output is also machine-readable: `lint: N error(s), M warning(s) -> exit C`.

Warnings alone do not fail; pass `--strict` to make them fail.

**Pre-existing findings.** If lint reports warnings you believe are untouched repo debt, do not hand-check them file by file. Record a baseline at the merge-base and lint against it — findings then print grouped as `NEW` (these drive the exit code) and `PRE-EXISTING`:

```bash
git stash && "$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd -- --baseline-write .lint-baseline.json > /dev/null 2>&1; git stash pop
"$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd -- --baseline .lint-baseline.json > lint.log 2>&1; echo "exit=$?"
```

Finding keys are `file|rule|subject` with no line numbers, so a finding survives unrelated edits to the same file.

Optionally, `-- --find-orphans` warns about public functions whose only callers outside their own file live under `test_dir` — code that has passing unit tests and no reachable caller. It is a heuristic (advisory only, never fails the run) but it catches the case where both gates say "clean" and the system is simply never invoked.

```bash
"$GODOT_BIN" --headless --path . --script res://tools/run_tests.gd > tests.log 2>&1; echo "exit=$?"
cat tests.log
```

`run_tests.gd` auto-discovers tests under the configured `test_dir`. Stop if any tests fail.

**Narrowing the run.** `--filter NAME` matches a test's method name **or its script filename**; `--file NAME` matches the script path (bare name, filename, or substring). They combine with AND:

```bash
"$GODOT_BIN" --headless --path . --script res://tools/run_tests.gd -- --file test_player --filter damage
```

A selector that matches nothing is **exit 2**, not a pass: `SELECTED NOTHING - filter 'spawner' selected 0 of 111 discovered test(s)`. Before this, a filter that hit nothing skipped the whole suite and printed `Total: 0 | Passed: 0 | Failed: 0` with exit 0 — indistinguishable from a clean run, and work shipped on the strength of it. Discovering no test scripts at all is exit 2 for the same reason. The summary line now also prints `Selected: N of M discovered` whenever a selector is in play, so a filter quietly matching two of eleven files is visible in the output rather than only in the exit code.

**Capture stderr, and read it.** Two failure modes only appear there:

- A test script that fails to parse. The runner now reports this as exit `2` with an `[ERR]` line, but before that fix a broken script printed `Total: 0 | ALL TESTS PASSED` and exited `0` while real tests sat undiscovered beside it. Never read "all tests passed" without checking the test *count* is what you expect.
- A runtime error *inside* a test method. GDScript has no exception handling: the error aborts only that method and returns the declared type's default — `""` for a `-> String` test, which is indistinguishable from a pass. The suite cannot detect this. The `[ERR]`/`[SCRIPT ERROR]` lines on stderr are the only signal.

Treat exit `2` as "**you verified nothing**" — it is not a test failure and must not be reported as one, nor waved through as a pass.

## Phase 2: Launch Game

Launch in the background. Append `--mute` only when `config.mute` is true (it defaults to true):

```bash
# --mute suppresses audio during automated testing (omit if config.mute is false)
"$GODOT_BIN" --path . --mute &
```

Wait for startup, then confirm connection:

```bash
sleep 5 && python3 tools/devtools.py ping
```

If ping fails, retry once:

```bash
sleep 3 && python3 tools/devtools.py ping
```

If it fails twice, check the Godot terminal output for errors and stop. A failed call now returns in ~2s rather than hanging for the full timeout, so the retry is cheap. **Read which of the three failures you got — they have different causes:**

| Message | Means |
|---|---|
| `game not running: … was never picked up` | Nothing is polling that directory: the game is dead, **or** you are polling the wrong `user://`. The signal can't tell these apart — check `--userdata` / `GODOT_USERDATA` before concluding it crashed. |
| `Crossed replies: …` | Another client or thread is driving the same game. Serialize your calls. |
| `No response … WAS picked up` | The game is alive and the handler is hung. This is a bug in the verb, not a connection problem. |

### Determine the live scene (do NOT assume a name)

After ping succeeds, discover the current root scene rather than assuming any particular one:

```bash
python3 tools/devtools.py scene-tree | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',d).get('name','?'))"
```

Record the root scene name — you will use it to build node paths later (paths are typically `/root/<RootName>/...`).

### Reach the playable scene (entry_hook)

Many projects boot into an entry/menu screen that gates input. If `config.entry_hook` defines a non-empty `node_path` and `method`, call it to advance into the playable scene:

```bash
# only if config.entry_hook.node_path and .method are non-empty
python3 tools/devtools.py run-method --node "<entry_hook.node_path>" --method "<entry_hook.method>" --args "[]"
sleep 3
```

Then re-check `scene-tree` and confirm the root scene changed to the expected playable scene (use `config.main_scene`'s basename if set, otherwise just confirm it is no longer the entry screen). If `entry_hook` is empty, proceed with whatever scene loaded. If the scene is in an unexpected/error state (root name is `?` or empty), stop and report the failure.

### Named entry points (diff-aware)

A single `entry_hook` reaches only the default playable scene, so a change to a script that only runs in some *other* scene — a boss room, a shop, a level-2 variant — has **no runtime path at all** and gets verified by reading code instead of by observing the game. Optional `config.entry_points` fixes that:

```json
"entry_points": {
  "boss": {
    "scene": "res://scenes/boss_room.tscn",
    "node_path": "/root/Main/Encounters",
    "method": "start_boss_fight",
    "args": [],
    "match": ["boss", "encounters/"]
  }
}
```

Selection: after reading the diff (Phase 4 Step 1), if any changed path contains one of an entry point's `match` substrings, use that entry point instead of the default `entry_hook` — load its `scene` (`cmd start_game --args '{"scene": "..."}'` or the project's own scene-loading verb, discovered via `list-commands`), then call its `node_path`/`method`. If several match, run the phase once per matching entry point. If none match, use the default `entry_hook`.

Note the difference from `entry_hook`: reaching a scene is rarely enough. A boss room that loads with the boss hidden until an intro sequence runs is *not* in a testable state — the entry point's `method` must be whatever the game itself calls to actually start the encounter. If a changed script has no reachable entry point, say so explicitly in the summary rather than reporting the change as verified.

**Baseline the orphan count here.** Scene loads and entry hooks create orphans that have nothing to do with the diff. Once the playable scene is up, re-baseline so Phase 3's performance check measures *your* growth:

```bash
python3 tools/devtools.py performance --reset-baseline
```

## Phase 3: Validation

Run in order:

1. `python3 tools/devtools.py validate-all` — 0 issues required
2. `python3 tools/devtools.py validate-ui` — 0 issues required. If `config.safe_area_inset` is non-zero this also reports `ui_outside_safe_area` for Controls straying under an overlay/notch/rounded corner; with an all-zero inset the check is skipped entirely.
3. `python3 tools/devtools.py screenshot` — visual verification (add a short `sleep` first if you just changed state)
4. `python3 tools/devtools.py performance` — FPS must be `>= config.fps_min` (default 30); orphan **growth** since the baseline must be `<= config.orphan_growth_max`

   Gate on `orphan_growth`, not the absolute count. A real project reports dozens of orphans on a fresh launch before any test action, so the historical `orphan_max: 0` was unreachable — and a threshold nothing can ever satisfy trains you to skip the check entirely. Growth-since-baseline is the number that actually means "this change leaks". Report the absolute count alongside it for context, and if `orphan_growth` is unavailable (older harness install), say so rather than silently falling back to a check you know is noise.

No game-specific exceptions are baked in. If `validate-ui` reports issues you believe are pre-existing/benign, report them explicitly in the summary rather than silently ignoring — and consider a Phase 6 proposal to add a baseline via `save-ui-baseline`.

## Phase 4: Change-Specific Tests (diff-aware, generic)

The point of this phase is to prove the **actual changes in this session** work at runtime. There are no pre-written game recipes — you design tests dynamically from the diff, using generic primitives plus whatever verbs the project registered.

### Step 1: Read the diff

```bash
git diff --name-only HEAD
git diff HEAD
```

If the working tree is clean, use your knowledge of what was modified in this session. From the diff, identify:
- **New/changed code paths** — new functions, new branches, new signals, new exported vars.
- **Triggers** — what makes them run: a method call, an input action, a timer, a state value, a node entering/leaving.
- **Observable effects** — position/size changes, property value changes, node creation/removal, visual changes. These are what you will assert on.

Map each changed script to the node(s) it drives using the root scene name discovered in Phase 2 (e.g. a changed `player.gd` likely corresponds to a node you can find via `scene-tree`).

### Step 2: Discover the project's registered debug verbs

The core exposes a fixed set of generic verbs; individual projects register their own domain verbs in their DevTools extension. Discover them:

```bash
python3 tools/devtools.py list-commands
```

This prints all currently registered action strings. Any verb beyond the generic set (below) is project-specific and can be invoked verbatim:

```bash
python3 tools/devtools.py cmd <verb> --args '{"key": value}'
```

`cmd` sends `{action:<verb>, args:<parsed json>}` to the bus and prints the `{success, message, data}` result. Use this for domain setup/trigger steps that the generic primitives can't express (e.g. a project verb that spawns an entity, resets a session, or sets a batch of levels).

### Step 3: Generic primitives available for building tests

| Command | Use for |
|---|---|
| `get-state --node PATH --property NAME` | Read node properties (primary assertion tool). `--property` is repeatable — always use it; an unfiltered `Label` returns ~120 keys. Assert transforms on `data.transform`, which is always present: Godot hides `position`/`scale`/`rotation` on container children, so a scale animation on a `VBoxContainer` child is invisible to the property dump while working perfectly on screen |
| `step-time --seconds N` | Advance ~N game-seconds with `time_scale` pinned to 1.0 — for sampling a tween at a chosen moment instead of guessing with `set-game-speed` + sleeps. Physics time is exact; process-driven tweens (the `Tween` default) land within ~1 frame, so compare the returned `process_seconds` rather than assuming |
| `touch <press\|release\|drag\|clear\|list> --index N --pos X,Y` | Real `InputEventScreenTouch`/`Drag` — the only way to exercise multi-touch |
| `set-feature --touchscreen true` | Make touch UI visible on desktop (it hides itself when no touchscreen is reported). Set it **before** the scene loads: a Control that read availability in its own `_ready()` won't re-evaluate |
| `set-state --node PATH --property NAME --value V` | Set a raw property (see pitfall about signals below) |
| `run-method --node PATH --method NAME --args "[...]"` | Call any method on a node — **preferred** for anything that should emit a signal / run side effects |
| `input tap ACTION --hold N` / `input press` / `input release` | Simulate input actions defined in the project |
| `set-game-speed N` | Speed up time-dependent behavior (timers, tweens, physics) |
| `wait-frames N` | Advance N physics frames deterministically |
| `node-bounds PATH` | Exact position/size of a node (ground truth for layout/movement) |
| `ui-snapshot` / `ui-snapshot-diff` | Structured UI state; diff against a saved baseline |
| `clear-nodes` | Free matching nodes: `--group NAME`, or via `cmd clear_nodes --args '{"method":"..."}'` / `'{"class":"..."}'` |
| `screenshot` | Visual verification (always `sleep 0.5`–`1` after a state change first) |
| `cmd <verb> --args '{...}'` | Any project-registered verb from `list-commands` |
| `harness-version` | The installed harness revision, game-side and client-side. Fills the `harness:` field of every gap logged in Phase 6; a non-zero exit means the addon and the client are on different versions (re-run `/scaffold-godot-harness`) |

### Step 4: Design, execute, verify

For each significant change in the diff, design a test that:
1. **Sets preconditions** — via `set-state`, `run-method`, `input`, `set-game-speed`, or a discovered `cmd <verb>`.
2. **Triggers the behavior** — call the method, send the input, spawn/advance frames.
3. **Asserts the observable effect** — read it back with `get-state` on the node(s) the diff touched, or `node-bounds` / `ui-snapshot` / `screenshot`. Verify through concrete state, never through domain intuition.

Also test at least one guard/edge case per behavior (e.g. the effect must NOT happen when a precondition is absent). Report each test with a name, what it verified, and pass/fail.

**Generic worked example** — suppose the diff adds a method `apply_damage(amount)` to a node that reduces a `health` property and emits a `health_changed` signal, but only while `is_alive` is true:

```bash
# Discover the node's path from the scene tree, then inspect current state
python3 tools/devtools.py get-state --node "/root/<Root>/Entities/Enemy"
# Precondition: ensure it is alive (prefer run-method if a setter emits a signal)
python3 tools/devtools.py set-state --node "/root/<Root>/Entities/Enemy" --property is_alive --value true
# Trigger via run-method (emits health_changed, unlike a raw set-state on health)
python3 tools/devtools.py run-method --node "/root/<Root>/Entities/Enemy" --method apply_damage --args "[30]"
# Assert the observable effect
python3 tools/devtools.py get-state --node "/root/<Root>/Entities/Enemy"   # expect health decreased by 30
# Guard case: dead entities must not take damage
python3 tools/devtools.py set-state --node "/root/<Root>/Entities/Enemy" --property is_alive --value false
python3 tools/devtools.py run-method --node "/root/<Root>/Entities/Enemy" --method apply_damage --args "[30]"
python3 tools/devtools.py get-state --node "/root/<Root>/Entities/Enemy"   # expect health unchanged
```

### Generic pitfalls (apply regardless of project)

- **Prefer signal-emitting `run-method` over raw `set-state`.** A direct `set-state` writes the property but bypasses any setter/signal, so dependent UI and systems won't react. If a value is normally changed through a method that emits a signal, call that method instead.
- **Toggle stateful UI once per launch.** UI opened/closed via tweened toggles often have a guard that blocks rapid re-entry. Trigger such a toggle at most once per launch; if state looks corrupted, `quit` and relaunch rather than toggling again.
- **Screenshots need a short sleep.** State changes (tweens, physics, layout) are not instant — `sleep 0.5`–`1` before `screenshot`. For deterministic ground truth prefer `node-bounds` or `ui-snapshot` over pixels.
- **DevTools input may not reach gated scenes.** Simulated input drives both the polled action state and a dispatched `InputEventAction`, so polling (`Input.is_action_pressed`) and event-based (`_input`/`_unhandled_input`) handlers both see it. It can still be swallowed by an unfocused window or an entry/menu screen that gates gameplay: if input tests appear to do nothing, ensure you advanced past the entry screen via `config.entry_hook` (Phase 2), and re-apply the hook manually if you relaunched mid-run.
- **A run that never changes is broken, not passing.** If repeated samples return identical values — especially all-zero ones — suspect the session is dead or frozen before you conclude the code under test is wrong. Check the `status` field the project's status provider attaches to every response (see the extension section of the harness CLAUDE.md); if the project has not registered one, verify liveness explicitly before trusting a flat result. A dead player or a paused tree answers every query with well-formed zeros.
- **One in-flight command at a time.** The bridge is a single command/result file pair, so concurrent callers overwrite each other and replies come back for the wrong request (typically surfacing as a missing key in the response). Never poll from a background thread while sampling on the main one — serialize every call.

## Phase 5: Clean Shutdown

```bash
python3 tools/devtools.py quit
```

## Phase 6: Log the gaps (REQUIRED)

Append an entry to `log-devtools.md` at the project root (create it if missing) naming every gap in this workflow or the devtools that showed up during the run, each with the smallest improvement that would have closed it. If the run hit none, write one explicit "no gaps this turn" line.

```markdown
## YYYY-MM-DD — <what this run verified>

- Gap: **<what was missing>** — <the command run, the output it gave, the workaround used>
  - [G-001] status: open | seen: 1 | harness: 0.5.0
  - Improvement: <the smallest change that would have closed it>
```

The `[G-NNN]` status line is required — it is what lets a later reader tell an open gap from one fixed two versions ago:

| Field | Rule |
|---|---|
| `[G-NNN]` | Next unused id in this file. **Stable, never reused.** |
| `status:` | `open`, `fixed` (add `fixed-in: X.Y.Z`), or `wontfix` (say why on the Improvement line). |
| `seen:` | Bump the **existing** entry when a known gap bites again; do not file a second one. |
| `harness:` | The installed version, from `python3 tools/devtools.py harness-version`. Read it once at the start of the run — a gap that can't be tied to a version can't be told from a regression later. |

Before writing a new gap, scan the file for one that already describes it. A `seen: 3` is the strongest signal this log can produce; three separately-worded entries are the weakest. When a gap you find here is already closed by the installed version, mark it `status: fixed | fixed-in: <version>` instead of leaving it to be re-upstreamed.

This is not bookkeeping. Every capability the harness has beyond its first version — the status provider, the node-path normalization, the property filter, the touch verbs, the orphan baseline — exists because a run like this one wrote down what it couldn't do. Quote real output; a gap without evidence can't be acted on later.

Open gaps only become fixes once they reach the harness repo, which is a one-liner:

```bash
python3 tools/upstream_gaps.py log-devtools.md --into /path/to/godot-selftest-harness/log-devtools.md
```

Distinguish this from the Self-Improvement section below: that one proposes edits to `/verify` itself and needs user approval. Phase 6 is an unconditional write to the project's log, no approval needed.

## Self-Improvement (Post-Run)

After writing the Pass/Fail Summary, reflect on the entire run and identify improvements to this file. **Do not edit this file directly.** Instead, present proposals to the user for approval.

### What to Look For

| Signal | Example |
|---|---|
| **Workflow friction** | A phase required manual workarounds, retries, or steps not in the instructions |
| **Silent failures** | A command exited 0 but produced wrong results, or a test ran without asserting its outcome |
| **Stale references** | Node paths, method names, arg formats, or config keys that no longer match the project |
| **Missing coverage** | A change was made but no Phase 4 test exercised it |
| **Timing / ordering** | A step that only works before/after another, or a sleep too short to be reliable |
| **Unclear instructions** | You had to guess because the instructions were ambiguous |
| **Unnecessary steps** | A check that always passes and adds no value |
| **Config drift** | `devtools_config.json` thresholds/hooks that no longer reflect reality |

### How to Propose

For each issue, present a recommendation in this format:

```
**Issue:** [What went wrong — quote the specific command output or describe the friction]
**Proposal:** [Which section to change, plus the literal text to insert or replace as a code block — so approval is a single yes/no]
**Rationale:** [Why this improves future runs]
```

### Rules

1. **Evidence required.** Every proposal must cite specific output from this run. No speculative improvements.
2. **Max 3 proposals per run.** If more surface, prioritize: silent failures > workflow blockers > stale references > polish.
3. **No transient issues.** Do not propose changes for issues that resolved by retrying the same command.
4. **No re-proposals.** Do not re-propose an issue the user declined this session unless new evidence changes the rationale.
5. **Nothing is off-limits.** Any section — phases, commands, thresholds, pitfalls, even this section — can be proposed for change. The user decides.
6. **Wait for approval.** Present proposals after the Pass/Fail Summary (so the user sees results first). Only edit this file after explicit approval. Apply all approved changes in a single edit.
7. **Soft cap.** If Generic Pitfalls exceeds 10 entries, propose consolidation or removal of entries not triggered in recent runs.
8. **Keep it concise.** Each pitfall/example entry should be a few lines, including any command block.

## Pass/Fail Summary

Report results as a table: Godot binary used, harness drift (Phase 0) if any, config thresholds (fps_min / orphan_growth_max), lint status, unit test status, live scene name (and which entry point fired), validate-all, validate-ui, performance (FPS + orphan growth vs baseline), and each change-specific test (name + what it verified + pass/fail). List the project verbs discovered via `list-commands` that you used. Also check the Godot terminal output for GDScript runtime errors or warnings. If all pass, the commit is safe to proceed.
