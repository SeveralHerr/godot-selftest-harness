@tool
extends SceneTree

## Generic headless unit test runner for the godot_selftest harness.
## Run: godot --headless --script res://tools/run_tests.gd
## Args: -- --json          Output results as JSON
##       -- --filter NAME   Run only tests whose METHOD NAME or SCRIPT FILENAME
##                          contains NAME (case-insensitive)
##       -- --file NAME     Run only tests from this test script. NAME may be a
##                          bare basename (test_player), a filename
##                          (test_player.gd) or any substring of the path.
##
## --filter and --file combine with AND, so `--file test_player --filter damage`
## runs the damage tests of one file.
##
## Exit codes. The runner always calls quit() with its own result, so the process
## exit code means "the tests said so" and never "Godot leaked an RID at shutdown":
##   0  every selected test passed
##   1  one or more tests failed
##   2  the runner itself could not run, i.e. NOTHING WAS VERIFIED - test_dir is
##      missing, a test script failed to load / instantiate, no test scripts were
##      discovered at all, or a --filter/--file selected zero of the discovered
##      tests. Treat this as a broken gate, not as a test failure.
##
## That last case used to exit 0. A filter matching nothing skipped the entire
## suite and printed `Total: 0 | Passed: 0 | Failed: 0`, which is byte-identical
## to a clean pass for anything reading the exit code - and two agents in one
## session shipped work on the strength of it. A selection that selects nothing
## is now a runner error naming what it did: `filter 'spawner' selected 0 of 111
## discovered test(s)`.
##
## Windows note: the stock Godot .exe is the non-console build and prints nothing
## to a PowerShell console, so redirect headless runs to a file and read that back
## (e.g. `godot --headless --script res://tools/run_tests.gd > tests.txt 2>&1`).
##
## Test contract (unchanged from the original project runner):
##   - Test scripts extend RefCounted.
##   - They may expose optional setup() and teardown() methods.
##   - Each test_* method returns a String: "" == pass, non-empty == failure message.
##   - A reference to this runner script is injected as `_T` so tests can call the
##     static assertion helpers (assert_eq, assert_true, ...) and the headless UI
##     helpers (instantiate_ui, free_ui).
##   - setup(), teardown() and any test_* method may be a coroutine (may `await`).
##     The runner awaits every call, so plain synchronous tests that return a
##     String directly keep working unchanged. There is no watchdog: a test that
##     awaits a signal which never fires will hang the run.
##
## Known limitation, since it affects how much the exit code is worth: GDScript
## has no exception handling, and a hard runtime error inside a test method (a
## null dereference, say) aborts only that method and hands back the return type's
## default value - "" for `-> String`, i.e. a pass. The runner cannot see it. The
## error is on stderr, so redirect it and check it; assert on the value you expect
## rather than relying on a bad call to fail the test for you.
##
## Test scripts are auto-discovered by recursively scanning the configured test_dir
## (res://addons/godot_selftest/devtools_config.json key "test_dir", default
## "res://test/unit") for files named test_*.gd.

# harness-version: 0.7.0
## Harness revision these files were copied from. See lint_project.gd / the
## `harness_version` bus verb; bump with .claude-plugin/plugin.json.
const HARNESS_VERSION: String = "0.7.0"

const CONFIG_PATH: String = "res://addons/godot_selftest/devtools_config.json"
const DEFAULT_TEST_DIR: String = "res://test/unit"

const EXIT_OK: int = 0
const EXIT_TESTS_FAILED: int = 1
const EXIT_RUNNER_ERROR: int = 2

var _passed: int = 0
var _failed: int = 0
var _skipped: int = 0
var _errors: Array[Dictionary] = []
var _results: Array[Dictionary] = []
var _json_output: bool = false
var _filter: String = ""
var _file_filter: String = ""
var _runner_error: bool = false
## Test methods found before any selector was applied, and how many survived it.
var _discovered: int = 0
var _selected: int = 0
## Set when a selector matched nothing. Reported instead of "the suite did not
## complete", because the suite is fine - the selector isn't.
var _selection_error: String = ""


