#!/usr/bin/env python3
"""Keep the harness version stamps and the release history honest.

This repo ships files that get copied into other people's projects, and two
things about that only work if they are exact:

1. **Every copied file carries `# harness-version: X.Y.Z`**, matching
   `.claude-plugin/plugin.json`. A stamp that lags a release is worse than no
   stamp - it makes a gap look like it was logged against a version it wasn't.
2. **`harness_history.json` records the sha256 of every shipped template file
   per version.** `/scaffold-godot-harness` uses it to tell a *pristine older
   copy* (safe to overwrite silently) from a *file the project edited* (must be
   backed up). Without the history, a plain version bump leaves a `.bak` beside
   every tool - untracked junk the scaffolder can never clean up.

Usage:
    python tools/record_version.py --check     # verify stamps + history (exit 1 on drift)
    python tools/record_version.py --record    # write this version's hashes into the history

Run `--check` before committing a release. Run `--record` once the templates for
a version are final; re-recording the same version overwrites its entry, which is
correct while a release is still in progress and harmless afterwards.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"
HISTORY = REPO / "harness_history.json"

# Files copied verbatim into a target project, relative to templates/.
SHIPPED = [
    "addons/godot_selftest/dev_tools.gd",
    "addons/godot_selftest/scene_validator.gd",
    "tools/lint_project.gd",
    "tools/run_tests.gd",
    "tools/eval.gd",
    "tools/devtools.py",
    "tools/check_devtools_log.py",
    "tools/upstream_gaps.py",
    "tools/verify_ledger.py",
    "tools/import_check.py",
    "tools/name_check.py",
]

# Files that must be byte-identical to their template (this repo keeps runnable copies).
MIRRORED = {"tools/upstream_gaps.py": "tools/upstream_gaps.py"}

_STAMP_RE = re.compile(r"^#\s*harness-version:\s*(\S+)\s*$", re.M)
_CONST_RE = re.compile(r"""HARNESS_VERSION(?:\s*:\s*String)?\s*=\s*["'](\S+?)["']""")
_REGISTER_RE = re.compile(r'register_command\("(\w+)"')
_ADD_PARSER_RE = re.compile(r'add_parser\(\s*"([a-z][a-z0-9-]*)"')

# Docs that must name every verb (H-007). commands/verify.md is deliberately a
# curated subset (its primitives table), so it is not on this list.
DOC_RULES = [
    # (doc path, which surface it must cover)
    ("REFERENCE.md", "both"),                   # the reference manual: bus verbs + CLI
    ("templates/CLAUDE.harness.md", "cli"),     # the in-project cheat sheet: CLI surface
]


def plugin_version():
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]


