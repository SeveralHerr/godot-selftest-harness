# Purpose

## What this repo is

A Claude Code plugin that installs a **self-testing harness into someone else's Godot 4.x
project**. Nothing here runs against a game of its own — this repo is a directory of
templates plus two slash commands that copy, wire, and drive them.

Three things get installed, and they only matter together:

1. **A file-based DevTools bridge** — an autoload that polls `user://` for a command file
   and writes back a result, so any process (a Python CLI, a shell script, Claude Code)
   can drive a *running* game.
2. **Headless lint and test runners** — they need no display and no game, so they are
   cheap enough to run on every change.
3. **`/verify`** — a diff-aware gate that ties lint, tests, and a live runtime smoke test
   into one pre-commit answer.

## Why it exists

The interesting behavior of a game only exists at runtime, inside a scene tree that
someone has to be looking at. That makes the two habits that work everywhere else fail
here: an agent can't check its own work (it can read the diff, but not watch the thing
move), and CI can't either. The usual fallback — a human clicking through the editor —
doesn't scale to "after every change."

So the harness exposes the running scene tree over a file bus: spawn entities, inject
input and touch, read node state, snapshot the UI, measure FPS and orphan growth, and
assert on all of it from a script. What a human would have verified by watching becomes
something a script can assert, which means it can happen on every change instead of at
the end.

Inspired by [tea-leaves](https://github.com/cleak/tea-leaves), adapted to Godot.

## Design commitments

These are the decisions that shape every other one. Changing one is a change to what the
project is, not a refactor.

**The core is game-agnostic.** No verb in the core may know about coins, enemies, or
scores. Projects register domain verbs through `res://devtools_ext/commands.gd`
(last-writer-wins, so a project may override a generic verb). The moment game concepts
leak into the core, the harness stops being reusable and starts being one game's debug
menu — which is what the removed `spawn_coin` / `get_catcher_state` era actually was.

**Detected, not baked.** Project names, paths, scene names, and thresholds are detected at
scaffold time and written to `devtools_config.json`, or discovered at runtime
(`scene-tree`, `list-commands`). A template that hardcodes a project value is a bug.

**Scaffolding is idempotent, and it never clobbers.** Re-running `/scaffold-godot-harness`
on an installed project must be a no-op or a refresh — never a duplicated autoload line, a
truncated log, or an overwritten `commands.gd`. Addon-owned files refresh freely;
project-authored files are created-if-absent or backed up.

**Failures must be loud, and distinguishable from success.** This is the recurring failure
mode of the whole category: a dead session answers every query with well-formed zeros, and
that reads exactly like a clean pass. Hence request ids (a crossed reply errors instead of
returning someone else's data), the deleted-command-file liveness signal (a dead game
fails in ~2s naming the directory it polled), runner exit code `2` meaning *you verified
nothing* rather than *it's clean*, and the status provider that rides on every response.
A check that can't tell "broken" from "fine" is worse than no check, because it is
believed.

**Gate on the number that means something.** `orphan_max: 0` is unreachable — a fresh
launch reports dozens — and a threshold nothing can satisfy trains you to skip the check.
Growth-since-baseline is the number that means "this change leaks." Same reasoning behind
lint baselines: `NEW` vs `PRE-EXISTING` beats hand-triaging repo debt per file.

**The gaps log is the improvement pipeline.** Nearly every capability past v1 — the status
provider, node-path normalization, `--property`, `step-time`, the touch verbs, the orphan
baseline — exists because a session wrote down what it couldn't do at the moment it
couldn't do it. That evidence is perishable: once a workaround is found, the friction that
forced it is forgotten by the next turn. Entries that quote real output become features;
"it was awkward" doesn't.

## Non-goals

- **Not a test framework.** `run_tests.gd` is deliberately small. GUT and friends exist.
- **Not a CI service.** It produces exit codes; wiring them to a pipeline is the project's
  job.
- **Not concurrent.** One command file, one result file, one client. Request ids make a
  collision *detectable*, not *safe*.
- **Not a pixel-diff tool.** Headless renders nothing; `instantiate_ui` makes layout
  assertable, not appearance. Visual regressions still need a running game and eyes.
- **Not a place for game logic.** If a verb needs to know what a combo is, it belongs in
  that game's extension.

## How to tell it's working

A session changes a gameplay script, runs `/verify`, and gets back a specific claim about
the running game — this node moved, this label reads that, orphan growth was 3 — instead
of "the diff looks right." When `/verify` can't reach the changed code at runtime, it says
so explicitly rather than reporting a code read as verification.

See `README.md` for the full reference, `CLAUDE.md` for how to work in this repo, and
`log-devtools.md` for what's currently missing.
