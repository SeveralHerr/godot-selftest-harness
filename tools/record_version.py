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

# Files copied verbatim into a target project, relative to templates/. One list,
# owned by the installer: what `scaffold_install.py full` installs is exactly what
# this stamps and hashes, so a shipped file cannot be added to one and not the other.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scaffold_install import SHIPPED_FILES as SHIPPED  # noqa: E402

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


# Docs whose relative links must resolve (H-036, 0.54.0). A `git mv README.md` once
# left links pointing at a file that no longer existed and nothing said so.
LINK_DOCS = ["README.md", "REFERENCE.md", "PURPOSE.md", "CLAUDE.md",
             "templates/CLAUDE.harness.md", "templates/log-devtools.md",
             "commands/verify.md", "commands/scaffold-godot-harness.md"]
_LINK_RE = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def check_doc_links(root=None, docs=None):
    """Every relative link in LINK_DOCS points at a file that exists (H-036).
    URLs, anchors-only and template placeholders are skipped. A link that appears
    inside a fenced code block is skipped too - it is an example, not a reference."""
    problems = []
    checked = 0
    root = Path(root) if root else REPO
    for rel in (docs or LINK_DOCS):
        path = root / rel
        if not path.is_file():
            continue
        in_fence = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in _LINK_RE.findall(line):
                if re.match(r"^[a-z]+://", target) or target.startswith(("mailto:", "<", "$", "{")):
                    continue
                if "%" in target or "<" in target:
                    continue
                checked += 1
                candidate = (path.parent / target).resolve()
                if not candidate.exists():
                    problems.append("%s: link target %s does not exist" % (rel, target))
    return problems, checked


# A gap's status line, as `log-devtools.md` writes it:
#   - [H-020] status: fixed | fixed-in: 0.62.0 | verified-by: ... | seen: 1 | ...
_GAP_STATUS_RE = re.compile(
    r"^\s*-\s*\[((?:[A-Za-z0-9._-]+:)?[A-Za-z]+-\d+[a-z]?)\]\s*status:\s*(\S+)(.*)$",
    re.M)


def check_closures_are_evidenced(version, log_path=None):
    """H-020, open since 0.9.0: nothing checked that a shipped fix closes the gap it
    names, so an unevidenced close was indistinguishable from an evidenced one.

    Thirty status lines once moved to `fixed-in: 0.9.0` on the strength of one agent
    reading the source and deciding; four of thirty-four turned out partial. That is
    the process working AND the measure of its error rate - the same pass that caught
    four could have missed a fifth, and nothing downstream would ever say so.

    This does NOT prove a judgement right, and it is not meant to. It makes an
    unevidenced close VISIBLE. Scoped to closures claiming THIS version on purpose:
    the history is what it is, and retro-fitting evidence onto it would be inventing
    it. Requires the `verified-by:` field to name something a reader can go and
    re-run - a contract row, a check_templates stage, a test name, a lint rule.
    """
    path = Path(log_path) if log_path else (REPO / "log-devtools.md")
    if not path.is_file():
        return ["log-devtools.md: missing - gap closures cannot be checked"], 0
    text = path.read_text(encoding="utf-8", errors="replace")
    # Resolve per id from the LAST status line: the log is append-only, so an id's
    # history is `open` -> `open` -> `fixed` on separate lines and a per-line scan
    # would read every fixed gap as open forever (the gh#63 defect, one file over).
    latest = {}
    for m in _GAP_STATUS_RE.finditer(text):
        latest[m.group(1)] = (m.group(2).lower().rstrip("|").strip(), m.group(3),
                              text.count("\n", 0, m.start()) + 1)
    closed_here, unevidenced = [], []
    for gid, (status, rest, line_no) in sorted(latest.items()):
        if not status.startswith("fixed"):
            continue
        if ("fixed-in: %s" % version) not in rest:
            continue
        closed_here.append(gid)
        if "verified-by:" not in rest:
            unevidenced.append("%s (log-devtools.md:%d)" % (gid, line_no))
    if unevidenced:
        return ([
            "log-devtools.md: %d gap(s) closed in %s carry no `verified-by:` naming what "
            "proves it: %s. Add `| verified-by: <a contract row, a check_templates stage, "
            "a test name, a lint rule>` to each status line, or leave the gap open. An "
            "unevidenced close reads exactly like an evidenced one, which is the whole "
            "defect (H-020)." % (len(unevidenced), version, ", ".join(unevidenced))
        ], len(closed_here))
    return [], len(closed_here)


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
    link_problems, n_links = check_doc_links()
    problems.extend(link_problems)
    closure_problems, n_closed = check_closures_are_evidenced(version)
    problems.extend(closure_problems)

    if problems:
        print("version check FAILED (%d problem(s)):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("version check OK: %s stamped in %d shipped file(s), history recorded, "
          "%d bus verb(s) + %d CLI command(s) documented, %d relative doc link(s) resolve, "
          "%d gap closure(s) in %s evidenced."
          % (version, len(SHIPPED), n_bus, n_cli, n_links, n_closed, version))
    warning = git_release_state(version)
    if warning:
        print(warning)
    return 0


def git_release_state(version, repo=None, run=None):
    """One WARNING line when `version` is cut in the working tree but not committed
    (bead 1kh): every stamp, hash and doc can agree with each other and with nothing
    in git history, and `--check` used to print OK on exactly that. Returns "" on a
    clean tree at the recorded version, or when git is unavailable (not a repo, no
    binary) - the state is stated, never gated, so exit stays 0.

    `repo`/`run` exist for the unit test, which plants a dirty tree with bumped
    stamps and a clean one and asserts which of the two says something.
    """
    import subprocess
    repo = Path(repo) if repo else REPO
    run = run or (lambda cmd: subprocess.run(cmd, cwd=str(repo), capture_output=True,
                                             text=True, timeout=30))
    try:
        status = run(["git", "status", "--porcelain", "--untracked-files=no"])
        head = run(["git", "show", "HEAD:.claude-plugin/plugin.json"])
    except (OSError, subprocess.SubprocessError):
        return ""
    if status.returncode != 0 or head.returncode != 0:
        return ""
    dirty = [ln for ln in status.stdout.splitlines() if ln.strip()]
    try:
        head_version = json.loads(head.stdout)["version"]
    except (ValueError, KeyError, TypeError):
        return ""
    if head_version == version:
        return ""  # a dirty tree at the committed version is ordinary post-release editing
    return ("WARNING: %s is recorded but uncommitted - HEAD ships %s and %d tracked "
            "file(s) are dirty. Every stamp and hash agree with each other and with "
            "nothing in git history; this is a mid-release state, not a shipped one."
            % (version, head_version, len(dirty)))


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
