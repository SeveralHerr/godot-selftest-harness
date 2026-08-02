#!/usr/bin/env python3
"""Append-only record of what every `/verify` run actually did.

Installed by `/scaffold-godot-harness`; written by Phase 5 of `/verify`; read by
whoever wants to know whether the harness is earning its keep.

**Why this exists.** `log-devtools.md` records what the harness *couldn't* do. It has
no denominator: thirty gap entries tell you the harness was in the way thirty times,
not whether that was out of forty runs or four hundred. This file is the denominator —
one line per run, including the boring green ones, which are exactly the runs nobody
would otherwise write down.

**The field that matters is `reached`.** `verify.md` already tells a run to say so when
a changed script has no reachable entry point, but that confession lands in a chat
transcript and evaporates. Here it is computed instead of claimed: the scene-tree
snapshot carries each node's `script` and `scene_file` (harness 0.6.0+), so reach is a
set intersection against the diff rather than a self-assessment by the thing being
measured. A green run that never reached the changed code is the failure mode this
whole harness exists to make visible, and it is invisible in a pass/fail summary.

Two subcommands:

    python3 tools/verify_ledger.py record --scene-tree tree.json --run run.json
    python3 tools/verify_ledger.py stats

`record` derives the mechanical fields itself (timestamp, sha, branch, changed files,
reach) and takes only the run-specific ones it cannot know — runner exit codes, the
Phase 4 checks, duration — as a JSON object from `--run` (or stdin). Trust boundary:
anything derivable is derived, so a run can misreport its own checks but cannot
misreport whether it touched the diff.

`stats` aggregates. Reach rate is the headline; it is also broken out per harness
version, which is what tells you whether a release actually improved anything.

Exit codes: 0 fine, 1 bad input, 2 nothing to report (no ledger yet).
"""

import argparse
import datetime
import json
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# harness-version: 0.6.0
HARNESS_VERSION = "0.6.0"

LEDGER_PATH = Path(".devtools") / "verify-runs.jsonl"

# Extensions whose reach we can actually establish from a scene-tree snapshot: a .gd
# shows up as a node's `script`, a .tscn as an instanced node's `scene_file`. Anything
# else (a .cfg, a shader, project.godot) is recorded as changed but excluded from the
# reach ratio rather than silently counted as a miss - an unreachable-by-construction
# file dragging the rate down would make the number mean less, not more.
REACHABLE_SUFFIXES = {".gd", ".tscn"}

GRACE = 10  # seconds; git must never hang the tail end of a verify run


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


def _norm(path):
    """A single spelling for a path, so res:// and git agree.

    Strips a leading `./` as a whole prefix, never as a character class: `lstrip("./")`
    eats the dot off `.devtools/x` and `.lint-baseline.json`, which would leave a
    dot-directory path unable to match anything and silently score as unreached.
    """
    p = str(path).replace("\\", "/")
    if p.startswith("res://"):
        p = p[len("res://"):]
    while p.startswith("./"):
        p = p[2:]
    return p


def changed_files(root):
    """Files this run could plausibly have been verifying.

    Union of the uncommitted working tree and commits on this branch not on the base -
    the same rule `check_devtools_log.py` uses, for the same reason: the change and the
    thing that records it often land in different commits.
    """
    changed = set()

    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return None  # not a git repo -> reach is not computable, and says so
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:  # renames: "old -> new"
            path = path.split(" -> ", 1)[1]
        if path:
            changed.add(_norm(path.strip('"')))

    base = None
    for ref in ("origin/main", "main", "origin/master", "master"):
        merge_base = _git(root, "merge-base", "HEAD", ref)
        if merge_base and merge_base.strip():
            base = merge_base.strip()
            break
    if base:
        diff = _git(root, "diff", "--name-only", base, "HEAD")
        if diff:
            changed.update(_norm(p) for p in (l.strip() for l in diff.splitlines()) if p)

    return changed


def _walk(node, out):
    """Every script and scene path appearing anywhere in a scene-tree snapshot."""
    if not isinstance(node, dict):
        return
    for key in ("script", "scene_file"):
        val = node.get(key)
        if val:
            out.add(_norm(val))
    for child in node.get("children") or []:
        _walk(child, out)


def load_snapshots(paths):
    """Union of the paths present across scene-tree captures.

    A union rather than one capture because a run's reach is cumulative: a node that
    existed when the playable scene came up may be freed by the time the last test
    finishes, and one spawned mid-test never appears in an early capture. Taking one
    snapshot would undercount reach and quietly slander the harness.

    None when no capture could be read at all - "we could not tell" must stay distinct
    from "it reached nothing".
    """
    seen = set()
    ok = False
    for path in paths or []:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("verify_ledger: unreadable scene-tree snapshot %s (%s)" % (path, exc),
                  file=sys.stderr)
            continue
        ok = True
        # Accept either a bare tree or a full {success, message, data} envelope, since
        # `devtools.py scene-tree` prints the data and a raw capture might not.
        _walk(raw.get("data") if isinstance(raw, dict) and "data" in raw else raw, seen)
    return seen if ok else None


def compute_reach(changed, snapshot_paths):
    """(reached, unreached, skipped) over the changed files reach can speak to."""
    candidates = sorted(p for p in changed if Path(p).suffix.lower() in REACHABLE_SUFFIXES)
    skipped = sorted(p for p in changed if Path(p).suffix.lower() not in REACHABLE_SUFFIXES)
    if snapshot_paths is None:
        return None, None, skipped
    reached = [p for p in candidates if p in snapshot_paths]
    unreached = [p for p in candidates if p not in snapshot_paths]
    return reached, unreached, skipped


