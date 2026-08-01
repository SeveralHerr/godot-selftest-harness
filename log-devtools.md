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
  - [H-001] status: open | seen: 1 | harness: 0.4.0
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
  - [H-005] status: open | seen: 2 | harness: 0.4.0
  - Improvement: unchanged — `tools/check_templates.sh` per the previous entry.
