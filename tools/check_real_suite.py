#!/usr/bin/env python3
"""Run a sibling project's real test suite against this working tree's templates (H-070).

`check_templates.py` cannot see runner timing (gh#43): 0.40.0 added a synchronous
`user://` walk between tests, three releases passed `--full` and every unit test, and
the first real suite to run it segfaulted - the stall changed how many physics ticks
the next test's settle frames delivered, and a node that frees itself on a tick count
did. A real, tick-sensitive suite is the only instrument for that class, and this
makes running one a command instead of a paragraph in CLAUDE.md.

What it does, in order:
  1. Copies the sibling project to a scratch directory (never touching the sibling -
     it is another session's working tree), skipping .git/ and .devtools/.
  2. Sets BOTH `config/use_custom_user_dir=true` AND `config/custom_user_dir_name`
     in the copy's project.godot - the name alone is ignored by Godot, which is how a
     0.37.0 probe rewrote a developer's real save (H-067).
  3. Runs the copy's suite as the project currently ships it (its own harness version)
     -> the BEFORE line. `--baseline-total N --baseline-passed M` skips this run.
  4. Installs this working tree's templates into the copy with `scaffold_install.py
     full` (refusing nothing: the copy is disposable), and runs the suite again -> the
     AFTER line.
  5. Prints both `Total:` lines side by side and exits 1 if the AFTER run passed fewer
     tests, failed more, or exited worse than BEFORE; 2 if a run could not be read.

Usage:
    python tools/check_real_suite.py ../plant-tower-defense
    python tools/check_real_suite.py ../moving-in --godot "C:/.../Godot_v4.7.1-stable_win64.exe"
    python tools/check_real_suite.py ../plant-tower-defense --baseline-total 554 --baseline-passed 554

Four to eight minutes; run it whenever `run_tests.gd` (or anything the runner does
between/around tests) changed, and quote both lines in the release entry.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_TOTAL_RE = re.compile(r"^\s*Total:\s*(\d+)\s*\|\s*Passed:\s*(\d+)\s*\|\s*Failed:\s*(\d+)", re.M)
_STAMP_RE = re.compile(r"^#\s*harness-version:\s*(\d+\.\d+\.\d+)", re.M)


def _resolve_godot(explicit):
    if explicit:
        return Path(explicit)
    env = os.environ.get("GODOT_BIN")
    if env:
        return Path(env)
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import check_templates  # noqa: E402
        found = check_templates.resolve_godot(None)
        if found:
            return Path(found)
    except Exception:
        pass
    return None


def _copy_project(src: Path, dst: Path):
    def ignore(_dir, names):
        return {n for n in names if n in (".git", ".devtools")}
    shutil.copytree(src, dst, ignore=ignore, symlinks=False)


def _set_custom_user_dir(project: Path, name: str):
    pg = project / "project.godot"
    text = pg.read_text(encoding="utf-8")
    text = re.sub(r"^config/use_custom_user_dir=.*$", "", text, flags=re.M)
    text = re.sub(r"^config/custom_user_dir_name=.*$", "", text, flags=re.M)
    m = re.search(r'^config/name="[^"]*"\s*$', text, re.M)
    line = 'config/use_custom_user_dir=true\nconfig/custom_user_dir_name="%s"' % name
    if m:
        text = text[:m.end()] + "\n" + line + text[m.end():]
    else:
        text = text.replace("[application]", "[application]\n" + line, 1)
    pg.write_text(text, encoding="utf-8")


def _user_data_dir(name: str) -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Godot" / "app_userdata"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Godot" / "app_userdata"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "godot" / "app_userdata"
    return base / name


def _installed_version(project: Path):
    for rel in ("addons/godot_selftest/dev_tools.gd", "tools/run_tests.gd"):
        try:
            m = _STAMP_RE.search((project / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m:
            return m.group(1)
    return "?"


def run_suite(project: Path, godot: Path, timeout: int, extra):
    """(total, passed, failed, exit code, log tail) - or None when unreadable."""
    # Options first, in --key=value form: run_tests.py's passthrough is
    # nargs=REMAINDER, and a bare `--godot G` after `-p` was swallowed into it and
    # handed to run_tests.gd, which refused it (exit 2, Total 0) - the first run of
    # this script printed "real suite OK ... 0/0" over exactly that.
    # The binary goes in via $GODOT_BIN and nothing but -p is passed: the wrapper's
    # passthrough is nargs=REMAINDER, and an option the PROJECT's (older) wrapper does
    # not know - 0.38.0 had no --timeout - is swallowed into it together with
    # everything after, handed to run_tests.gd, and refused (exit 2, Total 0). The
    # first run of this script printed "real suite OK ... 0/0" over exactly that.
    cmd = [sys.executable, str(project / "tools" / "run_tests.py"), "-p", str(project)]         + (["--"] + extra if extra else [])
    env = dict(os.environ, GODOT_BIN=str(godot))
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 120,
                          encoding="utf-8", errors="replace", env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    m = None
    for m in _TOTAL_RE.finditer(out):
        pass
    if m is None:
        return None, proc.returncode, out[-3000:], time.time() - started
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))), proc.returncode, out[-3000:], time.time() - started


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project", help="Sibling Godot project to copy (never modified)")
    ap.add_argument("--godot", help="Godot binary (else $GODOT_BIN, then well-known paths)")
    ap.add_argument("--timeout", type=int, default=900, help="Per-run suite timeout (s)")
    ap.add_argument("--baseline-total", type=int, default=None,
                    help="Skip the BEFORE run; use this Total (with --baseline-passed)")
    ap.add_argument("--baseline-passed", type=int, default=None)
    ap.add_argument("--keep", action="store_true", help="Keep the copy and its user dir")
    ap.epilog = "Args after a literal -- go to run_tests.gd (e.g. -- --filter combat)."
    # Split on a literal `--` ourselves: argparse's nargs=REMAINDER swallowed this
    # script's own `--godot PATH` into the passthrough (it followed the positional),
    # forwarded it to run_tests.gd, which refused it - and the BEFORE line read
    # Total 0 / exit 2 with the real cause two layers down.
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = ap.parse_args(argv)

    src = Path(args.project).expanduser().resolve()
    if not (src / "project.godot").is_file():
        print("error: no project.godot at %s" % src, file=sys.stderr)
        return 2
    if not (src / "tools" / "run_tests.py").is_file():
        print("error: %s has no tools/run_tests.py - not a scaffolded project" % src, file=sys.stderr)
        return 2
    godot = _resolve_godot(args.godot)
    if godot is None or not Path(godot).exists():
        print("error: no Godot binary (pass --godot or set GODOT_BIN)", file=sys.stderr)
        return 2

    token = uuid.uuid4().hex[:8]
    user_name = "harness-real-%s" % token
    tmp = Path(tempfile.mkdtemp(prefix="harness-real-"))
    copy = tmp / src.name
    print("copying %s -> %s (skipping .git, .devtools)" % (src, copy))
    _copy_project(src, copy)
    _set_custom_user_dir(copy, user_name)
    print("user:// for the copy: %s (use_custom_user_dir=true AND custom_user_dir_name - both keys, H-067)"
          % _user_data_dir(user_name))
    rc = 0
    try:
        before_ver = _installed_version(copy)
        if args.baseline_total is not None and args.baseline_passed is not None:
            before = ((args.baseline_total, args.baseline_passed,
                       args.baseline_total - args.baseline_passed), 0, "", 0.0)
            print("BEFORE (harness %s): given as Total %d | Passed %d (no run)"
                  % (before_ver, args.baseline_total, args.baseline_passed))
        else:
            print("BEFORE: running the suite as the project ships it (harness %s)..." % before_ver)
            before = run_suite(copy, godot, args.timeout, extra)
            if before[0] is None:
                print("BEFORE run unreadable (exit %d); tail:\n%s" % (before[1], before[2]), file=sys.stderr)
                return 2
            print("BEFORE (harness %s): Total %d | Passed %d | Failed %d | exit %d | %.0fs"
                  % ((before_ver,) + before[0] + (before[1], before[3])))
            if before[1] == 2 or before[0][0] == 0:
                # The rule this repo exists for: 0 tests / exit 2 is "could not run",
                # never a baseline. Say so and stop; do not compare against it.
                print("BEFORE run VERIFIED NOTHING (exit %d, Total %d) - the copy could not run its "
                      "suite as shipped; nothing to compare against. Log tail:\n%s"
                      % (before[1], before[0][0], before[2]), file=sys.stderr)
                return 2

        inst = subprocess.run(
            [sys.executable, str(REPO / "tools" / "scaffold_install.py"), "full",
             "--project", str(copy), "--plugin-root", str(REPO), "--no-hook", "--allow-downgrade"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        ver_line = next((l for l in (inst.stdout or "").splitlines() if l.startswith("[version]")), "")
        print("install: %s (exit %d)" % (ver_line or "?", inst.returncode))
        if inst.returncode != 0:
            print((inst.stdout or "")[-1500:] + (inst.stderr or "")[-1500:], file=sys.stderr)
            return 2
        after_ver = _installed_version(copy)
        print("AFTER: running the suite under this working tree's templates (harness %s)..." % after_ver)
        after = run_suite(copy, godot, args.timeout, extra)
        if after[0] is None:
            print("AFTER run unreadable (exit %d); tail:\n%s" % (after[1], after[2]), file=sys.stderr)
            return 2
        print("AFTER  (harness %s): Total %d | Passed %d | Failed %d | exit %d | %.0fs"
              % ((after_ver,) + after[0] + (after[1], after[3])))
        if after[1] == 2 or after[0][0] == 0:
            print("AFTER run VERIFIED NOTHING (exit %d, Total %d) under this working tree's runner - "
                  "that IS a regression if BEFORE ran. Log tail:\n%s"
                  % (after[1], after[0][0], after[2]), file=sys.stderr)
            return 1

        b_total, b_pass, b_fail = before[0]
        a_total, a_pass, a_fail = after[0]
        worse = []
        if a_pass < b_pass:
            worse.append("passed %d -> %d" % (b_pass, a_pass))
        if a_fail > b_fail:
            worse.append("failed %d -> %d" % (b_fail, a_fail))
        if after[1] != 0 and (before[1] == 0):
            worse.append("exit %d -> %d" % (before[1], after[1]))
        if a_total < b_total:
            worse.append("total %d -> %d (tests vanished)" % (b_total, a_total))
        if worse:
            print("REGRESSION under this working tree's runner: %s. Log tail:\n%s"
                  % ("; ".join(worse), after[2]), file=sys.stderr)
            rc = 1
        else:
            print("real suite OK: %s -> %s, %d/%d passed both times%s"
                  % (before_ver, after_ver, a_pass, a_total,
                     "" if a_total == b_total else " (total %d -> %d)" % (b_total, a_total)))
    finally:
        if args.keep:
            print("kept: %s and %s" % (copy, _user_data_dir(user_name)))
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(_user_data_dir(user_name), ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
