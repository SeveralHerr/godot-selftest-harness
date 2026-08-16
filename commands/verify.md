---
description: Run runtime devtools verification on a Godot project (godot-selftest-harness). Use after modifying scripts, scenes, or gameplay before committing.
---

Run the Godot runtime verification workflow. Execute each phase sequentially, stopping on failures. This command is **generic** — it makes no assumptions about the game's node names, systems, or gameplay concepts. All project-specific behavior is discovered at runtime or read from `res://addons/godot_selftest/devtools_config.json`.

## Phase 0: Resolve Godot binary & config

**Python interpreter first.** On Windows, `python3` often resolves to the Microsoft
Store *App execution alias* stub, which exists but refuses to run. Probe by executing,
not by `command -v`, and use `"$PY"` in every command below:

```bash
PY=""; for c in python3 python py; do "$c" -c "import sys" >/dev/null 2>&1 && { PY="$c"; break; }; done
[ -z "$PY" ] && { echo "ERROR: no working Python found." >&2; exit 1; }
echo "Using Python: $PY"
```

**Resolve the Godot binary once, then use the resolved path everywhere below.** Do NOT hardcode a platform-specific path in the commands.

```bash
GODOT_BIN="${GODOT_BIN:-}"
if [ -z "$GODOT_BIN" ]; then
  # Scaffold step 11 records the probed binary here; trust it first.
  GODOT_BIN="$("$PY" -c "import json; print(json.load(open('addons/godot_selftest/devtools_config.json')).get('godot_bin',''))" 2>/dev/null || true)"
  [ -x "$GODOT_BIN" ] || GODOT_BIN=""
fi
if [ -z "$GODOT_BIN" ] && [ -x "/Applications/Godot.app/Contents/MacOS/Godot" ]; then
  GODOT_BIN="/Applications/Godot.app/Contents/MacOS/Godot"
fi
if [ -z "$GODOT_BIN" ]; then
  GODOT_BIN="$(command -v godot || true)"
fi
if [ -z "$GODOT_BIN" ]; then
  echo "ERROR: Godot binary not found. Set GODOT_BIN or devtools_config.json godot_bin." >&2; exit 1
fi
echo "Using Godot: $GODOT_BIN"
```

Resolution order: `GODOT_BIN` env var → `godot_bin` in `devtools_config.json` (written by scaffold step 11) → macOS default (`/Applications/Godot.app/Contents/MacOS/Godot`) → `which godot`. Because the shell does not persist between tool calls, re-run this resolution snippet (or inline it) in each Godot invocation below.

Read the config so you know the thresholds and hooks for this project:

```bash
"$PY" -c "import json; print(json.dumps(json.load(open('addons/godot_selftest/devtools_config.json')), indent=2))" 2>/dev/null || echo "{}"
```

Relevant keys: `fps_min` (default 30), `orphan_max` (default 0), `orphan_growth_max`, `mute` (default true), `main_scene`, `entry_hook` `{node_path, method}`, `entry_points` (see Phase 2), and `safe_area_inset`. If the file is missing, assume the defaults above.

### Harness version

Read the installed revision once, up front — Phase 6 needs it for the `harness:` field of every gap it logs, and a version that has to be reconstructed afterwards never gets recorded at all:

```bash
grep -m1 'harness-version:' tools/lint_project.gd   # works without a running game
```

Once the game is up (Phase 2), `"$PY" tools/devtools.py harness-version` reports the addon's version and the client's. A non-zero exit there means the two halves are on different versions — a half-refreshed install — and the fix is to re-run `/scaffold-godot-harness`.

### Harness drift check

The installed harness can silently diverge from the plugin's templates — a project may have patched `dev_tools.gd` or `devtools.py` locally, or may be running a version predating fixes it now depends on. Either direction is a real hazard: a local patch is silently reverted by the next `/scaffold-godot-harness`, and a stale install means the pitfalls documented here don't match the code.

```bash
# Compare line-ending-normalized: a CRLF checkout (Windows core.autocrlf) makes a raw
# `cmp` report every installed file as drifted when not one byte of content differs.
DRIFTED=""
for f in addons/godot_selftest/dev_tools.gd addons/godot_selftest/scene_validator.gd \
         tools/devtools.py tools/lint_project.gd tools/run_tests.gd tools/eval.gd \
         tools/import_check.py tools/name_check.py tools/check_devtools_log.py \
         tools/upstream_gaps.py tools/verify_ledger.py tools/coverage_check.py; do
  src="${CLAUDE_PLUGIN_ROOT}/templates/$f"
  [ -f "$src" ] && [ -f "$f" ] || continue
  diff -q <(tr -d '\r' < "$src") <(tr -d '\r' < "$f") >/dev/null || { echo "DRIFT: $f"; DRIFTED="$DRIFTED $f"; }
done
```

For each drifted file, get a **bearing** — which side is ahead — from the release history rather than guessing from mtimes. `harness_history.json` records the LF-normalized sha256 of every shipped file per released version:

```bash
[ -n "$DRIFTED" ] && "$PY" - $DRIFTED <<'EOF'
import hashlib, json, os, sys
hist = json.load(open(os.path.join(os.environ["CLAUDE_PLUGIN_ROOT"], "harness_history.json")))
vkey = lambda v: tuple(int(x) for x in v.split("."))
current = max(hist, key=vkey)
for f in sys.argv[1:]:
    h = hashlib.sha256(open(f, "rb").read().replace(b"\r\n", b"\n")).hexdigest()
    versions = sorted((v for v in hist if hist[v].get(f) == h), key=vkey)
    if not versions:
        print("%s: hash in NO released version -> project has local edits; port them into devtools_ext or upstream them" % f)
    elif versions[-1] == current:
        print("%s: matches current release %s but differs from the template -> plugin is ahead unreleased (mid-release); leave it" % (f, current))
    else:
        print("%s: matches %s, current is %s -> stale install; re-run /scaffold-godot-harness" % (f, versions[-1], current))
EOF
```

Report any drift in the summary with its bearing line. Do **not** auto-resolve it: if the project is ahead, the fix belongs upstream in the plugin; if the install is stale, re-run `/scaffold-godot-harness`. Drift is a finding, not an error — continue the run.

## Phase 0.5: Triage — how much verification does this diff need?

Classify the diff before running anything. A full runtime pass on a docs edit is where `overkill` rows come from; the fix is to not start one.

```bash
git status --porcelain --untracked-files=all
git diff HEAD --stat
```

