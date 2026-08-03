# Devtools / `/verify` Gaps Log — harness development

Gaps found while building `godot-selftest-harness` itself, and the smallest improvement
that would close each one.

This repo is the plugin, not a Godot game, so the entries here are a level up from the
ones the scaffolded `templates/log-devtools.md` collects: they are about what's missing
when **developing and validating the harness**, not when using it on a game. Same format,
same rule — an entry with quoted evidence is worth something later; "it was awkward" is not.

## Ids

Every gap carries `- [<id>] status: … | seen: N | harness: X.Y.Z` (the format
`templates/log-devtools.md` documents in full). Two id namespaces live in this file:

- **`H-NNN`** — a gap found while developing the harness itself. Allocated here, stable,
  never reused.
- **`<project>:G-NNN`** — a gap upstreamed from a project's own log by
  `tools/upstream_gaps.py`, qualified with the source project so that two games' `G-007`
  can't collide. Never renumber these; the project's log is the other half of the pair.

`status:` is `open`, `fixed` (with `fixed-in: X.Y.Z`), or `wontfix`. A gap whose fix
shipped only in part stays **open** — partial credit is what makes a log stop being
answerable. Bump `seen:` when a gap recurs instead of writing a second entry.

---

## 2026-08-01 — Ship the gaps log, close what it recorded (0.4.0)

Four agents extended `lint_project.gd`, `run_tests.gd`, `dev_tools.gd` and `devtools.py`
in parallel against a wire contract fixed up front. Verified against Godot 4.7.1 and
4.6.1. Gaps below are the ones the *process* exposed, not the ones the game's log listed.

- Gap: **each half of the bridge passed its own tests while three request/response key
  mismatches sat between them.** The GDScript and Python sides were built concurrently
  against a written contract, and each was tested against a hand-rolled fake of the
  other. Both reported green. Running the real client against the real game immediately
  found: `set_feature` returns the resulting state as `data.touchscreen_available` while
  the client read `data.touchscreen`; `touch_clear` returns `data.released` while the
  client read `data.cleared`; `step_time` returns `physics_seconds`/`frames_advanced`
  while the client read `advanced`/`frames`. The `touch_clear` one is the worst of the
  three — it printed `No active touches to clear` **while successfully clearing two**,
  a tool lying about what it just did.

  Evidence, before the fix:
  ```
  $ devtools.py touch drag --index 0 --to 150,250 --steps 3
  Active touches: 0@(150.0, 250.0), 1@(300.0, 400.0)
  $ devtools.py touch clear
  No active touches to clear
  ```
  - [H-001] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: 0.5.0
    (the three mismatches shipped fixed in 0.4.0; the structural gap — nothing exercises
    the two halves *together* — is what stays open)
  - Improvement: **a bridge contract test that ships with the plugin.** A script that
    creates a scratch Godot project from `templates/`, launches it, drives every generic
    verb over the real file bus, and asserts the keys each side promises. It would have
    caught all three before they shipped, and it is the natural thing to run after
    `/verify`'s Phase 0 drift check reports a difference. Nothing in this repo currently
    exercises the two halves *together* — the gap is structural, not an oversight.
  - Improvement (cheaper, partial): have the client warn when a `data` dictionary
    contains none of the keys it intends to print, instead of silently printing nothing.
    Two of the three mismatches were invisible precisely because the missing-key path
    printed a friendly fallback line.

- Gap: **a test script with a parse error still `load()`s, and the failure was silent.**
  `load()` returns a non-null `GDScript` for an unparseable file; `script.new()` then
  raises a runtime error that aborts the *calling* function. `run_tests.gd` printed
  `Total: 0 | ALL TESTS PASSED` with **exit 0** while a valid test file sat undiscovered
  next to the broken one. This shipped in every version before today.
  - [H-002] status: fixed | fixed-in: 0.4.0 | seen: 1 | harness: 0.3.1
  - Improvement (done): `can_instantiate()` guard plus an isolated `_instantiate_test()`
    helper, so a surviving error can only abort that helper. Exit `2` now means "the
    runner could not run".
  - Improvement (still open): `/verify` should assert the discovered test count is
    non-zero and ideally non-decreasing. "Passed" with a count of 0 is not a pass, and
    the runner reporting it correctly doesn't help if nobody reads the number.

- Gap: **a runtime error inside a test method is indistinguishable from a pass.**
  GDScript has no exception handling. The error aborts only that method and returns the
  declared return type's default — `""` for a `-> String` test, which is exactly the
  success value. Detecting it via the declared return type in `get_script_method_list()`
  was tried and backed out: the aborted call returns `typeof 4`, empty String, byte for
  byte what a genuine pass returns.
  - [H-003] status: wontfix | seen: 1 | harness: 0.4.0
    (a property of GDScript, not of the runner - documented in three places instead)
  - Improvement: none available inside the runner — this one is a property of the
    language. `/verify` must capture and read **stderr**; `[ERR]` / `[SCRIPT ERROR]`
    lines are the only evidence. Documented in the runner header, the README and
    `/verify` rather than papered over. Worth revisiting if Godot ever exposes a
    script-error hook the runner could install.

- Gap: **`command -v python3` succeeds on Windows and then refuses to run.** Windows
  ships a Microsoft Store *App execution alias* stub at `python3.exe`. Existence is not
  executability, and the failure only appears at invocation:
  ```
  Python was not found; run without arguments to install from the Microsoft Store,
  or disable this shortcut from Settings > Apps > Advanced app settings
  ```
  Caught while smoke-testing the scaffolder's `Stop`-hook wiring, which would otherwise
  have installed a hook that failed silently on every turn.
  - [H-004] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0
    (the probe fix shipped in 0.4.0; the cheat-sheets below are the part still open)
  - Improvement (done): probe interpreters by executing them (`"$c" -c "import sys"`).
  - Improvement (still open): every `python3 tools/devtools.py ...` line in `README.md`,
    `commands/verify.md` and `templates/CLAUDE.harness.md` still assumes a working
    `python3`. `/verify` Phase 0 now resolves `$PY` properly, but the cheat-sheets do
    not. Either the scaffolder should write the detected interpreter into the project's
    `CLAUDE.md`, or `devtools.py` should ship with a launcher shim.

- Gap: **nothing in this repo validates the templates before they ship.** The plugin is
  a directory of files copied into other projects; a syntax error in `dev_tools.gd`
  reaches a user's game before anything notices. Every check this session was ad-hoc —
  a scratch project assembled by hand in the scratchpad, a `--check-only` loop typed out
  per file.
  - [H-005] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0
  - Improvement: a `tools/check_templates.sh` (or a CI workflow) that builds the scratch
    project from `templates/`, parse-checks every `.gd`, `py_compile`s every `.py`,
    validates every `.json`, and runs both headless runners expecting exit 0. All of it
    ran green today; none of it is repeatable without retyping.

- Process note: **parallel agents on disjoint files worked, and the seam between them is
  where the bugs were.** File ownership prevented every merge conflict, and the two
  independent runners (`lint_project.gd`, `run_tests.gd`) came back clean. All three
  defects were on the one seam that spanned two owners — the bridge protocol. Worth
  remembering that the contract is the risk surface, so the contract is what needs a
  test, not the halves.

---

## 2026-08-01 — Add `PURPOSE.md` and this repo's own `CLAUDE.md`

- Gap: **the plugin repo had no always-on context of its own.** Every session started by
  reading the 461-line `README.md` to re-derive things that never change — that this repo
  is not a Godot project, that a game-specific verb belongs in a target's `commands.gd`,
  that a verb change touches four docs. `README.md` is a reference manual, so the working
  rules sit spread across it by topic rather than stated once.
  - [H-006] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.4.0
  - Improvement (done): `CLAUDE.md` (working rules, repo map, gotchas) and `PURPOSE.md`
    (design commitments and non-goals). The plugin scaffolds a `CLAUDE.md` into every
    target project and had none itself.

- Gap: **nothing keeps `CLAUDE.md`'s "docs move together" list honest.** A verb added to
  `dev_tools.gd` and `devtools.py` with no matching edit to `README.md`,
  `templates/CLAUDE.harness.md`, or `commands/verify.md` is exactly the cheat-sheet drift
  the list warns about, and it is currently caught by memory alone.
  - [H-007] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0
  - Improvement: the generic-verb set is machine-extractable from the `register_command(`
    calls — a check could diff it against the three docs and fail on a verb that only
    exists in the code.

- Gap: **still no way to validate a template change** — unchanged from the entry above,
  second appearance. `CLAUDE.md` now writes down the manual scratch-project procedure,
  which makes the gap cheaper to work around and no closer to closed.
  - [H-005] status: fixed | fixed-in: 0.8.0 | seen: 3 | harness: 0.5.0
  - Improvement: unchanged — `tools/check_templates.sh` per the previous entry.

## 2026-08-01 - Upstreamed 14 open gap(s) from gather (harness 0.4.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **Scaffold overwrote `tools/*` and left `.bak` files it can never clean up** —
  step 4 backs up on any byte difference, so a pure version bump of the harness's own
  files produced `tools/lint_project.gd.bak`, `tools/run_tests.gd.bak`,
  `tools/devtools.py.bak` as untracked repo noise. Diffing each showed only upstream
  template evolution (new flags, new docstrings); no project edits existed to protect.
  Workaround: diffed all three by hand to confirm they were disposable, then reported
  them to the user rather than deleting unprompted.
  - [gather:G-001] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have step 4 skip the backup when the existing file matches a *known
    previous template version* — e.g. stamp a `# harness-version: N` header into copied
    tools and only back up when the target's stamp is absent or modified.

- Gap: **Step 7's config patch has no way to know a key was deliberately customized** —
  merging `hud_layer_name` worked only because the existing value (`UI2`) happened to be
  non-default; a project that legitimately set it back to `"HUD"` would be
  indistinguishable from an unpatched default on the next refresh.
  - [gather:G-002] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: write a `"_scaffold_defaults"` sidecar block into
    `devtools_config.json` recording the values scaffold last wrote, so a later run can
    diff "what I wrote" against "what's there now" and only overwrite untouched keys.

- Gap: **No verb reports which harness version is installed** — deciding whether this
  refresh was a no-op or a real upgrade required diffing template files against the
  repo by hand. `list-commands` shows verbs but not the harness revision.
  - [gather:G-003] status: fixed | fixed-in: 0.5.0 | seen: 2 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: add a `harness-version` verb (and a line in `lint_project.gd`'s header
    output) reporting the template revision the installed files came from.

- Gap: **`run_tests.gd --filter` matches method names only, so a filter that hits nothing
  exits 0 with a full skip** — a subagent ran
  `run_tests.gd -- --filter spawner` on a new `test/unit/test_enemy_spawner.gd`; every
  test in the suite was skipped and the runner still reported
  `Total: 0 | Passed: 0 | Failed: 0` with `EXIT=0`. That is byte-for-byte what a clean
  pass looks like to an agent grepping for the exit code. Workaround: fell back to
  running the whole suite, which defeats the point of a filter when several agents are
  adding test files concurrently.
  - [gather:G-004] status: fixed | fixed-in: 0.5.0 | seen: 2 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: match `--filter` against the test *script filename* as well as the
    method name, and make a run that selected zero tests exit non-zero (or at minimum
    print `filter '<x>' selected 0 of N tests` as a warning).

