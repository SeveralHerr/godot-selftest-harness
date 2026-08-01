# CLAUDE.md — working in this repo

**This repo is the plugin, not a game.** There is no `project.godot` here, so
`/verify` and `/scaffold-godot-harness` do not run against it — they run against a
*target* Godot project. Everything under `templates/` is inert text that gets copied
into someone else's repo. Read `PURPOSE.md` for what the project is committed to;
`README.md` is the reference manual; this file is how to work here.

## Repo map

| Path | Role |
|---|---|
| `.claude-plugin/plugin.json` | Name, version, description. **Bump `version` on any shipped change.** |
| `commands/scaffold-godot-harness.md` | The installer. 13 idempotent steps. |
| `commands/verify.md` | The pre-commit gate the target project runs. |
| `templates/addons/godot_selftest/dev_tools.gd` | The bridge core + all generic verbs (~2k lines). |
| `templates/addons/godot_selftest/scene_validator.gd` | Scene/UI validation, namespaced `GodotSelftest*`. |
| `templates/tools/devtools.py` | Python CLI client — the *other half* of the wire contract. |
| `templates/tools/lint_project.gd`, `run_tests.gd` | Headless runners. Exit `0` pass / `1` findings / `2` couldn't run. |
| `templates/tools/check_devtools_log.py` | `Stop` hook installed into the target project. Always exits 0. |
| `templates/devtools_ext/commands.gd` | Stub the target owns; `commands.example.gd` is the reference. |
| `templates/CLAUDE.harness.md` | The delimited section merged into the target's `CLAUDE.md`. |
| `templates/log-devtools.md` | Seed gaps log for target projects. |
| `log-devtools.md` | **This repo's own** gaps log — harness-development level, not game level. |

## Where a change belongs

- **Generic runtime verb** → `dev_tools.gd` **and** `devtools.py`, together. Never one alone.
- **Anything that knows a game concept** (a coin, a boss, a score) → does not belong in this
  repo at all. It goes in that project's `devtools_ext/commands.gd`. Add it to
  `commands.example.gd` if it's worth showing as a pattern.
- **A detected or tunable value** → `devtools_config.json` schema + scaffolder step 7 + the
  README table. Never hardcode a project's value into a template.
- **Workflow change** (what to run, in what order, what to report) → `commands/verify.md`.
- **Install/merge behavior** → `commands/scaffold-godot-harness.md`, keeping every step
  idempotent: re-running must never duplicate an autoload line, truncate a log, or
  overwrite `commands.gd`, a real `CLAUDE.md`, or a project's `.claude/settings.json`.

## The wire contract is the risk surface

Every handler returns exactly `{success: bool, message: String, data: Dictionary}`. The
request carries an `id`; the response echoes it.

The GDScript side and the Python side are the only two halves that must agree, and **that
seam is where the bugs have actually been** — three key mismatches shipped at once (`0.4.0`)
because each half was tested against a hand-rolled fake of the other and both reported
green. One of them printed `No active touches to clear` while successfully clearing two.

So: when you change a verb's `data` keys, change both files in the same edit and state the
key names explicitly in the commit. If a client print path can't find the key it wants, it
must say so rather than falling back to a friendly line — a silent fallback is what made
those three invisible.

Also invariant: the core must hold a live reference to the instantiated extension
(`var _extension`), or every project verb's `Callable` is freed. `_ready()` order is
globalize paths → load config → register generic handlers → load extension → clear stale
files; the extension loads last so a project can override a generic verb.

## Validating a template change

Nothing in this repo checks the templates before they ship — a syntax error in
`dev_tools.gd` reaches a user's game before anything notices. This is a known open gap
(see `log-devtools.md`; the fix is a `tools/check_templates.sh`). Until it exists, do it by
hand in the scratchpad:

1. Assemble a scratch Godot 4.x project from `templates/` (addon, tools, `devtools_ext`,
   `test/`, a minimal `project.godot` with the `DevTools` autoload).
2. Parse-check every changed `.gd` under it, `py_compile` every changed `.py`, and
   `json.load` every `.json`.
3. Run both headless runners against it; expect exit `0`.
4. If you touched the bridge, launch the scratch project and drive the changed verb over
   the real bus with the real `devtools.py`. Testing one half against a fake of the other
   is exactly the thing that failed before.

Say plainly which of these you actually ran. "Templates unchanged since last verified run"
is a fine answer; "should be fine" is not.

## Docs move together

A generic verb appears in up to four places. Changing the verb without changing these
leaves a cheat-sheet that lies:

- `README.md` — generic-commands list, notable behaviors, sharp edges, CLI flags.
- `templates/CLAUDE.harness.md` — the target project's always-on cheat-sheet (keep it
  lean and reference-style; it is per-session context, not a manual).
- `commands/verify.md` — the Phase 4 primitives table.
- `commands/scaffold-godot-harness.md` — only if the config schema or install set changed.

## This repo's gaps log

`log-devtools.md` here records what's missing when **developing and validating the
harness** — one level up from the game-level log that `templates/log-devtools.md` seeds.
Append an entry at the end of a working session: what you couldn't do, the real output that
proved it, and the smallest change that would have closed it. An honest "no gaps this turn"
line counts — it's what distinguishes an absent gap from a forgotten log. There is no
`Stop` hook wired in this repo; the discipline is manual here.

## Gotchas that have already cost time

- **`python3` exists on Windows and refuses to run.** The Microsoft Store App execution
  alias stub satisfies `command -v` and then errors on invocation. Probe interpreters by
  *executing* them (`"$c" -c "import sys"`), never by existence — in scripts and in docs.
- **The Windows Godot build often prints nothing to the console.** Redirect headless runs
  to a file and read it back; an empty console is not a pass.
- **A GDScript runtime error inside a test looks exactly like a pass.** No exceptions; the
  method aborts and returns `""` for a `-> String` test. `[ERR]` / `[SCRIPT ERROR]` on
  stderr is the only signal, and `Total: 0 | ALL TESTS PASSED` is not a pass — check the
  count.
- **`load()` succeeds on an unparseable script.** Guard with `can_instantiate()` and
  isolate the `.new()` so a surviving error can't abort the caller.
- **Editing `project.godot` while the Godot editor has the project open** gets silently
  reverted on the editor's next save. The scaffolder warns about this; keep the warning.
- **Keep addon `class_name`s namespaced** (`GodotSelftest*`) — they land in a project that
  has its own class names, and the core loads by path anyway.
