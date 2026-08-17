#!/usr/bin/env python3
"""import_check.py - run Godot's `--import` and fail when the project no longer parses.

Why this exists: `godot --headless --path . --import` exits 0 whether or not the
scripts it just re-scanned actually compile. A real run looked like this --

    import exit=0
    SCRIPT ERROR: Parse Error: Cannot infer the type of "walk" variable
       at: GDScript::reload (res://player/player.gd:446)
    ERROR: Failed to load script "res://items/items.gd" with error "Parse error".

-- so "the class cache was regenerated" and "the project still parses" are the same
exit code, and a broken game reports as a clean import. The parse errors are printed,
never returned; nothing downstream of the exit code can see them.

The failure is worst in exactly the case `--import` is run for: a `class_name` that
arrived from a rebase, a pull or a branch switch. One missing entry in
`.godot/global_script_class_cache.cfg` cascades into every file that names the type,
and the tool you ran to fix it told you it had succeeded.

So this wrapper runs the same import, captures stdout and stderr to `.devtools/import.log`,
and scans that log for the signals Godot only ever prints on a real failure. Findings
are quoted back, not counted, so the terminal output is enough to act on.

Exit codes follow the harness convention (`lint_project.gd`, `run_tests.gd`):
    0  the import ran AND the captured output is clean
    1  the import ran but the output contains parse / load errors  <- the whole point
    2  could not run at all: no Godot binary, no project.godot, no output captured,
       Godot crashed, or the import timed out

Usage:
    python tools/import_check.py                       # import the project in .
    python tools/import_check.py -p ../other-game
    python tools/import_check.py --godot /path/to/godot
    python tools/import_check.py --json                # machine-readable verdict

(`python` on Windows -- `python3` there is a Microsoft Store alias stub that satisfies
`command -v` and then refuses to run.)

Godot binary resolution matches `devtools.py launch`:
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

# harness-version: 0.59.0
HARNESS_VERSION = "0.59.0"

# Substrings that mean the import did not leave a parseable project behind. Every one
# of these is taken from real captured output, not from guesswork:
#
#   SCRIPT ERROR         the GDScript compiler reporting on a script it re-loaded
#   Parse Error          the specific form above; listed separately because Godot also
#                        prints "Parse Error:" under other prefixes
#   Failed to load script  a dependent file that could not be compiled (the cascade)
#   Compilation failed   the same cascade when the dependency is a missing class_name
#
# Matching is deliberately literal and case-insensitive substring, not a regex over
# Godot's message grammar -- the grammar changes between engine versions, and a scan
# that silently stops matching is the failure mode this tool exists to prevent.
FAILURE_SIGNALS = (
    "SCRIPT ERROR",
    "Parse Error",
    "Failed to load script",
    "Compilation failed",
)

# Godot decorates progress lines with SGR colour codes; error lines have been plain in
# every sample so far, but strip them before matching so a coloured one still matches.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# "   at: GDScript::reload (res://player/player.gd:446)" -- the continuation line that
# carries the file and line number. Printed alongside its error so the quote is actionable.
_AT_RE = re.compile(r"^\s+at: ")

# Enough to see the shape of a cascade without flooding a terminal; a stale class cache
# has produced 135 SCRIPT ERROR lines from one missing entry.
MAX_QUOTED = 40


def _read_harness_config(project_path: Path) -> dict:
    """addons/godot_selftest/devtools_config.json as a dict; {} when unreadable.

    Deliberately duplicated from devtools.py rather than imported. This tool has to
    work when the bridge client is the thing that is broken -- a parse error in a
    template, a half-refreshed install, a devtools.py that will not import -- and a
    checker that cannot run when the project is broken checks nothing. It is a dict
    read and a three-line precedence chain; the copy is cheaper than the coupling.
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
    """Return [(line_no, line, [signals]), ...] for every failing line in the import log.

    Continuation lines ("   at: ...") are attached to the finding above them rather than
    reported separately, so each finding quotes both the error and where it happened.
    """
    findings = []
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        line = _ANSI_RE.sub("", raw).rstrip()
        matched = [s for s in FAILURE_SIGNALS if s.lower() in line.lower()]
        if not matched:
            continue
        detail = ""
        if index + 1 < len(lines):
            nxt = _ANSI_RE.sub("", lines[index + 1]).rstrip()
            if _AT_RE.match(nxt):
                detail = nxt.strip()
        findings.append({
            "line_no": index + 1,
            "text": line.strip(),
            "at": detail,
            "signals": matched,
        })
    return findings