| Diff is… | Tier | What runs |
|---|---|---|
| (a) Nothing Godot loads: only docs/`.md` outside code, `.beads/`, `log-devtools.md`, CI/git files | **Nothing** | Print "nothing to verify". Write **no** ledger row. Log the Phase 6 entry with `Value: **overkill** — avoided: triaged out at Phase 0.5, no res:// change` and **STOP the run here.** |
| (b) Only comments/docstrings inside `.gd`/`.tscn` files, or `.md` files in code dirs | **Lint-only** | Phase 1 name gate + import gate + lint. Skip tests and runtime; say so in the summary. |
| (c) Only `static func`s or `const` tables that existing unit tests cover | **Headless-only** | Phase 1 (name gate + import gate + lint + tests). Skip runtime; the Phase 6 entry must **name which tests** stood in for runtime. |
| (d) The project **cannot be launched**: neither `project.godot`'s `run/main_scene` nor `config.main_scene` names a scene | **Headless-only (forced)** | Phase 1 only — there is nothing for Phase 2 to start. This is not a pass at runtime and must not be reported as one: the summary says `runtime unreached: project has no main scene`, the Phase 5 ledger row is written with `--no-reach` and `skipped: "no main_scene"`, and the Phase 6 verdict is **inconclusive**, not overkill (gh#10). Every Godot project is in this state for its first commits, which is exactly when the DEVELOPMENT RULE is followed most literally. |
| Anything else — instance methods, signals, scenes, exports, node paths, config | **Full run** | All phases. |

Check tier (d) mechanically rather than by recollection — an empty `main_scene` is a state, not a diff, and the diff will not show it:

```bash
grep -q '^run/main_scene=' project.godot || "$PY" -c "import json,sys; c=json.load(open('addons/godot_selftest/devtools_config.json')); sys.exit(0 if c.get('main_scene') else 1)" || echo "TIER (d): no main scene - runtime unreachable"
```

The name gate is cheap enough that it runs on every tier that reaches Phase 1, including lint-only — it costs no engine launch and catches the class of failure (a `class_name` that arrived from a rebase) the diff cannot show you.

Rules: when in doubt, full run. Tier (c) requires you to have *checked* the tests exist and exercise the changed functions (`--filter` them in Phase 1), not assumed it. Tiers (b) and (c) still write a ledger row in Phase 5 — use `--no-reach` (there is no session to observe) and `value: overkill` with `cheaper_alternative` naming the tier. Record `found: []` unless the lint or test gate actually caught something, in which case record that finding with its `phase` — a tier that catches a real defect is the strongest possible evidence the cheaper tier was the right call.

## Phase 1: Headless Gates — Names, Import, Lint & Unit Tests (no game running)

### Name gate (before everything, and the only gate that is safe in parallel)

```bash
"$PY" tools/name_check.py; echo "exit=$?"
```

`name_check.py` resolves every name the scripts mention — types, `class_name`s,
autoloads, `preload("res://…")` targets, engine classes and their members, and the
method/signal names inside string literals — against the project's own declarations plus
an engine API index cached per engine version under the user's cache dir. It opens no
project, writes nothing to `.godot/`, and takes no lock, so it is the one gate that N
agents can run at the same time on the same checkout. Exit codes are the usual contract:
`0` clean, `1` findings that count, `2` could not run.

It runs first because it is the cheapest gate and it names its causes directly. The
import gate below tells you a cascade happened; this one tells you which identifier
started it, and it does so on a working tree the import gate cannot even reach.

**Clean here means the names resolve, not that the file compiles.** Type inference is the
gap: `var kids := root.get_children()` on a `Node`-typed `root` is a hard parse error
(`Cannot infer the type of "kids" variable because the value doesn't have a set type`),
and `name_check.py` prints `errors: 0 | warnings: 0` straight over it. Deciding it needs
method resolution order and return types — i.e. opening the project, the one thing this
gate exists not to do. The import gate below is where that class of error surfaces, so a
`name_check` pass never licenses skipping it. The tool prints this itself as a
`NOT COVERED:` line on every clean run. This matters most in a fan-out: `name_check` is
the only gate that is concurrency-safe, so it is the only one a parallel agent gets, and
an agent reporting "verified" on it alone has not verified that its code builds.

**In a fresh worktree, this is the only Phase 1 gate that works at all.** A never-imported
worktree has no class cache, so lint reports a thousand `Identifier "X" not declared`
errors and still exits 0, and the test runner prints `[PASS]` for tests whose first
statement errored. `name_check.py` does not care: it never needed the cache. Run it, fix
what it finds, and only then decide whether the worktree is worth importing.

If it reports `engine index: NONE`, the engine-name half was **skipped, not passed** —
seed it once with `"$PY" tools/name_check.py --refresh-api` (runs Godot in a temp dir with
no project; safe while other agents are mid-verify). Pass `--require-api` to make a
missing index an exit `2` rather than a quiet downgrade.

Useful here: `--only <prefix>` reports just your own files while still resolving names
across the whole project, which is what a fan-out agent wants; `--strict` counts warnings;
`--baseline`/`--baseline-write` adopt the checker on a project with pre-existing findings
so only NEW ones gate.

### Import gate (after names, before lint)

`godot --headless --path . --import` **exits 0 while printing parse errors.** Real captured output:

```
BARE --import exit=0
SCRIPT ERROR: Parse Error: Could not parse global class "ScratchPlayer" from "res://player.gd".
ERROR: Failed to load script "res://items.gd" with error "Parse error".
```

"the class cache was regenerated" and "the project still parses" are the same exit code, so nothing downstream of the bare command can tell them apart. `tools/import_check.py` runs the same import, captures stdout+stderr to `.devtools/import.log`, scans it for `SCRIPT ERROR` / `Parse Error` / `Failed to load script` / `Compilation failed`, and quotes the failing lines back with their `at:` locations.

It runs **ahead of lint and tests** because a stale or broken class cache is the *cause* of the cascade they would otherwise report: one missing entry in `.godot/global_script_class_cache.cfg` turns into parse errors in dozens of files nobody touched, and working through them in file order means fixing the wrong thing first.

Guard `project.godot` around it. Godot's import pass sometimes rewrites it, stripping comments and web-renderer overrides. Plain lint/test runs do **not** dirty `project.godot`; only `--import` does, and not every `--import` — which is exactly why the change goes unnoticed:

```bash
mkdir -p .devtools && cp project.godot .devtools/project.godot.bak
"$PY" tools/import_check.py; echo "exit=$?"
diff .devtools/project.godot.bak project.godot >/dev/null || echo "WARNING: --import REWROTE project.godot — diff it before staging; restore the comments/overrides it stripped (cp .devtools/project.godot.bak project.godot if the rewrite was pure loss)"
```

Exit codes are this repo's usual contract: `0` imported clean, `1` parse/load errors in the output — stop and fix the quoted script, `2` **could not run** (no Godot binary, no `project.godot`, a timeout, Godot exited non-zero, or it wrote no output at all — which on Windows means the non-console build, and an empty log is an unverified run, not a clean one). Never read a `2` as a pass. `--json` emits the same verdict machine-readably; `--timeout N` (default 900s) bounds a first import of a large project.

Run this for **every tier that reaches Phase 1, including lint-only.** The failure it catches is one you *received* — a rebase, a pull, a branch switch that brought in a `class_name` you never wrote — so triaging on the diff cannot exempt it: the diff is the one place the cause does not appear.

### Lint and unit tests

Both runners write to stdout and set a meaningful exit code. **Redirect to a file and read it back** rather than trusting the console — the Godot binary on Windows is often the non-console build, which prints nothing to PowerShell, so a failed run looks like a silent success.

```bash
"$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd > lint.log 2>&1; echo "exit=$?"
cat lint.log
```

