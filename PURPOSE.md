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
someone has to be looking at. Does the UI make sense? Does the recent change fit the
style of the game? Does the player have the best possible experience here? That makes
the two habits that work everywhere else fail here: an agent can't check its own work
(it can read the diff, but not watch the thing move), and CI can't either. The usual fallback — a human clicking through the editor —
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

**A pass's claim must match its actual reach — no narrower, no wider.** Two failure
directions, same fix. Overclaiming: `import_check.py` printed `Import OK` when `--import`
had only registered class names and never evaluated a function or `const` body, reading
as a compile verdict it wasn't. Underclaiming: `coverage_check.py` scored a directory
sweep that loads every scene on disk as UNCHECKED because it isn't a `res://x.tscn`
literal, while a *weaker* hardcoded load flipped it to COVERED — the incentive pointed at
the worse implementation. Both were caught by a session reading its own tool's source
after it said something the session already knew wasn't quite right (0.24.0, gh#21/#23).
A check's prose is a claim like any other verb's answer, and it is wrong the same two
ways: saying more than was verified, or crediting less than was actually observed.

**The docs it installs are a claim surface, and they fail the same two ways.** The
reach rule above was written about checks; it applies verbatim to every sentence
`/scaffold-godot-harness` writes into someone else's `CLAUDE.md`, because that file is
per-session context and a session acts on it without re-deriving it. Two independent
reports landed on this in one cycle. `CLAUDE.md` said a headless gate "brings the
autoload up passive: safe to run while another session drives this game" — true of the
*bus*, and read as being about headless runs generally, so a suite that writes a save
rewrote a developer's real one (`user://` is shared and `--isolated` does not isolate
it). And it said `name_check.py` resolves "engine classes and their members", which
reads as covering `x.method()`; it does not, and three mutations were spent proving
that (gh#64). Neither sentence was false about the thing it was describing. Both were
read as covering more, which is what an overclaim is. So a documented capability is
verified the way a verb's answer is — against what the code actually does — and where
a claim is narrower than it sounds, the doc says what is *not* covered rather than
leaving the reader to find the edge. `name_check` prints its own `NOT COVERED:` line
for this reason; that is the pattern, not an exception.

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
says `Root node not found`. The same holds one level up, for the **working tree**: two
sessions have converged on one checkout and edited it at once (H-061), so a release
re-reads `git status` immediately before it commits, and a fan-out never runs a
repo-wide git command — untracked work has no copy anywhere else (H-052).

**It must not ship with the game.** Every other commitment here is about the developer's
loop; this one is about the boundary of it. The bridge is an autoload, so it goes into the
`.pck` whether or not anyone meant it to, and until 0.61.0 the only thing distinguishing a
developer's launch from a player's copy was whether `--script` was on the command line —
which is false in both. A project exported to the web therefore ran its `entry_hook` on
every page load, calling `TitleScreen.skip_to_game()` so players never saw the menu, and
polled `user://` in a browser's storage for the life of every session. Nothing errored,
the export was green, and it took running the live build to notice (gh#58). That is this
project's own recurring failure mode aimed at a stranger: a well-formed result that reads
as fine. So a build with `OS.has_feature("template")` is inert — no config, no handlers,
no extension, no bus, no log, no hook — and a developer who wants the bridge inside a
build opts in deliberately, with a `devtools` feature tag on the export preset or
`--devtools-force` at launch. Two opt-ins rather than one because a web export has no
command line and a shipped desktop binary cannot be re-exported; the reporting project
had already hand-patched the second and guarded it with a test, which is the kind of
evidence that outranks a tidier design. The general rule: a
harness that can affect what a player sees has stopped being a harness, and the default
has to be off, because the person who would have to know to turn it off is exactly the
person who does not know it is on.

**When the tool cannot tell two states apart, it says so — it never prints the benign
one.** The reporter of gh#44 named the shape better than this file had: "the harness
knows a thing is ambiguous and reports the benign reading of it." A `reach 0/0` after a
commit was glossed *"a real zero: every changed file is excused"* when the diff had been
committed away; a `run.json` key the normaliser did not read was dropped and the row
then warned the evidence was missing; a version number was printed as a nag when the
tool could name the reporter's own fixed gaps. In each the tool held both halves of
the answer and printed the reassuring one. The rule is the same as "failures must be
loud" one level down: an ambiguity the tool can see is reported *as* an ambiguity —
both readings, and what would resolve it — and if it can be resolved from disk (a
`git diff HEAD~1`, a set difference, a grep of the newer templates) it is resolved
before it is printed.

**A verification run leaves the developer's state as it found it.** `--isolated`
isolates the bus and only the bus; `user://` is the game's, and a verb that behaves
correctly — `capture()` rebinding a key, `bank_score()` filing a run — persists there
exactly as it should. Two projects on one day (gh#39, gh#40) had a live pass write the
developer's real save that way and read the consequence twenty minutes later as
unrelated headless failures; one had spent the previous cycle closing the *suite* half
of the same hazard, and the bridge half structurally cannot be closed from test code.
So the default is the safe one: `launch` copies the save aside and `quit` puts it back,
and a run whose writes are the point opts out (`--no-snapshot-userstate`). The flag
that only helps people who already know the hazard exists is a flag for the wrong
population. And the restore reports itself every time — restored, nothing to restore,
kept because the game is still alive, or *not covered by the patterns* — because a
restore that silently does not happen is worse than none: the flag was the reason
to stop checking the file by hand (plant G-054, second sighting; moving-in G-063). The same rule reaches into the suite: a test that writes `user://` is
named by `run_tests.gd`, per test, so the redirect can go where the write is.

**Say it while it can still be acted on.** A report that arrives after the moment of
choice is a post-mortem, and the harness has shipped several of those before noticing:
`quit` said "this run wrote the developer's REAL user data — changed: highscore.save"
when the previous value existed nowhere (plant G-050); a stopped test run's Godot kept
writing into a later run's results file, and the two `Total:` lines sat there for a
human to spot after the verdict had been read (plant G-051b); a second checkout
shared the first one's `user://` and the launch line printed the path without saying
so (H-067). Each fix moved the sentence earlier or made the state recoverable: every
`launch` now copies the save aside so the warning at `quit` names a copy, not a loss;
`run_tests.py` refuses a mixed file instead of printing it; `launch` names the other
checkout on the line where `--snapshot-userstate` can still be added. The test for a
new warning is not "is it true" but "can the reader still do something about it when
they read it" — and if not, the tool's job is to have kept the way back open.

**A fix is delivered when the project runs it, not when it ships.** The gaps log
feeds this repo, and this repo feeds the projects back only through
`/scaffold-godot-harness`; a project stays on the version it was scaffolded with until
someone re-runs that. Two real projects sat on 0.21.0 and 0.25.0 through a day in which
0.26.0–0.31.0 shipped, and roughly half the gaps they pooled upstream that evening had
been fixed releases earlier — real friction, faithfully logged, against a bug that no
longer existed. So the tool says so from where the project stands: `harness-version`
names the versions already on the machine (the plugin cache, the marketplace clone,
this session's plugin) and says when one is newer than the install it is running in.
The distribution lag is a property of the system, and a system that reports on itself
must report that too.

**The gaps log is the improvement pipeline.** Nearly every capability past v1 — the status
provider, node-path normalization, `--property`, `step-time`, the touch verbs, the orphan
baseline — exists because a session wrote down what it couldn't do at the moment it
couldn't do it. That evidence is perishable: once a workaround is found, the friction that
forced it is forgotten by the next turn. Entries that quote real output become features;
"it was awkward" doesn't.

**A fixed report gets closed the same turn it's fixed, not just marked fixed in this
log.** Seven of nine open `skill-feedback` issues on this repo's tracker turned out to
already be fixed in `main` — some for two releases — because closing this log's own
`H-`/`gh#` line was treated as the whole job and the upstream issue was left open. A
stranger reading the tracker to decide whether the tool is worth trying sees nine open
defects where two are real; that is the same failure this project fixes everywhere
else — a reader who cannot tell "fixed" from "still broken" from the outside. Closing
the loop is part of the fix, not a follow-up.

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
