#!/usr/bin/env python3
"""coverage_check.py - report on this project's *checks*, not on their results.

Why this exists: a capable model handed a Godot game writes itself a 50-70 check
selftest almost every time, and then signs off with "70 checks, 0 failures, confidence
high" over a screen a human can see is broken. The suite is not lying. It is thorough
about the questions it thought to ask, and structurally incapable of noticing the ones
it never asked -- a hand-rolled suite has no way to report the shape of its own hole.
Every other gate in this harness answers "did the checks pass". This one answers
"which questions do the checks ask at all", which is the only question whose answer a
green run cannot supply.

So it enumerates the defect classes this harness knows about from real Godot failures,
and for each one says whether anything in the project ever asks it:

    UNCHECKED  ui_layout       is a Control on screen, and is it bigger than 0x0
               cheapest cover: python tools/devtools.py validate-ui
    COVERED    orphan_growth   test/unit/test_leaks.gd:22  Performance.get_monitor

**Evidence, not verdicts.** Every COVERED prints the file, the line and the token that
convinced it. This is not decoration: a checker whose only possible output is "all
covered" is indistinguishable from one that is not running, and this repo has shipped
exactly that three times (see [H-035] in CLAUDE.md). The evidence line is what lets a
reader catch a false COVERED in ten seconds instead of never.

**Conservative on purpose.** A false UNCHECKED costs a reader ten seconds. A false
COVERED is the entire bug this tool exists to catch, so weak evidence does not promote
a class -- it is printed underneath the UNCHECKED verdict as a "weak signal" line
naming what was seen and why it is not enough. Three real examples of weak:

  * `ui.size` with no `instantiate_ui` -- a Control that never entered a tree reports
    0x0 for everything, so a suite can assert sizes all day without ever putting one
    on a screen. That is the failure, not the check for it.
  * `PackedScene` with no `res://....tscn` literal -- the harness's own example test
    builds a PackedScene in code. Counting that as "the project's scenes load" would
    mark scene_validation covered on a project with zero scenes tested.
  * `connect(` with no `is_connected` -- connecting a signal in a fixture is setup;
    asking whether the *game* connected one is the check.

**It never opens the Godot project.** No `godot` invocation, no `--import`, no write to
`.godot/`, nothing but reading files. Same property `name_check.py` has and for the same
reason: N agents can run it at once on one checkout, and it works in a git worktree that
has never been imported. It is advisory, not a gate: exit 0 always unless you ask for
`--strict`.

What it reads:
  1. the project's test dir -- every `test_*.gd`, with comments and string bodies
     blanked out so a token quoted in a docstring cannot fake coverage (the shipped
     `test_example.gd` documents `_T.instantiate_ui(...)` in a `##` header, which is
     precisely that trap);
  2. `test/sequences/*.json` -- an input sequence, but only counted as driving input
     when it actually contains a press/release/tap/hold step (the shipped `smoke.json`
     contains none, and is reported as weak);
  3. `.devtools/verify-runs.jsonl` -- what past `/verify` runs actually did;
  4. `devtools_log*.jsonl` -- which bridge verbs a session actually called;
  5. which gate tools are installed -- `lint_project.gd` compiles every shader on every
     run, `name_check.py` resolves every name, unconditionally.

Sources 3 and 4 are *past observations*, not standing checks, so they report
`COVERED (session)` and print the timestamp. An absent source is reported as ABSENT --
never as "nothing found there", which reads the same as "checked and clean".

What this is NOT: a measure of whether the checks are any good. It counts whether a
question is ever asked, not whether the answer was right, and its class list is this
harness's list of failures it has been burned by -- not a complete enumeration of ways
a game can be broken. A project can be UNCHECKED on all eight and correct, or covered
on all eight and broken.

Exit codes follow the harness convention (`name_check.py`, `lint_project.gd`,
`run_tests.gd`):
    0  reported (always, by default -- this is advisory)
    1  --strict and at least one class is UNCHECKED
    2  could not run: no project.godot, or an unreadable config

Usage:
    python tools/coverage_check.py                 # the report
    python tools/coverage_check.py --strict        # UNCHECKED classes gate
    python tools/coverage_check.py --json          # machine-readable
    python tools/coverage_check.py --only ui_layout
    python tools/coverage_check.py --test-dir test/unit --config path/to/config.json

(`python` on Windows -- `python3` there is a Microsoft Store alias stub that satisfies
`command -v` and then refuses to run.)
"""

import argparse
import datetime
import json
import re
import sys
from bisect import bisect_right
from pathlib import Path

# harness-version: 0.24.0
HARNESS_VERSION = "0.24.0"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_RUN = 2

# Same keys, same defaults, as run_tests.gd / lint_project.gd / verify_ledger.py. A
# disagreement here would have this tool scanning a directory the runner does not run.
DEFAULT_TEST_DIR = "res://test/unit"
DEFAULT_SCAN_ROOT = "res://"

CONFIG_REL = Path("addons") / "godot_selftest" / "devtools_config.json"
LEDGER_REL = Path(".devtools") / "verify-runs.jsonl"

STATUS_COVERED = "covered"                  # a check in this project asks the question
STATUS_BY_GATE = "covered_by_gate"          # an always-run tool asks it, every run
STATUS_BY_SESSION = "covered_by_session"    # a past session asked it once; not standing
STATUS_UNCHECKED = "unchecked"

STATUS_LABEL = {
    STATUS_COVERED: "COVERED",
    STATUS_BY_GATE: "COVERED (gate)",
    STATUS_BY_SESSION: "COVERED (session)",
    STATUS_UNCHECKED: "UNCHECKED",
}

