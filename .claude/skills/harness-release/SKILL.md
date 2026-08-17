---
name: harness-release
description: Cut a release of the godot-selftest-harness plugin — bump the version stamps, record hashes, run the validation gates, log the turn, and commit. Use whenever shipping a change in this repo, or when asked to "release", "bump the version", "cut 0.x.0", or when a change under templates/ is finished and needs to go out. Also use to check release state without shipping.
---

# Releasing the harness

Every shipped file carries a `# harness-version: X.Y.Z` stamp and a matching
`HARNESS_VERSION` constant. They let a gap name the version it was seen on and let a
refresh tell a stale file from a customized one, so they must never lag the release.
This is the exact sequence, in order. Do not skip a step because the change felt small.

## 0a. Confirm you are looking at the current HEAD

**Do this before reading a single source file.** Also `gh issue list --state open
--limit 5` — a parallel session can file an issue mid-turn (gh#17 arrived while 0.22.0
was being built and was addressed in the same release). The `gitStatus` block in a session's
context is a snapshot taken when the session started, and this repo can move under a long
session — a merged PR, a parallel session, a `pull --ff-only` between turns.

```bash
git log --oneline -3
git update-index --refresh >/dev/null; git status --short
```

(`update-index --refresh` first: a stale stat cache, or a parallel session, can move the
modified-file count under a long session — gh#17 saw 6 become 8 across one worktree
add/remove. Re-run it rather than remember it.)

Compare the top sha against whatever your context claims. If they differ, **re-read every
file you were about to change** and re-derive line numbers; do not edit by remembered
offsets. Then check whether the work you are about to do is already in the log:

```bash
git log --oneline -20 -- templates/          # has this shipped already?
grep -n "status: fixed | fixed-in" log-devtools.md | tail -20
```

This is not hypothetical. In one session three of five assigned fixes had already shipped
in a release committed minutes earlier, and were within one step of being rewritten on top
of a better version of themselves — the shipped fix covered five call sites where the
rewrite covered three, and had been validated against a real project the scratch one
cannot measure ([H-030]). A stale snapshot does not announce itself; the files simply read
as though the work is still to do.

The same applies to a gap you are about to close: check that it is not already `fixed` in
`log-devtools.md` under a different id. Gaps arrive by two paths — pooled from a project
by `tools/upstream_gaps.py` as `<project>:G-NNN`, and filed as GitHub issues by
`skill-feedback-issue` as `gh#N` — and **neither dedupes against the other**, so the same
defect can be open under one id and fixed under another ([H-044]).

## 0. Decide whether this is a release at all

A docs-only turn that touches nothing under `templates/` **does not need a bump**, and
saying so is a valid outcome. Check first:

```bash
git status --porcelain templates/
```

Empty means: no bump, no `check_templates.py`, and the log entry says "templates
unchanged since last verified run". That is an honest answer; "should be fine" is not.

Minor bump (`0.13.0` → `0.14.0`) for a new capability or a behavior change. Patch for a
fix that changes no interface.

## 1. Bump the stamps

Only **stamp lines** change. Prose mentions of an older version elsewhere in the docs are
historical facts ("reachable while paused since 0.12.0") and must be left alone — a blind
find-and-replace across the repo rewrites history and is the main way this step goes
wrong.

```bash
python .claude/skills/harness-release/bump_version.py 0.13.0 0.14.0
```

It edits every file in `record_version.py`'s `SHIPPED` list — 13 as of 0.19.0, and it
reads that list at runtime, so adding a shipped file needs no edit here — plus the
`tools/upstream_gaps.py` mirror and
`.claude-plugin/plugin.json`, and prints a per-file count. **Every file must report
`stamp=1 const=1`.** A `0` means a file drifted out of the expected shape — go look
before continuing.

## 2. Record and check

```bash
python tools/record_version.py --record   # write this version's hashes
python tools/record_version.py --check    # exits 1 on any drift
```

`--check` verifies three things at once: every stamp and constant equals
`plugin.json`'s version, `tools/upstream_gaps.py` is byte-identical to its template, and
`harness_history.json` holds current hashes for this version.

**Never edit or delete a past entry in `harness_history.json`.** The scaffolder uses it
to recognize files it shipped; a rewritten hash turns a pristine file into one that looks
project-edited and starts getting backed up on every refresh.

## 3. Run the gates

```bash
python tools/check_templates.py           # required if anything under templates/ changed
python tools/check_real_suite.py ../plant-tower-defense   # required if run_tests.gd (or what the
                                          # runner does between/around tests) changed - H-070
python tools/check_templates.py --full    # required if a generic bus verb was added or
                                           # changed - stage 6 exercises the full
                                           # every-verb contract table
python -m unittest discover -s tools      # scaffold/install unit tests
```

`check_templates.py` needs a real Godot binary. On this machine it resolves
`C:\Users\gotmi\Documents\Godot_v4.7.1-stable_win64.exe` by default. If it prints
`WARNING: no Godot binary found ... This is not a pass`, it returned **2** and you have
verified nothing — do not proceed.

