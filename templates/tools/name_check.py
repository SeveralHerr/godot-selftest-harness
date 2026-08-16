#!/usr/bin/env python3
"""name_check.py - resolve every name a GDScript file mentions, without launching Godot.

Why this exists: `.godot/` is a single-writer resource, and every gate in this harness
goes through it. `--import` rewrites the import cache; `lint_project.gd` and
`run_tests.gd` open the project and take the same locks. So N agents working the same
checkout cannot validate concurrently -- the honest policy has been "agents never run
Godot", which serialises the slowest part of the work behind one agent. A fresh git
worktree is the same problem in its worst form: it has never been imported, so the first
lint run reports 1108 `SCRIPT ERROR: Identifier "Types" not declared` lines and still
exits 0, and the test runner prints [PASS] for tests whose first statement errored.

Almost every one of those 1108 lines is a *name* that did not resolve. Names are decidable
from source text plus a table of what the engine provides -- no project needs to be opened
to answer "does `class_name BoneWorker` exist" or "is `Vecor2` a type". This tool answers
exactly that class of question and nothing else, from:

  1. the `.gd` files themselves (class_name / inner class / const / enum / func / signal
     declarations, and the `extends` graph that puts a base class's members in scope),
  2. `project.godot`'s `[autoload]` section,
  3. an *engine API index* distilled from `godot --dump-extension-api`.

The third one is the trick that makes this safe under parallelism. `--dump-extension-api`
runs in an empty temp directory with no project at all -- it never opens `.godot/`, never
takes a project lock, and produces the same bytes for a given engine build. So it is
dumped **once per engine version per machine**, cached in a user-level directory that
every clone and worktree shares, and from then on this tool launches nothing:

    python tools/name_check.py --refresh-api     # once, ~6s, touches no project
    python tools/name_check.py                   # every run after that, no engine

What this is NOT: a compiler. It does not type-check, does not evaluate, and cannot see a
name built at runtime. It resolves identifiers in type positions, `res://` paths in
preload/load/extends, and the method/signal names inside string literals. Anything it
cannot decide it reports as advisory or not at all -- a static checker that guesses is
worse than no checker, because a project that learns to ignore its output has lost the
signal it was installed for.

**A clean run means the names resolve. It does not mean the file compiles**
(`moving-in:G-009`). The clearest example is inferred typing: `var x := node.get_thing()`
is a hard parse error when `node` is only known as a `Node` -- `Cannot infer the type of
"x" variable because the value doesn't have a set type` -- and every name in that line
resolves, so this tool reports `errors: 0 | warnings: 0` on a file the engine refuses to
load. Deciding it needs method resolution order and return types, i.e. opening the
project, which is the one thing this tool exists not to do. It is deliberately left to
`import_check.py` and `lint_project.gd`, and this matters most where it is least visible:
this is the only gate safe to run concurrently, so it is the only gate a fan-out agent on
a shared checkout gets, and two such agents have already shipped code that did not compile
behind a clean name_check. Treat clean here as "cleared to integrate", never as "builds".

Exit codes follow the harness convention (`lint_project.gd`, `run_tests.gd`,
`import_check.py`):
    0  scanned clean (no errors; no warnings either, under --strict)
    1  findings that count -- errors, plus warnings under --strict
    2  could not run: no project.godot, an unreadable config or baseline, a missing
       engine index under --require-api, or a --refresh-api that produced nothing

Usage:
    python tools/name_check.py                     # check the project in .
    python tools/name_check.py -p ../other-game
    python tools/name_check.py --refresh-api       # dump/cache the engine index, then check
    python tools/name_check.py --only player/      # report only findings under a prefix
    python tools/name_check.py --json              # machine-readable verdict
    python tools/name_check.py --strict            # warnings count towards the exit code
    python tools/name_check.py --baseline-write lint_names.json   # adopt on a live project
    python tools/name_check.py --baseline lint_names.json         # only NEW findings gate

(`python` on Windows -- `python3` there is a Microsoft Store alias stub that satisfies
`command -v` and then refuses to run.)

Godot binary resolution (only ever used by --refresh-api) matches `import_check.py`:
    --godot flag  ->  $GODOT_BIN  ->  `godot_bin` in
    addons/godot_selftest/devtools_config.json
"""

import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

# harness-version: 0.24.0
HARNESS_VERSION = "0.24.0"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_CANNOT_RUN = 2

# Bump when the distilled index layout changes in a way that makes an old cache file
# unreadable. A cache miss is cheap (one ~6s dump); a silently misread index is not.
#
# The index holds only what the engine reported. The GDScript-level supplements below are
# unioned in at resolution time, never baked in -- otherwise adding one missing name to
# those lists would need every machine to re-dump before the fix took effect, and the
# stale caches would keep reporting the false positive the fix was for.
INDEX_VERSION = 3

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_ADVISORY = "advisory"

# ---------------------------------------------------------------------------
# Names GDScript provides that `--dump-extension-api` does not list.
#
# The dump describes what is exposed to GDExtension, which is ClassDB plus the builtin
# Variant types. GDScript adds a layer on top of that -- @GDScript annotations, the
# language's own constants, `super`/`self` -- and `global_constants` comes back empty in
# every 4.x build sampled so far even though PI and INF are certainly usable. These are
# spelled out rather than inferred, because a missing entry here is a permanent false
# ERROR in every project.
# ---------------------------------------------------------------------------

GDSCRIPT_GLOBAL_CONSTANTS = frozenset({
    "PI", "TAU", "INF", "NAN",
})

# Usable in a type position but not a class: `void` is a return type only, `Variant` is
# the untyped fallback, `Signal`/`Callable`/`StringName` etc. do come from the dump but
# are listed here too so the tool still resolves them when no index is loaded at all.
GDSCRIPT_TYPE_KEYWORDS = frozenset({
    "void", "Variant", "null",
})

# Identifiers the language binds implicitly inside every script body.
GDSCRIPT_IMPLICIT_NAMES = frozenset({
    "self", "super", "true", "false", "null",
})

# `@GlobalScope` utility functions are in the dump under utility_functions, but the
# GDScript-only ones are not callable through ClassDB and never appear there. This list
# is the difference measured against a real 4.6.1 dump, not a guess: every name here was
# checked to be absent from `utility_functions` and usable from GDScript. `Color8` is the
# one that bites -- PascalCase, so `unknown_global_ref` sees it, and it is the only
# capitalised global function in the language.
GDSCRIPT_ONLY_FUNCTIONS = frozenset({
    "Color8", "assert", "char", "dict_to_inst", "get_stack", "inst_to_dict",
    "is_instance_of", "len", "load", "ord", "preload", "print_debug",
    "print_stack", "range", "type_exists",
})

# ---------------------------------------------------------------------------
# Source scanning
# ---------------------------------------------------------------------------

# Godot skips a directory holding this file; so does every pass here, exactly as
# lint_project.gd does, so the two gates never disagree about what is in scope.
GDIGNORE = ".gdignore"


