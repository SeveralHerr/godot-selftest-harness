extends Node

## Generic DevTools autoload providing a file-based command interface for
## automation, testing, and CI. Commands are read from a JSON file on disk,
## dispatched to registered handlers, and results written back.
##
## This is the game-agnostic core of the godot_selftest harness. It ships only
## engine-generic verbs (ping, screenshot, scene tree, state/method access,
## action + touch input simulation, time stepping, engine feature flags,
## scene/UI validation, ...). Project-specific verbs live in a
## registry extension script (see devtools_config.json -> "extension_script")
## that implements `register_commands(dev: Node) -> void` and calls
## `dev.register_command(action, handler)` for each verb it adds. Because the
## extension loads AFTER the generic handlers, a project may override a generic
## verb by registering the same action string (last-writer-wins).

# harness-version: 0.55.0
# --- Constants ---

## Version of the godot-selftest-harness these files were copied from. Reported by the
## `harness_version` verb and stamped into every copied tool script, so a gap logged
## against a project can name the version it was seen on, and a refresh can tell a
## stale file from a customized one. Bump with .claude-plugin/plugin.json.
const HARNESS_VERSION: String = "0.55.0"

## Default bus filenames. With a session id (see _resolve_session) the id is spliced in
## before the extension, so two instances can each own a bus in the same user:// dir.
const COMMANDS_PATH: String = "user://devtools_commands.json"
const RESULTS_PATH: String = "user://devtools_results.json"
const LOG_PATH: String = "user://devtools_log.jsonl"
## Bus-identity record (G-009): written at _ready with this instance's pid, start
## time, project name and session, so a client can tell WHO owns the bus it is
## polling instead of guessing from a silent timeout.
const OWNER_PATH: String = "user://devtools_owner.json"

## Command-line flag and environment variable that name this instance's bus.
const SESSION_ARG: String = "--devtools-session"
const SESSION_ENV: String = "GODOT_DEVTOOLS_SESSION"

## Absolute directory to put the bus files in, replacing user://. Godot has no
## command line switch for user:// and honours no GODOT_USERDATA, so `launch
## --isolated` used to print a temp dir the game would never write to and then a
## follow-up command that could not work -- which reads as success and cost three
## sessions before it was believed (gather:G-091, G-111, G-115). The bus is the
## part that actually has to be isolated for two instances to coexist, and the bus
## is entirely ours, so this moves it. Saves and screenshots still go to the real
## user:// -- see _cmd_ping's bus_dir field, which reports exactly what was used.
const BUSDIR_ARG: String = "--devtools-busdir"
const BUSDIR_ENV: String = "GODOT_DEVTOOLS_BUSDIR"

## Breadcrumb naming the verb currently being served, written before dispatch and
## left behind if the process dies inside a handler (gather:G-103). A project verb
## took the game down and the only evidence was the last line of the log, which
## says what STARTED, not that it never finished.
const LAST_COMMAND_PATH: String = "user://devtools_last_command.json"
const CONFIG_PATH: String = "res://addons/godot_selftest/devtools_config.json"

## Frames to let elapse after _ready() before sampling the orphan-node baseline, so
## the main scene has finished coming up. ~0.5s at the default 60 FPS.
const ORPHAN_BASELINE_FRAMES: int = 30

## Hard ceiling on a single step_time request. The bridge serves one command at a
## time, so a typo'd "seconds" would otherwise wedge it for as long as the typo says.
const STEP_TIME_MAX_SECONDS: float = 60.0

## How long a single dispatch may hold the re-entrancy guard before the guard is
## force-released (H-038). This is a deadlock escape, NOT a command timeout: the
## handler is not cancelled (GDScript cannot), it is simply no longer allowed to
## keep the bus deaf.
##
## The ceiling has to clear the longest LEGITIMATE handler or it would reintroduce
## the overlap it exists to prevent -- step_time alone may run 60s, and validate_all
## over a large project has been measured near 90s. 300s is well past both, which is
## the correct bias: a live-but-slow handler must never trip this, while a handler
## killed by a runtime error mid-await (which never resumes, so it never clears the
## guard) must not brick the bus until the game is restarted.
const DISPATCH_WATCHDOG_MSEC: int = 300000

## Every check the `findings` verb aggregates, in report order. Named here rather
## than implied by the loop so the reply can carry a denominator: "reports clean
## while doing nothing" is this repo's documented failure mode, and a consolidated
## report is the easiest place for a check to disappear from without anyone
## noticing. Every id below ends up in exactly one of data["checks_run"] /
## data["checks_skipped"], and the client prints both. These ids are also the
## `source` values on the findings themselves and the keys of data["counts"].
const FINDINGS_CHECKS: Array = [
	"ui_layout",
	"ui_reachable",
	"signal_unconnected",
	"performance",
	"scene_validation",
]

## Default configuration, used verbatim when CONFIG_PATH is missing. Keys read by
## this core: validator_script, extension_script, hud_layer_name, scan_root,
## safe_area_inset. Remaining keys are consumed by sibling tools (lint_project.gd,
## run_tests.gd, the Python client, and the /verify command) and are carried here so
## the whole harness shares one schema.
##
## Every key MUST have a default here: a project whose devtools_config.json predates
## a new key still has to work, and _load_config() only overlays the keys it finds.
const DEFAULT_CONFIG: Dictionary = {
	"validator_script": "res://addons/godot_selftest/scene_validator.gd",
	"extension_script": "res://devtools_ext/commands.gd",
	"hud_layer_name": "HUD",
	"test_dir": "res://test/unit",
	"scan_root": "res://",
	"fps_min": 30,
	# Absolute orphan ceiling. Kept for compatibility; note that 0 is unreachable in
	# a real project (a fresh launch routinely reports dozens before any test action),
	# so prefer gating on orphan_growth_max. See _capture_orphan_baseline().
	"orphan_max": 0,
	# Tolerated growth in orphan nodes between the startup baseline and now. This is
	# the number a run can actually be held to.
	"orphan_growth_max": 20,
	# Pixels trimmed off each viewport edge before validate_ui judges whether a
	# control is on screen. Use it for a decorative overlay -- a CRT bezel, a notch,
	# a rounded corner -- that eats screen edges the viewport rect knows nothing
	# about. All-zero (the default) disables the check entirely.
	"safe_area_inset": {"left": 0, "top": 0, "right": 0, "bottom": 0},
	"main_scene": "",
	"entry_hook": {"node_path": "", "method": ""},
	# Named alternates to entry_hook, each {scene, node_path, method, args, match}.
	# scene/method/args/match all optional; node_path required. Nothing here fires on
	# its own - a caller reaches one by name via the fire_entry_point verb. `/verify`
	# picks the one whose `match` substrings hit the diff (commands/verify.md), so a
	# change to a boss/shop/level script gets a runtime path instead of only a code
	# read. {} means none configured.
	"entry_points": {},
	"mute": true,
	# Read by tools/verify_ledger.py, not by this core. Maps a script that reach can
	# never observe -- a RefCounted or Resource that is never any node's script -- to
	# the observed script(s) that vouch for it. Credited in a bucket of its own, so an
	# alias can never be mistaken for an observation. {} means "claim nothing".
	"reach_aliases": {},
	# Also read by tools/verify_ledger.py only. Dirs whose scripts run solely under
	# `godot --headless --script`, so no node can ever carry them and reach must not
	# score them as misses. [] if this project's tools/ holds real game code.
	"reach_headless_dirs": ["tools/"],
}

# --- Variables ---

## Bus paths in use. These are the session-resolved forms of COMMANDS_PATH / RESULTS_PATH
## / LOG_PATH and are what every read and write actually goes through - the consts are
## only the sessionless defaults.
var _commands_path: String = COMMANDS_PATH
var _results_path: String = RESULTS_PATH
var _log_path: String = LOG_PATH
var _owner_path: String = OWNER_PATH
var _last_command_path: String = LAST_COMMAND_PATH
## Session id, or "" for the shared default bus.
var _session: String = ""
## Absolute directory the bus files live in, or "" when they live in user://.
var _bus_dir: String = ""
var _commands_abs_path: String
var _results_abs_path: String
var _log_abs_path: String
var _owner_abs_path: String
var _last_command_abs_path: String
## Unix time this instance came up. Stamped (with the pid) onto every reply so a
## client can detect a foreign instance answering on its bus (G-036a).
var _start_unix: float = 0.0
## Owner-file heartbeat. See _touch_owner_heartbeat().
const HEARTBEAT_INTERVAL_MSEC: int = 1000
var _last_heartbeat_msec: int = 0
var _last_command_check_msec: int = 0
var _config: Dictionary = {}
var _handlers: Dictionary = {}
## Optional project-supplied callable whose Dictionary is merged into every response
## as "status". See register_status_provider().
var _status_provider: Callable = Callable()
var _active_simulated_inputs: Array[String] = []
## Simulated touches currently held down: { index: int -> position: Vector2 }.
## Kept so a release can default to the last known position, and so a run cannot
## end leaving phantom fingers down. See _clear_all_simulated_touches().
var _active_touches: Dictionary = {}
## Orphan-node count sampled shortly after startup, or -1 before it is captured.
## See _capture_orphan_baseline() for why an absolute orphan ceiling is useless.
var _orphan_baseline: int = -1
## Process frame at which the orphan baseline was (re)captured, or -1. Lets
## `performance` report how stale the baseline is (G-058).
var _orphan_baseline_frame: int = -1
## The last scale set through set_game_speed this session, or null if never.
## `performance` reports it so a leftover speed override is visible (G-059).
var _devtools_set_speed: Variant = null
## Every distinct script resource_path that has entered the tree since launch
## (G-074b/G-068): the existing tree is walked once at _ready to seed it, then
## `node_added` keeps it current. Keys are paths; values are `true`.
var _scripts_seen: Dictionary = {}
## entry_hook outcome, reported on every `ping` reply (gh#29): a config key that
## silently did nothing was worse than an absent one, because the project LOOKED
## configured. "not_configured" (default), "fired", or an error string naming what
## went wrong ("node not found: X", "no such method: X.Y", "misconfigured: ...").
## Never anything else, so a caller can branch on it without parsing prose.
var _entry_hook_status: String = "not_configured"
## The entry method's own return value, if it returned one. null otherwise -
## including when the method legitimately returned null, which `ping`'s
## entry_hook_result key cannot distinguish from "did not fire"; entry_hook_status
## is what answers that question.
var _entry_hook_result: Variant = null
## The "id" of the command currently being served, echoed verbatim onto its reply
## ("" when the request carried none).
##
## THIS IS A PURE ECHO AND NOT CONCURRENCY SUPPORT. The bridge is still strictly
## single-in-flight: one command file, one result file, no locking, no queue. The id
## exists only so that a crossed reply is DETECTABLE -- the client compares the
## echoed id against the one it sent and can tell "this is not my answer" instead of
## silently parsing another request's payload and reporting a missing key. Driving
## the bridge from two threads is still wrong; it is now merely loud about it.
var _current_request_id: String = ""
## True from the moment a handler is dispatched until it returns (H-038).
##
## _process calls _check_for_commands() WITHOUT await, so an awaiting handler --
## step_time, input_tap, any project verb that yields -- hands control straight back
## to _process. The next 100 ms tick would then read, DELETE and dispatch whatever
## command file had arrived, running a second handler on top of the first in the same
## scene tree and racing both replies onto the one result file. Measured, not
## theorised: step_time 5s + a ping 1s later produced the ping's reply at 1.26s and
## the step_time reply overwriting it at 5.12s.
##
## _current_request_id fixes neither half of that; it only makes a crossed reply
## detectable after the fact. The guard prevents the overlap instead, and it must be
## checked BEFORE the read and the delete -- checking it after would eat the command,
## since pickup deletes the file.
var _dispatch_busy: bool = false
var _dispatch_busy_since_msec: int = 0
var _dispatch_busy_action: String = ""
# Live reference to the instantiated registry extension. MUST be held so the
# Callables it bound (via register_command) are not freed out from under us.
var _extension: RefCounted = null
# True when this instance was brought up by `godot --script <runner>` (lint,
# tests, eval, capture) rather than by a running game. A passive instance
# registers its handlers - a test may call them in-process - but never touches
# the bus files: it does not claim the owner file, does not delete a "stale"
# command/result, and does not poll. Before 0.22.0 every headless gate did all
# three, so `lint_project.gd` run in one session deleted the owner file AND any
# in-flight command of the game another session was driving, and then left an
# owner record naming its own exited pid that refused the next `launch` for 30s
# (moving-in:G-018, G-025). Nothing in a --script run answers commands, so the
# claim was pure cost.
var _passive: bool = false
var _auto_name_re: RegEx = RegEx.new()
var _auto_name_re_compiled: bool = false
# In-tree node count baseline, sampled with the orphan baseline (moving-in:G-030).
var _node_baseline: int = -1
var _node_type_baseline: Dictionary = {}
# Smallest time scale set_game_speed accepts; below it the game is frozen, not slow.
const MIN_GAME_SPEED: float = 0.01


# --- Lifecycle ---

func _ready() -> void:
	# Keep polling while the tree is paused (findmyballs:G-003). The bus poll runs
	# from a pausable process callback, so without this ANY paused state is
	# unreachable over the bridge - which is exactly the UI most worth verifying:
	# pause menus, settings screens, death screens, level-complete screens.
	#
	# The failure it caused was worse than the outage. `run-method /root/SceneFlow
	# toggle_pause` succeeded, and every call after it answered "that process is
	# STILL ALIVE, so it is running but not polling THIS directory" - which reads
	# as a wrong --userdata problem, so the project debugged the bus for a cycle
	# before finding the pause. A confidently wrong diagnosis costs more than
	# silence. See also the owner file's last_poll_unix, which makes "alive but
	# not polling" a fact the client can state instead of guess at.
	#
	# Safe as a default: DevTools drives nothing gameplay-side on its own, it only
	# answers requests. A project that wants the bridge to pause with the game can
	# still set process_mode back from its own code.
	process_mode = Node.PROCESS_MODE_ALWAYS

	# Session resolution comes first: it decides which files every later step reads,
	# writes and clears. _process_command_line_args() runs much later and is far too
	# late to influence the paths.
	_resolve_session()
	_resolve_bus_dir()
	_build_bus_paths()
	_start_unix = Time.get_unix_time_from_system()
	_commands_abs_path = ProjectSettings.globalize_path(_commands_path)
	_results_abs_path = ProjectSettings.globalize_path(_results_path)
	_log_abs_path = ProjectSettings.globalize_path(_log_path)
	_owner_abs_path = ProjectSettings.globalize_path(_owner_path)
	_last_command_abs_path = ProjectSettings.globalize_path(_last_command_path)

	_load_config()
	_register_generic_handlers()
	_load_extension()

	_passive = _is_script_run()
	if not _passive:
		_clear_stale_files()
		_write_owner_file()
		# Fire-and-forget (gh#29): a --script runner has no real game to enter play
		# on, so entry_hook only fires for an actual launch. Does not block the
		# rest of _ready() - see _start_entry_hook().
		_start_entry_hook()

	# Script census (G-074b): seed from whatever is already in the tree (autoload
	# order means the main scene is usually not up yet), then track every node
	# that enters from here on.
	get_tree().node_added.connect(_on_node_added_track_script)
	_seed_scripts_seen(get_tree().root)

	_write_log("system", "DevTools initialized", {
		"commands_path": _commands_abs_path,
		"results_path": _results_abs_path,
		"handlers": _handlers.size(),
		"session": _session,
		"pid": OS.get_process_id(),
		"passive": _passive,
	})

	_process_command_line_args()
	_capture_orphan_baseline()


## `godot --script X` / `-s X` brings the autoloads up with no main scene; that is
## every shipped runner. Read from the ENGINE args (not user args after `--`), which
## is where the flag lives.
func _is_script_run() -> bool:
	var args: PackedStringArray = OS.get_cmdline_args()
	for i: int in args.size():
		if args[i] == "--script" or args[i] == "-s":
			return true
	return false


## True when this instance owns and polls a bus. Exposed so an extension or a test
## can tell a live bridge from a passive --script instance.
func is_bus_active() -> bool:
	return not _passive


func _process(_delta: float) -> void:
	if _passive:
		return
	var now_msec: int = Time.get_ticks_msec()
	if now_msec - _last_command_check_msec >= 100:
		_last_command_check_msec = now_msec
		_check_for_commands()
		_touch_owner_heartbeat(now_msec)


## Refreshes last_poll_unix on the owner file (findmyballs:G-004).
##
## The owner record used to carry a pid and nothing else that changes, so the
## client's only liveness test was "does this pid exist". That is a proxy, and
## it failed in both directions on real machines:
##
##   - Windows recycles pids. A dead Godot's pid was inherited by an unrelated
##     process (MoNotificationUx), so `launch` refused to start with "pid 14856
##     still owns this bus" and `ping` called that pid STILL ALIVE.
##   - A live owner that is not polling - paused tree, wrong --userdata, a
##     handler wedged mid-call - looks identical to a healthy one.
##
## A poll timestamp is not a proxy: it is the fact the client actually wants,
## and one number covers all of it. Recording the process NAME instead (the
## originally proposed fix) needs platform-specific lookup on both sides and
## still cannot tell a paused owner from a polling one.
##
## Throttled to once a second - the poll itself runs at 10Hz and this would
## otherwise be 10 writes/sec of the same few bytes for the life of the game.
func _touch_owner_heartbeat(now_msec: int) -> void:
	if now_msec - _last_heartbeat_msec < HEARTBEAT_INTERVAL_MSEC:
		return
	_last_heartbeat_msec = now_msec
	_write_owner_file()


func _exit_tree() -> void:
	_clear_all_simulated_inputs()
	_clear_all_simulated_touches()


# --- Public API ---

## Registers a handler for a bus action. Last writer wins, so a project's
## extension may override a generic verb by re-registering its action string.
## Handler signature: func(args: Dictionary) -> Dictionary returning exactly
## { "success": bool, "message": String, "data": Dictionary }.
##
## Guidance for SETTER verbs: leave the system in a state the game itself can reach.
## A debug verb that writes one half of an invariant pair is a latent trap. It
## manufactures a state no real play session can produce, so whatever you observe
## afterwards is the behavior of an impossible state -- and the verb rots silently
## the moment anything starts reading the other half. (Real case: a set_combo that
## wrote the combo count but not the combo window. It looked fine while the HUD drew
## the count unconditionally, and became useless the day the readout began fading on
## the window timer. Nothing announced the change.) If the value you are writing has
## a partner -- a timer, a window, a flag, a matching entry in some registry -- write
## the partner too, or better, route the write through the same method the game
## itself calls.
func register_command(action: String, handler: Callable) -> void:
	_handlers[action] = handler


## Registers a callable whose return Dictionary is merged into EVERY response under
## "status". Signature: func(args: Dictionary) -> Dictionary. Pass an empty Callable
## to clear it. At most one provider is active; last writer wins.
##
## Use it for the handful of facts that decide whether a reading means anything at all
## — typically "is the thing under test still alive/running". Without it, a session
## that has silently entered a dead or frozen state keeps answering every query with
## well-formed zeros, which reads identically to a genuine all-clear result. Keep the
## payload tiny: it rides on every single reply.
func register_status_provider(provider: Callable) -> void:
	_status_provider = provider


## Lets a script self-report that it ran (gh#30 / plant-tower-defense:G-014).
##
## `scripts-seen` (and `reach`, which reads it) can only ever learn a script's path
## from a NODE's `script` property, via node_added. A static-utility class
## (`class_name Music extends RefCounted` with static entry points, or one that
## builds a host Node with no script of its own attached) is never itself a node's
## script, no matter how much of it actually ran — the identical shape as a base
## class only a subclass instantiates (reached_base), one level further out, where
## no descendant carries the base's own script either. Call this once from each real
## entry point of a script that has this shape:
##   DevTools.mark_script_reached(<its own res:// path>)
## and it lands in the same `_scripts_seen` dictionary `scripts-seen` already
## reports and `reach` already reads — a second way to populate the existing
## signal, not a second signal a caller has to know to ask for separately. Get the
## path from `get_script().resource_path` in an instance method, or hardcode the
## literal in a purely static one (there is no `self` to ask). A no-op on an empty
## path rather than an error, so a defensive call site costs nothing.
func mark_script_reached(path: String) -> void:
	if not path.is_empty():
		_scripts_seen[path] = true


# --- Setup ---

## Resolves this instance's bus id from `--devtools-session <id>` (after a bare `--`)
## or the GODOT_DEVTOOLS_SESSION environment variable, the flag winning. Empty means
## the shared default bus, which is exactly the old behavior.
##
## Why: the bridge is one command file and one result file in one user:// directory, so
## a second running instance answers the first client's commands and neither notices.
## That makes parallel runtime verification impossible - agents working concurrently
## have to be forbidden from launching the game at all. A session id gives each instance
## its own pair of filenames, so N instances can share a user:// dir without crossing.
##
## The id is sanitized to [A-Za-z0-9_-] because it becomes part of a filename; anything
## else is dropped rather than escaped, and an id that sanitizes away is ignored.
func _resolve_session() -> void:
	var raw: String = ""
	var args: PackedStringArray = OS.get_cmdline_user_args()
	for i: int in args.size():
		if args[i] == SESSION_ARG and i + 1 < args.size():
			raw = args[i + 1]
			break
	if raw.is_empty():
		raw = OS.get_environment(SESSION_ENV)

	var clean: String = ""
	for c: String in raw:
		if (c >= "a" and c <= "z") or (c >= "A" and c <= "Z") or (c >= "0" and c <= "9") or c == "_" or c == "-":
			clean += c
	if clean.is_empty():
		return

	_session = clean


## Resolves an absolute directory to put the bus files in, from
## `--devtools-busdir <path>` (after a bare `--`) or GODOT_DEVTOOLS_BUSDIR, the
## flag winning. Empty -- the default -- keeps every bus file in user://, which is
## exactly the old behavior.
##
## This exists because `launch --isolated` promised isolation it could not give.
## Godot resolves user:// inside the engine and has no switch for it, so the temp
## dir the client printed stayed empty forever while the game happily wrote to the
## shared default bus; the follow-up command the client printed then failed with
## `game not running`, which reads as a dead game rather than as a broken flag
## (gather:G-091, G-111, G-115). The bus files are ours, so we can honour a
## directory for them even though we cannot move user:// itself.
##
## A directory that cannot be created is IGNORED with a log line rather than
## silently half-applied: half-applied isolation is what made this a three-session
## bug. _cmd_ping reports the directory actually in use so a client can verify
## rather than assume.
func _resolve_bus_dir() -> void:
	var raw: String = ""
	var args: PackedStringArray = OS.get_cmdline_user_args()
	for i: int in args.size():
		if args[i] == BUSDIR_ARG and i + 1 < args.size():
			raw = args[i + 1]
			break
	if raw.is_empty():
		raw = OS.get_environment(BUSDIR_ENV)
	raw = raw.strip_edges()
	if raw.is_empty():
		return

	if not DirAccess.dir_exists_absolute(raw):
		var err: int = DirAccess.make_dir_recursive_absolute(raw)
		if err != OK:
			_write_log("error", "Bus directory unusable; falling back to user://", {
				"bus_dir": raw, "error": err,
			})
			return

	_bus_dir = raw


## Builds the four bus paths from the session id and the bus directory. Called
## once, after both are resolved and before anything reads or writes them.
func _build_bus_paths() -> void:
	var suffix: String = "" if _session.is_empty() else "_%s" % _session
	var names: Dictionary = {
		"commands": "devtools_commands%s.json" % suffix,
		"results": "devtools_results%s.json" % suffix,
		"log": "devtools_log%s.jsonl" % suffix,
		"owner": "devtools_owner%s.json" % suffix,
		"last": "devtools_last_command%s.json" % suffix,
	}
	var base: String = _bus_dir if not _bus_dir.is_empty() else "user://"
	_commands_path = base.path_join(names["commands"])
	_results_path = base.path_join(names["results"])
	_log_path = base.path_join(names["log"])
	_owner_path = base.path_join(names["owner"])
	_last_command_path = base.path_join(names["last"])


func _load_config() -> void:
	_config = DEFAULT_CONFIG.duplicate(true)
	if not FileAccess.file_exists(CONFIG_PATH):
		return

	var json_text: String = FileAccess.get_file_as_string(CONFIG_PATH)
	if json_text.is_empty():
		return

	var parsed: Variant = JSON.parse_string(json_text)
	if parsed == null or not parsed is Dictionary:
		_write_log("error", "Failed to parse config JSON; using defaults", {"path": CONFIG_PATH})
		return

	for key: String in (parsed as Dictionary):
		_config[key] = (parsed as Dictionary)[key]


func _register_generic_handlers() -> void:
	register_command("ping", _cmd_ping)
	register_command("batch", _cmd_batch)
	register_command("screenshot", _cmd_screenshot)
	register_command("scene_tree", _cmd_scene_tree)
	register_command("validate_scene", _cmd_validate_scene)
	register_command("validate_all", _cmd_validate_all)
	register_command("get_state", _cmd_get_state)
	register_command("set_state", _cmd_set_state)
	register_command("run_method", _cmd_run_method)
	register_command("performance", _cmd_performance)
	register_command("quit", _cmd_quit)
	register_command("input_press", _cmd_input_press)
	register_command("input_release", _cmd_input_release)
	register_command("input_tap", _cmd_input_tap)
	register_command("input_clear", _cmd_input_clear)
	register_command("input_actions", _cmd_input_actions)
	register_command("input_sequence", _cmd_input_sequence)
	register_command("input_key", _cmd_input_key)
	register_command("mouse_move", _cmd_mouse_move)
	register_command("reload", _cmd_reload)
	register_command("input_state", _cmd_input_state)
	register_command("tilemap_cells", _cmd_tilemap_cells)
	register_command("tilemap_region", _cmd_tilemap_region)
	register_command("scripts_seen", _cmd_scripts_seen)
	register_command("touch_press", _cmd_touch_press)
	register_command("touch_release", _cmd_touch_release)
	register_command("touch_drag", _cmd_touch_drag)
	register_command("touch_clear", _cmd_touch_clear)
	register_command("touch_list", _cmd_touch_list)
	register_command("set_feature", _cmd_set_feature)
	register_command("set_game_speed", _cmd_set_game_speed)
	register_command("pause", _cmd_pause)
	register_command("repaint", _cmd_repaint)
	register_command("unpause", _cmd_unpause)
	register_command("step_time", _cmd_step_time)
	register_command("wait_frames", _cmd_wait_frames)
	register_command("clear_nodes", _cmd_clear_nodes)
	register_command("validate_ui", _cmd_validate_ui)
	register_command("get_ui_snapshot", _cmd_get_ui_snapshot)
	register_command("get_node_bounds", _cmd_get_node_bounds)
	register_command("contained_in", _cmd_contained_in)
	register_command("canvas_scale", _cmd_canvas_scale)
	register_command("aabb", _cmd_aabb)
	register_command("look_at", _cmd_look_at)
	register_command("fire_entry_point", _cmd_fire_entry_point)
	register_command("set_resolution", _cmd_set_resolution)
	register_command("save_ui_baseline", _cmd_save_ui_baseline)
	register_command("ui_snapshot_diff", _cmd_ui_snapshot_diff)
	register_command("list_commands", _cmd_list_commands)
	register_command("harness_version", _cmd_harness_version)
	register_command("find_nodes", _cmd_find_nodes)
	register_command("press", _cmd_press)
	register_command("raycast", _cmd_raycast)
	register_command("sample_pixels", _cmd_sample_pixels)
	register_command("curve", _cmd_curve)
	register_command("reachable_ui", _cmd_reachable_ui)
	register_command("findings", _cmd_findings)
	register_command("first_frame", _cmd_first_frame)
	register_command("project_settings", _cmd_project_settings)


func _load_extension() -> void:
	var ext_path: String = _config.get("extension_script", "")
	if ext_path.is_empty():
		return
	if not ResourceLoader.exists(ext_path):
		_write_log("system", "No registry extension found", {"path": ext_path})
		return

	var script: GDScript = load(ext_path) as GDScript
	if script == null:
		_write_log("error", "Failed to load registry extension", {"path": ext_path})
		return

	var instance: Variant = script.new()
	if instance == null or not instance is RefCounted:
		_write_log("error", "Registry extension did not produce a RefCounted instance", {"path": ext_path})
		return
	if not (instance as Object).has_method("register_commands"):
		_write_log("error", "Registry extension has no register_commands(dev) method", {"path": ext_path})
		return

	# Hold the instance so its bound Callables stay alive.
	_extension = instance
	_extension.register_commands(self)
	_write_log("system", "Registry extension loaded", {"path": ext_path, "handlers": _handlers.size()})


# --- Command Processing ---

func _check_for_commands() -> void:
	# Re-entrancy guard (H-038). FIRST, ahead of the existence test and well ahead of
	# the read/delete below: a command that arrives mid-await stays on disk and is
	# picked up on the tick after the current handler returns. Deferring is the whole
	# point -- the command is not dropped, only delayed.
	if _dispatch_busy:
		_check_dispatch_watchdog()
		return

	if not FileAccess.file_exists(_commands_path):
		return

	var json_text: String = FileAccess.get_file_as_string(_commands_path)
	DirAccess.remove_absolute(_commands_abs_path)

	if json_text.is_empty():
		_write_log("error", "Empty command file")
		return

	var parsed: Variant = JSON.parse_string(json_text)
	if parsed == null or not parsed is Dictionary:
		_write_log("error", "Failed to parse command JSON", {"raw": json_text.substr(0, 200)})
		return

	var command: Dictionary = parsed
	var action: String = command.get("action", "")
	var args: Dictionary = command.get("args", {})

	# Echo the caller's opaque request id back on the reply. Absent -> "". See the
	# _current_request_id declaration: this makes a crossed reply detectable, it does
	# NOT make the bridge concurrency-safe.
	var raw_id: Variant = command.get("id", "")
	var request_id: String = str(raw_id) if raw_id != null else ""
	_current_request_id = request_id

	if action.is_empty():
		_write_result("unknown", {"success": false, "message": "No action specified"})
		return

	if not _handlers.has(action):
		# Handlers register with underscores; the CLI, the docs and every generic
		# verb spell them with hyphens, and the sequence-step dispatcher already
		# normalizes one to the other. Accepting both here is what makes `cmd
		# light-get` behave like the identical step inside a sequence, which it
		# did not (gh#1) -- and the reply for the wrong spelling was
		# indistinguishable from the reply for a verb that was never registered.
		var underscored: String = action.replace("-", "_")
		if _handlers.has(underscored):
			action = underscored
		else:
			var msg: String = "Unknown action: %s" % action
			var near: String = _nearest_handler(underscored)
			if not near.is_empty():
				msg += " (did you mean '%s'? list_commands names them all)" % near
			_write_result(action, {"success": false, "message": msg})
			_write_log("error", msg)
			return

	_write_log("command", "Executing: %s" % action, args)
	_write_breadcrumb(action, args, request_id)

	var handler: Callable = _handlers[action]
	_dispatch_busy = true
	_dispatch_busy_since_msec = Time.get_ticks_msec()
	_dispatch_busy_action = action
	var result: Dictionary = await handler.call(args)
	_dispatch_busy = false
	_dispatch_busy_action = ""
	_clear_breadcrumb()
	# Belt and braces on the id (H-038 made the overlap unreachable, but the watchdog
	# can still release the guard under a wedged handler that later resumes). Restore
	# ours so this reply carries the id of the request it actually answers.
	_current_request_id = request_id
	_write_result(action, result)


## The registered verb closest to `action`, or "" when nothing is close enough.
##
## A bare "Unknown action" cannot tell a typo from a verb the project never
## registered, and those want opposite next steps. String.similarity() is a cheap
## bigram score; 0.6 keeps "set_stat" -> "set_state" while refusing to guess at a
## name that is simply absent.
func _nearest_handler(action: String) -> String:
	var best: String = ""
	var best_score: float = 0.6
	for name: String in _handlers.keys():
		var score: float = name.similarity(action)
		if score > best_score:
			best_score = score
			best = name
	return best


## Releases a guard that a handler has held past DISPATCH_WATCHDOG_MSEC.
##
## A GDScript runtime error inside an awaiting handler does not raise -- the coroutine
## simply never resumes -- so the line that clears _dispatch_busy would never run and
## the bus would stay deaf until the game was restarted, with nothing on the wire
## saying why. That is strictly worse than the overlap this guard prevents: a silent
## permanent outage versus a rare race. So the guard has an escape, and the escape is
## LOUD -- it names the verb, which the client also has from the breadcrumb file.
func _check_dispatch_watchdog() -> void:
	var held_msec: int = Time.get_ticks_msec() - _dispatch_busy_since_msec
	if held_msec < DISPATCH_WATCHDOG_MSEC:
		return
	var stuck_action: String = _dispatch_busy_action
	_dispatch_busy = false
	_dispatch_busy_action = ""
	var detail: String = "Dispatch guard force-released after %.1fs in '%s'; that handler never returned (a runtime error inside an await never resumes). Accepting commands again -- if '%s' does resume, its reply may cross another." % [
		held_msec / 1000.0, stuck_action, stuck_action,
	]
	_write_log("error", detail)
	# ANSWER the caller, do not just log at it (gh#1). _current_request_id still
	# holds the wedged request's id, so a client that is still waiting gets a real
	# failure instead of its full timeout and a log line written long after it gave
	# up. A client that already gave up ignores the reply -- it is stamped with an
	# id that is no longer anyone's.
	_write_result(stuck_action, {
		"success": false,
		"message": detail,
		"data": {"wedged_action": stuck_action, "held_seconds": held_msec / 1000.0},
	})


## Writes the single result file. `id` is always present -- the request's id verbatim,
## or "" when the request carried none -- so a client can reject a reply that is not
## its own instead of parsing it as an answer.
func _write_result(action: String, result: Dictionary) -> void:
	var response: Dictionary = {
		"id": _current_request_id,
		"action": action,
		"success": result.get("success", false),
		"message": result.get("message", ""),
		# The wire contract promises data is always a Dictionary. Handlers that
		# omit it on failure paths (or return null) must not leak that omission
		# to clients, so it is defaulted centrally here rather than per handler.
		"data": result.get("data") if result.get("data") is Dictionary else {},
		"timestamp": Time.get_unix_time_from_system(),
		# Bus identity (G-009/G-036a/G-038): every reply names the process that
		# wrote it. The client compares this against the owner file, so a foreign
		# instance answering on this bus is a loud error, not silent corruption.
		"pid": OS.get_process_id(),
		"start_unix": _start_unix,
	}
	var status: Dictionary = _collect_status()
	if not status.is_empty():
		response["status"] = status
	# Write-then-rename, so the reply file only ever exists complete (gh#5).
	# A plain open(WRITE) on the target holds a brief exclusive lock on Windows,
	# and a client clearing a stale reply in that window got WinError 32 -- after
	# the verb had already taken effect, so it could not tell whether to retry.
	# The client also keeps a bounded unlink retry for older game-side harnesses.
	var tmp_path: String = _results_path + ".tmp"
	var file: FileAccess = FileAccess.open(tmp_path, FileAccess.WRITE)
	if file == null:
		_write_log("error", "Failed to write result file", {"error": FileAccess.get_open_error(), "path": tmp_path})
		return
	file.store_string(JSON.stringify(response, "  "))
	file.close()
	var err: Error = DirAccess.rename_absolute(tmp_path, _results_path)
	if err != OK:
		# The client may be mid-read of a stale reply, which on Windows blocks the
		# replace. Fall back to the direct write rather than lose the reply.
		_write_log("warn", "Atomic rename of result file failed; writing directly", {"error": err})
		var direct: FileAccess = FileAccess.open(_results_path, FileAccess.WRITE)
		if direct == null:
			_write_log("error", "Failed to write result file", {"error": FileAccess.get_open_error()})
			return
		direct.store_string(JSON.stringify(response, "  "))
		direct.close()
		DirAccess.remove_absolute(tmp_path)