def sha256(path):
    """Hash with line endings normalized to LF - see tools/scaffold_install.py.

    Must stay identical to the one there: they read and write the same hashes in
    harness_history.json, and on Windows (core.autocrlf) a raw byte hash would
    silently match nothing.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _normalize_doc(text):
    """Flatten table/pipe syntax so `input <press\\|release>` matches `input press`."""
    return re.sub(r"\s+", " ", re.sub(r"[<>`\\|/]", " ", text))


def _word_in(candidate, doc_text_normalized):
    """Word-boundary containment, so `curve` does not match inside `curves`."""
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(candidate),
                     doc_text_normalized) is not None


def _doc_has(verb, doc_text_normalized, shadowing_families=()):
    """Is `verb` named in this doc?

    `shadowing_families` names CLI families that already contain a sub-verb of
    the same name (`touch press`, `input press`). For a TOP-LEVEL verb that
    collides with one, a bare occurrence proves nothing: a doc listing only
    `touch press` used to satisfy the coverage check for a brand-new top-level
    `press`, so the one surface meant to make docs impossible to forget silently
    exempted exactly the names most likely to be forgotten. Such a verb must
    appear at least once NOT immediately preceded by a shadowing family word.
    """
    cands = {verb, verb.replace("_", "-")}
    if "_" in verb:
        family, _, sub = verb.partition("_")
        cands.add("%s %s" % (family, sub))

    if not shadowing_families:
        return any(_word_in(c, doc_text_normalized) for c in cands)

    # Fixed-width negative lookbehinds, one per family. Python's re allows
    # several of these in sequence where a single variable-width one is illegal.
    guards = "".join("(?<!%s )" % re.escape(f) for f in sorted(shadowing_families))
    return any(
        re.search(r"(?<![\w-])%s%s(?![\w-])" % (guards, re.escape(c)),
                  doc_text_normalized) is not None
        for c in cands)


def _cli_surface(text):
    """(top_level_names, {name: set(families that also define it)}).

    `subparsers.add_parser("press")` and `touch_sub.add_parser("press")` are two
    different commands, and the bare name cannot tell them apart. The receiver
    variable is the only thing in the source that can: `subparsers` is the root,
    `<family>_sub` is that family's subparser.
    """
    top = []
    shadows = {}
    for receiver, name in re.findall(
            r'(\w+)\.add_parser\(\s*"([a-z][a-z0-9-]*)"', text):
        if receiver.endswith("_sub"):
            shadows.setdefault(name, set()).add(receiver[:-len("_sub")])
        else:
            # `subparsers`, or any unrecognised receiver: treat as top level
            # rather than silently dropping the verb from the requirement.
            top.append(name)
    return top, shadows


def check_doc_fanout():
    """Every verb must appear in the docs that promise to list them (H-007).

    The bus surface comes from register_command() calls in dev_tools.gd; the CLI
    surface from add_parser() calls in devtools.py. A new verb that misses a doc
    fails --check, so the tables can no longer silently lag the code.
    """
    problems = []
    bus_verbs = _REGISTER_RE.findall(
        (REPO / "templates/addons/godot_selftest/dev_tools.gd").read_text(encoding="utf-8"))
    cli_top, cli_shadows = _cli_surface(
        (REPO / "templates/tools/devtools.py").read_text(encoding="utf-8"))
    cli_top_set = set(cli_top)
    # Sub-verbs are still required, but only ever appear as `input clear` /
    # `touch drag`, so the shadow guard must NOT be applied to them - it exists
    # solely to stop a TOP-LEVEL verb from being satisfied by its sub-verb
    # namesake. Applying it to a sub-verb demands an occurrence that should not
    # exist and would push the docs to document a command that isn't there.
    cli_names = cli_top + sorted(cli_shadows)

    if not bus_verbs:
        problems.append("doc fan-out: found no register_command() calls - regex broken?")
    if not cli_names:
        problems.append("doc fan-out: found no add_parser() calls - regex broken?")

    for doc_rel, scope in DOC_RULES:
        doc = _normalize_doc((REPO / doc_rel).read_text(encoding="utf-8"))
        wanted = []
        if scope in ("both", "bus"):
            wanted += bus_verbs
        if scope in ("both", "cli"):
            wanted += cli_names
        missing = sorted({
            v for v in wanted
            if not _doc_has(v, doc, cli_shadows.get(v, ()) if v in cli_top_set else ())})
        if missing:
            problems.append("%s: undocumented verb(s): %s" % (doc_rel, ", ".join(missing)))
    return problems, len(bus_verbs), len(cli_names)


def check():
    version = plugin_version()
    problems = []

    for rel in SHIPPED:
        path = REPO / "templates" / rel
        if not path.is_file():
            problems.append("%s: missing" % rel)
            continue
        text = path.read_text(encoding="utf-8")

        stamps = _STAMP_RE.findall(text)
        if not stamps:
            problems.append("%s: no `# harness-version:` stamp" % rel)
        elif any(s != version for s in stamps):
            problems.append("%s: stamp %s != plugin.json %s" % (rel, ", ".join(stamps), version))

        consts = _CONST_RE.findall(text)
        if any(c != version for c in consts):
            problems.append("%s: HARNESS_VERSION %s != plugin.json %s"
                            % (rel, ", ".join(consts), version))

    for rel, mirror in MIRRORED.items():
        src, dst = REPO / "templates" / rel, REPO / mirror
        if not dst.is_file():
            problems.append("%s: mirror missing (copy templates/%s across)" % (mirror, rel))
        elif sha256(src) != sha256(dst):
            problems.append("%s: differs from templates/%s - copy the template across" % (mirror, rel))

    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}
    if version not in history:
        problems.append("harness_history.json: no entry for %s (run --record)" % version)
    else:
        for rel in SHIPPED:
            path = REPO / "templates" / rel
            if path.is_file() and history[version].get(rel) != sha256(path):
                problems.append("harness_history.json: %s hash is stale for %s (run --record)"
                                % (rel, version))

    fanout_problems, n_bus, n_cli = check_doc_fanout()
    problems.extend(fanout_problems)

    if problems:
        print("version check FAILED (%d problem(s)):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("version check OK: %s stamped in %d shipped file(s), history recorded, "
          "%d bus verb(s) + %d CLI command(s) documented."
          % (version, len(SHIPPED), n_bus, n_cli))
    return 0


def record():
    version = plugin_version()
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}
    entry = {}
    for rel in SHIPPED:
        path = REPO / "templates" / rel
        if not path.is_file():
            print("error: %s is missing; refusing to record a partial version" % rel,
                  file=sys.stderr)
            return 1
        entry[rel] = sha256(path)
    was = "updated" if version in history else "added"
    history[version] = entry
    ordered = {k: history[k] for k in sorted(history, key=lambda v: [int(n) for n in v.split(".")])}
    HISTORY.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    print("%s %s in harness_history.json (%d file(s))" % (was, version, len(entry)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Verify stamps, mirrors and history")
    g.add_argument("--record", action="store_true", help="Record this version's template hashes")
    args = ap.parse_args()
    return check() if args.check else record()


if __name__ == "__main__":
    sys.exit(main())