func _initialize() -> void:
	var args: PackedStringArray = OS.get_cmdline_user_args()
	for i: int in args.size():
		match args[i]:
			"--json":
				_json_output = true
			"--filter":
				if i + 1 < args.size():
					_filter = args[i + 1]
			"--file":
				if i + 1 < args.size():
					_file_filter = args[i + 1]

	var test_dir: String = _load_test_dir()
	if not DirAccess.dir_exists_absolute(test_dir):
		_runner_error = true
		_errors.append({"script": test_dir, "error": "test_dir does not exist"})
		_print_results()
		quit(_exit_code())
		return

	var test_scripts: Array[String] = _discover_test_scripts(test_dir)
	if test_scripts.is_empty():
		# "0 tests passed" is not a pass. Discovering nothing means the gate did
		# not run, so say so rather than reporting a clean sweep of an empty set.
		_runner_error = true
		_errors.append({
			"script": test_dir,
			"error": "no test_*.gd scripts found - nothing was verified",
		})
		_print_results()
		quit(_exit_code())
		return

	# Test methods may await, so the whole run is a coroutine. _initialize()
	# returns at the first await and the main loop resumes it frame by frame;
	# quit() is only reached once every test has actually finished.
	await _run_all_tests(test_scripts)

	if _selected == 0 and _discovered > 0:
		_runner_error = true
		_selection_error = "%s selected 0 of %d discovered test(s)" % [
			_selector_description(), _discovered,
		]

	_print_results()

	quit(_exit_code())


## Human-readable form of whichever selectors were passed, for the zero-match error.
func _selector_description() -> String:
	var parts: PackedStringArray = []
	if _filter != "":
		parts.append("filter '%s'" % _filter)
	if _file_filter != "":
		parts.append("file '%s'" % _file_filter)
	if parts.is_empty():
		return "selection"
	return " + ".join(parts)


## True when a test method survives --filter and --file.
##
## --filter deliberately matches the SCRIPT FILENAME as well as the method name:
## matching method names alone meant `--filter spawner` against a brand new
## test_enemy_spawner.gd selected nothing at all, because none of its methods
## happened to repeat the word. Both comparisons are case-insensitive.
func _is_selected(method_name: String, script_path: String) -> bool:
	var file_name: String = script_path.get_file()

	if _file_filter != "":
		var want: String = _file_filter.to_lower()
		var have: String = file_name.to_lower()
		var matched: bool = (
			have == want
			or have == want + ".gd"
			or script_path.to_lower().contains(want)
		)
		if not matched:
			return false

	if _filter != "":
		var needle: String = _filter.to_lower()
		if not (method_name.to_lower().contains(needle) or file_name.to_lower().contains(needle)):
			return false

	return true


func _exit_code() -> int:
	if _runner_error:
		return EXIT_RUNNER_ERROR
	if _failed > 0:
		return EXIT_TESTS_FAILED
	return EXIT_OK


func _load_test_dir() -> String:
	var text: String = FileAccess.get_file_as_string(CONFIG_PATH)
	if text == "":
		return DEFAULT_TEST_DIR
	var parsed: Variant = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		return DEFAULT_TEST_DIR
	var config: Dictionary = parsed
	var dir: String = str(config.get("test_dir", DEFAULT_TEST_DIR))
	if dir == "":
		return DEFAULT_TEST_DIR
	return dir


func _discover_test_scripts(test_dir: String) -> Array[String]:
	var found: Array[String] = []
	var dir: DirAccess = DirAccess.open(test_dir)
	if dir != null:
		found += _scan_dir(dir)
	found.sort()
	return found


func _scan_dir(dir: DirAccess) -> Array[String]:
	var out: Array[String] = []
	dir.list_dir_begin()
	while true:
		var f: String = dir.get_next()
		if f == "":
			break
		if dir.current_is_dir():
			if not f.begins_with("."):
				var sub: DirAccess = DirAccess.open(dir.get_current_dir() + "/" + f)
				if sub != null:
					out += _scan_dir(sub)
		else:
			if f.begins_with("test_") and f.ends_with(".gd"):
				out.append(dir.get_current_dir() + "/" + f)
	dir.list_dir_end()
	return out


func _run_all_tests(test_scripts: Array[String]) -> void:
	for script_path: String in test_scripts:
		var script: GDScript = load(script_path) as GDScript
		if script == null:
			# Not a test failure - the gate itself is broken. See EXIT_RUNNER_ERROR.
			_errors.append({"script": script_path, "error": "Failed to load script"})
			_runner_error = true
			continue

		# A script with a parse error still loads as a non-null GDScript, and
		# calling new() on it raises a runtime error that aborts the *calling*
		# function - which would silently end the whole run and still report
		# "all passed". Guard with can_instantiate(), and instantiate from a
		# helper so any surviving error only aborts that helper.
		if not script.can_instantiate():
			_errors.append({"script": script_path, "error": "Script failed to compile (see stderr)"})
			_runner_error = true
			continue

		var test_obj: RefCounted = _instantiate_test(script)
		if test_obj == null:
			_errors.append({"script": script_path, "error": "Failed to instantiate"})
			_runner_error = true
			continue

		# Inject assertion helper script reference
		var runner_script: GDScript = get_script() as GDScript
		test_obj.set("_T", runner_script)

		var methods: Array[Dictionary] = script.get_script_method_list()
		for method: Dictionary in methods:
			var method_name: String = method["name"]
			if not method_name.begins_with("test_"):
				continue
			_discovered += 1
			if not _is_selected(method_name, script_path):
				_skipped += 1
				continue
			_selected += 1

			await _run_single_test(test_obj, method_name, script_path)


