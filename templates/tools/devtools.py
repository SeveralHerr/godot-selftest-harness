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


# harness-version: 0.10.0
# Version of the godot-selftest-harness this client was copied from. Compared against
# the running game's own stamp by the `harness-version` verb, so a half-refreshed
# install (new client, old autoload) is visible instead of mysterious.
HARNESS_VERSION = "0.10.0"

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
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, we just may not signal it
    except OSError:
        return True          # Windows raises here for cases we cannot classify
    except Exception:
        return True
    return True


def owner_status(user_data: Path) -> dict:
    """{present, pid, alive, path} for the bus owner record. Never raises.

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
        return {"present": False, "pid": None, "alive": False, "path": path}
    pid = owner.get("pid")
    return {"present": True, "pid": pid, "alive": pid_alive(pid), "path": path}


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


_MANGLED_ROOT = re.compile(r"^[A-Za-z]:[\/].*?[\/](root[\/].*)$")


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
        return "/" + m.group(1).replace("\\", "/")
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
    """
    deadline = time.time() + PRECHECK_SECONDS
    while time.time() < deadline:
        if not commands_path.exists():
            return
        time.sleep(_PRECHECK_POLL)

    if not commands_path.exists():
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
        pid = owner.get("pid")
        fate = ("that process is CONFIRMED GONE, so this is the ordinary "
                "aftermath of a crash or quit - relaunch"
                if not pid_alive(pid) else
                "that process is STILL ALIVE, so it is running but not polling "
                "THIS directory - check --userdata/--session")
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

    # Clear any existing result
    if results_path.exists():
        results_path.unlink()

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

            results_path.unlink()

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
                            "driving. Kill it and relaunch:\n  {kill}".format(
                                owner_pid=owner_pid, reply_pid=reply_pid,
                                kill=("taskkill /F /PID %d" % reply_pid
                                      if sys.platform == "win32"
                                      else "kill -9 %d" % reply_pid)))
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
        # The precheck proved the game consumed the command, so this is a
        # handler problem, not a liveness problem.
        raise NoReplyError(
            "No response from Godot after {t}s. The command WAS picked up (the "
            "game is alive) but '{action}' never answered - it is hung, still "
            "running, or it crashed mid-handler.".format(t=timeout, action=action)
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
        print("WARNING: --hide/--hide-group matched no CanvasItem - the capture "
              "shows everything.", file=sys.stderr)


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


def print_validation_result(result: dict):
    """Pretty-print validation results."""
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
        # Handle single scene validate response: data.issues is a list
        issues = data.get("issues", [])
        if isinstance(issues, list):
            for issue in issues:
                severity = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(issue["severity"], "???")
                print(f"  [{severity}] {issue['code']}: {issue['message']}")

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
        print(json.dumps(result["data"], indent=2))
    else:
        print(f"Failed: {result['message']}", file=sys.stderr)
        sys.exit(1)


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
    """Get performance metrics."""
    cmd_args = {}
    if getattr(args, "reset_baseline", False):
        cmd_args["reset_baseline"] = True

    result = send_command(project_path, "performance", cmd_args)
    if result["success"]:
        data = result["data"]
        print(f"FPS:              {data['fps']:.1f}")
        print(f"Frame time:       {data['frame_time_ms']:.2f} ms")
        print(f"Physics FPS:      {int(data['physics_fps'])}")
        print(f"Draw calls:       {int(data['draw_calls'])}")
        print(f"Objects:          {int(data['objects'])}")
        print(f"Static memory:    {data['static_memory_mb']:.1f} MB")
        print(f"Video memory:     {data['video_memory_mb']:.1f} MB")
        print(f"Total nodes:      {int(data['nodes'])}")
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
        return

    deadline = time.time() + max(1.0, args.wait)
    while time.time() < deadline:
        if not pid_alive(pid):
            print(f"  pid {pid} exited")
            return
        time.sleep(0.2)

    print(f"\nWARNING: pid {pid} is STILL ALIVE {args.wait:g}s after quit.\n"
          "A survivor answers the bus alongside any new instance, and the symptom "
          "is empty replies, not an error. Kill it before launching again:\n"
          + (f"  taskkill /F /PID {pid}" if sys.platform == "win32" else f"  kill -9 {pid}"),
          file=sys.stderr)
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
    if core_path.is_file():
        generic = sorted(set(_REGISTER_COMMAND_RE.findall(
            core_path.read_text(encoding="utf-8"))))

    config = _read_harness_config(project_path)
    ext_res = str(config.get("extension_script", "") or "res://devtools_ext/commands.gd")
    ext_path = project_path / ext_res.replace("res://", "")
    project_verbs = []
    if ext_path.is_file():
        project_verbs = sorted(set(_REGISTER_COMMAND_RE.findall(
            ext_path.read_text(encoding="utf-8"))))

    if args.json:
        print(json.dumps({
            "generic": generic,
            "project": project_verbs,
            "static_parse": True,
            "core_script": str(core_path),
            "extension_script": str(ext_path),
        }, indent=2))
        return

    print("Registered commands (STATIC PARSE of the scripts; the running game may differ):")
    print(f"  generic ({len(generic)}) from {core_path.name}:")
    for action in generic:
        print(f"    {action}")
    if ext_path.is_file():
        print(f"  project ({len(project_verbs)}) from {ext_res}:")
        for action in project_verbs:
            print(f"    {action}")
    else:
        print(f"  project: no extension script at {ext_res}")


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
    if args.json:
        print(json.dumps(actions, indent=2))
        return

    print(f"Registered commands ({len(actions)}):")
    for action in actions:
        print(f"  {action}")


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
            "bridge": "cold",
            "reason": why,
        }, indent=2))
    else:
        print(f"Client:    {HARNESS_VERSION}  (tools/devtools.py)")
        print(f"Installed: {installed or 'unreadable'}  "
              "(addons/godot_selftest/dev_tools.gd, read from disk)")
        print("Game:      unknown - the bridge is cold, so the RUNNING build was "
              "not asked.")
        print(f"  ({why})", file=sys.stderr)
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
        print(json.dumps({"client": HARNESS_VERSION, **data}, indent=2))
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
        print(f"Game speed: {data['previous_scale']:.1f} -> {data['current_scale']:.1f}")
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
    if data.get("tree_paused"):
        # A paused tree still emits frames while nothing advances, so this would
        # otherwise look like a successful step of a frozen game.
        print("  WARNING: the tree is paused — nothing actually advanced.")
    if data.get("budget_exhausted"):
        print("  WARNING: frame budget exhausted before the target was reached.")