- Gap: **Nothing in the harness lets more than one agent verify at a time** — the bridge
  is a single command/result file pair, so four parallel subagents had to be forbidden
  from launching the game at all, and one owner (me) does every runtime check serially.
  `godot --headless --path . --import` is a second shared-state hazard: the class cache
  is a single file, so a new `class_name` from any agent forces a global rebuild.
  Workaround: pre-created stub files declaring all four new `class_name`s, ran `--import`
  once up front, then told every agent not to run it.
  - [gather:G-005] status: open | seen: 3 | harness: 0.4.0 | source: gather 2026-08-01
    (0.5.0 shipped the `--session` half, so N instances no longer answer each
    other's commands; the `--import` class-cache race this entry also names is
    untouched, so the gap stays open)
  - Improvement: teach `tools/devtools.py` to derive its command/result filenames from a
    `--session` id (defaulting to the current behaviour), and have `scaffold` document a
    `--session` + `use_custom_user_dir` recipe, so N agents can each own an instance.

- Gap: **New scripts created outside the editor have no `.uid` sidecar and lint does not
  notice** — `lint_project.gd` reported `UIDs: OK` for `test/unit/test_enemy_spawner.gd`
  while the file had no sidecar at all, because the check only validates sidecars that
  exist. CLAUDE.md requires committing them alongside the script.
  - [gather:G-006] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have the UID pass flag `.gd` files under `scan_root`/`test_dir` with no
    `.uid` sidecar as a warning, so the omission is visible before commit rather than at
    review time.

- Gap: **No entry in this log has a status, so "is this already fixed?" is unanswerable
  from the file** — the Format section says "Log closures too", but every one of the six
  entries reads as permanently open. Answering whether the loop had ever actually closed
  required leaving the project entirely: `git -C ~/Documents/GitHub/godot-selftest-harness
  log --oneline` showed `922c45d Ship the devtools gaps log, and close the gaps it recorded
  (0.4.0)`. Nothing in this repo records that.
  - [gather:G-007] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: give each gap a stable id and a status line —
    `- [G-007] status: open | fixed-in: 0.5.0 | seen: 2` — so a fixed gap can be filtered
    out before the log is pasted back, and recurrences can be counted instead of narrated.

- Gap: **The `Stop` hook checks that the log file changed, not that anything was said** —
  `tools/check_devtools_log.py:132` is `missing = [f for f in log_files if f not in
  normalized]`, so any byte-level change to `log-devtools.md` satisfies it. A session that
  appends "no gaps this turn" forever passes the check forever, which is precisely the
  decay mode the hook exists to catch.
  - [gather:G-008] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: require an entry whose `## ` heading carries today's date, rather than
    treating the file's mere presence in `git status` as compliance.

- Gap: **Nothing detects a second Godot instance still holding the bridge**, and the
  error it produces points at the wrong cause. A prior session's process was still
  alive; `devtools.py ping` answered `game not running: 'ping' was never picked up`
  while `tasklist | grep -i godot` showed **two** live PIDs, and a save/load test in
  between returned an empty reply that crashed the python client with
  `json.decoder.JSONDecodeError: Expecting value: line 1 column 1`. I spent a cycle
  hunting a non-existent load crash before checking the process list. The existing
  "Crossed replies" detection did not fire, because the stale instance was answering
  every request — just for a different world.
  - [gather:G-009] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have the DevTools autoload write a `devtools_owner.json` with its PID
    and start time, and have `devtools.py` refuse to run (naming the other PID) when a
    live owner file belongs to a different process. Failing that, make `ping`'s
    "game not running" message list matching OS processes.

- Gap: **`run-method` requires an absolute `/root/...` path while every other verb takes
  the short form.** `--node Main/InputManager` returned `Failed: Node not found:
  Main/InputManager`, but `cmd`-registered verbs resolve `Main/...` fine via
  `get_tree().root.get_node_or_null`. The inconsistency cost a debugging round on a
  path that was actually correct.
  - [gather:G-010] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have `run-method` / `get-state` / `set-state` retry a failed lookup
    with `/root/` prefixed, or say so in the error text.

- Gap: **`saveObject()` failures are structurally invisible** — a `-> Dictionary` method
  that raises still returns `{}`, so `SaveLoad` wrote a blank line and lost a whole
  node's state with no failed assertion anywhere. This is the same trap the test runner
  has (`gather-1t9`), but in game code, and it hid a real data-loss bug
  (`gather-hxa.8`) for as long as the file has existed.
  - [gather:G-011] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: add a `save_roundtrip` verb that calls every `SaveLoad` member's
    `saveObject()` and reports any that return an empty dict or omit `filepath` —
    a one-call check for a class of bug that is otherwise silent.

- Gap (second sighting): **the missing upstream path, logged earlier today, is now the
  only reason this turn had work to do.** Producing the handoff meant reading this log,
  `~/Documents/GitHub/godot-selftest-harness/log-devtools.md`, `plugin.json` (`0.4.0`),
  `templates/tools/run_tests.gd:174` and the scaffold step headings by hand, then writing
  the result to `prompt-harness-0.5.0.md` for a human to carry across. Confirmed the
  transport has never run for this batch: the harness log's only heading is
  `## 2026-08-01 — Ship the gaps log, close what it recorded (0.4.0)`, so all six of this
  project's gaps are still local.
  - [gather:G-012] status: fixed | fixed-in: 0.5.0 | seen: 2 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: as already filed — an `upstream_gaps` script that appends open gaps to
    the harness repo's log, deduped by id. The prompt written this turn is the manual
    version of exactly that script, which is the strongest evidence yet that it should
    exist.

- Gap: **No harness check that the project's renderer is web-exportable** — gather ships
  `config/features=PackedStringArray("4.7", "Forward Plus")` while a Godot 4 web export only
  runs on `gl_compatibility`. Nothing in `lint_project.gd` or `/verify` flags this; it was
  caught only by hand-diffing `project.godot` against the AtomicRobot repo, which has
  `renderer/rendering_method="gl_compatibility"`. A Forward+ web build exports cleanly and
  then fails to start in the browser — the failure surfaces on itch.io, not in CI.
  - [gather:G-013] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: add a lint rule that reads `renderer/rendering_method` (plus its `.web` /
    `.mobile` overrides) and errors when a `Web` preset exists in `export_presets.cfg`
    without a compatibility override.

- Gap: **`/verify` has no headless export check** — the harness validates lint, tests and
  runtime, but nothing exercises `--export-release`, so a broken or missing export preset is
  invisible until CI. The new `Web` preset in `export_presets.cfg` could not be validated
  locally at all; the deploy agent reported "I did not run the Godot binary, so the new preset
  has not been round-tripped through the editor."
  - [gather:G-014] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: a `tools/check_exports.gd` that enumerates presets, asserts each has an
    `export_path` and that the matching export template is installed, runnable headless.

## 2026-08-01 - Upstreamed 1 open gap(s) from gather (harness 0.4.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **Nothing validates a vendored addon's UIDs against the host project before lint runs** —
  the ported `addons/virtual_joystick/test/test.tscn` carried AtomicRobot's icon UID
  (`uid="uid://cw7a6wede53n1"` for `res://icon.svg`) while gather's is `uid://c6knbegisd067`,
  so `lint_project.gd` (`scan_root: "res://"`) would have reported it as a project defect
  rather than as imported third-party debt. Found by hand-diffing, patched by hand.
  - [gather:G-015] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: teach `lint_project.gd` a `--baseline`-style vendored-path skip list (or reuse
    the existing `--baseline` split) so `addons/*` findings report as PRE-EXISTING/VENDORED
    instead of NEW.

## 2026-08-01 — Close the game's backlog and repair the feedback loop (0.5.0)

First release driven by gaps logged in a *different* repo. Nine items: four on the loop
itself (ids/status, the `Stop` hook, an upstream script, version stamping) and five on
what the game actually hit. Validated against Godot 4.6.1 in a scratch project assembled
from `templates/`, plus a reconstructed 0.4.0 install for the upgrade path.