# Isolated so a runtime error inside new() aborts only this call, leaving
# _run_all_tests free to carry on with the remaining scripts.
func _instantiate_test(script: GDScript) -> RefCounted:
	return script.new() as RefCounted


func _run_single_test(test_obj: RefCounted, method_name: String, script_path: String) -> void:
	# Call setup if it exists. `await` on a non-coroutine call resolves at once,
	# so synchronous setup/teardown/tests are unaffected.
	if test_obj.has_method("setup"):
		await test_obj.call("setup")

	# Recorded as a failure up front, so a test that never comes back (a runtime
	# error aborts this function too) can never be silently missing from the tally.
	var result: Dictionary = {
		"script": script_path,
		"test": method_name,
		"status": "FAIL",
		"message": "Test aborted before returning (runtime error - see stderr)",
		"elapsed_ms": 0,
	}
	_results.append(result)
	_failed += 1

	var start_time: int = Time.get_ticks_msec()

	# Run the test - assertion failures come back as the return value
	var raw_result: Variant = await test_obj.call(method_name)

	result["elapsed_ms"] = Time.get_ticks_msec() - start_time

	var error_msg: String = "" if raw_result == null else str(raw_result)
	if error_msg != "":
		result["message"] = error_msg
	else:
		result["status"] = "PASS"
		result["message"] = ""
		_failed -= 1
		_passed += 1

	# Call teardown if it exists
	if test_obj.has_method("teardown"):
		await test_obj.call("teardown")


func _print_results() -> void:
	if _json_output:
		var output: Dictionary = {
			"harness_version": HARNESS_VERSION,
			"passed": _passed,
			"failed": _failed,
			"skipped": _skipped,
			"total": _passed + _failed + _skipped,
			"discovered": _discovered,
			"selected": _selected,
			"filter": _filter,
			"file": _file_filter,
			"selection_error": _selection_error,
			"errors": _errors,
			"results": _results,
			"runner_error": _runner_error,
			"exit_code": _exit_code(),
		}
		print(JSON.stringify(output, "  "))
		return

	# Pretty-print
	var project_name: String = str(ProjectSettings.get_setting("application/config/name", ""))
	if project_name == "":
		project_name = "Godot"

	print("")
	print("=" .repeat(60))
	print("  %s Unit Tests  (godot-selftest-harness %s)" % [project_name, HARNESS_VERSION])
	print("=" .repeat(60))
	print("")

	for result: Dictionary in _results:
		var status: String = result["status"]
		var icon: String = "[PASS]" if status == "PASS" else "[FAIL]"
		var test_name: String = result["test"]
		var elapsed: int = result.get("elapsed_ms", 0)
		print("  %s %s (%dms)" % [icon, test_name, elapsed])
		if status == "FAIL":
			print("         %s" % result.get("message", ""))

	for err: Dictionary in _errors:
		print("  [ERR]  %s: %s" % [err["script"], err["error"]])

	print("")
	print("-" .repeat(60))
	var total: int = _passed + _failed + _skipped
	print("  Total: %d  |  Passed: %d  |  Failed: %d  |  Skipped: %d" % [total, _passed, _failed, _skipped])
	if _filter != "" or _file_filter != "":
		print("  Selected: %d of %d discovered  (%s)" % [_selected, _discovered, _selector_description()])
	print("-" .repeat(60))

	if _selection_error != "":
		print("  SELECTED NOTHING - %s (exit 2)" % _selection_error)
		print("  Nothing was verified. --filter matches method names and test script")
		print("  filenames; --file matches the script path. Run without selectors to")
		print("  see what exists.")
	elif _runner_error:
		print("  RUNNER ERROR - the suite did not run to completion (exit 2)")
	elif _failed == 0:
		print("  ALL TESTS PASSED")
	else:
		print("  SOME TESTS FAILED")
	print("")


# ============= Assertion Helpers (static) =============
# These are called by test scripts via the _T reference.

