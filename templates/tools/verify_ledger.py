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
snapshot carries each node's `script` and `scene_file` (harness 0.6.0+), and the
`scripts-seen` bridge verb (0.8.0+) accumulates every script ever attached to any node,
so reach is a set intersection against the diff rather than a self-assessment by the
thing being measured. A green run that never reached the changed code is the failure
mode this whole harness exists to make visible, and it is invisible in a pass/fail
summary.

**Two denominators.** "Changed" used to be one set: worktree edits unioned with the
branch's commits. The union is kept (old rows stay comparable), but the two are now
also computed apart: the *worktree* ratio is the honest per-session number — what this
session edited and whether this run loaded it — while the *branch* ratio dilutes one
run's contribution as the branch accumulates commits nobody expects a single run to
re-exercise.

**Implicit reach.** Autoload scripts (from `project.godot` `[autoload]`) and the
DevTools `extension_script` run in every launched session but own no persistent node a
snapshot can see. A changed file in that set that was not otherwise observed lands in
`reached_implicit` — credited as running, kept out of `unreached`, and deliberately not
folded into `reached`, which stays what was *observed*.

**Three things that were being scored as misses and are not.** A metric that reports a
file as unreached when it demonstrably ran does not merely lose accuracy — it teaches
its readers to discount the number, which is worse than not printing it.

* *Unit tests.* Scripts under the configured `test_dir` ran in Phase 1 and are
  structurally incapable of appearing in a scene-tree snapshot of a game session. They
  were being counted in the denominator anyway, so writing a test alongside a fix
  permanently capped the ratio below 100% — the exact opposite of the intended
  incentive. They now join `not_applicable` (sub-list `test_scripts`), the same bucket
  their own `.uid` sidecars were already landing in. Excused, not credited: the ledger
  cannot see Phase 1's results from here, so it does not claim they passed.
* *Headless tools.* Scripts under `reach_headless_dirs` (default `["tools/"]`) only ever
  run as `godot --headless --script res://tools/x.gd`. There is no node for them to be
  the `script` of, so they can never register in a snapshot no matter how thoroughly
  they ran — `lint_project.gd` and `run_tests.gd` were being charged as misses by the
  very runs they had just executed. Same bucket (`headless_tools`), same terms: excused,
  not credited. `addons/` is deliberately not covered — `dev_tools.gd` is the autoload
  and already resolves through `reached_implicit`.
* *Non-Node scripts.* A `RefCounted` helper held as a plain field, a `Resource`
  subclass, an item model — none of them is ever a node's `script`, so no amount of
  exercising them can register. A project can declare the observed Node that vouches
  for one via `reach_aliases` in `devtools_config.json` (see `_reach_aliases`). Those
  land in `reached_alias`, with the voucher recorded alongside in `reached_alias_via`,
  and never in `reached` — a declaration is a project's claim, not an observation, and
  the whole value of this field is that it does not blur the two.

**Reach has three states, not two.** `reached N/M` is one; `reach not computed` (no
capture) is the second; and `reach: unavailable (not a git repository)` is the third,
added in 0.17.0 after a project with no `.git` was handed `reached 0/0 changed file(s)`
and a ledger row saying `reached: [], unreached: []` (moving-in:G-003). Its coverage
that run was in fact good, and nothing said so; worse, a row went into the permanent
record claiming a denominator it never had. A 0/0 reads as "nothing to check" and is
indistinguishable from a clean sweep, which is the exact failure this file exists to
prevent one level down. So the no-VCS case now carries `changed_unavailable: true` and
null buckets - the same null-means-unknown shape `stats` and older readers already skip
rather than average - and prints a line that cannot be mistaken for a score.

**The other half is whether it was worth running at all.** Reach says the harness did
something; it cannot say the something was needed. A log that only records gaps can only
ever recommend more harness — it has no vocabulary for *this task didn't need the tool*,
so a harness that is the wrong choice for half its runs would produce a tidy stream of
feature requests and never once suggest being used less. Hence `value` (one of
`warranted` / `overkill` / `insufficient` / `inconclusive`) and `cheaper_alternative`,
which names what would have produced the same confidence for less.

Those two are self-reported, and self-reports about one's own usefulness bias one way.
Four things push back: the verdict is a countable enum rather than prose; `expected` is
written before the run and copied in verbatim afterwards; and `_reconcile_value()`
downgrades a `warranted` on two independent mechanical grounds — one whose changed files
were never loaded becomes `insufficient`, one whose `found` list is empty becomes
`overkill`. None of that makes the field objective. It makes it harder to inflate without
noticing.

**`found` is the field that says what the run caught**, and it exists because every other
field in a row describes the run's *final state*. A defect found four minutes in and
fixed before Phase 5 leaves no trace anywhere else: `checks` gets written green, the
runners are re-run clean, and the row reads exactly like a run that confirmed what was
already known. The first 52 rows recorded in anger showed the cost of that — 319 Phase 4
checks with not one `fail`, zero new lint findings, zero failing tests, and 98% of runs
grading themselves `warranted` on the strength of catches that appear only as prose
inside `cheaper_alternative`. Reach says the run touched the code; `found` says the run
told you something.

Three states, and the difference between the last two is the whole point:

* a non-empty list — what this run caught, one entry each;
* `[]` — **this run found nothing**, an answer rather than a silence, and the one that
  earns `overkill`;
* `null` — not recorded. Every row written before 0.10.0 is this, permanently; `stats`
  excludes them from the rate rather than scoring them as zeros.

Three subcommands:

    python3 tools/verify_ledger.py reach  --scene-tree tree.json --scripts-seen seen.json
    python3 tools/verify_ledger.py record --scene-tree tree.json --scripts-seen seen.json --run run.json
    python3 tools/verify_ledger.py stats

`record` derives the mechanical fields itself (timestamp, sha, branch, changed files,
reach) and takes only the run-specific ones it cannot know — runner exit codes, the
Phase 4 checks, duration, and the value reflection — as a JSON object from `--run` (or
stdin). Trust boundary: anything derivable is derived, so a run can misreport its own
checks but cannot misreport whether it touched the diff.

`record` **refuses** to write a row with unknown reach: with no readable `--scene-tree`
and no readable `--scripts-seen` it exits 1 and says what to pass, because a silently
null reach is the well-formed-zeros failure this file exists to prevent. `--no-reach`
is the deliberate escape hatch for aborted runs — it writes `reach: null` on purpose,
so the failure mode is a choice, not a default.

`reach` computes reach without writing a row, because the verdict depends on it: a run
that never loaded the changed file is `insufficient` however well its checks went, and
that has to be knowable before the row is written.

`stats` aggregates. Reach rate is the headline, broken out per harness version so a
release's effect is visible; the value mix and the collected `cheaper_alternative` lines
are the part that can tell you to run /verify less often.