**Closed in 0.5.0** — `gather:G-001` `G-002` (scaffold refresh hygiene), `gather:G-003`
(harness version), `gather:G-004` (test filter), `gather:G-006` (missing UID sidecars),
`gather:G-007` (gap ids/status), `gather:G-008` (Stop hook), `gather:G-012` (upstream
path), `H-006` (this repo's own always-on context). `gather:G-005` stays **open**: the
`--session` half shipped, the `--import` class-cache race it also names did not.

**Still open and untouched**: `gather:G-009` (no owner-PID detection for a stale
instance), `gather:G-010` (`run-method` needs an absolute `/root/` path),
`gather:G-011` (game-level, belongs in that project's `commands.gd`), `gather:G-013`
`G-014` (web-export renderer lint, headless export check), `gather:G-015` (vendored
addon UIDs). All six are now pooled here rather than only in the game's log, which is
the loop working.

- Gap: **a byte-level hash comparison silently does nothing on Windows, and the failure
  looks exactly like the bug it was meant to fix.** `B4`'s "don't back up a pristine
  file" check compares sha256 against recorded hashes. With `core.autocrlf=true` — the
  default on Windows — the plugin and the target project hold CRLF copies of files whose
  recorded hashes were computed from LF bytes, so *nothing ever matches* and every file
  is backed up exactly as before. The first run against a reconstructed 0.4.0 install:
  ```
  ! tools/lint_project.gd was MODIFIED locally -> saved as tools/lint_project.gd.bak
  ...
  .bak files created: 6
  ```
  Found only because the upgrade was actually simulated; reading the code shows nothing,
  and the feature would have shipped looking implemented and doing nothing.
  - [H-008] status: fixed | fixed-in: 0.5.0 | seen: 1 | harness: 0.5.0
  - Improvement (done): hash content with line endings normalized to LF, in both
    `record_version.py` and `scaffold_install.py`, with a comment in each saying why they
    must agree.
  - Improvement (still open): the template-check script `H-005` keeps asking for should
    include an **upgrade** case, not just a fresh install — build a project from the
    previous release's templates, run the installer, and assert zero `.bak` files. Every
    scaffold bug in this log is a refresh bug, and a fresh-install test would have caught
    none of them.

- Gap: **the seeded `log-devtools.md` can never be updated in a project that already has
  one.** Scaffold step 9a creates it only if absent (correctly — it must never truncate a
  real log), so the `## Format` section installed on day one is frozen forever. `A1`
  changed that format, and the game's log still documents the old one; the ids had to be
  retrofitted there by hand, from this repo. `CLAUDE.md` solves the same problem with
  `<!-- BEGIN/END -->` markers and refreshes in place.
  - [H-009] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.5.0
  - Improvement: wrap the log's header/Format section in the same delimiters and have
    step 9a refresh *that block* while leaving every entry untouched — so the format can
    evolve without a hand edit in every project that uses the harness.

- Gap: **nothing tests the scaffolder, which is now the most intricate part of the
  plugin.** `scaffold_install.py` has a genuinely non-obvious ownership rule (a key the
  project edits becomes project-owned permanently, so a value later set *back* to the
  default is still preserved). Getting it right took three attempts, each caught only by
  hand-building a fake project in the scratchpad and eyeballing the output — including
  one bug where keys matching the shipped default never entered the owned set at all and
  every upstream default change was silently declined.
  - [H-010] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.5.0
  - Improvement: a `tools/test_scaffold.py` driving `scaffold_install.py` over temp
    projects and asserting the outcomes (fresh, pristine upgrade, edited file, edited
    key, key-reverted-to-default, re-run). Every case already exists as a shell
    incantation in this session's transcript; none is repeatable.

- Verified this turn with no gap: driving the real `devtools.py` against the real
  autoload over the file bus caught nothing new, because the two halves were edited
  together this time — `harness_version` and `ping` gained their `data` keys on both
  sides in one commit, per `CLAUDE.md`. Two headless instances sharing one `user://`
  answered only their own clients, which is the first time parallel runtime verification
  has been possible at all.

## 2026-08-01 - Upstreamed 7 open gap(s) from gather (harness 0.4.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **No way to read whether an input action is currently pressed** — the whole point of
  the touch overlay is that a button latches a real `InputMap` action and later releases it,
  but `input list` reports only the *bindings*:
  `gather: E - Physical` / `attack: Space - Physical`. There is no `Input.is_action_pressed`
  readout, so proving "the MINE button is holding `gather` right now" had to be done
  indirectly through whatever gameplay node happened to expose the state —
  `cmd player_state` (`state=PlayerGather`) for gather, and
  `get-state --node /root/Main/DestroyManager --property is_holding_e` for destroy. A project
  without such a node could not assert a held action at all.
  - [gather:G-021] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: add `input state [ACTION...]` returning
    `{action: {pressed: bool, strength: float}}` from `Input.is_action_pressed` /
    `get_action_strength`, so a hold/release pair is assertable without a gameplay proxy.

- Gap: **The drift check names files but cannot say which side is ahead** — Phase 0 reported
  `DRIFT:` on all six harness files (`dev_tools.gd`, `scene_validator.gd`, `devtools.py`,
  `lint_project.gd`, `run_tests.gd`, `check_devtools_log.py`) while `tools/*.bak` copies from
  the last in-place refresh sat untracked beside them. `cmp -s` gives a boolean, so
  "the project patched this locally" and "the install predates the plugin" look identical, and
  the workflow's own instruction to compare `git log -1 --format=%cd` fails for the plugin side
  because those templates live outside this repo. Resolved by reporting drift unresolved.
  - [gather:G-022] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: stamp a `# harness-template: <sha>` line into each copied template at scaffold
    time; the drift check then compares stamps and reports ahead/behind instead of just differs.
    Closes with [G-003], which is the same missing-version-identity problem at verb level.

- Gap: **`set-state` cannot write a vector-typed property** — the documented `run-method`
  coercion gap (`gather-6sp`) also applies to `set-state`. Resizing the viewport to exercise
  `camera_hud.gd`'s `size_changed` handler needed `/root.size`, and every value form silently
  produced garbage rather than erroring:
  ```
  $ devtools.py set-state --node "/root" --property size --value '{"x":1280,"y":720}'
  State updated
  $ devtools.py get-state --node "/root" --property size
  size: (232, 64)
  ```
  `--value '[1280,720]'` produced the same `(232, 64)`. The run still proved the reflow
  (the HUD tracked 232x64 exactly: `Rect: -160, -62, 47x13` == `232/4.935 x 64/4.935`), but
  by accident — the resize I asked for is not the resize I got, and nothing said so.
  - [gather:G-016] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have `set-state` coerce a 2/3/4-element array or an `{x,y,...}` dict to the
    property's declared type via `type_convert()`, and **fail loudly** when the target
    property is a vector type and the value cannot be converted, instead of writing whatever
    the bad cast yields and answering `State updated`.

- Gap: **No verb resizes the window, so the single most important behaviour of a
  resolution change is untestable by design** — `/verify` has `set-game-speed`,
  `wait-frames` and `step-time` for the time axis and nothing at all for the viewport axis.
  Every anchor, every `size_changed` handler and every `get_viewport_rect()` caller in the
  project is only ever exercised at one size unless you quit and relaunch with
  `--resolution WxH`, which costs a full boot per data point and cannot test the
  *transition* at all.
  - [gather:G-017] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: add a generic `set-resolution --size WxH` verb that calls
    `DisplayServer.window_set_size()` and returns the resulting `get_viewport_rect().size`,
    so a caller can assert the resize landed before asserting on layout.

- Gap: **`validate-ui` applies screen-space checks to world-space Controls, so its verdict
  is a function of where the player is standing** — this project's diegetic HUD hangs off
  `Player/Camera2D`, and `ui_negative_pos` reports its *global* position:
  ```
  [WARN] ui_negative_pos: Label 'Label3' has negative position (-267, -51)
  ```
  That number is the player's world position plus an offset; it says nothing about layout.
  9 of the run's 9 findings were this. Deciding whether the change regressed anything took a
  full `git stash` + relaunch + `validate-ui` on HEAD to compare (HEAD: 10 issues, branch: 9
  — the change removes `ui_zero_size` on `UI`), which is exactly the hand-triage that lint's
  `--baseline` exists to abolish.
  - [gather:G-018] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: give `validate-ui` the same `--baseline PATH` / `--baseline-write PATH`
    split `lint_project.gd` already has, so UI findings report as `NEW` vs `PRE-EXISTING`;
    and skip `ui_negative_pos` for Controls whose canvas ancestor is not a `CanvasLayer`,
    where negative coordinates are the normal case rather than a defect.

- Gap: **`input press <action>` does not drive the gather loop, and the failure is
  indistinguishable from a real bug** — after `cmd goto_resource` put the player 6 units from
  a Stone node, holding `gather` for 1.8s left `state: PlayerIdle` and `xp: 0`, with the
  census unchanged. Confirming this was pre-existing rather than a regression from the scene
  edits cost another stash + relaunch cycle on HEAD (identical `PlayerIdle`). CLAUDE.md
  already warns that driving gather through the hotbar's stop signal leaves the timer
  running; the *start* side has the same class of problem and is undocumented.
  - [gather:G-019] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: add a project verb `gather_once` in `devtools_ext/commands.gd` that calls
    `ResourceManager2.start_removing_resource()` directly and returns the node it engaged
    (or an explicit `"no resource in reach"`), so a gather assertion tests the gather loop
    instead of testing input plumbing.

- Gap: **Harness drift is detected but the report has no bearing on the run** — Phase 0
  flagged `DRIFT: tools/check_devtools_log.py differs from the plugin template`, with the
  plugin ahead (`Sat Aug 1 15:45:28 2026` vs the project's `Sat Aug 1 14:33:55 2026`). Three
  stale `.bak` files from an earlier refresh (`tools/devtools.py.bak`, `tools/lint_project.gd.bak`,
  `tools/run_tests.gd.bak`) are still sitting untracked in the tree, which is what a
  half-finished refresh looks like.
  - [gather:G-020] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have `/scaffold-godot-harness` delete its own `.bak` files once the refreshed
    file passes a syntax check, so a completed refresh leaves no residue to mistake for drift.

## 2026-08-01 — Measure whether the harness is load-bearing (0.6.0)

Built the run ledger: `tools/verify_ledger.py`, a `script`/`scene_file` key on every
`scene-tree` node, and a Phase 5 step in `/verify` that appends one row per run to
`.devtools/verify-runs.jsonl`. The question it answers is not "did this run pass" but
"did this run touch the code it claimed to verify" — which no existing artifact could
answer, because `log-devtools.md` records only the runs that went badly.

- Gap: **The gaps log has no denominator, so no claim about the harness's value is
  falsifiable from anything in the repo** — 36 status lines across two projects say the
  harness was in the way 36 times; nothing anywhere says out of how many runs. Asked
  directly whether the harness earns its keep in the projects using it, the honest answer
  was that the repo cannot tell, and neither `harness_history.json` (ships what, when) nor
  the gaps log (friction only) closes it.
  - [H-011] status: fixed | fixed-in: 0.6.0
  - Improvement: shipped — `verify_ledger.py record` appends every run including the clean
    ones; `stats` reports reach, verdict mix, and reach broken out per harness version, so
    "did 0.6.0 actually improve anything" is answerable instead of asserted.

- Gap: **`/verify` already knew when it hadn't reached the changed code, and threw the
  fact away every run** — `commands/verify.md` has instructed since 0.3.0 that "if a
  changed script has no reachable entry point, say so explicitly in the summary rather
  than reporting the change as verified". That confession only ever landed in a chat
  transcript. There was no way to ask how often it happened, which is precisely the
  number that says whether the runtime phase is doing work or decorating a lint run.
  - [H-012] status: fixed | fixed-in: 0.6.0
  - Improvement: shipped — reach is now computed, not claimed: `scene-tree` carries each
    node's `script` and `scene_file`, and the ledger intersects a snapshot union against
    `git diff`. A run can misreport its own checks but cannot misreport whether it loaded
    the diff.

- Gap: **Nothing in this repo could have caught the dot-path bug that the end-to-end run
  caught in three seconds** — `_norm()` used `lstrip("./")`, which strips leading `.` and
  `/` as a character class, so `.devtools/run.json` normalized to `devtools/run.json`. A
  changed file under any dot-directory would have scored as permanently unreached. Both
  halves would have agreed with each other in any unit test written against them; only
  running the real client against a real Godot game with a real `git status` surfaced it,
  which is the same lesson as the 0.4.0 three-mismatch release.
  - [H-013] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.6.0
  - Improvement: the `tools/check_templates.sh` already tracked as [H-008] should assemble
    the scratch project, launch it, and drive at least one verb end-to-end — a parse check
    and a `py_compile` would both have passed on this bug.

- Gap: **`prompt.md` was deleted from the working tree by something outside this session**
  — session-start `git status` was clean; midway through, `git diff --stat` showed
  `prompt.md | 40 ------`. No command run this session touched it. Restored from HEAD
  rather than folded into the release commit, but there is no record of what removed it.
  - [H-014] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.6.0
  - Improvement: none obvious at the harness level — noting it so a recurrence is
    recognizable as a pattern rather than re-diagnosed from scratch.

## 2026-08-01 — Make the log able to say the harness wasn't needed (0.7.0)

Added the `Value:` block: a four-way verdict, the prediction that preceded the run, and
the cheapest alternative that would have produced the same confidence. It goes in the
log as prose, in the ledger as an enum, and the `Stop` hook now requires it.

- Value: **warranted** — driving the real bus is what proved the two new `scene-tree`
  keys populate correctly (`res://main.gd` / `res://main.tscn` / `res://child.gd`), and
  running the hook against the actual shipped seed log is what exposed the fence bug
  below. Neither was visible from the diff.
  - Expected: that `reach` would report 1/2 with `lonely_hud.gd` unreached, and that the
    hook would go quiet only after a compliant entry.
  - Got: exactly that — plus one thing not predicted, the fenced-example parsing.
  - Cheaper: nothing for the bus check. The `stats` and enum work could have been proven
    with fixtures alone; the ~3 min of Godot launches bought only the two bus assertions.

- Gap: **A log that only records gaps can only ever recommend more harness** — asked
  whether the tool was helping, the repo could report reach and run counts but had no
  field anywhere meaning *this task did not need it*. Every one of the 40 gap entries
  across two projects is a feature request; not one is "should have skipped /verify".
  That is a property of the form, not of the work.
  - [H-015] status: fixed | fixed-in: 0.7.0
  - Improvement: shipped — `Value:` verdict + `Cheaper:` in the log, `value` /
    `cheaper_alternative` / `expected` in the ledger row, value mix in `stats`.

- Gap: **A self-report about one's own usefulness has a known bias direction and no
  mechanical check** — the `value` field is exactly the kind of judgment field this repo
  criticized in the 0.6.0 design notes. Mitigated four ways (enum not prose, prediction
  written first, `Cheaper:` must name something concrete, `_reconcile_value()` downgrades
  a `warranted` whose files were never loaded, preserving `value_reported`), none of
  which make it objective.
  - [H-016] status: open | seen: 1 | harness: 0.7.0
  - Improvement: the only real check would be an outside one — sample N `warranted` rows
    and re-derive the verdict from the row alone, without the prose. Worth doing once
    `gather` has accumulated enough rows to sample.

- Gap: **The `Stop` hook counted the log's own worked examples as entries** — adding two
  fenced examples to `templates/log-devtools.md` made the pristine seed report
  `its newest is 2026-07-26` instead of `no dated entry at all`, because `_HEADING_RE`
  reads `##` inside ``` fences. Behaviorally near-harmless, but a fenced example dated
  today would have silenced the hook for a session that logged nothing — the file
  documenting itself into compliance.
  - [H-017] status: fixed | fixed-in: 0.7.0
  - Improvement: shipped — `_entry_dates()` tracks fences and skips their contents.
    Found by running the hook against the real shipped seed rather than a fixture,
    which is the same lesson as [H-013] one release earlier.

## 2026-08-01 - Upstreamed 5 open gap(s) from gather (harness 0.4.0, 0.7.0, unknown)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`gather_stats` cannot observe an in-progress gather** — the one verb named for the
  gather loop returns only world state (`cap`, `census`, `land_tiles`, `live_nodes`,
  `spawnable`, `tuning`). Holding the touch MINE button and re-reading it produced a
  byte-identical response, so the hold was unobservable through the verb that exists for it:
  ```
  {'cap': 40, 'land_tiles': 111, 'live_nodes': 40, 'spawnable': [...], 'tuning': {...}}
  ```
  Falling back to `get-state --node /root/Main/ResourceManager --property hold_timer` returned
  `@Timer@14:<Timer#92090140860>` — an opaque object id, not a remaining time. The hold was
  verified indirectly instead (the ITEM button cycling `HotBarInventory.selected_index` 0 -> 1
  proves the same touch -> `_input` -> `send_action` path).
  - [gather:G-024] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: unknown | source: gather 2026-08-01
  - Improvement: add `is_gathering`, `hold_time_left` and `target_resource` to the
    `gather_stats` payload — three fields off `ResourceManager2`, and the gather loop becomes
    assertable at runtime instead of only in unit tests.

- Gap: **git-bash rewrites `/root/...` node paths into Windows paths** — every `--node`
  argument starting with `/root` is mangled by MSYS path conversion before Python sees it:
  ```
  Unknown property on C:/Program Files/Git/root/Main/ResourceManager: removing_resource
  ```
  `devtools.py` normalizes it back (the node resolved and `hold_timer` returned fine), but the
  mangled path is echoed in every error message, so a genuine typo and a path-conversion
  artifact look identical and the first instinct is to debug the wrong thing.
  - [gather:G-025] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: unknown | source: gather 2026-08-01
  - Improvement: echo the *normalized* path in error messages, not the raw argv one.

- Gap: **`.gdignore` does not exclude a directory from `validate-all`** — added
  `addons/virtual_joystick/{previews,test}/.gdignore` so the vendored demo leaves the import
  and lint scan, but the validator still walks it:
  ```
  res://addons/virtual_joystick/test/test.tscn:
    [INFO] relative_nodepath: Node 'Player' property 'joystick_left' uses relative path: ...
  ```
  Two of the 28 validate-all findings are from a directory Godot itself is told to skip.
  - [gather:G-026] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: unknown | source: gather 2026-08-01
  - Improvement: have `scene_validator.gd` skip any directory containing a `.gdignore`, the
    same rule the engine applies.

- Gap: **`/scaffold-godot-harness` step 11 has no Windows branch for locating Godot** — the
  documented probe is `$GODOT_BIN` -> `/Applications/Godot.app/...` -> `command -v godot`.
  On this machine all three miss (the binary is a bare
  `/c/Users/gotmi/Documents/Godot_v4.7.1-stable_win64.exe`, never on PATH), so step 11 falls
  through to `WARN: no Godot binary found` and steps 12's smoke check would be skipped
  entirely. Only this project's own `CLAUDE.md` records the real path; a first-time scaffold
  on a Windows box would report install-success having verified nothing.
  - [gather:G-027] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: add a Windows branch to the step 11 probe — glob
    `~/Documents/Godot_v*_win64.exe`, `/c/Program Files/Godot/*.exe` and
    `$LOCALAPPDATA/Programs/Godot/*.exe` — and, once found, write the resolved path into
    `devtools_config.json` as `godot_bin` so `/verify` and later refreshes stop re-deriving it.

- Gap: **`--import` silently rewrites `project.godot`, dropping comments and explicit
  settings** — CLAUDE.md requires `--import` after any new `class_name` and the scaffold
  smoke check runs it, but nothing warns that Godot *rewrites the file it read*. After
  `godot --headless --path . --import`, `git diff project.godot` showed a clean tree turn
  dirty with every explanatory comment stripped and three settings deleted outright:
  ```
  -window/stretch/mode="disabled"
  -renderer/rendering_method="forward_plus"
  -renderer/rendering_method.web="gl_compatibility"
  ```
  Godot drops keys it considers default, but `.web` is the override the in-file comment says
  keeps the itch.io build from failing to start in the browser — a loss that only surfaces on
  deploy, long after the commit. It was caught only because `git status` was read again before
  staging; had the refresh been committed straight from the earlier clean status, it would have
  shipped inside a "harness refresh" commit nobody would think to check for a renderer change.
  Workaround: `git checkout -- project.godot`, then re-confirmed the `DevTools` autoload
  survived (it did — it was already committed).
  - [gather:G-028] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: have the scaffolder and `/verify` snapshot `project.godot` before any
    `--import` and restore it (or diff and warn loudly) afterward — the import step needs the
    *cache* rebuilt, never the project file edited. Failing that, CLAUDE.md's `--import` rule
    should carry an explicit "check `git diff project.godot` afterward" warning.

## 2026-08-02 — Open the 0.8.0 batch: log bookkeeping and the pooling run that found a parser bug

- Gap: **`upstream_gaps.py` harvested "no gaps this turn" absence markers as gaps** — the
  dry run against gather's log proposed appending 8 `auto-XXXXXX` entries whose content was
  the format's own required "no gaps this turn" line. The format mandates writing that line
  precisely so absence is distinguishable from forgetting; the pooling tool then read every
  one of them as a nameless gap. Fixed in this session: `_NO_GAP_RE` filter in
  `parse_gaps()`, applied to both byte-identical copies.
  - [H-018] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0
  - Improvement (shipped): skip gap blocks whose first line matches
    `- Gap: no( new)? gaps this turn`, case-insensitive, `**` tolerated.

- Note: gather's four ID collisions (two gaps each under G-036, G-037, G-044, G-074) were
  repaired at the source before this pooling run: the second entry of each pair is now
  G-065, G-066, G-067, G-068 respectively, marked `(was G-0XX, reassigned — ID collision)`.
  Entries pooled below therefore carry the corrected ids; nothing previously pooled under
  the old ids referred to the (b) entries, so no destination rewrite was needed.

## 2026-08-02 - Upstreamed 49 open gap(s) from gather (harness 0.4.0, 0.7.0, 0.7.0 (was G-036, reassigned — ID collision), 0.7.0 (was G-037, reassigned — ID collision), 0.7.0 (was G-044, reassigned — ID collision), 0.7.0 (was G-074, reassigned — ID collision), unknown)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather-devtools\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`place_station` wrote a cell that instanced nothing, and the bridge could not say
  why** — `cmd place_station` returned `{"success": true, "message": "placed Sawmill"}` while
  `craft_state` kept answering `"stations": 0`, and `scene-tree` showed 14 TileMap children,
  all stone nodes. The verb had passed `item.tile_atlas_location` (the inventory *icon* cell,
  `(0,2)`) where the live path `player_manager.place_tile:29` passes `item.atlas_location`
  (`(0,0)`); a `TileSetScenesCollectionSource` silently instances nothing for the wrong
  coords. Nothing in the response distinguished "cell written" from "scene instanced".
  Workaround: read `player_manager.gd` and compare against `main.gd:set_tile_item`, which
  turns out to have the same latent bug and is dead code (no callers).
  - [gather:G-029] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: `set_tile`-style verbs should verify the cell afterwards —
    `get_cell_source_id`/`get_cell_tile_data` on the written cell, and for a scene tile a
    child-count delta — and report `success: false` when the write produced no tile. A verb
    that reports success for a no-op is the "one half of an invariant pair" trap in the
    harness CLAUDE.md, in the harness's own tooling.

- Gap: **`verify_ledger reach` under-reports for anything that is not a node script under the
  root scene** — it said `reached 6/18`, listing `crafting/recipes.gd`,
  `inventory/inventory_data.gd`, `systems/skill_tree.gd`, `items/types.gd`,
  `devtools_ext/commands.gd` and `ui/recipe_card.gd` as NOT reached. All six ran: recipes.gd
  is the `/root/Recipes` autoload (outside `Main`, so a `Main` snapshot cannot contain it),
  inventory_data.gd is a `Resource`, skill_tree.gd is a `RefCounted`, types.gd is
  `class_name`-only, commands.gd is loaded by the DevTools autoload — none of them are ever a
  node's `script`. `ui/recipe_card.gd` is a node script, but the cards nest ~12 deep and the
  snapshot's max depth is 10, so 15 cards visible in the screenshot still read as unreached.
  Deleted files (`crafting/crafting_ui.gd`, `cost_row.tscn`) are also counted against reach.
  Workaround: computed the depth by hand
  (`max depth in snapshot: 10`, `recipe_card.gd -> False`) and reported reach with the
  structural blind spots named, rather than reporting 6/18 as if it were a coverage number.
  - [gather:G-030] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: three things, cheapest first — (1) exclude paths deleted in the diff from the
    denominator; (2) have `scene-tree` take a depth argument for the reach snapshots, or have
    `reach` warn when the tree hit its depth cap; (3) widen the reach signal beyond node
    `script` paths — autoload scripts are enumerable from `/root`, and a "not a node script,
    reach cannot speak to this" bucket (which already exists for `.uid`/`.png`) would stop
    Resource and RefCounted scripts from reading as untested code.

- Gap: **no way to exercise "N input events in one frame" from a test** — the leading
  hypothesis is that `systems/input_manager.gd:44` calls `Input.is_action_just_pressed()`
  from inside `_input()`, which is frame-scoped while `_input()` is per-event. Reproducing
  that needs a test that dispatches several `InputEvent`s within one frame and counts signal
  emissions. `_T` offers `instantiate_ui` / `free_ui` and `await tree.process_frame`, but
  nothing to push a batch of events through a viewport in a single frame, and
  `Input.parse_input_event` from a headless test does not reach a `_input()` handler without
  a live viewport. Workaround this turn: static tracing plus a new `gather_state` project verb
  to assert the *consequence* (stranded tilemap cells) at runtime instead of the cause.
  - [gather:G-031] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: a `_T.dispatch_events(viewport, [events])` helper that pushes an array of
    events through `Viewport.push_input` without yielding between them, so frame-scoped vs
    event-scoped input bugs — a whole class, and the one that only bites on touch where the
    joystick emits a drag every frame — become unit-testable at all.

- Gap: **nothing can assert tilemap cell contents** — the whole bug is "a cell is left set",
  and `get-state` on the TileMap reports no cell contents, `gather_stats` counts *nodes* (a
  stranded highlight is not a node), and a screenshot of a stuck selector is pixel-identical
  to a legitimately selected tile. `validate-all` returned 0 issues throughout. Workaround:
  wrote a project verb, `gather_state`, that reads `get_used_cells(3)` and scans layer 1 for
  cells sitting on a `gathering_atlas_location`, with a `stranded` flag. Writing it also
  surfaced why a generic verb would not have done: the flag has to discount the chest
  highlight `player.gd:_process` legitimately redraws every frame, which is game knowledge.
  - [gather:G-032] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: a generic `tilemap-cells --node PATH --layer N` verb returning used cells
    with their source id and atlas coords. Tile-based games keep real state in cells, and it
    is currently the one part of the scene the bridge cannot read at all — every project
    hitting this has to hand-roll its own verb.
  - Seen again by the stone-wall work, which needed to prove a placed wall had been
    autotiled — i.e. that its cell held one of 47 solved atlas coords rather than the blob's
    base. Same conclusion, second hand-rolled verb: `tile_at`, reading `get_cell_source_id`
    and `get_cell_atlas_coords` at an offset from the player. Two independent gathers of the
    same missing primitive in one day is the argument for the generic verb.

- Gap: **`get-state` cannot read a `_panel_root.visible` behind an `is_open()`** — while
  testing the `disable_input` path I read `skill_panel` state that disagreed with
  `disable_input`, concluded the release was being swallowed, and said so before a clean
  re-run showed it was not. The verb reports `open` from `is_open()`, but `set_open()`
  early-returns on `_panel_root.visible == open`, so a panel whose root and wrapper disagree
  answers confidently and wrongly. Workaround: re-ran the sequence from a known-closed state
  and asserted every step instead of trusting the first reading.
  - [gather:G-033] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: for state a verb derives from a method, also report the raw fields it derives
    from (here `_panel_root.visible` alongside `open`), so a disagreement is visible in the
    reply rather than inferred three commands later. Same argument as the status provider.

- Gap: **`lint_project.gd` does not compile-check scripts, so a broken `main.gd` passed
  lint clean** — after the wall refactor, lint reported `lint: 0 error(s), 7 warning(s)
  -> exit 0` while `main.gd` had three real parse errors (`Identifier "wall_tiles_min"
  not declared`, and two `Cannot use subscript operator on a base of type "null"`). They
  only surfaced on the *next* phase, as `Failed to load script "res://items/items.gd"`
  buried in the unit-test log — and that log still printed `Total: 145 | ALL TESTS
  PASSED | exit 0`, because the tests that depend on items.gd were the ones that failed
  to load rather than to run. Two gates in a row reported success on a project that
  could not boot. Workaround: grepped the test log for `SCRIPT ERROR` by hand, which is
  the only reason it was caught at all.
  - [gather:G-034] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: lint already walks every `.gd` under `scan_root` for the UID pass —
    have it `load()` each one and report a failed compile as a lint *error*. Failing
    that, `run_tests.gd` should exit 2 when any `Failed to load script` appears on
    stderr, on the same reasoning that already makes an unparseable test script exit 2:
    a suite that could not load half its dependencies verified nothing.

- Gap: **no way to place a building tile through the generic primitives** — `set_tile`
  takes two `Vector2i`, and `run-method` hands raw JSON to `callv` with no vector
  coercion (the project's own `gather-6sp`), so the entire build path was undrivable.
  Workaround: added `place_build` and `tile_at` project verbs, which is the documented
  answer and worked first try — recording it because the *generic* gap is real and
  every project hits it independently.
  - [gather:G-035] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: teach `run-method` to coerce a 2-element JSON array into `Vector2`/
    `Vector2i` when the target method's argument list says so — `Object.get_method_list()`
    exposes the parameter types, so the coercion can be driven off the signature rather
    than guessed.

- Gap: **A stale bus file in a shared `user://` answers requests with another build's
  data, with no process running to explain it.** First `scene-tree` of the session
  returned a `UI2` whose children were in the pre-change order *and* included an
  `IslandCompass` node that does not exist anywhere in this worktree
  (`grep -rl IslandCompass .` matched only the JSON I had just written). The game log
  also showed a `_cmd_set_state` on camera zoom that I never sent
  (`ERROR: Zoom level must be different from 0`, at `dev_tools.gd:707`).
  `Get-CimInstance Win32_Process` showed exactly **one** Godot process, and it was mine —
  so this was not the live-second-instance case in [G-009]; it was leftover
  `devtools_commands.json` / `devtools_results.json` from an earlier session in the
  shared `user://`. The existing id-echo "Crossed replies" check did not fire. I only
  caught it because a node name happened not to exist in my source; had the stale tree
  been merely *older* rather than foreign, I would have verified nothing and reported a
  pass. Workaround: relaunch with `-- --devtools-session uiverify` and call with
  `--session uiverify`, which gives a private file pair; `ping` then confirms
  `session: uiverify`.
  - [gather:G-036] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: have the DevTools autoload delete any pre-existing command/result file
    for its own bus id during `_ready()`, and have `devtools.py` reject a result whose
    request id it never issued instead of returning it. Note `GODOT_USERDATA` does not
    help here — it redirects only the *client*; the game resolves `user://` from the
    engine, so the two silently poll different directories and the error reads as a
    dead game.

- Gap: **`place_station` can drop a station outside the crafting panel's own keep-open
  radius, making the panel unverifiable from the CLI.** `_cmd_place_station` ring-searches
  from the player's tile but its `dx` loop starts at `-ring` and takes the first free
  cell, biasing toward the far corner. Observed: player at (12.9, 8.9), station placed at
  (24, 56) — 48 units away, while `crafting_ui.gd:640` closes the panel past 24. So
  `cmd crafting_panel --args '{"open":true}'` returns `"open": true` and the very next
  command reads `Visible: False` / `"panel_open": false`. `set-game-speed 0` does not
  help (`_physics_process` still runs), and `set-state` cannot reposition the player
  because it will not coerce a dict to `Vector2` — it silently wrote (0, 0), which is
  [G-035] biting a second verb. Walking there with `input press move_down` +
  `step-time` was blocked by collision after ~9 units. Net result: `ui/recipe_card.gd`
  is the one changed file this run could not reach, and the crafting panel was verified
  only through its 5 passing unit tests plus a `set_title` readback of `SAWMILL`.
  Filed as gather-7y9. (The commit that introduced this entry, 441668f, cites a
  non-existent `gather-1ph` for the same bug — the id was written before the bead was
  read back. gather-7y9 is the real one.)
  - [gather:G-037] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: have `place_station` collect the candidate free cells and pick the one
    nearest the player rather than the first the scan hits, so the station lands inside
    the interaction radius the panel itself requires.

- Gap: **no verb reports the island footprint, so the connection guarantee in
  `gather-b6r.5` has no cheap runtime oracle.** `land_cells_for_radius` is noise-filtered
  and the noise is a smooth gradient over the ±34 span, so at max radius the home island
  can be lopsided with a whole side missing — which decides whether a pregenerated island
  ever becomes walkable. Today the only readouts are `count_land_tiles()` (a scalar, via
  `gather_stats`) and `screenshot`. Neither can answer "is cell X connected to the home
  island", and a scalar count is identical whether the land is one blob or two.
  Workaround for this turn: none needed, since nothing was built yet — recording it now
  because the acceptance criterion on `gather-b6r.5` ("reachable across a sample of random
  seeds") is unverifiable without it and will otherwise get eyeballed on one run.
  - [gather:G-065] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 (was G-036, reassigned — ID collision) | source: gather 2026-08-01
  - Improvement: a generic `tilemap-region --layer N --atlas X,Y` verb returning the
    connected components of matching cells as bounding boxes plus cell counts. That is
    game-agnostic (it only needs a `TileMap` and an atlas coord), and it turns "did the
    islands connect" into one assertion instead of a screenshot. The project-specific
    alternative — an `island_census` verb in `devtools_ext/commands.gd` — is the
    documented fallback and is what I will add if the generic one does not exist by then.

- Gap: **`performance` reported `Orphan growth: +0` on a save/load round-trip that
  `gather-jjg` says leaks 2 orphans** — and the baseline resets per session, so a `+0`
  after a load is unfalsifiable without knowing when the baseline was taken. The reading
  was the same before and after my change, which means it told me nothing about whether I
  made the known leak worse. Workaround: none; I recorded the absolute (`absolute 0`)
  alongside the growth and moved on.
  - [gather:G-066] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 (was G-037, reassigned — ID collision) | source: gather 2026-08-01
  - Improvement: have `performance` report the baseline's *age* in frames and whether it
    was taken this session, and let `--reset-baseline` be asserted against explicitly, so
    "+0" can be distinguished from "+0 because the baseline moved under you".

- Gap: **three Godot instances were live on one bus and answered each other's commands.**
  Symptom was a census reporting `home_radius: 34` in a session where no land had been
  bought, and a response missing the `boss` key I had just added — i.e. an *older build*
  replying. Both readings were well-formed and neither was flagged. `ping` reported a
  healthy bridge throughout. This is the documented one-bus hazard and a stored memory, and
  I still lost about ten minutes to it because nothing in the reply says which process
  produced it. Workaround: `Get-Process Godot* | Stop-Process -Force` before every launch.
  - [gather:G-038] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-01
  - Improvement: put the answering process's pid and its script-load timestamp in the
    `status` block on every reply. The bus already echoes a request id to catch crossed
    replies; a pid would catch the case where the reply is *not* crossed but comes from a
    different, staler game than the one just launched. Failing that, have the game write
    its pid beside the bus at startup and let the client warn when it changes between calls.

- Gap: **`get-state` has no machine-readable output mode**, so scripted assertions on it
  are guesswork. `python tools/devtools.py get-state --node PATH --property loads` prints
  `loads: [{...}]` — GDScript-flavoured, not JSON — and piping it to `json.load` fails with
  `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`. `run-method` has the same
  shape (`Result: None`). I wanted `len(properties['loads'])` as an assertion and had to
  eyeball the string instead, which is precisely the read that a tired session gets wrong.
  `run_tests.gd` has `--json`; the bridge client does not.
  - [gather:G-039] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: add a global `--json` flag to `tools/devtools.py` that prints the raw
    response dictionary the game already sends, for every verb. The data is structured on
    the wire — only the client's pretty-printer discards it. `cmd island_census` already
    emits JSON, which is why every scripted assertion I write ends up routed through a
    project verb rather than through the generic primitives.

- Gap: **the documented `python3` invocation does not run on this machine** — it is the
  Microsoft Store alias stub,
  so the documented `python3 tools/devtools.py ping` fails with "Python was not found; run
  without arguments to install from the Microsoft Store". CLAUDE.md warns to probe by
  running it, and I still spent a launch cycle on it because the harness docs and the
  `/verify` skill both spell `python3`.
  - [gather:G-040] status: fixed | fixed-in: 0.8.0 | seen: 3 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: ship a `tools/devtools` shim (or have the skill resolve the interpreter
    once and cache it) so the documented invocation is interpreter-agnostic; the Store stub
    exits 9009 with a message on stdout, which is detectable.

- Gap: **an `input tap` that engages nothing is indistinguishable from one that works** —
  `python tools/devtools.py input tap gather --hold 3.0` printed `Tapped: gather (hold:
  3.0s)` and xp did not move; `player_state` afterwards showed `"state": "PlayerIdle"`, so
  the press had been and gone without the gather ever starting. The reply is the same
  string whether the action reached a handler or fell on the floor. Workaround was to
  re-anchor with `goto_resource` and drive it as `input press` + `step-time --seconds 6` +
  `input release`, sampling state between each.
  - [gather:G-041] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have `input tap`/`press` return the count of handlers the dispatched
    `InputEventAction` was consumed by (`get_viewport().set_input_as_handled` already
    distinguishes this), or at minimum echo the acting node's state machine state before
    and after, so "nothing listened" is visible in the reply.

- Gap: **no way to hold xp income still while asserting an xp delta** — the ambient world
  pays xp on its own (pickups at 1 per 3 drops, and enemies the spawner trickles in), so
  between `get-state ... --property xp` and the gather it drifted `3 -> 4 -> 6` unprompted.
  Every xp assertion in this run had to be read as "at least/at most", and the coal
  assertion (+1) is only conclusive because the old value (3) is above the noise floor. A
  smaller change than 3->1 would not have been measurable this way at all.
  - [gather:G-042] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a project verb `xp_ledger` returning the last N awards as
    `{source, amount}` rather than a running total — the awards are already individually
    routed through `LevelUpManager.add_xp`, so attribution exists at the call site and is
    thrown away. Failing that, a generic `freeze_ambient` that pauses spawners and pickup
    collection for the duration of an assertion.

- Gap: **no way to enumerate registered project verbs without a running game** — the
  planned debug panel wants to drive the same handlers the CLI does, so the verb roster is
  a design input, not a runtime question. `list-commands` is the documented source of
  truth and it requires a live bus; recovering the roster meant hand-parsing the
  `register_command` block in `devtools_ext/commands.gd:14-46`. Worked, but it is the kind
  of list that silently drifts from the docs.
  - [gather:G-043] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a headless mode for `list-commands` (load the extension script, call
    `register_commands` against a stub Node, dump the `_handlers` keys and exit) so the
    verb roster is readable from the same place lint and tests run.

- Gap: **`verify_ledger.py reach` scopes "changed files" to the branch, not the working
  tree** — it reported `reached 15/23 changed file(s)` and listed `test/unit/test_ore_chain.gd`,
  `ui/debug_panel_ui.gd` and `devtools_ext/commands.gd` as unreached. This session changed
  three files (`world/island_manager.gd`, `world/resource_manager2.gd`, and a new test);
  the other twenty are earlier branch commits plus another session's uncommitted work in
  the same tree. Both files this session touched *were* reached, but that had to be
  established by subtracting the NOT-reached list from `git status --porcelain` by hand.
  The failure mode is the flattering one: a large branch dilutes the ratio, so a run that
  reached everything it changed still reads as 65% covered.
  - [gather:G-044] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: report reach for the working-tree diff and the branch diff as two
    numbers, or take a `--since` ref so the run can scope reach to what it actually edited.

- Gap: **`input tap` on a release-triggered action toggled the panel twice, minutes
  apart** — `input tap debug_panel` opened the panel (`visible: true`, confirmed with
  `disable_input: true`), and four read-only commands later it was `visible: false` with
  nothing having sent input in between. Split into halves it is deterministic:
  `input press debug_panel` -> `visible: false` (correct, InputManager fires on release),
  `input release debug_panel` -> `visible: true`, still true after `wait-frames 60`. A
  second `input tap` from open then produced exactly one toggle and stayed put, so it is
  intermittent rather than a plain double-fire. Most plausible mechanism is Godot
  synthesizing a release for a still-held simulated action when the window's focus
  changes — which is guaranteed here, since every CLI call runs while the game is
  unfocused. Workaround: drove the rest of the run with explicit `press`/`release` and
  `run-method`.
  - [gather:G-067] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 (was G-044, reassigned — ID collision) | source: gather 2026-08-02
  - Improvement: have `input tap` await its own release and report the action's final
    state in the reply (`{"action": "...", "pressed": false}`), so a tap that left
    something held is visible at the call rather than four commands later. Failing that,
    document that release-triggered actions should use explicit press/release.

- Gap: **launching the game rewrites `main.tscn` and the `.tres` assets, and the workflow
  never mentions it** — a session that started `(clean)` ended with
  `main.tscn | 235 ++++----` and `world_tile_set.tres | 3612 +++-----` (1354 insertions,
  2264 deletions) purely from Godot 4.7 re-serializing on load (adding `uid=` to every
  `ext_resource`). Phase 5 says to commit the ledger and hands back a `git status` in
  which these sit indistinguishable from real edits, and the tileset rewrite is exactly
  the kind of thing that gets waved through in a diff that large. Reverted with
  `git checkout --` after confirming none of it was mine.
  - [gather:G-045] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have Phase 5 snapshot `git status --short` before Phase 2 and diff it
    against the post-run status, listing engine-touched files separately from the working
    diff — the data is free and it is the difference between reverting three files and
    committing a 3600-line tileset rewrite by accident.

- Gap: **`verify_ledger.py record` silently drops reach when the snapshots are gone** —
  the workflow calls `tree-*.json` "inputs, not records" and says to leave them out of the
  commit, so I deleted them before recording; `record` then wrote the row anyway with
  `reach not computed (no scene-tree snapshot)`. The row that survives is the one missing
  the only field the workflow says to believe over self-reported checks. (Reach *was*
  computed separately: `reached 16/23 changed file(s)`, with all four of my scripts in the
  reached set.)
  - [gather:G-046] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have `reach` cache its computed result next to the ledger so `record` can
    pick it up without the raw snapshots, or make Phase 5 state plainly that the snapshots
    must outlive the `record` call.

- Gap: **`cmd skill_panel --args '{}'` toggles, so using it to *read* state changes it** —
  I called it to check whether the skill tree had opened and got `"open": false`, which
  looked like the debug panel's button had failed. It had not; the read closed the panel.
  The verb is documented as "absent = toggle", so this is a footgun in the project's own
  extension rather than the harness core, but the shape is general: several verbs here
  (`skill_panel`, `land_panel`, `crafting_panel`) are setter-or-toggle depending on
  whether a key is present, and none of them has a pure reader.
  - [gather:G-047] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: give the toggle verbs a read-only sibling, or make the status provider
    carry the panel-open flags (it already carries `skill_panel_open`) so state can be
    read without dispatching a mutation. Asserting on a mutating verb is a mistake the
    reply cannot warn you about.

- Gap: **no way to stand up a Control whose collaborators come from deep `@onready` node
  paths, so the badge bug could not be turned into a unit test** — `SkillTreeUi._ready()`
  builds the badge only after finding a `LevelUpManager` in the tree, and `LevelUpManager`
  resolves `$"../PlayerInfo/XpBar"` and `$"../../../../../ResourceManager"` at ready time.
  Standing up the second means a `ResourceManager2`, whose own `_ready()` dereferences
  `tile_map_handler.resource_found` and `tile_map_handler.tileMap` — so the fixture for a
  50-line badge is most of `main.tscn`. `_T.instantiate_ui()` takes a Node and handles the
  viewport, which is the hard half, but there is nothing for "give this node the neighbours
  its `@onready`s expect". The bug was therefore verified at runtime and left with no
  regression guard; `test_hud_toolbar.gd` covers the strip but cannot touch the badge.
  - [gather:G-048] status: open | seen: 2 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `_T.stub_tree({"../PlayerInfo/XpBar": ProgressBar, …})` helper that
    materialises placeholder nodes at a set of node paths before the node under test enters
    the tree. It cannot satisfy a typed `@onready` that needs a real class, but it would
    cover the common case — a path that exists only so `get_node` does not error — which is
    what four of the five paths above are.

- Gap: **the bridge has no raw-key primitive, so every raw-keycode handler in the project
  is unreachable from it** — `list-commands` offers `input_press` / `input_release` /
  `input_tap` / `input_sequence` / `input_actions`, and all of them dispatch an
  `InputEventAction`. `_unhandled_key_input` is only ever called with an `InputEventKey`,
  so the hotbar's number keys, its step keys and the Q/E pair were all invisible to the
  harness. That is not a small corner: it is why `E` being simultaneously `gather` and
  "next hotbar slot" — every pickaxe swing advancing the hotbar a slot — survived lint, the
  full unit suite and a complete `/verify` run earlier the same day.
  - [gather:G-049] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a generic `key <press|release|tap> --key NAME` verb in the harness core,
    taking the name `OS.find_keycode_from_string` accepts and setting both `keycode` and
    `physical_keycode` (projects split between the two — this one compares `keycode` in
    GDScript while `project.godot` binds by `physical_keycode`). Worked around by
    registering `press_key` in `devtools_ext/commands.gd`; it is ~25 lines and nothing in
    it is project-specific, so it belongs upstream rather than in every project that
    reads a raw key.

- Gap: **reach cannot see a RefCounted, so the file carrying the actual design change scores
  as unreached** — `verify_ledger.py reach` reported `NOT reached: ... systems/skill_tree.gd`,
  yet that file holds the `[Types.Item.IronResource]` unlock this whole change moved, and the
  run demonstrably exercised it (`cmd learn_skill --args '{"id":"smelting"}'` → `"learned
  smelting"` → iron nodes appear in the census). Reach intersects the diff against `script`
  fields in a `scene-tree` snapshot, and `SkillTree` extends `RefCounted` precisely so tests
  can build it without a SceneTree — it is never any node's script and so can never be
  reached by construction. Same for `crafting/recipes.gd`: autoloads live at `/root`, outside
  the `Main`-rooted snapshot. A verdict that silently downgrades on these is measuring the
  object model, not the run.
  - [gather:G-050] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have `reach` credit a file when a `cmd`/`run-method` call during the run
    touched it — the client already knows every verb it invoked, so recording the invoked
    verb names in the run row and letting projects map verb -> files would cover both
    RefCounted helpers and autoloads. Failing that, snapshot from `/root` rather than the
    main scene so autoloads at least appear. Worked around by reading the census delta after
    `learn_skill` and reporting reach as partial-by-construction in the summary.

- Gap: **reach cannot see a node that only exists for 0.85s** — `verify_ledger.py reach`
  reported `NOT reached: ... ui/splash_text.gd` on a run whose central assertion was
  `get-state --node /root/Main/Node2D/SplashTexts/SplashText --property text` returning
  `+5 XP`. Reach intersects the diff against `script` paths in a `scene-tree` snapshot, and
  every SplashText had freed itself by the time the Phase 5 snapshot was taken, so a file
  the run demonstrably exercised is filed as unverified. Worked around by taking the
  evidence from the live `get-state` and saying so in the summary — but the ledger row now
  under-reports, which is the one thing the ledger exists to prevent.
  - [gather:G-051] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have the game side accumulate a set of script paths seen across *every*
    `scene-tree` call in the session (or a `scripts-seen` verb that reports it), so reach is
    a union over the run rather than a single instant. A transient node — a splash, a
    particle, a projectile, a pickup — is exactly the kind of thing worth verifying at
    runtime and exactly the kind reach currently cannot credit.

- Gap: **lint cannot distinguish a genuinely unresolvable scene NodePath from its own false positives** — `lint_project.gd` printed `res://main.tscn | : SceneState: 'resource_manager' NodePath unresolved: Systems/ResourceManager` for correct paths and stayed silent about `Player.input_manager = NodePath("../../InputManager")`, which pointed at a node that no longer existed. Workaround was launching the game and reading `Player._ready` blow up. The checker already walks `SceneState`; resolving each NodePath against the scene's own node list would separate "cannot see into an instanced sub-scene" from "this target is not in this file", and the second class is exactly what a restructure produces.
  - [gather:G-052] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: in the `SceneState` pass, resolve each NodePath against the set of node paths declared in the same `.tscn`. Report `ERROR` when the path is rooted in this scene and matches nothing; keep the current advisory `INFO` only when it crosses into an instanced sub-scene it genuinely cannot see.

- Gap: **`reach` cannot see code that runs but owns no node** — the ledger reported `NOT reached: devtools_ext/commands.gd`, yet every assertion this run made went through it (`add_xp`, `goto_resource`, `gather_state`, `island_census`, `player_state`). Same for `items/pick_up_manager.gd`, whose pickups were created and vacuumed between two snapshots. Reach is computed by intersecting the diff against `script`/`scene_file` paths in a `scene-tree` snapshot, so an autoload, a devtools extension, or a transient node is structurally invisible to it and lands in the "not reached" list beside files that genuinely were not loaded.
  - [gather:G-053] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: fold the autoload list (`project.godot [autoload]`) and the configured `extension_script` into the reached set, and report transient-node scripts separately as `reached-transient` rather than as not reached — so the not-reached list stays a list of things that actually went unverified.

- Gap: **no way to evaluate an expression against project classes headlessly** — needed a
  single value (`LevelUpManager.xp_for_level(n)` for n in 2..17) straight from the engine
  rather than from a model of it. There is no verb for this: `run-method` is bridge-only
  and the bridge was off-limits, and `run_tests.gd` only runs discovered `test_*` methods.
  Workaround was writing `test/unit/test_zzz_scratch_curve.gd` whose whole body is
  `return _T.assert_true(false, "CURVE: " + ", ".join(out))`, running it with
  `--file test_zzz_scratch_curve` to read the value out of the *failure* message
  (`Selected: 1 of 194 discovered`, exit 1), then deleting the file. Abusing a failing
  assert as a print statement means the artifact that answers the question is one that
  must never be committed, and a forgotten cleanup ships a permanently-red suite.
  - [gather:G-054] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a headless `tools/eval.gd` taking a GDScript expression on the command
    line, resolving `class_name` globals, and printing the result — the no-game sibling of
    `run-method`. `godot --headless --path . --script res://tools/eval.gd -- --expr
    'LevelUpManager.xp_for_level(7)'`. Cheap to build (one `Expression.parse/execute`) and
    it removes the only reason to ever create a scratch test file.

- Gap: **a `--file` selector cannot report its own result when an unrelated test script
  fails to compile** — `run_tests.gd -- --file test_island_manager` printed
  `Selected: 7 of 189 discovered (file 'test_island_manager')` and
  `Total: 189 | Passed: 7 | Failed: 0`, then `RUNNER ERROR - the suite did not run to
  completion (exit 2)`. The 2 came entirely from `res://test/unit/test_mobile_controls.gd`,
  which another agent's in-flight `ui/mobile_controls.gd` (`Identifier "HOTBAR_CYCLE" not
  declared`) breaks — a file my diff does not touch and my selector did not select.
  Discovery loads every script before the selector is applied, so exit 2 is contagious: in a
  parallel-agent repo the documented "2 means you verified nothing" reading is wrong here,
  because the selected 7 verifiably ran and passed. Workaround was reading the per-test
  lines and the `Selected:` line and disregarding the exit code, which is precisely the
  habit the exit codes exist to prevent.
  - [gather:G-055] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: score the exit code over the *selected* set. A discovery-time compile
    failure in an unselected script should be a printed `[ERR]` and a distinct signal (a
    `Discovery errors: N` line, or exit 3), never the same 2 that means "your run did not
    happen". With a selector active, exit 1 if a selected test failed, 0 if none did, and
    let the unselected wreckage be reported without being fatal.

- Gap: **the live half of the resolution rule is unreachable from a headless test, so the
  test seam I added to make it testable is also what lets the real code go unrun** —
  `resolve_primary(item, enemy_in_reach)` is static and pure and its 12-row table is walked
  exhaustively, but the two functions that *supply* those arguments in the game,
  `_held_item()` (walks `get_parent().get_node_or_null("HotBarInventory")` then
  `Object.get("selected_slot_data")`) and `_enemy_in_reach()` (filters
  `get_tree().get_nodes_in_group("SaveLoad")` by `is Enemy` against `PlayerManager.player`),
  are executed by no test in the suite. `instantiate_ui` gives the overlay a `SubViewport`
  with no sibling `HotBarInventory`, no `PlayerManager.player` and no `Enemy`, so both return
  their null-guard answers and the whole live path is a straight line to
  `resolve_primary(null, false)`. `run_tests.gd -- --file test_mobile_controls` reports
  `Selected: 14 of 203 discovered (file 'test_mobile_controls')`, `Total: 203 | Passed: 14 |
  Failed: 0 | Skipped: 189`, exit 0 — a clean pass that is silent about whether the button
  can read the world at all. The workaround was `var primary_resolver: Callable`, injected by
  every test that needs a non-default answer; it makes the *pairing* assertable, which is the
  part that can strand a timer, and explicitly gives up on the *reading*. A typo in the
  `"selected_slot_data"` string literal would pass this entire suite.
  - [gather:G-056] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `_T.stub_siblings({name: Node})` that mounts named siblings alongside the
    control `instantiate_ui` creates, plus a documented way to populate an autoload field
    (`PlayerManager.player`) and a group for the duration of one test. Both `_held_item()` and
    `_enemy_in_reach()` would then be one three-line fixture away from being asserted, and the
    resolver seam could go back to being a convenience rather than the only route in. This is
    the same shortfall [G-048] describes from the world-generation side — "the reachable
    surface stops at the one `static func` I could carve out" — arriving at a UI file, which
    suggests the fixture, not the file, is what is missing.

- Gap: **no runtime pass at all this turn, by instruction** — the orchestrator forbade
  launching the game or running `/verify` because the DevTools bridge is a single shared
  command/result file pair and sibling agents were live (the same collision [G-055] was filed
  against from the other side: another agent's `--file` run took exit 2 from *this* file
  mid-edit). So `.devtools/verify-runs.jsonl` gets no row for a change that is entirely about
  what a thumb sees and does, and the three things only the running game can answer —
  whether `CONTEXT_POLL_INTERVAL = 0.15` repaints fast enough to be believed, whether
  `ENEMY_REACH = 28.0` flips at the moment it should rather than across the clearing, and
  whether BREAK at the top-right is actually reachable one-handed — are unverified by
  anything but argument. Not a harness defect; recorded so the ledger's denominator is not
  quietly wrong about a diff of this shape.
  - [gather:G-057] status: open | seen: 2 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: the per-session bus already exists (`-- --devtools-session <id>` +
    `--session <id>`), but `user://` is still shared for screenshots, baselines and the
    `.godot/` import cache, so it is not enough on its own and the standing advice is a
    manual project copy plus `GODOT_USERDATA`. A `devtools.py launch --isolated` that does the
    copy, the session id and the userdata dir in one command would make "verify inline" the
    default in a parallel-agent repo instead of something an orchestrator has to forbid.

- Gap: **the harness owns `Engine.time_scale` and has no way to say so, or to ask who else
  does.** `dev_tools.gd:1412` (`_cmd_set_game_speed`) writes it unconditionally and reports
  `previous_scale`; `dev_tools.gd:1469` (`_cmd_step_time`) pins it to 1.0 and restores
  `previous_scale` afterwards. Neither has any notion of the *game* also driving it, which is
  exactly what hit-stop does. Two concrete failure modes fall out of reading those two
  handlers, and I had to design around both without being able to observe either: a
  `set-game-speed` issued during a dip has its `previous_scale` recorded as `0.12` and would
  be "restored" to a value that was never the session's intent; and a `step-time` sampling
  across a dip has its process clock stretched, which surfaces as the `budget_exhausted`
  warning — i.e. as a *starved tree*, which is a completely different diagnosis from "the game
  deliberately slowed down for 100ms". My mitigation was to make `Juice.hit_stop` refuse to
  engage unless `Engine.time_scale` is already exactly 1.0, so the two never overlap in the
  engaging direction; that is a decision I made from source-reading, and it is untested
  against the actual verbs.
  - [gather:G-058] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `time-scale` status field in the standard status provider (who set it, and
    when), plus `step-time` reporting `time_scale_changed_during: bool` instead of folding a
    deliberate game-side dip into `budget_exhausted`. That turns "is this a hitch or is this
    hit-stop?" from an argument into a field.

- Gap: **`wait-frames` is time_scale-independent, which silently invalidates it as a clock probe**
  — I used `time python tools/devtools.py wait-frames 60` to check whether hit-stop had left
  `Engine.time_scale` stuck, and got `real 0m0.729s` after a kill against `0m0.745s` at rest.
  That reads as a clean pass. It is not evidence of anything: calibrating against a *known*
  slow clock gave `set-game-speed 0.2` → `wait-frames 60` = `0m0.680s`, identical. Godot's
  `time_scale` scales delta, not the tick rate, so physics still ticks 60x per real second and
  the verb cannot see the clock at all. Had I not calibrated, I would have reported the
  hit-stop safety property as verified on the strength of a measurement that could not fail.
  Workaround: used camera `trauma` decay as the probe instead — it advances on scaled delta,
  and discriminated 0.667 / 0.933 / (0.96 predicted for a stuck 0.12 dip) cleanly.
  - [gather:G-059] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have `performance` report `Engine.time_scale` in its output. It is one line,
    it is the state most likely to be left dirty by a test run, and there is currently **no**
    verb that reads it — `set_game_speed` only writes. A `get-state` on a node cannot reach
    `Engine`, so today the only way to know the game's clock is to infer it.

- Gap: **transient effects shorter than the bus round-trip are unobservable** — `HIT_STOP_MAX`
  is 0.25s and a devtools call round-trips in ~0.7s, so I could confirm the dip *ended*
  correctly but never that it *engaged*. `run-method _on_died` then `get-state` always lands
  after the deadline. This is the same shape as G-058 but from the opposite side (that one is
  about `step-time` across a dip; this is about sampling inside one).
  - [gather:G-060] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `--after-frames N` flag on `get-state`, so the read is scheduled inside the
    game at a known frame offset from the triggering call rather than racing the file bus.

- Gap: **`place_build` bypasses the code path it appears to test** — it calls
  `handler.set_tile(...)` directly (`devtools_ext/commands.gd:1100`), so it never runs
  `GameItemPlaceable._place()` and never awards build xp. My first runtime check used it and
  read `xp: 0, built_cells: {}` after a successful `"placed Wood Wall at (-1, 0)"`, which
  looks exactly like the new code being broken. It is not — the verb was never on that path,
  before this change or after. Ten minutes went into the wrong hypothesis.
  - [gather:G-061] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: either route `place_build` through `PlayerManager.place_tile(slot_data)` so
    it exercises the real chain, or rename it `set_build_tile` and say in its message that it
    writes the tilemap directly. A setup verb that silently skips the gameplay path is the
    "setter must leave the game in a state the game itself can reach" rule from the harness
    docs, broken.

- Gap: **`tile_at`'s cell and the game's "tile in front of player" are not the same cell** —
  `tile_at` reported `front cell {'x': 0, 'y': -1} source -1` for a cell that was
  simultaneously empty and unbuildable, because `_cell_near_player` does not apply the
  facing offset that `main.gd:get_tile_in_front_of_player()` does (`+/- Vector2i(1, 0)` on
  `is_facing_left()`). Every placement assertion I tried to anchor on it was ambiguous.
  - [gather:G-062] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have `tile_at` default to the game's own facing-aware front cell (call
    `get_tile_in_front_of_player()` when no offset args are given), and return the facing in
    `data` so the caller can see which square was read.

- Gap: **the Phase 0 drift check reports every harness file as drifted on a CRLF checkout.**
  `cmp -s` against `templates/` flagged all 8 files; `diff <(tr -d '\r' < src) <(tr -d '\r' < dst)`
  is empty for all 8. The project's copies have Windows line endings, the plugin's have Unix,
  and the check compares bytes — so "DRIFT" here means nothing at all, and a real local patch
  would be indistinguishable from this noise.
  - [gather:G-063] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: normalize line endings on both sides before comparing, e.g.
    `diff -q <(tr -d '\r' < "$src") <(tr -d '\r' < "$f")` in the Phase 0 snippet.

- Gap: **[G-006-style] no verb drives a placement through the real `place_wall` path.**
  `place_build` calls `handler.set_tile` directly and skips `is_occupied` entirely (the same
  bypass `gather-15o` files against build XP), and `run-method` cannot pass the `Vector2i` that
  `is_occupied` takes (`gather-6sp`). The workaround was to reach the fix through the whole
  player: `give_item` → `run-method select_slot` → walk with `input press move_*` until the
  terrain in front was the case under test → `input tap gather`. That worked, and it is honest
  end-to-end coverage, but finding a plain-grass cell and an open-water cell took ~15 bridge
  calls of blind walking because the only way to ask "what is in front of me" is `tile_at` one
  offset at a time.
  - [gather:G-064] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a project verb `goto_cell` taking a predicate (`"grass_clear"`, `"shore"`,
    `"resource"`) that teleports the player beside a matching cell and sets facing — the
    placement analogue of `goto_resource`, which already exists for exactly this reason on the
    gather side.

- Gap: **no way to assert an accumulated CanvasItem transform headlessly** — the property
  that actually matters here is `XpLabel.get_global_transform_with_canvas().get_scale()`
  against the camera zoom, i.e. the product across `HUD -> FloatingText -> XpLabel`. In a
  `--script` run there is no camera transform applied to the root viewport, so the test
  can only assert the one factor it passes in and trust that nothing above multiplies it.
  The workaround was to make `style_xp_label()` static and total (it takes the zoom
  reciprocal and the legibility factor as arguments), plus a comment in
  `camera_hud.gd:37-48` saying why `FloatingText` must stay out of `SCALED_CHILDREN` — a
  convention, not a gate. A future edit re-adding it would make the label blurry again and
  every test here would still pass.
  - [gather:G-073] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `run-method`-free bridge verb (or a headless helper) that reports a
    node's accumulated canvas transform scale, so "these glyphs rasterize 1:1" is one
    assertion against the live tree rather than an argument reconstructed from three
    files. `node-bounds` reports position/size but not the accumulated scale.

- Gap: **no way to preview a candidate sprite against real game tiles without importing
  it.** Judging 16x16 pixel art needs it seen at game scale, on the tileset's own grass,
  next to a tileset tree — otherwise the axe head reads as a floating grey brick (it did,
  for three iterations). The workaround was a throwaway `mockup.py` that opens
  `assets/art/tiles.png` with PIL, crops the pine at `(80,48,96,80)` and the grass green
  `(90,197,79)` by scanning the land tiles for the modal colour, and composites the
  candidate over it. Finding those two coordinates cost four crop-and-look rounds, because
  nothing in the project states where anything sits in the 400x400 atlas.
  - [gather:G-074] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `tile_at` style verb — or a headless `tools/atlas_map.gd` — that dumps
    the registry's `atlas_location` + `tile_source_id` per `Types.Item`, so "where is the
    tree tile" is one query instead of a binary search by cropping. The registry already
    holds this; it is just not readable from outside a running game. Would also have made
    the palette sampling one command rather than four PIL one-liners.

- Gap: **`verify_ledger reach` cannot see transient nodes, so it under-reports runtime
  coverage for anything short-lived.** `reach` reported `ui/splash_text.gd` and
  `items/pick_up.gd` as NOT reached, in the same run where I read live state off
  `/root/Main/World/SplashTexts/SplashText` (`font_size: 60`, `text: +10 XP`) and watched
  `PickUps` go to 0 children. Both files unquestionably executed. Reach intersects the diff
  against `script`/`scene_file` in a `scene-tree` snapshot, and both snapshots are taken at
  phase boundaries — by then every splash had freed itself (`live_splashes: 0`, which is the
  *pass condition* for this change) and every drop was collected. The failure mode is
  perverse: **the better the cleanup, the worse the reported reach**, so a leak-free
  short-lived node can never be credited.
  - [gather:G-068] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 (was G-074, reassigned — ID collision) | source: gather 2026-08-02
  - Improvement: have `dev_tools.gd` accumulate a set of every script path seen on any node
    across the session (hook `node_added`, or union the script paths on each `scene-tree`
    call into a persistent set) and expose it as a `scripts-seen` verb. `reach` should union
    that with the snapshots. A one-line addition to the status provider — `"scripts_seen": N`
    — would also make it visible that the set is being collected.

- Gap: **no way to assert a fractional-scale/filter combination, which is the actual bug
  class here.** The whole defect was "a 16px glyph atlas magnified 1.6x through a LINEAR
  filter". I can read `texture_filter: 1` and `scale: {"x": 0.125}` separately and do the
  multiplication in my head against a zoom I fetched from a third node, but nothing asserts
  the *product*. A future edit that changes the camera zoom re-breaks every world-space label
  in the game and every check here still passes.
  - [gather:G-075] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a `canvas-scale --node PATH` verb returning the accumulated
    `get_global_transform_with_canvas().get_scale()` plus the effective texture filter, so
    "this text rasterizes 1:1" is one assertion instead of three reads and an inference.
    This is the same gap [G-073] filed from the HUD side; if that entry is still open, these
    should be merged — [G-073] wanted the accumulated transform, this wants the filter with
    it, and they are one verb.

- Gap: **`step-time` does not sustain a held input action across stepped frames** — first
  observed as an unfiled Note (2026-08-02): `step-time --seconds 3` advanced a held-move
  player 4px where 2.5s of wall-clock `input press` + `sleep` moved it ~55px. The docs only
  promise the clock advances, but the practical effect is that "press, step-time, read"
  silently asserts nothing about held-input behavior.
  - [gather:G-084] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: `step-time --hold <action>` that re-asserts the action's pressed state on
    every stepped frame, and a `held_actions` field in the reply so the caller can see
    whether anything was sustained.

- Gap: **a `blocked` check does not affect the run verdict** — ledger row
  2026-08-02T04:58:57Z records `{"name": "crafting recipe cards rendered", "result":
  "blocked"}` and still `"verdict": "pass"`. A check that could not run is being scored as
  if it had passed.
  - [gather:G-085] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: `verify_ledger.py record` should refuse `verdict: pass` when any check is
    `blocked` (downgrade to `aborted` or a new `partial`), so the summary can't claim more
    than the run demonstrated.

- Gap: **a stash-based A/B that stashes only one file of a multi-file change produces a
  false "already fixed"** — recorded 2026-08-01: stashing `input_manager.gd` alone made the
  bug not reproduce because the rest of the causal chain was still applied. Nothing in the
  /verify workflow warns that an A/B must carry the whole diff.
  - [gather:G-086] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a Phase 4 note (and checklist line) for A/B testing: stash/restore the
    full changed-file set from Phase 0's diff, never a hand-picked subset.

- Gap: **slow-motion screenshots distort size and stacking judgments** — recorded
  2026-08-02: at `set-game-speed 0.08` the XP splash "looked enormous" purely because a
  stacking tween was stretched eight-fold. Good for draw-order questions, misleading for
  scale questions; nothing documents the distinction.
  - [gather:G-087] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: one paragraph in the verify skill's screenshot guidance: use slow-mo for
    ordering/occlusion, use `canvas-scale`-style reads (see G-073/G-075) for size claims.

- Gap: **`--import`'s blast radius is known only from prose** — two entries (2026-08-01,
  2026-08-02) narrowed it by hand: running the test suite does not dirty `project.godot`;
  only `--import` does, and not every import. That scoping lives nowhere actionable, so
  every agent re-derives when a dirty `project.godot` is self-inflicted.
  - [gather:G-088] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: document the blast radius next to the `--import` instruction in
    CLAUDE.harness.md, and have the verify skill snapshot/restore `project.godot` around
    any `--import` it performs (pairs with G-028).

- Gap: **no seed-sweep / property-test tier, though it repeatedly proved the cheapest
  decisive check** — the island-connectivity work found a 6% stranding rate only via a
  200-seed headless sweep after two live runs both reported "every island connects"
  (2026-08-01), and the entry concluded the sweep "should have come before the launch, not
  after". The lesson exists only as prose.
  - [gather:G-089] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a documented sweep pattern in the verify skill (Phase 1.5: "if the diff
    touches procedural generation, run the relevant `test_*` sweep with a seed count from
    config before launching"), plus a `sweep_hints` config key mapping path globs to test
    filters.

- Gap: **the wire contract's `data: Dictionary` promise was violated by two generic verbs
  on their failure paths** — `get_node_bounds` on a non-Control and `screenshot` on a
  failed capture omit `data` entirely, so the reply carries `"data": null` and a client
  indexing into it crashes. Found by the very first `check_templates.py --full` run (the
  H-001 contract test doing exactly what it was built for). Fixed centrally: `_write_result`
  now defaults non-Dictionary `data` to `{}` for every handler, extensions included.
  - [H-019] status: fixed | fixed-in: 0.8.0 | seen: 1 | harness: 0.7.0
  - Improvement (shipped): enforce the envelope at the single write point instead of
    trusting every handler's every return path.

- Note: launching the scratch game with `subprocess.PIPE` stdout/stderr stalled it before
  the bridge's first poll on Windows; redirecting to files fixed it. `check_templates.py`
  documents this in code; anyone writing a launcher (3b's `launch --isolated`) should
  redirect to files, never pipes it does not drain.

## 2026-08-02 — 0.8.0 close-out: self-validation shipped, 60+ gaps closed

The release gate now exists and gated this very release: `tools/test_scaffold.py`
(17-case matrix incl. the H-008 upgrade case), `tools/check_templates.py --full`
(6 stages, 51/51 verb-contract rows — it caught H-019 and a typed-assignment parse
error in `canvas_scale` before either could ship), and the doc fan-out check in
`record_version.py --check` (caught the undocumented `logs` command on its first run).
Closed this release: H-001, H-004, H-005, H-007, H-008 (remaining half), H-009, H-010,
H-013, H-014, H-018, H-019, plus the 53 pooled gather gaps whose status lines above now
read `fixed-in: 0.8.0`. Still open by choice: gather:G-005 and gather:G-057's full
project-copy isolation (env-only `launch --isolated` shipped), gather:G-018 and
gather:G-048 (partial — world-space skip and `instantiate_scene` shipped, baselines and
full `stub_tree` deferred), gather:G-011 (game-level), gather:G-013/G-014 (need export
templates; revisit with gather-m3f), H-016 (revisit once the post-tier ledger is longer).

- Gap: **no gaps this turn** — the release ran on the new gates end to end; the two
  bugs they caught (H-019, the `canvas_scale` parse error) are the system working.

## 2026-08-02 - Upstreamed 2 open gap(s) from gather (harness 0.4.0, 0.7.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather-devtools\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`cmd` verbs are not hyphen-aliased, contradicting commands.gd's own header** —
  `cmd goto-cell --args '{"predicate":"grass_clear"}'` returned
  `"message": "Unknown action: goto-cell"`; `cmd gather-stats` (the header's own example)
  fails identically. Workaround: underscore forms work; my new docstrings now show those.
  - [gather:G-082] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: normalize hyphens to underscores in the game-side dispatcher for `cmd`
    payload actions (the top-level verbs already get this), or fix the header comment.

- Gap: **a fresh git worktree cannot run ANY harness validation before `--import`, and
  `--import` is barred by its project.godot rewrite (G-028/G-088)** — first lint run in
  the never-imported worktree produced 1108 SCRIPT ERRORs ("Identifier "Types" not
  declared…" for every global class) yet still `exit 0`, and the runner then printed
  `[PASS]` for tests whose first line had errored (`Invalid call. Nonexistent function
  'cells_within' in base 'GDScript'` — the gather-1t9 shape, four times). Workaround that
  worked end-to-end: copy the project (minus .git/.godot) to a scratch dir, run
  `--import` there, verify there — 0 script errors, 250/250, full runtime session.
  - [gather:G-083] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a documented `worktree-verify` recipe (or flag on /verify) that
    auto-stages the scratch copy + import; failing that, the runner should refuse to
    report PASS when the class cache is absent, since every "pass" in that state is
    unverified.