static func assert_eq(actual: Variant, expected: Variant, context: String = "") -> String:
	if actual == expected:
		return ""
	var msg: String = "Expected %s but got %s" % [str(expected), str(actual)]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_true(condition: bool, context: String = "") -> String:
	if condition:
		return ""
	var msg: String = "Expected true but got false"
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_false(condition: bool, context: String = "") -> String:
	if not condition:
		return ""
	var msg: String = "Expected false but got true"
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_float_eq(actual: float, expected: float, tolerance: float = 0.001, context: String = "") -> String:
	if absf(actual - expected) <= tolerance:
		return ""
	var msg: String = "Expected %.6f but got %.6f (tolerance: %.6f)" % [expected, actual, tolerance]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_gt(actual: Variant, threshold: Variant, context: String = "") -> String:
	if actual > threshold:
		return ""
	var msg: String = "Expected %s > %s" % [str(actual), str(threshold)]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_gte(actual: Variant, threshold: Variant, context: String = "") -> String:
	if actual >= threshold:
		return ""
	var msg: String = "Expected %s >= %s" % [str(actual), str(threshold)]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


# ============= Headless UI Helpers (static) =============
# Called by test scripts via the _T reference. Both are documented below; only
# instantiate_ui() is a coroutine, so it must be awaited:
#
#   var ui: Control = await _T.instantiate_ui("res://scenes/hud.tscn", Vector2i(640, 360))
#   var err: String = _T.assert_eq(ui.size, Vector2(640, 360), "hud fills the viewport")
#   _T.free_ui(ui)
#   return err
#
# Why they exist: a test script is a RefCounted and never enters the scene tree,
# so a Control instantiated inside one has no viewport to anchor against. `size`
# stays (0, 0), get_global_rect() is empty, and _ready() - and therefore every
# @onready var - never fires. instantiate_ui() puts the scene into a real
# SubViewport of a known size (entering a live tree is what fires _ready() for
# the whole subtree, so no propagate_notification(NOTIFICATION_READY) hack is
# needed), then lets the main loop tick so Godot's deferred layout pass actually
# resolves anchors, offsets, container sorting and minimum sizes.
#
# What headless still cannot do: the run uses the dummy rendering driver, so
# nothing is ever drawn. Layout is real and assertable - anchors, offsets,
# position/size, get_global_rect(), container arrangement, theme metrics. Anything
# pixel-dependent is out of scope: screenshots, viewport captures, texture or
# shader output, font rasterization, and anything that reads back rendered image
# data. Test the geometry here; test the pixels against a running game over the
# DevTools bridge.

const UI_SETTLE_FRAMES: int = 2
const UI_HOST_META: String = "_selftest_ui_host"


## Instantiates a UI scene into a real, sized SubViewport and lets the layout
## resolve. `scene` may be a PackedScene, a res:// path to one, or an already
## built Node (handy for building a Control tree in code). Returns the scene root,
## ready to assert on, or null if the scene could not be loaded. Always await it,
## and always pair it with free_ui().
static func instantiate_ui(scene: Variant, viewport_size: Vector2i = Vector2i(1152, 648)) -> Node:
	var tree: SceneTree = Engine.get_main_loop() as SceneTree
	if tree == null:
		push_error("instantiate_ui: no SceneTree main loop available")
		return null

	var node: Node = null
	if scene is Node:
		node = scene
	else:
		# `scene as PackedScene` would hard-error on a String, so branch on type.
		var packed: PackedScene = null
		if scene is PackedScene:
			packed = scene
		else:
			packed = load(str(scene)) as PackedScene
		if packed == null:
			push_error("instantiate_ui: could not load a PackedScene from %s" % str(scene))
			return null
		node = packed.instantiate()

	var host: SubViewport = SubViewport.new()
	host.name = "SelfTestUIHost"
	host.size = viewport_size
	host.disable_3d = true
	host.render_target_update_mode = SubViewport.UPDATE_DISABLED
	tree.root.add_child(host)
	host.add_child(node)
	node.set_meta(UI_HOST_META, host)

	# Control layout runs in a deferred pass, so tick the main loop until it settles.
	for _i: int in UI_SETTLE_FRAMES:
		await tree.process_frame

	return node


## Tears down a tree returned by instantiate_ui(): removes the host SubViewport
## from the scene tree and frees it immediately (not queue_free), so the nodes
## never show up in the orphan count that /verify's performance gate watches.
## Safe to call with null or an already-freed node.
static func free_ui(node: Node) -> void:
	if node == null or not is_instance_valid(node):
		return

	var target: Node = node
	if node.has_meta(UI_HOST_META):
		var host: Variant = node.get_meta(UI_HOST_META)
		if host is Node and is_instance_valid(host):
			target = host

	var parent: Node = target.get_parent()
	if parent != null:
		parent.remove_child(target)
	target.free()