class SourceText:
    """A .gd file with comments and string bodies blanked out, positions preserved.

    Every pass below runs against `code`, in which each string literal's body and each
    comment has been replaced by spaces of the same length and newlines kept as newlines.
    That means a regex can never match inside a string or a comment -- the single most
    common way a text scan produces a confident finding about nothing -- while
    `line_of(pos)` still reports the real line number in the real file.

    The quote *delimiters* survive blanking. They are what `preload("res://…")` and
    `has_method("…")` anchor on, and blanking them too made both of those checks match
    nothing at all while still reporting a clean run.

    The string bodies themselves are not thrown away: `strings` keeps (line, value) for
    the literal checks (preload paths, has_method("x")), which need exactly the text the
    blanking removed.
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


def _blank_strings_and_comments(text):
    """Return (blanked_code, [(line, value, quote), ...]).

    Handles the full set of GDScript literal forms: ''' and \"\"\" blocks, ' and "
    strings, the r"" raw prefix (no escape processing) and the &"" / ^"" StringName and
    NodePath prefixes. Backslash escapes are honoured outside raw strings, so a literal
    containing a quote does not end the string early and leave the rest of the file
    parsed as code.
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
        if ch in "\"'" or (
            ch in "r&^" and i + 1 < n and text[i + 1] in "\"'"
        ):
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
                        # An unterminated single-line string. Godot will not parse this
                        # file at all; stop the literal at the newline rather than
                        # swallowing the rest of the file into it.
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


# --- declaration patterns (all applied to blanked code) ---------------------

RE_CLASS_NAME = re.compile(r"(?m)^[ \t]*class_name[ \t]+([A-Za-z_]\w*)")
RE_EXTENDS_TOP = re.compile(r"(?m)^extends[ \t]+(.+?)[ \t]*$")
RE_CLASS_NAME_EXTENDS = re.compile(
    r"(?m)^[ \t]*class_name[ \t]+[A-Za-z_]\w*[ \t]+extends[ \t]+(.+?)[ \t]*$")
RE_INNER_CLASS = re.compile(
    r"(?m)^[ \t]*class[ \t]+([A-Za-z_]\w*)[ \t]*(?:extends[ \t]+([\w.]+))?[ \t]*:")
RE_VAR = re.compile(
    r"(?m)^[ \t]*(?:@\w+(?:[ \t]*\([^)\n]*\))?[ \t]*)*"
    r"(?:static[ \t]+)?var[ \t]+([A-Za-z_]\w*)[ \t]*"
    r"(:[ \t]*[^=\n]+?)?[ \t]*(?::?=|:|$)")
# `:?=` matters: `const X := 0.3` is the inferred form and is everywhere in real code.
# Requiring a bare `=` made every inferred const invisible, which turned every reference
# to one into a confident "has no member" warning -- 466 of them on the first real
# project this ran against.
RE_CONST = re.compile(
    r"(?m)^[ \t]*const[ \t]+([A-Za-z_]\w*)[ \t]*"
    r"(:[ \t]*[^=\n]+?)?[ \t]*:?=[ \t]*(.*)$")
RE_FUNC = re.compile(r"(?m)^[ \t]*(?:static[ \t]+)?func[ \t]+([A-Za-z_]\w*)[ \t]*\(")
RE_SIGNAL = re.compile(r"(?m)^[ \t]*signal[ \t]+([A-Za-z_]\w*)[ \t]*(\()?")
RE_ENUM = re.compile(r"(?m)^[ \t]*enum[ \t]*([A-Za-z_]\w*)?[ \t]*\{")
RE_FOR = re.compile(r"(?m)^[ \t]*for[ \t]+([A-Za-z_]\w*)[ \t]*(:[ \t]*[^\n]+?)?[ \t]+in\b")
RE_LAMBDA = re.compile(r"func[ \t]*\(")
RE_AS_IS = re.compile(r"\b(?:as|is)[ \t]+([A-Z][\w.]*)")
RE_NEW_CALL = re.compile(r"\b([A-Z][\w.]*)[ \t]*\.[ \t]*new[ \t]*\(")
RE_ROOT_REF = re.compile(r"(?<![\w.$@\"'])([A-Z][A-Za-z0-9_]*)[ \t]*([.(])")
RE_ROOT_MEMBER = re.compile(r"[ \t]*\.[ \t]*([A-Za-z_]\w*)")
RE_PRELOAD = re.compile(r"\b(preload|load)[ \t]*\([ \t]*[\"']")
RE_CONST_PRELOAD = re.compile(r"^[ \t]*preload[ \t]*\(")
RE_IDENT_PATH = re.compile(r"^[A-Za-z_][\w.]*$")

# Every string-literal member reference lint_project.gd's `string_ref_unresolved` rule
# knows about, kept name-for-name so the two gates agree on what they flag.
RE_STRING_REF = re.compile(
    r"\b(has_method|has_signal|emit_signal|call|call_deferred|connect|"
    r"disconnect|is_connected)[ \t]*\([ \t]*([\"'])")


def _match_paren(code, open_pos):
    """Index just past the `)` closing the `(` at open_pos, or -1.

    Function signatures wrap across lines in real projects, and GDScript needs no
    continuation backslash inside parens. A line-oriented regex silently truncates those
    signatures and every parameter after the wrap goes unseen -- which reads as a clean
    file, not as a skipped one.
    """
    depth = 0
    i = open_pos
    n = len(code)
    while i < n:
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _split_top_level(text, sep=","):
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _type_names(text):
    """Every identifier path inside a type expression.

    `Array[Item]` yields Array and Item; `Dictionary[StringName, Types.Recipe]` yields
    all three. Splitting rather than parsing is deliberate: an element type is checked
    the same way as a container type, and an unfamiliar bracket form degrades to
    "check the pieces I recognise" instead of to a false finding.
    """
    text = (text or "").strip()
    if text.startswith(":"):
        text = text[1:]
    out = []
    for token in re.split(r"[\[\],\s]+", text):
        token = token.strip()
        if token and RE_IDENT_PATH.match(token):
            out.append(token)
    return out


class GDFile:
    """One .gd file's declarations and the names it refers to."""

    def __init__(self, res_path, fs_path, vendored=False):
        self.res_path = res_path          # "player/player.gd", no res:// prefix
        self.fs_path = fs_path
        self.vendored = vendored
        self.class_name = None
        self.class_name_line = 0
        self.extends_name = None          # `extends Foo` / `extends Foo.Bar`
        self.extends_path = None          # `extends "res://foo.gd"`
        self.inner_classes = {}           # name -> extends target or None
        self.consts = {}                  # name -> preloaded res path or None
        self.enums = {}                   # name -> [value names]; "" key = anonymous
        self.funcs = set()
        self.func_sigs = {}               # top-level func name -> (line, required, total)
        self.signals = set()
        self.variables = set()
        self.local_names = set()          # everything bound anywhere in the file
        self.bindings = set()             # value bindings only: vars, params, loop vars.
                                          # These shadow any same-named global, and their
                                          # type is usually unannotated -- so a member
                                          # reference through one is unknowable, not wrong.
        self.type_refs = []               # (line, type_path)
        self.root_refs = []               # (line, root_identifier, member or None)
        self.path_refs = []               # (line, kind, res_path)
        self.string_refs = []             # (line, verb, name)
        self.parse_error = None

    @property
    def display(self):
        return self.res_path


def scan_file(res_path, fs_path, vendored=False):
    gd = GDFile(res_path, fs_path, vendored)
    try:
        text = fs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = fs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        gd.parse_error = str(exc)
        return gd

    src = SourceText(text)
    code = src.code

    for m in RE_CLASS_NAME.finditer(code):
        gd.class_name = m.group(1)
        gd.class_name_line = src.line_of(m.start(1))
        gd.local_names.add(m.group(1))
        break

    m = RE_CLASS_NAME_EXTENDS.search(code)
    extends_raw = m.group(1) if m else None
    if extends_raw is None:
        m = RE_EXTENDS_TOP.search(code)
        extends_raw = m.group(1) if m else None
    if extends_raw:
        extends_raw = extends_raw.strip()
        if extends_raw[:1] in "\"'":
            # `extends "res://x.gd"` -- the literal was blanked out of `code`, so the
            # path comes from the preserved string list below, matched by line.
            gd.extends_path = _first_res_string(src, code.count("\n", 0, m.start()) + 1)
        elif RE_IDENT_PATH.match(extends_raw):
            gd.extends_name = extends_raw

    for m in RE_INNER_CLASS.finditer(code):
        gd.inner_classes[m.group(1)] = m.group(2)
        gd.local_names.add(m.group(1))
        if m.group(2):
            gd.type_refs.append((src.line_of(m.start(2)), m.group(2)))

    for m in RE_VAR.finditer(code):
        gd.variables.add(m.group(1))
        gd.local_names.add(m.group(1))
        gd.bindings.add(m.group(1))
        if m.group(2):
            for t in _type_names(m.group(2)):
                gd.type_refs.append((src.line_of(m.start(2)), t))

    for m in RE_CONST.finditer(code):
        name = m.group(1)
        gd.local_names.add(name)
        # `const Foo = preload("res://foo.gd")` makes Foo a usable type and a member
        # namespace. The path was blanked out of `code`, so it comes back off the
        # preserved string list by line.
        target = None
        if RE_CONST_PRELOAD.match(m.group(3) or ""):
            target = _first_res_string(src, src.line_of(m.start(3)))
        gd.consts[name] = target
        if m.group(2):
            for t in _type_names(m.group(2)):
                gd.type_refs.append((src.line_of(m.start(2)), t))

    for m in RE_FUNC.finditer(code):
        gd.funcs.add(m.group(1))
        gd.local_names.add(m.group(1))
        open_pos = code.index("(", m.end() - 1)
        close = _match_paren(code, open_pos)
        if close < 0:
            continue
        params = code[open_pos + 1:close - 1]
        # Signature of a TOP-LEVEL func (column 0, so an inner class's methods -
        # which override a different base - are not attributed to this file's
        # engine base). Static funcs never override a virtual.
        top_level = (m.start() == 0 or code[m.start() - 1] == "\n") and code[m.start()] == "f"
        parts = [p for p in _split_top_level(params) if p.strip()]
        required = sum(1 for p in parts if "=" not in p)
        if top_level:
            gd.func_sigs[m.group(1)] = (src.line_of(m.start()), required, len(parts))
        for part in parts:
            pm = re.match(r"([A-Za-z_]\w*)[ \t]*(:[ \t]*[^=]+)?", part)
            if not pm:
                continue
            gd.local_names.add(pm.group(1))
            gd.bindings.add(pm.group(1))
            if pm.group(2):
                for t in _type_names(pm.group(2)):
                    gd.type_refs.append((src.line_of(open_pos), t))
        tail = re.match(r"[ \t]*->[ \t]*([^:\n]+):", code[close:])
        if tail:
            for t in _type_names(tail.group(1)):
                gd.type_refs.append((src.line_of(close), t))

    for m in RE_SIGNAL.finditer(code):
        gd.signals.add(m.group(1))
        gd.local_names.add(m.group(1))
        if m.group(2):
            open_pos = code.index("(", m.end() - 1)
            close = _match_paren(code, open_pos)
            if close > 0:
                for part in _split_top_level(code[open_pos + 1:close - 1]):
                    pm = re.match(r"([A-Za-z_]\w*)[ \t]*(:[ \t]*[^=]+)?", part)
                    if pm and pm.group(2):
                        for t in _type_names(pm.group(2)):
                            gd.type_refs.append((src.line_of(open_pos), t))

    for m in RE_ENUM.finditer(code):
        open_pos = code.index("{", m.end() - 1)
        close = _match_paren(code, open_pos)
        body = code[open_pos + 1:close - 1] if close > 0 else ""
        values = []
        for part in _split_top_level(body):
            vm = re.match(r"([A-Za-z_]\w*)", part)
            if vm:
                values.append(vm.group(1))
        name = m.group(1) or ""
        gd.enums[name] = values
        if name:
            gd.local_names.add(name)
        gd.local_names.update(values)

    for m in RE_FOR.finditer(code):
        gd.local_names.add(m.group(1))
        gd.bindings.add(m.group(1))
        if m.group(2):
            for t in _type_names(m.group(2)):
                gd.type_refs.append((src.line_of(m.start(2)), t))

    for m in RE_LAMBDA.finditer(code):
        open_pos = m.end() - 1
        close = _match_paren(code, open_pos)
        if close < 0:
            continue
        for part in _split_top_level(code[open_pos + 1:close - 1]):
            pm = re.match(r"([A-Za-z_]\w*)", part)
            if pm:
                gd.local_names.add(pm.group(1))
                gd.bindings.add(pm.group(1))

    for m in RE_AS_IS.finditer(code):
        gd.type_refs.append((src.line_of(m.start(1)), m.group(1)))
    for m in RE_NEW_CALL.finditer(code):
        gd.type_refs.append((src.line_of(m.start(1)), m.group(1)))
    for m in RE_ROOT_REF.finditer(code):
        member = None
        if m.group(2) == ".":
            mm = RE_ROOT_MEMBER.match(code, m.end(1))
            member = mm.group(1) if mm else None
        gd.root_refs.append((src.line_of(m.start(1)), m.group(1), member))

    _collect_literal_refs(gd, src, code)
    return gd


def _first_res_string(src, line):
    for sline, value, _q in src.strings:
        if sline == line and value.startswith("res://"):
            return value[len("res://"):]
    return None


def _collect_literal_refs(gd, src, code):
    """preload/load paths, `extends "res://"` targets, and string-literal member names.

    Each pattern is matched in the blanked code (so it cannot fire inside a comment or a
    nested literal) and the argument is then read back out of the preserved string list
    by line -- the one place this tool needs the text it deliberately erased.
    """
    by_line = {}
    for sline, value, _q in src.strings:
        by_line.setdefault(sline, []).append(value)

    for m in RE_PRELOAD.finditer(code):
        line = src.line_of(m.start())
        for value in by_line.get(line, []):
            if value.startswith("res://"):
                gd.path_refs.append((line, m.group(1), value[len("res://"):]))
                break

    for m in RE_STRING_REF.finditer(code):
        line = src.line_of(m.start())
        candidates = by_line.get(line, [])
        if not candidates:
            continue
        # The literal opened by this call is the first one at or after the call's own
        # opening quote; with several calls on one line, position order is the tie-break.
        idx = sum(1 for other in RE_STRING_REF.finditer(code[:m.start()])
                  if src.line_of(other.start()) == line)
        if idx < len(candidates):
            gd.string_refs.append((line, m.group(1), candidates[idx]))


# ---------------------------------------------------------------------------
# Project model
# ---------------------------------------------------------------------------

class Project:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.files = []
        self.by_class = {}
        self.by_path = {}
        self.duplicate_classes = {}
        self.autoloads = {}
        self.vendored_dirs = []

    def scan(self):
        self.vendored_dirs = _vendored_dirs(self.root)
        for fs_path in _walk_gd(self.root):
            res_path = fs_path.relative_to(self.root).as_posix()
            vendored = any(res_path.startswith(v + "/") for v in self.vendored_dirs)
            gd = scan_file(res_path, fs_path, vendored)
            self.files.append(gd)
            self.by_path[res_path] = gd
            if gd.class_name:
                if gd.class_name in self.by_class:
                    self.duplicate_classes.setdefault(gd.class_name, [
                        self.by_class[gd.class_name].res_path]).append(gd.res_path)
                else:
                    self.by_class[gd.class_name] = gd
        self.autoloads = _read_autoloads(self.root)

    def base_of(self, gd, seen=None):
        """The project file `gd` extends, or None when the base is engine-side/unknown."""
        if gd.extends_path:
            return self.by_path.get(gd.extends_path)
        if gd.extends_name:
            root = gd.extends_name.split(".")[0]
            if root in gd.consts and gd.consts[root]:
                return self.by_path.get(gd.consts[root])
            return self.by_class.get(root)
        return None

    def engine_base_of(self, gd):
        """The engine class name at the top of `gd`'s extends chain, or None."""
        seen = set()
        cur = gd
        while cur is not None and cur.res_path not in seen:
            seen.add(cur.res_path)
            nxt = self.base_of(cur)
            if nxt is None:
                if cur.extends_name:
                    return cur.extends_name.split(".")[0]
                return None
            cur = nxt
        return None

    def scope_names(self, gd):
        """Every name visible inside `gd`: its own, plus each project base class's.

        Without the chain walk, a subclass using a constant its base declares looks like
        a reference to nothing -- the single largest source of false positives a
        file-at-a-time scan produces, and the reason this walks rather than guesses.
        """
        return self._chain_union(gd, "local_names")

    def binding_names(self, gd):
        """Value bindings visible inside `gd` -- its own and every project base's."""
        return self._chain_union(gd, "bindings")

    def _chain_union(self, gd, attr):
        names = set(getattr(gd, attr))
        seen = {gd.res_path}
        cur = self.base_of(gd)
        while cur is not None and cur.res_path not in seen:
            seen.add(cur.res_path)
            names |= getattr(cur, attr)
            cur = self.base_of(cur)
        return names

    def members_of(self, gd, api, depth=0):
        """Names reachable as `Something.member` when Something resolves to this file."""
        if depth > 16:
            return set()
        names = set(gd.funcs) | set(gd.signals) | set(gd.consts) | set(gd.variables)
        names |= set(gd.inner_classes)
        for ename, values in gd.enums.items():
            if ename:
                names.add(ename)
            names.update(values)
        base = self.base_of(gd)
        if base is not None and base.res_path != gd.res_path:
            names |= self.members_of(base, api, depth + 1)
        elif api is not None:
            engine = self.engine_base_of(gd)
            if engine:
                names |= api.members_of(engine)
        return names


def _vendored_dirs(root):
    """addons/<name> directories holding a plugin.cfg: third-party, exempt from checks.

    Their `class_name` declarations are still harvested -- a vendored addon's global
    classes are as real as any other, and treating them as absent would flag every
    project file that legitimately names one.
    """
    out = []
    addons = root / "addons"
    if not addons.is_dir():
        return out
    for child in sorted(addons.iterdir()):
        if child.is_dir() and (child / "plugin.cfg").is_file():
            out.append(f"addons/{child.name}")
    return out


def _walk_gd(root):
    """Every .gd under root, honouring .gdignore and skipping dot-directories."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name.startswith("."):
                    continue
                if (entry / GDIGNORE).is_file():
                    continue
                stack.append(entry)
            elif entry.suffix == ".gd":
                yield entry


def _read_autoloads(root):
    """`[autoload]` from project.godot as {name: res_path}.

    A hand-rolled section read rather than a ConfigFile port: the values are always
    `"*res://path.gd"` or `"res://path.tscn"`, and the failure mode of a partial parse
    here is a missing autoload name, which shows up immediately as a finding on a name
    the project uses everywhere.
    """
    out = {}
    path = root / "project.godot"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = stripped == "[autoload]"
            continue
        if not in_section or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        value = value.strip().strip('"').lstrip("*")
        if name:
            out[name] = value[len("res://"):] if value.startswith("res://") else value
    return out


def _read_class_cache(root):
    """class names in .godot/global_script_class_cache.cfg, or None when absent.

    Read-only, so it stays safe with N agents in the same checkout -- the point of this
    tool is that nothing here writes to `.godot/`. The cache is not a source of truth for
    any check; it is compared against the source declarations so a run can say "your
    engine will disagree with your files until you import", which is the one thing a
    static pass can tell you about a cache it refuses to touch.
    """
    path = root / ".godot" / "global_script_class_cache.cfg"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return set(re.findall(r'"class"[ \t]*:[ \t]*&?"([A-Za-z_]\w*)"', text))


# ---------------------------------------------------------------------------
# Engine API index
# ---------------------------------------------------------------------------

class ApiIndex:
    def __init__(self, data):
        self.engine = data.get("engine", "")
        self.classes = data.get("classes", {})
        self.builtins = data.get("builtins", {})
        self.singletons = data.get("singletons", {})
        self.global_enums = data.get("global_enums", {})
        self.global_constants = set(data.get("global_constants", []))
        self.utility_functions = set(data.get("utility_functions", []))
        # class -> {virtual method name: [required_args, total_args]}. Absent on an
        # index distilled before 0.22.0; has_virtuals says so and the check
        # reports itself SKIPPED rather than passing.
        self.virtuals = data.get("virtuals")
        self.source_path = None
        self._all_members = None

    @property
    def has_virtuals(self):
        return isinstance(self.virtuals, dict)

    def virtuals_of(self, class_name, depth=0):
        """Every virtual method an instance of `class_name` inherits, nearest
        declaration winning: {name: [required, total]}."""
        if not self.has_virtuals or depth > 64:
            return {}
        entry = self.classes.get(class_name)
        if entry is None:
            return {}
        out = {}
        parent = entry.get("inherits")
        if parent:
            out.update(self.virtuals_of(parent, depth + 1))
        out.update(self.virtuals.get(class_name, {}))
        return out

    def knows_type(self, name):
        return (name in self.classes or name in self.builtins
                or name in self.singletons or name in self.global_enums)

    def members_of(self, class_name, depth=0):
        if depth > 64:
            return set()
        entry = self.classes.get(class_name)
        if entry is None:
            entry = self.builtins.get(class_name)
            return set(entry.get("members", [])) if entry else set()
        names = set(entry.get("members", []))
        parent = entry.get("inherits")
        if parent:
            names |= self.members_of(parent, depth + 1)
        return names

    def members_of_any(self, name):
        """Members of a class, a singleton's class, or a global enum's value list."""
        if name in self.global_enums:
            return set(self.global_enums[name])
        if name in self.singletons:
            return self.members_of(self.singletons[name])
        return self.members_of(name)

    def all_member_names(self):
        if self._all_members is None:
            names = set()
            for cls in self.classes:
                names |= set(self.classes[cls].get("members", []))
            for cls in self.builtins:
                names |= set(self.builtins[cls].get("members", []))
            self._all_members = names
        return self._all_members

    def to_dict(self):
        return {
            "index_version": INDEX_VERSION,
            "harness_version": HARNESS_VERSION,
            "engine": self.engine,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "classes": self.classes,
            "builtins": self.builtins,
            "singletons": self.singletons,
            "global_enums": self.global_enums,
            "global_constants": sorted(self.global_constants),
            "utility_functions": sorted(self.utility_functions),
            "virtuals": self.virtuals if self.has_virtuals else {},
        }


def distill_api(dump):
    """extension_api.json (~6.7MB) -> the name tables this tool actually resolves against.

    Reduced to ~130KB gzipped, which is what makes a per-engine-version cache in a shared
    user directory reasonable rather than something a project would be tempted to commit.
    """
    classes = {}
    virtuals = {}
    for c in dump.get("classes", []):
        members = set()
        for key in ("methods", "properties", "signals", "constants"):
            members.update(x["name"] for x in c.get(key, []) if "name" in x)
        for e in c.get("enums", []):
            members.add(e["name"])
            members.update(v["name"] for v in e.get("values", []))
        classes[c["name"]] = {
            "inherits": c.get("inherits", "") or "",
            "members": sorted(members),
        }
        # Virtual methods with their arity, so a script overriding one with the
        # wrong signature is caught here (2 s) instead of at --import (40 s), where
        # Godot reports it against the DEPENDENT file (moving-in:G-022).
        vt = {}
        for mth in c.get("methods", []):
            if not mth.get("is_virtual") or "name" not in mth:
                continue
            arguments = mth.get("arguments", []) or []
            required = sum(1 for a in arguments if "default_value" not in a)
            vt[mth["name"]] = [required, len(arguments)]
        if c["name"] == "Object":
            # Object's script-level virtuals are GDScript's own and are NOT in the
            # extension API (no is_virtual entry, not even a member) - and they are
            # the ones with the short tempting names a UI file reaches for.
            # Arities per the Object class reference; `_init` is deliberately
            # absent (a GDScript constructor takes anything).
            vt.update({
                "_get": [1, 1], "_get_property_list": [0, 0], "_iter_get": [1, 1],
                "_iter_init": [1, 1], "_iter_next": [1, 1], "_notification": [1, 1],
                "_property_can_revert": [1, 1], "_property_get_revert": [1, 1],
                "_set": [2, 2], "_to_string": [0, 0], "_validate_property": [1, 1],
            })
        if vt:
            virtuals[c["name"]] = vt
    builtins = {}
    for c in dump.get("builtin_classes", []):
        members = set()
        for key in ("members", "constants", "methods"):
            members.update(x["name"] for x in c.get(key, []) if "name" in x)
        for e in c.get("enums", []):
            members.add(e["name"])
            members.update(v["name"] for v in e.get("values", []))
        builtins[c["name"]] = {"members": sorted(members)}

    global_enums = {}
    global_constants = set()
    for e in dump.get("global_enums", []):
        values = [v["name"] for v in e.get("values", [])]
        # `Variant.Type` and `Variant.Operator` are dotted; keep the leaf, which is how
        # GDScript spells them.
        global_enums[e["name"].split(".")[-1]] = values
        global_constants.update(values)
    global_constants.update(x["name"] for x in dump.get("global_constants", []) if "name" in x)

    index = ApiIndex({
        "engine": dump.get("header", {}).get("version_full_name", ""),
        "classes": classes,
        "builtins": builtins,
        "singletons": {s["name"]: s.get("type", s["name"]) for s in dump.get("singletons", [])},
        "global_enums": global_enums,
        "global_constants": sorted(global_constants),
        "utility_functions": sorted(
            f["name"] for f in dump.get("utility_functions", []) if "name" in f),
        "virtuals": virtuals,
    })
    return index


def cache_dir():
    """One index cache per machine, not per checkout.

    Every clone and every git worktree of a project shares it, so the dump cost is paid
    once no matter how many agents are working -- and nothing generated lands inside a
    project directory, where `.devtools/` is committed and a 130KB binary blob per engine
    version would be noise in every diff.
    """
    env = os.environ.get("GODOT_SELFTEST_CACHE")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "godot-selftest-harness" / "api"


def _sanitize(version):
    return re.sub(r"[^A-Za-z0-9._-]", "_", version) or "unknown"


def cache_path_for(version):
    return cache_dir() / f"engine_api_{_sanitize(version)}.json.gz"


def load_index(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, EOFError):
        return None
    if data.get("index_version") != INDEX_VERSION:
        return None
    index = ApiIndex(data)
    index.source_path = path
    return index


def save_index(index, path):
    """Atomic write: two agents refreshing at once must not leave a half-written index."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(index.to_dict(), fh, separators=(",", ":"))
    os.replace(tmp, path)


def godot_version(godot_path):
    try:
        proc = subprocess.run([str(godot_path), "--version"], capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
        line = line.strip()
        if re.match(r"^\d+\.\d+", line):
            return line
    return None


def refresh_index(godot_path, force=False, timeout=300):
    """Dump the engine API into the shared cache. Returns (index, note).

    The dump runs in a throwaway temp directory with no `--path`, so it opens no project,
    creates no `.godot/`, and takes no lock -- the property that lets this run while other
    agents are mid-verify on the same checkout.
    """
    version = godot_version(godot_path)
    if not version:
        return None, "could not read `%s --version`" % godot_path
    dest = cache_path_for(version)
    if dest.is_file() and not force:
        index = load_index(dest)
        # An index distilled before 0.22.0 has no virtual-method table; a refresh
        # that handed it back would leave that check SKIPPED forever.
        if index is not None and index.has_virtuals:
            return index, "cached (%s)" % version
    tmp = Path(tempfile.mkdtemp(prefix="godot-api-"))
    try:
        proc = subprocess.run(
            [str(godot_path), "--headless", "--dump-extension-api"],
            cwd=str(tmp), capture_output=True, text=True, timeout=timeout)
        dumped = tmp / "extension_api.json"
        if not dumped.is_file():
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            return None, ("--dump-extension-api wrote no extension_api.json (exit %d)%s"
                          % (proc.returncode,
                             ": " + tail[-1] if tail else ""))
        index = distill_api(json.loads(dumped.read_text(encoding="utf-8")))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return None, "dump failed: %s" % exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    save_index(index, dest)
    index.source_path = dest
    return index, "dumped %s -> %s" % (index.engine or version, dest)


def find_cached_index(resolve_version=None):
    """Newest usable cached index, preferring an exact engine-version match.

    `resolve_version` is a callable and is only invoked when the cache holds more than
    one index -- reading the version means running `godot --version`, and the whole
    premise of this tool is that a normal run launches nothing. With a single cached
    index there is nothing to disambiguate, so nothing is launched.
    """
    directory = cache_dir()
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob("engine_api_*.json.gz"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    if len(candidates) > 1 and resolve_version is not None:
        version = resolve_version()
        if version:
            exact = cache_path_for(version)
            if exact.is_file():
                index = load_index(exact)
                if index is not None:
                    return index
    for path in candidates:
        index = load_index(path)
        if index is not None:
            return index
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def finding(file, line, rule, severity, subject, message):
    return {"file": file, "line": line, "rule": rule, "severity": severity,
            "subject": subject, "message": message}


def finding_key(f):
    """Deliberately carries no line number, so a finding survives an unrelated edit.

    Same convention as lint_project.gd's baseline keys, so a project that already keeps
    one baseline does not have to learn a second format.
    """
    return "%s|%s|%s" % (f["file"], f["rule"], f["subject"])


REFRESH_HINT = "run `python tools/name_check.py --refresh-api` (opens no project)"

# The scope of a clean verdict, in one sentence, printed where the verdict is
# (moving-in:G-009). A checker that reports clean has to be honest about what
# "clean" claims, or the next reader upgrades it to "compiles" for free.
NOT_COVERED = ("a clean name_check resolves names, it does not compile the file. "
               "Type-inference errors (`:=` on an untyped call) are invisible to it; "
               "only lint / --import will catch them.")

# Members every type answers to but that no member list carries. `new` is ClassDB's
# static constructor, not a bound method, so it appears in no class's method list; the
# rest are Object methods that a builtin Variant type still accepts.
MEMBER_ALLOWLIST = frozenset({"new", "call", "callv", "bind", "unbind", "get", "set"})


class Checker:
    def __init__(self, project, api, extra_types, options):
        self.project = project
        self.api = api
        self.extra_types = set(extra_types)
        self.options = options
        self.findings = []
        # Names already reported as unknown_type, per file. An identifier that failed to
        # resolve in a type position will also fail as a bare reference, and reporting it
        # twice at two severities makes one typo look like two problems.
        self._unknown_types = {}
        self._project_funcs = set()
        self._project_signals = set()
        for gd in project.files:
            self._project_funcs |= gd.funcs
            self._project_signals |= gd.signals

    def add(self, *args):
        self.findings.append(finding(*args))

    # --- name resolution ---------------------------------------------------

    def resolve_root(self, name, gd, scope):
        """Does `name` name anything at all, in this file's scope or the engine's?"""
        if name in scope or name in GDSCRIPT_IMPLICIT_NAMES or name in GDSCRIPT_TYPE_KEYWORDS:
            return True
        if name in GDSCRIPT_GLOBAL_CONSTANTS or name in GDSCRIPT_ONLY_FUNCTIONS:
            return True
        if name in self.extra_types or name in self.project.by_class:
            return True
        if name in self.project.autoloads:
            return True
        if self.api is None:
            return True  # engine half unknown: never claim a name is absent
        return (self.api.knows_type(name)
                or name in self.api.global_constants
                or name in self.api.utility_functions)

    def members_for(self, name, gd, scope):
        """Member names reachable through `name.`, or None when the scope is unknowable.

        None is the important return: `some_local.foo()` where the local's type was never
        annotated is not a finding, it is a question this tool declined to guess at.
        """
        if name in gd.consts and gd.consts[name]:
            target = self.project.by_path.get(gd.consts[name])
            if target is not None:
                return self.project.members_of(target, self.api)
            return None
        if name in gd.inner_classes:
            return None
        # A value binding shadows any global of the same name, and its type is usually
        # unannotated -- so `Foo.bar` where Foo is a local `var Foo` says nothing about
        # whether some global class Foo has a `bar`. Checking it anyway would flag correct
        # code against the wrong class's member list.
        if name != gd.class_name and name in self.project.binding_names(gd):
            return None
        if name in self.project.by_class:
            return self.project.members_of(self.project.by_class[name], self.api)
        if name in self.project.autoloads:
            target = self.project.by_path.get(self.project.autoloads[name])
            if target is not None:
                return self.project.members_of(target, self.api)
            return None
        if self.api is not None and self.api.knows_type(name):
            return self.api.members_of_any(name)
        return None

    # --- individual rules --------------------------------------------------

    def check_duplicate_classes(self):
        for name, paths in sorted(self.project.duplicate_classes.items()):
            for path in paths[1:]:
                line = self.project.by_path[path].class_name_line
                self.add(path, line, "duplicate_class_name", SEVERITY_ERROR, name,
                         'class_name "%s" is declared in %d files (%s) - Godot registers '
                         "one global class per name and the others will not load"
                         % (name, len(paths), ", ".join(paths)))

    def check_class_name_shadowing(self):
        if self.api is None:
            return
        for gd in self.project.files:
            if gd.vendored or not gd.class_name:
                continue
            if self.api.knows_type(gd.class_name):
                self.add(gd.res_path, gd.class_name_line, "class_name_shadows_engine",
                         SEVERITY_ERROR,
                         gd.class_name,
                         'class_name "%s" collides with an engine class of the same name '
                         "- Godot refuses to register it" % gd.class_name)

    def check_paths(self):
        for gd in self.project.files:
            if gd.vendored:
                continue
            if gd.extends_path and not (self.project.root / gd.extends_path).is_file():
                self.add(gd.res_path, 1, "missing_extends_path", SEVERITY_ERROR,
                         gd.extends_path,
                         'extends "res://%s" - no such file' % gd.extends_path)
            for line, kind, target in gd.path_refs:
                if (self.project.root / target).exists():
                    continue
                severity = SEVERITY_ERROR if kind == "preload" else SEVERITY_WARNING
                self.add(gd.res_path, line, "missing_%s" % kind, severity, target,
                         '%s("res://%s") - no such file' % (kind, target))

    def check_types(self):
        for gd in self.project.files:
            if gd.vendored:
                continue
            scope = self.project.scope_names(gd)
            reported = set()
            for line, path in gd.type_refs:
                root = path.split(".")[0]
                if root in reported:
                    continue
                if not self.resolve_root(root, gd, scope):
                    reported.add(root)
                    self._unknown_types.setdefault(gd.res_path, set()).add(root)
                    self.add(gd.res_path, line, "unknown_type", SEVERITY_ERROR, root,
                             'type "%s" resolves to nothing: no class_name, inner class, '
                             "const, autoload or engine class by that name" % root)
                # The member half of a dotted type (`Types.Recipe`) is not checked here:
                # every dotted form also reaches check_global_refs as a `Name.member`
                # reference, and reporting it in both places made one typo look like two
                # findings at two different severities.

    def check_global_refs(self):
        """Advisory: a PascalCase identifier used as `Name.` or `Name(` that names nothing.

        Restricted to PascalCase on purpose. A lowercase identifier could be an inherited
        property this scan never saw, and SCREAMING_CASE could be an inherited constant --
        flagging either would produce exactly the "lint cries wolf" outcome that gets a
        gate switched off. PascalCase is the convention for types and autoloads, which is
        also where the failure actually happens: `Identifier "Types" not declared` is one
        missing autoload, repeated in a thousand files.
        """
        for gd in self.project.files:
            if gd.vendored:
                continue
            scope = self.project.scope_names(gd)
            reported = set(self._unknown_types.get(gd.res_path, ()))
            seen_members = set()
            for line, name, member in gd.root_refs:
                if name.isupper() or not any(c.islower() for c in name):
                    continue
                if not self.resolve_root(name, gd, scope):
                    if name in reported:
                        continue
                    reported.add(name)
                    self.add(gd.res_path, line, "unknown_global_ref", SEVERITY_WARNING,
                             name,
                             '"%s" is used here but declared nowhere: not a class_name, '
                             "autoload, engine class or name in scope" % name)
                    continue
                if member is None or member in MEMBER_ALLOWLIST:
                    continue
                key = (name, member)
                if key in seen_members:
                    continue
                members = self.members_for(name, gd, scope)
                if members is None or member in members:
                    continue
                seen_members.add(key)
                self.add(gd.res_path, line, "unknown_member", SEVERITY_WARNING,
                         "%s.%s" % (name, member),
                         '"%s" has no member "%s"' % (name, member))

    def check_string_refs(self):
        known = self._project_funcs | self._project_signals
        if self.api is not None:
            known |= self.api.all_member_names()
        known |= GDSCRIPT_ONLY_FUNCTIONS
        for gd in self.project.files:
            if gd.vendored:
                continue
            for line, verb, name in gd.string_refs:
                if not name or not re.match(r"^[A-Za-z_]\w*$", name):
                    continue
                if name in known:
                    continue
                self.add(gd.res_path, line, "string_ref_unresolved", SEVERITY_ADVISORY,
                         name,
                         '%s("%s") - no `func %s` and no `signal %s` anywhere in the '
                         "project, and no engine member by that name"
                         % (verb, name, name, name))

    def check_virtual_signatures(self):
        """A top-level func whose name is an engine virtual on the script's engine
        base must accept the arity the engine calls it with (moving-in:G-022).

        `func _set(action: Callable) -> void` passed every static check and then
        failed --import with `The function signature doesn't match the parent`,
        reported against the file that PRELOADED it. Only arity is checked - a
        script may add optional parameters, so the engine's count has to fall
        within [required, total] - because parameter types are not recorded in
        the index and GDScript accepts a looser type. `_init` is GDScript's own
        constructor and takes anything.
        """
        if self.api is None or not self.api.has_virtuals:
            return
        for gd in self.project.files:
            if gd.vendored or not gd.func_sigs:
                continue
            base = self.project.engine_base_of(gd)
            if not base:
                continue
            virtuals = self.api.virtuals_of(base)
            if not virtuals:
                continue
            for name, (line, required, total) in sorted(gd.func_sigs.items()):
                if name == "_init" or name not in virtuals:
                    continue
                engine_required, engine_total = virtuals[name]
                if required <= engine_total <= total:
                    continue
                self.add(gd.res_path, line, "virtual_signature_mismatch", SEVERITY_ERROR,
                         name,
                         "%s() overrides %s.%s, which the engine calls with %d argument(s); "
                         "this declaration takes %s - the import will fail with 'The "
                         "function signature doesn't match the parent', reported against "
                         "whichever file loads this one"
                         % (name, base, name, engine_total,
                            str(required) if required == total
                            else "%d to %d" % (required, total)))

    def check_class_cache(self):
        """Warn when `.godot/` and the source disagree -- without touching `.godot/`."""
        cached = _read_class_cache(self.project.root)
        if cached is None:
            return
        declared = {gd.class_name: gd for gd in self.project.files if gd.class_name}
        missing = sorted(set(declared) - cached)
        for name in missing:
            self.add(declared[name].res_path, declared[name].class_name_line,
                     "class_cache_stale", SEVERITY_WARNING,
                     name,
                     'class_name "%s" is declared here but absent from '
                     ".godot/global_script_class_cache.cfg - the engine will not resolve "
                     "it until you run `godot --headless --path . --import`" % name)

    def run(self):
        self.check_duplicate_classes()
        self.check_class_name_shadowing()
        self.check_paths()
        self.check_types()
        self.check_global_refs()
        if self.options.get("strings", True):
            self.check_string_refs()
        self.check_virtual_signatures()
        self.check_class_cache()
        return self.findings


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_ADVISORY: 2}
SEVERITY_LABEL = {SEVERITY_ERROR: "ERROR", SEVERITY_WARNING: "WARN",
                  SEVERITY_ADVISORY: "ADVISORY"}


def _read_harness_config(project_path):
    """addons/godot_selftest/devtools_config.json as a dict; {} when unreadable.

    Duplicated from import_check.py for the same reason it duplicates devtools.py: this
    tool has to work when the bridge client is the thing that is broken, and a checker
    that cannot run on a broken project checks nothing.
    """
    cfg_path = project_path / "addons" / "godot_selftest" / "devtools_config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _xy(version: str) -> str:
    """Major.minor out of a version string, from either side of the comparison.

    Must handle both shapes: config's `godot_version` is bare ("4.7.1"), while
    an index's `engine` is the binary's own banner ("Godot Engine v4.7.1.stable
    .official"). A regex anchored at the start matches the first and silently
    never matches the second, which made this check dead code that reported
    clean on a real mismatch - caught only by planting one.
    """
    m = re.search(r"v?(\d+)\.(\d+)", version or "")
    return "%s.%s" % (m.group(1), m.group(2)) if m else ""


def _resolve_godot(args, project_path):
    config = _read_harness_config(project_path)
    godot = args.godot or os.environ.get("GODOT_BIN") or str(config.get("godot_bin", "") or "")
    if not godot:
        return None
    godot_path = Path(godot).expanduser()
    return godot_path if godot_path.is_file() else None


def report(findings, index, project, counts, args, skipped, baseline_info,
           engine_skew=None):
    print("name_check %s - static name resolution, no engine launched" % HARNESS_VERSION)
    print("project: %s" % project.root)
    print("scanned: %d script(s), %d global class(es), %d autoload(s)%s"
          % (len(project.files), len(project.by_class), len(project.autoloads),
             ", %d vendored dir(s) skipped" % len(project.vendored_dirs)
             if project.vendored_dirs else ""))
    if index is not None:
        print("engine index: %s (%d classes, %d builtins) from %s"
              % (index.engine or "unknown", len(index.classes), len(index.builtins),
                 index.source_path or "--api"))
        if engine_skew:
            print("  WARNING: %s" % engine_skew)
    else:
        print("engine index: NONE - engine-name checks were SKIPPED, not passed. %s"
              % REFRESH_HINT)
    if baseline_info:
        print("baseline: %s (%d known finding(s))" % baseline_info)
    print("")

    if not findings:
        print("No findings.")
    else:
        by_file = {}
        for f in findings:
            by_file.setdefault(f["file"], []).append(f)
        for path in sorted(by_file):
            rows = sorted(by_file[path], key=lambda f: (SEVERITY_ORDER[f["severity"]],
                                                        f["line"], f["rule"]))
            print(path)
            for f in rows:
                where = ":%d" % f["line"] if f["line"] else ""
                new = " [NEW]" if f.get("baseline_new") else (
                    " [PRE-EXISTING]" if f.get("baseline_seen") else "")
                print("  %-9s %s%s  %s%s"
                      % (SEVERITY_LABEL[f["severity"]], f["rule"], where,
                         f["message"], new))
            print("")

    print("errors: %d | warnings: %d | advisory: %d"
          % (counts["error"], counts["warning"], counts["advisory"]))
    for note in skipped:
        print("SKIPPED: %s" % note)
    if not counts["error"]:
        # Said on exactly the run where it is dangerous. A clean verdict here is
        # the only gate a parallel agent is allowed to run, and twice now that has
        # been read as "it builds" (moving-in:G-009).
        print("NOT COVERED: %s" % NOT_COVERED)


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Resolve every name a GDScript project mentions, without launching Godot.\n"
                    "\n"
                    "NOT COVERED: a clean name_check resolves names, it does not compile the\n"
                    "file. Type-inference errors (`:=` on an untyped call) are invisible to\n"
                    "it; only lint / --import will catch them.",
        epilog="Exit codes: 0 clean, 1 findings that count, 2 could not run.\n"
               "\n"
               "This is the only gate that never opens the project, so it is the only one\n"
               "safe to run in parallel - and the only one a fan-out agent gets. Clean here\n"
               "means cleared to integrate, not that it builds (moving-in:G-009).")
    parser.add_argument("--project", "-p", default=".", help="Path to the Godot project")
    parser.add_argument("--api", help="Path to an engine API index (.json.gz) to use")
    parser.add_argument("--refresh-api", action="store_true",
                        help="Dump the engine API into the shared cache first. Runs Godot "
                             "in a temp dir with no project: no .godot/, no lock.")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Re-dump even when this engine version is already cached")
    parser.add_argument("--require-api", action="store_true",
                        help="Exit 2 when no engine index is available instead of "
                             "reporting the engine checks as skipped")
    parser.add_argument("--godot", help="Godot binary (else $GODOT_BIN, else config godot_bin)")
    parser.add_argument("--only", action="append", default=[],
                        help="Report only findings whose path starts with this prefix "
                             "(repeatable). The whole project is always scanned; this "
                             "filters the report, so cross-file names still resolve.")
    parser.add_argument("--no-strings", action="store_true",
                        help="Skip the advisory string-literal member check")
    parser.add_argument("--strict", action="store_true",
                        help="Count warnings towards the exit code")
    parser.add_argument("--baseline-write", metavar="PATH",
                        help="Write the current finding keys to PATH and exit 0")
    parser.add_argument("--baseline", metavar="PATH",
                        help="Compare against a baseline: only NEW findings gate")
    parser.add_argument("--json", action="store_true", help="Emit the verdict as JSON")
    args = parser.parse_args()

    project_root = Path(args.project).expanduser().resolve()
    if not (project_root / "project.godot").is_file():
        print("Error: no project.godot in %s. Run from the project root or pass "
              "--project/-p." % project_root, file=sys.stderr)
        return EXIT_CANNOT_RUN

    config = _read_harness_config(project_root)
    extra_types = config.get("name_check_extra_types", [])
    if not isinstance(extra_types, list):
        print("Error: name_check_extra_types in devtools_config.json is not a list.",
              file=sys.stderr)
        return EXIT_CANNOT_RUN
    ignore_prefixes = config.get("name_check_ignore", [])
    if not isinstance(ignore_prefixes, list):
        print("Error: name_check_ignore in devtools_config.json is not a list.",
              file=sys.stderr)
        return EXIT_CANNOT_RUN
    ignore_prefixes = [str(p).replace("res://", "") for p in ignore_prefixes]

    # --- engine index ---
    index = None
    skipped = []
    if args.api:
        index = load_index(Path(args.api).expanduser())
        if index is None:
            print("Error: could not read an engine API index at %s (wrong format, or "
                  "written by a different index_version)." % args.api, file=sys.stderr)
            return EXIT_CANNOT_RUN
        index.source_path = args.api
    else:
        godot_path = _resolve_godot(args, project_root)
        if args.refresh_api or args.force_refresh:
            if godot_path is None:
                print("Error: --refresh-api needs a Godot binary. Pass --godot PATH, set "
                      "$GODOT_BIN, or set \"godot_bin\" in "
                      "addons/godot_selftest/devtools_config.json.", file=sys.stderr)
                return EXIT_CANNOT_RUN
            index, note = refresh_index(godot_path, force=args.force_refresh)
            if index is None:
                print("Error: %s" % note, file=sys.stderr)
                return EXIT_CANNOT_RUN
            print("engine API: %s" % note)
        else:
            index = find_cached_index(
                (lambda: godot_version(godot_path)) if godot_path else None)

    # An index built by a DIFFERENT engine resolves classes the project's engine
    # may not have, and the substitution used to be silent (H-032). The
    # scaffolder records the resolved engine version in devtools_config.json, so
    # the comparison costs nothing - no launch, which is the whole point of this
    # tool. Comparing X.Y only: a patch bump does not add or remove API.
    engine_skew = None
    if index is not None:
        want = _xy(str(config.get("godot_version", "") or ""))
        have = _xy(str(index.engine or ""))
        if want and have and want != have:
            engine_skew = (
                "engine index is %s but this project declares Godot %s. Names "
                "resolved against the wrong engine can be wrong in both "
                "directions. Run --refresh-api with the %s binary."
                % (index.engine, want, want)
            )

    if index is None:
        note = ("engine-name checks (unknown_type against ClassDB, engine members, "
                "class_name shadowing): no API index cached. %s" % REFRESH_HINT)
        if args.require_api:
            print("Error: %s" % note, file=sys.stderr)
            return EXIT_CANNOT_RUN
        skipped.append(note)
    elif not index.has_virtuals:
        skipped.append("virtual_signature_mismatch (a script overriding an engine "
                       "virtual with the wrong arity): the cached index predates "
                       "0.22.0 and holds no virtual-method table. %s" % REFRESH_HINT)

    # --- scan ---
    project = Project(project_root, config)
    project.scan()

    checker = Checker(project, index, extra_types, {"strings": not args.no_strings})
    findings = checker.run()

    if ignore_prefixes:
        findings = [f for f in findings
                    if not any(f["file"].startswith(p) for p in ignore_prefixes)]
    if args.only:
        prefixes = [p.replace("res://", "") for p in args.only]
        findings = [f for f in findings
                    if any(f["file"].startswith(p) for p in prefixes)]

    # --- baseline ---
    baseline_info = None
    if args.baseline_write:
        keys = sorted({finding_key(f) for f in findings})
        try:
            Path(args.baseline_write).write_text(
                json.dumps({"tool": "name_check", "harness_version": HARNESS_VERSION,
                            "keys": keys}, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print("Error: could not write baseline: %s" % exc, file=sys.stderr)
            return EXIT_CANNOT_RUN
        print("Wrote %d finding key(s) to %s" % (len(keys), args.baseline_write))
        return EXIT_OK

    known = set()
    if args.baseline:
        try:
            data = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            known = set(data["keys"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print("Error: could not read baseline %s: %s" % (args.baseline, exc),
                  file=sys.stderr)
            return EXIT_CANNOT_RUN
        for f in findings:
            if finding_key(f) in known:
                f["baseline_seen"] = True
            else:
                f["baseline_new"] = True
        baseline_info = (args.baseline, len(known))

    gating = [f for f in findings if not f.get("baseline_seen")]
    counts = {"error": 0, "warning": 0, "advisory": 0}
    for f in findings:
        counts[f["severity"]] += 1
    gating_errors = sum(1 for f in gating if f["severity"] == SEVERITY_ERROR)
    gating_warnings = sum(1 for f in gating if f["severity"] == SEVERITY_WARNING)

    exit_code = EXIT_OK
    if gating_errors or (args.strict and gating_warnings):
        exit_code = EXIT_FINDINGS

    if args.json:
        print(json.dumps({
            "tool": "name_check",
            "harness_version": HARNESS_VERSION,
            "project": str(project_root),
            "engine_index": ({"engine": index.engine,
                              "path": str(index.source_path),
                              "classes": len(index.classes)} if index else None),
            "scanned": {"scripts": len(project.files),
                        "global_classes": len(project.by_class),
                        "autoloads": len(project.autoloads),
                        "vendored_skipped": project.vendored_dirs},
            "counts": counts,
            "skipped": skipped,
            # A machine reader of an all-zero `counts` needs the same caveat the
            # human report prints, or it draws the stronger conclusion silently.
            "not_covered": NOT_COVERED,
            "baseline": args.baseline,
            "findings": findings,
            "exit": exit_code,
        }, indent=2))
    else:
        report(findings, index, project, counts, args, skipped, baseline_info,
               engine_skew)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