# The scope of a clean verdict, in one sentence, printed where the verdict is -- the
# convention name_check.py's NOT_COVERED establishes. A tool that reports "8 of 8
# covered" and says nothing else has handed the reader a stronger claim than it made.
NOT_COVERED = (
    "a covered class means some check asks the question, not that the answer was right, "
    "and this list is the failures this harness has been burned by - not every way a "
    "game can break. Coverage here is a floor, never a pass."
)


# ---------------------------------------------------------------------------
# Source scanning
#
# Ported from name_check.py deliberately, not re-invented: the two tools scan the same
# .gd text and must agree about what is code. A token inside a comment or a string is
# the single most common way a text scan produces a confident finding about nothing --
# and here it produces a false COVERED, which is the one output this tool must never
# manufacture.
# ---------------------------------------------------------------------------

class SourceText:
    """A .gd file with comments and string bodies blanked out, positions preserved.

    Blanking replaces each comment and each string body with spaces of the same length
    and keeps newlines, so a regex can never match inside one while `line_of(pos)` still
    reports the real line in the real file. Quote *delimiters* survive, because the
    `load("res://x.tscn")` detector anchors on them. The bodies are kept separately in
    `strings` as (line, value) -- the one place this needs the text it erased.
    """

    def __init__(self, text):
        self.raw = text
        self.code, self.strings = _blank_strings_and_comments(text)
        self._line_starts = [0]
        for i, ch in enumerate(self.code):
            if ch == "\n":
                self._line_starts.append(i + 1)

    def line_of(self, pos):
        return bisect_right(self._line_starts, pos)

    def line_text(self, line):
        """The raw source line, stripped -- printed as evidence next to a match."""
        lines = self.raw.splitlines()
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def _blank_strings_and_comments(text):
    """Return (blanked_code, [(line, value, quote), ...]).

    Handles every GDScript literal form: ''' and \"\"\" blocks, ' and " strings, the r""
    raw prefix and the &"" / ^"" StringName and NodePath prefixes. Backslash escapes are
    honoured outside raw strings, so a literal containing a quote does not end the string
    early and leave the rest of the file parsed as code.
    """
    out = []
    strings = []
    i = 0
    n = len(text)
    line = 1
    while i < n:
        ch = text[i]
        if ch == "\n":
            out.append("\n")
            line += 1
            i += 1
            continue
        if ch == "#":
            j = text.find("\n", i)
            if j < 0:
                j = n
            out.append(" " * (j - i))
            i = j
            continue
        if ch in "\"'" or (ch in "r&^" and i + 1 < n and text[i + 1] in "\"'"):
            prefix = ""
            if ch in "r&^":
                prefix = ch
                i += 1
                out.append(" ")
                ch = text[i]
            raw = prefix == "r"
            triple = text[i:i + 3] in ('"""', "'''")
            quote = text[i:i + 3] if triple else ch
            start_line = line
            out.append(quote)
            i += len(quote)
            body_start = i
            while i < n:
                if text[i] == "\\" and not raw and i + 1 < n:
                    if text[i + 1] == "\n":
                        # Length-preserving blank, but the newline the tracked `line`
                        # counter depends on must survive too, or every finding after
                        # a continued string literal is reported one line early.
                        out.append(" \n")
                        line += 1
                    else:
                        out.append("  ")
                    i += 2
                    continue
                if text.startswith(quote, i):
                    break
                if text[i] == "\n":
                    if not triple:
                        # Unterminated single-line string: Godot will not parse this file
                        # at all. Stop at the newline rather than swallowing the rest of
                        # the file into the literal.
                        break
                    out.append("\n")
                    line += 1
                else:
                    out.append(" ")
                i += 1
            strings.append((start_line, text[body_start:i], quote))
            if text.startswith(quote, i):
                out.append(quote)
                i += len(quote)
            continue
        out.append(ch)
        i += 1
    return "".join(out), strings


# Every `_T.assert_*(` call site. This is the "asserts N things" number, and it is
# counted the same way run_tests.gd counts it (`line.contains("_T.assert")`), so the
# denominator this prints is the runner's own.
RE_ASSERT = re.compile(r"\b_T\s*\.\s*assert\w*\s*\(")

# A load/preload of a scene, matched in blanked code; the path comes back off the
# preserved string list by line.
RE_SCENE_LOAD = re.compile(r"\b(preload|load)\s*\(\s*[\"']")
# A directory sweep that filters on ".tscn" and then loads what it finds (gh#21 /
# plant-tower-defense:G-029) -- not a literal, so RE_SCENE_LOAD can't see it, but it
# is stronger evidence than any literal: it can't go stale and covers scenes added
# after it was written.
RE_TSCN_SWEEP_SUFFIX = re.compile(r'''(?:ends_with|match)\(\s*["']\*?\.tscn["']''')


# ---------------------------------------------------------------------------
# The defect classes
#
# Each is a class of Godot failure this harness has been burned by, phrased as the
# question nothing else in the project asks. `strong` promotes a class to COVERED;
# `weak` never does -- it is printed under the UNCHECKED verdict so the reader can see
# what was nearly enough and why it is not.
# ---------------------------------------------------------------------------

def _tok(label, pattern):
    return (label, re.compile(pattern))


