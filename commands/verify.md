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

Relevant keys: `fps_min` (default 30), `orphan_max` (default 0), `mute` (default true), `main_scene`, and `entry_hook` `{node_path, method}`. If the file is missing, assume the defaults above.

## Phase 1: Headless Lint & Unit Tests (no game running)

```bash
"$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd
```

Stop if lint reports errors.

```bash
"$GODOT_BIN" --headless --path . --script res://tools/run_tests.gd
```

`run_tests.gd` auto-discovers tests under the configured `test_dir`. Stop if any tests fail.

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

If it fails twice, check the Godot terminal output for errors and stop.

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

## Phase 3: Validation

Run in order:

1. `python3 tools/devtools.py validate-all` — 0 issues required
2. `python3 tools/devtools.py validate-ui` — 0 issues required
3. `python3 tools/devtools.py screenshot` — visual verification (add a short `sleep` first if you just changed state)
4. `python3 tools/devtools.py performance` — FPS must be `>= config.fps_min` (default 30) and orphan nodes `<= config.orphan_max` (default 0)

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
| `get-state --node PATH` | Read any node's exported/script properties (primary assertion tool) |
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
- **DevTools input may not reach gated scenes.** Simulated input may not trigger `_unhandled_input` on an unfocused window or an entry/menu screen that gates gameplay. If input tests appear to do nothing, ensure you advanced past the entry screen via `config.entry_hook` (Phase 2). If you relaunched mid-run, re-apply the hook manually before input tests.

## Phase 5: Clean Shutdown

```bash
python3 tools/devtools.py quit
```

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

Report results as a table: Godot binary used, config thresholds (fps_min / orphan_max), lint status, unit test status, live scene name (and whether the entry_hook fired), validate-all, validate-ui, performance (FPS + orphan nodes vs thresholds), and each change-specific test (name + what it verified + pass/fail). List the project verbs discovered via `list-commands` that you used. Also check the Godot terminal output for GDScript runtime errors or warnings. If all pass, the commit is safe to proceed.