Exit-code contract (both runners): `0` = pass, `1` = findings (lint errors / failing tests) — stop and report them, `2` = **the runner itself could not run** (bad `devtools_config.json`, missing `test_dir`, unreadable `scan_root`) — stop with a different message, because a `2` means you verified nothing, not that the code is clean. The runners call `quit(n)` with their own counts, so the code is not polluted by Godot's leaked-RID-at-shutdown noise. The last line of lint output is also machine-readable: `lint: N error(s), M warning(s) -> exit C`.

Warnings alone do not fail; pass `--strict` to make them fail.

**`class_cache_stale` is reported first and must be fixed first.** A `class_name X` declared in a script but absent from `.godot/global_script_class_cache.cfg` is an error finding raised *ahead of* the parse-error cascade it causes, with `hint: many scripts failing together usually means a stale class cache`. Recognize it on sight — it presents as errors in files you never touched — and do not start reading them: the fix is a command, not a code change. Run `godot --headless --path . --import`, or `"$PY" tools/import_check.py` for the same import with the errors actually surfaced, then re-lint. Passing the import gate above means this cannot fire; you will see it on a run that reached lint another way (a fresh clone, a lint-only rerun). `class_cache_missing` — no cache file at all — is the never-imported case and is advisory only.

**The `Shaders:` line is a denominator, not a checkmark.** Lint compiles every `.gdshader` under `scan_root` and every `Shader` embedded in a `.tres`, and always reports what it looked at: `Shaders: 41 of 42 compiled OK (29 file, 13 embedded)`. A failure is an error finding naming the file, with the engine's `SHADER ERROR` lines on stderr giving the offending line. Two readings to make deliberately: `Shaders: none found` means the project has no shaders, **not** that shaders passed; and `N .gdshaderinc skipped` is honest bookkeeping — include files declare no `shader_type`, so they are checked through their includers, never on their own. This is the one gate for a failure class the rest of the run structurally cannot see: a scene with a broken shader loads clean, lints clean, and tests green, and shows magenta only when it is on screen. Pass `-- --no-shaders` to skip the pass.

`UIDs: OK` now means both halves of the UID pass are clean: no stale `uid=` reference **and** no `.gd` missing its `.uid` sidecar. A script you just created outside the editor has no sidecar, and that is reported as `WARN: <path>: no .uid sidecar` rather than being counted as OK. Commit the sidecar with the script; `uid_check_ignore` in the config exempts paths (default: `addons/`, `tools/`).

**Pre-existing findings.** If lint reports warnings you believe are untouched repo debt, do not hand-check them file by file. Record a baseline at the merge-base and lint against it — findings then print grouped as `NEW` (these drive the exit code) and `PRE-EXISTING`:

```bash
git stash && "$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd -- --baseline-write .lint-baseline.json > /dev/null 2>&1; git stash pop
"$GODOT_BIN" --headless --path . --script res://tools/lint_project.gd -- --baseline .lint-baseline.json > lint.log 2>&1; echo "exit=$?"
```

Finding keys are `file|rule|subject` with no line numbers, so a finding survives unrelated edits to the same file.