def _load_run(args):
    if args.run:
        try:
            return json.loads(Path(args.run).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("verify_ledger: cannot read --run %s (%s)" % (args.run, exc),
                  file=sys.stderr)
            return None
    data = sys.stdin.read().strip()
    if not data:
        return {}
    try:
        return json.loads(data)
    except ValueError as exc:
        print("verify_ledger: stdin is not JSON (%s)" % exc, file=sys.stderr)
        return None


def cmd_record(args, root):
    run = _load_run(args)
    if run is None:
        return 1
    if not isinstance(run, dict):
        print("verify_ledger: run payload must be a JSON object", file=sys.stderr)
        return 1

    changed = changed_files(root)
    snapshot = load_snapshots(args.scene_tree)
    reached, unreached, skipped = compute_reach(changed or set(), snapshot)

    sha = _git(root, "rev-parse", "--short", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")

    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc)
                  .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "harness": run.get("harness") or HARNESS_VERSION,
        "sha": (sha or "").strip() or None,
        "branch": (branch or "").strip() or None,
        "changed": sorted(changed) if changed is not None else None,
        "verdict": run.get("verdict", "unknown"),
        "lint": run.get("lint"),
        "tests": run.get("tests"),
        "runtime": run.get("runtime"),
        "checks": run.get("checks") or [],
        "duration_s": run.get("duration_s"),
        "reach": {
            # null (not []) when no snapshot was supplied: "we could not tell" is a
            # different fact from "it reached nothing", and conflating them is exactly
            # the well-formed-zeros failure this harness is built to avoid.
            "reached": reached,
            "unreached": unreached,
            "not_applicable": skipped,
        },
    }

    path = root / LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")

    if reached is None:
        detail = "reach not computed (no scene-tree snapshot)"
    else:
        total = len(reached) + len(unreached)
        detail = "reached %d/%d changed file(s)" % (len(reached), total)
        if unreached:
            detail += "; NOT reached: " + ", ".join(unreached)
    print("verify_ledger: recorded %s run - %s" % (row["verdict"], detail))
    return 0


def _iter_rows(path):
    bad = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            bad += 1
    if bad:
        print("verify_ledger: skipped %d unparseable row(s)" % bad, file=sys.stderr)


def _pct(num, denom):
    return "n/a" if not denom else "%.0f%%" % (100.0 * num / denom)


def cmd_stats(args, root):
    path = root / LEDGER_PATH
    if not path.exists():
        print("No ledger yet at %s - it is written by /verify Phase 5." % LEDGER_PATH)
        return 2

    rows = list(_iter_rows(path))
    if not rows:
        print("Ledger at %s is empty." % LEDGER_PATH)
        return 2

    verdicts = defaultdict(int)
    reached_n = unreached_n = 0
    per_version = defaultdict(lambda: [0, 0])
    runtime_findings = 0
    static_only_findings = 0
    no_snapshot = 0
    durations = []

    for row in rows:
        verdicts[row.get("verdict", "unknown")] += 1

        reach = row.get("reach") or {}
        r, u = reach.get("reached"), reach.get("unreached")
        if r is None:
            no_snapshot += 1
        else:
            reached_n += len(r)
            unreached_n += len(u or [])
            slot = per_version[row.get("harness") or "?"]
            slot[0] += len(r)
            slot[1] += len(r) + len(u or [])

        failed_checks = [c for c in (row.get("checks") or [])
                         if c.get("result") == "fail"]
        runtime = row.get("runtime") or {}
        if failed_checks or runtime.get("orphan_growth_exceeded"):
            runtime_findings += 1
        else:
            lint = row.get("lint") or {}
            tests = row.get("tests") or {}
            if lint.get("new") or tests.get("failed"):
                static_only_findings += 1

        if isinstance(row.get("duration_s"), (int, float)):
            durations.append(row["duration_s"])

    total = len(rows)
    print("runs: %d  (%s)" % (
        total,
        " | ".join("%s %d" % (k, v) for k, v in sorted(verdicts.items())) or "no verdicts",
    ))

    denom = reached_n + unreached_n
    print("reach: %s of changed reachable files exercised at runtime (%d/%d)"
          % (_pct(reached_n, denom), reached_n, denom))
    if no_snapshot:
        print("       %d run(s) recorded no snapshot - reach unknown, not counted" % no_snapshot)

    if len(per_version) > 1 or args.verbose:
        print("reach by harness version:")
        for ver in sorted(per_version):
            got, tot = per_version[ver]
            print("  %-8s %s (%d/%d)" % (ver, _pct(got, tot), got, tot))

    print("runs where a runtime check caught something: %d (%s)"
          % (runtime_findings, _pct(runtime_findings, total)))
    print("runs where only lint/tests caught something: %d" % static_only_findings)
    if durations:
        print("duration: median %.0fs, total %.0f min"
              % (statistics.median(durations), sum(durations) / 60.0))

    aborted = verdicts.get("aborted", 0)
    if aborted:
        print("\n%d run(s) verified nothing (exit 2). Those are not passes." % aborted)
    if denom and reached_n < denom:
        print("\nThe %d unreached file(s) are the harness's blind spot - a green /verify "
              "on those was a statement about the diff, not the running game."
              % (denom - reached_n))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Append-only ledger of /verify runs, and stats over it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Two subcommands:")[-1],
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("record", help="Append one run to the ledger")
    p.add_argument("--scene-tree", metavar="FILE", action="append",
                   help="A `devtools.py scene-tree` capture. Repeatable; reach is the "
                        "union across captures. Without any, reach is recorded as "
                        "unknown rather than guessed.")
    p.add_argument("--run", metavar="FILE",
                   help="JSON object of this run's results. Default: read stdin.")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("stats", help="Aggregate the ledger")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Always break reach out per harness version")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    return args.func(args, root)


if __name__ == "__main__":
    sys.exit(main() or 0)