**Run `--full` at least once per release, not only when a new verb needs it (H-062).**
It is opt-in and nothing routine exercises it, which is exactly how three genuine
contract-table mismatches (`clear_nodes`, `raycast`, `reachable_ui` all failing on a
perfectly clean 0.25.0 tree) sat unreported release after release: the default run
every past log entry quotes stayed clean throughout. A stage that only runs when
someone remembers to ask for it is the "reports success, is not running" shape this
project keeps finding everywhere else. **When a `--full` row fails on a clean tree,
check the fixture before the row:** an earlier stage-5 check may have mutated the
fixture (`check_raycast_3d` removes and restores `Wall2D`) and a restore helper whose
return value nothing asserts reads exactly like row drift — the `raycast` row in H-062
was that for seven releases (H-065, 0.32.0). Two of the three H-062 rows were drift;
the third was a real bug.

`python3` on Windows is the Microsoft Store alias stub: it satisfies `command -v` and then
refuses to run. Use `python`.

If the change touched a **static analysis** template (`name_check.py`,
`coverage_check.py`, the lint passes),
also run it against a real scaffolded project and report the finding count. The scratch
project is small and synthetic and cannot measure a false-positive rate — `name_check.py`
once passed every stage while emitting 466 bogus warnings on a real project, and
`coverage_check.py` shipped in 0.19.0 only because this step caught it reporting
`ui_layout` COVERED off the harness's **own seed test** on two of three real projects —
every freshly scaffolded project would have read as covered on day one. In both cases the
tool had already passed every scratch stage and its author's own fixtures. Fifteen
seconds here is the cheapest step in this file and has the best hit rate. Candidates with
the harness installed: `../gather`, `../findmyballs`, `../moving-in`.

If you added a check, it must **plant the defect it claims to detect** and be confirmed
to fail before shipping. A stage that can only report success is not a stage. The best
evidence is a check that failed *organically* while you built the fix (in 0.20.0 two of
three new controls did — one caught the wrong first implementation, one caught an
unrelated headless hang). For any check that has never been seen failing, run a mutation:

```bash
cp templates/addons/godot_selftest/dev_tools.gd "$TEMP/dev_tools.gd.orig"
# one-line edit that disables the behaviour the check asserts (e.g. make the
# helper return "" / false unconditionally), then:
python tools/check_templates.py > "$TEMP/mutation.log" 2>&1; echo "exit=$?"   # expect 1
grep FAIL "$TEMP/mutation.log"                                                # expect YOUR check
cp "$TEMP/dev_tools.gd.orig" templates/addons/godot_selftest/dev_tools.gd
git diff --stat templates/addons/godot_selftest/dev_tools.gd                  # prove the restore
```

**Prove the mutation landed before you run.** Put `git diff --stat` (or a `grep` for
the mutated line) *between* the edit and `check_templates.py`, and quote it beside the
FAIL line in the log. In 0.21.0 a mutation written through a shell heredoc had its `	`
/ `
` escapes rewritten by the tool, the script asserted-out, and the run that followed
printed every new check green against the pristine file — a mutation that did not
apply reads exactly like a passing control ([H-057]). Write the mutation script with the
file-write tool, not a heredoc, and check `s.count(anchor) == 1` inside it. (The same
heredoc rewriting bites *every* Python edit script that carries a `\n` or `\t` — in
0.22.0 three template edits silently failed their anchor for that reason before the
pattern was recognised. Any edit script with a backslash goes through the file-write tool.)