The orphan scan runs by default (0.21.0+, gh#11) and prints `Orphans: N of M public function(s) across S script(s) have no live reference` — a public function whose only callers outside its own file live under `test_dir`, or nowhere. It is a heuristic (advisory only, never fails the run) but it catches the case where both gates say "clean" and the system is simply never invoked. **Read its `WARN:` lines for any method the diff added**: a new public method with no live reference is a feature that cannot run. `-- --no-orphans` skips it.

```bash
"$GODOT_BIN" --headless --path . --script res://tools/run_tests.gd > tests.log 2>&1; echo "exit=$?"
cat tests.log
```

`run_tests.gd` auto-discovers tests under the configured `test_dir`. Stop if any tests fail.

**Read the `Suite:` line, and report it.** Every run prints `Suite: N test script(s) in res://test/unit` alongside `Assertions: M executed`. That pair is the checking previous sessions left behind for this one — say it out loud in your Phase 1 report (`inherited suite: 4 scripts, 61 assertions`), because it is what tells you whether to *extend* an existing selftest or start the project's first one. A project sitting at `Suite: 1` with only the seeded sanity checks has no accumulated coverage, and every session that writes a throwaway check instead of adding here keeps it at 1 forever.

### Assertion coverage (advisory, no engine, parallel-safe)

```bash
"$PY" tools/coverage_check.py
```

A green suite says nothing about what the suite never asks. `coverage_check.py` reports on the *checks* rather than their results: it enumerates the defect classes the harness knows about — UI layout, UI reachability, unconnected signals, orphan growth, input path, scene validation, shader compile, name resolution — and names the ones nothing in this project exercises, printing the file:line evidence for each class it calls covered.

This is advisory and **never fails the run** (it exits 0 by default; `--strict` is opt-in). Report the unchecked classes in the Phase 1 summary anyway. It is the one signal here that a project-authored suite structurally cannot produce for itself, and the failure mode it addresses is a session signing off "70 checks, 0 failures, confidence high" over a screen that was visibly broken in a way nothing in those 70 checks was looking at.

If Phase 4 ends up writing a check that closes one of the named classes, promote it into `test_dir` (Phase 4 Step 5) and the class goes quiet on the next run — that is the loop working.

It opens no project and writes nothing to `.godot/`, so it is safe alongside other agents and works in a never-imported worktree, same as `name_check.py`.

**Narrowing the run.** `--filter NAME` matches a test's method name **or its script filename**; `--file NAME` matches the script path (bare name, filename, or substring). They combine with AND:

```bash
"$GODOT_BIN" --headless --path . --script res://tools/run_tests.gd -- --file test_player --filter damage
```

A selector that matches nothing is **exit 2**, not a pass: `SELECTED NOTHING - filter 'spawner' selected 0 of 111 discovered test(s)`. Before this, a filter that hit nothing skipped the whole suite and printed `Total: 0 | Passed: 0 | Failed: 0` with exit 0 — indistinguishable from a clean run, and work shipped on the strength of it.

**Every way of running nothing is now exit 2**, and the summary says which one:

| Line | Means |
|---|---|
| `SELECTED NOTHING - …` | The selector matched no test. |
| `no test_*.gd scripts found` | Nothing under `test_dir` — check the config before believing the suite is empty. |
| `N test script(s) found, but no test_* methods in them` | The files exist and define nothing runnable. This used to print `ALL TESTS PASSED` at exit 0 — a scaffolded-but-unwritten suite reading as a green one. |

Read `Selected: N of M discovered  (<selector>)` on **every** run, filtered or not — it is printed unconditionally now. On a bare run it is the discovery denominator, which is what tells you the whole suite was even found; on a filtered run it is what the selector cut it down to, so a `--file` quietly matching two of eleven scripts is visible in the output instead of only in the exit code.

**Capture stderr, and read it.** Two failure modes only appear there:

- A test script that fails to parse. The runner now reports this as exit `2` with an `[ERR]` line, but before that fix a broken script printed `Total: 0 | ALL TESTS PASSED` and exited `0` while real tests sat undiscovered beside it. Never read "all tests passed" without checking the test *count* is what you expect.
- A runtime error *inside* a test method. GDScript has no exception handling: the error aborts only that method and returns the declared type's default — `""` for a `-> String` test, which is indistinguishable from a pass. The suite cannot detect this. The `[ERR]`/`[SCRIPT ERROR]` lines on stderr are the only signal.

Treat exit `2` as "**you verified nothing**" — it is not a test failure and must not be reported as one, nor waved through as a pass.

## Phase 2: Launch Game

**Snapshot the working tree first.** A live editor-less Godot session can still re-serialize files it merely opens (`.tscn`, `.tres`, `project.godot` are the usual suspects), and without a before-picture those edits are indistinguishable from yours at commit time. Phase 5 diffs against this:

```bash
mkdir -p .devtools && git status --porcelain > .devtools/git-status-before.txt
```

**Prefer `launch` over a bare `&` and a sleep.** It spawns Godot detached with logs under `.devtools/`, then *proves the bus answers a ping* before returning — so a launch that reports success has been verified rather than waited for. It also refuses to start when a live pid already owns this bus, which is the failure that presents as flaky verbs: two instances answering one command file corrupt each other silently, and nothing in a reply says so.

```bash
"$PY" tools/devtools.py launch; echo "exit=$?"
```

It always passes `--mute` unless you pass `--no-mute` — it does not read `config.mute`, so pass `--no-mute` when that key is false or when the run needs audio (a `--write-movie` capture records the audio bus, and a muted run captures silence). Everything after a bare `--` goes to the Godot command line. `--isolated` gives the session a private id **and** a private bus directory, verified before the follow-up command is printed; `user://` itself is still shared, because Godot has no switch for it. `--allow-second-instance` overrides the live-pid refusal; `--no-wait` returns without proving the bus, which forfeits the reason to use this verb.

The manual equivalent, when you need to drive the Godot command line yourself:

```bash
# --mute suppresses audio during automated testing (omit if config.mute is false)
"$GODOT_BIN" --path . --mute &
sleep 5 && "$PY" tools/devtools.py ping
```

If ping fails, retry once with `sleep 3`. If it fails twice, check the Godot terminal output for errors and stop. A failed call now returns in ~2s rather than hanging for the full timeout, so the retry is cheap. **Read which of the three failures you got — they have different causes:**

| Message | Means |
|---|---|
| `game not running: … was never picked up` | Nothing is polling that directory: the game is dead, **or** you are polling the wrong `user://`. The signal can't tell these apart — check `--userdata` / `GODOT_USERDATA` before concluding it crashed. |
| `Crossed replies: …` | Another client or thread is driving the same game. Serialize your calls. |
| `No response … WAS picked up` | The game is alive and the handler is hung. This is a bug in the verb, not a connection problem. |

### Determine the live scene (do NOT assume a name)

After ping succeeds, discover the current root scene rather than assuming any particular one:

```bash
"$PY" tools/devtools.py scene-tree | "$PY" -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',d).get('name','?'))"
```

Record the root scene name — you will use it to build node paths later (paths are typically `/root/<RootName>/...`).

### Reach the playable scene (entry_hook)

Many projects boot into an entry/menu screen that gates input. If `config.entry_hook` defines a non-empty `node_path` and `method`, call it to advance into the playable scene:

```bash
# only if config.entry_hook.node_path and .method are non-empty
"$PY" tools/devtools.py run-method --node "<entry_hook.node_path>" --method "<entry_hook.method>" --args "[]"
sleep 3
```

Then re-check `scene-tree` and confirm the root scene changed to the expected playable scene (use `config.main_scene`'s basename if set, otherwise just confirm it is no longer the entry screen). If `entry_hook` is empty, proceed with whatever scene loaded. If the scene is in an unexpected/error state (root name is `?` or empty), stop and report the failure.

**Then confirm the tree is actually running.** `ping` reports this without being asked, and it is the gate for everything below (gh#6):

```bash
"$PY" tools/devtools.py ping | grep -i "PAUSED" && echo "TREE IS PAUSED - stop"
```

A `tree is PAUSED` here **stops the run**. The bridge is `PROCESS_MODE_ALWAYS`, so every verb keeps answering on a frozen game and the numbers look healthy: `performance` reports a plausible FPS for a tree that is not stepping (it now says `TREE IS PAUSED` first, but a run that reads only the exit code will not see it), `validate-all` passes, `screenshot` captures a still frame, and every Phase 4 assertion about a tween, a timer or a `queue_free` fails for a reason that has nothing to do with the diff. This is the common case for a project that boots behind a title screen with `get_tree().paused = true` and has no `entry_hook` — the pause is exactly what the hook exists to undo, so an empty hook plus a paused tree means the config is **incomplete**, not that the game is fine. Set `entry_hook` to the menu's own start handler (or `pause false` if the project has no such handler) and re-run; do not carry a paused tree into Phase 3.

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
"$PY" tools/devtools.py performance --reset-baseline
```

## Phase 3: Validation

**One command does the whole sweep:**

```bash
"$PY" tools/devtools.py findings
```

`findings` runs scene validation, UI layout, UI reachability, declared-but-unconnected signals, and FPS/orphan-growth in a single bus call, and returns one flat findings list. This is the zero-config half of the harness — it asserts things this project never asked it to, which is exactly why it catches what a project-authored suite omits. **Report its output verbatim in the Phase 3 summary**, including a clean one.

Two numbers in that output are not optional reading:

- **`N finding(s) across K of M checks`** — if `K < 5`, some check did not run. A check that could not run is listed under `checks_skipped` with a reason, and a report missing a check is not a clean report. Say which were skipped and why.
- **`new_count` vs `pre_existing_count`** — only NEW UI findings gate (see the baseline discussion below).

Add `--no-scenes` to skip the scene-validation pass when it is slow and the diff touched no `.tscn`; say so if you do. Then:

```bash
"$PY" tools/devtools.py screenshot   # visual verification; short `sleep` first if you just changed state
```

**When a finding fires, re-check that one thing alone after fixing it** — `validate-ui`, `reachable-ui`, `performance` and `validate-all` are still individually callable and are much faster than the full sweep.

On the orphan check specifically: gate on `orphan_growth`, not the absolute count. A real project reports dozens of orphans on a fresh launch before any test action, so the historical `orphan_max: 0` was unreachable — and a threshold nothing can ever satisfy trains you to skip the check entirely. Growth-since-baseline is the number that actually means "this change leaks". Report the absolute count alongside it for context, and if `orphan_growth` is unavailable (older harness install), say so rather than silently falling back to a check you know is noise.

If `config.safe_area_inset` is non-zero the UI pass also reports `ui_outside_safe_area` for Controls straying under an overlay/notch/rounded corner; with an all-zero inset that check is skipped entirely — and will say so.

No game-specific exceptions are baked in, but a *permanent* finding is a real
category and 0.12.0 gives it somewhere to go. `validate-ui` splits findings into
`NEW` and `PRE` against `user://ui_findings_baseline.json`, keyed on (rule, node
path) rather than on the message, and **only NEW findings fail the check**. Two
projects independently stalled here — a `+125` popup resting at alpha 0 between
pops (`ui_transparent` forever) and a diegetic HUD whose screen position is
wherever the player is standing — and a check that can only ever be ignored has
been switched off in practice.

If the run reports `No UI findings baseline`, every finding gates; that is the
correct state for a project that has never triaged its UI. Accept the current set
with `"$PY" tools/devtools.py validate-ui --baseline-write` **only after reading
each finding** and saying in the summary which ones you are accepting and why. A
baseline written without that read is indistinguishable from deleting the check.
`--no-baseline` re-reports everything for a one-off audit.

**Upgrading to 0.17.0 with an existing baseline: re-audit it.** Before 0.17.0 the
overflow and off-screen checks measured Controls in `CanvasLayer` space against a
viewport measured in pixels, so any project with a scaled UI layer accumulated
false `ui_overflow` findings — on one real project, 51 of 51 were false, and it
had baselined 53 of them to get a usable gate. Those entries no longer match
anything and are harmless, but they are also hiding how few real findings there
were. Run `validate-ui --no-baseline`, read what is left, and re-write the
baseline from that instead of carrying the old one forward.

**Capture a scene-tree snapshot before moving on.** Phase 5 uses it to compute which changed files this run actually reached; it costs one command and cannot be reconstructed after the game exits.

```bash
mkdir -p .devtools   # first run of a fresh clone has no .devtools/ yet
"$PY" tools/devtools.py scene-tree > .devtools/tree-phase3.json
```

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

**Write down one prediction before you test anything.** One line, held until Phase 6:

> Expected: <what runtime should reveal that reading this diff cannot>

If you cannot name anything — the change is a rename, a comment, a constant with no
observable effect — **say that instead**, and expect to record the run as `overkill` in
Phase 6. That is a legitimate and useful outcome, not a failure to try hard enough.

This exists because "using the harness was worth it" is easy to write after any run that
passed, and hard to write honestly with a prediction already on the page that the run
merely confirmed. Write it first; it costs one line and it is the only thing standing
between the log and a stream of self-congratulation.

### Step 2: Discover the project's registered debug verbs

The core exposes a fixed set of generic verbs; individual projects register their own domain verbs in their DevTools extension. Discover them:

```bash
"$PY" tools/devtools.py list-commands
```

This prints all currently registered action strings. Any verb beyond the generic set (below) is project-specific and can be invoked verbatim:

```bash
"$PY" tools/devtools.py cmd <verb> --args '{"key": value}'
```

`cmd` sends `{action:<verb>, args:<parsed json>}` to the bus and prints the `{success, message, data}` result. Use this for domain setup/trigger steps that the generic primitives can't express (e.g. a project verb that spawns an entity, resets a session, or sets a batch of levels).

### Step 3: Generic primitives available for building tests

| Command | Use for |
|---|---|
| `get-state --node PATH --property NAME` | Read node properties (primary assertion tool). `--property` is repeatable — always use it; an unfiltered `Label` returns ~120 keys. A **dotted** name walks into Resources and Dictionaries (`--property texture.region`, `--property slot_data.item.name`), so a nested value is one call rather than a `run-method` to fetch the sub-object; an unknown name exits 1 instead of reporting absence as a value. Assert transforms on `data.transform`, which is always present: Godot hides `position`/`scale`/`rotation` on container children, so a scale animation on a `VBoxContainer` child is invisible to the property dump while working perfectly on screen |
| `find-nodes --class X --where prop=value` | Identify a node by what it *is*, instead of one `get-state` round trip per auto-named sibling. `--class` / `--group` / `--method` pick the population, `--where NAME=VALUE` narrows it (repeatable, dotted paths allowed), `--property NAME` reports a value per hit, `--root` confines the search. A node lacking the property is a non-match, never an error, so a predicate is safe across a mixed subtree |
| `step-time --seconds N` | Advance ~N game-seconds with `time_scale` pinned to 1.0 — for sampling a tween at a chosen moment instead of guessing with `set-game-speed` + sleeps. Physics time is exact; process-driven tweens (the `Tween` default) land within ~1 frame, so compare the returned `process_seconds` rather than assuming |
| `touch <press\|release\|drag\|clear\|list> --index N --pos X,Y` | Real `InputEventScreenTouch`/`Drag` — the only way to exercise multi-touch |
| `set-feature --touchscreen true` | Make touch UI visible on desktop (it hides itself when no touchscreen is reported). Set it **before** the scene loads: a Control that read availability in its own `_ready()` won't re-evaluate |
| `set-state --node PATH --property NAME --value V` | Set a raw property (see pitfall about signals below). Dotted paths write through the same walk `get-state` reads through, so a knob one level in (`environment.ambient_light_energy`, `mesh.material.albedo_color`) is reachable — that mutates the **Resource**, so a shared material changes for every node using it. A component of a built-in struct (`size.x`) is refused, naming the call that works |
| `run-method --node PATH --method NAME --args "[...]"` | Call any method on a node — **preferred** for anything that should emit a signal / run side effects |
| `curve --node PATH --method NAME --from N --to M` | Sweep a pure method over an integer range and get the whole series back (`points`, plus `min`/`max`/`sum`) — a difficulty or cost ramp asserted as data instead of hand-evaluated in prose arithmetic, which is where the slips happen. `--step`, `--args` for the method's other parameters, `--arg-index` for which one the sweep fills. Capped at 500 points; the bus serves one command at a time and a typo'd range would wedge it |
| `input tap ACTION --hold N` / `input press` / `input release` | Simulate input actions defined in the project |
| `press --node PATH` | Emit `pressed` on the nearest `BaseButton` at or under a path (a container path works — it looks one level down). **Use this instead of `run-method` on the button's callback:** calling `_on_thing_pressed` directly proves the action and not the wiring, which is how a mis-wired `pressed.connect` used to ship green. A `disabled` button is refused rather than pressed, because a real press would also do nothing. `--toggle BOOL` sets a `toggle_mode` button's state before emitting |
| `mouse-move --relative DX,DY [--steps N]` | A real `InputEventMouseMotion` with a chosen `relative`, through `Input.parse_input_event` like `key` — the only way to drive a mouse-look camera or anything else that reads motion deltas. `--steps N` splits the delta into one event per frame for handlers that clamp per event. If the reply says the cursor is `captured`, your physical mouse is steering the same camera between commands — release it (`Input.mouse_mode`) before a read that must be stable |
| `reload res://PATH` | Re-read an edited shader / `.tres` / texture into the running game; every node already holding it sees the new content without a relaunch. `was_cached: false` means nothing held it and nothing changed on screen |
| `raycast --from X,Y[,Z] --to X,Y[,Z] [--mask N]` | What a collision mask would actually hit — two components query the 2D space, three the 3D space, and a 2D ray on a tree whose only colliders are 3D is refused naming the fix. — the only form the question takes once a project has more than one physics layer — with the bits resolved to the layer names in `project.godot`, so the answer is readable. `--areas` also hits `Area2D`, `--exclude` drops a collider. Engine sharp edge it inherits: a ray *starting inside* a shape reports nothing, so several bisecting probes can all come back `clear` with a wall between them — cast from outside |
| `sample-pixels --rect X,Y,W,H` | Mean / dominant / brightest / darkest colour over a screen rect, so a colour regression is assertable rather than eyeballed. Same capture path as `screenshot`, summarised instead of saved; `dominant_share` says how much of the rect that colour owns |
| `set-game-speed N` | Speed up (or slow down) time-dependent behavior (timers, tweens, physics). Refuses a scale below 0.01 — that is a freeze reported as a set, not a speed |
| `performance --frames N [--by-type]` | FPS as a **mean over N frames** with min/max, marked `STILL SETTLING` when the window's halves disagree — read it after `wait-frames 60`+ past any settings change, never straight after. `Total nodes … growth +N` is in-tree accumulation the orphan count cannot see; `--by-type` names the classes that grew |
| `wait-frames N` | Advance N physics frames deterministically |
| `node-bounds PATH` | Exact **screen-space** position/size of a node (ground truth for layout/movement). Ancestor `CanvasLayer` transforms are applied, so a HUD built on a scaled layer reports where it actually renders. Prints `GEOMETRY CAVEAT` when the game is headless — the window is 64×64 there, so a node the game centres from `get_window().size` sits off-viewport headless and centred for a player; **confirm any off-viewport verdict windowed before reporting it** (H-051) |
| `aabb --node PATH` | Merged **world-space** AABB of a 3D node's geometry — `min`/`max`/`size`/`center`, `top_y`, `bottom_y`. The 3D answer to "is this actually on the table / sunk into the floor / overlapping that". Excludes `Light3D` (an `OmniLight3D`'s AABB is a cube of twice its range); fails rather than returning a zero box when a node has no geometry |
| `scene-tree --depth N` | The live hierarchy as JSON. Every node carries `script` (its `res://` script path, `""` if none) and `scene_file` (set on instanced scene roots) — which is how Phase 5 computes reach, and also the fastest way to map a changed `.gd` to the node path that runs it |
| `ui-snapshot` / `ui-snapshot-diff` | Structured UI state; diff against a saved baseline |
| `clear-nodes --group N` / `--class C` / `--method M` | Free matching nodes. Prefer `--via-method NAME` (with `--via-args JSON`), which calls the game's own removal path on each match: bare `queue_free()` skips death handling entirely, so a cleared enemy drops nothing, pays no xp, and the teardown you thought you tested never ran |
| `screenshot` | Visual verification (always `sleep 0.5`–`1` after a state change first) |
| `cmd <verb> --args '{...}'` | Any project-registered verb from `list-commands` |
| `harness-version` | The installed harness revision, game-side and client-side. Fills the `harness:` field of every gap logged in Phase 6; a non-zero exit means the addon and the client are on different versions (re-run `/scaffold-godot-harness`) |

### Step 4: Design, execute, verify

For each significant change in the diff, design a test that:
1. **Sets preconditions** — via `set-state`, `run-method`, `input`, `set-game-speed`, or a discovered `cmd <verb>`.
2. **Triggers the behavior** — call the method, send the input, spawn/advance frames.
3. **Asserts the observable effect** — read it back with `get-state` on the node(s) the diff touched, or `node-bounds` / `ui-snapshot` / `screenshot`. Verify through concrete state, never through domain intuition.

Also test at least one guard/edge case per behavior (e.g. the effect must NOT happen when a precondition is absent). Report each test with a name, what it verified, and pass/fail.

**Record a check's FIRST observation, not its final state.** If a check fails, you fix the code, and it then passes, that check is recorded `"result": "fail"` with `"fixed_in_run": true` — *not* `pass`. A `verdict: pass` run containing failed checks is a normal, correct, and highly informative row: it is what a run that did its job looks like. Rewriting the check green once the bug is gone erases the only evidence the run was worth doing — the first 52 runs recorded in anger produced 319 checks and not one `fail`, while their prose described real defects caught. Whatever you fix between writing a check and finishing the run belongs in `found` (Phase 5).

**Generic worked example** — suppose the diff adds a method `apply_damage(amount)` to a node that reduces a `health` property and emits a `health_changed` signal, but only while `is_alive` is true:

```bash
# Discover the node's path from the scene tree, then inspect current state
"$PY" tools/devtools.py get-state --node "/root/<Root>/Entities/Enemy"
# Precondition: ensure it is alive (prefer run-method if a setter emits a signal)
"$PY" tools/devtools.py set-state --node "/root/<Root>/Entities/Enemy" --property is_alive --value true
# Trigger via run-method (emits health_changed, unlike a raw set-state on health)
"$PY" tools/devtools.py run-method --node "/root/<Root>/Entities/Enemy" --method apply_damage --args "[30]"
# Assert the observable effect
"$PY" tools/devtools.py get-state --node "/root/<Root>/Entities/Enemy"   # expect health decreased by 30
# Guard case: dead entities must not take damage
"$PY" tools/devtools.py set-state --node "/root/<Root>/Entities/Enemy" --property is_alive --value false
"$PY" tools/devtools.py run-method --node "/root/<Root>/Entities/Enemy" --method apply_damage --args "[30]"
"$PY" tools/devtools.py get-state --node "/root/<Root>/Entities/Enemy"   # expect health unchanged
```

### Step 5: Promote the durable checks into `test_dir`

A Phase 4 check exists for the length of this run and then only in the transcript. Before you move on, decide for each check you just wrote: **does it need a live, playing game?**

- **No** — it is pure logic, a resource, a layout that `_T.instantiate_ui` can resolve, a data table. Write it as a `test_*` method in the project's selftest (`test_dir`, seeded as `test/unit/test_selftest.gd`). It then runs in Phase 1 of *every* future `/verify`, for free, forever, and the next session inherits it.
- **Yes** — real input over time, physics, a scene mid-transition, a tween landing. It stays a Phase 4 bridge check. Say so; that is a fine answer.

This is the whole reason `test_dir` is re-run automatically. The check you just proved works is the cheapest test the project will ever get, and leaving it in the transcript throws it away. Do not create a new test file if one already exists — add the method to it, so `Suite:` counts scripts a human would want to read rather than one per session.

Report the promotion explicitly: `promoted 2 of 5 checks into test/unit/test_selftest.gd; 3 need the running game`. If you promoted nothing, say which of the two reasons applied.

### Generic pitfalls (apply regardless of project)

- **Prefer signal-emitting `run-method` over raw `set-state`.** A direct `set-state` writes the property but bypasses any setter/signal, so dependent UI and systems won't react. If a value is normally changed through a method that emits a signal, call that method instead.
- **Toggle stateful UI once per launch.** UI opened/closed via tweened toggles often have a guard that blocks rapid re-entry. Trigger such a toggle at most once per launch; if state looks corrupted, `quit` and relaunch rather than toggling again.
- **Screenshots need a short sleep.** State changes (tweens, physics, layout) are not instant — `sleep 0.5`–`1` before `screenshot`. For deterministic ground truth prefer `node-bounds` or `ui-snapshot` over pixels.
- **DevTools input may not reach gated scenes.** Simulated input drives both the polled action state and a dispatched `InputEventAction`, so polling (`Input.is_action_pressed`) and event-based (`_input`/`_unhandled_input`) handlers both see it. It can still be swallowed by an unfocused window or an entry/menu screen that gates gameplay: if input tests appear to do nothing, ensure you advanced past the entry screen via `config.entry_hook` (Phase 2), and re-apply the hook manually if you relaunched mid-run.
- **A run that never changes is broken, not passing.** If repeated samples return identical values — especially all-zero ones — suspect the session is dead or frozen before you conclude the code under test is wrong. Check the `status` field the project's status provider attaches to every response (see the extension section of the harness CLAUDE.md); if the project has not registered one, verify liveness explicitly before trusting a flat result. A dead player or a paused tree answers every query with well-formed zeros.
- **One in-flight command at a time.** The bridge is a single command/result file pair, so concurrent callers overwrite each other and replies come back for the wrong request (typically surfacing as a missing key in the response). Never poll from a background thread while sampling on the main one — serialize every call.

## Phase 5: Record the run, then shut down

**Capture what the session loaded before quitting** — a tree snapshot while everything the tests spawned still exists, plus the cumulative `scripts-seen` set, which also covers scripts whose nodes lived and died *between* snapshots:

```bash
"$PY" tools/devtools.py scene-tree > .devtools/tree-phase4.json
"$PY" tools/devtools.py --json scripts-seen > .devtools/scripts-seen.json
"$PY" tools/devtools.py quit; echo "exit=$?"
```

`quit` waits for the process to actually go and **exits 1 if it survived** (`--wait SECONDS`, default 10), naming the survivor. It also sweeps every process this project launched earlier that is still alive (the `_console.exe` wrapper, an engine abandoned two launches ago) from `.devtools/launched.jsonl`, start-time verified. Do not ignore that code: a Godot that outlived its `quit` still owns the bus, so the next run's `launch` refuses to start or — worse — a second instance answers the same command file and replies come back for the wrong request. On a survivor, run `"$PY" tools/devtools.py quit --kill` (terminates exactly those pids — never kill by image name, other sessions run games on this machine); on Windows the printed fallback is `Stop-Process -Force -Id <pid>` in PowerShell, because `taskkill /F` through the Bash tool's MSYS layer becomes `F:/` and fails.

**Check what the engine touched.** Diff the current git status against the Phase 2 snapshot; any file changed now that is **not** in your session's edit set is engine re-serialization (`.tscn`, `.tres`, `project.godot` are the usual suspects) — report those separately as "engine-touched (re-serialization)", and do not stage them blindly with your work:

```bash
git status --porcelain > .devtools/git-status-after.txt
diff .devtools/git-status-before.txt .devtools/git-status-after.txt || true
```

**Read reach before you judge the run** — it decides between `warranted` and `insufficient`, and it is a fact rather than an impression:

```bash
"$PY" tools/verify_ledger.py reach \
  --scene-tree .devtools/tree-phase3.json \
  --scene-tree .devtools/tree-phase4.json \
  --scripts-seen .devtools/scripts-seen.json
```

It prints **two denominators**: `worktree` (this session's edits — the honest per-session number) and `branch` (everything since the merge base, which dilutes as the branch grows). Judge the run on the worktree line.

Three things are no longer scored as misses, and each is printed by name so you can disbelieve it:

- **Scripts under `test_dir` are out of the denominator entirely.** They ran in Phase 1 and can never appear in a game session's scene tree, so counting them capped the ratio below 100% for anyone who wrote a test alongside a fix. They are listed as *excused, not credited* — reach cannot tell you they passed, only that it has nothing to say.
- **`reach_aliases`** in `devtools_config.json` lets the project name an observed Node that vouches for a script no snapshot can ever see (a `RefCounted` helper, a `Resource` subclass). Those land in a **separate** `reached_alias` bucket with the voucher printed alongside, never folded into `reached` — a config declaration is the project's claim, not this run's observation.
- **Autoloads and the DevTools extension** are `reached_implicit`: they run in every session but own no persistent node.

**A checkout with no git repository prints `reach: unavailable (not a git repository)`, not a ratio.** There is no changed set for the run to have covered, so there is no denominator — and a `0/0` reads as "nothing to check" when the truth is "cannot tell". This is *not* `insufficient`: that verdict is a claim about the run, this is a statement about the checkout. Judge the run on its checks and say plainly in the summary that reach could not be computed. A genuine `0/0` in a real repository is a different line and says so.

So `reached 1/4` with three annotated credits is not a bad run, and a 100% line built on aliases is not the same evidence as one built on observation. Quote the line as printed rather than reducing it to a fraction.

Then append this run to the ledger. **Writing `run.json` and running `record` are one step, not two — if you wrote `run.json` you MUST run `record` in the same breath**, in the same command block; a summary written from a `run.json` that never reached the ledger is a row lost forever. Write the results you have into a JSON object and hand it over; everything else — timestamp, sha, branch, changed files, and reach — is derived, not asked for:

```bash
cat > .devtools/run.json <<'EOF'
{"verdict": "pass",
 "lint":  {"exit": 0, "new": 0, "pre_existing": 7},
 "tests": {"exit": 0, "total": 111, "failed": 0},
 "runtime": {"launched": true, "scene": "<root scene>",
             "fps": 58.2, "orphan_growth": 3, "orphan_growth_exceeded": false},
 "checks": [{"name": "<Phase 4 test name>", "result": "pass"},
            {"name": "<a check that failed first>", "result": "fail", "fixed_in_run": true}],
 "duration_s": 94,
 "expected": "<the Phase 4 Step 1 prediction, verbatim>",
 "found": [{"what": "<what this run caught that the diff alone would not have>",
            "phase": "runtime", "static_would_have_caught": false}],
 "value": "warranted",
 "cheaper_alternative": "<what would have given the same confidence for less, or 'nothing'>"}
EOF
"$PY" tools/verify_ledger.py record \
  --scene-tree .devtools/tree-phase3.json \
  --scene-tree .devtools/tree-phase4.json \
  --scripts-seen .devtools/scripts-seen.json \
  --run .devtools/run.json
```

**`found` is required, and `[]` is a real answer.** It is the list of things this run caught that reading the diff would not have — one entry each, `{"what": ..., "phase": "import"|"lint"|"tests"|"runtime"|"other", "static_would_have_caught": true|false}`. Every other field in the row describes the run's *end state*, so a defect you found at minute four and fixed at minute six is invisible everywhere else: the checks get written green, the runners re-run clean, and the row is indistinguishable from one where nothing was ever wrong. Write `"found": []` when the run genuinely confirmed what you already knew — that is the honest answer and it is what `overkill` means. Omitting the key entirely records `null` (unrecorded) and draws a warning; it is not a synonym for empty.

`value` is `warranted`, `overkill`, `insufficient`, or `inconclusive` — the same verdict Phase 6 writes up in prose, recorded here as an enum so it is countable. `record` downgrades a self-reported `warranted` on two grounds, and says so on stderr: one whose changed files were never loaded becomes `insufficient`, and one whose `found` is empty becomes **`overkill`** — a run that caught nothing confirmed what was already known, whatever it felt like at the time. Do not pad `found` to keep the verdict: an `overkill` row is a useful row, and the pattern across many of them is the only thing that can tell you to run `/verify` less often. Leaving `cheaper_alternative` blank also draws a warning: it is the field that can say the harness was the wrong tool, and therefore the easiest one to skip.

`verdict` is `pass`, `fail`, **`aborted`** (the import gate or a runner exited `2`, or the game never came up — never file it as a pass), or **`partial`** — which `record` sets itself, downgrading a reported `pass` whenever any check in `checks` has `"result": "blocked"`: a check that could not run is not a check that passed. Mark checks you could not execute as `blocked` rather than omitting them.

Each check is `{"name": ..., "result": "pass"|"fail"|"blocked"}`, plus `"fixed_in_run": true` on a check that failed and was repaired before the run ended. Per Phase 4, `result` is the check's **first** observation — do not rewrite it green because the bug is now gone.

The row has no `import` field — `record` keeps only the keys above and silently drops any others, so do not invent one. An import-gate failure lands in `verdict`; name the gate that failed in the summary prose instead.

`record` **refuses to run with unknown reach**: no readable `--scene-tree` and no readable `--scripts-seen` is exit 1, telling you to re-capture before quitting or pass `--scripts-seen`. For a genuinely aborted run (the game never came up, so there is nothing to capture) pass `--no-reach`, which records `reach: null` on purpose — the escape hatch is explicit so a null reach is always a choice, never a default.

The command prints which changed files the run reached and names the ones it did not, with the same worktree/branch split and the same `implicit` / `alias` / excused-test annotations described above. **Carry the worktree reach line into the Pass/Fail Summary verbatim.** A green run on a file nothing touched is a statement about the diff, not about the running game, and it is the one failure this workflow cannot otherwise see — the summary says "all checks passed" either way.

Both `checks` and `verdict` are self-reported; reach is computed from the snapshots. If they disagree — every check passing on a file the snapshots say was never loaded — believe the snapshots and say so.

To read the accumulated history at any time:

```bash
"$PY" tools/verify_ledger.py stats
```

Commit `.devtools/verify-runs.jsonl` along with the change. It is the only record of how often this harness was load-bearing, and its value is entirely in being long. The `tree-*.json` and `run.json` scratch files are inputs, not records — leave them out of the commit.

## Phase 6: Log the run (REQUIRED)

Append an entry to `log-devtools.md` at the project root (create it if missing). It has two required halves: **was this run worth doing**, and **what was missing from it**. If the run hit no gaps, write one explicit "no gaps this turn" line — but the `Value:` block is required either way, and a clean run is exactly when it is most worth writing.

```markdown
## YYYY-MM-DD — <what this run verified>

- Value: **<warranted|overkill|insufficient|inconclusive>** — <one sentence of why>
  - Expected: <the prediction you wrote in Phase 4 Step 1, copied verbatim>
  - Got: <what runtime actually told you — quote the assertion, not "it passed">
  - Found: <what this run caught that reading the diff would not have, or "nothing">
  - Cheaper: <the cheapest thing that would have given the same confidence>

- Gap: **<what was missing>** — <the command run, the output it gave, the workaround used>
  - [G-001] status: open | seen: 1 | harness: 0.7.0
  - Improvement: <the smallest change that would have closed it>
```

### Choosing the verdict

| Verdict | When |
|---|---|
| `warranted` | Runtime produced a claim the diff could not. Name it specifically — "the tween rests at 0.85, not 1.0", not "verified the animation". **Requires a non-empty `found`**; Phase 5 downgrades a `warranted` with `found: []` to `overkill` automatically. |
| `overkill` | Everything passed and confirmed what was already known. Renames, comments, constants, pure refactors, and anything lint alone settled belong here. This is the verdict for `found: []`. |
| `insufficient` | It ran but could not reach or assert the thing that mattered. **Reach from Phase 5 decides this, not your impression** — if the changed file was never loaded, the verdict is `insufficient` even when every check passed, and a `reach_aliases` credit is a declaration rather than an observation, so it does not rescue one. Reach printed as `unavailable` does not decide it either way — that is the ledger declining to answer, not an answer of zero. File the gap. |
| `inconclusive` | Aborted (a runner exit `2` from the import gate, lint, or the test runner, or the game never came up), or the change was too small to judge. |

Three rules that keep this honest:

1. **Copy `Expected:` from Phase 4 verbatim.** Do not rewrite it to match what you found. A prediction edited after the fact is worse than no prediction, because it reads as evidence.
2. **`Found:` counts what you fixed mid-run.** A bug this run surfaced and you repaired before Phase 5 is exactly what belongs here — it is invisible in every other field, since the checks end green and the runners end clean. If nothing surfaced, write "nothing" and take the `overkill`.
3. **`Cheaper:` must name something concrete** — "reading `player.gd:40-60`", "the existing unit test", "lint alone, 4s", or "nothing, this needed the running game". "Probably still worth it" is not an answer to the question.

`overkill` is the verdict that will be under-reported, because a run that passed feels like a run that helped. If a stretch of entries contains no `overkill` at all, suspect the log before believing the harness.

All three also go into the ledger row in Phase 5 (`found`, `value`, `cheaper_alternative`), which is what makes them countable across runs rather than only readable one at a time.

The `[G-NNN]` status line is required — it is what lets a later reader tell an open gap from one fixed two versions ago:

| Field | Rule |
|---|---|
| `[G-NNN]` | Next unused id in this file. **Stable, never reused.** |
| `status:` | `open`, `fixed` (add `fixed-in: X.Y.Z`), or `wontfix` (say why on the Improvement line). |
| `seen:` | Bump the **existing** entry when a known gap bites again; do not file a second one. |
| `harness:` | The installed version, from `"$PY" tools/devtools.py harness-version`. Read it once at the start of the run — a gap that can't be tied to a version can't be told from a regression later. |

Before writing a new gap, scan the file for one that already describes it. A `seen: 3` is the strongest signal this log can produce; three separately-worded entries are the weakest. When a gap you find here is already closed by the installed version, mark it `status: fixed | fixed-in: <version>` instead of leaving it to be re-upstreamed.

This is not bookkeeping. Every capability the harness has beyond its first version — the status provider, the node-path normalization, the property filter, the touch verbs, the orphan baseline — exists because a run like this one wrote down what it couldn't do. Quote real output; a gap without evidence can't be acted on later.

Open gaps only become fixes once they reach the harness repo, which is a one-liner:

```bash
"$PY" tools/upstream_gaps.py log-devtools.md --into /path/to/godot-selftest-harness/log-devtools.md
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

Report results as a table: Godot binary used, harness drift (Phase 0) if any, config thresholds (fps_min / orphan_growth_max), import gate status, lint status, unit test status (with the `Selected: N of M discovered`, `Autoloads: N of M ready` and `Assertions: N executed` figures, not just pass/fail — a suite reporting 0 assertions or an unready autoload verified far less than its pass count suggests), live scene name (and which entry point fired), validate-all, validate-ui, performance (FPS + orphan growth vs baseline), and each change-specific test (name + what it verified + pass/fail). List the project verbs discovered via `list-commands` that you used. Also check the Godot terminal output for GDScript runtime errors or warnings.

**Include the worktree reach line from Phase 5** (the per-session denominator; report the branch ratio alongside it, labeled as such), naming any changed file the run did not reach. Do not report a run as verified when its changed files were never loaded — say which ones were covered at runtime and which were only read. If all checks pass *and* the changed files were reached, the commit is safe to proceed; if checks pass but reach was partial, say so plainly and let the user decide.
