#!/usr/bin/env python3
"""Claude Code `Stop` hook: keep the devtools gaps log honest.

Installed by `/scaffold-godot-harness`. On every Stop event it asks one question:
did this session touch Godot code without adding an entry to `log-devtools.md`?
If so it prints a `systemMessage` reminder. It is **advisory by default** — it
warns, it does not fail or restart the turn.

Why a hook at all: the "append a gap entry every response" convention is a
prompt-level instruction with no enforcement, so it silently decays. The hook is
the only part of the loop that cannot forget.

**What counts as compliance.** An entry dated today — a `## ` heading containing
today's date, which is the format the log's own Format section prescribes:

    ## 2026-08-01 — Added the ore-tier chain

Until 0.5.0 this only checked whether the log file showed up in `git status`, so
*any* byte-level change satisfied it: a stray whitespace edit passed, and so did
a log whose newest entry was three weeks old. That is precisely the decay this
hook exists to prevent, so the check now reads the file rather than its git
status. Set `log_check_dated_entry` to false to fall back to the old behavior.

Config (all optional) is read from `addons/godot_selftest/devtools_config.json`:

    "log_files":              ["log-devtools.md"]  # files that must be updated alongside code
    "log_check_globs":        []                   # extra path substrings counted as "code"
    "log_check_block":        false                # true -> block the Stop instead of warning
    "log_check_dated_entry":  true                 # false -> only require the file to change

Set `log_check_block` to true only if the advisory reminder proves easy to ignore;
a blocking Stop hook forces the model to continue the turn, which costs a round trip
every time it fires.

Exit code is always 0 — a broken reminder must never break a session.
"""

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# harness-version: 0.6.0
HARNESS_VERSION = "0.6.0"

DEFAULT_LOG_FILES = ["log-devtools.md"]

# A dated entry heading: "## 2026-08-01 — ...". The date may sit anywhere in the
# heading text so a session that writes "## Session of 2026-08-01" still counts.
_HEADING_RE = re.compile(r"^#{2,}\s+(?P<text>.*)$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Game-agnostic: match by file type and by the harness's own directories rather than
# by folder names like "scenes/" or "sprites/", which differ per project.
CODE_SUFFIXES = {
    ".gd", ".cs", ".tscn", ".tres", ".res", ".gdshader", ".shader",
    ".gdextension", ".import", ".cfg",
}
CODE_PATHS = ("addons/", "devtools_ext/", "tools/", "test/")
CODE_FILES = ("project.godot",)

GRACE = 10  # seconds; git must never hang a Stop hook


def _git(root, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=GRACE,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _changed_files(root):
    """Every file this working session could plausibly have touched.

    Union of (a) the uncommitted working tree and (b) commits on this branch that
    are not on the base branch. (b) matters because the log entry and the code often
    land in different commits of the same session.
    """
    changed = set()

    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return None  # not a git repo, or git unavailable -> stay silent
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:  # renames: "old -> new"
            path = path.split(" -> ", 1)[1]
        if path:
            changed.add(path.strip('"'))

    base = None
    for ref in ("origin/main", "main", "origin/master", "master"):
        merge_base = _git(root, "merge-base", "HEAD", ref)
        if merge_base and merge_base.strip():
            base = merge_base.strip()
            break
    if base:
        diff = _git(root, "diff", "--name-only", base, "HEAD")
        if diff:
            changed.update(p for p in (l.strip() for l in diff.splitlines()) if p)

    return changed


def _is_code(path):
    p = path.replace("\\", "/")
    if p.endswith(".md"):
        return False
    if os.path.basename(p) in CODE_FILES:
        return True
    if Path(p).suffix.lower() in CODE_SUFFIXES:
        return True
    return any(seg in p for seg in CODE_PATHS)


def _load_config(root):
    cfg_path = root / "addons" / "godot_selftest" / "devtools_config.json"
    defaults = (DEFAULT_LOG_FILES, [], False, True)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    logs = cfg.get("log_files") or DEFAULT_LOG_FILES
    extra = cfg.get("log_check_globs") or []
    block = bool(cfg.get("log_check_block", False))
    dated = bool(cfg.get("log_check_dated_entry", True))
    return list(logs), list(extra), block, dated


def _entry_dates(path):
    """Every date appearing in a `## ` heading of the log, newest-looking last.

    Returns None when the file cannot be read at all, which is a different
    finding ("no log yet") from an unreadable set of headings.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    dates = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if not m:
            continue
        d = _DATE_RE.search(m.group("text"))
        if d:
            dates.append(d.group(0))
    return dates


def _log_status(root, rel_path, changed_normalized, require_dated):
    """Why this log file is (or isn't) compliant, as a short phrase or None."""
    if not require_dated:
        # Legacy behavior: any change to the file counts.
        return None if rel_path in changed_normalized else "was not updated this session"

    dates = _entry_dates(root / rel_path)
    if dates is None:
        return "does not exist yet"

    today = datetime.date.today().isoformat()
    if today in dates:
        return None

    if not dates:
        return "has no dated `## ` entry heading at all"
    newest = max(dates)
    touched = " (the file changed, but not with a new entry)" if rel_path in changed_normalized else ""
    return "has no entry dated %s - its newest is %s%s" % (today, newest, touched)


def main():
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    log_files, extra_paths, block, require_dated = _load_config(root)

    changed = _changed_files(root)
    if not changed:
        return  # nothing changed, or not a git repo

    def is_code(path):
        return _is_code(path) or any(
            seg in path.replace("\\", "/") for seg in extra_paths
        )

    if not any(is_code(p) for p in changed):
        return  # docs-only turn; the convention still applies but is not enforceable here

    normalized = {p.replace("\\", "/") for p in changed}
    problems = []
    for f in log_files:
        why = _log_status(root, f, normalized, require_dated)
        if why:
            problems.append("%s %s" % (f, why))
    if not problems:
        return

    msg = (
        "Devtools log reminder: this session changed Godot code, but "
        + "; ".join(problems)
        + ". Append an entry dated %s describing any gaps in /verify or the devtools "
        "harness that would have helped with this task - each with a `- [G-NNN] status: "
        "open | seen: 1 | harness: <version>` line and a suggested improvement - or an "
        "explicit 'no gaps this turn' line under today's heading. Bump `seen:` on an "
        "existing gap rather than filing a duplicate. See the file's own Format section."
        % datetime.date.today().isoformat()
    )

    if block:
        print(json.dumps({"decision": "block", "reason": msg}))
    else:
        print(json.dumps({"systemMessage": msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never break a session over a reminder
        print(f"check_devtools_log: skipped ({exc})", file=sys.stderr)
    sys.exit(0)