**Batch the mutations.** One run of `check_templates.py` costs ~5 minutes and every
check names itself in its FAIL line, so one run can carry every mutation whose checks
are independent — 0.22.0 proved eight in one run plus one follow-up. Restore from a
copy you took *before* mutating and prove it with `cmp` against that copy; never
restore a template with `git checkout --` (the file also holds the release's real edits).

**Do not edit the mutated file while the run is in flight** — the restore overwrites
whatever is on disk. Do other work (docs, log) for the five minutes, and quote the FAIL
line it printed in the log entry. Note that `bump_version.py` and any Windows-default
write may leave a template CRLF; anchors must match the file's actual line ending.

## 4. Log the turn

Append to `log-devtools.md` — this repo's own gaps log, at harness-development level.
The entry ends with a validation line naming what actually ran:

```markdown
**Validation run this turn:** `python tools/check_templates.py` — OK, including <the new
line it printed>. `python tools/record_version.py --record` then `--check` — OK at
`X.Y.Z`, 11 shipped files, N bus verbs + M CLI commands documented.
```

Close any gap this release fixed by editing its status line **in this log** to
`status: fixed | fixed-in: X.Y.Z`. A project's copy of a pooled gap stays open until that
project refreshes and confirms it. New gaps get
`- [H-NNN] status: open | seen: 1 | harness: X.Y.Z` with the next free `H-` number:

```bash
grep -oE '\[H-[0-9]+\]' log-devtools.md | sort -u | tail -1
```

An honest "no gaps this turn" line counts — it is what distinguishes an absent gap from a
forgotten log.

## 5. Reconcile beads

Close what shipped, and file what you found. Harness-native gaps should exist in both the
log and beads; they drift apart quietly.

```bash
bd close <id> --reason="<what shipped, and how it was verified>"
bd list --status=open
```

## 6. Commit and push

Only when the user has asked — and be precise about what "asked" covers. An unattended
"tackle these issues / cut the release" turn authorizes the *branch commit* (it is
reversible, and every past release arrived as one); it does **not** authorize a push or
a PR, which are outward-facing and stay a separate, explicit ask. If even the branch
commit was not clearly asked for, `git add -A` so the work has a recoverable object,
and hand off with the exact commands. **Work on a `release/X.Y.Z` branch, never directly
on `master`.** Every release in the log arrived that way — `Merge pull request #8 from
SeveralHerr/release/0.18.0`, `#4 from SeveralHerr/fix/...` — and the standing rule in the
user's global `CLAUDE.md` is *never commit to main*. **Cut it from `master`'s tip, not
from the previous release branch:** `git fetch && git checkout -b release/X.Y.Z master`
(a dirty tree carries over cleanly; `git stash` / `stash pop` around it if git refuses).
Two consecutive ticks on 2026-08-16 branched from `release/<previous>` instead; master
had gained a docs-only commit in between, so §6b's tree-hash assertion below failed
correctly, the `&&` chain stopped before the push, and the landing had to be finished
by hand each time. If you are already on `master` with the work in the tree,
`git checkout -b release/X.Y.Z` carries it over cleanly.

Landing it on `master` is a **separate, explicit ask.** When it comes, prefer a PR;
merge locally with `--no-ff` only if the user asked for `master` directly, so the history
keeps the merge-commit shape the PRs produce — and do it by §6b, never by checking out
`master` in the main worktree.

The commit message is the release note: what shipped, **why the obvious implementation
was not used** if it wasn't, how it was validated, and what was considered and rejected.
Recording a rejected option is worth as much as recording a shipped one — it is cheaper to
re-read than to re-refute.

```bash
git add -A && git commit -F - <<'EOF'
release X.Y.Z: <the one-line claim>

...

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git push -u origin release/X.Y.Z     # the branch; master is §6b, and only when asked
bd dolt push        # beads sync is separate from git; ask before running it
```

## 6b. Landing it on master (gh#17)

Separate, explicit ask; prefer a PR. When the user asks for `master` directly, **do not
`git checkout master` in the main worktree.** A release branch is normally dirty with the
next version's work, and `experiments/` and `.devtools/` are untracked by design with no
stash, reflog or Recycle Bin fallback ([H-052]) — `git checkout master` there either
refuses or carries all of it onto `master`, where the next `git add -A` commits an
unstamped half-version onto the default branch. Merge in a throwaway worktree instead; it
cannot reach the main tree at all:

```bash
WT="$TEMP/master-wt"
git worktree add "$WT" master
git -C "$WT" merge --no-ff release/X.Y.Z -m "Merge release/X.Y.Z: <release commit subject>"

# prove nothing from the working tree leaked in, and that the result is stamp-clean
[ "$(git rev-parse "$(git -C "$WT" rev-parse HEAD)^{tree}")" = "$(git rev-parse release/X.Y.Z^{tree})" ] \
  && echo "tree match OK"
(cd "$WT" && python tools/record_version.py --check)   # expect exit 0

git -C "$WT" push origin master
git worktree remove "$WT"
git update-index --refresh >/dev/null; git status --short   # your uncommitted work, still here
```

The tree-hash equality is the load-bearing assertion: a `--no-ff` merge of a descendant
branch must produce exactly the release commit's tree, so any difference means
working-tree content got in. **It only holds when the branch was cut from master's tip
(§6).** If master moved after the cut — the check fails but you know why — do not force
it: instead assert that the merged tree differs from the release tree by exactly the
commits master gained (`git -C "$WT" diff --stat release/X.Y.Z HEAD` names only those
files) and run `record_version.py --check` in the worktree; that is the same guarantee
by a different route. Do it in a *separate* command from the push, so a failed
assertion never leaves a half-finished landing. Then close the GitHub issues the release named
(`gh issue close N --comment "shipped in X.Y.Z"`) — they stay open until the merge, not
until the branch commit.

## Docs that must move with the code

Changing a verb without these leaves a cheat-sheet that lies:

| Surface | When |
|---|---|
| `REFERENCE.md` | always — `record_version.py --check` requires it to name every verb |
| `templates/CLAUDE.harness.md` | always — the target's per-session cheat-sheet; keep it lean |
| `commands/verify.md` | if the Phase 4 primitives table or the workflow changed |
| `commands/scaffold-godot-harness.md` | only if the config schema or install set changed |
| `templates/addons/godot_selftest/devtools_config.json` | new config key |
| `README.md` | almost never — it is the front door and names few verbs by design |