CLASSES = [
    {
        "id": "ui_layout",
        "question": "is a Control on screen, and is it bigger than 0x0",
        "absent": "nothing ever reads a Control's screen rect or size in a real viewport",
        "cheapest_cover": "python tools/devtools.py validate-ui",
        "strong": [
            _tok("_T.instantiate_ui(", r"\binstantiate_ui\s*\("),
            _tok("get_global_rect(", r"\bget_global_rect\s*\("),
            _tok("get_rect(", r"\bget_rect\s*\("),
            _tok("get_viewport_rect(", r"\bget_viewport_rect\s*\("),
            _tok("get_screen_position(", r"\bget_screen_position\s*\("),
            _tok("get_combined_minimum_size(", r"\bget_combined_minimum_size\s*\("),
        ],
        "weak": [
            # A Control that never entered a tree reports 0x0 for size and position, so
            # asserting them outside instantiate_ui asserts the failure mode itself.
            (_tok(".size", r"\.size\b(?!\s*\()"),
             "a Control outside a tree reports 0x0, so a size assertion with no "
             "instantiate_ui/viewport can pass over an unlaid-out node"),
            (_tok(".position", r"\.position\b"),
             "same: position is (0, 0) until something anchors it in a viewport"),
            (_tok(".global_position", r"\.global_position\b"),
             "same: global_position needs a real viewport to mean anything"),
        ],
        "verbs": ["validate_ui", "get_ui_snapshot", "get_node_bounds", "ui_snapshot_diff",
                  "save_ui_baseline", "canvas_scale"],
        "gate": None,
    },
    {
        "id": "ui_reachable",
        "question": "can a finger or cursor actually hit this Control, or is it blocked",
        "absent": "nothing asks whether a Control can actually be hit",
        "cheapest_cover": "python tools/devtools.py reachable-ui",
        "strong": [
            _tok("reachable_ui", r"\breachable_ui\b"),
            _tok("mouse_filter", r"\bmouse_filter\b"),
            _tok("MOUSE_FILTER_*", r"\bMOUSE_FILTER_\w+"),
        ],
        "weak": [
            (_tok("_gui_input", r"\b_gui_input\b"),
             "defining or naming a handler is not asking whether input can reach it"),
            (_tok("has_point(", r"\bhas_point\s*\("),
             "a rect containing a point says nothing about a later sibling covering it"),
            (_tok(".visible", r"\.visible\b"),
             "visible is not reachable - the panel this class exists for was visible, "
             "correctly laid out, and had a full-rect MOUSE_FILTER_STOP overlay on it"),
            (_tok("is_visible_in_tree(", r"\bis_visible_in_tree\s*\("),
             "same: visibility is one of four conditions reachability needs"),
        ],
        "verbs": ["reachable_ui"],
        "gate": None,
    },
    {
        "id": "signal_unconnected",
        "question": "is this declared signal actually connected to anything",
        "absent": "nothing asks whether a declared signal has a listener",
        "cheapest_cover": 'assert it: _T.assert_true(btn.pressed.is_connected(cb), "wired")'
                          "   |   or drive the real button: python tools/devtools.py "
                          "press <node_path>",
        "strong": [
            _tok("is_connected(", r"\bis_connected\s*\("),
            _tok("get_signal_connection_list(", r"\bget_signal_connection_list\s*\("),
            _tok("get_incoming_connections(", r"\bget_incoming_connections\s*\("),
        ],
        "weak": [
            (_tok("has_signal(", r"\bhas_signal\s*\("),
             "has_signal asks whether the signal was DECLARED; this class is about "
             "whether anything listens to it"),
            (_tok("connect(", r"\bconnect\s*\("),
             "connecting a signal in a fixture is setup; the check is whether the GAME "
             "connected one"),
            (_tok("emit_signal(", r"\bemit_signal\s*\("),
             "emitting proves the sender fires, not that anything is listening"),
            (_tok(".emit(", r"\.emit\s*\("),
             "same: a signal with no listener emits perfectly happily"),
        ],
        # `press` emits `pressed` on the real BaseButton rather than calling the handler,
        # which is exactly the mis-wired `pressed.connect` case (see _cmd_press).
        "verbs": ["press"],
        "gate": None,
    },
    {
        "id": "orphan_growth",
        "question": "does node count grow run over run (a leak)",
        "absent": "nothing counts orphan nodes, so a leak grows silently",
        "cheapest_cover": "python tools/devtools.py performance   (twice, comparing orphans)",
        "strong": [
            _tok("ORPHAN_NODE_COUNT", r"\bORPHAN_NODE_COUNT\b"),
            _tok("get_orphan_node_count(", r"\bget_orphan_node_count\s*\("),
        ],
        "weak": [
            # Deliberately not strong on its own: get_monitor reads whichever counter it
            # was passed, and FPS or draw calls say nothing about leaked nodes. The
            # ORPHAN_NODE_COUNT token above is what fires on the real thing, and it fires
            # on the same line.
            (_tok("Performance.get_monitor(", r"\bPerformance\s*\.\s*get_monitor\s*\("),
             "get_monitor with no ORPHAN_NODE_COUNT nearby is measuring some other "
             "counter"),
            (_tok("_T.free_ui(", r"\bfree_ui\s*\("),
             "tearing down cleanly is not the same as measuring that nothing leaked"),
            (_tok("queue_free(", r"\bqueue_free\s*\("),
             "same: freeing is the fix, counting orphans is the check"),
        ],
        "verbs": ["performance"],
        "gate": None,
        # verify_ledger rows carry runtime.orphan_growth as a number: structured
        # evidence rather than a keyword match on free-text check names.
        "ledger_runtime_key": "orphan_growth",
    },
    {
        "id": "input_path",
        "question": "does an input action actually drive the game",
        "absent": "nothing sends an input event, so a dead binding looks identical to a "
                  "live one",
        # Deliberately not "run smoke.json": the shipped seed sequence is waits and
        # screenshots, so pointing at it would recommend the very file this tool reports
        # as weak evidence.
        "cheapest_cover": "python tools/devtools.py input tap <action>   |   or add a "
                          "press/tap step to a test/sequences/*.json",
        # Every strong token here is a *delivery*, not a mention. `event as InputEventKey`
        # inside a helper that walks InputMap bindings is introspection of the keymap and
        # sends nothing -- it was matched as strong on the first draft and reported a real
        # project's input_path COVERED off a line that drives no input at all. `.new(` is
        # what separates constructing an event to send from casting one to read.
        "strong": [
            _tok("parse_input_event(", r"\bparse_input_event\s*\("),
            _tok("Input.action_press/release(",
                 r"\bInput\s*\.\s*action_(?:press|release)\s*\("),
            _tok("InputEvent*.new(", r"\bInputEvent\w*\s*\.\s*new\s*\("),
            _tok("direct _input()/_unhandled_input() call",
                 r"\.\s*_(?:unhandled_|unhandled_key_|shortcut_)?input\s*\("),
        ],
        "weak": [
            (_tok("InputEvent* (mentioned, not constructed)", r"\bInputEvent\w*\b"),
             "naming or casting to an event type is reading the keymap; the check is "
             "whether an event is ever delivered"),
            (_tok("Input.", r"\bInput\s*\."),
             "reading input state is not driving it; a binding nothing sends is untested"),
            (_tok("InputMap.", r"\bInputMap\s*\."),
             "an action existing in the map says nothing about it reaching the game"),
        ],
        "verbs": ["input_press", "input_release", "input_tap", "input_key",
                  "input_sequence", "touch_press", "touch_drag"],
        "gate": None,
    },
    {
        "id": "scene_validation",
        "question": "do the project's scenes load and validate",
        "absent": "nothing loads a res:// scene, so a broken .tscn is only found by "
                  "running the game",
        "cheapest_cover": "python tools/devtools.py validate-all",
        # Matched by two dedicated passes, not by a bare token: a res:// .tscn literal
        # inside load/preload (_scan_scene_loads), or a directory sweep that filters on
        # .tscn and loads what it finds (_scan_scene_sweeps, gh#21).
        "strong": [],
        "weak": [
            (_tok("PackedScene", r"\bPackedScene\b"),
             "a PackedScene built in code (as the shipped example test does) never "
             "touches a .tscn on disk"),
            (_tok(".instantiate(", r"\.instantiate\s*\("),
             "instantiating something says nothing about which scenes on disk load"),
        ],
        "verbs": ["validate_scene", "validate_all"],
        "gate": None,
    },
    {
        "id": "shader_compile",
        "question": "does every .gdshader and embedded Shader compile",
        "absent": "no shader gate is installed, so a broken shader reaches the game",
        "cheapest_cover": "godot --headless --path . --script res://tools/lint_project.gd",
        "strong": [],
        "weak": [],
        "verbs": [],
        "gate": {"path": "tools/lint_project.gd",
                 "note": "compiles every .gdshader and every Shader embedded in a .tres",
                 "denominator_suffix": ".gdshader"},
    },
    {
        "id": "name_resolution",
        "question": "does every identifier, autoload and preload path resolve",
        "absent": "no name gate is installed, so an undeclared identifier is only found "
                  "at runtime",
        "cheapest_cover": "python tools/name_check.py",
        "strong": [],
        "weak": [],
        "verbs": [],
        "gate": {"path": "tools/name_check.py",
                 "note": "resolves every type, class_name, autoload and preload path",
                 "denominator_suffix": ".gd"},
    },
]

