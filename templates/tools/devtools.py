#!/usr/bin/env python3
"""
devtools.py - Generic CLI for interacting with a running Godot instance via the
godot-selftest-harness DevTools autoload.

Commands are written as JSON to user://devtools_commands.json and results are
read back from user://devtools_results.json. The DevTools autoload polls for
commands and writes results. This client is completely game-agnostic: it ships
only the generic verbs the harness core registers, plus two escape hatches
(`cmd` and `list-commands`) so any project-registered verb is reachable without
editing this file.

Usage:
    python3 tools/devtools.py ping                     # Check if game is running
    python3 tools/devtools.py screenshot               # Capture screenshot
    python3 tools/devtools.py validate-all             # Validate all scenes
    python3 tools/devtools.py scene-tree               # Get node hierarchy
    python3 tools/devtools.py performance              # Get FPS, memory, etc.
    python3 tools/devtools.py get-state --node "/root/Main/Player"
    python3 tools/devtools.py get-state --node "/root/Main/HUD" --property visible --property size
    python3 tools/devtools.py set-state --node "/root/Main/Player" --property health --value 100
    python3 tools/devtools.py step-time --seconds 0.3   # Advance the paused tree
    python3 tools/devtools.py step-time --seconds 2 --hold move_left
    python3 tools/devtools.py touch press --index 0 --pos 640,360
    python3 tools/devtools.py key E                     # Raw keyboard event
    python3 tools/devtools.py input state               # Pressed/strength per action
    python3 tools/devtools.py tilemap-cells --node /root/Main/TileMap --rect 0,0,16,16
    python3 tools/devtools.py tilemap-region --node /root/Main/TileMap --atlas 3,1
    python3 tools/devtools.py scripts-seen              # Script census since launch
    python3 tools/devtools.py launch --isolated         # Start the game detached
    python3 tools/devtools.py set-feature --touchscreen true
    python3 tools/devtools.py --json ping               # Any verb, raw reply JSON
    python3 tools/devtools.py list-commands            # Discover all registered verbs
    python3 tools/devtools.py harness-version          # Which harness revision is installed
    python3 tools/devtools.py cmd my_project_verb --args '{"foo": 1}'
    python3 tools/devtools.py quit

Project selection:
    Run from the project root or pass --project/-p <path>.

Liveness precheck:
    The DevTools autoload deletes the command file the moment it picks it up, so
    a command file that is still on disk a couple of seconds later means nothing
    is polling that directory. Every command therefore fails fast with
    "game not running" instead of blocking for the full timeout. Pass
    --no-precheck to disable it.

Concurrency:
    The bridge is a single command/result file pair, so it is still one in-flight
    command at a time. Each request now carries a unique "id" and the client
    refuses to return a reply stamped with somebody else's id, which turns a
    crossed reply from silent data corruption into a clear error.

    For genuinely parallel work, give each instance its own bus with --session:

        godot --path . -- --devtools-session a &
        python3 tools/devtools.py --session a ping

    The id is spliced into the bus filenames (devtools_commands_a.json, ...), so
    N instances can share one user:// dir without answering each other's
    commands. Without a session the filenames are unchanged, so nothing about
    existing usage moves.

    `launch --isolated` also gives the instance its own bus DIRECTORY, passed as
    `-- --devtools-busdir <dir>`, and proves the bus answers before printing the
    follow-up command. What it still does NOT do is move user:// - Godot resolves
    that inside the engine and honours no flag or environment variable for it, so
    screenshots, saves and UI baselines stay shared. `ping` reports bus_dir and
    user_dir separately so the difference is a read, not a promise.

User data path resolution (highest priority first):
    1. --userdata <path>                                (global CLI flag)
    2. GODOT_USERDATA environment variable
    3. project.godot: application/config/use_custom_user_dir +
       application/config/custom_user_dir_name
    4. Per-platform default: <data dir>/Godot|godot/app_userdata/<config name>
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional  # noqa: F401


# harness-version: 0.44.0
# Version of the godot-selftest-harness this client was copied from. Compared against
# the running game's own stamp by the `harness-version` verb, so a half-refreshed
# install (new client, old autoload) is visible instead of mysterious.
HARNESS_VERSION = "0.44.0"

COMMANDS_FILE = "devtools_commands.json"
RESULTS_FILE = "devtools_results.json"
LOG_FILE = "devtools_log.jsonl"

# Set from the global --session flag (or GODOT_DEVTOOLS_SESSION). Empty means the
# shared default bus, i.e. the historical behavior.
_SESSION = ""

# Same character class the autoload's _resolve_session() accepts: the id becomes part
# of a filename, so anything outside it is dropped rather than escaped.
_SESSION_SAFE = re.compile(r"[^A-Za-z0-9_-]")

# How long to wait for the game to *consume* the command file before declaring
# it dead. The autoload polls every ~100 ms, so 2.0s is ~20 poll cycles of
# slack: far more than a loaded machine, a GC pause, or a mid-frame hitch can
# eat, but ~15x faster than letting the default 30s response timeout fire.
PRECHECK_SECONDS = 2.0

# Poll interval while waiting for the command file to be picked up.
_PRECHECK_POLL = 0.05

# Set once in main() from the global --userdata flag. Takes priority over
# every other user-data resolution mechanism when non-empty.
_USERDATA_OVERRIDE: Optional[str] = None

# Cleared by the global --no-precheck flag.
_PRECHECK_ENABLED = True

# Arg keys whose value is a Godot node path and therefore needs un-mangling.
# See normalize_node_path(). "node_path" is used by get_state / set_state /
# run_method / get_node_bounds and by most project verbs; "node" is the key
# input_sequence assert steps use.
_NODE_PATH_KEYS = ("node_path", "node")


def sanitize_session(session):
    """Mirror the autoload's session sanitization. Must stay in lockstep with it."""
    return _SESSION_SAFE.sub("", session or "")


def bus_filenames(session=None):
    """(commands, results, log) filenames for a session id.

    Splices the id in before the extension, exactly as `_resolve_session()` does on
    the GDScript side. These two are the only halves that have to agree; if they ever
    disagree the client polls a file nothing writes, which looks identical to a dead
    game.
    """
    s = sanitize_session(_SESSION if session is None else session)
    if not s:
        return COMMANDS_FILE, RESULTS_FILE, LOG_FILE
    return (
        "devtools_commands_%s.json" % s,
        "devtools_results_%s.json" % s,
        "devtools_log_%s.jsonl" % s,
    )


def _breadcrumb_path(user_data: Path) -> Path:
    """The `died inside this verb` record for the current session.

    Mirrors _write_breadcrumb in dev_tools.gd. Present on disk means one thing
    only: some process entered a handler and never came back out of it.
    """
    s = sanitize_session(_SESSION)
    name = "devtools_last_command.json" if not s else "devtools_last_command_%s.json" % s
    return user_data / name


