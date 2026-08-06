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

# harness-version: 0.10.0
# --- Constants ---

## Version of the godot-selftest-harness these files were copied from. Reported by the
## `harness_version` verb and stamped into every copied tool script, so a gap logged
## against a project can name the version it was seen on, and a refresh can tell a
## stale file from a customized one. Bump with .claude-plugin/plugin.json.
const HARNESS_VERSION: String = "0.10.0"

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
	"mute": true,
	# Read by tools/verify_ledger.py, not by this core. Maps a script that reach can
	# never observe -- a RefCounted or Resource that is never any node's script -- to
	# the observed script(s) that vouch for it. Credited in a bucket of its own, so an
	# alias can never be mistaken for an observation. {} means "claim nothing".
	"reach_aliases": {},
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
# Live reference to the instantiated registry extension. MUST be held so the
# Callables it bound (via register_command) are not freed out from under us.
var _extension: RefCounted = null


# --- Lifecycle ---

func _ready() -> void:
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

	_clear_stale_files()
	_write_owner_file()

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
	})

	_process_command_line_args()
	_capture_orphan_baseline()


func _process(_delta: float) -> void:
	var now_msec: int = Time.get_ticks_msec()
	if now_msec - _last_command_check_msec >= 100:
		_last_command_check_msec = now_msec
		_check_for_commands()


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
	register_command("step_time", _cmd_step_time)
	register_command("wait_frames", _cmd_wait_frames)
	register_command("clear_nodes", _cmd_clear_nodes)
	register_command("validate_ui", _cmd_validate_ui)
	register_command("get_ui_snapshot", _cmd_get_ui_snapshot)
	register_command("get_node_bounds", _cmd_get_node_bounds)
	register_command("canvas_scale", _cmd_canvas_scale)
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
		_write_result(action, {"success": false, "message": "Unknown action: %s" % action})
		_write_log("error", "Unknown action: %s" % action)
		return

	_write_log("command", "Executing: %s" % action, args)
	_write_breadcrumb(action, args, request_id)

	var handler: Callable = _handlers[action]
	var result: Dictionary = await handler.call(args)
	_clear_breadcrumb()
	# A command that arrived while this one was awaiting (step_time, input_tap, a
	# long project verb) would have moved _current_request_id. Restore ours so this
	# reply carries the id of the request it actually answers.
	_current_request_id = request_id
	_write_result(action, result)


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
	var file: FileAccess = FileAccess.open(_results_path, FileAccess.WRITE)
	if file == null:
		_write_log("error", "Failed to write result file", {"error": FileAccess.get_open_error()})
		return
	file.store_string(JSON.stringify(response, "  "))
	file.close()


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
			"bus_dir": _bus_dir if not _bus_dir.is_empty() else ProjectSettings.globalize_path("user://"),
			"user_dir": ProjectSettings.globalize_path("user://"),
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
	var to_hide: Array[CanvasItem] = []
	var was_visible: Array[bool] = []
	var hidden_paths: Array = []
	for entry: Variant in (args.get("hide", []) if args.get("hide") is Array else []):
		var found: Dictionary = _resolve_node(str(entry))
		if found["node"] is CanvasItem:
			to_hide.append(found["node"] as CanvasItem)
	for entry: Variant in (args.get("hide_group", []) if args.get("hide_group") is Array else []):
		for node: Node in get_tree().get_nodes_in_group(str(entry)):
			if node is CanvasItem:
				to_hide.append(node as CanvasItem)

	for item: CanvasItem in to_hide:
		was_visible.append(item.visible)
		hidden_paths.append(str(item.get_path()))
		item.visible = false

	# One frame so the hidden nodes are actually off the rendered image.
	if not to_hide.is_empty():
		await RenderingServer.frame_post_draw

	var image: Image = get_viewport().get_texture().get_image()

	for i: int in to_hide.size():
		to_hide[i].visible = was_visible[i]

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
		else:
			return {
				"ok": false, "value": null,
				"reason": "%s is a %s, not an object -- cannot read .%s off it" % [
					".".join(walked), type_string(typeof(current)), segment,
				],
			}
		walked.append(segment)

	return {"ok": true, "value": current, "reason": ""}


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
	var current: Variant = node.get(property)
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

	node.set(property, value)

	var read_back: Variant = node.get(property)
	if not _values_match(read_back, value):
		return {
			"success": false,
			"message": "set had no effect on %s.%s: wrote %s but read back %s (unknown property, or a setter clamped/rejected it)" % [
				node_path, property,
				JSON.stringify(_serialize_variant(value)),
				JSON.stringify(_serialize_variant(read_back)),
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
func _cmd_performance(args: Dictionary) -> Dictionary:
	var orphan_nodes: int = int(Performance.get_monitor(Performance.OBJECT_ORPHAN_NODE_COUNT))

	if bool(args.get("reset_baseline", false)):
		_orphan_baseline = orphan_nodes
		_orphan_baseline_frame = int(Engine.get_process_frames())
		_write_log("system", "Orphan baseline reset", {"orphan_baseline": _orphan_baseline})

	var baseline_captured: bool = _orphan_baseline >= 0
	var orphan_growth: int = (orphan_nodes - _orphan_baseline) if baseline_captured else 0

	var fps: float = Engine.get_frames_per_second()
	var data: Dictionary = {
		"fps": fps,
		"frame_time_ms": 1000.0 / maxf(1.0, fps),
		"physics_fps": Engine.physics_ticks_per_second,
		"static_memory_mb": OS.get_static_memory_usage() / (1024.0 * 1024.0),
		"video_memory_mb": Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / (1024.0 * 1024.0),
		"draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		"objects": Performance.get_monitor(Performance.OBJECT_COUNT),
		"nodes": Performance.get_monitor(Performance.OBJECT_NODE_COUNT),
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
	}

	var message: String = "Performance metrics collected"
	if baseline_captured:
		message += " (orphans %d, baseline %d, growth %d)" % [orphan_nodes, _orphan_baseline, orphan_growth]
	else:
		message += " (orphan baseline not sampled yet; growth is not meaningful)"

	return {
		"success": true,
		"message": message,
		"data": data,
	}


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
	_write_log("system", "Orphan baseline captured", {
		"orphan_baseline": _orphan_baseline,
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
	if not cls.is_empty() and (node.is_class(cls) or node.get_class() == cls):
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
	var scale: float = clampf(float(args.get("scale", 1.0)), 0.0, 100.0)
	Engine.time_scale = scale
	# Remembered so `performance` can report a leftover override (G-059).
	_devtools_set_speed = scale

	return {
		"success": true,
		"message": "Game speed: %.1f -> %.1f" % [prev, scale],
		"data": {"previous_scale": prev, "current_scale": scale},
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
		"budget_exhausted": budget_exhausted,
		"held_action": hold_action if not hold_action.is_empty() else null,
	}

	var message: String = "Advanced %.4fs of physics time (%d frames @ %d Hz, exact) and %.4fs of process time (approximate, +/- one frame); requested %.4fs. time_scale pinned to 1.0 and restored to %.3f." % [
		data["physics_seconds"], physics_frames, ticks_per_second, process_seconds, seconds, previous_scale,
	]
	if tree_paused:
		message += " WARNING: get_tree().paused is true, so node processing did not actually advance."
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
			break
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
	var vp: Vector2 = Vector2(get_tree().root.size)

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
			"severity": "warning",
			"code": "interactive_overlap",
			"message": "Interactive controls overlap: '%s' and '%s' (overlap area: %.0fpx)" % [
				overlap["node_a"], overlap["node_b"], overlap["overlap_area"],
			],
		})

	return {
		"success": issues.is_empty(),
		"message": "%d UI issues found" % issues.size() if not issues.is_empty() else "No UI issues found",
		"data": {
			"issues": issues,
			"safe_area_checked": check_safe_area,
			"safe_area_inset": inset,
			"safe_area_rect": {
				"x": safe_rect.position.x,
				"y": safe_rect.position.y,
				"w": safe_rect.size.x,
				"h": safe_rect.size.y,
			},
		},
	}


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
		var rect: Rect2 = control.get_global_rect()

		# World-space Controls get only the intrinsic checks (zero size,
		# transparency); every screen-position check would report where the
		# camera happens to be, not a layout defect.
		var world_space: bool = _is_world_space_control(control)

		# Collect interactive controls for overlap detection
		if (control is Button or control is TextureButton or control is LinkButton) and control.visible and not world_space:
			interactive_controls.append({"path": str(control.get_path()), "rect": rect})

		# Check 1: Viewport overflow
		if not world_space and (rect.position.x + rect.size.x > vp.x or rect.position.y + rect.size.y > vp.y):
			issues.append({
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
				var text_width: float = font.get_string_size(control.text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
				if text_width > control.size.x and control.size.x > 0.0:
					var display_text: String = control.text
					if display_text.length() > 50:
						display_text = display_text.substr(0, 47) + "..."
					issues.append({
						"severity": "warning",
						"code": "ui_text_overflow",
						"message": "%s '%s' text '%s' exceeds width (text: %.0fpx, label: %.0fpx)" % [
							control.get_class(), control.name, display_text, text_width, control.size.x,
						],
					})

		# Check 5: Negative position
		if not world_space and (rect.position.x < 0.0 or rect.position.y < 0.0):
			issues.append({
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
					"severity": "warning",
					"code": "container_layout_drift",
					"message": "Node '%s' position (%.0f) is left of parent container (%.0f) - possible layout corruption" % [
						path, node_rect.position.x, parent_rect.position.x,
					],
				})
			if node_rect.end.x > parent_rect.end.x + 2.0:
				issues.append({
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
	var vp: Vector2 = Vector2(get_tree().root.size)
	var elements: Array = []
	_snapshot_ui_recursive(get_tree().current_scene, vp, elements)

	return {
		"success": true,
		"message": "%d UI elements captured" % elements.size(),
		"data": {
			"viewport": {"width": int(vp.x), "height": int(vp.y)},
			"elements": elements,
		},
	}


func _snapshot_ui_recursive(node: Node, vp: Vector2, elements: Array) -> void:
	if node is Control:
		var control: Control = node as Control
		var eff_visible: bool = _is_effectively_visible(control)
		var eff_alpha: float = _get_effective_alpha(control)

		if eff_visible or eff_alpha > 0.0:
			var rect: Rect2 = control.get_global_rect()
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
## For a Control the rect is its own get_global_rect(). For any other CanvasItem
## the rect is derived from the canvas transform, sized from whatever the node
## can report (a Sprite2D's texture rect, a CollisionShape2D's shape) and
## degenerate (w=h=0) when it can report nothing -- an origin point is still a
## useful answer to "where on screen is this", and a zero size says plainly that
## the extent is unknown rather than inventing one.
## data keys: name, type, path, global_rect{x,y,w,h}, visible, modulate_a, text,
## in_viewport, size_source.
func _cmd_get_node_bounds(args: Dictionary) -> Dictionary:
	var node_path: String = args.get("node_path", "")
	if node_path.is_empty():
		return {"success": false, "message": "No node_path provided"}

	var resolved: Dictionary = _resolve_node(node_path)
	var node: Node = resolved["node"]
	if node == null:
		return {"success": false, "message": "Node not found: %s" % node_path}

	if not node is CanvasItem:
		return {
			"success": false,
			"message": "Node is not a CanvasItem, so it has no screen rect: %s (%s)" % [
				node_path, node.get_class()],
			"data": {"class": node.get_class()},
		}

	var item: CanvasItem = node as CanvasItem
	var vp: Vector2 = Vector2(get_tree().root.size)
	var rect: Rect2
	var size_source: String

	if item is Control:
		rect = (item as Control).get_global_rect()
		size_source = "Control.get_global_rect"
	else:
		var xform: Transform2D = item.get_global_transform_with_canvas()
		var local: Rect2 = _local_draw_rect(item)
		rect = xform * local
		size_source = "canvas transform x %s" % _local_draw_rect_source(item)

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
		var rect: Rect2 = control.get_global_rect()
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
## args: { "class": String, "group": String, "method": String,
##         "where": Dictionary (property -> expected value, dotted paths allowed),
##         "properties": Array[String] (extra properties to report per hit),
##         "root": String (subtree to search, default the whole tree),
##         "limit": int (default 200) }
## data keys: nodes (Array of {path, name, type, properties}), count, truncated.
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
	var limit: int = int(args.get("limit", 200))

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
	for node: Node in candidates:
		if not _matches_where(node, where):
			continue
		if hits.size() >= limit:
			truncated = true
			break
		var props: Dictionary = {}
		for entry: Variant in report:
			var name: String = str(entry)
			var walk: Dictionary = _resolve_property_path(node, name)
			props[name] = _serialize_variant(walk["value"]) if walk["ok"] else null
		hits.append({
			"path": str(node.get_path()),
			"name": str(node.name),
			"type": node.get_class(),
			"properties": props,
		})

	return {
		"success": true,
		"message": "%d node(s) matched" % hits.size(),
		"data": {"nodes": hits, "count": hits.size(), "truncated": truncated},
	}


## Every key in `where` must equal the node's value at that (possibly dotted)
## property path. A property the node does not have is a NON-match, never an
## error: the predicate is applied across a heterogeneous subtree by design.
func _matches_where(node: Node, where: Dictionary) -> bool:
	for key: Variant in where:
		var walk: Dictionary = _resolve_property_path(node, str(key))
		if not walk["ok"]:
			return false
		if not _values_match(walk["value"], where[key]):
			# Compare through the serialized form too, so a JSON string can match
			# a StringName / enum-backed value without the caller knowing which.
			if str(_serialize_variant(walk["value"])) != str(where[key]):
				return false
	return true


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
## args: { "from": [x,y], "to": [x,y], "mask": int (default all layers),
##         "areas": bool (also hit Area2Ds, default false),
##         "exclude": Array[String] of node paths }
## data keys: clear (bool), collider (String path or ""), collider_class,
## position {x,y}, normal {x,y}, mask, mask_names (Array).
func _cmd_raycast(args: Dictionary) -> Dictionary:
	var from: Variant = _parse_vector2_or_null(args.get("from"))
	var to: Variant = _parse_vector2_or_null(args.get("to"))
	if from == null or to == null:
		return {"success": false, "message": "raycast needs from and to as [x, y] (or {\"x\":..,\"y\":..})"}

	var world: World2D = get_tree().root.world_2d
	if world == null:
		return {"success": false, "message": "No World2D (is this a headless run with no 2D space?)"}

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
				"mask": params.collision_mask, "mask_names": mask_names,
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
		},
	}


## Resolves a 2D collision mask to the project's own layer names, because the
## whole class of bug here is a number nobody can read. Unnamed layers report as
## "layer_N" so a bit is never silently dropped from the list.
func _layer_names_for_mask(mask: int) -> Array:
	var names: Array = []
	for bit: int in range(32):
		if mask & (1 << bit) == 0:
			continue
		var setting: String = "layer_names/2d_physics/layer_%d" % (bit + 1)
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
## args: { "rect": [x, y, w, h] (default the whole viewport) }
## data keys: rect{x,y,w,h}, pixels (int), mean{r,g,b}, brightest{r,g,b},
## darkest{r,g,b}, dominant{r,g,b} (the most common colour after quantising to
## 5 bits per channel), dominant_share (0..1).
func _cmd_sample_pixels(args: Dictionary) -> Dictionary:
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

	var total: Vector3 = Vector3.ZERO
	var brightest: Color = Color(0, 0, 0)
	var darkest: Color = Color(1, 1, 1)
	var best_lum: float = -1.0
	var worst_lum: float = 2.0
	var buckets: Dictionary = {}
	var count: int = 0
	for y: int in range(rect.position.y, rect.position.y + rect.size.y):
		for x: int in range(rect.position.x, rect.position.x + rect.size.x):
			var c: Color = image.get_pixel(x, y)
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

	return {
		"success": true,
		"message": "%d px in (%d, %d, %d, %d): mean #%s, dominant #%s (%.0f%%)" % [
			count, rect.position.x, rect.position.y, rect.size.x, rect.size.y,
			Color(mean.x, mean.y, mean.z).to_html(false), dominant.to_html(false),
			100.0 * float(top_count) / maxf(1.0, float(count))],
		"data": {
			"rect": {"x": rect.position.x, "y": rect.position.y,
				"w": rect.size.x, "h": rect.size.y},
			"pixels": count,
			"mean": {"r": mean.x, "g": mean.y, "b": mean.z},
			"brightest": {"r": brightest.r, "g": brightest.g, "b": brightest.b},
			"darkest": {"r": darkest.r, "g": darkest.g, "b": darkest.b},
			"dominant": {"r": dominant.r, "g": dominant.g, "b": dominant.b},
			"dominant_share": float(top_count) / maxf(1.0, float(count)),
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
## data keys: controls (Array of {path, type, text, rect, on_screen, blocked_by,
## kind}), count, reachable, viewport {w, h}.
func _cmd_reachable_ui(_args: Dictionary) -> Dictionary:
	var vp: Vector2 = Vector2(get_tree().root.size)
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
	for entry: Dictionary in found:
		var clean: Dictionary = entry.duplicate()
		clean.erase("_rect")
		clean.erase("_stops_input")
		if clean["on_screen"] and clean["blocked_by"].is_empty():
			reachable += 1
		out.append(clean)

	return {
		"success": true,
		"message": "%d of %d interactive control(s) are actually reachable at %dx%d" % [
			reachable, out.size(), int(vp.x), int(vp.y)],
		"data": {
			"controls": out,
			"count": out.size(),
			"reachable": reachable,
			"viewport": {"w": vp.x, "h": vp.y},
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
			var rect: Rect2 = control.get_global_rect()
			if rect.size.x > 0.0 and rect.size.y > 0.0:
				out.append({
					"path": str(control.get_path()),
					"type": control.get_class(),
					"text": _get_control_text(control),
					"rect": {"x": rect.position.x, "y": rect.position.y,
						"w": rect.size.x, "h": rect.size.y},
					"on_screen": Rect2(Vector2.ZERO, vp).intersects(rect),
					"blocked_by": "",
					"kind": kind,
					"_rect": rect,
					"_stops_input": control.mouse_filter == Control.MOUSE_FILTER_STOP,
				})
	for child: Node in node.get_children():
		_collect_reachable(child, vp, out)


func _load_validator() -> GDScript:
	var validator_path: String = _config.get("validator_script", "")
	if validator_path.is_empty() or not ResourceLoader.exists(validator_path):
		return null
	return load(validator_path) as GDScript


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
		"project": str(ProjectSettings.get_setting("application/config/name", "")),
		"session": _session,
		"bus_dir": _bus_dir if not _bus_dir.is_empty() else ProjectSettings.globalize_path("user://"),
	}, "  "))
	file.close()