CLASS_IDS = [c["id"] for c in CLASSES]

# Bus actions -> the classes they are evidence for, inverted from the table above once.
VERB_CLASSES = {}
for _c in CLASSES:
    for _v in _c["verbs"]:
        VERB_CLASSES.setdefault(_v, []).append(_c["id"])

# Input sequence step types that actually drive input. A sequence of waits and
# screenshots (which is what the shipped smoke.json is) drives none.
INPUT_STEP_TYPES = {"press", "release", "tap", "hold"}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def evidence(source, location, token, note=None):
    row = {"source": source, "location": location, "token": token}
    if note:
        row["note"] = note
    return row


class Result:
    """One class's verdict plus everything that argued for it."""

    def __init__(self, spec):
        self.spec = spec
        self.id = spec["id"]
        self.strong = []
        self.weak = []

    @property
    def status(self):
        if not self.strong:
            return STATUS_UNCHECKED
        sources = {e["source"] for e in self.strong}
        if sources <= {"gate"}:
            return STATUS_BY_GATE
        if sources <= {"gate", "ledger", "devtools_log"}:
            return STATUS_BY_SESSION
        return STATUS_COVERED


# --- 1. the project's test dir ---------------------------------------------

def scan_test_files(test_dir, root):
    """Every `test_*.gd` under the test dir, scanned once. Returns [(rel, SourceText)]."""
    out = []
    if not test_dir or not test_dir.is_dir():
        return out
    for path in sorted(test_dir.rglob("test_*.gd")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        out.append((rel, SourceText(text)))
    return out


def count_assertions(files):
    """`_T.assert_*(` call sites across the suite -- the 'asserts N things' number."""
    total = 0
    for _rel, src in files:
        total += len(RE_ASSERT.findall(src.code))
    return total


def _scan_scene_loads(rel, src):
    """`load("res://x.tscn")` / `preload(...)` -- the only strong scene_validation token.

    A bare `PackedScene` is deliberately not enough: the harness's own example test packs
    one in code, and crediting that would report "your scenes load" on a project whose
    scenes were never opened.
    """
    by_line = {}
    for sline, value, _q in src.strings:
        by_line.setdefault(sline, []).append(value)
    hits = []
    for m in RE_SCENE_LOAD.finditer(src.code):
        line = src.line_of(m.start())
        for value in by_line.get(line, []):
            if value.startswith("res://") and value.lower().endswith(".tscn"):
                hits.append(evidence("test", "%s:%d" % (rel, line),
                                     '%s("%s")' % (m.group(1), value)))
                break
    return hits


def _scan_scene_sweeps(rel, src):
    """A filesystem walk that filters on `.tscn` and then loads what it finds.

    gh#21 / plant-tower-defense:G-029: `_scan_scene_loads` only matches a `res://...tscn`
    string literal, so a project that walks `res://` at runtime and loads every scene it
    finds -- strictly stronger than any hardcoded literal, since it cannot go stale and
    covers scenes added after it was written -- scored UNCHECKED. Credited as its own
    strong hit rather than folded into the literal scan, so the two look different in
    the `--fmt json` evidence even though both count as COVERED.
    """
    m = RE_TSCN_SWEEP_SUFFIX.search(src.code)
    if not m:
        return []
    if not re.search(r"\b(?:ResourceLoader\s*\.\s*)?load\s*\(", src.code):
        return []
    line = src.line_of(m.start())
    return [evidence("test", "%s:%d" % (rel, line),
                      "sweeps res:// for .tscn and loads what it finds")]


# The methods the harness itself ships in test/unit/test_selftest.gd (and, on
# installs predating 0.19.0, test_example.gd). They are a working example of the
# assertion API, not a statement about this project.
_SEED_METHODS = frozenset((
    "test_arithmetic_sanity",
    "test_boolean_sanity",
    "test_ui_layout_resolves_headlessly",
    "_build_hud_scene",
))
RE_FUNC = re.compile(r"^\s*(?:static\s+)?func\s+([A-Za-z_]\w*)", re.M)


def _enclosing_func(src, line):
    """Name of the func a 1-based line sits in, or "" above the first one."""
    name = ""
    for m in RE_FUNC.finditer(src.code):
        if src.line_of(m.start()) <= line:
            name = m.group(1)
        else:
            break
    return name


def _is_seed_evidence(src, line):
    """Is this hit inside a method the harness shipped, in the seeded test file?

    The seed calls `_T.instantiate_ui()` and asserts `ui.size` on a HUD it builds
    in code, purely to demonstrate the headless UI helpers. Counting that as
    ui_layout coverage marks EVERY freshly scaffolded project covered on day one
    for a check about a two-node scene the project does not own -- caught on two
    real projects (`moving-in`, `findmyballs`), both crediting
    `test_example.gd:42`. Same reasoning that keeps a bare `PackedScene` weak.

    Keyed on the enclosing method, not the file, so a project that ADDS real
    tests to the seeded file still gets full credit for those.
    """
    return _enclosing_func(src, line) in _SEED_METHODS


def collect_test_evidence(results, files):
    for spec in CLASSES:
        res = results[spec["id"]]
        for rel, src in files:
            for label, pattern in spec["strong"]:
                # finditer, not search: the seeded example's call is usually the
                # FIRST occurrence in its file, so stopping at the first match
                # would demote a project's own real check sitting below it.
                seeded = None
                for m in pattern.finditer(src.code):
                    line = src.line_of(m.start())
                    if _is_seed_evidence(src, line):
                        if seeded is None:
                            seeded = evidence(
                                "test", "%s:%d" % (rel, line), label,
                                "this is the example the harness seeds, not a "
                                "check about your project - assert it against "
                                "your own UI to make it count")
                        continue
                    res.strong.append(evidence(
                        "test", "%s:%d" % (rel, line), label,
                        src.line_text(line)))
                    seeded = None
                    break
                if seeded is not None:
                    res.weak.append(seeded)
            for (label, pattern), why in spec["weak"]:
                m = pattern.search(src.code)
                if m:
                    line = src.line_of(m.start())
                    res.weak.append(evidence(
                        "test", "%s:%d" % (rel, line), label, why))
            if spec["id"] == "scene_validation":
                res.strong.extend(_scan_scene_loads(rel, src))
                res.strong.extend(_scan_scene_sweeps(rel, src))


# --- 2. input sequences -----------------------------------------------------

def find_sequence_dirs(test_dir, root):
    """`sequences/` beside or inside the test dir, whichever exists."""
    out = []
    for candidate in (test_dir / "sequences" if test_dir else None,
                      test_dir.parent / "sequences" if test_dir else None,
                      root / "test" / "sequences"):
        if candidate is not None and candidate.is_dir() and candidate not in out:
            out.append(candidate)
    return out


def collect_sequence_evidence(results, dirs, root):
    """A sequence file counts only when it contains a step that drives input.

    The shipped `smoke.json` is waits and screenshots; treating its presence as input
    coverage would credit every scaffolded project on day one for a file that sends
    nothing.
    """
    res = results["input_path"]
    seen = []
    for directory in dirs:
        for path in sorted(directory.glob("*.json")):
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            seen.append(rel)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                res.weak.append(evidence("sequences", rel, "unreadable json",
                                         "could not be parsed, so it proves nothing"))
                continue
            steps = data.get("steps") if isinstance(data, dict) else None
            types = [str(s.get("type", "")) for s in steps or []
                     if isinstance(s, dict)]
            driving = sorted({t for t in types if t in INPUT_STEP_TYPES})
            if driving:
                res.strong.append(evidence(
                    "sequences", rel, "step type(s): %s" % ", ".join(driving),
                    "%d step(s) total" % len(types)))
            else:
                res.weak.append(evidence(
                    "sequences", rel, "no press/release/tap/hold step",
                    "a sequence of waits and screenshots drives no input"))
    return seen


# --- 3. what past sessions actually ran -------------------------------------

def collect_ledger_evidence(results, ledger_path, root):
    """`.devtools/verify-runs.jsonl` -- schema per verify_ledger.py, not per guess.

    Only `runtime.<key>` is taken as strong: it is a number the ledger computes. `checks`
    entries are free-text names a run writes about itself, so a keyword hit there is weak
    by construction.
    """
    if not ledger_path.is_file():
        return {"present": False, "path": _rel(ledger_path, root), "rows": 0}
    rows = 0
    bad = 0
    try:
        lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"present": False, "path": _rel(ledger_path, root), "rows": 0,
                "error": str(exc)}
    rel = _rel(ledger_path, root)
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict):
            bad += 1
            continue
        rows += 1
        runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
        ts = str(row.get("ts") or "?")
        for spec in CLASSES:
            key = spec.get("ledger_runtime_key")
            if key and isinstance(runtime.get(key), (int, float)):
                results[spec["id"]].strong.append(evidence(
                    "ledger", "%s:%d" % (rel, n),
                    "runtime.%s = %s" % (key, runtime[key]),
                    "recorded by a /verify run at %s" % ts))
        for check in row.get("checks") or []:
            if not isinstance(check, dict):
                continue
            name = str(check.get("name") or "")
            low = name.lower().replace("-", "_")
            for verb, ids in VERB_CLASSES.items():
                if verb in low:
                    for cid in ids:
                        results[cid].weak.append(evidence(
                            "ledger", "%s:%d" % (rel, n),
                            'check name mentions "%s"' % verb,
                            "a check name is prose a run writes about itself, not a "
                            "record that the verb ran"))
    return {"present": True, "path": rel, "rows": rows, "unparseable": bad}


