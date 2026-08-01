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
  - [H-001] status: open | seen: 2 | harness: 0.5.0
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
  - [H-004] status: open | seen: 1 | harness: 0.4.0
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
  - [H-005] status: open | seen: 1 | harness: 0.4.0
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
  - [H-007] status: open | seen: 1 | harness: 0.4.0
  - Improvement: the generic-verb set is machine-extractable from the `register_command(`
    calls — a check could diff it against the three docs and fail on a verb that only
    exists in the code.

- Gap: **still no way to validate a template change** — unchanged from the entry above,
  second appearance. `CLAUDE.md` now writes down the manual scratch-project procedure,
  which makes the gap cheaper to work around and no closer to closed.
  - [H-005] status: open | seen: 3 | harness: 0.5.0
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
  - [gather:G-009] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have the DevTools autoload write a `devtools_owner.json` with its PID
    and start time, and have `devtools.py` refuse to run (naming the other PID) when a
    live owner file belongs to a different process. Failing that, make `ping`'s
    "game not running" message list matching OS processes.

- Gap: **`run-method` requires an absolute `/root/...` path while every other verb takes
  the short form.** `--node Main/InputManager` returned `Failed: Node not found:
  Main/InputManager`, but `cmd`-registered verbs resolve `Main/...` fine via
  `get_tree().root.get_node_or_null`. The inconsistency cost a debugging round on a
  path that was actually correct.
  - [gather:G-010] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-015] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [H-009] status: open | seen: 1 | harness: 0.5.0
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
  - [H-010] status: open | seen: 1 | harness: 0.5.0
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
  - [gather:G-021] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-022] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-016] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-017] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
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
  - [gather:G-020] status: open | seen: 1 | harness: 0.4.0 | source: gather 2026-08-01
  - Improvement: have `/scaffold-godot-harness` delete its own `.bak` files once the refreshed
    file passes a syntax check, so a completed refresh leaves no residue to mistake for drift.