## Records the verb about to be dispatched, and deletes the record once it returns.
## A file left behind therefore means exactly one thing: the process died INSIDE
## that handler.
##
## The log already says what started, which is not the same claim -- a project verb
## took the game down and the log's last line (`[command] Executing: give_item`)
## was indistinguishable from a verb that was merely still running, so "the game
## died during X" had to be inferred rather than read (gather:G-103). Args are
## included because the argument is usually the reason.
func _write_breadcrumb(action: String, args: Dictionary, request_id: String) -> void:
	var file: FileAccess = FileAccess.open(_last_command_path, FileAccess.WRITE)
	if file == null:
		return
	file.store_string(JSON.stringify({
		"action": action,
		"args": _serialize_variant(args),
		"id": request_id,
		"pid": OS.get_process_id(),
		"started_unix": Time.get_unix_time_from_system(),
	}, "  "))
	file.close()


func _clear_breadcrumb() -> void:
	if FileAccess.file_exists(_last_command_path):
		DirAccess.remove_absolute(_last_command_abs_path)


## Never let a broken provider take the bridge down: a status hook that errors would
## otherwise poison every reply, including the ones you would use to diagnose it.
func _collect_status() -> Dictionary:
	if not _status_provider.is_valid():
		return {}
	var out: Variant = _status_provider.call({})
	return out if out is Dictionary else {}


func _process_command_line_args() -> void:
	var args: PackedStringArray = OS.get_cmdline_args()
	for arg in args:
		match arg:
			"--devtools-screenshot":
				# Take a screenshot on the next frame so the scene is rendered.
				await get_tree().process_frame
				# await: _cmd_screenshot is a coroutine whenever it hides nodes.
				var result: Dictionary = await _cmd_screenshot({})
				_write_log("cli", "CLI screenshot", result)
			"--devtools-validate":
				# Validate after the scene tree is ready.
				await get_tree().process_frame
				var result: Dictionary = _cmd_validate_all({})
				_write_result("validate_all", result)
				_write_log("cli", "CLI validate_all", {"success": result.get("success", false)})


# --- Command Handlers ---

## Wire contract - data keys: timestamp (float), session (String, "" on the default
## bus), pid (int), start_unix (float), scripts_seen (int), bus_dir (String, the
## absolute directory the bus files are in), user_dir (String, where user:// really
## points).
##
## bus_dir and user_dir are reported separately and always, because `--isolated`
## previously claimed an isolation it did not have and nothing could contradict it
## (gather:G-115). When they differ, the bus is isolated and saves/screenshots are
## not; when they match, nothing is isolated. Either way it is now a read rather
## than an assumption.
## Runs several verbs in ONE round trip (0.47.0, bead ik8). Every bus call costs a
## command-file write, up to a 100 ms poll and a result-file read; verb-usage on the
## two live projects showed 13k calls, most of them get_state / set_state /
## run_method / get_node_bounds in tight read-modify-read runs. This dispatches
## each item through the same registry (project verbs included, hyphens accepted),
## awaits it, and returns every item's full envelope in order - a failure does not
## stop the batch unless `stop_on_error` says so, and the reply's `failed` names
## the indices. `batch` and `quit` are refused inside a batch (recursion; a quit
## would kill the game before the result is written). Cap: 200 items.
##
## args: { "commands": [ {"action": String, "args": Dictionary}, ... ],
##         "stop_on_error": bool (default false) }
## data: { "results": [ {"action", "success", "message", "data"} ... ], "count",
##         "succeeded", "failed": [indices], "stopped_at": int|-1 }
func _cmd_batch(args: Dictionary) -> Dictionary:
	var items: Variant = args.get("commands", [])
	if not (items is Array):
		return {"success": false, "message": "batch: 'commands' must be an array of {action, args}", "data": {}}
	var commands: Array = items
	if commands.is_empty():
		return {"success": false, "message": "batch: no commands given", "data": {"results": [], "count": 0}}
	if commands.size() > 200:
		return {"success": false, "message": "batch: %d commands; the cap is 200 per batch" % commands.size(), "data": {}}
	var stop_on_error: bool = bool(args.get("stop_on_error", false))
	var results: Array = []
	var failed: Array = []
	var stopped_at: int = -1
	for i: int in commands.size():
		var item: Variant = commands[i]
		var action: String = ""
		var cargs: Dictionary = {}
		if item is Dictionary:
			action = str((item as Dictionary).get("action", ""))
			var a: Variant = (item as Dictionary).get("args", {})
			if a is Dictionary:
				cargs = a
		var entry: Dictionary = {"action": action, "success": false, "message": "", "data": {}}
		var key: String = action.replace("-", "_")
		if action.is_empty():
			entry["message"] = "batch item %d: no action" % i
		elif key == "batch" or key == "quit":
			entry["message"] = "batch item %d: '%s' is not allowed inside a batch" % [i, action]
		elif not _handlers.has(key):
			var near: String = _nearest_handler(key)
			entry["message"] = "Unknown action: %s%s" % [action, (" (did you mean '%s'?)" % near) if not near.is_empty() else ""]
		else:
			_write_log("command", "Executing: %s (batch item %d)" % [key, i], cargs)
			var handler: Callable = _handlers[key]
			var r: Variant = await handler.call(cargs)
			if r is Dictionary:
				var rd: Dictionary = r
				entry["success"] = bool(rd.get("success", false))
				entry["message"] = str(rd.get("message", ""))
				entry["data"] = rd.get("data", {})
			else:
				entry["message"] = "batch item %d: handler returned no envelope" % i
		results.append(entry)
		if not entry["success"]:
			failed.append(i)
			if stop_on_error:
				stopped_at = i
				break
	var succeeded: int = results.size() - failed.size()
	return {
		"success": failed.is_empty(),
		"message": "batch: %d of %d succeeded%s" % [succeeded, commands.size(),
			(" - stopped at item %d" % stopped_at) if stopped_at >= 0 else ""],
		"data": {"results": results, "count": commands.size(), "succeeded": succeeded,
			"failed": failed, "stopped_at": stopped_at},
	}


func _cmd_ping(_args: Dictionary) -> Dictionary:
	return {
		"success": true,
		"message": "pong",
		"data": {
			"timestamp": Time.get_unix_time_from_system(),
			"session": _session,
			"pid": OS.get_process_id(),
			"start_unix": _start_unix,
			"scripts_seen": _scripts_seen.size(),
			# Since 0.12.0 the bridge answers while paused, so a reply no longer
			# implies a running tree. Report it rather than let a client assume.
			"paused": get_tree().paused,
			"bus_dir": _bus_dir if not _bus_dir.is_empty() else ProjectSettings.globalize_path("user://"),
			"user_dir": ProjectSettings.globalize_path("user://"),
			# Where this game's res:// is on disk, so a client can tell a
			# worktree sibling's game from its own (plant-tower-defense:G-018).
			"project_path": ProjectSettings.globalize_path("res://"),
			# gh#29: "not_configured", "fired", or an error naming what went
			# wrong - never silent. See _start_entry_hook().
			"entry_hook_status": _entry_hook_status,
			"entry_hook_result": _serialize_variant(_entry_hook_result),
		},
	}


## Captures the viewport to a PNG.
##
## args: { "filename": String, "region": [x, y, w, h], "hide": Array[String] of
##         node paths, "hide_group": Array[String] of group names }
## data keys: path, width, height, size_bytes, region, hidden (Array of the node
## paths actually hidden and restored).
##
## Why region/hide exist: store art is game pixels with no UI on them at a fixed
## aspect, and the verb could only capture the whole window with everything
## visible. Producing one cover took two set_state calls to hide HUD nodes by
## hand plus a separate PIL script to crop -- and hiding by hand is easy to get
## wrong in the other direction, with nothing to catch a HUD left switched off
## (gather:G-079). Visibility here is ALWAYS restored, including on the failure
## paths, so this verb cannot leave the game in a state no player can reach.
func _cmd_screenshot(args: Dictionary) -> Dictionary:
	var default_name: String = "screenshot_%s.png" % Time.get_datetime_string_from_system().replace(":", "-")
	var filename: String = args.get("filename", default_name)
	var screenshots_dir: String = ProjectSettings.globalize_path("user://screenshots")
	DirAccess.make_dir_recursive_absolute(screenshots_dir)

	# Collect what to hide, remembering each node's PREVIOUS state so a node that
	# was already invisible is not "restored" into visibility.
	#
	# CanvasItem OR CanvasLayer (gh#5). CanvasLayer extends Node, not CanvasItem,
	# and it is what nearly every HUD, pause menu and overlay is rooted in -- so
	# the CanvasItem-only version silently dropped the exact node this flag exists
	# to hide, warned, and handed back a HUD-bearing capture as the thing asked
	# for. Both classes have the same `visible` semantics. Anything else named is
	# an ERROR, not a warning: a capture that ignored --hide is worse than none.
	var to_hide: Array[Node] = []
	var was_visible: Array[bool] = []
	var hidden_paths: Array = []
	var unhideable: Array = []
	for entry: Variant in (args.get("hide", []) if args.get("hide") is Array else []):
		var found: Dictionary = _resolve_node(str(entry))
		var n: Node = found["node"]
		if n is CanvasItem or n is CanvasLayer:
			to_hide.append(n)
		elif n == null:
			unhideable.append("%s: no such node" % str(entry))
		else:
			unhideable.append("%s: %s has no visibility (not a CanvasItem or CanvasLayer)" % [
				str(entry), n.get_class()])
	for entry: Variant in (args.get("hide_group", []) if args.get("hide_group") is Array else []):
		var matched: int = 0
		for node: Node in get_tree().get_nodes_in_group(str(entry)):
			if node is CanvasItem or node is CanvasLayer:
				to_hide.append(node)
				matched += 1
		if matched == 0:
			unhideable.append("group '%s': no CanvasItem or CanvasLayer in it" % str(entry))
	if not unhideable.is_empty():
		return {
			"success": false,
			"message": "--hide matched nothing it could hide; no capture written. " + "; ".join(PackedStringArray(unhideable)),
			"data": {"unhideable": unhideable},
		}

	for item: Node in to_hide:
		was_visible.append(bool(item.get("visible")))
		hidden_paths.append(str(item.get_path()))
		item.set("visible", false)

	# One frame so the hidden nodes are actually off the rendered image. The
	# visibility change lands in THIS frame's draw; process_frame resumes at the
	# start of the next, after that draw. NOT RenderingServer.frame_post_draw:
	# headless never draws, so that signal never fires and the verb hung forever
	# with the flag (found by check_templates once --hide got a positive control).
	if not to_hide.is_empty():
		await get_tree().process_frame

	var image: Image = get_viewport().get_texture().get_image()

	for i: int in to_hide.size():
		to_hide[i].set("visible", was_visible[i])

	if image == null:
		return {"success": false, "message": "Failed to capture viewport image"}

	var region: Dictionary = {}
	var raw_region: Variant = args.get("region")
	if raw_region is Array and (raw_region as Array).size() == 4:
		var a: Array = raw_region
		var rect: Rect2i = Rect2i(int(a[0]), int(a[1]), int(a[2]), int(a[3])).intersection(
			Rect2i(0, 0, image.get_width(), image.get_height()))
		if rect.size.x <= 0 or rect.size.y <= 0:
			return {
				"success": false,
				"message": "region %s does not overlap the %dx%d capture" % [
					str(raw_region), image.get_width(), image.get_height()],
			}
		image = image.get_region(rect)
		region = {"x": rect.position.x, "y": rect.position.y,
			"w": rect.size.x, "h": rect.size.y}

	var abs_path: String = screenshots_dir.path_join(filename)
	var err: Error = image.save_png(abs_path)
	if err != OK:
		return {"success": false, "message": "Failed to save PNG: error %d" % err}

	return {
		"success": true,
		"message": "Screenshot saved",
		"data": {
			"path": abs_path,
			"width": image.get_width(),
			"height": image.get_height(),
			"size_bytes": FileAccess.get_file_as_bytes(abs_path).size() if FileAccess.file_exists(abs_path) else -1,
			"region": region,
			"hidden": hidden_paths,
		},
	}


## args: { "depth": int, "root": String (subtree to start from),
##         "properties": Array[String] (report these on every node) }
##
## `root` exists because a whole-scene snapshot truncates before it reaches a
## deep UI subtree, so the buttons a panel just built could not even be
## enumerated (gather:G-119). `properties` exists because identifying one node
## among auto-named siblings otherwise costs a get_state round trip per child
## (gather:G-109) -- though find_nodes answers that question directly and is
## usually the better verb.
func _cmd_scene_tree(args: Dictionary) -> Dictionary:
	var depth: int = args.get("depth", 10)
	var root: Node = get_tree().current_scene
	var root_path: String = args.get("root", "")
	if not root_path.is_empty():
		var resolved: Dictionary = _resolve_node(root_path)
		if resolved["node"] == null:
			return {"success": false, "message": "Root node not found: %s" % root_path}
		root = resolved["node"]
	if root == null:
		return {"success": false, "message": "No current scene"}

	var extra: Array = args.get("properties", []) if args.get("properties") is Array else []
	var tree_data: Dictionary = _serialize_node(root, depth, extra)
	return {
		"success": true,
		"message": "Scene tree captured",
		"data": tree_data,
	}


func _serialize_node(node: Node, depth: int, extra: Array = []) -> Dictionary:
	# `script` is the node's script resource path, or "" when it has none (or has a
	# built-in one, which has no path). It exists so a caller can answer "did this run
	# actually touch the file I changed?" by intersecting a scene-tree snapshot with a
	# diff, instead of asking a human or a model to remember. `tools/verify_ledger.py`
	# is that caller. Keep the key present-but-empty rather than absent: a missing key
	# and "no script" must not look the same to the client.
	var script_path: String = ""
	var node_script: Script = node.get_script() as Script
	if node_script != null:
		script_path = node_script.resource_path

	# `scene_file` is set by Godot on the root of an instanced scene, so a changed
	# .tscn is reachable the same way a changed .gd is. Empty for ordinary nodes.
	var data: Dictionary = {
		"name": node.name,
		"type": node.get_class(),
		"path": str(node.get_path()),
		"script": script_path,
		"scene_file": node.scene_file_path,
	}

	if node is Node2D:
		var n2d: Node2D = node as Node2D
		data["position"] = {"x": n2d.position.x, "y": n2d.position.y}
		data["rotation"] = n2d.rotation
		data["visible"] = n2d.visible

	if node is Control:
		var ctrl: Control = node as Control
		data["position"] = {"x": ctrl.position.x, "y": ctrl.position.y}
		data["size"] = {"x": ctrl.size.x, "y": ctrl.size.y}
		data["visible"] = ctrl.visible

	# Requested properties are reported per node under "properties", present but
	# null where the node does not have one -- an absent key and "this node has no
	# such property" must not look the same to a client scanning for a match.
	if not extra.is_empty():
		var props: Dictionary = {}
		for entry: Variant in extra:
			var name: String = str(entry)
			var walk: Dictionary = _resolve_property_path(node, name)
			props[name] = _serialize_variant(walk["value"]) if walk["ok"] else null
		data["properties"] = props

	if depth > 0 and node.get_child_count() > 0:
		var children: Array = []
		for child in node.get_children():
			children.append(_serialize_node(child, depth - 1, extra))
		data["children"] = children

	return data


func _cmd_validate_scene(args: Dictionary) -> Dictionary:
	var path: String = args.get("path", "")
	if path.is_empty():
		return {"success": false, "message": "No scene path provided"}

	var validator_script: GDScript = _load_validator()
	if validator_script == null:
		return {"success": false, "message": "Validator script not found: %s" % _config.get("validator_script", "")}

	var issues: Array = validator_script.validate_scene(path)
	return {
		"success": issues.is_empty(),
		"message": "%d issues found" % issues.size() if not issues.is_empty() else "No issues found",
		"data": {"path": path, "issues": issues},
	}


func _cmd_validate_all(_args: Dictionary) -> Dictionary:
	var validator_script: GDScript = _load_validator()
	if validator_script == null:
		return {"success": false, "message": "Validator script not found: %s" % _config.get("validator_script", "")}

	var scan_root: String = _config.get("scan_root", "res://")
	var scenes: Array[String] = _find_all_scenes(scan_root)
	var all_issues: Array = []
	var scene_results: Array = []

	for scene_path in scenes:
		var issues: Array = validator_script.validate_scene(scene_path)
		scene_results.append({
			"path": scene_path,
			"issues": issues,
			"valid": issues.is_empty(),
		})
		all_issues.append_array(issues)

	return {
		"success": all_issues.is_empty(),
		"message": "%d scenes validated, %d total issues" % [scenes.size(), all_issues.size()],
		"data": {
			"total_scenes": scenes.size(),
			"total_issues": all_issues.size(),
			"scenes": scene_results,
		},
	}


## Reads a node's state. Two deliberate departures from a plain property dump:
##
## 1. args["properties"] -- an Array of property-name Strings (a bare String is also
##    accepted) -- narrows the response to just those properties. An unfiltered read
##    of a Label is ~120 keys and of a typical game node ~200, which is a lot of
##    tokens to grep for one number. A requested name the node does NOT have is
##    listed in data["missing"] rather than silently dropped: a silent omission is
##    indistinguishable from "the value happens to be absent", which is exactly how
##    the transform bug below stayed invisible for weeks. The read itself still
##    reports success -- probing for an optional property is a legitimate use -- so
##    check data["missing"], which is always present when a filter was supplied.
##
## 2. data["transform"] is ALWAYS present, filter or no filter, and is read straight
##    off the node instead of through get_property_list(). This is not redundancy.
##    Godot clears PROPERTY_USAGE_STORAGE on position/rotation/scale/pivot_offset for
##    the children of a Container, so the enumeration above cannot see them at all --
##    a scale animation on a VBoxContainer child is completely absent from the dump
##    while working perfectly on screen. (Verified on 4.7.1: a Label under a plain
##    Control reports scale with usage 6; the same Label under a VBoxContainer
##    reports it with usage 0x10000004, which fails the filter.) Controls also expose
##    offset_transform_scale, which stays 1.0 while the node visibly scales, so the
##    enumerated dump can be actively misleading. data["transform"] is the value to
##    assert on. Requesting "transform" in args["properties"] is accepted and never
##    counts as missing.
##
##    Note: data["transform"] shadows the engine's own raw `transform` property (a
##    Transform2D / Transform3D) on nodes that have one. Nothing readable was lost --
##    that property carries usage 0 and so never passed the filter anyway.
func _cmd_get_state(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}

	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path}
	node_path = resolved["path"]

	var wanted: Array = []
	var raw_wanted: Variant = args.get("properties", [])
	if raw_wanted is String:
		wanted = [raw_wanted]
	elif raw_wanted is Array:
		wanted = raw_wanted
	elif raw_wanted != null:
		return {"success": false, "message": "args.properties must be an array of property names"}

	var state: Dictionary = {}
	for prop in node.get_property_list():
		var usage: int = prop.get("usage", 0)
		if usage & PROPERTY_USAGE_SCRIPT_VARIABLE or usage & PROPERTY_USAGE_STORAGE:
			var prop_name: String = prop["name"]
			state[prop_name] = _serialize_variant(node.get(prop_name))

	var data: Dictionary = state
	var missing: Array = []

	if not wanted.is_empty():
		var filtered: Dictionary = {}
		for entry: Variant in wanted:
			var prop_name: String = str(entry)
			if prop_name == "transform":
				# Always served below, from the node itself.
				continue
			if prop_name.contains("."):
				# A dotted path walks into Resources and Dictionaries, which is
				# where "which picture / which item / what colour" actually lives
				# (gather:G-110, gather:G-117). A failed walk is reported in
				# `missing` with its reason, never silently omitted.
				var walk: Dictionary = _resolve_property_path(node, prop_name)
				if walk["ok"]:
					filtered[prop_name] = _serialize_variant(walk["value"])
				else:
					missing.append("%s (%s)" % [prop_name, walk["reason"]])
				continue
			if state.has(prop_name):
				filtered[prop_name] = state[prop_name]
			elif prop_name in node:
				# Present on the node but hidden from the enumeration above (container
				# child, editor-only usage flags, ...). Read it directly.
				filtered[prop_name] = _serialize_variant(node.get(prop_name))
			else:
				missing.append(prop_name)
		filtered["missing"] = missing
		data = filtered

	data["transform"] = _read_transform(node)

	var message: String = "State retrieved for %s" % node_path
	if not missing.is_empty():
		message += " -- %d requested propert%s not found on this node: %s" % [
			missing.size(),
			"y" if missing.size() == 1 else "ies",
			", ".join(PackedStringArray(missing)),
		]

	return {
		"success": true,
		"message": message,
		"data": data,
	}


## Walks a dotted property path from `root`, e.g. "texture.region",
## "slot_data.item.name", "texture.atlas.resource_path". Returns
## { "ok": bool, "value": Variant, "reason": String }.
##
## Why: `--property texture` answered `<AtlasTexture#-92233719...>` -- an object
## id -- and WHICH picture a Sprite2D shows lives in `texture.region`, one hop
## down. There is no node path for a sub-resource, so no generic verb could reach
## it and every Resource-shaped question needed a bespoke project verb
## (gather:G-110, gather:G-117). Every inventory slot, every pickup, every
## StyleBox colour and Shape2D extent sits exactly one or two hops past what
## get_state could say.
##
## Each hop must land on an Object (Resource, Node, ...) or a Dictionary; a hop
## off a plain value fails with a reason naming the segment that ran out, rather
## than returning null and letting the caller read that as "the value is null".
func _resolve_property_path(root: Variant, path: String) -> Dictionary:
	var segments: PackedStringArray = path.split(".", false)
	if segments.is_empty():
		return {"ok": false, "value": null, "reason": "empty property path"}

	var current: Variant = root
	var walked: Array[String] = []
	for segment: String in segments:
		if current == null:
			return {
				"ok": false, "value": null,
				"reason": "%s is null, so .%s has nothing to read from" % [
					".".join(walked) if not walked.is_empty() else "the node", segment,
				],
			}
		if current is Dictionary:
			var dict: Dictionary = current
			if not dict.has(segment):
				return {
					"ok": false, "value": null,
					"reason": "no key %s in %s (keys: %s)" % [
						segment, ".".join(walked), ", ".join(PackedStringArray(dict.keys())),
					],
				}
			current = dict[segment]
		elif current is Object:
			var obj: Object = current
			if not (segment in obj):
				return {
					"ok": false, "value": null,
					"reason": "%s (%s) has no property %s" % [
						".".join(walked) if not walked.is_empty() else "the node",
						obj.get_class(), segment,
					],
				}
			current = obj.get(segment)
		elif _BUILTIN_COMPONENTS.has(typeof(current)):
			# A built-in struct (Vector2/3, Color, Rect2, Transform...). `position.x`
			# is plainly what the caller means, and Variant indexing by member
			# name answers it (H-046: before this branch `find-nodes --property
			# position.x` printed `null`, indistinguishable from a property that
			# genuinely holds null, and get-state refused outright).
			var members: Array = _BUILTIN_COMPONENTS[typeof(current)]
			if not members.has(segment):
				return {
					"ok": false, "value": null,
					"reason": "%s is a %s, which has no component .%s (has: %s)" % [
						".".join(walked), type_string(typeof(current)), segment,
						", ".join(PackedStringArray(members)),
					],
				}
			current = current[segment]
		else:
			return {
				"ok": false, "value": null,
				"reason": "%s is a %s, not an object -- cannot read .%s off it" % [
					".".join(walked), type_string(typeof(current)), segment,
				],
			}
		walked.append(segment)

	return {"ok": true, "value": current, "reason": ""}


## Named components readable off each built-in struct via Variant indexing
## (`Vector2(1, 2)["x"]`). Consulted by _resolve_property_path so a dotted path may
## end (or hop) inside a struct; writes still go through the whole-value path.
const _BUILTIN_COMPONENTS: Dictionary = {
	TYPE_VECTOR2: ["x", "y"],
	TYPE_VECTOR2I: ["x", "y"],
	TYPE_VECTOR3: ["x", "y", "z"],
	TYPE_VECTOR3I: ["x", "y", "z"],
	TYPE_VECTOR4: ["x", "y", "z", "w"],
	TYPE_VECTOR4I: ["x", "y", "z", "w"],
	TYPE_COLOR: ["r", "g", "b", "a", "r8", "g8", "b8", "a8", "h", "s", "v"],
	TYPE_RECT2: ["position", "size", "end"],
	TYPE_RECT2I: ["position", "size", "end"],
	TYPE_QUATERNION: ["x", "y", "z", "w"],
	TYPE_PLANE: ["x", "y", "z", "d", "normal"],
	TYPE_AABB: ["position", "size", "end"],
	TYPE_BASIS: ["x", "y", "z"],
	TYPE_TRANSFORM2D: ["x", "y", "origin"],
	TYPE_TRANSFORM3D: ["basis", "origin"],
	TYPE_PROJECTION: ["x", "y", "z", "w"],
}


## One member read off whatever _resolve_property_path() landed on. Dictionary and
## Object take different syntax, and set_state has to handle both because the walk
## it shares with get_state hops through both.
func _read_member(target: Variant, member: String) -> Variant:
	if target is Dictionary:
		var dict: Dictionary = target
		return dict.get(member)
	if target is Object:
		return (target as Object).get(member)
	return null


## The write half of _read_member. A Dictionary key is created if absent -- that is
## what writing to a Dictionary means -- while Object.set stays a silent no-op on an
## unknown property, which is what the read-back in _cmd_set_state is there to catch.
func _write_member(target: Variant, member: String, value: Variant) -> void:
	if target is Dictionary:
		var dict: Dictionary = target
		dict[member] = value
		return
	if target is Object:
		(target as Object).set(member, value)


## Resolves a node path segment by segment, matching child names literally.
##
## Godot auto-names an unnamed Control `@Label@249`, `scene-tree` prints exactly
## that, and feeding the printed path straight back answered `Node not found` --
## so a node the snapshot had just listed was unaddressable, and its auto-named
## ancestors along with it (gather:G-108). Round-tripping a path the harness
## itself printed must never 404, so when get_node_or_null() fails we descend
## manually with get_node_or_null(NodePath(name)) per child, comparing names as
## plain strings.
##
## Returns the Node, or null when a segment genuinely does not exist.
func _find_node_by_literal_names(node_path: String) -> Node:
	var trimmed: String = node_path.strip_edges()
	if not trimmed.begins_with("/root"):
		return null
	var segments: PackedStringArray = trimmed.substr(1).split("/", false)
	var current: Node = get_tree().root
	# segments[0] is "root" itself.
	for i: int in range(1, segments.size()):
		var wanted: String = segments[i]
		var found: Node = null
		for child: Node in current.get_children():
			if String(child.name) == wanted:
				found = child
				break
		if found == null:
			return null
		current = found
	return current


## Reads a node's live transform directly off the object, bypassing
## get_property_list() entirely -- see _cmd_get_state for why that indirection is
## unreliable. Returns an empty Dictionary for a node with no spatial transform.
func _read_transform(node: Node) -> Dictionary:
	var out: Dictionary = {}

	if node is Node2D:
		var n2d: Node2D = node as Node2D
		out["space"] = "2d"
		out["position"] = _serialize_variant(n2d.position)
		out["global_position"] = _serialize_variant(n2d.global_position)
		out["rotation"] = n2d.rotation
		out["rotation_degrees"] = rad_to_deg(n2d.rotation)
		out["scale"] = _serialize_variant(n2d.scale)
		out["global_scale"] = _serialize_variant(n2d.global_scale)
		out["skew"] = n2d.skew
	elif node is Control:
		var ctrl: Control = node as Control
		out["space"] = "control"
		out["position"] = _serialize_variant(ctrl.position)
		out["global_position"] = _serialize_variant(ctrl.global_position)
		out["size"] = _serialize_variant(ctrl.size)
		out["global_rect"] = _serialize_variant(ctrl.get_global_rect())
		# global_rect above is the Control's own answer, in CANVAS-LAYER space --
		# it belongs here beside position/size because it mirrors a property.
		# screen_rect is where it lands after ancestor CanvasLayer transforms, and
		# is the one every screen-position verb measures (gh#2). On a scaled layer
		# the two differ, and that difference is the bug this key makes visible.
		out["screen_rect"] = _serialize_variant(_screen_rect_of(ctrl))
		out["rotation"] = ctrl.rotation
		out["rotation_degrees"] = rad_to_deg(ctrl.rotation)
		out["scale"] = _serialize_variant(ctrl.scale)
		out["pivot_offset"] = _serialize_variant(ctrl.pivot_offset)
	elif node is Node3D:
		var n3d: Node3D = node as Node3D
		out["space"] = "3d"
		out["position"] = _serialize_variant(n3d.position)
		out["global_position"] = _serialize_variant(n3d.global_position)
		out["rotation"] = _serialize_variant(n3d.rotation)
		out["rotation_degrees"] = _serialize_variant(n3d.rotation_degrees)
		out["scale"] = _serialize_variant(n3d.scale)

	if node is CanvasItem:
		var item: CanvasItem = node as CanvasItem
		out["visible"] = item.visible
		out["effective_visible"] = _is_effectively_visible(item)
		out["modulate_a"] = item.modulate.a
		out["effective_modulate_a"] = _get_effective_alpha(item)
	elif node is Node3D:
		out["visible"] = (node as Node3D).visible

	return out


## Extracts up to `max_count` numeric components from a JSON array ([x, y, ...])
## or a Dictionary keyed by `keys` ({"x": ..., "y": ...}). Returns an empty Array
## when the shape does not fit -- callers treat that as "not coercible", never as
## a zero vector. `min_count` components are required; extras up to `max_count`
## are taken when present (Color's optional alpha).
func _numeric_components(value: Variant, keys: Array, min_count: int, max_count: int) -> Array:
	var out: Array = []
	# A String spelling of the same tuple: "x,y", "(x, y)", "[x,y]". The CLI cannot
	# always hand us a JSON array -- argparse eats a leading "-" -- and the error a
	# caller used to get named the target type without naming a syntax that produces
	# it, so the working form was found by guessing (gather:G-137). Accepted here so
	# every consumer of _coerce_arg (set_state, run_method, project verbs) gains it
	# at once rather than one call site at a time.
	if value is String:
		var text: String = (value as String).strip_edges().lstrip("([").rstrip(")]")
		if text.is_empty():
			return []
		var parts: PackedStringArray = text.split(",", false)
		if parts.size() < min_count or parts.size() > max_count:
			return []
		for part: String in parts:
			var token: String = part.strip_edges()
			if not token.is_valid_float():
				return []
			out.append(token.to_float())
		return out
	if value is Array:
		var arr: Array = value
		if arr.size() < min_count or arr.size() > max_count:
			return []
		for item: Variant in arr:
			if not _is_number(item):
				return []
			out.append(float(item))
		return out
	if value is Dictionary:
		var dict: Dictionary = value
		for i: int in range(max_count):
			var key: String = keys[i]
			if dict.has(key):
				if not _is_number(dict[key]):
					return []
				out.append(float(dict[key]))
			elif i < min_count:
				return []
			else:
				break
		return out
	return []


## Coerces a JSON-decoded argument toward `target_type` (a TYPE_* constant).
## Returns { "ok": bool, "value": Variant, "reason": String }.
##
## JSON can only carry null/bool/number/string/array/dict, so a typed method
## parameter or property (Vector2, Color, StringName, ...) is unreachable from the
## bus without this. The rules, in order:
##   * target TYPE_NIL (untyped) or already the right type -> passed through.
##   * [x, y] / {"x": .., "y": ..} (and 3-component / r,g,b,a forms) -> Vector2,
##     Vector2i, Vector3, Vector3i, Color per the target.
##   * int <-> float <-> bool numeric widening/narrowing.
##   * String <-> StringName / NodePath, and numbers/bools stringified.
##   * A JSON Array feeding a Packed*Array parameter goes through type_convert().
## Anything else is ok:false with a reason naming BOTH types -- type_convert()
## never fails (it returns the target's default value), so it is deliberately NOT
## used as a blanket fallback: a silently-defaulted argument is exactly the bug
## this helper exists to prevent (G-016, G-035).
func _coerce_arg(value: Variant, target_type: int) -> Dictionary:
	var from_type: int = typeof(value)
	if target_type == TYPE_NIL or from_type == target_type:
		return {"ok": true, "value": value, "reason": ""}

	# GDScript itself accepts `null` for any Object-typed parameter (Node,
	# Resource, a custom class, ..) - it is the language's own nilable case,
	# not a coercion. The bus was stricter than the language it drives: a
	# losing path called with a null Object arg by both a unit test and the
	# game's own code (`_on_pest_escaped(_pest: Pest)`) was unreachable from
	# run-method, which "cannot convert Nil (null) to Object" made read like a
	# bug in the caller rather than a bus limitation (plant-tower-defense:G-026).
	if from_type == TYPE_NIL and target_type == TYPE_OBJECT:
		return {"ok": true, "value": null, "reason": ""}

	match target_type:
		TYPE_VECTOR2, TYPE_VECTOR2I:
			var comps: Array = _numeric_components(value, ["x", "y"], 2, 2)
			if comps.size() == 2:
				if target_type == TYPE_VECTOR2:
					return {"ok": true, "value": Vector2(comps[0], comps[1]), "reason": ""}
				return {"ok": true, "value": Vector2i(roundi(comps[0]), roundi(comps[1])), "reason": ""}
		TYPE_VECTOR3, TYPE_VECTOR3I:
			var comps: Array = _numeric_components(value, ["x", "y", "z"], 3, 3)
			if comps.size() == 3:
				if target_type == TYPE_VECTOR3:
					return {"ok": true, "value": Vector3(comps[0], comps[1], comps[2]), "reason": ""}
				return {"ok": true, "value": Vector3i(roundi(comps[0]), roundi(comps[1]), roundi(comps[2])), "reason": ""}
		TYPE_COLOR:
			var comps: Array = _numeric_components(value, ["r", "g", "b", "a"], 3, 4)
			if comps.size() >= 3:
				var alpha: float = comps[3] if comps.size() >= 4 else 1.0
				return {"ok": true, "value": Color(comps[0], comps[1], comps[2], alpha), "reason": ""}
		TYPE_INT:
			if from_type == TYPE_FLOAT or from_type == TYPE_BOOL:
				return {"ok": true, "value": int(value), "reason": ""}
		TYPE_FLOAT:
			if from_type == TYPE_INT or from_type == TYPE_BOOL:
				return {"ok": true, "value": float(value), "reason": ""}
		TYPE_BOOL:
			if from_type == TYPE_INT or from_type == TYPE_FLOAT:
				return {"ok": true, "value": bool(value), "reason": ""}
		TYPE_STRING:
			if from_type in [TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_STRING_NAME, TYPE_NODE_PATH]:
				return {"ok": true, "value": str(value), "reason": ""}
		TYPE_STRING_NAME:
			if from_type == TYPE_STRING:
				return {"ok": true, "value": StringName(value), "reason": ""}
		TYPE_NODE_PATH:
			if from_type == TYPE_STRING:
				return {"ok": true, "value": NodePath(value), "reason": ""}
		_:
			# Packed arrays from a plain JSON array: the one family where
			# type_convert() is trusted, because the source shape is known.
			if from_type == TYPE_ARRAY and target_type >= TYPE_PACKED_BYTE_ARRAY and target_type <= TYPE_PACKED_COLOR_ARRAY:
				return {"ok": true, "value": type_convert(value, target_type), "reason": ""}

	# Name a syntax that WOULD have worked, not just the type that did not. The
	# rejected-type-only message sent callers guessing through three spellings of a
	# Vector2 before finding the one the bus accepts (gather:G-137).
	var accepted: String = ""
	match target_type:
		TYPE_VECTOR2, TYPE_VECTOR2I:
			accepted = ' -- accepted forms: [x, y] | {"x": .., "y": ..} | "x,y" | "(x,y)"'
		TYPE_VECTOR3, TYPE_VECTOR3I:
			accepted = ' -- accepted forms: [x, y, z] | {"x": .., "y": .., "z": ..} | "x,y,z"'
		TYPE_COLOR:
			accepted = ' -- accepted forms: [r, g, b] | [r, g, b, a] | {"r": .., "g": .., "b": ..} | "r,g,b,a"'

	return {
		"ok": false,
		"value": null,
		"reason": "cannot convert %s (%s) to %s%s" % [
			type_string(from_type), JSON.stringify(_serialize_variant(value)), type_string(target_type),
			accepted,
		],
	}