Exit codes: 0 fine, 1 bad input, 2 nothing to report (no ledger yet).
"""

import argparse
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# harness-version: 0.27.0
HARNESS_VERSION = "0.27.0"

LEDGER_PATH = Path(".devtools") / "verify-runs.jsonl"

# Extensions whose reach we can actually establish from a scene-tree snapshot: a .gd
# shows up as a node's `script`, a .tscn as an instanced node's `scene_file`. Anything
# else (a .cfg, a shader, project.godot) is recorded as changed but excluded from the
# reach ratio rather than silently counted as a miss - an unreachable-by-construction
# file dragging the rate down would make the number mean less, not more.
REACHABLE_SUFFIXES = {".gd", ".tscn"}

CONFIG_PATH = Path("addons") / "godot_selftest" / "devtools_config.json"

# Mirrors run_tests.gd / lint_project.gd: same key, same default, and `""` means "this
# project has no test dir" rather than "use the default", so the three agree on what a
# test path is. Disagreeing here would excuse files one tool considers game code.
DEFAULT_TEST_DIR = "res://test/unit"

# Dirs whose scripts only ever run under `godot --headless --script` and so can never
# appear in a scene-tree snapshot of a game session - the same structural argument as
# test_scripts. Default mirrors uid_check_ignore's treatment of tools/ as not-game-code:
# it holds the harness's own lint_project.gd / run_tests.gd / eval.gd plus whatever
# one-off generators the project keeps there. addons/ is deliberately NOT here:
# dev_tools.gd is the autoload and already resolves through reached_implicit.
DEFAULT_HEADLESS_DIRS = ["tools/"]

GRACE = 10  # seconds; git must never hang the tail end of a verify run

# Config complaints are printed once per process, not once per denominator: split_reach
# runs three times (union/worktree/branch) and a triplicated warning reads like three
# separate faults.
_WARNED = set()


def _warn(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        print("verify_ledger: %s" % msg, file=sys.stderr)


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


def _git_present(root):
    """Whether `root` is inside a git working tree at all.

    `_git` returns None for two unrelated reasons - the command failed, and there is no
    repository for it to have succeeded in - and every caller that cannot tell them apart
    ends up reporting the second as if it were an ordinary empty result. This is the one
    probe that asks the question directly, so `changed_branch` can say "no base ref
    resolved" and "there is no git here" as two different answers (moving-in:G-003).
    """
    return _git(root, "rev-parse", "--is-inside-work-tree") is not None


def changed_worktree(root):
    """Uncommitted working-tree changes - what *this session* plausibly edited.

    None when git itself is unavailable (not a repo): reach is then not computable,
    and says so, rather than being an empty set that scores as a clean sweep.

    That None used to be coerced back to `set()` at both call sites, so a checkout with
    no `.git` reported `reached 0/0` - a line that reads as "nothing to check" when it
    means "cannot tell" - and `record` wrote the same 0/0 into the permanent ledger
    (moving-in:G-003). It is now carried through `split_reach` to the printed line and to
    the recorded row, because a check that cannot tell must not be indistinguishable from
    a check that passed.
    """
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if status is None:
        return None
    changed = set()
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:  # renames: "old -> new"
            path = path.split(" -> ", 1)[1]
        if path:
            changed.add(_norm(path.strip('"')))
    return changed


def changed_branch(root):
    """Commits on this branch not on the base - the branch's accumulated diff.

    Empty set (not None) when no base ref resolves: "nothing beyond base" is a fact,
    unlike git being absent altogether.

    That reasoning was sound and the code did not implement it. With no repository at
    all, every `_git` call here returns None, no base resolves, and the function fell out
    of the loop returning the empty set that its own docstring reserves for a *fact* -
    reporting "nothing beyond base" in a directory that has no base and no branch
    (moving-in:G-003). The distinction is now made rather than assumed: `_git_present`
    is asked first, and git being absent returns None like `changed_worktree` does. The
    empty set keeps its original meaning, and now only ever carries it.
    """
    if not _git_present(root):
        return None
    changed = set()
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


def _union_changed(worktree, branch):
    """The union denominator, or None when there was no VCS to ask.

    None only when *both* halves are None, which is the no-repository case: if git
    answered one question and failed the other, we still know something changed and the
    honest union is what it told us. Written once because both `record` and `reach`
    need it and they used to spell it differently - `record` returned None, `reach`
    coerced to `set()`, and the two disagreed about the same checkout.
    """
    if worktree is None and branch is None:
        return None
    return (worktree or set()) | (branch or set())


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


def load_scripts_seen(paths):
    """Union of script paths from `devtools.py scripts-seen` captures.

    The bridge verb accumulates every script resource_path ever attached to any node
    (initial walk + node_added hook), which catches the case snapshots structurally
    miss: a script whose node lived and died between captures. Accepts the full reply
    envelope ({"success":..., "data": {"scripts": [...]}}), a bare {"scripts": [...]},
    or a bare list. None when nothing could be read - same distinction as snapshots.
    """
    seen = set()
    ok = False
    for path in paths or []:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("verify_ledger: unreadable scripts-seen capture %s (%s)" % (path, exc),
                  file=sys.stderr)
            continue
        scripts = None
        if isinstance(raw, dict):
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            if isinstance(data.get("scripts"), list):
                scripts = data["scripts"]
        elif isinstance(raw, list):
            scripts = raw
        if scripts is None:
            print("verify_ledger: %s has no 'scripts' list - ignored" % path,
                  file=sys.stderr)
            continue
        ok = True
        seen.update(_norm(s) for s in scripts if s)
    return seen if ok else None


def load_config(root):
    """The project's devtools_config.json as a dict, or `{}`.

    Every read of it is a `.get` with a default, because a config written by an older
    scaffolder legitimately predates any key added since - a missing key must degrade to
    the old behavior, never to a crash in the middle of a verify run.
    """
    try:
        cfg = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _test_dir(cfg):
    """Repo-relative test dir, `""` if the project declares it has none."""
    raw = cfg.get("test_dir", DEFAULT_TEST_DIR)
    if not isinstance(raw, str):
        _warn("devtools_config.json test_dir is not a string - using %s"
              % DEFAULT_TEST_DIR)
        raw = DEFAULT_TEST_DIR
    return _norm(raw).rstrip("/")


def _under(path, folder):
    """Prefix match on whole path segments; `test/unit` must not swallow `test/unity`."""
    return bool(folder) and (path == folder or path.startswith(folder + "/"))


def _headless_dirs(cfg):
    """Repo-relative dirs whose scripts only ever run under `--headless --script`.

    `[]` if the project declares it has none. Default `["tools/"]` mirrors
    `uid_check_ignore`, which already treats `tools/` as not-game-code: it is where
    the scaffolder installs `lint_project.gd`, `run_tests.gd` and `eval.gd`, and where
    projects put their own one-off generators.
    """
    raw = cfg.get("reach_headless_dirs", DEFAULT_HEADLESS_DIRS)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        _warn("devtools_config.json reach_headless_dirs is not a list - using %s"
              % DEFAULT_HEADLESS_DIRS)
        raw = DEFAULT_HEADLESS_DIRS
    out = []
    for entry in raw:
        if not isinstance(entry, str):
            _warn("devtools_config.json reach_headless_dirs entry %r is not a string "
                  "- ignored" % (entry,))
            continue
        folder = _norm(entry).rstrip("/")
        if folder:
            out.append(folder)
    return out


def _reach_aliases(cfg):
    """`{script: (voucher, ...)}` - who has to be observed for a script to be credited.

    Reach can only see a script that is some node's `script` or some node's
    `scene_file`. A `RefCounted` helper held as a plain field, a `Resource` subclass, an
    item model - all of them can run for the whole session and register nothing. This is
    the project's declaration of that relationship:

        "reach_aliases": {
          "world/tile_path_finder.gd": ["world/tile_scenes/bone_worker.gd"],
          "items/types.gd":            ["items/items.gd", "ui/inventory_panel.gd"]
        }

    A single string is accepted where a list is meant. Keys and vouchers are `_norm`ed,
    so `res://` spellings work. The vouchers are OR-ed: the first one that was observed
    (or is itself implicit - an autoload that always runs can honestly vouch) credits
    the script, and *is recorded*, so a reader can see which claim was leaned on.

    A voucher that was not itself reached credits nothing. That is the entire integrity
    of the mechanism: an alias converts an observation about a Node into a statement
    about the non-Node it owns, and with no observation there is nothing to convert.

    `"*"` is accepted and means "credited whenever the run observed anything at all" -
    for code that genuinely runs on every launch. It is deliberately the weakest form,
    since nothing can falsify it; prefer `extension_script` or an autoload, which
    `implicit_scripts()` credits with no per-project configuration at all. Aliases do
    not chain: a voucher must be observed, not itself alias-credited.
    """
    raw = cfg.get("reach_aliases")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn("devtools_config.json reach_aliases is not an object - ignored")
        return {}
    out = {}
    for key, val in raw.items():
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            _warn("reach_aliases[%r] is neither a string nor a list - ignored" % key)
            continue
        vouchers = tuple(v if v == "*" else _norm(v)
                         for v in val if isinstance(v, str) and v.strip())
        if vouchers:
            out[_norm(key)] = vouchers
        else:
            _warn("reach_aliases[%r] names no voucher - ignored" % key)
    return out


def implicit_scripts(root, cfg=None):
    """Code that runs in any launched session but owns no persistent node.

    Two known sources: autoload scripts declared in project.godot's `[autoload]`
    section (values like `"*res://path.gd"` - the `*` marks singleton enablement), and
    the DevTools `extension_script` from devtools_config.json, which the bridge loads
    on startup. A changed file here *did* run; the snapshot just cannot testify to it,
    so it is credited as `reached_implicit` rather than counted unreached - and never
    folded into `reached`, which stays strictly what was observed.
    """
    out = set()
    try:
        lines = (root / "project.godot").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    in_autoload = False
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            in_autoload = (s == "[autoload]")
            continue
        if in_autoload and "=" in s and not s.startswith(";"):
            val = s.split("=", 1)[1].strip().strip('"')
            if val.startswith("*"):
                val = val[1:]
            if val:
                out.add(_norm(val))
    cfg = load_config(root) if cfg is None else cfg
    ext = cfg.get("extension_script") or ""
    if isinstance(ext, str) and ext:
        out.add(_norm(ext))
    return out


_EXTENDS_RE = re.compile(r'^\s*extends\s+(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))', re.M)
_CLASS_NAME_RE = re.compile(r'^\s*class_name\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)


def _script_class_index(root):
    """`class_name` -> normalized script path, for resolving `extends Foo`.

    Reads .godot/global_script_class_cache.cfg when the project has been imported;
    otherwise scans the project's .gd files for `class_name` lines, which is the
    same declaration the cache is built from.
    """
    out = {}
    cache = root / ".godot" / "global_script_class_cache.cfg"
    if cache.is_file():
        try:
            text = cache.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for m in re.finditer(r'"class":\s*&?"([A-Za-z_][A-Za-z0-9_]*)"[^}]*?"path":\s*"([^"]+)"', text):
            out[m.group(1)] = _norm(m.group(2))
    if out:
        return out
    for path in root.rglob("*.gd"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".godot/") or rel.startswith("addons/"):
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        m = _CLASS_NAME_RE.search(head)
        if m:
            out[m.group(1)] = _norm(rel)
    return out


def _extends_chain(script_rel, root, class_index, _seen=None):
    """Every script ancestor of `script_rel` (normalized paths), nearest first.

    Static read of the `extends` line: a quoted `res://` path, or a `class_name`
    resolved through the class index. Stops at an engine class, an unresolvable
    name, a missing file, or a cycle. Only the first `extends` in a file counts -
    inner classes have their own and are not the file's base.
    """
    seen = _seen if _seen is not None else set()
    out = []
    current = script_rel
    while current and current not in seen:
        seen.add(current)
        try:
            text = (root / current).read_text(encoding="utf-8", errors="replace")
        except OSError:
            break
        m = _EXTENDS_RE.search(text)
        if not m:
            break
        if m.group(1):
            base = _norm(m.group(1))
        else:
            base = class_index.get(m.group(2))
        if not base:
            break
        out.append(base)
        current = base
    return out


def _base_credits(observed, root, changed_rest):
    """{changed script: the observed descendant that credits it} (gh#15.3).

    A node reports only the script attached to it, never its ancestry, so a base
    class was scored unreached whenever only subclasses were live - `_draw()` on
    the base ran every frame and reach said NOT reached. This walks each observed
    script's `extends` chain and credits every ancestor that is in the changed set.
    Distinct bucket (`reached_base`), so an inherited credit stays distinguishable
    from a direct observation the way `reached_alias` and `reached_implicit` do.
    """
    wanted = set(changed_rest)
    if not wanted or not observed:
        return {}
    class_index = _script_class_index(root)
    via = {}
    for obs in sorted(observed):
        if not obs.endswith(".gd"):
            continue
        for ancestor in _extends_chain(obs, root, class_index):
            if ancestor in wanted and ancestor not in via:
                via[ancestor] = obs
    return via


def _vouched_by(path, aliases, observed, vouching):
    """The first declared voucher for `path` that actually ran, or None."""
    for v in aliases.get(path, ()):
        if v == "*":
            if observed:  # a run that observed nothing vouches for nothing
                return "*"
            continue
        if v in vouching:
            return v
    return None


def split_reach(changed, observed, implicit, root, cfg=None):
    """Classify one changed-set against what the run observed.

    Returns a dict of sorted lists:
      reached          - observed at runtime (snapshot or scripts-seen)
      reached_implicit - not observed, but autoload/extension code that runs in any
                         launched session; excluded from unreached, not added to reached
      reached_alias    - not observed, but a `reach_aliases` voucher for it was; same
                         treatment as implicit, and likewise never folded into reached
      reached_alias_via- {script: the voucher that credited it}, so the claim is
                         auditable rather than an anonymous bump in a count
      reached_base     - not observed directly, but an observed script `extends` it
                         (statically, through the class index); a base class whose
                         only live instances are subclasses (gh#15.3). Never folded
                         into reached
      reached_base_via - {script: the observed descendant that credited it}
      unreached        - reachable-suffix files the run never loaded
      not_applicable   - files reach cannot speak to (wrong suffix, deleted, or a test)
      deleted          - subset of not_applicable: in the diff but gone from disk;
                         a deleted file can never be loaded, so counting it unreached
                         would manufacture a permanent miss
      test_scripts     - subset of not_applicable: under the configured `test_dir`.
                         They ran in Phase 1 and cannot appear in a snapshot of a game
                         session, so scoring them as misses charged a developer for
                         writing a test - a permanent cap on the ratio, applied
                         precisely to the behavior the harness wants more of
      headless_tools   - subset of not_applicable: under `reach_headless_dirs`. Same
                         structural argument as test_scripts, one directory over -
                         a `--headless --script` runner has no node to be the script of
      changed_unavailable - True when the *changed set itself* could not be determined
                         (no git repository). Every list is then None, including the ones
                         that do not depend on `observed`
    When observed is None the observation-dependent lists are None.

    Two different unknowns, kept apart. `observed is None` means the run produced no
    capture: we know what changed, we cannot say what ran. `changed is None` means there
    is no VCS: we may know exactly what ran, we cannot say what it was supposed to cover.
    Both used to be flattened into an empty set somewhere upstream, and an empty set
    scores as a clean sweep - `reached 0/0`, which reads as "nothing to check"
    (moving-in:G-003). Neither is a zero and neither is reported as one.
    """
    if changed is None:
        # Not `{}`-with-empty-lists: every bucket is None, which is the shape this file
        # already uses for "could not tell" (see load_snapshots) and which `stats` and
        # every older reader already skip rather than average in as a zero.
        return {
            "reached": None,
            "reached_implicit": None,
            "reached_alias": None,
            "reached_alias_via": None,
            "reached_base": None,
            "reached_base_via": None,
            "unreached": None,
            "not_applicable": None,
            "deleted": None,
            "test_scripts": None,
            "headless_tools": None,
            "changed_unavailable": True,
        }
    cfg = load_config(root) if cfg is None else cfg
    test_dir = _test_dir(cfg)
    headless_dirs = _headless_dirs(cfg)
    aliases = _reach_aliases(cfg)

    candidates = sorted(p for p in changed if Path(p).suffix.lower() in REACHABLE_SUFFIXES)
    skipped = sorted(p for p in changed if Path(p).suffix.lower() not in REACHABLE_SUFFIXES)
    deleted = sorted(p for p in candidates if not (root / p).exists())
    tests = sorted(p for p in candidates if _under(p, test_dir))
    headless = sorted(p for p in candidates
                      if any(_under(p, d) for d in headless_dirs) and p not in tests)
    # A file can qualify for more than one sub-list (a deleted test, a headless tool that
    # was removed). Each sub-list reports it, and the set keeps not_applicable from
    # counting it twice.
    excused = set(deleted) | set(tests) | set(headless)
    live = [p for p in candidates if p not in excused]
    result = {
        "reached": None,
        "reached_implicit": None,
        "reached_alias": None,
        "reached_alias_via": None,
        "reached_base": None,
        "reached_base_via": None,
        "unreached": None,
        "not_applicable": sorted(set(skipped) | excused),
        "deleted": deleted,
        "test_scripts": tests,
        "headless_tools": headless,
        # Explicitly False rather than absent: a reader that has to infer "the changed
        # set was knowable" from a missing key is one bug away from inferring it from a
        # missing row, and this is the key that separates 0/0-real from 0/0-unknown.
        "changed_unavailable": False,
    }
    if observed is None:
        return result
    # An implicit script (autoload / extension) may vouch: it demonstrably runs in every
    # launched session, which is the same standard an observed node meets. An
    # alias-credited script may not - crediting off a credit chains claims until nothing
    # in the chain is an observation.
    vouching = observed | implicit
    result["reached"] = [p for p in live if p in observed]
    rest = [p for p in live if p not in observed]
    result["reached_implicit"] = [p for p in rest if p in implicit]
    rest = [p for p in rest if p not in implicit]
    via = {}
    for p in rest:
        voucher = _vouched_by(p, aliases, observed, vouching)
        if voucher:
            via[p] = voucher
    result["reached_alias"] = sorted(via)
    result["reached_alias_via"] = via
    rest = [p for p in rest if p not in via]
    base_via = _base_credits(observed, root, rest)
    result["reached_base"] = sorted(base_via)
    result["reached_base_via"] = base_via
    result["unreached"] = [p for p in rest if p not in base_via]
    return result


def _sub_reach(split):
    """The per-denominator object stored on the row: same keys, no nesting.

    Keys are only ever added here, never repurposed: `stats` reads rows written by
    every past version, and a bucket name that changed meaning would silently mix two
    populations in one average.

    `changed_unavailable` (0.17.0+) is an addition of exactly that kind. It is safe to
    add because a reader that has never heard of it still cannot misread the row: when it
    is True every list beside it is None, and null buckets are the shape both `stats` and
    `verify.md` already treat as "reach unknown, not counted". The key tells a new reader
    *which* unknown it was; it does not have to be read for the row to be read correctly
    (moving-in:G-003).
    """
    return {k: split[k] for k in
            ("reached", "reached_implicit", "reached_alias", "reached_alias_via",
             "reached_base", "reached_base_via",
             "unreached", "not_applicable", "deleted", "test_scripts",
             "headless_tools", "changed_unavailable")}


# The four verdicts a run can carry. `warranted` needs a named claim, `overkill` means
# it confirmed what was already known, `insufficient` means it could not reach or assert
# the thing that mattered, `inconclusive` means it aborted or was too small to judge.
# Deliberately not a free-text field: a countable enum is what lets `stats` say "31% of
# runs were overkill", which is a sentence no amount of prose in a log will produce.
VALUES = ("warranted", "overkill", "insufficient", "inconclusive")

# Which gate caught a thing. `runtime` is the only one that needed a running game, and
# separating it is what lets `stats` say how often the expensive half was the load-bearing
# one. `null` means the run did not say.
FOUND_PHASES = ("import", "lint", "tests", "runtime", "other")


def _normalize_found(found):
    """Coerce a run's `found` into stored shape, or None if it did not record one.

    Lenient in, strict out: a bare string is accepted as shorthand for a finding with no
    phase attributed, because this field is hand-written into `run.json` and a format
    quibble that costs a row is a worse outcome than a loosely-typed one. Returns
    (list-or-None, note-or-None); `[]` survives as `[]` and is never conflated with None,
    which is the entire distinction the field exists to carry.
    """
    if found is None:
        return None, None
    if isinstance(found, (str, dict)):          # one finding, unwrapped
        found = [found]
    if not isinstance(found, list):
        return None, ("`found` must be a list (or a single finding) - got %s, recorded "
                      "as null (unrecorded) rather than guessed at"
                      % type(found).__name__)

    out, notes = [], []
    for item in found:
        if isinstance(item, str):
            item = {"what": item}
        if not isinstance(item, dict):
            notes.append("dropped a `found` entry that was neither string nor object "
                         "(%s)" % type(item).__name__)
            continue
        what = str(item.get("what") or "").strip()
        if not what:
            notes.append("dropped a `found` entry with no `what`")
            continue
        phase = item.get("phase")
        if phase is not None and phase not in FOUND_PHASES:
            notes.append("`found` phase %r is not one of %s - recorded as null"
                         % (phase, ", ".join(FOUND_PHASES)))
            phase = None
        static = item.get("static_would_have_caught")
        if not isinstance(static, bool):
            static = None
        out.append({"what": what, "phase": phase,
                    "static_would_have_caught": static})

    # A non-empty input whose entries were all unusable is not the same statement as an
    # empty one. Letting it collapse to `[]` would turn a malformed answer into "this run
    # found nothing", which `_reconcile_value` then reads as grounds to downgrade the
    # verdict - a formatting slip quietly rewriting the finding. Record it as unrecorded.
    if found and not out:
        return None, ("; ".join(notes + ["every `found` entry was unusable - recorded as "
                                         "null (unrecorded), not as an empty list"]))
    return out, ("; ".join(notes) or None)


def _reconcile_value(value, reached, unreached, checks, found=None):
    """Cross-check a self-reported verdict against what the snapshots actually show.

    The verdict is the one field here the run grades itself on, so it gets every
    mechanical check available. Two exist. A run whose changed files were never loaded
    did not earn `warranted`, however well its checks went - and a run that says outright
    it found nothing did not either, because confirming what was already known is the
    definition of `overkill`. Reach is tried first: "could not see the code" is a more
    useful thing to be told than "saw it and it was fine". Returns (value, note-or-None).
    """
    if value not in VALUES:
        return "inconclusive", ("value %r is not one of %s - recorded as inconclusive"
                                % (value, ", ".join(VALUES)))
    if value == "warranted" and reached is not None and not reached and unreached:
        return "insufficient", (
            "downgraded warranted -> insufficient: no changed file was loaded at "
            "runtime (%s), so nothing runtime said was about this diff"
            % ", ".join(unreached))
    # `found == []` is the run's own statement that it caught nothing; `found is None`
    # is an older row or a run that declined to answer, and coercing that would be
    # scoring a silence.
    if value == "warranted" and found == []:
        return "overkill", (
            "downgraded warranted -> overkill: `found` is empty, so this run confirmed "
            "what was already known - which is what overkill means. If it did catch "
            "something, record it in `found` rather than only in prose")
    if value == "warranted" and not checks:
        return value, ("warranted with no Phase 4 checks recorded - the claim that "
                       "earned it is not in the row")
    return value, None


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


def _observed(args):
    """Union of everything the run demonstrably loaded; None if nothing was readable."""
    snapshot = load_snapshots(args.scene_tree)
    scripts = load_scripts_seen(getattr(args, "scripts_seen", None))
    if snapshot is None and scripts is None:
        return None
    return (snapshot or set()) | (scripts or set())


def _about_set(args):
    """Normalized `--about` paths, or None when the flag was not given.

    gh#20.2: reach's denominator is `git status`'s whole dirty set, which is right for
    a solo session and wrong for a fan-out one - a run graded on a sibling agent's
    still-uncommitted file, or (the inverse, and more dangerous) silently credited for
    one it never touched. `--about` lets a caller name what THIS run was actually about;
    the denominator becomes that set intersected with what actually changed, so a file
    outside it is neither reached nor unreached - it just isn't this run's business.
    """
    about = getattr(args, "about", None)
    if not about:
        return None
    return {_norm(p) for p in about}


def _restrict_to_about(changed, about):
    """Intersect a changed-set with `--about`; pass through unchanged otherwise.

    `None` passes straight through - a `None` changed-set already means "no VCS", which
    `--about` cannot supply an answer for either.
    """
    if not about or changed is None:
        return changed
    return changed & about


def cmd_record(args, root):
    run = _load_run(args)
    if run is None:
        return 1
    if not isinstance(run, dict):
        print("verify_ledger: run payload must be a JSON object", file=sys.stderr)
        return 1

    worktree = changed_worktree(root)
    branch_set = changed_branch(root)
    # None from both halves means git is absent, not that nothing changed. It is carried
    # into split_reach as None rather than coerced to set(), which is what used to write
    # `reached: 0, total: 0` into the permanent record (moving-in:G-003).
    union = _union_changed(worktree, branch_set)

    about = _about_set(args)
    if about:
        stray = sorted(about - (union or set()))
        if stray:
            print("verify_ledger: --about names %d path(s) not in the changed set - "
                  "typo, or a file that has not been saved: %s"
                  % (len(stray), ", ".join(stray)), file=sys.stderr)
        worktree = _restrict_to_about(worktree, about)
        branch_set = _restrict_to_about(branch_set, about)
        union = _restrict_to_about(union, about)

    cfg = load_config(root)
    implicit = implicit_scripts(root, cfg)

    observed = _observed(args)
    if observed is None and not args.no_reach:
        print("verify_ledger: refusing to record with unknown reach - no readable "
              "--scene-tree and no readable --scripts-seen were given. Re-run "
              "`devtools.py scene-tree` and/or `devtools.py --json scripts-seen` "
              "before quitting the game, or pass --scripts-seen with a saved capture. "
              "If the run genuinely aborted before the game came up, pass --no-reach "
              "to record reach as null on purpose.", file=sys.stderr)
        return 1

    if args.no_reach and observed is None:
        reach_obj = None
        u = split_reach(set(), None, implicit, root, cfg)  # _reconcile inputs stay None
    else:
        # None passes straight through in all three: `or set()` here was half of the bug.
        u = split_reach(union, observed, implicit, root, cfg)
        w = split_reach(worktree, observed, implicit, root, cfg)
        b = split_reach(branch_set, observed, implicit, root, cfg)
        reach_obj = _sub_reach(u)
        reach_obj["worktree"] = _sub_reach(w)
        reach_obj["branch"] = _sub_reach(b)
        if about:
            # Auditable, not just applied: a reader of the row can see the denominator
            # was narrowed on purpose, and to what, rather than mistaking it for a
            # small diff.
            reach_obj["about"] = sorted(about)

    sha = _git(root, "rev-parse", "--short", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")

    checks = run.get("checks") or []
    found, found_note = _normalize_found(run.get("found"))
    if found_note:
        print("verify_ledger: %s" % found_note, file=sys.stderr)
    if found is None:
        print("verify_ledger: no `found` recorded - this row cannot say whether the run "
              "caught anything, which is the one question the ledger exists to answer. "
              "Record `found: []` if it genuinely found nothing.", file=sys.stderr)
    value, note = _reconcile_value(run.get("value"), u["reached"], u["unreached"],
                                   checks, found)
    cheaper = (run.get("cheaper_alternative") or "").strip()

    # A blocked check is one the run could not execute at all - and a run that could
    # not execute one of its own checks did not fully pass, whatever the others said.
    verdict = run.get("verdict", "unknown")
    blocked = [str(c.get("name") or "?") for c in checks
               if isinstance(c, dict) and c.get("result") == "blocked"]
    if blocked and verdict == "pass":
        verdict = "partial"
        print("verify_ledger: verdict downgraded pass -> partial: blocked check(s) "
              "%s - a check that could not run is not a check that passed"
              % ", ".join(blocked), file=sys.stderr)

    # `entry_point` was recorded inside `runtime` by 0.6.0-0.7.0 rows and never read
    # by anything; drop it from new rows (old rows are never rewritten).
    runtime = run.get("runtime")
    if isinstance(runtime, dict) and "entry_point" in runtime:
        runtime = {k: v for k, v in runtime.items() if k != "entry_point"}

    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc)
                  .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "harness": run.get("harness") or HARNESS_VERSION,
        "sha": (sha or "").strip() or None,
        "branch": (branch or "").strip() or None,
        "changed": sorted(union) if union is not None else None,
        "verdict": verdict,
        "lint": run.get("lint"),
        "tests": run.get("tests"),
        "runtime": runtime,
        "checks": checks,
        "duration_s": run.get("duration_s"),
        # Was the harness worth running here, and what would have been cheaper? Both are
        # self-reported and both are the point: gaps can only ever recommend more
        # harness, so without these the log cannot say "this task didn't need it".
        "value": value,
        "value_reported": run.get("value"),
        "cheaper_alternative": cheaper or None,
        # What this run caught that the diff alone would not have. `[]` means "nothing,
        # and I am saying so"; null means unrecorded and is excluded from the rate rather
        # than counted as a zero. Every field above describes the run's end state, so
        # without this one a defect found and fixed mid-run is indistinguishable from a
        # run where nothing was ever wrong.
        "found": found,
        "expected": (run.get("expected") or "").strip() or None,
        # null only via the explicit --no-reach escape hatch (aborted runs). The
        # top-level lists are computed over worktree|branch so old readers keep
        # working; `worktree` and `branch` are the same split per denominator.
        "reach": reach_obj,
    }

    path = root / LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")

    print("verify_ledger: recorded %s run, value=%s - %s"
          % (row["verdict"], value, _reach_line(u)))
    if reach_obj is not None and reach_obj.get("changed_unavailable"):
        print("verify_ledger: reach recorded as UNAVAILABLE (not a git repository), not "
              "as 0/0 - the row's buckets are null, so `stats` excludes it instead of "
              "averaging in a clean sweep it never made. Note in the summary that reach "
              "could not be computed; do not report this as `insufficient`, which is a "
              "claim about the run rather than about the checkout.", file=sys.stderr)
    if note:
        print("verify_ledger: %s" % note, file=sys.stderr)
    if not cheaper:
        print("verify_ledger: no cheaper_alternative given - the field that says when "
              "the harness was the wrong tool is the one it is easiest to leave blank",
              file=sys.stderr)
    return 0


def _reach_line(split):
    # Three lines, three states, and they have to be told apart at a glance or the
    # distinction may as well not be computed. "unavailable" (no VCS, no denominator)
    # borrows the voice of the existing "not computed" line rather than inventing a
    # second dialect for the same idea (moving-in:G-003).
    if split is not None and split.get("changed_unavailable"):
        return ("reach: unavailable (not a git repository) - there is no changed set to "
                "score against, so this is not a 0/0 and must not be read as one")
    if split is None or split.get("reached") is None:
        return "reach not computed (no scene-tree / scripts-seen capture)"
    reached = split["reached"]
    implicit = split.get("reached_implicit") or []
    alias = split.get("reached_alias") or []
    via = split.get("reached_alias_via") or {}
    base = split.get("reached_base") or []
    base_via = split.get("reached_base_via") or {}
    tests = split.get("test_scripts") or []
    headless = split.get("headless_tools") or []
    unreached = split.get("unreached") or []
    total = len(reached) + len(implicit) + len(alias) + len(base) + len(unreached)
    if total:
        detail = "reached %d/%d changed file(s)" % (len(reached), total)
    elif split.get("not_applicable"):
        # A zero that git can vouch for: files did change, and every one of them is
        # excused from the denominator. The annotations below name which.
        detail = ("reached 0/0 changed file(s) - a real zero: every changed file is "
                  "excused from the denominator")
    else:
        detail = ("reached 0/0 changed file(s) - a real zero: git reports nothing "
                  "changed here, which is a fact rather than an unknown")
    if implicit:
        detail += " (+%d implicit: %s)" % (len(implicit), ", ".join(implicit))
    if alias:
        # Naming the voucher inline is the point: "+1 by alias" alone would be a number
        # the reader has to trust, and an alias is a project's claim, not an observation.
        detail += " (+%d by alias: %s)" % (
            len(alias), ", ".join("%s via %s" % (p, via.get(p, "?")) for p in alias))
    if base:
        # A static fact about the files, not a project claim: the observed subclass
        # `extends` this one, so its code ran. Named so it stays auditable.
        detail += " (+%d as base class: %s)" % (
            len(base), ", ".join("%s extended by %s" % (p, base_via.get(p, "?")) for p in base))
    if tests:
        # Said out loud rather than quietly dropped from the denominator - a ratio that
        # improves without saying what left it is the kind of number this file exists
        # to not produce.
        detail += ("; %d test script(s) excluded (ran in Phase 1; a game session cannot "
                   "load them): %s" % (len(tests), ", ".join(tests)))
    if headless:
        # Same reasoning as the tests line above: named, not silently dropped.
        detail += ("; %d headless tool(s) excluded (run only under --headless --script, "
                   "so no node can carry them): %s" % (len(headless), ", ".join(headless)))
    if unreached:
        detail += "; NOT reached: " + ", ".join(unreached)
    elif total > len(reached):
        # "reached 1/4" with three credited files reads as a bad run at a glance, and a
        # number that has to be read twice to be read right is a number people stop
        # reading. Say the conclusion out loud; the annotations above still hold the
        # evidence, and `reached` is still only what was observed.
        detail += "; nothing left unreached"
    return detail


def cmd_reach(args, root):
    """Compute reach without writing a row.

    Phase 6 picks its verdict partly from reach - a run that never loaded the changed
    file is `insufficient` however well its checks went - so reach has to be readable
    before the row is written, not only after.
    """
    worktree = changed_worktree(root)
    branch_set = changed_branch(root)
    union = _union_changed(worktree, branch_set)

    about = _about_set(args)
    if about:
        stray = sorted(about - (union or set()))
        if stray:
            print("verify_ledger: --about names %d path(s) not in the changed set - "
                  "typo, or a file that has not been saved: %s"
                  % (len(stray), ", ".join(stray)), file=sys.stderr)
        worktree = _restrict_to_about(worktree, about)
        branch_set = _restrict_to_about(branch_set, about)
        union = _restrict_to_about(union, about)

    cfg = load_config(root)
    implicit = implicit_scripts(root, cfg)
    observed = _observed(args)

    u = split_reach(union, observed, implicit, root, cfg)
    w = split_reach(worktree, observed, implicit, root, cfg)
    b = split_reach(branch_set, observed, implicit, root, cfg)

    if about:
        print("--about: denominator narrowed to %d path(s): %s"
              % (len(about), ", ".join(sorted(about))))
    print("worktree (this session's edits - the honest number): " + _reach_line(w))
    print("branch   (all commits since base - dilutes as the branch grows): "
          + _reach_line(b))
    if u.get("changed_unavailable"):
        # Deliberately printed instead of the `insufficient` warning below, not as well
        # as it. "I cannot tell" and "nothing was reached" are different claims and the
        # ledger's whole argument is that they must not print the same (moving-in:G-003).
        # The observed count is offered as a count, never as a ratio: without a diff to
        # compare it against there is no denominator to make it one.
        seen = "unknown (no capture was readable)" if observed is None \
            else "%d script/scene path(s)" % len(observed)
        print("\nReach is UNAVAILABLE, not zero. This checkout has no git repository, so "
              "there is no changed set for the run to have covered.")
        print("  This run observed: %s. That is a count, not a ratio - nothing here can "
              "turn it into one." % seen)
        print("  Phase 6: this is NOT grounds for `insufficient`. `insufficient` means "
              "the run could not reach the thing that mattered; this means the ledger "
              "cannot say what the thing that mattered was. Judge the run on its checks "
              "and state plainly that reach could not be computed.")
        return 0
    if u["not_applicable"]:
        print("not applicable (reach cannot speak to these): "
              + ", ".join(u["not_applicable"]))
    if u["deleted"]:
        print("deleted (in the diff, gone from disk - never counted unreached): "
              + ", ".join(u["deleted"]))
    if u["test_scripts"]:
        print("test scripts (ran in Phase 1, invisible to a game session's scene tree - "
              "excused, not credited: reach cannot tell you they passed): "
              + ", ".join(u["test_scripts"]))
    if u.get("headless_tools"):
        print("headless tools (run only under --headless --script, so no node can carry "
              "them - excused, not credited): " + ", ".join(u["headless_tools"]))
    if u.get("reached_alias"):
        via = u.get("reached_alias_via") or {}
        print("credited by reach_aliases (declared by this project, NOT observed - a "
              "claim the config makes, shown so you can disbelieve it): "
              + ", ".join("%s via %s" % (p, via.get(p, "?")) for p in u["reached_alias"]))
    if u.get("reached_base"):
        bv = u.get("reached_base_via") or {}
        print("credited as base class (an observed script `extends` it - a static read "
              "of the files, no config): "
              + ", ".join("%s extended by %s" % (p, bv.get(p, "?")) for p in u["reached_base"]))
    if u["reached"] is not None and not u["reached"] \
            and not (u["reached_implicit"] or []) \
            and not (u["reached_alias"] or []) \
            and not (u.get("reached_base") or []) and u["unreached"]:
        print("\nNo changed file was loaded at runtime. Phase 6 verdict is "
              "`insufficient`, not `warranted`, even if every check passed.")
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
    values = defaultdict(int)
    cheaper = []
    overkill_seconds = 0.0
    reached_n = unreached_n = 0
    implicit_n = 0
    # Buckets added after 0.8.0: rows written before it have no such key, and `or []`
    # is what keeps a mixed-vintage ledger aggregating instead of raising.
    alias_n = 0
    base_n = 0
    test_n = 0
    headless_n = 0
    wt_reached = wt_denom = 0        # worktree denominator (0.8.0+ rows)
    br_reached = br_denom = 0        # branch denominator (0.8.0+ rows)
    per_version = defaultdict(lambda: [0, 0])
    runtime_findings = 0
    static_only_findings = 0
    no_snapshot = 0
    # Rows whose reach was unknown because there was no VCS (0.17.0+). Both land in the
    # excluded-from-the-ratio pile, but reporting one as the other would be this file
    # telling the same lie in a smaller font: "no snapshot" blames the run for not
    # capturing, "no repository" blames nothing at all.
    no_vcs = 0
    durations = []
    # `found` (0.10.0+). Rows predating it carry no key at all and are excluded from the
    # denominator - scoring a silence as "found nothing" would read as a tool that helps
    # less than it does, the mirror of the flattery the field exists to correct.
    found_rows = 0
    found_hit_rows = 0
    found_items = 0
    found_only_runtime = 0
    found_phases = defaultdict(int)
    found_examples = []

    for row in rows:
        verdicts[row.get("verdict", "unknown")] += 1
        val = row.get("value") or "unrecorded"
        values[val] += 1

        found = row.get("found")
        if isinstance(found, list):
            found_rows += 1
            if found:
                found_hit_rows += 1
            for item in found:
                if not isinstance(item, dict):
                    continue
                found_items += 1
                found_phases[item.get("phase") or "unattributed"] += 1
                if item.get("static_would_have_caught") is False:
                    found_only_runtime += 1
                    if len(found_examples) < 5:
                        found_examples.append(str(item.get("what") or "?"))
        if row.get("cheaper_alternative"):
            cheaper.append((val, row["cheaper_alternative"]))
        if val == "overkill" and isinstance(row.get("duration_s"), (int, float)):
            overkill_seconds += row["duration_s"]

        reach = row.get("reach") or {}
        r, u = reach.get("reached"), reach.get("unreached")
        if r is None:
            if reach.get("changed_unavailable"):
                no_vcs += 1
            else:
                no_snapshot += 1
        else:
            reached_n += len(r)
            unreached_n += len(u or [])
            implicit_n += len(reach.get("reached_implicit") or [])
            alias_n += len(reach.get("reached_alias") or [])
            base_n += len(reach.get("reached_base") or [])  # 0.21.0+ key
            test_n += len(reach.get("test_scripts") or [])
            # .get on a key added in 0.13.0: every row written before it must still
            # parse, or stats silently mixes two populations in one average.
            headless_n += len(reach.get("headless_tools") or [])
            slot = per_version[row.get("harness") or "?"]
            slot[0] += len(r)
            slot[1] += len(r) + len(u or [])
            for sub, acc in (("worktree", "wt"), ("branch", "br")):
                d = reach.get(sub) or {}
                sr = d.get("reached")
                if sr is None:
                    continue
                su = len(d.get("unreached") or [])
                if acc == "wt":
                    wt_reached += len(sr)
                    wt_denom += len(sr) + su
                else:
                    br_reached += len(sr)
                    br_denom += len(sr) + su

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
    if wt_denom or br_denom:
        print("       worktree: %s (%d/%d) - the honest per-session number: what "
              "each session edited vs what its run loaded"
              % (_pct(wt_reached, wt_denom), wt_reached, wt_denom))
        print("       branch:   %s (%d/%d) - dilutes as branches grow; old commits "
              "drag it down through no fault of any one run"
              % (_pct(br_reached, br_denom), br_reached, br_denom))
    if implicit_n:
        print("       %d file(s) credited implicitly (autoload/extension - runs in "
              "every session, invisible to snapshots)" % implicit_n)
    if alias_n:
        print("       %d file(s) credited by reach_aliases - declared by the project, "
              "not observed. If this outgrows the observed count, the number above is "
              "mostly the config talking." % alias_n)
    if base_n:
        print("       %d file(s) credited as base classes - an observed script extends "
              "them (a static read of the files, 0.21.0+)" % base_n)
    if test_n:
        print("       %d changed test script(s) excluded - they ran in Phase 1, which "
              "is not something reach can see or vouch for" % test_n)
    if headless_n:
        print("       %d changed headless tool(s) excluded - they run under "
              "--headless --script, so no node ever carries them" % headless_n)
    if no_snapshot:
        print("       %d run(s) recorded no snapshot - reach unknown, not counted" % no_snapshot)
    if no_vcs:
        print("       %d run(s) had no git repository - reach was not computable at all, "
              "not 0/0. Excluded from the ratio; a checkout with no VCS has no diff for "
              "a run to have covered." % no_vcs)

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

    # The headline for "is this tool earning its keep". The two lines above count runs
    # that ended red; this one counts runs that *told you something*, including the ones
    # where the finding was fixed before the row was written - which is most of them.
    print("\ndid it tell you anything?")
    if not found_rows:
        print("  no run has recorded `found` yet (added 0.10.0). Until one does, the "
              "only evidence that this harness catches anything is prose.")
    else:
        print("  %d of %d run(s) with `found` recorded caught something (%s), %d "
              "finding(s) total"
              % (found_hit_rows, found_rows, _pct(found_hit_rows, found_rows),
                 found_items))
        if found_items:
            print("  by phase: %s" % ", ".join(
                "%s %d" % (k, found_phases[k]) for k in sorted(found_phases)))
            print("  %d finding(s) (%s) reported that lint and tests would NOT have "
                  "caught them - this is the number that justifies a running game"
                  % (found_only_runtime, _pct(found_only_runtime, found_items)))
        for what in found_examples:
            print("    - %s" % what)
        skipped = total - found_rows
        if skipped:
            print("  %d row(s) predate `found` and are excluded from the rate rather "
                  "than counted as zeros." % skipped)

    print("\nwas it worth running?")
    for name in VALUES + ("unrecorded",):
        if values.get(name):
            print("  %-13s %3d  (%s)" % (name, values[name], _pct(values[name], total)))

    if overkill_seconds:
        print("  time spent on runs judged overkill: %.0f min" % (overkill_seconds / 60.0))

    if cheaper:
        print("\nwhat would have been cheaper (most recent first):")
        for val, text in list(reversed(cheaper))[:8]:
            print("  [%s] %s" % (val, text))
        print("  -- a phrase repeating here is a finding about *when* to run /verify,")
        print("     which no amount of feature work on the harness would surface.")

    if not values.get("overkill") and total >= 8:
        print("\nNot one run in %d judged itself overkill. That is possible, and it is "
              "also what a log that flatters the tool looks like - check the entries "
              "before believing the number." % total)

    # A `warranted` row with no `found` is the exact shape the field was added to fix:
    # the claim is there and the evidence for it is not.
    warranted_unrecorded = sum(
        1 for row in rows
        if (row.get("value") == "warranted") and not isinstance(row.get("found"), list))
    if warranted_unrecorded >= 4:
        print("\n%d run(s) called themselves `warranted` without recording `found`. "
              "That verdict is self-graded and `found` is the field that can contradict "
              "it, so those rows assert usefulness with the evidence left out."
              % warranted_unrecorded)

    aborted = verdicts.get("aborted", 0)
    if aborted:
        print("\n%d run(s) verified nothing (exit 2). Those are not passes." % aborted)
    partial = verdicts.get("partial", 0)
    if partial:
        print("\n%d run(s) recorded partial - a blocked check capped a would-be pass. "
              "A blocked check is unexecuted, not green." % partial)
    if denom and reached_n < denom:
        print("\nThe %d unreached file(s) are the harness's blind spot - a green /verify "
              "on those was a statement about the diff, not the running game."
              % (denom - reached_n))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Append-only ledger of /verify runs, and stats over it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Three subcommands:")[-1],
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("record", help="Append one run to the ledger")
    p.add_argument("--scene-tree", metavar="FILE", action="append",
                   help="A `devtools.py scene-tree` capture. Repeatable; reach is the "
                        "union across captures.")
    p.add_argument("--scripts-seen", metavar="FILE", action="append",
                   help="A `devtools.py --json scripts-seen` capture (full reply or "
                        "bare {\"scripts\": [...]}). Repeatable; unioned with the "
                        "scene-tree captures.")
    p.add_argument("--no-reach", action="store_true",
                   help="Record reach as null ON PURPOSE (aborted runs: the game "
                        "never came up). Without this flag, record refuses to write "
                        "a row when no capture is readable.")
    p.add_argument("--about", metavar="PATH", action="append",
                   help="Restrict the reach denominator to these path(s), intersected "
                        "with what actually changed (gh#20.2). For a fan-out session: "
                        "name only the file(s) THIS run set out to verify, so a "
                        "sibling agent's still-uncommitted file is neither a false "
                        "miss nor a silent free credit. Repeatable.")
    p.add_argument("--run", metavar="FILE",
                   help="JSON object of this run's results. Default: read stdin.")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("reach", help="Compute reach without recording a run")
    p.add_argument("--scene-tree", metavar="FILE", action="append",
                   help="A `devtools.py scene-tree` capture. Repeatable.")
    p.add_argument("--scripts-seen", metavar="FILE", action="append",
                   help="A `devtools.py --json scripts-seen` capture. Repeatable.")
    p.add_argument("--about", metavar="PATH", action="append",
                   help="Restrict the reach denominator to these path(s), intersected "
                        "with what actually changed. Repeatable. See `record --about`.")
    p.set_defaults(func=cmd_reach)

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
