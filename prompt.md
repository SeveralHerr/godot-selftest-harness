You're in the `godot-selftest-harness` plugin repo (v0.4.0) — a Claude Code plugin that installs a self-test harness into Godot 4.x projects: a file-based DevTools bridge, headless lint/test runners, a `/verify` gate, and a `log-devtools.md` gaps log meant to feed real friction back here.

The loop has run exactly once (0.4.0 closed the gaps it recorded) and has since stalled. Read the evidence before writing anything:

- `C:\Users\gotmi\Documents\GitHub\gather\log-devtools.md` — a real game using the harness. Six gaps, none of them upstreamed.
- `./log-devtools.md` — this repo's own log. One entry, about itself.

Everything you change belongs in `templates/`, `commands/`, or the README. The game repo's copies are installs — don't edit them.

Ship this as 0.5.0, one commit per item, in this order.

## Part A — fix the feedback loop itself (priority)

**A1. Gap ids and status.** Gap entries are prose with no identity, so nothing can distinguish an open gap from one fixed two versions ago, and a recurrence can only be narrated in a sentence. Add a per-gap line to the format in `templates/log-devtools.md`: `- [G-007] status: open | seen: 2 | harness: 0.4.0`. Ids stable, never reused. Update the Format section, `commands/verify.md`, and `templates/CLAUDE.harness.md` so entries are written this way from now on. Retrofit ids onto the existing entries in both logs.

**A2. Harden the Stop hook.** `templates/tools/check_devtools_log.py:132` only checks whether `log-devtools.md` shows up in `git status`. Any byte-level change satisfies it, so a session that appends "no gaps this turn" forever passes forever — precisely the decay the hook exists to prevent. Require an entry whose `## ` heading carries today's date. Keep it advisory by default and keep the exit-0 "never break a session" guarantee.

**A3. An upstream path.** The only transport today is a human pasting a log between repos, and it hasn't happened — six gaps are still sitting in the game repo. Add `tools/upstream_gaps.py` (a template, plus a copy usable in this repo) that reads one or more project logs, appends their open gaps to this repo's `log-devtools.md` deduped by id, and bumps `seen:` when an id reappears. No PR, no review step — evidence pooling should be boring and safe. Document it in the README.

**A4. Version stamping.** Nothing reports which harness version a project has installed, so a gap can't be tied to a version and a refresh can't tell a stale file from a customized one. Stamp `# harness-version: X.Y.Z` into copied tool scripts, add a `harness-version` devtools verb, and print it in `lint_project.gd`'s header. A3 and B4 both depend on this.

## Part B — the gaps the game actually hit

**B1. `run_tests.gd` filtering — highest value, two separate agents hit it in one session.** `--filter` matches method names only (`templates/tools/run_tests.gd:174`), so a filter that matches nothing skips the entire suite and still exits 0 with `Total: 0 | Passed: 0 | Failed: 0` — byte-identical to a clean pass for an agent grepping exit codes. Match `--filter` against the test script filename as well, add `--file <basename>`, and make a run that selected zero tests exit non-zero with `filter '<x>' selected 0 of N tests`.

**B2. `lint_project.gd` missing-UID blindness.** The UID pass only validates sidecars that already exist, so a script created outside the editor with no `.uid` at all reports `UIDs: OK`. Flag `.gd` files under `scan_root`/`test_dir` with no sidecar as a warning.

**B3. `devtools.py` single-bridge collision.** One command/result file pair in `user://` means two Godot instances silently answer each other's commands, and parallel agents can't do runtime verification at all. Add `--session <id>` deriving the filenames (default = current behavior), and document the `--session` + `use_custom_user_dir` recipe in the README and in `commands/scaffold-godot-harness.md`.

**B4. Scaffold refresh hygiene.** Step 4 backs up on any byte difference, so a plain version bump left three untracked `.bak` files in the game repo that scaffold can never clean up. With A4's stamp in place, skip the backup when the existing file matches a known previous template version. Step 7 is blind the same way for config keys — write a `_scaffold_defaults` block into `devtools_config.json` recording what scaffold last wrote, so a later run only overwrites untouched keys.

**B5. Make a refresh reviewable.** When scaffold runs in a git repo that already has the harness installed, work on a branch (`harness/refresh-<version>`) and finish by printing a diff summary — so an upgrade gets read as a diff instead of discovered afterward as junk in `git status`.

## Finish by

- Bumping `.claude-plugin/plugin.json` to 0.5.0
- Adding an entry to this repo's `log-devtools.md` recording which gap ids 0.5.0 closed
- Telling me what to run in the game repo to pick this up, and which gaps I should expect to see marked fixed there

If an item turns out to be wrong or much larger than it looks, do the rest and say so — don't silently drop it.