## Approximate-equality check for the set_state read-back: float and vector types
## compare with is_equal_approx so the engine's own storage precision cannot fail
## a legitimate write; everything else compares exactly.
func _values_match(a: Variant, b: Variant) -> bool:
	if typeof(a) != typeof(b):
		if _is_number(a) and _is_number(b):
			return is_equal_approx(float(a), float(b))
		return false
	match typeof(a):
		TYPE_FLOAT:
			return is_equal_approx(a, b)
		TYPE_VECTOR2, TYPE_VECTOR3, TYPE_VECTOR4, TYPE_COLOR, TYPE_QUATERNION, TYPE_RECT2, TYPE_TRANSFORM2D, TYPE_TRANSFORM3D:
			return a.is_equal_approx(b)
		_:
			return a == b


## Resolves a node path, retrying a bare path under /root (G-010): the autoload
## sits at /root/DevTools, so a relative "Main/Player" would otherwise be looked
## up under the autoload and fail confusingly. Returns { "node": Node-or-null,
## "path": String } where `path` is the one that actually resolved.
func _resolve_node(node_path: String) -> Dictionary:
	var node: Node = get_node_or_null(node_path)
	var used: String = node_path
	if node == null and not node_path.begins_with("/root"):
		var prefixed: String = "/root/" + node_path.trim_prefix("/")
		node = get_node_or_null(prefixed)
		if node != null:
			used = prefixed
	if node == null:
		# Last resort: literal per-segment descent, which addresses the @Name@id
		# names Godot auto-assigns and scene-tree prints (gather:G-108).
		var literal_path: String = used if used.begins_with("/root") else "/root/" + node_path.trim_prefix("/")
		node = _find_node_by_literal_names(literal_path)
		if node != null:
			used = literal_path
	return {"node": node, "path": used}


## Sets a property. Two guards beyond a raw `node.set` (G-035, G-029):
##  * The value is coerced toward the property's CURRENT type (JSON cannot carry
##    a Vector2), unless the current value is null -- then there is nothing to
##    key off and the value is written as-is.
##  * The property is READ BACK after the write and compared. `Object.set` on an
##    unknown property is a silent no-op, and a setter may clamp or reject the
##    value; either way "set had no effect" is a failure, not a success.
##
## A DOTTED path writes through to the object the path lands on, using the same
## _resolve_property_path() get_state reads through (gh#1). Neither Object.set nor
## Object.get walks dots, so `environment.ambient_light_energy` used to write
## nothing and then blame the property name -- and every knob in a lighting rig,
## a material or a sky lives exactly one level in, so the whole class was
## unreachable while reading it back worked fine. NOTE this mutates the Resource
## itself: a material shared by several nodes changes for all of them, which is
## usually what a tuning call wants and is worth knowing when it is not.
func _cmd_set_state(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}

	var node: Node = get_node_or_null(node_path)
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path}

	var property: String = args.get("property", "")
	if property.is_empty():
		return {"success": false, "message": "No property specified"}

	var value: Variant = args.get("value")

	# Resolve every segment but the last; the write lands on whatever that is.
	# A single-segment property leaves target == node, i.e. the old behavior.
	var target: Variant = node
	var leaf: String = property
	if property.contains("."):
		var segments: PackedStringArray = property.split(".", false)
		leaf = segments[segments.size() - 1]
		segments.remove_at(segments.size() - 1)
		var container_path: String = ".".join(segments)
		var walk: Dictionary = _resolve_property_path(node, container_path)
		if not walk["ok"]:
			return {
				"success": false,
				"message": "Cannot set %s.%s: %s" % [node_path, property, walk["reason"]],
				"data": {"property": property},
			}
		target = walk["value"]
		if not (target is Object or target is Dictionary):
			# Almost always a built-in struct (Vector2.x, Color.r). Those cannot be
			# written a component at a time through this path, but they CAN be
			# written whole, so say which call would have worked rather than
			# leaving the caller to find it by guessing.
			return {
				"success": false,
				"message": "Cannot set %s.%s: %s is a %s, not an object. Set it whole instead: --property %s --value <%s>" % [
					node_path, property, container_path,
					type_string(typeof(target)), container_path,
					type_string(typeof(target)),
				],
				"data": {"property": property, "container": container_path,
					"container_type": type_string(typeof(target))},
			}

	var current: Variant = _read_member(target, leaf)
	var coerced: bool = false
	if value != null and current != null and typeof(value) != typeof(current):
		var conversion: Dictionary = _coerce_arg(value, typeof(current))
		if not conversion["ok"]:
			return {
				"success": false,
				"message": "Cannot set %s.%s: %s" % [node_path, property, conversion["reason"]],
				"data": {"property": property},
			}
		value = conversion["value"]
		coerced = true
	elif value is Array and current is Array and (current as Array).is_typed():
		# plant-tower-defense:G-019: the bus carries a plain JSON Array, and
		# assigning one to an `Array[StringName]` (or any typed array) property
		# is a silent no-op in GDScript -- the read-back below catches that, but
		# the caller wanted the write to WORK. Rebuild the value as the target's
		# own typed array; Array's typed constructor converts each element (a
		# String becomes a StringName) and drops the lot with an engine error
		# when one cannot be converted, which the size check turns into a reply.
		var typed: Array = current
		var rebuilt: Array = Array(value, typed.get_typed_builtin(),
			typed.get_typed_class_name(), typed.get_typed_script())
		if rebuilt.size() != (value as Array).size():
			return {
				"success": false,
				"message": "Cannot set %s.%s: %d element(s) do not convert to the property's element type %s" % [
					node_path, property, (value as Array).size() - rebuilt.size(),
					type_string(typed.get_typed_builtin()) if typed.get_typed_class_name().is_empty() else String(typed.get_typed_class_name()),
				],
				"data": {"property": property, "written": _serialize_variant(value)},
			}
		value = rebuilt
		coerced = true

	_write_member(target, leaf, value)

	var read_back: Variant = _read_member(target, leaf)
	if not _values_match(read_back, value):
		# Name the object the write actually landed on. With a dotted path that is
		# NOT the node, and "unknown property" against the wrong class is what sent
		# a caller hunting for a typo in a correct name (gh#1).
		var owner_desc: String = node.get_class()
		if target is Object and target != node:
			owner_desc = "%s (%s)" % [".".join(property.split(".", false).slice(0, -1)),
				(target as Object).get_class()]
		elif target is Dictionary:
			owner_desc = "%s (Dictionary)" % ".".join(property.split(".", false).slice(0, -1))
		return {
			"success": false,
			"message": "set had no effect on %s.%s: wrote %s but read back %s -- %s has no property '%s', or its setter clamped/rejected the value" % [
				node_path, property,
				JSON.stringify(_serialize_variant(value)),
				JSON.stringify(_serialize_variant(read_back)),
				owner_desc, leaf,
			],
			"data": {
				"property": property,
				"written": _serialize_variant(value),
				"read_back": _serialize_variant(read_back),
			},
		}

	return {
		"success": true,
		"message": "Set %s.%s" % [node_path, property],
		"data": {
			"property": property,
			"value": _serialize_variant(read_back),
			"read_back": _serialize_variant(read_back),
			"coerced": coerced,
		},
	}


## Calls a method with its args coerced to the declared parameter types (G-016):
## the bus carries JSON, so a `func take_vec(v: Vector2)` was previously
## uncallable -- callv() with an Array where a Vector2 belongs fails, or worse,
## quietly misbehaves. Types come from get_method_list(); a method absent from it
## (some built-ins) falls back to the old uncoerced callv with a note in data. A
## coercion that cannot be done fails the command -- the method is NEVER called
## with a silently-wrong argument.
func _cmd_run_method(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}

	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s (also tried under /root)" % node_path}
	var used_path: String = resolved["path"]

	var method: String = args.get("method", "")
	if method.is_empty():
		return {"success": false, "message": "No method specified"}

	if not node.has_method(method):
		return {"success": false, "message": "Node %s has no method: %s" % [used_path, method]}

	var raw_args: Variant = args.get("args", [])
	if not raw_args is Array:
		return {"success": false, "message": "args must be a JSON array"}
	var method_args: Array = (raw_args as Array).duplicate()

	var signature: Dictionary = {}
	for entry: Dictionary in node.get_method_list():
		if entry.get("name", "") == method:
			signature = entry
			break

	var coercion_note: String = ""
	if signature.is_empty():
		coercion_note = "method not in get_method_list(); args passed uncoerced"
	else:
		var params: Array = signature.get("args", [])
		for i: int in range(method_args.size()):
			if i >= params.size():
				break  # varargs / extra args: leave them alone.
			var param_type: int = int(params[i].get("type", TYPE_NIL))
			var conversion: Dictionary = _coerce_arg(method_args[i], param_type)
			if not conversion["ok"]:
				return {
					"success": false,
					"message": "Argument %d of %s.%s(): %s" % [i, used_path, method, conversion["reason"]],
					"data": {"node_path": used_path, "method": method, "argument": i},
				}
			method_args[i] = conversion["value"]

	var result: Variant = node.callv(method, method_args)

	# `result: null` on its own is ambiguous: a `-> void` that ran perfectly and a
	# `-> int` that aborted on a runtime error both land here as null, and GDScript
	# gives the caller no exception to tell them apart (gather:G-096). What IS
	# knowable is what the method DECLARED it returns, so say that and let the
	# client phrase the difference. declared_return is a type_string(); "Nil" for a
	# `-> void` or an untyped func, "" when the method is absent from
	# get_method_list() and nothing can be claimed.
	var declared_return: String = ""
	if not signature.is_empty():
		var ret: Dictionary = signature.get("return", {})
		declared_return = type_string(int(ret.get("type", TYPE_NIL)))

	var data: Dictionary = {
		"result": _serialize_variant(result),
		"node_path": used_path,
		"method": method,
		"returned_null": result == null,
		"declared_return": declared_return,
	}
	if not coercion_note.is_empty():
		data["note"] = coercion_note

	return {
		"success": true,
		"message": "Called %s.%s()" % [used_path, method],
		"data": data,
	}


## Collects engine metrics. Beyond the absolute counters it reports orphan GROWTH
## against a baseline sampled at startup (see _capture_orphan_baseline), because the
## absolute orphan count is not a number a project can be held to.
##
## args["reset_baseline"] = true re-samples the baseline from the current count
## before reporting, so growth is measured from here on. Call it once the game has
## reached the state a run actually starts from -- typically right after /verify's
## entry hook -- otherwise the scene load itself dominates the delta.
##
## args["frames"] (int, default 30): FPS is MEASURED over this many frames by
## wall clock and reported as fps (the mean), fps_min, fps_max, fps_samples,
## fps_window_sec, plus fps_settling when the second half of the window ran
## more than 15% faster or slower than the first (moving-in:G-021, seen twice: a
## single Engine.get_frames_per_second() read straight after a quality-preset
## switch produced HIGH 110 / MEDIUM 50 / LOW 105 - a ladder that would have
## reported the slowest preset as the fastest - and a shadow toggle read as
## 70 -> 37 where a controlled A/B says the cost is 2 fps). frames = 0 gives the
## old instantaneous read; fps_instant carries it either way. Wall clock, not
## process delta, so a leftover set_game_speed cannot stretch it.
##
## Node growth (moving-in:G-030): a node parented under a live node is never an
## orphan, so a UI that adds a layer per visit or a pool that grows per spawn
## reports orphan growth +0 forever. node_baseline / node_growth are sampled and
## reset alongside the orphan baseline; args["by_type"] = true adds
## node_types_delta = { class: +N } for every class whose live count changed
## since the baseline, which turns "something accumulates" into "3 more
## CanvasLayers".
func _cmd_performance(args: Dictionary) -> Dictionary:
	var orphan_nodes: int = int(Performance.get_monitor(Performance.OBJECT_ORPHAN_NODE_COUNT))
	var node_count: int = int(Performance.get_monitor(Performance.OBJECT_NODE_COUNT))

	if bool(args.get("reset_baseline", false)):
		_orphan_baseline = orphan_nodes
		_orphan_baseline_frame = int(Engine.get_process_frames())
		_node_baseline = node_count
		_node_type_baseline = _count_nodes_by_type()
		_write_log("system", "Orphan baseline reset", {"orphan_baseline": _orphan_baseline, "node_baseline": _node_baseline})

	var baseline_captured: bool = _orphan_baseline >= 0
	var orphan_growth: int = (orphan_nodes - _orphan_baseline) if baseline_captured else 0
	var node_growth: int = (node_count - _node_baseline) if _node_baseline >= 0 else 0

	var fps_instant: float = Engine.get_frames_per_second()
	var frames: int = maxi(0, int(args.get("frames", 30)))
	var window: Dictionary = await _sample_fps_window(frames)
	var fps: float = float(window.get("mean", fps_instant)) if frames > 0 else fps_instant
	var data: Dictionary = {
		"fps": fps,
		"fps_instant": fps_instant,
		"fps_min": window.get("min", fps_instant),
		"fps_max": window.get("max", fps_instant),
		# Headless: a frame does no rendering work and waits on nothing, so
		# fps_max reads five figures (58823 from a 17us frame) and looks
		# broken rather than merely uninformative (H-060). fps (mean) and
		# fps_min are still the numbers a gate reads; this just says which
		# one not to trust, the way geometry findings already do.
		"fps_max_trustworthy": DisplayServer.get_name() != "headless",
		"fps_max_caveat": ("" if DisplayServer.get_name() != "headless" else
			"measured HEADLESS: an idle frame does no rendering work, so fps_max is not " +
			"a ceiling a player would ever see - read fps (mean) and fps_min instead"),
		"fps_samples": frames,
		"fps_window_sec": window.get("seconds", 0.0),
		"fps_settling": window.get("settling", false),
		"fps_first_half": window.get("first_half", fps_instant),
		"fps_second_half": window.get("second_half", fps_instant),
		"frame_time_ms": 1000.0 / maxf(1.0, fps),
		"physics_fps": Engine.physics_ticks_per_second,
		"static_memory_mb": OS.get_static_memory_usage() / (1024.0 * 1024.0),
		"video_memory_mb": Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / (1024.0 * 1024.0),
		"draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		"objects": Performance.get_monitor(Performance.OBJECT_COUNT),
		"nodes": node_count,
		# In-tree growth since the (orphan) baseline; -1 baseline = not sampled.
		"node_baseline": _node_baseline,
		"node_growth": node_growth,
		# Absolute count, unchanged and still reported.
		"orphan_nodes": orphan_nodes,
		# Growth against the startup baseline. -1 baseline means "not sampled yet".
		"orphan_baseline": _orphan_baseline,
		"orphan_baseline_captured": baseline_captured,
		"orphan_growth": orphan_growth,
		# Both thresholds are echoed so a gate does not have to re-read the config.
		"orphan_max": int(_config.get("orphan_max", 0)),
		"orphan_growth_max": int(_config.get("orphan_growth_max", 20)),
		"physics_2d_active_objects": Performance.get_monitor(Performance.PHYSICS_2D_ACTIVE_OBJECTS),
		"physics_3d_active_objects": Performance.get_monitor(Performance.PHYSICS_3D_ACTIVE_OBJECTS),
		# Time-scale context (G-058/G-059/G-066): an FPS or growth reading taken
		# under a leftover speed override is not comparable to one at 1.0, and
		# nothing used to say which one you had.
		"time_scale": Engine.time_scale,
		# The last value set via set_game_speed this session; null if never.
		"devtools_set_speed": _devtools_set_speed,
		# Frames since the orphan baseline was captured; -1 before capture.
		"orphan_baseline_age_frames": (int(Engine.get_process_frames()) - _orphan_baseline_frame) if _orphan_baseline_frame >= 0 else -1,
		# gh#6: the bridge is PROCESS_MODE_ALWAYS, so it answers on a paused tree
		# -- with a plausible FPS for a game that is not stepping. A whole /verify
		# phase validated a title-screen pause as healthy on those numbers. The
		# fact goes in the reply, so a reader who skipped `ping` still sees it.
		"tree_paused": get_tree().paused,
	}

	if bool(args.get("by_type", false)):
		var now_by_type: Dictionary = _count_nodes_by_type()
		var delta: Dictionary = {}
		if _node_baseline >= 0:
			for cls: String in now_by_type:
				var d: int = int(now_by_type[cls]) - int(_node_type_baseline.get(cls, 0))
				if d != 0:
					delta[cls] = d
			for cls: String in _node_type_baseline:
				if not now_by_type.has(cls):
					delta[cls] = -int(_node_type_baseline[cls])
		data["node_types_delta"] = delta
		data["node_types_now"] = now_by_type

	var message: String = "Performance metrics collected"
	if get_tree().paused:
		message += " on a PAUSED tree - FPS and growth here describe a game that is not stepping"
	if frames > 0:
		message += " (fps mean %.1f over %d frames, min %.1f max %.1f%s)" % [
			fps, frames, float(data["fps_min"]), float(data["fps_max"]),
			" - STILL SETTLING" if bool(data["fps_settling"]) else ""]
	if baseline_captured:
		message += " (orphans %d, baseline %d, growth %d; nodes %d, growth %d)" % [
			orphan_nodes, _orphan_baseline, orphan_growth, node_count, node_growth]
	else:
		message += " (orphan baseline not sampled yet; growth is not meaningful)"

	return {
		"success": true,
		"message": message,
		"data": data,
	}


## Wall-clock frame times over `frames` process frames. Returns {} for 0.
## mean = frames / elapsed; min/max are the slowest/fastest single frame.
## settling is true when the two halves' means differ by more than 15%, which
## is what "the renderer has not settled after that change" looks like from
## inside one window - the caller cannot tell it from "this is the rate"
## otherwise (moving-in:G-021).
func _sample_fps_window(frames: int) -> Dictionary:
	if frames <= 0:
		return {}
	var deltas: Array[float] = []
	var last_usec: int = Time.get_ticks_usec()
	for _i: int in range(frames):
		await get_tree().process_frame
		var now_usec: int = Time.get_ticks_usec()
		deltas.append(float(now_usec - last_usec) / 1_000_000.0)
		last_usec = now_usec
	var total: float = 0.0
	var slowest: float = 0.0
	var fastest: float = INF
	for d: float in deltas:
		total += d
		slowest = maxf(slowest, d)
		fastest = minf(fastest, d)
	var mean: float = float(deltas.size()) / maxf(total, 0.000001)
	var half: int = deltas.size() / 2
	var first_total: float = 0.0
	var second_total: float = 0.0
	for i: int in deltas.size():
		if i < half:
			first_total += deltas[i]
		else:
			second_total += deltas[i]
	var first_mean: float = float(half) / maxf(first_total, 0.000001) if half > 0 else mean
	var second_mean: float = float(deltas.size() - half) / maxf(second_total, 0.000001)
	var settling: bool = half > 0 and absf(first_mean - second_mean) > 0.15 * maxf(first_mean, second_mean)
	return {
		"mean": mean,
		"min": 1.0 / maxf(slowest, 0.000001),
		"max": 1.0 / maxf(fastest, 0.000001),
		"seconds": total,
		"first_half": first_mean,
		"second_half": second_mean,
		"settling": settling,
	}


## Live node count per class name, whole tree. O(N); called only at baseline
## capture/reset and on `performance --by-type`.
func _count_nodes_by_type() -> Dictionary:
	var counts: Dictionary = {}
	var stack: Array[Node] = [get_tree().root]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		var cls: String = n.get_class()
		counts[cls] = int(counts.get(cls, 0)) + 1
		for c: Node in n.get_children(true):
			stack.append(c)
	return counts


## Samples the orphan-node count once the main scene has settled, so `performance`
## can report orphan growth rather than only an absolute count.
##
## Why: an absolute ceiling of 0 is unreachable in a real project. A fresh launch
## into a main scene routinely reports dozens of orphans before any test action, and
## scene swaps push it into the hundreds; measured deltas of a few are noise. A
## threshold nothing can ever satisfy trains you to skip the check, which is worse
## than having no check. Growth against a launch baseline is a number a run can be
## held to -- see `orphan_growth_max` in devtools_config.json.
##
## Deliberately fire-and-forget: _ready() does not await it, so a slow first scene
## cannot delay the bridge coming up. Until it lands, _orphan_baseline stays -1 and
## `performance` says so rather than reporting a fictitious growth of 0.
func _capture_orphan_baseline() -> void:
	for _i: int in range(ORPHAN_BASELINE_FRAMES):
		await get_tree().process_frame
	_orphan_baseline = int(Performance.get_monitor(Performance.OBJECT_ORPHAN_NODE_COUNT))
	_orphan_baseline_frame = int(Engine.get_process_frames())
	_node_baseline = int(Performance.get_monitor(Performance.OBJECT_NODE_COUNT))
	_node_type_baseline = _count_nodes_by_type()
	_write_log("system", "Orphan baseline captured", {
		"orphan_baseline": _orphan_baseline,
		"node_baseline": _node_baseline,
		"frames_waited": ORPHAN_BASELINE_FRAMES,
	})


func _cmd_quit(args: Dictionary) -> Dictionary:
	var exit_code: int = args.get("exit_code", 0)
	_write_log("system", "Quit requested", {"exit_code": exit_code})
	# Write result before quitting so the caller can read it.
	_write_result("quit", {"success": true, "message": "Quitting with code %d" % exit_code})
	get_tree().quit(exit_code)
	# Return value won't be used since we quit, but needed for type safety.
	return {"success": true, "message": "Quitting"}


func _cmd_list_commands(_args: Dictionary) -> Dictionary:
	var actions: Array = _handlers.keys()
	actions.sort()
	return {
		"success": true,
		"message": "%d commands registered" % actions.size(),
		"data": {"actions": actions},
	}


## Reports which harness revision the installed files came from. Without this, deciding
## whether a refresh was a no-op or a real upgrade meant diffing template files against
## the plugin repo by hand, and a gap logged before an upgrade could not be told apart
## from a regression after one.
##
## Wire contract - data keys: harness_version (String), handlers (int),
## extension_loaded (bool), config_path (String), session (String),
## commands_path (String).
func _cmd_harness_version(_args: Dictionary) -> Dictionary:
	return {
		"success": true,
		"message": "harness %s" % HARNESS_VERSION,
		"data": {
			"harness_version": HARNESS_VERSION,
			"handlers": _handlers.size(),
			"extension_loaded": _extension != null,
			"config_path": CONFIG_PATH,
			"session": _session,
			"commands_path": _commands_abs_path,
		},
	}


# --- Node Lifecycle Handlers ---

func _cmd_clear_nodes(args: Dictionary) -> Dictionary:
	var scene: Node = get_tree().current_scene
	if scene == null:
		return {"success": false, "message": "No current scene"}

	var group: String = args.get("group", "")
	var method: String = args.get("method", "")
	var cls: String = args.get("class", "")

	if group.is_empty() and method.is_empty() and cls.is_empty():
		return {
			"success": false,
			"message": "No selector provided. Supply exactly one of: group, method, or class (refusing to free the whole tree).",
		}
	if not cls.is_empty() and not _class_is_known(cls):
		return {
			"success": false,
			"message": "Unknown class '%s': not an engine class and no script declares class_name %s - cleared nothing" % [cls, cls],
		}

	var matches: Array[Node] = []
	_collect_matching_nodes(scene, group, method, cls, matches)

	# `via_method` calls the game's own removal path instead of queue_free().
	#
	# queue_free() is always the WRONG way to remove a node whose removal has game
	# meaning: freeing an enemy skips its death path, so nothing drops, no xp is
	# paid, the run counter never increments and the boss's own `died` connection
	# never fires -- a test built on clear_nodes proves the nodes are gone and
	# nothing else (gather:G-123). Naming a method keeps this verb generic: the
	# harness still knows nothing about health or damage, only that the project has
	# a method it would rather have called.
	var via: String = args.get("via_method", "")
	var via_args: Array = args.get("via_args", []) if args.get("via_args") is Array else []

	var cleared: int = 0
	var skipped: Array = []
	for node: Node in matches:
		if via.is_empty():
			node.queue_free()
			cleared += 1
			continue
		if not node.has_method(via):
			skipped.append(str(node.get_path()))
			continue
		node.callv(via, via_args)
		cleared += 1

	var how: String = "queue_free()" if via.is_empty() else "%s()" % via
	var message: String = "Cleared %d nodes via %s (group='%s', method='%s', class='%s')" % [
		cleared, how, group, method, cls]
	if not skipped.is_empty():
		message += " -- %d matched but have no %s(): %s" % [
			skipped.size(), via, ", ".join(PackedStringArray(skipped))]

	return {
		"success": skipped.is_empty(),
		"message": message,
		"data": {"count": cleared, "via": via, "skipped": skipped},
	}


func _collect_matching_nodes(node: Node, group: String, method: String, cls: String, matches: Array[Node]) -> void:
	for child: Node in node.get_children():
		if _node_matches(child, group, method, cls):
			matches.append(child)
		_collect_matching_nodes(child, group, method, cls, matches)


func _collect_all_nodes(node: Node, out: Array[Node]) -> void:
	for child: Node in node.get_children():
		out.append(child)
		_collect_all_nodes(child, out)


func _node_matches(node: Node, group: String, method: String, cls: String) -> bool:
	if not group.is_empty() and node.is_in_group(group):
		return true
	if not method.is_empty() and node.has_method(method):
		return true
	if not cls.is_empty() and _node_is_class(node, cls):
		return true
	return false


## `is_class()` and `get_class()` know only engine classes, so `--class Pest`
## against six live nodes whose script declares `class_name Pest` matched
## nothing -- they report `type: Node2D` -- and the empty result then blamed the
## `--where` predicate (gh#15.2). A script class is matched by walking the
## node's script and its base-script chain comparing `get_global_name()`, so
## `--class Plant` also finds every subclass, which is the natural reading.
func _node_is_class(node: Node, cls: String) -> bool:
	if node.is_class(cls) or node.get_class() == cls:
		return true
	var script: Variant = node.get_script()
	while script is Script:
		var sc: Script = script as Script
		if String(sc.get_global_name()) == cls:
			return true
		script = sc.get_base_script()
	return false


## Whether `cls` names anything at all: an engine class or a script that
## declares `class_name cls`. A `--class` value that names neither is a typo,
## and must be reported as one rather than as a clean zero-match.
func _class_is_known(cls: String) -> bool:
	if ClassDB.class_exists(cls):
		return true
	for entry: Dictionary in ProjectSettings.get_global_class_list():
		if String(entry.get("class", "")) == cls:
			return true
	return false


# --- Input Simulation Handlers ---

## Input.action_press/action_release only update the polled action state; they never
## dispatch an InputEvent, so game code driven by _input/_unhandled_input (event-based
## handlers) would not see simulated input. Dispatch a matching InputEventAction too.
func _dispatch_action_event(action: String, pressed: bool, strength: float = 1.0) -> void:
	var ev := InputEventAction.new()
	ev.action = action
	ev.pressed = pressed
	ev.strength = strength
	Input.parse_input_event(ev)


func _cmd_input_press(args: Dictionary) -> Dictionary:
	var action: String = args.get("action", "")
	if action.is_empty():
		return {"success": false, "message": "No action specified"}

	if not InputMap.has_action(action):
		return {"success": false, "message": "Unknown input action: %s" % action}

	var strength: float = args.get("strength", 1.0)
	Input.action_press(action, strength)
	_dispatch_action_event(action, true, strength)
	if action not in _active_simulated_inputs:
		_active_simulated_inputs.append(action)

	return {
		"success": true,
		"message": "Pressed: %s" % action,
		"data": {"action": action, "strength": strength, "active_inputs": _active_simulated_inputs.duplicate()},
	}


func _cmd_input_release(args: Dictionary) -> Dictionary:
	var action: String = args.get("action", "")
	if action.is_empty():
		return {"success": false, "message": "No action specified"}

	if not InputMap.has_action(action):
		return {"success": false, "message": "Unknown input action: %s" % action}

	Input.action_release(action)
	_dispatch_action_event(action, false)
	_active_simulated_inputs.erase(action)

	return {
		"success": true,
		"message": "Released: %s" % action,
		"data": {"action": action, "active_inputs": _active_simulated_inputs.duplicate()},
	}


## Press-and-release in one verb. The release is dispatched on the FRAME AFTER
## the press (or after `seconds`/`hold` of game time), never inside the same
## frame (G-067): `is_action_just_pressed` and release-triggered handlers only
## fire when the two events land on different frames, so a same-frame tap tested
## nothing for that whole class of game code. The reply now comes back AFTER the
## release, so a release-driven state change is observable on the very next read.
##
## data.pressed_during / data.pressed_after report the action's polled state
## right before and right after the release (G-041): Viewport.is_input_handled()
## is not reliably readable across the buffered dispatch, so the post-tap pressed
## state is the honest best-effort signal that the tap registered and cleared.
func _cmd_input_tap(args: Dictionary) -> Dictionary:
	var action: String = args.get("action", "")
	if action.is_empty():
		return {"success": false, "message": "No action specified"}

	if not InputMap.has_action(action):
		return {"success": false, "message": "Unknown input action: %s" % action}

	var hold: float = args.get("seconds", args.get("hold", 0.0))
	var strength: float = args.get("strength", 1.0)

	Input.action_press(action, strength)
	_dispatch_action_event(action, true, strength)
	if action not in _active_simulated_inputs:
		_active_simulated_inputs.append(action)

	if hold > 0.0:
		await get_tree().create_timer(maxf(hold, 0.0)).timeout
	else:
		await get_tree().process_frame

	var pressed_during: bool = Input.is_action_pressed(action)
	Input.action_release(action)
	_dispatch_action_event(action, false)
	_active_simulated_inputs.erase(action)
	await get_tree().process_frame
	var pressed_after: bool = Input.is_action_pressed(action)

	return {
		"success": true,
		"message": "Tapped: %s (hold %.2fs, released on a later frame)" % [action, hold],
		"data": {
			"action": action,
			"hold": hold,
			"strength": strength,
			"pressed_during": pressed_during,
			"pressed_after": pressed_after,
		},
	}


func _cmd_input_clear(_args: Dictionary) -> Dictionary:
	var cleared: Array[String] = _clear_all_simulated_inputs()
	var released: Array = _clear_all_simulated_touches()
	return {
		"success": true,
		"message": "Cleared %d simulated inputs and released %d touches" % [cleared.size(), released.size()],
		"data": {"cleared": cleared, "touches_released": released},
	}


## Dispatches a RAW keyboard event by OS keycode name (G-049): action simulation
## cannot reach game code that reads `InputEventKey.keycode` directly (rebindable
## controls, debug keys, text fields). args:
##   { "key": String (e.g. "E", "LEFT", "SPACE"), "count": int = 1,
##     "hold_frames": int = 0 }.
## Both `keycode` AND `physical_keycode` are set on the event, so code matching
## either sees it. The release always lands on a later frame than the press
## (after `hold_frames` frames when > 0), for the same reason as input_tap.
func _cmd_input_key(args: Dictionary) -> Dictionary:
	var key_name: String = str(args.get("key", ""))
	if key_name.is_empty():
		return {"success": false, "message": "No key specified. Pass e.g. {\"key\": \"E\"}."}

	# OS.find_keycode_from_string is picky about casing ("Space", not "SPACE"),
	# so try the obvious spellings before failing.
	var keycode: Key = KEY_NONE
	for candidate: String in [key_name, key_name.capitalize(), key_name.to_upper()]:
		keycode = OS.find_keycode_from_string(candidate)
		if keycode != KEY_NONE:
			break
	if keycode == KEY_NONE:
		return {"success": false, "message": "Unknown key name: %s (OS.find_keycode_from_string found nothing)" % key_name}

	var count: int = clampi(int(args.get("count", 1)), 1, 100)
	var hold_frames: int = clampi(int(args.get("hold_frames", 0)), 0, 600)

	for tap: int in range(count):
		var press := InputEventKey.new()
		press.keycode = keycode
		press.physical_keycode = keycode
		press.pressed = true
		Input.parse_input_event(press)

		# Release on a later frame -- a same-frame press+release is invisible to
		# just_pressed/just_released style handlers (G-067).
		for _f: int in range(maxi(1, hold_frames)):
			await get_tree().process_frame

		var release := InputEventKey.new()
		release.keycode = keycode
		release.physical_keycode = keycode
		release.pressed = false
		Input.parse_input_event(release)

		if tap < count - 1:
			await get_tree().process_frame

	return {
		"success": true,
		"message": "Key %s (keycode %d) tapped %d time%s" % [
			OS.get_keycode_string(keycode), keycode, count, "" if count == 1 else "s",
		],
		"data": {
			"key": key_name,
			"keycode": keycode,
			"keycode_string": OS.get_keycode_string(keycode),
			"count": count,
			"hold_frames": hold_frames,
		},
	}


## Dispatches a real InputEventMouseMotion (moving-in:G-029). Mouse-look is the
## one input a first-person game cannot be tested without, and the bridge could
## drive actions, raw keys and touch but never produce a `relative` - so a
## camera that reads InputEventMouseMotion.relative was unreachable, and
## `run-method _unhandled_input` with a JSON event was silently a no-op (the arg
## cannot become an object). Goes through Input.parse_input_event like `key`, so
## every _input/_unhandled_input in the tree sees it, captured cursor or not.
## args: { "relative": [dx, dy], "steps": int = 1 (split the motion into this
##         many events, one per frame - a look handler that clamps per event sees
##         them individually), "position": [x, y] (absolute cursor position to
##         report; defaults to the current one), "buttons": int (button mask) }
## data keys: relative {x,y}, steps, position {x,y}, mouse_mode.
func _cmd_mouse_move(args: Dictionary) -> Dictionary:
	var rel: Variant = _parse_vector2_or_null(args.get("relative"))
	if rel == null:
		return {"success": false, "message": "mouse_move needs relative as [dx, dy]"}
	var steps: int = clampi(int(args.get("steps", 1)), 1, 600)
	var pos_raw: Variant = _parse_vector2_or_null(args.get("position"))
	var pos: Vector2 = pos_raw if pos_raw != null else get_viewport().get_mouse_position()
	var buttons: int = int(args.get("buttons", 0))
	var per_step: Vector2 = (rel as Vector2) / float(steps)
	for i: int in range(steps):
		var ev := InputEventMouseMotion.new()
		ev.relative = per_step
		ev.screen_relative = per_step  # 4.1+; the unscaled twin a Window-scaled game reads
		pos += per_step
		ev.position = pos
		ev.global_position = pos
		ev.button_mask = buttons
		Input.parse_input_event(ev)
		if i < steps - 1:
			await get_tree().process_frame
	# One more frame so the last event has been delivered before the reply, and a
	# get-state issued next reads the moved camera, not the pre-move one.
	await get_tree().process_frame
	var mode_names: Dictionary = {
		Input.MOUSE_MODE_VISIBLE: "visible", Input.MOUSE_MODE_HIDDEN: "hidden",
		Input.MOUSE_MODE_CAPTURED: "captured", Input.MOUSE_MODE_CONFINED: "confined",
		Input.MOUSE_MODE_CONFINED_HIDDEN: "confined_hidden",
	}
	return {
		"success": true,
		"message": "Mouse moved by (%.1f, %.1f) in %d event%s (mouse mode: %s)" % [
			(rel as Vector2).x, (rel as Vector2).y, steps, "" if steps == 1 else "s",
			str(mode_names.get(Input.mouse_mode, str(Input.mouse_mode)))],
		"data": {
			"relative": {"x": (rel as Vector2).x, "y": (rel as Vector2).y},
			"steps": steps,
			"position": {"x": pos.x, "y": pos.y},
			"mouse_mode": str(mode_names.get(Input.mouse_mode, str(Input.mouse_mode))),
		},
	}


