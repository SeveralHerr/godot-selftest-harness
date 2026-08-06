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
| `harness_history.json` | sha256 of every shipped template file, per released version. The scaffolder reads it to tell a pristine older copy (overwrite silently) from a project-edited one (back up first). |
| `tools/record_version.py` | `--check` (stamps + mirrors + history agree with `plugin.json`) / `--record` (write this version's hashes). |
| `commands/scaffold-godot-harness.md` | The installer. 13 idempotent steps. |
| `commands/verify.md` | The pre-commit gate the target project runs. |
| `templates/addons/godot_selftest/dev_tools.gd` | The bridge core + all generic verbs (~2k lines). |
| `templates/addons/godot_selftest/scene_validator.gd` | Scene/UI validation, namespaced `GodotSelftest*`. |
| `templates/tools/devtools.py` | Python CLI client — the *other half* of the wire contract. |
| `templates/tools/lint_project.gd`, `run_tests.gd` | Headless runners. Exit `0` pass / `1` findings / `2` couldn't run. |
| `templates/tools/eval.gd` | Headless one-off script evaluator. Shipped and installed; easy to forget because no doc surface is required to name it. |
| `templates/tools/import_check.py` | Wraps `godot --import`, which exits `0` while printing parse errors, and exits `1` on them. Runs ahead of lint in `/verify`. |
| `templates/tools/check_devtools_log.py` | `Stop` hook installed into the target project. Always exits 0. |
| `templates/tools/upstream_gaps.py` | Pools a project log's open gaps into this repo's log, deduped by id. **`tools/upstream_gaps.py` is a byte-identical copy** — edit the template, then copy it across. |
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

**`python tools/check_templates.py`** does this, and it is the gate — a syntax error in
`dev_tools.gd` otherwise reaches a user's game before anything notices. Five stages:

1. `py_compile` every `.py`, `json.load` every `.json`.
2. Assemble a scratch Godot 4.x project from `templates/` (addon, tools, `devtools_ext`,
   `test/`, a minimal `project.godot` with the `DevTools` autoload).
3. Parse-check every `.gd` under it.
4. Run both headless runners; expect exit `0` from each.
5. Launch the scratch project and drive the bridge over the real file bus with the real
   `devtools.py`. Testing one half against a fake of the other is the thing that failed
   before, and this stage is why the check is worth its runtime.

Run it before committing any change under `templates/`, alongside
`python tools/record_version.py --check`. Say plainly which you actually ran — "templates
unchanged since last verified run" is a fine answer; "should be fine" is not. Stage 5
needs a real Godot binary; see the memory note for where it lives on this machine.

## Releasing a version

Every shipped file carries `# harness-version: X.Y.Z` and a matching `HARNESS_VERSION`
constant. They are what let a gap name the version it was seen on and let a refresh tell
a stale file from a customized one, so they must not lag the release:

```bash
python tools/record_version.py --record   # after bumping plugin.json and the stamps
python tools/record_version.py --check    # exits 1 on any drift
```

`--check` verifies three things at once: every stamp and constant equals `plugin.json`'s
version, `tools/upstream_gaps.py` is still byte-identical to its template, and
`harness_history.json` holds current hashes for this version. Run it before committing.
**Never edit or delete a past entry in `harness_history.json`** — the scaffolder uses it
to recognize files it shipped, and a rewritten hash turns a pristine file into one that
looks project-edited.

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

Every gap carries `- [<id>] status: … | seen: N | harness: X.Y.Z`. Harness-native gaps use
the `H-NNN` namespace; gaps pooled from a project arrive as `<project>:G-NNN` via
`tools/upstream_gaps.py` and are never renumbered. Pull before starting a release:

```bash
python tools/upstream_gaps.py ../<game>/log-devtools.md   # deduped by id, safe to re-run
```

Closing a gap means editing its status line to `status: fixed | fixed-in: <version>` in
**this** log — the project's copy stays open until that project refreshes and confirms it.

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


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


 After a session, reflect on it's the output and suggest concrete imrpovements for the repo. 