def run_import(godot_path: Path, project_path: Path, log_path: Path, timeout: int):
    """Run the import with stdout+stderr going to log_path. Returns Godot's exit code.

    Output goes to a file, never subprocess.PIPE -- an unread pipe fills its buffer and
    stalls Godot on Windows, which is how `launch` in devtools.py does it too. The file
    also answers the other Windows gotcha: the non-console Godot build often shows
    nothing in the terminal, so an empty console is not evidence of a clean run. Reading
    the log back is the only honest check, and this tool reads it back either way.
    """
    cmd = [str(godot_path), "--headless", "--path", str(project_path), "--import"]
    with log_path.open("w", encoding="utf-8") as log_f:
        proc = subprocess.run(cmd, stdout=log_f, stderr=subprocess.STDOUT,
                              cwd=str(project_path), timeout=timeout)
    return proc.returncode


# Ceiling on --import attempts when each crash still made progress (G-044).
IMPORT_MAX_ATTEMPTS = 4


def _imported_artifacts(imported_dir):
    """Finished (non-.tmp) files under .godot/imported, by name."""
    if not imported_dir.is_dir():
        return set()
    return {f.name for f in imported_dir.iterdir() if f.is_file() and f.suffix != ".tmp"}


_REIMPORT_RE = re.compile(r"\[\s*\d+%\s*\]\s*reimport\s*\|\s*(\S.*)")


def sweep_import_tmp(project_path):
    """Delete the `<asset>.import*.tmp` files a crashed --import leaves beside the
    assets (plant-tower-defense:G-044, 7th sighting). They are the engine's own
    write-then-rename temporaries for `.import` metadata; a crash mid-write strands
    them, they show up as untracked in `git status` and invite being committed.
    Only names containing `.import` and ending in `.tmp` (any case), never anything
    under `.godot/`. Returns the deleted paths, relative to the project.
    """
    swept = []
    for path in project_path.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if ".import" not in name or not name.endswith(".tmp"):
            continue
        rel = path.relative_to(project_path)
        if rel.parts and rel.parts[0] == ".godot":
            continue
        try:
            path.unlink()
        except OSError:
            continue
        swept.append(rel.as_posix())
    return swept


def _last_reimport_line(captured):
    """The last `[ N% ] reimport | file` line, i.e. the asset the crash happened on."""
    last = ""
    for m in _REIMPORT_RE.finditer(captured):
        last = m.group(1).strip()
    return last