## Re-reads a resource from disk INTO the running game (moving-in:G-033). Godot's
## resource cache is keyed on path, so after editing a shader/tres/texture a
## plain load() hands back the copy compiled at startup and the edit looks like a
## no-op - indistinguishable from an edit that genuinely did nothing, and every
## iteration cost a relaunch. CACHE_MODE_REPLACE loads the file and copies it
## over the cached instance, so every node already holding that resource sees
## the new content without being touched. args: { "path": "res://..." }
## data keys: path, resource_class, was_cached, holders_note.
func _cmd_reload(args: Dictionary) -> Dictionary:
	var path: String = str(args.get("path", ""))
	if path.is_empty():
		return {"success": false, "message": "reload needs path (res://...)"}
	if not path.begins_with("res://") and not path.begins_with("user://"):
		path = "res://" + path.trim_prefix("/")
	if not ResourceLoader.exists(path):
		return {"success": false, "message": "reload: no resource at %s (ResourceLoader.exists is false; is the path spelled as the project sees it?)" % path}
	var was_cached: bool = ResourceLoader.has_cached(path)
	# (GDScript has no ResourceCache; a REUSE load of a cached path is the
	# cached instance and reads nothing from disk.)
	var old: Resource = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_REUSE) if was_cached else null
	var res: Resource = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_REPLACE)
	if res == null:
		return {"success": false, "message": "reload: %s exists but failed to load - a parse/compile error in the edited file? see the game's stderr" % path}
	# Text resources are re-parsed INTO the cached object under REPLACE, so
	# holders see the change; binary/shader/texture loaders build a NEW object
	# that merely takes over the path, and every node still holding the old one
	# keeps the old content. Copy the stored properties across in that case, so
	# the verb means the same thing for every resource type.
	var copied: int = 0
	var in_place: bool = old != null and old == res
	if old != null and old != res and old.get_class() == res.get_class():
		for prop: Dictionary in res.get_property_list():
			var pname: String = str(prop.get("name", ""))
			if pname.is_empty() or pname.begins_with("resource_") or pname == "script":
				continue
			if int(prop.get("usage", 0)) & PROPERTY_USAGE_STORAGE == 0:
				continue
			old.set(pname, res.get(pname))
			copied += 1
	var holders_updated: bool = in_place or copied > 0
	return {
		"success": true,
		"message": "Reloaded %s (%s%s)" % [path, res.get_class(),
			(", re-parsed into the cached instance - holders see the new content" if in_place
				else ", %d stored propert%s copied onto the cached instance - holders see the new content" % [copied, "y" if copied == 1 else "ies"])
			if was_cached else ", was not cached: nothing in the running game held it"],
		"data": {
			"path": path,
			"resource_class": res.get_class(),
			"was_cached": was_cached,
			"holders_updated": holders_updated,
			"properties_copied": copied,
		},
	}


## Reads the polled state of input actions (G-021): pressed + strength, so a test
## can assert what the game is CURRENTLY seeing instead of inferring it from
## whatever the last simulated event should have done. args:
##   { "actions": Array of names = [] } -- empty means every project action
## (built-in ui_* actions excluded, exactly as input_actions defaults). Requested
## names that are not in the InputMap come back in data["unknown"] rather than
## being silently dropped.
func _cmd_input_state(args: Dictionary) -> Dictionary:
	var raw_wanted: Variant = args.get("actions", [])
	if not raw_wanted is Array:
		return {"success": false, "message": "args.actions must be an array of action names"}
	var wanted: Array = raw_wanted

	var states: Dictionary = {}
	var unknown: Array = []

	if wanted.is_empty():
		for action: StringName in InputMap.get_actions():
			var action_str: String = str(action)
			if action_str.begins_with("ui_"):
				continue
			states[action_str] = {
				"pressed": Input.is_action_pressed(action_str),
				"strength": Input.get_action_strength(action_str),
			}
	else:
		for entry: Variant in wanted:
			var action_str: String = str(entry)
			if not InputMap.has_action(action_str):
				unknown.append(action_str)
				continue
			states[action_str] = {
				"pressed": Input.is_action_pressed(action_str),
				"strength": Input.get_action_strength(action_str),
			}

	var message: String = "%d action state%s read" % [states.size(), "" if states.size() == 1 else "s"]
	if not unknown.is_empty():
		message += " -- unknown action%s: %s" % [
			"" if unknown.size() == 1 else "s", ", ".join(PackedStringArray(unknown)),
		]

	return {
		"success": true,
		"message": message,
		"data": {"actions": states, "unknown": unknown, "count": states.size()},
	}


# --- Touch Simulation Handlers ---

## Touch has no polled equivalent of Input.action_press() -- InputEventScreenTouch /
## InputEventScreenDrag are event-only -- so a simulated touch must be injected with
## Input.parse_input_event(). Two consequences worth knowing before trusting a result:
##
##  * The game only sees these if it handles InputEventScreenTouch/Drag itself, or if
##    Input.set_emulate_mouse_from_touch() is on (it is, by default) and the game
##    handles the resulting mouse events.
##  * A game that hides its touch UI behind DisplayServer.is_touchscreen_available()
##    will not be showing that UI on a desktop run. Call `set_feature` with
##    {"touchscreen": true} first -- that is what it is for.
func _dispatch_touch(index: int, position: Vector2, pressed: bool) -> void:
	var ev := InputEventScreenTouch.new()
	ev.index = index
	ev.position = position
	ev.pressed = pressed
	Input.parse_input_event(ev)


func _dispatch_drag(index: int, position: Vector2, relative: Vector2, delta: float) -> void:
	var ev := InputEventScreenDrag.new()
	ev.index = index
	ev.position = position
	ev.relative = relative
	# Velocity is what gesture code reads to decide flick vs. drag, so it has to be
	# derived from a real interval rather than left at zero.
	ev.velocity = relative / maxf(delta, 0.0001)
	Input.parse_input_event(ev)


## Presses a finger down. args: { "index": int = 0, "position": [x, y] }.
## `position` is required for an index that is not already held; for one that is, it
## defaults to that finger's last known position.
func _cmd_touch_press(args: Dictionary) -> Dictionary:
	var index: int = int(args.get("index", 0))
	var position: Vector2 = Vector2.ZERO

	if args.has("position"):
		var parsed: Variant = _parse_vector2_or_null(args["position"])
		if parsed == null:
			return {"success": false, "message": "'position' must be [x, y] or {\"x\": x, \"y\": y}"}
		position = parsed
	elif _active_touches.has(index):
		position = _active_touches[index]
	else:
		return {"success": false, "message": "touch_press on a new index requires 'position' as [x, y]"}

	_dispatch_touch(index, position, true)
	_active_touches[index] = position

	return {
		"success": true,
		"message": "Touch %d pressed at (%.1f, %.1f)" % [index, position.x, position.y],
		"data": {
			"index": index,
			"position": {"x": position.x, "y": position.y},
			"active_touches": _serialize_touches(),
		},
	}


## Lifts a finger. args: { "index": int = 0, "position": [x, y] optional }.
## `position` defaults to the last known position for that index. Releasing an index
## that is not held is allowed (it is the safe direction) and reported via
## data["was_held"].
func _cmd_touch_release(args: Dictionary) -> Dictionary:
	var index: int = int(args.get("index", 0))
	var was_held: bool = _active_touches.has(index)
	var position: Vector2 = _active_touches.get(index, Vector2.ZERO)

	if args.has("position"):
		var parsed: Variant = _parse_vector2_or_null(args["position"])
		if parsed == null:
			return {"success": false, "message": "'position' must be [x, y] or {\"x\": x, \"y\": y}"}
		position = parsed

	_dispatch_touch(index, position, false)
	_active_touches.erase(index)

	return {
		"success": true,
		"message": "Touch %d released at (%.1f, %.1f)%s" % [
			index, position.x, position.y,
			"" if was_held else " (index was not held; released anyway)",
		],
		"data": {
			"index": index,
			"position": {"x": position.x, "y": position.y},
			"was_held": was_held,
			"active_touches": _serialize_touches(),
		},
	}


## Drags a finger. args:
##   { "index": int = 0, "to": [x, y], "from": [x, y] optional, "steps": int = 1 }
##
## The drag starts from `from`, else from that index's tracked position; an index
## that is neither held nor given a `from` is an error rather than a drag from the
## origin. With steps > 1 the events are spread one per process frame -- N drag
## events inside a single frame is not something a real finger produces and gesture
## recognisers can legitimately reject it -- so a multi-step drag takes steps-1
## frames of wall time.
func _cmd_touch_drag(args: Dictionary) -> Dictionary:
	var index: int = int(args.get("index", 0))

	if not args.has("to"):
		return {"success": false, "message": "touch_drag requires 'to' as [x, y]"}
	var to_parsed: Variant = _parse_vector2_or_null(args["to"])
	if to_parsed == null:
		return {"success": false, "message": "'to' must be [x, y] or {\"x\": x, \"y\": y}"}
	var to_pos: Vector2 = to_parsed

	var from_pos: Vector2 = Vector2.ZERO
	if args.has("from"):
		var from_parsed: Variant = _parse_vector2_or_null(args["from"])
		if from_parsed == null:
			return {"success": false, "message": "'from' must be [x, y] or {\"x\": x, \"y\": y}"}
		from_pos = from_parsed
	elif _active_touches.has(index):
		from_pos = _active_touches[index]
	else:
		return {
			"success": false,
			"message": "Touch index %d is not held. Press it first, or pass 'from' as [x, y]." % index,
		}

	var steps: int = maxi(1, int(args.get("steps", 1)))
	var delta: float = maxf(get_process_delta_time(), 0.0001)
	var previous: Vector2 = from_pos
	# Track from the start point, so a touch_clear after a failed run still lifts it.
	_active_touches[index] = from_pos

	for step: int in range(1, steps + 1):
		var point: Vector2 = from_pos.lerp(to_pos, float(step) / float(steps))
		_dispatch_drag(index, point, point - previous, delta)
		previous = point
		_active_touches[index] = point
		if step < steps:
			await get_tree().process_frame
			delta = maxf(get_process_delta_time(), 0.0001)

	return {
		"success": true,
		"message": "Touch %d dragged (%.1f, %.1f) -> (%.1f, %.1f) in %d step%s" % [
			index, from_pos.x, from_pos.y, to_pos.x, to_pos.y, steps, "" if steps == 1 else "s",
		],
		"data": {
			"index": index,
			"from": {"x": from_pos.x, "y": from_pos.y},
			"to": {"x": to_pos.x, "y": to_pos.y},
			"steps": steps,
			"active_touches": _serialize_touches(),
		},
	}


## Lifts every tracked finger. Cheap insurance: a run that ends mid-gesture leaves
## the game holding a touch nothing will ever finish, and the next run inherits it.
func _cmd_touch_clear(_args: Dictionary) -> Dictionary:
	var released: Array = _clear_all_simulated_touches()
	return {
		"success": true,
		"message": "Released %d simulated touches" % released.size(),
		"data": {"released": released},
	}


## Reports the fingers the harness believes are down. This is the harness's own
## tracking, not a query of the OS -- it cannot see touches the harness did not send.
func _cmd_touch_list(_args: Dictionary) -> Dictionary:
	var touches: Array = _serialize_touches()
	return {
		"success": true,
		"message": "%d simulated touch%s held" % [touches.size(), "" if touches.size() == 1 else "es"],
		"data": {
			"touches": touches,
			"count": touches.size(),
			"touchscreen_available": DisplayServer.is_touchscreen_available(),
		},
	}


# --- Engine Feature Flags ---

## Forces engine-level feature flags that a game queries to decide what UI to show.
## args: { "touchscreen": bool }. The dictionary is deliberately open -- unrecognised
## keys are reported back in data["unknown"] rather than rejected, so future flags
## can be added without breaking a client.
##
## {"touchscreen": true} calls Input.set_emulate_touch_from_mouse(true). That IS the
## lever behind DisplayServer.is_touchscreen_available(): verified on Godot 4.7.1
## against both the headless and the real Windows display server, which reported
## false before the call and true after. data["touchscreen_available"] returns the
## live value so the caller can confirm it took effect on their build rather than
## trusting this comment. Two caveats:
##
##  * It is a query, not a signal. A Control that read is_touchscreen_available()
##    once in its own _ready() will not re-evaluate. Set the flag BEFORE the scene
##    that reads it loads, or call that node's own refresh method afterwards.
##  * It is real emulation: while on, mouse input in the window is also delivered as
##    touch events. That is usually what you want for a touch-UI screenshot, but it
##    means the session is no longer a faithful mouse session.
func _cmd_set_feature(args: Dictionary) -> Dictionary:
	# {"query": true} returns the current values WITHOUT writing anything (G-033):
	# there was previously no way to read the flags back, so a test could not
	# tell a leftover override from a fresh session.
	if bool(args.get("query", false)):
		return {
			"success": true,
			"message": "Feature flags (read-only query; nothing was written). Supported: touchscreen (bool).",
			"data": {
				"query": true,
				"touchscreen_available": DisplayServer.is_touchscreen_available(),
				"emulate_touch_from_mouse": Input.is_emulating_touch_from_mouse(),
			},
		}

	var applied: Dictionary = {}
	var unknown: Array = []

	for key: String in args:
		match key:
			"touchscreen":
				var want: bool = bool(args[key])
				Input.set_emulate_touch_from_mouse(want)
				applied["touchscreen"] = want
			"query":
				pass  # Handled above when true; a false query is a no-op key.
			_:
				unknown.append(key)

	var data: Dictionary = {
		"applied": applied,
		"unknown": unknown,
		"touchscreen_available": DisplayServer.is_touchscreen_available(),
		"emulate_touch_from_mouse": Input.is_emulating_touch_from_mouse(),
	}

	if applied.is_empty():
		return {
			"success": false,
			"message": "No known feature flags supplied. Supported: touchscreen (bool).",
			"data": data,
		}

	return {
		"success": true,
		"message": "Applied %s. DisplayServer.is_touchscreen_available() is now %s. UI that read this flag during its own _ready() will not re-evaluate on its own." % [
			str(applied), str(data["touchscreen_available"]),
		],
		"data": data,
	}


func _cmd_input_actions(args: Dictionary) -> Dictionary:
	var include_builtin: bool = args.get("include_builtin", false)
	var actions: Array = []

	for action in InputMap.get_actions():
		var action_str: String = str(action)
		if not include_builtin and action_str.begins_with("ui_"):
			continue

		var events: Array = []
		for event in InputMap.action_get_events(action_str):
			events.append(event.as_text())

		actions.append({
			"name": action_str,
			"events": events,
			"pressed": Input.is_action_pressed(action_str),
		})

	return {
		"success": true,
		"message": "%d actions found" % actions.size(),
		"data": {"actions": actions},
	}


func _cmd_input_sequence(args: Dictionary) -> Dictionary:
	var steps: Variant = args.get("steps", [])
	if not steps is Array or steps.is_empty():
		return {"success": false, "message": "No steps provided or steps is not an array"}

	var timeout: float = args.get("timeout", 30.0)
	var sequence_id: String = str(randi())

	# Validate all steps before executing.
	for i in steps.size():
		var step: Variant = steps[i]
		if not step is Dictionary:
			return {"success": false, "message": "Step %d is not a dictionary" % i}
		var step_dict: Dictionary = step
		var step_type: String = step_dict.get("type", "")
		if step_type.is_empty():
			return {"success": false, "message": "Step %d has no type" % i}
		if step_type not in ["press", "release", "tap", "hold", "wait", "wait_frames", "screenshot", "assert", "clear", "command"]:
			return {"success": false, "message": "Step %d has unknown type: %s" % [i, step_type]}
		# Validate action exists for input steps.
		if step_type in ["press", "release", "tap", "hold"]:
			var action: String = step_dict.get("action", "")
			if action.is_empty():
				return {"success": false, "message": "Step %d (%s) has no action" % [i, step_type]}
			if not InputMap.has_action(action):
				return {"success": false, "message": "Step %d: unknown action: %s" % [i, action]}

	# Launch the async sequence.
	_execute_sequence(sequence_id, steps as Array, timeout)

	return {
		"success": true,
		"message": "Sequence %s started with %d steps" % [sequence_id, steps.size()],
		"data": {"sequence_id": sequence_id, "step_count": steps.size()},
	}


func _execute_sequence(sequence_id: String, steps: Array, timeout: float) -> void:
	var start_time: float = Time.get_unix_time_from_system()

	for i in steps.size():
		if Time.get_unix_time_from_system() - start_time > timeout:
			_write_log("input", "Sequence %s timed out at step %d" % [sequence_id, i])
			return

		var step: Dictionary = steps[i]
		match step["type"]:
			"press":
				var action: String = step["action"]
				var strength: float = step.get("strength", 1.0)
				Input.action_press(action, strength)
				_dispatch_action_event(action, true, strength)
				_active_simulated_inputs.append(action)

			"release":
				var action: String = step["action"]
				Input.action_release(action)
				_dispatch_action_event(action, false)
				_active_simulated_inputs.erase(action)

			"tap":
				var action: String = step["action"]
				var hold: float = step.get("seconds", step.get("hold", 0.0))
				var strength: float = step.get("strength", 1.0)
				Input.action_press(action, strength)
				_dispatch_action_event(action, true, strength)
				_active_simulated_inputs.append(action)
				await get_tree().create_timer(maxf(hold, get_process_delta_time())).timeout
				Input.action_release(action)
				_dispatch_action_event(action, false)
				_active_simulated_inputs.erase(action)

			"hold":
				var action: String = step["action"]
				var strength: float = step.get("strength", 1.0)
				Input.action_press(action, strength)
				_dispatch_action_event(action, true, strength)
				_active_simulated_inputs.append(action)
				await get_tree().create_timer(step["seconds"]).timeout
				Input.action_release(action)
				_dispatch_action_event(action, false)
				_active_simulated_inputs.erase(action)

			"wait":
				await get_tree().create_timer(step["seconds"]).timeout

			"screenshot":
				var filename: String = step.get("filename", "seq_%s_%d.png" % [sequence_id, i])
				_cmd_screenshot({"filename": filename})

			"assert":
				var target: Node = get_node_or_null(step["node"])
				if target == null:
					_write_log("input", "Sequence %s assert failed: node not found %s" % [sequence_id, step["node"]])
					return
				var actual: Variant = target.get(step["property"])
				var expected: Variant = step["equals"]
				var values_match: bool = false
				if typeof(actual) in [TYPE_INT, TYPE_FLOAT] and typeof(expected) in [TYPE_INT, TYPE_FLOAT]:
					values_match = is_equal_approx(float(actual), float(expected))
				else:
					values_match = str(actual) == str(expected)
				if not values_match:
					_write_log("input", "Sequence %s assert failed: %s.%s = %s, expected %s" % [
						sequence_id, step["node"], step["property"], str(actual), str(expected)
					])
					return

			"wait_frames":
				var count: int = step.get("frames", step.get("count", 1))
				for _f: int in range(count):
					await get_tree().process_frame

			"command":
				var cmd_name: String = step.get("name", "")
				if cmd_name.is_empty():
					_write_log("input", "Sequence %s step %d: command has no name" % [sequence_id, i])
					return
				var cmd_args: Dictionary = {}
				for key: String in step:
					if key not in ["type", "name", "comment"]:
						cmd_args[key] = step[key]
				# Dispatch through the registry so project-registered verbs work too.
				var action_key: String = cmd_name.replace("-", "_")
				if not _handlers.has(action_key):
					_write_log("input", "Sequence %s step %d: unknown command '%s'" % [sequence_id, i, cmd_name])
					return
				var handler: Callable = _handlers[action_key]
				var result: Variant = await handler.call(cmd_args)
				# Handlers report failure via the "success" bool (not a "status" field).
				if result is Dictionary and not (result as Dictionary).get("success", false):
					_write_log("input", "Sequence %s step %d: command '%s' failed: %s" % [
						sequence_id, i, cmd_name, (result as Dictionary).get("message", "")
					])
					return

			"clear":
				_clear_all_simulated_inputs()

	_write_log("input", "Sequence %s completed (%d steps)" % [sequence_id, steps.size()])


# --- Time Control Handlers ---

func _cmd_set_game_speed(args: Dictionary) -> Dictionary:
	var prev: float = Engine.time_scale
	var requested: float = float(args.get("scale", 1.0))
	# A time scale of 0 stops the game dead while the bus keeps answering, so
	# every later read returns well-formed, identical, stale values - "a run
	# that never changes" arrived at from a verb that reported success
	# (moving-in:G-019: 0.04 was echoed as `1.0 -> 0.0` by a 1-dp print and the
	# session chased a freeze). Pausing is what `ping` reports and the tree's
	# own pause is for; this verb refuses zero and names its floor.
	if requested < MIN_GAME_SPEED:
		return {
			"success": false,
			"message": "set_game_speed refuses %.4f: a scale below %.3f freezes the game while the bus keeps answering (use the 'pause' verb for that); smallest accepted is %.3f" % [
				requested, MIN_GAME_SPEED, MIN_GAME_SPEED],
			"data": {"previous_scale": prev, "current_scale": prev, "min_scale": MIN_GAME_SPEED},
		}
	var scale: float = clampf(requested, MIN_GAME_SPEED, 100.0)
	Engine.time_scale = scale
	# Remembered so `performance` can report a leftover override (G-059).
	_devtools_set_speed = scale

	return {
		"success": true,
		"message": "Game speed: %.3f -> %.3f" % [prev, scale],
		"data": {"previous_scale": prev, "current_scale": scale},
	}


## Pauses SceneTree.paused directly (gh#26) - the thing `set_game_speed`'s own
## refusal message names ("use the tree's pause for that") but that nothing in
## the generic verb set implemented until now. This bridge autoload already
## runs PROCESS_MODE_ALWAYS (see _ready()), so it keeps answering on a paused
## tree - `ping` already reports `paused` for exactly this reason. That means a
## caller racing the bus round-trip against a sub-second effect (a fade, a
## hit-flash, a cooldown tween) can drive the trigger, poll for the moment it
## wants, pause the instant it is caught, and then take as long as it likes over
## sample-pixels/screenshot/node-bounds without racing anything - `unpause`
## resumes. Idempotent: pausing an already-paused tree is not an error.
func _cmd_pause(_args: Dictionary) -> Dictionary:
	var was_paused: bool = get_tree().paused
	get_tree().paused = true
	# moving-in:G-061: a paused tree is not a frozen one. Any node the project set
	# to PROCESS_MODE_ALWAYS keeps processing - and a HUD, where ALWAYS is
	# mandatory or the pause menu cannot draw itself, is exactly what a caller
	# pauses to measure. `RewardCard` read visible:true then visible:false one
	# command later on a tree `ping` insisted was paused, and it read as a bus
	# fault. Say it in the reply, at the moment it matters, and name the way
	# round it (set-game-speed 0.01 slows those nodes too; pause does not).
	var always_roots: Array = []
	var always_count: int = _count_process_always(get_tree().root, false, always_roots)
	var msg: String = "Tree paused" if not was_paused else "Tree was already paused"
	if always_count > 0:
		var shown: Array = always_roots.slice(0, 5)
		msg += "; %d node(s) are PROCESS_MODE_ALWAYS (%d set explicitly: %s%s) and keep processing on a paused tree - `set-game-speed 0.01` slows them where pause cannot" % [
			always_count, always_roots.size(), ", ".join(shown),
			", ..." if always_roots.size() > 5 else ""]
	# gh#51 / plant-tower-defense:G-061 (0.55.0): the converse asymmetry. A paused
	# tree does not call _process, so a CanvasItem that repaints from _process
	# (`func _process(_d): queue_redraw()`) keeps its LAST frame however much the
	# state under it changes - a screenshot after `pause` + `run_method` showed the
	# pre-pause frame and read as a failed method, a wrong region or a dead bus.
	msg += "; canvas: a CanvasItem that repaints from _process keeps its last frame while paused - after changing state under pause, `repaint [--node PATH]` calls queue_redraw() so the next frame reflects it"
	return {
		"success": true,
		"message": msg,
		"data": {"was_paused": was_paused, "paused": true,
			"always_count": always_count, "always_roots": always_roots,
			"canvas_repaint": "frozen_for_process_driven_redraw"},
	}


## Calls queue_redraw() on a node and every CanvasItem under it (gh#51 /
## plant-tower-defense:G-061, 0.55.0). A paused tree does not run _process, so a
## _draw()-based cue whose owner repaints from _process keeps its last frame no
## matter what `run_method` changed under it; the workaround needed the node's
## source read first (which node draws it, and whether it draws from _process or
## an explicit call), and neither is discoverable from the bus. This is the one
## command that makes `pause` + mutate + `screenshot` honest for such cues.
## args: { "node_path": String (default the tree root) }
## data: { "node_path", "touched": int (CanvasItems queued), "paused": bool }
func _cmd_repaint(args: Dictionary) -> Dictionary:
	var node: Node = get_tree().root
	var path: String = str(args.get("node_path", ""))
	if not path.is_empty():
		node = get_tree().root.get_node_or_null(NodePath(path))
		if node == null:
			return {"success": false, "message": "Node not found: %s" % path}
	var touched: int = _queue_redraw_recursive(node)
	return {
		"success": true,
		"message": "repaint: queued redraw on %d CanvasItem(s) under %s%s" % [
			touched, node.get_path(),
			" (tree is paused - the next rendered frame reflects the current state; a screenshot right after reads it)" if get_tree().paused else ""],
		"data": {"node_path": str(node.get_path()), "touched": touched, "paused": get_tree().paused},
	}


func _queue_redraw_recursive(node: Node) -> int:
	var n: int = 0
	if node == self:
		return 0
	if node is CanvasItem:
		(node as CanvasItem).queue_redraw()
		n += 1
	for child: Node in node.get_children():
		n += _queue_redraw_recursive(child)
	return n


## Nodes that keep processing while the tree is paused: explicit
## PROCESS_MODE_ALWAYS plus everything inheriting from one. Skips the bridge
## itself (it is ALWAYS by design). Returns the effective count; `roots` collects
## the paths that set it explicitly.
func _count_process_always(node: Node, inherited_always: bool, roots: Array) -> int:
	if node == self:
		return 0
	var effective: bool = inherited_always
	match node.process_mode:
		Node.PROCESS_MODE_ALWAYS:
			effective = true
			roots.append(str(node.get_path()))
		Node.PROCESS_MODE_PAUSABLE, Node.PROCESS_MODE_DISABLED, Node.PROCESS_MODE_WHEN_PAUSED:
			effective = false
		_:
			pass  # INHERIT keeps the parent's answer
	var n: int = 1 if effective else 0
	for child: Node in node.get_children():
		n += _count_process_always(child, effective, roots)
	return n


## Reverses _cmd_pause. Idempotent: unpausing an already-running tree is not an error.
func _cmd_unpause(_args: Dictionary) -> Dictionary:
	var was_paused: bool = get_tree().paused
	get_tree().paused = false
	return {
		"success": true,
		"message": "Tree unpaused" if was_paused else "Tree was already unpaused",
		"data": {"was_paused": was_paused, "paused": false},
	}


## Advances the running game by (approximately) `seconds` of game time, so a tween or
## animation can be sampled at a chosen moment instead of wherever a real-time sleep
## happened to land. args: { "seconds": float }.
##
## READ THE LIMITS BEFORE TRUSTING A SAMPLE TAKEN THIS WAY.
##
##  * There is no manual tick. GDScript cannot drive SceneTree iterations, so there
##    is no "advance the world by exactly N seconds and stop" primitive to build on.
##    This verb runs the game at normal speed and returns once enough time has
##    passed. The tree is NOT paused and NOT stepped, despite what the name suggests.
##  * Engine.time_scale is pinned to 1.0 for the duration and restored afterwards, so
##    a leftover `set_game_speed 0.05` cannot silently stretch the interval. That
##    pinning is the part that makes repeated samples comparable.
##  * PHYSICS IS EXACT. Physics deltas are fixed at 1/physics_ticks_per_second, so
##    advancing N physics frames advances physics-driven state by exactly
##    N/physics_ticks_per_second seconds. data["physics_seconds"] is that number and
##    it is the one to trust. A Tween created with TWEEN_PROCESS_PHYSICS, or an
##    AnimationPlayer set to ANIMATION_PROCESS_PHYSICS, lands on it.
##  * PROCESS IS NOT EXACT. Tween defaults to TWEEN_PROCESS_IDLE and AnimationPlayer
##    to ANIMATION_PROCESS_IDLE; both advance by the real frame delta, which is
##    wall-clock and jittery. This verb therefore also accumulates the process deltas
##    it actually observed and reports them as data["process_seconds"]. The sample
##    lands within roughly one frame (~16 ms at 60 FPS) of the requested moment, not
##    on it. Compare process_seconds against seconds_requested rather than assuming
##    they match; for a tighter sample, drive the tween off physics.
##  * A paused tree still emits process/physics frames while nothing in the game
##    advances, so this would happily "step" a frozen game. data["tree_paused"] says
##    whether that happened -- check it before concluding the animation did nothing.
##  * The loop has a frame budget so a starved or stalled tree cannot wedge the
##    single-in-flight command bus. data["budget_exhausted"] reports it and the
##    command returns success:false.
func _cmd_step_time(args: Dictionary) -> Dictionary:
	var seconds: float = float(args.get("seconds", 0.0))
	if seconds <= 0.0:
		return {"success": false, "message": "step_time requires a positive 'seconds' value"}
	if seconds > STEP_TIME_MAX_SECONDS:
		return {
			"success": false,
			"message": "step_time refuses %.3fs; the maximum is %.1fs (the bridge serves one command at a time)" % [
				seconds, STEP_TIME_MAX_SECONDS,
			],
		}

	# Optional held action (G-084): "step 2s while move_left is down" used to need
	# an input_press, a step, and an input_release -- three bus round-trips during
	# which nothing guaranteed the press survived. The action is re-asserted
	# pressed on every stepped frame and released at the end.
	var hold_action: String = str(args.get("hold", ""))
	if not hold_action.is_empty() and not InputMap.has_action(hold_action):
		return {"success": false, "message": "Unknown input action for 'hold': %s" % hold_action}

	# plant-tower-defense:G-016: every step + read pair used to cost unbounded
	# ambient game time on top of the seconds requested, because the tree keeps
	# running between the reply and the next command. `then_pause` freezes the
	# tree the moment the step completes (and lifts a pre-existing pause for the
	# step itself), so a short-lived state can be stepped INTO and then read at
	# leisure. Nothing here is a manual tick -- see the limits above -- it only
	# closes the gap AFTER the step. `unpause` (0.25.0) resumes.
	var then_pause: bool = bool(args.get("then_pause", false))
	var was_paused_before: bool = get_tree().paused
	if then_pause and was_paused_before:
		get_tree().paused = false

	var ticks_per_second: int = Engine.physics_ticks_per_second
	var target_physics_frames: int = ceili(seconds * float(ticks_per_second))
	var previous_scale: float = Engine.time_scale
	Engine.time_scale = 1.0

	var start_physics_frames: int = int(Engine.get_physics_frames())
	var start_process_frames: int = int(Engine.get_process_frames())
	var start_msec: int = Time.get_ticks_msec()
	var process_seconds: float = 0.0
	# What the request should need, plus generous headroom for a loop running below
	# real time. Purely a stuck-loop guard, not a pacing mechanism.
	var frame_budget: int = target_physics_frames * 4 + 240
	var frames_spent: int = 0
	var budget_exhausted: bool = false

	if not hold_action.is_empty():
		Input.action_press(hold_action)
		_dispatch_action_event(hold_action, true)
		if hold_action not in _active_simulated_inputs:
			_active_simulated_inputs.append(hold_action)

	while true:
		var physics_advanced: int = int(Engine.get_physics_frames()) - start_physics_frames
		# Wait for BOTH clocks: the physics one so physics-driven state advances the
		# full requested amount, the process one so idle tweens do too.
		if physics_advanced >= target_physics_frames and process_seconds >= seconds:
			break
		if frames_spent >= frame_budget:
			budget_exhausted = true
			break
		await get_tree().process_frame
		frames_spent += 1
		process_seconds += get_process_delta_time()
		if not hold_action.is_empty():
			# Re-asserted every frame so nothing (an input_clear, game code
			# calling action_release) can silently drop the hold mid-step.
			Input.action_press(hold_action)

	if not hold_action.is_empty():
		Input.action_release(hold_action)
		_dispatch_action_event(hold_action, false)
		_active_simulated_inputs.erase(hold_action)

	var physics_frames: int = int(Engine.get_physics_frames()) - start_physics_frames
	var process_frames: int = int(Engine.get_process_frames()) - start_process_frames
	var elapsed_ms: int = Time.get_ticks_msec() - start_msec
	var tree_paused: bool = get_tree().paused
	Engine.time_scale = previous_scale
	if then_pause:
		get_tree().paused = true

	var data: Dictionary = {
		"seconds_requested": seconds,
		"frames_advanced": physics_frames,
		"physics_frames": physics_frames,
		"target_physics_frames": target_physics_frames,
		"physics_seconds": float(physics_frames) / float(maxi(1, ticks_per_second)),
		"physics_ticks_per_second": ticks_per_second,
		"process_frames": process_frames,
		"process_seconds": process_seconds,
		"elapsed_wall_ms": elapsed_ms,
		"previous_time_scale": previous_scale,
		"restored_time_scale": Engine.time_scale,
		"tree_paused": tree_paused,
		"paused_after": get_tree().paused,
		"was_paused_before": was_paused_before,
		"budget_exhausted": budget_exhausted,
		"held_action": hold_action if not hold_action.is_empty() else null,
	}

	var message: String = "Advanced %.4fs of physics time (%d frames @ %d Hz, exact) and %.4fs of process time (approximate, +/- one frame); requested %.4fs. time_scale pinned to 1.0 and restored to %.3f." % [
		data["physics_seconds"], physics_frames, ticks_per_second, process_seconds, seconds, previous_scale,
	]
	if tree_paused:
		message += " WARNING: get_tree().paused is true, so node processing did not actually advance."
	if then_pause:
		message += " Tree left PAUSED (then_pause); `unpause` resumes it."
	if budget_exhausted:
		message += " WARNING: frame budget exhausted before the target was reached -- the loop is starved."

	return {
		"success": not budget_exhausted,
		"message": message,
		"data": data,
	}