# ==================== TOUCH SIMULATION ====================


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


# ==================== UI VALIDATION ====================


def cmd_validate_ui(args, project_path: Path):
    """Run all UI layout checks."""
    result = send_command(project_path, "validate_ui")
    print_validation_result(result)


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
    print()
    for el in elements:
        r = el["global_rect"]
        vis = "visible" if el["visible"] else "hidden"
        text_preview = f' "{el["text"]}"' if el.get("text") else ""
        if len(text_preview) > 53:
            text_preview = text_preview[:50] + '..."'
        print(f"  {el['name']} ({el['type']}) [{r['x']:.0f},{r['y']:.0f} {r['w']:.0f}x{r['h']:.0f}] {vis} alpha={el['modulate_a']:.1f}{text_preview}")


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


# ==================== LAUNCH / TILEMAP / SCRIPT CENSUS ====================


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
        if owner["present"] and owner["alive"]:
            print(f"Error: pid {owner['pid']} still owns this bus "
                  f"({owner['path'].name}).\n"
                  "Quit it first, or launch with --isolated to get a bus of your own.\n"
                  "Pass --allow-second-instance if you really mean to run two.",
                  file=sys.stderr)
            if not args.allow_second_instance:
                sys.exit(1)

    devtools_dir = project_path / ".devtools"
    devtools_dir.mkdir(exist_ok=True)
    out_log = devtools_dir / "launch_stdout.log"
    err_log = devtools_dir / "launch_stderr.log"

    cmd = [str(godot_path), "--path", str(project_path)]
    if not args.no_mute:
        # Opt-out rather than unconditional: --write-movie records the audio bus
        # into a .wav, and a muted run captures silence (gather:G-092).
        cmd.append("--mute")

    env = os.environ.copy()
    session = ""
    bus_dir = ""
    user_args = []
    if args.isolated:
        session = uuid.uuid4().hex[:8]
        bus_dir = tempfile.mkdtemp(prefix="devtools_bus_")
        env["GODOT_DEVTOOLS_SESSION"] = session
        env["GODOT_DEVTOOLS_BUSDIR"] = bus_dir
        user_args = ["--devtools-session", session, "--devtools-busdir", bus_dir]
    elif _SESSION:
        session = _SESSION
        env["GODOT_DEVTOOLS_SESSION"] = _SESSION
        user_args = ["--devtools-session", _SESSION]

    # argparse.REMAINDER keeps the separator itself; drop it so `launch -- --foo`
    # forwards `--foo` and not `-- --foo`.
    passthrough = list(getattr(args, "godot_args", None) or [])
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    cmd += passthrough
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

    print(f"Launched pid {proc.pid}: {' '.join(cmd)}")
    print(f"  stdout: {out_log}")
    print(f"  stderr: {err_log}")
    if passthrough:
        print(f"  forwarded to Godot: {' '.join(passthrough)}")

    if args.no_wait:
        print("  --no-wait: the bus was NOT verified. "
              "python tools/devtools.py ping")
        return

    reply = _await_bus(project_path, session, bus_dir)
    if reply is None:
        print("\nERROR: launched, but the bus never answered a ping within 20s.",
              file=sys.stderr)
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
    if session:
        flags = f"--session {session}"
        if bus_dir:
            flags += f" --userdata {bus_dir}"
        print(f"  Subsequent calls: python tools/devtools.py {flags} <verb>")
    else:
        print("  Subsequent calls: python tools/devtools.py <verb>")
    if bus_dir:
        print(f"  bus dir:  {data.get('bus_dir', bus_dir)}   (isolated)")
        print(f"  user://:  {data.get('user_dir', '?')}   (SHARED - saves, "
              "screenshots and UI baselines are not isolated)")


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
    viewport.

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
        if not c.get("on_screen"):
            why = "  OFF-SCREEN"
        elif c.get("blocked_by"):
            why = f"  BLOCKED BY {c['blocked_by']}"
        label = f' "{_printable(c["text"])}"' if c.get("text") else ""
        print(f"  {c.get('path')}{label}  [{c.get('kind')}] "
              f"{r.get('x'):.0f},{r.get('y'):.0f} {r.get('w'):.0f}x{r.get('h'):.0f}{why}")


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
        suffix = ("  " + "  ".join(f"{k}={_format_value(v)}" for k, v in extra.items())
                  if extra else "")
        print(f"  {node.get('path')}  [{node.get('type')}]{suffix}")
    if data.get("truncated"):
        print(f"  ... truncated at --limit {args.limit}")
    if not nodes:
        # An empty match is a legitimate answer, but exiting 0 on it makes a typo'd
        # predicate indistinguishable from a real absence in a shell pipeline.
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
    cmd_args = {
        "from": [float(args.origin[0]), float(args.origin[1])],
        "to": [float(args.to[0]), float(args.to[1])],
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
    print(f"  mask {data.get('mask')} = {', '.join(data.get('mask_names') or [])}")
    if not data["clear"]:
        pos = data.get("position") or {}
        print(f"  collider: {data.get('collider')} [{data.get('collider_class')}]")
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


def main():
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
    p = subparsers.add_parser("performance", help="Get performance metrics")
    p.add_argument("--reset-baseline", action="store_true",
                   help="Re-baseline the orphan count at the current value")
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
    p.add_argument("--no-mute", action="store_true",
                   help="Do not pass --mute (a --write-movie run records the audio bus, "
                        "and a muted run captures silence)")
    p.add_argument("--no-wait", action="store_true",
                   help="Return as soon as the process is spawned, without proving the "
                        "bus answers")
    p.add_argument("--allow-second-instance", action="store_true",
                   help="Launch even when a live process already owns this bus "
                        "(two instances answering one bus is silent corruption)")
    p.add_argument("godot_args", nargs=argparse.REMAINDER, metavar="-- GODOT ARGS",
                   help="Passed straight to Godot after a bare --")
    p.set_defaults(func=cmd_launch)

    # harness-version - which harness revision is installed
    p = subparsers.add_parser("harness-version",
                              help="Report the installed harness version (game + client)")
    p.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
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
    p.add_argument("--from", dest="origin", required=True, type=coord_pair, metavar="X,Y")
    p.add_argument("--to", required=True, type=coord_pair, metavar="X,Y")
    p.add_argument("--mask", type=int, help="Collision mask (default: every layer)")
    p.add_argument("--areas", action="store_true", help="Also hit Area2Ds")
    p.add_argument("--exclude", action="append", metavar="NODE",
                   help="Exclude this CollisionObject2D (repeatable)")
    p.set_defaults(func=cmd_raycast)

    # reachable-ui
    p = subparsers.add_parser(
        "reachable-ui",
        help="Every Control a finger or cursor could actually hit this frame "
             "(off-screen and input-blocked ones are named, not omitted)")
    p.set_defaults(func=cmd_reachable_ui)

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

    # wait-frames
    p = subparsers.add_parser("wait-frames", help="Wait for N physics frames")
    p.add_argument("count", type=int, help="Number of frames to wait")
    p.set_defaults(func=cmd_wait_frames)

    # step-time
    p = subparsers.add_parser("step-time", help="Pause the tree and advance it by N game-seconds")
    p.add_argument("--seconds", "-s", type=float, required=True,
                   help="Game-seconds to advance (e.g. 0.05 to sample mid-tween)")
    p.add_argument("--hold", metavar="ACTION",
                   help="Action re-asserted pressed on every stepped frame, released at the end")
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

    # ==================== UI VALIDATION ====================

    # validate-ui
    p = subparsers.add_parser("validate-ui", help="Run UI layout validation checks")
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
    main()
