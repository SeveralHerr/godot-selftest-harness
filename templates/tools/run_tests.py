#!/usr/bin/env python3
"""run_tests.py - run run_tests.gd and refuse to report PASS over an aborted test.

Why this exists (gh#27 / moving-in:G-050, reported independently by two projects the
same day): a test method that hits a runtime error part-way through is reported [PASS].
Godot coerces an aborted coroutine's return value to the declared return type's
default -- "" for a `-> String` test -- which is byte-for-byte identical to a genuine
pass. run_tests.gd's own PASS/FAIL tally reads that return value and cannot tell the
difference; a real run was measured passing on a `-> String` test whose body raised
`SCRIPT ERROR: Invalid operands 'float' and 'Nil' in operator '+'` and never reached its
own assertions. [VACUOUS] does not catch this either: it only fires when a test executed
ZERO assertions, and an abort part-way through has usually already executed some -- the
more dangerous case, because the assertion count looks healthy.

The signal cannot come from inside run_tests.gd. GDScript has no way to observe its own
process's stderr after the fact, and the coerced return value is the only channel back
from an aborted coroutine -- which is exactly the channel that lies. So this wrapper
runs the suite as a subprocess, captures stdout+stderr together (in the order Godot
wrote them, matching import_check.py's proven approach for the identical reason:
`--import`'s exit code lies about parse errors the same way run_tests.gd's tally lies
about aborts), counts SCRIPT ERROR / USER SCRIPT ERROR lines, and refuses to let a
nonzero count pass through as a clean run -- regardless of what run_tests.gd itself
reported.

Exit codes follow the harness convention (`import_check.py`, `lint_project.gd`):
    0  the suite ran, run_tests.gd reported success, AND no error line was emitted
    1  run_tests.gd reported failures, OR it reported success but errors were emitted
       during the run (the exact case this tool exists to catch)
    2  could not run at all: no Godot binary, no project.godot, no output captured,
       Godot crashed, or the run timed out

Usage:
    python tools/run_tests.py                          # run the whole suite
    python tools/run_tests.py -- --filter foo           # passthrough args to run_tests.gd
    python tools/run_tests.py -- --file test_player.gd
    python tools/run_tests.py --json                    # this wrapper's own verdict as JSON

(`python` on Windows -- `python3` there is a Microsoft Store alias stub that satisfies
`command -v` and then refuses to run.)

Godot binary resolution matches `devtools.py launch` / `import_check.py`:
    --godot flag  ->  $GODOT_BIN  ->  `godot_bin` in
    addons/godot_selftest/devtools_config.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# harness-version: 0.36.0
HARNESS_VERSION = "0.36.0"

# The runtime-error prefixes Godot emits for a GDScript that raised mid-execution.
# Deliberately narrower than import_check.py's FAILURE_SIGNALS: "Parse Error" /
# "Failed to load script" / "Compilation failed" are load-time signals import_check.py
# already owns, and including them here would flag a test suite for a defect that
# belongs to a different gate's report. A runtime abort prints "SCRIPT ERROR" (an
# unhandled engine error, e.g. "Invalid operands 'float' and 'Nil'") or
# "USER SCRIPT ERROR" (a push_error() call) -- either way, code that was running when
# the harness's own tally says nothing went wrong.
FAILURE_SIGNALS = ("SCRIPT ERROR", "USER SCRIPT ERROR")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_AT_RE = re.compile(r"^\s+at: ")


def _read_harness_config(project_path: Path) -> dict:
    """addons/godot_selftest/devtools_config.json as a dict; {} when unreadable.

    Deliberately duplicated from devtools.py / import_check.py rather than imported --
    see import_check.py's identical helper for why the copy is cheaper than the coupling.
    """
    cfg_path = project_path / "addons" / "godot_selftest" / "devtools_config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_godot(args, project_path: Path) -> Path:
    """--godot -> $GODOT_BIN -> config godot_bin. Exits 2 when there is no usable binary."""
    config = _read_harness_config(project_path)
    godot = args.godot or os.environ.get("GODOT_BIN") or str(config.get("godot_bin", "") or "")
    if not godot:
        print("Error: no Godot binary found. Pass --godot PATH, set $GODOT_BIN, or set "
              '"godot_bin" in addons/godot_selftest/devtools_config.json.', file=sys.stderr)
        sys.exit(2)
    godot_path = Path(godot).expanduser()
    if not godot_path.is_file():
        print(f"Error: Godot binary not found: {godot_path}", file=sys.stderr)
        sys.exit(2)
    return godot_path


def scan_output(text: str):
    """Return [(line_no, line, at), ...] for every runtime-error line in the log.

    Same shape as import_check.py's scan_output: the continuation "   at: ..." line is
    attached to the finding above it rather than reported separately.
    """
    findings = []
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        line = _ANSI_RE.sub("", raw).rstrip()
        if not any(s in line for s in FAILURE_SIGNALS):
            continue
        detail = ""
        if index + 1 < len(lines):
            nxt = _ANSI_RE.sub("", lines[index + 1]).rstrip()
            if _AT_RE.match(nxt):
                detail = nxt.strip()
        findings.append({"line_no": index + 1, "text": line.strip(), "at": detail})
    return findings


def run_suite(godot_path: Path, project_path: Path, log_path: Path, passthrough, timeout: int):
    """Run the suite with stdout+stderr going to log_path. Returns Godot's exit code.

    Output goes to a file, never subprocess.PIPE -- see import_check.py's run_import
    for why (an unread pipe stalls Godot on Windows; a non-console build often prints
    nothing to a live terminal at all, so the file is the only honest read either way).
    """
    cmd = [str(godot_path), "--headless", "--path", str(project_path),
           "--script", "res://tools/run_tests.gd"]
    if passthrough:
        cmd += ["--"] + list(passthrough)
    with log_path.open("w", encoding="utf-8") as log_f:
        proc = subprocess.run(cmd, stdout=log_f, stderr=subprocess.STDOUT,
                              cwd=str(project_path), timeout=timeout)
    return proc.returncode


_ASSERT_RE = re.compile(r"\b_T\s*\.\s*assert\w*\s*\(")
_EXECUTED_RE = re.compile(r"^\s*Assertions:\s*(\d+)\s+executed", re.M)


def declared_assertions(project_path):
    """(count, files) of `_T.assert_*(` call sites written across the test dir.

    moving-in:G-054 / gh#27: run_tests.gd counts assertions EXECUTED and cannot know
    how many a method contains, so a method that aborts after its first assert reads
    as a pass with one assertion. Measured on a real four-method file, written-vs-
    executed came out 4/2, 2/1, 2/1, 2/2 - the three aborts separated cleanly from
    the one genuine pass. Counted with the same word-bounded pattern coverage_check.py
    uses, over comment/string-blanked source, so a doc-comment mentioning
    `_T.assert_eq` is not a declaration. Advisory only: it is printed, never gated.
    """
    cfg = project_path / "addons" / "godot_selftest" / "devtools_config.json"
    test_dir = "res://test/unit"
    try:
        test_dir = str(json.loads(cfg.read_text(encoding="utf-8")).get("test_dir") or test_dir)
    except (OSError, ValueError):
        pass
    root = project_path / test_dir.replace("res://", "", 1)
    if not root.is_dir():
        return 0, 0
    blank = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import coverage_check  # shipped beside this file
        blank = coverage_check._blank_strings_and_comments
    except Exception:  # noqa: BLE001 - counting is advisory; fall back to raw text
        blank = None
    count = files = 0
    for path in sorted(root.rglob("*.gd")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if blank is not None:
            try:
                text = blank(text)[0]
            except Exception:  # noqa: BLE001
                pass
        count += len(_ASSERT_RE.findall(text))
        files += 1
    return count, files


def main():
    parser = argparse.ArgumentParser(
        description="Run run_tests.gd and fail the run when it emitted a runtime error "
                    "it did not itself notice.",
        epilog="Exit codes: 0 clean, 1 the suite failed OR emitted an error under a "
               "reported pass, 2 could not run. Args after -- pass through to "
               "run_tests.gd (--filter, --file, --json, ...).")
    parser.add_argument("--project", "-p", help="Path to Godot project", default=".")
    parser.add_argument("--godot", help="Godot binary (else $GODOT_BIN, else config godot_bin)")
    parser.add_argument("--timeout", type=int, default=900,
                        help="Seconds to wait for the suite before giving up (default: 900)")
    parser.add_argument("--json", action="store_true",
                        help="Emit this wrapper's own verdict as JSON instead of prose "
                             "(independent of any --json passed through to run_tests.gd)")
    parser.add_argument("passthrough", nargs=argparse.REMAINDER,
                        help="Args after -- are forwarded to run_tests.gd verbatim")
    args = parser.parse_args()

    passthrough = list(args.passthrough)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    project_path = Path(args.project).expanduser().resolve()
    if not (project_path / "project.godot").is_file():
        print(f"Error: no project.godot in {project_path}. Run from the project root "
              "or pass --project/-p.", file=sys.stderr)
        sys.exit(2)

    godot_path = _resolve_godot(args, project_path)

    devtools_dir = project_path / ".devtools"
    devtools_dir.mkdir(exist_ok=True)
    log_path = devtools_dir / "tests.log"

    try:
        godot_rc = run_suite(godot_path, project_path, log_path, passthrough, args.timeout)
    except subprocess.TimeoutExpired:
        print(f"Error: the suite did not finish within {args.timeout}s; partial output "
              f"in {log_path}. Nothing was verified.", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"Error: could not run {godot_path}: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        captured = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Error: the suite ran but its log could not be read back ({exc}). "
              "Nothing was verified.", file=sys.stderr)
        sys.exit(2)

    if not captured.strip():
        print(f"Error: Godot exited {godot_rc} but wrote no output to {log_path}, so "
              "the suite could not be checked. On Windows use the *_console.exe build.",
              file=sys.stderr)
        sys.exit(2)

    # Print run_tests.gd's own output unchanged -- the per-test [PASS]/[FAIL]/[VACU]
    # listing and denominators are correct and worth keeping exactly as they read.
    print(captured, end="" if captured.endswith("\n") else "\n")

    findings = scan_output(captured)
    # run_tests.gd's own exit code: 0 all passed, 1 failures/vacuous, 2 could not run.
    # A nonzero error count overrides a reported 0 -- that is the entire point of this
    # wrapper -- but never downgrades a run_tests.gd exit 2 (could not run at all).
    if godot_rc == 2:
        exit_code = 2
    elif findings or godot_rc != 0:
        exit_code = 1
    else:
        exit_code = 0

    if args.json:
        print(json.dumps({
            "harness_version": HARNESS_VERSION,
            "project": str(project_path),
            "godot": str(godot_path),
            "godot_exit_code": godot_rc,
            "log": str(log_path),
            "error_count": len(findings),
            "errors": findings,
            "exit": exit_code,
        }, indent=2))
        sys.exit(exit_code)

    # `exit_code`, computed once above, is the only source of truth from here down --
    # every branch below only decides what to PRINT, never re-derives exit_code, so a
    # future edit to the decision cannot diverge from the exit it actually takes.
    if findings:
        print("")
        print(f"Errors: {len(findings)} emitted during the suite "
              f"(run_tests.gd itself reported exit {godot_rc})")
        for finding in findings[:40]:
            print(f"  {log_path.name}:{finding['line_no']}: {finding['text']}")
            if finding["at"]:
                print(f"      {finding['at']}")
        if len(findings) > 40:
            print(f"  ... and {len(findings) - 40} more line(s); full output: {log_path}")
        if godot_rc == 0:
            print("run_tests.gd reported ALL TESTS PASSED, but an aborted coroutine "
                  "returns \"\" for a -> String test -- identical to a genuine pass. "
                  "At least one test above did not finish; treat the run as FAILED.")
    elif godot_rc != 0:
        # run_tests.gd already explains its own failure/vacuous tally in the output
        # printed above; nothing to add except the exit code it earned.
        pass
    else:
        print(f"Errors: 0 emitted during the suite. Full log: {log_path}")

    # moving-in:G-054: written-vs-executed assertion count, suite level, advisory.
    # Skipped under --filter/--file, where the denominator is not the whole dir.
    selected = any(tok in ("--filter", "--file") or tok.startswith(("--filter=", "--file="))
                   for tok in passthrough)
    m = _EXECUTED_RE.search(captured)
    if not selected and m:
        declared, nfiles = declared_assertions(project_path)
        executed = int(m.group(1))
        if declared:
            line = f"Declared: {declared} assertion call site(s) across {nfiles} test file(s); {executed} executed"
            if executed < declared:
                line += (f" -- {declared - executed} written but not run this suite: a test that "
                         "aborted before reaching them, or a branch nothing exercised (advisory)")
            elif executed > declared:
                line += " (loops or helpers run some sites more than once)"
            print(line)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
