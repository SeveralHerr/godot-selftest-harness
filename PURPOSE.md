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
someone has to be looking at. Does the UI make sense? Does the recent change fit the style of the game? Does the user have the best possible experience here? That makes the two habits that work everywhere else fail
here: an agent can't check its own work (it can read the diff, but not watch the thing
move), and CI can't either. The usual fallback — a human clicking through the editor —
doesn't scale to "after every change."

So the harness exposes the running scene tree over a file bus: spawn entities, inject
input and touch, read node state, snapshot the UI, measure FPS and orphan growth, and
assert on all of it from a script. What a human would have verified by watching becomes
something a script — or the agent driving the bus — can assert, which means it can happen
on every change instead of at the end.

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

**Coverage is reported, not implied.** Every pass names what it looked at — `Shaders: N
of M`, `Assertions: N executed`, `Orphans: N of M public function(s)`, `reached N/M` —
so "checked, found nothing" and "never looked" cannot print the same line. The corollary
is that an advisory check runs by default and *reports*; it is not opt-in and silent.
The orphan scan spent nine releases behind a flag because it is a heuristic, which
justified not *failing* on it and never justified not *running* it: the default gate
passed on a method nothing could call, and no line of output said the check existed. A
verb obeys the same rule from the other side: it answers the question it was asked, and
when it cannot — the selector matched nothing, the row is only reachable by scrolling,
the geometry was measured headless — it says which, rather than returning a well-formed
answer to a different question. The A/B study found the harness's one measured
advantage over a hand-rolled selftest was an assertion the bespoke one omitted; its
one measured *dis*advantage was a verb that lied. A tool that cannot be trusted loses to
the selftest the model writes anyway.

**Gate on the number that means something.** `orphan_max: 0` is unreachable — a fresh
launch reports dozens — and a threshold nothing can satisfy trains you to skip the check.
Growth-since-baseline is the number that means "this change leaks." Same reasoning behind
lint baselines: `NEW` vs `PRE-EXISTING` beats hand-triaging repo debt per file. And a
number is a measurement only with its spread: one frame's FPS read straight after a
settings change ranked three quality presets `110 / 50 / 105` and nearly shipped the
slowest as the fastest. `performance` reports a mean over a window with min, max and
whether it is still settling, because a single sample presented as a rate invites
exactly the wrong conclusion.

**It shares the machine.** The normal case is now several sessions, agents and git
worktrees on one box, each with a game of its own — and a bus that assumes it is alone
turns every one of them into a source of plausible wrong answers. So: a headless gate
never touches a bus it did not open (a `--script` runner is passive — it does not claim,
clear or poll); nothing is ever killed by image name, only by a pid this project
started and can name; the owner record and `ping` carry the checkout the game runs
from, and a client refuses to send to a game from another one rather than reading its
replies as its own. Each of these was learned from a session that debugged its own scene
for a cycle while a neighbour's game answered — the failure never says "wrong game", it
says `Root node not found`.

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
- **Not concurrent on one bus.** One command file, one result file, one client per bus.
  Request ids make a collision *detectable*, not *safe*. (Several buses on one machine
  is the supported case — see "It shares the machine" — one bus with several clients is
  not.)
- **Not a pixel-diff tool.** Headless renders nothing; `instantiate_ui` makes layout
  assertable, not appearance. Visual regressions still need a running game and eyes.
- **Not a place for game logic.** If a verb needs to know what a combo is, it belongs in
  that game's extension.

## How to tell it's working

A session changes a gameplay script, runs `/verify`, and gets back a specific claim about
the running game — this node moved, this label reads that, orphan growth was 3 — instead
of "the diff looks right." When `/verify` can't reach the changed code at runtime, it says
so explicitly rather than reporting a code read as verification.

It is also working when it declines. A rename, a pure-logic change with its own unit
tests, a project with no main scene: the honest verdict there is `overkill` or
`inconclusive`, and `/verify`'s tiering exists so the launch is skipped rather than run
for the picture. Across the project logs roughly one run in seven is `overkill` (13 of 90 verdicts, August 2026), and every
one of those entries is a session that could tell — the `Value:` block is what lets the
tool say *use me less* as readily as *add a verb*.

See `README.md` for the short introduction, `REFERENCE.md` for the full reference,
`CLAUDE.md` for how to work in this repo, and
`log-devtools.md` for what's currently missing.