def find_devtools_logs(root):
    """`devtools_log*.jsonl` anywhere the bus could have put one inside the project.

    The bridge writes to `user://` by default, which is outside the project entirely, so
    finding nothing here is the ordinary case and is reported as ABSENT rather than as a
    clean result.
    """
    found = []
    for directory in (root, root / ".devtools"):
        if directory.is_dir():
            found.extend(sorted(directory.glob("devtools_log*.jsonl")))
    return found


RE_EXECUTING = re.compile(r"^Executing:\s*(\w+)")


def _stamp(ts):
    """dev_tools.gd logs a unix float; print it as UTC so a reader can date the run."""
    if isinstance(ts, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(
                ts, datetime.timezone.utc).replace(microsecond=0).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(ts)
    return str(ts)


def collect_log_evidence(results, log_paths, root):
    """Bridge verbs a session actually called.

    dev_tools.gd writes one `{"category": "command", "message": "Executing: <action>"}`
    line per handled command, so the action name is structured data rather than a guess
    at what a session did.
    """
    if not log_paths:
        return {"present": False, "path": "devtools_log*.jsonl", "commands": 0}
    seen = {}
    total = 0
    for path in log_paths:
        rel = _rel(path, root)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("category") != "command":
                continue
            total += 1
            m = RE_EXECUTING.match(str(row.get("message") or ""))
            if not m:
                continue
            action = m.group(1)
            if action in VERB_CLASSES and action not in seen:
                seen[action] = ("%s:%d" % (rel, n), row.get("timestamp"))
    for action, (location, ts) in sorted(seen.items()):
        for cid in VERB_CLASSES[action]:
            results[cid].strong.append(evidence(
                "devtools_log", location, "%s (bridge verb)" % action,
                "a past session called it%s; a session is an observation, not a "
                "standing check" % (" at %s" % _stamp(ts) if ts else "")))
    return {"present": True,
            "path": ", ".join(_rel(p, root) for p in log_paths),
            "commands": total, "verbs": sorted(seen)}


# --- 4. gates that always run ----------------------------------------------

def collect_gate_evidence(results, root, scan_root):
    installed = []
    for spec in CLASSES:
        gate = spec.get("gate")
        if not gate:
            continue
        path = root / gate["path"]
        res = results[spec["id"]]
        if not path.is_file():
            res.weak.append(evidence(
                "gate", gate["path"], "not installed",
                "this class is covered unconditionally only while the gate is present"))
            continue
        installed.append(gate["path"])
        suffix = gate.get("denominator_suffix")
        note = gate["note"]
        if suffix:
            count = _count_files(scan_root, suffix)
            # lint_project.gd's own rule: always name the denominator. "Shaders: OK" over
            # a project the pass found no shader in is the report lying about what it
            # looked at.
            note += " (%d %s file(s) under %s)" % (
                count, suffix, _rel(scan_root, root) or ".")
            if count == 0:
                note += " - the gate runs, but there is nothing here for it to check"
        res.strong.append(evidence("gate", gate["path"], "always run", note))
    return installed


def _count_files(root, suffix):
    if not root.is_dir():
        return 0
    n = 0
    for path in root.rglob("*%s" % suffix):
        parts = path.relative_to(root).parts
        if any(p.startswith(".") for p in parts):
            continue
        n += 1
    return n


# ---------------------------------------------------------------------------
# Project plumbing
# ---------------------------------------------------------------------------

def _rel(path, root):
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _res_to_path(root, value, default):
    """`res://test/unit` -> <root>/test/unit. `""` means the project declares it has none."""
    raw = value if isinstance(value, str) else default
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("res://"):
        raw = raw[len("res://"):]
    raw = raw.strip("/")
    return root / raw if raw else root


def read_config(path):
    """The config as a dict. Returns (dict, error). A missing file is not an error --
    sane defaults are the documented behavior; an unreadable one is exit 2."""
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "top level is not an object"
    return data, None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

STATUS_W = 18
ID_W = 18
EVIDENCE_SHOWN = 4      # per class, in the text report; --json carries all of it
WEAK_SHOWN = 3


def report(results, order, stats, sources, args):
    print("coverage_check %s - which defect classes this project's checks ask about"
          % HARNESS_VERSION)
    print("never opens the project: no godot, no --import, no .godot/ write")
    print("project: %s" % stats["project"])
    print("")
    print("This project asserts %d thing(s) across %d test file(s)."
          % (stats["assertions"], stats["test_files"]))
    print("")
    print("evidence sources:")
    for line in sources:
        print("  %s" % line)
    print("")

    unchecked = [r for r in order if r.status == STATUS_UNCHECKED]
    total = len(order)
    scope = ("defect class(es) the harness knows about" if total == len(CLASSES)
             else "of the %d class(es) you asked for" % total)
    if unchecked:
        print("%d %s %s never exercised:"
              % (len(unchecked),
                 "of %d %s" % (total, scope) if total == len(CLASSES) else scope,
                 "is" if len(unchecked) == 1 else "are"))
    else:
        print("Every %s is exercised by something. Read the evidence below before "
              "believing it." % ("one of the %d defect classes the harness knows about"
                                 % total if total == len(CLASSES)
                                 else "class you asked for"))
    print("")

    for res in order:
        label = STATUS_LABEL[res.status]
        spec = res.spec
        if res.status == STATUS_UNCHECKED:
            print("  %-*s %-*s %s" % (STATUS_W, label, ID_W, res.id, spec["absent"]))
            print("  %-*s %-*s %s" % (STATUS_W, "", ID_W, "cheapest cover:",
                                      spec["cheapest_cover"]))
            for w in res.weak[:WEAK_SHOWN]:
                print("  %-*s %-*s %s  %s" % (STATUS_W, "", ID_W, "weak signal:",
                                              w["location"], w["token"]))
                if w.get("note"):
                    print("  %-*s %-*s   -> %s" % (STATUS_W, "", ID_W, "", w["note"]))
            hidden = len(res.weak) - WEAK_SHOWN
            if hidden > 0:
                print("  %-*s %-*s %d more weak signal(s) (see --json)"
                      % (STATUS_W, "", ID_W, "", hidden))
        else:
            first = res.strong[0]
            print("  %-*s %-*s %s  %s" % (STATUS_W, label, ID_W, res.id,
                                          first["location"], first["token"]))
            if first.get("note"):
                print("  %-*s %-*s   -> %s" % (STATUS_W, "", ID_W, "", first["note"]))
            # Extras are capped: the first line carries the verdict's evidence, and a
            # class matched by six tokens in twenty files must not push the UNCHECKED
            # list off the top of the terminal. --json carries every one.
            for extra in res.strong[1:EVIDENCE_SHOWN]:
                print("  %-*s %-*s %s  %s" % (STATUS_W, "", ID_W, "also:",
                                              extra["location"], extra["token"]))
            hidden = len(res.strong) - EVIDENCE_SHOWN
            if hidden > 0:
                print("  %-*s %-*s %d more (see --json)" % (STATUS_W, "", ID_W, "",
                                                            hidden))
        print("")

    print("classes: %d covered, %d by gate, %d by session, %d UNCHECKED"
          % (stats["counts"][STATUS_COVERED], stats["counts"][STATUS_BY_GATE],
             stats["counts"][STATUS_BY_SESSION], stats["counts"][STATUS_UNCHECKED]))
    if stats["counts"][STATUS_BY_SESSION]:
        print("NOTE: a `COVERED (session)` class was asked once by a past run, not by a "
              "check that runs again. Write it into a test if you want it to stay true.")
    print("NOT COVERED: %s" % NOT_COVERED)


def build_sources(stats):
    lines = []
    td = stats["test_dir"]
    lines.append("%-20s %s" % ("test dir",
                               "%s (%d file(s), %d assertion(s))"
                               % (td, stats["test_files"], stats["assertions"])
                               if td else "ABSENT - the project declares no test_dir"))
    seqs = stats["sequence_files"]
    lines.append("%-20s %s" % ("input sequences",
                               ", ".join(seqs) if seqs
                               else "ABSENT - no sequences/*.json found"))
    ledger = stats["ledger"]
    lines.append("%-20s %s" % ("verify ledger",
                               "%s (%d run(s))" % (ledger["path"], ledger["rows"])
                               if ledger["present"]
                               else "ABSENT (%s) - no session evidence, which is not "
                                    "the same as no coverage" % ledger["path"]))
    log = stats["devtools_log"]
    lines.append("%-20s %s" % ("devtools log",
                               "%s (%d command(s))" % (log["path"], log["commands"])
                               if log["present"]
                               else "ABSENT (%s; the bridge writes to user:// by "
                                    "default) - no session evidence" % log["path"]))
    lines.append("%-20s %s" % ("installed gates",
                               ", ".join(stats["gates"]) if stats["gates"]
                               else "NONE FOUND - gate-covered classes are not covered"))
    return lines


def to_json(order, stats, sources, exit_code):
    return {
        "tool": "coverage_check",
        "harness_version": HARNESS_VERSION,
        "project": stats["project"],
        "assertions": stats["assertions"],
        "test_files": stats["test_files"],
        "sources": {
            "test_dir": stats["test_dir"],
            "sequence_files": stats["sequence_files"],
            "ledger": stats["ledger"],
            "devtools_log": stats["devtools_log"],
            "gates_installed": stats["gates"],
        },
        "counts": stats["counts"],
        "classes": [
            {
                "id": r.id,
                "status": r.status,
                "question": r.spec["question"],
                "evidence": r.strong,
                "weak_evidence": r.weak,
                "cheapest_cover": r.spec["cheapest_cover"],
            }
            for r in order
        ],
        "not_covered": NOT_COVERED,
        "exit": exit_code,
    }


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Report which defect classes this project's checks ask about.\n"
                    "\n"
                    "It reports on the CHECKS, not on their results: a hand-rolled suite\n"
                    "cannot notice what it forgot to check, and that hole is invisible in\n"
                    "any pass/fail summary.\n"
                    "\n"
                    "NEVER OPENS THE PROJECT: no godot invocation, no --import, no write\n"
                    "to .godot/. Safe to run with N agents on one checkout, and works in a\n"
                    "worktree that has never been imported.",
        epilog="Exit codes: 0 reported (always, by default - this is advisory), 1 under\n"
               "--strict with at least one UNCHECKED class, 2 could not run.\n"
               "\n"
               "A COVERED class means some check asks the question, not that the answer\n"
               "was right. Weak evidence never promotes a class; it is printed under the\n"
               "UNCHECKED verdict so you can see what was nearly enough.")
    parser.add_argument("--project", "-p", default=".", help="Path to the Godot project")
    parser.add_argument("--test-dir", help="Override the configured test_dir "
                                           "(res:// or filesystem path)")
    parser.add_argument("--config", help="Path to devtools_config.json "
                                         "(default: addons/godot_selftest/)")
    parser.add_argument("--only", action="append", default=[], metavar="ID",
                        help="Report only this class (repeatable). One of: %s"
                             % ", ".join(CLASS_IDS))
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any reported class is UNCHECKED")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args()

    root = Path(args.project).expanduser().resolve()
    if not (root / "project.godot").is_file():
        print("Error: no project.godot in %s. Run from the target project root or pass "
              "--project/-p. (This tool runs against a scaffolded game, not against the "
              "harness repo.)" % root, file=sys.stderr)
        return EXIT_CANNOT_RUN

    unknown = [o for o in args.only if o not in CLASS_IDS]
    if unknown:
        print("Error: unknown class id(s) %s. Known: %s"
              % (", ".join(unknown), ", ".join(CLASS_IDS)), file=sys.stderr)
        return EXIT_CANNOT_RUN

    config_path = Path(args.config).expanduser() if args.config else root / CONFIG_REL
    config, err = read_config(config_path)
    if err is not None:
        print("Error: could not read %s (%s). Fix it or pass --config."
              % (config_path, err), file=sys.stderr)
        return EXIT_CANNOT_RUN

    test_dir = _res_to_path(root, args.test_dir or config.get("test_dir"),
                            DEFAULT_TEST_DIR)
    scan_root = _res_to_path(root, config.get("scan_root"), DEFAULT_SCAN_ROOT) or root

    results = {spec["id"]: Result(spec) for spec in CLASSES}

    files = scan_test_files(test_dir, root) if test_dir else []
    collect_test_evidence(results, files)
    seq_dirs = find_sequence_dirs(test_dir, root) if test_dir else []
    sequence_files = collect_sequence_evidence(results, seq_dirs, root)
    ledger = collect_ledger_evidence(results, root / LEDGER_REL, root)
    log = collect_log_evidence(results, find_devtools_logs(root), root)
    gates = collect_gate_evidence(results, root, scan_root)

    order = [results[cid] for cid in CLASS_IDS
             if not args.only or cid in args.only]
    # UNCHECKED first: the whole product of this tool is the list of questions nobody
    # asks, and burying it under the good news is how it stops being read.
    order.sort(key=lambda r: (0 if r.status == STATUS_UNCHECKED else 1,
                              CLASS_IDS.index(r.id)))

    counts = {STATUS_COVERED: 0, STATUS_BY_GATE: 0, STATUS_BY_SESSION: 0,
              STATUS_UNCHECKED: 0}
    for r in order:
        counts[r.status] += 1

    stats = {
        "project": str(root),
        "assertions": count_assertions(files),
        "test_files": len(files),
        "test_dir": _rel(test_dir, root) if test_dir else None,
        "sequence_files": sequence_files,
        "ledger": ledger,
        "devtools_log": log,
        "gates": gates,
        "counts": counts,
    }

    exit_code = EXIT_FINDINGS if (args.strict and counts[STATUS_UNCHECKED]) else EXIT_OK

    sources = build_sources(stats)
    if args.json:
        print(json.dumps(to_json(order, stats, sources, exit_code), indent=2))
    else:
        report(results, order, stats, sources, args)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