func _cmd_wait_frames(args: Dictionary) -> Dictionary:
	var count: int = int(args.get("count", 1))
	var start_time := Time.get_ticks_msec()
	for i in range(count):
		await get_tree().process_frame
	var elapsed_ms := Time.get_ticks_msec() - start_time

	return {
		"success": true,
		"message": "Waited %d frames" % count,
		"data": {"frames": count, "elapsed_ms": elapsed_ms},
	}


# --- UI Validation Helpers ---

func _get_effective_alpha(node: Node) -> float:
	var alpha: float = 1.0
	var current: Node = node
	while current != null:
		if current is CanvasItem:
			alpha *= current.modulate.a * current.self_modulate.a
		if current is CanvasLayer:
			break
		current = current.get_parent()
	return alpha


func _is_effectively_visible(node: Node) -> bool:
	var current: Node = node
	while current != null:
		if current is CanvasItem and not current.visible:
			return false
		if current is CanvasLayer:
			# The layer's own `visible` counts (gh#5 sweep): a pause menu whose
			# CanvasLayer is hidden was reported as visible, so its buttons were
			# "reachable" and its panels were laid out for checks nobody could see.
			# The walk stops here either way -- a CanvasLayer renders independently
			# of any CanvasItem above it.
			return (current as CanvasLayer).visible
		current = current.get_parent()
	return true


func _get_control_text(node: Control) -> String:
	if node is Label:
		return node.text
	if node is Button:
		return node.text
	if node is RichTextLabel:
		return node.get_parsed_text()
	return ""


## Finds the HUD CanvasLayer by the configured `hud_layer_name`, falling back to
## the first CanvasLayer found (in the current scene, then under the root).
## Provided for extensions that need to reach the HUD generically.
func _find_hud_node() -> Node:
	var hud_name: String = _config.get("hud_layer_name", "HUD")
	var scene: Node = get_tree().current_scene
	var first_layer: Node = null

	if scene != null:
		for child in scene.get_children():
			if child is CanvasLayer:
				if child.name == hud_name:
					return child
				if first_layer == null:
					first_layer = child

	for child in get_tree().root.get_children():
		if child is CanvasLayer:
			if child.name == hud_name:
				return child
			if first_layer == null:
				first_layer = child

	return first_layer


## The rectangle every screen-position check measures against, in the same units
## _screen_rect_of() returns.
##
## NOT get_tree().root.size (gh#2). Two things are wrong with it:
##  * It is window pixels. Under stretch/mode=canvas_items the root viewport is
##    content_scale_size and the stretch to the window happens after, so a rect
##    already in viewport units was being compared against a different scale.
##  * Headless has no window at all, so root.size is 64x64 and EVERY Control
##    wider than 64px "extends past viewport". check_templates.py drives the
##    bridge headless, which is exactly why its UI stages never caught this.
## get_visible_rect() answers the first. The project's designed viewport size
## answers the second -- it is what a windowed run of the same project reports.
func _screen_reference_rect() -> Rect2:
	var vp: Viewport = get_viewport()
	var rect: Rect2 = vp.get_visible_rect() if vp != null else Rect2()
	if rect.size.x >= 2.0 and rect.size.y >= 2.0 and DisplayServer.get_name() != "headless":
		return rect
	var designed: Vector2 = Vector2(
		float(ProjectSettings.get_setting("display/window/size/viewport_width", 0)),
		float(ProjectSettings.get_setting("display/window/size/viewport_height", 0)))
	if designed.x < 2.0 or designed.y < 2.0:
		return rect
	return Rect2(Vector2.ZERO, designed)


## Why a screen-space rect measured HEADLESS must not be read as what a player
## sees (H-051). Empty string when the geometry is the real thing.
##
## Headless has no window: get_window().size is 64x64 and window_get_size() is
## 0x0, whatever the project designed. Under a stretch mode the root viewport
## still lays anchored Controls out against the designed size, so most geometry
## is right -- but anything the game positions FROM the window size lands where
## no player ever sees it. Reproduced: a panel centred with
## `(get_window().size - size) / 2` on an 800x600 design sits at (-368,-268)
## headless and at (0,0) windowed. The verb reported the headless number
## faithfully, the caller read it as the player's screen, and a "whole end-of-run
## screen off-viewport" defect reached a published report before a windowed
## screenshot overturned it. So every verb that returns screen geometry carries
## this alongside the number, and the client prints it next to any off-viewport
## verdict, because a well-formed rect that is not measuring what the reader
## thinks is the failure this whole file is written against.
func _geometry_caveat() -> String:
	if DisplayServer.get_name() != "headless":
		return ""
	var designed: Vector2 = Vector2(
		float(ProjectSettings.get_setting("display/window/size/viewport_width", 0)),
		float(ProjectSettings.get_setting("display/window/size/viewport_height", 0)))
	var win: Vector2i = get_window().size if get_window() != null else Vector2i.ZERO
	return ("measured HEADLESS: the window is %dx%d, not the designed %dx%d (stretch mode '%s'). "
		+ "Anchored/viewport-relative layout is unaffected, but anything the game positions from "
		+ "the window size (get_window().size, DisplayServer.window_get_size) lands elsewhere than "
		+ "a player sees. Confirm any off-viewport verdict windowed before reporting it.") % [
		win.x, win.y, int(designed.x), int(designed.y),
		str(ProjectSettings.get_setting("display/window/stretch/mode", "disabled"))]


## Where a Control actually lands on screen.
##
## Control.get_global_rect() stops at the CanvasLayer, so a HUD built on a layer
## with a scale -- an ordinary way to get resolution independence -- reports rects
## in layer units while the viewport is measured in pixels, and every
## right/bottom-anchored Control reads as overflowing (gh#2: 55 false positives on
## one project, which then had to baseline 53 of them to get a usable gate).
## get_global_transform_with_canvas() is the transform the renderer uses; it
## includes ancestor CanvasLayer transforms and the viewport's canvas transform.
## A rotated Control yields the enclosing axis-aligned box, which is the right
## answer for "is any of it off screen" and for "could a finger hit it".
func _screen_rect_of(control: Control) -> Rect2:
	return control.get_global_transform_with_canvas() * Rect2(Vector2.ZERO, control.size)


# --- UI Validation Command Handlers ---

## Runs the UI checks over the current scene.
##
## The safe-area check (issue code "ui_outside_safe_area") reads `safe_area_inset`
## from devtools_config.json, overridable per call via args["safe_area_inset"]. It is
## SKIPPED ENTIRELY when the inset is all zeros -- with a zero inset it would only
## restate checks 1 and 5, and an existing project must see exactly the findings it
## saw before this check existed.
func _cmd_validate_ui(args: Dictionary) -> Dictionary:
	var issues: Array = []
	var interactive_controls: Array = []
	var vp: Vector2 = _screen_reference_rect().size

	var inset: Dictionary = _resolve_safe_area_inset(args)
	var check_safe_area: bool = inset["left"] != 0.0 or inset["top"] != 0.0 \
		or inset["right"] != 0.0 or inset["bottom"] != 0.0
	var safe_rect: Rect2 = Rect2(
		inset["left"],
		inset["top"],
		maxf(0.0, vp.x - inset["left"] - inset["right"]),
		maxf(0.0, vp.y - inset["top"] - inset["bottom"]))

	_validate_ui_recursive(get_tree().current_scene, vp, issues, interactive_controls, safe_rect, check_safe_area)

	# Check for overlapping interactive controls
	var overlaps: Array = _check_interactive_overlaps(interactive_controls)
	for overlap: Dictionary in overlaps:
		issues.append({
			"path": str(overlap["node_a"]),
			"severity": "warning",
			"code": "interactive_overlap",
			"message": "Interactive controls overlap, both reachable (focusable or clickable): '%s' and '%s' (overlap area: %.0fpx)" % [
				overlap["node_a"], overlap["node_b"], overlap["overlap_area"],
			],
		})
	# plant-tower-defense:G-046: "not overlapping" used to pass for "touching" -
	# Rect2.intersects is false for boxes sharing an edge, and a Back button flush
	# under a row reads as broken to a player. Off by default (min_control_gap 0
	# keeps today's behaviour); a project that sets it gets a named finding.
	var min_gap: float = float(_config.get("min_control_gap", 0))
	if min_gap > 0.0:
		for pair: Dictionary in _check_interactive_gaps(interactive_controls, min_gap):
			issues.append({
				"path": str(pair["node_a"]),
				"severity": "warning",
				"code": "controls_touching",
				"message": "Interactive controls too close: '%s' and '%s' are %.0fpx apart (min_control_gap %.0f)" % [
					pair["node_a"], pair["node_b"], pair["gap"], min_gap,
				],
			})

	# plant-tower-defense:G-048b: a Control that visibly belongs to a panel drawn as
	# its SIBLING (a legend row over a pause card, both children of the screen) is
	# "inside its parent" trivially, so 34px of it hanging off the paper passed
	# every gate. For each visible Panel/PanelContainer, a sibling Control whose
	# CENTRE sits on the panel but whose box is not fully inside it is reported.
	# The centre rule is what keeps a neighbouring HUD element that merely brushes
	# an edge out of the report; a Control that CONTAINS the panel (a backdrop) is
	# skipped the same way.
	for esc: Dictionary in _collect_panel_escapes(get_tree().current_scene):
		issues.append({
			"path": str(esc["path"]),
			"severity": "warning",
			"code": "ui_escapes_panel",
			"message": "%s '%s' hangs off sibling panel '%s' by %s (control %s, panel %s)" % [
				esc["type"], esc["name"], esc["panel_name"], esc["overhang_text"],
				esc["control_rect_text"], esc["panel_rect_text"],
			],
		})

	var baseline: Dictionary = _apply_ui_baseline(issues, args)

	# Gate on what is NEW when a baseline is in play. A finding that is correct
	# by design - a popup resting at alpha 0, a diegetic HUD in world space -
	# must not fail every run forever, or the check gets switched off.
	var gating: int = int(baseline["new_count"]) if bool(baseline["in_use"]) else issues.size()
	var summary: String = "No UI issues found"
	if not issues.is_empty():
		if bool(baseline["in_use"]):
			summary = "%d UI issues found (%d NEW, %d pre-existing)" % [
				issues.size(), baseline["new_count"], baseline["pre_existing_count"],
			]
		else:
			summary = "%d UI issues found" % issues.size()
	if bool(baseline["written"]):
		summary = "UI baseline written: %d finding(s) now pre-existing" % issues.size()
	var last_path: String = _persist_last_findings("validate_ui", issues)

	return {
		"success": gating == 0,
		"message": summary,
		"data": {
			"issues": issues,
			"last_findings_path": last_path,
			"baseline_in_use": baseline["in_use"],
			"baseline_written": baseline["written"],
			"baseline_path": baseline["path"],
			"new_count": baseline["new_count"],
			"pre_existing_count": baseline["pre_existing_count"],
			"safe_area_checked": check_safe_area,
			"safe_area_inset": inset,
			"safe_area_rect": {
				"x": safe_rect.position.x,
				"y": safe_rect.position.y,
				"w": safe_rect.size.x,
				"h": safe_rect.size.y,
			},
			"geometry_trustworthy": _geometry_caveat().is_empty(),
			"geometry_caveat": _geometry_caveat(),
		},
	}


## Splits validate_ui findings into NEW and PRE-EXISTING against a saved
## baseline, the same split lint_project.gd has had since 0.4.0
## (findmyballs:G-006, gather:G-018).
##
## Two projects independently hit the same wall: a finding that is CORRECT and
## permanent. findmyballs' cash-delta popup rests at alpha 0 between pops, so
## `ui_transparent` fires on every run forever; gather's diegetic HUD hangs off
## a Camera2D, so its position findings were a function of where the player was
## standing - 9 of 9 findings on one run. Neither project could gate on
## validate_ui at all, and a check that can only ever be ignored is a check that
## has been switched off.
##
## Keyed on (code, node path), NOT on the message: messages carry rects, alphas
## and text widths, so a baseline keyed on them would go stale the first time a
## label moved a pixel and quietly re-report everything as new.
##
## args: { "baseline_write": bool, "use_baseline": bool (default true) }
## Returns { in_use, written, path, new_count, pre_existing_count }, and stamps
## each issue with "baseline": "new" | "pre_existing".
func _apply_ui_baseline(issues: Array, args: Dictionary,
		name: String = "ui_findings_baseline.json") -> Dictionary:
	# `name` (a bare file name since 0.45.0, gh#48 / moving-in:G-066; a `user://`
	# path since 0.40.0 for the signal check) resolves through _baseline_file():
	# the project's `.devtools/` first - committable, so an accepted baseline
	# travels to a clone, a second developer and CI, where `user://` did not - then
	# the legacy `user://` copy so every existing install still reads its own.
	# Writes go to `.devtools/` unless `baseline_dir` says otherwise.
	var write_mode: bool = bool(args.get("baseline_write", false))
	var use: bool = bool(args.get("use_baseline", true))
	var path: String = _baseline_file(name.get_file(), args, write_mode)

	if write_mode:
		var keys: Array = []
		for issue: Dictionary in issues:
			keys.append(_ui_finding_key(issue))
			issue["baseline"] = "pre_existing"
		var file: FileAccess = FileAccess.open(path, FileAccess.WRITE)
		if file == null and path.begins_with("res://"):
			# An exported build's res:// is read-only; fall back to user:// and say so
			# in the path the reply carries.
			path = "user://" + name.get_file()
			file = FileAccess.open(path, FileAccess.WRITE)
		if file == null:
			return {
				"in_use": false, "written": false, "path": path,
				"new_count": issues.size(), "pre_existing_count": 0,
			}
		file.store_string(JSON.stringify({
			"harness_version": HARNESS_VERSION,
			"written_unix": Time.get_unix_time_from_system(),
			"keys": keys,
		}, "  "))
		file.close()
		return {
			"in_use": true, "written": true, "path": path,
			"new_count": 0, "pre_existing_count": issues.size(),
		}

	# key -> how many findings the baseline holds under it. Multiplicity matters
	# because keys are normalised (see _ui_finding_key): three runtime-built rows
	# under one named parent share a key, and a FOURTH broken row must still read
	# as NEW rather than hide behind the accepted three.
	var known: Dictionary = {}
	var in_use: bool = false
	if use and FileAccess.file_exists(path):
		var text: String = FileAccess.get_file_as_string(path)
		var parsed: Variant = JSON.parse_string(text)
		if parsed is Dictionary:
			var keys_value: Variant = (parsed as Dictionary).get("keys", [])
			if keys_value is Array:
				in_use = true
				for key: Variant in keys_value as Array:
					# Baselines written before 0.22.0 hold raw @Type@NNN keys;
					# normalising on read keeps them valid.
					var k: String = _normalize_ui_key(str(key))
					known[k] = int(known.get(k, 0)) + 1

	var new_count: int = 0
	var pre_existing: int = 0
	for issue: Dictionary in issues:
		var k: String = _ui_finding_key(issue)
		if in_use and int(known.get(k, 0)) > 0:
			known[k] = int(known[k]) - 1
			issue["baseline"] = "pre_existing"
			pre_existing += 1
		else:
			issue["baseline"] = "new"
			new_count += 1

	return {
		"in_use": in_use, "written": false, "path": path,
		"new_count": new_count, "pre_existing_count": pre_existing,
	}


## Where a findings baseline lives (gh#48 / moving-in:G-066, 0.45.0). `args.baseline_dir`
## (a `res://`-relative directory such as ".devtools", or an absolute/`user://` path)
## pins it; otherwise reads prefer `res://.devtools/<name>` and fall back to the
## legacy `user://<name>`, and writes go to `res://.devtools/<name>` (created on
## demand). The verdict of an adjudication belongs where the evidence lives - in
## the repo - or a fresh clone sees eleven accepted findings as eleven new ones.
func _baseline_file(name: String, args: Dictionary, write_mode: bool) -> String:
	var dir_arg: String = str(args.get("baseline_dir", ""))
	if not dir_arg.is_empty():
		var dir_path: String = dir_arg
		if not (dir_path.begins_with("res://") or dir_path.begins_with("user://") or dir_path.is_absolute_path()):
			dir_path = "res://" + dir_path.trim_prefix("./")
		if write_mode:
			DirAccess.make_dir_recursive_absolute(dir_path)
		return dir_path.path_join(name)
	var repo_path: String = "res://.devtools/" + name
	var legacy: String = "user://" + name
	if write_mode:
		DirAccess.make_dir_recursive_absolute("res://.devtools")
		return repo_path
	if FileAccess.file_exists(repo_path):
		return repo_path
	return legacy


## Stable identity of a UI finding: the rule that fired and the node it fired
## on. Deliberately excludes the message - see _apply_ui_baseline().
##
## Auto-generated node names (`@VBoxContainer@465`) are normalised to their type
## (`@VBoxContainer`) because the counter is a per-process allocation order, not
## an identity: an unrelated commit that inserts one sibling anywhere earlier in
## the scene renumbers every runtime-built row after it, and a baseline keyed on
## the raw path re-presented 30 accepted findings as NEW on a diff that touched
## no UI (moving-in:G-031, twice). A gate that fires on someone else's commit is
## a gate that gets waved through. Multiplicity is kept by _apply_ui_baseline.
func _ui_finding_key(issue: Dictionary) -> String:
	return _normalize_ui_key("%s@%s" % [str(issue.get("code", "")), str(issue.get("path", ""))])


## `.../@VBoxContainer@465/@HBoxContainer@466` -> `.../@VBoxContainer/@HBoxContainer`.
func _normalize_ui_key(key: String) -> String:
	if not _auto_name_re_compiled:
		_auto_name_re.compile("@([A-Za-z_][A-Za-z0-9_]*)@[0-9]+")
		_auto_name_re_compiled = true
	return _auto_name_re.sub(key, "@$1", true)


## Reads the safe-area inset (pixels trimmed off each viewport edge) from args, else
## from `safe_area_inset` in devtools_config.json, else zero. Always returns all four
## edges as floats so callers need no further guarding.
func _resolve_safe_area_inset(args: Dictionary) -> Dictionary:
	var inset: Dictionary = {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
	var source: Variant = args.get("safe_area_inset", _config.get("safe_area_inset", {}))
	if source is Dictionary:
		var source_dict: Dictionary = source
		for edge: String in ["left", "top", "right", "bottom"]:
			var value: Variant = source_dict.get(edge, 0.0)
			if _is_number(value):
				inset[edge] = float(value)
	return inset


## A Control parented under a Node2D with no CanvasLayer in between renders in
## WORLD coordinates (a diegetic HUD riding a camera, a health bar over an enemy).
## Screen-position checks are meaningless for it: its "position" is a function of
## where the player is standing, which is how validate-ui once produced 9 findings
## that all evaporated on a different save (gap gather:G-018).
## Overhang of `inner` past `outer` on each side, in px (0 = inside on that side).
func _rect_overhang(inner: Rect2, outer: Rect2) -> Dictionary:
	return {
		"left": maxf(0.0, outer.position.x - inner.position.x),
		"top": maxf(0.0, outer.position.y - inner.position.y),
		"right": maxf(0.0, inner.end.x - outer.end.x),
		"bottom": maxf(0.0, inner.end.y - outer.end.y),
	}


func _overhang_text(over: Dictionary) -> String:
	var parts: PackedStringArray = PackedStringArray()
	for side: String in ["left", "top", "right", "bottom"]:
		if float(over[side]) > 0.5:
			parts.append("%.0fpx %s" % [float(over[side]), side])
	return ", ".join(parts) if not parts.is_empty() else "0px"


func _rect_text(r: Rect2) -> String:
	return "%.0f,%.0f %.0fx%.0f" % [r.position.x, r.position.y, r.size.x, r.size.y]


## Sibling Controls hanging off a Panel/PanelContainer they visibly sit on
## (plant-tower-defense:G-048b). See the call site in _cmd_validate_ui.
func _collect_panel_escapes(root: Node) -> Array:
	var out: Array = []
	if root == null:
		return out
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var parent: Node = stack.pop_back()
		var panels: Array[Control] = []
		var others: Array[Control] = []
		for child: Node in parent.get_children():
			stack.append(child)
			if not (child is Control) or not _is_effectively_visible(child):
				continue
			var c: Control = child as Control
			if _is_world_space_control(c):
				continue
			if c is Panel or c is PanelContainer:
				panels.append(c)
			else:
				others.append(c)
		if panels.is_empty() or others.is_empty():
			continue
		for panel: Control in panels:
			var prect: Rect2 = _screen_rect_of(panel)
			if prect.size.x <= 0.0 or prect.size.y <= 0.0:
				continue
			for other: Control in others:
				var orect: Rect2 = _screen_rect_of(other)
				if orect.size.x <= 0.0 or orect.size.y <= 0.0:
					continue
				if not prect.has_point(orect.get_center()):
					continue
				if prect.encloses(orect):
					continue
				if orect.encloses(prect):
					continue  # a backdrop or container the panel sits on
				var over: Dictionary = _rect_overhang(orect, prect)
				out.append({
					"path": str(other.get_path()), "name": str(other.name), "type": other.get_class(),
					"panel": str(panel.get_path()), "panel_name": str(panel.name),
					"overhang": over, "overhang_text": _overhang_text(over),
					"control_rect_text": _rect_text(orect), "panel_rect_text": _rect_text(prect),
				})
	return out


## Is one Control's screen box inside another's? (plant-tower-defense:G-048b - the
## question three screens in one project each answered with a bespoke test.)
## args: { "node_path": String, "within": String }
## data keys: inside (bool), overhang {left, top, right, bottom} px, control_rect,
##            within_rect, control_centre_inside (bool).
func _cmd_contained_in(args: Dictionary) -> Dictionary:
	var node_path: String = str(args.get("node_path", ""))
	var within_path: String = str(args.get("within", ""))
	if node_path.is_empty() or within_path.is_empty():
		return {"success": false, "message": "contained_in needs node_path and within"}
	var a: Dictionary = _resolve_node(node_path)
	var b: Dictionary = _resolve_node(within_path)
	if a["node"] == null:
		return {"success": false, "message": "Node not found: %s" % node_path}
	if b["node"] == null:
		return {"success": false, "message": "Node not found: %s" % within_path}
	if not (a["node"] is Control) or not (b["node"] is Control):
		return {"success": false, "message": "contained_in measures Controls; got %s and %s" % [
			(a["node"] as Node).get_class(), (b["node"] as Node).get_class()]}
	var inner: Rect2 = _screen_rect_of(a["node"])
	var outer: Rect2 = _screen_rect_of(b["node"])
	var over: Dictionary = _rect_overhang(inner, outer)
	var inside: bool = outer.encloses(inner)
	var caveat: String = _geometry_caveat()
	var message: String = "%s is %s %s" % [
		a["path"], "inside" if inside else "NOT inside", b["path"]]
	if not inside:
		message += " -- hangs off by %s" % _overhang_text(over)
	if not caveat.is_empty():
		message += " (%s)" % caveat
	return {
		"success": inside,
		"message": message,
		"data": {
			"inside": inside,
			"overhang": over,
			"control_rect": {"x": inner.position.x, "y": inner.position.y, "w": inner.size.x, "h": inner.size.y},
			"within_rect": {"x": outer.position.x, "y": outer.position.y, "w": outer.size.x, "h": outer.size.y},
			"control_centre_inside": outer.has_point(inner.get_center()),
			"geometry_trustworthy": caveat.is_empty(),
			"geometry_caveat": caveat,
		},
	}


func _is_world_space_control(control: Control) -> bool:
	var ancestor: Node = control.get_parent()
	while ancestor != null:
		if ancestor is CanvasLayer:
			return false
		if ancestor is Node2D:
			return true
		ancestor = ancestor.get_parent()
	return false


func _validate_ui_recursive(node: Node, vp: Vector2, issues: Array, interactive_controls: Array = [], safe_rect: Rect2 = Rect2(), check_safe_area: bool = false) -> void:
	if node is Control and _is_effectively_visible(node):
		var control: Control = node as Control
		var rect: Rect2 = _screen_rect_of(control)

		# World-space Controls get only the intrinsic checks (zero size,
		# transparency); every screen-position check would report where the
		# camera happens to be, not a layout defect.
		var world_space: bool = _is_world_space_control(control)

		# Collect interactive controls for overlap detection. gh#42 (0.43.0): a
		# control that cannot be focused AND cannot be clicked is not interactive,
		# whatever its class - making a covered layer inert (FOCUS_NONE +
		# MOUSE_FILTER_IGNORE) is the standard fix for the hazard this check
		# exists to find, so counting those fired hardest at projects that had
		# already fixed it, and the only way to quiet it was a baseline that
		# would also hide a genuine overlap arriving later at the same pair.
		if (control is Button or control is TextureButton or control is LinkButton) and control.visible and not world_space:
			if control.focus_mode == Control.FOCUS_NONE and control.mouse_filter == Control.MOUSE_FILTER_IGNORE:
				pass  # inert by both channels: a player can reach it neither by Tab nor by pointer
			else:
				interactive_controls.append({"path": str(control.get_path()), "rect": rect})

		# Check 1: Viewport overflow
		if not world_space and (rect.position.x + rect.size.x > vp.x or rect.position.y + rect.size.y > vp.y):
			issues.append({
				"path": str(control.get_path()),
				"severity": "warning",
				"code": "ui_overflow",
				"message": "%s '%s' extends past viewport (rect: %.0f,%.0f -> %.0f,%.0f, viewport: %.0fx%.0f)" % [
					control.get_class(), control.name,
					rect.position.x, rect.position.y,
					rect.position.x + rect.size.x, rect.position.y + rect.size.y,
					vp.x, vp.y,
				],
			})

		# Check 2: Zero-size visible
		if control.size.x == 0.0 or control.size.y == 0.0:
			issues.append({
				"path": str(control.get_path()),
				"severity": "warning",
				"code": "ui_zero_size",
				"message": "%s '%s' is visible but has zero size (%.0fx%.0f)" % [
					control.get_class(), control.name, control.size.x, control.size.y,
				],
			})

		# Check 3: Fully transparent
		var effective_alpha: float = _get_effective_alpha(control)
		if effective_alpha == 0.0:
			issues.append({
				"path": str(control.get_path()),
				"severity": "info",
				"code": "ui_transparent",
				"message": "%s '%s' is visible but fully transparent (effective alpha: %.2f)" % [
					control.get_class(), control.name, effective_alpha,
				],
			})

		# Check 4: Text overflow (Label only, autowrap disabled)
		if control is Label and control.autowrap_mode == TextServer.AUTOWRAP_OFF:
			var font: Font = control.get_theme_font("font")
			if font != null:
				var font_size: int = control.get_theme_font_size("font_size")
				if font_size <= 0:
					font_size = control.get_theme_default_font_size()
				# Per line, not the whole string: get_string_size() lays a string
				# holding "\n" out as ONE line, so a two-line banner that renders
				# perfectly inside its box measured 1052px against 896px and gated a
				# run (gh#15.1). A Label breaks on "\n" whether or not it autowraps,
				# so the widest single line is what the box actually has to hold.
				var lines: PackedStringArray = control.text.split("\n")
				var text_width: float = 0.0
				for line: String in lines:
					text_width = maxf(text_width, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x)
				if text_width > control.size.x and control.size.x > 0.0:
					var display_text: String = control.text.replace("\n", "\\n")
					if display_text.length() > 50:
						display_text = display_text.substr(0, 47) + "..."
					var line_note: String = " (widest of %d lines)" % lines.size() if lines.size() > 1 else ""
					# Two different facts used to share one code (plant:G-017): a Label
					# with clip_text or an overrun behaviour is not spilling past its
					# box, it is TRIMMING the string - the player reads a cut readout,
					# and the fix is room or a shorter string. Reported apart so a
					# reader does not triage the two by eye.
					var trimmed: bool = control.clip_text \
						or control.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING
					if trimmed:
						issues.append({
							"path": str(control.get_path()),
							"severity": "warning",
							"code": "ui_text_trimmed",
							"message": "%s '%s' text '%s' is trimmed by its box (text: %.0fpx%s, label: %.0fpx; %s) - the player sees a cut string" % [
								control.get_class(), control.name, display_text, text_width, line_note, control.size.x,
								"clip_text" if control.clip_text else "text_overrun_behavior",
							],
						})
					else:
						issues.append({
							"path": str(control.get_path()),
							"severity": "warning",
							"code": "ui_text_overflow",
							"message": "%s '%s' text '%s' exceeds width (text: %.0fpx%s, label: %.0fpx)" % [
								control.get_class(), control.name, display_text, text_width, line_note, control.size.x,
							],
						})

		# Check 5: Negative position
		if not world_space and (rect.position.x < 0.0 or rect.position.y < 0.0):
			issues.append({
				"path": str(control.get_path()),
				"severity": "warning",
				"code": "ui_negative_pos",
				"message": "%s '%s' has negative position (%.0f, %.0f)" % [
					control.get_class(), control.name, rect.position.x, rect.position.y,
				],
			})

		# Check 6: Button text overflow
		if control is Button and control.text.length() > 0:
			var btn_font: Font = control.get_theme_font("font")
			var btn_font_size: int = control.get_theme_font_size("font_size")
			if btn_font:
				var btn_text_width: float = btn_font.get_string_size(control.text, HORIZONTAL_ALIGNMENT_LEFT, -1, btn_font_size).x
				var padding: float = 16.0
				if btn_text_width + padding > control.size.x and control.size.x > 0.0:
					issues.append({
						"path": str(control.get_path()),
						"severity": "warning",
						"code": "button_text_overflow",
						"message": "Button '%s' text '%s' (%.0fpx) exceeds button width (%.0fpx)" % [
							control.name, control.text, btn_text_width, control.size.x,
						],
					})

		# Check 7: Minimum tap target size for interactive controls
		if (control is Button or control is TextureButton or control is LinkButton) and control.visible:
			var min_tap: float = 40.0
			if control.size.x < min_tap or control.size.y < min_tap:
				issues.append({
					"path": str(control.get_path()),
					"severity": "warning",
					"code": "small_tap_target",
					"message": "Interactive control '%s' size %.0fx%.0f below minimum %.0fx%.0f" % [
						control.name, control.size.x, control.size.y, min_tap, min_tap,
					],
				})

		# Check 8: Container child position consistency
		# If a node is inside a BoxContainer (HBox/VBox) with layout_mode 2,
		# its position should be within the container's bounds
		if node.get_parent() is BoxContainer:
			var parent_container: BoxContainer = node.get_parent() as BoxContainer
			var parent_rect: Rect2 = parent_container.get_global_rect()
			var node_rect: Rect2 = control.get_global_rect()
			var path: String = str(control.get_path())
			# Check if child extends beyond parent bounds (layout corruption)
			if node_rect.position.x < parent_rect.position.x - 2.0:
				issues.append({
					"path": str(control.get_path()),
					"severity": "warning",
					"code": "container_layout_drift",
					"message": "Node '%s' position (%.0f) is left of parent container (%.0f) - possible layout corruption" % [
						path, node_rect.position.x, parent_rect.position.x,
					],
				})
			if node_rect.end.x > parent_rect.end.x + 2.0:
				issues.append({
					"path": str(control.get_path()),
					"severity": "warning",
					"code": "container_layout_drift",
					"message": "Node '%s' extends past parent container right edge (%.0f > %.0f) - possible layout corruption" % [
						path, node_rect.end.x, parent_rect.end.x,
					],
				})

		# Check 9: Safe-area inset. Only interactive or text-bearing controls are
		# judged -- a full-bleed background legitimately covers the trimmed edges, and
		# flagging it would bury the findings that matter. Skipped wholesale when the
		# inset is all zeros, so an existing project sees no new findings.
		if check_safe_area and control.visible and not world_space:
			var is_interactive: bool = control is Button or control is TextureButton or control is LinkButton
			var carries_text: bool = not _get_control_text(control).is_empty()
			if (is_interactive or carries_text) and not safe_rect.encloses(rect):
				issues.append({
					"path": str(control.get_path()),
					"severity": "warning",
					"code": "ui_outside_safe_area",
					"message": "%s '%s' falls outside the safe area (rect: %.0f,%.0f -> %.0f,%.0f, safe area: %.0f,%.0f -> %.0f,%.0f)" % [
						control.get_class(), control.name,
						rect.position.x, rect.position.y, rect.end.x, rect.end.y,
						safe_rect.position.x, safe_rect.position.y, safe_rect.end.x, safe_rect.end.y,
					],
				})

	for child in node.get_children():
		_validate_ui_recursive(child, vp, issues, interactive_controls, safe_rect, check_safe_area)


func _cmd_get_ui_snapshot(_args: Dictionary) -> Dictionary:
	var vp: Vector2 = _screen_reference_rect().size
	var elements: Array = []
	_snapshot_ui_recursive(get_tree().current_scene, vp, elements)

	return {
		"success": true,
		"message": "%d UI elements captured" % elements.size(),
		"data": {
			"viewport": {"width": int(vp.x), "height": int(vp.y)},
			"elements": elements,
			"geometry_trustworthy": _geometry_caveat().is_empty(),
			"geometry_caveat": _geometry_caveat(),
		},
	}


func _snapshot_ui_recursive(node: Node, vp: Vector2, elements: Array) -> void:
	if node is Control:
		var control: Control = node as Control
		var eff_visible: bool = _is_effectively_visible(control)
		var eff_alpha: float = _get_effective_alpha(control)

		if eff_visible or eff_alpha > 0.0:
			var rect: Rect2 = _screen_rect_of(control)
			elements.append({
				"name": str(control.name),
				"type": control.get_class(),
				"path": str(control.get_path()),
				"global_rect": {
					"x": rect.position.x,
					"y": rect.position.y,
					"w": rect.size.x,
					"h": rect.size.y,
				},
				"visible": eff_visible,
				"modulate_a": eff_alpha,
				"text": _get_control_text(control),
				"in_viewport": rect.position.x >= 0.0 and rect.position.y >= 0.0
					and rect.position.x + rect.size.x <= vp.x
					and rect.position.y + rect.size.y <= vp.y,
			})

	for child in node.get_children():
		_snapshot_ui_recursive(child, vp, elements)


## Screen-space rect for ANY CanvasItem, not just a Control.
##
## `node-bounds .../Sprite2D` used to answer `Node is not a Control`, so every
## visual check on a game object meant reconstructing the camera transform by
## hand -- `(world - player) * zoom + viewport/2`, three devtools calls and an
## assumption that the camera is centred on the player (gather:G-120). A
## CanvasItem already knows its own answer:
## get_global_transform_with_canvas() is the same transform the renderer uses,
## so it accounts for the camera, every ancestor's scale, and the CanvasLayer.
##
## Both branches go through that transform. A Control used to take its own
## get_global_rect() instead, which stops at the CanvasLayer -- so this verb
## contradicted the paragraph above for exactly the nodes most likely to sit on a
## scaled layer (gh#2). For any other CanvasItem
## the rect is derived from the canvas transform, sized from whatever the node
## can report (a Sprite2D's texture rect, a CollisionShape2D's shape) and
## degenerate (w=h=0) when it can report nothing -- an origin point is still a
## useful answer to "where on screen is this", and a zero size says plainly that
## the extent is unknown rather than inventing one.
##
## CanvasItem-only by construction -- there is no screen rect for a
## MeshInstance3D. `aabb` is the 3D counterpart, in world units.
## data keys: name, type, path, global_rect{x,y,w,h}, visible, modulate_a, text,
## in_viewport, size_source, canvas_scale, geometry_trustworthy, geometry_caveat.
func _cmd_get_node_bounds(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}

	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path}

	if not node is CanvasItem:
		# Name the verb that DOES answer for this node. Being told only what the
		# harness cannot do is what sent a 3D project off to hand-roll glTF
		# arithmetic twice (moving-in:G-002).
		var alternative: String = " Use `aabb` for a 3D world-space box." if node is Node3D else ""
		return {
			"success": false,
			"message": "Node is not a CanvasItem, so it has no screen rect: %s (%s).%s" % [
				node_path, node.get_class(), alternative],
			"data": {"class": node.get_class()},
		}

	var item: CanvasItem = node as CanvasItem
	var vp: Vector2 = _screen_reference_rect().size
	var rect: Rect2
	var size_source: String

	if item is Control:
		rect = _screen_rect_of(item as Control)
		size_source = "get_global_transform_with_canvas() x Control.size (screen space)"
	else:
		var xform: Transform2D = item.get_global_transform_with_canvas()
		var local: Rect2 = _local_draw_rect(item)
		rect = xform * local
		size_source = "get_global_transform_with_canvas() x %s (screen space)" % \
			_local_draw_rect_source(item)
	# The accumulated canvas scale, reported alongside the rect rather than only by
	# the separate canvas-scale verb. A HUD on a CanvasLayer scaled 0.6 produced 55
	# false ui_overflow findings in a real project, and diagnosing it took a second
	# call to a verb the reader had no reason to suspect they needed
	# (moving-in:G-008/G-015). One call should be enough to see it.
	var canvas_scale: Vector2 = item.get_global_transform_with_canvas().get_scale()

	return {
		"success": true,
		"message": "Bounds for %s" % item.name,
		"data": {
			"name": str(item.name),
			"type": item.get_class(),
			"path": str(item.get_path()),
			"global_rect": {
				"x": rect.position.x,
				"y": rect.position.y,
				"w": rect.size.x,
				"h": rect.size.y,
			},
			"visible": _is_effectively_visible(item),
			"modulate_a": _get_effective_alpha(item),
			"text": _get_control_text(item as Control) if item is Control else "",
			"in_viewport": rect.position.x >= 0.0 and rect.position.y >= 0.0
				and rect.position.x + rect.size.x <= vp.x
				and rect.position.y + rect.size.y <= vp.y,
			"size_source": size_source,
			"canvas_scale": {"x": canvas_scale.x, "y": canvas_scale.y},
			"geometry_trustworthy": _geometry_caveat().is_empty(),
			"geometry_caveat": _geometry_caveat(),
		},
	}