def _utf8_console() -> None:
    """gh#34: the client inherits the Windows console's cp1252 stdout, so any verb
    echoing game text with a glyph outside it (a Back button reading "\u2190 Back",
    a key legend using arrows) died with UnicodeEncodeError - and the traceback read
    as "this verb is broken on this node", not "the reporting is". `errors="replace"`
    rather than bare utf-8: a console that genuinely cannot render a glyph shows `?`
    and keeps going, instead of trading one crash for another."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main():
    _utf8_console()
    parser = argparse.ArgumentParser(
        description="Run Godot's --import and exit non-zero when the project no longer parses.",
        epilog="Exit codes: 0 imported clean, 1 parse/load errors in the import output, "
               "2 could not run.")
    parser.add_argument("--project", "-p", help="Path to Godot project", default=".")
    parser.add_argument("--godot", help="Godot binary (else $GODOT_BIN, else config godot_bin)")
    parser.add_argument("--timeout", type=int, default=900,
                        help="Seconds to wait for the import before giving up (default: 900)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the verdict as JSON instead of prose")
    args = parser.parse_args()

    project_path = Path(args.project).expanduser().resolve()
    if not (project_path / "project.godot").is_file():
        print(f"Error: no project.godot in {project_path}. Run from the project root "
              "or pass --project/-p.", file=sys.stderr)
        sys.exit(2)

    godot_path = _resolve_godot(args, project_path)

    devtools_dir = project_path / ".devtools"
    devtools_dir.mkdir(exist_ok=True)
    log_path = devtools_dir / "import.log"

    # plant-tower-defense:G-044: --import segfaulted (exit 139) on its first call of a
    # session and imported clean on an immediate retry with nothing else changed - same
    # cause as the shared .godot/ class-cache contention this repo already knows about
    # (findmyballs:G-004 territory), a harder failure mode of it. A crash produces an
    # abnormal exit with NO recognizable parse-error text, which this scan can already
    # tell apart from a genuine reported failure - so retry exactly that shape once,
    # transparently, before surfacing it as "could not run". A real parse error (findings
    # present) is never retried: retrying would not fix broken code and could reorder a
    # cascade's line numbers between attempts.
    attempt = 0
    imported_dir = project_path / ".godot" / "imported"
    imported_before = _imported_artifacts(imported_dir)
    progress_marker = (len(imported_before), "")
    while True:
        attempt += 1
        try:
            godot_rc = run_import(godot_path, project_path, log_path, args.timeout)
        except subprocess.TimeoutExpired:
            print(f"Error: import did not finish within {args.timeout}s; partial output in "
                  f"{log_path}. Nothing was verified.", file=sys.stderr)
            sys.exit(2)
        except OSError as exc:
            print(f"Error: could not run {godot_path}: {exc}", file=sys.stderr)
            sys.exit(2)

        try:
            captured = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Error: import ran but its log could not be read back ({exc}). "
                  "Nothing was verified.", file=sys.stderr)
            sys.exit(2)

        crashed_with_no_findings = (
            godot_rc != 0 and captured.strip() and not scan_output(captured))
        # plant-tower-defense:G-044, 5th sighting: one retry was not enough - the
        # same importer crashed twice at the same .ogg and a THIRD --import wrote
        # the cache. So: retry while a retry is still making progress (the count
        # of finished artifacts under .godot/imported grew, or the last reimport
        # line moved), up to IMPORT_MAX_ATTEMPTS; a crash that gained nothing
        # twice running is reported, not retried forever.
        if crashed_with_no_findings and attempt < IMPORT_MAX_ATTEMPTS:
            gained_now = len(_imported_artifacts(imported_dir))
            last_line = _last_reimport_line(captured)
            progressed = attempt == 1 or gained_now > progress_marker[0] \
                or (last_line and last_line != progress_marker[1])
            progress_marker = (gained_now, last_line)
            swept = sweep_import_tmp(project_path)
            if swept:
                print(f"Note: swept {len(swept)} stranded .import temp file(s) the crash left "
                      f"beside the assets: {', '.join(swept[:5])}"
                      f"{', ...' if len(swept) > 5 else ''}", file=sys.stderr)
            if progressed:
                print(f"Note: godot --import exited {godot_rc} with no recognizable parse/load "
                      f"error in its output - a crash signature (plant-tower-defense:G-044), not "
                      f"a reported failure. Retrying (attempt {attempt + 1} of "
                      f"{IMPORT_MAX_ATTEMPTS}; {gained_now} finished artifact(s) so far"
                      f"{f', last at {last_line}' if last_line else ''}). "
                      f"Crashed attempt's log: {log_path}", file=sys.stderr)
                continue
            print(f"Note: attempt {attempt} crashed again with no new artifact and the same "
                  f"last file ({last_line or '?'}); not retrying further.", file=sys.stderr)
        break

    # No output at all is not a clean run -- it is an unverified one. This is the shape
    # the Windows non-console build takes when its output goes nowhere, and reporting it
    # as success would recreate the exact trap this tool exists to close.
    if not captured.strip():
        print(f"Error: Godot exited {godot_rc} but wrote no output to {log_path}, so the "
              "import could not be checked. On Windows use the *_console.exe build.",
              file=sys.stderr)
        sys.exit(2)

    findings = scan_output(captured)

    if args.json:
        print(json.dumps({
            "harness_version": HARNESS_VERSION,
            "project": str(project_path),
            "godot": str(godot_path),
            "godot_exit_code": godot_rc,
            "log": str(log_path),
            "clean": not findings,
            "finding_count": len(findings),
            "findings": findings,
        }, indent=2))
        sys.exit(1 if findings else (0 if godot_rc == 0 else 2))

    if findings:
        print(f"IMPORT FAILED: {len(findings)} parse/load error line(s) in the import output "
              f"(Godot itself exited {godot_rc}).")
        for finding in findings[:MAX_QUOTED]:
            print(f"  {log_path.name}:{finding['line_no']}: {finding['text']}")
            if finding["at"]:
                print(f"      {finding['at']}")
        if len(findings) > MAX_QUOTED:
            print(f"  ... and {len(findings) - MAX_QUOTED} more line(s); full output: {log_path}")
        print(f"Full import log: {log_path}")
        print("The class cache was regenerated, but the project does not parse. Fix the "
              "script above, then re-run.")
        sys.exit(1)

    if godot_rc != 0:
        print(f"Error: no parse/load errors in the output, but Godot exited {godot_rc} - "
              f"treat this as an import that did not complete. Full log: {log_path}",
              file=sys.stderr)
        # plant-tower-defense:G-044 (4th sighting): the same importer crashed at the
        # same .ogg on every attempt in a fresh worktree, and the retry above did
        # nothing. Say what the crash left behind - a .godot/imported that gained no
        # finished artifact is the signature - and name the legal way out: the
        # import cache is keyed on res:// paths, identical across worktrees of one
        # project, so a sibling checkout's .godot/imported (+ uid_cache.bin,
        # scene_groups_cache.cfg) seeds this one.
        after = _imported_artifacts(imported_dir)
        gained = len(after - imported_before)
        swept = sweep_import_tmp(project_path)
        if swept:
            print(f"  Swept {len(swept)} stranded .import temp file(s): {', '.join(swept[:5])}"
                  f"{', ...' if len(swept) > 5 else ''}", file=sys.stderr)
        tmp_files = sum(1 for f in imported_dir.glob("*.tmp")) if imported_dir.is_dir() else 0
        last = _last_reimport_line(captured)
        print(f"  .godot/imported gained {gained} finished artifact(s) across this run"
              f"{f' and holds {tmp_files} .tmp file(s)' if tmp_files else ''}"
              f"{f'; the log ends at: {last}' if last else ''}.", file=sys.stderr)
        if gained == 0:
            print("  The import made no progress. If this is a worktree or fresh clone of a "
                  "project that imports cleanly elsewhere, seed the cache from that checkout: "
                  "copy its .godot/imported/, .godot/uid_cache.bin and "
                  ".godot/scene_groups_cache.cfg here (the cache is keyed on res:// paths, "
                  "so it is byte-identical across checkouts), then re-run.", file=sys.stderr)
        sys.exit(2)

    line_count = len(captured.splitlines())
    print(f"Import OK: the class cache regenerated and godot --import's {line_count} line(s) "
          "of output contain no SCRIPT ERROR, Parse Error, Failed to load script or "
          "Compilation failed.")
    print("NOT COVERED: --import registers global class names; it does not compile function "
          "or const bodies. A const whose initializer is not a constant expression, and "
          "anything else that fails only at compile time, passes this gate. "
          "lint_project.gd runs the compile pass.")
    print(f"Full import log: {log_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
