#!/usr/bin/env python
"""What the harness's consumers are actually RUNNING (H-081, gh#68.1).

Every improvement in this repo is scored on being implemented. None has ever been
scored on being adopted, and the first time anyone looked -- 2026-08-20, at loop tick
32 -- every one of five consumer projects was between 20 and 53 releases behind, three
of them under heavy active development (886 commits that month on a 25-release-old
harness). `PURPOSE.md` has said since early on that "a fix is delivered when the project
runs it, not when it ships"; nothing measured delivery, so a 53-release gap accumulated
in silence.

This is that measurement, and it is deliberately cheap: it reads a version stamp and a
git log. No engine, no bus, no project opened. Run it every release and put the line in
the log entry, so adoption is a visible series rather than something discovered by
accident.

    python tools/adoption.py                       # auto-discover sibling projects
    python tools/adoption.py ../plant ../gather    # or name them
    python tools/adoption.py --drift               # also ask what a refresh would cost

Always exits 0. This reports; it does not gate. A release is not wrong because its
consumers are behind -- but it should not be able to avoid knowing.
"""

# No `harness-version:` stamp on purpose: this is a repo tool, not a shipped
# template, so nothing in record_version.py maintains it and a stamp here would
# silently go stale - a version number that lies is worse than none.

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STAMP_RE = re.compile(r'HARNESS_VERSION\s*:\s*String\s*=\s*"([^"]+)"')


def _version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (TypeError, ValueError):
        return ()


def current_version():
    try:
        return json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json")
                          .read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return None