## Best-effort local-space extent of a non-Control CanvasItem. Rect2() (a zero
## size at the origin) when nothing can be claimed -- see _cmd_get_node_bounds.
func _local_draw_rect(item: CanvasItem) -> Rect2:
	if item is Sprite2D:
		var sprite: Sprite2D = item as Sprite2D
		if sprite.texture != null:
			var size: Vector2 = sprite.texture.get_size()
			if sprite.region_enabled:
				size = sprite.region_rect.size
			size *= Vector2(1.0 / maxf(1.0, float(sprite.hframes)), 1.0 / maxf(1.0, float(sprite.vframes)))
			var origin: Vector2 = -size * 0.5 if sprite.centered else Vector2.ZERO
			return Rect2(origin + sprite.offset, size)
	elif item is CollisionShape2D:
		var shape: Shape2D = (item as CollisionShape2D).shape
		if shape != null:
			return shape.get_rect() if shape.has_method("get_rect") else Rect2()
	elif item is TileMapLayer:
		var layer: TileMapLayer = item as TileMapLayer
		var used: Rect2i = layer.get_used_rect()
		var cell: Vector2i = layer.tile_set.tile_size if layer.tile_set != null else Vector2i.ONE
		return Rect2(Vector2(used.position * cell), Vector2(used.size * cell))
	return Rect2()


func _local_draw_rect_source(item: CanvasItem) -> String:
	if item is Sprite2D:
		return "Sprite2D texture rect"
	if item is CollisionShape2D:
		return "CollisionShape2D shape rect"
	if item is TileMapLayer:
		return "TileMapLayer used rect"
	return "origin only (this class reports no extent)"


## Reports a CanvasItem's ACCUMULATED canvas transform scale -- what the player's
## eye actually sees after every ancestor's zoom/scale multiplies through -- plus
## the effective texture filter. One verb for two gaps (gather:G-073 + G-075): a
## crisp/blurry question is always "what scale is this REALLY drawn at, through
## WHICH filter", and both answers are invisible to get-state (containers hide
## position/scale) and to node-bounds (position/size only).
## args: { "node_path": String }
func _cmd_canvas_scale(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided", "data": {}}
	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path, "data": {}}
	if not node is CanvasItem:
		return {"success": false, "message": "Node is not a CanvasItem (no canvas transform): %s"
			% node.get_class(), "data": {"class": node.get_class()}}

	var ci: CanvasItem = node as CanvasItem
	var xform: Transform2D = ci.get_global_transform_with_canvas()
	var accumulated: Vector2 = xform.get_scale()

	# Per-ancestor contributions, leaf first, so a surprising accumulated scale
	# can be traced to the ancestor that introduced it.
	var chain: Array = []
	var walker: Node = ci
	while walker is CanvasItem:
		var entry: Dictionary = {"name": str(walker.name), "class": walker.get_class()}
		if walker is Node2D:
			entry["scale"] = {"x": (walker as Node2D).scale.x, "y": (walker as Node2D).scale.y}
		elif walker is Control:
			entry["scale"] = {"x": (walker as Control).scale.x, "y": (walker as Control).scale.y}
		chain.append(entry)
		walker = walker.get_parent()

	# Effective texture filter: TEXTURE_FILTER_PARENT_NODE delegates upward, so
	# resolve the chain to the first explicit value, falling back to the project
	# default when every ancestor inherits.
	var filter_walker: CanvasItem = ci
	var filter: int = ci.texture_filter
	var filter_source: String = str(ci.get_path())
	while filter == CanvasItem.TEXTURE_FILTER_PARENT_NODE and filter_walker.get_parent() is CanvasItem:
		filter_walker = filter_walker.get_parent() as CanvasItem
		filter = filter_walker.texture_filter
		filter_source = str(filter_walker.get_path())
	var filter_names: Dictionary = {
		CanvasItem.TEXTURE_FILTER_NEAREST: "nearest",
		CanvasItem.TEXTURE_FILTER_LINEAR: "linear",
		CanvasItem.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS: "nearest_with_mipmaps",
		CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS: "linear_with_mipmaps",
		CanvasItem.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS_ANISOTROPIC: "nearest_with_mipmaps_anisotropic",
		CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC: "linear_with_mipmaps_anisotropic",
	}
	var filter_name: String
	if filter == CanvasItem.TEXTURE_FILTER_PARENT_NODE:
		var project_default: int = int(ProjectSettings.get_setting(
			"rendering/textures/canvas_textures/default_texture_filter", 1))
		filter_name = "%s (project default)" % {0: "nearest", 1: "linear",
			2: "linear_with_mipmaps", 3: "nearest_with_mipmaps"}.get(project_default, str(project_default))
		filter_source = "project setting"
	else:
		filter_name = filter_names.get(filter, str(filter))

	# Which canvas this node renders into. A CanvasModulate tints exactly ONE
	# canvas, so "will that tint reach this node" is a question about CanvasLayer
	# ancestry -- and answering it used to mean grepping the .tscn for every
	# `type="CanvasLayer"` and hand-walking parents, because scene-tree reports
	# script and scene_file and nothing about canvases (gather:G-105). This walk
	# was already here to accumulate scale; it just never said what it passed.
	var layer_node: CanvasLayer = null
	var layer_walker: Node = ci
	while layer_walker != null:
		if layer_walker is CanvasLayer:
			layer_node = layer_walker as CanvasLayer
			break
		layer_walker = layer_walker.get_parent()

	return {
		"success": true,
		"message": "%s draws at %.3f x %.3f through %s (canvas layer %d)" % [
			node.name, accumulated.x, accumulated.y, filter_name,
			layer_node.layer if layer_node != null else 0],
		"data": {
			"accumulated_scale": {"x": accumulated.x, "y": accumulated.y},
			"rotation": xform.get_rotation(),
			"chain": chain,
			"effective_filter": filter_name,
			"filter_source": filter_source,
			# 0 and "" mean the root canvas (the default viewport canvas), which is
			# a real answer, not a missing one.
			"canvas_layer": layer_node.layer if layer_node != null else 0,
			"canvas_layer_path": str(layer_node.get_path()) if layer_node != null else "",
		},
	}


## Merged WORLD-SPACE AABB of everything a node draws -- the 3D half of node-bounds.
##
## node-bounds is CanvasItem-only, so a 3D project could not ask the harness where
## anything IS: `node-bounds /root/House/Living/tableCoffeeGlass` answered "Node is
## not a CanvasItem, so it has no screen rect". The gap was logged three times
## across two logs before it was closed (moving-in:G-002, moving-in:G-006), and in
## between it produced a real shipped defect -- a book placed 4cm past the edge of
## the table it was supposed to rest on, caught only by a screenshot -- plus about
## 25 lines of hand-rolled glTF arithmetic written twice in one session. `top_y` is
## here because the recurring question is not "how big is it" but "what do I rest
## something on".
##
## Light3D is EXCLUDED, and that exclusion is the whole reason this is a walk and
## not a one-liner. Light3D extends VisualInstance3D, so "merge every
## VisualInstance3D" quietly includes it, and an OmniLight3D's AABB is a cube of
## TWICE ITS RANGE: a 0.2-unit lamp with a light child measured 7.2 x 7.2 x 7.2 and
## corrupted every top_of() / center_of() derived from it. Only GeometryInstance3D
## descendants are merged (MeshInstance3D, MultiMeshInstance3D, CSG shapes, Label3D,
## Sprite3D, GPUParticles3D) -- the set that actually draws geometry. Everything
## skipped is named in data.excluded with the reason, so a surprising number can be
## traced without a second call.
##
## A node with NO geometry FAILS rather than returning a zero AABB at its origin: a
## zero AABB reads exactly like an honest measurement of a tiny object sitting right
## there, and this verb exists to be trusted for placement arithmetic.
##
## The AABB is axis-aligned in WORLD space, so a rotated object reports its enclosing
## box, not its footprint. data.node_transform carries the node's own rotation and
## scale (and `axis_aligned`) so a caller can tell those two cases apart.
##
## args: { "node_path": String }
## data keys: path, name, type, min{x,y,z}, max{x,y,z}, size{x,y,z}, center{x,y,z},
## top_y, bottom_y, merged_count, merged[{path,class,visible}],
## excluded[{path,class,reason}], node_transform{origin,rotation_deg,scale,axis_aligned}.
func _cmd_aabb(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided", "data": {}}

	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s (also tried under /root)" % node_path,
			"data": {}}
	var used_path: String = resolved["path"]

	var geometry: Array = []
	var excluded: Array = []
	_collect_geometry_instances(node, geometry, excluded)

	if geometry.is_empty():
		var hint: String = ""
		if node is CanvasItem:
			hint = " It is a CanvasItem -- use node-bounds for a screen rect."
		elif not excluded.is_empty():
			hint = " %d visual node(s) were skipped; see data.excluded." % excluded.size()
		return {
			"success": false,
			"message": "No 3D geometry under %s (%s): nothing to measure.%s" % [
				used_path, node.get_class(), hint],
			"data": {"path": used_path, "type": node.get_class(), "merged_count": 0,
				"merged": [], "excluded": excluded},
		}

	var merged_nodes: Array = []
	var box: AABB = AABB()
	var have: bool = false
	for entry: GeometryInstance3D in geometry:
		var world_box: AABB = _world_aabb_of(entry)
		box = world_box if not have else box.merge(world_box)
		have = true
		merged_nodes.append({
			"path": str(entry.get_path()),
			"class": entry.get_class(),
			"visible": entry.is_visible_in_tree(),
		})

	var center: Vector3 = box.position + box.size * 0.5
	var node_transform: Dictionary = {}
	if node is Node3D:
		var xform: Transform3D = (node as Node3D).global_transform
		var euler: Vector3 = xform.basis.get_euler()
		var basis_scale: Vector3 = xform.basis.get_scale()
		node_transform = {
			"origin": _serialize_variant(xform.origin),
			"rotation_deg": _serialize_variant(Vector3(
				rad_to_deg(euler.x), rad_to_deg(euler.y), rad_to_deg(euler.z))),
			"scale": _serialize_variant(basis_scale),
			"axis_aligned": _basis_is_axis_aligned(xform.basis),
		}

	return {
		"success": true,
		"message": "%s spans %.3f x %.3f x %.3f, (%.3f, %.3f, %.3f)..(%.3f, %.3f, %.3f), top_y %.3f (%d geometry node(s) merged, %d excluded)" % [
			node.name, box.size.x, box.size.y, box.size.z,
			box.position.x, box.position.y, box.position.z,
			box.end.x, box.end.y, box.end.z,
			box.end.y, merged_nodes.size(), excluded.size()],
		"data": {
			"path": used_path,
			"name": str(node.name),
			"type": node.get_class(),
			"min": _serialize_variant(box.position),
			"max": _serialize_variant(box.end),
			"size": _serialize_variant(box.size),
			"center": _serialize_variant(center),
			# The y of the top and bottom faces. "What do I rest this on" and "is it
			# sunk into the floor" are the two questions this verb keeps being asked.
			"top_y": box.end.y,
			"bottom_y": box.position.y,
			"merged_count": merged_nodes.size(),
			"merged": merged_nodes,
			"excluded": excluded,
			"node_transform": node_transform,
		},
	}


## Orients a Node3D to face another node's world-space centre (moving-in:G-044,
## seen twice the same day). `aabb` gives a node's exact world
## centre and a project's own extension can teleport a node TO a position, but
## nothing pointed a camera or the player AT one - framing a fixture for a
## screenshot was four blind attempts at a heading in degrees. args:
## {"node": PATH (required, the target to look at),
##  "from_node": PATH (optional; default: get_viewport().get_camera_3d(), the
##    active Camera3D - no project knowledge required to find "the camera"),
##  "up": [x,y,z] (optional, default Vector3.UP)}.
##
## Orientation only - `from_node` is never moved, only rotated in place. A
## "sensible standoff" is exactly the ambiguity that would have made this verb
## a second source of guessing; positioning is what teleport/set-state already
## do, and the reported blocker was heading, not position ("the player position
## read back correctly... what is missing is any way to point the camera AT a
## node").
func _cmd_look_at(args: Dictionary) -> Dictionary:
	var target_path: String = str(args.get("node", ""))
	if target_path.is_empty():
		return {"success": false, "message": "look_at requires 'node' (the target to look at)",
			"data": {}}
	var target_resolved: Dictionary = _resolve_node(target_path)
	var target: Node = target_resolved["node"]
	if target == null:
		return {"success": false,
			"message": ("look_at: target node not found: %s (also tried under /root)"
				% target_path),
			"data": {}}

	var from_node: Node = null
	var from_path: String = str(args.get("from_node", ""))
	if not from_path.is_empty():
		var from_resolved: Dictionary = _resolve_node(from_path)
		from_node = from_resolved["node"]
		if from_node == null:
			return {"success": false,
				"message": ("look_at: from_node not found: %s (also tried under /root)"
					% from_path),
				"data": {}}
	else:
		from_node = get_viewport().get_camera_3d()
		if from_node == null:
			return {"success": false,
				"message": "look_at: no 'from_node' given and " +
					"get_viewport().get_camera_3d() found no active Camera3D. Pass " +
					"--from-node, or make a camera current first.",
				"data": {}}
	if not (from_node is Node3D):
		return {"success": false,
			"message": ("look_at: from_node %s is a %s, not a Node3D - there is no "
				+ "heading to set. (2D has no look_at(); orient it via set_state "
				+ "rotation instead.)") % [str(from_node.get_path()), from_node.get_class()],
			"data": {}}

	# The target's world-space AABB centre when it has geometry (same measurement
	# `aabb` reports); its own global_position otherwise - a spawn marker, an empty
	# Node3D, a Marker3D all have a position worth facing and no geometry to merge.
	var target_center: Vector3
	var center_source: String
	if target is Node3D:
		var geometry: Array = []
		var excluded: Array = []
		_collect_geometry_instances(target, geometry, excluded)
		if not geometry.is_empty():
			var box: AABB = AABB()
			var have: bool = false
			for entry: GeometryInstance3D in geometry:
				var world_box: AABB = _world_aabb_of(entry)
				box = world_box if not have else box.merge(world_box)
				have = true
			target_center = box.position + box.size * 0.5
			center_source = "aabb centre (%d geometry node(s) merged)" % geometry.size()
		else:
			target_center = (target as Node3D).global_position
			center_source = "global_position (no 3D geometry under it to merge an AABB from)"
	else:
		return {"success": false,
			"message": ("look_at: target %s is a %s, not a Node3D - no world-space "
				+ "position to face") % [target_resolved["path"], target.get_class()],
			"data": {}}

	var from3d: Node3D = from_node as Node3D
	var from_pos: Vector3 = from3d.global_position
	if from_pos.distance_to(target_center) < 0.0001:
		return {"success": false,
			"message": ("look_at: %s is already at the target's centre "
				+ "(%.3f, %.3f, %.3f) - no direction to face") % [
					str(from3d.get_path()), target_center.x, target_center.y, target_center.z],
			"data": {}}
	var up: Vector3 = Vector3.UP
	if args.has("up"):
		var up_v: Variant = _parse_vector3_or_null(args.get("up"))
		if up_v is Vector3:
			up = up_v
	from3d.look_at(target_center, up)

	return {
		"success": true,
		"message": "%s now faces %s at (%.3f, %.3f, %.3f) via %s" % [
			str(from3d.get_path()), target_resolved["path"],
			target_center.x, target_center.y, target_center.z, center_source],
		"data": {
			"from": str(from3d.get_path()),
			"target": target_resolved["path"],
			"target_center": _serialize_variant(target_center),
			"center_source": center_source,
		},
	}


## Where a GeometryInstance3D's geometry actually sits in the world.
##
## The 3D sibling of _screen_rect_of(), and deliberately the same shape: take the
## node's own LOCAL extent and push it through the transform the renderer uses.
## get_aabb() is local space; global_transform carries every ancestor's rotation,
## scale and offset, and Transform3D * AABB yields the enclosing axis-aligned box
## of the transformed result. A rotated mesh therefore yields its enclosing box,
## which is the right answer for "does this overlap that" and an OVERESTIMATE for
## "what is its footprint" -- see _cmd_aabb's node_transform.axis_aligned.
func _world_aabb_of(item: GeometryInstance3D) -> AABB:
	return item.global_transform * item.get_aabb()


## Splits a subtree into the GeometryInstance3D nodes worth merging and everything
## visual that was deliberately left out, with the reason. See _cmd_aabb for why
## Light3D is the exclusion that matters; the rest are recorded so a caller can see
## that the walk considered them rather than missed them.
##
## Children are walked even under an excluded node -- a MeshInstance3D parented to a
## lamp's Light3D is still geometry.
func _collect_geometry_instances(node: Node, geometry: Array, excluded: Array) -> void:
	if node is Light3D:
		excluded.append({
			"path": str(node.get_path()),
			"class": node.get_class(),
			"reason": "Light3D: its AABB is the light's range volume, not geometry",
		})
	elif node is GeometryInstance3D:
		var gi: GeometryInstance3D = node as GeometryInstance3D
		var local: AABB = gi.get_aabb()
		if local.size == Vector3.ZERO:
			# A MeshInstance3D with no mesh, an unbuilt CSG shape, an empty
			# MultiMesh. Merging its origin would drag the box to that point and
			# read as a real measurement.
			excluded.append({
				"path": str(gi.get_path()),
				"class": gi.get_class(),
				"reason": "reports a zero-size AABB (no mesh/geometry assigned?)",
			})
		else:
			geometry.append(gi)
	elif node is VisualInstance3D:
		excluded.append({
			"path": str(node.get_path()),
			"class": node.get_class(),
			"reason": "VisualInstance3D that draws no geometry (not a GeometryInstance3D)",
		})

	for child: Node in node.get_children():
		_collect_geometry_instances(child, geometry, excluded)


## True when each basis column points down a single world axis, i.e. the node is
## rotated by some multiple of 90 degrees (or not at all). When it is false, the
## world AABB is an enclosing box and is LARGER than the object's real footprint --
## which is exactly the thing a caller doing placement arithmetic must not miss.
## Basis.x/y/z ARE the columns, and are the only spelling that works: Godot 4.7's
## Basis exposes no get_column() (verified against the engine API index -- its
## members are x, y, z plus the transforms), and reaching for it is a parse error
## that no static gate here caught (H-045).
func _basis_is_axis_aligned(node_basis: Basis) -> bool:
	var columns: Array[Vector3] = [node_basis.x, node_basis.y, node_basis.z]
	for axis: Vector3 in columns:
		if axis.length() < 0.0001:
			return false
		axis = axis.normalized()
		# Exactly one non-zero component means this column IS a world axis.
		var significant: int = int(absf(axis.x) > 0.001) \
			+ int(absf(axis.y) > 0.001) \
			+ int(absf(axis.z) > 0.001)
		if significant != 1:
			return false
	return true


## Resizes the game window so anchors and size_changed handlers can be exercised
## at more than the one resolution the game booted with (gap gather:G-017). The
## read-back is honest: a headless or tiled environment may clamp or ignore the
## resize, and that is reported as a failure rather than "Resized".
## args: { "width": int, "height": int }
func _cmd_set_resolution(args: Dictionary) -> Dictionary:
	var width: int = int(args.get("width", 0))
	var height: int = int(args.get("height", 0))
	if width <= 0 or height <= 0:
		return {"success": false, "message": "width and height are required and must be positive",
			"data": {}}
	var window: Window = get_window()
	var before: Vector2i = window.size
	window.size = Vector2i(width, height)
	await get_tree().process_frame
	var after: Vector2i = window.size
	var visible: Rect2 = get_viewport().get_visible_rect()
	var applied: bool = after == Vector2i(width, height)
	return {
		"success": applied,
		"message": ("Resized %s -> %s" % [before, after]) if applied
			else ("Resize not applied: asked %dx%d, window reports %s (headless and tiling WMs may clamp or ignore resizes)"
				% [width, height, after]),
		"data": {
			"before": {"x": before.x, "y": before.y},
			"after": {"x": after.x, "y": after.y},
			"visible_rect": {"x": visible.position.x, "y": visible.position.y,
				"w": visible.size.x, "h": visible.size.y},
			"content_scale_mode": window.content_scale_mode,
			"applied": applied,
		},
	}


# --- UI Baseline & Diff ---

func _cmd_save_ui_baseline(_args: Dictionary) -> Dictionary:
	var snapshot: Array = _capture_ui_snapshot_flat()
	var json_str: String = JSON.stringify(snapshot, "\t")
	var file: FileAccess = FileAccess.open("user://ui_baseline.json", FileAccess.WRITE)
	if file == null:
		return {"success": false, "message": "Failed to write baseline file"}
	file.store_string(json_str)
	file.close()
	return {
		"success": true,
		"message": "Baseline saved with %d nodes" % snapshot.size(),
		"data": {"nodes_saved": snapshot.size()},
	}


func _cmd_ui_snapshot_diff(_args: Dictionary) -> Dictionary:
	if not FileAccess.file_exists("user://ui_baseline.json"):
		return {"success": false, "message": "No baseline found. Run save_ui_baseline first."}

	var file: FileAccess = FileAccess.open("user://ui_baseline.json", FileAccess.READ)
	var baseline_text: String = file.get_as_text()
	file.close()
	var baseline: Variant = JSON.parse_string(baseline_text)
	if baseline == null or not baseline is Array:
		return {"success": false, "message": "Failed to parse baseline JSON"}

	var current: Array = _capture_ui_snapshot_flat()
	var diffs: Array = []
	var threshold: float = 5.0

	# Build lookup by node path
	var baseline_map: Dictionary = {}
	for node_data: Dictionary in baseline:
		baseline_map[node_data["path"]] = node_data

	for node_data: Dictionary in current:
		var path: String = node_data["path"]
		if baseline_map.has(path):
			var base: Dictionary = baseline_map[path]
			var dx: float = absf(node_data["x"] - base["x"])
			var dy: float = absf(node_data["y"] - base["y"])
			var dw: float = absf(node_data["w"] - base["w"])
			var dh: float = absf(node_data["h"] - base["h"])
			if dx > threshold or dy > threshold or dw > threshold or dh > threshold:
				diffs.append({
					"path": path,
					"type": "changed",
					"position_delta": [dx, dy],
					"size_delta": [dw, dh],
					"baseline": {"x": base["x"], "y": base["y"], "w": base["w"], "h": base["h"]},
					"current": {"x": node_data["x"], "y": node_data["y"], "w": node_data["w"], "h": node_data["h"]},
				})
		else:
			diffs.append({"path": path, "type": "new_node"})

	for path: String in baseline_map:
		var found: bool = false
		for node_data: Dictionary in current:
			if node_data["path"] == path:
				found = true
				break
		if not found:
			diffs.append({"path": path, "type": "removed_node"})

	return {
		"success": diffs.size() == 0,
		"message": "%d diffs found" % diffs.size() if diffs.size() > 0 else "No layout drift detected",
		"data": {
			"status": "pass" if diffs.size() == 0 else "drift_detected",
			"diffs": diffs,
		},
	}


func _capture_ui_snapshot_flat() -> Array:
	var elements: Array = []
	var scene: Node = get_tree().current_scene
	if scene:
		_snapshot_flat_recursive(scene, elements)
	return elements


func _snapshot_flat_recursive(node: Node, elements: Array) -> void:
	if node is Control and _is_effectively_visible(node):
		var control: Control = node as Control
		var rect: Rect2 = _screen_rect_of(control)
		elements.append({
			"path": str(control.get_path()),
			"name": str(control.name),
			"type": control.get_class(),
			"x": rect.position.x,
			"y": rect.position.y,
			"w": rect.size.x,
			"h": rect.size.y,
		})
	for child in node.get_children():
		_snapshot_flat_recursive(child, elements)


# --- UI Overlap Detection ---

func _check_interactive_overlaps(controls: Array) -> Array:
	var overlaps: Array = []
	for i in range(controls.size()):
		for j in range(i + 1, controls.size()):
			var rect_a: Rect2 = controls[i]["rect"]
			var rect_b: Rect2 = controls[j]["rect"]
			if rect_a.intersects(rect_b):
				var intersection: Rect2 = rect_a.intersection(rect_b)
				overlaps.append({
					"type": "interactive_overlap",
					"node_a": controls[i]["path"],
					"node_b": controls[j]["path"],
					"overlap_area": intersection.get_area(),
					"overlap_rect": {"x": intersection.position.x, "y": intersection.position.y, "w": intersection.size.x, "h": intersection.size.y},
				})
	return overlaps


## Pairs of NON-overlapping interactive controls whose rects are closer than
## `min_gap` px on either axis (a shared edge is gap 0). Overlapping pairs are
## already reported by _check_interactive_overlaps and are skipped here so one
## fault never prints twice.
func _check_interactive_gaps(controls: Array, min_gap: float) -> Array:
	var close: Array = []
	for i in range(controls.size()):
		for j in range(i + 1, controls.size()):
			var a: Rect2 = controls[i]["rect"]
			var b: Rect2 = controls[j]["rect"]
			if a.intersects(b):
				continue
			var dx: float = maxf(0.0, maxf(b.position.x - a.end.x, a.position.x - b.end.x))
			var dy: float = maxf(0.0, maxf(b.position.y - a.end.y, a.position.y - b.end.y))
			# The pair must be near on BOTH axes to count as touching: a button far
			# to the right of another shares no edge whatever their y-overlap.
			var gap: float = maxf(dx, dy)
			if gap < min_gap:
				close.append({
					"node_a": controls[i]["path"], "node_b": controls[j]["path"],
					"gap": gap, "dx": dx, "dy": dy,
				})
	return close


# --- TileMap Inspection Handlers ---

## Maximum cells a single tilemap_cells reply will carry. A full world layer can
## be tens of thousands of cells; past this the reply reports truncated:true and
## the caller should pass a rect.
const TILEMAP_CELLS_MAX: int = 2000


## Builds per-cell accessors for a TileMap (needs the layer index) or a
## TileMapLayer (ignores it). Returns {} for anything else; callers turn that
## into the failure message.
func _tilemap_accessors(node: Node, layer: int) -> Dictionary:
	if node is TileMapLayer:
		var tl: TileMapLayer = node as TileMapLayer
		return {
			"used": tl.get_used_cells(),
			"atlas": func(c: Vector2i) -> Vector2i: return tl.get_cell_atlas_coords(c),
			"source": func(c: Vector2i) -> int: return tl.get_cell_source_id(c),
		}
	if node is TileMap:
		var tm: TileMap = node as TileMap
		return {
			"used": tm.get_used_cells(layer),
			"atlas": func(c: Vector2i) -> Vector2i: return tm.get_cell_atlas_coords(layer, c),
			"source": func(c: Vector2i) -> int: return tm.get_cell_source_id(layer, c),
		}
	return {}


## Reads a tilemap's used cells as data instead of pixels (G-032): a screenshot
## shows ~a screenful of tiles and a human guess at what they are; this returns
## the actual source/atlas ids, which are the persistence key in most projects.
## args: { "node_path": String, "layer": int = 0 (TileMap only),
##         "rect": [x, y, w, h] optional clip in cell coordinates }.
## Output is capped at TILEMAP_CELLS_MAX cells with data.truncated = true --
## pass a rect to narrow the window rather than paging.
func _cmd_tilemap_cells(args: Dictionary) -> Dictionary:
	var node_path: String = str(args.get("node_path", ""))
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}
	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s (also tried under /root)" % node_path}

	var layer: int = int(args.get("layer", 0))
	var access: Dictionary = _tilemap_accessors(node, layer)
	if access.is_empty():
		return {"success": false, "message": "Node %s is a %s, not a TileMap or TileMapLayer" % [resolved["path"], node.get_class()]}

	var clip: Variant = null
	if args.has("rect"):
		var comps: Array = _numeric_components(args["rect"], ["x", "y", "w", "h"], 4, 4)
		if comps.size() != 4:
			return {"success": false, "message": "'rect' must be [x, y, w, h] in cell coordinates"}
		clip = Rect2i(roundi(comps[0]), roundi(comps[1]), roundi(comps[2]), roundi(comps[3]))

	var get_atlas: Callable = access["atlas"]
	var get_source: Callable = access["source"]
	var cells: Array = []
	var considered: int = 0
	var truncated: bool = false
	for cell: Vector2i in access["used"]:
		if clip != null and not (clip as Rect2i).has_point(cell):
			continue
		considered += 1
		if cells.size() >= TILEMAP_CELLS_MAX:
			truncated = true
			continue
		var atlas: Vector2i = get_atlas.call(cell)
		cells.append({
			"x": cell.x,
			"y": cell.y,
			"source_id": get_source.call(cell),
			"atlas": {"x": atlas.x, "y": atlas.y},
		})

	return {
		"success": true,
		"message": "%d cell%s%s%s" % [
			considered, "" if considered == 1 else "s",
			" in rect" if clip != null else "",
			" (reply truncated at %d; pass a rect)" % TILEMAP_CELLS_MAX if truncated else "",
		],
		"data": {
			"node_path": resolved["path"],
			"layer": layer,
			"cells": cells,
			"count": considered,
			"truncated": truncated,
		},
	}


## Flood-fills connected components (4-neighbor) among used cells matching an
## atlas coordinate (G-065): "is this island one landmass or three" is a
## structural question a screenshot cannot answer and a cell dump makes the
## caller re-derive. args: { "node_path": String, "layer": int = 0,
## "atlas": [x, y], "source_id": int optional (any source when absent) }.
## data.components is sorted by size, largest first.
func _cmd_tilemap_region(args: Dictionary) -> Dictionary:
	var node_path: String = str(args.get("node_path", ""))
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}
	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s (also tried under /root)" % node_path}

	var layer: int = int(args.get("layer", 0))
	var access: Dictionary = _tilemap_accessors(node, layer)
	if access.is_empty():
		return {"success": false, "message": "Node %s is a %s, not a TileMap or TileMapLayer" % [resolved["path"], node.get_class()]}

	var atlas_comps: Array = _numeric_components(args.get("atlas"), ["x", "y"], 2, 2)
	if atlas_comps.size() != 2:
		return {"success": false, "message": "tilemap_region requires 'atlas' as [x, y]"}
	var atlas: Vector2i = Vector2i(roundi(atlas_comps[0]), roundi(atlas_comps[1]))
	var filter_source: bool = args.has("source_id")
	var source_id: int = int(args.get("source_id", -1))

	var get_atlas: Callable = access["atlas"]
	var get_source: Callable = access["source"]
	var matching: Dictionary = {}
	for cell: Vector2i in access["used"]:
		if get_atlas.call(cell) != atlas:
			continue
		if filter_source and get_source.call(cell) != source_id:
			continue
		matching[cell] = true

	var visited: Dictionary = {}
	var components: Array = []
	var neighbors: Array = [Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)]
	for start: Vector2i in matching:
		if visited.has(start):
			continue
		var stack: Array = [start]
		visited[start] = true
		var count: int = 0
		var min_c: Vector2i = start
		var max_c: Vector2i = start
		while not stack.is_empty():
			var cell: Vector2i = stack.pop_back()
			count += 1
			min_c = Vector2i(mini(min_c.x, cell.x), mini(min_c.y, cell.y))
			max_c = Vector2i(maxi(max_c.x, cell.x), maxi(max_c.y, cell.y))
			for offset: Vector2i in neighbors:
				var next: Vector2i = cell + offset
				if matching.has(next) and not visited.has(next):
					visited[next] = true
					stack.append(next)
		components.append({
			"cells": count,
			"bounds": {
				"x": min_c.x,
				"y": min_c.y,
				"w": max_c.x - min_c.x + 1,
				"h": max_c.y - min_c.y + 1,
			},
		})
	components.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a["cells"] > b["cells"])

	return {
		"success": true,
		"message": "%d matching cell%s in %d component%s" % [
			matching.size(), "" if matching.size() == 1 else "s",
			components.size(), "" if components.size() == 1 else "s",
		],
		"data": {
			"node_path": resolved["path"],
			"layer": layer,
			"atlas": {"x": atlas.x, "y": atlas.y},
			"source_id": source_id if filter_source else null,
			"components": components,
			"total": matching.size(),
		},
	}


# --- Script Census (G-074b / G-068) ---

func _track_script(node: Node) -> void:
	var script: Script = node.get_script() as Script
	if script != null and not script.resource_path.is_empty():
		_scripts_seen[script.resource_path] = true


func _on_node_added_track_script(node: Node) -> void:
	_track_script(node)


func _seed_scripts_seen(node: Node) -> void:
	_track_script(node)
	for child: Node in node.get_children():
		_seed_scripts_seen(child)


## Reports every distinct script resource_path that has been attached to a node
## in the tree since launch. This is what lets a verify run measure REACH against
## the whole session rather than one scene-tree snapshot: a node that lived and
## died between snapshots still counts here.
## Wire contract - data keys: scripts (sorted Array of String), count (int).
func _cmd_scripts_seen(_args: Dictionary) -> Dictionary:
	var scripts: Array = _scripts_seen.keys()
	scripts.sort()
	return {
		"success": true,
		"message": "%d distinct script%s seen since launch" % [scripts.size(), "" if scripts.size() == 1 else "s"],
		"data": {"scripts": scripts, "count": scripts.size()},
	}


