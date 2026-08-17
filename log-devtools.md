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
  - [gather:G-014] status: open | seen: 2 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-016] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-018] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-025] status: fixed | fixed-in: 0.8.0 | seen: 2 | harness: unknown | source: gather 2026-08-01
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
  - [gather:G-046] status: fixed | fixed-in: 0.8.0 | seen: 3 | harness: 0.7.0 | source: gather 2026-08-02
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
  - [gather:G-050] status: fixed | fixed-in: 0.8.0 | seen: 4 | harness: 0.7.0 | source: gather 2026-08-02
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
  - [gather:G-057] status: open | seen: 5 | harness: 0.7.0 | source: gather 2026-08-02
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
  - [gather:G-083] status: fixed | fixed-in: 0.11.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a documented `worktree-verify` recipe (or flag on /verify) that
    auto-stages the scratch copy + import; failing that, the runner should refuse to
    report PASS when the class cache is absent, since every "pass" in that state is
    unverified.
  - Note (0.11.0): closed by a different route than either option offered. `tools/name_check.py`
    resolves names from source plus a cached engine API index and never opens the project,
    so a never-imported worktree now has a Phase 1 gate that works — and it catches exactly
    the class of failure the 1108 lines were (a `class_name` that did not resolve). The
    scratch-copy recipe is no longer the only path. The **second** half of this entry — the
    test runner still printing `[PASS]` with no class cache — is genuinely not fixed and is
    carried forward as [H-029].

## 2026-08-05 - Upstreamed 57 open gap(s) from gather (harness 0.4.0, 0.7.0, 0.8.0, unread, unread (bridge was down when I asked))

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\gather\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **reach cannot see RefCounted item classes, so it under-reports what ran.** The
  ledger recorded `reached 3/9 changed file(s); NOT reached: … items/game_item_bone_enemy.gd
  …`, but that file was exercised decisively — `had_target_in_reach: False` can only be
  produced by `GameItemBoneEnemy.can_use()`, and the skull-preservation result by its
  `use()`. Reach is computed by intersecting the diff against `script`/`scene_file` paths in
  a `scene-tree` snapshot, and this project's entire item model is `GameItem` subclasses held
  as plain RefCounted values in `items.gd`'s `item_list` — they are never any node's script,
  so no amount of exercising them can register. The same applies to `items/types.gd` and to
  `devtools_ext/commands.gd`, which ran on every single call in this session. The honest
  residue after discounting those is `crafting/recipes.gd` and `systems/skill_tree.gd`, which
  genuinely were not reached: the recipe cost and the Industry unlock were never opened in a
  running game.
  - [gather:G-076] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: have the game side report the set of script paths actually *loaded*
    (`ResourceLoader`'s cache, or a `ClassDB`/`global_script_class_cache` walk) as a second
    reach input alongside the scene tree, so non-Node scripts can register. Without it, any
    project whose logic lives in Resources or RefCounted gets a permanently deflated reach
    number, which trains readers to ignore the field — the opposite of why it exists.
  - Note (0.9.0): closed by a different mechanism than the one proposed. Rather than a
    game-side `ResourceLoader`/`global_script_class_cache` walk, `verify_ledger.py` gained a
    `reach_aliases` map in `devtools_config.json` — the project declares which observed Node
    vouches for which non-Node script. Alias-credited files leave `NOT reached` and land in
    `reached_alias` with the voucher named inline (`+1 by alias: items/types.gd via
    items/items.gd`), never folded into `reached`, because a declaration is a claim and an
    observation is not. A voucher that was not itself reached credits nothing, and aliases
    do not chain. That solves the stated problem — a class that plainly ran no longer reads
    as a miss — at the cost of per-project configuration the proposed fix would not have
    needed. Same shipped change closes [G-102], whose Improvement asked for exactly this.

- Gap: **no verb places an arbitrary placeable tile, so the chest-delivery branch is
  untestable.** `_deliver()` prefers a chest in reach and falls back to a ground pickup; only
  the fallback got exercised. `place_build` refused with
  `type must be one of ["woodwall", "stonewall", "woodfloor", "stonefloor"]`, and
  `place_station` only maps `sawmill`/`furnace`. Driving the real path instead —
  `select_slot(4)` then `_on_gather()` — left the player still holding `Chest x 1` with the
  target cell reading `source -1` (empty), so placement silently no-opped exactly as
  `GameItemPlaceable`'s own comment warns. The only `TestChest` in the world turned out to be
  a worldgen chest at `(-552, 136)`, ~570px from the worker. Two smaller things compounded
  it: `set-state --property position --value "[400,400]"` reported `State updated` and wrote
  `(0, 0)` — the no-vector-coercion gap (gather-6sp) applying to `set-state`, not just
  `run-method`, and silently writing a wrong value rather than failing — and `tile_at`
  ignored its `x`/`y` arguments entirely, returning cell `(0, -1)` for all six cells queried.
  - [gather:G-077] status: open | seen: 3 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: a generic `place_tile` verb taking an item name and a cell offset, built on
    the same `handler.set_tile(cell, source_id, atlas)` call `place_worker` already uses.
    Every placeable in the game is one registry lookup away, and the per-feature placer verbs
    (`place_station`, `place_build`, now `place_worker`) are three partial reimplementations
    of it that each cover their own author's case and nobody else's.
  - Note (0.9.0, partial): only the compounding half shipped. `set-state --value` now
    coerces `"400,400"`, `"(400,400)"` and `[400,400]` to a Vector2 and prints the
    read-back, so the "reported `State updated` and wrote `(0, 0)`" behaviour is gone —
    that is the `set_state` side of [G-137]. The headline is untouched: there is still no
    verb that places an arbitrary placeable tile, so the chest-delivery branch is still
    untestable, and `tile_at` ignoring its `x`/`y` is project-side. Stays open on the
    `place_tile` ask.

- Gap: **no headless fixture for "the actual world", so world-shaped assumptions go
  unchecked until runtime.** Every routing rule was unit-tested and correct in the abstract;
  what was wrong was the mapping from those rules onto real tile data. The unit suite cannot
  see that a scene tile occupies its own cell, or which atlas coord the starting island uses,
  because `for_cells` invents the world it tests against.
  - [gather:G-078] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-03
  - Improvement: a headless fixture that loads `world/tile_map.tscn` (a plain PackedScene,
    no autoloads needed) and exposes its layer-0/1 cells, so "is the home island walkable"
    becomes a unit test rather than a runtime discovery. The scene is already on disk and
    already parsed by lint; nothing about it needs a running game.

- Gap: **no new gaps.** Worker/player collision was settled with `input press move_right`
  - [gather:auto-bff6fa] status: open | seen: 1 | source: gather 2026-08-03
  plus `step-time` and two `get-state` reads: the player went from x=8 to x=43 through a
  worker sitting at x=40. No verb was missing for any of it.

- Gap: **`screenshot` cannot exclude the HUD or crop, so it cannot produce store art.** A
  store cover is game pixels with no UI on them, at a fixed aspect. The verb only captures
  the whole window with everything visible, so the sequence was two `set-state` calls to hide
  `/root/Main/World/Player/Camera2D/HUD` and `/root/Main/UI` by hand, then a separate PIL
  script to crop 1260x1000 out of 1920x1080 and box-filter it to 630x500. Hiding the UI by
  hand is also easy to get wrong in the other direction — I re-enabled both before the
  gallery shots, and nothing would have told me if I had not.
  - [gather:G-079] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: `screenshot --region X,Y,W,H` and `--hide-group GROUP` (repeatable), so a
    crop is reproducible from the command line instead of living in a throwaway script, and
    so "capture the world without the HUD" is one call that cannot leave the HUD hidden.

- Gap: **nothing in the harness can answer "does the shipped web build still boot".** The
  page now serves `gather-html5.zip` pushed by CI, and every check available here runs the
  desktop build from source. The export that itch actually serves is never exercised.
  - [gather:G-080] status: open | seen: 1 | harness: 0.7.0 | source: gather 2026-08-02
  - Improvement: an entry point that serves `bin/` and drives the exported HTML5 build
    through the same bridge, so `--export-release` output is verifiable before it is public.

- Gap: **a stale `.godot` class cache is undiagnosable from either gate's output.** After
  rebasing this worktree onto a `main` that had added `class_name BoneWorker`, lint exited
  1 and the runner exited 2 with `Total: 295 | Passed: 257 | Failed: 5` and **135**
  `SCRIPT ERROR` lines, every one of them a cascade from a single missing entry in
  `.godot/global_script_class_cache.cfg`:
  ```
  SCRIPT ERROR: Parse Error: Could not find type "BoneWorker" in the current scope.
  ERROR: Failed to load script "res://items/items.gd" with error "Compilation failed".
  ERROR: Failed to load script "res://devtools_ext/commands.gd" with error "Parse error".
  ```
  CLAUDE.md documents `--import` as required after *adding* a `class_name`; the case that
  bites is *receiving* one — a rebase, a pull, a branch switch — where you did not add
  anything and the failure presents as five broken tests in files you never touched.
  `--import` fixed it (`0 error(s), 0 warning(s)`, 295/295), but only because the project's
  own docs had the answer; the harness's output pointed nowhere.
  - [gather:G-090] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-02
  - Improvement: have `lint_project.gd` compare the `class_name` declarations it already
    scans against `.godot/global_script_class_cache.cfg` and, on a mismatch, fail with one
    line — `stale class cache: BoneWorker declared in res://… but absent from the cache; run
    --import` — ahead of the 135 parse errors rather than buried under them. It has both
    halves of the comparison in hand already.

- Gap: **`devtools.py launch --isolated` advertises userdata isolation it does not
  deliver, and the failure reads as a dead game.** It prints `userdata:
  C:\...\devtools_userdata__8__cbit` and `Subsequent calls: ... --userdata <that path>`,
  but Godot resolves `user://` from the engine and honours no `GODOT_USERDATA`, so the
  temp dir stayed empty (`ls` → nothing) and every call with the flag it told me to use
  failed with `game not running: 'ping' was never picked up`. Dropping `--userdata` and
  keeping only `--session` worked first try. This is the *client* half of the note already
  under [G-036]; the difference is that `--isolated` now actively hands you the broken
  invocation.
  - [gather:G-091] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: either drop the `userdata` line from `--isolated` output and say plainly
    that only the bus is isolated, or make it real by writing a scratch copy of
    `project.godot` with `use_custom_user_dir=true` — which is the [G-057] ask anyway.
  - Note (0.9.0): the first of the two options this entry offered, plus more than it asked.
    `--isolated` no longer prints a `userdata:` line at all; it prints the isolated bus dir
    and, separately, `user://: <path>   (SHARED …)`. The bus really is isolated now
    (`--devtools-busdir` / `GODOT_DEVTOOLS_BUSDIR`, honoured by the autoload), and `launch`
    pings it before printing the follow-up command, so it can no longer hand you a broken
    invocation. The `user://` half stays open under [G-115].

- Gap: **`launch` cannot pass extra arguments to the Godot binary, so any run needing an
  engine flag has to re-implement launching.** Recording needs `--write-movie
  <dir>/frame.png --fixed-fps 30`, and `cmd_launch` builds a fixed
  `[godot, --path, project, --mute]` with no passthrough. `tools/capture_clip.py` therefore
  duplicates the whole launch path — binary resolution from `devtools_config.json`, the
  `GODOT_DEVTOOLS_SESSION` env var, detached stdout/stderr redirection — about 30 lines
  that will drift from the harness's copy. It also has to *not* pass `--mute`, since the
  movie writer records the audio bus into a `.wav` and a muted run captures silence.
  - [gather:G-092] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: let `launch` forward everything after a bare `--` to the Godot command
    line (`devtools.py launch --isolated -- --write-movie out/frame.png --fixed-fps 30`),
    and make `--mute` opt-out rather than unconditional.

- Gap: **Nothing in the harness lets N agents validate in parallel, so the only safe
  policy is "agents never run Godot".** The bridge is one command/result pair and `.godot/`
  is one import cache, so three agents each running `run_tests.gd` is already a shared-writer
  hazard before the bridge is even involved. I forbade all Godot execution in the subagent
  prompts and took every gate myself, which serialises the slowest part of the work behind
  one agent.
  - [gather:G-093] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: a `devtools.py scratch-clone` that stamps out a copy of the project with
    `use_custom_user_dir=true` and a unique `custom_user_dir_name`, prints the path, and
    cleans up on exit — the [G-057] ask, but reached for parallel *validation* rather than
    parallel play. Headless lint/test would still need a separate `.godot/` per clone,
    which the clone gives for free.
  - Note (0.11.0): partially addressed, deliberately not closed. `tools/name_check.py` is a
    real gate that N agents can run at once on one checkout — it opens no project and writes
    nothing to `.godot/` — so "agents never run Godot" is no longer the same as "agents never
    validate". But lint, the test runner and the bridge still each need the import cache, so
    the `scratch-clone` this entry asks for is still the missing piece and this stays open.

- Gap: **A subagent that cannot run Godot also cannot generate a `.uid`, so it hand-writes
  one and nobody can validate it until the orchestrator imports.** The agent said so
  explicitly: "the `.uid` I hand-wrote has not been validated by Godot." Lint's `UIDs: OK`
  covers presence and staleness but is only reachable with a working tree that compiles,
  which is exactly what a fan-out does not have until every agent lands.
  - [gather:G-094] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: a `devtools.py new-uid` that emits a fresh, correctly-encoded, collision-
    checked uid string without launching the editor or importing. It is a pure function over
    `ResourceUID::id_to_text` plus a scan of existing sidecars, it needs no game, and it
    would let any agent write a valid sidecar for a script it just created.

- Gap: **`reach` counts `test_dir` scripts in the worktree denominator, but a game session
  can never load them.** This run reported `worktree … reached 2/4 changed file(s); NOT
  reached: test/unit/test_player_save.gd, test/unit/test_save_load.gd`. Both game files were
  reached; the two "misses" are unit tests that ran in Phase 1 and are structurally incapable
  of appearing in a `scene-tree` snapshot. The `.uid` sidecars beside them were correctly
  binned as "not applicable", so the classifier already has the concept — the test scripts
  just are not in it. Effect is the unflattering mirror of the fixed [G-044]: writing a test
  alongside a fix permanently caps your reach ratio below 100%.
  - [gather:G-095] status: fixed | fixed-in: 0.9.0 | seen: 3 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: put paths under the configured `test_dir` in the existing "not applicable"
    bucket, or better, credit them from the Phase 1 run — `run_tests.gd` already knows
    exactly which test scripts it selected and executed.

- Gap: **`cmd` and `run-method` results are printed as Python-repr-ish text, not JSON, so
  every reply has to be re-parsed by hand.** `cmd build_demo_world` printed a dict I had to
  slice from the first `{` and feed to `json.loads`, and `run-method` answers `Result: None`
  for a `-> void` — indistinguishable from a call that raised, which is the exact failure
  mode `gather-5my` is about. I asserted around it by reading the world afterwards, but a
  verb that reports "did this call complete" would have been a one-liner.
  - [gather:G-096] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: give `run-method` a `--json` envelope like `scripts-seen` has, and have it
    distinguish "returned null" from "aborted" — the bridge already knows, since a raise
    unwinds before the reply is written.

- Gap: **no headless way to check a layout budget** — deciding whether a fourth toolbar
  button fits a 390px portrait viewport meant deriving it on paper across `UiTheme.scale_for`,
  `scaled`, `scaled_touch` and `_apply_scale`, because the only executable check needs a
  running game or a full test run with `_T.instantiate_ui`. The subagent reached the same
  wall and proposed the same fix independently.
  - [gather:G-097] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: a headless `layout-probe` that takes a scene path plus a viewport size and
    prints the resulting node rects, so a width budget is one command rather than a launch.
  - Note: the agent proposed filing this as [G-060], which is already taken *and* resolved
    `wontfix` in this file. Ids are assigned by the orchestrator, not by subagents — worth
    stating in the brief next time, since a colliding id silently corrupts `upstream_gaps.py`.

- Gap: **a contract handed to N agents has no checker, so a seam only fails at runtime.**
  I pinned the `SaveLoad` slot API precisely and all three agents matched it exactly; the
  break was in the API I described only in prose — I told the verbs agent the panel would
  expose `open()`/`close()`, and told the panel agent to build on `PanelFrame` without
  pinning its wrapper names. It chose `set_open(bool)`. Nothing in lint, the type system or
  the tests connects a `has_method("open")` string in one file to a `func set_open` in
  another.
  - [gather:G-098] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: extend `--find-orphans`-style static analysis to flag `has_method("x")` /
    `call("x")` / `connect("x", …)` string literals naming a method or signal that exists
    nowhere in the project. It is the same scan, and it would have printed this one as a
    finding before the game was ever launched.
  - Note (0.9.0, partial): the scan shipped — `lint_project.gd` rule `string_ref_unresolved`
    flags `has_method` / `has_signal` / `emit_signal` / `call` / `call_deferred` / `connect`
    string literals that resolve to no `func` and no `signal` anywhere in the project. It
    suppresses any name `ClassDB` carries on **any** engine class, and this gap's own
    example is one of those: `open` is `FileAccess.open`; so are `close`, `start`, `stop`,
    `play`, `clear`. So `has_method("open")` against a panel that implements
    `set_open(bool)` is still not flagged — the exact literal that cost the session. What
    it does catch is project-shaped contract names (`set_opened`, `wave_cleared`), which is
    worth having but is not the case filed. Dropping the ClassDB suppression is not the
    fix: every legitimate `connect("pressed", …)` would become a finding, and an advisory
    that noisy is one nobody reads. Closing it properly needs the receiver's static type,
    which a text scan does not have.

- Gap: **no way to stage two interacting world states without the setup verbs undoing each
  other.** `place_station` puts a station at the nearest free cell *to the player* and
  `goto_resource` teleports the player to a resource; there is no "put the player somewhere
  that satisfies both predicates at once". Every attempt cost a launch-to-assert cycle and
  the run still ends with a blocked check.
  - [gather:G-099] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: let the project's own setup verbs take an explicit target cell
    (`place_station --args '{"cell":{"x":-8,"y":3}}'`, `goto_cell` already does) so a test
    can compose a scene deliberately instead of hoping two nearest-free-cell searches
    happen to cooperate. That is a `devtools_ext` change, not a harness one, but the gap is
    the harness's: nothing in it makes composing world state any easier than by hand.

- Gap: **a stale instance from an earlier turn silently took the bus, and the failure looked
  like a JSON parse error in my own script** — `scene-tree` returned nothing parseable, and
  only re-running it surfaced `Foreign instance on the bus: the reply to 'scene_tree' came
  from pid 22412, but ... devtools_owner.json says pid 11968 owns this bus`. The detection
  is good and it is exactly what the owner file is for; the problem is that the *first*
  call failed opaquely and the diagnosis only appeared on the retry.
  - [gather:G-100] status: open | seen: 3 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: check the owner file before writing the command rather than when reading a
    crossed reply, so the very first call fails with the pid mismatch instead of with an
    empty response the caller has to interpret.
  - Note (0.9.0, partial): `send_command` now reads the owner record *before* it writes the
    command file (`owner_status()`), so the pid a reply is judged against is the one that
    was there when the request went out, and both the precheck message and the mismatch
    error now say whether that pid is **confirmed gone** or **still alive** rather than
    guessing "has likely exited". A dead-owner mismatch is called out as a survivor of an
    earlier run, with the kill command — which is the [G-103] half.
    An earlier cut of this fix *deleted* the stale owner file pre-write, and the review of
    this log caught that it removes the very record `reply.pid` is compared against, so a
    survivor's reply would then be accepted silently. It does not delete any more; staleness
    is reported, never tidied away. What remains open is the gap as literally written: the
    only condition a pre-write read can detect is "the recorded owner is gone". A **live**
    second instance answering a bus it does not own is still caught from `reply.pid` after
    the reply comes back, not before the command goes out — and it cannot be otherwise while
    nothing on disk distinguishes a second live poller from none.
    Stays open: the first call still cannot name a live foreign instance before it answers.

- Gap: **`worker_state` indices are not stable across calls, and the trace silently lies.**
  `devtools_ext/commands.gd:1896` sorts `_bone_machines()` by live `position`, so as workers
  walk they swap places in the array. A per-index trace across `step-time` steps therefore
  attributes one worker's state to another: my first 24-sample run showed `W0` with
  `tgt=(-17,-11)` and then `tgt=(0,0)` two samples later, which is impossible for one node
  (`_target_cell` is never reset). I lost a trace to it before spotting it. The sort's own
  docstring promises stability ("in a stable order so `--args '{\"station\": 1}'` means the
  same thing across calls") — true for stations, which do not move, false for workers.
  - [gather:G-101] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: sort walkers by an identity that does not move — `_home_position` when
    anchored, else the placed cell — or have `_worker_report` carry a stable `id` field
    (`get_instance_id()`) so a caller can key on something other than array position. The
    workaround was re-keying every sample on `_home_position` client-side.

- Gap: **reach cannot see a `RefCounted`, so a class that plainly ran reports as unreached.**
  `verify_ledger.py reach` returned `reached 1/4 ... NOT reached: world/tile_path_finder.gd`
  for the very file this run was about. `TilePathFinder` is held as a plain field on the
  worker (`_finder`) and deliberately never added to the tree — CLAUDE.md requires that of
  `RefCounted` helpers after `HealthManager` leaked one object per enemy — so neither
  `scene-tree` nor `scripts-seen` can observe it, and the ledger downgrades a run that
  exercised it for 40 game-seconds and produced 17-waypoint paths out of it.
  - [gather:G-102] status: fixed | fixed-in: 0.9.0 | seen: 7 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: let a project declare non-Node scripts that a reached Node owns — e.g. a
    `reach_aliases` map in `devtools_config.json` (`world/tile_path_finder.gd` credited when
    `world/tile_scenes/bone_worker.gd` is reached) — or credit them the way autoloads are
    already credited as `implicit`. Without it, every `RefCounted` helper in the project is
    permanently unreachable by the metric, which trains readers to discount the number.

- Gap: **a project verb can kill the game, and the bus cannot tell you it was the verb.**
  `cmd give_item --args '{"name":"Gold Coin","count":9000}'` — intended to fund the land
  purchases that open the boss island — took the process down. The next call reported
  `game not running: 'buy_land' was never picked up`, and `logs --tail` ended on
  `[15:53:13] [command] Executing: give_item` with no error line after it, so the log shows
  what was running when it died but nothing about why. Worse, the relaunch then failed its
  precheck against a STALE `devtools_owner.json` (`says pid 7080 ... has likely exited`) while
  a fresh instance was in fact up, and `tasklist` showed two live Godot processes — the
  crashed one had not fully exited, which is the multi-instance cross-talk hazard arriving by
  accident rather than by choice. Recovery was `taskkill //F`, delete the owner and
  command/result files, relaunch.
  - [gather:G-103] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: two small things. (1) Have the bus write a `last_command` breadcrumb that
    survives the process, so "the game died during verb X" is readable rather than inferred
    from the last log line. (2) Make the ping precheck notice that the owner pid is dead AND
    a bus file is being consumed, and clear the stale owner itself instead of refusing — a
    stale owner file after a crash is the normal case, not an anomaly, and right now it makes
    the recovery path look like a second failure.

- Gap: **`island_census` reports `opened`, but nothing reports what last called
  `refresh_connections` or `seed_island`.** The census told me *that* home was starved and the
  islands closed at radius 34, which is already the useful half. Attributing it still meant
  grepping every writer of `parcels_bought`/`_expand` by hand to find the one path that skips
  the signal. A verb-level "who stocked this region, and when" — even just a frame counter and
  the caller — would have gone from symptom to `_max_out_land` in one call.
  - [gather:G-104] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have `LandRegion` record `stocked_at_frame` and `opened_at_frame`, and surface
    both in `island_census`. A region that is open with `stocked_at_frame == -1` is this bug,
    stated rather than inferred.

- Gap: **no verb reports a node's effective canvas / `CanvasLayer` ancestry.** The plan's
  central risk is that `CanvasModulate` tints exactly one canvas, so `Ocean` (layer -100)
  and `UI` (layer 1) are missed while the diegetic `HUD` is hit. Establishing that meant
  grepping `main.tscn` for every `type="CanvasLayer"` and hand-walking parents; `scene-tree`
  reports `script` and `scene_file` per node but nothing about which canvas it renders into.
  `canvas-scale --node PATH` is adjacent — it already walks the canvas chain to accumulate
  scale — and stops one field short.
  - [gather:G-105] status: fixed | fixed-in: 0.9.0 | seen: 4 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have `canvas-scale` also return `canvas_layer_path` and `canvas_layer` (the
    `layer` int, or `0` for the root canvas). Then "will this CanvasModulate reach that node"
    is one call per node instead of a manual read of the scene file.

- Gap: **[G-106] no way to discover the item names `give_item` accepts.** Eight calls,
  four of them wasted: `Iron`, `Gold`, `Coal`, `IronBar`, `GoldBar` each answered
  `"no item named 'IronBar'"`. The verb keys off the display name, so the working
  strings are `"Iron Ore"`, `"Gold Ore"`, `"Iron Bar"`, `"Gold Bar"` — with spaces —
  while `Types.Item` spells them `IronOre`/`IronBar`. Nothing in the reply says which
  space it wants, and `list-commands` describes the verb, not its vocabulary. Had to
  read `items/types.gd` and infer the display-name mapping.
  - [gather:G-106] status: open | seen: 2 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have the failure reply carry candidates —
    `"no item named 'IronBar' (did you mean 'Iron Bar'?)"` — built from a fuzzy match
    over `GameItems.item_list`. A bare `items` verb listing registered names would
    close it outright and costs about ten lines.

- Gap: **[G-107] `build_demo_world` mutates the world, then fails.** Its `data` shows
  `"land": {"parcels_granted": 0, "radius_after": 34, ...}` — it runs `_max_out_land`
  *before* searching for a house site, so the refusal leaves land already bought. On a
  fresh world that is a one-way change to the thing being set up, reported under
  `"success": false` where it reads like nothing happened. It also gives no hint where
  a clear 7x5 site does exist, so there is no retry short of walking the player somewhere
  and guessing.
  - [gather:G-107] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: search for the site first and bail before granting land, or return the
    nearest viable corner in `data` so the caller can `goto_cell` and retry.

- Gap: **`get-state` cannot address a Control that Godot auto-named** — the new price label
  was initially unnamed, like the three siblings already on that card, so it landed under
  `@Label@249`. Feeding that path straight back from `scene-tree` gives
  `Failed: Node not found: …/@MarginContainer@243/@HBoxContainer@244/@Label@249`, so a node
  the snapshot had just listed was unreadable. Worked around in the project rather than the
  harness (`_cost_label.name = "Cost"`), which is the right fix here but does not help
  anyone reading a Control they did not write — the parent `@MarginContainer@243` and
  `@HBoxContainer@244` in that same path are still unaddressable.
  - [gather:G-108] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have `get-state`/`node-bounds` accept the `@Name@id` segments that
    `scene-tree` emits — they are stable for the life of the node, and round-tripping a path
    the harness itself just printed should never 404. Failing that, have `scene-tree` mark
    such nodes as unaddressable so the dead end is visible before it is hit.

- Gap: **no way to find a node by a property value; auto-named siblings must be probed one
  at a time, and their names churn between calls.** The boss is an `Enemy` among the
  EnemySpawner's children, and persisted/ambient enemies get engine names:
  `scene-tree` returns `@CharacterBody2D@385`, `@CharacterBody2D@388`, `@CharacterBody2D@416`,
  `@CharacterBody2D@419`, `Enemy`, `SpiderEnemy` — none of which says which one is the
  Elite. Finding it costs one `get-state --property type` round trip per child (6 calls,
  twice over), and by the time the loop finished the ids had rotated as the spawner
  trickled enemies in: `Failed: Node not found:
  /root/Main/World/EnemySpawner/@CharacterBody2D@1015 (also tried under /root)` on a path
  that had answered 20 seconds earlier. Workaround: drove `IslandManager._on_boss_died`
  directly instead of killing the real node, which tests the reward path but not the
  `health_manager.died` wiring that reaches it.
  - [gather:G-109] status: fixed | fixed-in: 0.9.0 | seen: 2 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: let `scene-tree` take a `--property NAME` to include that property on
    every node it emits (`--property type` would have answered this in one call), or add a
    `find-nodes --class Enemy --where type=Elite` verb returning matching paths. The
    existing `clear-nodes --group/--class/--method` already does this matching internally
    to *free* nodes; exposing the same predicate as a read would cost little.

- Gap: **`get-state` cannot see inside a Resource, so the one field that identifies a sprite
  never crosses the bus.** `get-state --node /root/Main/World/Player/Gather --property texture`
  answered `texture: ():<AtlasTexture#-9223371914985075739>` — an object id. Which *picture* a
  Sprite2D is showing lives in `texture.region`, one hop down, and there is no node path for a
  sub-resource, so no generic verb can reach it. Worked around by registering an
  `equipped_sprite` project verb that flattens the AtlasTexture to its base image plus an
  absolute rect. Flattening was not optional: main.tscn authors textures straight over
  `tiles.png` while `GameItem.get_atlas()` wraps `game_items_atlas.tres` (an AtlasTexture whose
  own atlas is that same png), so comparing the wrapper reports a mismatch between identical
  pictures, and comparing the rect alone can match two different ones across the three sheets.
  - [gather:G-110] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: let `--property` walk one level into a Resource — `--property texture.region`,
    `--property texture.atlas.resource_path` — falling back to the object id when the path does
    not resolve. Sprite regions, StyleBox colors, Shape2D extents and Timer sub-resources all
    sit exactly one hop past what `get-state` can currently say.

- Gap: **`launch --isolated` prints a `--userdata` the game does not poll.** The gap it was
  built for is [G-057], now shipped in 0.8.0 — but it does not work. `launch --isolated`
  reported `session: cda33b18`, `userdata: …\Temp\devtools_userdata_5_4b1mjg` and the exact
  follow-up command to use; the process started and reached `Entering state: PlayerIdle` in
  `launch_stdout.log`, yet `--session cda33b18 --userdata …` answered `game not running:
  'ping' was never picked up … polling: …\Temp\devtools_userdata_5_4b1mjg`. So the client
  watches the isolated dir and the game does not write to it — the session id reaches the game
  but the userdata override does not. Fell back to serialising on the default bus, which cost
  a run: two instances ended up live and the client caught it with `Foreign instance on the
  bus: the reply to 'use_slot' came from pid 19256, but devtools_owner.json says pid 19472
  owns this bus`.
  - [gather:G-111] status: fixed | fixed-in: 0.9.0 | seen: 2 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have `--isolated` pass the userdata dir to the game the way it passes the
    session id (`--userdata`/`GODOT_USERDATA` on the child process), and have `launch` verify
    the new instance answers a `ping` on its own bus before printing the follow-up command —
    printing a command that cannot work is worse than failing, because it reads as success.
  - Note (0.9.0): both asks, in the only form Godot allows. The dir the follow-up command
    names is now a dir the game genuinely writes to — `launch` passes it as
    `-- --devtools-busdir <dir>` and as `GODOT_DEVTOOLS_BUSDIR`, and the autoload puts the
    command, result, owner and breadcrumb files there — so "the client watches the isolated
    dir and the game does not write to it" is no longer true. And `launch` proves it with a
    `ping` on that bus before printing anything. It is the *bus* that moved, not `user://`;
    the sharing that leaves behind is [G-115], which stays open.

- Gap: **`quit` is not reliably fatal, and nothing notices the survivor until it corrupts a
  read.** Three separate times a `quit` followed by a relaunch left the old process alive
  (`tasklist` showed two Godot PIDs, once at 1.4 GB), and the only symptom was verbs returning
  empty output. `python tools/devtools.py ping` then said `No response`, which reads as *no*
  game rather than *two*. Had to `taskkill //IM` and start over.
  - [gather:G-112] status: fixed | fixed-in: 0.9.0 | seen: 2 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have `quit` wait for the process to actually exit and report if it did not,
    and have `launch` refuse to start when another instance already owns the bus — the owner
    file it already writes has everything needed to detect it.

- Gap: **`goto_resource` cannot reach a scene-tile resource** — `cmd goto_resource --args
  '{"name":"berry"}'` returned `"no live 'berry' node on the island"` while
  `cmd berry_bushes` in the same session listed five, three of them in `home`.
  `_resource_cells()` builds its atlas map with `if not resource.is_scene_tile`, so the
  two scene-backed resources are invisible to the verb that exists to stand next to a
  resource. Workaround: read a cell from `berry_bushes`, derive world coords by hand
  (`cell * 16 + (8, 8)`, inferred from an earlier reply's tree position) and
  `set-state --property position`.
  - [gather:G-113] status: open | seen: 5 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: fall back to `TileMapHandler.scene_tile_at`/the scene-tile children when
    the atlas map misses, so `goto_resource` covers both representations the way
    `resource_node_census` already does.

- Gap: **the launched instance exited with an empty stderr log and no other signal** —
  a `set-state` mid-session returned `Owner file devtools_owner.json says pid 19168 …
  that process has likely exited`, and `.devtools/launch_stderr.log` was zero bytes, so
  there is nothing to distinguish a crash from a clean exit after the fact. Workaround:
  none needed — the capture was already taken — but the run could not be resumed.
  - [gather:G-114] status: open | seen: 3 | harness: 0.8.0 | source: gather 2026-08-03
  - Improvement: have `launch` record the process exit code to `.devtools/` when the
    child dies, so a later bus failure can say "exited with 0" instead of "likely exited".

- Gap: **`launch --isolated` half-applies its isolation, which is worse than not applying
  it.** `python tools/devtools.py launch --isolated` printed
  `session: 73c99d1d` / `userdata: C:\...\Temp\devtools_userdata_lage9wvx` and told me to
  pass both on subsequent calls. Doing exactly that gave `game not running`, as did
  `--userdata` alone, as did the default bus. `ls` on the printed userdata dir showed it was
  never created; the game had in fact taken the session and was writing
  `devtools_log_73c99d1d.jsonl` into the DEFAULT `app_userdata/Gather`. So the session half
  works and the userdata half silently does not. Workaround: drop `--userdata` and call
  `--session 73c99d1d` against the default dir, which answered immediately.
  This matters more than an ordinary ergonomics gap because `--isolated` is the documented
  escape hatch for the one-instance-at-a-time rule: it tells you that you are isolated from
  other instances while leaving you sharing their `user://`, screenshots and baselines. A
  second Godot was in fact live in this checkout throughout this session.
  - [gather:G-115] status: open | seen: 2 | harness: unread (bridge was down when I asked) | source: gather 2026-08-03
  - Improvement: have `--isolated` fail loudly if the child's userdata dir does not exist a
    second after launch, rather than printing a path nothing will ever write to. Passing
    `GODOT_USERDATA` through to the spawned process — or `--userdata` straight to the game —
    would fix the underlying cause.
  - Note (0.9.0, partial): the dishonesty is gone and the bus half is real. The autoload
    honours `--devtools-busdir` / `GODOT_DEVTOOLS_BUSDIR`, `launch --isolated` passes a
    fresh temp dir on both, and it now proves the new instance answers a `ping` on that bus
    *before* printing the follow-up command — so a half-applied isolation fails at launch
    instead of handing you a working-looking invocation. `launch` also prints
    `user://: <path>   (SHARED — saves, screenshots and UI baselines are not isolated)`,
    and `ping` reports `bus_dir` and `user_dir` as separate fields. What this entry names
    and 0.9.0 does not change: `user://` is still shared, so screenshots, saves and UI
    baselines still collide between instances. Godot resolves `user://` inside the engine
    with no runtime switch, so the improvement above is not implementable as written; the
    real close is [G-057]'s scratch project copy with `use_custom_user_dir=true`. Stays open
    for that. `--isolated` is now honest about what it isolates, which is the smaller half.

- Gap: **`harness-version` cannot be answered without a running game, so the field it exists
  to fill is unfillable at the moment you write the log.** `python tools/devtools.py
  harness-version` after `quit` returned `game not running: 'harness_version' was never
  picked up`. Every entry in this file is written after the session is over, which is
  precisely when the bridge is down. Workaround: none — the `harness:` field above says
  "unread" rather than carrying a number I would have had to copy from a neighbouring entry
  and might have been stale.
  - [gather:G-116] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: unread | source: gather 2026-08-03
  - Improvement: read the client-side revision from disk when the bridge is down and report
    it alone, flagged as client-only, instead of failing the whole verb.

- Gap: **`get-state` cannot see into a `Resource`-typed property, which makes the most common
  "what is on the ground / in this slot" question unanswerable.** `get-state --node
  .../PickUps/PickUp --property slot_data` returns
  `slot_data: ():<Resource#-9223371682201202966>`, and the same for the sprite's
  `texture: ():<AtlasTexture#...>`. Every inventory slot, every pickup and every AtlasTexture
  in this project is behind that wall. Workaround: I added a project verb
  (`charged_state` -> `ground_drops`) that walks the pickups and reports `slot_data.item.name`
  — which works, but it is a bespoke verb per Resource-shaped question, and the generic
  primitive should not need one.
  - [gather:G-117] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: let `--property` take a dotted path (`--property slot_data.item.name`), or
    have `get-state` expand a Resource one level into its script variables instead of
    printing an object id. One level would have answered this without a new verb.

- Gap: **`step-time` caps at 60s, and the cap is not discoverable before you hit it.**
  `step-time --seconds 90` returned `Failed: step_time refuses 90.000s; the maximum is 60.0s`.
  The message is good; the problem is that spawning enough enemies to test against needs
  minutes of game time, so this is two or three calls where the caller thinks in one. Not
  a bug — the bus serves one command at a time and a long block would look like a hang.
  - [gather:G-118] status: open | seen: 2 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: mention the 60s ceiling in the `step-time` row of the CLAUDE.md
    cheat-sheet, so it is read before it is discovered.

- Gap: **there is no way to press a UI button from the harness, so panel wiring is verified one
  layer below what ships.** I added weather / bolt / strike-a-skull controls to
  `ui/debug_panel_ui.gd` and verified them by calling `_on_set_weather`, `_on_strike` and
  `_on_charge_skeleton` through `run-method` — which proves the actions, not the buttons.
  `scene-tree` truncates before the World tab's children, so I could not even enumerate the
  Buttons to confirm `_build_world_tab` had produced them, let alone emit `pressed`. Workaround:
  test the callables directly and accept that a mis-wired `pressed.connect` would ship green.
  - [gather:G-119] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: a `press --node PATH` verb that finds the nearest `BaseButton` and emits
    `pressed`, plus a `--filter`/`--depth` on `scene-tree` so a deep UI subtree can be listed
    without returning the whole scene.

- Gap: **`node-bounds` is Control-only, so there is no way to ask where a world sprite is on
  screen.** `node-bounds .../Sprite2D` returns `Failed: Node is not a Control`. Every visual
  check on a game object therefore needs the camera transform reconstructed by hand
  (`(world - player) * zoom + viewport/2`), which is three devtools calls and an assumption
  that the camera is centred on the player. Workaround: exactly that, scripted.
  - [gather:G-120] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: make `node-bounds` accept any `CanvasItem` and return its screen-space rect
    via `get_global_transform_with_canvas()`. That one change turns "is this thing drawn, and
    what colour is it" from a scripted reconstruction into two calls.

- Gap: **no way to sample the framebuffer, so colour claims need a PNG decoder.** Confirming
  "this sprite renders blue" meant writing a zlib/PNG reader inline to read pixels back. It
  worked and it is what caught the shader, but it is ~30 lines of scanline defiltering that
  every visual assertion would have to carry.
  - [gather:G-121] status: fixed | fixed-in: 0.9.0 | seen: 3 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: a `sample-pixels --rect X,Y,W,H` verb returning mean/brightest/dominant RGB
    from the same capture path `screenshot` already uses. Colour regressions become assertable
    instead of eyeballed.

- Gap: **[G-122] no way to dry-run a director clip without owning the single-instance bus** —
  both clip-authoring subagents ended their reports with the same six-to-eight item list of
  "things only a runtime run can confirm", because the bus is one command/result file pair and
  a recording was live on it. Their lint-and-tests pass proved the file compiled and nothing
  else; every framing, pacing and reach claim came back to the orchestrator unverified.
  - [gather:G-122] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: a headless `clip-preview --clip NAME --at 2,6,13,24` that runs the director
    offscreen and writes a handful of PNGs, with no bus and no `--write-movie` pass. Framing
    and reach are most of what a clip author cannot check, and neither needs a window, audio,
    or the ~10 minutes a real take costs.

- Gap: **[G-123] no verb kills an enemy through its real death path** — `clear-nodes --group
  Enemy` frees nodes with `queue_free()`, which skips `HealthManager.died`: nothing drops, no xp
  is paid, `RunStats` never counts it and the boss's own `died` connection never fires. Testing
  "the boss ends the run" was therefore impossible with the shipped verbs — a test built on
  `clear-nodes` proves the nodes are gone and nothing else. Worked around by writing a project
  verb, `kill_enemy --args '{"type":"Elite"}'`, which drives `take_damage`.
  - [gather:G-123] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: this is arguably project-specific (it needs to know about `HealthManager`), but
    the *shape* is not: `clear-nodes` is the only generic way to remove a node and it is always
    the wrong one when the node's removal has game meaning. A generic
    `clear-nodes --via-method <name>` — call this method instead of `queue_free()` — would have
    covered it with no game knowledge at all.

- Gap: **[G-124] `get-state` on a String property is unreadable through the CLI** — reading a
  Button's `text` back to confirm the two-step NEW RUN confirm returned
  `Binary file (standard input) matches` from grep, because the reply carries embedded nulls.
  `tr -d '\000'` worked around it. Small, but it turns a one-line assertion into a pipeline.
  - [gather:G-124] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: strip or escape non-printable bytes in the CLI's text output path.

- Gap: **a dry run does not reproduce the recording's timestep, and the skill's "three clean dry
  runs" rule is not sufficient because of it.** `capture_clip.py` passes `--fixed-fps 30`; a dry
  run launched per the skill (`godot --path . --mute`) runs uncapped. The player therefore moves
  in sub-pixel steps in a dry run and 1.7px steps in the recording, and a chase settles at a
  different distance in each. Three clean dry runs of the `raid` clip were followed by a recording
  whose fight never finished — ten minutes of wall time to learn something a one-minute run could
  have told me.
  - [gather:G-125] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: launch dry runs with `--fixed-fps 30` (the workaround used here, and it
    reproduced the failure on the first run). Worth putting in the skill directly, and worth
    `capture_clip.py` growing a `--dry-run` that launches with the recording's flags minus
    `--write-movie` so the two can never drift apart.

- Gap: **the notes array is a pass/fail flag with no denominator, and a stochastic clip needs
  one.** The raid clip fails roughly one run in eight, because `RaidDirector._pick_spawn_cell` is
  random and a raider can land somewhere it cannot path out of. Deciding whether that rate was
  acceptable meant hand-rolling a bash loop and eyeballing eight lines; `demo_state` has no notion
  of "run this clip N times and report the failure rate", and `verify-runs.jsonl` records gate
  runs rather than clip takes.
  - [gather:G-126] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: a `demo_clip --args '{"name":"raid","repeat":8}'` that re-runs in-process and
    returns per-run notes, or a `capture_clip.py --dry-run --repeat N` that prints the rate. Either
    turns "is this clip stable" from a judgement call into a number.

- Gap: **no way to ask the running game for a difficulty curve as data.** Calibrating the raid
  ramp meant hand-evaluating `size_for_day` / `health_mult_for_day` / `cost_for_parcel` /
  `xp_for_level` across 20+ days in prose arithmetic, and the same tables get recomputed by hand
  every time anyone touches these constants. `raid_state` reports `tonight_size` and
  `tonight_health_mult` for exactly one day, and `land_state` reports one parcel price; there is
  no verb that sweeps a pure static across a range. Two arithmetic slips in this session were
  caught only by a second pass.
  - [gather:G-127] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: a generic `curve --node PATH --method NAME --from N --to M` verb that calls a
    static/pure method over an integer range and returns the series, so "what does this ramp
    actually look like" is one call rather than a transcription. It generalises past raids —
    `cost_for_parcel`, `xp_for_level`, `cap_for_land_tiles` and `reward_for_size` all have the
    same shape, and all four are things a design change has to re-read.

- Gap: **the test suite pins tuning constants in bands, but nothing reports which band a proposed
  constant lands in without running the suite.** `test_island_manager.gd:297` gates the boss chest
  against `cost_for_parcel(MAX_PARCELS-1) * [0.15, 0.6)`, and `test_raid_director.gd:160` gates the
  days-3..22 reward sum to `(500, 5000)`. Both are the right shape of test, and both are invisible
  until a full `run_tests.gd` — so exploring "what if MAX_PARCELS were 16" is a 40-second round
  trip per candidate rather than a read.
  - [gather:G-128] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: `run_tests.gd --json` already carries pass/fail; if band assertions also emitted
    the computed value and the bounds, a failing tuning sweep would say "894 not in [372, 1489)"
    instead of a message. That is a convention for `_T.assert_*` (an `assert_between`) more than a
    harness feature, but the harness is where it would have to live to be uniform.

- Gap: **nothing can answer "what can the player reach right now" in one read, which is exactly
  the question this bug was.** The board had a key, a desktop button, a passing suite and no way
  in on a phone; finding that took forcing the feature flag, reading `visible` on two nodes,
  looking up a button path in `scene-tree`, computing a rect centre, and sending two `touch`
  events. Every one of those is available and none of them is the question. `validate-ui` reports
  0 issues for a UI whose only affordance is invisible on the current device, because an
  unreachable panel is not a layout fault.
  - [gather:G-129] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-04
  - Improvement: a generic `reachable-ui` verb that walks visible `Control`s with a `pressed`
    signal or a registered input action and reports `{action, node, rect, on_screen}` — the set a
    finger or cursor could actually hit this frame. Diffing that between `--touchscreen true` and
    `false` would have named this bug outright ("`quests` reachable on desktop, not on touch")
    instead of it shipping. It generalises past touch: the same read catches a button laid out
    off-screen, one under a full-rect `MOUSE_FILTER_STOP` sibling, and one whose whole strip is
    hidden behind a device check.
  - Note (0.9.0): shipped as `reachable_ui` / `reachable-ui`, close to the filed shape. It
    reports `{path, type, text, rect, on_screen, blocked_by, kind}` per Control rather than
    `action` — a registered input action is not reachable *through a Control*, and pretending
    the two are one list would have made the count mean nothing. Occlusion is real: a later
    `MOUSE_FILTER_STOP` sibling covering a control's centre is named in `blocked_by`.
    Unreachable controls are listed with the reason, not omitted, so the touch-vs-desktop
    diff the entry asks for is a diff of two full lists. Contract row asserts the fixture's
    one Button comes back `count: 1, reachable: 1`.

- Gap: **no verb answers "what would this cost the player", so balance questions have no
  - [gather:auto-2ac87a] status: open | seen: 1 | source: gather 2026-08-05
  runtime primitive at all.** Every tuning read is indirect: `gather_stats` reports a gather
  in progress, `player_state` reports current totals, `run_summary` reports one finished run.
  Asking "how long to the first furnace at each pickaxe tier" means reading five files and
  recomputing by hand what the game already knows. Noting it rather than filing it as a
  harness gap, since it is project-shaped (a `devtools_ext` verb) rather than generic.
  - Improvement: a project verb `balance_table` returning the derived economy — per-resource
    xp/sec and yield/sec at each pickaxe tier, recipe cost in gather-seconds, and the level
    curve in nodes-per-level — so a balance claim is one read instead of a spreadsheet, and
    so a tuning edit can be asserted against in a unit test rather than eyeballed.

- Gap: **a committed generated artifact has no link to its generator, so nothing reports it
  stale.** This turn committed `tools/balance_model.gd` alongside `.devtools/balance_model.json`,
  which is that script's output captured against some earlier tree. Lint checks scripts, the
  test runner checks tests, and `verify-runs.jsonl` records reach against the diff — none of them
  knows that a JSON file in `.devtools/` is downstream of a `.gd` file and may now disagree with
  it. Verified by hand instead: re-ran the generator and diffed
  (`resources=8 recipes=30 skills=17 quests=14 findings=19`, output byte-identical), which is a
  step that is easy to skip and silent when skipped — a stale committed model would read as
  authoritative balance data.
  - [gather:G-130] status: open | seen: 2 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: let `devtools_config.json` declare `generated_artifacts: [{script, output}]` and
    have `lint_project.gd` re-run each generator into a temp path and compare, failing when the
    committed output differs. It generalises past this one file — the same shape covers any
    checked-in fixture with a builder, including `test/fixtures/demo_homestead_save` and the
    `build_demo_world` verb that produces it, which has already been baked wrong twice
    (`gather-3m9`).

- Gap: **`run-method` replies in plain text, not the JSON envelope every other verb uses.**
  `python tools/devtools.py run-method --node ... --method add_random_resource --args "[]"`
  prints `Result: True`, so piping it to a JSON parser — which works for every `cmd` verb —
  dies with `JSONDecodeError: Expecting value: line 1 column 1`. Five parallel calls produced
  five stack traces and no data before I noticed the verb itself had worked fine.
  - [gather:G-131] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: give `run-method` the same `{action, success, message, data}` envelope as the
    registered verbs, with the return value under `data.result`, or accept `--json` on it. A
    client that has to special-case one verb's output format cannot be scripted uniformly.

- Gap: **no headless way to assert that a collision layer/mask pair actually blocks a body** — the
  suite can read `collision_layer`, `collision_mask` and `TileData.get_collision_polygons_count()`
  and infer the intersection by hand (which is exactly what `_layers_matching(ENEMY_MASK)` in
  `test/unit/test_collision_layers.gd` does), but "would this body be stopped by this tile" is a
  physics-server question and there is no primitive for it. The workaround is a test that restates
  the bitmask arithmetic, which passes if the arithmetic and the data agree *and I got the
  arithmetic right*.
  - [gather:G-132] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: a `collides(--node PATH --against PATH)` style helper, or headlessly a
    `PhysicsServer2D`-backed assertion in the runner — `_T.assert_blocked(body_scene, tile_source,
    coords)` that builds a one-tile TileMapLayer in the hosted SubViewport, does a
    `move_and_collide` toward the cell and reports whether it was stopped. That turns the whole
    Structure/Prop contract from arithmetic into an observation.

- Gap: **`--import` reports exit 0 while printing another agent's parse error**, so an import that
  "succeeded" left `res://items/items.gd` unloadable. Output was `import exit=0` alongside
  `SCRIPT ERROR: Parse Error: Cannot infer the type of "walk" variable ... at: GDScript::reload
  (res://player/player.gd:446)` and `ERROR: Failed to load script "res://items/items.gd" with error
  "Parse error"`. In a single-agent session that is a broken game reported as a clean import; here
  it was a concurrent edit mid-save, and only re-reading the log distinguished the two.
  - [gather:G-133] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: have the harness wrap `--import` the way it wraps lint — a `tools/import.py`
    (or a lint phase flag) that greps the import log for `SCRIPT ERROR` / `Failed to load script`
    and exits 1, so "the class cache was regenerated" and "the project still parses" stop being the
    same exit code.

- Gap: **no headless way to assert that a state's active window survives an external
  `AnimationPlayer.stop()`** — the entire bug being fixed is "something outside the state machine
  ended a swing early", and the closest a headless test can get is asserting the *predicate*
  (`Player.release_may_stop_animation(state)` is false mid-swing) rather than the *behaviour* (the
  animation is still playing and `$Attack.monitoring` is still true after the stop). I had to
  factor four static predicates out of `player.gd` purely so the rules were reachable at all,
  because `Player` is scene-backed and `_T.instantiate_ui` cannot stand one up — its `@onready`
  fields resolve `../../Systems` and `../../UI`.
  - [gather:G-134] status: open | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: a runner helper that hosts an arbitrary *scene fragment* with stubbed external
    node paths — `_T.instantiate_fragment("res://main.tscn", "World/Player", {"../../Systems": ...})`
    — so a scene-backed node with upward `@onready` paths can be brought up headlessly. Today the
    only way to test one is to extract statics from it, which is a real design tax: `player.gd` now
    carries four static predicates that exist for the test harness rather than for the game.

- Gap: **`run_tests.gd` prints no `Selected: N of M` line on an unfiltered run**, so a full-suite
  run cannot be distinguished from one whose discovery silently found fewer files than it should.
  `--file test_player_combat` reports `Selected: 38 of 662 discovered`; the bare run reports only
  `Total: 662 | Passed: 660 | Failed: 2`. The two numbers are the same fact, but only the filtered
  form states the denominator explicitly, which is the form worth quoting in a handoff.
  - [gather:G-135] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: always emit the `Selected:` line, with `(no selector)` where the filter would go.

- Gap: **no generic verb for a space-state query, so "what would collide along this segment"
  had to be added as a project verb before the contract could be checked at all.** The harness
  has `tilemap-cells` and `tilemap-region`, which answer what tiles are *where*, and
  `node-bounds`, which answers where a node is — but nothing answers what a given
  `collision_mask` would actually hit, which is the only form the question takes once a project
  has more than one physics layer. I wrote `los_probe` (segment + mask → clear/blocked + hit
  path + hit position) and it immediately did work I had not planned for: reading `hit_position`
  back from two opposite-facing probes located a wall's faces at x=-256 and x=-240 exactly,
  after five short bisecting probes had all returned `clear` because a Godot ray originating
  inside a shape reports nothing.
  - [gather:G-136] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: a generic `raycast --from X,Y --to X,Y [--mask N] [--areas]` verb, reporting
    `clear`, the collider's node path, and the hit position. It is ~20 lines against
    `direct_space_state`, needs nothing project-specific, and is the direct read for any
    question about layers, masks, walls, sight lines or reachability. Pair it with a
    `--mask-names` flag that resolves `[layer_names]` from `project.godot`, since the whole
    class of bug here is a number nobody can read.

- Gap: **`set-state --value` takes no Vector2 in the form the error message asks for.**
  `--value "-200,-296"` fails argparse (reads as two args), `--value "(-200,-296)"` fails with
  `cannot convert String ("(-200,-296)") to Vector2`, and `--value '[-200,-296]'` works. The
  error names the type it wanted and not the syntax that produces it, so the working form is
  found by guessing. This is the same coercion hole as `run-method`'s documented `gather-6sp`,
  one layer up.
  - [gather:G-137] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: accept `x,y` and `(x,y)` as well as the JSON array, and on failure print the
    accepted forms rather than only the rejected type.

- Gap: **`harness-version` reports `game not running`**, so the `harness:` field on every gap
  above is copied from the previous entry rather than read. The client half of the version is
  knowable with no game at all — it is the installed revision on disk.
  - [gather:G-138] status: fixed | fixed-in: 0.9.0 | seen: 1 | harness: 0.8.0 | source: gather 2026-08-05
  - Improvement: print the client revision unconditionally and mark the game half `unknown`
    when the bridge is cold, instead of failing the whole verb.

- Gap: **[G-130] status: open | seen: 2 | harness: 0.8.0** — same gap as last session, hit again
  - [gather:auto-9785ce] status: open | seen: 1 | source: gather 2026-08-05
  and in exactly the predicted way. Measuring a regrowth rate still takes launch -> census ->
  `world_clock` -> `set-game-speed` -> sleep -> `set-game-speed` -> `world_clock` -> census, seven
  calls, of which two exist purely to establish how much game time passed. The improvement filed
  last time (put `game_time` in the status provider merged into every reply) would have cut this
  to three calls and removed the only step that can silently invalidate the result.

## 2026-08-05 — 0.9.0: 57 gaps pooled from gather, 31 of them closed

`tools/upstream_gaps.py ../gather/log-devtools.md` pooled 57 open gaps (the section
above), and three commits then closed a batch of them: `1b0b308` (bus honesty, reads, five
new generic verbs, the four sibling tools), `dc842e0` (`curve`, `new-uid`, screenshot
cropping/hiding, `scene-tree --root/--property`) and the release commit, which also
carried `reachable_ui` — filed below as closing gather:G-129, and landed after the
closure pass below had already been written, which is why it is not in the list.
Closed as `fixed-in: 0.9.0`:
gather:G-076, G-079, G-090, G-091, G-092, G-094, G-095, G-096, G-102, G-103, G-105,
G-108, G-109, G-110, G-111, G-112, G-116, G-117, G-119, G-120, G-121, G-123, G-124,
G-127, G-131, G-133, G-135, G-136, G-137, G-138. Four named in those commit messages
stay **open** because only part of what they describe shipped, each with a note under it
saying which part: gather:G-077 (the `set-state` coercion landed; no `place_tile` verb
did), gather:G-098 (the string-literal scan landed but ClassDB owns `open`/`close`/
`start`/`stop`/`play`/`clear`, so the gap's own `has_method("open")` example is still not
flagged), gather:G-100 (the owner record is now read before the write and reported as
confirmed-gone or still-alive; a *live* foreign instance is still only caught from
`reply.pid` after it answers), gather:G-115 (the bus
is genuinely isolated and verified now, but `user://` — saves, screenshots, UI baselines
— is still shared, which is the sharing that entry is about).

Validation actually run: `python tools/check_templates.py --full` against Godot 4.6.1,
all stages passing, **72/72 verb-contract rows** on the final tree (71 before
`reachable_ui` landed). The contract table grew two things this
release that matter more than the row count: rows now assert the `data` KEYS the client
reads by name, and a row that claims an effect carries an expected `{key: value}` map, so
the `press` row asserts that the connected callable actually incremented a counter and
the `"x,y"` / `"(x,y)"` coercion rows assert the Vector2 that was stored, not merely that
the call returned success. The gate earned its keep once, on the second commit: a
GDScript parse error in `_cmd_raycast` from Python-style implicit string concatenation
across two lines —

```
templates/addons/godot_selftest/dev_tools.gd:3437 - Parse Error: Expected closing "}" after dictionary elements.
```

— which is the worst class of template defect there is, because a `dev_tools.gd` that
does not parse does not instantiate, and the autoload takes the entire bridge down with
it. Every verb in the user's game would have answered nothing.

Not validated: nothing was driven over a real bus by hand this session beyond what
`check_templates.py --full` drives itself, and the per-gap closure judgements below were
made by reading the code, not by re-running each gap's original failure. That is H-020.

- Gap: **nothing in this repo checks that a shipped fix closes the gap it names, so a
  wrong judgement is invisible.** Thirty status lines moved to `fixed-in: 0.9.0` this
  session on the strength of one agent reading `dev_tools.gd`, `devtools.py`,
  `lint_project.gd`, `run_tests.gd`, `verify_ledger.py` and `import_check.py` and
  deciding. Four of the thirty-four ids named in the commit messages turned out to be
  partial and were left open — which is the process working, but it is also the measure
  of the error rate: the same reading pass that caught four could have missed a fifth.
  `check_templates.py` proves a verb answers with the keys it promises; it has no notion
  of a gap id, so it cannot tell you that the verb answers the *question that was filed*.
  The commit messages cite gap ids, `record_version.py --check` verifies stamps and doc
  fan-out, and neither of them ever reads `log-devtools.md`.
  - [H-020] status: open | seen: 1 | harness: 0.9.0
  - Improvement: let a gap's status line carry the evidence that closed it — a
    `verified-by:` field naming a `check_templates.py` contract row, a lint rule id, or a
    named test — and have `record_version.py --check` fail when a line says
    `fixed-in: <this version>` with no such reference, the same way it already fails on a
    stale stamp. It would not prove the judgement right, but it would make an unevidenced
    close visible instead of indistinguishable from an evidenced one.

- Gap: **`check_templates.py` cannot be run by more than one agent at a time, so all
  validation serialises behind the orchestrator.** It assembles a scratch Godot project
  by copying `templates/` and then imports and launches it; two copies taken while a third
  agent is mid-edit read different trees, and a copy taken during a write reads a
  half-written file. This session ran four agents against this repo — three on
  `README.md`, `templates/CLAUDE.harness.md` and `commands/verify.md`, one on this log —
  and the only safe policy was that none of them touch the gate, exactly as
  `gather:G-093` describes for a game project. Honest limit on this entry: no concurrent
  run was attempted, so the corruption is predicted from the mechanism rather than quoted
  from a failure — but the policy that avoided it cost real serialisation, and that part
  is observed.
  - [H-021] status: open | seen: 1 | harness: 0.9.0
  - Improvement: have `check_templates.py` snapshot `templates/` into its scratch dir from
    `git stash create` / `git archive HEAD` rather than from the working tree — a commit is
    immutable and a concurrent edit cannot tear it — and give the scratch dir and its
    `.godot/` a per-invocation name so two runs cannot share an import cache. A `--worktree`
    flag for the deliberate case of validating uncommitted edits keeps today's behaviour
    available where it is wanted.

---

## 2026-08-05 — The ledger could not see what the harness caught (0.10.0)

A question about whether the harness is improving turned into a measurement of it. The
answer came from `verify_ledger.py stats` against `../gather`, which is the good news:
the instrument existed and had 52 real rows in it. The bad news is what it said.

- Gap: **the ledger records a run's end state, so a defect found and fixed mid-run leaves
  no trace anywhere.** Every field describes how the run came out. A bug surfaced at
  minute four and repaired by minute six ends with `checks` written green, lint and tests
  re-run clean, and a row byte-identical in shape to one where nothing was ever wrong.
  Across 52 runs the ledger therefore recorded **319 Phase 4 checks with not one `fail`**,
  zero new lint findings, zero failing tests — while 98% of those same runs graded
  themselves `warranted`, on the strength of catches that survived only as prose in
  `cheaper_alternative`.

  Evidence, before the fix:
  ```
  $ python tools/verify_ledger.py stats
  runs: 52  (partial 8 | pass 44)
  runs where a runtime check caught something: 0 (0%)
  runs where only lint/tests caught something: 0
  was it worth running?
    warranted      51  (98%)
    insufficient    1  (2%)

  Not one run in 52 judged itself overkill. That is possible, and it is also what a log
  that flatters the tool looks like - check the entries before believing the number.
  ```
  The tool was already saying the number was untrustworthy. It could not say why, because
  the field that would have explained it did not exist — the strongest single piece of
  evidence in the whole ledger ("both defects found were invisible to lint and to all 464
  unit tests") is a sentence in a free-text field, uncountable and unqueryable.
  - [H-022] status: fixed | fixed-in: 0.10.0 | seen: 1 | harness: 0.9.0
  - Improvement (done): a `found` field — a list of `{what, phase,
    static_would_have_caught}`, with `[]` meaning *this run caught nothing* and `null`
    meaning unrecorded, kept rigorously distinct. `stats` gained a "did it tell you
    anything?" block reporting the hit rate, the phase split, and the share of findings
    that lint and tests would **not** have caught, which is the number that justifies
    launching a game at all. Rows predating the field are excluded from the rate rather
    than scored as zeros.

- Gap: **`value` had one mechanical gate where it needed two, so `warranted` was
  effectively unfalsifiable.** `_reconcile_value()` downgraded a `warranted` whose changed
  files were never loaded, which catches the run that saw nothing — but nothing at all
  contradicted a run that saw the code, found it fine, and called that warranted anyway.
  With 51 of 52 rows self-graded `warranted` and zero `overkill`, the enum had stopped
  discriminating; a field that only ever takes one value is not a measurement.
  - [H-023] status: fixed | fixed-in: 0.10.0 | seen: 1 | harness: 0.9.0
  - Improvement (done): a second downgrade, `warranted` + `found: []` → `overkill`, with
    the original preserved in `value_reported` like the existing one. Deliberately keyed
    on `[]` and never on `null`: coercing an unrecorded field would be scoring a silence,
    and every pre-0.10.0 row is silent forever. `stats` also now calls out a pile of
    `warranted` rows carrying no `found` at all — the shape where the claim is present and
    its evidence is not.

- Gap: **`/verify` told Phase 4 to report checks, and never said *when*, so they were all
  written at the end.** Nothing in the workflow was wrong, exactly; a check recorded after
  the fix is a truthful description of the final state. It is also the mechanism behind
  319 checks and zero fails, and it means the runs that did the most work look identical
  to the ones that did the least.
  - [H-024] status: fixed | fixed-in: 0.10.0 | seen: 1 | harness: 0.9.0
  - Improvement (done): Phase 4 now specifies a check records its **first** observation,
    with `fixed_in_run: true` on one that was repaired during the run — making
    `verdict: pass` with failed checks a normal and informative row rather than an
    unrepresentable one. This is a discipline, not a gate: no script can tell a check
    written before a fix from one written after.

- Gap: **`CLAUDE.md` described a validation gate that had already been built.** The
  "Validating a template change" section said nothing checks the templates before they
  ship and gave a four-step manual procedure, citing `tools/check_templates.sh` as the gap
  that would close it. `tools/check_templates.py` exists, does all four steps plus a real
  bridge round-trip, and passes. A doc describing the repo's own verification discipline
  had drifted from the repo — the exact failure the harness exists to prevent, one level
  up.
  - [H-025] status: fixed | fixed-in: 0.10.0 | seen: 1 | harness: 0.9.0
  - Improvement (done): the section now names the command, lists its five stages, and says
    to run it before committing anything under `templates/`.

- Gap (open): **the honest reach number is going down and nothing flags a regression.**
  `stats` breaks reach out per harness version — `0.7.0 60% (220/366)` → `0.8.0 55%
  (193/354)` — which is exactly the comparison it was built for, but reading it requires
  someone to run `stats` and notice. Two versions is not yet a trend, and there is no
  point at which the tool says so itself.
  - [H-026] status: open | seen: 1 | harness: 0.10.0
  - Improvement: have `stats` compare the newest harness version's reach against the
    previous one and print a regression line when it drops by more than a few points with
    a comparable denominator. The data is already in the aggregate; only the sentence is
    missing.

- Gap (open): **nothing counts which of the 48 verbs is ever used.** `dev_tools.gd` is
  4001 lines and `devtools.py` 2951, and every release adds verbs pooled from one
  project's gaps. There is no signal distinguishing a verb that three projects call every
  run from one that has never been invoked outside its own documentation, which means "we
  added a verb" cannot currently be told apart from "we improved the harness". Related to
  the single-source problem below.
  - [H-027] status: fixed | fixed-in: 0.46.0 (devtools.py verb-usage) | seen: 1 | harness: 0.10.0
  - Improvement: have the bridge append `{verb, ts}` to a rotating counter file per
    session, and a `verb-usage` subcommand that reports never-invoked verbs. Cheap, and it
    turns the growth of the surface area into something with a denominator.

- Gap (open): **84% of gaps come from one game, and the log cannot show that.** Of 157
  entries, 132 are `gather:G-*` and 21 are `H-*`. "The core is game-agnostic" is a design
  commitment currently validated against a single project, and a second scaffolded project
  would be worth more directional information than the next ten verbs. Nothing in the log
  or in `upstream_gaps.py` surfaces the concentration.
  - [H-028] status: fixed | fixed-in: 0.41.0 (upstream_gaps.py prints open gaps by source every run) | seen: 1 | harness: 0.10.0
  - Improvement: have `upstream_gaps.py` print the per-project split after a pool, so a
    release notices when it is being shaped entirely by one game's needs.

**Validation run this turn:** `tools/check_templates.py` — all five stages, including the
real-bus bridge round-trip against Godot 4.7.1 (`stage 5 bridge: ping answered (pong)`).
`record_version.py --check` OK at 0.10.0. `verify_ledger.py` was additionally exercised
end-to-end in a scratch git repo: both downgrade paths fired through the real `record`
path, and `stats` was checked against a mixed-vintage fixture ledger holding `found`
absent / `[]` / populated rows, confirming pre-0.10.0 rows are excluded from the rate
rather than counted as zeros. One bug was found and fixed that way — a `found` list whose
entries were all malformed collapsed to `[]` and would have silently triggered the new
`overkill` downgrade, turning a formatting slip into a rewritten verdict; it records
`null` now.

## 2026-08-14 — A gate that never opens the project (0.11.0)

Four agents in a parallel session on a game project independently asked for the same
thing: a static GDScript/ClassDB name checker that runs without launching the engine,
because the shared `.godot/` cache made the harness unusable while they worked at the
same time. That is `gather:G-093` arriving from four directions at once, and it is worth
noting that none of them asked for a faster linter — they asked for a *different kind* of
gate, one whose cost is not a lock.

Shipped `templates/tools/name_check.py`. It resolves the names a project mentions from
three inputs, none of which is `.godot/`: the `.gd` files themselves (`class_name`, inner
classes, `const`, `enum`, `func`, `signal`, and the `extends` graph that puts a base
class's members in scope), `project.godot`'s `[autoload]` section, and an engine API index
distilled from `godot --dump-extension-api`.

The third input is the whole trick. `--dump-extension-api` runs in an empty temp directory
with **no project at all** — verified: it opens no `.godot/`, takes no lock, and
`check_templates.py` stage 2.5 now asserts that a `--refresh-api` leaves no `.godot/`
behind. 6.7 MB of dump reduces to ~134 KB gzipped, cached per engine version under the
user's cache dir where every clone and worktree on the machine shares it. After one ~6s
dump, the checker launches nothing at all.

Nine rules, split by what is actually decidable: `unknown_type`, `duplicate_class_name`,
`class_name_shadows_engine`, `missing_preload` and `missing_extends_path` are errors;
`missing_load`, `unknown_member`, `unknown_global_ref` and `class_cache_stale` are
warnings; `string_ref_unresolved` is advisory, matching `lint_project.gd`'s rule of the
same name. Wired into `/verify` as the Phase 1 name gate ahead of the import gate, into
the scaffolder's install set and step 12, and into `check_templates.py` as stage 2.5.

- Value: **warranted**. The checker found a real defect in itself twice during
  development, both times only because it was run against a real 174-script project rather
  than the synthetic scratch one — see the two gaps below.

- Gap: **the scratch project in `check_templates.py` is too small and too synthetic to
  expose a false-positive rate.** Every stage passed on it while `name_check.py` was
  emitting **466** bogus warnings on the first real project it saw. The cause was one
  regex: `const X := 0.3` (the inferred-const form) never matched, so every inferred const
  in the project was invisible and every reference to one became a confident
  `"Juice" has no member "NODE_BREAK_TIME"`. The scratch project happened to contain no
  inferred consts. A gate that only ever runs against a fixture it also authors cannot
  measure noise, and noise is the failure mode that gets a static checker switched off.
  - [H-030] status: open | seen: 1 | harness: 0.11.0
  - Improvement: let `check_templates.py` take an optional `--against <project>` that runs
    the static stages over a real scaffolded project and reports the finding count as a
    number to eyeball, without gating on it. One flag, and the 466 would have been visible
    before the commit rather than after.

- Gap: **a second literal-anchored check can silently match nothing and still report a
  clean run.** `name_check.py` blanks string bodies so no regex can fire inside a literal;
  the first version blanked the quote *delimiters* too, which is what `preload("…")` and
  `has_method("…")` anchor on. Both checks matched zero times across every file and the
  tool printed a clean verdict — the exact `Total: 0 | ALL TESTS PASSED` shape this repo
  already warns about, in a new place. It was caught only because the test project had
  planted defects those two rules were supposed to find.
  - [H-031] status: open | seen: 1 | harness: 0.11.0
  - Improvement: a rule can report "I matched nothing anywhere in the project" as a
    diagnostic under a `--self-check` flag. A rule with zero *matches* (not zero findings)
    across 174 files is either dead code or a broken anchor, and neither should look like
    a pass.

- Gap: **`run_tests.gd` still prints `[PASS]` when there is no class cache.** This is the
  half of `gather:G-083` that `name_check.py` does not touch: the name gate now tells a
  worktree its names are fine, but if someone runs the test runner there anyway, a test
  whose first statement errored still reports as passing. The runner has the evidence —
  `.godot/global_script_class_cache.cfg` is absent — and says nothing about it.
  - [H-029] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.11.0
  - Improvement: have `run_tests.gd` refuse to report a pass when the class cache file is
    missing, exiting `2` with one line naming `--import` as the fix. `lint_project.gd`
    already reports `class_cache_missing` as an advisory; the runner should treat the same
    condition as disqualifying, because unlike lint it makes a positive claim.

- Gap: **the index cache falls back to a different engine version without saying so.**
  `find_cached_index` prefers an exact match on the resolved binary's version, but when
  several indexes are cached and the binary cannot be resolved, it takes the newest by
  mtime and reports that engine version in its header without flagging the substitution.
  A 4.5 project checked against a 4.6 index would resolve a class that does not exist in
  its engine. Deliberate for now — detecting the mismatch means running `godot --version`,
  and not launching anything is the point — but the silence is the wrong half to keep.
  - [H-032] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.11.0
  - Improvement: record the project's engine version in `devtools_config.json` at scaffold
    time (it is already probing the binary in step 11) and have `name_check.py` compare the
    index's engine against that string, warning on a mismatch. No launch, no guess.

**Validation run this turn:** `python tools/check_templates.py` — all stages including the
new 2.5, against Godot 4.6.1 (`stage 5 bridge: ping answered (pong)`, `check_templates:
OK`). `python tools/record_version.py --check` OK at 0.11.0. `name_check.py` was
additionally exercised against the real `gather` project (174 scripts, 91 global classes,
6 autoloads): **0 errors, 0 warnings, 2 advisories**, both of them correct calls on
`commands.example.gd`. Its exit-code contract, `--json`, `--strict`, `--only`,
`--no-strings`, `--require-api`, and both baseline paths were checked against a fixture
project holding planted defects of every rule, plus a mutation test that removed a
`class_name` declaration and confirmed the resulting `unknown_type` error.

## 2026-08-14 - Upstreamed 6 open gap(s) from findmyballs (harness 0.10.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\findmyballs\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **the Godot binary probe ignores the project's declared engine version** — step 11's
  glob `"$HOME"/Documents/Godot_v*_win64.exe` takes the first match. This machine has
  `Godot_v4.5.1-stable_win64.exe`, `Godot_v4.6.1-stable_win64.exe` and
  `Godot_v4.7.1-stable_win64.exe` side by side, so it picked **4.5.1** for a project whose
  `config/features` is `PackedStringArray("4.7", "Forward Plus")`. Recording that in
  `godot_bin` would have pinned every later `/verify` to an engine two minor versions
  behind the project. Workaround: overrode the glob by hand and passed
  `--set godot_bin=.../Godot_v4.7.1-stable_win64.exe`.
  - [findmyballs:G-001] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.10.0 | source: findmyballs 2026-08-14
  - Improvement: when the glob returns more than one candidate, parse the version out of
    `config/features` and prefer the binary whose filename carries the same `X.Y`; fall
    back to the highest version, not the first glob hit. Reverse-sorting the glob alone
    would have been enough here.

- Gap: **`--script` mode never adds autoloads to the scene tree, so `_ready()` never runs** —
  both headless runners start this way. `BallCatalog.all_balls()` returned `0` under
  `run_tests.gd` while the same call returned `75` in the live game. A probe test confirmed it:
  `PROBE before=0 after_manual=75 purse_before=0 purse_after=10`, and
  `BallCatalog.get_tree()` raised `Parameter "data.tree" is null`. Nothing in lint or the test
  summary hints at it — `Scripts: 33 compiled OK`, `UIDs: OK`, `lint: 0 error(s)`. Workaround:
  made the catalog build lazily via `_ensure_built()` on all 13 public accessors.
  - [findmyballs:G-002] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.10.0 | source: findmyballs 2026-08-14
  - Improvement: have `run_tests.gd` and `lint_project.gd` warn when a configured autoload is
    not `is_inside_tree()` — one line, and it turns an invisible empty-data failure into a
    named one. A stronger fix is for the runners to add autoloads to the tree before
    discovery so `_ready()` fires as it does in the real game.

- Gap: **pausing the tree kills the DevTools bridge** — `run-method /root/SceneFlow
  toggle_pause` succeeded, and every subsequent call returned `that process is STILL ALIVE, so
  it is running but not polling THIS directory`, which reads as a wrong-`--userdata` problem
  rather than as a paused game. The DevTools autoload polls from a pausable process callback,
  so no pause-menu, settings-screen or death-screen state is reachable over the bus — exactly
  the UI most likely to need verifying. Workaround: `set-state --node /root/DevTools --property
  process_mode --value 3` *before* pausing, after which `screenshot` and `scene-tree` worked
  normally and confirmed the pause menu.
  - [findmyballs:G-003] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.10.0 | source: findmyballs 2026-08-14
  - Improvement: set `process_mode = Node.PROCESS_MODE_ALWAYS` on the DevTools autoload in
    `dev_tools.gd`. It has no gameplay side effects and makes every paused UI testable.

- Gap: **`devtools_owner.json` trusts a bare pid, and Windows recycles pids** — `launch`
  refused to start with `Error: pid 14856 still owns this bus`, and `ping` reported that pid as
  `STILL ALIVE`. It was `MoNotificationUx`, an unrelated Windows process that had inherited the
  dead Godot's pid. Workaround: `launch --isolated` to sidestep the stale claim entirely.
  - [findmyballs:G-004] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.10.0 | source: findmyballs 2026-08-14
  - Improvement: record the process *name* (and ideally start time) alongside the pid in
    `devtools_owner.json`, and treat a pid whose name no longer matches as dead rather than as
    a live owner.

- Gap: **the harness cannot be run at all while parallel agents share `.godot/`** — four
  subagents wrote ~2,300 lines across disjoint file sets with the engine explicitly forbidden
  to them, because concurrent `--import`/lint runs corrupt the shared class cache. Every one of
  them reported the same thing independently: the residual risk in their work was engine API
  names they could not check. Workaround: all four wrote blind and the orchestrator linted
  centrally afterwards; the first central run was clean, so the cost here was latency, not
  defects.
  - [findmyballs:G-005] status: fixed | fixed-in: 0.11.0 | seen: 1 | harness: 0.10.0 | source: findmyballs 2026-08-14
    (the log was written against 0.10.0; the second half of its own Improvement line —
    "a static GDScript/ClassDB name checker that needs no engine run" — shipped as
    `name_check.py` in 0.11.0, which is the same ask as `gather:G-093`. The `--import-cache`
    half is untouched and stays open as [H-034]: the engine gates still cannot be run
    concurrently, they can only now be *skipped* by a gate that never opens the project.)
  - Improvement: a `--import-cache DIR` passthrough on `lint_project.gd` / `run_tests.gd` (or a
    documented `GODOT_PROJECT_METADATA` override) so each agent can lint against a private
    cache. Failing that, a static GDScript/ClassDB name checker that needs no engine run.

- Gap: **`validate-ui` flags the transient cash-delta popup as `ui_transparent`** — tracked
  as `findmyballs-6mm`. `validate-ui` reports `[INFO] ui_transparent: Label 'Delta' is
  visible but fully transparent (alpha 0.00)`. Correct by design: it is the `+125` popup at
  rest between pops. Workaround: none applied yet (left open).
  - [findmyballs:G-006] status: fixed | fixed-in: 0.12.0 | seen: 1 | harness: 0.10.0 | source: findmyballs 2026-08-14
  - Improvement: either the HUD hides the label (`visible = false`) instead of driving
    `alpha = 0` at rest, or `validate-ui` gains a way to allowlist a node (by group or
    metadata) as "intentionally transparent at rest" via `save-ui-baseline`.

## 2026-08-14 — Every gate reports what it looked at (0.12.0)

First release shaped by a **second** project. `findmyballs` scaffolded the harness this
week and logged six gaps in one session; `[H-028]` had already named the problem those
six answer — 84% of gaps came from `gather`, so "the core is game-agnostic" was a design
commitment validated against a single sample. Two of the six independently reproduce a
`gather` gap, and those two are the highest-confidence items in the whole corpus.

Worth recording what the six did **not** contain: a request for a new verb. Not one.
`[H-027]` says nothing counts which of the 46 bus verbs is ever used, so a release that
adds surface area cannot be told apart from a release that improves anything. This one
adds no verbs. It adds denominators.

**The theme.** `findmyballs:G-002` reports three catalog tests that passed by iterating an
empty array — `test_ball_ids_are_unique`, `test_every_ball_is_populated`,
`test_lookup_by_id_round_trips`. An empty collection satisfies every assertion inside a
loop over it. That is the fourth sighting of one failure mode here: `[H-002]` (`Total: 0 |
ALL TESTS PASSED`), `gather:G-004` (a filter selecting 0 of N), `[H-031]` (a rule matching
nothing anywhere), and now a test method executing none of its own assertions. Each was
fixed locally and the invariant was never written down. It is now: **every gate reports
the denominator of what it actually looked at, and zero is disqualifying rather than
passing.**

**Closed in 0.12.0** — `findmyballs:G-001` `G-002` `G-003` `G-004` `G-006`, `gather:G-018`
(the same validate-ui gap `findmyballs:G-006` reports, from the other direction), and
`[H-032]`. `findmyballs:G-005` was closed by 0.11.0's `name_check.py` before this session
began — its log was written against 0.10.0 — and is marked as such, with the
`--import-cache` half it also names left open as `[H-034]`.

- Value: **warranted** — running the real thing overturned the diagnosis in the log, and
  planting defects caught two checks that were reporting clean while doing nothing.
  - Expected: that `--script` mode does not instantiate autoloads (what `findmyballs:G-002`
    concluded, and what `eval.gd`'s own header has asserted since it shipped), so the fix
    would be for the runners to instantiate and parent them.
  - Got: **the opposite.** A probe on 4.6.1 shows autoloads ARE instantiated and ARE
    parented to `root` when `_initialize()` begins — `get_parent()` is `root` — but the
    tree has not been stepped, so `is_inside_tree()` is `false` and `_ready()` has not run.
    One `await process_frame` and the same autoload reports `is_inside_tree() = true`,
    `_ready()` run, its array populated. The fix is one awaited frame, not a re-parenting
    pass.
  - Found: **(1)** the real mechanism above, which also makes `eval.gd`'s documented
    limitation wrong — it now awaits a frame and ships `get_autoload("Name")`, verified
    returning `3` for a catalog that reported `0` before. **(2)** The pre-fix failure was
    worse than uniformly-empty: the runner awaits inside `_run_single_test`, so the first
    test that happened to await a frame flushed the notifications, and every test after it
    saw populated autoloads while every test before it saw empty ones. Suite behaviour
    depended on test order. **(3)** `name_check.py`'s new engine-skew warning was **dead
    code on arrival** — `godot_version` in config is bare (`4.7.1`) while an index's
    `engine` is the binary's banner (`Godot Engine v4.7.1.stable.official`), and a regex
    anchored with `re.match` matched the first and never the second. It reported clean on a
    planted 4.0-vs-4.7 mismatch. Caught only by planting one.
  - Cheaper: nothing. The probe that overturned the diagnosis took one scratch project and
    ~40s, and no amount of reading either log would have produced it — both written
    sources said the same wrong thing.

- Gap: **a fix built to a gap report's stated cause would have shipped doing nothing.**
  `findmyballs:G-002`'s Improvement line asks the runners to "add autoloads to the tree
  before discovery so `_ready()` fires as it does in the real game". They are already in
  the tree. Following it literally means `root.add_child()` on a fresh instance, which
  Godot would auto-rename beside the real one, leaving the node GDScript resolves
  untouched — a change that passes review, passes lint, and fixes nothing. The log's
  `Improvement:` field is a hypothesis from a project that was working around the problem,
  not a diagnosis, and this repo has been treating it as the latter.
  - [H-033] status: open | seen: 1 | harness: 0.12.0
  - Improvement: require reproducing a pooled gap's *mechanism* before implementing its
    *Improvement*, and record the reproduction in the release entry. Three of the six gaps
    this session were reproduced first; this is the one where it changed the answer. Cheap
    version: add a `Reproduced:` line to the gap format, so an entry can distinguish "I saw
    this happen" from "I inferred this from a workaround that helped".

- Gap: **the engine gates still cannot run concurrently; 0.11.0 made them skippable, not
  parallel.** `name_check.py` answers the question four `findmyballs` agents were actually
  asking, and this is why `findmyballs:G-005` closes. But `--import`, `lint_project.gd` and
  `run_tests.gd` still share one `.godot/`, so N agents still serialise behind one owner
  for anything the static checker cannot decide.
  - [H-034] status: open | seen: 4 | harness: 0.12.0
    (inherits the count from `gather:G-005`/`G-093` and `findmyballs:G-005`; the
    `--import-cache` half has now been asked for by two projects and four agents)
  - Improvement: unchanged — a `--import-cache DIR` passthrough, or a documented
    `GODOT_PROJECT_METADATA` override. Failing that, a lockfile that makes concurrent
    runners **queue** instead of corrupting the class cache: you cannot parallelise a
    single-writer resource, but you can stop it silently producing garbage.

- Gap: **`check_templates.py` caught two dead checks this session, and both times only
  because a defect was planted.** The scratch project reports 0 UI findings, so the
  validate-ui baseline round-tripped NEW→PRE→pass over an empty set on the first attempt —
  a result an implementation that does nothing whatsoever also produces. Same for the skew
  warning. Planting is now permanent for both (`check_ui_baseline`, `check_engine_skew`,
  `stage_vacuous_control`), but nothing makes it the default posture for the *next* check.
  - [H-035] status: open | seen: 1 | harness: 0.12.0
    (third sighting of the family behind `[H-030]` and `[H-031]`)
  - Improvement: a rule for this repo, enforced by review rather than code — a new stage in
    `check_templates.py` must plant the defect it claims to detect, and the printed line
    must name what fired. A stage that can only report success is not a stage.

**Validation run this turn:** `python tools/check_templates.py --full` — all stages against
Godot 4.7.1, `72/72` contract rows, plus the three new positive controls
(`stage 4 tests: vacuous control fired (exit 1, [VACU] on the empty-loop test only)`,
`stage 5 bridge: validate_ui baseline 2 finding(s) ['small_tap_target', 'ui_transparent']
-> NEW, written, -> PRE, run passes`, `stage 5 bridge: paused tree still answers`).
`python tools/record_version.py --check` OK at 0.12.0 (11 shipped files, 46 bus verbs, 48
CLI commands). Per `CLAUDE.md`'s rule for static-analysis changes, `name_check.py` was also
run against the real `gather` project (174 scripts, 91 global classes, 6 autoloads):
**0 errors, 0 warnings, 2 advisories** — identical to the 0.11.0 baseline, so the skew
change added no false positives. The autoload mechanism was established by a standalone
probe on 4.6.1 before any code was written, and the pause fix is checked by pausing a real
tree over the bus rather than by reading `process_mode` back.

## 2026-08-14 — README split: front door vs. reference manual

- Value: **inconclusive** — a docs-only change. No Godot code moved, so `/verify` and
  `check_templates.py` had nothing new to reach; `record_version.py --check` is the only
  gate this touches and it is the one that ran.
  - Cheaper: nothing. The doc-coverage rule is enforced by a script, and the script
    names the file it enforces against, so the rename had to be made in both places.

`README.md` was 1195 lines: the complete verb-by-verb manual, every CLI flag, and 30
sharp edges — correct, and unreadable by anyone deciding in 60 seconds whether this tool
is for them. Split into `README.md` (~90 lines: one-sentence claim, a mermaid diagram of
the file bus, a worked example, a five-row capability table) and `REFERENCE.md` (the
old file verbatim, `git mv`'d so history follows it).

The first draft of the worked example was a list of `python tools/devtools.py …` lines,
which is what the *implementation* looks like and not what *using it* looks like — the
primary caller is an agent, and a reader who is deciding whether they want this needs to
see the loop (ask in English → it picks the verbs → it reads the output → it makes a
claim), not the argv. Replaced with a Claude Code transcript. Every line in it is the
real print format, checked against `cmd_reachable_ui`, `cmd_set_feature` and `cmd_launch`
in `templates/tools/devtools.py` and against the `%d of %d interactive control(s)…`
message in `dev_tools.gd`, because a fabricated transcript in the README is a promise the
tool then has to keep. Pointers updated in `CLAUDE.md`,
`PURPOSE.md`, `commands/scaffold-godot-harness.md`, and — the one that had teeth —
`DOC_RULES` in `tools/record_version.py`, which requires one doc to name all 46 bus verbs
and all 48 CLI commands.

- Gap: **nothing checks that a repo doc's internal links resolve.** `git mv README.md
  REFERENCE.md` silently invalidated five cross-references in four files; four were found
  by `grep -rn README`, and the fifth (`DOC_RULES`) only because it happened to be in the
  same grep output. A rename of a doc nothing greps for by name would have shipped broken.
  - [H-036] status: open | seen: 1 | harness: 0.12.0
  - Improvement: a `--check` stage in `record_version.py` that resolves every relative
    markdown link (`](foo.md)` and `` `foo.md` ``) in the repo's own docs against the
    filesystem and exits 1 on a miss. It is ~15 lines and it runs in the gate that
    already blocks a release.

- Gap (not new, restated with evidence): **the README could not be shortened without a
  human judging what a newcomer needs**, because nothing in the repo records who the
  document is for. Every rule in `CLAUDE.md`'s "Docs move together" section pushes doc
  content *in* — a verb must appear in the reference or the gate fails — and there was no
  counter-pressure keeping any surface short. The result is the predictable one: the only
  entry point grew to 1195 lines because growing it was always the compliant move.
  - [H-037] status: open | seen: 1 | harness: 0.12.0
  - Improvement: state the audience and a soft length ceiling at the top of each doc-role
    (done for `README.md` in `CLAUDE.md` this turn: "the front door and deliberately names
    almost none of them — don't grow it"). A ceiling nothing enforces is still better than
    no stated intent, because a reviewer can now point at it.

**Validation run this turn:** `python tools/record_version.py --check` — OK at 0.12.0,
`46 bus verb(s) + 48 CLI command(s) documented`, which is the receipt that the coverage
requirement followed the content to `REFERENCE.md` rather than being quietly dropped.
`check_templates.py` was **not** run: nothing under `templates/` changed this turn.
No version bump — no shipped file changed, so `harness_history.json` is untouched.

## 2026-08-14 — Review of the bus design: one stale fact, one latent hazard

No code changed this turn. A question about whether the file-bus implementation is
sound turned into a read of `_check_for_commands`, and the read produced two things.

- Value: **inconclusive** — a docs-and-log turn. `/verify` does not run in this repo and
  no template changed, so the only gate with anything to say was
  `record_version.py --check`.
  - Cheaper: nothing for the hazard — it is only visible by reading the dispatch path
    against the `_process` tick, which is exactly the reading that was done. The stale
    line count would have been caught by `wc -l`, and never was, because nothing runs it.

**Fixed this turn:** `CLAUDE.md`'s repo map called `dev_tools.gd` "~2k lines". It is
**4,172**. A 2x miss in the one table that tells a new session what to expect is worse
than no number, because it is believed. Replaced with the current figure, the version it
was measured at, and the durable half of the claim ("by far the largest file here — find
the `_cmd_<verb>` you need"). Every other factual claim in the map was checked at the same
time: the scaffolder's "13 idempotent steps" is exactly right (`grep -c '^## Step'` = 13).

- Gap: **a command that arrives while a handler is awaiting is dispatched on top of it,
  and both replies race on the one result file.** `_process` calls
  `_check_for_commands()` without `await` (`dev_tools.gd:230`), so an awaiting handler —
  `step_time`, `input_tap`, any project verb that yields — returns control to `_process`
  immediately. The next 100 ms tick re-enters `_check_for_commands`, and if a command
  file is there it is read, deleted and dispatched while the first handler is still live.
  Line 535 (`_current_request_id = request_id`) makes each reply carry the *right* id,
  which is what it was written for, but it does not stop the two handlers overlapping in
  the scene tree, and it does not stop reply A from being overwritten by reply B in
  `user://devtools_results.json` before its client ever reads it. Reachable with a single
  client: let a slow verb exceed the 30 s client timeout, then send another command.
  - [H-038] status: fixed | fixed-in: 0.16.0 | seen: 1 | harness: 0.12.0
  - **Reproduced before fixing, as `[H-033]` requires.** The probe was the one described
    here: `step_time 5s`, then a `ping` command file written by hand 1 s later. The ping's
    reply appeared at 1.26 s — inside the still-running `step_time` — and the `step_time`
    reply overwrote it at 5.12 s. The read of the dispatch path was right in every
    particular.
  - Improvement: a re-entrancy guard, not a queue. A `_busy` bool set around the
    dispatch, checked at the very top of `_check_for_commands` **before** the read and
    the delete, so a command arriving mid-await stays on disk and is picked up on the
    next tick after the current handler returns. That is ~3 lines and it prevents the
    overlap rather than relabeling it. Checking it after the read would be worse than
    nothing: the file is deleted on pickup, so an early return past that point silently
    eats the command.

**Considered and rejected this turn**, recorded so it is not re-proposed:

- **Making the bus concurrent.** The bus drives one process with one scene tree, and
  every verb reads or mutates it. `press` then `get-state` only means anything in that
  order, so serialization is the semantics, not a limitation. The real need — N agents at
  once — is already met twice, and neither time through the bus: `--session` gives each
  *instance* its own filenames, and `name_check.py` gives agents a full static gate that
  opens no project at all.
- **Dropping the 100 ms poll to per-frame.** Worth ~50 ms mean per verb by arithmetic,
  which is perhaps 5% of a `/verify` run that already spends seconds launching Godot. And
  the constant may be load-bearing: 60 `file_exists` syscalls/sec on Windows with an
  antivirus filter hooking file ops is not obviously free. Measure before touching it.
- **Splitting `dev_tools.gd`.** 4,172 lines in one file is the thing most likely to hurt
  later, and it is still not worth a refactor that has to preserve every `_handlers`
  registration exactly. The plugin's value is being trustworthy. Revisit with a concrete
  reason, not a line count.

**Validation run this turn:** `python tools/record_version.py --check` — OK at 0.12.0,
11 shipped files, 46 bus verbs + 48 CLI commands documented. `check_templates.py` was
**not** run: nothing under `templates/` changed. No version bump for the same reason.

## 2026-08-14 — Comparison against tea-leaves, and what it paid for (0.13.0)

Prompted by cloning [tea-leaves](https://github.com/cleak/tea-leaves), the project this
one was inspired by, and asking which does it better. Four improvements came out of the
comparison. One shipped, two were **reversed by evidence**, one was already rejected here
on better grounds. Recording all four, because a recommendation that dies to measurement
is worth as much as one that ships and is cheaper to re-propose than to re-refute.

**Shipped: the shader compile pass.** tea-leaves has `tools/lint_shaders.gd`; this repo
had no shader gate at all, and `REFERENCE.md` filed shaders under `not_applicable` for
reach. A broken shader is precisely the runtime-only failure `PURPOSE.md` names as the
reason this project exists: the scene holding it loads clean, lints clean, tests green,
and shows magenta only when it is on screen with someone looking.

It landed as a **pass inside `lint_project.gd`**, not a fifth tool. That file already
owns tree walking, `.gdignore`/vendored skipping, the finding/baseline/severity model,
`--json` and the `0`/`1`/`2` contract; a separate script re-implements all of it and buys
a fifth row in four doc surfaces. ~120 lines instead of ~400, and one fewer Godot launch
per `/verify`.

The mechanism is not tea-leaves'. Their version builds a `SubViewport`, a `Sprite2D` or
`MeshInstance3D` per shader type, adds a camera for spatial, and forces a render. A
40-second scratch probe ([H-033]'s rule, applied to an upstream design rather than a
pooled gap) showed **none of that is needed**: assigning `Shader.code` runs the real
`ShaderLanguage` parser even under the dummy rendering driver
(`servers/rendering/dummy/storage/material_storage.cpp:192` prints `Shader compilation
failed`), and `RenderingServer.get_shader_parameter_list()` returns `[]` on failure. Set
the code with a sentinel uniform appended, check the sentinel came back. No viewport, no
node, no render — and it covers every `shader_type` uniformly instead of needing a `match`
arm per type. The probe also established two things worth keeping:

- **`load()` on a broken shader still returns a `Shader`.** Same trap as `load()` on an
  unparseable script, already in `CLAUDE.md`'s gotcha list. Load success is not an oracle.
- **`#include` resolves from raw source**, so errors inside a `.gdshaderinc` surface
  through the includers. The include files themselves have no `shader_type` and are
  reported as *skipped*, never as passed.

Beyond tea-leaves' coverage: shaders embedded in a `.tres` are compiled too, found by a
text pre-filter on `type="Shader"` so the pass does not `load()` every resource in the
project to discover it holds no shader.

- Validated per [H-035]: `check_templates.py` gained `stage_shader_control`, which plants
  an uncompilable shader and requires exit `1` naming the file, plus
  `check_shader_denominator`, which requires the clean run to report `Shaders: N of M`
  over fixtures in all three shapes. `Shaders: none found` and a broken pass are otherwise
  the same output.
- **False-positive rate on real projects: 0.** `gather` (3 shaders) and `BoomerShooter`
  (28 files + 13 embedded) both report every shader compiling. Planting one broken shader
  in BoomerShooter took it to `41 of 42` and exit `1`. This is the [H-030] discipline —
  the scratch project cannot measure a false-positive rate.

**Reversed by measurement: `gdlint`.** tea-leaves runs it; the recommendation was to
copy that. Run against `gather` (169 non-addon `.gd` files) it produced **1,420 findings**
— `class-definitions-order` 579, `max-line-length` 398, `trailing-whitespace` 206, naming
rules 173. About 96% pure style; the correctness-adjacent remainder was 33 findings
(2.3%), none of them a bug. That is the "gate that cries wolf on install day gets ignored"
shape verbatim, plus this harness's first non-stdlib Python dependency, to cover ground
`name_check.py` and `lint_project.gd` already hold. tea-leaves benefits because it has
neither of those. Documented in `REFERENCE.md` *with the numbers*, so the next person to
propose it re-reads a measurement instead of re-running one.

**Reversed by its own data: cutting `verify_ledger.py`.** The proposal was to cut 1,103
lines unless reach had ever changed a decision. It has not — across 54 recorded runs in
`gather`, 50 had unreached files and not one changed the verdict. But that evidence
measures a **broken version of the metric**: 53 of the 54 runs are harness `0.7.0`/`0.8.0`
and predate the `test_scripts` exclusion (the key is absent from every row), and in them
`test/unit/*.gd` and `devtools_ext/commands.gd` dominate the unreached lists — up to 11 of
19 files in one run. The post-fix sample is **n=1**. Cutting a metric on numbers produced
by its own already-fixed bug is the wrong inference, so it stays.

- Gap: **reach still scores headless-only tooling as unreached.** In the one post-fix run
  (harness `0.10.0`, 2026-08-06), 4 of the 7 `unreached` entries were
  `tools/eval.gd`, `tools/lint_project.gd`, `tools/run_tests.gd` and
  `addons/godot_selftest/scene_validator.gd` — scripts that only ever run under
  `godot --headless --script` and are structurally incapable of appearing in a scene-tree
  snapshot of a game session. An earlier run charged `tools/generate_placeholder_art.gd`
  the same way. This is the identical mechanism `REFERENCE.md` already names for test
  scripts: "a metric that reports a file as unreached when it demonstrably ran teaches its
  readers to discount the number."
  - [H-039] status: fixed | fixed-in: 0.14.0 | seen: 1 | harness: 0.13.0
  - Improvement: a `headless_tools` sub-list of `not_applicable`, driven by a configurable
    `reach_headless_dirs` defaulting to `["tools/"]` — mirroring `uid_check_ignore`, which
    already treats `tools/` as not-game-code. Leave `addons/` alone: `dev_tools.gd`
    resolves via `reached_implicit` already, and `scene_validator.gd` showing unreached
    after `validate_ui` ran is an *observation* gap in `scripts_seen`, not a
    classification one — confirm that separately before folding the two together.

**Considered and rejected, again:** splitting `dev_tools.gd`. Re-proposed this turn on a
line count (4,172), which is exactly what the 0.12.0 entry said not to do: "Revisit with a
concrete reason, not a line count." Looking for one turned up nothing — the file is
sectioned, every verb greps as `_cmd_<verb>`, and no session in this log has lost time to
its size. Against that, a split re-introduces the freed-`Callable` hazard that already bit
this repo once through `_extension`, across the 45-verb surface named as the risk surface.
Standing rejection; reopen only with a session that actually lost time.

**Validation run this turn:** `python tools/check_templates.py` — **OK**, including both
new lines (`shaders 3 of 3 compiled (2 file, 1 embedded), include skipped` and `shader
control fired`). `python tools/record_version.py --record` then `--check` — OK at
`0.13.0`, 11 shipped files, 46 bus verbs + 48 CLI commands documented. Shader pass also
run against two real projects (`gather`, `BoomerShooter`) for the false-positive count
above.

## 2026-08-14 — Close H-039: stop charging headless tools as unreached (0.14.0)

Same session as 0.13.0, acting on the gap that release's ledger audit opened.

**Fixed [H-039].** `split_reach()` gains a `headless_tools` sub-list of
`not_applicable`, driven by `reach_headless_dirs` (default `["tools/"]`). Scripts that
only ever run as `godot --headless --script res://tools/x.gd` have no node to be the
`script` of, so they can never appear in a scene-tree snapshot however thoroughly they
ran — `lint_project.gd` and `run_tests.gd` were being scored misses by the runs that had
just executed them. Excused, not credited, on the same terms as `test_scripts`.

Checked against the run that exposed it (`gather`, harness 0.10.0, 2026-08-06): its
`unreached` goes **7 → 4**, with `tools/eval.gd`, `tools/lint_project.gd` and
`tools/run_tests.gd` moving to `headless_tools`. The reach ratio for that run goes from
11/18 to 11/15 without a single file being credited that was not already running.

`addons/` is deliberately **not** covered. `dev_tools.gd` is the autoload and already
resolves through `reached_implicit`, and `scene_validator.gd` showing unreached after
`validate_ui` demonstrably ran is an *observation* gap in `scripts_seen`, not a
classification one. Folding the two together would have hidden the second bug inside the
fix for the first — which is [H-033]'s rule (reproduce the mechanism, don't build to the
report) applied to a gap this repo wrote itself.

- Gap: **`scripts_seen` does not report a script the bridge loaded on demand.**
  `addons/godot_selftest/scene_validator.gd` was in the changed set of the 2026-08-06
  run, `validate_ui` ran during it, and reach still scored the file unreached. Either the
  census only sees scripts attached to nodes (the validator is loaded and called, never
  parented), or it snapshots before the validator loads. Unlike the headless-tool case
  this one is a *real* miss the metric should be able to close, because the script
  genuinely executed.
  - [H-040] status: fixed | fixed-in: 0.29.0 (gh#30; status line left open until 0.42.0's --triage) | seen: 1 | harness: 0.14.0
  - **Not reproduced** — inferred from one ledger row, so per [H-033] the mechanism above
    is a hypothesis. The probe is small: scaffold a scratch project, call `validate_ui`
    over the bus, then call `scripts_seen` and check whether the validator's path is in
    the list.
  - Improvement: if it is the not-parented case, have the census record every script the
    bridge itself `load()`s, not only those it finds on nodes. That is the same
    "credited because it demonstrably ran" standard `reached_implicit` already uses.

**Validation run this turn:** `python tools/check_templates.py` — **OK**, including the
new `stage 1.5 reach`. That stage plants every bucket rather than asserting the happy
path, and both regressions were confirmed to fail it before shipping: reverting the
default to `[]` gives `headless_tools was [], expected [...]` and exit `1`, and replacing
the segment-aware `_under()` with a naive `startswith` swallows the planted `toolsy/`
decoy and also exits `1`. A stage that only reports success is not a stage ([H-035]).
`python tools/record_version.py --record` then `--check` — OK at `0.14.0`.
`python -m unittest discover -s tools` — 17 tests, OK.

**Noted, not fixed:** `tools/test_scaffold.py` (17 passing tests) is referenced by
nothing — not `check_templates.py`, not `CLAUDE.md`, not any command. It only ran this
turn because it was gone looking for. A suite nothing invokes is a suite that will rot
without anyone noticing, which is the same class of problem as a gate that cannot fail.

## 2026-08-14 — A release skill, and standalone scene capture (0.15.0)

**Added `.claude/skills/harness-release/`.** The release sequence had been run by hand
three times in one day — bump stamps, `--record`, `--check`, `check_templates.py`, log
entry, close beads, commit — and it is fixed enough to encode. The skill carries
`bump_version.py`, which rewrites *only* the two stamp shapes and leaves prose mentions
of older versions alone (a line like "reachable while paused since 0.12.0" is a
historical fact, and a repo-wide replace turns it into a lie — the way this step actually
goes wrong).

It found a bug in itself within the hour. `bump_version.py` shipped with its own copy of
the shipped-file list, `capture.gd` was added to `record_version.py`'s `SHIPPED` and not
to that copy, and the bump silently left the new tool a version behind. `--check` caught
it (`tools/capture.gd: stamp 0.14.0 != plugin.json 0.15.0`), which is the gate doing its
job — but the right fix was deleting the duplicate: `bump_version.py` now imports
`SHIPPED` and `MIRRORED` from `record_version.py`, so there is one list and it lives with
the checker that enforces it. A partial bump also now re-runs cleanly, printing
`already at X.Y.Z` instead of reporting every finished file as a failure — which is
precisely the state someone is in when they need that output to be readable.

**Added `templates/tools/capture.gd`**, from another session's feedback: it hand-rolled a
`capture.gd` (SceneTree, frame-stepped, `root.get_texture().get_image().save_png()`) and
noted that this is the boilerplate every visual Godot check needs. It is — and the fact
that a session had to write it is the finding. The harness could photograph a *running
session* through the bridge and had no way at all to photograph *a scene*, so the ask
landed as a shipped tool rather than a skill carrying a snippet: a template gets a
version stamp, a `harness_history` hash, a `check_templates` stage, and installation into
every scaffolded project. A skill would have told the next agent how to write the file
again.

The sharp edge is that **it must not run headless**, which inverts the rule every other
runner here follows. Probed on Godot 4.7.1 (scratchpad/shotprobe): under `--headless`
`DisplayServer.get_name()` is `"headless"`, `root.get_texture()` returns **null**, and a
naive implementation would write a blank or zero-byte PNG and report success. Windowed,
the same script captured 320x200 with `pixel(10,10)` equal to the exact colour painted
into the fixture — so it is genuinely rendering, not reading a cleared buffer. The tool
therefore exits `2` under headless, before doing any work, naming the fix.

Every run prints the **distinct colours sampled**, because a flat image is what a broken
scene, a capture taken too early, and a working solid-colour splash all produce, and only
the first two are bugs. Default is a loud `WARNING` at exit `0` (a solid scene is legal);
`--fail-on-uniform` makes it gate. `--frames` defaults to 3 and the docs say why two is
the floor: `@onready` and container sizing land on the first frame, so an earlier capture
is a correctly-rendered picture of an unfinished layout — which reads as a layout bug and
is not one.

- Also fixed a documentation gap `CLAUDE.md` had already flagged about itself: `eval.gd`
  was shipped and installed but named in **no** doc surface ("easy to forget because no
  doc surface is required to name it"). `REFERENCE.md` now has a *Standalone runners*
  section covering both it and `capture.gd`.

**Validation run this turn:** `python tools/check_templates.py` — **OK**, with the new
`stage 4 capture` proving all three cases: headless refused (exit `2`, **no file
written**), a real windowed capture (3 colours), and the flat control firing (exit `1` on
a scene that draws nothing). That last one is the [H-035] point — without a genuinely
blank scene, a colour counter hard-wired to report "plenty" passes every other check.
`python .claude/skills/harness-release/bump_version.py 0.14.0 0.15.0` then
`record_version.py --record` / `--check` — OK at `0.15.0`, **12** shipped files, 46 bus
verbs + 48 CLI commands documented.

No new gaps this turn beyond the one closed above.

## 2026-08-14 — H-038: the bridge now actually serves one command at a time (0.16.0)

`[H-038]` was logged as read-not-run, so the first move was the probe `[H-033]` requires,
and it landed the hypothesis exactly. `step_time 5s`, then a `ping` command file written
by hand 1 s later:

```
  0.00s  wrote A = step_time 5s
  1.16s  wrote B = ping
  1.26s  RESULT FILE -> id=B action=ping        <- inside the still-running step_time
  5.12s  RESULT FILE -> id=A action=step_time   <- overwrites B before any client reads it
```

Two handlers in one scene tree, and B's reply destroyed by A's. The fix is the guard the
gap proposed — a `_dispatch_busy` bool set around the dispatch and checked at the very top
of `_check_for_commands`, before the read and the delete — and after it the same probe
shows A at 5.09 s and B at 5.19 s: deferred by one poll tick, not dropped.

**The one-line fix was not one line, because the guard makes a previously true statement
false.** `devtools.py`'s 2 s liveness precheck read "command file still on disk" as proof
that nothing is polling, and a deferred command sits on disk *on purpose*. Shipping the
guard alone would have made every command sent during a slow verb die instantly with
`game not running` — a confidently wrong diagnosis, the failure mode this repo has paid
for more than once. Proven, not assumed: with the guard in and the client untouched, the
new stage fails with `send_command('ping') raised GameNotRunningError while the game was
alive and busy inside step_time`. The discriminator is the breadcrumb file plus a fresh
owner heartbeat (`_handler_in_flight`) — breadcrumb alone is ambiguous by design, since
the same file is what names the verb that took a game *down*. A timeout now also says
which case it hit and withdraws the queued command rather than letting it fire minutes
later against changed state.

The guard also needed an escape it was not asked for. A GDScript runtime error inside an
`await` does not raise — the coroutine simply never resumes — so the line clearing the
flag would never run and the bus would go deaf until restart, silently. That is worse
than the race it prevents, so a 300 s watchdog releases the guard with a log line naming
the verb. The ceiling clears the longest legitimate handler (`step_time` 60 s,
`validate_all` ~90 s) on purpose: a slow handler must never trip it.

- **A check that watches the result file for both replies cannot work, and looked like it
  did.** The first version of the stage polled `devtools_results.json` at 20 ms for the
  two replies in order. The second command is picked up within one ~100 ms poll of the
  first reply being written, so the first reply exists on disk for a few milliseconds —
  the stage missed it roughly half the time and reported the *passing* implementation as
  the bug, with a confident message about handlers sharing the tree. The game's log had
  the truth all along (`Executing: step_time` frame 271, `Executing: ping` frame 706,
  3.01 s apart). The stage now reads those durable timestamps and answers by subtraction
  instead of by winning a race; 4 consecutive runs report 3.10–3.11 s.
  - [H-041] status: fixed | fixed-in: 0.16.0 | seen: 1 | harness: 0.16.0
  - Worth keeping as a rule: **when asserting ordering on this bus, assert against the
    log, not the result file.** The result file is last-writer-wins by design and holds
    any given reply only until the next one lands.
- **Two concurrent `send_command`s are not a way to test anything**, and the second draft
  of the client check used them. Both clients poll the one result file, so whichever
  reads second finds its reply already consumed and times out after 30 s — the
  single-client hazard `REFERENCE.md` documents, reproduced by the test rather than by
  the code under test. The slow verb is now started by writing its command file directly,
  leaving exactly one real client on the bus.

**Validation run this turn:** `python tools/check_templates.py --full` — **OK**, with two
new lines under stage 5: the deferral gap (`ping sent 0.5s into a 3s step_time was
dispatched 3.10s in`) and the client surviving it (`answered after 2.6s instead of failing
the 2s liveness precheck`). Both were confirmed to **fail** first, in all three ways they
can ([H-035]): guard removed → `dispatched ON TOP ... ping started 0.52s after step_time
began`; guard moved after the read/delete → `the deferred command was EATEN`; client
precheck reverted → `GameNotRunningError while the game was alive and busy`.
`python -m unittest discover -s tools` — 17 tests OK. `record_version.py --record` then
`--check` — OK at `0.16.0`, 12 shipped files, 46 bus verbs + 48 CLI commands documented.

No new gaps this turn beyond the two closed above.

## 2026-08-14 — gh#1 and gh#2: four reported defects, three real (0.17.0)

Two GitHub issues arrived from `moving-in` via `skill-feedback-issue`, carrying four
claims and four proposed diffs. Working the [H-033] rule — reproduce the mechanism
before implementing the `Improvement:` line — three reproduced and one did not, and the
one that did not came with the most confident patch of the four.

- **A scaled `CanvasLayer` made every right-anchored Control read as off-screen.**
  `Control.get_global_rect()` stops at the `CanvasLayer`; `get_tree().root.size` is
  window pixels. Nothing applied the transform between them, so a HUD on a layer with
  `scale = 0.6` reported rects in layer units against a viewport in pixels. The
  reporter found it in `validate-ui` and `reachable-ui`; it was in **five** places
  (`validate_ui`, `get_ui_snapshot`, `get_node_bounds`, the `ui-snapshot-diff` flat
  snapshot, `reachable_ui`). `get_node_bounds` was the tell — its docstring promises
  "the same transform the renderer uses, so it accounts for the camera, every
  ancestor's scale, and the `CanvasLayer`", and then took a `Control` branch that used
  none of it. Both halves are now `_screen_rect_of()` / `_screen_reference_rect()`.
  - [gh#2] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0
  - **Measured on the real project, because the scratch one cannot measure this**
    ([H-030]): `moving-in` went from **53 findings (51 `ui_overflow`) to 2**, and the
    2 survivors are `ui_zero_size`, which are real and unrelated. 51 of 51 overflow
    findings were false. Run on a copy; its working tree and branch were not touched.

- **The same probe found a second viewport bug nobody reported.** Headless has no
  window, so `root.size` is **64x64** — against which every Control wider than 64px
  "extends past viewport". `check_templates.py` drives the bridge headless, which is
  exactly why its UI stages never caught any of this: the planted findings were
  trivially true and the check was measuring nothing. The UI verbs now fall back to
  the project's designed `display/window/size/viewport_*`, and the new stage asserts
  the reported viewport is `1152x648` and not `64x64` — without that assertion every
  other assertion in the stage is vacuous.
  - [H-042] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0

- **`set-state` could not write a dotted path that `get-state` reads fine.** Neither
  `Object.set` nor `Object.get` walks dots, so `environment.ambient_light_energy` wrote
  nothing and then reported "unknown property" — about a name that was correct, which
  is the part that cost the reporter the most time. `_resolve_property_path()` was
  already there and already used by `get_state`. The write now resolves through it and
  the failure names the object it landed on (`Theme has no property 'x'`), not the
  node. A component of a built-in struct (`size.x`) is refused naming the call that
  works, rather than silently doing nothing.
  - [gh#1.1] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0

- **The bus rejected the hyphen spelling its own scaffolding documents.** `commands.gd`'s
  header said verbs are addressed "via the Python client with hyphens", every generic
  verb is hyphenated, and the sequence-step dispatcher already did
  `cmd_name.replace("-", "_")` — but `_check_for_commands` matched verbatim, so
  `cmd light-get` failed while the identical step inside a sequence worked. One
  fallback. An action matching nothing now suggests the nearest registered verb, since
  a bare `Unknown action` cannot tell a typo from a verb that was never registered and
  those want opposite next steps.
  - [gh#1.3] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0

- **The one that did not reproduce: `set-state --property position` on a
  `CharacterBody3D` "consumes the whole timeout and writes nothing".** The report was
  honest that the cause was not isolated, and proposed deferring the write to
  `call_deferred` on a physics-re-entrancy theory. A scratch probe — a `CharacterBody3D`
  running `move_and_slide()` every physics frame — answered in **0.1s, `success=True`,
  reading back exactly `1.5,0,-5.5`**. `_cmd_set_state` is synchronous and contains
  nothing that can block, so `call_deferred` would have moved an abort one frame later
  while breaking the read-back guarantee that is the verb's entire point: a pass that
  fixed nothing, which is the [H-033] failure mode precisely.

  What is real, and separate: a GDScript runtime error raised by *project* code
  reacting to a verb (a setter, a signal, an `Area` `body_entered`) kills the handler
  before it can reply, and there is no exception to catch. The game survives, so every
  later verb answers and the verb looks selectively broken — which matches the report
  exactly. `DISPATCH_WATCHDOG_MSEC` is 300000 against a 30s client default, and the
  watchdog only wrote to the **log**, so the explanation landed 4.5 minutes after the
  caller gave up. It now writes a failure **result** carrying the wedged request's id,
  and the client's timeout message names the mechanism and points at `[SCRIPT ERROR]`
  on stderr.
  - [H-043] status: open | seen: 1 | harness: 0.17.0
  - Still open because the watchdog cannot fire inside a normal client timeout without
    force-releasing legitimate long verbs (`step_time`, a sequence with waits) that
    hold the guard on purpose. A per-verb expected duration would let the guard be
    tight for `set_state` and loose for `step_time`; nothing carries that today.

**Validation run this turn:** `python tools/check_templates.py --full` — **OK**,
72/72 contract rows, with two new stage 5 lines: `canvas-layer space OK (scaled HUD:
inside 720..900 clean, outside 1800 still flagged, viewport 1152x648 not 64x64)` and
`set_state dotted write OK (theme.default_font_size -> 21; bad leaf, struct component
and absent verb all still refused; 'scene-tree' accepted)`. Both plant the defect they
detect ([H-035]) — the fixture carries a Control that is genuinely off screen and three
writes that must still be refused, so a check that reports nothing fails rather than
passes. Confirmed to fail first: the contract table's `reachable_ui` row caught the
fixture change on its own (`count is 4, expected 2`), and an early fixture typo failed
stages 3, 4 and 5 in a row. Real-project false-positive count reported above.
`python -m unittest discover -s tools` — 17 tests OK. `record_version.py --record` then
`--check` — OK at `0.17.0`, 12 shipped files, 46 bus verbs + 48 CLI commands documented.

## 2026-08-14 - Upstreamed 16 open gap(s) from moving-in (harness 0.11.0, 0.16.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **no 3D equivalent of `node-bounds`** — `node-bounds` is CanvasItem-only, so a 3D
  project cannot ask the harness where a node actually is.
  `python tools/devtools.py node-bounds /root/House/Home/Bedroom/lampSquareCeiling` →
  `Failed: Node is not a CanvasItem, so it has no screen rect: ... (Node3D)`. Workaround:
  wrote `HouseBuilder.local_aabb()` plus an `audit()` method on the builder and reached it
  via `run-method --json`. Every 3D project will need to rebuild that.
  - [moving-in:G-002] status: fixed | fixed-in: 0.18.0 | seen: 1 | harness: 0.11.0 | source: moving-in 2026-08-14
  - Improvement: make `node-bounds` return the world-space AABB (position/size/end, and
    the transform) for a `Node3D`, merging `GeometryInstance3D` children and excluding
    `Light3D` — the exclusion is not obvious and cost this run a bug.

- Gap: **reach is silently 0/0 in a non-git project** — this checkout has no `.git`, so
  `git diff --name-only HEAD` fails, Phase 0.5 triage has no input, and
  `verify_ledger.py reach` prints `worktree ... reached 0/0 changed file(s)` /
  `branch ... reached 0/0`. A `0/0` reads as "nothing to check" rather than "cannot tell",
  and `record` accepted the row without comment. Coverage was in fact good — the
  `scripts-seen` set names 3 of the 4 new scripts, the fourth being a static-only
  `RefCounted` that owns no node — but nothing in the ledger says so.
  - [moving-in:G-003] status: fixed | fixed-in: 0.18.0 | seen: 1 | harness: 0.11.0 | source: moving-in 2026-08-14
  - Improvement: have `reach` distinguish "0 changed files" from "no VCS" and report
    `reach: unavailable (not a git repository)`, and have Phase 0.5 name an explicit
    no-VCS tier that goes straight to a full run instead of failing its first command.

- Gap: **scaffold installs new `.gd` tools without a `.uid` sidecar, and
  `uid_check_ignore` hides it** — the refresh added `tools/capture.gd` as the only
  new file. `scaffold_install.py files` copies the script alone; the plugin ships no
  `templates/tools/*.uid`, and a `--headless --script` run does not import it. Lint
  still printed `UIDs: OK`, because the default config has
  `"uid_check_ignore": ["res://addons/", "res://tools/"]` — so the one check that
  would have caught it is switched off exactly where scaffold writes. The result is
  a `tools/` directory with `eval.gd.uid`, `lint_project.gd.uid`, `run_tests.gd.uid`
  and no `capture.gd.uid`, which reads as drift on the next refresh. Workaround:
  `python tools/devtools.py new-uid --write tools/capture.gd` → `uid://cfltmy4sah1s`.
  - [moving-in:G-004] status: fixed | fixed-in: 0.18.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: have `scaffold_install.py files` emit a `.uid` for any `.gd` it
    installs that lacks one (it can call the same generator `new-uid` uses), or ship
    the sidecars in `templates/`. Either way scaffold should report it, since the
    project's own lint is configured never to.

- Gap: **no gate for asset conformance** — the harness lints scripts, scenes, shaders
  and UIDs, but `assets/furniture/` is unchecked. `scripts/house_builder.gd:18` loads
  by directory (`const MODEL_DIR := "res://assets/furniture/"`), so a new `.glb` with a
  stray material name, a centred pivot or a base above y=0 enters the game with lint
  clean, tests green and no error anywhere — it just sits wrong in the room. This is
  the same class of silent failure `Shaders: N of M compiled OK` was added for.
  - [moving-in:G-005] status: open | seen: 3 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: an `asset_check.py` in the `name_check.py` mould — no engine, walks
    the glTF JSON, and fails any model in `scan_root`'s asset dirs whose material names
    fall outside the kit's established set or whose pivot/base/facing deviates. The
    reference values should be captured from the existing kit into a baseline file
    (same `--baseline` / `--baseline-write` split lint already uses) so the check
    describes *this* project's kit rather than hardcoding Kenney's.

- Gap: **`node-bounds` is `Control`-only, so there is no way to ask the bridge what a
  `Node3D` occupies** — `node-bounds /root/House/Home/Living/tableCoffeeGlass` returns
  `Failed: Node is not a CanvasItem, so it has no screen rect`. In a 3D game the
  equivalent question ("where does this table's top surface actually end") is asked
  constantly, and `HouseBuilder` already answers it internally via `local_aabb()`. With no
  verb for it I read `global_position`, separately probed the model's size out of the
  `.glb`, and did the arithmetic by hand — and got it wrong first time, placing the book
  4 cm over the table edge, which only the screenshot revealed.
  - [moving-in:G-006] status: fixed | fixed-in: 0.18.0 | seen: 2 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: an `aabb --node PATH` verb returning the merged world-space AABB of the
    node's `GeometryInstance3D` descendants (position, size, centre, and the y of the top
    face), excluding non-geometry `VisualInstance3D`s — the skill's own notes warn that an
    `OmniLight3D`'s AABB is a cube of twice its range and will silently corrupt any
    measurement that includes it. That single verb would have removed every manual step
    above and the placement error with it.

- Gap: **`aabb --node PATH` still missing — hit a second time in the same session.**
  - [moving-in:auto-04954d] status: open | seen: 1 | source: moving-in 2026-08-14
  Verifying the placement meant re-deriving the table's world extent by hand: read
  `global_position` off the bridge, read the pivot offset and size out of `tableCoffeeGlass.glb`
  with a separate script, add them, then rotate the book's own footprint by -12 deg in a
  throwaway Python file to get its bounds. Roughly 25 lines to answer "do these two boxes
  overlap", against a game that computes exactly this internally on every single
  `b.item()` call.
  - (see [G-006] above — `seen:` bumped to 2)
  - Improvement: unchanged, and now with a second use case. Beyond the verb itself,
    `audit()` is the natural home for the check that was actually wanted: it already
    tests room escape and floor-item overlap, but exempts anything resting on furniture,
    so an item can sit 90% off the table it stands on and audit stays clean. A
    `supported_by` check — footprint of a raised item against the footprint of whatever
    it rests on — would have made this whole manual pass a single call.

- Gap: **lint reports `UIDs: OK` and exit `0` on an asset the engine cannot load** — its
  sidecar check covers `.gd`/`.uid` pairs but not imported resources, so a `.glb`, `.png`
  or `.ogg` added outside the editor with no `.import` is invisible to every Phase 1 gate.
  Ran `godot --headless --path . --script res://tools/lint_project.gd` immediately after
  writing `bookOpenMCP.glb`; got `UIDs: OK` … `exit 0` with no `.import` file on disk.
  Workaround: run `godot --headless --path . --import` by hand before trusting lint on any
  turn that adds a non-script asset.
  - [moving-in:G-007] status: open | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: extend the existing UID pass to walk `scan_root` for importable
    extensions and report `MISSING IMPORT <path>` when a recognised asset has no
    `.import` sidecar — the same shape as the `.uid` check already beside it, and it would
    have turned this turn's silent pass into a one-line failure.

- Gap: **`validate-ui` and `node-bounds` ignore the CanvasLayer transform, so a scaled
  HUD reports as entirely off-screen** — `validate-ui` returned 64 findings, including
  `ui_overflow: PanelContainer 'Room_Office' extends past viewport (rect: 1618,455 ->
  1854,507, viewport: 1152x648)`, for a panel that a screenshot shows sitting correctly
  inside the right edge. `node-bounds` agreed: `Rect: 1586, 32, 302x497 / In viewport:
  False`. Both read `Control.get_global_rect()`, which is in the CanvasLayer's own
  space; `python tools/devtools.py canvas-scale --node .../Panel` reports
  `accumulated scale: (0.6, 0.6)`, and 1586 x 0.6 = 952 — exactly where it draws. The
  harness already computes the missing factor in a different verb. Workaround: read
  each finding, confirm against a screenshot and `canvas-scale`, then
  `validate-ui --baseline-write` — which is accepting 64 findings to silence a
  coordinate-space bug, and would equally silence a real overflow appearing later.
  - [moving-in:G-008] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Arrived twice: pooled here as `moving-in:G-008`, and separately as GitHub issue
    [gh#2], which is the id 0.17.0 closed it under. Same defect, two intake paths, two
    id namespaces — see [H-044].
  - Improvement: multiply the Control's rect by the accumulated `CanvasLayer.transform`
    (the calculation `canvas-scale` already does) before comparing against the viewport
    in both `validate-ui`'s `ui_overflow` rule and `node-bounds`' `In viewport` line.
    Failing that, have both print the accumulated scale alongside the rect, so a reader
    can see in one line that the numbers are not in screen space.

- Gap: **`name_check.py` cannot see GDScript type-inference errors, and it is the only
  gate a fan-out agent is allowed to run** — `:=` against a call on a `Node`-typed
  variable is a hard compile error (`Parse Error: Cannot infer the type of "name"
  variable because the value doesn't have a set type`), but `python tools/name_check.py`
  reported `errors: 0 | warnings: 0` on the same file. It bit twice in one session: once
  in an agent's `devtools_ext/commands.gd`, once in a test I wrote myself. Because
  `CLAUDE.md` correctly forbids parallel agents from running lint (shared `.godot/`),
  every agent's "verified" claim excluded the entire class.
  - [moving-in:G-009] status: fixed | fixed-in: 0.18.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: teach `name_check.py` the narrow, high-value case it can already
    almost see — `var x := <call on a variable whose declared type is a base class that
    does not declare that method>` — and report it as an error. It resolves declared
    types and engine members today, which is most of the machinery. Short of that, say
    plainly in the `--only` help text and in `CLAUDE.md` that a clean `name_check` does
    **not** imply the file compiles, so a fan-out agent knows what its one permitted
    gate does not cover.

- Gap: **no way to simulate mouse motion, so first-person look is unverifiable** —
  `input list` covers only InputMap actions and mouse look goes nowhere near the
  InputMap; `key`, `input tap` and `touch` cover everything except the one device that
  aims the camera. The result is that a game whose camera cannot turn passes every gate
  the harness has. Workaround: wrote a project verb, `mouse_look`, that builds an
  `InputEventMouseMotion` and pushes it with `get_viewport().push_input()` — entering at
  the Viewport deliberately, so anything that would swallow it in a real session
  (a Control with `mouse_filter` STOP, an earlier `set_input_as_handled()`) swallows it
  here too. It reports before/after heading and pitch and says outright
  `Camera did NOT move — the motion event was delivered and ignored`.
  - [moving-in:G-010] status: open | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: promote it to a generic verb — `mouse move --by DX,DY [--to X,Y]`
    and `mouse button left|right [--pressed]` — since nothing in it is project-specific
    beyond reading back a heading. Pair it with a `--report NODE.method` or simply have
    it return the active `Camera3D`'s global basis before and after, which is
    game-agnostic and enough to tell a turning camera from a frozen one.

- Gap: **`find-nodes --where` cannot match an enum property, which is how this bug was
  nearly missed** — `find-nodes --class Control --where mouse_filter=0` returned nothing
  at all, while `--class Control --property mouse_filter` listed all 89 Controls with
  `mouse_filter=0` plainly visible among them. The filtered form silently answered "no
  such nodes" for a predicate that has four matches; taken at face value it clears the
  UI of exactly the fault it has.
  - [moving-in:G-011] status: fixed | fixed-in: 0.18.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: make `--where` compare numerically when the property's value is an int
    or a float (`mouse_filter=0`, `layer=6`), rather than only by string equality — and,
    when a `--where` predicate matches zero nodes but the property exists on candidates,
    say so (`0 of 89 matched on mouse_filter`) instead of printing nothing. A silent
    empty result and a genuine absence must not look the same; that is the same failure
    the test runner's `Selected: N of M` line exists to prevent.

- Gap: **`set-state` cannot write through a dotted path, only read through one** — every
  knob in a Godot lighting rig lives on a sub-resource, so tuning the thing this change is
  about was unreachable from the generic verbs. `python tools/devtools.py set-state --node
  /root/House/WorldEnvironment --property environment.ambient_light_energy --value 0.30`
  returned `Failed: set had no effect ... wrote 0.3 but read back null (unknown property,
  or a setter clamped/rejected it)`, while `get-state --property environment.ambient_light_energy`
  reads it fine. The asymmetry is undocumented: the CLAUDE.md table advertises dotted paths
  under `get-state` and says nothing either way under `set-state`, so the natural reading is
  that both support them. Workaround: wrote a project verb (`light_get` / `light_set` in
  `devtools_ext/commands.gd`) that reaches the Environment and the OmniLight3Ds directly.
  - [moving-in:G-012] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Closed under [gh#1.1] — same defect, arrived by both intake paths. See [H-044].
  - Improvement: make `set-state` walk the same dotted path `get-state` already walks —
    resolve every segment but the last, then set the last on whatever object that lands on
    (Resource, Dictionary, or nested Object). Failing that, the error message should name
    the real cause ("dotted paths are read-only; `environment` is a Resource") instead of
    "unknown property", which sent me looking for a typo in a name that was correct.

- Gap: **`set-state --property position` on a `CharacterBody3D` hangs the bus for the full
  timeout** — `set-state --node /root/House/Player --property position --value "1.5,0,-5.5"`
  returned `No response from Godot after 30.0s. The command WAS picked up (the game is
  alive) but 'set_state' never answered`, and the read-back afterwards showed the player
  still at its spawn `(0.5, 0.0, -3.5)`: 30 seconds spent, nothing written, no error.
  Workaround: called the game's own `teleport_to_grid` through `run-method`, which is
  instant — but that only exists because this project happens to have written one.
  - [moving-in:G-013] status: wontfix | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Investigated under [gh#1.2] for 0.17.0 and **did not reproduce**: a scratch project
    with a `CharacterBody3D` running `move_and_slide()` every physics frame answered in
    0.1 s, `success=True`, reading back `1.5,0,-5.5` exactly. `_cmd_set_state` is
    synchronous and contains nothing that can block, so the report's proposed
    `call_deferred` would have moved an abort one frame later while breaking the
    read-back guarantee that is the verb's whole point — clean review, clean lint, fixes
    nothing, the [H-033] failure mode exactly.
  - wontfix reason: the reported *mechanism* is wrong, but the *symptom* was real. What
    actually produces it is a GDScript runtime error raised by **project** code reacting
    to a verb (a setter, a signal, an `Area.body_entered`), which kills the handler
    before it can reply with no exception to catch. That is tracked as [H-043] and is
    where any further work belongs. Closing this id rather than leaving it open so it
    cannot be picked up again and built to its wrong Improvement line.
  - Improvement: whatever `set_state` does after the write (a read-back that re-enters the
    physics server on a body mid-`_physics_process` is the likely culprit) needs a guard:
    defer the write to the next idle frame, or bound the read-back and answer with what it
    got. A verb that can consume the whole timeout and write nothing is worse than one that
    refuses the property outright, because the failure costs 30 s and looks like a crash.

- Gap: **`cmd <verb>` does not accept the hyphenated spelling its own scaffolding
  documents** — `python tools/devtools.py cmd light-get` replied `"message": "Unknown
  action: light-get", "success": false`, and only `cmd light_get` works. Both the harness
  CLAUDE.md and the header comment scaffolded into `devtools_ext/commands.gd` state the
  opposite: *"Verbs are addressed over the bus with underscores ("example_ping") and via
  the Python client with hyphens ("cmd example-ping")."* Every generic verb IS hyphenated
  (`scene-tree`, `find-nodes`), so the hyphen is the form the whole CLI trains you to type.
  (This is the same defect the project already tracks as bd `moving-in-c67`, filed against
  the docs; recording it here is what puts it in front of the harness.)
  - [moving-in:G-014] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Closed under [gh#1.3] — same defect, arrived by both intake paths. See [H-044]. The
    fix is `action.replace("-", "_")` at `dev_tools.gd:570`; note the sequence-step
    dispatcher had always done this, so only `_check_for_commands` matched verbatim.
  - Improvement: one line in the `cmd` handler — try the verb as given, then retry with
    `-` translated to `_` before reporting `Unknown action`. Cheaper and less breakable
    than correcting two documents, and it makes the project verbs match the generic ones.
    If the hyphen form is deliberately unsupported, the fix is instead to correct the
    scaffolded header and the CLAUDE.md sentence, and to have `Unknown action: light-get`
    add "(did you mean `light_get`?)" when the underscore form is registered.

- Gap: **`validate-ui` and `reachable-ui` compare canvas-space rects against screen
  pixels, so any UI on a scaled `CanvasLayer` is reported as off-screen** —
  `validate-ui` returned `[FAIL] 65 UI issues found (55 NEW, 10 pre-existing)` on a diff
  that touched no UI file, every NEW one a `ui_overflow` like `MarginContainer ... (rect:
  1598,40 -> 1874,521, viewport: 1152x648)`, while the screenshot showed all of them on
  screen. `node-bounds` gave `Rect: 1586, 32, 302x497` and
  `get-state --node /root --property content_scale_size` gave `(1152, 648)` — so not a
  stretch-mode issue — and the layer itself reported `scale: {"x": 0.6, "y": 0.6}`.
  1586 × 0.6 = 952, which is exactly where it draws. `reachable-ui` shares the bug and
  labelled two clickable buttons `OFF-SCREEN`. Workaround: read each finding, prove the
  cause, then accept 53 of them into `ui_findings_baseline.json` — which is the outcome
  the baseline feature exists to prevent.
  - [moving-in:G-015] status: fixed | fixed-in: 0.17.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - The same defect as `moving-in:G-008` above, reported a second time by the project
    itself, and closed under [gh#2]. Three ids for one bug across two intake paths is
    the clearest evidence for [H-044]. 0.17.0 fixed it in five call sites, not the two
    the report named, and found a sixth viewport bug ([H-042]) while in there.
  - Improvement: use `control.get_global_transform_with_canvas() * Rect2(Vector2.ZERO,
    control.size)` instead of `control.get_global_rect()` in `_validate_ui_recursive()`
    and in `reachable-ui`'s off-screen test — it is the same call but it includes
    ancestor `CanvasLayer` transforms. Filed upstream with the diff. A regression test
    is one Control on a `CanvasLayer` with `scale = 0.5` at x = 1200 of a 1152-wide
    viewport, asserting **no** overflow finding.

- Gap: **a bare `/root` node path is mangled by Git Bash on Windows** —
  `python tools/devtools.py get-state --node /root --property size` came back
  `Failed: Node not found: C:/Program Files/Git/root`. MSYS path conversion rewrites a
  single-segment absolute path into a Windows one; `/root/House/Player` survives because
  multi-segment paths are left alone, so this only bites on the shortest and most
  obvious path in the system. Workaround: `MSYS_NO_PATHCONV=1` (or quoting as `//root`).
  - [moving-in:G-016] status: fixed | fixed-in: 0.18.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: the client already knows what a node path looks like. When `--node`
    arrives matching `^[A-Za-z]:[\/].*[\/]root$` — a Windows path ending in `/root`
    that no Godot tree could contain — it should either recover it as `/root` or fail
    with "looks like your shell rewrote this; try MSYS_NO_PATHCONV=1". A one-line note
    in the CLAUDE.md Gotchas would do nearly as well, since the symptom is baffling and
    the fix is a shell variable.

## 2026-08-14 — Nine gaps from `moving-in`, four agents, and a release that landed underneath them (0.18.0)

- Value: **warranted** — the gate caught a parse error in a new verb that four
  independent static checks had passed, and the investigation half of the turn
  rejected two of the nine reports outright.
  - Expected: that the five code fixes I had scoped from `moving-in`'s log were all
    real and all still open, and that the main risk of a four-agent fan-out was file
    collisions.
  - Got: neither. **Three of the five had already shipped in 0.17.0**, committed and
    merged to `master` two minutes before the agents started, from a parallel session
    working the same findings through GitHub issues. Zero file collisions occurred;
    the actual hazard was a stale context snapshot. And `check_templates.py --full`
    then failed on `Parse Error: Cannot find member "get_column" in base "Basis"` —
    Godot 4.7's `Basis` exposes `x`/`y`/`z` and no `get_column()`.
  - Found: five things. (1) The `Basis.get_column` parse error above, in the new
    `aabb` verb — invisible to `name_check.py`, to `py_compile`, to a bracket-balance
    pass and to a hand review, because the agent that wrote it was forbidden the
    engine ([H-021]/[H-034]) and the only gate that sees this class of error needs it.
    (2) `get_node_bounds` was missing the `canvas_scale` key its own agent reported
    adding — caught by the contract row, not by the report. (3) Two new stages I added
    passed **silently**, printing nothing on success, which is the exact shape [H-035]
    forbids; they now name what they proved. (4) `moving-in:G-011` does not reproduce
    (below). (5) `moving-in:G-013` did not reproduce either — established in 0.17.0
    and independently confirmed here on a separate fixture.
  - Cheaper: for the *investigation* half, yes and decisively — reading
    `_values_match` settled `G-011` before any scratch project existed, and the
    scratch project's only job was to prove it by planting the removal. For the `aabb`
    half nothing was cheaper: a GDScript parse error has no static detector in this
    repo, which is now [H-045].

**What shipped.** `aabb --node PATH` (merged world-space AABB of a 3D node's geometry,
`Light3D` excluded and every exclusion named in `data.excluded`, failing rather than
returning a zero box); the `find-nodes --where` denominator; reach's third state;
`.uid` sidecars minted at install; `name_check`'s `NOT COVERED:` line; `node-bounds`
gaining `canvas_scale`; bare `/root` recovered from Git Bash's rewrite; `set-state`
routed through `_resolve_node`.

**What was rejected, and why that is the more useful half.** Two of the nine reports
asked for fixes to code that was already correct:

- `moving-in:G-011` claimed `--where` compares only by string equality, so
  `mouse_filter=0` matched nothing. It does not. Godot's JSON parser makes **every**
  number a float, so the predicate genuinely arrives as `float(0.0)` against an
  `int(0)` property — and `_values_match`'s `_is_number` widening branch has caught
  exactly that since 0.8.0 (`git log -S` confirms the branch and the function shipped
  in the same commit). Deleting that one branch in a scratch copy reproduces the
  report **verbatim**: int predicates silently zero, floats/bools/Strings fine,
  `--property` still printing the value plainly. The reporter's own installed 0.16.0
  file was read and is intact. The likely real cause is whitespace or case in the
  argument — `--where "mouse_filter = 0"` and `--where "Mouse_filter=0"` both return 0
  silently, because `cmd_find_nodes` partitions on `=` without stripping. Building the
  requested fix would have added a second numeric path beside the working one.
- `moving-in:G-013` proposed `call_deferred` for a `set-state` timeout on a
  `CharacterBody3D`. Answered in 0.17.0 and re-confirmed here: 0.1 s, `success=True`,
  exact read-back, with `_physics_process` demonstrably live at ~9700 ticks. One
  addition to the record — the reporter's "still at spawn" read-back is their own grid
  code re-asserting position on the next physics tick, not a write that never landed.

The [H-033] rule paid for itself twice in one turn. Both reports carried confident
patches; both patches would have reviewed clean, linted clean and fixed nothing.

- Gap: **two intake paths allocate ids in different namespaces and neither dedupes
  against the other** — gaps arrive pooled from a project by `tools/upstream_gaps.py`
  as `<project>:G-NNN`, and filed as GitHub issues by `skill-feedback-issue` as
  `gh#N`. 0.17.0 closed the canvas-space defect as `gh#2` and the dotted `set-state`
  as `gh#1.1`; pooling the same project's log an hour later re-appended them as
  `moving-in:G-008`, `G-015` and `G-012`, all `status: open`. Three ids for one bug.
  Nothing detected it — I reconciled the five duplicates by hand after noticing HEAD
  had moved. A project that files an issue AND gets pooled is the normal case here,
  not an edge one.
  - [H-044] status: fixed | fixed-in: 0.41.0 (pooled entries carry dup-of: gh#NN from the project's `filed upstream:` field) | seen: 1 | harness: 0.18.0
  - Improvement: have `upstream_gaps.py` refuse to append a gap whose evidence block
    substantially matches one already `fixed` in the destination, printing
    `SKIPPED <id>: looks like <other-id>, already fixed in X.Y.Z` rather than
    appending silently. A `see-also:` field on the status line would let the two
    namespaces point at each other once a human confirms the match.

- Gap: **no way to compile-check GDScript without the engine, so a concurrent agent
  writes it blind** — `check_templates.py` and every Godot invocation are serialized
  by the shared `.godot` cache ([H-021], [H-034]), so a fan-out agent editing
  `dev_tools.gd` is forbidden the only tool that can parse what it just wrote. The
  agent substituted `py_compile` on the Python half, a bracket-balance pass, a
  tab-indentation check and a precedent search for every syntax form it used — and
  still shipped `Basis.get_column()`, which does not exist in Godot 4.7.
  `name_check.py` did not catch it either: it resolves members on engine **classes**,
  and `Basis` is a builtin, so a bogus method on a builtin-typed local passes. The
  engine API index it already downloads *does* carry `builtins.Basis.members` — the
  data is present and unused.
  - [H-045] status: open | seen: 1 | harness: 0.18.0
  - Improvement: two independent halves, smallest first. (a) Teach `name_check.py` to
    check member access on locals whose declared type is a builtin it has members for.
    It already parses `var x: Basis` and already holds the member list, so this is a
    lookup, not new inference — and it would have caught this exact defect. (b) Add a
    real parse-only gate that needs no project (`gdtoolkit`'s `gdparse`, or
    tree-sitter-gdscript), runnable concurrently because it touches no `.godot/`.

- Gap: **`find-nodes --property` prints `null` for a path it could not resolve** — a
  dotted path into a built-in struct (`position.x`, `modulate.a`, `size.y`) is
  unresolvable, because `_resolve_property_path` walks only `Object` and `Dictionary`.
  `get-state` is honest about it (`position is a Vector3, not an object -- cannot read
  .x off it`, exit 1); `find-nodes` prints `position.x=null`, indistinguishable from a
  property that genuinely holds null. `--where` shares the root cause and is fixed
  this release by carrying the resolver's reason; the `--property` half still lies.
  Found while investigating `moving-in:G-011`, reported by nobody.
  - [H-046] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.18.0
  - Improvement: the report loop in `_cmd_find_nodes` should carry the resolver's
    `reason` instead of `null` — either as `{"unresolved": reason}` or by omitting the
    key and listing it once per query. Alternatively, let `_resolve_property_path`
    read components off Vector2/3/4, Color and Rect2, which is plainly what a caller
    writing `position.x` means; that closes the cause rather than the symptom.

**Validation run this turn:** `python tools/check_templates.py --full` — **OK**, 74/74
contract rows, including two new stage-5 lines that plant the defect they detect
([H-035]): `aabb measured the prop at 0.200 (the box), not ~10 (the OmniLight3D's
range volume); 1 node(s) excluded by name`, and `find_nodes --where int predicate
matched 4 (the numeric widening works), and the two empty results read differently`.
Stage 1.5 gained `no-VCS distinct from a real zero`, confirmed to FAIL when the fix is
reverted. `python -m unittest discover -s tools` — 17 OK. `python
tools/record_version.py --record` then `--check` — OK at `0.18.0`, 12 shipped files,
47 bus verbs + 49 CLI commands documented. `name_check.py` was touched this release,
so it was run against two real scaffolded projects per the false-positive rule
([H-030]): `../moving-in` and `../gather` both `errors: 0 | warnings: 0 | advisory: 2`,
unchanged from before the edit.

## 2026-08-15 — Measuring whether the harness helps: an A/B against a session without it (0.18.0)

Built `experiments/harness-ab/` and ran 8 real sessions (arm A = no plugin, arm B =
scaffolded + `--plugin-dir`), graded by a 16-check runtime oracle driven over the
bridge. Result: **no correctness gap** — 16/16 in all 8 sessions, both arms, on both a
build task and a 6-bug repair task — and a consistent ~1.7x cost difference against
arm B (repair: $0.96–1.23 vs $1.66–1.78, non-overlapping, 3/3). The cause is not that
arm B wasted effort: it used the bridge properly (73 launches, 745 verb calls, 20
verbs, own verbs registered through `devtools_ext/commands.gd`). It is that **arm A
built its own harness rather than skipping verification** — on the build task a
`tests/selftest.gd` with 51 runtime assertions plus a Vulkan screenshot pass. The
tasks were small and fully specified, so ad-hoc verification was cheaper than a
general tool. Report: `experiments/harness-ab/README.md`.

- Gap: **there is no non-LLM way to install the harness into a project**, so anything
  automated — CI, a benchmark, a grader, a scratch fixture — has to reimplement the
  installer. `/scaffold-godot-harness` is an LLM-driven slash command, and
  `tools/scaffold_install.py` covers only `files` and `config`: it does not wire the
  `DevTools` autoload, create `devtools_ext/`, seed `test/`, or place
  `CLAUDE.harness.md`. I wrote `experiments/harness-ab/setup_arms.py` to do those
  four steps, which means a second definition of "installed" now exists beside the
  slash command and will drift from it. `check_templates.py` has a third
  (`stage_assemble`).
  - [H-047] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.18.0
  - Improvement: add `scaffold_install.py full --project ROOT`, doing files + config
    + autoload + `devtools_ext/` + `test/` + `CLAUDE.md` merge, and have both
    `commands/scaffold-godot-harness.md` and `check_templates.py stage_assemble` call
    it instead of open-coding those steps. One definition of a complete install,
    testable directly, and usable by any automation.

- Gap: **the autoload insert has to know to go last, and every caller re-derives it.**
  Step 10 of the scaffolder documents that `DevTools=` belongs at the END of the
  `[autoload]` block so project autoloads the extension depends on are ready first.
  Writing that correctly in `setup_arms.py` took a fix after the naive version put it
  first — the rule is documented in prose, in one place, and is not shipped as code.
  Same root cause as [H-047].
  - [H-048] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.18.0
  - Improvement: fold it into the `full` mode above so the ordering rule exists once,
    as code, with a test that a project with its own `[autoload]` block ends up with
    `DevTools=` after its own entries.

- **Not a gap, recorded so the next session does not re-file it.** I was about to log
  "two projects with the same `config/name` silently share one `user://` bus" as a
  third gap, having avoided it by renaming each run's project pre-emptively. The
  harness already solves this and I simply never looked: `--session <id>` splices the
  id into all five bus filenames (`_build_bus_paths`, `dev_tools.gd:411`), it has its
  own **Parallel verification (`--session`)** section in `REFERENCE.md:772`, it is in
  `CLAUDE.harness.md:247`, and `devtools.py:249` already names this exact condition —
  *"two instances launched without --session"* — when a reply arrives stamped with a
  foreign pid. Textbook [H-033]: the `Improvement:` line I had drafted was written
  from a workaround rather than a diagnosis, and would have added a second isolation
  mechanism beside the working one. Checking it cost four minutes.

**Validation run this turn:** no `templates/` file was touched, so
`check_templates.py` was not run — templates unchanged since the last verified run.
`python tools/record_version.py --check` — OK at `0.18.0`, 12 shipped files, 47 bus
verbs + 49 CLI commands documented. The new work is confined to `experiments/`, whose
own oracle was validated by planting 11 defects into a known-good game and asserting
the judge failed exactly the right checks each time (11/11), per [H-035]; that suite
caught two real bugs in the oracle before it graded anything — reading `get_state`
properties from a nonexistent `data["properties"]` key, and ignoring a `set_state`
`success:false` that was correctly refusing a dotted write into a `Vector2`.

## 2026-08-15 — The A/B control was being told the answer (0.18.0)

Follow-up to the entry above. The first three experiments appended one sentence to
**both** arms: *"satisfy yourself that the game actually works when it runs — not
merely that the code looks correct. Use whatever means you have."* That is this
harness's thesis in plain English, handed to the arm meant to be ignorant of it, and
it is the likeliest reason 8 sessions showed no gap. Removed it and re-ran.

**With a naive control, the gap appeared.** Build task, n=1 each: arm A scored 15/16,
shipping `GameOverPanel` at 800x600 positioned `(-368,-268)` — the entire end-of-run
screen outside the viewport — while signing off *"Confidence: high … 70/67/79 checks,
0 failures … I checked the real windowed build via captured frames of every state,
including the game-over and all-clear panels."* Arm B: 16/16, $5.77 vs $5.62 (3%).
The control still built its own harness unprompted; what it lacked was an on-screen
assertion, which is exactly what a general tool carries and a hand-rolled one omits.

**The regression experiment found nothing.** Three features added by three fresh
sessions each on a shared 413-line base: zero regressions in either arm at every
step. The metric the experiment was designed around came back flat.

- **Correction to my own result, recorded because it nearly shipped.** Arm A's pause
  scored 3 feature-check failures that are *my oracle's fault, not its code*. It reads
  `ui_cancel` from both a polled `Input.is_action_just_pressed` and an event handler,
  deduped per frame — correct for a human, whose key press yields both on one frame.
  `input_tap` straddles frames and toggles twice, netting zero. Its own comment says
  it was written for exactly that case.
  - [H-050] status: open | seen: 1 | harness: 0.18.0
  - Improvement: `input_tap` on an action a game both polls and listens for is
    ambiguous, and nothing says so. Either press and release within one frame (so
    `is_action_just_pressed` and the dispatched event land together, as a real key
    does), or have the reply name the frames the press and release landed on, so a
    caller asserting a toggle can see it fired twice. A verb that silently produces a
    double-toggle reads as "the game's pause is broken".

- **Methodological note for anything measuring this harness.** The oracle was written
  in the harness's own vocabulary — node paths, properties, injected actions — which
  is the interface arm B develops against. On any check depending on that interface,
  arm B is structurally advantaged. [H-050] is that bias caught in the act, and it
  argues for at least one check per experiment expressed in a way neither arm's tools
  privilege.

**Validation run this turn:** no `templates/` change; `record_version.py --check` OK at
`0.18.0`. Oracle validated both directions before use — 11/11 planted defects with the
right signature, reference implementation of all 3 features 31/31, featureless base
exactly 16/31 (all 15 feature checks fail, all 16 base checks pass). 16 sessions,
$46.64.

## 2026-08-15 — Retraction: the one gap the A/B found was my oracle's, not the game's

The entry above reports a naive-control session shipping `GameOverPanel` at
`(-368,-268)`, "the entire end-of-run screen outside the viewport", as the single
correctness difference in 16 sessions. **That is wrong and is retracted.** Running the
same build **windowed** and screenshotting the game-over state shows a correctly
centred card ("TIME UP / 0 of 8 coins collected / Press R or SPACE to play again"),
and `get_node_bounds` on the running windowed game reports `{x:0, y:0, w:800, h:600},
in_viewport: true`. The player sees exactly what they should.

Across the whole study there is now **no measured correctness difference between a
session with the harness and one without**, on any task, with or without a
verification nudge.

- Gap: **`get_node_bounds` (and therefore `validate_ui`'s off-screen findings) can be
  wrong headless for a project using a stretch mode.** Both projects set
  `window/stretch/mode="canvas_items"` with `aspect="keep"`. Headless there is no
  window, so the stretch transform that `get_global_transform_with_canvas()` folds in
  is not the one a player gets, and a correctly-placed Control resolves to a rect
  outside `_screen_reference_rect()`. It reported `in_viewport: false` on a panel that
  is dead centre in the real game. The verb was believed, and it produced a false
  defect that reached a published report.
  - [H-051] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.18.0
  - Improvement: when `DisplayServer.get_name() == "headless"` **and**
    `ProjectSettings.get("display/window/stretch/mode") != "disabled"`, the reply must
    say so — either refuse the geometry (`success: false`, naming the stretch mode) or
    carry a `"geometry_trustworthy": false` flag plus the reason, so a caller cannot
    read an off-viewport verdict as fact. Same for the `ui_offscreen` findings in
    `validate_ui`. Silence here is the failure mode the whole project is written
    against: a well-formed number that is not measuring what the caller thinks.

- Method note: this was only caught because someone asked to *look at the game*. Eight
  judged runs, a planted-defect suite, positive and negative controls, and a published
  report all passed over it, because every one of them consumed the same headless
  geometry. A screenshot took two minutes and overturned the headline. `capture.gd`'s
  "must not be run headless" rule exists for exactly this reason and I did not extend
  the suspicion to `get_node_bounds`.

## 2026-08-15 — Acting on the A/B: lead with findings, report coverage, host the selftest (0.19.0)

Implemented the three beads the A/B study produced (`ctj`, `mjn`, `djq`), fanning the
two independent implementation tracks out to subagents and doing the doc inversion
inline.

- **`findings`** (bead ctj) — one bus verb running all five live checks (`ui_layout`,
  `ui_reachable`, `signal_unconnected`, `performance`, `scene_validation`) into one
  flat list, reusing each existing `_cmd_*` rather than re-deriving it. `signal_unconnected`
  is the only new logic. README and `CLAUDE.harness.md` now lead with it; the verb table
  is tiered to ten, with the rest named but pushed to `REFERENCE.md`.
- **`tools/coverage_check.py`** (bead mjn) — reports on the *checks*: eight defect
  classes, `UNCHECKED` with the cheapest cover or `COVERED` with the file:line that
  convinced it. Never opens the project, so it is parallel-safe like `name_check.py`.
- **`test_selftest.gd`** (bead djq) — the seed test renamed from `test_example.gd` and
  reheaded *add to this*, plus a `Suite: N test script(s)` line on every run and a
  `/verify` Phase 4 Step 5 that promotes durable checks into `test_dir`.

- Value: **warranted** — validation caught things review would not have.
  - Expected: the two subagent tracks would compile and I would spend the time on docs.
  - Got: `stage 1.6 coverage` fired on a mutated `coverage_check.py` with `fixture A
    reported ui_layout 'covered'. The docstring naming instantiate_ui/get_global_rect
    flipped it - comments are not coverage.` — the stage can fail, which is the only
    thing that makes its passing mean anything ([H-035]).
  - Found: **`coverage_check.py` credited the harness's own seed test as the project's
    coverage.** `moving-in` and `findmyballs` both reported `ui_layout` COVERED off
    `test_example.gd:42` — the shipped example, which really does call
    `_T.instantiate_ui()` and assert `ui.size`, on a two-node HUD it builds in code.
    Every freshly scaffolded project would have read as covered on day one, for a check
    about a scene the project does not own. This is precisely the false-COVERED class
    the tool exists to prevent, and the scratch fixtures could not see it — it took the
    real-project run the [H-030] rule requires. Fixed by keying the demotion on the
    enclosing *method* rather than the file, so a project that adds real tests to the
    seeded file still gets credit; all three real projects now cite their own UI
    (`UnpackPanel`, `BUSH_SCENE`) instead. A third `check_templates` fixture pins it.
    Also: a shipped em-dash inside a `print()` in `devtools.py:1804`
    (`the tree is paused — nothing actually advanced`), the only non-ASCII printed
    string in the file, found by a subagent hitting it on a Windows console. Fixed.
    Also: `record_version.py --check` caught eight CLI verbs the tiered cheat-sheet had
    dropped (`clear`, `drag`, `save-ui-baseline`, `sequence`, `tilemap-region`,
    `ui-snapshot-diff`, `validate`, `validate-all`) — the doc-coverage rule doing
    exactly its job against a deliberate doc shrink.
  - Cheaper: nothing, and the cheapest step was the highest-yield one. Running
    `coverage_check.py` against three real projects costs about fifteen seconds and is
    the only thing that saw the seed-evidence bug; 32 green `check_templates` stages,
    two subagents' own validation and a full read of the diff all passed over it. That
    is [H-030] restated with a second tool — the scratch project is synthetic and
    cannot measure what a checker does to a codebase it did not author.

- Gap: **untracked work in this repo is unprotected against a subagent fan-out.**
  `experiments/harness-ab/` — the A/B study that produced these three beads, untracked
  at session start — was deleted during a two-agent fan-out. Both agents were scoped in
  their prompts to a named file each, and `git status` afterwards confirmed neither had
  modified a file outside its scope, so the deletion came from a repo-wide command
  (a `git clean`-shaped one) rather than from an edit. Nothing recovered it: git had no
  copy because it was never committed, `git stash list` was empty, `git fsck
  --unreachable` held nothing relevant, and the Recycle Bin had no matching entry
  (a shell `rm` does not populate it). The study's *conclusions* survive in this log's
  previous entry; `run_ab.py`, `judge.py`, `blind_pack.py`, `setup_arms.py`,
  `report.py`, the fixtures and the seed do not.
  - [H-052] status: open | seen: 1 | harness: 0.18.0
  - Improvement: two lines, both cheap. (1) A `Gotchas that have already cost time`
    entry in `CLAUDE.md`: *never run `git clean`, `git checkout -- .`, or any repo-wide
    discard in this repo — `experiments/` and `.devtools/` are untracked by design, and
    a fan-out amplifies one such command into unrecoverable loss.* (2) A standing line
    in every fan-out prompt: *you may edit only the files named above; run no repo-wide
    git command of any kind.* Scoping an agent to a file list does not scope it away
    from `git clean`, and this session proved the gap the expensive way.

- Gap: **nothing enforces a subagent's declared file ownership.** The fan-out kept two
  tracks off each other's files by prompt convention alone, and the only check that it
  held was me reading `git status` afterwards. That worked, but it detects a collision
  after both agents have finished rather than preventing it.
  - [H-053] status: open | seen: 1 | harness: 0.18.0
  - Improvement: before spawning, `git stash create` (or a plain `git add -A` to the
    index) to give every untracked file a recoverable object, and diff the resulting
    tree afterwards to see exactly which files each track touched. One command before
    and one after, and it turns both this gap and [H-052] into recoverable events.

**Validation run this turn:** `python tools/check_templates.py` — OK, 33 stages, including
three new lines: `stage 1.6 coverage: fixture A -> unchecked, and the docstring trap did
not flip it` / `fixture B -> covered, evidence test/unit/test_hud.gd:4` / `the shipped
seed alone -> unchecked, with the seeded call named as a weak signal`, plus
`stage 5 bridge: findings ran 5 of 5 checks, 4 finding(s) incl. 3 ui_layout on the
planted defects; --no-scenes -> 4 checks with scene_validation named as skipped` and
`stage 4 tests: Suite: 1 test script(s) in res://test/unit (matches 1 on disk)`. Stage 1.6
was confirmed to FAIL before shipping by mutating `coverage_check.py` to report every
class covered — it printed `fixture A reported ui_layout 'covered'. The docstring naming
instantiate_ui/get_global_rect flipped it - comments are not coverage.` and the mutation
was reverted. `python -m unittest discover -s tools` — 17 tests OK.
`python tools/record_version.py --record` then `--check` — OK at `0.19.0`, 13 shipped
files, 48 bus verbs + 50 CLI commands documented. Per [H-030], `coverage_check.py` was
also run against three real scaffolded projects (`../gather` 1340 assertions/53 files,
`../moving-in` 448/19, `../findmyballs` 46/3) — which is what caught the seed-evidence
false COVERED described above.

## 2026-08-15 — Reflecting on "the tool was not useful": the top ten open issues, and what they had in common (0.20.0)

Asked to reflect on the A/B's null result and tackle the top ten of the open issues
(gh#5, #6, #7, #9, #10; beads dfj, e8g, 0nl, cki and the rest). Reading them together
against the study, one thing stands out: **the study found no correctness gap, and the
one difference it did find was the harness lying** ([H-051]). Every open issue is the
same shape — a verb or runner returning a well-formed answer that is not measuring what
the reader thinks (`--hide` that hid nothing, `performance` on a paused tree, a selector
blamed for a compile failure, a `quit` that reported a survivor that had exited). A tool
that cannot be trusted loses to the bespoke selftest the model writes anyway, because
at least *that* one is not believed blindly. So the ranking was: trust defects first,
then the thing that makes the measurement re-runnable ([H-047]).

Ten shipped, each with the defect planted as a gate where a gate can hold it:

1. **[H-051] headless geometry is now flagged**, and the diagnosis differs from the
   filed hypothesis. It is not the stretch transform — `get_global_transform_with_canvas()`
   is identity in both modes on the scratch project. It is `get_window().size`, which is
   `64x64` headless (`window_get_size()` even reports `0x0`): a panel centred with
   `(get_window().size - size) / 2` on 800x600 sits at exactly `(-368,-268)` headless and
   `(0,0)` windowed. Reproduced in a 40-line scratch before touching the verb ([H-033]).
   The verb was *right about the headless run*; what it lacked was saying so. Now
   `get_node_bounds` / `get_ui_snapshot` / `validate_ui` / `reachable_ui` / `findings`
   carry `geometry_trustworthy` + `geometry_caveat`, `findings` stamps `caveat` on
   geometry-code findings only, and the client prints it beside the numbers. Verdicts
   still gate (CI is headless). Stage 5 asserts the flag on the aggregate, on the
   planted `ScaledOutside` overflow, and NOT on `ui_transparent`; a mutation that
   returned `""` made both new checks fail (`geometry_trustworthy=True, caveat=''`).
2. **gh#5.1 `screenshot --hide` accepts `CanvasLayer`** and refuses (no file) when it
   can hide nothing. The positive control found a *second* bug: `--hide` awaited
   `RenderingServer.frame_post_draw`, which headless never emits, so any `--hide` hung
   the bridge for the client's whole timeout. Now `process_frame`. Sweep finding:
   `_is_effectively_visible` ignored a `CanvasLayer`'s own `visible`, so a hidden pause
   menu's buttons were "reachable" — fixed in the same walk.
3. **gh#5.2 the reply write is atomic** (temp + `rename_absolute`, direct-write fallback)
   and the client's two `unlink()`s ride out `WinError 32`.
4. **gh#6.1 `performance` says `tree_paused`**, message says `PAUSED`, client leads with
   `TREE IS PAUSED`; `/verify` Phase 2 gates on `ping` before Phase 3. Stage 5's paused
   check now asserts it both ways.
5. **gh#6.2 was not slow shutdown.** `pid_alive()` on Windows used `os.kill(pid, 0)`,
   which raises `WinError 87` for a **dead** pid; the tolerant `OSError -> True` read
   that as alive, so `quit` could never see the game exit and named a pid `tasklist`
   no longer had — measured here, 13s of "STILL ALIVE" on a game that had gone. The
   proposed grace re-poll would not have fixed it ([H-033] again). Now
   `OpenProcess` + `GetExitCodeProcess`; verified dead / foreign-live / own / child /
   killed-child.
6. **gh#7.1 / e8g `config --set` no longer reverts owned keys it was not passed** —
   reproduced on master (`godot_bin: "C:/fake/godot.exe" -> ""`), fixed, two unit tests
   plant it. Step 11 collapsed to one call and reads `godot_bin` back; tier 4 resolves a
   `#!` wrapper to its exec target and `cygpath`s MSYS paths (gh#7.2); step 12 imports
   before linting (gh#7.3).
7. **gh#10.1** the filed fix used `_discovery_errors`; those are the *unselected*
   failures (G-055). A selected script that fails to load goes to `_errors`, so a new
   `_selected_load_failures` drives the verdict. The stage-4 control failed on my first
   (issue-shaped) implementation and passed on the second — which is the point.
8. **gh#10.2** `/verify` Phase 0.5 tier (d): no `main_scene` → headless-only (forced),
   `runtime unreached`, ledger `--no-reach`, verdict `inconclusive`, with a mechanical
   check rather than recollection.
9. **[H-029]** `run_tests.gd` exits 2 naming `--import` when the class cache is absent
   (confirmed the file exists after import even with zero `class_name`s). Stage-4 control
   hides the file and asserts.
10. **[H-047]/[H-048] `scaffold_install.py full`** — one definition of installed: files
    → config (with `main_scene` detected) → `devtools_ext/` → `test/` seed → `CLAUDE.md`
    → log → `Stop` hook → autoload **last**. `SHIPPED_FILES` moved into it and
    `record_version.py` imports the list. Five unit tests, including a project with two
    autoloads asserting `DevTools` lands after both and nothing else moves.
    `check_templates.py stage_assemble` now builds its scratch through `full`, and the
    slash command's step 3 is one call with steps 4–10 kept as spec + fallback.

- Value: **warranted** — the controls did work the review would not have.
  - Expected: ten point fixes from ten well-written reports.
  - Got: three of the ten reports proposed a fix that would not have fixed the bug
    (H-051's stretch-transform hypothesis, gh#6.2's grace re-poll, gh#10.1's
    `_discovery_errors`), and each was found by planting the defect first. Two more
    bugs nobody had filed fell out of the plants (`--hide` hanging headless; a hidden
    `CanvasLayer` reading as visible).
  - Cheaper: the 40-second scratch probe before each fix, every time. It is the only
    step that told the filed hypothesis from the mechanism.

- Gap: **a `--hide` that hangs headless was invisible for four releases** because no
  stage sent the flag; the verb's own docstring advertised the case. Same class as
  [H-035]: a verb argument nobody drives in `check_templates.py` is untested, and the
  contract table only sends each verb's happy-path args.
  - [H-054] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.19.0
  - Improvement: shipped — `check_geometry_caveat_and_hide` drives `--hide` three ways.
    Remaining: an audit of which documented verb *arguments* the contract table never
    sends; that list is the next set of untested paths.

- Gap: **`pid_alive` was wrong on Windows since it was written**, and every survivor
  warning on this platform was noise. Nothing measured liveness against a known-dead pid.
  - [H-055] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.19.0
  - Improvement: shipped. A `tools/` unit test that spawns a child, kills it and asserts
    `pid_alive` flips would pin it without an engine — not written this turn.

- Gap (working practice, not code): **I killed Godot by image name** to clear a hung
  probe and took a second process with it — most likely another session's `moving-in`
  game, which was live on this machine at the time (a fresh one appeared 20s later).
  Nothing in the repo said not to.
  - [H-056] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.19.0
  - Improvement: shipped as a `CLAUDE.md` gotcha — kill the owner-file pid or the
    handle you hold, never the image.

- Not done, deliberately, and why: bead **mqy** (re-run the A/B on a weaker model) and
  **1hb** (regression-under-feature-addition) are the two beads that would actually
  answer "is it useful". They need the rig that [H-052] destroyed, and rebuilding it is
  its own session; `full` mode is the prerequisite it lacked. **[H-050]** (`input_tap`
  double-toggling a polled+handled action) is real and measurement-relevant but needs a
  frame-timing probe I did not want to rush beside ten other changes. Beads 2ih, ik8,
  1kh, 0hk and the H-02x/H-03x/H-04x concurrency and telemetry items are unchanged.

**Validation run this turn:** `python tools/check_templates.py` — OK, new lines:
`stage 2 assemble: ... (installed by scaffold_install.py full: 13 shipped files, config,
devtools_ext, test seed, CLAUDE.md, log, autoload)`, `stage 4 tests: uncompilable --file
target -> verdict names the compile failure, not the selector`, `stage 4 tests: no class
cache -> exit 2 naming --import (H-029 control)`, `stage 5 bridge: findings headless ->
geometry_trustworthy=false, 2 of 4 finding(s) carry the H-051 caveat (geometry codes
only)`, `stage 5 bridge: node-bounds headless carries the H-051 caveat; screenshot --hide
refuses a missing node and a Node3D by name, accepts a CanvasLayer`, `... performance
says tree_paused=True`. Confirmed to FAIL: the gh#10 control on the first
implementation, the `--hide` control on the pre-existing hang, and the H-051 checks
under a mutation returning `""` (both printed FAIL, mutation reverted). `python -m
unittest discover -s tools` — 24 tests OK (was 17). `python tools/record_version.py
--record` then `--check` — OK at `0.20.0`, 13 shipped files, 48 bus verbs + 50 CLI
commands documented. No static-analysis template changed, so no real-project
false-positive run was owed; the live-bus verbs were exercised on a scratch project only
(a real project's game was in use by another session).

## 2026-08-15 - Upstreamed 19 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`scaffold_install.py config` does not record `--set` values as scaffold-owned,
  so a later bare `config` call reverts them to the shipped schema default** — step 11 of
  `scaffold-godot-harness` issues two separate invocations by design:
  `--set godot_bin=<path>` then `--set godot_version=<ver>`. The second printed
  `^ godot_bin: "C:/Users/.../Godot_v4.7.1-stable_win64_console.exe" -> ""`, wiping the
  value the first had just written. Worked around by passing both `--set` flags in one
  invocation, which produced the correct result.
  - [plant-tower-defense:G-001] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.18.0 | source: plant-tower-defense 2026-08-15
  - Improvement: in `config`, write each `--set` key's value into `_scaffold_defaults`
    (so the key stays scaffold-owned at its *new* value rather than the schema default).
    Failing that, collapse step 11 into a single `--set godot_bin=… --set godot_version=…`
    call in the command doc.

- Gap: **Steps 5 and 6 copy `.gd` files with plain `cp`, bypassing the installer's `.uid`
  minting, and those paths are outside `uid_check_ignore`** — so the scaffold's own smoke
  check (step 12) reports 3 warnings on a *fresh* install. `uid_check_ignore` defaults to
  `["res://addons/", "res://tools/"]`, but the `cp`-copied files land in `res://devtools_ext/`
  and `res://test/unit/`, which lint does check. Step 4's summary explicitly promises `.uid`
  sidecars are minted for "every `.gd` the installer writes" — these three are not written
  by the installer. Worked around with
  `python tools/devtools.py new-uid --write devtools_ext/commands.gd devtools_ext/commands.example.gd test/unit/test_example.gd`
  (run per-file), after which lint reported `UIDs: OK`, 0 warnings.
  - [plant-tower-defense:G-002] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.18.0 | source: plant-tower-defense 2026-08-15
  - Improvement: route steps 5 and 6 through `scaffold_install.py files` (which already
    mints uids and skips existing ones) instead of `cp`, keeping the "never overwrite
    `commands.gd`" rule. `commands.example.gd`, `test_example.gd` and `smoke.json` are
    refreshable and fit the installer's normal path unchanged.

- Gap: **`run_tests.gd` blames the selector when the cause was a failed compile.** A test
  script that fails to parse is excluded from the discovered count, so a `--file` selector
  naming it "matches nothing" and the run ends on
  `SELECTED NOTHING - file 'test_sprite_style.gd' selected 0 of 3 discovered test(s) (exit 2)`
  followed by three lines of advice about how `--filter` matches method names. The real
  cause — `[ERR] res://test/unit/test_sprite_style.gd: Script failed to compile` — is
  printed above but is not what the verdict points at, and stderr above it was 60 lines
  of backtrace. Worked around by scrolling back to the `[ERR]` line.
  - [plant-tower-defense:G-003] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.18.0 | source: plant-tower-defense 2026-08-15
  - Improvement: track the count of scripts that failed to compile during discovery, and
    when it is non-zero prefer that in the final verdict —
    `SELECTED NOTHING - 1 test script failed to compile (see [ERR] above); selector
    'test_sprite_style.gd' cannot match a script that did not load` — before falling back
    to the selector-syntax advice, which is only correct when every script loaded.

- Gap: **the DEVELOPMENT RULE is unsatisfiable on a project with no main scene, and
  nothing says so.** `project.godot` has no `run/main_scene` and
  `devtools_config.json` has `"main_scene": ""`, so `/verify`'s runtime phases have
  nothing to launch; this change (art pipeline + tooling + tests) is real work that
  cannot reach a running game. Ran the three headless gates instead —
  `name_check` (errors 0), `lint_project.gd` (`Scripts: 9 compiled OK | UIDs: OK`,
  exit 0), `run_tests.gd` (`Total: 10 | Passed: 10 | Assertions: 54`, exit 0) — and
  am reporting that as "lint + tests green, runtime unreached", not as "verified".
  - [plant-tower-defense:G-004] status: fixed | fixed-in: 0.20.0 | seen: 2 | harness: 0.18.0 | source: plant-tower-defense 2026-08-15
  - Improvement: have `/verify` detect an empty `main_scene` up front and exit with an
    explicit *degraded* verdict — run Phases 1–2, skip 3–4, and still write the Phase 5
    ledger row with `reach: 0` and a `skipped: no main_scene` field — rather than leaving
    the caller to decide whether an unlaunchable project counts as a pass.

- Gap: **`find-nodes` locates a node but the auto-generated name is the only handle you
  get, and it is not stable across launches.** `find-nodes --class ChompFlower` returned
  `/root/Game/Entities/@Node2D@128`, which is what every follow-up `run-method` and
  `get-state` then has to be typed against; relaunch and it is `@Node2D@131`. Workaround
  was to re-run `find-nodes` before every read and paste the path back in by hand.
  - [plant-tower-defense:G-005] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.18.0 | source: plant-tower-defense 2026-08-15
  - Improvement: give `find-nodes` a `--call METHOD` / `--property NAME` pass-through that
    invokes the read on each match and reports it beside the path, so identifying a node
    and reading it are one command. `--property` already exists for properties; the
    missing half is a method call, which is the only way to read anything behind a getter.

- Gap: **`scaffold_install.py config` treats an empty shipped default as a proposal, so a
  refresh wipes `godot_bin`/`godot_version` before the step that re-detects them.** The
  template ships `"godot_bin": ""`, the key is scaffold-owned, and step 7 runs *before*
  step 11's binary detection — so every refresh transiently destroys a working recorded
  path. Here step 11 put it back, but on a machine where the detection globs miss (binary
  moved, `GODOT_BIN` unset) the refresh would leave the project with no binary at all,
  having deleted the one an earlier run had found. Workaround: re-ran
  `scaffold_install.py config --set godot_bin=… --set godot_version=…` by hand after
  detection.
  - [plant-tower-defense:G-006] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: in `cmd_config`, an empty/None shipped default is a placeholder, not a
    proposal — never let it clear a non-empty existing value unless `--set` names the key:

    ```python
            for key, value in proposed.items():
                if key not in merged:
                    ...
                    continue
    +           # An empty shipped default is a placeholder, not a proposal. It must not
    +           # clear a value a previous run detected and recorded (e.g. godot_bin).
    +           if (key not in overrides and value in ("", None)
    +                   and merged.get(key) not in ("", None)):
    +               owned.add(key)
    +               print("  = %s kept as %s (shipped default is empty - not a proposal)"
    +                     % (key, json.dumps(merged[key])))
    +               continue
    ```

- Gap: **the check that catches a dead-on-arrival feature is opt-in, so the default gate
  reports green on code that can never execute.** `--find-orphans` found `set_selected` in
  one run, but nothing suggests reaching for it: plain lint does not mention orphans exist,
  and `/verify` does not pass the flag. A method added in the same diff as its only reader,
  with no caller, is the signature of unfinished wiring — and it is invisible to every gate
  that runs by default.
  - [plant-tower-defense:G-007] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: run the orphan pass always and print the count as a denominator line
    (`Orphans: 0 of 24 script(s)`), the way `Shaders:` and `UIDs:` already report. Keep it
    non-gating — the heuristic has real false positives on callbacks — but a silent absence
    and a clean result should not look identical. Narrower alternative: gate only on a
    public method that is **new in the diff** and has no reference outside its own file,
    which is the unfinished-wiring case without the callback noise.

- Gap: **`cmd place_plant`'s devtools arg name doesn't match the intuitive `cell` used
  everywhere else in this project's own docs/tests, and a wrong key is silently ignored
  rather than reported.** `_cmd_place_plant` reads `args.get("x", 0)` / `args.get("y", 0)`;
  passing `{"plant":"...", "cell":[1,1]}` doesn't error, it just defaults both to 0 and
  reports success at `(0, 0)` — which reads exactly like "I placed it where I asked."
  - [plant-tower-defense:G-008] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: this is a project verb (`devtools_ext/commands.gd`), not harness core, so
    the actual fix belongs there: accept `cell: [x,y]` as an alias, or reject unknown keys
    when `x`/`y` are absent instead of defaulting silently. Noting it here because the
    *pattern* — a project verb accepting an args dict with no key validation — is generic
    enough that the harness's own `list-commands`/`cmd` help text could recommend project
    verbs assert `args.has(...)` rather than `.get(key, default)` for anything spatial.

- Gap: **`quit` reported the launched process as "STILL ALIVE 10s after quit" three times
  in one session** (pids 19132, 18560, and one earlier), every time requiring a manual
  `Stop-Process -Force` before the next `launch` would proceed. `taskkill /F /PID <n>` run
  through the Bash tool's MSYS path translation mangles `/F` into a phantom `F:/` path
  argument and fails outright — PowerShell's `Stop-Process -Id <n> -Force` is what actually
  worked.
  - [plant-tower-defense:G-009] status: fixed | fixed-in: 0.21.0 | seen: 5 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: on Windows, `devtools.py quit`'s own follow-up guidance
    (`taskkill /F /PID <pid>`) is wrong for an agent shelling out through a POSIX-translating
    bash — either quote/escape the flag in the printed suggestion, or print the
    PowerShell form (`Stop-Process -Id <pid> -Force`) as an alternative on Windows.

- Gap: **`godot --headless --path . --import` must be re-run after adding any new
  `class_name`-declared script, or every script that references it fails with
  `Could not resolve external class member` / `stale class cache` — and the failure mode
  cascades into completely unrelated files**, which reads like a broad regression rather
  than "the cache needs a refresh." Hit twice this session: once after adding
  `compost_meter.gd`/`husk_layer.gd`/`sunflower.gd`/`title_screen.gd`/`notebook_screen.gd`,
  and once more after editing (not even adding) `notebook_screen.gd`/`title_screen.gd` for
  the layout fix, which briefly looked like a *different* bug (`get_viewport_height()` not
  found) before `lint_project.gd`'s own `class_cache_stale` hint pointed at the real cause.
  - [plant-tower-defense:G-010] status: fixed | fixed-in: 0.21.0 | seen: 2 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: `lint_project.gd` already prints the `stale class cache` hint — the CLAUDE.md
    workflow section could say explicitly "after creating a new `class_name` file, run
    `--import` before the next lint/test pass" rather than leaving it to be rediscovered
    from the hint each time.

- Gap: **project verb arg names aren't discoverable from `list-commands` or `--help`** —
  `place_plant`'s actual keys are `plant`/`x`/`y` (read from `devtools_ext/commands.gd`
  source), but a first guess of `id`/`cell` (matching `Game.place_plant(id, cell)`'s own
  signature) was silently accepted and planted the *default* plant at the *default* cell
  instead of erroring on the unknown keys — `args.get("plant", "corn_cobbler")` treats a
  wrong key name identically to an omitted one. Cost two wasted calls before reading the
  handler source.
  - [plant-tower-defense:G-011] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: `list-commands` already enumerates registered project verbs by name;
    printing each verb's `args.get(...)` keys (grep-able straight out of the handler, no
    schema needed) alongside the name would remove the guess-then-read-source step for
    every project verb, not just this one.

- Gap: none — `spawn_pest --args '{"mutation": "hungry"}'` worked first try (arg name
  - [plant-tower-defense:auto-01d27c] status: open | seen: 1 | source: plant-tower-defense 2026-08-15
  guessed correctly this time, unlike `place_plant`'s `plant`/`x`/`y` in G-011).

- Gap: none this run.
  - [plant-tower-defense:auto-3fd0c9] status: open | seen: 1 | source: plant-tower-defense 2026-08-15

- Gap: **three separate live Godot processes were found still running simultaneously**
  (`Get-Process | Where-Object ProcessName -like "*Godot*"` showed pids started at
  6:02pm, 6:37pm and 6:39pm, all in this one session), after *every* `quit` in this
  session reported "STILL ALIVE" (G-009) and every follow-up `Stop-Process -Id <pid>`
  reported success. The pid `quit`'s warning names and the pid `Stop-Process` killed
  successfully is the **bus-answering engine pid** — but on Windows, `launch`'s own
  console-wrapper process (`..._console.exe`) spawns a separate child process that
  actually owns the window and answers the bus, and `Stop-Process` on the wrapper's own
  reported "Launched pid" does not reliably take the child down with it. Across 5+
  quit/relaunch cycles this session, that left a trail of zombie engines all still
  polling the same `user://` bus directory, which is exactly the "Crossed replies"
  failure mode named in the harness's own gotchas — it presented as newly-spawned pest
  nodes reporting "Node not found" seconds after a `scene-tree` call had just listed
  them (a `spawn_pest`/`scene-tree`/`set-state` triplet hitting three different engine
  instances). Fixed for the rest of this run with
  `Get-Process | Where-Object { $_.ProcessName -like "*Godot*" } | Stop-Process -Force`
  instead of killing one named pid.
  - [plant-tower-defense:G-012] status: fixed | fixed-in: 0.21.0 | seen: 2 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: `quit --wait` already detects "STILL ALIVE" — it could also print
    `Get-Process`-style guidance for Windows specifically (kill every process matching
    the Godot binary's name, not just the one pid it tracked as bus owner), since the
    pid it names is demonstrably not sufficient to guarantee a clean kill on this
    platform. Named it G-012 rather than folding into G-009, since G-009 is about the
    `taskkill`-vs-`Stop-Process` command form and this is about which pid to target at
    all — the two compound (a session hitting G-009 five times in a row is exactly the
    session at risk of also hitting this).

- Gap: **`find-nodes --class X` does not match a script `class_name`, only engine
  classes, and reports the miss as an empty result rather than as an unknown type.**
  `python tools/devtools.py find-nodes --class Pest` returned `0 node(s) matched:` with
  six live `Pest` nodes in the tree — they report `type: Node2D`, since that is their
  engine class. Worse, combining it with a predicate produces a *misleading* diagnosis:
  `find-nodes --class Pest --where mutation=hungry` printed
  `no candidate exposes 'mutation'`, which reads as "the property is wrong" when the
  truth is "the population was empty". Workaround was `--group pests`, which only works
  because this project happens to add its pests to a group.
  - [plant-tower-defense:G-013] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: resolve `--class` against the global class cache
    (`ProjectSettings.get_global_class_list()` / the script's `get_global_name()`) before
    falling back to `is_class()`, and when a `--class` value matches neither an engine
    class nor a registered `class_name`, exit 1 naming it as an unknown type instead of
    returning a clean zero-match. A predicate message must not be emitted for an empty
    candidate set.

- Gap: **`validate-ui`'s overflow check does not account for newlines or autowrap, so any
  multiline `Label` is a permanent false positive.** The game-over banner is a two-line
  Label; the check joined the lines and compared 1052px of text against an 896px box,
  gating a run over UI that a screenshot shows is correct. There is no way to express
  "this Label is multiline" short of baselining the node, which also suppresses real
  overflow findings on it forever.
  - [plant-tower-defense:G-014] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: measure per line. Split the Label's `text` on `\n` and compare the
    widest line against the box; when `autowrap_mode != AUTOWRAP_OFF`, compare
    `get_line_count() * get_line_height()` against the box *height* instead and skip the
    width test entirely, since a wrapping Label is supposed to exceed its width.

- Gap: **reach treats a base class as unreached whenever only subclasses are
  instantiated, which is silent and systematic rather than project-specific.**
  `verify_ledger.py reach` printed `NOT reached: game/selection_marker.gd` for a script
  whose `_draw_brackets()` ran on every frame of the session, because the only live node
  running it was a `PlacementPreview`. `reach_aliases` fixes it per-pair by hand, but
  that is a config declaration — the tool's own docs say an alias is "the project's
  claim, not this run's observation" — being used to paper over something the tool could
  observe on its own.
  - [plant-tower-defense:G-015] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: walk the `extends` chain. For every `script` path in the scene-tree
    snapshot, parse its `extends` (a `class_name` or a `res://` path) and credit the
    whole ancestry as reached, in a distinct `reached_base` bucket so it stays
    distinguishable from a directly-observed hit. That is a static read of files the tool
    is already opening, and it would have credited `selection_marker.gd` with no config
    at all.

- Gap: **`step-time` cannot isolate a short-lived state, because the wall clock keeps
  running between bus round trips — and nothing says so.** Observing a 4.5s husk expire
  while a 10s one survives means sampling inside a 5.5s window, but each
  `step-time` + read pair costs unbounded real game-time on top of the seconds
  requested. The reply is honest about what *it* advanced
  (`process_seconds: 1.008`) and silent about the ~0.5s of ambient time that elapsed
  around it, so the numbers look exact while the experiment is not.
  The workaround that did work is worth writing down:
  `set-state --node /root --property paused --value true`, then
  `run-method --node <the node> --method _process --args "[5.0]"` — the bridge answers
  while paused, so the system under test can be stepped by hand with zero ambient
  drift.
  - [plant-tower-defense:G-016] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: give `step-time` a `--paused` flag that pauses the tree, advances by
    calling the loop by hand, and restores the previous pause state — and have the reply
    include `wall_seconds_elapsed` alongside `process_seconds` either way, so the gap
    between "what I advanced" and "what actually passed" is visible instead of inferred.

- Gap: **a clipped Label silently hides its own overflow, and only `validate-ui` can
  tell you — but its finding for that case is indistinguishable from the false positive
  in [G-014].** Both arrive as `ui_text_overflow`. One meant "your readout is being
  ellipsised away, fix it" and the other meant "this Label is multiline, ignore me", and
  they were in the same run's output four lines apart. Triage was by eye.
  - [plant-tower-defense:G-017] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: split the rule. When `clip_text` is true or
    `text_overrun_behavior != OVERRUN_NO_TRIMMING`, the text is not overflowing its box,
    it is being *trimmed* — report it as `ui_text_trimmed` with the trimmed rendering
    quoted, since the consequence (the player cannot read it) and the fix (make room or
    shorten the string) are both different from an untrimmed overflow. Combined with the
    multiline fix already proposed in [G-014], `ui_text_overflow` would then mean exactly
    one thing.

## 2026-08-15 - Upstreamed 1 open gap(s) from harness-test-1 (harness 0.20.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\harness-test-1\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`ui_reachable` has no baseline mechanism, unlike `ui_layout`** — a shop list taller than the viewport (a `ScrollContainer`, by design) permanently reports its lower rows as `is interactive but cannot be hit: off screen`, and `findings --baseline-write` accepts `ui_layout` findings but leaves these 5 gating on every run (`By check: ... ui_reachable=5`, exit 1) with no way to mark them as "reachable via scroll, accepted".
  - [harness-test-1:G-001] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.20.0 | source: harness-test-1 2026-08-15
  - Improvement: either teach `ui_reachable` to recognize a `ScrollContainer` ancestor and treat a scrollable-but-off-screen control as reachable-via-scroll rather than unreachable, or extend the same NEW/PRE baseline mechanism `validate-ui` already has to the `ui_reachable` check.
  - Filed upstream: https://github.com/SeveralHerr/godot-selftest-harness/issues/16

## 2026-08-15 — Keeping the tool useful: gh#11–#16 and two project logs, ranked and shipped (0.21.0)

Asked to read the newest GitHub issues (#11–#16, all filed today from `/verify` runs on
`plant-tower-defense` and a fresh `harness-test-1`) and the project logs, pick the ten
things most worth doing to keep the harness *useful* — not treat them as a feature
queue — and ship. Ranking principle, carried over from the 0.20.0 reflection: **trust
defects first** (a verb answering a well-formed reply to a different question), then
whatever the logs show sessions paying for repeatedly (`plant:G-009` seen 5 times in one
day), then docs. Every `Value:` verdict across six project logs was tallied first: 13 of
90 are `overkill`, all on renames or pure-logic diffs, and the rest `warranted` — the
harness is earning its keep where it is used, so nothing here removes capability; the
work is in making what exists answer honestly.

Shipped, each with the defect planted as a gate:

1. **`find-nodes --class` matches a script `class_name`, subclasses included** (gh#15.2,
   `plant:G-013`). `is_class()` knows only engine classes; six live `Pest` nodes reported
   `type: Node2D` and matched nothing, and the empty result then diagnosed the `--where`
   predicate — sending the reader after a property name that was right. Now the script
   chain is walked by `get_global_name()`, an unknown class **fails** naming the project's
   classes, and an empty selector says the predicate was never evaluated. `clear_nodes`
   shares the matcher and the refusal. Stage 5 plants Critter/Elite; the mutation (chain
   walk off) printed `matched [], expected the base AND the subclass`.
2. **`validate-ui` measures a Label per line** (gh#15.1, `plant:G-014`) — a two-line
   banner rendering perfectly measured 1052px against 896px because `get_string_size()`
   lays `\n` out on one line. And, from `plant:G-017` pooled this turn: a `clip_text` /
   overrun Label is **trimmed**, not overflowing — a different defect with a different
   fix — so it is `ui_text_trimmed` now. Stage 5 plants both a TwoLines Label (must not
   fire) and a clipped one (must fire, as trimmed); the mutation (joined measure) fired.
3. **`ui_reachable` knows a `ScrollContainer`** (gh#16, `harness-test-1:G-001`). A row past
   the fold that lies within the scrolled content is `scroll_reachable` — counted and
   printed by `findings`, never a finding — and a row clipped by its container no longer
   reads as hittable. Chose (a) over the issue's suggested (b) baseline: a baseline would
   have accepted the rows and *also* hidden a genuine later unreachability behind them.
   Stage 5 plants a six-row shop across the bottom edge and asserts rows 1–2 hittable,
   3–6 scroll-reachable, count 4, zero gated, and the genuinely-off-screen `ScaledOutside`
   still flagged; the mutation (no ancestor walk) printed `on_screen Shop rows were
   ['Row1', 'Row2', 'Row3']`.
4. **The orphan scan runs by default and prints a denominator** (gh#11, `plant:G-007`):
   `Orphans: N of M public function(s) across S script(s) have no live reference`.
   Advisory, never gates, `--no-orphans` to skip. **The real-project run changed the
   design**: on `plant-tower-defense` it printed 31 lines and 13 were the harness talking
   about itself (`run_tests.gd`'s `assert_*` "referenced only from tests" — their job). An
   always-on scan that leads with the tool is one readers learn to skip, so `addons/`,
   `tools/` and `devtools_ext/` are excluded as *declarers* (still callers). Stage 4
   plants `game/harness_check_orphan.gd`; a first control keyed on the fixture's `ramp()`
   failed organically — `ramp` appears in `dev_tools.gd`'s curve docs and the scan is an
   identifier heuristic, which is exactly why it is advisory.
5. **`quit`/`launch` see every process this project launched, not just the owner**
   (gh#12, gh#14.1, `plant:G-009` ×5, `plant:G-012`). Mechanism, confirmed on this
   machine while writing this: a `_console.exe` launch is two OS processes (wrapper +
   engine child; `Get-CimInstance` showed pid 3616 console + 22156 engine for the plant
   worktree, identical start), and `launch.json`/the owner file each name one pid, so a
   game abandoned two launches ago fell out of both. Now `.devtools/launched.jsonl`
   records launcher and engine pids; `quit` sweeps it after the owner exits and
   `launch` warns before starting; **liveness is start-time verified**
   (`GetProcessTimes` / `/proc/<pid>/stat`) so a recycled pid is never named or killed;
   `quit --kill` / `launch --kill-survivors` terminate exactly those — never by image
   name ([H-056]). The Windows hint prints **both** `Stop-Process -Force -Id` and
   `taskkill /F /PID`, naming the MSYS symptom (`Invalid argument/option - 'F:/'`).
   Seven engine-free unit tests in `tools/test_devtools_client.py` plant a live child, a
   dead one, a recycled-pid record and an exclude+kill. What this does *not* do: explain
   why `get_tree().quit()` failed to exit those engines in the first place — that needs a
   repro on the project, and the report itself is ambiguous about the pids it killed.
6. **`list-commands` prints each verb's arg keys** (gh#14.2, `plant:G-011`, `G-008`):
   `place_plant  args: plant, x, y`, online and `--offline`, scanned from the handler
   body's `args.get/has/[]`. First cut attributed `reset_baseline` to `run_method` — the
   doc block *above* the next handler sits inside the previous body span; comment lines
   are dropped now, and the unit test carries that trap. Online `--json` shape changed
   (list → `{actions, args}`), documented.
7. **Reach credits base classes** (gh#15.3, `plant:G-015`): a node reports only the
   script attached to it, so a base whose only live instances were subclasses scored
   NOT reached while its `_draw()` ran every frame — in every project. `reached_base` /
   `reached_base_via`, credited by a static walk of each observed script's `extends`
   (path or `class_name`, via the class cache or a `class_name` scan). Stage 1.5 plants
   Marker ← Bracket (by name) ← Fancy (by path), observes only Fancy, and asserts both
   ancestors credited and a decoy base left unreached.
8. **Docs that would have saved a session**: `CLAUDE.harness.md` now says to `--import`
   after adding a `class_name` file *before* the next lint (gh#13, `plant:G-010` ×2 —
   lint's own hint names it only after the cascade), and to read the `Orphans:` line for
   a method the diff added.
9. **`full` mints `.uid`s for `devtools_ext/` and the test seed** (`plant:G-002`) — they
   land outside `uid_check_ignore`, so a fresh install failed its own step-12 smoke check
   with three warnings; unit test asserts the three sidecars, idempotency test still
   byte-stable. The slash-command fallback steps mint them too.
10. **`PURPOSE.md` gains a commitment — "Coverage is reported, not implied"** — the
    principle behind items 1, 3, 4 and 7 stated once: every pass names what it looked
    at, an advisory check runs by default and reports, and a verb that cannot answer the
    question asked says which. And a paragraph under *How to tell it's working*: it is
    also working when it declines (13 of 90 verdicts are `overkill`, all sessions that
    could tell).

- Value: **warranted** — three checks failed for reasons the reports did not predict.
  - Expected: ten point fixes from six well-written issues.
  - Got: the real-project lint run changed a design (harness self-noise, item 4); the
    orphan control's first anchor was itself a heuristic false negative; the arg-key
    scanner's first cut bled a neighbour's doc comment; and one gate run *appeared* to
    pass under mutation because the Bash tool's heredoc rewrites `\t`/`\n` escapes so the
    mutation script silently asserted-out — the checks were passing against unmutated
    code. Caught only because a later foreground run showed the traceback.
  - Cheaper: the 40-second real-project lint (item 4) and the 90-second unit test
    for the scanner. Neither needed the engine.

- Gap (working practice): **a mutation run whose mutation did not apply reads exactly
  like a passing control.** The recipe in the `harness-release` skill says "one-line
  edit … then run"; nothing in it asserts the edit landed. Here the edit was written
  through a shell heredoc that mangled escapes, the script raised, and the following
  `check_templates.py` ran the pristine file and printed every new check green.
  - [H-057] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.21.0
  - Improvement (shipped in the same release, `harness-release` SKILL.md): require `git diff --stat` (or a
    `grep` for the mutated line) **between** the edit and the run, and quote it in the log
    beside the FAIL line — a mutation is evidence only if it is shown to exist.

- Gap: **Godot invocations on this machine exited `0xFFFFFFFF` in three of seven gate
  runs today** — `--import` once (so the class cache never built and everything after
  cascaded), the windowed `capture.gd` once, and the bridge game inside `step_time`
  twice — while two other sessions' games were live. Each passed on re-run; nothing
  captures the crashing invocation's stderr, so the cause is unknown.
  - [H-058] status: fixed | fixed-in: 0.22.0 | seen: 3 | harness: 0.21.0
  - Improvement: `check_templates.py` should keep stderr for the `--import` and capture
    invocations and print the tail on a non-zero exit (it already does for the game), and
    the `note: --import exited …` line should be a FAIL when the class cache is then
    absent — the cascade that followed was reported as three unrelated stage failures.

- Not done, deliberately: `plant:G-005` (`find-nodes --call METHOD`) — `run-method` on
  the returned path is one more call and the auto-name churn it worries about is
  real but was not re-hit; `plant:G-016` (`step-time --paused` and `wall_seconds`) and
  `moving-in:G-039` (step-time rate discrepancy) — the verb already reports both clocks
  and the discrepancy needs the project to reproduce; the (b) baseline for
  `ui_reachable` from gh#16 — superseded by (a) for the reason in item 3. `plant:G-001`,
  `G-003`, `G-004`, `G-006` were already fixed in 0.20.0 (gh#7 / gh#10) and are marked so
  below.

**Validation run this turn:** `python tools/check_templates.py` — OK, new lines: `stage
1.5 reach: … base classes credited through an observed subclass by name and by path
(decoy stays unreached)`, `stage 4 lint: orphans 1 of 1 public function(s) across 12
script(s) (advisory, exit still 0; never_called_anywhere() named)`, `… --no-orphans
prints no Orphans line`, `stage 5 bridge: find_nodes --class matches a script class_name
(base finds the subclass too), refuses a typo naming the known classes, and an empty
selector does not blame the --where predicate`, `stage 5 bridge: TwoLines Label not
flagged while Overflowing is, as ui_text_trimmed (per-line measure; clip_text =
trimmed); Shop rows 1-2 hittable, 3-6 scroll-reachable (4 counted, 0 gated),
ScaledOutside still off-screen`. Confirmed to FAIL under mutation, each naming itself:
`find_nodes --class HarnessCheckCritter matched []`, `validate_ui flagged the two-line
Label 'TwoLines'`, `reachable_ui: on_screen Shop rows were ['Row1', 'Row2', 'Row3']`,
and the orphan control `0 of 0` before the game-side fixture existed. Real project:
`lint_project.gd` 0.21.0 on `plant-tower-defense` — exit 0, `Orphans: 31 of 110` before
the harness-path exclusion (13 self-noise), 18 game findings after, 4 of them methods
with no reference anywhere. `python -m unittest discover -s tools` — 31 tests OK (was
24). `python tools/record_version.py --record` then `--check` — OK at `0.21.0`, 13
shipped files, 48 bus verbs + 50 CLI commands documented.

## 2026-08-15 - Upstreamed 4 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **a second Godot from a sibling git worktree silently answers your bus, and
  nothing in the failure says so.** `launch` refuses a second instance *of the same
  checkout* by pid, but a worktree is a different directory with the same project name,
  so the guard does not fire and both processes poll the same
  `%APPDATA%/Godot/app_userdata/plant-tower-defense`. Errors arrive as
  `no Game in the tree` and `Root node not found`, i.e. as bugs in your own scene.
  `launch --isolated` fixes it but you have to already suspect the problem to reach for
  it, and its own banner says `user:// … (SHARED)` without saying what shares it.
  - [plant-tower-defense:G-018] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: have `ping` and `launch` compare the answering game's `res://` project
    path against the client's `--path`, and report a mismatch loudly
    (`the game answering this bus is running from <other path>`). The bridge already
    knows both. Failing that, make `launch`'s owner-file check key on project *path*
    rather than pid, so a worktree instance is detected as a second owner.

- Gap: **`set-state` on a typed Array property silently no-ops.**
  `set-state --node /root/Game/SeedBank --property unlocked --value '["corn_cobbler","sunflower"]'`
  reported success; the immediately following `get-state` returned
  `['corn_cobbler']`. No error, no warning, and the printed read-back in the
  `set-state` reply is the thing that is supposed to catch exactly this. A `Variant`
  Array cannot be assigned to an `Array[StringName]` in GDScript, so the write is
  dropped — but the verb reports as though it landed.
  - [plant-tower-defense:G-019] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: `set-state` already reads the property back; compare it against what
    was requested and exit 1 with both values when they differ. That is a general fix,
    not an Array-specific one, and it would also catch setters that clamp or ignore.
    Where the type is known (`Array[StringName]` via `get_property_list()`'s hint
    string), converting the parsed JSON array to the typed array before assigning would
    make the common case work rather than merely fail loudly.

- Gap: **`press` bypasses the input path, so hover state never clears** — pressing a
  button over the bus fires `pressed` directly, so a tooltip already open stays open and
  renders over the overlay the press just created. A real click cancels the tooltip as
  part of the mouse event; the bridge's press does not, so a screenshot taken after
  `press` can contain a popup a player would never see. Cost roughly fifteen minutes
  chasing a "tooltip bleeding through the notebook" bug that only exists under the
  harness. Workaround: none needed in the end — those tooltips were the wrong design
  anyway and their text moved into the button labels.
  - [plant-tower-defense:G-020] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: have `press` push a synthetic `InputEventMouseButton` through
    `Input.parse_input_event` when the target is under the pointer; failing that,
    document on the verb that hover/tooltip state is not cleared and that a screenshot
    taken straight afterwards may contain a stale popup.

- Gap: **orphaned instances fight over the bus, and the error does not say so plainly** —
  four Godot processes accumulated across a session of launch/quit/capture cycles. The
  symptom was not `game not running` but `Foreign instance on the bus: the reply to
  'scene_tree' came from pid 10584, but devtools_owner.json says pid 704 owns this bus`,
  raised part-way through a checker that had already made twenty successful calls, so
  half its measurements were from one process and half from another. Workaround: kill
  every Godot process from PowerShell and relaunch.
  - [plant-tower-defense:G-021] status: fixed | fixed-in: by 0.41.0 (launch ignores a dead owner - gather:G-112; survivors listed and killed via quit --kill / the launch ledger - gh#14.1, gh#24; found by 0.42.0's --triage) | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: `launch` already refuses when a live bus answers; it should also
    recognise a *stale* owner whose pid is dead and reclaim it, and grow a
    `launch --reap` that kills instances pointed at this project's `user://` before
    starting. A mid-run owner change should abort loudly rather than surface as one
    failed call among many.

## 2026-08-15 - Upstreamed 26 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`find-nodes --class` silently fails on script `class_name`s** — 
  `python tools/devtools.py find-nodes --class PauseMenu` → `0 node(s) matched:` while
  `scene-tree` showed `/root/House/UnpackUi/PauseMenu` with
  `script: res://scripts/ui/pause_menu.gd`. `--class` resolves through `Node.is_class()`,
  which only knows engine classes, so every script class in the project is a silent
  empty result. This is the worst possible failure shape for this verb: "not found" and
  "not a class I can see" are the same output, and the CLAUDE.md line for `find-nodes`
  ("Locate nodes by what they *are*") reads as though script classes work. It cost me
  three wrong conclusions about where the bug was.
  - [moving-in:G-017] status: fixed | fixed-in: 0.21.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: resolve `--class` against the script class cache as well —
    `node.get_script()` → `Script.get_global_name()` — and match either. Failing that,
    when `--class X` matches zero nodes AND `X` is not a known engine class, say so:
    `0 matched ('X' is not an engine class; script class_names are not searchable by
    --class — try --method or --group)`. An empty result that cannot distinguish
    "absent" from "unsearchable" is the one case worth spending a line of output on.

- Gap: **a headless lint/test run leaves a stale bus owner that refuses the next launch** —
  immediately after `run_tests.gd` exited, `python tools/devtools.py launch` failed with
  `Error: pid 23964 still owns this bus (devtools_owner.json). that process polled the bus
  3.1s ago, so it is live and listening`. That pid was the just-exited test runner —
  `tasklist` showed no such process. The `--script` runners load the DevTools autoload,
  which claims the bus and heartbeats, and nothing releases it on quit. Workaround: wait
  ~30s for the heartbeat to go stale and re-run `launch`, which then succeeds with
  `not a live owner (a recycled pid, or a game that stopped polling)`.
  - [moving-in:G-018] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: have `dev_tools.gd` delete `devtools_owner.json` on `NOTIFICATION_WM_CLOSE_REQUEST`
    / `_exit_tree()` when it is the owner. Cheaper alternative: skip the ownership claim
    entirely under `--script` mode (`OS.has_feature("headless")` plus no main loop scene),
    since a headless runner is never a bus a client wants to drive.

- Gap: **`set-game-speed` silently rounds to one decimal, and 0.04 becomes a freeze** —
  `python tools/devtools.py set-game-speed 0.04` replied `Game speed: 1.0 -> 0.0`. A
  0.0 time scale stops the game dead while the bus keeps answering, so every subsequent
  read returns well-formed, identical, completely stale values — the "a run that never
  changes is broken, not passing" failure, arrived at from a command that reported
  success. `0.1` works and `0.06` works, so the rounding is not a simple 1-dp clamp;
  whatever it is, 0.04 lands on zero. Workaround: use 0.06 or larger and read the
  echoed value rather than the requested one.
  - [moving-in:G-019] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: refuse a resolved speed of 0.0 outright — `set-game-speed 0` has no
    legitimate use through this verb (the bus already answers while paused, and `ping`
    reports `tree is PAUSED`), and a value that rounds to zero should be an error
    naming the smallest speed that works, not a silent freeze reported as a set.

- Gap: **no way to ask the harness what a single render feature costs** — the whole
  perf half of this turn was a hand-rolled loop: `find-nodes --class OmniLight3D`, then
  `set-state ... shadow_enabled false` per node, then `performance`, then set it back,
  repeated for `ssil_enabled`, `ssao_enabled` and `light_size`. The FPS counter swings
  ±3 between samples, so single readings are near-useless and I had to eyeball
  stability across repeats. Every number in this entry took four commands.
  - [moving-in:G-020] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: a `perf-ab --set NODE.PROP=VALUE [--frames N]` verb that samples FPS
    over N frames, applies the change, samples again, restores, and reports both means
    with their spread. The primitives all exist — this is a loop the harness should own
    rather than one every project rewrites in bash, and the mean-over-N is the part
    hand-rolling gets wrong.

- Gap: **`performance` reports one instantaneous FPS with no spread** — readings across
  this turn ranged 35 to 70 on identical builds depending on view and warm-up, which
  made the first shadow measurement (70 -> 37) look like a 47% regression when the
  controlled A/B says the true cost is 2 fps. I nearly gated a feature behind a video
  option on the strength of the bad number.
  - [moving-in:G-021] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: sample over a window and report `fps: mean 50.2, min 47, max 53, n=60`.
    A single frame's rate is not a measurement and presenting it as one invites exactly
    the wrong conclusion.

- Gap: **[G-021] `performance` reports one instantaneous FPS with no spread** —
  - [moving-in:auto-fd14cd] status: fixed | fixed-in: 0.22.0 | seen: 1 | source: moving-in 2026-08-14
  status: open | **seen: 2** | harness: 0.16.0. Bit again and worse this time: reading
  the three quality presets straight after switching them produced
  `HIGH 110 / MEDIUM 50 / LOW 105`, a non-monotonic ladder that would have led me to
  report that the High preset was the fastest one. The fix was `wait-frames 90` between
  the switch and the read, plus a second reading per tier to confirm it had settled.
  - Improvement: unchanged from the original entry — sample over a window and report
    `fps: mean 50.2, min 47, max 53, n=60`. Add to that: `performance` should note when
    the last N frames are still trending, because "the renderer has not settled" and
    "this is the frame rate" are indistinguishable in a single sample and the first one
    is what you get immediately after every settings change worth measuring.

- Gap: **`name_check.py` does not flag a script method that shadows an engine virtual
  with the wrong signature** — `func _set(action: Callable) -> void:` passed
  `name_check.py` clean (`errors: 0 | warnings: 0`) and then failed the import gate with
  `Parse Error: The function signature doesn't match the parent. Parent signature is
  "_set(StringName, Variant) -> bool"`. The checker already resolves engine class
  members from its API index, which is exactly the data needed: if a script declares a
  method whose name matches a virtual on any ancestor engine class, its arity and
  parameter types have to match too.
  - [moving-in:G-022] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: add a `virtual_signature_mismatch` error. The names most at risk are
    the short, tempting ones a UI file naturally wants — `_set`, `_get`, `_init`,
    `_process`, `_ready`, `_notification` — and the symptom is maximally confusing
    because Godot reports the failure against the *dependent* script, not the one with
    the mistake. Cheap to add given the index already exists, and it moves a failure
    from a 40 s engine round trip to a 2 s static one.

- Gap: **`raycast` is 2D-only and reports `clear` in a 3D project rather than refusing** —
  `python tools/devtools.py raycast --from 6.0,0.6 --to 6.0,-0.2` returned
  `clear from (6.0, 0.6) to (6.0, -0.2) on mask 4294967295 ... note that a ray STARTING
  INSIDE a shape reports nothing`, in a project whose physics engine is Jolt **3D** and
  where a wall demonstrably stands on that line. The verb takes `X,Y` pairs and queries
  `direct_space_state_2d`, so in a 3D game it can only ever answer "clear" — and it
  dresses that up with a plausible explanation ("a ray starting inside a shape reports
  nothing") which sent me looking for a geometry problem that did not exist. The
  CLAUDE.md table lists it among the general verbs with no dimensional caveat.
  - [moving-in:G-023] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: detect the project's dimension (a `Node3D`/`Node2D` root, or simply
    whether `World2D` has any bodies) and either refuse with
    `raycast is 2D-only; this project's main scene is 3D - use --from X,Y,Z --to X,Y,Z`
    or accept 3-component coordinates and query `direct_space_state_3d`. A verb that
    answers confidently in the wrong dimension is worse than one that is absent.

- Gap: **the bridge cannot pin the camera while the game holds the mouse** — after the
  fix I re-cast and got `normal {0, 1, 0}`, which looked like the fix picking the wrong
  axis. It was not: `debug_state` showed `"heading": -17.0, "pitch": -31.1` when I had
  set heading 0 and never touched pitch. The game had the cursor captured and my
  **physical mouse** had been steering the camera between commands, so the ray was
  pointing into the floor. Every `set_heading` I had issued was being overwritten within
  frames. Workaround: `capture_mouse(false)`, then `set_heading`, then `set-state _pitch 0`
  AND `set-state Camera.rotation 0,0,0` — the pitch lives in two places and setting one
  leaves the camera where it was.
  - [moving-in:G-024] status: open | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: this is the "a run that never changes is broken" hazard in reverse — a
    run that changes when nothing asked it to. Two things would close it: a
    `set-feature --ignore-os-input true` that makes the game drop real
    `InputEventMouseMotion`/`Key` while a bridge session is driving it, and a note in the
    Gotchas that a captured cursor means the desktop is a second, invisible input source.
    Any project whose camera reads relative mouse motion has this, and the symptom is a
    measurement that silently disagrees with the command that set it up.

- Gap: **[G-024] the bridge cannot pin the camera while the game holds the mouse** —
  - [moving-in:auto-abf843] status: open | seen: 1 | source: moving-in 2026-08-14
  status: open | **seen: 2** | harness: 0.16.0. Bit twice more this turn. Two matched
  A/B screenshots came back framed at completely different parts of the kitchen because
  the physical cursor moved between them, which makes a visual comparison worthless
  without any indication that anything went wrong. The working incantation is now four
  commands before every screenshot: `capture_mouse(false)`, `teleport_to_grid`,
  `set_heading`, `set-state Camera.rotation 0,0,0`.
  - Improvement: unchanged — `set-feature --ignore-os-input true`. Adding to the
    original note: a `screenshot --from GRID --heading D --pitch D` convenience would
    close it from the other direction, since "put the camera exactly here and capture"
    is what every visual regression check actually wants, and hand-rolling it is four
    commands that are easy to get subtly wrong.

- Gap: **every engine-side gate claims the bus, so none of them can run while another
  session drives the game** — [G-018] again from the other direction, and it deserves
  its own entry because the consequence is different. `lint_project.gd` is the only
  thing that compiles shaders, and it runs under `--script`, which loads the DevTools
  autoload, which takes ownership of `devtools_owner.json`. Running it while a colleague
  session has a live game would hijack their bus mid-command. So the one gate I actually
  needed — "does this GLSL compile" — was unavailable for the entire turn, on a change
  that was *nothing but* GLSL.
  - [moving-in:G-025] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: two things, and the first is nearly free. **(1)** `lint_project.gd`
    should not claim the bus at all — nothing in a lint run answers commands, so the
    autoload's ownership claim is pure cost. Gate the claim on the game actually
    running a scene. **(2)** Ship the isolated-compile trick as a flag:
    `lint_project.gd -- --shaders-only --isolated` copying `scan_root`'s shaders into a
    temp project with its own `.godot` and no autoloads. What I hand-rolled this turn
    was 30 lines and it is the right answer for any project where more than one agent
    might be working, which is increasingly the normal case. The check itself is the one
    already documented in the lint header: assign `Shader.code`, then
    `RenderingServer.get_shader_parameter_list()` returns `[]` on a compile failure —
    that works under the dummy driver, so it needs no renderer and no project.

- Gap: **`screenshot --hide` cannot hide a HUD, because a HUD root is a `CanvasLayer`
  and the verb matches `CanvasItem` only** — and it warns rather than failing, so the
  capture is written anyway and looks fine until you notice the HUD is still in it.
  ```
  $ python tools/devtools.py screenshot --filename hidetest.png \
      --hide /root/House/UnpackUi/GameHud --hide /root/House/UnpackUi/UnpackPanel
  WARNING: --hide/--hide-group matched no CanvasItem - the capture shows everything.
  Screenshot saved: .../hidetest.png
  ```
  This is the verb's headline use case — the docs sell it as "can't leave the HUD
  switched off" — and the one node type every HUD is rooted in is the one type it
  refuses. Worked around by writing `visible` on each layer with `set-state` before the
  batch and restoring it in a shell `trap`, which is exactly the un-restorable manual
  toggle the verb exists to prevent.
  - [moving-in:G-026] status: fixed | fixed-in: 0.20.0 | seen: 2 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: accept `CanvasLayer` in the `--hide` node walk. It has a `visible`
    property with the same semantics, so this is a type check widening, not new
    machinery. Failing that, make a `--hide` that matched nothing an **exit 1** — a
    capture that silently ignored the flag is worse than no capture, because it is
    presented as the thing that was asked for.

- Gap: **two devtools commands issued back to back can collide on the result file on
  Windows**, and the command reports failure after the game has already acted on it.
  ```
  $ python tools/devtools.py run-method --node /root/House/Player --method set_heading --args "[-20]"
  PermissionError: [WinError 32] The process cannot access the file because it is being
  used by another process: '...\moving-in\devtools_results.json'
  ```
  The heading had in fact been applied; only the read-back of the reply failed. That is
  the dangerous shape — a caller scripting a sequence sees an exception and cannot tell
  whether to retry, and retrying a non-idempotent verb is its own bug. Worked around with
  `sleep 0.25` between every call in `tools/capture_styles.sh`, which is guesswork
  standing in for a lock.
  - [moving-in:G-027] status: fixed | fixed-in: 0.20.0 | seen: 2 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: retry the result-file read on `PermissionError`/`WinError 32` for a few
    hundred milliseconds before giving up — Windows holds a brief exclusive lock while
    the game writes, and the file is complete moments later. A bare retry loop around the
    read in `devtools.py` closes it; the alternative (write to a temp name and `os.replace`
    game-side, which is atomic on Windows) is better still and fixes it for every client.

- Gap: **[G-025] every engine-side gate claims the bus** — status: open | **seen: 2** |
  - [moving-in:auto-c0be6f] status: fixed | fixed-in: 0.22.0 | seen: 1 | source: moving-in 2026-08-14
  harness: 0.16.0. `launch` immediately after the lint run was refused with
  `pid ... still owns this bus`, the lint runner having taken ownership on its way past.
  Same root cause as G-018, recorded again here because this is the second time in one
  session that the *ordering the workflow itself prescribes* produced the failure.
  - Improvement: unchanged — `lint_project.gd` should not claim the bus at all. Nothing
    in a lint run answers commands, so the ownership claim is pure cost and its only
    observable effect is breaking the launch that always follows it.

- Gap: **[G-025] every engine-side gate claims the bus** — status: open | **seen: 3** |
  - [moving-in:auto-bd25ef] status: fixed | fixed-in: 0.22.0 | seen: 1 | source: moving-in 2026-08-14
  harness: 0.16.0. Third time this session. This run it refused `launch` **twice** in a
  row, and `ping` diagnosed it as `that process EXISTS but last polled the bus 9s ago, so
  it is not listening: the tree is paused, the game is wedged, or the pid was recycled` —
  three plausible causes, none of them the actual one, which was the lint process on its
  way out still holding the claim. The message cannot name the real cause because the
  harness does not distinguish "a headless runner owns this" from "a game owns this".
  - Improvement: unchanged and now urgent enough to be worth doing before anything else
    in this log — `lint_project.gd` and `run_tests.gd` should not claim the bus. Failing
    that, the owner file should record HOW the owner was started, so `ping` can say
    `owned by a headless --script run, which does not answer commands; it will clear
    shortly` instead of listing three wrong guesses.

- Gap: **nothing looks for a signal the project emits and nobody connects** — the exact
  shape of this bug. `restart_requested` was declared, emitted from two call sites, and
  had zero listeners for the life of the project; lint validates scenes, `name_check`
  resolves names (the signal exists, so it resolves), and every test that touches the UI
  either ignores restart or connects the signal itself. The one gate that comes close is
  lint's `--find-orphans`, which reports public functions called only from tests — this
  is the same idea one step over.
  - [moving-in:G-028] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: extend `--find-orphans` to signals: for each `signal x` declared under
    `scan_root`, report it when the project emits it and nothing outside the declaring
    file connects to it. Advisory, like the function version, because a signal meant for
    an external host is a legitimate design (this project has one — `quit_requested`,
    which deliberately checks `has_connections()` before falling back). That distinction
    is exactly why it should be advisory and exactly why it is worth printing: the two
    cases look identical in the source and only one of them is a dead button.

- Gap: **input events cannot be synthesised into a specific node's handler** — [G-029].
  The bridge can drive actions (`input tap`), raw keys (`key`) and touch (`touch`), and
  all three go through `Input.parse_input_event` to the whole tree. There is no way to
  hand a specific node a specific `InputEvent`, and no way at all to produce an
  `InputEventMouseMotion` with a chosen `relative` — which is the single most common
  thing a first-person camera reads. `run-method --method _unhandled_input --args
  '[{"__type__":"InputEventMouseMotion"}]'` is silently a no-op: the arg cannot become
  an object, the call succeeds, and the state does not change.
  - [moving-in:G-029] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: a `mouse move --relative X,Y [--buttons N]` verb that builds a real
    `InputEventMouseMotion` and pushes it through `Input.parse_input_event`, matching
    what `key` already does for keyboard. Mouse-look is the one input a first-person
    game cannot be tested without, and it is the one the harness cannot produce. Second
    best, and cheaper: say so in the CLAUDE.md input section, so the next person extracts
    a testable method instead of spending a cycle discovering it.

- Gap: **`performance`'s orphan count is the only leak signal, and it misses in-tree
  accumulation** — the case above. A node parented to a live node is never orphaned, so
  a UI that adds a layer per visit, a pool that grows per spawn, or a list that appends
  per event all report `orphan growth +0` forever.
  - [moving-in:G-030] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: `performance` already prints `Total nodes:` — the gap is that nothing
    baselines or diffs it. Baseline it alongside orphans under `--reset-baseline` and
    gate on its growth the same way, and this would have been caught the moment the menu
    was opened twice. A stronger version: a `node-delta` verb reporting which node TYPES
    grew since the baseline, which turns "something is accumulating" into "three more
    CanvasLayers under UnpackUi".

- Gap: **`validate-ui`'s baseline is keyed on auto-generated node paths, so an unrelated
  insertion invalidates it wholesale** — this run reported `53 UI issues found (30 NEW,
  23 pre-existing)` where the previous session reported 0 NEW / 53 pre-existing. Same 53
  findings. Nothing in my diff touches UI. The findings are on runtime-built rows named
  `@VBoxContainer@465` / `@HBoxContainer@466`, and a parallel session's commit inserting a
  `TitleLayer` sibling renumbered them — so 30 baseline keys stopped matching and
  re-presented as NEW. A gate that fires on someone else's unrelated commit is a gate that
  gets waved through, which is the exact failure the baseline was added to prevent.
  - [moving-in:G-031] status: fixed | fixed-in: 0.22.0 | seen: 2 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: key the baseline on something stable across renumbering — the node's
    path with `@Type@NNN` segments normalised to `@Type@*`, or path-from-nearest-named-
    ancestor plus sibling index. Failing that, report `NEW (path changed, matched by
    rule+type)` as a third category so a renumber is visibly not a regression.

- Gap: **`sample-pixels` and `screenshot --region` disagree about what is at a given
  rect** — `sample-pixels --rect 350,470,90,90` reported `mean #99b470` (olive green);
  `screenshot --region 350,470,90,90` on the same frame, seconds apart, returned a crop
  of tan cabinet and dark line with no green in it, and no olive appears anywhere in the
  full frame. I could not resolve which coordinate space or colour handling differs, and
  abandoned `sample-pixels` for the run, measuring the saved PNGs with PIL instead. The
  cost was real: an A/B I ran through `sample-pixels` gave two readings that disagreed by
  more than the effect I was testing, and I spent several commands chasing a camera drift
  that turned out not to exist.
  - [moving-in:G-032] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: state the origin and colour space in the `sample-pixels` reply
    (`"origin": "top-left"`, `"space": "srgb"`) and make its rect interpretation
    identical to `screenshot --region`'s by construction — ideally one shared crop
    helper, so the two verbs cannot drift apart again. Until then the two should not be
    documented as interchangeable views of the same pixels.

- Gap: **an edited resource cannot be reloaded into a running session** — after editing
  `gouache.gdshader`, `style_set` re-`load()`ed it and got the version compiled at
  startup, because Godot's resource cache is keyed on path. The edit appears to have had
  no effect, which is indistinguishable from an edit that genuinely did nothing. Worked
  around with `ResourceLoader.load(path, "Shader", CACHE_MODE_IGNORE_DEEP)`, and every
  shader iteration before that cost a full quit/launch/dismiss-title/re-pose cycle.
  - [moving-in:G-033] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: a generic `reload --path res://...` verb that re-loads a resource with
    `CACHE_MODE_IGNORE_DEEP` and reports what re-loaded, so iterating on any resource a
    running game holds does not require a relaunch.

- Gap: **Phase 2 declares the game "up" without noticing the tree is PAUSED, and every
  later phase then measures a frozen game** — `entry_hook` is empty in this project's
  config, so /verify skipped the advance-past-the-menu step entirely and went straight to
  Phase 3. `performance` reported `FPS: 68.0` and `validate-all` reported `[OK] 2 scenes
  validated` against a tree that was not stepping. `ping` *does* say `tree is PAUSED
  (bridge still polling: PROCESS_MODE_ALWAYS)`, but nothing in Phase 2 asks you to read
  it, and I only found out at Phase 4 because `step-time` happened to warn
  `WARNING: the tree is paused - nothing actually advanced.` A whole phase of green
  results had already been recorded by then.
  - [moving-in:G-034] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: make Phase 2's post-launch check read the paused flag `ping` already
    returns — after the entry hook (or after launch, when no hook is configured), a
    paused tree should stop the run with "the tree is paused; set `entry_hook` to whatever
    dismisses the menu, because FPS, validate-all and every animation assertion below are
    meaningless on a frozen tree". The datum is already in the reply; only the gate is
    missing.

- Gap: **`quit` reports a survivor that has already exited, and tells you to kill a pid
  that is gone** — `python tools/devtools.py quit` printed `WARNING: pid 1440 is STILL
  ALIVE 10s after quit` with `taskkill /F /PID 1440` and exited 1. Seconds later
  `tasklist /FI "PID eq 1440"` returned `INFO: No tasks are running which match the
  specified criteria` and the taskkill itself returned `ERROR: The process "1440" not
  found`. The game had shut down; it just took longer than the 10 s default. The exit 1
  is the problem, not the wait: this project's Godot exits slowly enough to trip it on an
  ordinary run, and an exit code that cries wolf is one you stop reading — which is
  exactly the failure the code exists to prevent (a real survivor answering the bus
  alongside the next launch).
  - [moving-in:G-035] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: re-poll the pid once after the wait expires before declaring a survivor,
    and distinguish the two outcomes in the exit code — "gone, but took longer than
    `--wait`" is a note at exit 0, "still alive on re-poll" is the exit 1 that means
    something.

- Gap: **`scaffold_install.py config` blanks a scaffold-owned key to the template's
  static default the moment a call omits it**, when that key's *real* value only
  ever comes from being explicitly `--set` on a separate, later call. Step 7's call
  (`--set main_scene=... --set hud_layer_name=...`, no `godot_version`) rewrote
  `godot_version` from `"4.7.1"` (recorded by a prior scaffold run) to `""`. Cause:
  `patch_config` builds `proposed = dict(template)` then `.update(overrides)`, so a
  key absent from `--set` still enters `proposed` at the template's blank default;
  since `godot_version` was in the previous run's `owned` list and unchanged since,
  `scaffold_owns` is true and the blank "proposal" silently overwrites the real
  value. `godot_bin` escaped only because it happened to already be project-owned
  from an earlier manual edit — the same call would have blanked it too otherwise.
  Confirmed by re-running step 11's detection afterward, which restored it.
  - [moving-in:G-036] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-15
  - Improvement: in `patch_config`, don't seed `proposed` with template defaults for
    keys the caller didn't pass via `--set` this invocation — only compare/merge keys
    actually present in `overrides` (plus genuinely new schema keys, via `template`
    minus `existing`). A key nobody supplied this run should never be treated as
    "scaffold proposes blank," regardless of ownership. Proposed diff:
    ```python
    # before
    proposed = dict(template)
    proposed.update(overrides)
    ...
    for key, value in proposed.items():
    # after
    new_keys = {k: v for k, v in template.items() if existing is None or k not in existing}
    proposed = dict(new_keys)
    proposed.update(overrides)
    ...
    for key, value in proposed.items():
    ```
    (When `existing is None` — first-ever install — behavior is unchanged: every
    template key is still proposed.)

- Gap: **`step-time`'s reported rate does not match the actual `_process` delta
  accumulation**, or the two disagree in a way this session did not get to the bottom
  of. Two isolated 1s-scale windows measured on the SAME spinning fixture (rate =
  16 deg/s by design) gave *different* apparent rates: ~44.5 deg/s over one
  `step-time --seconds 3` window and ~95.9 deg/s over a following
  `step-time --seconds 1` window (computed from `rotation.y` deltas against the
  tool's own reported `process time`). Both are wrong, and they disagree with each
  other, which rules out a single fixed multiplier. Not investigated further this
  session — the qualitative claim (does it spin at all, does it keep spinning) is what
  `moving-in-ad0` needed, and that was answered cleanly; only the quantitative rate
  reading is suspect. Left as a live discrepancy rather than a diagnosed root cause: it
  could be `_process` firing at an idle-frame rate decoupled from the 60 Hz physics
  count `step-time` reports, `rotation.y`'s Euler read-back wrapping non-linearly
  across ±π, or something in how `step-time` drives frames under `--mute --headless`-
  adjacent launch. Any test that needs an exact real-time rotation *rate* (not just "did
  it move") should not trust `step-time`'s printed duration as the actual elapsed
  `_process` time until this is root-caused.
  - [moving-in:G-039] status: open | seen: 1 | harness: 0.19.0 | source: moving-in 2026-08-15
  - Improvement: reproduce with a minimal counter script (`_process(delta): count +=
    1; total_delta += delta`) driven the same way, and compare `total_delta` against
    `step-time`'s own reported `process_seconds` — if they disagree, the bug is in
    `step-time`'s frame-driving loop, not in this project's script.

- Gap: **a large `scene-tree` reply piped into `head` wedges the game.**
  `python tools/devtools.py scene-tree --depth 4 | head -50` printed fine, and every verb
  after it returned `game not running: 'find_nodes' was never picked up ... that process
  EXISTS but last polled the bus 14s ago`. The game had to be killed and relaunched; the
  same `scene-tree --depth 3` on a fresh launch worked perfectly, so the reply itself was
  never the problem — closing the pipe mid-write is. This is a footgun for exactly the
  advice the harness gives (prefer `scene-tree` over a screenshot), since the first thing
  anyone does with a 1000-node tree is pipe it somewhere narrower.
  - [moving-in:G-040] status: fixed | fixed-in: 0.22.0 | seen: 1 | harness: 0.19.0 | source: moving-in 2026-08-15
  - Improvement: have the client write large replies to a file and print the path (as
    `screenshot` already does), or catch `BrokenPipeError` in `devtools.py`'s output path
    and finish draining the result file before exiting — either one keeps a truncated
    read from leaving the bus in a state the game cannot recover from.

## 2026-08-15 — 0.22.0: it shares the machine — the top ten from 27 newly pooled gaps

Ranked from `moving-in` G-017..G-040 (26 entries pooled for the first time this turn),
`plant-tower-defense:G-018`, `harness-test-1:G-001` (= gh#16, shipped in 0.21.0) and this
repo's H-058. gh#11–16 were all closed by 0.21.0 and nothing newer is filed, so this turn is
the project logs' turn. Two themes carried nine of the ten items: **several sessions,
agents and worktrees share one machine** (a bus that assumes it is alone hands out
plausible wrong answers), and **a number is a measurement only with its spread**. Both are
now `PURPOSE.md` commitments (*It shares the machine*; the spread paragraph under *Gate on
the number that means something*), and the "Not concurrent" non-goal is narrowed to
one bus — several buses on one machine is the supported case now.

Verified before building, per [H-033]: `moving-in:G-017` (find-nodes --class), `G-026`
(screenshot --hide on a CanvasLayer), `G-027` (WinError 32 on the result file), `G-034`
(Phase 2 paused gate), `G-035` (quit re-poll) and `G-036` (config blanking) were already
fixed in 0.20.0/0.21.0 under gh#5/6/7/15 — the moving-in log was written on 0.16.0 —
and are marked so below rather than rebuilt ([H-044] again: six of 26 pooled entries were
open under one id and fixed under another).

1. **A `--script` instance is passive on the bus** (`moving-in:G-018`, `G-025` ×3). The
   mechanism, read from `_ready()`: every runner brings the autoload up, and its
   `_ready()` ran `_clear_stale_files()` (deleting the live game's owner, command AND
   result files) then `_write_owner_file()` with its own pid — so a headless lint in one
   session hijacked a colleague's game mid-command and, once the runner exited, refused
   their next `launch` for 30 s with a dead-pid owner. `_is_script_run()` reads
   `--script`/`-s` off the engine args; a passive instance registers handlers (tests may
   call them in-process), never touches a bus file, never polls, never heartbeats.
   Stage 4 plants a live-looking owner record and an in-flight command in the scratch
   user dir before both runners and asserts both survive byte-for-byte.
2. **The owner file and `ping` carry `project_path`** (`plant-tower-defense:G-018`). A
   worktree sibling shares the project name, so the same `user://` and bus; its game
   overwrites the owner record with its own pid and from then on the reply-pid check is
   satisfied by the wrong game. The client compares the owner's path to its `--path`
   *before writing the command* and raises `ForeignInstanceError` naming both checkouts;
   `launch` says the same in its refusal; `ping` prints the path and flags a mismatch.
   Four engine-free unit tests, one of which asserts the command file was never written.
3. **`performance` measures FPS over a window** (`G-021` ×2, `G-020`): `--frames N`
   (default 30) → `fps` mean, `fps_min/max`, `fps_samples`, `fps_window_sec`,
   `fps_instant`, `fps_settling` when the halves disagree >15%; `findings` uses it too.
   `set-game-speed` refuses a scale below 0.01 and prints 3 dp (`G-019`: `0.04` echoed as
   `1.0 -> 0.0`). Not built: `perf-ab` — `set-state` + `performance --frames` is the loop,
   and the part hand-rolling got wrong (mean-over-N) is the part that shipped.
4. **`raycast` is 2D or 3D by arity** (`G-023`): `[x,y,z]` queries `direct_space_state`
   3D with `layer_names/3d_physics`; `data.space` says which; a 2D ray on a tree whose
   only colliders are `CollisionObject3D`s is refused naming `--from X,Y,Z`. Counted from
   the tree, not the physics monitors — a static body is not an "active object". Stage 5
   plants a Wall2D and a Wall3D, hits each in its space, and removes the 2D wall to see
   the refusal fire.
5. **`validate-ui`'s baseline survives auto-name renumbering** (`G-031` ×2): keys
   normalise `@Type@NNN` → `@Type`, with multiplicity (three accepted rows under one key
   stay accepted, a fourth is NEW); pre-0.22.0 baselines are normalised on read. Stage 5
   frees and rebuilds an auto-named holder (0 NEW), then adds one more broken row (1 NEW).
6. **`performance` reports in-tree node growth** (`G-030`): `node_baseline` /
   `node_growth` sampled with the orphan baseline, `--by-type` → `node_types_delta`.
   Stage 5 parents 7 nodes and asserts growth +7, `Node2D +7`, orphan growth 0.
7. **`mouse-move --relative DX,DY [--steps N]`** (`G-029`): a real
   `InputEventMouseMotion` through `Input.parse_input_event`. Stage 5's fixture counts
   `_unhandled_input` motion events and reads the last `relative` (10, −2 from 40,−8 in
   4 steps). The first cut failed there organically — `screen_position` is not a property
   of `InputEventMouseMotion` in 4.7 and the handler aborted with an empty reply.
8. **`reload res://path`** (`G-033`): `CACHE_MODE_REPLACE`, and when the loader hands back
   a new object (shaders, binaries) the stored properties are copied onto the cached
   instance so holders see the edit. Stage 5 rewrites a held `.tres` (11 → 23) and a held
   `.gdshader` (0.5 → 0.75) and reads both back through the fixture. GDScript has no
   `ResourceCache`; a REUSE load of a cached path is the cached instance.
9. **`name_check.py` gains `virtual_signature_mismatch`** (`G-022`): the index now carries
   `virtuals` (`is_virtual` methods with `[required, total]` arity) and a hand table for
   `Object`'s script-level virtuals (`_set`, `_get`, `_notification`, …), which are **not
   in `--dump-extension-api`** — the first cut passed every scratch stage and named
   nothing, because `_set` was simply absent from the dump. An index without the table
   reports the check SKIPPED and `--refresh-api` regenerates it. Stage 2.5's planted file
   carries the verbatim `func _set(action: Callable)` plus three negative controls (a
   correct `_process`, an extra-optional-arg `_notification`, an inner class's
   `_process`) and asserts the rule names `_set` only. **Real projects**: 0 findings across
   plant-tower-defense (39 scripts), moving-in (57), findmyballs (32), gather (174).
10. **Lint's orphan scan covers signals** (`G-028`): `Signals: N of M declared signal(s)
    have no listener anywhere` — declared, emitted, connected by no other game file and
    no scene, tests not counting as the game hearing it. Advisory. Stage 4 plants a
    dead button and a heard control. (The runtime `findings` check `signal_unconnected`
    already existed for live nodes; this is the headless, no-game counterpart.)

Also: `sample-pixels` states `origin`, `space`, `image_size`, `same_image_as_screenshot`
(`G-032` — same image, same `Rect2i` as `screenshot --region`, by construction; the
reported disagreement was on 0.16.0 and did not reproduce by reading); `devtools.py`
exits quietly on `BrokenPipeError` (`G-040` — the client half; whether the *game* still
wedges after a truncated pipe needs a project sighting); `check_templates.py` keeps
`--import`'s and the windowed capture's stderr and FAILs when `--import` leaves no class
cache ([H-058]). And gh#17, filed mid-turn by the session that landed 0.21.0 on master while
this one was building 0.22.0: the `harness-release` skill gains §6b (land on master in a
throwaway worktree, assert tree-hash equality with the release commit and a stamp-clean
`--check` before pushing), its §6 block pushes the release branch rather than `master`, and
§0a refreshes the index before trusting `git status` — the count moved under exactly this
pair of concurrent sessions.

- Not done, deliberately: `moving-in:G-024` (`--ignore-os-input`) — needs a way to tell
  synthesized events from OS ones inside `_input`, and the cheap half (say so in the docs,
  and `mouse-move` warns when the cursor is captured) shipped; `G-039` (step-time rate)
  — still needs the project's repro; the `first-frame` verb from moving-in's *second*
  `G-027` — the id collision means it pooled as one entry under the fixed one, so it is
  re-filed here as [H-059] below rather than lost.

- Value: **warranted** — three checks failed for reasons the reports did not predict, and
  one real-project run changed nothing only because it was run.
  - Expected: ten point fixes from a well-written log.
  - Got: `_set` absent from the extension API (item 9) — a check that would have shipped
    reporting clean forever; `screen_position` not a property (item 7); a `ResourceCache`
    that GDScript does not have (item 8, caught by the parse stage before the bridge).
  - Cheaper: the parse stage (40 s) for item 8; the 15-second real-project runs for item 9.

- Gap: **moving-in's second `[G-027]` (a `first-frame` verb: what can the player see on
  frame N — visible CanvasLayers, topmost Control, paused, cursor mode) was lost to an id
  collision.** `upstream_gaps.py` keys on id, so two entries with one id pool as one and
  the second is silently the one that is not there. The idea itself is good — it is the
  one state every game has and no check looks at.
  - [H-059] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.22.0
  - Improvement: `upstream_gaps.py` should detect a duplicate id in the *source* log and
    append both, suffixing the second (`G-027b`) and saying so, rather than pooling the
    first and dropping the second. Then build `first-frame`.
  - Update (0.23.0): **the lost idea half is shipped** — `first_frame` is built
    (`dev_tools.gd` `_cmd_first_frame` / `devtools.py first-frame`). The pooling bug
    itself is not: `upstream_gaps.py` still drops the second of two source entries
    sharing one id, silently. This gap stays `open` for that half; do not read the
    verb's existence as the id-collision bug being fixed too — the earlier version of
    this line claimed exactly that, and it was wrong.

- Gap: **`performance`'s `fps_max` is meaningless headless** — 58823 fps from a 17 µs
  frame, because a headless frame does no work and waits for nothing. Harmless (min and
  mean are the numbers a gate reads) but the line reads as broken.
  - [H-060] status: fixed | fixed-in: 0.23.0 | seen: 1 | harness: 0.22.0
  - Improvement: cap or annotate `fps_max` when `DisplayServer.get_name() == "headless"`,
    the way geometry findings already carry the headless caveat.

**Validation run this turn:** `python tools/check_templates.py` — OK (fourth run; runs
one to three failed as described above), new lines: `stage 2.5 names: … virtual_signature_mismatch names _set only`, `stage 4 lint: signals 1 of 2 unheard (harness_dead_button named, harness_heard_signal not)`, `stage 4 runners: a planted owner record and in-flight command survived both --script runners untouched (passive bus)`, `stage 5 bridge: validate_ui baseline survives auto-name renumbering (0 NEW), and one extra auto row is exactly 1 NEW`, `stage 5 bridge: ping/owner carry project_path = the scratch; the same game read from another --path is reported foreign`, `stage 5 bridge: performance --frames 24 -> mean 157.5 in [143.5, 58823.5] over 0.15s, --frames 0 instantaneous; 7 in-tree nodes -> node_growth +7, by-type Node2D +7, orphan growth 0`, `stage 5 bridge: set_game_speed 0.0 refused (names the floor), 0.04 applied as 0.04, restored`, `stage 5 bridge: raycast 3D hits Wall3D (space 3d), 2D hits Wall2D, mixed arity refused, 2D on a 3D-only tree refused naming X,Y,Z`, `stage 5 bridge: mouse_move 40,-8 in 4 steps reached _unhandled_input 4 times, last relative (10, -2)`, `stage 5 bridge: reload updated a held .tres (font_size 11 -> 23) and a held .gdshader (0.5 -> 0.75) in place; missing path refused`.
Confirmed to FAIL under mutation - eight one-line mutations in one run (mutation script asserted each anchor once; `grep -c` of the mutated lines printed `5` and `1` between the edit and the run; both files restored byte-exact against a pre-mutation copy, `cmp` OK), each check naming itself: `signal scan reports 0 of 2`, `a --script runner touched the bus it does not own (moving-in:G-018/G-025): owner file REWRITTEN` (lint AND tests), `after renumbering the auto-named holder, findings must stay pre-existing: new=1 pre=4 of 5; NEW paths ['/root/Main/@VBoxContainer@9/@Button@8']`, `ping.project_path should name the scratch project, got None`, `7 nodes parented under a live node must show as node_growth 7, got 0`, `set_game_speed 0.0 must be refused naming the floor, got 'Game speed: 1.000 -> 0.000'`, `a 2D raycast on a tree whose only colliders are 3D must be refused`; the reload mutation (REPLACE -> REUSE) in a second run: `after reload the HELD LabelSettings should read font_size 23, got 11`. The virtual-signature and mouse_move checks failed organically before they passed (items 7 and 9), and the first mutation run showed the reload property-copy branch is never taken on 4.7 - the shader loader re-parses in place too - so that branch stays as a fallback for loaders that do not.
`python -m unittest discover -s tools` — 35 tests OK. Real-project `name_check.py`
runs as in item 9. `python tools/record_version.py --record` then `--check` — OK at
`0.22.0`.

## 2026-08-15 - Upstreamed 4 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **a confirm window measured in seconds is shorter than a handful of bus
  round-trips, and nothing in the reply says the state expired** — the window is
  `Game.UPROOT_CONFIRM_SECONDS = 4.0`. Arming it with `press` and then reading the
  button took two calls at ~1s each, so `run-method --method has_theme_color_override`
  returned `Result: false` and `get-state --property text` returned `Uproot (+6)`,
  both well-formed answers describing a state that had already lapsed. A second
  attempt read `_uproot_left: 0.0` *after* a press, which looked like the press had
  failed when in fact it had committed an arming left over from the previous block.
  Workaround that worked: `python tools/devtools.py set-game-speed 0.02` (clamped to
  `Game speed: 1.0 -> 0.0`), which freezes the countdown while the bridge keeps
  answering, then `set-game-speed 1.0` to watch the expiry land.
  - [plant-tower-defense:G-022] status: fixed | fixed-in: 0.23.0 | seen: 1 | harness: 0.19.0 | source: plant-tower-defense 2026-08-15
  - Improvement: a `with-time-frozen` flag on `press`/`run-method` that pins
    `time_scale` to 0 for the duration of the call, or — cheaper — document
    `set-game-speed 0` as the standard technique for observing any state with a
    lifetime shorter than a few seconds. `step-time` already exists for advancing
    time deterministically; the inverse (hold it still while I look) is the missing
    half, and every short-lived cue — a combo window, a hitstop, an i-frame, this
    confirm — hits it.

- Gap: **`scaffold_install.py detect_main_scene()` does not resolve a `uid://` main scene**
  — `project.godot` here holds `run/main_scene="uid://ce2dtga2f08e"` (what the Godot 4.4+
  editor writes by default). The installer's regex returns that string verbatim, so
  `full` printed `[full] detected: main_scene=uid://ce2dtga2f08e` and would have written a
  `uid://` into `devtools_config.json` on a **fresh** install. This project only escaped it
  because `main_scene` was already project-owned as `res://game/title.tscn`. The scaffold
  doc compounds it: step 7 tells the agent to "open the main scene" to detect
  `hud_layer_name`, which cannot be done from a uid without the same resolution step.
  - [plant-tower-defense:G-024] status: fixed | fixed-in: 0.23.0 | seen: 1 | harness: 0.21.0 | source: plant-tower-defense 2026-08-15
  - Improvement: in `detect_main_scene()`, when the value starts with `uid://`, grep the
    project's `*.tscn` headers (`uid="uid://…"`) and `*.uid` sidecars for the id and return
    the owning `res://` path; fall back to the raw uid only if nothing matches, and say so.

- Gap: **a subagent has no parallel-safe way to compile what it writes** — the one
  gate documented as concurrency-safe is `name_check.py`, and it says of itself
  `NOT COVERED: a clean name_check resolves names, it does not compile the file`.
  So the agent implementing `project_identity` shipped a handler and four test
  methods that had never been parsed by the engine and never executed; it reported
  this honestly and worked around it by porting `_git_identity` line-for-line to
  Python and running that against the repo instead. That workaround happened to be
  sound, and is not one the next agent should have to invent.
  - [plant-tower-defense:G-025] status: fixed | fixed-in: 0.27.0 | seen: 1 | harness: 0.21.0 | source: plant-tower-defense 2026-08-15 | dup-of: gh#20.1
  - Improvement: a `--project-copy` mode on `lint_project.gd` / `run_tests.gd` that
    imports into a private `.godot/` under a temp dir, so N agents can type-check
    and run tests concurrently. Failing that, `name_check --require-compile` that
    shells one `godot --check-only` per changed file — slower than a full lint but
    parallel-safe, and it would turn "names resolve" into "this file builds".

- Gap: **the bus cannot pass `null` to a typed Object parameter, so a losing path a
  unit test drives directly is unreachable from the bridge** —
  `run-method --node /root/Game --method _on_pest_escaped --args "[null]"` answered
  `Failed: Argument 0 of /root/Game._on_pest_escaped(): cannot convert Nil (null) to
  Object`. That signature takes `_pest: Pest` and is deliberately called with null by
  both `test_lane_pressure_is_committed_even_when_the_last_life_is_lost_mid_wave` and
  the game's own losing branch, so GDScript accepts it and only the bus does not.
  Workaround: `set-state game_over true` then `run-method _end_run '["..."]'`, which
  reaches the same UI but skips the life-loss bookkeeping the real path performs —
  i.e. the workaround verifies less than the call it replaces, quietly.
  - [plant-tower-defense:G-026] status: fixed | fixed-in: 0.23.0 | seen: 1 | harness: 0.21.0 | source: plant-tower-defense 2026-08-15
  - Improvement: marshal a JSON `null` to the parameter's own nil-able default rather
    than to a bare `Nil` Variant — GDScript permits `null` for any Object-typed
    parameter, so the bridge is stricter than the language it drives. Failing that,
    say so in the error: "the bus cannot type a null Object argument; call a wrapper
    or set the state directly" would have saved the guessing.

## 2026-08-15 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **in a fan-out, `reach` grades a run against the whole repo's dirty set, so
  a run that fully verified its own slice is downgraded for someone else's** —
  `verify_ledger record` answered
  `downgraded warranted -> insufficient: no changed file was loaded at runtime
  (game/corn_cobbler.gd, game/pest.gd)`. Both of those belong to two subagents
  still mid-task; my own three items were committed moments earlier, so by record
  time the working tree's changed set was entirely other people's work. The run
  did load and exercise `game/hud.gd` and `game/game.gd` — it simply got no credit,
  because reach is computed from `git status` rather than from what the run
  claimed to be about. The inverse error is the dangerous one and it is equally
  available: had I committed nothing, an agent's untouched files would have been
  silently counted as *my* denominator.
  - [plant-tower-defense:G-027] status: fixed | fixed-in: 0.24.0 | seen: 1 | harness: 0.21.0 | source: plant-tower-defense 2026-08-15
  - Improvement: let `record` take `--about PATH...` (or read it from the run.json)
    naming the files this run set out to verify, and compute reach against that
    intersected with the changed set. Absent that, `reach` should at least report
    the two numbers separately — "reached 2/2 of the files this run named, 0/2 of
    the rest of the dirty tree" — instead of collapsing them into one verdict that
    is wrong in both directions depending on commit timing.

## 2026-08-15 — 0.23.0: closing the loop — 7 stale-closed issues, 6 real fixes, and a live collision

Asked (`/loop`, self-paced) to read this repo's open GitHub issues plus the harness's own
and `plant-tower-defense`'s gaps logs, rank the top ten, and ship. Pooled 4 new gaps from
`plant-tower-defense` (`G-022`, `G-024`..`G-026`; `G-023`/a duplicate `G-024` were
`status: fixed` in the source and correctly skipped) and confirmed `harness-test-1` had
nothing new. Reading the nine open `gh#` issues against `main` first (0a) found **seven
were already fixed** — `gh#11`–`#17`, closed in 0.21.0/0.22.0 and tagged by id in the
source, just never closed on the tracker. That is now `PURPOSE.md`'s newest commitment:
closing the report is part of the fix.

Shipped, ranked:

1. **`gh#19.1` / `plant:G-024` — `detect_main_scene()` resolves `uid://`.** Godot 4.4+
   writes `run/main_scene` as a uid by default; the installer returned it verbatim, which
   only read as fine on `plant-tower-defense` because `main_scene` was already
   project-owned there. Now resolved against every `.tscn`'s `uid=` header and every
   `.uid` sidecar, falling back to the raw uid (loudly) only if nothing claims it.
   Verified directly against `plant-tower-defense`'s real `project.godot`
   (`uid://ce2dtga2f08e` → `res://game/title.tscn`, matching the value the issue said
   only survived by luck).
2. **`gh#18` / `gh#19.3` — `ensure_uid_sidecars()` says "none needed".** The
   nothing-to-mint path was the one silent exit in a function that names every other
   outcome. Verified against `plant-tower-defense`'s `game/*.gd` (all already sidecared):
   prints `= .uid sidecars: none needed (5 installed .gd file(s) already had one)`.
3. **`gh#19.2` — step 7's `hud_layer_name` detection covers a runtime-built HUD.** Falls
   back to the first `CanvasLayer` found anywhere under `scan_root`, not just the main
   scene, before defaulting to `"HUD"` — the case the original doc's only fallback got
   right by luck.
4. **`gh#19.4` — `templates/CLAUDE.harness.md` regains its lint-flags line**, updated for
   the 0.21.0 orphan-scan flip (`--no-orphans` in, `--find-orphans` out).
5. **`plant:G-026` — the bus accepts `null` for an Object-typed `run-method` arg.**
   GDScript itself permits `null` for any Object-typed parameter; `_coerce_arg` did not,
   so a losing path called by both a unit test and the game's own code
   (`_on_pest_escaped(_pest: Pest)`) was unreachable from the bridge. One early-return in
   `_coerce_arg` before the match block.
6. **`H-059` — the `first_frame` verb**, rebuilt after moving-in's second `G-027` was lost
   to the id-collision `upstream_gaps.py` still has (that bug itself stays open, filed as
   `H-059` originally — fixing the idea did not fix the pooling bug that ate it).
   `{tree_paused, cursor_mode, visible_canvas_layers, topmost_control, viewport}` in one
   call. `topmost_control` needs no z-index math — Godot paints children after parents and
   later siblings after earlier ones, so the last visible on-screen Control a depth-first
   walk finds is the last one painted, by construction.
7. **`H-060` — `performance`'s `fps_max` carries a headless caveat**, same
   `*_trustworthy`/`*_caveat` convention as `geometry_trustworthy`.
8. **`plant:G-022` — documented, not built.** `set-game-speed 0` as the standard technique
   for reading a state with a lifetime shorter than a few round-trips is now next to
   `step_time` in `REFERENCE.md`: freeze, read, unfreeze. No code needed; the capability
   already existed and only the pointer was missing.
9. **Bookkeeping — closed `gh#11`–`#16` on the tracker**, each comment naming the fix's
   location and version so a reader does not have to re-derive it.
10. **`PURPOSE.md`** gains the closing-the-loop commitment (above).

- Not done, deliberately: **`plant:G-025`** (a parallel-safe compile check — `.godot/` per
  worktree, or `name_check.py --require-compile` shelling one `godot --check-only` per
  changed file) is a real structural gap but a session of its own, not a rider on nine
  other changes; left open rather than rushed. `gh#17`'s fix is real but only on
  `release/0.22.0`, unmerged to `master` — this turn does not land it.

- Value: **warranted, with a caveat the session did not expect.** `detect_main_scene`'s
  fix was verified against the real project that reported it before being trusted, per
  [H-033] — cheap (one Python call, no engine) and decisive (produced exactly the value
  the issue said only survived by luck). But the same check on `ensure_uid_sidecars`
  surfaced something outside the plan entirely (below).

- Gap: **`plant-tower-defense`'s installed harness was already refreshed to a `0.23.0`
  build — content-identical (byte-for-byte modulo CRLF) to this session's own
  independently-written `dev_tools.gd`, including the exact `_cmd_first_frame` helper
  name and the exact H-060/G-026 code-comment wording — and `log-devtools.md` gained a
  new pooled entry (`G-027`, an unrelated `reach` gap) mid-session, written by a process
  that was not this one.** This session never scaffolded `plant-tower-defense`, never
  wrote to its `addons/`, and ran `upstream_gaps.py` against it exactly once, before
  `G-027` existed in its source log. The only explanation consistent with all of that is
  a second, live session working the *same `git status` on this exact checkout* at the
  same time, converging on the same fixes from the same public inputs (this log, the
  GitHub issues) closely enough to be indistinguishable from a shared source — which
  `PURPOSE.md`'s "It shares the machine" commitment was written for buses, not for a
  working tree, and evidently needed to be. No file conflict resulted (every edit this
  session made landed as a clean, isolated hunk), but committing here without checking
  first would have been exactly the risk gh#17 named for a *dirty* tree, one level up: a
  release built by reading a snapshot that moved under it.
  - [H-061] status: open | seen: 1 | harness: 0.23.0
  - Improvement: the same discipline 0a already prescribes for a stale HEAD — re-run
    `git status`/`git log` immediately before commit, not just at the start — should be
    stated for the working tree generally, not only for the sha. Whether two sessions
    should ever share one checkout at all (vs. one `git worktree` each) is a question for
    the user, not something a gaps-log entry can resolve on its own.

**Validation run this turn:** `python .claude/skills/harness-release/bump_version.py
0.22.0 0.23.0` — all 13 shipped files + `plugin.json` `stamp=1 const=1`.
`python tools/record_version.py --record` then `--check` — OK at `0.23.0`, 13 shipped
files, 51 bus verbs + 53 CLI commands documented (first catching two undocumented-verb
misses on `first-frame`'s hyphenated CLI form, fixed before the second `--check`).
`python tools/check_templates.py` — OK, all five stages, no new FAIL; the new verbs
(`first_frame`, the null-Object coercion, the `fps_max` caveat) are not yet exercised by
a dedicated `check_templates.py` stage — validated instead against real state
(`detect_main_scene` and `ensure_uid_sidecars` against `plant-tower-defense`'s actual
files, above) and by the source-level correctness of the fix; adding contract-table
coverage for them is follow-up work, not done this turn. `python -m unittest discover -s
tools` — 35 tests OK.

## 2026-08-15 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **a static-only class that demonstrably ran is invisible to `reach`** —
  `record` reported `reached 2/4 changed file(s) … NOT reached: game/sfx.gd`.
  `Sfx` is `class_name Sfx extends RefCounted` with static entry points, so it
  owns no node and can never appear in a `scene-tree` snapshot — even though this
  run *observed the `SfxPool` node that only `sfx.gd` creates*, which is stronger
  evidence than reach normally has for anything. Worked around with a
  `reach_aliases` entry (`game/sfx.gd` vouched for by `game/game.gd` and
  `game/plant.gd`), which the harness correctly buckets as a declaration rather
  than an observation. This is the same shape as G-015 (a base class invisible
  because only a subclass owned the live node).
  - [plant-tower-defense:G-028] status: fixed | fixed-in: 0.29.0 (gh#30 mark_script_reached; the 0.29.0 entry said 'closes G-028' and this line was never edited - found by 0.42.0's --triage) | seen: 3 | harness: 0.23.0 | source: plant-tower-defense 2026-08-15
  - Improvement: `scripts-seen` already records every script the engine *loaded*,
    which for a static-only class is exactly the right signal and is an
    observation rather than a declaration. Reach consults it today only as a
    fallback for scripts absent from the tree; crediting a `scripts-seen` hit as
    `reached_loaded` — a third bucket beside `reached` and `reached_alias` —
    would retire the whole class of alias entries projects are currently writing
    for RefCounted helpers.
  - **Not fixed this turn (0.24.0) — reproduced first, per H-033.** Read
    `_cmd_scripts_seen` / `_seed_scripts_seen` in `dev_tools.gd`: the bridge only
    records a `script.resource_path` when that script is attached to a *node*
    (initial tree walk + `node_added`). A pure `RefCounted` with only static
    entry points is never attached to anything, so it is invisible to
    `scripts-seen` on exactly the same grounds it is invisible to a scene-tree
    snapshot — the Improvement's premise ("scripts-seen already records every
    script the engine loaded") doesn't hold; there is no existing bridge signal
    for "this script was loaded/called" independent of node attachment. Closing
    this for real needs either an engine-side hook on script load/parse (a
    `ResourceLoader` load callback, if Godot 4 exposes one) or accepting
    `reach_aliases` as the correct, permanent answer for this shape of file and
    saying so in the docs instead of treating it as a workaround.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`coverage_check.py` credits `scene_validation` only for a `res://….tscn`
  literal inside `load`/`preload`, so a discovery-based scene walk — which is
  strictly stronger — scores as no coverage at all.** The first version of
  `test_every_scene_in_the_project_actually_instantiates` walked `res://` with
  `DirAccess` and instantiated every `.tscn` it found, including any added later
  by anyone. The tool still printed `UNCHECKED scene_validation`, because
  `_scan_scene_loads` requires the path to be a literal (`coverage_check.py:530`:
  "the only strong scene_validation token"). The check that covers *more* scenes
  and cannot rot is the one that scores zero, and the fix is to add a hard-coded
  list beside it — i.e. the tool rewards the weaker pattern. I did add the two
  literals, and they are defensible on their own as "these two scenes must exist",
  but they were written to satisfy the scanner rather than because the walk needed
  them.
  - [plant-tower-defense:G-029] status: fixed | fixed-in: 0.24.0 | seen: 1 | harness: 0.23.0 | source: plant-tower-defense 2026-08-16
  - Improvement: also credit an instantiation of a path that is not a literal when
    the same file contains a directory walk reaching `.tscn` — or, more simply,
    treat `PackedScene.instantiate()` / `can_instantiate()` in a `test_dir` file as
    a strong token in its own right, since nothing else in a test suite calls it.
    The current rule tests for a spelling, not for the behaviour it stands for.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **a finding that fires once cannot be re-asked without re-creating the
  frame that produced it.** `validate-ui` reports what is true at the instant it
  samples, which is correct, but leaves nothing to investigate with: there is no
  record of *which* node and rule fired, only a count in a consolidated line I had
  already truncated. The verb re-run seconds later is a different frame and says
  `[OK]`. Everything else in this harness is reproducible by construction — a
  scene, a diff, a seed — and this is the one signal that is not.
  - [plant-tower-defense:G-030] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.23.0 | source: plant-tower-defense 2026-08-16
  - Improvement: have `findings` and `validate-ui` write the full finding records
    of the most recent non-clean run to `user://ui_findings_last.json` (node path,
    rule, measured rect, timestamp), and print that path whenever the count is
    non-zero. A transient would then be diagnosable after the fact instead of
    being a number that has already gone. Cheap: the records exist in memory at
    the moment they are counted.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **the harness has no defect class for source-asset conformance at all.**
  `coverage_check.py` enumerates eight classes — UI layout, UI reachability,
  unconnected signals, orphan growth, input path, scene validation, shader
  compile, name resolution — and every one is about code or a live tree. A project
  whose art is authored to a written contract has no way to ask "does the source
  conform" without rendering, which needs the engine, which is not parallel-safe.
  This whole issue existed because of that hole.
  - [plant-tower-defense:G-031] status: open | seen: 1 | harness: 0.23.0 | source: plant-tower-defense 2026-08-16
  - Improvement: an `asset_contract` class in `coverage_check.py`, covered by any
    project-local checker that reads asset sources and is credited the way
    `name_resolution` credits `name_check.py`. The harness need not ship the
    checker — sprite contracts are project-specific — but naming the class is what
    makes its absence visible, which is the tool's whole job.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`name_check.py`'s string blanker drops a newline on a backslash
  continuation, so every later finding in that file is reported one line early.**
  At `tools/name_check.py:232-237`, a `\` + newline inside a string literal appends
  `"  "` (two spaces) and increments the tracked `line`, but the blanked text it
  builds `_line_starts` from is now one newline short. `coverage_check.py:213` has
  the same shape. Latent in this project — no `.gd` here uses a continued string
  literal, verified — so it costs nothing today and will silently mis-point
  findings the moment one appears. Found by a checker I had written against this
  blanker as a reference, which is the only reason it surfaced at all.
  - [plant-tower-defense:G-032] status: fixed | fixed-in: 0.24.0 | seen: 1 | harness: 0.23.0 | source: plant-tower-defense 2026-08-16
  - Improvement: one line —
    `out.append(" \n" if text[i + 1] == "\n" else "  ")` — keeping the blank
    length-preserving while restoring the newline the line index depends on.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`Control.get_minimum_size()` returns ~1px on any Label with
  `clip_text`, so the natural way to ask "does this text fit its column" passes
  unconditionally.** It is the obvious call to reach for — it is what a Container
  uses to size a child — and on a clipping Label it reports the clip stub rather
  than the text. Every value label on the post-mortem card sets `clip_text`, and
  so do three of the four HUD stats readouts, so a width check written the obvious
  way over either of those surfaces is decoration. The project's own
  `test_no_readout_clips_its_own_worst_case` gets this right by measuring through
  `Font.get_string_size` with the label's real theme font — but that is a thing
  someone had to already know, and it is nowhere in the harness docs. This is the
  same family as the vacuous-pass problem the runner already detects: an assertion
  that cannot fail.
  - [plant-tower-defense:G-033] status: fixed | fixed-in: 0.24.0 | seen: 2 | harness: 0.23.0 | source: plant-tower-defense 2026-08-16
  - Improvement: a `_T.text_width(label) -> float` helper that resolves the
    label's own theme font and measures the string, plus one line in the harness
    CLAUDE.md's gotchas naming `get_minimum_size()` on a clipping Label as a
    false-pass. The helper is four lines and removes the need to know the trap.
    `findings`' `ui_text_trimmed` check already does this measurement internally,
    so the code exists — it is just not reachable from a test.

## 2026-08-16 — 0.24.0: four fixes from the still-open skill-feedback issues, one declined

`gh issue list --state open` at session start named seven open reports (#17-23).
Reading the code first, per H-033/0a: **#17, #18 and all four parts of #19 were
already fixed on `release/0.23.0`** — each carries a "Fixed on release/0.23.0, not
yet on master" comment from the session that closed it. Nothing to re-do; landing
0.23.0 on master (separately, this session) closes them for real, per PURPOSE.md's
"a fixed report gets closed the same turn it's fixed" commitment.

Of the four still genuinely open, three shipped this turn and one was investigated
and declined:

1. **[gh#22 / plant-tower-defense:G-032] name_check.py / coverage_check.py's string
   blanker drops a newline on a `\`-continued string literal.** The blank stayed
   length-preserving (`"  "`, two spaces) but lost the newline the tracked `line`
   counter depends on, so every finding after one continued string in a file was
   reported one line early. One-line fix in both files: `" \n"` instead of `"  "`
   on that branch. Latent in every project checked against it so far — no shipped
   `.gd` uses a continued string literal — so this is a correctness fix with no
   behavior change on any file seen in the wild yet.
2. **[gh#23] import_check.py's success line overclaimed a full compile.** `--import`
   only registers global class names; a `const` initializer that calls a method
   (not a constant expression) passes it clean and is only caught by
   `lint_project.gd`'s real compile. `Import OK: godot --import ran...` read as a
   compile verdict it wasn't. Now states what was actually checked and adds a
   `NOT COVERED:` line, matching `name_check.py`'s own convention for the same
   problem. Severity was always bounded — lint runs alongside import in every
   `/verify` tier that reaches Phase 1 — so this is an honesty fix, not a coverage
   fix.
3. **[gh#21 / plant-tower-defense:G-029] coverage_check.py credited a scene-load
   literal but not a directory sweep for `scene_validation`.** A project that
   walks `res://`, filters on `.tscn`, and loads whatever it finds — strictly
   stronger than any hardcoded literal, since it can't go stale and covers scenes
   added later — scored UNCHECKED, while adding one weaker literal flipped it to
   COVERED. Added `_scan_scene_sweeps` as its own strong-evidence path (an
   `ends_with`/`match` `.tscn` filter alongside a `load(`/`ResourceLoader.load(`
   call in the same file), reported with its own evidence line rather than folded
   into the literal scan.
4. **[gh#20.2 / plant-tower-defense:G-027] `verify_ledger.py record`/`reach` graded
   a fan-out run against the whole dirty tree.** A subagent that fully verified its
   own file got `downgraded warranted -> insufficient` because a sibling agent's
   still-uncommitted file was also in `git status`. Added `--about PATH` (repeatable)
   to both subcommands: when given, the reach denominator narrows to that set
   intersected with what actually changed, and the row records `about` for
   auditability. A stray path (named but not actually in the changed set) warns on
   stderr rather than silently doing nothing. Also fixes the inverse the report
   named as more dangerous: without `--about`, an untouched sibling file used to be
   silently eligible for a free credit it never earned.

**Bonus, same investigation session: [gh#20-adjacent / plant-tower-defense:G-033]
`_T.text_width(label)`.** `Label.get_minimum_size()` reports the clip stub (~1px)
rather than the text on any Label with `clip_text` or a non-default
`text_overrun_behavior`, so the obvious width assertion passes unconditionally on
exactly the labels worth checking. Added a four-line static helper to `run_tests.gd`
that measures through the label's own resolved theme font — the same measurement
`dev_tools.gd`'s `ui_text_trimmed` finding already does internally, now reachable
from a test — plus a gotcha line in `CLAUDE.harness.md`.

**[plant-tower-defense:G-028] investigated and declined for this turn.** The
Improvement text assumed `scripts-seen` "already records every script the engine
loaded"; reading `_cmd_scripts_seen`/`_seed_scripts_seen` in `dev_tools.gd` shows it
only records a script attached to a *node* — a pure `RefCounted` with static-only
entry points is exactly as invisible to `scripts-seen` as it is to a scene-tree
snapshot, for the same reason. There is no existing bridge signal for "this script
was loaded/called" independent of node attachment; closing this needs either a real
engine-side load hook or a documented acceptance that `reach_aliases` is the correct
answer for this file shape, not a workaround. Left open with this note rather than
shipping a fix built on a premise that doesn't hold. [gh#20.1 / plant-tower-defense
G-025] (a parallel-safe compile gate) was scoped and left for a future turn — it is
a real feature (`--project-copy` or a per-file `godot --check-only` mode), not a bug
fix, and this turn's budget went to the four items above plus landing 0.23.0.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.24.0, 13 shipped files, 51 bus verb(s) + 53 CLI command(s)
documented. `python -m unittest discover -s tools` — 35 tests OK. `python
tools/check_templates.py` — OK, all six stages, including stage 5's full bridge
round-trip against a real launched Godot 4.7.1 instance; nothing in this release
touches a stage-checked code path directly (the four fixes are in
`name_check.py`/`coverage_check.py`'s string blanker, `import_check.py`'s message,
`coverage_check.py`'s scene sweep evidence, and `verify_ledger.py`'s `--about`), so
this run is a regression check rather than a new-defect-plant — each fix was instead
verified directly: the blanker fix by feeding a continued-string sample through the
regex and confirming the newline survives; the sweep-credit regex against three
GDScript samples (two should-match, one should-not); `--about` against a scratch git
repo with and without the flag, confirming the denominator narrows and a stray path
warns. No gaps found in the harness itself this turn beyond the ones fixed above.

## 2026-08-16 — 0.25.0: a mismatched liveness check and a missing pause verb

`gh issue list --state open` after landing 0.24.0 named three fresh reports
(#24-26), all filed against 0.24.0 the moment it shipped. `git fetch` confirmed
master was still exactly where the previous turn left it — nothing to re-merge —
so this turn's work was entirely these three.

1. **[gh#24] `quit --kill`/the launch sweep reported a pid dead for hours as an
   unverifiable survivor, forever.** `_pid_alive_windows` and `_pid_started_unix`
   both call `OpenProcess` with the same access right and interpret a failure
   differently: the former treats any non-87 error as "alive" (comment: "5 =
   exists but is not ours to open"), the latter treats *any* failure as
   "unknowable". A pid recycled onto an inaccessible process hits exactly that
   combination — alive per the first, unreadable per the second — and a ledger
   row with `started_verified: true` (we *could* read it when we launched it)
   made it look ambiguous rather than gone. Fixed in `_ledger_survivors`: when a
   pid was readable at launch and is now unreadable at all, that is not
   ambiguity, it is a different process wearing the old pid number — skip it,
   don't report it. Proved with a standalone scratch probe (three synthetic
   ledger rows: a recycled pid, a genuine survivor, a genuinely-never-verified
   one) before touching the real `_quit_sweep` exit code, which had the same bug
   one layer up: `verified: None` (genuinely unknowable, never auto-killed per
   its own docstring) was exiting 1 same as `verified: True` (an actual,
   killable survivor) — an exit code a reader could never act on, the exact
   failure mode PURPOSE.md's "failures must be loud, and distinguishable from
   success" commitment exists to prevent. Now only `verified: True` fails the
   run; the kill hint and the `--kill` "STILL ALIVE" tally were also narrowed
   to verified rows only, since `_kill_survivors` was already silently skipping
   the unverified ones — the hint used to name a pid a literal copy-paste of it
   would not touch.

2. **[gh#26] no bus verb reaches `SceneTree.paused`, so a sub-second effect
   (a fade, a hit-flash, a cooldown tween) cannot be caught and held for
   inspection.** `set_game_speed`'s own refusal message has said "use the
   tree's pause for that" since 0.22.0, but nothing implemented it — the only
   way to pause was a project's own test-fixture method
   (`harness_set_paused`), which is exactly how `check_paused_bridge` in this
   repo's own `check_templates.py` had to reach a paused tree, and which no
   project ships by default. Added generic `pause`/`unpause` bus verbs
   (idempotent either direction, `was_paused` in the reply) plus CLI
   subcommands. Given a stage-5 probe of its own (`check_pause_verb`) rather
   than left to `check_paused_bridge`'s existing fixture-based coverage, and
   the mutation was proved organically: commenting out the two
   `register_command` lines and running `check_templates.py` failed exactly
   that new check (`Unknown action: pause`), restored and reran clean
   afterward (`cmp` confirmed byte-identical restore).

3. **[gh#25.2] the scaffold's refresh-branch safety net degrades silently in
   any beads-tracked project.** Step 5's dirty-tree check
   (`git status --porcelain`) trips on `.beads/*.jsonl`'s auto-export from
   every `bd update --claim` — this project's own workflow — so the branch
   protection meant to keep a refresh reviewable falls back to "apply directly
   on current branch" in exactly the sessions doing real work, silently. Fixed
   with a pathspec exclusion (`-- . ':!.beads'`); real code WIP still trips it.

4. **[gh#25.1] a stale plugin marketplace cache can downgrade an install with
   no warning.** Step 4's pristine-file check only asks "does this match *a*
   released version," not "is it the latest one" — so a real, current file
   matches `harness_history.json` and gets silently overwritten by an older
   cached copy. Added a version-gate step before `full` runs: read the
   installed `.harness_manifest.json`'s `harness_version`, compare as a
   version *tuple* (not a string — `"0.9.0" > "0.10.0"` lexically, wrongly)
   against `${CLAUDE_PLUGIN_ROOT}`'s own `plugin.json`, and abort with
   remediation instructions rather than run `full`. Unit-tested the tuple
   comparison and the JSON reads standalone (both directions, plus the
   absent-manifest/fresh-install no-op case); this is prose the agent
   executes, not a shipped template `check_templates.py` can drive live, so
   the comparison logic is what got proved, not an end-to-end scaffold run
   against a deliberately-stale plugin cache.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.25.0, 13 shipped files, 53 bus verb(s) + 55 CLI command(s)
documented (up from 51+53 — exactly `pause`/`unpause`). `python -m unittest
discover -s tools` — 35 tests OK. `python tools/check_templates.py` — OK, all
six stages, run twice: once mutated (pause/unpause registration commented out)
to prove `check_pause_verb` actually fails naming `Unknown action: pause`, once
clean after a verified byte-identical restore, printing `stage 5 bridge:
pause/unpause verbs flip SceneTree.paused directly ... idempotent both
directions`. The gh#24 ledger fix was proved separately with a standalone
scratch probe (three synthetic pids covering the recycled/genuine/unverifiable
cases) since it needs no live Godot to exercise. gh#25's two fixes are prose
instructions, not template code `check_templates.py` drives — validated by
unit-testing their embedded logic in isolation instead.

## 2026-08-16 - Upstreamed 2 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-034] `import_check.py` reports "Import OK" on a hard parse error.** New.
  Reproduced deliberately, all four gates on the identical tree:
  ```
  name_check.py      -> exit 0   (correct; it does not compile)
  import_check.py    -> exit 0   "Import OK: godot --import ran (exit 0) and its 28
                                  line(s) of output contain no SCRIPT ERROR, Parse
                                  Error, Failed to load script or Compilation failed."
  lint_project.gd    -> exit 1   "lint: 2 error(s), 0 warning(s)"
  run_tests.gd       -> exit 2   "Total: 164 | Passed: 149 | Failed: 15"
  ```
  The error text `SCRIPT ERROR: Parse Error: Assigned value for constant "OK_COLOR"
  isn't a constant expression.` appears in `run_tests` output and in lint's findings,
  but never in the import log import_check scans. Severity is bounded: lint catches it,
  and lint runs alongside import in every `/verify` tier that reaches Phase 1, so
  nothing ships broken. But import_check is documented as the gate that catches what
  name_check cannot, and is positioned FIRST specifically so you fix the cause instead
  of reading a cascade — and it was the one gate that missed. It also cost me the
  detour above, because I had the diagnosis and dropped it when the gate disagreed.
  - [plant-tower-defense:G-034] status: fixed | fixed-in: 0.24.0 | seen: 1 | harness: 0.23.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#23 (H-044 - two intake paths, no dedupe: this same defect was filed as a GitHub issue and fixed before this project-log copy was pooled)
  - Improvement: either `godot --import` does not surface errors for scripts it does
    not re-import (in which case import_check cannot claim "the project parses" and its
    success message should say what it actually verified), or the scan needs to catch
    this error class. The success string is the specific thing to change — it currently
    asserts the absence of four phrases and reads as a compile verdict.

- Gap: **could not pixel-sample the low-alpha instant** — tried `sample-pixels`
  on the pip's computed screen position immediately after a forced fire, but
  every attempt raced the bus round-trip against the 0.8s reload interval and
  landed after most of the recovery had already happened (readiness read back
  at 0.95-1.0 by the time the sample command reached the game), even after
  slowing `set-game-speed` — because `Engine.time_scale` scales tick *rate*,
  not per-tick delta, so a slow scale does not lengthen the real-time window a
  low value is held at any better than 1.0x does once a command is already
  in flight. `set-game-speed 0` is refused ("use the tree's pause for that");
  there is no bus verb for a real `SceneTree.paused = true` freeze.
  - [plant-tower-defense:G-036] status: fixed | fixed-in: 0.25.0 | seen: 1 | harness: 0.24.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#26 (H-044 - same pause/unpause request, filed as a GitHub issue and fixed before this project-log copy was pooled)
  - Improvement: a `pause`/`unpause` verb (or a `--freeze-on` flag on
    `set-game-speed`) that flips the actual tree pause rather than time_scale,
    so a caller can catch a fast, sub-second state transition and hold it
    indefinitely for a leisurely `sample-pixels`/`screenshot`, independent of
    bus latency.

## 2026-08-16 - Upstreamed 6 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **no way to observe a transient UI element** — a Control that shows for ~3s and
  fades cannot be caught, because every verb is its own process round-trip (~1s) and the
  bus serves one command at a time, so `fire` and `screenshot` are always seconds apart. I
  lost the card four times: at `sleep 1`, at no sleep, and twice more at
  `set-game-speed 0.08` (the card's tween is process-driven and did not slow with it).
  What finally worked was giving up on the picture entirely and asserting `node-bounds`,
  which is the right answer here but is not available for anything whose defect is genuinely
  visual — a wrong colour, a clipped glyph, a z-order.
  - [moving-in:G-041] status: likely-fixed | fixed-in: 0.25.0 (unconfirmed against this
    project) | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-15 | same-mechanism-as:
    gh#26 / plant-tower-defense:G-036, where the `pause`/`unpause` verbs shipped in 0.25.0
    were confirmed to fix an identical ask (catch a fast fade, freeze it, inspect at no
    rush). Not marked plain `fixed` because it needs the actual retry this project's own
    discipline calls for (H-033) - poll for the card's on-screen frame, `pause`, then
    `screenshot`/`node-bounds` at leisure - and that retry has to happen in `moving-in`,
    not here. Left open until that project confirms; the `--after` verb below is still
    worth building if the poll-then-pause workflow turns out to be too slow to reliably
    catch a 3s window, but try the cheaper fix first.
  - Improvement: a `--after` on `screenshot` that takes a verb to run first, executed and
    captured inside ONE bus round-trip
    (`screenshot --after 'run-method --node X --method show_card ...' --delay 0.4`).
    The bus already serialises commands, so the game side could run the trigger, wait the
    delay in engine frames, and capture — with no client round-trip in between. Failing
    that, a `hold-next-frame` verb that pauses the tree N frames after the next command
    completes would let a fired animation be frozen mid-flight and then photographed.

- Gap: **`coverage_check.py` can only see a scene load written as a LITERAL.** Its
  `_scan_scene_loads` matches a `res://…tscn` string inside `load()`/`preload()`, so the
  sweep added here — which walks `res://`, finds every scene, and is strictly stronger than
  any hardcoded load — left `scene_validation` reporting UNCHECKED. A project that
  `preload`s one scene once is credited; a project that validates all of them is not.
  The incentive points the wrong way: the cheapest way to turn the class green is to write
  the weaker check.
  - [moving-in:G-042] status: fixed | fixed-in: 0.24.0 | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16 | dup-of: gh#21 / plant-tower-defense:G-029 (H-044 - same coverage_check sweep-credit request, fixed before this project-log copy was pooled)
  - Improvement: also credit a dynamic sweep — a `.tscn` suffix test (`ends_with(".tscn")`,
    `"*.tscn"`) in the same file as a `ResourceLoader.load`/`load` call, or any
    `DirAccess` walk feeding one — and report it as a distinct, STRONGER evidence kind than
    the literal (`sweeps res:// for .tscn` vs `loads res://x.tscn`). Failing that, say in
    the `absent:` line that only literals count, so a session that just wrote a sweep is not
    told its scenes are unchecked.

- Gap: **`quit` reported a long-dead pid as "still alive"** — `python tools/devtools.py
  quit` exited 1 with `WARNING: 1 process(es) this project launched EARLIER are still
  alive (from launched.jsonl): pid 7176  launcher started 02:48:40  (start time
  unverifiable - not auto-killed)`. `Get-Process -Id 7176` returns nothing: the process
  died hours ago, in an earlier session. The pid is simply a stale line in
  `.devtools/launched.jsonl` that nothing ever reconciles, and it will now be re-reported
  on every single run of this project forever. Workaround: confirmed by hand with
  `Get-Process` and ignored the exit code — which is the habit the check exists to
  prevent. Distinct from [G-035], which is about a game that is genuinely still exiting.
  - [moving-in:G-043] status: fixed | fixed-in: 0.25.0 | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16 | dup-of: gh#24 (H-044 - same _ledger_survivors/pid-recycling defect, fixed before this project-log copy was pooled)
  - Improvement: when a `launched.jsonl` pid cannot have its start time verified, probe
    whether the pid exists at all before reporting it as alive, and prune the entry when
    it does not — a warning that can never clear is one nobody reads. If the probe itself
    is unreliable, say `unverifiable` rather than `still alive`, and do not exit 1 on it.

- Gap: **no way to aim the camera at a node.** `aabb` gives the shower's exact world
  centre (`3.321, 0.547, -2.650`) and `teleport_to_grid` takes grid coordinates, but
  nothing converts between them or points the player at a known node, so framing a
  fixture for a screenshot is guesswork about the heading convention. Four attempts —
  `set_heading` 130, 200, and two teleports — produced a wall, a hallway and the kitchen,
  and the visual check was recorded `blocked` (which correctly downgraded the run to
  `partial`). Everything else about the shower was settled numerically; this was only the
  cosmetic confirmation, but that is exactly the check a screenshot is for.
  - [moving-in:G-044] status: open | seen: 2 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: a `look-at --node PATH [--from-node PATH] [--distance N]` verb that
    places the observing camera (or the group-`player` node) at a sensible standoff and
    orients it at the target's AABB centre. The engine already has `Node3D.look_at`; the
    entire difficulty is that the caller has to rediscover the project's own heading
    convention, which the harness can read off the node it is moving.

- Gap: **`tools/plant.py --sub` is mangled by MSYS/Git-Bash path translation.** Running
  `python tools/plant.py --file scripts/house_plan.gd --sub
  's/Paintable.attach\(node3d, room_name, "ceiling"\)/Paintable.attach(node3d, room_name,
  "floor")/'` reported `pattern '...\"ceiling\"\)/Paintable.attach(node3d, room_name,
  "floor")C:/Program Files' matched nothing` — Git-Bash saw the `/` separating the two
  halves as the start of a path and pasted a Windows prefix onto the end of the
  expression. Workaround: prefix the command with `MSYS_NO_PATHCONV=1`, which works.
  Same class as the already-documented `taskkill /F` -> `F:/` gotcha, so the shape is
  known; it is the substitution syntax that is newly affected.
  - [moving-in:G-045] status: open | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16
  - **Out of scope for this repo (2026-08-16):** `tools/plant.py` does not exist under
    `templates/` or `tools/` here - `grep -rn "plant.py"` against `REFERENCE.md` and
    `commands/*.md` finds nothing. It is a `moving-in`-local tool, not a harness-shipped
    one, so there is no template-side fix to make. Left open in case a future scaffold
    ships an equivalent substitution helper and inherits the same MSYS quoting trap.
  - Improvement: accept an alternate delimiter (`s|old|new|`) so a substitution never has
    to contain a bare `/`, and mention `MSYS_NO_PATHCONV=1` in `plant.py --help`. Cheaper
    still: take `--from` and `--to` as two separate arguments and skip expression parsing
    altogether — the `s///` shape buys nothing here, since the tool only ever does one
    substitution.

- Gap: **[G-044] again** — see its entry above, now `seen: 2`. Framing the painted room for
  - [moving-in:auto-3fdcfc] status: open | seen: 1 | source: moving-in 2026-08-16
  a screenshot took five attempts (two teleports, three camera rotations, one
  `sample-pixels`) and never landed on the bathroom; the pixel sample came back `#7e6a43`,
  the ceiling's own lit ochre, from somewhere that was not the room I had painted. The
  player position read back correctly (`4.11, 0.82, -3.81`), so `teleport_to_grid` is
  fine — what is missing is any way to point the camera AT a node. The visual judgement is
  recorded `blocked`, which correctly downgraded the run to `partial`. A `look-at --node`
  verb would have made this one call, and this is now the second consecutive cycle where
  the only unanswered question was a framing one.

- Gap: **`check_templates.py --full`'s stage 6 contract table has drifted from the
  fixture and gone unnoticed** — spotted incidentally while validating the `look_at`
  verb, which needed `--full` to exercise its new contract row. Three rows fail on a
  perfectly clean tree, no mutation, nothing of mine touching any of the three:
  `clear_nodes` (expects a script class `harness_check_no_such_class` that either was
  renamed or never existed as written), `raycast` (expects a `CollisionObject2D` in
  the scratch scene; the fixture currently has none, only a 3D one), `reachable_ui`
  (expects `count: 4, reachable: 3`; the live scratch answers `11, 6`). `stage 6
  contract: 74/77 rows passed` on a tree that should read 77/77. The default
  (non-`--full`) run — the one every release in this log's validation lines actually
  quotes — stays clean throughout, which is exactly how this went unnoticed: `--full`
  is opt-in and nothing routine exercises it, so three genuine contract mismatches sat
  unreported release after release. Not fixed this turn — three separate root causes,
  each needing real investigation into what changed and when (H-035 already fired
  here: a check that is not run is a check that is not passing, whichever the log
  claims).
  - [H-062] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.25.0
  - Improvement: run `check_templates.py --full` at least once per release cycle (a
    line in the harness-release skill's §3, alongside the mutation-testing step),
    not only when a new contract row happens to need it — the whole reason `--full`
    exists is stage 6, and a stage that only runs when someone remembers is the
    exact "reports success, is not running" shape this project keeps finding
    elsewhere.

## 2026-08-16 — 0.26.0: pooling caught its own duplicates, and a camera verb

`tools/upstream_gaps.py` against `plant-tower-defense` and `moving-in` pooled 8
gaps this cycle. Four were already fixed under GitHub issue numbers before this
project-log copy was pooled — a live instance of H-044 (two intake paths, no
dedupe), now with four fresh examples in one pass: `plant-tower-defense:G-034`
(dup of gh#23), `G-036` (dup of gh#26), `moving-in:G-042` (dup of gh#21 /
`plant-tower-defense:G-029`), `G-043` (dup of gh#24). All four marked `fixed`
with `dup-of:` cross-references rather than re-implemented; H-044's bead
(`godot-selftest-harness-7x5`) updated with the fresh evidence.

Of the four genuinely new-to-this-repo gaps:

- **[gh#28 / moving-in:G-044, seen twice the same day] no way to point a
  camera at a node.** Framing a fixture for a screenshot meant guessing a
  heading in degrees — four blind attempts on one real run (a wall, a hallway,
  the kitchen), then a second consecutive session hitting the identical wall.
  Added a generic `look_at --node PATH [--from-node PATH] [--up X,Y,Z]` verb:
  orients `--from-node` (default `get_viewport().get_camera_3d()`, the active
  camera — no project knowledge needed) toward the target's AABB centre (the
  same measurement `aabb` reports), falling back to `global_position` for a
  target with no geometry. Deliberately orientation-only, never repositions —
  "a sensible standoff" would have made this verb a second source of guessing,
  and the reported blocker was always heading, never position.

  **Cost a real mistake worth recording.** The first implementation used
  Python-style adjacent string literal concatenation (`"a " "b" % x`) across
  five error messages — valid Python, NOT valid GDScript, and it does not fail
  quietly: `check_templates.py` caught it immediately as `Parse Error: Expected
  closing "}" after dictionary elements`, because GDScript has no such feature
  at all. Fixed with explicit `+`/single-parenthesized-`%`, matching the
  pattern two pre-existing `raycast` messages already used correctly. Worth
  naming because it is exactly the class of defect this repo's own mutation
  discipline exists to catch, and it worked: the FIRST `check_templates.py`
  run after writing the verb failed, not the last one before shipping.

  **Second mistake, same feature: a stale mutation-test backup.** After fixing
  the parse error (with the register_command mutation still active from
  testing), I restored from a backup taken BEFORE the string fix — silently
  reintroducing the just-fixed bug. Caught by re-grepping for the broken
  pattern rather than trusting the restore. `cp` before a mutation is only
  safe backup if nothing legitimate changes the file between the backup and
  the restore; here something did. Lesson for next time: take the backup
  immediately before mutating, never earlier in the same session.

  Along the way, **discovered `check_templates.py --full`'s stage 6 has three
  pre-existing, unrelated contract-table mismatches** ([H-062] above) —
  `clear_nodes`, `raycast`, `reachable_ui` all fail on a perfectly clean tree.
  Not fixed this turn (three separate root causes); logged, and
  `harness-release/SKILL.md` §3 now says to run `--full` every release rather
  than only when a new verb needs it.

- **[moving-in:G-041] no way to observe a transient UI element** — marked
  `likely-fixed` (0.25.0's `pause`/`unpause`), not plain `fixed`: the mechanism
  matches `plant-tower-defense:G-036` exactly (poll for the visible frame,
  `pause`, inspect at no rush), but this repo cannot retry it against
  `moving-in`'s actual fixture. Left open pending that project's own
  confirmation, per H-033.

- **[moving-in:G-045] `tools/plant.py` MSYS quoting** — confirmed out of scope:
  `plant.py` does not exist anywhere under this repo's `templates/` or
  `tools/`. It is a `moving-in`-local tool. Left open, noted as out of scope.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.26.0, 13 shipped files, 54 bus verb(s) + 56 CLI command(s)
(up from 53+55 — exactly `look_at`). `python -m unittest discover -s tools` —
35 tests OK. `python tools/check_templates.py` (default) — OK, all stages,
`check_look_at` passing with a real mutation-test cycle behind it (see above).
`python tools/check_templates.py --full` — stage 6 contract table run for the
first time this session; 74/77 rows pass, all 3 failures pre-existing and
unrelated (H-062), confirmed by reproducing them against the clean tree before
any of this turn's edits existed. `look_at`'s own contract row (added to
`contract_rows()` alongside the two `pause`/`unpause` rows 0.25.0 should have
had and did not) passes.

## 2026-08-16 — 0.27.0: the parallel compile gate, reproduced before implemented

No new GitHub issues and nothing new pooled from `plant-tower-defense` or
`moving-in` this cycle (both re-checked; `moving-in` had already confirmed
`G-044` — last cycle's `look_at` — fixed on their own end, the first live
confirmation of a fix from this repo). With nothing new externally, used the
cycle on gh#20.1 / `plant-tower-defense:G-025`, deferred twice already as "a
real feature, out of scope this turn": a parallel-safe compile gate.

**Reproduced the mechanism before implementing, per H-033 — the proposal's
"failing that" fallback (`name_check --require-compile` shelling one
`godot --check-only` per file) turned out to need real verification, not
assumption, on exactly the property that matters: does it touch `.godot/`.**
Tested directly against `plant-tower-defense` (a real project, not the scratch
fixture): `--check-only --script` on a single file writes **nothing** under
`.godot/` — confirmed by file-list-plus-mtime diff before/after, single call
and three concurrent calls, byte-identical either way. Positive control: a
deliberately broken script (`const X := SomeUndeclaredMethod()`) caught
correctly, exit 1, real script; a good one passes, exit 0. Also tested, and
this mattered: a project that has **never** been imported at all works fine
and still creates no `.godot/` — but a file referencing another file's
`class_name` **false-positives** `Could not find type` without a prior import,
because `--check-only` reads the existing class cache, it does not build one.
That caveat is real and is documented everywhere the flag is, not discovered
and then dropped.

Implemented `name_check.py --require-compile FILE [FILE ...]`: shells the
verified-safe call per file, reports failures as `compile_error` findings
alongside the static ones, names files that passed in a `compiled OK:` line
(coverage reported, not implied, per PURPOSE.md), and narrows rather than
suppresses the `NOT COVERED` caveat for exactly the files that got compiled.
Given a `check_templates.py` stage-2.5 control with a negative control built
in: the planted defect (`const` initializer calling a real, resolvable engine
method) is invisible to plain `name_check` — asserted directly, 0 findings —
and only catchable by `--require-compile`, which is the whole point of the
flag. Mutation-tested: forcing the pass/fail decision to always report success
made the new check fail correctly (`must exit 1, got 0`), reverted and
reran clean.

**Also closed `godot-selftest-harness-1tq` (H-045), a different bead than the
GitHub issue but the same underlying ask** — its acceptance criteria named a
specific regression (`Basis.get_column()`, absent in Godot 4.7, shipped
undetected in 0.18.0) and two proposed fixes (teach `name_check.py` to
resolve members on builtin-typed locals, or bolt on a third-party parser).
`--require-compile` is a third path neither bead considered, and it is
stronger than either proposed fix: re-created the exact regression
(`func probe(b: Basis) -> Vector3: return b.get_column(0)`) and confirmed
`--check-only` catches it — `Cannot find member "get_column" in base "Basis"`,
exit 1 — which a parse-only tool structurally cannot (it has no member table)
and which the builtin-locals fix would have covered only for that one class of
error. One fix closed two tracked reports that arrived by different paths and
never realized they were the same ask (H-044's shape again, this time within
this repo's own tracking rather than across the intake-path boundary).

**`plant-tower-defense:G-025` marked fixed as `dup-of: gh#20.1`.** gh#20's
remaining sub-finding (`G-028`, the static-only `RefCounted` invisible to
`reach`) stays open — investigated and explicitly declined last cycle because
the Improvement's premise didn't hold; commented on the GitHub issue with the
updated status rather than closing it, since one of its four findings is
still genuinely unresolved.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.27.0, 13 shipped files (`--require-compile` is a flag on
an existing tool, not a new bus verb, so the verb/CLI counts are unchanged
from 0.26.0). `python -m unittest discover -s tools` — 35 tests OK. `python
tools/check_templates.py` — OK, all stages including the new
`check_require_compile` stage-2.5 control, run twice: once mutated (the
pass/fail decision forced to always succeed) to prove the control fails
correctly, once clean after a verified restore. The parallel-safety claim
itself was proved outside `check_templates.py`, against a real project with
an established import cache, before any of this turn's code existed —
the empirical work, not the unit test, is what makes the claim trustworthy.

## 2026-08-16 - Upstreamed 7 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **no verb reports what the placement ray actually HIT** — `unpack_aim` returns
  the resolved placement (`gx`, `gz`, `y`, `reason`) but never the collider, the hit
  point or the hit normal. Every wrong conclusion above came from inferring the hit
  backwards out of the placement. Diagnosing "why is this refused" means knowing which
  body answered, and `raycast` cannot help because it takes world coordinates and the
  question is about the ray the game itself casts from its own camera.
  - Workaround: reconstructed the ray by hand from camera `global_position`, pitch and
    `reach = 3.5 * KIT_SCALE`, then compared against `aabb`. That is where the -15
    misreading came from.
  - [moving-in:G-047] status: open | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: add `collider_path`, `hit_position`, `hit_normal` and `surface_kind` to
    `unpack_aim`'s `data`. Four lines in the handler; it already holds the hit dict.

- Gap: **`--filter` silently takes no comma list.** `--filter test_a,test_b` matched
  nothing and exited 2 (`Selected: 0 of 245 discovered`). The exit code is honest and
  the denominator made it obvious, so this cost one run rather than a wrong conclusion —
  but a selector naming two real tests reads as though it should work.
  - [moving-in:G-048] status: open | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: split the filter on `,` and match any; or reject a filter containing a
    comma with "one pattern per run" rather than matching nothing.

- Gap: **`cmd mouse_look` cannot move the camera while a menu is up**, and the game
  boots to its title screen, so the first three verbs of any session act on a house
  nobody is standing in. The verb itself was exemplary — it reported "Camera did NOT
  move — the motion event was delivered and ignored" instead of returning success — but
  `devtools_config.json` has an `entry_hook` for exactly this and it is not set.
  - [moving-in:G-049] status: open | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: set `entry_hook` to the title screen's start button so `launch` lands
    in the playable scene. Project-side fix, but the symptom is generic enough that
    `ping` reporting "a modal/menu layer is up" alongside `tree is PAUSED` would have
    named it in one call.

- Gap: **the runner reports `[PASS]` for a test that emitted a SCRIPT ERROR.** This is
  the highest-value gap logged so far and it is not project-specific. `Total: 249 |
  Passed: 249 | ALL TESTS PASSED` was true of a run in which two methods aborted
  mid-way. `[VACUOUS]` does not catch it, because it only fires when a test executed
  *no* assertions — an abort partway through has already executed some, which is
  exactly the dangerous case.
  - Workaround: `grep -c "SCRIPT ERROR"` on the redirected output, by hand, every run.
    The full suite has had 2 for at least this whole cycle and nobody noticed
    (`moving-in-mgr`).
  - [moving-in:G-050] status: fixed | fixed-in: 0.27.0 | seen: 3 | harness: 0.21.0 | source: moving-in 2026-08-16 | dup-of: gh#27 (same defect, filed as a GitHub issue and fixed the same day this project-log copy was pooled)
  - Improvement: count errors emitted between a test method's start and end, attribute
    them to that method, and mark it `[ABORTED]` rather than `[PASS]`. Failing that,
    a single summary line — `Errors: N emitted during the suite` — next to
    `Assertions: N executed`, gating the exit code on it. The denominator philosophy
    this harness already has, applied to the one number it does not print.

- Gap: **no `--filter` for a whole test SCRIPT, only for method names.** Attributing the
  two script errors meant bisecting by guessing distinctive method-name substrings,
  because there is no `--file test_unpack_director.gd`. With one, the same attribution
  is two runs instead of eight.
  - Workaround: `--filter test_operable_placed_fires_once`, chosen by reading the file
    first to find a substring unique to it.
  - [moving-in:G-051] status: open | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: accept a path or basename as a selector and report it in the
    `Selected: N of M` line the same way, so a selector matching one file is still
    visibly a subset.

- Gap: **no verb prints a collision shape's actual geometry.** `aabb` gives the merged
  visual bounds and `node-bounds` the screen rect, but nothing answers "what horizontal
  surfaces does this collider actually have, and at what heights" — which is the only
  question that matters for placement. Getting the five planes above required
  instrumenting a test with a deliberately-failing assertion to smuggle the numbers out
  in a failure message.
  - Workaround: `_T.assert_true(false, "DBG %s" % dbg)` inside a temporary copy of a
    test, then `grep -o "DBG.*"`.
  - [moving-in:G-052] status: open | seen: 3 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: a `collider-planes --node PATH` verb reporting each shape's type and,
    for a mesh shape, its up-facing planes as `{y, rect}` — the placement question in
    one call. Generic: every 3D game that rests things on things needs it, and the
    existing `aabb` verb already has the traversal.

- Gap: **no gap on the aim verb — [G-047] is closed and the fix was exactly the four
  - [moving-in:auto-de695e] status: open | seen: 1 | source: moving-in 2026-08-16
  lines predicted.** Recording that explicitly: a gap filed one turn, fixed the next,
  at the estimated size, is the loop working, and it is worth one line saying so rather
  than only ever logging what hurt.

## 2026-08-16 — 0.28.0: a false pass and a silent no-op, both from the same day's reports

Two fresh GitHub issues (#27, #28) arrived within 20 minutes of each other, both
independently reproduced by `moving-in` in its own log the same day (`G-050` for
#27, no separate report for #28's two findings). No new master drift to land
first this cycle.

**[gh#27 / moving-in:G-050] `run_tests.gd` reports `[PASS]` for a test that
aborts mid-method after already running a real assertion.** The third member of
a family this repo already knows: `[VACUOUS]` catches zero assertions, this
catches SOME-then-abort, and neither the return value nor the exit code can
carry the signal — Godot coerces an aborted coroutine's return to the declared
type's default (`""` for `-> String`), byte-identical to a genuine pass.
Reproduced exactly before fixing anything (H-033): a planted test with one real
`_T.assert_true` before a `float + null` runtime error, run directly against
`run_tests.gd`, printed `[PASS]` and `ALL TESTS PASSED`, exit 0 — confirmed the
bug is real and not a misreading of the report.

Shipped `tools/run_tests.py`, a new wrapper (`import_check.py`'s pattern:
GDScript cannot observe its own stderr after the fact, so the fix has to be an
external process capture). Runs the suite as a subprocess, prints
`run_tests.gd`'s own output unchanged, counts `SCRIPT ERROR`/`USER SCRIPT
ERROR` lines, and overrides a reported-clean exit when that count is nonzero.
Added to `SHIPPED_FILES` so the scaffolder installs it.

**Two mistakes caught by the harness's own gates while building this, worth
recording:**
1. The first draft had a duplicate, independently-computed exit-code decision
   in the text-output branch (`sys.exit(1 if exit_code != 2 else 2)` inside
   `if findings:`) that did not actually read the `exit_code` variable the
   mutation targeted — so the FIRST mutation test (forcing `exit_code`'s
   computation to never fail) passed clean, which would have shipped a
   mutation-tested-but-not-actually-tested control. Caught by noticing the
   mutation had no effect rather than trusting the green run; fixed by
   consolidating to one `sys.exit(exit_code)` at the end of `main()`, so every
   branch above it only decides what to print.
2. `check_run_tests_py`'s planted-defect control initially failed for an
   unrelated reason (`_T` referenced with no `var _T` declaration, then `:=`
   on an untyped call) before it ever tested the real thing — both artifacts
   of iterating the plant against a live Godot rather than assuming the
   GDScript would just work, which is exactly why check_templates.py's own
   rule is to run every planted defect, not just write it.

**[gh#28] two defects in `launch`, worse first:**
1. **`launch -- --devtools-session X` silently never wired the session.**
   `cmd_launch` appended bare passthrough straight onto the engine command
   line with no Godot `--` separator ahead of it — two top-level tokens Godot
   does not recognize, silently ignored, and every later `ping --session X`
   timed out reading exactly like a crashed game. Fixed by partitioning
   passthrough into godot-native and `--devtools-`-prefixed tokens and routing
   the latter after Godot's own `--`, matching what `--isolated`/top-level
   `--session` already built correctly.

   **This fix alone was not enough, and the reason is worth recording.** The
   pre-launch "a live pid already owns this bus" refusal reads the *global*
   `_SESSION` variable to pick which owner file to check, and a bare
   passthrough session left that variable empty for the whole function — so
   with the command-line fix alone, `launch -- --devtools-session X` while a
   *different*, unrelated default-session instance was alive would still
   refuse, reading the wrong owner file as a live conflict. Caught by
   `check_launch_session_passthrough`'s own first run inside `check_templates.py`
   (which launches its main scratch instance under the default session before
   this control runs) — a scratch-project false-negative that a solo manual
   test against a single quiet instance had not exposed. Fixed by parsing the
   passthrough and adopting a bare `--devtools-session` value into the global
   `_SESSION` *before* the owner pre-check runs, not after — one parse instead
   of the three separate ones the first draft had, which is also what let the
   two drift apart in the first place.

   Verified end-to-end against `plant-tower-defense` (a real project, not the
   scratch fixture): before the fix, `launch -- --devtools-session hudwork`
   timed out; after, `bus answered: pid N` and `ping --session hudwork`
   succeeded, including with another live default-session instance running
   concurrently.

2. **`CLAUDE.md`'s `GODOT_USERDATA` claim was false.** Godot has no
   `--user-data-dir` flag and honours no `GODOT_USERDATA` env var — only
   `devtools.py` reads it, to decide where *it* polls, and only after
   something else (a per-worker `project.godot`'s `custom_user_dir_name`) has
   actually moved where the game writes. `REFERENCE.md`'s own worked example
   (`GODOT_USERDATA=/tmp/run-a godot --path . ...`) was the same bug at one
   remove: setting it before launching Godot does nothing, so the client would
   have polled an empty directory while the game wrote to the real one — the
   exact silent-timeout shape this whole issue is about. Reworded
   `templates/CLAUDE.harness.md` and `REFERENCE.md` to state the true
   limitation and lead with the one mechanism that actually works.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.28.0, 14 shipped files (up from 13 — `run_tests.py`).
`python -m unittest discover -s tools` — 35 tests OK. `python
tools/check_templates.py` — OK, all stages including two new controls
(`check_run_tests_py`, `check_launch_session_passthrough`), each mutation-tested
twice (the first mutation test for `run_tests.py` didn't fail as designed —
see mistake 1 above — the corrected version does). `gh#27`'s abort-detection
and `gh#28`'s launch fix were both additionally verified live against
`plant-tower-defense` before being considered done, per this repo's own
"reproduce before implementing" discipline (H-033).

## 2026-08-16 - Upstreamed 4 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **the orchestrator-suggested `launch -- --devtools-session X` form
  silently does not wire the session to the game.** Ran exactly
  `python tools/devtools.py launch -- --devtools-session hudwork`; the
  process launched, `ping --session hudwork` never got picked up (`game not
  running` after the 2s grace). Root cause, read from `cmd_launch` in
  `tools/devtools.py`: passthrough args after a bare `--` are appended
  directly to the engine command line (`cmd += passthrough`) with **no**
  Godot-side `--` inserted first, so `--devtools-session hudwork` never
  reaches `OS.get_cmdline_user_args()` (which the addon reads at
  `dev_tools.gd:405`) -- only `--isolated` or the top-level `--session`
  flag correctly append `["--"] + user_args`. This project's own
  AGENTS.md/CLAUDE.md never actually recommends the broken form (it only
  shows `launch` and `launch --isolated`), so the instruction came from
  outside this repo -- but the failure mode (a launch that "succeeds" and a
  ping that then reads as a dead game, indistinguishable from a crash) is
  exactly the kind of silent-wrong-mode this project's other G-entries keep
  naming. Recovered by using `launch --isolated`, which the tool already
  builds correctly.
  - [plant-tower-defense:G-042] status: fixed | fixed-in: 0.28.0 | seen: 1 | harness: 0.25.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#28 (identical root cause, fixed before this project-log copy was pooled)
  - Improvement: either have `cmd_launch` insert Godot's own `--` before ANY
    passthrough token that starts with `--devtools-` (so the natural-looking
    form works), or have `launch` print a one-line warning when passthrough
    args contain `--devtools-session`/`--devtools-busdir` without a
    preceding bare `--` reaching the engine, naming `--isolated`/`--session`
    as the forms that actually wire it.

- Gap: **CLAUDE.md/AGENTS.md's own words for `--isolated` promise something
  `GODOT_USERDATA` cannot deliver.** The line read this session: "`user://`
  ... stays shared unless you also set `GODOT_USERDATA`" -- phrased as if
  setting it isolates `user://`. `addons/godot_selftest/dev_tools.gd:41`
  says the opposite in its own comment: "Godot has no command line switch
  for `user://` and honours no `GODOT_USERDATA`". Checked directly this
  session: `godot --help` on this project's 4.7.1 build has no
  `--user-data-dir`/`--userdata` engine flag at all, so there is no
  mechanism by which setting the env var could change where the actual
  Godot process's `user://` resolves -- confirmed by the sequence in this
  session (`GODOT_USERDATA=/tmp/... launch` still wrote its owner file etc.
  to the default `%APPDATA%/Godot/app_userdata/plant-tower-defense/`, not
  the temp dir). The sentence in CLAUDE.md is the one a reader acts on; the
  comment naming the true limit is three files away in the addon.
  - [plant-tower-defense:G-043] status: fixed | fixed-in: 0.28.0 | seen: 1 | harness: 0.25.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#28 (identical GODOT_USERDATA claim bug, fixed before this project-log copy was pooled)
  - Improvement: reword the harness-generated CLAUDE.md/AGENTS.md line to
    stop implying `GODOT_USERDATA` isolates `user://` -- e.g. "`user://` ...
    stays shared; there is no supported way to isolate it (Godot has no
    `--user-data-dir` flag), so saves/screenshots/baselines from parallel
    `--isolated` instances can still collide" -- so a multi-agent
    orchestrator stops handing out an instruction that cannot work.

- Gap: **`launch -- --devtools-session X` silently fails to wire the session** —
  five consecutive launches this way (`python tools/devtools.py launch --
  --devtools-session plantwork`) all reported `launched, but the bus never
  answered a ping within 20s`, with the spawned process stuck at ~6MB RSS and
  ~0.015s total CPU time indefinitely (confirmed via `Get-Process ... | Select
  CPU,WorkingSet`) — a genuine hang, not slow startup, and it reproduced
  identically under both `--rendering-driver opengl3` and the default D3D12,
  ruling out a GPU-contention theory. A sibling agent in a concurrent worktree
  had already diagnosed the same failure and reported that `launch --isolated`
  (which sets `--devtools-session` AND `--devtools-busdir` together) works
  where the bare `-- --devtools-session X` form does not; switching to
  `launch --isolated --kill-survivors` fixed it on the very next attempt, and
  every subsequent launch that session answered a ping within 1-2s. Lost
  roughly 15 minutes and 5 launch/kill cycles chasing GPU-contention and
  windowing-hang theories before the correct fix (a different flag) came from
  outside this session.
  - [plant-tower-defense:G-037] status: fixed | fixed-in: 0.28.0 | seen: 1 | harness: 0.25.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#28 (this project's own text already names it: "SeveralHerr/godot-selftest-harness#28, same root cause")
  - Improvement: either make bare `--devtools-session NAME` (without
    `--isolated`) actually wire a working bus the same way `--isolated` does,
    or have `launch` refuse/warn on that combination instead of reporting a
    generic 20s ping timeout that reads identically to a crashed engine — the
    symptom gives no hint that the fix is a different flag.
  - Root cause, found afterward by reading the installed `cmd_launch()`
    (`tools/devtools.py`): `-- --devtools-session X` with no top-level
    `--session` flag leaves `user_args` empty, so the `cmd += ["--"] + user_args`
    line that would add Godot's OWN `--` separator never runs — `cmd +=
    passthrough` alone appends `--devtools-session plantwork` straight onto the
    engine's command line as two unrecognized top-level tokens, which never
    reach `OS.get_cmdline_user_args()` at all. Confirmed against my own printed
    launch line, which shows no `--` before `--devtools-session`. A sibling
    agent had already filed this precisely
    (SeveralHerr/godot-selftest-harness#28, same root cause plus a related
    `GODOT_USERDATA` claim bug) before I got to filing; added a confirming
    comment with the CPU/memory signature above rather than duplicating it.

- Gap: `godot --headless --path . --import` segfaulted on its first run this
  session (exit 139, mid-reimport of vendored audio) and produced a clean exit
  0 on an immediate retry with no other change. Same signature as the
  already-logged half-built-cache gap above (concurrent-agent load against a
  shared `.godot/` import cache), but this time the crash was a hard segfault
  rather than a truncated cache, and it happened on the FIRST import call of
  the session rather than after `/verify` was already mid-run.
  - [plant-tower-defense:G-044] status: fixed | fixed-in: 0.29.0, 0.34.0, 0.35.0 (5th sighting: retry while progressing, up to 4; 6th sighting pooled at 0.37.0 came from a 0.33.0 cache - nothing further; 7th sighting was a BARE --import outside import_check.py, plus .import*.tmp debris - the sweep shipped in 0.39.0) | seen: 7 | harness: 0.25.0 | source: plant-tower-defense 2026-08-16
  - Improvement: `/verify`'s import step retrying once on a non-zero exit
    before surfacing failure would turn "verified nothing, investigate a
    crash" into "verified cleanly, noted a transient" — the same shape as the
    existing G- entry about `--import` racing another worktree's concurrent
    import, just caught one step earlier (segfault vs. truncated cache).

## 2026-08-16 — 0.29.0: a self-report API, a real limitation found while building it, and a numbering mistake corrected

`gh#30` arrived with a genuinely better design than the two options this repo's
own H-040/G-028 investigation had already declined — not a re-report, a third
path. Pooling also caught three more duplicates of `gh#28` (all fixed before
this cycle) and one real new item (`G-044`).

**[gh#30 / plant-tower-defense:G-014, closes H-040/G-028 for real] a
static-utility script can now self-report into `reach`.** `class_name Music
extends RefCounted` with only static entry points is never itself a node's
`script` — structurally invisible to `scripts-seen`'s node_added hook no matter
how much of it ran, the same shape as `reached_base`'s problem one level
further out. Added `DevTools.mark_script_reached(path)`, modeled on the
existing `register_status_provider` pattern: a script calls it once from each
real entry point, and the path lands straight in the same `_scripts_seen`
dict `scripts-seen` already reports and `reach` already reads. No engine-side
load hook needed — the previous investigation's blocker — because the script
reports itself; nothing has to detect it from outside.

**Ate the harness's own dog food.** `GodotSelftestSceneValidator`
(`scene_validator.gd`) is exactly this shape — a static-utility class the core
loads by path and calls, never attached to any node — so every project running
`findings`/`validate-ui`/`validate-all` was scoring its own validator
permanently unreached. `_load_validator()` now credits the path directly
(the core doing it inline, not the validator calling back into `DevTools` —
simpler, and sidesteps the autoload-resolution finding below entirely).

**A real limitation found empirically while building the test fixture, not
guessed at:** the first draft of the planted test called
`DevTools.mark_script_reached(...)` by the bare autoload name from the fixture
script, and `check_templates.py` stage 3 failed to PARSE it —
`Identifier not found: DevTools`. Reproduced as a minimal two-file repro (an
autoload plus a caller) outside the fixture to confirm it wasn't specific to
the scratch project: **`godot --check-only` on an isolated file does not
resolve an autoload SINGLETON by its global name at all, even after a prior
`--import`, even with the autoload correctly declared in `project.godot`.**
This directly affects `--require-compile` (shipped 0.27.0): any file that
calls `DevTools.<verb>(...)` the normal way — which is how this harness's own
`REFERENCE.md` had been telling projects to call `register_status_provider`,
and now `mark_script_reached` — will false-positive a compile error under
`--require-compile`. Documented prominently everywhere `--require-compile` is
mentioned, with the `get_node("/root/X").call(...)` workaround (a runtime
lookup, not a static identifier, so `--check-only` has nothing to fail to
resolve). Fixed the test fixture itself to use that form, since real project
code correctly using the ergonomic form was never the bug — the false-positive
gate was.

**[gh#28 numbering correction.]** While investigating gh#30, noticed `look_at`
(built in the 0.26.0 cycle) was mislabeled `gh#28` in four places — `gh#28`
did not exist as a GitHub issue at the time; `look_at`'s real citation was
always `moving-in:G-044` alone. The actual `gh#28` (the launch/`GODOT_USERDATA`
fix, shipped 0.28.0) collided with the stale placeholder. Fixed all four
occurrences (`dev_tools.gd`, `check_templates.py` ×2, `REFERENCE.md`); past
commit messages are left as historical record, not rewritten.

**[plant-tower-defense:G-044] `import_check.py` now retries once on a crash
signature.** `--import` segfaulted (exit 139) on the first call of a real
session and imported clean on an immediate retry with nothing else changed —
the harder-failure sibling of the shared `.godot/` cache contention already
known under concurrent load. A crash is distinguishable from a genuine parse
failure by exactly what this tool already scans for: a nonzero exit with NO
recognizable `SCRIPT ERROR`/`Parse Error` text. Retries that specific shape
once, transparently, prints a `Note:` saying so; a run with real findings is
never retried. Verified both directions with a stand-in "godot" binary: exits
139-then-0 on two calls → one retry, exit 0, `Note:` printed; exits 1 with a
real `SCRIPT ERROR` every call → zero retries, immediate exit 1, called
exactly once (confirmed via a call-count file).

**Three more `gh#28` duplicates surfaced by pooling** (`plant-tower-defense:
G-037`, `G-042`, `G-043`) — all the identical launch/`GODOT_USERDATA` root
cause, filed independently before `gh#28`'s fix had propagated back. Marked
fixed with `dup-of:` cross-references; H-044 (two intake paths, no dedupe)
keeps finding fresh examples every pooling pass.

**Not done this turn: gh#29 (`entry_hook`/`entry_points` documented, accepted,
read by nothing).** A real feature gap, not a bug fix — actually consuming
`entry_hook` needs a design decision (does `launch` call it automatically, is
it opt-in, how does `entry_points`' diff-matching integrate with `/verify`
Phase 1) that deserves its own turn rather than being rushed alongside four
other fixes. Left open, queued.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.29.0, 14 shipped files (unchanged — `mark_script_reached`
is a direct GDScript API, not a bus verb, so it needs no `devtools.py`
counterpart and does not move the verb/CLI counts). `python -m unittest
discover -s tools` — 35 tests OK. `python tools/check_templates.py` — OK, all
stages including two new controls (`check_mark_script_reached`,
`check_validator_reach`), each mutation-tested (forcing the write to no-op
correctly failed both). One transient flake mid-session (`run_method` timeout
on the main scratch instance, unrelated to any of this turn's changes —
confirmed by two clean reruns back to back) — noted rather than chased, since
it reproduced on neither retry. `import_check.py`'s retry logic was verified
outside `check_templates.py` (no existing coverage of that tool there at all,
a gap this turn didn't have room to close) via a stand-in Godot binary in both
directions, matching the standard this repo already applies to claims that
need a real process to prove, not a scratch fixture.

## 2026-08-16 — 0.30.0: gh#29, patched on request

Deferred last cycle as "needs a design decision, not a quick patch" — asked
to proceed anyway. Implemented option 1 from the report's own preference
order (implement it), not options 2 (delete) or 3 (fail loudly and stop
there).

**`entry_hook` now fires automatically, once, shortly after launch.**
`{node_path, method}`, both required together. Resolution is polled rather
than fired on a fixed frame delay — autoloads run before the main scene is
instantiated (the same timing problem `_seed_scripts_seen` already solved
with `node_added`; this uses a bounded poll instead since a genuine typo
never fires another `node_added` to wake an event-driven version). Gives up
after 10s and reports `node not found` — a typo is now an error, not
permanent silence, which was the entire complaint. Outcome rides on every
`ping` reply as `entry_hook_status` (`not_configured` / `fired` / a specific
error) and `entry_hook_result` (the method's own return value); the CLI's
`ping` print surfaces it too, and `performance`'s `TREE IS PAUSED` advice
(`devtools.py:1350`) — which told the reader to set `entry_hook` when doing
so could not fix anything — is now honest about being one of two real
remedies (the other being the `unpause` verb shipped 0.25.0).

**`entry_points` now exist at runtime, not only in the scaffolder's shipped
config template.** The gap was narrower than the report first read: the
shipped `templates/addons/godot_selftest/devtools_config.json` already
carried `"entry_points": {}`, so `patch_config`'s merge already added it to
every refreshed project as a "new key" — accepted, validated, present in the
file, and still read by nothing, which is the precise shape the report
diagnosed. Added `entry_points` to `dev_tools.gd`'s in-code `DEFAULT_CONFIG`
too (the fallback for a project with no config file at all) and a new
`fire_entry_point` verb / `fire-entry-point NAME` CLI command: resolves a
named entry (`{node_path, method, scene, args, match}`, first two required),
switches `scene` first if configured and different from the current one
(polling for the target the same way, since `change_scene_to_file()` defers
the new scene to end-of-frame), then calls `node_path.method(*args)` and
reports the return value. Refuses an unconfigured name by listing what IS
configured, never silently. `commands/verify.md` already had a full,
well-written "Named entry points (diff-aware)" section describing exactly
this selection logic (`match` substrings against the diff) — written
speculatively for a feature that did not exist yet, which is itself a small
instance of the same failure class. Updated it to call the real verb instead
of the placeholder `cmd start_game` workaround it had been carrying.

Neither mechanism guards against firing twice - that is the target method's
own job, per the report's own worked example (`"already in play, nothing to
dismiss"`), and is now called out as the pattern to copy rather than left
implicit.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.30.0, 14 shipped files (unchanged - `fire_entry_point` is
the only new bus verb, `entry_hook` fires from existing startup plumbing).
`python -m unittest discover -s tools` — 35 tests OK. `python
tools/check_templates.py` — OK, all stages including a new dedicated control
(`check_entry_hook_and_entry_points`) that mutates `devtools_config.json` for
a SEPARATE launched instance (entry_hook fires once at `_ready()`, before the
main scratch instance - already running under the default session - would
ever see a config change), asserts `fired` status, the surfaced return value,
a successful named `fire-entry-point` call with args, and refusal on an
unconfigured name; config restored and the instance quit in `finally`
regardless of outcome. Mutation-tested both mechanisms separately (the
`_start_entry_hook()` call site, and the `fire_entry_point` registration) -
each failure correctly named the specific assertion it broke, confirmed
against a byte-identical restore both times.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: `launch --isolated --kill-survivors` hung at the classic "bus never
  answered a ping within 20s" symptom the local `godot-devtools-concurrent-launch`
  skill describes — but this was a *different* cause than the one that skill
  documents, and the symptom is indistinguishable from the ping side: the process
  was alive, `MainWindowHandle` was 0, one thread, 0.015s CPU, exactly like the
  skill's malformed-cmdline signature. The actual cause was a plain, silent
  Godot OS.alert() dialog titled "ALERT!" blocking the main thread on "Main
  scene's path could not be resolved from UID. Make sure the project is
  imported first." — after a `godot --headless --path . --import` had already
  been run and appeared to finish (printed reimport steps through "[ DONE ]",
  no visible error). `.godot/uid_cache.bin` was in fact absent after that first
  import and present only after a second, identical `--import` call. `ping`'s
  20s timeout gives zero signal that a blocking native dialog is the reason —
  distinguishing "malformed cmdline hang" from "modal alert dialog hang" from
  "still loading" currently requires reading `MainWindowTitle` via PowerShell
  and, since the alert box draws no child controls `EnumChildWindows` can read,
  screen-scraping it with `PrintWindow` into a PNG to read the message at all.
  - [plant-tower-defense:G-045] status: fixed | fixed-in: 0.31.0 | seen: 1 | harness: 0.25.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#31 (fix 2 shipped - ping timeout now reads both launch logs; fix 1 deferred, see 0.31.0 entry)
  - Improvement: two independent fixes would each have closed this faster.
    (1) `--import` exiting non-zero (or printing a distinguishable warning)
    when it does not end up writing `uid_cache.bin`, rather than looking
    identical to a clean run — this is the same shape as the already-logged
    G-044 (`--import` segfault-then-clean-retry) and G-036-ish concurrent-import
    races, but here the first run didn't even error, it just quietly didn't
    finish the one file that matters for the next launch. (2) `ping`'s timeout
    message reading the launched process's own stdout/stderr tail (already
    captured to `.devtools/launch_stdout.log` / `launch_stderr.log` by
    `cmd_launch`) and surfacing a line like "the game already logged: ERROR:
    Main scene's path could not be resolved from UID" instead of a bare
    "never answered a ping" — since that exact diagnostic was sitting in the
    log file the whole time from the FIRST launch attempt, and would have
    named the fix immediately instead of sending the session toward the
    concurrent-launch skill's (correct, but irrelevant here) troubleshooting
    path.

## 2026-08-16 - Upstreamed 2 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`name_check` reported `errors: 0` on a hard parse error**, and the suite went
  from green to 48 failures with 119 `SCRIPT ERROR`s. The line was
  `shape_node == null or shape_node.shape as ConcavePolygonShape3D == null` — the `as`
  swallows the whole surrounding `or` and tries to cast a bool. This is not a defect in
  `name_check`: its own `NOT COVERED:` line says a clean run resolves names and does not
  compile, and names `:=` type inference as the example. This is that warning met for
  real, with a different construct.
  - Workaround: none needed — the test suite caught it immediately and loudly, which is
    the system working. The cost was one confused minute reading 119 errors.
  - [moving-in:G-053] status: fixed | fixed-in: 0.31.0 | seen: 1 | harness: 0.21.0 | source: moving-in 2026-08-16
  - Improvement: `name_check --refresh-api` already downloads the engine's class list,
    so it knows `ConcavePolygonShape3D` is a type. A cheap targeted rule — flag
    `X as Type == null` and `X as Type != null` without parentheses, since the
    precedence is a trap and the parenthesised form is never wrong — would catch this
    exact shape without attempting a compile. Worth it because the construct is common
    in this project (`hit.get("collider") as CollisionObject3D` appears throughout) and
    the failure is total rather than local.

- Gap: **[G-054] — the runner does not know how many assertions a test METHOD contains.**
  It prints the suite total, and the `[VACUOUS]` check already scans a method's source
  for `_T.assert_*` calls to decide whether to expect any. It therefore has, in the same
  pass, the number it would need to say "this method contains 6 assert calls and
  executed 3". That comparison is the abort detector #27 asks for, available without
  attributing stderr to a method at all.
  - [moving-in:G-054] status: fixed | fixed-in: 0.28.0 | seen: 3 | harness: 0.21.0 | source: moving-in 2026-08-16 | also: the advisory Declared: line it proposed shipped in 0.34.0 once the reporter measured 4/2, 2/1, 2/1 vs 2/2 | dup-of: gh#27 / moving-in:G-050 (same abort-detection ask; shipped as run_tests.py's stderr-scan wrapper rather than the per-method assertion-count heuristic proposed here - the wrapper is exact where the count is advisory, and it was already in 0.28.0 before this copy was pooled)
  - Improvement: reuse the `[VACUOUS]` source scan to count `_T.assert_*` occurrences per
    method, and report `[PARTIAL] method (3 of 6 assertions executed)` when a passing
    method runs fewer than it contains. Imperfect — loops and early returns legitimately
    skip assertions — so it should be advisory like the orphan check, not a gate. But it
    would have caught all three aborts this session, and it needs no new plumbing.

## 2026-08-16 — 0.31.0: a timeout that names its cause, and a precedence trap

One fresh GitHub issue (#31), pooling surfaced one duplicate of it, one
duplicate of gh#27, and one genuinely new small rule.

**[gh#31 / plant-tower-defense:G-045] `launch`'s ping timeout now leads with
the game's own `ERROR:` line, from EITHER captured log.** The reported case:
`--import` had printed a clean-looking completion but never wrote
`uid_cache.bin`; the next launch blocked on a native `OS.alert()` dialog
("Main scene's path could not be resolved from UID... Aborting") and `launch`
reported only "the bus never answered a ping within 20s" - reading identically
to gh#28's unrelated missing-separator hang. The exact diagnostic was sitting
in `launch_stdout.log` the whole time; the old code tailed only stderr.
Extracted a `launch_log_errors()` helper that scans both logs (a pure function
of the paths, so it is testable without a real launch) and wired it into the
timeout path. Shipped the reporter's fix 2; **fix 1 (make an incomplete
`--import` observable by asserting `uid_cache.bin` got written) deliberately
deferred** - the reporter themselves flagged the mechanism as unconfirmed
("plausibly a race... the exact mechanism isn't confirmed"), the file's
presence is version-dependent, and fix 2 alone turns the same failure from a
screen-scrape into a one-line read, which was the actual cost. Logged as
follow-up, not silently dropped.

**A real Windows finding while building its test, worth recording:** the first
draft drove the timeout path with a stand-in "godot" `.bat` that never answers
the bus. It failed - and reproducing it with a direct `Popen` probe showed
**a `.bat` launched with `DETACHED_PROCESS` (which `cmd_launch` correctly
uses for a real Godot `.exe`) does not even execute its body on Windows**:
exit 1, nothing written, not a redirected-handle problem but a no-console
problem. A real `.exe` is unaffected (every launch this session proves it),
and `python.exe` cannot stand in because `cmd_launch`'s own `--path` is
python's first arg, which it rejects. So the control drives the extracted
helper directly with the reporter's exact log shape (the ERROR: line in stdout,
benign noise in stderr, plus a missing-logs tolerance case) - and since the
wiring into `cmd_launch` is one line, testing the helper IS testing the fix.
Mutation-tested: forcing the scan to find nothing failed the control naming
the exact assertion.

**[moving-in:G-053] `name_check.py` gains an `as_precedence` rule.** `x as
Type == y` with no parentheses is a hard parse error - GDScript's `as` binds
looser than `==`, so it casts to a bool - and one such line took a real suite
from green to 48 failures. `name_check`'s own `NOT COVERED` line already says
it does not compile; this is one specific, common, cheap instance of that gap
that a regex CAN see, because it is a shape and not a type question. Only
fires on a PascalCase operand after `as`; the parenthesised form is never
flagged. Planted the reporter's verbatim line into stage 2.5's bad-names
control alongside a parenthesised negative control on the very next func, and
asserted exactly one hit. **Zero false positives across `plant-tower-defense`,
`moving-in`, and `findmyballs`** - measured, per this repo's rule for any
static-analysis change (H-030), not assumed. Mutation-tested (rule un-dispatched
-> `as_precedence` missing from the expected set, control fails).

**Two duplicates reconciled by pooling:** `plant-tower-defense:G-045` = gh#31
(fixed above), `moving-in:G-054` = gh#27 (0.28.0's `run_tests.py`; the
per-method assertion-count heuristic proposed here is a weaker, advisory
version of the exact stderr scan already shipped).

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.31.0, 14 shipped files (no new bus verb; `launch_log_errors`
is a Python helper and `as_precedence` a name_check rule). `python -m unittest
discover -s tools` — 35 tests OK. `python tools/check_templates.py` — OK, all
stages, two new controls each mutation-tested. Real-project false-positive
sweep for the new static rule: 0/0/0.

## 2026-08-16 - Upstreamed 2 open gap(s) from BoomerShooter (harness 0.16.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\BoomerShooter\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`scaffold_install.py config --set` does not merge across separate invocations
  in the same run** — calling it three times (once per key: `hud_layer_name`, then
  `main_scene`, then `godot_bin`) silently reverted the *previous* call's key back to the
  shipped schema default each time, because `--set` values are apparently not folded into
  `_scaffold_defaults` until the process that set them exits, so the next invocation reads
  the file's on-disk `_scaffold_defaults` and treats the on-disk value as still-scaffold-owned,
  overwriting it. Concretely: set `hud_layer_name=UI` and `main_scene=res://scenes/main.tscn`
  in one call → correct. Then a second call setting only `godot_bin=...` silently rewrote
  `hud_layer_name` back to `"HUD"` and `main_scene` back to `""`. Worked around by passing
  all four `--set` flags in a single invocation instead of splitting them per detected value
  (which the scaffold instructions' own step 7/11 examples do split across calls).
  - [BoomerShooter:G-101] status: fixed | fixed-in: 0.20.0 | seen: 1 | harness: 0.16.0 | source: BoomerShooter 2026-08-14
  - Improvement: either document loudly in step 7/11 that all `--set` calls for one
    scaffold run must be batched into a single `scaffold_install.py config` invocation, or
    (better) fix the tool so each invocation reads and re-merges the *current* on-disk
    `_scaffold_defaults`/values before deciding ownership, so sequential `--set` calls are
    safe.

- Gap: **step 11's Godot-binary detection can resolve to a POSIX shell shim instead of a
  real Win32 executable on Windows/Git-Bash**, and nothing downstream catches it early.
  `command -v godot` found `/c/Users/gotmi/bin/godot`, a `#!/bin/sh` wrapper script
  (`exec ".../Godot_v4.7.1-stable_win64_console.exe" "$@"`) that Bash happily executes but
  `subprocess.CreateProcess` (used by `name_check.py --refresh-api` and presumably by the
  Godot-launching parts of `devtools.py`) cannot: `OSError: [WinError 193] %1 is not a
  valid Win32 application`. `name_check.py --refresh-api` failed with only `Error: could
  not read \`<path> --version\`` — no hint that the path itself was the problem. Had to
  manually `file` the resolved binary, find it was a shell script, and locate the real
  `.exe` under `Downloads/Godot_v4.7.1/` to fix `godot_bin`.
  - [BoomerShooter:G-102] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.16.0 | source: BoomerShooter 2026-08-14
  - Improvement: step 11 should validate the resolved `godot_bin` by actually invoking it
    (not just checking `-x`) and reject/skip candidates that raise `WinError 193` /
    "not a valid Win32 application", falling through to the next candidate rather than
    recording a shim path as `godot_bin`. Worth a general note for Windows/Git-Bash setups:
    `command -v` can resolve a wrapper script fine for Bash while being useless to any
    Python subprocess call the harness makes later.

## 2026-08-16 - Upstreamed 2 open gap(s) from dave-game (harness 0.18.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\dave-game\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **a project setting silently not applying has no gate at all.**
  `environment/defaults/default_clear_color=Color(0.035, 0.05, 0.055, 1)` was written into
  `project.godot` under `[rendering]`, survived the import pass, lint reported
  `0 error(s), 0 warning(s)`, `validate-all` reported `0 total issues`, and the game still
  rendered on the stock mid grey. Nothing in the harness reads project settings back from the
  running game, so the only detection was opening a PNG. Workaround: called
  `RenderingServer.set_default_clear_color()` in `_ready()` instead.
  - [dave-game:G-003] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.18.0 | source: dave-game 2026-08-15
  - Improvement: a `project-settings [--filter PREFIX]` verb returning `ProjectSettings.get_setting()`
    for the live session, so "did the setting I wrote actually land" is one read instead of a
    screenshot. `get-state` cannot do it — `ProjectSettings` is not a node in the tree.

- Gap: **`sample-pixels` / `screenshot` are the only way to catch a light-and-shadow
  regression, and neither is assertable.** Every visual fix this session (ambient at 0.20
  rendering the room as a black void, the player washing out to a featureless disc under its
  own spill light, the tipped carboy covering the flask readout the player pours by) was found
  by opening a PNG and looking at it. `validate-ui` correctly reported `No UI issues found`
  throughout — it is a layout check and these are none of its business.
  - [dave-game:G-004] status: open | seen: 1 | harness: 0.18.0 | source: dave-game 2026-08-15
  - Improvement: `sample-pixels --rect` already returns mean/dominant colour; what is missing
    is the ability to name a rect and a baseline together. A `save-pixel-baseline` /
    `pixel-diff` pair keyed on named rects (the way `save-ui-baseline` already works for
    layout) would turn "is the room still readable" into a gate rather than an inspection.

## 2026-08-16 - Upstreamed 2 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **I reproduced the harness's own documented `validate-ui` bug inside a test I
  wrote** — the first version measured overflow as `Control.global_position + Control.size`,
  which adds a **scaled** position to an **unscaled** size. `ResultsScreen` scales itself
  through `UiTheme.fit`, so the sum overstates the layout by 1/scale, and it reported a
  104 px overflow on a screen that fitted. The correct reading is
  `c.get_global_transform() * Rect2(Vector2.ZERO, c.size)`. The `/verify` notes describe
  this exact failure for `validate-ui` before 0.17.0 — "measured Controls in CanvasLayer
  space against a viewport measured in pixels" — and I walked into it anyway, because the
  note is filed under a *tool's* history rather than as a thing to know when measuring UI.
  There was a real overflow underneath, which is luck, not vindication.
  - [moving-in:G-026b] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: put the rule where someone measuring UI will hit it — a Generic Pitfall
    in `/verify` Phase 4 saying that `Control.size` is local and `global_position` is not,
    that the two must never be added, and giving the one-line transform that is correct.
    Better still, expose it as a verb: `node-bounds` already returns screen-space rects
    with ancestor CanvasLayer transforms applied, so `node-bounds --recursive --outside`
    could answer "what is off screen" without anyone re-deriving the arithmetic.

- Gap: **nothing checks that a shipped scene enables the flags the shipped game needs.**
  `show_title` sat false in `house.tscn` for the whole project and no gate could care:
  lint validates scene structure, `validate-all` reports 0 issues, and every test that
  instantiates `UnpackUi` deliberately wants the title OFF. The only way to notice is to
  boot the game and look at the first frame.
  - [moving-in:G-027b] status: fixed | fixed-in: 0.23.0 | seen: 1 | harness: 0.16.0 | source: moving-in 2026-08-14
  - Improvement: a `first-frame` verb — launch, advance N frames, and report what the
    player can actually see (visible CanvasLayers, topmost interactive Control, whether
    the tree is paused, whether the cursor is captured). It is the one state every game
    has and no automated check ever looks at, and it would have printed "no Control above
    the HUD; cursor captured; tree running" for a game that should have opened on a menu.

## 2026-08-16 — 0.32.0: ten things, chosen by reading the whole pool — and the pool itself was lying twice

Brief for this turn: land 0.31.0 on master (it already was — `3c0faaf`, pushed),
then read the open GitHub issues, `PURPOSE.md`, this log and the project logs
(`plant-tower-defense`, `moving-in`, `findmyballs`, `BoomerShooter`, `dave-game`,
`harness-test-1`) and pick the ten changes most likely to keep the tool useful,
without treating any request as mandatory. Pooled all six logs first
(`upstream_gaps.py`), which is where two of the ten came from before a single
project gap was read.

**What the reading actually said.** Of the plant log's 20 open gaps at 0.21.0+,
**twelve were already fixed here** (0.24.0–0.31.0) and still open there because the
project runs 0.25.0; `moving-in` runs 0.21.0. The marketplace clone on this machine
is itself at 0.25.0 (`~/.claude/plugins/marketplaces/godot-selftest-harness`, last
updated 14:51 today — six releases ago). So the single largest "usefulness" fact in
the pool is not a missing verb: it is that **fixes are not reaching the projects**,
and the projects go on logging real friction against bugs that no longer exist. That
became item 9 and a new commitment in `PURPOSE.md` ("A fix is delivered when the
project runs it, not when it ships"). The user-side action it cannot do for them:
`/plugin marketplace update godot-selftest-harness`, then `/scaffold-godot-harness`
in each project.

**The ten, and what shipped:**

1. **[H-063, new — fixed] `upstream_gaps.py` pooled `**no new gap.**` bullets as
   OPEN gaps.** This turn's own pooling run appended `moving-in:auto-0d4eca` and
   `auto-628dc7`, both "no new gap" absence markers, because `_NO_GAP_RE` knew only
   the spelling `no gaps this turn`. Broadened to every absence spelling seen in the
   six logs (`no new gap`, `no harness gaps`, `none this turn`, `nothing new`), with
   a negative control for the commonest real-gap openings (`no way to…`, `no verb…`,
   `None of the three gates…`) — the first draft's `none\b` matched that last one and
   the unit test caught it. Removed the two bogus entries from this log. **[H-059 —
   fixed] the second of two source entries sharing one id is no longer dropped:** a
   collision (both `seen: 1`) is appended as `G-NNNb` and announced with `!`; a
   recurrence (`seen: 2+` on the later block) still collapses. Re-pooling `moving-in`
   immediately surfaced two real ones — `G-026b` (measure Controls in viewport
   space; already what `validate-ui`/`reachable-ui` do since 0.17.0/0.19.0) and
   `G-027b` (the `first-frame` verb, shipped 0.23.0) — both closed on arrival.
   `tools/test_upstream_gaps.py` (4 tests) is new; the tool had none.
2. **[plant-tower-defense:G-019 — fixed] `set-state` rebuilds a JSON array as the
   property's typed Array.** Probed first (H-033): `Array(value, typed_builtin,
   class, script)` converts String→StringName and drops the lot with an engine
   error on an inconvertible element — the size check turns that into a refusal
   with a count. Contract rows: `["corn","sun"]` → `Array[StringName]` lands with
   `coerced: true`; `[1,"x"]` refused.
3. **[plant-tower-defense:G-016 — fixed] `step-time --then-pause` + `Wall clock:`.**
   Read the handler before building: the verb never paused the tree, and the CLI
   help said `Pause the tree and advance it` — a claim wider than the behaviour, in
   the client's own `--help`. Fixed the help; added `then_pause` (pause the moment
   the step lands, lifting a pre-existing pause for the step itself) and printed
   the `elapsed_wall_ms` the reply already carried. Contract row asserts
   `paused_after: true`, followed by an `unpause` row.
4. **[plant-tower-defense:G-005 — fixed] `find-nodes --call METHOD`.** A zero-arg
   getter read beside each hit; a missing method lands in `call_errors`, never
   aborts. `check_find_nodes_calls()` asserts `get_class()=Button` on the fixture.
5. **[H-046 — fixed] dotted paths read built-in struct components.**
   `_resolve_property_path` gained a `_BUILTIN_COMPONENTS` table (Vector2/3/4,
   Color, Rect2, Quaternion, Plane, AABB, Basis, Transform2D/3D, Projection) and
   reads `value[segment]` — probed first: Variant indexing by member name works, `in`
   does not (`Invalid operands 'String' and 'Vector3' in operator 'in'`, and the
   probe hung on it, which is the CLAUDE.md gotcha exactly). `find-nodes` now
   carries `property_errors` and prints `<unresolved: reason>` instead of `null`.
   First `--full` run failed to PARSE: `PackedStringArray([...])` is not a constant
   expression inside a `const` Dictionary — plain arrays are. Caught by stage 3, as
   designed.
6. **[plant-tower-defense:G-030 — fixed] `findings` / `validate-ui` persist the last
   non-clean run** to `user://findings_last.json` and print the path when the count
   is non-zero. `check_findings_aggregate` reads the file back and asserts its count
   equals the reply's (the planted defects make the run non-clean by construction).
7. **[dave-game:G-003 — fixed] `project-settings` verb** (`--filter PREFIX` /
   `--name KEY`, a missing key exits 1 under `missing`). Two contract rows.
8. **[BoomerShooter:G-101 / G-102 — reproduced, one already fixed, one improved.]**
   G-101 (sequential `config --set` reverting earlier keys) reproduced clean on
   0.31.0 — gh#7's guard fixed it in 0.20.0; closed as such. G-102 (a Git-Bash shell
   shim recorded as `godot_bin`): step 11 already resolves a `#!` wrapper to its
   exec target; what remained was `name_check.py --refresh-api` saying only
   ``could not read `<path> --version` `` — it now appends the OSError, and names
   "not a Win32 executable (a shell wrapper script?)" on WinError 193.
9. **[H-064, new — fixed] `harness-version` reports the versions this machine can
   offer** — `$CLAUDE_PLUGIN_ROOT`, the plugin cache (`installed_plugins.json`), the
   marketplace clone — and says when one is newer than the project's install. Every
   value is a file on disk; nothing asks the network, so it can only ever
   under-claim. Verified against `plant-tower-defense`: `Machine: plugin cache
   0.25.0, marketplace clone 0.25.0` — honest, and the reason nothing newer is
   offered is the stale clone above.
10. **Docs / PURPOSE / loop-closing.** `PURPOSE.md`: "It shares the machine" now
   covers the working tree (H-061 / H-052) and the new delivery commitment.
   `press` not clearing tooltips (plant G-020) is documented as a sharp edge in
   `REFERENCE.md` and the cheat-sheet rather than emulated — a synthetic mouse event
   would change what `press` *is*. gh#20's last open item (#3, static RefCounted
   invisible to reach) has been answered by 0.29.0's self-report API; closed with a
   comment. **[H-062 — fixed]** the three `--full` contract rows that failed on a
   clean tree since 0.19.0–0.25.0: `clear_nodes` (the row expected success from a
   class gh#15.2 now correctly refuses — split into a refusal row and a real
   `RigidBody2D` row asserting `count: 0`), `reachable_ui` (4/3 → 11/6: the fixture
   grew six Shop rows and `Overflowing`; the note now lists what each number is
   made of), and `raycast` — **a real bug in the fixture, not the row**:
   `harness_set_wall_2d(true)` looked the removed wall up by path, found nothing,
   and returned quietly, so every row after `check_raycast_3d` ran on a tree with
   no 2D collider. Held by reference now; the 2D row asserts `clear: false` and a
   3D row was added beside it.

**Considered and not done:** dave-game:G-004 (named-rect pixel baselines) — a
real feature, larger than one turn, and `sample-pixels` + `pause` already make
the manual version cheap; moving-in:G-052 (`collider-planes`) — three sightings,
one project, 3D-placement-specific, and the placement-audit skill covers it
outside the bus; plant G-031 (`asset_contract` coverage class) — naming a class no
shipped checker fills would be a check that can only report UNCHECKED forever;
plant G-028 / gh#20 #3 — declined for the reason 0.24.0 gave and answered by
0.29.0's `mark_script_reached()`.

- Gap: **the fixture's own restore helper was a silent no-op for seven releases,
  and the stage that would have said so was opt-in.** `--full` was run this turn
  because H-062 asked for it per release; the raycast row's failure had been read
  as "row drift" and was actually `harness_set_wall_2d(true)` returning `false`
  after `remove_child` made the wall unreachable by path. Nothing in the fixture
  checked the helper's return value.
  - [H-065] status: fixed | fixed-in: 0.32.0 | seen: 1 | harness: 0.32.0
  - Improvement: shipped — hold the wall by reference; the row asserts `clear:
    false` so a missing wall fails the row on content, not only on shape.

- Gap: **no gap in the harness for this — a note on the machine.** The marketplace
  clone is six releases stale and the plugin cache with it, so no project on this
  box can be refreshed past 0.25.0 until `/plugin marketplace update
  godot-selftest-harness` is run by the user; `harness-version` now says what it
  can see, and cannot see further than that.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.32.0, 14 shipped files, 56 bus verbs + 58 CLI commands
documented. `python -m unittest discover -s tools` — 39 tests OK (35 + 4 new).
`python tools/check_templates.py --full` — three runs: (1) FAILED at stage 3
parse (`PackedStringArray([...])` in a `const` Dictionary is not a constant
expression; fixed to plain arrays), (2) a batched 5-site mutation of
`dev_tools.gd` (`git diff --stat` showed the mutation landed; `grep -c` 5)
FAILED naming every mutated check — `find_nodes --call get_class should report
'Button'`, `findings found 6 thing(s) but reported no last_findings_path`,
`contract set_state: ... read back ["seed"] ... coerced is None, expected True`,
`contract step_time: data['paused_after'] is False, expected True`, and H-046's
mutation surfaced inside the find_nodes check as `position.x: None` — restore
proved byte-identical by `cmp`, then (3) clean: OK, `stage 6 contract: 88/88 rows
passed` (was 83/86 on a clean 0.31.0 tree). `name_check.py` on
`plant-tower-defense` / `moving-in` / `findmyballs`: 0/1/3, 0/0/3, 2/24/23 —
identical to master's counts, so the message change moved no finding.

## 2026-08-16 — 0.33.0: gh#32 arrived while 0.32.0 was landing, and it is the same fact from the other side

gh#32 was filed twenty minutes after 0.32.0's item 9 (H-064) went in, by a session
that hit the same distribution lag from the scaffold side: the skill loads from a
plugin cache pinned at one version and every path in it is interpolated from that
root, so `/scaffold-godot-harness` "to upgrade" reinstalls 0.21.0 over 0.21.0 and
reports `updated from 0.21.0 (unmodified - no backup needed)` on every file — and,
sharper, a *stale* cache meeting a *newer* vendored harness downgrades it silently
by construction, because a file matching `harness_history.json` is pristine and
overwritten with no `.bak`. The reporter's diagnosis and fix were both right and
both cheap; shipped as machinery rather than only prose, so CI, `check_templates.py`
and a grader are protected too, not just a reader of the skill.

**[gh#32 — fixed] `scaffold_install.py` names the transition before touching
anything, and refuses a downgrade.** `version_transition()` runs first in `full`
and `files`: plugin root's `plugin.json` against the project's
`_scaffold_defaults.harness_version` (falling back to the installed
`# harness-version:` stamp for a pre-record install) → `[version] fresh install of X`
/ `already at X - this is a same-version refresh, not an upgrade (…the plugin root
you are installing from is pinned at X…)` / `upgrade Y -> X` / `DOWNGRADE Y -> X`.
A downgrade exits 2 with nothing written unless `--allow-downgrade`; `full` ends
with `[full] harness: <transition>` — the reporter's "one line a reader wants".
Step 3 and Step 13 of the slash command carry it into the summary. Seven tests in
`tools/test_scaffold.py` (fresh / same / upgrade / refused-installs-nothing /
allow-downgrade proceeds / stamp fallback / CLI round-trip prints the line).

**Considered:** having the guard *fetch* the newest release and say "0.31.0 exists".
No — every other line the tool prints is a fact about a file on disk, and one line
that depends on the network would be the one a reader could not tell from the
others when it was wrong. `harness-version` (0.32.0) already says what the machine
holds; the guard says what this call is; the user does the update.

- Gap: no new gap this turn.

**Validation run this turn:** `python tools/record_version.py --record` then
`--check` — OK at 0.33.0, 14 shipped files (stamps only; no template body changed).
`python -m unittest discover -s tools` — 46 tests OK (39 + 7). `python
tools/check_templates.py` — OK, all stages; its own stage 2 now prints `[version] fresh
install of 0.33.0` through the same installer users get.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`findings` and the layout gates have no concept of a minimum gap between
  two Controls** — `python tools/devtools.py findings` reported
  `0 finding(s) across 4 of 5 checks` over a Keys screen whose "← Back" button sat
  at y=528 directly under a row button ending at y=528. `ui_layout` measures a
  Control against its own box, and the project's own pair-wise checks
  (`test_the_pause_card_lists_the_keys_and_still_fits_its_paper`, and the helper
  written this session) use `Rect2.intersects`, which is false for boxes sharing an
  edge. So "not overlapping" passes for "touching", and touching is what reads as
  broken. Worked around with an explicit `assert_gte(gap, 16.0)` in the test.
  - [plant-tower-defense:G-046] status: fixed | fixed-in: 0.34.0 | seen: 1 | harness: 0.25.0 | source: plant-tower-defense 2026-08-16
  - Improvement: give the UI checks a `min_control_gap` threshold in
    `devtools_config.json` (default 0 = today's behaviour) and have the sibling
    comparison report `controls_touching` as its own finding class, so a flush
    edge is named rather than being indistinguishable from a laid-out one.

## 2026-08-16 - Upstreamed 1 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.31.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-055] — `harness-version` cannot answer without a running game, and says so
  in a way that reads like a failure.** Asked for the version to stamp this entry; got
  `(game not running: 'harness_version' was never picked up ...)` on stderr and then the
  answer I wanted, `Client: 0.31.0 (tools/devtools.py)`, printed underneath. The client
  version is a static read of a file on disk and needs no bus at all. The log entry
  format requires this value on every turn, including turns with no game running, so
  this warning fires constantly and trains the reader to skip it.
  - [moving-in:G-055] status: fixed | fixed-in: 0.34.0 | seen: 1 | harness: 0.31.0 | source: moving-in 2026-08-16
  - Improvement: when the bus is unreachable, print the client version alone and exit 0
    without the `game not running` preamble — or take a `--client` flag that never opens
    the bus. The installed version is the one the log entry wants.

## 2026-08-16 — 0.34.0: loop tick two — four new sightings, four small fixes, not a second ten

The loop re-fired an hour after 0.32.0/0.33.0. No new GitHub issues; the plant and
moving-in logs had moved (pooled: 2 new gaps, 3 `seen:` bumps). Read them and acted
on what was actually new instead of inventing another top-ten — the honest size of
this tick is four items. Noted in passing: `plant-tower-defense` and the marketplace
clone are now at 0.32.0, so the user ran the update the last entry asked for.

- **[plant-tower-defense:G-046 — fixed] `min_control_gap` → `controls_touching`.**
  `Rect2.intersects` is false for a shared edge, so "not overlapping" passed for
  "touching". New config key (default `0` = old behaviour); when set, `validate_ui`
  / `findings` report non-overlapping interactive pairs closer than that on both
  axes. **The first draft of its stage-5 control failed organically** (`got []`):
  the Shop rows I meant to use sit under `Main`, a Node2D, and everything under a
  Node2D is world-space, which validate_ui's interactive walk deliberately excludes
  — found by a debug print in a probe copy, not by reasoning. The control now
  plants two flush Buttons on their own CanvasLayer via a fixture helper, held by
  reference and removed afterwards (H-065's lesson applied the same day), flips the
  live config through the same dotted `set_state` a project would use, asserts
  `0px apart`, and asserts nothing fires at the default.
- **[moving-in:G-055 — fixed] `harness-version --client`** never opens the bus;
  the log format and cheat-sheet now point at it for the `harness:` field.
- **[moving-in:G-054 / gh#27 — shipped, advisory] `run_tests.py` prints
  `Declared: N assertion call site(s) across F test file(s); M executed`**, with a
  "written but not run" clause when M < N; skipped under `--filter`/`--file`.
  0.31.0 declined this as weaker than the stderr scan; the reporter came back with
  a measurement (4/2, 2/1, 2/1 for three aborts vs 2/2 for the pass) and that is
  the bar. Stage 4 asserts it on the planted abort — `7 declared, 6 executed` —
  and asserts absence under `--filter`. On plant's real 248-test suite: `2276 …
  7962 executed (loops or helpers run some sites more than once)`.
- **[plant-tower-defense:G-044 — 4th sighting] `import_check.py`** on a crash
  with no findings now reports how many finished artifacts `.godot/imported`
  gained, its `.tmp` count, the last `[ N% ] reimport | file` line (the asset the
  crash was on), and — when nothing was gained — names seeding the cache from a
  sibling checkout as the legal way out (keyed on `res://` paths).

**Not done, on purpose:** plant G-028 (3rd sighting, static RefCounted) — answered
by 0.29.0's `mark_script_reached()`; the project is on 0.32.0 and can use it now.

- Gap: **my own rule failed twice more this turn** — two heredoc edit scripts
  carrying backslashes were mangled (one emitted a literal `% BT` into
  `check_templates.py`, caught by a NameError at stage 2). The memory note was
  sharpened an hour earlier and still did not fire before typing. Recording the
  count because it is the evidence: six in one day.
  - [H-066] status: open | seen: 1 | harness: 0.34.0
  - Improvement: none the harness can make; the fix is a habit. Defined `BT`/`BQ`
    constants in `check_templates.py` so fixture lines never need an escape.

**Validation run this turn:** `record_version.py --record` then `--check` OK at
0.34.0 (14 files, 56 verbs + 58 CLI). `unittest discover -s tools` — 46 OK.
`check_templates.py --full` — first run FAILED on the new control (`got []`, the
world-space finding above), then OK with `stage 6 contract: 88/88` and `stage 5
bridge: min_control_gap=4 names the planted flush pair as controls_touching (0px
apart); 0 reports none; pair removed`; a default run afterwards for the stage-4
Declared control — OK, `7 … 6 executed -- 1 written but not run`. `harness-version
--client` and `run_tests.py` exercised against `plant-tower-defense` live.

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0, 0.32.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`--isolated` isolates the bus, so a live check that exercises a persisted
  setting has to write through the developer's real save and put it back by hand.**
  Reading staged milestones back was safe (`set-state /root/RunConfig
  earned_milestones` calls no `_save()`), but exercising the colourblind toggle at
  all goes through `set_colorblind_safe()`, which writes `user://highscore.save` on
  every press. The workaround was to read the original values first
  (`colorblind_safe: false`, `earned_milestones: {}`), stage, screenshot, then press
  the key an even number of times and re-read to confirm — a discipline nothing in
  the harness enforces and which a crash mid-check would have skipped, leaving the
  developer's own save altered by a verification run. (The 2026-08-16 entry above
  files the *merge* half of this as "not a harness gap"; this is the other half —
  not two checkouts disagreeing, one checkout mutating state it only meant to read.)
  - [plant-tower-defense:G-047] status: fixed | fixed-in: 0.35.0 | seen: 2 | harness: 0.32.0 | source: plant-tower-defense 2026-08-16
  - Improvement: a `--snapshot-userstate` flag on `launch` that copies `user://*.save`
    aside and restores it on `quit` (or on the next launch, if the game died) would
    make a live check that touches persisted settings safe by default rather than by
    convention. It needs no `user://` isolation to work.

## 2026-08-16 - Upstreamed 1 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.31.0, 0.33.0 (cache) / 0.31.0 (vendored))

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-056] — `scene-tree` output cannot be counted, and the obvious way to count
  it is wrong.** `scene-tree --root /root/Sfx --depth 1 | grep -ci ambient` returns 2
  for a single node, because each node prints both a `"name"` and a `"path"` line. The
  authoritative answer needs `find-nodes --class AudioStreamPlayer --where
  name=SfxAmbient_rain`, which prints `1 node(s) matched`. That is a fine workaround
  once known, but the failure is silent and reads as a real duplicate — the exact thing
  I was testing for, since a stacked audio loop is what `play_ambient()` guards against.
  - [moving-in:G-056] status: fixed | fixed-in: 0.35.0 | seen: 1 | harness: 0.33.0 (cache) / 0.31.0 (vendored) | source: moving-in 2026-08-16
  - Improvement: have `scene-tree` print a trailing `N node(s)` denominator the way
    `find-nodes` already does. Every other verb in this harness ends with a count and
    that is the house style; this one leaves the reader to derive it from JSON, and the
    derivation has a trap in it.

## 2026-08-16 — 0.35.0: loop tick three — three new sightings, three fixes

Same discipline as the last tick: no new issues, two logs moved, act on what is new.

- **[plant-tower-defense:G-047 — fixed] `launch --snapshot-userstate [GLOB ...]`**
  copies matching `user://` files (default `*.save`) aside; `quit` restores them and
  removes files the run created under those globs; a stale snapshot from a game that
  died is restored by the next `launch`. Round-trip unit-tested (mutated, created and
  untouched-outside-pattern files all behave). `--isolated` never isolated `user://`
  and this is the half of that limit a check can actually be made safe against.
- **[moving-in:G-056 — fixed] `scene-tree` prints `N node(s) in this subtree`** on
  stderr; unit test pins that the JSON line-count trap (2 lines per node) is real.
- **[plant-tower-defense:G-044 — 5th sighting] `import_check.py`** retries a crashing
  `--import` while it still makes progress (artifacts grew or the last reimport line
  moved), up to 4 attempts; the observed failure needed two retries and the cap was one.

Not done: G-054 seen 3 (0.33.0 cache) — the `Declared:` line shipped in 0.34.0, one
release after that sighting's cache; nothing further. G-028 3rd sighting — 0.29.0's
self-report API is the answer and the project now runs 0.32.0.

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.35.0
(14 files, 56 verbs + 58 CLI). `unittest discover -s tools` — 49 OK (3 new).
`check_templates.py` — OK, all stages (client-side changes only; no bus verb touched,
so the default run is the right gate and `--full` was run for 0.34.0 an hour ago on
an identical `dev_tools.gd`).

## 2026-08-16 - Upstreamed 1 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.31.0, 0.33.0 (cache) / 0.31.0 (vendored))

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-057] — nothing measures a constant's MARGIN, and it keeps having to be
  hand-rolled.** Three thresholds in this project now have bespoke edge-case gates:
  `MIN_CEILING_AREA`, `MIN_SOLIDITY` (`test_shelf_finder.gd:293`), and now
  `MIN_PLANES_FOR_SHELVES` — each a hand-written sweep plus a recorded table of the
  models sitting near the line. `MAX_FRONT_COVER` still has none and cannot get one,
  because `_front_cover()` is private. The shape is identical every time: sweep a
  corpus, compute one number per item, record who is within epsilon of the threshold,
  fail on anyone new joining or any recorded value moving.
  - [moving-in:G-057] status: fixed | fixed-in: 0.36.0 | seen: 1 | harness: 0.33.0 (cache) / 0.31.0 (vendored) | source: moving-in 2026-08-16
  - Improvement: a `_T.assert_margin(values: Dictionary, threshold: float,
    margin: float, recorded: Dictionary) -> String` helper in the test harness would
    collapse all three to one call and make the fourth cheap enough to write. The
    pattern has now recurred three times in this project alone, which is the bar for
    lifting it out of the project and into the harness.

## 2026-08-16 — 0.36.0: loop tick four — gh#33 (the report half of the user:// problem) and a lifted test helper

One new issue (gh#33), one new gap (moving-in:G-057). gh#33 is the consequence half
of what 0.35.0's `--snapshot-userstate` guards against, filed by a session running
0.33.0: a live pass altered the developer's real save, an autoload read it at the next
start, and three headless tests went red for a reason nothing in the output pointed at.
The reporter's fix (a) — stat `user://` at launch and name what changed at quit — is
better than the opt-in flag alone, because it costs nothing and turns a silent mutation
into a named one every time; shipped always-on. Fix (b) — the launch line says the
failure, not the fact — shipped as written.

- **[gh#33 — fixed] `quit` names every `user://` file the run changed / created /
  deleted** (bridge files excluded), or says `no file changed`; `launch --isolated`'s
  `user://` line now names the failure mode. Unit-tested (changed/created/deleted +
  bridge-file exclusion; untouched run reports nothing). Together with 0.35.0's
  `--snapshot-userstate` this closes both halves of the issue.
- **[moving-in:G-057 — fixed] `_T.assert_margin(values, threshold, margin, recorded)`**
  — the threshold-margin gate one project had hand-rolled three times. Stage 4 plants
  a recorded set that passes and a new near-the-line item that must be refused; the
  second planted test PASSES only when the helper FAILS, so a helper that always
  returns "" fails the stage by construction.

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.36.0
(14 files, 56 verbs + 58 CLI). `unittest discover -s tools` — 51 OK (2 new).
`check_templates.py` — OK, all stages, stage 4 line quoting the assert_margin control
(no bus verb touched; `--full` last ran clean on 0.34.0's identical `dev_tools.gd`).

## 2026-08-16 - Upstreamed 4 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0, 0.32.0, 0.33.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`node-bounds` crashes on a Button whose text contains a non-cp1252
  character.** `python tools/devtools.py node-bounds .../KeysScreen/BackButton`
  exited with a Python traceback ending `UnicodeEncodeError: 'charmap' codec can't
  encode character '←' in position 17` at `cmd_node_bounds`, devtools.py:3096
  (`print(f"  Text: ...")`). The button's label is the left-arrow + " Back" that
  every overlay in this game uses, so the most obvious verb to point at an
  overlay's Back button is the one that cannot print it on a default Windows
  console. Workaround: `PYTHONIOENCODING=utf-8` in front of the command, which is
  not discoverable from the traceback.
  - [plant-tower-defense:G-048] status: fixed | fixed-in: 0.37.0 | seen: 1 | harness: 0.33.0 | source: plant-tower-defense 2026-08-16
  - Improvement: reconfigure stdout once in `main()` -
    `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` - so no verb can
    take the whole client down over a character in game text. Failing that,
    `_printable()` should strip unencodable characters, since it is already the
    function every text field is routed through.

- Gap: **nothing measures a Control's BOX against the panel it is drawn on when
  the two are siblings.** This is the half of `neg` that no gate could see, and it
  is distinct from the text-fitting gap above.
  `python tools/devtools.py findings` reported `0 finding(s) across 5 of 5 checks`
  against a live game whose pause card had 34px of legend hanging off the paper.
  Every check was right to: `ui_layout` measures a Control against its own box and
  its own parent, and `KeyRow4`'s parent is `PauseScreen` (full-viewport), not
  `Card` — the paper it visibly belongs to is a SIBLING, so "inside its parent" is
  trivially true. `validate-ui`'s `ui_text_trimmed` measures text against
  `control.size` and passed too, because after the clamp `size` had *become* 326.
  The project's own pause-card test checked vertical fit and pairwise overlap, and
  a Label sticking out sideways over a backdrop overlaps nothing.
  Workaround: hand-written, per-screen — `right = row.global_position.x +
  row.size.x` asserted against `card.global_position.x + card.size.x`, with the
  card found by node name. That is the third screen in this project to grow its
  own bespoke version of "stays on the paper", after
  `NotebookScreen.SUBHEAD_MAX_WIDTH` and now `PauseScreen.KEY_ROW_MAX_WIDTH`.
  - [plant-tower-defense:G-048b] status: fixed | fixed-in: 0.37.0 | seen: 1 | harness: 0.33.0 | source: plant-tower-defense 2026-08-16
  - Improvement: a `contained-in --node PATH --within PATH` verb, and a
    corresponding `ui_escapes_panel` check driven by an opt-in map in
    `devtools_config.json` (`{"PauseScreen/KeyRow*": "PauseScreen/Card"}`). The
    generic version is guessable without config too: for each visible Panel, flag
    any SIBLING Control that overlaps it and is not fully inside it. A Control half
    on and half off a piece of paper is a defect in every UI, and it is currently
    invisible to every check this harness ships.

- Gap: **the headless suite rewrites the developer's real `user://highscore.save`,
  and no gate says so.** Four tests in `test_selftest.gd` (`:679`, `:1014`, `:4326`,
  `:4921`) stage low scores in memory and call `RunConfig.record_score()` while
  `RunConfig.save_path` is still the real file; `record_score` calls `_save()`.
  Observed across two full runs: `v5/308/5008` -> `v6/310/5010` -> `v6/2/2`. Both high
  scores destroyed, recovered only from a copy taken into the scratchpad before the
  work started. Every one of those tests stashes and restores the in-memory scores,
  which is exactly what hides it — the FILE keeps the last number written, and the
  suite reports `ALL TESTS PASSED`. Filed as `plant-tower-defense-csl`.
  - [plant-tower-defense:G-048c] status: fixed | fixed-in: 0.37.0 | seen: 1 | harness: 0.33.0 | source: plant-tower-defense 2026-08-16
  - Improvement: the harness knows `test_dir` and it knows `user://`. A `/verify`
    step that snapshots `user://` before the suite and diffs it after — printing
    `user:// writes: N file(s) changed by the suite` as a denominator — would turn
    this from an invisible loss into a line. Advisory is enough; a test suite
    legitimately writes `user://`, but a suite that writes a file NO test named a
    path for is a suite driving production state. Related to gh#33/gh#28: with two
    agents in two worktrees sharing one `user://`, this is not a niche case.

- Gap: **`run_tests.py` silently ignores a `--select` passed after `--`.** `python
  tools/run_tests.py -- --select test_economy` printed `Selected: 491 of 491
  discovered  (no selector)` and ran the whole suite. `run_tests.py --select ...`
  without the `--` errors correctly (`unrecognized arguments: --select`), so the
  passthrough form is the one that fails quietly. Cost here was small (two full
  ~90s runs where one file would have done); the shape is the harness's own
  documented worst failure mode — a denominator that reads fine while describing a
  different run from the one you asked for.
  - [plant-tower-defense:G-049] status: fixed | fixed-in: 0.37.0 | seen: 1 | harness: 0.33.0 | source: plant-tower-defense 2026-08-16
  - Improvement: `run_tests.py` should forward everything after `--` into
    `run_tests.gd`'s own argument parsing, or, failing that, `run_tests.gd` should
    exit 2 on an argument it does not recognise rather than printing
    `(no selector)` beside a full-suite run. The parenthetical is already the
    evidence; it just isn't fatal.

## 2026-08-16 - Upstreamed 1 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.31.0, 0.33.0 (cache) / 0.31.0 (vendored))

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-058] — the runner counts assertions and tests but not ERRORS, so a run
  that emits engine errors on every pass reports clean.** `Array is in read-only
  state.` appeared once per run for an entire cycle. `run_tests.gd` prints `Total`,
  `Selected`, `Autoloads`, `Assertions` and `Suite` — five denominators, none of which
  is "how many errors did this run emit". Related to [G-054]/#27 but strictly simpler:
  that one needs per-method attribution, this one needs a count.
  - [moving-in:G-058] status: fixed | fixed-in: 0.37.0 | seen: 2 | harness: 0.33.0 (cache) / 0.31.0 (vendored) | source: moving-in 2026-08-16
  - Improvement: `tools/run_tests.py` already wraps the runner specifically to catch
    `SCRIPT ERROR` text the `-> String` return value cannot carry. Have it count
    `ERROR:` lines too and print `Errors: N emitted` beside the other denominators.
    PRINT BEFORE GATING — some engine errors may be legitimate for a test exercising a
    failure path, and the clean baseline should be measured before zero is assumed
    reachable. Filed locally as `moving-in-bjh` as well, because this project can do
    the printing half itself without waiting on the harness.

## 2026-08-16 — 0.37.0: loop tick five — two issues, five gaps, six fixes, and the tool caught me

Two new GitHub issues (#34, #35) and five new gap entries (plant G-048/b/c, G-049,
moving-in G-058) — the biggest tick since the first. All six shipped; the plant
G-048 pooling shows the 0.32.0 collision fix (`G-048b`, `G-048c`) working on real
input: three different gaps had been handed one id and all three arrived.

- **[gh#34 / plant G-048 — fixed] every Python client reconfigures stdout/stderr to
  UTF-8 with `errors="replace"`** (`devtools.py`, `run_tests.py`, `import_check.py`,
  `name_check.py`). A `← Back` caption killed `node-bounds` on a cp1252 console and
  the traceback pointed at the node, not the reporting.
- **[gh#35 / moving-in G-058 — fixed] `run_tests.py` prints `Engine errors: N ERROR:
  line(s) emitted`**, quoted, **never gated** — the reporter measured a clean baseline
  of two legitimate ones, so zero is not the threshold. Probed first: `push_error`
  prints plain `ERROR:` on 4.7.1, so a deliberate one under test is counted, not
  gated; stage 4 plants exactly that and asserts count 1 and exit 0.
- **[plant G-048b — fixed] `ui_escapes_panel` + `contained-in` verb.** A Control whose
  centre sits on a SIBLING Panel but whose box hangs off it (a legend row past a pause
  card) passed every gate because its parent is the screen. Centre rule + skip a
  Control that contains the panel (backdrops). Stage 5 plants Panel + escaping Label +
  inside Label; stock fixture must report none. **False-positive sweep on the real
  plant game, live: title, pause, notebook, keys and options screens — 0 findings**, and
  `contained-in KeyRow4 within Card` read `inside` (the project already clamps it).
  moving-in not swept this tick (3D game, no panels on the boot screen).
- **[plant G-048c — fixed] `run_tests.py` prints `user:// writes: N file(s) changed by
  the suite`** — four tests staged scores through a real `_save()` and destroyed both
  high scores across two runs; advisory, but a suite writing a file no test named is
  driving production state. Uses `devtools.py`'s stat helpers when it sits beside
  the wrapper; says "not checked" otherwise.
- **[plant G-049 — fixed] `run_tests.gd` refuses an unknown argument** (exit 2, named).
  `-- --select x` used to run the whole suite as `(no selector)`.

**The tool caught me.** The false-positive sweep ran the new addon in a throwaway
copy of `plant-tower-defense`; I set `config/custom_user_dir_name` and forgot
`config/use_custom_user_dir=true`, so the copy used the developer's REAL `user://`.
`quit` printed `user://: this run wrote the developer's REAL user data … changed:
highscore.save` — 0.36.0's report, doing exactly its job on its second day. The file
was rewritten with identical content (`v6 / 308 / 5008`, size unchanged; only mtime
moved — `record_score` only ever raises and the probe scored nothing), so no harm,
but the correct move was `launch --snapshot-userstate`, which exists precisely for
this, and I did not reach for it. Recording it as the evidence gh#33 asked for.

- Gap: **`launch` should tell you when a copy of a project shares another checkout's
  `user://`.** The probe's `custom_user_dir_name` was ignored because
  `use_custom_user_dir` was not set, and the launch line printed the shared path
  without saying "this is the same directory `../plant-tower-defense` uses". The
  owner file already carries the checkout path; a second checkout of the same
  `config/name` on one machine is the fan-out case, not an edge one.
  - [H-067] status: fixed | fixed-in: 0.38.0 | seen: 1 | harness: 0.37.0
  - Improvement: when `launch` resolves `user://` to a directory whose
    `devtools_owner.json` (or `.devtools/launched.jsonl` history) names a DIFFERENT
    checkout, say so on the launch line and suggest `--snapshot-userstate`; and
    document `use_custom_user_dir=true` beside `custom_user_dir_name` wherever the
    scaffold mentions isolating a copy.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.37.0
(14 files, 57 verbs + 59 CLI). `unittest discover -s tools` — 51 OK.
`check_templates.py --full` — OK, `stage 6 contract: 90/90`, stage 5
`ui_escapes_panel names the planted Legend (40px right) and nothing else; contained_in
says NOT inside/right=40 for it, inside for Inside, refuses a Node2D`; two mutation runs
(the second because the first stage-4 failure returned before the engine-error control
ran): `_collect_panel_escapes` disabled → `ui_escapes_panel should name exactly the
planted Legend, got []`; unknown-arg branch removed → `run_tests.gd must exit 2 naming
an unknown argument (--select); got exit 0`; ERROR regex broken → `must count the
planted push_error as exactly 1 engine error`. Restores proved by `cmp`. Real-project
sweep as above.

## 2026-08-16 — 0.38.0: loop tick six — one issue already answered, one promise kept

gh#36 (`run_tests.py -- --select` silently runs the whole suite) is plant-tower-defense
G-049, shipped in 0.37.0 forty minutes before it was filed by a session on a 0.33.0
cache — closed with the pointer. One factual correction in the close: `--select` is
not "understood by run_tests.gd" as the report says; the runner takes `--filter`,
`--file`, `--json`, and since 0.37.0 exits 2 naming that list on anything else. The
passthrough was always forwarded; the runner ignored it. moving-in G-058 seen 2 →
already fixed 0.37.0. Nothing else moved.

- **[H-067 — fixed] `launch` says when the resolved `user://` was last used by a game
  from a DIFFERENT checkout** (owner file's `project_path`), and names the fix: both
  `use_custom_user_dir=true` and `custom_user_dir_name`, because the name alone is
  ignored — the exact mistake last tick's probe made. REFERENCE and the scaffold doc
  now say "both keys". Unit-tested (other checkout named; same checkout silent).

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.38.0
(14 files, 57 verbs + 59 CLI). `unittest discover -s tools` — 53 OK (2 new).
`check_templates.py` — OK, all stages (client + docs only; `--full` ran clean for
0.37.0 on the identical `dev_tools.gd`).

## 2026-08-16 - Upstreamed 4 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0, 0.32.0, 0.33.0, 0.36.0, 0.38.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`launch --snapshot-userstate` is opt-in, and the warning that you needed it
  arrives at `quit`, after the file is already unrecoverable.** The flag added for
  G-047 works exactly as advertised — the second launch restored cleanly. The first
  launch did not use it, and `quit` then printed:

  ```
  user://: this run wrote the developer's REAL user data in
  C:\Users\gotmi\AppData\Roaming\Godot\app_userdata\plant-tower-defense
  -- changed: highscore.save.
  ```

  By then the developer's campaign best had been overwritten with 36074 from a run
  driven on 35,000 injected seeds, and the pre-existing value exists nowhere — no
  snapshot, no history, nothing to restore from. The warning names the damage at the
  one moment nothing can be done about it. Note the damage is not test pollution: it
  is the *game* saving normally, which is why no test-side rule catches it.
  - [plant-tower-defense:G-050] status: fixed | fixed-in: 0.39.0 | seen: 1 | harness: 0.36.0 | source: plant-tower-defense 2026-08-16
  - Improvement: take the snapshot on **every** `launch` (it is a file copy of
    `user://*.save`, cost is microseconds) and make `--snapshot-userstate` control
    only whether `quit` restores it. Then a run that turns out to have written the
    real save is recoverable after the fact instead of only before it. Failing that,
    `launch` should print the "this session shares your real `user://`" line it
    already prints *with* the names of the files that exist there and would be
    overwritten, so the decision is offered at the moment it can still be made.

- Gap: **`set_physics_process(false)` before `add_child()` does not stick, and the
  harness's own test guidance does not say so.** A `Dandelion` built with physics
  disabled and then hosted had already fired a seed by the first assertion
  (`a fresh head is full: Expected 3 but got 2`), because Godot re-enables physics
  processing at `NOTIFICATION_READY` for any script declaring `_physics_process`.
  `test_combat.gd` already works around it by calling the setter AFTER
  `instantiate_scene` and resetting `_cooldown` by hand, but nothing says why, so the
  next test writer rediscovers it. Cost: one full suite round trip.
  - [plant-tower-defense:G-050b] status: fixed | fixed-in: 0.39.0 | seen: 1 | harness: 0.36.0 | source: plant-tower-defense 2026-08-16
  - Improvement: one paragraph in the harness's "Where the checks you write live"
    section, beside the existing `instantiate_ui` note — headless pumps no frames for
    Controls, but it DOES pump the settle frames for a hosted Node2D, and a node
    quiesced before hosting is not quiesced. Better still, a `_T.quiesce(node)`
    helper that sets the flag after the host is live, so the ordering is not
    something each test has to know.

- Gap: **`instantiate_ui`'s contract says a Control's `size` stays `(0, 0)` without it, and
  stops there - it never says the size that lands can be LARGER than the one the code
  assigned.** Every doc line about this helper is about the value being too small
  (`headless pumps no frames, so without it size stays (0,0)`), so the trap it actually
  set was the opposite one. `probe.heading.size` came back `(720.0, 42.0)` against an
  `add_heading` that had just executed `heading.size = Vector2(panel.size.x, 40.0)`,
  because `Control.size` is clamped up to `get_combined_minimum_size()` and a Label's
  minimum is its font. Workaround: assert position exactly, width exactly, and height
  with `assert_gte`. This repo's own history has hit the same clamp before from the
  other side (a Label whose "assigned 264 width loses to its own minimum size" draws
  past its paper) without it ever being filed.
  - [plant-tower-defense:G-051] status: fixed | fixed-in: 0.39.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16
  - Improvement: one sentence beside the existing `(0, 0)` warning - "and after the
    settle frames a Control's `size` is clamped UP to `get_combined_minimum_size()`, so
    an exact-equality assertion on a text-bearing Control's size is asserting the theme's
    font metrics as much as the code's layout; assert position exactly and size with
    `assert_gte`." Better still, a `_T.assert_box(control, rect)` helper that does exactly
    that split, so the right assertion is the shortest one to write.

- Gap: **a stopped background test run keeps writing to the results file, and a results
  file containing two runs looks like one run with a contradiction in it.** A suite run
  was moved to the background on timeout and stopped via `TaskStop`; the shell died, the
  `godot` child did not, and it kept appending to the same redirect target a later
  foreground run had truncated. The result was one file with two `Total:` lines —
  `519/519 | 11310 assertions` and `516/519 | Failed: 3` — with per-test times inflated
  from 154ms to 5610ms by the CPU contention. The house doctrine is "read the
  denominators, not the exit code", and here there were two sets of denominators
  disagreeing with each other in one file. Diagnosing it needed
  `Get-CimInstance Win32_Process` to prove the surviving pids were a *sibling agent's*
  bridge session on another checkout and not mine.
  - [plant-tower-defense:G-051b] status: fixed | fixed-in: 0.39.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16
  - Improvement: have `run_tests.gd` stamp a per-run nonce on both its opening and its
    `Total:` line (`run 7f3a1c pid 12345`), and have `run_tests.py` exit `2` when its
    captured output contains more than one distinct run nonce — "this file is two runs,
    you are reading a mixture" rather than leaving a human to notice the duplicate
    `Total:`. Cheap, and it turns an invisible misread into a refusal.

## 2026-08-16 - Upstreamed 1 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.31.0, 0.33.0 (cache) / 0.31.0 (vendored), 0.36.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-059] — `verify_ledger.py stats` reports alias credits as a cumulative sum
  in a sentence that reads as a file count.** The line is *"138 file(s) credited by
  reach_aliases — declared by the project, not observed. If this outgrows the observed
  count, the number above is mostly the config talking."* The comparison against 658 is
  sound, both being cumulative. But "138 file(s)" is five files credited 51+50+21+8+8
  times, and it cost this project a kanban entry, a todo item and a re-audit verdict —
  a subagent independently recomputed 138 and called the entry accurate.
  - [moving-in:G-059] status: fixed | fixed-in: 0.39.0 | seen: 2 | harness: 0.36.0 | source: moving-in 2026-08-16
  - Improvement: say both, e.g. *"138 alias credit(s) across 5 distinct file(s)"*. The
    caveat sentence is the good part and should stay; it is the noun that misleads.

## 2026-08-16 — 0.39.0: loop tick seven — the friction has moved to the test-writing surface

Reviewed for this tick: the 16 open beads, the tracker (0 open issues; #32–#36 all
closed last tick), `PURPOSE.md`, and the plant-tower-defense and moving-in logs. Five
new gaps pooled (plant G-050, G-050b, G-051, G-051b; moving-in G-059) and one 7th
sighting (plant G-044). **Every one of the five is about the headless test surface —
`_T` helpers, `run_tests.py`, `verify_ledger.py stats` — and none is about a bus
verb.** The bus has 57 verbs and has not gained a gap in three ticks; the place the
tool is actually used every turn is `test/unit/` and the wrappers around it. That is
where this tick's work went, and it is why the three open bus-side beads (batch
assertion verb, wedged-handler timeout, per-verb usage counts) stayed open: no project
produced evidence for them this tick, and building to a hypothesis is what H-033 warns
about.

- **[plant G-050 — fixed] a `user://` snapshot is taken on EVERY `launch`; the flag
  only arms the automatic restore.** `quit` used to say "this run wrote the developer's
  REAL user data … changed: highscore.save" at the one moment nothing could be done —
  the campaign best (35,000-seed run → 36074) existed nowhere. Now `launch` copies
  `*.save` under `.devtools/userstate_snapshot/` (previous launch's copy kept as
  `_prev/`), names the files at risk on the launch line, and `restore-userstate` puts
  them back after the fact; `quit`'s warning names the copy. Two unit tests: an unarmed
  snapshot does not revert a legitimate run; `restore-userstate` forces it.
- **[moving-in G-059 — fixed] `stats` says "N alias credit(s) across M distinct
  file(s)"** — "138 file(s)" was five files credited 51+50+21+8+8 times and cost a
  project a kanban entry, a todo and a re-audit before the noun was noticed.
- **[plant G-051b — fixed] `run_tests.py` exits 2 on a results file two runs wrote.**
  `run_tests.gd` prints `Run: <id> pid <n> started` first and `… finished` after
  `Total:` (`run_id`/`pid` in `--json`); the wrapper refuses >1 distinct id, or >1
  `Total:` line from a pre-nonce runner, naming both pids: "the tallies above are a
  MIXTURE. Nothing was verified." Stage 4 asserts one id brackets the real output; four
  unit tests cover single/two/interleaved/old-runner captures.
- **[plant G-051 — fixed] `_T.assert_box(control, rect)`** — position exact, size
  `== max(assigned, combined minimum)` per axis; failure names the axis and "assigned 40
  clamped up to combined minimum 42". Stage 4 plants a 30px Label assigned `(200, 10)`,
  proves `assert_eq` on its size FAILS (the mechanism), and that `assert_box` passes it
  and refuses a moved position.
- **[plant G-050b — fixed] `_T.quiesce(node)`** — after hosting; warns and does nothing
  on a node not yet in a tree. **The stage-4 control caught my first version of the
  control:** the ticker quiesced after hosting had already ticked once *during
  `instantiate_scene`'s two settle frames* (`Expected 0 but got 1`, while the
  before-hosting ticker read 4). Real finding, and exactly the shape the plant log
  described (`test_combat.gd` resets `_cooldown` by hand): quiesce holds from the
  moment it is called, and state that must be pristine is reset after it. The control
  now measures ticks *since* quiesce; the helper doc and REFERENCE say so.
- **[plant G-044, 7th — swept] `import_check.py` deletes stranded `<asset>.import*.tmp`
  files** on a crash retry and on the final failure path, naming them; never touches
  `.godot/`. The 7th sighting itself was a bare `godot --import` run outside
  `import_check.py` — the wrapper with the retry already existed; the tmp sweep is what
  it lacked. Two unit tests (planted debris beside kept files; clean tree sweeps none).
- **[bead 1kh — fixed] `record_version.py --check` reads git.** It printed `OK` on
  0.18.0 with HEAD at 0.17.0 and 25 files dirty; it now adds `WARNING: 0.39.0 is
  recorded but uncommitted - HEAD ships 0.38.0 and 24 tracked file(s) are dirty` (exit
  still 0 — a valid mid-release state, stated). It fired on this very tick's tree, which
  is the state it was written for. Five unit tests in `tools/test_record_version.py`
  (dirty+bumped warns; clean quiet; dirty-at-HEAD-version quiet; no git quiet).
- **PURPOSE.md gained a commitment: "Say it while it can still be acted on."** G-050,
  G-051b and H-067 are one failure shape — a true report that arrives after the moment
  of choice — and the design test for a warning is now "can the reader still do
  something when they read it, and if not, was the way back kept open."

- Gap: **`check_templates.py` has no way to run one stage.** The full run failed at the
  new stage-4 control, kept going through stage 5/6 (correctly), and the fix cost a
  second full run to prove — ~10 minutes for a 4-line change to one planted test.
  - [H-068] status: fixed | fixed-in: 0.40.0 | seen: 1 | harness: 0.39.0
  - Improvement: `--stage 4` (or `--only runners`) that assembles the scratch project
    and runs one stage; the assemble step is cheap and every stage already takes
    `(godot, scratch)`.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.39.0
(14 files, 57 verbs + 60 CLI, plus the new git WARNING naming HEAD 0.38.0).
`unittest discover -s tools` — 66 OK (13 new). `check_templates.py --full` — first
run FAILED at the new stage-4 control (`Expected 0 but got 1`, above), everything else
green including `stage 6 contract: 90/90`; second run (control fixed) — OK, all stages, `Run: f819a3 pid 6204 brackets the output`, `assert_box accepted a font-clamped Label and refused a moved one; quiesce() after hosting held at 0 ticks where set_physics_process(false) before hosting was undone`.

## 2026-08-16 - Upstreamed 3 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0, 0.32.0, 0.33.0, 0.36.0, 0.38.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **the user:// reporter names the file the suite wrote and cannot name the test
  that wrote it.** `user:// writes: 1 file(s) changed by the suite ... changed:
  highscore.save` is exactly one bit more than "something happened", and recovering the
  rest cost a hand-instrumented `_save()` and a full 535-test run. The machinery to do
  better is already in `devtools.py` and already called by the wrapper —
  `userstate_stat_take` / `userstate_stat_diff` are a snapshot and a diff, and
  `run_tests.gd` already brackets every test method with setup/teardown.
  - [plant-tower-defense:G-052] status: fixed | fixed-in: 0.40.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16
  - Improvement: take the snapshot per test method rather than per run (it is a `stat`
    of one directory, cheap next to a scene instantiation), and print
    `user:// writes: highscore.save <- test_quitting_a_run_through_pause_still_files_the_score
    (test_selftest.gd)`. Same check, same cost class, and it turns a cycle of
    instrumentation into a line of output. Gate it behind a flag if the per-test stat is
    unwelcome by default — the information is worth a `--trace-user-writes`.

- Gap: **`_T` has no `assert_ne`.** Asserting "this path is NOT the player's save" — the
  runtime half of this cycle's fix — has to be written
  `_T.assert_false(a == b, "...%s...%s" % [a, b])`, and the message has to carry both
  values by hand, because `assert_false` reports only `Expected false but got true`.
  The helper set has `assert_eq`, `assert_true`, `assert_false`, `assert_float_eq`,
  `assert_gt`, `assert_gte` and `assert_margin`; inequality is the obvious missing one,
  and it is the shape every "did the guard actually move this" check wants.
  - [plant-tower-defense:G-053] status: fixed | fixed-in: 0.40.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16
  - Improvement: add `static func assert_ne(actual, unexpected, context := "") -> String`
    beside `assert_eq` in `run_tests.gd`, reporting `Expected anything but <value>` and
    printing the actual — six lines, and it removes the hand-formatted message that is
    the only reason the failure above is readable.

- Gap: **nothing stops a live bridge session persisting into the developer's `user://`,
  and nothing tells you afterwards that it did.** `quit` names what the run changed —
  and I did not read it, because the write happened many verbs earlier and the failure
  surfaced twenty minutes later as five unrelated-looking test failures. The suite now
  has `tools/save_persist_check.py` and per-script `setup()` redirects; the bridge has
  neither and cannot have the second one.
  - [plant-tower-defense:G-054] status: fixed | fixed-in: 0.40.0 | seen: 2 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16
  - Improvement: `launch --snapshot-userstate` already exists and makes `quit` restore
    what the run changed. Make it **the default for `launch`**, with
    `--no-snapshot-userstate` to opt out — a verification session that silently mutates
    the developer's save is never what was wanted, and the flag only helps the people
    who already know to look for it. Failing that, have `launch` print one line naming
    the `user://` files it is prepared to see change, so the hazard is stated at the
    moment the risk is taken rather than at `quit`, after the damage.

## 2026-08-16 - Upstreamed 3 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.33.0 (cache) / 0.31.0 (vendored), 0.36.0 (project) / 0.38.0 (cache))

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`reach` is file-level, and this run is the case that shows what that costs.**
  `verify_ledger.py reach` printed
  `worktree (this session's edits - the honest number): reached 1/1 changed file(s)` —
  a clean 100% — for a run in which the lines I actually changed provably did not
  execute. `_search_left: 12` is the proof: the game loaded `unpack_ui.gd`, so the file
  counts as reached, while `_process()`'s body ran zero times. The ledger's whole
  argument for reach is that it "says whether a run actually loaded the code it claimed
  to verify rather than asking the run to grade itself", and at file granularity a 1/1
  can mean the changed function never ran. I found this by hand, by reading a private
  counter I happened to know was there.
  - [moving-in:G-060] status: fixed | fixed-in: 0.40.0 | seen: 1 | harness: 0.36.0 (project) / 0.38.0 (cache) | source: moving-in 2026-08-16
    RECONCILED cycle 38: still open upstream as #38, no release since. Unchanged.
    Filed upstream as SeveralHerr/godot-selftest-harness#38 — re-verified against the
    0.38.0 template first (`_reach_line()` line 1067, `split_reach()` line 615 both
    still purely file-path), so it reproduces on current, not only on the vendored copy.
    Noted while checking: #27 and #35 are both CLOSED now, contradicting cycle 33's
    note that they were open/half-done.
  - Improvement: intersect `git diff -U0`'s changed line ranges with the enclosing
    `func` names, and have `reach` report a second line —
    `functions: 0/1 changed function(s) observed executing` — as **advisory**, not a
    gate. It cannot be computed from a scene tree alone, so the honest minimum is for
    `reach` to say out loud that a reached FILE is not a reached CHANGE, the same way it
    already distinguishes `reached_alias` from `reached`. Today nothing in the output
    hints at the distinction, which is why a 1/1 reads as stronger evidence than it is.

- Gap: **`pause` freezing nothing is undiscoverable until it wastes a run.** CLAUDE.md
  recommends "poll for the moment, pause, then inspect at no rush", and `ping` prints
  `tree is PAUSED (bridge still polling: PROCESS_MODE_ALWAYS)`. Both are true and neither
  says the thing that matters: **any node the project set to `PROCESS_MODE_ALWAYS` keeps
  animating**, which for a HUD — where `ALWAYS` is mandatory, or the pause menu cannot
  draw itself — means pause freezes everything EXCEPT the thing being measured. Observed:
  `RewardCard` read `visible: true`, then `visible: false` one command later, on a tree
  that `ping` insisted was paused. That reads like a bus fault. The workaround was
  `set-game-speed 0.01`, which is not mentioned near `pause` anywhere.
  - [moving-in:G-061] status: fixed | fixed-in: 0.40.0 | seen: 2 | harness: 0.36.0 (project) / 0.38.0 (cache) | source: moving-in 2026-08-16
    RECONCILED cycle 38: not filed upstream yet, by choice — still want a second
    sighting before claiming the shape of the fix. `set-game-speed 0.01` has now
    served as the workaround twice (cycles 35 and 37) without a third surprise, so
    the workaround is at least stable.
  - Improvement: have `pause` count the `PROCESS_MODE_ALWAYS` nodes under the configured
    `hud_layer_name` and say so in its own reply — `paused; N node(s) under GameHud are
    PROCESS_MODE_ALWAYS and keep animating (use set-game-speed 0.01 to slow them
    instead)`. The information is one `get_tree()` walk away and it is only ever needed
    at the moment `pause` is called.

- Gap: **[G-062] — `findings` prints 57 lines for 11 facts, and the multiplicity is why
  nobody read it.** Not a duplicate of #41's baseline argument; this is the cheaper half
  and stands alone. `_collect_unconnected_signals` walks nodes, so a signal declared once
  on a script instanced 24 times produces 24 findings whose text differs only by a node
  index. The finding is about the SCRIPT, not the node.
  - [moving-in:G-062] status: fixed | fixed-in: 0.40.0 | seen: 1 | harness: 0.36.0 (project) / 0.38.0 (cache) | source: moving-in 2026-08-16
  - Improvement: collapse to one line per `(script, signal)` with a count —
    `signal 'emptied' declared by unpack_box.gd is never connected (24 nodes)` — and keep
    the paths in the JSON for anyone who wants them. Turns an unreadable 57 into a
    readable 11 without changing what gates. Included as the fallback proposal in #41.

## 2026-08-16 — 0.40.0: loop tick eight — four issues in an hour, all from the plant and moving-in sessions

Reviewed: 15 open beads, four NEW issues (gh#38–#41, all filed in the hour after 0.39.0
shipped, all against 0.38.0 caches), `PURPOSE.md`, and the two project logs (six new gaps
pooled — plant G-052/G-053/G-054, moving-in G-060/G-061/G-062 — which are the same six
facts as the four issues, so this tick reconciled them rather than counting them twice).
Two independent sessions hit the developer's real `user://` from the bridge on the same
day; that is the tick's headline, and it changed a default and `PURPOSE.md`.

- **[gh#40 / plant G-054 — fixed] `launch` restores `user://*.save` on `quit` BY
  DEFAULT.** 0.39.0 made the copy always-on and the restore opt-in; two reporters
  said, correctly, that the flag only helps people who already know the hazard.
  `--no-snapshot-userstate` keeps a run's writes; the launch line says `will be
  RESTORED on quit`. Considered and rejected: leaving it opt-in with a louder line —
  gh#40's own weaker proposal — because the line was already there (0.39.0) and the
  reporter's session had it and still lost the save.
- **[gh#39.1 / plant G-052 — fixed] `run_tests.gd` names the TEST that wrote
  `user://`** — per-method stat+md5, `user:// writes by test:` after `Suite:`, with
  `[content changed]` / `[rewritten identically]` / `[created]` / `[deleted]`; the same
  distinction now on `quit`'s report (`highscore.save (rewritten identically - a writer
  ran; the values matched)`). Stage 4 plants a writer and an identical rewriter and
  asserts both attributions by name and kind.
- **[gh#39.2 / plant G-053 — fixed] `_T.assert_ne`.** Stage 4 asserts it FAILS on equal
  values naming the value.
- **[gh#41 / moving-in G-062 — fixed] `signal_unconnected` is one finding per (script,
  signal) with a node count, and has its own baseline** (`user://signal_findings_baseline.json`,
  written by `findings --baseline-write`, keyed on the pair, not the path). 57 lines for
  11 facts → 11 lines, and a deliberately-unconnected outward API is accepted once.
  `_apply_ui_baseline` grew a `path` parameter rather than a second implementation.
  Stage 5 instances one emitter script 3× and asserts 1 finding / nodes=3 / accept /
  exclude / re-report. Client says `Signal baseline: …` beside `UI baseline: …` and
  says "not reported" for a pre-0.40.0 game rather than guessing.
- **[gh#38 / moving-in G-060 — fixed, the honest half] `reach` says `[file-level:
  loaded, not lines-executed]` on the line and lists the changed functions in reached
  files** (`git diff -U0` ∩ enclosing `func`; `<top-level>` for a const). Advisory,
  never gating, never claims execution — the scene tree cannot see it. Rejected the
  "observed executing" version for now: it needs a per-function signal the bridge does
  not carry, and a line that says "0/1 observed" while observing nothing is the
  overclaim PURPOSE forbids. `/verify` Phase 5 tells the session what to do instead
  (`get-state` the state the change would have moved).
- **[moving-in G-061 — fixed] `pause` names the `PROCESS_MODE_ALWAYS` nodes** that keep
  processing (`data.always_count` / `always_roots`) and points at `set-game-speed 0.01`.
  The reporter wanted a second sighting before proposing a shape; the data is a tree
  walk and the message states a fact, not a hypothesis, so it shipped. Stage 5 plants
  one ALWAYS node (`set_state process_mode=3`) and asserts count 1 and the path.
- **[H-068 / gh#37 — fixed] `check_templates.py --stage {1,2.5,3,4,5}`.** Used the same
  tick: the stage-4 user-writes control was proved in ~90 s instead of a second full run.
- **PURPOSE.md gained "A verification run leaves the developer's state as it found it."**

**Considered and not done:** the bus-side beads again (batch verb, wedged handler,
verb-usage counts) — still no project evidence; per-function reach observation
(above); a `--trace-user-writes` flag (gh#39 offered it as a fallback — the per-test
stat is cheap enough to be always on, and an advisory that is opt-in is one nobody
runs, per PURPOSE).

- Gap: no new gap this turn. (The plant log's G-050b/G-051b "same id, different
  evidence" pattern recurred — H-044 — and `upstream_gaps.py` handled it by suffix
  again; the intake-side fix is still the open bead.)

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.40.0
(14 files, 57 verbs + 60 CLI). `unittest discover -s tools` — 69 OK (3 new: identical
rewrite vs change; changed_functions names only the changed `func`s and `<top-level>`,
skips an unchanged file, lists every func of an untracked one). `check_templates.py
--full` — OK first run, all stages: `signal_unconnected collapsed 3 emitter instances to
1 finding (nodes=3); its own baseline accepts the (script, signal) pair (pre=1,
excluded), and --no-baseline re-reports it`; `pause names the 1 planted
PROCESS_MODE_ALWAYS node and the set-game-speed way round it`; `stage 6 contract:
90/90`. Then `--stage 4` for the user-writes/assert_ne control added after: OK,
`user:// writes attributed to their test as [created] then [rewritten identically];
assert_ne named the value`.

## 2026-08-16 - Upstreamed 1 open gap(s) from moving-in (harness 0.11.0, 0.16.0, 0.19.0, 0.21.0, 0.33.0 (cache) / 0.31.0 (vendored), 0.36.0 (project) / 0.38.0 (cache))

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **[G-063] — `quit` recommends `--snapshot-userstate` in the same reply that proves
  it would not have helped.** The message names the changed file (`changed: settings.cfg`)
  and the flag knows its own patterns (`*.save`). Those two facts are one comparison
  apart, and as it stands the advice confidently sends you to a mechanism that protects
  nothing — I followed it, believed it, and reported upstream that it worked.
  - [moving-in:G-063] status: fixed | fixed-in: 0.41.0 | seen: 1 | harness: 0.36.0 (project) / 0.38.0 (cache) | source: moving-in 2026-08-16
  - Improvement: two, independent. (1) Have `quit` compare the changed filenames against
    the active snapshot patterns and say so — *"changed: settings.cfg (NOT matched by
    --snapshot-userstate's patterns: \*.save)"*. (2) Widen the default beyond `*.save`;
    `ConfigFile` is Godot's own idiom and `user://settings.cfg` must be one of the most
    common paths in the ecosystem. Both filed on #40 with the transcript.

## 2026-08-16 — 0.41.0: loop tick nine — the restore that did not say so

Reviewed: 13 open beads, the tracker (0 open — #37–#41 closed last tick), `PURPOSE.md`,
both project logs. One new gap (moving-in G-063) and one sharper second sighting (plant
G-054: `--snapshot-userstate` armed, `quit` run, save still dirty, snapshot on disk
correct, a later bare `quit` restored it). Both are the same lesson as last tick's
PURPOSE addition, one level down: the default was right and the *report* was not.

- **[plant G-054 (2nd) — fixed] `quit` restores on every exit path and says what it did
  every time.** Reproduced by reading, not guessing: the survivor branch (`pid N is
  STILL ALIVE` — with or without `--kill`) `sys.exit(1)`ed before the restore, with no
  line. That is the reporter's shape exactly (a game that lingered a few seconds; output
  redirected). Restoring under a live game would be undone by its own exit-time save,
  so the survivor path now KEEPS the snapshot and prints the pid and the command to run
  once it is gone; the gone paths restore; and every path prints one of `restored …`,
  `no snapshot to restore`, `kept, NOT restored (--no-snapshot-userstate)`, `KEPT …
  still alive`. Unit-tested (alive → kept + named; gone → restored; none → said).
- **[moving-in G-063 — fixed] `quit` names the changed files the patterns do NOT
  cover** (`NOT covered by the snapshot patterns (*.save) and so NOT restored:
  settings.cfg - relaunch with --snapshot-userstate *.save *.cfg`), and **the default
  globs widened** to `*.save *.sav *.cfg *.dat *.json *.tres *.res *.bin`. The bridge's
  own files (owner, `*_baseline.json`, `findings_last.json`, `devtools_*`) are never
  snapshotted, never removed on quit, and never reported as the run's writes — or a
  mid-session `findings --baseline-write` would be undone at `quit`. Unit-tested.
- **[H-044 — fixed] `upstream_gaps.py` carries `dup-of: gh#NN`** when the project's id
  line says `filed upstream: gh#NN` — the two intake paths now meet in the file rather
  than in a human's memory. **[H-028 — fixed]** every run ends with `open gaps in
  log-devtools.md by source: gather 40, harness 22, moving-in 17, plant-tower-defense 5,
  dave-game 1 (85 total)`.

**That last line is the tick's real finding.** 85 "open" gaps, 40 of them from `gather`
at harness 0.8–0.10 — a project that has not pooled since — and 22 harness-native ones
back to 0.7.0. Some are genuinely open (H-031's `--self-check`, H-052/H-053), but a
reader of this log cannot tell which, which is the same failure PURPOSE names for the
tracker ("nine open defects where two are real"). Filed as a bead, not done this tick:
triage needs each gap's mechanism re-checked against the current templates, and that
is a session's work, not an hour's.

**Considered and not done:** H-043's remaining half (a watchdog inside a 30 s client
timeout — still architectural); the bus-side beads (unchanged, no evidence);
`--self-check` for name_check (H-031) — no sighting since 0.11.0.

- Gap: **the pooled log's open count is not a backlog, and nothing in it says which
  entries were re-checked and when.** `gather 40` reads as forty things to do; most were
  logged against 0.8.0–0.10.0 templates that have been rewritten since, and the log's
  own rule ("the project's copy stays open until that project refreshes") means they
  can never close from here. Real output: the by-source line above.
  - [H-069] status: fixed | fixed-in: 0.42.0 (--triage listing + explicit --mark-unverified; the age-based bulk mark the Improvement line asked for was built, tried, and withdrawn - see the 0.42.0 entry) | seen: 1 | harness: 0.41.0
  - Improvement: a `stale-since:` field (or an `upstream_gaps.py --triage` that lists
    every open pooled gap whose `harness:` predates the last N releases, grouped by
    project, so a session can re-check mechanisms and mark `status: unverified` /
    `superseded` explicitly), and a rule that an open pooled gap older than K releases
    is reported as *unverified*, not open.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.41.0
(14 files, 57 verbs + 60 CLI). `unittest discover -s tools` — 76 OK (7 new).
`check_templates.py` — OK, all stages (`--full` not run: `dev_tools.gd` is stamp-only
since 0.40.0's `--full` green; stage 5 still launches/quits with the new client).

## 2026-08-16 — 0.42.0: loop tick ten — nothing arrived, so the backlog got looked at

Reviewed: 12 open beads, the tracker (0 open), `PURPOSE.md`, both project logs. **No new
issue and no new gap** — the first quiet tick since the loop began. So the tick went to
the thing last tick's by-source line exposed (H-069): 83 "open" pooled gaps, most against
templates rewritten long ago.

- **[H-069 — fixed] `upstream_gaps.py --triage` and `--mark-unverified ID…`.** `--triage`
  lists the open pooled set by project, oldest first, `STALE` on anything logged 15+
  minor releases behind this one (`?` for an unknown `harness:`, never flagged; `H-NNN`
  never flagged). **H-033 earned its keep here:** the Improvement line asked for an
  age-based bulk rewrite to `unverified`, I built it, ran it in dry-run, and read the
  first five it would have relabelled — plant G-031 (asset conformance), moving-in G-005
  (same), moving-in G-052 (collider planes), G-021, G-028. Three are still-wanted
  requests that no template rewrite touched; "not re-checked" would have been a lie
  about them. So the mark takes explicit ids only, after a session has re-read them,
  and the STALE flag stays a flag for the reader. Unit-tested (age flag project-only,
  unknown-not-old, H- never flagged; mark rewrites only named open project gaps and
  `gaps_by_source` reports `unverified` separately).
- **The first `--triage` pass re-checked the plant set and closed three by reading:**
  plant G-028 and H-040 were fixed in 0.29.0 (`mark_script_reached`, gh#30) — that
  release's entry says "closes H-040/G-028 for real" and neither status line was ever
  edited, thirteen releases ago; plant G-021 (dead owner on the bus) — `launch` ignores a
  dead owner and `quit --kill` / the launch ledger reap survivors. CLAUDE.md now says
  "edit the status line, not just the prose", and why. Gather's 36 STALE and moving-in's
  12 are listed and left as-is: re-checking each is a session's work, and the mark exists
  now for the session that does it.
- **Distribution.** The installed plugin cache is 0.38.0 (`installed_plugins.json`,
  sha `cd79697`) — every issue filed since (#38–#41) came from that cache, and three of
  them were fixed before they were filed. Plant runs 0.38.0, moving-in 0.36.0, gather and
  findmyballs 0.10.0. This tick ends by running `claude plugin update` so new sessions
  scaffold from current; the projects' own refresh is theirs to run and is not touched
  from here (another session's working tree).

**Considered and not done:** re-checking gather's 36 (a session, not an hour); H-031's
`--self-check`; the bus-side beads (no evidence, fourth tick running — worth asking
whether they should be closed as `wontfix-until-seen` next quiet tick).

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.42.0
(14 files, 57 verbs + 60 CLI). `unittest discover -s tools` — 78 OK (2 new).
`check_templates.py --static-only` — OK (only `upstream_gaps.py` changed under
templates/; the engine stages ran green for 0.41.0 on otherwise identical templates).

## 2026-08-16 - Upstreamed 1 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0, 0.32.0, 0.33.0, 0.36.0, 0.38.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`interactive_overlap` counts controls that cannot be interacted with.** Two
  Buttons overlapping is only a defect if a player can reach both; one at
  `FOCUS_NONE` with `MOUSE_FILTER_IGNORE` can be reached by neither channel, and
  making a covered layer inert is the standard fix for exactly the hazard this check
  exists to find. So the check currently fires hardest at projects that have already
  fixed the problem, and the only way to quiet it is a baseline — which then also
  hides a REAL overlap arriving later at the same node pair.
  - [plant-tower-defense:G-055] status: fixed | fixed-in: 0.43.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#42
  - Improvement: skip a Control whose `focus_mode == FOCUS_NONE` **and** whose
    `mouse_filter == MOUSE_FILTER_IGNORE` when pairing for `interactive_overlap`, and
    say so in the finding's own text for the ones it does report ("both reachable").
    That turns "these overlap" into "these overlap and both can be used", which is
    the claim the check is actually making. Cheap: both properties are already read
    by `reachable-ui`.

## 2026-08-16 — 0.43.0: loop tick eleven — the first regression the loop shipped, and what caught it

Reviewed: 12 open beads, two NEW issues (gh#42, gh#43), `PURPOSE.md`, both project logs
(one new gap, plant G-055 — which arrived carrying `dup-of: gh#42` from last tick's
change, on its first real run). gh#43 is the tick: **0.42.0 segfaults a real 552-test
suite that 0.38.0 passes, deterministically, bisected by the reporter against unchanged
project code.** The plugin cache was refreshed to 0.42.0 last tick; this is the first
project to run it, and it is a regression the loop shipped.

- **[gh#43 — fixed] the runner clamps physics catch-up to one tick per frame; the
  per-test `user://` walk is top-level only.** Reproduced by reading first, then by
  mechanism: the failing test's own comment says a pest parked on leg 3 "escaped and
  freed itself during the settle frames" if it advances one leg too far — and 0.40.0's
  recursive md5 walk of `user://` (178 files / 11 MB on the reporter's machine:
  `screenshots/`, `shader_cache/`, `logs/`), run right before each test, is a stall,
  and Godot's physics catch-up turns a stall into up to 8 `_physics_process` ticks in
  the next process frame instead of 1. `instantiate_scene`'s two settle frames went
  from 2 ticks to as many as 16; the pest advanced past its last safe leg, freed
  itself, and the typed array built after the `await` held a freed object (`Attempted
  to set an invalid (previously freed?) object instance into a 'TypedArray'` — the
  reporter's first error line, verbatim). **Reproduced the mechanism, not the crash:**
  a copy of the plant repo under 0.42.0 ran 554/554 twice on this machine, once with
  the reporter's real 11 MB `user://` copied in — hashing here was fast enough, and
  their run shared the box with other sessions. Stage 4 now plants the mechanism and
  it fired: `Engine.max_physics_steps_per_frame = 8` + a 400 ms stall → >2 ticks in
  the settle frames; `= 1` → ≤2. Fix is both halves: the runner sets the clamp for
  the whole suite (two settle frames are two ticks whatever the wall clock says; N
  ticks = await N `physics_frame`s, as before), and the walk reads only top-level
  `user://` files with a 256 KB md5 cap — the same set devtools.py stats, for the same
  reason. The plant copy under the fixed runner: 554/554, `Errors: 0`, exit 0 — the clamp does not disturb a real 554-test suite.
- **[gh#42 / plant G-055 — fixed] `interactive_overlap` skips a control inert by both
  channels** (`FOCUS_NONE` + `MOUSE_FILTER_IGNORE`); the finding says `both reachable`.
  Stage 5: overlap A/B → 1 finding; B inert → 0; one channel back → 1.

**What this says about the loop.** Three ticks (0.39–0.41) shipped with `--full` green
and 66–78 unit tests green, and none of that could see a real suite's physics timing.
The scratch project's tests host nothing that frees itself on a tick count. CLAUDE.md
already says the scratch cannot measure a *false-positive rate* for static analysis
(H-030); the same is true of *timing* for the runner: **any change to what
`run_tests.gd` does synchronously between tests must be run against a real suite that
hosts self-mutating nodes** — the plant copy under `scratchpad/`, four minutes a side,
is that. Written into CLAUDE.md. Distribution cut both ways this tick: refreshing the
cache last tick is what let the regression be found in an hour instead of a week.

- Gap: **the loop's gate cannot see runner timing.** A real suite's tick-sensitive
  tests are the only instrument, and there is no step that runs one. Real output:
  gh#43's `3 runs out of 3` against `check_templates: OK`.
  - [H-070] status: fixed | fixed-in: 0.46.0 (tools/check_real_suite.py) | seen: 1 | harness: 0.42.0
  - Improvement: a `tools/check_real_suite.py <project>` that copies a sibling project
    to scratch (custom user dir set correctly — the 0.37.0 trap), installs the working
    tree's templates, runs `run_tests.py`, and compares `Total:` against the project's
    own last recorded run; the release skill runs it when `run_tests.gd` changed.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.43.0
(14 files, 57 verbs + 60 CLI). `unittest discover -s tools` — 78 OK. `check_templates.py
--stage 4` — OK with the new stall control (loose >2, clamped ≤2). `check_templates.py
--full` — OK, all stages, `interactive_overlap pairs the overlapping A/B (both reachable), skips B once inert by both channels, pairs again with one channel back`, `stage 6 contract: 90/90`. Plant copy (554 tests) under 0.42.0: 554/554 (small user dir),
554/554 (11 MB user dir).

## 2026-08-17 - Upstreamed 3 open gap(s) from plant-tower-defense (harness 0.18.0, 0.19.0, 0.21.0, 0.23.0, 0.24.0, 0.25.0, 0.32.0, 0.33.0, 0.36.0, 0.38.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\plant-tower-defense\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **nothing documents `pause` as the tool for a deterministic property read.** The
  verb table sells it as "sets `SceneTree.paused` directly, bus keeps answering — catch a
  sub-second effect", and `ping`'s note frames the answering-while-paused property as
  *pause menus are verifiable*. Both are true and neither says the thing that cost me
  three reads: **a property a `_process` timer mutates cannot be read reliably without
  freezing the tree first.** I read an empty Label three times and had no way to tell "the
  row is blank" from "something else is holding the row right now", because a single read
  of a moving value carries no evidence that it was moving.
  - [plant-tower-defense:G-056] status: fixed | fixed-in: 0.44.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#44
  - Note: re-checked against 0.42.0 before filing and NARROWED. 0.42.0 does state
    the idea for `step-time --then-pause` ("so the read that follows carries no
    ambient drift") -- attached to stepping, not stated as a general rule about
    reading. Filing the un-narrowed version would have been a false alarm.

- Gap: **`verify_ledger.py record` reports a post-commit row as "a real zero".**
  Recorded this cycle's row after committing and got `reached 0/0 changed file(s) -
  a real zero: every changed file is excused from the denominator`. reach is the
  diff intersected against what the game loaded, so after a commit it is empty by
  construction -- and the tool asserts the benign reading of an ambiguity it cannot
  resolve. "Nothing was in scope" and "the evidence was committed away" are opposite
  claims and the row cannot be told apart afterwards.
  - [plant-tower-defense:G-057] status: fixed | fixed-in: 0.44.0 | seen: 1 | harness: 0.38.0 | source: plant-tower-defense 2026-08-16 | dup-of: gh#44
  - Improvement: when the reach denominator is 0, check whether the working tree is
    clean while HEAD just touched the run's files, and say so instead of glossing it.
    Failing that, write `reach: null` with a reason -- honest beats reassuring.
  - Improvement: one line in the Gotchas list — `**A single read of a timer-driven
    property is not a measurement.** Anything a `_process`/`_physics_process` timer
    mutates should be read after `pause` (the bus answers while paused), or with
    `step-time --then-pause`. An unexpected value read live is ambiguous between "wrong"
    and "mid-transition", and the read itself cannot tell you which.` It belongs beside
    the existing "A run that never changes is broken, not passing" entry, which is the
    same lesson pointing the other way.

- Gap: **`verify_ledger record` silently discards unrecognised keys in `run.json`, then
  reports the discarded evidence as missing.** I passed Phase 4 evidence under `phase4`
  (with `check`/`result` entries). `record` accepted it without a word, wrote the row with
  `checks: []`, and printed:

  ```
  verify_ledger: warranted with no Phase 4 checks recorded - the claim that earned it is
  not in the row
  ```

  Both halves of the information were present in the same invocation and never met. `tier`,
  `phases` and `notes` were dropped the same way. The warning is good and it is what made
  me look; what it cannot do is say *you supplied this under the wrong name*.
  - [plant-tower-defense:G-058] status: fixed | fixed-in: 0.44.0 | seen: 2 | harness: 0.38.0 | source: plant-tower-defense 2026-08-17 | dup-of: gh#46
  - Process note, recorded because it nearly cost something: I wrote the issue's
    Environment line claiming the code was unchanged at 0.42.0 BEFORE checking it,
    then checked. It holds (`checks = run.get("checks") or []` at 0.42.0:1031, and
    no unknown-key handling anywhere in that file). But the order was wrong, and
    the whole reason skill-feedback-issue demands a re-check is that a stale claim
    in a public issue is the most common way this loop wastes a maintainer's time.
  - Improvement: on unknown top-level keys, name them and suggest the nearest known one —
    `run.json: ignoring unknown key 'phase4' (did you mean 'checks'?)`. The known-key set
    is already in the code that normalises the row; this is a set difference and a
    `difflib.get_close_matches` call. Silent key-dropping in a file whose entire purpose
    is to be a record is the same class as the `reach 0/0` gloss in gh#44: the tool has
    the information needed to be unambiguous and states the convenient reading instead.

## 2026-08-17 — 0.44.0: loop tick twelve — the tool knows and says the convenient thing

Reviewed: 13 open beads, three NEW issues (gh#44, gh#45, gh#46), `PURPOSE.md`, both
project logs (three new plant gaps, all arriving with `dup-of:` set — G-056/G-057 → gh#44,
G-058 → gh#46 — so intake reconciled itself). Two reporters, one shape, named by one of
them: *the harness knows a thing is ambiguous and reports the benign reading of it.*
gh#44.1 (a post-commit row glossed as "a real zero"), gh#46 (a misnamed key dropped and
then reported as missing), gh#45 (a version number printed as a nag when the tool could
name the reporter's own fixed gaps).

- **[gh#44.1 / plant G-057 — fixed] `record` and `reach` say when the row was recorded
  AFTER the commit.** Working tree holds no `.gd`/`.tscn` change but `HEAD~1..HEAD`
  touched some → `This row was almost certainly recorded AFTER the commit, which
  destroys reach`, files named, `reach.post_commit_suspected` in the row. Unit-tested
  on a real scratch git repo (dirty code → not suspected; committed → suspected, row
  carries it). Phase 5 in `/verify` now says "before the commit, because reach is
  computed from the diff".
- **[gh#46 / plant G-058 — fixed] `record` names unknown `run.json` keys with the
  nearest known one** (`'phase4' (did you mean 'checks'?)`, `'notes' → 'expected'`;
  a `checks[]` entry with `check`/no `name` is called out), and **`record --schema`**
  prints the key set. Unit-tested end to end.
- **[gh#45 — fixed] `harness-version --client` names the project's own open gaps
  credited as fixed in releases it does not have**, plus a `N release(s) behind` count,
  plus — the half the reporter did not ask for and the more common case — the open
  gaps already credited in the templates the project *runs* (fix installed; log line
  stale). Read-only run against the two live checkouts: plant `5 release(s) behind`,
  5 not-have (G-050..G-054), 12 stale-in-log; moving-in `7 behind`, 1 not-have
  (G-058), 24 stale-in-log. `/verify` Phase 0 runs and quotes it. Unit-tested with a
  fake project + fake newer root (config/name and dir name both searched).
- **[gh#44.2 / plant G-056 — fixed] the Gotcha:** *a single read of a timer-driven
  property is not a measurement* — beside "a run that never changes is broken, not
  passing", the same lesson pointing the other way.

**PURPOSE.md gained the commitment** "When the tool cannot tell two states apart, it says so — it never prints the benign one", in the reporter's own words.

**Considered and not done:** rewriting the "a real zero" phrasing itself — it is still
right when neither condition holds, and the new line prints instead of it in the
suspect case; a `--dry-run` for `record`; the bus-side beads (unchanged).

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.44.0
(14 files, 57 verbs + 60 CLI). `unittest discover -s tools` — 82 OK (4 new).
`check_templates.py` — OK, all stages (`dev_tools.gd` stamp-only since 0.43.0's
`--full`). `run_tests.gd` unchanged this tick, so no real-suite run (per CLAUDE.md the
rule is for changes around/between tests).

## 2026-08-17 - Upstreamed 2 open gap(s) from moving-in (harness 0.43.0)

Pooled by `tools/upstream_gaps.py` from `C:\Users\gotmi\Documents\GitHub\moving-in\log-devtools.md`. Gap text is the project's,
verbatim; only the id line is rewritten (qualified with the project name so
ids from two projects cannot collide, plus a `source:` back-pointer).

- Gap: **`upstream_gaps.py` mints a new id for a repeat sighting that names its own id.**
  ```
  $ python tools/upstream_gaps.py log-devtools.md --into /tmp/sink.md
    + moving-in:auto-04954d appended      <- `aabb ...` (no id line: correct to mint)
    + moving-in:auto-674313 appended      <- `**[G-021] performance reports one ...**`
    + moving-in:auto-30e69f appended      <- `**[G-024] the bridge cannot pin the camera ...**`
    + moving-in:auto-ec6688 appended      <- `**[G-025] every engine-side gate ...**`
    + moving-in:auto-3fdcfc appended      <- `**[G-025] ...**` again, same id, second sighting
    + moving-in:auto-aeb2e8 appended      <- `**[G-044] again**`
    - G-002 (status: fixed) skipped
  ```
  Four of those five carry a `[G-NNN]` in the Gap title and a `status:` on the wrapped
  continuation line; the parser only reads `- [G-NNN] status:` as its own list item. Two of
  them are the SAME gap (`G-025`, seen 2 and seen 3) and got different auto ids, so the
  dedupe the tool advertises — "deduped by id, re-running is a no-op" — silently does not
  apply to the entries most likely to matter, which are the ones seen more than once.
  - [moving-in:G-065] status: fixed | fixed-in: 0.45.0 | seen: 1 | harness: 0.43.0 | source: moving-in 2026-08-17 | dup-of: gh#47
  - Improvement: fall back to a `\[G-\d{3}\]` match anywhere in the gap's first line before
    minting an `auto-` id, and treat a `status:` found anywhere in the paragraph as the
    entry's status. Both are one regex each, and either alone kills four of the five.
    Failing that, the tool should say what it did — `minted auto-674313 (no id line found;
    the title mentions G-021 — is that the same gap?)` — because the current output looks
    identical whether it deduped correctly or not.

- Gap: **`--baseline-write` puts the acceptance somewhere it cannot be committed.**
  ```
  $ python tools/devtools.py findings --no-scenes --baseline-write
    Signal baseline: written to user://signal_findings_baseline.json - 11 pair(s) accepted.
  $ python tools/devtools.py findings --no-scenes ; echo $?
    0 finding(s) across 4 of 5 checks
    0
  $ git check-ignore -v .../app_userdata/moving-in/signal_findings_baseline.json
    fatal: ... is outside repository
  $ python tools/devtools.py findings --help | grep baseline
    [--no-baseline] [--baseline-write]        # no path argument
  ```
  The gate is green **on this machine only**. A fresh clone, a second developer or CI sees
  eleven findings again with no record that they were ever adjudicated — and the whole
  point of an accepted baseline is that the adjudication is durable. Note the asymmetry
  this creates: the *reasons* are versioned in the repo (`MULTIPLY_DECLARED`,
  `FIRE_AND_FORGET`, both asserted total) and only the *acceptance* is not, so the
  evidence survives and the verdict does not.
  - [moving-in:G-066] status: fixed | fixed-in: 0.45.0 | seen: 1 | harness: 0.43.0 | source: moving-in 2026-08-17 | dup-of: gh#48
  - Improvement: `--baseline-write [PATH]`, defaulting to `.devtools/` — which this
    project already commits, for exactly this reason, since `verify-runs.jsonl` lives
    there and the harness's own docs say to commit it. `lint_project.gd` already takes
    `--baseline PATH` / `--baseline-write PATH`, so the flag shape exists one tool over;
    this is making `findings` match its sibling rather than inventing anything.

## 2026-08-17 — 0.45.0: loop tick thirteen — the first project on a current build, and what it found

Reviewed: 13 open beads, two NEW issues (gh#47, gh#48), `PURPOSE.md`, both project logs.
**moving-in refreshed 0.36.0 → 0.43.0 this tick** — the first sibling project on a
build newer than 0.38.0 — and both issues came from that session running the tool as
shipped. Its log also arrived with four `auto-` gaps that were not gaps: repeat sightings
whose id sat in the Gap title. Pooling was rerun after the parser fix; the pooled log
gained two real gaps (G-065, G-066), not six.

- **[gh#47.2 / moving-in G-065 — fixed] `upstream_gaps.py` reads the id from the Gap
  title and `status:` from the wrapped paragraph** before minting; the output says
  `(id read from the Gap title)` / `(minted: no id anywhere in the entry)`. Re-pooling
  the reporter's log: the four sightings resolved to G-021/G-024/G-025/G-044 (three
  skipped as fixed, one already present) — verified against the earlier bogus run in
  this same tick. Unit-tested.
- **[gh#47.1 — addressed, and the report's premise corrected] `scaffold_install.py
  version` and one version record.** The reporter inferred that running the loaded
  0.33.0 body would have installed 0.33.0 over 0.36.0 "with no objection"; it would
  not — `full` has refused `DOWNGRADE` (exit 2, nothing touched) since 0.33.0 itself
  (gh#32, `da94ffe`), and I said so in the close rather than let a wrong premise stand.
  What was real: two records of the project's version (config vs manifest) that could
  drift, a manual pre-flight the doc presented as the only guard, and a command body
  that never said its own version. Now `vendored_version` takes the newest of config /
  manifest / stamps; `version` mode prints the transition, the body's own version and
  the newest on the machine, exit 3 `STALE COMMAND BODY` when the loaded skill is
  behind the cache; step 1.4b of the command runs it first; step 6 says the installer
  refuses regardless.
- **[gh#48 / moving-in G-066 — fixed] findings baselines live in the project's
  `.devtools/`**, not `user://`: `--baseline-write` writes there (committable, like the
  ledger), reads prefer it and fall back to the legacy `user://` copy, `--baseline-dir`
  pins another, an exported build falls back to `user://` and the reply says so. Stage
  5 asserts both baseline files land under the scratch project's `.devtools/`. The
  reporter's asymmetry — "the evidence survives a clone and the verdict does not, which
  is exactly backwards" — is the whole argument.

**Distribution, again.** moving-in on 0.43.0 filed two issues in its first session on
it; plant is still on 0.38.0. Every issue this week came from a session that had just
refreshed or was about to. That is the pipeline working: a project that refreshes finds
things a scratch project cannot. The cost is that they arrive as issues rather than as
`--full` failures, and (H-070) the real-suite step that would move some of them earlier
is still a bead.

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.45.0
(14 files, 57 verbs + 60 CLI). `unittest discover -s tools` — 84 OK (2 new).
`check_templates.py --full` — OK, all stages, `baseline lives at res://.devtools/ (committable)`, `stage 6 contract: 90/90`. `run_tests.gd` unchanged this tick (no
real-suite run needed).

## 2026-08-17 — 0.46.0: loop tick fourteen — quiet again, so the two instruments the loop kept wishing for

Reviewed: 13 open beads, the tracker (0 open), `PURPOSE.md`, both project logs — nothing
new. Second quiet tick. Two backlog items got built, both instruments rather than
features, both things this loop had been doing by hand.

- **[H-070 — fixed] `tools/check_real_suite.py <sibling>`.** Copies the sibling to
  scratch (never the sibling itself), sets BOTH custom-user-dir keys, runs the suite as
  shipped (BEFORE), installs the working tree's templates, runs again (AFTER), exits 1
  on a regression. **It caught itself three times before it worked**, each a lesson
  this repo already had written down: (1) its first run printed `real suite OK … 0/0
  passed both times` over two `exit 2` runs — the exact "success over nothing" shape
  PURPOSE forbids; now `Total 0` / exit 2 on either side is exit 2 (BEFORE) or exit 1
  (AFTER), never OK. (2) The exit 2 was its own doing: argparse `nargs=REMAINDER` had
  swallowed the script's own `--godot PATH` into the passthrough and forwarded it to
  `run_tests.gd`, which refused it — the same argparse shape moving-in G-049 hit in
  0.37.0. Now it splits on a literal `--` itself and passes the binary via `$GODOT_BIN`
  with nothing but `-p`, because the *project's* wrapper may be older than this one
  and refuse an option it never had. (3) The cause sat two layers down in a tail the
  script did not print; it prints the tail now. Real run on plant: `BEFORE (0.38.0):
  Total 561 | Passed 560 | Failed 1 | exit 1 | 69s`, `AFTER (0.46.0): 561 | 560 | 1 |
  exit 1 | 55s` — the one failure is the project's own, present on both sides. Wired
  into CLAUDE.md and the release skill: required whenever `run_tests.gd` changes.
- **[H-027 — fixed] `devtools.py verb-usage`** — a count per verb from the bridge's own
  `devtools_log.jsonl`, generic/project told apart, and `generic verbs never called
  here: N of M`. Read-only against both live projects: plant 8,295 commands, moving-in
  4,653; the same seven verbs top both (`run_method`, `get_state`, `set_state`,
  `get_node_bounds`, `ping`, `scene_tree`, `find_nodes`); 22 of 57 generic verbs never
  called on moving-in. **Nothing trimmed** — every generic verb has a gap behind it and
  the tail is a 3D/2D split as much as disuse — but the number is a command now.

**On the bus-side beads (batch verb, wedged handler, reach-regression stats):** the
usage data says the bus is used heavily (13k commands across two projects) and
narrowly (seven verbs carry most of it). A batch verb would serve exactly that top
seven; it is the one bus-side bead the data now argues *for*. Left open, noted.

- Gap: no new gap this turn.

**Validation run this turn:** `record_version.py --record` then `--check` OK at 0.46.0
(14 files, 57 verbs + 61 CLI). `unittest discover -s tools` — 84 OK. `check_templates.py`
— OK, all stages (`dev_tools.gd` stamp-only). `check_real_suite.py ../plant-tower-defense`
— OK, lines above.