def _read_breadcrumb(user_data: Path):
    """The breadcrumb dict, or None. Never raises."""
    try:
        data = json.loads(_breadcrumb_path(user_data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _breadcrumb_note(user_data: Path) -> str:
    """One line naming the verb a dead instance died inside, or ''.

    A project verb took the game down and the only trace was a log line saying
    the verb had STARTED, which reads the same as a verb still running - so "the
    game died during give_item" had to be inferred (gather:G-103).
    """
    crumb = _read_breadcrumb(user_data)
    if not crumb:
        return ""
    return (
        "\nA '{action}' (pid {pid}) was still in its handler when the process "
        "stopped: {file} was never cleared. If the game is gone, that verb is "
        "what took it down.".format(
            action=crumb.get("action", "?"),
            pid=crumb.get("pid", "?"),
            file=_breadcrumb_path(user_data).name,
        )
    )


def _handler_in_flight(user_data: Path) -> bool:
    """Is a live game currently INSIDE a handler, with our command deferred behind it?

    Two facts, and both are needed (H-038):

      - a breadcrumb exists, so some process entered a handler and has not come back
        out of it. On its own this is ambiguous by design - the same file is what
        names the verb that took a game DOWN (gather:G-103), and a corpse leaves it
        behind forever.
      - the owner's heartbeat is fresh, so that process is still polling. _process
        keeps ticking (and keeps writing last_poll_unix) while a handler awaits, so a
        busy game is live-and-listening; a dead one goes stale within seconds.

    An owner with no heartbeat at all is a pre-0.12.0 game: unknown, never assumed
    live. Those builds have no re-entrancy guard either, so they never defer, and the
    old fail-fast behavior is the right one for them.
    """
    if not _read_breadcrumb(user_data):
        return False
    owner, _ = _read_owner(user_data)
    if owner is None:
        return False
    age = poll_age(owner)
    return age is not None and age <= POLL_STALE_AFTER_SEC


class BridgeError(TimeoutError):
    """Base for every "the bridge did not answer" failure.

    Subclasses TimeoutError so pre-existing `except TimeoutError` callers (and
    any project script wrapping this module) keep working unchanged.
    """


class GameNotRunningError(BridgeError):
    """The command file was never picked up: nothing is polling user://."""


class CrossedReplyError(BridgeError):
    """Replies arrived, but all of them were stamped for a different request."""


class NoReplyError(BridgeError):
    """The command was picked up but no reply ever appeared."""


class ForeignInstanceError(BridgeError):
    """A reply arrived, but from a process that does not own this bus (G-036a).

    The autoload writes user://devtools_owner*.json at startup with its pid; a
    reply stamped with a different pid means another Godot instance is answering
    on a bus it no longer (or never) owned - typically a stale instance that
    predates the current owner, or two instances launched without --session.
    """


class _RawJsonPrinted(Exception):
    """Control flow for the global --json flag: the raw reply has already been
    printed by send_command, so the per-command formatter must not run. Carries
    the exit code (0 on success:true, 1 otherwise)."""

    def __init__(self, code):
        super().__init__("raw json printed")
        self.code = code


# Set by the global --json flag: print every bus reply as the raw dict
# (json.dumps, indent=2) instead of the per-command formatted view (G-039).
_RAW_JSON = False


def _owner_file_path(user_data: Path) -> Path:
    """The bus-identity file for the current session, mirroring the autoload's
    session splicing (see _write_owner_file in dev_tools.gd)."""
    s = sanitize_session(_SESSION)
    name = "devtools_owner.json" if not s else "devtools_owner_%s.json" % s
    return user_data / name


def _read_owner(user_data: Path):
    """(owner_dict_or_None, path). Never raises: a missing or garbled owner file
    just means nothing is known about the bus owner."""
    path = _owner_file_path(user_data)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, path
    return (data if isinstance(data, dict) else None), path


def pid_alive(pid) -> bool:
    """Is this pid a live process? Unknown counts as alive.

    Used to tell "the owner file is stale after a crash" (the normal case) from
    "another instance really does own this bus" (the dangerous one). Erring
    towards alive is deliberate: wrongly declaring a live instance dead would
    clear an owner file that is doing its job, which is the exact corruption the
    owner file exists to prevent.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, we just may not signal it
    except OSError:
        return True
    except Exception:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness via OpenProcess + GetExitCodeProcess (gh#6).

    `os.kill(pid, 0)` cannot answer this on Windows: for a pid that no longer
    exists it raises WinError 87 (ERROR_INVALID_PARAMETER), which is a plain
    OSError, and the tolerant mapping above read that as "alive". So `quit`
    could never see the game exit and warned `STILL ALIVE` after every --wait,
    naming a pid `tasklist` no longer had - the reporter, correctly, stopped
    reading that exit code. Measured here: a headless game that had exited was
    reported alive for the whole 13s window. Unknown still counts as alive, for
    the reason the docstring above gives; only a definite answer says dead.
    """
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # 87 = no such process. 5 = exists but is not ours to open (alive).
            return ctypes.get_last_error() != 87
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return True


def _pid_started_unix(pid: int):
    """Process creation time as a unix timestamp, or None when unknowable.

    A pid alone is not an identity - Windows recycles them onto unrelated
    processes (findmyballs:G-004) - so a ledger of pids this client launched has
    to be checked against WHEN each was started before it names anything a
    survivor. Windows: OpenProcess + GetProcessTimes. Linux: /proc/<pid>/stat
    field 22 against btime and CLK_TCK. Elsewhere: None, and the caller treats
    the pid as unverifiable rather than as matching.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                created = wintypes.FILETIME()
                exited = wintypes.FILETIME()
                kern = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                           ctypes.byref(kern), ctypes.byref(user)):
                    return None
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                return ticks / 10_000_000 - 11644473600  # 100ns since 1601 -> unix
            finally:
                k32.CloseHandle(handle)
        stat = Path("/proc/%d/stat" % pid).read_text(encoding="utf-8", errors="replace")
        fields = stat.rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])  # field 22, 1-based, after the (comm) split
        btime = None
        for line in Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
        if btime is None:
            return None
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def _kill_pid(pid: int) -> bool:
    """Terminate one pid this client launched. Never by image name (H-056)."""
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if not handle:
                return False
            try:
                return bool(k32.TerminateProcess(handle, 1))
            finally:
                k32.CloseHandle(handle)
        os.kill(pid, 9)
        return True
    except Exception:
        return False


def _kill_hint(pids) -> str:
    """The kill command(s) to print for a survivor, in every shell the reader
    might be in.

    `taskkill /F /PID n` is right for cmd.exe and WRONG through Git-Bash/MSYS,
    which rewrites `/F` into a phantom path and fails with
    `Invalid argument/option - 'F:/'` - a message that reads as a bad path, not a
    mangled flag, and cost a project five identical rounds (gh#12, plant:G-009).
    So on Windows both forms are printed, with the symptom named.
    """
    pids = [int(p) for p in pids]
    if sys.platform == "win32":
        return ("  python tools/devtools.py quit --kill        (terminates exactly these pids)\n"
                "  Stop-Process -Force -Id %s        (PowerShell)\n"
                "  taskkill /F %s        (cmd.exe ONLY - through Git-Bash/MSYS the /F becomes "
                "'F:/' and it fails with \"Invalid argument/option - 'F:/'\"; use "
                "MSYS_NO_PATHCONV=1 or the PowerShell form there)"
                % (",".join(str(p) for p in pids), " ".join("/PID %d" % p for p in pids)))
    return "  kill -9 %s" % " ".join(str(p) for p in pids)


LAUNCH_LEDGER_NAME = "launched.jsonl"


def _launch_ledger_path(project_path: Path) -> Path:
    return project_path / ".devtools" / LAUNCH_LEDGER_NAME


def _ledger_record(project_path: Path, pid: int, role: str, extra: dict = None) -> None:
    """Append one pid this client started (or saw answer the bus) to
    .devtools/launched.jsonl. The owner file names only the CURRENT owner and
    launch.json only the LAST launch, so a game abandoned two launches ago fell
    out of both and no later `quit` could see it (gh#14.1). This ledger is what
    lets quit/launch sweep everything this project ever started."""
    started = _pid_started_unix(pid)
    row = {"pid": int(pid), "role": role, "started_unix": started if started else time.time(),
           "started_verified": started is not None, "recorded_unix": time.time()}
    if extra:
        row.update(extra)
    try:
        path = _launch_ledger_path(project_path)
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _ledger_survivors(project_path: Path, exclude=()):
    """Every ledger pid still alive AND provably the process we started.

    Alive-and-same-start-time is the test: a recycled pid whose creation time
    is not within a few seconds of the recorded one is someone else's process
    and is left alone. A pid whose start time cannot be read at all, having
    been readable when the ledger recorded it, is the same case one layer
    earlier (gh#24) - not left alone, not reported at all. Only a pid that was
    NEVER readable (never `started_verified`) is genuinely ambiguous; that one
    is reported with `verified=None` and never killed automatically.
    """
    path = _launch_ledger_path(project_path)
    if not path.is_file():
        return []
    rows = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("pid"), int):
                rows[row["pid"]] = row  # last record for a pid wins
    except OSError:
        return []
    out = []
    for pid, row in rows.items():
        if pid in exclude or pid == os.getpid() or not pid_alive(pid):
            continue
        now_started = _pid_started_unix(pid)
        recorded = float(row.get("started_unix") or 0)
        if now_started is None and row.get("started_verified"):
            # We could read this pid's start time when we launched it (gh#24).
            # That we cannot now - not even to query it - is not ambiguity: on
            # Windows, OpenProcess with PROCESS_QUERY_LIMITED_INFORMATION uses
            # the same access right pid_alive() just used to call this pid
            # alive, so if it were still OUR process the query would still
            # succeed. It is someone else's process recycled onto the same pid
            # number - not ours, not a survivor, not reported.
            continue
        if now_started is None or not row.get("started_verified"):
            verified = None  # genuinely cannot tell; report, never kill
        else:
            verified = abs(now_started - recorded) < 5.0
            if not verified:
                continue  # a recycled pid: not ours
        out.append({"pid": pid, "role": str(row.get("role", "?")),
                    "started_unix": recorded, "verified": verified})
    out.sort(key=lambda r: r["started_unix"])
    return out


def _describe_survivors(rows) -> str:
    parts = []
    for r in rows:
        when = time.strftime("%H:%M:%S", time.localtime(r["started_unix"]))
        tag = "" if r["verified"] else "  (start time unverifiable - not auto-killed)"
        parts.append("  pid %d  %-8s started %s%s" % (r["pid"], r["role"], when, tag))
    return "\n".join(parts)


def _kill_survivors(rows) -> list:
    """Kill the verified survivors; returns the pids that are gone afterwards."""
    gone = []
    for r in rows:
        if not r["verified"]:
            continue
        _kill_pid(r["pid"])
    deadline = time.time() + 3.0
    while time.time() < deadline:
        gone = [r["pid"] for r in rows if r["verified"] and not pid_alive(r["pid"])]
        if len(gone) == sum(1 for r in rows if r["verified"]):
            break
        time.sleep(0.1)
    return gone


#: How many missed heartbeats before an owner counts as not polling. The game
#: writes last_poll_unix about once a second (HEARTBEAT_INTERVAL_MSEC in
#: dev_tools.gd); a few seconds of slack absorbs a slow frame or a long handler
#: without calling a healthy owner dead.
POLL_STALE_AFTER_SEC = 5.0


def poll_age(owner: dict):
    """Seconds since the owner last polled the bus, or None if it never said.

    None means an owner file written by a harness older than 0.12.0, which had
    no last_poll_unix. Callers must treat that as "unknown", never as "stale":
    an old game is not a dead one.
    """
    if not isinstance(owner, dict):
        return None
    ts = owner.get("last_poll_unix")
    if not isinstance(ts, (int, float)):
        return None
    return max(0.0, time.time() - float(ts))


def owner_status(user_data: Path) -> dict:
    """{present, pid, alive, polling, poll_age, path} for the bus owner record.
    Never raises.

    `alive` is a pid check and `polling` is a heartbeat check, and the second is
    the one worth believing (findmyballs:G-004). A pid can be alive and still
    not own this bus: Windows recycles pids, so a dead Godot's number was found
    attached to an unrelated process and reported STILL ALIVE while `launch`
    refused to start. A pid can also be alive AND the real owner AND not
    answering - a paused tree does exactly that, and the message it produced
    ("running but not polling THIS directory") sent a project debugging its
    --userdata for a cycle.

    polling is None when the owner file predates 0.12.0 and carries no
    heartbeat. Unknown is not stale; callers say so rather than guessing.

    Read BEFORE the command is written, so the very first call can say what it
    knows instead of leaving the caller to interpret an empty response
    (gather:G-100).

    It deliberately does NOT delete a stale record, even though a dead owner
    after a crash is the normal condition rather than an anomaly. The owner pid
    is the only thing the reply-pid check has to compare against: deleting it
    would turn "a survivor from an earlier run answered your command" - the
    single most corrupting failure this bridge has - into silence. Staleness is
    reported instead, which fixes the thing that actually hurt (a clean relaunch
    reading as a second failure stacked on the first) without trading detection
    for tidiness.
    """
    owner, path = _read_owner(user_data)
    if owner is None:
        return {
            "present": False, "pid": None, "alive": False,
            "polling": False, "poll_age": None, "path": path, "project_path": None,
        }
    pid = owner.get("pid")
    age = poll_age(owner)
    return {
        "present": True,
        "pid": pid,
        "alive": pid_alive(pid),
        "polling": None if age is None else age <= POLL_STALE_AFTER_SEC,
        "poll_age": age,
        "path": path,
        # Absent on an owner file written before 0.22.0.
        "project_path": owner.get("project_path") or None,
    }


def _same_project_dir(a, b) -> bool:
    """Do two project directory spellings name the same checkout? Tolerates
    slash direction, a trailing slash, and case on Windows - the game reports
    `ProjectSettings.globalize_path("res://")` (forward slashes, trailing slash),
    the client holds whatever --path was typed."""
    if not a or not b:
        return True
    try:
        na = os.path.normcase(os.path.normpath(os.path.abspath(str(a))))
        nb = os.path.normcase(os.path.normpath(os.path.abspath(str(b))))
    except (OSError, ValueError):
        return True
    return na == nb


def foreign_project_owner(owner: dict, project_path: Path):
    """The owner's project_path when a LIVE, POLLING owner of this bus runs from a
    different checkout than `project_path`; else None (plant-tower-defense:G-018).

    A sibling git worktree has the same project name, so the same user:// and the
    same bus files. Its game overwrites the owner record with its own pid, and
    from then on every reply's pid matches the owner - the pid check that catches a
    survivor cannot see this. Errors then arrive as `no Game in the tree` /
    `Root node not found`, i.e. as bugs in your own scene, on a game that is not
    yours. The path is the one fact that differs, and this is the one place it is
    compared. A dead or stale owner proves nothing and is left to the liveness
    logic.
    """
    if not owner or not owner.get("present") or not owner.get("alive"):
        return None
    if owner.get("polling") is False:
        return None
    other = owner.get("project_path")
    if not other or _same_project_dir(other, project_path):
        return None
    return other


def owner_liveness_phrase(owner: dict) -> str:
    """One clause describing what the owner record actually proves.

    Kept in one place because three call sites print it and they used to
    disagree, which is how "STILL ALIVE" ended up on a paused game.
    """
    pid = owner.get("pid")
    age = poll_age(owner)
    if age is None:
        state = ("that process is alive" if pid_alive(pid) else "that process is gone")
        return "%s (no heartbeat in the owner file - harness older than 0.12.0)" % state
    if age <= POLL_STALE_AFTER_SEC:
        return "that process polled the bus %.1fs ago, so it is live and listening" % age
    if pid_alive(pid):
        return (
            "that process EXISTS but last polled the bus %.0fs ago, so it is not "
            "listening: the tree is paused, the game is wedged, or the pid was "
            "recycled by an unrelated process" % age
        )
    return "that process is gone (last polled %.0fs ago); the record is stale" % age


def _parse_project_godot(project_file: Path) -> dict:
    """Extract the handful of application/config/* keys we care about.

    project.godot is an INI-like file. We only need a few flat keys from the
    [application] section, so a simple line scan (matching the `config/<key>=`
    prefix) is sufficient and avoids any INI-parser quirks with res:// values.
    """
    values: dict = {}
    with open(project_file, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            for key in ("config/name",
                        "config/use_custom_user_dir",
                        "config/custom_user_dir_name"):
                prefix = key + "="
                if line.startswith(prefix):
                    values[key] = line[len(prefix):].strip().strip('"')
    return values


def _sanitize_dir_name(name: str) -> str:
    """Mirror Godot's sanitization of custom_user_dir_name / project name.

    Godot strips characters that are invalid in a directory name. We keep it
    conservative: collapse anything outside [A-Za-z0-9_.-] into nothing (while
    preserving path separators, since Godot allows nested custom user dirs),
    which matches the common case for project names.
    """
    normalized = name.replace("\\", "/")
    return re.sub(r"[^A-Za-z0-9_.\- /]", "", normalized).strip()


def _platform_data_dir() -> Path:
    """Base OS data directory that Godot writes user data beneath."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    else:  # Linux and other unix-likes
        return Path.home() / ".local" / "share"


def get_user_data_path(project_path: Path) -> Path:
    """Resolve the user:// directory for the Godot project.

    Resolution priority:
      1. --userdata CLI flag        (_USERDATA_OVERRIDE)
      2. GODOT_USERDATA env var
      3. project.godot custom user dir (use_custom_user_dir + custom_user_dir_name)
      4. per-platform default Godot app_userdata/<config name>
    """
    # 1. Explicit CLI override.
    if _USERDATA_OVERRIDE:
        return Path(_USERDATA_OVERRIDE).expanduser()

    # 2. Environment variable override.
    env_override = os.environ.get("GODOT_USERDATA")
    if env_override:
        return Path(env_override).expanduser()

    project_file = project_path / "project.godot"
    if not project_file.exists():
        raise FileNotFoundError(f"No project.godot found in {project_path}")

    cfg = _parse_project_godot(project_file)

    project_name = cfg.get("config/name") or project_path.name

    # 3. Custom user directory (application/config/use_custom_user_dir=true).
    use_custom = str(cfg.get("config/use_custom_user_dir", "")).lower() == "true"
    if use_custom:
        custom_name = cfg.get("config/custom_user_dir_name", "") or project_name
        custom_name = _sanitize_dir_name(custom_name)
        # Godot places a custom user dir directly under the platform data dir,
        # without the Godot/app_userdata prefix.
        return _platform_data_dir() / custom_name

    # 4. Per-platform default: <data dir>/<Godot|godot>/app_userdata/<name>.
    # Godot uses lowercase "godot" on Linux and "Godot" elsewhere.
    godot_dir = "godot" if sys.platform not in ("win32", "darwin") else "Godot"
    return _platform_data_dir() / godot_dir / "app_userdata" / _sanitize_dir_name(project_name)


# The trailing segment is OPTIONAL. It used to be `root[\/].*`, which required
# something AFTER "root", so "/root/House/Player" recovered and bare "/root" did
# not -- the shortest and most obvious path in the whole system was the one path
# the guard missed, and `get-state --node /root` failed with
# "Node not found: C:/Program Files/Git/root" (moving-in:G-016).
_MANGLED_ROOT = re.compile(r"^[A-Za-z]:[\/].*?[\/](root(?:[\/].*)?)$")


def normalize_node_path(path):
    """Undo MSYS/Git-Bash rewriting of an absolute Godot node path.

    Git Bash treats a leading "/" as a POSIX root and rewrites "/root/Globals" into
    something like "C:/Program Files/Git/root/Globals" before Python ever sees it, so
    the node lookup fails with a confusing Windows path in the error. Callers can also
    write "//root/..." to defeat the rewrite; both forms normalize back to "/root/...".
    """
    if not isinstance(path, str) or not path:
        return path
    m = _MANGLED_ROOT.match(path)
    if m:
        recovered = "/" + m.group(1).replace("\\", "/")
        # A trailing separator survives the rewrite ("<...>/Git/root/") and Godot
        # does not resolve a node path that ends in one.
        return recovered.rstrip("/") or "/"
    if path.startswith("//"):
        return "/" + path.lstrip("/")
    return path


def normalize_command_args(args: dict) -> dict:
    """Return a copy of `args` with every node-path-bearing value normalized.

    Only `node_path` used to be normalized, which left the same Git-Bash
    mangling in place for input_sequence steps (which carry `node` / `node_path`
    per step) and for project verbs driven through `cmd --args`. Anything not
    matching the mangled/double-slash shapes is returned untouched, so this is
    safe to apply to arbitrary args.
    """
    out = dict(args or {})
    for key in _NODE_PATH_KEYS:
        if key in out:
            out[key] = normalize_node_path(out[key])

    # input_sequence carries a list of step dicts, each of which may name a node.
    steps = out.get("steps")
    if isinstance(steps, list):
        normalized_steps = []
        for step in steps:
            if isinstance(step, dict):
                step = dict(step)
                for key in _NODE_PATH_KEYS:
                    if key in step:
                        step[key] = normalize_node_path(step[key])
            normalized_steps.append(step)
        out["steps"] = normalized_steps

    return out


def _wait_for_pickup(commands_path: Path, user_data: Path, action: str):
    """Liveness precheck: fail fast when nothing is polling the command file.

    The DevTools autoload reads the command file and immediately deletes it
    (`DirAccess.remove_absolute` in `_check_for_commands`), so the file still
    existing after the grace period is proof that no game is watching this
    directory. That distinguishes "game is dead" from "command is slow" without
    sending an extra ping.

    ONE exception, and it is not an edge case (H-038): since the re-entrancy guard
    landed, a game that is polling normally deliberately leaves the command file on
    disk while a handler is in flight, and picks it up on the tick after that handler
    returns. `step_time 5s` followed by anything else hits this every time. The
    breadcrumb plus a fresh heartbeat is the discriminator (see _handler_in_flight),
    so "file still here AND a live game is inside a handler" means alive and busy,
    not dead, and the wait belongs to the caller's timeout budget rather than to this
    2s liveness grace.
    """
    deadline = time.time() + PRECHECK_SECONDS
    while time.time() < deadline:
        if not commands_path.exists():
            return
        if _handler_in_flight(user_data):
            return
        time.sleep(_PRECHECK_POLL)

    if not commands_path.exists() or _handler_in_flight(user_data):
        return

    # Don't leave our command lying around for a future launch to pick up.
    try:
        commands_path.unlink()
    except OSError:
        pass

    # Say WHO last claimed this bus, if anyone did (G-009). The owner file
    # outlives its process, so this names "the game that was here", not proof
    # of a live one - phrased accordingly.
    owner, owner_path = _read_owner(user_data)
    owner_note = ""
    if owner is not None:
        # "has likely exited" was a guess, and quoting it at every later call made
        # a clean relaunch read as a second failure stacked on the first
        # (gather:G-103). The pid is checkable, so check it and say which it is.
        #
        # The heartbeat is checkable too, and it is the better question
        # (findmyballs:G-004): pointing at --userdata was WRONG for the project
        # that hit this, whose tree was merely paused. Lead with what the poll
        # timestamp proves, and only mention the directory when it is still a
        # live possibility.
        pid = owner.get("pid")
        age = poll_age(owner)
        if age is None:
            fate = ("that process is CONFIRMED GONE, so this is the ordinary "
                    "aftermath of a crash or quit - relaunch"
                    if not pid_alive(pid) else
                    "that process is STILL ALIVE, so it is running but not polling "
                    "THIS directory - check --userdata/--session")
        elif age > POLL_STALE_AFTER_SEC and pid_alive(pid):
            fate = (
                "%s. If it is paused, the DevTools autoload should be "
                "PROCESS_MODE_ALWAYS (harness 0.12.0+ sets this by default; an "
                "older install can be patched live with `set-state --node "
                "/root/DevTools --property process_mode --value 3`)"
                % owner_liveness_phrase(owner)
            )
        else:
            fate = owner_liveness_phrase(owner)
        owner_note = (
            "\nOwner file {p} says pid {pid} (project {proj!r}, session {sess!r}) "
            "last claimed this bus; {fate}.".format(
                p=owner_path.name,
                pid=pid,
                proj=owner.get("project", ""),
                sess=owner.get("session", ""),
                fate=fate,
            )
        )

    raise GameNotRunningError(
        "game not running: '{action}' was never picked up "
        "({secs:.1f}s grace, ~{cycles:.0f} of the game's ~100ms poll cycles).\n"
        "  polling: {dir}\n"
        "The DevTools autoload deletes {file} as soon as it reads it, so a file "
        "still sitting there means nothing is polling that directory.\n"
        "Note that polling the WRONG user:// directory looks exactly like a dead "
        "game - set --userdata <path> or GODOT_USERDATA if the game is running.\n"
        "Pass --no-precheck to skip this check and wait the full timeout."
        "{session}{owner}{crumb}".format(
            action=action,
            secs=PRECHECK_SECONDS,
            cycles=PRECHECK_SECONDS / 0.1,
            dir=user_data,
            file=commands_path.name,
            session=(
                "\nThis client is on session '%s'; the game must have been launched "
                "with `-- --devtools-session %s` (or GODOT_DEVTOOLS_SESSION set) or it "
                "is writing a different bus." % (_SESSION, _SESSION)
                if _SESSION else ""
            ),
            owner=owner_note,
            crumb=_breadcrumb_note(user_data),
        )
    )


def _unlink_retry(path: Path, attempts: int = 20, delay: float = 0.025) -> bool:
    """Delete `path`, riding out the brief exclusive lock Windows holds while the
    game writes it (gh#5). Since 0.20.0 the game writes the reply to a temp name
    and renames it into place, so the reply file only ever exists complete and
    this rarely fires; older game-side harnesses still open the target directly,
    and a client that raced that write got `PermissionError: [WinError 32]` AFTER
    the verb had taken effect - the worst shape for a scripted caller, who cannot
    tell whether to retry. `exists()` is not a promise that `unlink()` will work.
    Returns False only if the lock outlived every attempt (~0.5s)."""
    for _ in range(attempts):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(delay)
    return False


def send_command(project_path: Path, action: str, args: dict = None, timeout: float = 30.0) -> dict:
    """Send a command to the running Godot instance and wait for the result.

    Every request carries a short unique `id`. A reply whose `id` is a non-empty
    string that differs from ours belongs to another client's request, so it is
    ignored rather than returned (returning it is what produced the historical
    "KeyError: 'enemies'" corruption). A reply with **no** `id` key at all is
    accepted: that is an older game build, and those still have to work.
    """
    user_data = get_user_data_path(project_path)
    user_data.mkdir(parents=True, exist_ok=True)

    commands_name, results_name, _ = bus_filenames()
    commands_path = user_data / commands_name
    results_path = user_data / results_name

    # Read the owner BEFORE writing, so a mismatch is attributable on the very
    # first call rather than only once a crossed reply comes back (G-100).
    owner_before = owner_status(user_data)

    # A live owner from ANOTHER checkout (a git worktree of this project) is a
    # foreign game whose pid the reply check would accept, so it is refused
    # before the command is written - sending it is the harm.
    foreign_path = foreign_project_owner(owner_before, project_path)
    if foreign_path is not None:
        raise ForeignInstanceError(
            "The game answering this bus is running from a DIFFERENT checkout:\n"
            "  owner: pid {pid}, project {other}\n"
            "  you:   {mine}\n"
            "Same project name means the same user:// and the same bus files "
            "({owner_file}), so its replies would read as yours - '{action}' was NOT "
            "sent. Quit that game, or give one side its own bus: launch with "
            "--isolated, or `--session <id>` on both the launch and this client."
            .format(pid=owner_before["pid"], other=foreign_path,
                    mine=os.path.abspath(str(project_path)),
                    owner_file=owner_before["path"], action=action))

    # Clear any existing result
    if results_path.exists() and not _unlink_retry(results_path):
        print(f"WARNING: could not clear a stale reply at {results_path} (locked by another "
              "process for >0.5s); if the reply below is not for this request it will be "
              "ignored by id, but check for a second client on this bus.", file=sys.stderr)

    # Write command
    request_id = uuid.uuid4().hex[:12]
    command = {"id": request_id, "action": action, "args": normalize_command_args(args)}
    commands_path.write_text(json.dumps(command), encoding="utf-8")

    # Liveness precheck: does not consume any of the response timeout, so a slow
    # command still gets its full budget once the game has taken the request.
    if _PRECHECK_ENABLED:
        _wait_for_pickup(commands_path, user_data, action)

    # Wait for result
    crossed_ids = []
    start_time = time.time()
    while time.time() - start_time < timeout:
        if results_path.exists():
            try:
                result = json.loads(results_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # Half-written file; try again on the next tick.
                time.sleep(0.1)
                continue

            reply_id = result.get("id") if isinstance(result, dict) else None
            if isinstance(reply_id, str) and reply_id and reply_id != request_id:
                # Somebody else's reply. Leave it on disk (it is theirs to
                # consume) and keep waiting for ours within the same timeout.
                if reply_id not in crossed_ids:
                    crossed_ids.append(reply_id)
                time.sleep(0.1)
                continue

            _unlink_retry(results_path)

            # Bus-identity check (G-036a): a reply from a pid that differs from
            # the recorded bus owner means a foreign instance answered. Older
            # game builds carry no pid, and a missing owner file proves nothing;
            # both are accepted as before.
            reply_pid = result.get("pid") if isinstance(result, dict) else None
            if isinstance(reply_pid, int):
                owner_pid = owner_before["pid"] if owner_before["present"] else None
                if isinstance(owner_pid, int) and owner_pid != reply_pid:
                    # Two different situations, and conflating them is what made
                    # the recovery path read as a second failure (G-103): a LIVE
                    # owner means someone else legitimately holds this bus; a
                    # DEAD one means a survivor from an earlier run answered,
                    # which is the more corrupting case and the one that used to
                    # be indistinguishable from a fresh instance.
                    if owner_before["alive"]:
                        detail = (
                            "Another Godot instance owns this bus; use --session <id> "
                            "(launch with `-- --devtools-session <id>`) or quit the "
                            "other instance.")
                    else:
                        detail = (
                            "The recorded owner pid {owner_pid} is GONE, so pid "
                            "{reply_pid} is a survivor of an earlier run still "
                            "polling this bus - not the instance you think you are "
                            "driving. Kill it and relaunch:\n{kill}".format(
                                owner_pid=owner_pid, reply_pid=reply_pid,
                                kill=_kill_hint([reply_pid])))
                    raise ForeignInstanceError(
                        "Foreign instance on the bus: the reply to '{action}' came "
                        "from pid {reply_pid}, but {owner_file} says pid {owner_pid} "
                        "owns this bus.\n{detail}".format(
                            action=action,
                            reply_pid=reply_pid,
                            owner_file=owner_before["path"],
                            owner_pid=owner_pid,
                            detail=detail,
                        )
                    )

            if _RAW_JSON:
                # Global --json (G-039): print the raw reply centrally so every
                # bus-backed subcommand honors it, then skip the formatter.
                print(json.dumps(result, indent=2))
                raise _RawJsonPrinted(0 if result.get("success") else 1)
            return result
        time.sleep(0.1)

    if crossed_ids:
        raise CrossedReplyError(
            "Crossed replies: saw {n} response(s) stamped for another request "
            "({ids}) but none for ours ({mine}) within {t}s.\n"
            "The bridge is one command file and one result file - another client "
            "or thread is driving the same game. Run one command at a time.".format(
                n=len(crossed_ids),
                ids=", ".join(crossed_ids),
                mine=request_id,
                t=timeout,
            )
        )

    if _PRECHECK_ENABLED:
        # The precheck can now clear on "alive but inside a handler" as well as on
        # "consumed" (H-038), so which of those happened has to be re-read here
        # rather than assumed. Claiming the command was picked up when it is still
        # sitting on disk behind a long verb would send the reader hunting for a bug
        # in a handler that has not started.
        if commands_path.exists():
            crumb = _read_breadcrumb(user_data) or {}
            # Abandon it rather than leave it queued. We have given up waiting, and a
            # mutating verb that lands minutes later against changed state is worse
            # than one that did not run - same reasoning as the precheck's unlink.
            try:
                commands_path.unlink()
            except OSError:
                pass
            raise NoReplyError(
                "No response from Godot after {t}s. '{action}' was never picked up, "
                "but the game is ALIVE and busy: it is still inside '{busy}' and the "
                "bridge serves one command at a time, so yours was queued behind it. "
                "The queued command has been withdrawn; raise --timeout and re-send, "
                "or wait for that verb to finish.".format(
                    t=timeout, action=action, busy=crumb.get("action", "another verb"))
            )
        # The precheck proved the game consumed the command, so this is a
        # handler problem, not a liveness problem.
        raise NoReplyError(
            "No response from Godot after {t}s. The command WAS picked up (the "
            "game is alive) but '{action}' never answered - it is hung, still "
            "running, or it aborted mid-handler.\n"
            "Most often it aborted: a GDScript runtime error raised by YOUR code "
            "reacting to what the verb did (a setter, a signal, a "
            "NOTIFICATION_TRANSFORM_CHANGED handler, an Area body_entered) kills "
            "the handler before it can reply, and GDScript has no exception to "
            "catch. The game survives, so every later verb answers normally and "
            "the verb looks selectively broken.\n"
            "Check the game's stderr for [SCRIPT ERROR] / [ERR] stamped around "
            "now - that names the line. The bus itself recovers on its own after "
            "the dispatch watchdog fires.".format(t=timeout, action=action)
        )
    raise NoReplyError(
        "No response from Godot after {t}s running '{action}'. The liveness "
        "precheck was disabled (--no-precheck), so this could be either a dead "
        "game or a hung handler; re-run without --no-precheck to tell them "
        "apart.".format(t=timeout, action=action)
    )


def cmd_screenshot(args, project_path: Path):
    """Take a screenshot, optionally cropped and with nodes/groups hidden.

    Data keys read: path, width, height, region, hidden. Hiding is done and
    undone game-side inside one command, so a capture cannot leave the HUD
    switched off (gather:G-079).
    """
    cmd_args = {}
    if args.filename:
        cmd_args["filename"] = args.filename
    if args.region is not None:
        cmd_args["region"] = args.region
    if args.hide:
        cmd_args["hide"] = [normalize_node_path(p) for p in args.hide]
    if args.hide_group:
        cmd_args["hide_group"] = args.hide_group

    result = send_command(project_path, "screenshot", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result["data"]
    print(f"Screenshot saved: {data['path']}")
    print(f"Size: {data['width']}x{data['height']}")
    if data.get("region"):
        r = data["region"]
        print(f"Cropped to: {r['x']},{r['y']} {r['w']}x{r['h']}")
    if data.get("hidden"):
        print(f"Hidden for the capture (restored after): {', '.join(data['hidden'])}")
    elif args.hide or args.hide_group:
        # Since 0.20.0 the game refuses (success:false, no file) when a --hide
        # names nothing it can hide, so reaching here means an older game-side
        # harness silently ignored the flag. That is not a capture of what was
        # asked for (gh#5): exit 1, and remove the file so nothing downstream
        # mistakes it for one.
        print("ERROR: --hide/--hide-group matched nothing the game could hide, and the "
              "installed harness wrote the capture anyway (older than this client). "
              f"Discarding {data['path']} - a capture that ignored --hide is not the "
              "capture asked for.", file=sys.stderr)
        try:
            Path(data["path"]).unlink()
        except OSError:
            pass
        sys.exit(1)


def cmd_validate(args, project_path: Path):
    """Validate a specific scene."""
    if not args.scene:
        print("Error: --scene is required", file=sys.stderr)
        sys.exit(1)
    result = send_command(project_path, "validate_scene", {"path": args.scene})
    print_validation_result(result)


def cmd_validate_all(args, project_path: Path):
    """Validate all scenes in the project."""
    result = send_command(project_path, "validate_all", timeout=60.0)
    print_validation_result(result)


def print_geometry_caveat(data: dict, verb: str, *, only_if_suspect: bool = False) -> None:
    """Print the game's own warning that its screen geometry was measured headless (H-051).

    Every verb that returns screen-space rects (node-bounds, ui-snapshot,
    validate-ui, findings) carries `geometry_trustworthy` + `geometry_caveat`
    since 0.20.0. Headless the window is 64x64 whatever the project designed, so
    a node the game positions from the window size lands off-viewport there and
    dead centre for a player. The number is faithful to the headless run; what
    the reader must not do is take it for the player's screen. An off-viewport
    verdict shipped to a published report that way before anyone ran the game
    windowed.

    `only_if_suspect` prints only when the caveat applies (headless), which is
    right for the list-style verbs; node-bounds prints its trust state always,
    because a single rect is read as an answer.

    Loud on absence, to stderr, like canvas_scale: a key this client expects and
    the game does not send means the two halves are at different versions, and
    a silent skip is how three wire mismatches shipped unnoticed in 0.4.0.
    """
    if "geometry_trustworthy" not in data or "geometry_caveat" not in data:
        print(f"{verb}: the reply carried no 'geometry_trustworthy'/'geometry_caveat' key "
              "(installed harness older than this client?). Whether this geometry was "
              f"measured headless is UNKNOWN. Keys: {sorted(data)}", file=sys.stderr)
        return
    if data["geometry_trustworthy"]:
        if not only_if_suspect:
            print("  Geometry:     measured windowed (what a player sees)")
        return
    print(f"  GEOMETRY CAVEAT: {_printable(str(data['geometry_caveat']))}")


def print_validation_result(result: dict, geometry_verb: str = ""):
    """Pretty-print validation results.

    `geometry_verb` names the CLI verb when the reply is screen geometry
    (validate-ui); the scene validators are not, and print no caveat.
    """
    if result["success"]:
        print("[OK] " + result["message"])
    else:
        print("[FAIL] " + result["message"])

    data = result.get("data", {})

    # Handle validate_all response: data.scenes is an array of {path, issues, valid}
    scenes = data.get("scenes", [])
    if scenes:
        for scene in scenes:
            if scene.get("issues"):
                print(f"\n{scene['path']}:")
                for issue in scene["issues"]:
                    severity = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(issue["severity"], "???")
                    print(f"  [{severity}] {issue['code']}: {issue['message']}")
    else:
        # Handle single scene validate response: data.issues is a list.
        # validate_ui stamps each issue with data.baseline ("new"/"pre_existing")
        # since 0.12.0; other validate verbs do not, and print unchanged.
        issues = data.get("issues", [])
        if isinstance(issues, list):
            for issue in issues:
                severity = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(issue["severity"], "???")
                mark = ""
                state = issue.get("baseline")
                if state == "new":
                    mark = "NEW "
                elif state == "pre_existing":
                    mark = "PRE "
                print(f"  {mark}[{severity}] {issue['code']}: {issue['message']}")

    # plant-tower-defense:G-030: the records of a non-clean run are kept on disk
    # so a transient can be investigated after the frame that produced it is
    # gone. Printed whenever the game wrote it (i.e. the count was non-zero).
    if data.get("last_findings_path"):
        print(f"\nFull records kept at {data['last_findings_path']} "
              "(overwritten by the next non-clean run).")

    if data.get("baseline_written"):
        print(f"\nBaseline written to {data.get('baseline_path', '?')}. "
              f"{data.get('pre_existing_count', 0)} finding(s) are now pre-existing; "
              "later runs gate on NEW ones only.")
    elif data.get("baseline_in_use"):
        print(f"\nBaseline: {data.get('new_count', 0)} NEW, "
              f"{data.get('pre_existing_count', 0)} pre-existing "
              f"({data.get('baseline_path', '?')}). Only NEW findings fail this check.")
    elif "new_count" in data:
        # validate_ui with no baseline on disk. Say so: a project that never
        # writes one is gating on every finding forever, which is how a check
        # that fires on correct-by-design UI ends up being ignored entirely.
        print("\nNo UI findings baseline. Every finding gates. "
              "Run `validate-ui --baseline-write` to accept the current set.")

    if geometry_verb:
        print_geometry_caveat(data, geometry_verb, only_if_suspect=True)

    if not result["success"]:
        sys.exit(1)


def cmd_scene_tree(args, project_path: Path):
    """Get the current scene tree, or a subtree of it."""
    cmd_args = {"depth": args.depth}
    if args.root:
        cmd_args["root"] = normalize_node_path(args.root)
    if args.properties:
        cmd_args["properties"] = args.properties
    result = send_command(project_path, "scene_tree", cmd_args)
    if result["success"]:
        data = result["data"]
        print(json.dumps(data, indent=2))
        # moving-in:G-056: every node prints a "name" AND a "path" line, so
        # `| grep -c` over-counts by two - and read as a duplicated node, which is
        # exactly what the reader was testing for. Every other verb ends with a
        # denominator; so does this one, on stderr so the JSON stays parseable.
        print(f"{_count_tree_nodes(data)} node(s) in this subtree "
              f"(depth {args.depth}; do not count JSON lines - each node prints several)",
              file=sys.stderr)
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def _count_tree_nodes(data) -> int:
    """Nodes in a scene_tree reply: every dict carrying a "path" key, recursively."""
    if isinstance(data, dict):
        own = 1 if "path" in data and "name" in data else 0
        return own + sum(_count_tree_nodes(v) for v in data.values())
    if isinstance(data, list):
        return sum(_count_tree_nodes(v) for v in data)
    return 0


def cmd_curve(args, project_path: Path):
    """Sweep a pure method over an integer range (bus verb: curve, G-127).

    Data keys read: points, min, max, sum, node_path, method.
    """
    cmd_args = {
        "node_path": normalize_node_path(args.node),
        "method": args.method,
        "from": args.start,
        "to": args.end,
        "step": args.step,
        "arg_index": args.arg_index,
    }
    if args.args:
        try:
            extra = json.loads(args.args)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --args: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(extra, list):
            print("Error: --args must be a JSON array", file=sys.stderr)
            sys.exit(1)
        cmd_args["args"] = extra

    result = send_command(project_path, "curve", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data") or {}
    if "points" not in data:
        print(f"curve: the reply carried no 'points' key. Keys: {sorted(data)}",
              file=sys.stderr)
        sys.exit(1)
    print(result.get("message", ""))
    for point in data["points"]:
        print(f"  {point.get('input')}: {_format_value(point.get('value'))}")


def cmd_performance(args, project_path: Path):
    """Get performance metrics.

    Data keys read: fps (mean over the window since 0.22.0), fps_instant, fps_min,
    fps_max, fps_max_trustworthy, fps_max_caveat, fps_samples, fps_window_sec,
    fps_settling, fps_first_half, fps_second_half, nodes, node_baseline,
    node_growth, node_types_delta.
    """
    cmd_args = {}
    if getattr(args, "reset_baseline", False):
        cmd_args["reset_baseline"] = True
    frames = getattr(args, "frames", None)
    if frames is not None:
        cmd_args["frames"] = int(frames)
    if getattr(args, "by_type", False):
        cmd_args["by_type"] = True

    # The window is measured game-side in real frames: give it its seconds.
    budget = 30.0 + (int(frames) if frames is not None else 30) / 5.0
    result = send_command(project_path, "performance", cmd_args, timeout=budget)
    if result["success"]:
        data = result["data"]
        # gh#6: the bridge answers on a paused tree, and the numbers look fine.
        # Lead with the fact rather than burying it after the metrics.
        if data.get("tree_paused"):
            print("TREE IS PAUSED - every number below describes a game that is not "
                  "stepping. Call `unpause` if you paused it yourself, or set "
                  "entry_hook in devtools_config.json to advance past a menu/title "
                  "screen automatically on launch - check `ping`'s entry_hook_status "
                  "if you already did and it is still paused.")
        elif "tree_paused" not in data:
            print("performance: the reply carried no 'tree_paused' key (installed harness "
                  "older than this client); whether the tree is paused is UNKNOWN - "
                  "check `ping`.", file=sys.stderr)
        samples = int(data.get("fps_samples", 0) or 0)
        if samples > 0:
            settling = " - STILL SETTLING (first half %.1f, second half %.1f): re-read after wait-frames" % (
                float(data.get("fps_first_half", 0)), float(data.get("fps_second_half", 0))
            ) if data.get("fps_settling") else ""
            print(f"FPS:              mean {data['fps']:.1f}  min {float(data.get('fps_min', 0)):.1f}  "
                  f"max {float(data.get('fps_max', 0)):.1f}  n={samples} frames "
                  f"({float(data.get('fps_window_sec', 0)):.2f}s){settling}")
            # H-060: headless fps_max reads five figures and looks broken rather
            # than merely uninformative - say which number not to trust, same as
            # the geometry_caveat convention.
            if data.get("fps_max_caveat"):
                print(f"                  fps_max NOT trustworthy: {data['fps_max_caveat']}")
        elif "fps_samples" in data:
            print(f"FPS:              {data['fps']:.1f}  (single instantaneous read; pass --frames N for a measured window)")
        else:
            # Game older than 0.22.0: a single Engine.get_frames_per_second() read.
            print(f"FPS:              {data['fps']:.1f}  (instantaneous - installed game predates windowed sampling; "
                  "one frame's rate is not a measurement)")
        print(f"Frame time:       {data['frame_time_ms']:.2f} ms")
        print(f"Physics FPS:      {int(data['physics_fps'])}")
        print(f"Draw calls:       {int(data['draw_calls'])}")
        print(f"Objects:          {int(data['objects'])}")
        print(f"Static memory:    {data['static_memory_mb']:.1f} MB")
        print(f"Video memory:     {data['video_memory_mb']:.1f} MB")
        if "node_growth" in data and int(data.get("node_baseline", -1)) >= 0:
            print(f"Total nodes:      {int(data['nodes'])}   growth {int(data['node_growth']):+d} "
                  f"since baseline {int(data['node_baseline'])}")
        else:
            print(f"Total nodes:      {int(data['nodes'])}")
        if "node_types_delta" in data:
            delta = data.get("node_types_delta") or {}
            if delta:
                rows = sorted(delta.items(), key=lambda kv: (-abs(int(kv[1])), kv[0]))
                print("Node types changed since baseline: " + ", ".join(
                    f"{cls} {int(n):+d}" for cls, n in rows))
            else:
                print("Node types changed since baseline: none")
        _print_orphans(data, cmd_args.get("reset_baseline", False))
        print(f"Physics 2D objs:  {int(data['physics_2d_active_objects'])}")
        print(f"Physics 3D objs:  {int(data['physics_3d_active_objects'])}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def _print_orphans(data: dict, reset_baseline: bool):
    """Print orphan growth (the actionable number) with the absolute alongside.

    A fresh launch legitimately reports dozens of orphans, so the absolute count
    can never be gated on; growth since the baseline can. Older game builds
    return only `orphan_nodes`, so fall back to the original single line rather
    than crashing on the missing keys.
    """
    absolute = int(data.get("orphan_nodes", 0))
    has_growth = "orphan_growth" in data or "orphan_baseline" in data
    if not has_growth:
        print(f"Orphan nodes:     {absolute}")
        return

    baseline = int(data.get("orphan_baseline", 0))
    growth = int(data.get("orphan_growth", absolute - baseline))
    print(f"Orphan growth:    {growth:+d}   (baseline {baseline}, absolute {absolute})")
    if reset_baseline:
        print(f"                  baseline reset to {baseline}")


def _printable(text: str) -> str:
    r"""Escape control bytes so a text reply stays greppable (gather:G-124).

    Reading a Button's `text` back returned embedded NULs, and grep answered
    `Binary file (standard input) matches` instead of the value - a one-line
    assertion became a `tr -d '\000'` pipeline. Tabs and newlines are legitimate
    inside a Godot string and are kept; everything else below 0x20, plus DEL, is
    rendered as \xNN so it is visible rather than merely absent.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x7F or (code < 0x20 and ch not in "\t\n"):
            out.append("\\x%02x" % code)
        else:
            out.append(ch)
    return "".join(out)


def _format_value(value) -> str:
    """Render one property value on a single line."""
    if isinstance(value, str):
        return _printable(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return json.dumps(value)
    # Vector2/Rect2/etc. arrive as small lists or dicts; keep them on one line.
    return json.dumps(value, separators=(", ", ": "))


def cmd_get_state(args, project_path: Path):
    """Get node state, optionally filtered to specific properties."""
    # Normalize up front so every message this function prints echoes the path
    # that was actually queried, not the raw MSYS-mangled input (G-025).
    args.node = normalize_node_path(args.node)
    cmd_args = {}
    if args.node:
        cmd_args["node_path"] = args.node
    if args.properties:
        cmd_args["properties"] = args.properties

    result = send_command(project_path, "get_state", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result["data"]
    if not args.properties:
        print(json.dumps(data, indent=2))
        return

    # Filtered read: print "name: value" per line instead of a JSON blob, in the
    # order the flags were given, so the output is diffable and greppable.
    values = data.get("properties")
    if not isinstance(values, dict):
        values = {k: v for k, v in data.items() if k not in ("missing", "properties")}

    missing = list(data.get("missing", []) or [])
    for name in args.properties:
        if name in values:
            print(f"{name}: {_format_value(values[name])}")
        elif not any(str(m) == name or str(m).startswith(name + " (") for m in missing):
            # The game neither returned it nor listed it as missing. A dotted path
            # is listed as "a.b (reason it ran out)", hence the prefix match.
            missing.append(name)

    if missing:
        node_label = args.node or "(current scene)"
        print(
            f"Unknown propert{'y' if len(missing) == 1 else 'ies'} on {node_label}: "
            f"{', '.join(str(m) for m in missing)}",
            file=sys.stderr,
        )
        print(
            "  (the node does not expose that name - check spelling, or drop "
            "--property to dump everything it does expose)",
            file=sys.stderr,
        )
        sys.exit(1)


_TUPLE_VALUE = re.compile(r"^[\(\[]?\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)+\s*[\)\]]?$")


def parse_value_arg(text: str):
    """Decode a --value / --args scalar the way a person would write it.

    JSON first (so `true`, `null`, `[1,2]`, `{"x":1}` and quoted strings keep
    their exact meaning), then a bare number, then a bare numeric TUPLE:
    `-200,-296` and `(-200,-296)` both become `[-200, -296]`, which is the shape
    the game's _coerce_arg turns into a Vector2. Anything else is a plain string.

    Why the tuple forms exist (gather:G-137): the error a caller got named the
    type it wanted (Vector2) and not a syntax that produces one, so the working
    spelling was found by guessing - `[-200,-296]` worked, `(-200,-296)` and
    `-200,-296` did not. All three work now, on this side and on the game side.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        return int(text)
    except (ValueError, TypeError):
        pass
    try:
        return float(text)
    except (ValueError, TypeError):
        pass
    if isinstance(text, str) and _TUPLE_VALUE.match(text.strip()):
        body = text.strip().strip("()[]")
        return [float(p) if "." in p else int(p) for p in (q.strip() for q in body.split(","))]
    return text


def cmd_set_state(args, project_path: Path):
    """Set a node property."""
    args.node = normalize_node_path(args.node)  # G-025
    value = parse_value_arg(args.value)

    result = send_command(project_path, "set_state", {
        "node_path": args.node,
        "property": args.property,
        "value": value
    })
    if result["success"]:
        data = result.get("data") or {}
        # Say what actually landed, not just that a write happened. `State updated`
        # was printed for a write that stored (0, 0) instead of the requested
        # position, because the client never looked at the read-back the game had
        # already put in the reply (gather:G-077).
        if "read_back" in data:
            print(f"State updated: {args.property} = {_format_value(data['read_back'])}"
                  + ("  (coerced)" if data.get("coerced") else ""))
        else:
            print("State updated")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        if "cannot convert" in str(result.get("message", "")):
            print("  note: a leading '-' makes argparse read the value as a flag - "
                  "write --value=-200,-296 (with '=') for negative tuples.",
                  file=sys.stderr)
        sys.exit(1)


def cmd_run_method(args, project_path: Path):
    """Call a method on a node.

    Data keys read: result, returned_null, declared_return, node_path, method,
    note (see _cmd_run_method in dev_tools.gd - these two halves must agree).
    With --json the FULL reply envelope is printed, so this verb is pipeable into
    a JSON parser like every `cmd` verb is (gather:G-131).
    """
    global _RAW_JSON
    if getattr(args, "json", False):
        _RAW_JSON = True

    args.node = normalize_node_path(args.node)  # G-025
    method_args = []
    if args.args:
        try:
            method_args = json.loads(args.args)
            if not isinstance(method_args, list):
                print("Error: --args must be a JSON array, e.g., '[25, \"name\"]'", file=sys.stderr)
                sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --args: {e}", file=sys.stderr)
            sys.exit(1)

    result = send_command(project_path, "run_method", {
        "node_path": args.node,
        "method": args.method,
        "args": method_args
    })
    # Only reached without --json (raw mode prints and raises inside send_command).
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data") or {}
    if "result" not in data:
        # Never paper over a missing key with a friendly line - a silent fallback
        # is what made three wire mismatches invisible in 0.4.0.
        print("run-method: the reply carried no 'result' key. "
              f"Keys present: {sorted(data)}", file=sys.stderr)
        sys.exit(1)

    if not data.get("returned_null"):
        print(f"Result: {_format_value(data['result'])}")
        return

    # null is ambiguous in GDScript: a `-> void` that ran perfectly and a `-> int`
    # that aborted mid-body both come back as null, and the engine raises nothing
    # the bridge could catch. Say which of the two the DECLARATION allows rather
    # than printing a bare `Result: None` that reads as a failure (gather:G-096).
    declared = data.get("declared_return", "")
    if declared == "Nil":
        print("Result: <no value>  (the method declares no return type; the call "
              "itself completed)")
    elif declared:
        print(f"Result: null  (but the method declares -> {declared}; a null here "
              "means it returned null OR aborted on a runtime error - check the "
              "game's stderr for [ERR]/[SCRIPT ERROR])", file=sys.stderr)
    else:
        print("Result: null  (return type unknown: the method is absent from "
              "get_method_list(), so null cannot be told from an abort)")
    if data.get("note"):
        print(f"  note: {data['note']}")


def cmd_logs(args, project_path: Path):
    """View DevTools logs."""
    user_data = get_user_data_path(project_path)
    log_path = user_data / bus_filenames()[2]

    if not log_path.exists():
        print("No logs found")
        return

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")

    if args.category:
        lines = [l for l in lines if f'"category":"{args.category}"' in l or f'"category": "{args.category}"' in l]

    if args.tail:
        lines = lines[-args.tail:]

    for line in lines:
        try:
            entry = json.loads(line)
            ts = time.strftime("%H:%M:%S", time.localtime(entry["timestamp"]))
            cat = entry["category"]
            msg = entry["message"]
            print(f"[{ts}] [{cat}] {msg}")
        except json.JSONDecodeError:
            print(line)


def cmd_ping(args, project_path: Path):
    """Check if Godot DevTools is responding."""
    try:
        result = send_command(project_path, "ping", timeout=5.0)
        if result["success"]:
            data = result.get("data") or {}
            # data.session (see _cmd_ping in dev_tools.gd). Absent on a pre-0.5.0
            # build, which is fine - it can only be on the default bus anyway.
            session = data.get("session") or ""
            where = f", session: {session}" if session else ""
            print(f"DevTools is running (timestamp: {data['timestamp']:.0f}{where})")
            # Reachable while paused since 0.12.0, so ping answering no longer
            # implies an unpaused tree. Say which, rather than let the caller
            # infer it. paused is absent on a pre-0.12.0 game.
            if data.get("paused") is True:
                print("  tree is PAUSED (bridge still polling: PROCESS_MODE_ALWAYS)")
            # data.project_path (0.22.0+): where the answering game's res:// is.
            # Printed always, and flagged when it is not the --path you gave,
            # because a worktree sibling's game reads exactly like your own
            # otherwise (plant-tower-defense:G-018).
            if data.get("project_path"):
                if _same_project_dir(data["project_path"], project_path):
                    print(f"  project: {data['project_path']}")
                else:
                    print(f"  WARNING: the answering game runs from {data['project_path']}, "
                          f"NOT {os.path.abspath(str(project_path))} - a different checkout "
                          "(git worktree?) is on this bus")
            # gh#29: entry_hook must never again accept a value and do nothing -
            # "not_configured" is silent on purpose (the common, correct case);
            # anything else is worth a line, success included, since "fired" is
            # the caller's confirmation the config actually did something.
            hook_status = data.get("entry_hook_status")
            if hook_status and hook_status != "not_configured":
                if hook_status == "fired":
                    result_val = data.get("entry_hook_result")
                    suffix = f" -> {result_val}" if result_val is not None else ""
                    print(f"  entry_hook: fired{suffix}")
                else:
                    print(f"  WARNING: entry_hook {hook_status}")
        else:
            print("DevTools responded but with error")
            sys.exit(1)
    except GameNotRunningError as e:
        # The precheck knows more than "no response" does - say what it knows.
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print("No response - is the game running with DevTools autoload?")
        sys.exit(1)


# ==================== USER:// STATE SNAPSHOT (plant-tower-defense:G-047) ====================
#
# --isolated isolates the bus and only the bus; user:// is shared, so a live check
# that presses a key whose handler calls _save() writes the developer's real save
# file, and putting it back is a discipline nothing enforced - a crash mid-check
# skipped it. `launch --snapshot-userstate` copies the matching files aside
# before the game starts and `quit` puts them back (a file that did not exist is
# removed again). A snapshot left behind by a game that died is restored by the
# next `launch`, so the developer's state is never more than one launch away.

USERSTATE_DIR = "userstate_snapshot"
USERSTATE_STAT = "userstate_stat.json"


def userstate_stat_take(project_path: Path, user_dir: Path) -> int:
    """Record (size, mtime) of every top-level user:// file at launch (gh#33 a).

    Cheap and always on: `quit` diffs against it and NAMES what a live pass wrote,
    so a save mutated by a verification run is a reported event, not a mystery
    that resurfaces as failing headless tests two runs later.
    """
    stat = {}
    try:
        for f in sorted(user_dir.iterdir()):
            if f.is_file():
                stat[f.name] = _userstate_stat_entry(f)
    except OSError:
        return 0
    out = project_path / ".devtools" / USERSTATE_STAT
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"user_dir": str(user_dir), "files": stat,
                               "taken_unix": time.time()}), encoding="utf-8")
    return len(stat)


_USERSTATE_MD5_MAX = 4 * 1024 * 1024


def _userstate_stat_entry(f: Path):
    """[size, mtime, md5-or-""] - the md5 (gh#39, 0.40.0) tells `content changed`
    from `rewritten identically`: an identical rewrite says a writer RAN and the
    values happened to match, which is a different bug from a value overwritten,
    and it is where an investigation should start."""
    import hashlib
    st = f.stat()
    digest = ""
    if st.st_size <= _USERSTATE_MD5_MAX:
        try:
            digest = hashlib.md5(f.read_bytes()).hexdigest()
        except OSError:
            digest = ""
    return [st.st_size, st.st_mtime, digest]


# The bridge's own files churn every run and are not the developer's state.
_USERSTATE_OWN = {"devtools_owner.json", "devtools_command.json", "devtools_results.json",
                  "devtools_log.jsonl", "findings_last.json", "ui_baseline.json"}


def userstate_stat_diff(project_path: Path):
    """(changed, created, deleted, user_dir) since userstate_stat_take, consuming the
    record; None when there is no record. Pure bookkeeping, no printing."""
    path = project_path / ".devtools" / USERSTATE_STAT
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        user_dir = Path(rec["user_dir"])
        before = rec["files"]
    except (OSError, ValueError, KeyError):
        path.unlink(missing_ok=True)
        return None
    path.unlink(missing_ok=True)
    if not user_dir.is_dir():
        return None
    now = {}
    for f in user_dir.iterdir():
        if f.is_file():
            now[f.name] = _userstate_stat_entry(f)
    changed = []
    for n in sorted(now):
        if n not in before or _userstate_bridge_owned(n) or now[n][:2] == before[n][:2]:
            continue
        b_md5 = before[n][2] if len(before[n]) > 2 else ""
        if b_md5 and now[n][2] and b_md5 == now[n][2] and now[n][0] == before[n][0]:
            changed.append(n + " (rewritten identically - a writer ran; the values matched)")
        else:
            changed.append(n)
    created = sorted(n for n in now if n not in before and not _userstate_bridge_owned(n))
    deleted = sorted(n for n in before if n not in now and not _userstate_bridge_owned(n))
    return changed, created, deleted, user_dir


def userstate_stat_report(project_path: Path) -> None:
    """Print which user:// files a run changed, created or deleted (gh#33)."""
    diff = userstate_stat_diff(project_path)
    if diff is None:
        return
    changed, created, deleted, user_dir = diff
    if not (changed or created or deleted):
        print(f"user://: no file changed during this run ({user_dir})")
        return
    parts = []
    if changed:
        parts.append("changed: " + ", ".join(changed))
    if created:
        parts.append("created: " + ", ".join(created))
    if deleted:
        parts.append("deleted: " + ", ".join(deleted))
    recovery = ""
    manifest_path = _userstate_dir(project_path) / USERSTATE_MANIFEST
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not m.get("restore_on_quit", True) and m.get("files"):
            recovery = (f" A copy taken at launch of {', '.join(m['files'])} is in "
                        f".devtools/{USERSTATE_DIR}/ - `python tools/devtools.py "
                        f"restore-userstate` puts it back.")
        # moving-in:G-063: the changed names and the patterns are one comparison
        # apart; make it, so the advice never points at a mechanism that protects
        # nothing.
        unmatched = userstate_unmatched(changed + created, m.get("patterns") or [])
        if unmatched:
            pats = m.get("patterns") or []
            suggest = sorted({("*" + Path(u.split(" (", 1)[0]).suffix) if Path(u.split(" (", 1)[0]).suffix
                              else u.split(" (", 1)[0] for u in unmatched})
            recovery += (f" NOT covered by the snapshot patterns ({', '.join(pats)}) and so "
                         f"NOT restored: {', '.join(unmatched)} - relaunch with "
                         f"--snapshot-userstate {' '.join(pats + suggest)} to cover them.")
    except (OSError, ValueError):
        pass
    print(f"user://: this run wrote the developer's REAL user data in {user_dir} -- "
          + "; ".join(parts) + ". A save changed here is loaded by the game's next start, "
          "including the headless test suite, and reads there as an unrelated failure."
          + recovery + " `launch --snapshot-userstate` restores such files on quit "
          "automatically.", file=sys.stderr)
USERSTATE_MANIFEST = "manifest.json"
USERSTATE_DEFAULT_GLOB = "*.save"
# moving-in:G-063 (0.41.0): `*.save` alone missed `user://settings.cfg` - ConfigFile
# is Godot's own idiom - and `quit` recommended the flag in the same reply that
# proved it would not have helped. The default now covers the common persistence
# shapes; the bridge's own files (baselines, findings, owner) are never in the set,
# or restoring on quit would undo a `findings --baseline-write` made mid-session.
USERSTATE_DEFAULT_GLOBS = ["*.save", "*.sav", "*.cfg", "*.dat", "*.json", "*.tres", "*.res", "*.bin"]


def _userstate_bridge_owned(name: str) -> bool:
    return (name in _USERSTATE_OWN or name.startswith("devtools_")
            or name.endswith("_baseline.json") or name == "findings_last.json")


def userstate_unmatched(names, patterns):
    """The changed/created names no snapshot pattern covers (moving-in:G-063)."""
    import fnmatch
    return [n for n in names
            if not any(fnmatch.fnmatch(n.split(" (", 1)[0], pat) for pat in patterns)]


def _userstate_dir(project_path: Path) -> Path:
    return project_path / ".devtools" / USERSTATE_DIR


def userstate_snapshot(project_path: Path, user_dir: Path, patterns, restore_on_quit=True) -> dict:
    """Copy user_dir files matching `patterns` under .devtools/, write a manifest.

    Records every file the patterns match NOW, and every pattern, so restore can
    also delete files the game creates during the run (a save that did not
    exist before must not exist after). Returns the manifest.

    plant-tower-defense:G-050: taken on EVERY launch since 0.39.0 (a file copy of
    `*.save`; microseconds), and `restore_on_quit` - the old `--snapshot-userstate`
    flag - decides only whether `quit` puts it back automatically. Before this the
    warning that a run had overwritten a developer's campaign best arrived at
    `quit`, when the previous value existed nowhere. The last snapshot is kept
    beside the current one (`userstate_snapshot_prev`) so the run before the one
    you noticed is recoverable too.
    """
    import shutil
    dest = _userstate_dir(project_path)
    prev = dest.with_name(dest.name + "_prev")
    if dest.exists():
        if prev.exists():
            shutil.rmtree(prev)
        dest.rename(prev)
    dest.mkdir(parents=True)
    files = []
    for pattern in patterns:
        for src in sorted(user_dir.glob(pattern)):
            if not src.is_file() or _userstate_bridge_owned(src.name):
                continue
            rel = src.relative_to(user_dir).as_posix()
            copy_to = dest / rel
            copy_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, copy_to)
            files.append(rel)
    manifest = {
        "user_dir": str(user_dir),
        "patterns": list(patterns),
        "files": files,
        "taken_unix": time.time(),
        "restore_on_quit": bool(restore_on_quit),
        "harness_version": HARNESS_VERSION,
    }
    (dest / USERSTATE_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def userstate_restore(project_path: Path, reason: str, force: bool = False) -> "dict | None":
    """Put the snapshot back and delete it. Returns the manifest, or None if there
    was nothing to restore - or if the snapshot was taken without restore-on-quit
    and `force` is False (the always-on snapshot is a recovery point, not an
    automatic revert; `restore-userstate` forces it). Prints one line when it acts."""
    import shutil
    dest = _userstate_dir(project_path)
    manifest_path = dest / USERSTATE_MANIFEST
    if not manifest_path.is_file():
        if reason == "quit":
            # plant-tower-defense:G-054 (2nd): say what happened with the snapshot EVERY
            # time - a restore that silently does not happen is worse than no restore.
            print("userstate: no snapshot to restore (none was taken at launch)")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"userstate: snapshot manifest unreadable ({exc}); leaving {dest} in place "
              "for a human", file=sys.stderr)
        return None
    if not force and not manifest.get("restore_on_quit", True):
        print(f"userstate: snapshot of {len(manifest.get('files', []))} file(s) kept, NOT "
              f"restored (--no-snapshot-userstate); `restore-userstate` reverts it")
        return None
    user_dir = Path(manifest["user_dir"])
    restored, removed = 0, 0
    if not user_dir.is_dir():
        print(f"userstate: {user_dir} no longer exists; leaving {dest} in place", file=sys.stderr)
        return None
    # Anything matching the patterns now that was NOT in the snapshot was created
    # by the run: remove it, so "did not exist before" holds after too.
    before = set(manifest["files"])
    for pattern in manifest["patterns"]:
        for cur in sorted(user_dir.glob(pattern)):
            if cur.is_file() and cur.relative_to(user_dir).as_posix() not in before \
                    and not _userstate_bridge_owned(cur.name):
                cur.unlink()
                removed += 1
    for rel in manifest["files"]:
        src = dest / rel
        if src.is_file():
            target = user_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            restored += 1
    shutil.rmtree(dest, ignore_errors=True)
    print(f"userstate: restored {restored} file(s) and removed {removed} created during the "
          f"run ({reason}; patterns {', '.join(manifest['patterns'])}) into {user_dir}")
    return manifest


def cmd_restore_userstate(args, project_path: Path):
    """Put back the user:// files copied at the last launch (bus verb: none; plant G-050).
    Exit 0 restored, 1 nothing to restore."""
    m = userstate_restore(project_path, "restore-userstate", force=True)
    if m is None:
        prev = _userstate_dir(project_path).with_name(USERSTATE_DIR + "_prev")
        hint = (f" The launch before that is under {prev.name}/ - move it to {USERSTATE_DIR}/ "
                f"and re-run to restore it." if prev.is_dir() else "")
        print(f"restore-userstate: no snapshot from the last launch under .devtools/{USERSTATE_DIR}/."
              + hint, file=sys.stderr)
        sys.exit(1)


def cmd_quit(args, project_path: Path):
    """Quit the running instance and WAIT for the process to actually go.

    `quit` was not reliably fatal: three separate times the old process was still
    alive after a relaunch (once at 1.4 GB), and the only symptom was verbs
    returning empty output while `ping` said `No response` - which reads as *no*
    game rather than as *two* (gather:G-112). A survivor is worse than a failed
    quit, so this reports one rather than assuming success.
    """
    try:
        user_data = get_user_data_path(project_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    owner, _ = _read_owner(user_data)
    pid = owner.get("pid") if isinstance(owner, dict) else None

    try:
        send_command(project_path, "quit", {"exit_code": args.exit_code or 0}, timeout=5.0)
        print("Quit command sent")
    except BridgeError:
        print("Quit command sent (no response expected)")

    if not isinstance(pid, int):
        print("  (no owner file, so there is no pid to confirm the exit against)")
        _quit_sweep(args, project_path, exclude=())
        userstate_stat_report(project_path)
        userstate_restore(project_path, "quit")
        return

    deadline = time.time() + max(1.0, args.wait)
    while time.time() < deadline:
        if not pid_alive(pid):
            print(f"  pid {pid} exited")
            _quit_sweep(args, project_path, exclude=(pid,))
            userstate_stat_report(project_path)
            userstate_restore(project_path, "quit")
            return
        time.sleep(0.2)

    # A slow-but-clean shutdown is not a survivor (gh#6). The reported case
    # turned out to be pid_alive() itself - on Windows it could not see a dead
    # pid at all (see _pid_alive_windows) - and that is fixed at the source. The
    # grace re-poll stays for the genuine slow-exit case: an exit 1 that fires
    # on a clean shutdown is an exit code people stop reading, which defeats
    # the check, since a REAL survivor answers the bus alongside the next launch
    # and the symptom is empty replies, not an error.
    grace = 3.0
    grace_deadline = time.time() + grace
    while time.time() < grace_deadline:
        if not pid_alive(pid):
            print(f"  pid {pid} exited (slower than the {args.wait:g}s --wait, but it is gone; "
                  f"pass --wait {int(args.wait) + int(grace) + 2} to stop seeing this)")
            _quit_sweep(args, project_path, exclude=(pid,))
            userstate_stat_report(project_path)
            userstate_restore(project_path, "quit")
            return
        time.sleep(0.2)

    # The owner is a survivor. Fold in everything else this project launched that
    # is still alive - the console-wrapper sibling of this very launch, and the
    # engine abandoned two launches ago (gh#14.1) - so one kill clears the lot.
    others = _ledger_survivors(project_path, exclude=(pid,))
    owner_row = {"pid": pid, "role": "owner", "started_unix": _pid_started_unix(pid) or time.time(),
                 "verified": True}
    all_rows = [owner_row] + others
    # gh#24: --kill only ever touches verified rows (_kill_survivors skips the
    # rest), so a kill hint or a "STILL ALIVE" tally built from ALL rows can
    # name a pid that command was never going to act on. The owner itself is
    # always verified here - pid_alive(pid) already gated this whole branch.
    verified_rows = [r for r in all_rows if r["verified"]]
    print(f"\nWARNING: pid {pid} is STILL ALIVE {args.wait + grace:g}s after quit.\n"
          "A survivor answers the bus alongside any new instance, and the symptom "
          "is empty replies, not an error.", file=sys.stderr)
    if others:
        print("%d other process(es) this project launched are ALSO still alive:\n%s"
              % (len(others), _describe_survivors(others)), file=sys.stderr)
    if getattr(args, "kill", False):
        gone = _kill_survivors(all_rows)
        left = [r["pid"] for r in verified_rows if r["pid"] not in gone]
        print("--kill: terminated %s%s" % (
            ", ".join(str(p) for p in gone) or "nothing",
            ("; STILL ALIVE: %s" % ", ".join(str(p) for p in left)) if left else ""),
            file=sys.stderr)
        userstate_stat_report(project_path)
        _quit_userstate_finish(project_path, left)
        sys.exit(1 if left else 0)
    print("Kill before launching again:\n" + _kill_hint([r["pid"] for r in verified_rows]),
          file=sys.stderr)
    userstate_stat_report(project_path)
    _quit_userstate_finish(project_path, [pid])
    sys.exit(1)


def _quit_userstate_finish(project_path: Path, alive_pids) -> None:
    """The snapshot's fate on the survivor paths (plant-tower-defense:G-054, 2nd
    sighting): `quit` used to exit 1 here WITHOUT restoring, so a session whose game
    lingered a few seconds kept the run's writes with the flag armed and no line
    saying so. Restoring under a live game would be undone by its own exit-time
    save, so: game gone -> restore; game alive -> keep the snapshot and say what to
    run once it is gone."""
    if alive_pids:
        dest = _userstate_dir(project_path)
        if (dest / USERSTATE_MANIFEST).is_file():
            print(f"userstate: snapshot KEPT, not restored - pid(s) "
                  f"{', '.join(str(p) for p in alive_pids)} still alive and may write user:// "
                  f"on exit. Once gone: `python tools/devtools.py restore-userstate`.",
                  file=sys.stderr)
        else:
            print("userstate: no snapshot to restore (none was taken at launch)")
        return
    userstate_restore(project_path, "quit")


def _quit_sweep(args, project_path: Path, exclude) -> None:
    """After a clean quit: anything else this project launched still alive?

    This is the case the owner pid structurally cannot see (gh#14.1): the
    `_console.exe` wrapper that spawned the engine, and any engine from an
    earlier launch that stopped polling (so `launch` treated it as stale and
    moved on) without exiting. Reported always; killed only under --kill.

    gh#24: an all-`verified=None` sweep exits 0, not 1. `verified=None` means
    genuinely unknowable AND never auto-killed - the docstring on
    `_ledger_survivors` says so - so there was nothing the caller could ever do
    about that exit code, which is exactly how an exit code stops being read.
    A row with `verified=True` still exits 1: that one names a real, killable
    survivor.
    """
    rows = _ledger_survivors(project_path, exclude=exclude)
    if not rows:
        return
    verified_rows = [r for r in rows if r["verified"]]
    print("\nWARNING: %d process(es) this project launched EARLIER are still alive "
          "(from %s):\n%s" % (len(rows), _launch_ledger_path(project_path).name,
                                _describe_survivors(rows)), file=sys.stderr)
    if getattr(args, "kill", False):
        gone = _kill_survivors(rows)
        left = [r["pid"] for r in verified_rows if r["pid"] not in gone]
        print("--kill: terminated %s%s" % (
            ", ".join(str(p) for p in gone) or "nothing",
            ("; STILL ALIVE: %s" % ", ".join(str(p) for p in left)) if left else ""),
            file=sys.stderr)
        if left:
            sys.exit(1)
        return
    if not verified_rows:
        return  # unverifiable only: a note, never auto-killed, never exit 1
    print("They answer the bus alongside the next instance (crossed replies, `Node not "
          "found` on a node scene-tree just listed). Kill them:\n"
          + _kill_hint([r["pid"] for r in verified_rows]), file=sys.stderr)
    sys.exit(1)


# ==================== GENERIC ESCAPE HATCHES ====================


def cmd_cmd(args, project_path: Path):
    """Send an arbitrary registered verb: {action:<action>, args:<json>}.

    Lets any project-registered handler be invoked without adding a dedicated
    subcommand. --args must be a JSON object (defaults to {}).
    """
    parsed_args: dict = {}
    if args.args:
        try:
            parsed_args = json.loads(args.args)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --args: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(parsed_args, dict):
            print("Error: --args must be a JSON object, e.g., '{\"foo\": 1}'", file=sys.stderr)
            sys.exit(1)

    result = send_command(project_path, args.action, parsed_args, timeout=args.timeout)
    # Print the whole envelope so unknown verbs are fully observable.
    print(json.dumps(result, indent=2))
    if not result.get("success", False):
        sys.exit(1)


def _read_harness_config(project_path: Path) -> dict:
    """addons/godot_selftest/devtools_config.json as a dict; {} when unreadable."""
    cfg_path = project_path / "addons" / "godot_selftest" / "devtools_config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_REGISTER_COMMAND_RE = re.compile(r'register_command\(\s*"([A-Za-z0-9_]+)"')
# register_command("name", _handler) / register_command("name", Callable(self, "_handler"))
_REGISTER_HANDLER_RE = re.compile(
    r'register_command\(\s*"([A-Za-z0-9_]+)"\s*,\s*'
    r'(?:Callable\(\s*self\s*,\s*"([A-Za-z0-9_]+)"\s*\)|([A-Za-z_][A-Za-z0-9_]*))')
_FUNC_DEF_RE = re.compile(r'^func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)?', re.M)


def _scan_verb_args(text: str) -> dict:
    """verb -> the arg keys its handler reads, from a static scan of the script.

    A wrong key name is silently accepted as "omitted" by `args.get("x", default)`,
    so a guessed `cell` against a handler reading `x`/`y` planted the default at the
    default cell and reported success (gh#14.2). The keys are grep-able straight
    out of the handler body - `args.get("k")`, `args["k"]`, `args.has("k")` -
    with no schema for the project author to write. Heuristic: a handler that
    forwards its dict to another function, or reads keys through a variable,
    shows fewer keys than it accepts; it never shows a key it does not read.
    """
    funcs = {}
    matches = list(_FUNC_DEF_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        funcs[m.group(1)] = (m.group(2) or "args", text[m.end():end])
    out = {}
    for m in _REGISTER_HANDLER_RE.finditer(text):
        verb = m.group(1)
        handler = m.group(2) or m.group(3)
        if handler not in funcs:
            continue
        param, body = funcs[handler]
        # Comment lines are dropped: the doc block ABOVE the next handler sits
        # inside this span, and `## args["k"]` in it is not a read.
        body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
        key_re = re.compile(
            r'\b%s\s*(?:\.get\(|\.has\(|\[)\s*"([A-Za-z0-9_]+)"' % re.escape(param))
        keys = []
        for k in key_re.findall(body):
            if k not in keys:
                keys.append(k)
        out[verb] = keys
    return out


def _print_verbs_with_args(actions, arg_map: dict, indent: str = "    "):
    width = max((len(a) for a in actions), default=0)
    for action in actions:
        keys = arg_map.get(action)
        if keys:
            print(f"{indent}{action:<{width}}  args: {', '.join(keys)}")
        elif keys == []:
            print(f"{indent}{action:<{width}}  (reads no args)")
        else:
            print(f"{indent}{action}")


def _list_commands_offline(args, project_path: Path):
    """Static parse of verb registrations, no running game needed (G-043).

    Reads register_command("...") calls out of the installed core autoload and
    the project's extension script (path from the config's extension_script).
    This is a text scan, not a runtime truth: verbs registered conditionally or
    under computed names are invisible to it, and last-writer-wins overrides
    cannot be seen. It answers "what CAN I call", not "what IS registered".
    """
    core_path = project_path / "addons" / "godot_selftest" / "dev_tools.gd"
    generic = []
    core_args = {}
    if core_path.is_file():
        core_text = core_path.read_text(encoding="utf-8")
        generic = sorted(set(_REGISTER_COMMAND_RE.findall(core_text)))
        core_args = _scan_verb_args(core_text)

    config = _read_harness_config(project_path)
    ext_res = str(config.get("extension_script", "") or "res://devtools_ext/commands.gd")
    ext_path = project_path / ext_res.replace("res://", "")
    project_verbs = []
    project_args = {}
    if ext_path.is_file():
        ext_text = ext_path.read_text(encoding="utf-8")
        project_verbs = sorted(set(_REGISTER_COMMAND_RE.findall(ext_text)))
        project_args = _scan_verb_args(ext_text)

    if args.json:
        print(json.dumps({
            "generic": generic,
            "project": project_verbs,
            "args": {**core_args, **project_args},
            "static_parse": True,
            "core_script": str(core_path),
            "extension_script": str(ext_path),
        }, indent=2))
        return

    print("Registered commands (STATIC PARSE of the scripts; the running game may differ):")
    print(f"  generic ({len(generic)}) from {core_path.name}:")
    _print_verbs_with_args(generic, core_args)
    if ext_path.is_file():
        print(f"  project ({len(project_verbs)}) from {ext_res}:")
        _print_verbs_with_args(project_verbs, project_args)
    else:
        print(f"  project: no extension script at {ext_res}")
    print("  args: keys the handler reads (args.get/has/[]), scanned from source - "
          "a key not listed is silently ignored by that verb.")


def cmd_list_commands(args, project_path: Path):
    """Discover and print all registered verbs (generic + project extensions)."""
    if getattr(args, "offline", False):
        _list_commands_offline(args, project_path)
        return

    result = send_command(project_path, "list_commands")
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    actions = result.get("data", {}).get("actions", [])
    # The game names the verbs; the arg keys come from a static scan of the
    # scripts on disk (gh#14.2). Registered names are runtime truth, keys are a
    # source read - a verb registered by a script this scan cannot see prints
    # with no keys, not with wrong ones.
    arg_map = {}
    for rel in ("addons/godot_selftest/dev_tools.gd",
                str(_read_harness_config(project_path).get("extension_script", "")
                    or "res://devtools_ext/commands.gd").replace("res://", "")):
        path = project_path / rel
        if path.is_file():
            try:
                arg_map.update(_scan_verb_args(path.read_text(encoding="utf-8")))
            except OSError:
                pass
    if args.json:
        print(json.dumps({"actions": actions, "args": arg_map}, indent=2))
        return

    print(f"Registered commands ({len(actions)}):")
    _print_verbs_with_args(actions, arg_map, indent="  ")
    print("  args: keys the handler reads, from a static scan of the scripts on disk - "
          "a key not listed is silently ignored by that verb.")


_ADDON_VERSION_RE = re.compile(
    r'^\s*const\s+HARNESS_VERSION\s*:\s*String\s*=\s*"([^"]+)"', re.M)


def _installed_addon_version(project_path: Path):
    """The addon's own stamp, read off disk. None when it cannot be read.

    This is the half of the answer that needs no running game: the installed
    revision is a constant in a file that is sitting right there.
    """
    path = project_path / "addons" / "godot_selftest" / "dev_tools.gd"
    try:
        m = _ADDON_VERSION_RE.search(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _version_tuple(text):
    try:
        return tuple(int(x) for x in str(text).strip().split("."))
    except ValueError:
        return ()


def _plugin_versions_on_this_machine() -> dict:
    """The harness versions this MACHINE can offer, read from disk (H-064).

    A project stays on the version it was scaffolded with until someone re-runs
    /scaffold-godot-harness, and nothing used to say that a newer one was already
    sitting in the plugin cache: two real projects ran 0.21.0 and 0.25.0 for a
    day in which the harness shipped 0.26.0-0.31.0, and roughly half the gaps
    their logs pooled upstream had been fixed releases earlier. Every value here
    is what a file on disk says - no network, no guess about the newest release.

    Keys: plugin_root (the plugin Claude Code is running THIS session from, if
    $CLAUDE_PLUGIN_ROOT is set), plugin_cache (installed_plugins.json's record),
    marketplace (the marketplace clone's plugin.json). Absent = unreadable.
    """
    out = {}
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        v = _read_plugin_json_version(Path(root) / ".claude-plugin" / "plugin.json")
        if v:
            out["plugin_root"] = v
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
    plugins = home / ".claude" / "plugins"
    try:
        installed = json.loads((plugins / "installed_plugins.json").read_text(encoding="utf-8"))
        for key, entries in (installed.get("plugins") or {}).items():
            if not key.startswith("godot-selftest-harness@"):
                continue
            versions = [e.get("version") for e in (entries or []) if _version_tuple(e.get("version"))]
            if versions:
                out["plugin_cache"] = max(versions, key=_version_tuple)
    except (OSError, ValueError, AttributeError):
        pass
    v = _read_plugin_json_version(
        plugins / "marketplaces" / "godot-selftest-harness" / ".claude-plugin" / "plugin.json")
    if v:
        out["marketplace"] = v
    return out


def _plugin_roots_on_this_machine() -> dict:
    """{source key: (version, root Path)} for every harness root disk can prove
    (gh#45, 0.44.0) - the same three sources as _plugin_versions_on_this_machine,
    with the directory, so the newest one's templates can be read."""
    out = {}
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        v = _read_plugin_json_version(Path(root) / ".claude-plugin" / "plugin.json")
        if v:
            out["plugin_root"] = (v, Path(root))
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
    plugins = home / ".claude" / "plugins"
    try:
        installed = json.loads((plugins / "installed_plugins.json").read_text(encoding="utf-8"))
        for key, entries in (installed.get("plugins") or {}).items():
            if not key.startswith("godot-selftest-harness@"):
                continue
            best = None
            for e in entries or []:
                if _version_tuple(e.get("version")) and e.get("installPath"):
                    if best is None or _version_tuple(e["version"]) > _version_tuple(best[0]):
                        best = (e["version"], Path(e["installPath"]))
            if best:
                out["plugin_cache"] = best
    except (OSError, ValueError, AttributeError):
        pass
    mp = plugins / "marketplaces" / "godot-selftest-harness"
    v = _read_plugin_json_version(mp / ".claude-plugin" / "plugin.json")
    if v:
        out["marketplace"] = (v, mp)
    return out


_LOG_OPEN_GAP_RE = re.compile(r"^\s*-\s*\[(G-\d+[a-z]?)\]\s*status:\s*open\b", re.M)


def _project_gap_names(project_path: Path):
    """The names a project's gaps are credited under upstream: `config/name` from
    project.godot and the checkout's directory name (either may be the one the pool
    used)."""
    names = {project_path.name}
    try:
        text = (project_path / "project.godot").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^config/name="([^"]*)"', text, re.M)
        if m and m.group(1).strip():
            names.add(m.group(1).strip())
    except OSError:
        pass
    return sorted(n for n in names if n)


def fixed_upstream_for_project(project_path: Path, newest_root: Path):
    """The project's still-OPEN gap ids (from its own log-devtools.md) that the newest
    templates on this machine credit by name in their source (gh#45, 0.44.0).

    Fixes credit the reporting project's id in a comment (`# moving-in:G-060: ...`),
    which is machine-readable; a project pinned at 0.36.0 verified fixes against the
    harness it RUNS for eleven cycles and could not find any, by construction. Returns
    (ids fixed only in the newer templates, ids already credited in the templates the
    project runs, [names searched]); empty when the log or templates are unreadable.
    """
    names = _project_gap_names(project_path)
    log = project_path / "log-devtools.md"
    try:
        open_ids = set(_LOG_OPEN_GAP_RE.findall(log.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return [], [], names
    if not open_ids:
        return [], [], names
    pats = [re.compile(r"\b%s:(G-\d+[a-z]?)\b" % re.escape(n)) for n in names]

    def credits_under(*dirs):
        found = set()
        for d in dirs:
            if not d.is_dir():
                continue
            for f in list(d.rglob("*.py")) + list(d.rglob("*.gd")) + list(d.rglob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for pat in pats:
                    found.update(pat.findall(text))
        return found

    newest = credits_under(newest_root / "templates")
    # The templates the project already RUNS: a credit there is a fix it has, so its
    # log's status line is stale, not its install.
    installed = credits_under(project_path / "addons" / "godot_selftest", project_path / "tools")
    order = lambda g: (len(g), g)
    return (sorted(open_ids & (newest - installed), key=order),
            sorted(open_ids & installed, key=order), names)


def _releases_behind(project_version: str, newest_root: Path):
    """How many recorded releases sit between the project's version and the newest,
    from the newest root's harness_history.json; None when unreadable."""
    try:
        hist = json.loads((newest_root / "harness_history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pv = _version_tuple(project_version)
    return sum(1 for v in hist if _version_tuple(v) > pv)


def _read_plugin_json_version(path: Path):
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("version") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def _print_plugin_staleness(project_version: str, project_path: "Path | None" = None) -> None:
    """One line per source this machine has, and a verdict against the project."""
    avail = _plugin_versions_on_this_machine()
    if not avail:
        return
    labels = {"plugin_root": "this session's plugin", "plugin_cache": "plugin cache",
              "marketplace": "marketplace clone"}
    print("Machine: " + ", ".join(
        f"{labels[k]} {avail[k]}" for k in ("plugin_root", "plugin_cache", "marketplace") if k in avail))
    newest = max(avail.values(), key=_version_tuple)
    if _version_tuple(newest) > _version_tuple(project_version):
        # gh#45: a version number is a nag; a release count and the project's OWN
        # gap ids credited in the newer templates are a reason to act.
        roots = _plugin_roots_on_this_machine()
        newest_root = None
        for v, r in roots.values():
            if v == newest:
                newest_root = r
                break
        behind = _releases_behind(project_version, newest_root) if newest_root else None
        gap = f" - {behind} release(s) behind" if behind else ""
        print(f"  A newer harness ({newest}) is already on this machine than this project "
              f"runs ({project_version}){gap}. /scaffold-godot-harness refreshes it; gaps you log "
              f"against {project_version} may be fixed there already.", file=sys.stderr)
        if newest_root is not None:
            fixed, stale, names = fixed_upstream_for_project(
                project_path or Path(".").resolve(), newest_root)
            if fixed:
                print(f"  {len(fixed)} gap(s) this project filed ({', '.join(fixed)}) are credited "
                      f"as fixed in releases it does not have (searched {newest}'s templates for "
                      f"{', '.join(names)}:G-*). Run /scaffold-godot-harness.", file=sys.stderr)
            if stale:
                print(f"  {len(stale)} open gap(s) in this project's log ({', '.join(stale)}) are "
                      f"already credited in the templates it RUNS - the fix is installed; the "
                      f"log's status line is what is stale.", file=sys.stderr)


def _warn_shared_user_dir(user_dir: Path, project_path: Path) -> None:
    """H-067: say when this checkout's user:// was last used by a game from ANOTHER
    checkout - a copy or worktree of the same `config/name` resolves to the same
    directory, and only `use_custom_user_dir=true` + `custom_user_dir_name` in the
    copy's project.godot moves it (`custom_user_dir_name` alone is ignored, which is
    how a 0.37.0 probe rewrote a developer's real save). The owner file already
    carries the checkout path; this is the one place it is compared before launch."""
    owner, _ = _read_owner(user_dir)
    other = owner.get("project_path") if isinstance(owner, dict) else None
    if not other or _same_project_dir(other, project_path):
        return
    print(f"user://: {user_dir} was last used by a game running from a DIFFERENT checkout "
          f"({other}). A copy or worktree of this project shares the same saves, screenshots "
          f"and baselines. `--snapshot-userstate` protects the save; to really separate it, "
          f"set application/config/use_custom_user_dir=true AND custom_user_dir_name in this "
          f"copy's project.godot (the name alone is ignored).", file=sys.stderr)


def _harness_version_offline(project_path: Path, as_json: bool, why: str) -> int:
    """Print what disk alone can prove, and say plainly what is unknown.

    Every gaps-log entry is written after the session is over, which is exactly
    when the bridge is down - so failing the whole verb made the `harness:` field
    it exists to fill unfillable at the only moment anyone fills it, and the field
    got copied from a neighbouring entry instead (gather:G-116, gather:G-138).
    """
    installed = _installed_addon_version(project_path)
    if as_json:
        print(json.dumps({
            "client": HARNESS_VERSION,
            "installed": installed,
            "harness_version": None,
            "bridge": "not asked" if why is None else "cold",
            "reason": why,
            "machine": _plugin_versions_on_this_machine(),
        }, indent=2))
    else:
        print(f"Client:    {HARNESS_VERSION}  (tools/devtools.py)")
        print(f"Installed: {installed or 'unreadable'}  "
              "(addons/godot_selftest/dev_tools.gd, read from disk)")
        if why is None:
            print("Game:      not asked (--client).")
        else:
            print("Game:      unknown - the bridge is cold, so the RUNNING build was "
                  "not asked.")
            print(f"  ({why})", file=sys.stderr)
        _print_plugin_staleness(installed or HARNESS_VERSION, project_path)
    if installed is not None and installed != HARNESS_VERSION:
        print(f"\nWARNING: half-refreshed install - the addon on disk is {installed} "
              f"and this client is {HARNESS_VERSION}. Re-run /scaffold-godot-harness.",
              file=sys.stderr)
        return 1
    return 0


def cmd_harness_version(args, project_path: Path):
    """Report the harness revision installed game-side, and this client's own.

    Data keys read: harness_version, handlers, extension_loaded (see the
    _cmd_harness_version docstring in dev_tools.gd - these two halves must agree).

    With no game running this falls back to the two revisions disk can prove
    (this client's, and the installed addon's constant) rather than failing.
    """
    if getattr(args, "client", False):
        # moving-in:G-055: the log-entry format wants the installed version on
        # every turn, most of which have no game running; the bus-first path
        # printed a "game not running" warning before the answer every time,
        # which trains a reader to skip it. This never opens the bus.
        sys.exit(_harness_version_offline(project_path, getattr(args, "json", False), None))
    try:
        result = send_command(project_path, "harness_version")
    except BridgeError as e:
        sys.exit(_harness_version_offline(
            project_path, getattr(args, "json", False), str(e).splitlines()[0]))

    if not result.get("success", False):
        message = result.get("message", "")
        if "Unknown action" in message:
            # A pre-0.5.0 autoload: the verb does not exist over there at all.
            print(f"Client:  {HARNESS_VERSION}  (tools/devtools.py)")
            print("Game:    pre-0.5.0 - the running build has no 'harness_version' verb.")
            print("The installed addon is older than this client. Re-run "
                  "/scaffold-godot-harness to refresh it.", file=sys.stderr)
            sys.exit(1)
        print(f"Failed: {message}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data") or {}
    if args.json:
        print(json.dumps({"client": HARNESS_VERSION, **data,
                          "machine": _plugin_versions_on_this_machine()}, indent=2))
        return

    game_version = data.get("harness_version")
    if game_version is None:
        # Never paper over a missing key with a friendly line - that is exactly how
        # three wire-contract mismatches shipped invisibly in 0.4.0.
        print("harness-version: the reply carried no 'harness_version' key. "
              f"Keys present: {sorted(data)}", file=sys.stderr)
        sys.exit(1)

    print(f"Game:    {game_version}  (addons/godot_selftest/dev_tools.gd)")
    print(f"Client:  {HARNESS_VERSION}  (tools/devtools.py)")
    if "handlers" in data:
        ext = data.get("extension_loaded")
        suffix = "" if ext is None else (", extension loaded" if ext else ", no extension")
        print(f"Verbs:   {data['handlers']} registered{suffix}")
    _print_plugin_staleness(str(game_version), project_path)

    if game_version != HARNESS_VERSION:
        print(f"\nWARNING: half-refreshed install - the game is on {game_version} and this "
              f"client on {HARNESS_VERSION}. Re-run /scaffold-godot-harness.", file=sys.stderr)
        sys.exit(1)


# ==================== INPUT SIMULATION ====================


def cmd_input_press(args, project_path: Path):
    """Press and hold an input action."""
    cmd_args = {"action": args.action}
    if args.strength is not None:
        cmd_args["strength"] = args.strength

    result = send_command(project_path, "input_press", cmd_args)
    if result["success"]:
        print(f"Pressed: {args.action}")
        if result.get("data", {}).get("active_inputs"):
            print(f"Active inputs: {', '.join(result['data']['active_inputs'])}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_input_release(args, project_path: Path):
    """Release an input action."""
    result = send_command(project_path, "input_release", {"action": args.action})
    if result["success"]:
        print(f"Released: {args.action}")
        if result.get("data", {}).get("active_inputs"):
            print(f"Active inputs: {', '.join(result['data']['active_inputs'])}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_input_tap(args, project_path: Path):
    """Tap (press and release) an input action."""
    cmd_args = {"action": args.action}
    if args.hold:
        cmd_args["seconds"] = args.hold
    if args.strength is not None:
        cmd_args["strength"] = args.strength

    result = send_command(project_path, "input_tap", cmd_args)
    if result["success"]:
        hold_info = f" (hold: {args.hold}s)" if args.hold else ""
        print(f"Tapped: {args.action}{hold_info}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_input_clear(args, project_path: Path):
    """Release all simulated inputs."""
    result = send_command(project_path, "input_clear")
    if result["success"]:
        cleared = result.get("data", {}).get("cleared", [])
        if cleared:
            print(f"Cleared {len(cleared)} inputs: {', '.join(cleared)}")
        else:
            print("No active inputs to clear")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_input_list(args, project_path: Path):
    """List available input actions."""
    cmd_args = {"include_builtin": args.all}
    result = send_command(project_path, "input_actions", cmd_args)
    if result["success"]:
        actions = result.get("data", {}).get("actions", [])
        if not actions:
            print("No actions found")
            return

        print(f"Available actions ({len(actions)}):")
        for action in actions:
            pressed = " [PRESSED]" if action.get("pressed") else ""
            events = ", ".join(action.get("events", [])) or "(no keys)"
            print(f"  {action['name']}{pressed}: {events}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_input_sequence(args, project_path: Path):
    """Execute an input sequence from a JSON file."""
    seq_path = Path(args.file)
    if not seq_path.exists():
        print(f"Error: Sequence file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(seq_path, encoding="utf-8") as f:
            seq_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in sequence file: {e}", file=sys.stderr)
        sys.exit(1)

    steps = seq_data.get("steps", [])
    if not steps:
        print("Error: Sequence has no steps", file=sys.stderr)
        sys.exit(1)

    description = seq_data.get("description", "")
    if description:
        print(f"Running sequence: {description}")
    print(f"Executing {len(steps)} steps...")

    cmd_args = {"steps": steps}
    if args.timeout:
        cmd_args["timeout"] = args.timeout

    result = send_command(project_path, "input_sequence", cmd_args, timeout=args.timeout + 10 if args.timeout else 70)
    if result["success"]:
        print(f"Sequence started: {result.get('data', {}).get('sequence_id', 'unknown')}")
        print("Note: Sequence runs asynchronously. Check logs for completion.")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_mouse_move(args, project_path: Path):
    """Dispatch a real InputEventMouseMotion (bus verb: mouse_move, moving-in:G-029).

    Data keys read: relative, steps, position, mouse_mode.
    """
    cmd_args = {"relative": [float(args.relative[0]), float(args.relative[1])]}
    if args.steps is not None:
        cmd_args["steps"] = args.steps
    if args.position is not None:
        cmd_args["position"] = [float(args.position[0]), float(args.position[1])]
    if args.buttons is not None:
        cmd_args["buttons"] = args.buttons
    result = send_command(project_path, "mouse_move", cmd_args,
                          timeout=30.0 + (args.steps or 1) / 10.0)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data") or {}
    if "relative" not in data or "mouse_mode" not in data:
        print(f"mouse-move: the reply carried no 'relative'/'mouse_mode' key. Keys: {sorted(data)}",
              file=sys.stderr)
        sys.exit(1)
    print(result.get("message", ""))
    pos = data.get("position") or {}
    print(f"  cursor now at ({pos.get('x')}, {pos.get('y')}); mouse mode {data['mouse_mode']}")
    if data["mouse_mode"] == "captured":
        print("  note: the cursor is CAPTURED, so your physical mouse is a second input source "
              "on this camera between commands (moving-in:G-024)")


def cmd_reload(args, project_path: Path):
    """Re-read a resource from disk into the running game (bus verb: reload,
    moving-in:G-033). Data keys read: path, resource_class, was_cached."""
    result = send_command(project_path, "reload", {"path": args.path})
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data") or {}
    if "was_cached" not in data:
        print(f"reload: the reply carried no 'was_cached' key. Keys: {sorted(data)}", file=sys.stderr)
        sys.exit(1)
    print(result.get("message", ""))
    if not data["was_cached"]:
        print("  (nothing held it, so nothing changed on screen - the next load() will read the new file)")
    elif data.get("holders_updated") is False:
        print("  WARNING: the cached instance could NOT be updated in place (class changed?) - "
              "nodes holding the old resource still show the old content", file=sys.stderr)
        sys.exit(1)


def cmd_key(args, project_path: Path):
    """Tap a raw keyboard key by OS keycode name (bus verb: input_key, G-049).

    Unlike `input tap`, this dispatches a real InputEventKey (keycode AND
    physical_keycode set), so game code reading key events directly - rebinding
    screens, debug keys, text input - actually sees it.
    """
    cmd_args = {"key": args.key}
    if args.count is not None:
        cmd_args["count"] = args.count
    if args.hold_frames is not None:
        cmd_args["hold_frames"] = args.hold_frames

    result = send_command(project_path, "input_key", cmd_args)
    if result["success"]:
        print(result.get("message") or f"Tapped key: {args.key}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_input_state(args, project_path: Path):
    """Read the polled pressed/strength state of input actions (G-021)."""
    cmd_args = {}
    if args.actions:
        cmd_args["actions"] = args.actions

    result = send_command(project_path, "input_state", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", {}) or {}
    states = data.get("actions", {}) or {}
    if not states:
        print("No actions to report")
    else:
        print(f"Action states ({len(states)}):")
        for name in sorted(states):
            state = states[name] or {}
            label = "PRESSED" if state.get("pressed") else "released"
            print(f"  {name}: {label} (strength {float(state.get('strength', 0.0)):.2f})")

    unknown = data.get("unknown", []) or []
    if unknown:
        print(f"Unknown action(s): {', '.join(str(u) for u in unknown)}", file=sys.stderr)
        sys.exit(1)


# ==================== NODE / TIME CONTROL ====================


def cmd_clear_nodes(args, project_path: Path):
    """Free scene nodes matching a selector (group, method, or class).

    Forwards exactly one selector to the generic clear_nodes handler. The
    handler refuses to run without a selector so we never blindly free the
    whole tree.
    """
    cmd_args = {}
    if args.group is not None:
        cmd_args["group"] = args.group
    if args.method is not None:
        cmd_args["method"] = args.method
    if getattr(args, "class_name", None) is not None:
        cmd_args["class"] = args.class_name

    if not cmd_args:
        print("Error: Specify a selector: --group, --method, or --class", file=sys.stderr)
        sys.exit(1)

    if args.via_method:
        cmd_args["via_method"] = args.via_method
        if args.via_args:
            try:
                cmd_args["via_args"] = json.loads(args.via_args)
            except json.JSONDecodeError as e:
                print(f"Error: Invalid JSON in --via-args: {e}", file=sys.stderr)
                sys.exit(1)

    result = send_command(project_path, "clear_nodes", cmd_args)
    data = result.get("data") or {}
    count = data.get("count", 0)
    how = data.get("via") or "queue_free()"
    print(f"Cleared {count} node(s) via {how}")
    if data.get("skipped"):
        print(f"  {len(data['skipped'])} matched but lack that method: "
              f"{', '.join(data['skipped'])}", file=sys.stderr)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_set_game_speed(args, project_path: Path):
    """Set game speed (time scale)."""
    result = send_command(project_path, "set_game_speed", {"scale": args.scale})
    if result["success"]:
        data = result["data"]
        # 3 dp: a 1-dp print echoed 0.04 as "1.0 -> 0.0" (moving-in:G-019).
        print(f"Game speed: {data['previous_scale']:.3f} -> {data['current_scale']:.3f}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_look_at(args, project_path: Path):
    """Orient a Node3D (default: the active Camera3D) to face another node (gh#28)."""
    cmd_args = {"node": normalize_node_path(args.node)}
    if args.from_node:
        cmd_args["from_node"] = normalize_node_path(args.from_node)
    if args.up is not None:
        if len(args.up) != 3:
            print("Error: --up takes exactly X,Y,Z", file=sys.stderr)
            sys.exit(1)
        cmd_args["up"] = args.up
    result = send_command(project_path, "look_at", cmd_args)
    if result["success"]:
        print(result["message"])
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_fire_entry_point(args, project_path: Path):
    """Fire a named entry_points entry from devtools_config.json (gh#29).

    45s, not the usual 30: a scene-changing entry point polls for its node up to
    ENTRY_HOOK_TIMEOUT_SEC (10s) game-side on top of however long the scene itself
    takes to load.
    """
    result = send_command(project_path, "fire_entry_point", {"name": args.name}, timeout=45.0)
    if result["success"]:
        print(result["message"])
        data = result.get("data") or {}
        if data.get("scene_changed"):
            print(f"  scene changed to reach {data.get('node_path')}")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_pause(args, project_path: Path):
    """Pause SceneTree.paused directly (gh#26). Data keys read: always_count,
    always_roots (0.40.0, moving-in:G-061) - the nodes that keep processing."""
    result = send_command(project_path, "pause", {})
    if result["success"]:
        print(result["message"])
        data = result.get("data") or {}
        roots = data.get("always_roots")
        if roots and len(roots) > 5:
            print("  PROCESS_MODE_ALWAYS set on: " + ", ".join(str(r) for r in roots))
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_unpause(args, project_path: Path):
    """Unpause SceneTree.paused directly (gh#26)."""
    result = send_command(project_path, "unpause", {})
    if result["success"]:
        print(result["message"])
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_wait_frames(args, project_path: Path):
    """Wait for N physics frames."""
    timeout = max(30, args.count / 10)
    result = send_command(project_path, "wait_frames", {"count": args.count}, timeout=timeout)
    if result["success"]:
        data = result["data"]
        print(f"Waited {data['frames']} frames ({data['elapsed_ms']}ms)")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_step_time(args, project_path: Path):
    """Advance the running game by roughly N game-seconds.

    It does NOT pause and step the tree — GDScript cannot tick the SceneTree. The
    game runs at normal speed with `Engine.time_scale` pinned to 1.0 and returns
    once enough time has passed: physics time is exact, process time (which is what
    a default Tween runs on) lands within about a frame. The game's own message is
    printed verbatim rather than this client claiming a precision it does not have.
    """
    # Stepping is bounded by how fast the game can run the frames, so give it
    # generous headroom over the requested game-time.
    timeout = max(30.0, args.seconds * 4 + 10)
    cmd_args = {"seconds": args.seconds}
    if getattr(args, "hold", None):
        # G-084: hold an action pressed across the whole step, released at the end.
        cmd_args["hold"] = args.hold
    if getattr(args, "then_pause", False):
        # plant-tower-defense:G-016: freeze the tree the moment the step lands.
        cmd_args["then_pause"] = True
    result = send_command(project_path, "step_time", cmd_args, timeout=timeout)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    print(result.get("message", "") or f"Stepped {args.seconds}s")
    data = result.get("data", {}) or {}
    print(f"  Requested:      {args.seconds}s")
    if data.get("held_action"):
        print(f"  Held action:    {data['held_action']} (released at the end)")
    if "physics_seconds" in data:
        print(f"  Physics time:   {float(data['physics_seconds']):.4f}s (exact)")
    if "process_seconds" in data:
        print(f"  Process time:   {float(data['process_seconds']):.4f}s (measured, +/- one frame)")
    if "frames_advanced" in data:
        print(f"  Frames:         {int(data['frames_advanced'])}")
    if "elapsed_wall_ms" in data:
        # G-016: the wall clock the step actually took, so the gap between "what
        # I advanced" and "what really passed" is visible instead of inferred.
        print(f"  Wall clock:     {int(data['elapsed_wall_ms']) / 1000.0:.3f}s")
    if data.get("paused_after"):
        print("  Tree left PAUSED (--then-pause); `unpause` resumes it.")
    if data.get("tree_paused"):
        # A paused tree still emits frames while nothing advances, so this would
        # otherwise look like a successful step of a frozen game.
        print("  WARNING: the tree is paused - nothing actually advanced.")
    if data.get("budget_exhausted"):
        print("  WARNING: frame budget exhausted before the target was reached.")


# ==================== TOUCH SIMULATION ====================


def coord_2_or_3(value: str):
    """Parse "X,Y" (2D) or "X,Y,Z" (3D) into a float list; the arity picks the
    physics space game-side (moving-in:G-023)."""
    text = str(value).strip().strip("()[]").strip()
    parts = [p for p in re.split(r"[,\s]+", text) if p]
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError(
            f"expected 'X,Y' (2D) or 'X,Y,Z' (3D), got {value!r}")
    try:
        return [float(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"non-numeric coordinate in {value!r}")


def coord_pair(value: str):
    """Parse an "X,Y" coordinate flag into [x, y] floats.

    Shared by every --pos / --to flag so a malformed value fails the same way
    everywhere. Tolerates whitespace and wrapping parens/brackets, e.g.
    "640,360", "640, 360", "(640, 360)".
    """
    text = str(value).strip().strip("()[]").strip()
    parts = [p for p in re.split(r"[,\s]+", text) if p]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"expected two comma-separated numbers 'X,Y' (e.g. 640,360), got {value!r}"
        )
    try:
        return [float(parts[0]), float(parts[1])]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"both coordinates must be numbers (e.g. 640,360), got {value!r}"
        )


def _format_position(pos) -> str:
    """Render a position the game reported back, whatever shape it used."""
    if isinstance(pos, dict):
        return f"({pos.get('x', '?')}, {pos.get('y', '?')})"
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        return f"({pos[0]}, {pos[1]})"
    return str(pos)


def _active_touches(result: dict):
    """Pull the active-touch list out of a reply, tolerating either key."""
    data = result.get("data", {}) or {}
    touches = data.get("touches")
    if touches is None:
        touches = data.get("active_touches")
    return touches or []


def _print_active_touches(result: dict):
    touches = _active_touches(result)
    if not touches:
        return
    labels = []
    for touch in touches:
        if isinstance(touch, dict):
            labels.append(f"{touch.get('index', '?')}@{_format_position(touch.get('position'))}")
        else:
            labels.append(str(touch))
    print(f"Active touches: {', '.join(labels)}")


def cmd_touch_press(args, project_path: Path):
    """Press a touch point (InputEventScreenTouch, pressed)."""
    result = send_command(project_path, "touch_press", {"index": args.index, "position": args.pos})
    if result["success"]:
        print(f"Touch {args.index} pressed at {args.pos[0]:g},{args.pos[1]:g}")
        _print_active_touches(result)
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_touch_release(args, project_path: Path):
    """Release a touch point."""
    cmd_args = {"index": args.index}
    if args.pos is not None:
        cmd_args["position"] = args.pos

    result = send_command(project_path, "touch_release", cmd_args)
    if result["success"]:
        print(f"Touch {args.index} released")
        _print_active_touches(result)
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_touch_drag(args, project_path: Path):
    """Drag a touch point (InputEventScreenDrag) to a new position."""
    cmd_args = {"index": args.index, "to": args.to}
    if args.pos is not None:
        cmd_args["from"] = args.pos
    if args.steps is not None:
        cmd_args["steps"] = args.steps

    result = send_command(project_path, "touch_drag", cmd_args)
    if result["success"]:
        origin = f"{args.pos[0]:g},{args.pos[1]:g}" if args.pos else "current position"
        steps_info = f" in {args.steps} steps" if args.steps else ""
        print(f"Touch {args.index} dragged from {origin} to {args.to[0]:g},{args.to[1]:g}{steps_info}")
        _print_active_touches(result)
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def _touch_label(entry):
    """Render one touch point as `index@(x, y)`, tolerating a bare index."""
    if not isinstance(entry, dict):
        return str(entry)
    pos = entry.get("position") or {}
    if isinstance(pos, dict) and "x" in pos:
        return f"{entry.get('index', '?')}@({pos['x']}, {pos['y']})"
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        return f"{entry.get('index', '?')}@({pos[0]}, {pos[1]})"
    return str(entry.get("index", entry))


def cmd_touch_clear(args, project_path: Path):
    """Release all simulated touch points."""
    result = send_command(project_path, "touch_clear")
    if result["success"]:
        data = result.get("data", {}) or {}
        # The core reports "released"; accept "cleared" too so a newer client
        # still reads an older game build.
        cleared = data.get("released", data.get("cleared", []))
        if cleared:
            print(f"Cleared {len(cleared)} touch(es): {', '.join(_touch_label(c) for c in cleared)}")
        else:
            print("No active touches to clear")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_touch_list(args, project_path: Path):
    """List currently-held simulated touch points."""
    result = send_command(project_path, "touch_list")
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    touches = _active_touches(result)
    if not touches:
        print("No active touches")
        return

    print(f"Active touches ({len(touches)}):")
    for touch in touches:
        if isinstance(touch, dict):
            print(f"  [{touch.get('index', '?')}] {_format_position(touch.get('position'))}")
        else:
            print(f"  {touch}")


# ==================== ENGINE FEATURE OVERRIDES ====================


def bool_arg(value: str) -> bool:
    """Parse a true/false flag value."""
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def cmd_set_feature(args, project_path: Path):
    """Override an engine feature probe (e.g. fake a touchscreen).

    Prints what the game reports as the *resulting* state: an override the
    engine refused must not read as success.
    """
    if getattr(args, "query", False):
        # Read-only (G-033): report the current flag values without writing.
        result = send_command(project_path, "set_feature", {"query": True})
        if not result["success"]:
            print(f"Failed: {result['message']}", file=sys.stderr)
            sys.exit(1)
        data = result.get("data", {}) or {}
        for key in ("touchscreen_available", "emulate_touch_from_mouse"):
            if key in data:
                print(f"{key}: {json.dumps(data[key])}")
        return

    requested = {}
    if args.touchscreen is not None:
        requested["touchscreen"] = args.touchscreen

    if not requested:
        print("Error: Specify a feature (--touchscreen true|false) or --query to read the current values",
              file=sys.stderr)
        sys.exit(1)

    result = send_command(project_path, "set_feature", requested)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", {}) or {}
    applied = data.get("applied", {}) or {}

    # Where the core reports the *resulting* engine state for each feature. This is
    # deliberately not the flag we asked it to set: the point of the check is to
    # catch an override the engine silently refused, so we read what the engine now
    # says, not what we requested.
    RESULT_KEYS = {"touchscreen": "touchscreen_available"}

    rejected = []
    for name, wanted in requested.items():
        result_key = RESULT_KEYS.get(name, name)
        if result_key in data:
            actual = data[result_key]
            print(f"{name}: {json.dumps(actual)}")
            if bool(actual) != bool(wanted):
                rejected.append(name)
        elif name in applied:
            # The core acknowledged the flag but reports no engine-level probe for
            # it — say so rather than implying it was verified.
            print(f"{name}: applied {json.dumps(applied[name])} (no engine-level probe to confirm it)")
        else:
            # Older build that doesn't echo the resulting state: report the
            # game's own message rather than asserting it took effect.
            print(f"{name}: requested {json.dumps(wanted)} (game reported no resulting state)")

    if data.get("unknown"):
        print(f"Warning: the game did not recognize: {', '.join(str(u) for u in data['unknown'])}",
              file=sys.stderr)

    if result.get("message"):
        print(result["message"])

    if rejected:
        print(
            "Failed: the game reports the override did not take effect for: "
            f"{', '.join(rejected)}",
            file=sys.stderr,
        )
        sys.exit(1)


# ==================== CONSOLIDATED FINDINGS ====================

# Every data key the `findings` reply must carry. Checked as a set, up front,
# and named in the error when one is missing. This is not defensive style for
# its own sake: three key mismatches shipped green in 0.4.0 because each half
# was tested against a fake of the other, and every one of them printed a
# friendly line instead of admitting the key was gone.
_FINDINGS_REQUIRED_KEYS = (
    "findings",
    "counts",
    "checks_run",
    "checks_skipped",
    "viewport",
    "baseline_in_use",
    "new_count",
    "pre_existing_count",
    "geometry_trustworthy",
    "geometry_caveat",
)

# Most severe first, so the worst finding in a report is the one at the top.
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SEVERITY_LABEL = {"error": "ERROR", "warning": "WARN", "info": "INFO"}


def cmd_findings(args, project_path: Path):
    """Every live check the harness knows, in one call (bus verb: findings).

    Data keys read: findings, counts, checks_run, checks_skipped, viewport,
    baseline_in_use, new_count, pre_existing_count, scroll_reachable,
    geometry_trustworthy, geometry_caveat (and a per-finding `caveat` on
    headless geometry verdicts).

    Exit codes follow the rest of the tool: 0 clean, 1 gating findings, 2 could
    not run (which includes a reply whose shape this client does not recognize
    - an unreadable answer is not a clean one).
    """
    cmd_args = {}
    if getattr(args, "no_scenes", False):
        cmd_args["scenes"] = False
    if getattr(args, "no_baseline", False):
        cmd_args["use_baseline"] = False
    if getattr(args, "baseline_write", False):
        cmd_args["baseline_write"] = True

    # Scene validation loads every scene under scan_root; give it room.
    timeout = 30.0 if cmd_args.get("scenes") is False else 120.0
    result = send_command(project_path, "findings", cmd_args, timeout=timeout)

    if args.json:
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)

    data = result.get("data") or {}
    missing = [k for k in _FINDINGS_REQUIRED_KEYS if k not in data]
    if missing:
        # No friendly summary line here on purpose. A reply we cannot read is
        # reported as unreadable, never as a result.
        print(
            "findings: the reply is missing required data key(s): "
            f"{', '.join(missing)}. Keys present: {sorted(data)}. "
            "This game's harness is older than this client, or the verb changed "
            "on one side only.",
            file=sys.stderr,
        )
        sys.exit(2)

    findings = data["findings"]
    checks_run = data["checks_run"]
    checks_skipped = data["checks_skipped"]
    vp = data["viewport"]

    # The denominator. Sub-conditions skipped inside a check that DID run carry
    # a dotted id ("performance.orphan_growth") and are reported but not counted
    # as whole checks, so run + dotless-skipped is the number of checks there are.
    top_level_skipped = {
        str(s.get("check", "?")) for s in checks_skipped if "." not in str(s.get("check", "?"))
    }
    total_checks = len(checks_run) + len(top_level_skipped)

    print(f"{len(findings)} finding(s) across {len(checks_run)} of {total_checks} "
          f"checks ({int(vp['w'])}x{int(vp['h'])})")

    # Group by code, most severe group first, then by size.
    groups = {}
    for f in findings:
        groups.setdefault(str(f.get("code", "?")), []).append(f)

    def _rank(entry):
        code, items = entry
        worst = min(_SEVERITY_ORDER.get(str(i.get("severity", "")), 3) for i in items)
        return (worst, -len(items), code)

    caveated = 0
    for code, items in sorted(groups.items(), key=_rank):
        items.sort(key=lambda i: _SEVERITY_ORDER.get(str(i.get("severity", "")), 3))
        sources = sorted({str(i.get("source", "?")) for i in items})
        worst = _SEVERITY_LABEL.get(str(items[0].get("severity", "")), "???")
        print(f"  {code:<22} {len(items):>3}  [{worst}] {', '.join(sources)}")
        for item in items:
            where = str(item.get("path", "")) or "-"
            flag = "  [HEADLESS geometry - confirm windowed]" if item.get("caveat") else ""
            caveated += 1 if item.get("caveat") else 0
            print(f"      {where}: {_printable(str(item.get('message', '')))}{flag}")

    # H-051: a screen-position finding measured headless is a finding about the
    # headless run, not necessarily about what a player sees. It still gates
    # (headless is where CI lives), but it must not be read as fact unqualified.
    if not data["geometry_trustworthy"]:
        if caveated:
            print(f"\n{caveated} of {len(findings)} finding(s) are screen-position verdicts "
                  f"measured HEADLESS - flagged above.\n  {_printable(str(data['geometry_caveat']))}")
        else:
            print(f"\nGeometry {_printable(str(data['geometry_caveat']))}")

    # Per-source counts, including the sources that ran and found nothing: a 0
    # and an absent source mean different things and must not print the same.
    counts = data["counts"]
    if counts:
        print("\nBy check: " + ", ".join(
            f"{src}={counts[src]}" for src in sorted(counts)))
    # gh#16: rows past the fold of a ScrollContainer are reachable by scrolling.
    # They are reported so the number is visible, and never gate.
    scroll_reachable = int(data.get("scroll_reachable", 0) or 0)
    if scroll_reachable:
        print(f"ui_reachable: {scroll_reachable} control(s) reachable only by scrolling a "
              "ScrollContainer - reported, not gated (reachable-ui lists them as SCROLL TO REACH).")

    if checks_skipped:
        print(f"\n{len(checks_skipped)} check(s) did NOT run:")
        for entry in checks_skipped:
            print(f"  {entry.get('check', '?')}: {entry.get('reason', 'no reason given')}")
    else:
        print("\nAll checks ran.")

    # The UI baseline, carried through from validate_ui. Pre-existing findings
    # are excluded from the list above, so say how many were excluded rather
    # than letting them vanish.
    if "ui_layout" not in checks_run:
        # The baseline numbers are carried through even when the UI check was
        # skipped. Advertising "run --baseline-write" here would be advice about
        # a check that did not happen.
        print("UI baseline: not consulted (the ui_layout check did not run).")
    elif data["baseline_in_use"]:
        print(f"UI baseline: {data['new_count']} NEW, {data['pre_existing_count']} "
              "pre-existing (excluded above). Only NEW ui_layout findings gate.")
    elif cmd_args.get("use_baseline") is False:
        # Distinct from "there isn't one": the file may well exist, we asked the
        # game to ignore it. Reporting that as "none on disk" would send someone
        # off to write a baseline that is already there.
        print("UI baseline: ignored (--no-baseline) - every ui_layout finding gates.")
    else:
        print("UI baseline: none on disk - every ui_layout finding gates. "
              "Run `findings --baseline-write` to accept the current UI set.")

    # gh#41 (0.40.0): the signal_unconnected baseline, own file, keyed on
    # (script, signal). Older games have no such keys: say so rather than guess.
    if "signal_baseline_in_use" not in data:
        print("Signal baseline: not reported (this game's harness predates 0.40.0; every "
              "signal_unconnected finding gates, one line per NODE).")
    elif data.get("signal_baseline_written"):
        print(f"Signal baseline: written to {data.get('signal_baseline_path')} - "
              f"{data.get('signal_pre_existing_count', 0)} (script, signal) pair(s) accepted.")
    elif data["signal_baseline_in_use"]:
        print(f"Signal baseline: {data.get('signal_new_count', 0)} NEW, "
              f"{data.get('signal_pre_existing_count', 0)} pre-existing (script, signal) pair(s) "
              "(excluded above). Only NEW signal_unconnected findings gate.")
    elif cmd_args.get("use_baseline") is False:
        print("Signal baseline: ignored (--no-baseline) - every signal_unconnected pair gates.")
    elif data.get("signal_new_count", 0):
        print(f"Signal baseline: none on disk - {data.get('signal_new_count', 0)} "
              "signal_unconnected pair(s) gate. A signal left unconnected on purpose (an "
              "outward API, a documented decision) is accepted with `findings --baseline-write`.")

    # plant-tower-defense:G-030: full records survive the frame they came from.
    if data.get("last_findings_path"):
        print(f"Full records kept at {data['last_findings_path']} "
              "(overwritten by the next non-clean run).")

    if not result.get("success"):
        sys.exit(1)


# ==================== UI VALIDATION ====================


def cmd_validate_ui(args, project_path: Path):
    """Run all UI layout checks, split NEW vs PRE-EXISTING against a baseline."""
    cmd_args = {}
    if getattr(args, "baseline_write", False):
        cmd_args["baseline_write"] = True
    if getattr(args, "no_baseline", False):
        cmd_args["use_baseline"] = False
    result = send_command(project_path, "validate_ui", cmd_args)
    print_validation_result(result, geometry_verb="validate-ui")


def cmd_save_ui_baseline(args, project_path: Path):
    """Save current UI layout as baseline for diff comparison."""
    result = send_command(project_path, "save_ui_baseline")
    if result["success"]:
        print(f"Baseline saved: {result['data']['nodes_saved']} nodes")
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


def cmd_ui_snapshot_diff(args, project_path: Path):
    """Compare current UI layout against saved baseline."""
    result = send_command(project_path, "ui_snapshot_diff")
    if not result["success"]:
        if result.get("data", {}).get("status") == "drift_detected":
            print(f"[DRIFT] {result['message']}")
            for diff in result["data"].get("diffs", []):
                diff_type = diff.get("type", "changed")
                if diff_type == "new_node":
                    print(f"  + NEW: {diff['path']}")
                elif diff_type == "removed_node":
                    print(f"  - REMOVED: {diff['path']}")
                else:
                    print(f"  ~ CHANGED: {diff['path']}")
                    if "position_delta" in diff:
                        print(f"    pos delta: {diff['position_delta']}, size delta: {diff['size_delta']}")
            sys.exit(1)
        else:
            print(f"Failed: {result['message']}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"[OK] {result['message']}")


def cmd_ui_snapshot(args, project_path: Path):
    """Get snapshot of all visible UI elements."""
    result = send_command(project_path, "get_ui_snapshot")
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result["data"], indent=2))
        return

    data = result["data"]
    vp = data["viewport"]
    elements = data.get("elements", [])
    print(f"Viewport: {vp['width']}x{vp['height']}")
    print(f"UI Elements: {len(elements)}")
    print_geometry_caveat(data, "ui-snapshot", only_if_suspect=True)
    print()
    for el in elements:
        r = el["global_rect"]
        vis = "visible" if el["visible"] else "hidden"
        text_preview = f' "{el["text"]}"' if el.get("text") else ""
        if len(text_preview) > 53:
            text_preview = text_preview[:50] + '..."'
        print(f"  {el['name']} ({el['type']}) [{r['x']:.0f},{r['y']:.0f} {r['w']:.0f}x{r['h']:.0f}] {vis} alpha={el['modulate_a']:.1f}{text_preview}")


def cmd_contained_in(args, project_path: Path):
    """Is one Control's screen box inside another's? (bus verb: contained_in,
    plant-tower-defense:G-048b). Data keys read: inside, overhang, control_rect,
    within_rect, control_centre_inside, geometry_trustworthy, geometry_caveat.
    Exit 0 inside, 1 not (or unmeasurable)."""
    result = send_command(project_path, "contained_in", {
        "node_path": normalize_node_path(args.node), "within": normalize_node_path(args.within)})
    data = result.get("data") or {}
    if "inside" not in data:
        print(f"Failed: {result.get('message', '')}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(result.get("message", ""))
        c, w = data.get("control_rect", {}), data.get("within_rect", {})
        print(f"  control: {c.get('x', 0):.0f},{c.get('y', 0):.0f} {c.get('w', 0):.0f}x{c.get('h', 0):.0f}"
              f"   within: {w.get('x', 0):.0f},{w.get('y', 0):.0f} {w.get('w', 0):.0f}x{w.get('h', 0):.0f}")
        print_geometry_caveat(data, "contained-in", only_if_suspect=True)
    if not data["inside"]:
        sys.exit(1)


def cmd_node_bounds(args, project_path: Path):
    """Get bounds for a specific node."""
    args.node_path = normalize_node_path(args.node_path)  # G-025
    result = send_command(project_path, "get_node_bounds", {"node_path": args.node_path})
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result["data"]
    r = data["global_rect"]
    print(f"{data['name']} ({data['type']})")
    print(f"  Rect:         {r['x']:.0f}, {r['y']:.0f}, {r['w']:.0f}x{r['h']:.0f}")
    print(f"  Visible:      {data['visible']}")
    print(f"  Alpha:        {data['modulate_a']:.1f}")
    print(f"  In viewport:  {data['in_viewport']}")
    if data.get("text"):
        print(f"  Text:         \"{_printable(data['text'])}\"")
    # Say where the extent came from, because for a non-Control it is derived and
    # may be degenerate - a 0x0 rect is "I know the origin, not the size", which
    # must not read the same as "this node is zero-sized".
    if data.get("size_source"):
        print(f"  Size from:    {data['size_source']}")
        if r["w"] == 0 and r["h"] == 0:
            print("                (0x0: this class reports no extent - the "
                  "position is real, the size is unknown)")
    # The accumulated canvas scale belongs beside the rect, not behind a second
    # verb: a HUD on a scaled CanvasLayer is the commonest reason a rect looks
    # wrong, and the reader has no reason to suspect canvas-scale exists
    # (moving-in:G-008/G-015). Loud on absence rather than quietly skipped - a
    # silent fallback is what hid three wire mismatches in 0.4.0.
    cs = data.get("canvas_scale")
    if cs is None:
        print("node-bounds: the reply carried no 'canvas_scale' key. "
              f"Keys: {sorted(data)}", file=sys.stderr)
    else:
        note = "" if (abs(cs["x"] - 1.0) < 1e-6 and abs(cs["y"] - 1.0) < 1e-6) else \
            "   <- not 1.0: this rect is screen space, but a CanvasLayer is scaling it"
        print(f"  Canvas scale: {cs['x']:.3f}, {cs['y']:.3f}{note}")
    # Whether this rect is what a player sees, or a headless run's (H-051). A
    # single rect is read as an answer, so the trust state prints either way.
    print_geometry_caveat(data, "node-bounds")


def cmd_aabb(args, project_path: Path):
    """Merged world-space AABB of a 3D node's geometry (bus verb: aabb, G-002/G-006).

    The 3D counterpart of node-bounds, which is CanvasItem-only. Light3D nodes are
    excluded by the game side (an OmniLight3D's AABB is a box of twice its range,
    which measured a 0.2-unit lamp at 7.2 units), and everything skipped comes back
    in data["excluded"] with the reason - printed here, because a merge count that
    is lower than expected is only diagnosable if the skips are visible.
    """
    args.node = normalize_node_path(args.node)  # G-025
    result = send_command(project_path, "aabb", {"node_path": args.node})
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        # A node with no geometry fails on purpose rather than reporting a zero box;
        # the excluded list is the explanation, so it is worth printing on failure.
        for entry in (result.get("data", {}) or {}).get("excluded", []) or []:
            print(f"  excluded: {entry.get('path')} [{entry.get('class')}] "
                  f"- {entry.get('reason')}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", {}) or {}
    missing = [k for k in ("min", "max", "size", "center", "top_y", "bottom_y",
                           "merged_count", "merged", "excluded", "node_transform")
               if k not in data]
    if missing:
        # Never paper over a shape mismatch with a friendly line: three key
        # mismatches shipped at once in 0.4.0 because the client fell back quietly.
        print(f"aabb reply is missing key(s) {missing} (keys: {sorted(data)})",
              file=sys.stderr)
        sys.exit(1)

    lo, hi, size, mid = data["min"], data["max"], data["size"], data["center"]
    print(f"{data.get('name')} ({data.get('type')}) {data.get('path')}")
    print(f"  Size:      {size['x']:.3f} x {size['y']:.3f} x {size['z']:.3f}")
    print(f"  Min:       {lo['x']:.3f}, {lo['y']:.3f}, {lo['z']:.3f}")
    print(f"  Max:       {hi['x']:.3f}, {hi['y']:.3f}, {hi['z']:.3f}")
    print(f"  Center:    {mid['x']:.3f}, {mid['y']:.3f}, {mid['z']:.3f}")
    print(f"  Top y:     {data['top_y']:.3f}   (rest something on this)")
    print(f"  Bottom y:  {data['bottom_y']:.3f}")

    xform = data["node_transform"] or {}
    if xform and not {"rotation_deg", "scale", "axis_aligned"} <= set(xform):
        print(f"aabb node_transform is missing key(s) (keys: {sorted(xform)})",
              file=sys.stderr)
        sys.exit(1)
    if xform:
        rot, scl = xform["rotation_deg"], xform["scale"]
        aligned = xform["axis_aligned"]
        note = "" if aligned else "  <- ROTATED: the box encloses the footprint, it is not the footprint"
        print(f"  Rotation:  {rot.get('x'):.1f}, {rot.get('y'):.1f}, {rot.get('z'):.1f} deg"
              f"   scale {scl.get('x'):.3f}, {scl.get('y'):.3f}, {scl.get('z'):.3f}{note}")
    else:
        print("  Rotation:  (this node is not a Node3D; the box comes from its "
              "3D descendants)")

    print(f"  Merged:    {data['merged_count']} GeometryInstance3D node(s)")
    for entry in data["merged"]:
        vis = "visible" if entry.get("visible") else "HIDDEN"
        print(f"    {entry.get('path')} [{entry.get('class')}] {vis}")
    if data["excluded"]:
        print(f"  Excluded:  {len(data['excluded'])}")
        for entry in data["excluded"]:
            print(f"    {entry.get('path')} [{entry.get('class')}] - {entry.get('reason')}")


# ==================== LAUNCH / TILEMAP / SCRIPT CENSUS ====================


def launch_log_errors(*logs) -> list:
    """Every `ERROR:` line across the given launch logs, tagged with its file (gh#31).

    Read from BOTH launch_stdout.log and launch_stderr.log, not stderr alone: Godot's
    destination for a startup-abort message - "Main scene's path could not be
    resolved from UID... Aborting", from an --import that had printed what looked
    like a clean completion but never wrote uid_cache.bin - is not reliably stderr.
    import_check.py already learned this for the identical reason and combines both
    streams. Before this, a ping timeout tailed only stderr, so that ERROR: line sat
    unread in the other log while the generic timeout read identically to gh#28's
    unrelated missing-`--`-separator bug, and diagnosing it took a PowerShell
    screen-scrape of a native alert dialog. A pure function of the log paths so it
    can be tested without a real launch.
    """
    found = []
    for log in logs:
        try:
            text = Path(log).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "ERROR:" in line:
                found.append(f"{Path(log).name}: {line.strip()}")
    return found


def _await_bus(project_path: Path, session: str, bus_dir: str, seconds: float = 20.0):
    """Poll `ping` on the freshly launched instance's own bus. Reply dict or None.

    Printing a follow-up command that cannot work is worse than failing, because
    it reads as success - which is exactly how a half-applied `--isolated` cost
    three sessions (gather:G-111). So the command is verified before it is
    advertised.
    """
    global _SESSION, _USERDATA_OVERRIDE
    prev_session, prev_userdata = _SESSION, _USERDATA_OVERRIDE
    _SESSION = sanitize_session(session)
    if bus_dir:
        _USERDATA_OVERRIDE = bus_dir
    try:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                reply = send_command(project_path, "ping", {}, timeout=3.0)
                if isinstance(reply, dict) and reply.get("success"):
                    return reply
            except BridgeError:
                pass
            time.sleep(0.5)
        return None
    finally:
        _SESSION, _USERDATA_OVERRIDE = prev_session, prev_userdata


def cmd_launch(args, project_path: Path):
    """Launch the game detached, logs under .devtools/ (G-057).

    Godot binary resolution: --godot flag -> $GODOT_BIN -> the config's
    `godot_bin` key. stdout/stderr are redirected to files, NEVER
    subprocess.PIPE - an unread pipe fills and stalls Godot on Windows.

    --isolated gives the new instance its own session id AND its own bus
    directory (passed as `-- --devtools-busdir <dir>`, which the autoload
    honours), then proves the bus answers before printing the follow-up command.

    What --isolated does NOT do: move user:// itself. Godot resolves that inside
    the engine and has no switch for it, so saves, screenshots and baselines are
    still shared. The previous version printed a temp `userdata:` path as though
    it had moved them; nothing ever wrote there, and the follow-up command it
    printed failed with `game not running` (gather:G-091/G-111/G-115). The claim
    is now exactly as large as the behaviour.

    Everything after a bare `--` is forwarded to the Godot command line, so a run
    needing an engine flag (`--write-movie out/frame.png --fixed-fps 30`) no
    longer has to re-implement launching (gather:G-092).
    """
    global _SESSION
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

    # gh#28: split passthrough into godot-native args and --devtools- prefixed ones
    # BEFORE the owner pre-check below, not after - the pre-check reads _SESSION to
    # pick which owner file to look at (_owner_file_path splices it in), and a bare
    # `launch -- --devtools-session X` used to leave _SESSION empty this whole
    # function, so the pre-check examined the DEFAULT bus's owner file instead of
    # X's. With another instance alive on the default bus, that read as "a live
    # process already owns this bus" and refused to launch a session that would
    # never have touched it. Doing this split first, and adopting X into the global
    # _SESSION the same way --isolated/--session already do, fixes both the
    # pre-check and (further down) the actual command line and post-launch poll
    # with the one parse instead of three separate ones that could drift apart.
    #
    # argparse.REMAINDER keeps the separator itself; drop it so `launch -- --foo`
    # forwards `--foo` and not `-- --foo`.
    passthrough = list(getattr(args, "godot_args", None) or [])
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    native_passthrough = []
    devtools_passthrough = []
    i = 0
    while i < len(passthrough):
        token = passthrough[i]
        if token.startswith("--devtools-"):
            devtools_passthrough.append(token)
            if i + 1 < len(passthrough) and not passthrough[i + 1].startswith("--"):
                i += 1
                devtools_passthrough.append(passthrough[i])
        else:
            native_passthrough.append(token)
        i += 1
    passthrough_session = ""
    passthrough_busdir = ""
    for j, tok in enumerate(devtools_passthrough):
        if tok == "--devtools-session" and j + 1 < len(devtools_passthrough):
            passthrough_session = devtools_passthrough[j + 1]
        elif tok == "--devtools-busdir" and j + 1 < len(devtools_passthrough):
            passthrough_busdir = devtools_passthrough[j + 1]
    if devtools_passthrough:
        print(f"  note: --devtools- passthrough arg(s) routed after Godot's own -- "
              f"so they actually reach the addon: {' '.join(devtools_passthrough)}")
        if passthrough_session and not args.isolated:
            if _SESSION and _SESSION != passthrough_session:
                print(f"  WARNING: --session named {_SESSION!r} but passthrough also "
                      f"names {passthrough_session!r} - the passthrough value is what "
                      "reaches Godot; adopting it here too so the owner check and the "
                      "post-launch poll agree with the game.")
            _SESSION = passthrough_session

    # Refuse to add a second instance to a bus a LIVE process already owns
    # (gather:G-112): two instances answering one bus is silent data corruption,
    # and the owner file already carries everything needed to detect it. A dead
    # owner is ignored, not obeyed - that is the normal post-crash state - but it
    # is left on disk, because it is what the reply-pid check compares against.
    if not args.isolated:
        try:
            user_data = get_user_data_path(project_path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        owner = owner_status(user_data)
        # A pid alone is not ownership (findmyballs:G-004). Windows recycled a
        # dead Godot's pid onto an unrelated process and this refused to launch
        # at all, costing the project a workaround (--isolated) to get moving.
        # Block only on an owner that is BOTH alive and still polling; an owner
        # that has not polled in POLL_STALE_AFTER_SEC is not using this bus
        # whatever its pid says. polling is None on a pre-0.12.0 owner file -
        # unknown, so fall back to the old pid-only behaviour rather than
        # launching a second instance on top of a live one.
        stale_claim = owner["present"] and owner["alive"] and owner["polling"] is False
        if owner["present"] and owner["alive"] and not stale_claim:
            other = foreign_project_owner(owner, project_path)
            if other:
                print(f"  That game runs from a DIFFERENT checkout: {other} - a git "
                      "worktree of this project shares its name, its user:// and this bus.",
                      file=sys.stderr)
            print(f"Error: pid {owner['pid']} still owns this bus "
                  f"({owner['path'].name}).\n"
                  f"  {owner_liveness_phrase(_read_owner(user_data)[0] or {})}\n"
                  "Quit it first, or launch with --isolated to get a bus of your own.\n"
                  "Pass --allow-second-instance if you really mean to run two.",
                  file=sys.stderr)
            if not args.allow_second_instance:
                sys.exit(1)
        elif stale_claim:
            print(f"Note: {owner['path'].name} names pid {owner['pid']}, which exists but "
                  f"last polled this bus {owner['poll_age']:.0f}s ago - not a live owner "
                  "(a recycled pid, or a game that stopped polling). Launching.",
                  file=sys.stderr)

    devtools_dir = project_path / ".devtools"
    devtools_dir.mkdir(exist_ok=True)
    out_log = devtools_dir / "launch_stdout.log"
    err_log = devtools_dir / "launch_stderr.log"

    # Independent of the owner file: every process THIS project ever launched
    # that is still alive, start-time verified (gh#14.1). The owner check above
    # sees only the current owner; a game abandoned two launches ago that
    # stopped polling but never exited is invisible to it and still answers the
    # bus. Warn always; --kill-survivors clears them first.
    leftovers = _ledger_survivors(project_path)
    if leftovers:
        print("WARNING: %d process(es) this project launched earlier are still alive:\n%s"
              % (len(leftovers), _describe_survivors(leftovers)), file=sys.stderr)
        if args.kill_survivors:
            gone = _kill_survivors(leftovers)
            left = [r["pid"] for r in leftovers if r["pid"] not in gone]
            print("--kill-survivors: terminated %s%s" % (
                ", ".join(str(p) for p in gone) or "nothing",
                ("; STILL ALIVE: %s" % ", ".join(str(p) for p in left)) if left else ""),
                file=sys.stderr)
        else:
            print("Launching anyway. If verbs answer with another instance's state, that is "
                  "why. Clear them with `launch --kill-survivors` or:\n"
                  + _kill_hint([r["pid"] for r in leftovers]), file=sys.stderr)

    cmd = [str(godot_path), "--path", str(project_path)]
    if not args.no_mute:
        # Opt-out rather than unconditional: --write-movie records the audio bus
        # into a .wav, and a muted run captures silence (gather:G-092).
        cmd.append("--mute")

    env = os.environ.copy()
    session = ""
    bus_dir = passthrough_busdir
    user_args = list(devtools_passthrough)
    if args.isolated:
        session = uuid.uuid4().hex[:8]
        bus_dir = tempfile.mkdtemp(prefix="devtools_bus_")
        env["GODOT_DEVTOOLS_SESSION"] = session
        env["GODOT_DEVTOOLS_BUSDIR"] = bus_dir
        user_args = ["--devtools-session", session, "--devtools-busdir", bus_dir]
    elif _SESSION:
        # Covers both the top-level --session flag AND a bare --devtools-session
        # passthrough - the block above adopted the latter into _SESSION already,
        # specifically so this is the only place that has to build the launch
        # command from it.
        session = _SESSION
        env["GODOT_DEVTOOLS_SESSION"] = _SESSION
        if not user_args:
            user_args = ["--devtools-session", _SESSION]

    cmd += native_passthrough
    if user_args:
        # Godot's own `--` separator: everything after it reaches
        # OS.get_cmdline_user_args(), which is where the autoload reads from.
        cmd += ["--"] + user_args

    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        popen_kwargs["start_new_session"] = True

    # plant-tower-defense:G-047: a snapshot left behind by a game that died is put
    # back before anything else starts; then, if asked, take a fresh one.
    userstate_restore(project_path, "previous launch never quit cleanly")
    try:
        launch_user_dir = get_user_data_path(project_path)
        userstate_stat_take(project_path, launch_user_dir)
        _warn_shared_user_dir(launch_user_dir, project_path)
    except FileNotFoundError:
        pass  # no user:// yet (first ever launch); nothing to diff against
    # gh#40 / plant-tower-defense:G-054 (0.40.0): restore on quit is the DEFAULT.
    # Two projects on one day had a bridge session persist into the developer's
    # real save through a verb that behaved correctly (`capture()` rebinding a
    # key; `bank_score()`), and the damage surfaced twenty minutes later as five
    # unrelated-looking headless failures. A verification session that silently
    # mutates the developer's state is never what was wanted; one that puts it
    # back costs a file copy. `--no-snapshot-userstate` opts out for a launch
    # whose writes are the point (a playtest); `--snapshot-userstate GLOB...`
    # widens what is restored beyond *.save.
    snapshot_patterns = getattr(args, "snapshot_userstate", None)
    restore_on_quit = not getattr(args, "no_snapshot_userstate", False)
    try:
        snap_user_dir = get_user_data_path(project_path)
    except FileNotFoundError as exc:
        snap_user_dir = None
        if snapshot_patterns is not None:
            print(f"Error: --snapshot-userstate needs the user:// directory: {exc}",
                  file=sys.stderr)
            sys.exit(2)
    if snap_user_dir is not None:
        patterns = snapshot_patterns or list(USERSTATE_DEFAULT_GLOBS)
        manifest = userstate_snapshot(project_path, snap_user_dir, patterns, restore_on_quit)
        names = ", ".join(manifest["files"][:6]) + (", ..." if len(manifest["files"]) > 6 else "")
        if restore_on_quit:
            print(f"userstate: {len(manifest['files'])} file(s) matching {', '.join(patterns)} "
                  f"in {snap_user_dir}{f' ({names})' if names else ''} will be RESTORED on "
                  f"`quit` (files the run creates under those patterns are removed again; "
                  f"--no-snapshot-userstate keeps this run's writes)")
        elif manifest["files"]:
            # plant-tower-defense:G-050: the copy exists either way; say so at the
            # moment the decision can still be made, naming the files at risk.
            print(f"userstate: {len(manifest['files'])} file(s) in {snap_user_dir} ({names}) "
                  f"copied to .devtools/{USERSTATE_DIR}/ and NOT restored on quit "
                  f"(--no-snapshot-userstate) - this run shares the developer's real "
                  f"user://; `restore-userstate` puts them back after the fact")

    with out_log.open("w", encoding="utf-8") as out_f, \
            err_log.open("w", encoding="utf-8") as err_f:
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, env=env,
                                cwd=str(project_path), **popen_kwargs)

    # Record what was started, so a later bus failure can say whether THIS pid is
    # gone instead of "likely exited" (gather:G-114). A detached child's exit CODE
    # is not recoverable after the fact on Windows - liveness is, and liveness plus
    # the breadcrumb is what actually answers "did it crash, and inside what".
    launch_record = devtools_dir / "launch.json"
    launch_record.write_text(json.dumps({
        "pid": proc.pid,
        "cmd": cmd,
        "session": session,
        "bus_dir": bus_dir,
        "started_unix": time.time(),
    }, indent=2), encoding="utf-8")
    # The Popen pid is the LAUNCHER: with a `_console.exe` build it is a wrapper
    # that spawns the engine as a child, and the bus answers with the child's
    # pid. Both go in the ledger so `quit` can sweep both.
    _ledger_record(project_path, proc.pid, "launcher", {"cmd": cmd[0], "session": session})

    print(f"Launched pid {proc.pid}: {' '.join(cmd)}")
    print(f"  stdout: {out_log}")
    print(f"  stderr: {err_log}")
    if native_passthrough:
        print(f"  forwarded to Godot: {' '.join(native_passthrough)}")

    if args.no_wait:
        print("  --no-wait: the bus was NOT verified. "
              "python tools/devtools.py ping")
        return

    reply = _await_bus(project_path, session, bus_dir)
    if reply is None:
        print("\nERROR: launched, but the bus never answered a ping within 20s.",
              file=sys.stderr)
        error_lines = launch_log_errors(out_log, err_log)
        if error_lines:
            print("The game's own log names the problem:", file=sys.stderr)
            for line in error_lines[-10:]:
                print(f"  {line}", file=sys.stderr)
        tail = ""
        try:
            tail = err_log.read_text(encoding="utf-8").strip()[-800:]
        except OSError:
            pass
        if tail:
            print(f"--- {err_log.name} (tail) ---\n{tail}", file=sys.stderr)
        print("Not printing a follow-up command: it would not work.", file=sys.stderr)
        sys.exit(1)

    data = reply.get("data") or {}
    print(f"  bus answered: pid {data.get('pid')}")
    if isinstance(data.get("pid"), int) and data.get("pid") != proc.pid:
        _ledger_record(project_path, data["pid"], "engine",
                       {"launcher_pid": proc.pid, "session": session})
    if session:
        flags = f"--session {session}"
        if bus_dir:
            flags += f" --userdata {bus_dir}"
        print(f"  Subsequent calls: python tools/devtools.py {flags} <verb>")
    else:
        print("  Subsequent calls: python tools/devtools.py <verb>")
    if bus_dir:
        print(f"  bus dir:  {data.get('bus_dir', bus_dir)}   (isolated)")
        print(f"  user://:  {data.get('user_dir', '?')}   (SHARED - a pass that "
              "exercises a persisted setting writes your real save, and an autoload "
              "that reads it at startup carries that into your next headless test run; "
              "`quit` names what changed, --snapshot-userstate puts it back)")


def rect_arg(value: str):
    """Parse an "X,Y,W,H" cell-rect flag into [x, y, w, h] ints."""
    text = str(value).strip().strip("()[]").strip()
    parts = [p for p in re.split(r"[,\s]+", text) if p]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"expected four comma-separated numbers 'X,Y,W,H' (e.g. 0,0,16,16), got {value!r}")
    try:
        return [int(float(p)) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"all four values must be numbers (e.g. 0,0,16,16), got {value!r}")


def cmd_tilemap_cells(args, project_path: Path):
    """Dump a tilemap's used cells as data (bus verb: tilemap_cells, G-032)."""
    args.node = normalize_node_path(args.node)  # G-025
    cmd_args = {"node_path": args.node}
    if args.layer is not None:
        cmd_args["layer"] = args.layer
    if args.rect is not None:
        cmd_args["rect"] = args.rect

    result = send_command(project_path, "tilemap_cells", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", {}) or {}
    cells = data.get("cells", []) or []
    print(f"{data.get('count', len(cells))} used cell(s) on {data.get('node_path', args.node)} "
          f"(layer {data.get('layer', 0)})")
    preview_cap = 50
    for cell in cells[:preview_cap]:
        atlas = cell.get("atlas", {}) or {}
        print(f"  ({cell.get('x')}, {cell.get('y')}) source={cell.get('source_id')} "
              f"atlas=({atlas.get('x')}, {atlas.get('y')})")
    if len(cells) > preview_cap:
        print(f"  ... {len(cells) - preview_cap} more (use --json for the full list)")
    if data.get("truncated"):
        print("  WARNING: reply truncated by the game - pass --rect to narrow the window.")


def cmd_tilemap_region(args, project_path: Path):
    """Connected components of matching cells (bus verb: tilemap_region, G-065)."""
    args.node = normalize_node_path(args.node)  # G-025
    cmd_args = {"node_path": args.node, "atlas": [int(args.atlas[0]), int(args.atlas[1])]}
    if args.layer is not None:
        cmd_args["layer"] = args.layer
    if args.source_id is not None:
        cmd_args["source_id"] = args.source_id

    result = send_command(project_path, "tilemap_region", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", {}) or {}
    components = data.get("components", []) or []
    print(f"{data.get('total', 0)} matching cell(s) in {len(components)} component(s) "
          f"on {data.get('node_path', args.node)}:")
    for i, comp in enumerate(components):
        bounds = comp.get("bounds", {}) or {}
        print(f"  #{i + 1}: {comp.get('cells')} cells, bounds "
              f"x={bounds.get('x')} y={bounds.get('y')} w={bounds.get('w')} h={bounds.get('h')}")


def cmd_canvas_scale(args, project_path: Path):
    """Accumulated canvas scale + effective texture filter (bus verb: canvas_scale, G-073/G-075)."""
    args.node = normalize_node_path(args.node)  # G-025
    result = send_command(project_path, "canvas_scale", {"node_path": args.node})
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data", {}) or {}
    scale = data.get("accumulated_scale", {}) or {}
    print(result.get("message", ""))
    print(f"  accumulated scale: ({scale.get('x')}, {scale.get('y')})  "
          f"filter: {data.get('effective_filter')} (from {data.get('filter_source')})")
    if "canvas_layer" not in data:
        print("  canvas layer: the reply carried no 'canvas_layer' key "
              f"(keys: {sorted(data)})", file=sys.stderr)
    else:
        where = data.get("canvas_layer_path") or "root canvas (no CanvasLayer ancestor)"
        print(f"  canvas layer: {data['canvas_layer']}  via {where}")
    for entry in data.get("chain", []) or []:
        s = entry.get("scale")
        s_txt = f" scale=({s.get('x')}, {s.get('y')})" if isinstance(s, dict) else ""
        print(f"    {entry.get('name')} [{entry.get('class')}]{s_txt}")


def cmd_set_resolution(args, project_path: Path):
    """Resize the game window and read back the applied size (bus verb: set_resolution, G-017)."""
    width, height = int(args.size[0]), int(args.size[1])
    result = send_command(project_path, "set_resolution",
                          {"width": width, "height": height})
    print(result.get("message", ""))
    if not result["success"]:
        sys.exit(1)
    data = result.get("data", {}) or {}
    rect = data.get("visible_rect", {}) or {}
    print(f"  visible rect: {rect.get('w')}x{rect.get('h')} at ({rect.get('x')}, {rect.get('y')})")


def cmd_scripts_seen(args, project_path: Path):
    """Every distinct script path that has entered the tree since launch
    (bus verb: scripts_seen, G-074b/G-068). With --json the FULL reply envelope
    is printed - tools/verify_ledger.py consumes reply["data"]["scripts"] from
    a redirect of exactly that output."""
    global _RAW_JSON
    if getattr(args, "json", False):
        _RAW_JSON = True

    result = send_command(project_path, "scripts_seen")
    # Only reached without --json (raw mode prints and raises in send_command).
    data = result.get("data", {}) or {}
    scripts = data.get("scripts", []) or []
    print(f"Scripts seen since launch ({len(scripts)}):")
    for script in scripts:
        print(f"  {script}")


_GLUE_FLAGS = ("--value", "--args", "-a")


def _glue_leading_dash_values(argv):
    """Rewrite `--value -200,-296` into `--value=-200,-296` before argparse sees it.

    argparse only exempts a token from option parsing when it matches its
    negative-NUMBER pattern, so a negative Vector2 tuple is read as an unknown
    flag and the command dies with `expected one argument` - which says nothing
    about the real cause (gather:G-137). Only the value-carrying flags are glued,
    and only when the next token already starts with '-', so nothing else moves.
    """
    out = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLUE_FLAGS and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            out.append("%s=%s" % (tok, argv[i + 1]))
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


# Godot's ResourceUID text encoding, reimplemented. Verified against
# ResourceUID.id_to_text() on 4.6.1 for 1, 33, 34, 35, 1234567890 and two 63-bit
# ids: base 34, digits 'a'..'z' (0..24) then '0'..'9' (25..33), most significant
# first, no padding.
_UID_CHAR_COUNT = ord("z") - ord("a")      # 25
_UID_BASE = _UID_CHAR_COUNT + (ord("9") - ord("0"))   # 34


def uid_to_text(uid_id: int) -> str:
    """Godot's ResourceUID::id_to_text."""
    if uid_id < 0:
        return "uid://<invalid>"
    out = []
    while uid_id:
        c = uid_id % _UID_BASE
        if c < _UID_CHAR_COUNT:
            out.append(chr(ord("a") + c))
        else:
            out.append(chr(ord("0") + (c - _UID_CHAR_COUNT)))
        uid_id //= _UID_BASE
    return "uid://" + "".join(reversed(out))


def cmd_new_uid(args, project_path: Path):
    """Emit a fresh, correctly-encoded, collision-checked uid:// string.

    A subagent that is not allowed to run Godot also cannot generate a `.uid`,
    so it hand-writes one and nobody can validate it until the orchestrator
    imports - and lint's `UIDs: OK` is only reachable from a tree that already
    compiles, which a fan-out does not have until every agent has landed
    (gather:G-094). This is a pure function over the same encoding plus a scan of
    the existing sidecars: no game, no editor, no import.
    """
    existing = set()
    for sidecar in project_path.rglob("*.uid"):
        try:
            existing.add(sidecar.read_text(encoding="utf-8").strip())
        except OSError:
            continue

    made = []
    for _ in range(max(1, args.count)):
        for _attempt in range(1000):
            # 63-bit, matching create_id()'s 0x7FFF... mask.
            text = uid_to_text(uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF)
            if text not in existing:
                existing.add(text)
                made.append(text)
                break
        else:
            print("Error: could not find an unused uid in 1000 attempts (that "
                  "should be impossible - is the project full of duplicates?)",
                  file=sys.stderr)
            sys.exit(1)

    if args.write:
        target = Path(args.write)
        if not target.is_absolute():
            target = project_path / target
        if target.suffix != ".uid":
            target = target.with_suffix(target.suffix + ".uid")
        if target.exists():
            print(f"Error: {target} already exists; refusing to overwrite a uid "
                  "(changing one breaks every reference to it).", file=sys.stderr)
            sys.exit(1)
        target.write_text(made[0], encoding="utf-8")
        print(f"{made[0]}  -> {target}")
        return

    for text in made:
        print(text)
    print(f"({len(existing) - len(made)} existing .uid sidecar(s) scanned for "
          "collisions)", file=sys.stderr)


def cmd_reachable_ui(args, project_path: Path):
    """What a finger or cursor could actually hit right now (bus verb:
    reachable_ui, gather:G-129). Data keys read: controls, count, reachable,
    scroll_reachable, viewport, geometry_trustworthy, geometry_caveat.

    Diff this between `set-feature --touchscreen true` and `false` to catch an
    affordance that exists on one device and not the other.
    """
    result = send_command(project_path, "reachable_ui", {})
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data") or {}
    if "controls" not in data:
        print(f"reachable-ui: the reply carried no 'controls' key. Keys: {sorted(data)}",
              file=sys.stderr)
        sys.exit(1)
    print(result.get("message", ""))
    for c in data["controls"]:
        r = c.get("rect") or {}
        why = ""
        if not c.get("on_screen") and c.get("scroll_reachable"):
            # gh#16: past the fold of an on-screen ScrollContainer - reachable
            # by scrolling, and `findings` does not count it against the run.
            why = f"  SCROLL TO REACH (inside {c.get('scroll_container')})"
        elif not c.get("on_screen"):
            why = "  OFF-SCREEN"
            if c.get("scroll_container"):
                why += f"  (inside {c.get('scroll_container')}, outside its content extent)"
        elif c.get("blocked_by"):
            why = f"  BLOCKED BY {c['blocked_by']}"
        label = f' "{_printable(c["text"])}"' if c.get("text") else ""
        print(f"  {c.get('path')}{label}  [{c.get('kind')}] "
              f"{r.get('x'):.0f},{r.get('y'):.0f} {r.get('w'):.0f}x{r.get('h'):.0f}{why}")
    print_geometry_caveat(data, "reachable-ui", only_if_suspect=True)


def cmd_first_frame(args, project_path: Path):
    """What a player would see right now, in one call (bus verb: first_frame,
    H-059). Data keys read: tree_paused, cursor_mode, visible_canvas_layers,
    topmost_control, viewport, geometry_trustworthy, geometry_caveat.

    Answers a narrower question than findings/reachable-ui do: not "is anything
    wrong" or "can a finger hit this ONE control", but "what IS on screen" -
    which CanvasLayers are visible and in what paint order, which single Control
    everything else is drawn under, whether the tree is paused, what the cursor
    is doing. The thing a human reads off a screenshot in half a second.
    """
    result = send_command(project_path, "first_frame", {})
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data") or {}
    if "visible_canvas_layers" not in data:
        print(f"first-frame: the reply carried no 'visible_canvas_layers' key. "
              f"Keys: {sorted(data)}", file=sys.stderr)
        sys.exit(1)
    print(result.get("message", ""))
    if data.get("tree_paused"):
        print("  TREE IS PAUSED")
    print(f"  cursor: {data.get('cursor_mode', 'UNKNOWN')}")
    print("  CanvasLayers (paint order, back to front):")
    for cl in data["visible_canvas_layers"]:
        print(f"    layer {cl.get('layer')}  {cl.get('path')}")
    top = data.get("topmost_control") or {}
    if top:
        r = top.get("rect") or {}
        label = f' "{_printable(top["text"])}"' if top.get("text") else ""
        print(f"  topmost: {top.get('path')}{label}  [{top.get('type')}] "
              f"{r.get('x'):.0f},{r.get('y'):.0f} {r.get('w'):.0f}x{r.get('h'):.0f}")
    else:
        print("  topmost: (no on-screen Control found)")
    print_geometry_caveat(data, "first-frame", only_if_suspect=True)


def cmd_project_settings(args, project_path: Path):
    """ProjectSettings as the running game sees them (bus verb: project_settings,
    dave-game:G-003). Data keys read: settings, count, missing, filter."""
    cmd_args = {}
    if args.names:
        cmd_args["names"] = args.names
    elif args.filter:
        cmd_args["filter"] = args.filter
    result = send_command(project_path, "project_settings", cmd_args)
    data = result.get("data") or {}
    if "settings" not in data:
        print(f"project-settings: the reply carried no 'settings' key. Keys: {sorted(data)}",
              file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(result.get("message", ""))
        for key in sorted(data["settings"]):
            print(f"  {key} = {_format_value(data['settings'][key])}")
        for key in data.get("missing") or []:
            print(f"  {key} = <no such setting>")
    if not result["success"]:
        sys.exit(1)


def cmd_find_nodes(args, project_path: Path):
    """Find nodes by class/group/method and property predicates (bus verb:
    find_nodes, gather:G-109). Data keys read: nodes, count, truncated."""
    cmd_args = {"limit": args.limit}
    if args.node_class:
        cmd_args["class"] = args.node_class
    if args.group:
        cmd_args["group"] = args.group
    if args.method:
        cmd_args["method"] = args.method
    if args.root:
        cmd_args["root"] = normalize_node_path(args.root)
    if args.properties:
        cmd_args["properties"] = args.properties
    if args.calls:
        cmd_args["calls"] = args.calls
    where = {}
    for pair in args.where or []:
        if "=" not in pair:
            print(f"Error: --where expects NAME=VALUE, got {pair!r}", file=sys.stderr)
            sys.exit(2)
        name, _, raw = pair.partition("=")
        where[name] = parse_value_arg(raw)
    if where:
        cmd_args["where"] = where

    result = send_command(project_path, "find_nodes", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data") or {}
    if "nodes" not in data:
        print(f"find-nodes: the reply carried no 'nodes' key. Keys: {sorted(data)}",
              file=sys.stderr)
        sys.exit(1)
    nodes = data["nodes"]
    print(f"{data.get('count', len(nodes))} node(s) matched:")
    for node in nodes:
        extra = node.get("properties") or {}
        # H-046: a property the resolver could not read used to print `=null`,
        # identical to a property that genuinely holds null. The game side now
        # sends the reason beside it; print that instead of the null.
        errors = node.get("property_errors") or {}
        parts = []
        for k, v in extra.items():
            if k in errors:
                parts.append(f"{k}=<unresolved: {errors[k]}>")
            else:
                parts.append(f"{k}={_format_value(v)}")
        for k, v in (node.get("calls") or {}).items():
            parts.append(f"{k}()={_format_value(v)}")
        for k, why in (node.get("call_errors") or {}).items():
            parts.append(f"{k}()=<error: {why}>")
        suffix = ("  " + "  ".join(parts)) if parts else ""
        print(f"  {node.get('path')}  [{node.get('type')}]{suffix}")
    if data.get("truncated"):
        print(f"  ... truncated at --limit {args.limit}")
    if not nodes:
        # An empty match is a legitimate answer, but exiting 0 on it makes a typo'd
        # predicate indistinguishable from a real absence in a shell pipeline.
        #
        # The game side appends the denominator to `message` when a --where matched
        # nothing ("0 of 89 matched on mouse_filter", or "no candidate exposes
        # 'mouse_filter '" when the name never resolved). Printing it is the whole
        # fix for moving-in:G-011 -- a silent empty result is what let a UI be
        # cleared of exactly the fault it had.
        detail = result.get("message") or ""
        if " -- " in detail:
            print(f"  {detail.split(' -- ', 1)[1]}", file=sys.stderr)
        sys.exit(1)


def cmd_press(args, project_path: Path):
    """Emit `pressed` on the nearest BaseButton (bus verb: press, gather:G-119)."""
    cmd_args = {"node_path": normalize_node_path(args.node)}
    if args.toggle is not None:
        cmd_args["toggle"] = args.toggle
    result = send_command(project_path, "press", cmd_args)
    print(result.get("message", ""))
    if not result["success"]:
        sys.exit(1)


def cmd_raycast(args, project_path: Path):
    """Cast a ray and report what it hit (bus verb: raycast, gather:G-136).

    Data keys read: clear, collider, collider_class, position, mask, mask_names.
    """
    if len(args.origin) != len(args.to):
        print("Error: --from and --to must both be X,Y (2D) or both X,Y,Z (3D)", file=sys.stderr)
        sys.exit(1)
    cmd_args = {
        "from": [float(v) for v in args.origin],
        "to": [float(v) for v in args.to],
        "areas": bool(args.areas),
    }
    if args.mask is not None:
        cmd_args["mask"] = args.mask
    if args.exclude:
        cmd_args["exclude"] = [normalize_node_path(p) for p in args.exclude]

    result = send_command(project_path, "raycast", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data") or {}
    if "clear" not in data:
        print(f"raycast: the reply carried no 'clear' key. Keys: {sorted(data)}",
              file=sys.stderr)
        sys.exit(1)
    print(result.get("message", ""))
    space = data.get("space")
    if space is None and len(args.origin) == 3:
        print("raycast: the installed game predates 3D raycasts (no 'space' key) - this "
              "answer came from the 2D space and is meaningless for a 3D query", file=sys.stderr)
        sys.exit(1)
    print(f"  space {space or '2d'}; mask {data.get('mask')} = {', '.join(data.get('mask_names') or [])}")
    if not data["clear"]:
        pos = data.get("position") or {}
        print(f"  collider: {data.get('collider')} [{data.get('collider_class')}]")
        if "z" in pos:
            print(f"  hit at:   ({pos.get('x')}, {pos.get('y')}, {pos.get('z')})")
        else:
            print(f"  hit at:   ({pos.get('x')}, {pos.get('y')})")


def cmd_sample_pixels(args, project_path: Path):
    """Mean/dominant colour over a screen rect (bus verb: sample_pixels, G-121)."""
    cmd_args = {}
    if args.rect is not None:
        cmd_args["rect"] = args.rect
    result = send_command(project_path, "sample_pixels", cmd_args)
    if not result["success"]:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)
    data = result.get("data") or {}
    print(result.get("message", ""))
    for key in ("mean", "dominant", "brightest", "darkest"):
        c = data.get(key)
        if isinstance(c, dict):
            print(f"  {key:<10} r={c.get('r'):.3f} g={c.get('g'):.3f} b={c.get('b'):.3f}")



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
    parser = argparse.ArgumentParser(description="DevTools CLI - interact with running Godot instance")
    parser.add_argument("--project", "-p", help="Path to Godot project", default=".")
    parser.add_argument("--userdata", "-u", help="Override user:// data directory (highest priority)")
    parser.add_argument(
        "--no-precheck",
        action="store_true",
        help=f"Skip the {PRECHECK_SECONDS:g}s 'is the game running' precheck and wait the full timeout",
    )
    parser.add_argument(
        "--session", "-S", default=os.environ.get("GODOT_DEVTOOLS_SESSION", ""),
        help="Bus id, so several game instances can share one user:// dir. Must match the "
             "game's `-- --devtools-session <id>`. Default: GODOT_DEVTOOLS_SESSION, else "
             "the shared bus (previous behavior).",
    )
    parser.add_argument(
        "--json", dest="raw_json", action="store_true",
        help="Print every bus reply as the raw JSON envelope instead of the formatted "
             "view (G-039). Global: goes BEFORE the subcommand, e.g. `--json ping`.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ping
    p = subparsers.add_parser("ping", help="Check if DevTools is running")
    p.set_defaults(func=cmd_ping)

    # screenshot
    p = subparsers.add_parser("screenshot", help="Take a screenshot")
    p.add_argument("--filename", "-f", help="Output filename")
    p.add_argument("--region", type=rect_arg, metavar="X,Y,W,H",
                   help="Crop to this pixel rect (game-side, so the crop is "
                        "reproducible from the command line)")
    p.add_argument("--hide", action="append", metavar="NODE",
                   help="Hide this node for the capture and restore it after "
                        "(repeatable)")
    p.add_argument("--hide-group", action="append", metavar="GROUP",
                   help="Hide every CanvasItem in this group for the capture "
                        "(repeatable)")
    p.set_defaults(func=cmd_screenshot)

    # validate
    p = subparsers.add_parser("validate", help="Validate a scene")
    p.add_argument("--scene", "-s", help="Scene path (res://...)")
    p.set_defaults(func=cmd_validate)

    # validate-all
    p = subparsers.add_parser("validate-all", help="Validate all scenes")
    p.set_defaults(func=cmd_validate_all)

    # scene-tree
    p = subparsers.add_parser("scene-tree", help="Get scene tree")
    p.add_argument("--depth", "-d", type=int, default=10, help="Max depth")
    p.add_argument("--root", help="Start from this node instead of the current "
                                  "scene (a deep UI subtree otherwise truncates)")
    p.add_argument("--property", dest="properties", action="append", metavar="NAME",
                   help="Report this property on every node (repeatable). "
                        "find-nodes is usually the better verb for identifying one.")
    p.set_defaults(func=cmd_scene_tree)

    # curve
    p = subparsers.add_parser(
        "curve", help="Call a pure method over an integer range and print the series")
    p.add_argument("--node", "-n", required=True, help="Node path")
    p.add_argument("--method", "-m", required=True, help="Method name")
    p.add_argument("--from", dest="start", type=int, required=True, metavar="N")
    p.add_argument("--to", dest="end", type=int, required=True, metavar="N")
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--args", help="JSON array of extra arguments")
    p.add_argument("--arg-index", type=int, default=0,
                   help="Which parameter the swept value fills (default 0)")
    p.set_defaults(func=cmd_curve)

    # performance
    p = subparsers.add_parser("performance", help="Get performance metrics (FPS is measured "
                              "over a window of frames, not read once)")
    p.add_argument("--reset-baseline", action="store_true",
                   help="Re-baseline the orphan AND node counts at the current values")
    p.add_argument("--frames", type=int, default=None, metavar="N",
                   help="Measure FPS over N frames by wall clock (game default 30; 0 = one "
                        "instantaneous read). Reports mean/min/max and flags a window "
                        "whose halves disagree by >15%% as still settling.")
    p.add_argument("--by-type", action="store_true", dest="by_type",
                   help="Also report which node classes grew or shrank since the baseline "
                        "(in-tree accumulation the orphan count cannot see)")
    p.set_defaults(func=cmd_performance)

    # get-state
    p = subparsers.add_parser("get-state", help="Get node state")
    p.add_argument("--node", "-n", help="Node path")
    p.add_argument("--property", dest="properties", action="append", metavar="NAME",
                   help="Only report this property (repeatable). Exits 1 if a name is "
                        "unknown. A dotted path walks into Resources and Dictionaries: "
                        "--property texture.region, --property slot_data.item.name.")
    p.set_defaults(func=cmd_get_state)

    # set-state
    p = subparsers.add_parser("set-state", help="Set node property")
    p.add_argument("--node", "-n", required=True, help="Node path")
    p.add_argument("--property", required=True, help="Property name")
    p.add_argument("--value", required=True,
                   help="Property value. JSON, a bare number, or a numeric tuple: "
                        "[-200,-296] | -200,-296 | (-200,-296) all reach the game as "
                        "a Vector2. Write --value=-200,-296 with an '=' when it starts "
                        "with '-', or argparse reads it as a flag.")
    p.set_defaults(func=cmd_set_state)

    # run-method
    p = subparsers.add_parser("run-method", help="Call a method")
    p.add_argument("--node", "-n", required=True, help="Node path")
    p.add_argument("--method", "-m", required=True, help="Method name")
    p.add_argument("--args", "-a", help="Method arguments as JSON array")
    p.add_argument("--json", action="store_true",
                   help="Print the full reply envelope as JSON (pipeable, like `cmd`)")
    p.set_defaults(func=cmd_run_method)

    # logs
    p = subparsers.add_parser("logs", help="View logs")
    p.add_argument("--tail", "-t", type=int, help="Show last N entries")
    p.add_argument("--category", "-c", help="Filter by category")
    p.set_defaults(func=cmd_logs)

    # quit
    p = subparsers.add_parser("quit", help="Quit Godot, and confirm the process is gone")
    p.add_argument("--exit-code", type=int, help="Exit code")
    p.add_argument("--wait", type=float, default=10.0, metavar="SECONDS",
                   help="How long to wait for the process to exit before reporting "
                        "a survivor (default 10; exits 1 if it is still alive)")
    p.add_argument("--kill", action="store_true",
                   help="Terminate any survivor: the owner pid if it outlives --wait, "
                        "plus every process this project launched earlier that is still "
                        "alive (start-time verified, from .devtools/launched.jsonl). "
                        "Never by image name - other sessions run games on this machine.")
    p.set_defaults(func=cmd_quit)

    # cmd - arbitrary registered verb
    p = subparsers.add_parser("cmd", help="Send an arbitrary registered verb")
    p.add_argument("action", help="Action name (any registered verb)")
    p.add_argument("--args", "-a", help="Args as a JSON object (default: {})")
    p.add_argument("--timeout", type=float, default=30.0, help="Response timeout in seconds")
    p.set_defaults(func=cmd_cmd)

    # list-commands - discover registered verbs
    p = subparsers.add_parser("list-commands", help="List all registered verbs")
    p.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
    p.add_argument("--offline", action="store_true",
                   help="No running game: statically parse register_command() names "
                        "from the installed scripts (labeled generic/project)")
    p.set_defaults(func=cmd_list_commands)

    # restore-userstate
    p = subparsers.add_parser(
        "restore-userstate",
        help="Put back the user:// files copied at the last launch (a run that wrote the "
             "developer's real save is recoverable after the fact)")
    p.set_defaults(func=cmd_restore_userstate)

    # launch - start the game detached
    p = subparsers.add_parser(
        "launch", help="Launch the game detached (logs under .devtools/)",
        epilog="Everything after a bare -- goes to the Godot command line: "
               "devtools.py launch --no-mute -- --write-movie out/frame.png --fixed-fps 30")
    p.add_argument("--godot", help="Godot binary (else $GODOT_BIN, else config godot_bin)")
    p.add_argument("--isolated", action="store_true",
                   help="Private session id AND a private bus directory, verified before "
                        "the follow-up command is printed. user:// itself is still shared - "
                        "Godot has no switch for it.")
    p.add_argument("--snapshot-userstate", dest="snapshot_userstate", nargs="*",
                   metavar="GLOB", default=None,
                   help="Which user:// files `quit` restores (default: *.save *.sav *.cfg *.dat "
                        "*.json *.tres *.res *.bin, never the bridge's own files). Restore on "
                        "quit is the DEFAULT since 0.40.0 (gh#40) - a copy is taken on every "
                        "launch and `quit` puts it back, removing files the run created "
                        "under the same patterns; this flag only widens the set. "
                        "--isolated does not isolate user://")
    p.add_argument("--no-snapshot-userstate", dest="no_snapshot_userstate", action="store_true",
                   help="Keep this run's user:// writes (a playtest whose save is the point). "
                        "The copy is still taken; `restore-userstate` can revert after the fact")
    p.add_argument("--no-mute", action="store_true",
                   help="Do not pass --mute (a --write-movie run records the audio bus, "
                        "and a muted run captures silence)")
    p.add_argument("--no-wait", action="store_true",
                   help="Return as soon as the process is spawned, without proving the "
                        "bus answers")
    p.add_argument("--allow-second-instance", action="store_true",
                   help="Launch even when a live process already owns this bus "
                        "(two instances answering one bus is silent corruption)")
    p.add_argument("--kill-survivors", action="store_true",
                   help="Before launching, terminate every process this project launched "
                        "earlier that is still alive (start-time verified). Without it "
                        "they are listed and the launch proceeds.")
    p.add_argument("godot_args", nargs=argparse.REMAINDER, metavar="-- GODOT ARGS",
                   help="Passed straight to Godot after a bare --")
    p.set_defaults(func=cmd_launch)

    # harness-version - which harness revision is installed
    p = subparsers.add_parser("harness-version",
                              help="Report the installed harness version (game + client)")
    p.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
    p.add_argument("--client", action="store_true",
                   help="Never open the bus: report this client's and the installed addon's "
                        "versions from disk (what a log entry's `harness:` field wants)")
    p.set_defaults(func=cmd_harness_version)

    # input - nested subcommands
    input_parser = subparsers.add_parser("input", help="Simulate input actions")
    input_sub = input_parser.add_subparsers(dest="input_command", required=True)

    # input press
    p = input_sub.add_parser("press", help="Press and hold an action")
    p.add_argument("action", help="Action name (e.g., move_left)")
    p.add_argument("--strength", type=float, help="Pressure strength 0.0-1.0 (default: 1.0)")
    p.set_defaults(func=cmd_input_press)

    # input release
    p = input_sub.add_parser("release", help="Release a held action")
    p.add_argument("action", help="Action name to release")
    p.set_defaults(func=cmd_input_release)

    # input tap
    p = input_sub.add_parser("tap", help="Press and release an action")
    p.add_argument("action", help="Action name to tap")
    p.add_argument("--hold", type=float, default=0, help="Hold duration in seconds before release")
    p.add_argument("--strength", type=float, help="Pressure strength 0.0-1.0 (default: 1.0)")
    p.set_defaults(func=cmd_input_tap)

    # input clear
    p = input_sub.add_parser("clear", help="Release all simulated inputs")
    p.set_defaults(func=cmd_input_clear)

    # input list
    p = input_sub.add_parser("list", help="List available input actions")
    p.add_argument("--all", "-a", action="store_true", help="Include built-in ui_* actions")
    p.set_defaults(func=cmd_input_list)

    # input sequence
    p = input_sub.add_parser("sequence", help="Execute input sequence from JSON file")
    p.add_argument("file", help="Path to sequence JSON file")
    p.add_argument("--timeout", type=float, default=60, help="Sequence timeout in seconds (default: 60)")
    p.set_defaults(func=cmd_input_sequence)

    # input state
    p = input_sub.add_parser("state", help="Read pressed/strength for actions "
                                           "(all project actions when none named)")
    p.add_argument("actions", nargs="*", help="Action names (default: every non-ui_ action)")
    p.set_defaults(func=cmd_input_state)

    # key - raw keyboard event (top-level; it is not an action)
    p = subparsers.add_parser("key", help="Tap a raw keyboard key by OS keycode name "
                                          "(e.g. E, LEFT, SPACE)")
    p.add_argument("key", help="Key name per OS.find_keycode_from_string, e.g. E, LEFT, Escape")
    p.add_argument("--count", type=int, help="Number of taps (default: 1)")
    p.add_argument("--hold-frames", type=int, dest="hold_frames",
                   help="Frames to hold before release (default: release on the next frame)")
    p.set_defaults(func=cmd_key)

    # mouse-move
    p = subparsers.add_parser("mouse-move", help="Dispatch a real InputEventMouseMotion with a "
                                                 "chosen relative delta (mouse-look)")
    p.add_argument("--relative", required=True, type=coord_pair, metavar="DX,DY",
                   help="Motion delta in pixels, e.g. 40,0 to look right")
    p.add_argument("--steps", type=int, help="Split the delta into N events, one per frame (default 1)")
    p.add_argument("--position", type=coord_pair, metavar="X,Y",
                   help="Absolute cursor position to report (default: current)")
    p.add_argument("--buttons", type=int, help="Button mask held during the motion (default 0)")
    p.set_defaults(func=cmd_mouse_move)

    # reload
    p = subparsers.add_parser("reload", help="Re-read an edited resource (shader, .tres, texture) "
                                             "into the running game without a relaunch")
    p.add_argument("path", help="res:// path of the resource to reload")
    p.set_defaults(func=cmd_reload)

    # ==================== TOUCH SIMULATION ====================

    # touch - nested subcommands (InputEventScreenTouch / InputEventScreenDrag)
    touch_parser = subparsers.add_parser("touch", help="Simulate touch / multi-touch events")
    touch_sub = touch_parser.add_subparsers(dest="touch_command", required=True)

    # touch press
    p = touch_sub.add_parser("press", help="Press a touch point")
    p.add_argument("--index", "-i", type=int, default=0, help="Touch index (default: 0)")
    p.add_argument("--pos", type=coord_pair, required=True, metavar="X,Y", help="Screen position, e.g. 640,360")
    p.set_defaults(func=cmd_touch_press)

    # touch release
    p = touch_sub.add_parser("release", help="Release a touch point")
    p.add_argument("--index", "-i", type=int, default=0, help="Touch index (default: 0)")
    p.add_argument("--pos", type=coord_pair, metavar="X,Y", help="Release position (default: where it is)")
    p.set_defaults(func=cmd_touch_release)

    # touch drag
    p = touch_sub.add_parser("drag", help="Drag a held touch point")
    p.add_argument("--index", "-i", type=int, default=0, help="Touch index (default: 0)")
    p.add_argument("--pos", type=coord_pair, metavar="X,Y", help="Start position (default: where it is)")
    p.add_argument("--to", type=coord_pair, required=True, metavar="X,Y", help="End position, e.g. 640,360")
    p.add_argument("--steps", type=int, help="Number of intermediate drag events")
    p.set_defaults(func=cmd_touch_drag)

    # touch clear
    p = touch_sub.add_parser("clear", help="Release all simulated touches")
    p.set_defaults(func=cmd_touch_clear)

    # touch list
    p = touch_sub.add_parser("list", help="List active simulated touches")
    p.set_defaults(func=cmd_touch_list)

    # ==================== ENGINE FEATURE OVERRIDES ====================

    # set-feature
    p = subparsers.add_parser("set-feature", help="Override an engine feature probe")
    p.add_argument("--touchscreen", type=bool_arg, metavar="true|false",
                   help="Fake DisplayServer.is_touchscreen_available()")
    p.add_argument("--query", action="store_true",
                   help="Read the current flag values without writing anything")
    p.set_defaults(func=cmd_set_feature)

    # ==================== NODE / TIME CONTROL ====================

    # clear-nodes
    p = subparsers.add_parser("clear-nodes", help="Free scene nodes matching a selector")
    p.add_argument("--group", help="Free nodes in this group")
    p.add_argument("--method", help="Free nodes that have this method")
    p.add_argument("--class", dest="class_name", help="Free nodes of this class")
    p.add_argument("--via-method", metavar="NAME",
                   help="Call this method on each match instead of queue_free(). "
                        "queue_free() skips the game's own removal path, so a "
                        "cleared enemy drops nothing and pays no xp.")
    p.add_argument("--via-args", metavar="JSON",
                   help="JSON array of arguments for --via-method")
    p.set_defaults(func=cmd_clear_nodes)

    # new-uid (offline: no game, no editor, no import)
    p = subparsers.add_parser(
        "new-uid", help="Emit a fresh uid:// string, collision-checked against "
                        "the project's existing .uid sidecars")
    p.add_argument("--count", type=int, default=1, help="How many to emit")
    p.add_argument("--write", metavar="PATH",
                   help="Write it to PATH (a .uid suffix is added if missing). "
                        "Refuses to overwrite an existing sidecar.")
    p.set_defaults(func=cmd_new_uid)

    # find-nodes
    p = subparsers.add_parser(
        "find-nodes", help="Find nodes by class/group/method and property predicates")
    p.add_argument("--class", dest="node_class", help="Nodes of this class")
    p.add_argument("--group", help="Nodes in this group")
    p.add_argument("--method", help="Nodes that have this method")
    p.add_argument("--where", action="append", metavar="NAME=VALUE",
                   help="Property must equal this (repeatable; dotted paths allowed, "
                        "e.g. --where type=Elite --where slot_data.item.name='Iron Bar')")
    p.add_argument("--property", dest="properties", action="append", metavar="NAME",
                   help="Also report this property for each hit (repeatable)")
    p.add_argument("--call", dest="calls", action="append", metavar="METHOD",
                   help="Call this zero-argument method on each hit and print the result "
                        "beside the path (repeatable) -- reads a getter without a second "
                        "round-trip against an auto-generated node name that changes "
                        "every launch")
    p.add_argument("--root", help="Search only this subtree (default: the whole tree)")
    p.add_argument("--limit", type=int, default=200, help="Max hits to return")
    p.set_defaults(func=cmd_find_nodes)

    # press
    p = subparsers.add_parser("press", help="Emit `pressed` on the nearest BaseButton")
    p.add_argument("--node", "-n", required=True, help="Button path (or its parent)")
    p.add_argument("--toggle", type=lambda v: v.lower() in ("1", "true", "yes"),
                   default=None, metavar="BOOL",
                   help="For a toggle_mode button, the state to set before emitting")
    p.set_defaults(func=cmd_press)

    # raycast
    p = subparsers.add_parser(
        "raycast", help="What would a ray on this collision mask hit?")
    p.add_argument("--from", dest="origin", required=True, type=coord_2_or_3, metavar="X,Y[,Z]",
                   help="Two components query the 2D space, three the 3D space")
    p.add_argument("--to", required=True, type=coord_2_or_3, metavar="X,Y[,Z]")
    p.add_argument("--mask", type=int, help="Collision mask (default: every layer)")
    p.add_argument("--areas", action="store_true", help="Also hit Area2D/Area3D")
    p.add_argument("--exclude", action="append", metavar="NODE",
                   help="Exclude this CollisionObject2D/3D (repeatable)")
    p.set_defaults(func=cmd_raycast)

    # reachable-ui
    p = subparsers.add_parser(
        "reachable-ui",
        help="Every Control a finger or cursor could actually hit this frame "
             "(off-screen and input-blocked ones are named, not omitted)")
    p.set_defaults(func=cmd_reachable_ui)

    # first-frame
    p = subparsers.add_parser(
        "first-frame",
        help="What a player would see right now: visible CanvasLayers, the "
             "topmost Control, paused state, cursor mode")
    p.set_defaults(func=cmd_first_frame)

    # contained-in
    p = subparsers.add_parser(
        "contained-in",
        help="Is one Control's screen box inside another's? Exit 1 with the per-side "
             "overhang when it hangs off (e.g. a legend row past its pause card)")
    p.add_argument("--node", "-n", required=True, help="The Control to test")
    p.add_argument("--within", required=True, help="The Control (panel/card) it should sit inside")
    p.add_argument("--json", action="store_true", help="Raw JSON")
    p.set_defaults(func=cmd_contained_in)

    # project-settings
    p = subparsers.add_parser(
        "project-settings",
        help="ProjectSettings as the RUNNING game sees them -- did the value written "
             "to project.godot actually land?")
    p.add_argument("--filter", metavar="PREFIX",
                   help="Only settings under this prefix, e.g. rendering/ or display/window/")
    p.add_argument("--name", dest="names", action="append", metavar="KEY",
                   help="Exact setting key (repeatable); a key no setting has exits 1")
    p.add_argument("--json", action="store_true", help="Raw JSON")
    p.set_defaults(func=cmd_project_settings)

    # sample-pixels
    p = subparsers.add_parser(
        "sample-pixels", help="Mean / dominant colour over a screen rect")
    p.add_argument("--rect", type=rect_arg, metavar="X,Y,W,H",
                   help="Screen rect (default: the whole viewport)")
    p.set_defaults(func=cmd_sample_pixels)

    # set-game-speed
    p = subparsers.add_parser("set-game-speed", help="Set game speed (time scale)")
    p.add_argument("scale", type=float, help="Time scale (0=pause, 1=normal, 10=fast)")
    p.set_defaults(func=cmd_set_game_speed)

    # pause / unpause
    p = subparsers.add_parser(
        "pause", help="Pause SceneTree.paused (the bus keeps answering)")
    p.set_defaults(func=cmd_pause)
    p = subparsers.add_parser("unpause", help="Reverse `pause`")
    p.set_defaults(func=cmd_unpause)

    # wait-frames
    p = subparsers.add_parser("wait-frames", help="Wait for N physics frames")
    p.add_argument("count", type=int, help="Number of frames to wait")
    p.set_defaults(func=cmd_wait_frames)

    # step-time
    p = subparsers.add_parser("step-time", help="Run the game for N game-seconds and return "
                              "(the tree is NOT paused or stepped; --then-pause freezes it afterwards)")
    p.add_argument("--seconds", "-s", type=float, required=True,
                   help="Game-seconds to advance (e.g. 0.05 to sample mid-tween)")
    p.add_argument("--hold", metavar="ACTION",
                   help="Action re-asserted pressed on every stepped frame, released at the end")
    p.add_argument("--then-pause", dest="then_pause", action="store_true",
                   help="Pause the tree the moment the step completes (lifting a "
                        "pre-existing pause for the step itself), so successive "
                        "step+read pairs carry zero ambient drift; `unpause` resumes")
    p.set_defaults(func=cmd_step_time)

    # tilemap-cells
    p = subparsers.add_parser("tilemap-cells",
                              help="Dump a tilemap's used cells (source/atlas ids) as data")
    p.add_argument("--node", "-n", required=True, help="TileMap or TileMapLayer node path")
    p.add_argument("--layer", type=int, help="TileMap layer index (default 0; TileMapLayer ignores it)")
    p.add_argument("--rect", type=rect_arg, metavar="X,Y,W,H",
                   help="Clip to a cell-coordinate rect, e.g. 0,0,16,16")
    p.set_defaults(func=cmd_tilemap_cells)

    # tilemap-region
    p = subparsers.add_parser("tilemap-region",
                              help="Connected components (4-neighbor) of cells matching an atlas coord")
    p.add_argument("--node", "-n", required=True, help="TileMap or TileMapLayer node path")
    p.add_argument("--atlas", type=coord_pair, required=True, metavar="X,Y",
                   help="Atlas coordinates to match, e.g. 3,1")
    p.add_argument("--layer", type=int, help="TileMap layer index (default 0; TileMapLayer ignores it)")
    p.add_argument("--source-id", type=int, dest="source_id",
                   help="Also require this tile source id (default: any source)")
    p.set_defaults(func=cmd_tilemap_region)

    # scripts-seen
    p = subparsers.add_parser("scripts-seen",
                              help="Every distinct script path that has entered the tree since launch")
    p.add_argument("--json", "-j", action="store_true",
                   help="Print the full raw reply (what tools/verify_ledger.py consumes)")
    p.set_defaults(func=cmd_scripts_seen)

    # ==================== CONSOLIDATED FINDINGS ====================

    # findings
    p = subparsers.add_parser(
        "findings",
        help="Run every live check at once and print one consolidated findings list")
    p.add_argument("--no-scenes", action="store_true",
                   help="Skip scene_validation (it loads every scene under scan_root "
                        "and is by far the slowest check)")
    p.add_argument("--no-baseline", action="store_true",
                   help="Ignore the saved UI findings baseline; every ui_layout finding gates")
    p.add_argument("--baseline-write", action="store_true",
                   help="Record the current ui_layout findings as pre-existing; "
                        "later runs gate only on NEW ones")
    p.add_argument("--json", "-j", action="store_true",
                   help="Print the full raw reply envelope")
    p.set_defaults(func=cmd_findings)

    # ==================== UI VALIDATION ====================

    # validate-ui
    p = subparsers.add_parser("validate-ui", help="Run UI layout validation checks")
    p.add_argument("--baseline-write", action="store_true",
                   help="Record the current findings as pre-existing; later runs "
                        "gate only on NEW ones (mirrors lint_project.gd --baseline-write)")
    p.add_argument("--no-baseline", action="store_true",
                   help="Ignore the saved baseline and gate on every finding")
    p.set_defaults(func=cmd_validate_ui)

    # save-ui-baseline
    p = subparsers.add_parser("save-ui-baseline", help="Save current UI layout as baseline")
    p.set_defaults(func=cmd_save_ui_baseline)

    # ui-snapshot-diff
    p = subparsers.add_parser("ui-snapshot-diff", help="Compare UI layout against saved baseline")
    p.set_defaults(func=cmd_ui_snapshot_diff)

    # ui-snapshot
    p = subparsers.add_parser("ui-snapshot", help="Get snapshot of all visible UI elements")
    p.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
    p.set_defaults(func=cmd_ui_snapshot)

    # node-bounds
    # canvas-scale
    p = subparsers.add_parser("canvas-scale",
                              help="Accumulated canvas transform scale + effective texture filter of a CanvasItem")
    p.add_argument("--node", "-n", required=True, help="CanvasItem node path")
    p.set_defaults(func=cmd_canvas_scale)

    # set-resolution
    p = subparsers.add_parser("set-resolution",
                              help="Resize the game window (reads back the applied size)")
    p.add_argument("--size", type=coord_pair, required=True, metavar="W,H",
                   help="Target window size, e.g. 1280,720")
    p.set_defaults(func=cmd_set_resolution)

    p = subparsers.add_parser("node-bounds", help="Get bounds for a specific node")
    p.add_argument("node_path", help="Node path (e.g., /root/Main/HUD/TopBar/CurrencyLabel)")
    p.set_defaults(func=cmd_node_bounds)

    # aabb - the 3D counterpart of node-bounds
    p = subparsers.add_parser("aabb",
                              help="Merged world-space AABB of a 3D node's geometry (Light3D excluded)")
    p.add_argument("--node", "-n", required=True,
                   help="Node path (e.g., /root/House/Living/tableCoffeeGlass)")
    p.set_defaults(func=cmd_aabb)

    # look-at
    p = subparsers.add_parser(
        "look-at",
        help="Orient a Node3D (default: the active Camera3D) to face another node")
    p.add_argument("--node", "-n", required=True,
                   help="Target to face (its AABB centre, or its position if it has no "
                        "3D geometry)")
    p.add_argument("--from-node", metavar="NODE",
                   help="The Node3D to orient (default: get_viewport().get_camera_3d(), "
                        "the active camera)")
    p.add_argument("--up", type=coord_2_or_3, metavar="X,Y,Z",
                   help="Up vector (default 0,1,0)")
    p.set_defaults(func=cmd_look_at)

    # fire-entry-point
    p = subparsers.add_parser(
        "fire-entry-point",
        help="Fire a named entry_points entry from devtools_config.json")
    p.add_argument("name", help="Key under entry_points in devtools_config.json")
    p.set_defaults(func=cmd_fire_entry_point)

    args = parser.parse_args(_glue_leading_dash_values(sys.argv[1:]))

    global _USERDATA_OVERRIDE, _PRECHECK_ENABLED, _SESSION, _RAW_JSON
    _USERDATA_OVERRIDE = args.userdata
    _PRECHECK_ENABLED = not args.no_precheck
    # getattr: subparsers that define their own local --json overwrite the dest
    # only for their own namespace; the global flag rides on raw_json.
    _RAW_JSON = bool(getattr(args, "raw_json", False))
    _SESSION = sanitize_session(args.session)
    if args.session and not _SESSION:
        print(f"error: --session {args.session!r} contains no usable characters "
              "(allowed: A-Z a-z 0-9 _ -)", file=sys.stderr)
        sys.exit(2)
    if args.session and _SESSION != args.session:
        # Say so rather than quietly using a different id than was asked for: the
        # game sanitizes identically, but a silently rewritten id is indistinguishable
        # from a dead game if the two ever stop agreeing.
        print(f"note: --session {args.session!r} sanitized to {_SESSION!r} "
              "(allowed: A-Z a-z 0-9 _ -)", file=sys.stderr)

    project_path = Path(args.project).resolve()
    try:
        args.func(args, project_path)
    except _RawJsonPrinted as e:
        # Global --json: send_command already printed the raw envelope.
        sys.exit(e.code)
    except BridgeError as e:
        # Bridge failures are expected operational states (dead game, crossed
        # replies, hung handler), not bugs: report them, don't traceback.
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # `devtools.py scene-tree | head` closes our stdout mid-print. The reply
        # has already been consumed off the bus by then, so exit quietly instead
        # of a traceback (moving-in:G-040). Redirect stdout to devnull first so
        # the interpreter's own flush at exit does not raise a second time.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