# --- Entry hook / entry points (gh#29) ---
#
# Before this, `entry_hook` accepted a value, validated fine, and was read by
# nothing - the harness's own worst failure mode (a check that could not run must
# be NAMED, not silently absent) applied to its own config. A project set it,
# `ping` kept reporting the title screen, and the natural conclusion was that the
# GAME was at fault. _entry_hook_status exists so that never happens again:
# "not_configured" and "fired" are both facts a caller can act on, and a typo'd
# node_path or method name is now an error, not silence.

## How long _resolve_entry_hook keeps retrying before reporting "node not found".
## Autoloads run before the main scene is instantiated, so the target usually does
## not exist at _ready() - this is not a bug in the caller's config. Polled rather
## than driven off node_added, so it terminates even if the scene tree stops
## changing entirely (a genuine typo never fires another node_added to wake it).
const ENTRY_HOOK_TIMEOUT_SEC: float = 10.0
const ENTRY_HOOK_POLL_SEC: float = 0.2

## Fire-and-forget from _ready(): does not block bridge startup on a scene that
## might take seconds to load, or might never contain the configured node at all.
func _start_entry_hook() -> void:
	var hook: Variant = _config.get("entry_hook", {})
	if not (hook is Dictionary):
		return
	var node_path: String = str(hook.get("node_path", ""))
	var method: String = str(hook.get("method", ""))
	if node_path.is_empty() and method.is_empty():
		return  # "not_configured" is already the default; nothing to do.
	if node_path.is_empty() or method.is_empty():
		_entry_hook_status = ("misconfigured: both node_path and method are "
			+ "required (got node_path=%r method=%r)") % [node_path, method]
		push_error("DevTools: entry_hook " + _entry_hook_status)
		return
	_resolve_and_fire_hook(node_path, method, [])


func _resolve_and_fire_hook(node_path: String, method: String, args: Array) -> void:
	var elapsed: float = 0.0
	var resolved: Dictionary = _resolve_node(node_path)
	while resolved["node"] == null and elapsed < ENTRY_HOOK_TIMEOUT_SEC:
		await get_tree().create_timer(ENTRY_HOOK_POLL_SEC).timeout
		elapsed += ENTRY_HOOK_POLL_SEC
		resolved = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		_entry_hook_status = "node not found: %s (waited %.0fs)" % [node_path, ENTRY_HOOK_TIMEOUT_SEC]
		push_error("DevTools: entry_hook " + _entry_hook_status)
		return
	if not node.has_method(method):
		_entry_hook_status = "no such method: %s.%s" % [node_path, method]
		push_error("DevTools: entry_hook " + _entry_hook_status)
		return
	_entry_hook_result = node.callv(method, args)
	_entry_hook_status = "fired"
	print("DevTools: entry_hook fired %s.%s()%s" % [
		node_path, method,
		(" -> %s" % _entry_hook_result) if _entry_hook_result != null else ""])


## `entry_points`: named alternates to entry_hook, reached on demand rather than
## fired automatically - a boss/shop/level's own runtime path instead of only a
## code read (see commands/verify.md for when an agent should reach for one).
## Wire contract - args: {"name": String}. data keys: name, node_path, method,
## result (the method's own return value, or null), scene_changed (bool).
func _cmd_fire_entry_point(args: Dictionary) -> Dictionary:
	var name: String = str(args.get("name", ""))
	if name.is_empty():
		return {"success": false, "message": "fire_entry_point requires 'name'", "data": {}}
	var points: Variant = _config.get("entry_points", {})
	if not (points is Dictionary) or not points.has(name):
		var known: Array = (points.keys() if points is Dictionary else [])
		known.sort()
		return {"success": false,
			"message": "no entry_point named %r in devtools_config.json. Known: %s"
				% [name, ", ".join(known) if not known.is_empty() else "(none configured)"],
			"data": {}}
	var point: Variant = points[name]
	if not (point is Dictionary):
		return {"success": false,
			"message": "entry_points.%s is not an object" % name, "data": {}}
	var node_path: String = str(point.get("node_path", ""))
	var method: String = str(point.get("method", ""))
	if node_path.is_empty() or method.is_empty():
		return {"success": false,
			"message": ("entry_points.%s is misconfigured: both node_path and "
				+ "method are required (got node_path=%r method=%r)")
				% [name, node_path, method],
			"data": {}}
	var args_list: Array = point.get("args", [])
	if not (args_list is Array):
		args_list = []

	var scene_changed: bool = false
	var scene_path: String = str(point.get("scene", ""))
	if not scene_path.is_empty():
		var current: Node = get_tree().current_scene
		var current_path: String = current.scene_file_path if current != null else ""
		if current_path != scene_path:
			var err: int = get_tree().change_scene_to_file(scene_path)
			if err != OK:
				return {"success": false,
					"message": "entry_points.%s: change_scene_to_file(%s) failed: %s"
						% [name, scene_path, error_string(err)],
					"data": {}}
			scene_changed = true
			# The new scene is not in the tree yet even after a successful
			# change_scene_to_file() call - it is deferred to the end of the
			# frame. Same reasoning as _resolve_and_fire_hook: poll, don't guess
			# a frame count.
			var elapsed: float = 0.0
			var resolved: Dictionary = _resolve_node(node_path)
			while resolved["node"] == null and elapsed < ENTRY_HOOK_TIMEOUT_SEC:
				await get_tree().create_timer(ENTRY_HOOK_POLL_SEC).timeout
				elapsed += ENTRY_HOOK_POLL_SEC
				resolved = _resolve_node(node_path)

	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false,
			"message": "entry_points.%s: node not found: %s" % [name, node_path],
			"data": {"scene_changed": scene_changed}}
	if not node.has_method(method):
		return {"success": false,
			"message": "entry_points.%s: no such method: %s.%s" % [name, node_path, method],
			"data": {"scene_changed": scene_changed}}
	var result: Variant = node.callv(method, args_list)
	return {
		"success": true,
		"message": "entry_points.%s fired %s.%s()%s" % [
			name, node_path, method, (" -> %s" % result) if result != null else ""],
		"data": {"name": name, "node_path": node_path, "method": method,
			"result": _serialize_variant(result), "scene_changed": scene_changed},
	}


# --- Utility Functions ---

# --- Query / interaction primitives ---

## Finds nodes by class, group and property predicates, returning their paths.
##
## Why a read and not just clear_nodes: a boss among an EnemySpawner's children is
## `@CharacterBody2D@385`, `@CharacterBody2D@388`, ... and nothing in a scene-tree
## snapshot says which one is the Elite. Identifying it cost one get_state round
## trip per child -- six calls, twice -- and by the time the loop finished the
## engine had rotated the auto-names, so a path that answered 20 seconds earlier
## 404'd (gather:G-109). clear_nodes already does exactly this matching internally
## in order to FREE nodes; exposing the same predicate as a read costs little.
##
## args: { "class": String (an engine class OR a script `class_name`, subclasses
##                  included; a name that is neither fails rather than matching nothing),
##         "group": String, "method": String,
##         "where": Dictionary (property -> expected value, dotted paths allowed),
##         "properties": Array[String] (extra properties to report per hit),
##         "calls": Array[String] (zero-arg methods to call and report per hit),
##         "root": String (subtree to search, default the whole tree),
##         "limit": int (default 200) }
## data keys: nodes (Array of {path, name, type, properties[, property_errors]
##            [, calls, call_errors]}), count, truncated.
func _cmd_find_nodes(args: Dictionary) -> Dictionary:
	var root: Node = get_tree().root
	var root_path: String = args.get("root", "")
	if not root_path.is_empty():
		var resolved: Dictionary = _resolve_node(root_path)
		if resolved["node"] == null:
			return {"success": false, "message": "Root node not found: %s" % root_path}
		root = resolved["node"]

	var cls: String = args.get("class", "")
	var group: String = args.get("group", "")
	var method: String = args.get("method", "")
	var where: Dictionary = args.get("where", {}) if args.get("where") is Dictionary else {}
	var report: Array = args.get("properties", []) if args.get("properties") is Array else []
	# G-005: a getter read beside each hit, so identifying a node whose auto-name
	# changes every launch and reading it are one round-trip, not two.
	var calls: Array = args.get("calls", []) if args.get("calls") is Array else []
	var limit: int = int(args.get("limit", 200))

	# A class that names nothing is a typo, not an absence (gh#15.2). Refusing
	# it here is what stops the empty result below from diagnosing the --where
	# predicate when the candidate set was empty before the predicate ran.
	if not cls.is_empty() and not _class_is_known(cls):
		var known: PackedStringArray = PackedStringArray()
		for entry: Dictionary in ProjectSettings.get_global_class_list():
			known.append(String(entry.get("class", "")))
		known.sort()
		return {
			"success": false,
			"message": "Unknown class '%s': not an engine class and no script declares class_name %s (script classes in this project: %s)" % [
				cls, cls, ", ".join(known) if not known.is_empty() else "none"],
		}

	var candidates: Array[Node] = []
	if cls.is_empty() and group.is_empty() and method.is_empty():
		# `where` alone is a legitimate query ("every node whose type is Elite"),
		# and _collect_matching_nodes matches nothing when every selector is empty
		# -- it is written for clear_nodes, where an empty selector must refuse.
		_collect_all_nodes(root, candidates)
	else:
		_collect_matching_nodes(root, group, method, cls, candidates)

	var hits: Array = []
	var truncated: bool = false
	var where_seen: Dictionary = {}
	for node: Node in candidates:
		if not _matches_where(node, where, where_seen):
			continue
		if hits.size() >= limit:
			truncated = true
			break
		var props: Dictionary = {}
		var prop_errors: Dictionary = {}
		for entry: Variant in report:
			var name: String = str(entry)
			var walk: Dictionary = _resolve_property_path(node, name)
			if walk["ok"]:
				props[name] = _serialize_variant(walk["value"])
			else:
				# `null` alone is indistinguishable from a property that genuinely
				# holds null (H-046); carry the resolver's reason beside it.
				props[name] = null
				prop_errors[name] = walk["reason"]
		var call_results: Dictionary = {}
		var call_errors: Dictionary = {}
		for entry: Variant in calls:
			var name: String = str(entry)
			if not node.has_method(name):
				call_errors[name] = "%s has no method %s" % [node.get_class(), name]
				continue
			call_results[name] = _serialize_variant(node.call(name))
		var hit: Dictionary = {
			"path": str(node.get_path()),
			"name": str(node.name),
			"type": node.get_class(),
			"properties": props,
		}
		if not prop_errors.is_empty():
			hit["property_errors"] = prop_errors
		if not calls.is_empty():
			hit["calls"] = call_results
			if not call_errors.is_empty():
				hit["call_errors"] = call_errors
		hits.append(hit)

	# An empty result is the one answer that must never be silent: "no node has
	# mouse_filter=0" and "nothing here has a property by that name" are opposite
	# facts and used to print identically, which is how a UI got cleared of exactly
	# the fault it had (moving-in:G-011). Same failure the test runner's
	# `Selected: N of M` line exists to prevent.
	var message: String = "%d node(s) matched" % hits.size()
	if hits.is_empty() and candidates.is_empty() and not (cls.is_empty() and group.is_empty() and method.is_empty()):
		# The selector itself matched nothing, so the --where predicate never
		# ran; a "no candidate exposes 'x'" line here would send the reader
		# chasing a property name that was right all along (gh#15.2).
		var selector: PackedStringArray = PackedStringArray()
		if not cls.is_empty():
			selector.append("class %s" % cls)
		if not group.is_empty():
			selector.append("group %s" % group)
		if not method.is_empty():
			selector.append("method %s" % method)
		message += " -- no node in the tree matched %s%s" % [
			" / ".join(selector),
			"; the --where predicate was not evaluated" if not where.is_empty() else ""]
	elif hits.is_empty() and not where.is_empty():
		var parts: PackedStringArray = PackedStringArray()
		for key: Variant in where:
			var exposed: int = int(where_seen.get(str(key), 0))
			if exposed == 0:
				var reason: String = str(where_seen.get("reason_" + str(key), ""))
				parts.append("no candidate exposes '%s'%s" % [
					str(key), (" (%s)" % reason) if not reason.is_empty() else ""])
			else:
				parts.append("0 of %d matched on %s (%d of %d expose it)" % [
					candidates.size(), str(key), exposed, candidates.size()])
		message += " -- " + " ; ".join(parts)

	return {
		"success": true,
		"message": message,
		"data": {
			"nodes": hits,
			"count": hits.size(),
			"truncated": truncated,
			"candidates": candidates.size(),
			"where_seen": where_seen,
		},
	}


## Every key in `where` must equal the node's value at that (possibly dotted)
## property path. A property the node does not have is a NON-match, never an
## error: the predicate is applied across a heterogeneous subtree by design.
## `seen` is an out-parameter tallying, per predicate key, how many candidates
## actually EXPOSED that property, plus the resolver's reason when none did. It
## exists because a `--where` that matches nothing and a `--where` whose property
## name never resolved printed the identical empty result (moving-in:G-011): a
## reporter read one as the other and concluded the numeric comparison was broken.
## It is not -- the widening branch in _values_match has carried every numeric
## predicate since 0.8.0, and removing it reproduces that report exactly. What was
## actually missing is the denominator, and only this side can count it.
##
## Note the loop no longer returns early: a tally that stops at the first failing
## key cannot say "89 of 89 expose it", which is the whole point.
func _matches_where(node: Node, where: Dictionary, seen: Dictionary = {}) -> bool:
	var matched: bool = true
	for key: Variant in where:
		var walk: Dictionary = _resolve_property_path(node, str(key))
		if not walk["ok"]:
			# _resolve_property_path already writes a precise reason ("position is a
			# Vector3, not an object -- cannot read .x off it"). Keeping the first
			# one turns an empty result into a self-diagnosing message instead of a
			# second puzzle.
			var reason_key: String = "reason_" + str(key)
			if not seen.has(reason_key):
				seen[reason_key] = walk.get("reason", "")
			matched = false
			continue
		seen[str(key)] = int(seen.get(str(key), 0)) + 1
		if _values_match(walk["value"], where[key]):
			continue
		# A JSON array is the only spelling the bus can carry for a Vector2/3/4,
		# Color or Rect2, so coerce toward the property's own type before giving
		# up -- `--where position=[12,1,0]` against a node standing exactly there
		# returned nothing, because Vector3-vs-Array fails both _values_match and
		# the string fallback below. _coerce_arg is the same converter set_state
		# uses, so the two verbs now agree about what a value means.
		if where[key] is Array:
			var conversion: Dictionary = _coerce_arg(where[key], typeof(walk["value"]))
			if conversion["ok"] and _values_match(walk["value"], conversion["value"]):
				continue
		# Compare through the serialized form too, so a JSON string can match
		# a StringName / enum-backed value without the caller knowing which.
		# This does NOT rescue numbers: str(7.0) is "7.0" and str(7) is "7", so an
		# int/float pair never meets here. _values_match's widening branch is the
		# only thing carrying numeric predicates -- do not add a second one.
		if str(_serialize_variant(walk["value"])) != str(where[key]):
			matched = false
	return matched


## Emits `pressed` on the nearest BaseButton at or under a node path.
##
## Every panel added through the harness was previously verified one layer below
## what ships: the callables (`_on_set_weather`, `_on_strike`) were driven through
## run_method, which proves the ACTIONS and not the BUTTONS -- a mis-wired
## `pressed.connect` shipped green (gather:G-119). This presses the button the way
## a finger does.
##
## args: { "node_path": String, "toggle": bool (for a toggle_mode button, the
##         pressed state to set before emitting) }
## data keys: node_path, type, disabled, button_pressed.
func _cmd_press(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}
	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path}

	var button: BaseButton = node as BaseButton
	if button == null:
		# One level of convenience: a container path is what scene-tree gives you.
		for child: Node in node.get_children():
			if child is BaseButton:
				button = child as BaseButton
				break
	if button == null:
		return {
			"success": false,
			"message": "No BaseButton at or directly under %s (%s)" % [
				resolved["path"], node.get_class()],
			"data": {"node_path": resolved["path"], "type": node.get_class()},
		}

	if button.disabled:
		# A disabled button ignores a real press too. Refusing is the honest
		# answer; silently emitting would manufacture a state no player can reach.
		return {
			"success": false,
			"message": "%s is disabled; a real press would do nothing either" % button.get_path(),
			"data": {"node_path": str(button.get_path()), "type": button.get_class(),
				"disabled": true, "button_pressed": button.button_pressed},
		}

	if button.toggle_mode and args.has("toggle"):
		button.button_pressed = bool(args["toggle"])
	button.emit_signal("pressed")

	return {
		"success": true,
		"message": "Pressed %s" % button.get_path(),
		"data": {
			"node_path": str(button.get_path()),
			"type": button.get_class(),
			"disabled": false,
			"button_pressed": button.button_pressed,
		},
	}


## Casts a ray through the 2D physics space and reports what it hit.
##
## The harness could say what tiles are where (tilemap_cells) and where a node is
## (node_bounds), but nothing could say what a given collision_mask would actually
## HIT -- which is the only form the question takes once a project has more than
## one physics layer. Every project that needed it wrote its own `los_probe`
## (gather:G-136). Note the engine's own sharp edge, which this verb inherits and
## the message names: a ray STARTING INSIDE a shape reports nothing, so five
## bisecting probes can all come back `clear` while a wall sits between them.
##
## Dimension is decided by the coordinates (moving-in:G-023): [x,y] queries the
## 2D space, [x,y,z] queries the 3D space, and data.space says which. Before
## 0.22.0 this was 2D-only and answered `clear` in a 3D project - with the
## inside-a-shape caveat attached, which sent a session hunting for a geometry
## bug that did not exist. A 2D query is now REFUSED when the 3D space has
## bodies and the 2D space has none, naming the fix; a verb that answers
## confidently in the wrong dimension is worse than one that is absent.
##
## args: { "from": [x,y] | [x,y,z], "to": same arity, "mask": int (default all
##         layers), "areas": bool (also hit Areas, default false),
##         "exclude": Array[String] of node paths }
## data keys: clear (bool), collider (String path or ""), collider_class,
## position {x,y[,z]}, normal {x,y[,z]}, mask, mask_names (Array), space ("2d"|"3d").
func _cmd_raycast(args: Dictionary) -> Dictionary:
	var raw_from: Variant = args.get("from")
	var raw_to: Variant = args.get("to")
	var from3: Variant = _parse_vector3_or_null(raw_from)
	var to3: Variant = _parse_vector3_or_null(raw_to)
	if from3 != null and to3 != null:
		return _raycast_3d(from3, to3, args)
	if (from3 == null) != (to3 == null):
		return {"success": false, "message": "raycast: from and to must have the same arity - both [x, y] (2D) or both [x, y, z] (3D)"}

	var from: Variant = _parse_vector2_or_null(raw_from)
	var to: Variant = _parse_vector2_or_null(raw_to)
	if from == null or to == null:
		return {"success": false, "message": "raycast needs from and to as [x, y] (2D) or [x, y, z] (3D)"}

	var world: World2D = get_tree().root.world_2d
	if world == null:
		return {"success": false, "message": "No World2D (is this a headless run with no 2D space?)"}

	# A 2D query in a project whose colliders all live in 3D can only ever say
	# `clear`; refuse it rather than answer it. Counted from the tree (static
	# bodies are not "active" physics objects, so the monitors cannot tell).
	var colliders: Array[int] = _count_collision_objects()
	if colliders[0] == 0 and colliders[1] > 0:
		return {
			"success": false,
			"message": "raycast: this tree has %d CollisionObject3D(s) and no CollisionObject2D - a 2D ray can only ever report clear here. Pass 3-component coordinates: --from X,Y,Z --to X,Y,Z" % colliders[1],
			"data": {"collision_objects_2d": colliders[0], "collision_objects_3d": colliders[1]},
		}

	var params: PhysicsRayQueryParameters2D = PhysicsRayQueryParameters2D.create(from, to)
	params.collision_mask = int(args.get("mask", 0xFFFFFFFF))
	params.collide_with_areas = bool(args.get("areas", false))
	params.collide_with_bodies = true

	var excluded: Array[RID] = []
	for entry: Variant in (args.get("exclude", []) if args.get("exclude") is Array else []):
		var found: Dictionary = _resolve_node(str(entry))
		var node: Node = found["node"]
		if node != null and node is CollisionObject2D:
			excluded.append((node as CollisionObject2D).get_rid())
	params.exclude = excluded

	var hit: Dictionary = world.direct_space_state.intersect_ray(params)
	var mask_names: Array = _layer_names_for_mask(params.collision_mask)

	if hit.is_empty():
		return {
			"success": true,
			"message": ("clear from (%.1f, %.1f) to (%.1f, %.1f) on mask %d [%s] -- note that a ray "
				+ "STARTING INSIDE a shape reports nothing") % [
				from.x, from.y, to.x, to.y, params.collision_mask, ", ".join(PackedStringArray(mask_names))],
			"data": {
				"clear": true, "collider": "", "collider_class": "",
				"position": {}, "normal": {},
				"mask": params.collision_mask, "mask_names": mask_names, "space": "2d",
			},
		}

	var collider: Variant = hit.get("collider")
	var collider_path: String = str((collider as Node).get_path()) if collider is Node else ""
	var point: Vector2 = hit.get("position", Vector2.ZERO)
	var normal: Vector2 = hit.get("normal", Vector2.ZERO)
	return {
		"success": true,
		"message": "blocked by %s at (%.1f, %.1f)" % [
			collider_path if not collider_path.is_empty() else "an unnamed collider",
			point.x, point.y],
		"data": {
			"clear": false,
			"collider": collider_path,
			"collider_class": (collider as Object).get_class() if collider is Object else "",
			"position": {"x": point.x, "y": point.y},
			"normal": {"x": normal.x, "y": normal.y},
			"mask": params.collision_mask,
			"mask_names": mask_names,
			"space": "2d",
		},
	}


## [CollisionObject2D count, CollisionObject3D count] over the whole tree.
func _count_collision_objects() -> Array[int]:
	var n2: int = 0
	var n3: int = 0
	var stack: Array[Node] = [get_tree().root]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is CollisionObject2D:
			n2 += 1
		elif n is CollisionObject3D:
			n3 += 1
		for c: Node in n.get_children(true):
			stack.append(c)
	return [n2, n3]


## The 3D half of _cmd_raycast. Same contract, Vector3 in and out, layer names
## from layer_names/3d_physics.
func _raycast_3d(from: Vector3, to: Vector3, args: Dictionary) -> Dictionary:
	var world: World3D = get_tree().root.world_3d
	if world == null:
		return {"success": false, "message": "No World3D (is this a headless run with no 3D space?)"}
	var params: PhysicsRayQueryParameters3D = PhysicsRayQueryParameters3D.create(from, to)
	params.collision_mask = int(args.get("mask", 0xFFFFFFFF))
	params.collide_with_areas = bool(args.get("areas", false))
	params.collide_with_bodies = true
	var excluded: Array[RID] = []
	for entry: Variant in (args.get("exclude", []) if args.get("exclude") is Array else []):
		var found: Dictionary = _resolve_node(str(entry))
		var node: Node = found["node"]
		if node != null and node is CollisionObject3D:
			excluded.append((node as CollisionObject3D).get_rid())
	params.exclude = excluded

	var hit: Dictionary = world.direct_space_state.intersect_ray(params)
	var mask_names: Array = _layer_names_for_mask(params.collision_mask, "3d_physics")
	if hit.is_empty():
		return {
			"success": true,
			"message": ("clear from (%.1f, %.1f, %.1f) to (%.1f, %.1f, %.1f) on mask %d [%s] -- note that a ray "
				+ "STARTING INSIDE a shape reports nothing") % [
				from.x, from.y, from.z, to.x, to.y, to.z, params.collision_mask, ", ".join(PackedStringArray(mask_names))],
			"data": {
				"clear": true, "collider": "", "collider_class": "",
				"position": {}, "normal": {},
				"mask": params.collision_mask, "mask_names": mask_names, "space": "3d",
			},
		}
	var collider: Variant = hit.get("collider")
	var collider_path: String = str((collider as Node).get_path()) if collider is Node else ""
	var point: Vector3 = hit.get("position", Vector3.ZERO)
	var normal: Vector3 = hit.get("normal", Vector3.ZERO)
	return {
		"success": true,
		"message": "blocked by %s at (%.2f, %.2f, %.2f)" % [
			collider_path if not collider_path.is_empty() else "an unnamed collider",
			point.x, point.y, point.z],
		"data": {
			"clear": false,
			"collider": collider_path,
			"collider_class": (collider as Object).get_class() if collider is Object else "",
			"position": {"x": point.x, "y": point.y, "z": point.z},
			"normal": {"x": normal.x, "y": normal.y, "z": normal.z},
			"mask": params.collision_mask,
			"mask_names": mask_names,
			"space": "3d",
		},
	}


## Resolves a 2D collision mask to the project's own layer names, because the
## whole class of bug here is a number nobody can read. Unnamed layers report as
## "layer_N" so a bit is never silently dropped from the list.
func _layer_names_for_mask(mask: int, group: String = "2d_physics") -> Array:
	var names: Array = []
	for bit: int in range(32):
		if mask & (1 << bit) == 0:
			continue
		var setting: String = "layer_names/%s/layer_%d" % [group, bit + 1]
		var name: String = str(ProjectSettings.get_setting(setting, ""))
		names.append(name if not name.is_empty() else "layer_%d" % (bit + 1))
	return names


## Reads back pixels from the rendered frame so a colour claim is assertable.
##
## Confirming "this sprite renders blue" previously meant writing a zlib/PNG
## reader inline -- ~30 lines of scanline defiltering that every visual assertion
## would have to carry (gather:G-121). The viewport texture is already in hand;
## this is the same capture path `screenshot` uses, summarised instead of saved.
##
## args: { "rect": [x, y, w, h] (default the whole viewport),
##         "points": [[x, y], ...] (0.49.0, gh#49: sample exactly these pixels instead
##                    of a rect - a cue is a point, and a 1x1 rect reads like the
##                    workaround it is),
##         "expect": ["RRGGBB", ...] (0.49.0, gh#49: count the sampled pixels within
##                    `tolerance` per 8-bit channel of each colour - a 3.6 px pip
##                    inside a 5x5 box is a few percent and `dominant` will never
##                    name it, however plainly it is drawn),
##         "tolerance": int per channel 0..255 (default 8) }
## data keys: rect{x,y,w,h}, pixels (int), mean{r,g,b}, brightest{r,g,b},
## darkest{r,g,b}, dominant{r,g,b} (the most common colour after quantising to
## 5 bits per channel), dominant_share (0..1); with points: points[{x,y,color}];
## with expect: expected[{color, count, fraction, absent}], absent (bool: any
## expected colour appeared zero times - the verb still succeeds; the CLIENT
## exits 1 on it, the way findings does).
func _cmd_sample_pixels(args: Dictionary) -> Dictionary:
	# Argument contract first (0.49.0): a bad `expect` or `points` is refused by name
	# whether or not there is a framebuffer, so the headless contract table can prove
	# the parsing that the pixel loop below (which needs an image) cannot be asked to.
	var expect_raw: Variant = args.get("expect", [])
	var expected: Array = []
	if expect_raw is Array:
		for e: Variant in expect_raw as Array:
			var hex: String = str(e).trim_prefix("#")
			if hex.length() != 6 or not Color.html_is_valid("#" + hex):
				return {"success": false, "message": "expect: '%s' is not a hex colour (RRGGBB)" % str(e)}
			expected.append({"hex": hex.to_lower(), "color": Color.html("#" + hex), "count": 0})
	elif expect_raw != null:
		return {"success": false, "message": "expect must be an array of RRGGBB strings"}
	var raw_points: Variant = args.get("points", [])
	if raw_points != null and not (raw_points is Array):
		return {"success": false, "message": "points must be an array of [x, y] pairs"}
	if raw_points is Array:
		for pt: Variant in raw_points as Array:
			if not (pt is Array and (pt as Array).size() == 2):
				return {"success": false, "message": "points: each entry is [x, y]; got %s" % str(pt)}
	var tolerance: int = clampi(int(args.get("tolerance", 8)), 0, 255)
	var tol_f: float = float(tolerance) / 255.0

	var viewport: Viewport = get_viewport()
	var texture: ViewportTexture = viewport.get_texture() if viewport != null else null
	if texture == null:
		return {"success": false, "message": "No viewport texture (headless run with no rendering?)"}
	var image: Image = texture.get_image()
	if image == null:
		return {"success": false, "message": "Viewport produced no image (headless run with no rendering?)"}

	var full: Rect2i = Rect2i(0, 0, image.get_width(), image.get_height())
	var rect: Rect2i = full
	var raw_rect: Variant = args.get("rect")
	if raw_rect is Array and (raw_rect as Array).size() == 4:
		var a: Array = raw_rect
		rect = Rect2i(int(a[0]), int(a[1]), int(a[2]), int(a[3]))
	rect = rect.intersection(full)
	if rect.size.x <= 0 or rect.size.y <= 0:
		return {
			"success": false,
			"message": "rect %s does not overlap the %dx%d viewport" % [
				str(raw_rect), full.size.x, full.size.y],
		}

	# The pixels to visit: every pixel of the rect, or exactly the named points.
	var point_list: Array = []
	if raw_points is Array:
		for pt: Variant in raw_points as Array:
			var px: int = int((pt as Array)[0])
			var py: int = int((pt as Array)[1])
			if px < 0 or py < 0 or px >= full.size.x or py >= full.size.y:
				return {"success": false, "message": "point (%d, %d) is outside the %dx%d viewport" % [
					px, py, full.size.x, full.size.y]}
			point_list.append(Vector2i(px, py))
	var use_points: bool = not point_list.is_empty()

	var total: Vector3 = Vector3.ZERO
	var brightest: Color = Color(0, 0, 0)
	var darkest: Color = Color(1, 1, 1)
	var best_lum: float = -1.0
	var worst_lum: float = 2.0
	var buckets: Dictionary = {}
	var count: int = 0
	var point_colors: Array = []
	var visit: Array = []
	if use_points:
		visit = point_list
	else:
		for y: int in range(rect.position.y, rect.position.y + rect.size.y):
			for x: int in range(rect.position.x, rect.position.x + rect.size.x):
				visit.append(Vector2i(x, y))
	for v: Vector2i in visit:
		var c: Color = image.get_pixel(v.x, v.y)
		total += Vector3(c.r, c.g, c.b)
		count += 1
		var lum: float = c.get_luminance()
		if lum > best_lum:
			best_lum = lum
			brightest = c
		if lum < worst_lum:
			worst_lum = lum
			darkest = c
		var key: int = (int(c.r * 31.0) << 10) | (int(c.g * 31.0) << 5) | int(c.b * 31.0)
		buckets[key] = int(buckets.get(key, 0)) + 1
		for ex: Dictionary in expected:
			var ec: Color = ex["color"]
			if absf(c.r - ec.r) <= tol_f and absf(c.g - ec.g) <= tol_f and absf(c.b - ec.b) <= tol_f:
				ex["count"] = int(ex["count"]) + 1
		if use_points:
			point_colors.append({"x": v.x, "y": v.y, "color": c.to_html(false)})

	var mean: Vector3 = total / maxf(1.0, float(count))
	var top_key: int = -1
	var top_count: int = 0
	for key: Variant in buckets:
		if int(buckets[key]) > top_count:
			top_count = int(buckets[key])
			top_key = int(key)
	var dominant: Color = Color(
		float((top_key >> 10) & 31) / 31.0,
		float((top_key >> 5) & 31) / 31.0,
		float(top_key & 31) / 31.0) if top_key >= 0 else Color(0, 0, 0)

	var expected_out: Array = []
	var any_absent: bool = false
	for ex: Dictionary in expected:
		var n: int = int(ex["count"])
		expected_out.append({"color": ex["hex"], "count": n,
			"fraction": float(n) / maxf(1.0, float(count)), "absent": n == 0})
		if n == 0:
			any_absent = true
	var where: String = "%d px in (%d, %d, %d, %d)" % [
		count, rect.position.x, rect.position.y, rect.size.x, rect.size.y] if not use_points \
		else "%d point(s)" % count
	var msg: String = "%s: mean #%s, dominant #%s (%.0f%%)" % [
		where, Color(mean.x, mean.y, mean.z).to_html(false), dominant.to_html(false),
		100.0 * float(top_count) / maxf(1.0, float(count))]
	for eo: Dictionary in expected_out:
		msg += "; expected #%s (tol %d): %d px (%.1f%%)%s" % [
			eo["color"], tolerance, eo["count"], 100.0 * float(eo["fraction"]),
			" <- ABSENT" if eo["absent"] else ""]
	return {
		"success": true,
		"message": msg,
		"data": {
			"rect": {"x": rect.position.x, "y": rect.position.y,
				"w": rect.size.x, "h": rect.size.y},
			"pixels": count,
			"points": point_colors,
			"expected": expected_out,
			"tolerance": tolerance,
			"absent": any_absent,
			"mean": {"r": mean.x, "g": mean.y, "b": mean.z},
			"brightest": {"r": brightest.r, "g": brightest.g, "b": brightest.b},
			"darkest": {"r": darkest.r, "g": darkest.g, "b": darkest.b},
			"dominant": {"r": dominant.r, "g": dominant.g, "b": dominant.b},
			"dominant_share": float(top_count) / maxf(1.0, float(count)),
			# Stated so a disagreement with an offline crop is diagnosable
			# (moving-in:G-032): the rect is in the root viewport's rendered
			# image, origin top-left, y down - the SAME image and the same
			# Rect2i that `screenshot --region` crops - and the values are the
			# 8-bit sRGB the PNG would hold, mapped 0..1.
			"origin": "top-left",
			"space": "srgb_0_1",
			"image_size": {"w": full.size.x, "h": full.size.y},
			"same_image_as_screenshot": true,
		},
	}


## Calls a pure method over an integer range and returns the series.
##
## Calibrating a difficulty ramp meant hand-evaluating `size_for_day` /
## `health_mult_for_day` / `cost_for_parcel` / `xp_for_level` across 20+ days in
## prose arithmetic, every time anyone touched the constants -- and two
## arithmetic slips in one session were caught only by a second pass
## (gather:G-127). The game already knows these numbers; nothing could ask it for
## more than one at a time.
##
## args: { "node_path": String, "method": String, "from": int, "to": int,
##         "step": int (default 1), "args": Array (extra args after the sweep
##         value), "arg_index": int (which parameter the sweep value fills,
##         default 0) }
## data keys: node_path, method, points (Array of {input, value}), min, max, sum.
##
## The sweep is capped so a typo'd range cannot wedge the single-command bus.
func _cmd_curve(args: Dictionary) -> Dictionary:
	const MAX_POINTS: int = 500

	var node_path: String = args.get("node_path", "")
	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path}

	var method: String = args.get("method", "")
	if method.is_empty() or not node.has_method(method):
		return {"success": false, "message": "Node %s has no method: %s" % [resolved["path"], method]}

	var from: int = int(args.get("from", 0))
	var to: int = int(args.get("to", 0))
	var step: int = int(args.get("step", 1))
	if step == 0:
		return {"success": false, "message": "step must not be 0"}
	if (to - from) / step < 0:
		return {"success": false, "message": "step %d never reaches %d from %d" % [step, to, from]}
	var count: int = int(abs(float(to - from) / float(step))) + 1
	if count > MAX_POINTS:
		return {
			"success": false,
			"message": "curve refuses %d points; the maximum is %d (narrow the range or widen --step)" % [
				count, MAX_POINTS],
		}

	var extra: Array = args.get("args", []) if args.get("args") is Array else []
	var arg_index: int = int(args.get("arg_index", 0))

	var points: Array = []
	var lowest: Variant = null
	var highest: Variant = null
	var sum: float = 0.0
	var numeric: bool = true

	var i: int = from
	while (step > 0 and i <= to) or (step < 0 and i >= to):
		var call_args: Array = extra.duplicate()
		call_args.insert(clampi(arg_index, 0, call_args.size()), i)
		var value: Variant = node.callv(method, call_args)
		points.append({"input": i, "value": _serialize_variant(value)})
		if _is_number(value):
			sum += float(value)
			if lowest == null or float(value) < float(lowest):
				lowest = value
			if highest == null or float(value) > float(highest):
				highest = value
		else:
			numeric = false
		i += step

	var data: Dictionary = {
		"node_path": resolved["path"],
		"method": method,
		"points": points,
		"min": lowest,
		"max": highest,
		"sum": sum if numeric else null,
	}
	return {
		"success": true,
		"message": "%s(%d..%d step %d): %d point(s)%s" % [
			method, from, to, step, points.size(),
			", min %s max %s sum %s" % [str(lowest), str(highest), str(sum)] if numeric else " (non-numeric)"],
		"data": data,
	}


