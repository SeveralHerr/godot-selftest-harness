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
  - [H-027] status: open | seen: 1 | harness: 0.10.0
  - Improvement: have the bridge append `{verb, ts}` to a rotating counter file per
    session, and a `verb-usage` subcommand that reports never-invoked verbs. Cheap, and it
    turns the growth of the surface area into something with a denominator.

- Gap (open): **84% of gaps come from one game, and the log cannot show that.** Of 157
  entries, 132 are `gather:G-*` and 21 are `H-*`. "The core is game-agnostic" is a design
  commitment currently validated against a single project, and a second scaffolded project
  would be worth more directional information than the next ten verbs. Nothing in the log
  or in `upstream_gaps.py` surfaces the concentration.
  - [H-028] status: open | seen: 1 | harness: 0.10.0
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
  - [H-029] status: open | seen: 1 | harness: 0.11.0
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
  - [H-038] status: open | seen: 1 | harness: 0.12.0
  - **Not reproduced.** This was found by reading the dispatch path, not by hitting it,
    and per `[H-033]` that makes the mechanism above a hypothesis until a scratch probe
    confirms it. The probe is small: register a project verb that awaits 5 s, call it,
    and write a second command file by hand while it runs.
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
  - [H-040] status: open | seen: 1 | harness: 0.14.0
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
