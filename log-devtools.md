# Devtools / `/verify` Gaps Log — harness development

Gaps found while building `godot-selftest-harness` itself, and the smallest improvement
that would close each one.

This repo is the plugin, not a Godot game, so the entries here are a level up from the
ones the scaffolded `templates/log-devtools.md` collects: they are about what's missing
when **developing and validating the harness**, not when using it on a game. Same format,
same rule — an entry with quoted evidence is worth something later; "it was awkward" is not.

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