## Every Control a finger or cursor could actually hit this frame.
##
## The bug this exists for: a feature had a key binding, a desktop button, a
## passing suite and no way in on a phone. `validate_ui` reported 0 issues,
## correctly -- an unreachable panel is not a layout fault -- and finding it took
## forcing a feature flag, reading `visible` on two nodes, looking a button path
## up in the scene tree, computing a rect centre and sending two touch events.
## Every one of those reads is available and none of them is the question
## (gather:G-129). Diffing this verb between `set_feature --touchscreen true` and
## `false` names that class of bug outright.
##
## A Control counts as reachable when it is effectively visible, has a non-zero
## on-screen rect, does not ignore the mouse, and is either a BaseButton or
## carries a `gui_input`/`pressed` connection. `blocked_by` names a later sibling
## whose own rect covers this one's centre and which stops input -- the
## full-rect MOUSE_FILTER_STOP overlay that silently eats a button.
##
## data keys: controls (Array of {path, type, text, rect, on_screen, scroll_reachable,
## scroll_container, blocked_by, kind}), count, reachable, scroll_reachable (int: hittable
## only after scrolling a ScrollContainer -- reported, never a finding), viewport {w, h}.
func _cmd_reachable_ui(_args: Dictionary) -> Dictionary:
	var vp: Vector2 = _screen_reference_rect().size
	var found: Array = []
	_collect_reachable(get_tree().root, vp, found)

	# Occlusion: a Control drawn later covers one drawn earlier. Compare each
	# candidate's centre against every LATER candidate's rect.
	for i: int in found.size():
		var entry: Dictionary = found[i]
		var rect: Rect2 = entry["_rect"]
		var centre: Vector2 = rect.position + rect.size * 0.5
		for j: int in range(i + 1, found.size()):
			var other: Dictionary = found[j]
			if not other["_stops_input"]:
				continue
			if (other["_rect"] as Rect2).has_point(centre):
				entry["blocked_by"] = other["path"]
				break

	var out: Array = []
	var reachable: int = 0
	var scroll_only: int = 0
	for entry: Dictionary in found:
		var clean: Dictionary = entry.duplicate()
		clean.erase("_rect")
		clean.erase("_stops_input")
		if clean["on_screen"] and clean["blocked_by"].is_empty():
			reachable += 1
		elif bool(clean.get("scroll_reachable", false)) and clean["blocked_by"].is_empty():
			scroll_only += 1
		out.append(clean)

	var message: String = "%d of %d interactive control(s) are actually reachable at %dx%d" % [
		reachable, out.size(), int(vp.x), int(vp.y)]
	if scroll_only > 0:
		message += " (+%d reachable only by scrolling a ScrollContainer)" % scroll_only
	return {
		"success": true,
		"message": message,
		"data": {
			"controls": out,
			"count": out.size(),
			"reachable": reachable,
			"scroll_reachable": scroll_only,
			"viewport": {"w": vp.x, "h": vp.y},
			"geometry_trustworthy": _geometry_caveat().is_empty(),
			"geometry_caveat": _geometry_caveat(),
		},
	}


func _collect_reachable(node: Node, vp: Vector2, out: Array) -> void:
	if node is Control:
		var control: Control = node as Control
		var kind: String = ""
		if control is BaseButton:
			kind = "BaseButton"
		elif control.gui_input.get_connections().size() > 0:
			kind = "gui_input"
		if not kind.is_empty() and _is_effectively_visible(control) \
				and control.mouse_filter != Control.MOUSE_FILTER_IGNORE:
			# Screen space, not layer space: this verb answers "can a finger hit
			# it", so it has to measure where the renderer actually put it. A
			# scaled CanvasLayer used to make visible, clickable buttons report
			# OFF-SCREEN (gh#2).
			var rect: Rect2 = _screen_rect_of(control)
			if rect.size.x > 0.0 and rect.size.y > 0.0:
				var screen: Rect2 = Rect2(Vector2.ZERO, vp)
				var on_screen: bool = screen.intersects(rect)
				# A row inside a ScrollContainer is hittable only where the
				# container's own rect shows it, and a row past the fold is
				# reachable by scrolling, not unreachable (gh#16). Without this the
				# lower rows of every shop list gated `findings` forever, and a row
				# clipped by its container -- inside the viewport, invisible -- read
				# as hittable. Nearest ScrollContainer ancestor wins.
				var scroll_reachable: bool = false
				var scroller_path: String = ""
				var scroller: ScrollContainer = _nearest_scroll_container(control)
				if scroller != null:
					scroller_path = str(scroller.get_path())
					var window: Rect2 = _screen_rect_of(scroller).intersection(screen)
					on_screen = window.has_area() and window.intersects(rect)
					if not on_screen and window.has_area():
						# Reachable by scrolling iff the row lies within the scrolled
						# content's extent (the container's first Control child).
						for child: Node in scroller.get_children():
							if child is Control:
								scroll_reachable = _screen_rect_of(child as Control).intersects(rect)
								break
				out.append({
					"path": str(control.get_path()),
					"type": control.get_class(),
					"text": _get_control_text(control),
					"rect": {"x": rect.position.x, "y": rect.position.y,
						"w": rect.size.x, "h": rect.size.y},
					"on_screen": on_screen,
					"scroll_reachable": scroll_reachable,
					"scroll_container": scroller_path,
					"blocked_by": "",
					"kind": kind,
					"_rect": rect,
					"_stops_input": control.mouse_filter == Control.MOUSE_FILTER_STOP,
				})
	for child: Node in node.get_children():
		_collect_reachable(child, vp, out)


func _nearest_scroll_container(control: Control) -> ScrollContainer:
	var parent: Node = control.get_parent()
	while parent != null:
		if parent is ScrollContainer:
			return parent as ScrollContainer
		parent = parent.get_parent()
	return null


## ProjectSettings as the RUNNING game sees them (dave-game:G-003). A setting
## written into project.godot that did not apply -- a typo'd key, an editor
## overwrite, a value the engine ignores at runtime -- had no gate at all: lint
## and validate-all reported clean while the game rendered on the stock clear
## colour, and the only detection was opening a PNG. get_state cannot answer it
## because ProjectSettings is not a node.
##
## args: { "filter": String (prefix, e.g. "rendering/"; empty = every setting),
##         "names": Array[String] (exact keys; wins over filter) }
## data keys: settings ({name: value}), count, missing (names asked for that no
##            setting has), filter.
func _cmd_project_settings(args: Dictionary) -> Dictionary:
	var prefix: String = str(args.get("filter", ""))
	var names: Array = args.get("names", []) if args.get("names") is Array else []
	var settings: Dictionary = {}
	var missing: Array = []
	if not names.is_empty():
		for entry: Variant in names:
			var key: String = str(entry)
			if ProjectSettings.has_setting(key):
				settings[key] = _serialize_variant(ProjectSettings.get_setting(key))
			else:
				missing.append(key)
	else:
		for info: Dictionary in ProjectSettings.get_property_list():
			var key: String = str(info.get("name", ""))
			if key.is_empty() or (not prefix.is_empty() and not key.begins_with(prefix)):
				continue
			if not ProjectSettings.has_setting(key):
				continue
			settings[key] = _serialize_variant(ProjectSettings.get_setting(key))
	var message: String = "%d setting(s)" % settings.size()
	if not prefix.is_empty():
		message += " under %s" % prefix
	if not missing.is_empty():
		message += "; %d asked-for name(s) do not exist: %s" % [missing.size(), ", ".join(PackedStringArray(missing))]
	return {
		"success": missing.is_empty(),
		"message": message,
		"data": {"settings": settings, "count": settings.size(), "missing": missing, "filter": prefix},
	}


## What a player would see right now, in one call (H-059 -- moving-in's second
## `G-027`, filed the same turn as its first and lost to an id collision because
## `upstream_gaps.py` keys on id: two entries with one id pool as one, and the
## second is silently the one that never arrives). Every other verb answers a
## narrower question -- reachable_ui answers "can a finger hit THIS ONE control",
## performance answers "is it fast" -- and none of them answer "what IS the
## screen showing", which is the one state every game has and the thing a human
## glancing at a screenshot reads in half a second.
func _cmd_first_frame(_args: Dictionary) -> Dictionary:
	var layers: Array = []
	_collect_canvas_layers(get_tree().root, layers)
	layers.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return int(a["layer"]) < int(b["layer"]))

	var vp: Vector2 = _screen_reference_rect().size
	var topmost: Dictionary = {}
	_find_topmost_control(get_tree().root, vp, topmost)

	var mouse_mode: String
	match Input.get_mouse_mode():
		Input.MOUSE_MODE_VISIBLE: mouse_mode = "VISIBLE"
		Input.MOUSE_MODE_HIDDEN: mouse_mode = "HIDDEN"
		Input.MOUSE_MODE_CAPTURED: mouse_mode = "CAPTURED"
		Input.MOUSE_MODE_CONFINED: mouse_mode = "CONFINED"
		Input.MOUSE_MODE_CONFINED_HIDDEN: mouse_mode = "CONFINED_HIDDEN"
		_: mouse_mode = "UNKNOWN"

	var message: String = "%d visible CanvasLayer(s)" % layers.size()
	if topmost.is_empty():
		message += "; no on-screen Control found"
	else:
		message += "; topmost Control is %s '%s'" % [topmost["type"], topmost["path"]]
	if bool(get_tree().paused):
		message += " -- TREE IS PAUSED"

	return {
		"success": true,
		"message": message,
		"data": {
			"tree_paused": bool(get_tree().paused),
			"cursor_mode": mouse_mode,
			"visible_canvas_layers": layers,
			"topmost_control": topmost,
			"viewport": {"w": vp.x, "h": vp.y},
			"geometry_trustworthy": _geometry_caveat().is_empty(),
			"geometry_caveat": _geometry_caveat(),
		},
	}


func _collect_canvas_layers(node: Node, out: Array) -> void:
	if node is CanvasLayer:
		var cl: CanvasLayer = node as CanvasLayer
		if cl.visible:
			out.append({"path": str(cl.get_path()), "layer": cl.layer, "name": str(cl.name)})
	for child: Node in node.get_children():
		_collect_canvas_layers(child, out)


## `out` is a single-entry Dictionary used as an out-param, overwritten on every
## visible on-screen Control found. Godot paints children after parents and a
## later sibling after an earlier one, so the LAST one a depth-first walk finds
## is the last one painted -- the topmost, by construction, with no z-index or
## occlusion math needed.
func _find_topmost_control(node: Node, vp: Vector2, out: Dictionary) -> void:
	if node is Control:
		var control: Control = node as Control
		if _is_effectively_visible(control):
			var rect: Rect2 = _screen_rect_of(control)
			if rect.size.x > 0.0 and rect.size.y > 0.0 and Rect2(Vector2.ZERO, vp).intersects(rect):
				out.clear()
				out["path"] = str(control.get_path())
				out["type"] = control.get_class()
				out["text"] = _get_control_text(control)
				out["rect"] = {"x": rect.position.x, "y": rect.position.y,
					"w": rect.size.x, "h": rect.size.y}
	for child: Node in node.get_children():
		_find_topmost_control(child, vp, out)


## Every check the harness can run against a live game, in one call, normalized
## into one flat findings list. Zero config, zero assertions, one exit code.
##
## Why this exists: each individual verb answers a different question in a
## different shape -- validate_ui returns `issues`, reachable_ui returns
## `controls` with two "unreachable" flags buried in them, performance returns
## eighteen metrics of which two are thresholded, validate_all returns a
## per-scene tree. Getting a single "is anything wrong" answer meant five calls
## and five bespoke readers, so in practice it meant one call and four checks
## nobody ran.
##
## Nothing here re-implements a check. Each branch calls the existing `_cmd_*`
## and normalizes its reply, so a fix to validate_ui is a fix here too and the
## two can never drift into disagreeing about the same scene. The one new check
## is `signal_unconnected` (see _collect_unconnected_signals).
##
## checks_run / checks_skipped are the point as much as the findings are. A
## consolidated report is where a check that silently did nothing looks exactly
## like a check that passed, so a check that could not run says so by name and
## reason, and the client prints the denominator. Sub-conditions that were
## skipped inside a check that DID run are reported with a dotted id
## ("performance.orphan_growth"); the client counts only dotless ids toward the
## denominator.
##
## args: { "scenes": bool (default true -- the slow one, it loads every scene),
##         "use_baseline": bool, "baseline_write": bool } -- the last two pass
## straight through to the UI findings baseline.
##
## data keys: findings (Array of {source, code, severity, path, message} -- a
## geometry finding measured headless also carries caveat), counts (source ->
## int), checks_run (Array[String]), checks_skipped (Array of {check, reason}),
## viewport {w, h}, baseline_in_use, new_count, pre_existing_count,
## scroll_reachable (controls hittable only after scrolling -- reported, not findings),
## geometry_trustworthy, geometry_caveat.
func _cmd_findings(args: Dictionary) -> Dictionary:
	var include_scenes: bool = bool(args.get("scenes", true))
	var findings: Array = []
	var counts: Dictionary = {}
	var checks_run: Array = []
	var checks_skipped: Array = []
	var vp: Vector2 = _screen_reference_rect().size
	# H-051: a ui_layout / ui_reachable verdict measured headless is stamped
	# here rather than only on the underlying verb, because this aggregate
	# re-shapes each reply and would otherwise drop the flag on the way through.
	var caveat: String = _geometry_caveat()

	# Carried through from the UI baseline even when the UI check is skipped, so
	# the client never has to guess whether a key is absent or merely false.
	var baseline_in_use: bool = false
	var new_count: int = 0
	var pre_existing_count: int = 0
	var scroll_reachable: int = 0

	var scene: Node = get_tree().current_scene

	# --- 1. ui_layout: whatever validate_ui already applies -------------------
	if scene == null:
		checks_skipped.append({
			"check": "ui_layout",
			"reason": "no current scene (get_tree().current_scene is null)",
		})
	else:
		var ui_args: Dictionary = {}
		if args.has("baseline_write"):
			ui_args["baseline_write"] = bool(args["baseline_write"])
		if args.has("use_baseline"):
			ui_args["use_baseline"] = bool(args["use_baseline"])
		if args.has("baseline_dir"):
			ui_args["baseline_dir"] = str(args["baseline_dir"])
		var ui: Dictionary = _cmd_validate_ui(ui_args)
		var ui_data: Dictionary = ui.get("data", {})
		baseline_in_use = bool(ui_data.get("baseline_in_use", false))
		new_count = int(ui_data.get("new_count", 0))
		pre_existing_count = int(ui_data.get("pre_existing_count", 0))
		for issue: Dictionary in ui_data.get("issues", []):
			# Only NEW findings gate, exactly as validate_ui decides it. A
			# pre-existing finding is one the project has already accepted; it
			# stays counted in pre_existing_count (which the client prints) so
			# it is excluded, not hidden.
			if baseline_in_use and str(issue.get("baseline", "new")) == "pre_existing":
				continue
			_append_finding(findings, counts, "ui_layout",
				str(issue.get("code", "ui_issue")),
				str(issue.get("severity", "warning")),
				str(issue.get("path", "")),
				str(issue.get("message", "")))
			if not caveat.is_empty() and str(issue.get("code", "")) in GEOMETRY_CODES:
				findings[findings.size() - 1]["caveat"] = caveat
		checks_run.append("ui_layout")

	# --- 2. ui_reachable: reachable_ui's OFF-SCREEN / BLOCKED BY -------------
	if scene == null:
		checks_skipped.append({
			"check": "ui_reachable",
			"reason": "no current scene (get_tree().current_scene is null)",
		})
	else:
		var reach: Dictionary = _cmd_reachable_ui({})
		var reach_data: Dictionary = reach.get("data", {})
		scroll_reachable = int(reach_data.get("scroll_reachable", 0))
		for control: Dictionary in reach_data.get("controls", []):
			var blocked: String = str(control.get("blocked_by", ""))
			var on_screen: bool = bool(control.get("on_screen", true))
			if on_screen and blocked.is_empty():
				continue
			# A row past the fold of an on-screen ScrollContainer is reachable by
			# scrolling (gh#16): counted in scroll_reachable, not a finding.
			if not on_screen and bool(control.get("scroll_reachable", false)) and blocked.is_empty():
				continue
			var rect: Dictionary = control.get("rect", {})
			var label: String = str(control.get("text", ""))
			var described: String = str(control.get("type", "Control"))
			if not label.is_empty():
				described += " '%s'" % label
			var why: String = "blocked by %s" % blocked
			if not on_screen:
				why = "off screen at %.0f,%.0f %.0fx%.0f (viewport %dx%d)" % [
					float(rect.get("x", 0.0)), float(rect.get("y", 0.0)),
					float(rect.get("w", 0.0)), float(rect.get("h", 0.0)),
					int(vp.x), int(vp.y)]
			_append_finding(findings, counts, "ui_reachable", "unreachable_ui", "warning",
				str(control.get("path", "")),
				"%s is interactive but cannot be hit: %s" % [described, why])
			if not caveat.is_empty() and not on_screen:
				findings[findings.size() - 1]["caveat"] = caveat
		checks_run.append("ui_reachable")

	# --- 3. signal_unconnected ----------------------------------------------
	# gh#41 / moving-in:G-062 (0.40.0): one finding per (script, signal), not per
	# node - a signal declared once on a script instanced 24 times printed 24
	# lines differing only by a node index (57 lines for 11 facts), and the
	# report was permanently red and permanently unread. Same baseline mechanism
	# as ui_layout, own file, keyed on the SCRIPT and signal: a per-instance key
	# would need 24 entries for one decision and re-present them all as NEW the
	# day a box is added. A deliberately unconnected signal (an outward API, an
	# emit the project documents leaving dangling) is accepted once, and only a
	# NEW pair gates.
	var dangling: Array = []
	_collect_unconnected_signals(get_tree().root, dangling)
	var by_pair: Dictionary = {}
	var pair_order: Array = []
	for entry: Dictionary in dangling:
		var key: String = "%s#%s" % [entry["script"], entry["signal"]]
		if not by_pair.has(key):
			by_pair[key] = {
				"code": "signal_unconnected", "path": key,
				"script": entry["script"], "signal": entry["signal"], "paths": [],
			}
			pair_order.append(key)
		(by_pair[key]["paths"] as Array).append(entry["path"])
	var signal_issues: Array = []
	for key: String in pair_order:
		signal_issues.append(by_pair[key])
	var sig_args: Dictionary = {}
	if args.has("baseline_write"):
		sig_args["baseline_write"] = bool(args["baseline_write"])
	if args.has("use_baseline"):
		sig_args["use_baseline"] = bool(args["use_baseline"])
	if args.has("baseline_dir"):
		sig_args["baseline_dir"] = str(args["baseline_dir"])
	var sig_base: Dictionary = _apply_ui_baseline(signal_issues, sig_args,
		"signal_findings_baseline.json")
	var signal_baseline_in_use: bool = bool(sig_base.get("in_use", false))
	var signal_new: int = 0
	var signal_pre: int = 0
	for issue: Dictionary in signal_issues:
		var paths: Array = issue["paths"]
		if signal_baseline_in_use and str(issue.get("baseline", "new")) == "pre_existing":
			signal_pre += 1
			continue
		signal_new += 1
		_append_finding(findings, counts, "signal_unconnected", "signal_unconnected", "warning",
			str(paths[0]),
			"signal '%s' declared by %s is never connected (%d node(s))" % [
				issue["signal"], issue["script"], paths.size()])
		findings[findings.size() - 1]["nodes"] = paths.size()
		findings[findings.size() - 1]["paths"] = paths.slice(0, 20)
		findings[findings.size() - 1]["script"] = issue["script"]
		findings[findings.size() - 1]["signal"] = issue["signal"]
	checks_run.append("signal_unconnected")

	# --- 4. performance: FPS vs fps_min, orphan GROWTH vs orphan_growth_max --
	# Measured over the default window (0.22.0), so the gate reads a mean rather
	# than one frame; the reply says fps_samples for the record.
	var perf: Dictionary = await _cmd_performance({})
	var perf_data: Dictionary = perf.get("data", {})
	var fps: float = float(perf_data.get("fps", 0.0))
	var fps_min: float = float(_config.get("fps_min", 30))
	if fps <= 0.0:
		# Not a pass. A launch that has not rendered a frame reports 0, and 0 is
		# below every threshold there is; gating on it would be noise.
		checks_skipped.append({
			"check": "performance.fps",
			"reason": "engine reports 0 fps (no frame measured yet)",
		})
	elif fps < fps_min:
		_append_finding(findings, counts, "performance", "fps_below_min", "warning", "",
			"FPS %.1f is below fps_min %.0f" % [fps, fps_min])
	if not bool(perf_data.get("orphan_baseline_captured", false)):
		checks_skipped.append({
			"check": "performance.orphan_growth",
			"reason": "orphan baseline not sampled yet; growth is not meaningful",
		})
	else:
		var growth: int = int(perf_data.get("orphan_growth", 0))
		var growth_max: int = int(perf_data.get("orphan_growth_max", 20))
		if growth > growth_max:
			_append_finding(findings, counts, "performance", "orphan_growth", "warning", "",
				"orphans grew %d (max %d)" % [growth, growth_max])
	checks_run.append("performance")

	# --- 5. scene_validation: validate_all over scan_root -------------------
	if not include_scenes:
		checks_skipped.append({
			"check": "scene_validation",
			"reason": "disabled by args (scenes=false)",
		})
	else:
		var scenes_result: Dictionary = _cmd_validate_all({})
		var scenes_data: Dictionary = scenes_result.get("data", {})
		if not scenes_data.has("scenes"):
			# validate_all only fails outright when it cannot load the validator.
			# That is a skip, not a clean scan, and it must say which.
			checks_skipped.append({
				"check": "scene_validation",
				"reason": str(scenes_result.get("message", "validate_all returned no scene list")),
			})
		else:
			for entry: Dictionary in scenes_data["scenes"]:
				for issue: Dictionary in entry.get("issues", []):
					_append_finding(findings, counts, "scene_validation",
						str(issue.get("code", "scene_issue")),
						str(issue.get("severity", "warning")),
						str(entry.get("path", "")),
						str(issue.get("message", "")))
			checks_run.append("scene_validation")

	# A check that ran and found nothing is a 0, not an absent key: absent means
	# "did not run", and the two must not read the same.
	for check: String in checks_run:
		if not counts.has(check):
			counts[check] = 0

	var message: String = "%d finding(s) across %d of %d check(s) at %dx%d" % [
		findings.size(), checks_run.size(), FINDINGS_CHECKS.size(), int(vp.x), int(vp.y)]
	if not checks_skipped.is_empty():
		message += " (%d skipped)" % checks_skipped.size()
	var last_path: String = _persist_last_findings("findings", findings)

	return {
		"success": findings.is_empty(),
		"message": message,
		"data": {
			"findings": findings,
			"last_findings_path": last_path,
			"counts": counts,
			"checks_run": checks_run,
			"checks_skipped": checks_skipped,
			"viewport": {"w": vp.x, "h": vp.y},
			"baseline_in_use": baseline_in_use,
			"signal_baseline_in_use": signal_baseline_in_use,
			"signal_new_count": signal_new,
			"signal_pre_existing_count": signal_pre,
			"signal_baseline_path": str(sig_base.get("path", "")),
			"signal_baseline_written": bool(sig_base.get("written", false)),
			"new_count": new_count,
			"pre_existing_count": pre_existing_count,
			"scroll_reachable": scroll_reachable,
			"geometry_trustworthy": caveat.is_empty(),
			"geometry_caveat": caveat,
		},
	}


## Writes the full records of a NON-CLEAN findings / validate_ui run to
## user://findings_last.json and returns its absolute path; a clean run writes
## nothing and returns "" (so the file always holds the most recent run that had
## something to say). plant-tower-defense:G-030: a finding that fires once on a
## transient frame left nothing to investigate with -- the verb re-run seconds
## later was a different frame and said [OK], and the only record was a count in a
## line the caller had already truncated. The records exist in memory at the
## moment they are counted; this is the cheapest possible way to keep them.
const LAST_FINDINGS_FILE: String = "user://findings_last.json"

func _persist_last_findings(verb: String, records: Array) -> String:
	if records.is_empty():
		return ""
	var payload: Dictionary = {
		"verb": verb,
		"count": records.size(),
		"unix_time": Time.get_unix_time_from_system(),
		"iso_time": Time.get_datetime_string_from_system(true),
		"process_frame": Engine.get_process_frames(),
		"physics_frame": Engine.get_physics_frames(),
		"tree_paused": get_tree().paused,
		"current_scene": str(get_tree().current_scene.scene_file_path) if get_tree().current_scene != null else "",
		"records": records,
	}
	var file: FileAccess = FileAccess.open(LAST_FINDINGS_FILE, FileAccess.WRITE)
	if file == null:
		return ""
	file.store_string(JSON.stringify(payload, "  "))
	file.close()
	return ProjectSettings.globalize_path(LAST_FINDINGS_FILE)


## validate_ui codes whose verdict is a screen-space position -- the ones a
## headless run can get wrong for the reason _geometry_caveat() explains.
## ui_transparent, ui_zero_size, text overflow and tap-target size are measured
## in the node's own units and are the same headless or windowed.
const GEOMETRY_CODES: Array[String] = [
	"ui_overflow", "ui_negative_pos", "ui_outside_safe_area", "interactive_overlap",
	"container_layout_drift",
]


func _append_finding(out: Array, counts: Dictionary, source: String, code: String,
		severity: String, path: String, message: String) -> void:
	out.append({
		"source": source,
		"code": code,
		"severity": severity,
		"path": path,
		"message": message,
	})
	counts[source] = int(counts.get(source, 0)) + 1


## Signals a script DECLARES and nothing ever connects to -- the emit that goes
## nowhere, which is silent at runtime and invisible to every other check here.
##
## Only script-declared signals, via Script.get_script_signal_list(). NOT
## Object.get_signal_list(), which also returns the ~10 built-ins every Node
## inherits (renamed, tree_entered, child_order_changed, ...). Those are
## legitimately unconnected on essentially every node in the tree, so including
## them would put thousands of non-findings in front of the handful that matter
## and the report would be discarded on sight.
##
## The bridge's own subtree is excluded: it is harness plumbing, and a finding a
## project cannot act on is noise.
func _collect_unconnected_signals(node: Node, out: Array) -> void:
	if node == self:
		return
	var script: Variant = node.get_script()
	if script is Script:
		var owner_script: Script = script as Script
		for declared: Dictionary in owner_script.get_script_signal_list():
			var signal_name: String = str(declared.get("name", ""))
			if signal_name.is_empty() or not node.has_signal(signal_name):
				continue
			if node.get_signal_connection_list(signal_name).is_empty():
				var source_path: String = owner_script.resource_path
				if source_path.is_empty():
					source_path = "<built-in script>"
				out.append({
					"path": str(node.get_path()),
					"signal": signal_name,
					"script": source_path,
				})
	for child: Node in node.get_children():
		_collect_unconnected_signals(child, out)


func _load_validator() -> GDScript:
	var validator_path: String = _config.get("validator_script", "")
	if validator_path.is_empty() or not ResourceLoader.exists(validator_path):
		return null
	var script: GDScript = load(validator_path) as GDScript
	if script != null:
		# gh#30: the validator is a static-utility class (GodotSelftestSceneValidator)
		# never attached as any node's script, the exact shape mark_script_reached
		# exists for. The core calls it directly here rather than the validator
		# calling back into DevTools, since this call site already IS the core and
		# already has the path - no autoload lookup, no extra hop.
		_scripts_seen[validator_path] = true
	return script


func _serialize_variant(value: Variant) -> Variant:
	match typeof(value):
		TYPE_NIL:
			return null
		TYPE_BOOL:
			return value
		TYPE_INT:
			return value
		TYPE_FLOAT:
			return value
		TYPE_STRING:
			return value
		TYPE_VECTOR2:
			return {"x": value.x, "y": value.y}
		TYPE_VECTOR3:
			return {"x": value.x, "y": value.y, "z": value.z}
		TYPE_COLOR:
			return {"r": value.r, "g": value.g, "b": value.b, "a": value.a}
		TYPE_RECT2:
			return {"x": value.position.x, "y": value.position.y, "w": value.size.x, "h": value.size.y}
		TYPE_DICTIONARY:
			var result: Dictionary = {}
			for key in value:
				result[str(key)] = _serialize_variant(value[key])
			return result
		TYPE_ARRAY:
			var result: Array = []
			for item in value:
				result.append(_serialize_variant(item))
			return result
		_:
			return str(value)


func _write_log(category: String, message: String, data: Variant = null) -> void:
	var entry: Dictionary = {
		"timestamp": Time.get_unix_time_from_system(),
		"frame": Engine.get_process_frames(),
		"category": category,
		"message": message,
	}
	if data != null:
		entry["data"] = data

	var file: FileAccess = FileAccess.open(_log_path, FileAccess.READ_WRITE)
	if file == null:
		# File may not exist yet; create it.
		file = FileAccess.open(_log_path, FileAccess.WRITE)
	if file == null:
		return
	file.seek_end()
	file.store_line(JSON.stringify(entry))
	file.close()


func _find_all_scenes(path: String) -> Array[String]:
	var scenes: Array[String] = []
	var dir: DirAccess = DirAccess.open(path)
	if dir == null:
		return scenes

	dir.list_dir_begin()
	var file_name: String = dir.get_next()
	while file_name != "":
		if file_name == ".godot" or file_name.begins_with("."):
			file_name = dir.get_next()
			continue

		var full_path: String = path.path_join(file_name)
		if dir.current_is_dir():
			scenes.append_array(_find_all_scenes(full_path))
		elif file_name.ends_with(".tscn") or file_name.ends_with(".scn"):
			scenes.append(full_path)

		file_name = dir.get_next()
	dir.list_dir_end()

	return scenes


func _clear_all_simulated_inputs() -> Array[String]:
	var cleared: Array[String] = _active_simulated_inputs.duplicate()
	for action in cleared:
		Input.action_release(action)
		_dispatch_action_event(action, false)
	_active_simulated_inputs.clear()
	return cleared


## Mirrors _clear_all_simulated_inputs() for touch. Hooked into input_clear,
## touch_clear and _exit_tree so a run cannot leave phantom fingers down: an
## unreleased touch is a gesture the game is still waiting to complete, and it
## outlives the run that started it.
func _clear_all_simulated_touches() -> Array:
	var released: Array = _serialize_touches()
	for entry: Dictionary in released:
		var position: Dictionary = entry["position"]
		_dispatch_touch(int(entry["index"]), Vector2(position["x"], position["y"]), false)
	_active_touches.clear()
	return released


func _serialize_touches() -> Array:
	var out: Array = []
	var indexes: Array = _active_touches.keys()
	indexes.sort()
	for index: int in indexes:
		var position: Vector2 = _active_touches[index]
		out.append({"index": index, "position": {"x": position.x, "y": position.y}})
	return out


func _is_number(value: Variant) -> bool:
	return typeof(value) == TYPE_INT or typeof(value) == TYPE_FLOAT


## Parses [x, y] or {"x": ..., "y": ...} into a Vector2. Returns null -- not a zero
## vector -- on anything else, so a malformed argument is rejected loudly rather than
## silently reinterpreted as the top-left corner of the screen.
## [x, y, z] or {x, y, z} -> Vector3; anything else (including a 2-vector) -> null.
func _parse_vector3_or_null(value: Variant) -> Variant:
	if value is Array:
		var arr: Array = value
		if arr.size() == 3 and _is_number(arr[0]) and _is_number(arr[1]) and _is_number(arr[2]):
			return Vector3(float(arr[0]), float(arr[1]), float(arr[2]))
	elif value is Dictionary:
		var dict: Dictionary = value
		if dict.has("x") and dict.has("y") and dict.has("z") and _is_number(dict["x"]) and _is_number(dict["y"]) and _is_number(dict["z"]):
			return Vector3(float(dict["x"]), float(dict["y"]), float(dict["z"]))
	return null


func _parse_vector2_or_null(value: Variant) -> Variant:
	if value is Array:
		var arr: Array = value
		if arr.size() >= 2 and _is_number(arr[0]) and _is_number(arr[1]):
			return Vector2(float(arr[0]), float(arr[1]))
	elif value is Dictionary:
		var dict: Dictionary = value
		if dict.has("x") and dict.has("y") and _is_number(dict["x"]) and _is_number(dict["y"]):
			return Vector2(float(dict["x"]), float(dict["y"]))
	return null


## Deletes leftovers from a dead instance: a stale command/result file would be
## answered/consumed as if it were current, and a stale owner file would name a
## process that no longer exists as the bus owner.
func _clear_stale_files() -> void:
	if FileAccess.file_exists(_commands_path):
		DirAccess.remove_absolute(_commands_abs_path)
	if FileAccess.file_exists(_results_path):
		DirAccess.remove_absolute(_results_abs_path)
	if FileAccess.file_exists(_owner_path):
		DirAccess.remove_absolute(_owner_abs_path)
	# NOT the breadcrumb: a leftover from a crashed predecessor is evidence, and
	# the client reads it after a launch precisely to explain the crash. It is
	# stamped with the dead pid, so it cannot be mistaken for this instance's.


## Writes the bus-identity record (G-009). The client reads it to say WHO owns a
## bus -- on a foreign-pid reply (another instance answering) and in the
## "game not running" precheck message (naming the process that last claimed it).
func _write_owner_file() -> void:
	var file: FileAccess = FileAccess.open(_owner_path, FileAccess.WRITE)
	if file == null:
		_write_log("error", "Failed to write owner file", {"error": FileAccess.get_open_error()})
		return
	file.store_string(JSON.stringify({
		"pid": OS.get_process_id(),
		"start_unix": _start_unix,
		# Refreshed ~1/sec by _touch_owner_heartbeat() for as long as this
		# instance is actually polling the bus. The client reads it to tell a
		# live owner from a recycled pid or a wedged one; see that function.
		"last_poll_unix": Time.get_unix_time_from_system(),
		"heartbeat_interval_sec": float(HEARTBEAT_INTERVAL_MSEC) / 1000.0,
		"project": str(ProjectSettings.get_setting("application/config/name", "")),
		# The checkout this game runs from (plant-tower-defense:G-018). A sibling
		# git worktree has the same project NAME, so the same user:// and the same
		# bus; its game overwrites this record and its pid then matches every
		# reply, so the pid check reads a foreign game as your own. The path is
		# the one fact that differs, and the client compares it to its --path.
		"project_path": ProjectSettings.globalize_path("res://"),
		"session": _session,
		"bus_dir": _bus_dir if not _bus_dir.is_empty() else ProjectSettings.globalize_path("user://"),
	}, "  "))
	file.close()