def releases_between(older, newer):
    """How many recorded releases sit between two versions, from harness_history.json."""
    try:
        hist = json.loads((REPO_ROOT / "harness_history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lo, hi = _version_tuple(older), _version_tuple(newer)
    if not lo or not hi:
        return None
    return sum(1 for v in hist if lo < _version_tuple(v) <= hi)


# Three distinct answers, and the whole point is that they never collapse into each
# other. `None` = no harness installed at all (nothing to be behind). `UNSTAMPED` = a
# dev_tools.gd exists but carries no version constant, which predates the stamp and is
# therefore OLDER than any number here, not newer. A version string = a real reading.
UNSTAMPED = "unstamped"


def installed_version(project: Path):
    """The addon's own stamp, `UNSTAMPED`, or None when there is no harness at all.

    The distinction earned itself twice. First: an unstamped install printed as "up to
    date", because `behind` was None and None is falsy. Then the check written to catch
    THAT found this one - a directory with no harness in it was also reporting
    `version UNKNOWN`, which reads as "installed, can't tell" rather than "not a
    consumer at all". A project that never installed the harness is not part of an
    adoption number in either direction.
    """
    f = project / "addons" / "godot_selftest" / "dev_tools.gd"
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = STAMP_RE.search(text)
    return m.group(1) if m else UNSTAMPED


def git_activity(project: Path):
    """(last commit date, commits in the last 30 days). A stale install on a dormant
    project is a footnote; a stale install on a project committing daily is the finding,
    and the two look identical without this."""
    def run(args):
        try:
            p = subprocess.run(["git"] + args, cwd=str(project), capture_output=True,
                               text=True, timeout=20)
            return p.stdout.strip() if p.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    last = run(["log", "-1", "--format=%ad", "--date=short"])
    recent = run(["log", "--since=30 days ago", "--oneline"])
    return (last or "unknown"), (len(recent.splitlines()) if recent else 0)


def discover(explicit):
    if explicit:
        return [Path(p).resolve() for p in explicit]
    parent = REPO_ROOT.parent
    found = []
    try:
        for child in sorted(parent.iterdir()):
            if child.resolve() == REPO_ROOT:
                continue
            if (child / "addons" / "godot_selftest" / "dev_tools.gd").is_file():
                found.append(child.resolve())
    except OSError:
        pass
    return found


def drift_verdict(project: Path):
    """One word from `harness-drift`, run from THIS working tree's client.

    Run from the plugin on purpose: the subcommand only exists from 0.63.0, so asking
    the project's own copy would fail on exactly the installs worth asking about. That
    is the same bootstrap trap the 0.64.0 release fixed in `commands/verify.md`.
    """
    tool = REPO_ROOT / "templates" / "tools" / "devtools.py"
    try:
        p = subprocess.run(
            [sys.executable, str(tool), "--project", str(project), "harness-drift"],
            capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT)})
    except (OSError, subprocess.SubprocessError) as exc:
        return "error (%s)" % exc
    if p.returncode == 0:
        return "refresh is LOSSLESS"
    if p.returncode == 2:
        return "could not tell"
    m = re.search(r"(\d+) carry local line\(s\)", p.stdout)
    return "%s file(s) carry local lines" % (m.group(1) if m else "some")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("projects", nargs="*",
                    help="Project dirs (default: siblings of this repo with a harness)")
    ap.add_argument("--drift", action="store_true",
                    help="Also run harness-drift per project (slower; asks what a "
                         "refresh would actually cost)")
    ap.add_argument("--json", action="store_true", help="Machine-readable")
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 when an ACTIVE consumer is far behind. Turns the "
                         "adoption number into a gate on THIS repo's release process - "
                         "never on the consumers, who are not doing anything wrong.")
    ap.add_argument("--max-behind", type=int, default=10,
                    help="Releases an active consumer may lag before --check fails "
                         "(default 10)")
    args = ap.parse_args()

    current = current_version()
    projects = discover(args.projects)
    if not projects:
        # Not a silent empty table: "nobody installed it" and "I looked nowhere" are
        # different answers and only one of them is about adoption.
        print("adoption: no sibling project with addons/godot_selftest/dev_tools.gd "
              "found next to %s. That is 'nothing to measure here', not 'zero lag' - "
              "name the projects explicitly if they live elsewhere." % REPO_ROOT.parent,
              file=sys.stderr)
        return 0

    rows = []
    for p in projects:
        v = installed_version(p)
        behind = releases_between(v, current) if (v and v != UNSTAMPED) else None
        last, recent = git_activity(p)
        row = {"project": p.name, "installed": v, "current": current,
               "behind": behind, "last_commit": last, "commits_30d": recent}
        if args.drift:
            row["drift"] = drift_verdict(p)
        rows.append(row)

    if args.json:
        print(json.dumps({"current": current, "projects": rows}, indent=2))
        return 0

    width = max(len(r["project"]) for r in rows)
    print("Harness adoption (this repo: %s)" % current)
    for r in rows:
        # THREE states, not two. The first version of this printed "up to date" for a
        # project whose version could not be read at all, because `behind` was None and
        # None is falsy - a well-formed reassuring answer for the one state that is not
        # reassuring, which is the exact failure this repo exists to prevent. An
        # unstamped dev_tools.gd predates the stamp (pre-0.6.0) and is the OLDEST thing
        # here, not the newest.
        if r["installed"] is None:
            behind = "NO HARNESS"
        elif r["installed"] == UNSTAMPED:
            behind = "version UNKNOWN"
        elif r["behind"]:
            behind = "%d behind" % r["behind"]
        else:
            behind = "up to date"
        line = "  %-*s  %-9s  %-15s  last commit %s, %d in 30d" % (
            width, r["project"], r["installed"] or "-", behind,
            r["last_commit"], r["commits_30d"])
        if "drift" in r:
            line += "  |  %s" % r["drift"]
        print(line)

    none = [r for r in rows if r["installed"] is None]
    unknown = [r for r in rows if r["installed"] == UNSTAMPED]
    consumers = [r for r in rows if r["installed"] is not None]
    stale = [r for r in rows if r["behind"]]
    active_stale = [r for r in stale if r["commits_30d"] >= 20]
    worst = max((r["behind"] for r in stale), default=0)
    if not consumers:
        print("No project here has the harness installed - there is no adoption number "
              "to report. That is 'not a consumer', not 'up to date'.")
        return 0
    print("%d of %d project(s) with the harness are behind (worst: %d releases); "
          "%d of those are ACTIVE (20+ commits in 30 days)."
          % (len(stale), len(consumers), worst, len(active_stale)))
    if unknown:
        print("  %d project(s) carry a dev_tools.gd with NO version stamp (%s) - that "
              "predates the stamp itself, so they are older than every number above, "
              "not newer." % (len(unknown), ", ".join(r["project"] for r in unknown)))
    if none:
        print("  %d named path(s) have no harness at all (%s) - excluded from the "
              "numbers above rather than counted as current."
              % (len(none), ", ".join(r["project"] for r in none)))
    if active_stale:
        # The number that matters, said as a sentence rather than left to be inferred
        # from a column: an active project on an old harness is friction this repo will
        # never hear about, because its gaps are all long since fixed upstream.
        print("  Actively developed and stale: %s. Gaps these projects hit are filed "
              "against versions this repo no longer ships, so their friction does not "
              "reach the log." % ", ".join(r["project"] for r in active_stale))

    if args.check:
        # A gate on the RELEASE, not on the consumers. Tick 32 measured the gap and
        # wrote it in the log; tick 33 measured it again and every number was one worse,
        # because a number recorded in a file nobody re-reads is not a feedback loop.
        # This one interrupts.
        failing = [r for r in active_stale if r["behind"] > args.max_behind]
        if failing:
            print("\nadoption --check FAILED: %d actively developed project(s) are more "
                  "than %d releases behind: %s.\nThis does not mean the release is wrong. "
                  "It means shipping it will not reach anyone, and the cheapest thing "
                  "this repo can do next is make one of those refresh rather than add a "
                  "capability to a version nobody runs."
                  % (len(failing), args.max_behind,
                     ", ".join("%s (%d)" % (r["project"], r["behind"]) for r in failing)),
                  file=sys.stderr)
            return 1
        print("\nadoption --check OK: no actively developed consumer is more than %d "
              "releases behind." % args.max_behind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
