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
##   1  one or more tests failed, or verified nothing (see [VACUOUS] below)
##   2  the runner itself could not run, i.e. NOTHING WAS VERIFIED - test_dir is
##      missing, a test script failed to load / instantiate, no test scripts were
##      discovered at all, the discovered scripts held no test_* methods, or a
##      --filter/--file selected zero of the discovered tests. Treat this as a
##      broken gate, not as a test failure.
##
## That last case used to exit 0. A filter matching nothing skipped the entire
## suite and printed `Total: 0 | Passed: 0 | Failed: 0`, which is byte-identical
## to a clean pass for anything reading the exit code - and two agents in one
## session shipped work on the strength of it. A selection that selects nothing
## is now a runner error naming what it did: `filter 'spawner' selected 0 of 111
## discovered test(s)`.
##
## The reverse poisoning is also fixed (G-055): when --file/--filter is active,
## a test script that fails to LOAD but is OUTSIDE the selection no longer
## forces exit 2 over a fully passing selected subset. Such files are counted
## and printed (`Discovery errors: N (unselected; not affecting this run's
## exit)`) and the exit code is scored over the selected set only. With NO
## selector active a broken file still exits 2 - a whole-suite run genuinely
## could not run - and a SELECTED file failing to load stays exit 2 too.
##
## Every run states its denominator, selector or not (G-135):
##   Selected: 38 of 662 discovered  (file 'test_player_combat')
##   Selected: 662 of 662 discovered  (no selector)
##   Autoloads: 6 of 6 ready
##   Assertions: 1184 executed
##   Suite: 11 test script(s) in res://test/unit
## The bare run used to print only `Total: 662 | ...`, which is the same fact with
## nothing to compare it against - a discovery pass that silently found half the
## files it should have looked exactly like a full suite. `ALL TESTS PASSED` is
## now unreachable with a zero total: every way of running nothing is exit 2.
##
## Autoloads are stepped into readiness before discovery (findmyballs:G-002).
## In --script mode they are instantiated and parented to root, but the tree has
## not been stepped, so _ready() has NOT run when _initialize() begins: an
## autoload that builds its data there answers every test with an empty
## collection while lint reports every script compiled clean. One awaited frame
## fixes it; `Autoloads: N of M ready` is the receipt. See _await_autoloads().
##
## A test that returns pass having executed NONE of its own _T.assert_* calls is
## reported `[VACUOUS]` and scored as a failure. This is the other half of the
## same bug: three tests in the project that found it iterate a collection and
## assert inside the loop, and an EMPTY collection satisfies all three. The
## runner only makes this call when the method's own source contains a
## _T.assert_* call, so a project that hand-rolls its failure strings is never
## accused of anything.
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

# harness-version: 0.29.0
## Harness revision these files were copied from. See lint_project.gd / the
## `harness_version` bus verb; bump with .claude-plugin/plugin.json.
const HARNESS_VERSION: String = "0.29.0"

const CONFIG_PATH: String = "res://addons/godot_selftest/devtools_config.json"
const DEFAULT_TEST_DIR: String = "res://test/unit"

const EXIT_OK: int = 0
const EXIT_TESTS_FAILED: int = 1
const EXIT_RUNNER_ERROR: int = 2

var _passed: int = 0
var _failed: int = 0
var _skipped: int = 0
var _errors: Array[Dictionary] = []
## Load failures in scripts a selector excluded (G-055): reported, never scored.
var _discovery_errors: Array[Dictionary] = []
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
## Scripts INSIDE the selection that failed to load (gh#10). A selector naming a
## script that does not compile "matches nothing" - the script contributed 0 to
## _discovered - and the verdict used to blame the selector's spelling while the
## parse error sat 60 lines up. These are what let the verdict blame the right thing.
var _selected_load_failures: Array[String] = []

## The suite as an inherited asset, not just this run's tally. A session that
## finds `Suite: 3 test script(s) in res://test/unit` knows there is prior work
## to extend; one that finds `Suite: 1 test script(s)` (the seed alone) knows it
## is the first. Without this line the only visible numbers describe methods and
## assertions, and a project with no accumulated suite looks exactly like one
## whose suite this run happened to filter down to nothing.
var _test_files: int = 0
var _test_dir: String = ""

## Autoload readiness, reported on every run. See _await_autoloads().
var _autoloads_total: int = 0
var _autoloads_ready: int = 0
var _autoloads_unready: Array[String] = []

## Assertions executed through the _T.assert_* helpers, counted across the whole
## process. _run_single_test() snapshots it either side of a test method; the
## delta is that method's assertion count. Static because the helpers are.
static var _assertions_run: int = 0

var _vacuous: int = 0


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

	# A never-imported project has no class cache, so every `class_name` in it is
	# unresolvable and a test whose first statement uses one aborts on a runtime
	# error -- which this runner sees as a pass (H-029, gather:G-083). The evidence
	# is right there on disk. Refuse rather than report a suite that could not
	# have run: exit 2, one line, the fix named.
	if not FileAccess.file_exists("res://.godot/global_script_class_cache.cfg"):
		_runner_error = true
		_errors.append({
			"script": "res://.godot/global_script_class_cache.cfg",
			"error": "missing - this project has never been imported, so no class_name resolves "
				+ "and any test using one aborts silently. Run `godot --headless --path . --import` first",
		})
		_print_results()
		quit(_exit_code())
		return

	# MUST happen before any test runs. See _await_autoloads() for why.
	await _await_autoloads()

	var test_dir: String = _load_test_dir()
	_test_dir = test_dir
	if not DirAccess.dir_exists_absolute(test_dir):
		_runner_error = true
		_errors.append({"script": test_dir, "error": "test_dir does not exist"})
		_print_results()
		quit(_exit_code())
		return

	var test_scripts: Array[String] = _discover_test_scripts(test_dir)
	_test_files = test_scripts.size()
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

	# Scripts existed but held no test_* methods between them. Same fact as an
	# empty test_dir - nothing was verified - and it used to exit 0 reporting
	# `Total: 0 | ALL TESTS PASSED`, the exact reading this runner exists to
	# prevent. A selector cannot be the cause: it only ever reduces _selected,
	# never _discovered. Skipped when there are discovery errors, so a run whose
	# scripts merely failed to compile keeps the sharper message the zero-match
	# branch below already gives it.
	if _discovered == 0 and _discovery_errors.is_empty() and not _runner_error:
		_runner_error = true
		_errors.append({
			"script": test_dir,
			"error": "%d test script(s) found, but no test_* methods in them - nothing was verified" % test_scripts.size(),
		})

	# Discovery errors alone must not leave a 0-test run looking green: a
	# selector that matched nothing verifiable is still a broken gate.
	if _selected == 0 and (_discovered > 0 or not _discovery_errors.is_empty()):
		_runner_error = true
		_selection_error = "%s selected 0 of %d discovered test(s)" % [
			_selector_description(), _discovered,
		]

	_print_results()

	quit(_exit_code())


## Steps the tree once so every autoload finishes entering it and _ready() runs
## (findmyballs:G-002). Without this the whole suite tests a different program
## than the one that ships.
##
## In --script mode the autoloads ARE instantiated and ARE parented to root by
## the time _initialize() is called - but the tree has not been stepped, so
## ENTER_TREE and READY are still queued. Measured on 4.6.1:
##
##   in _initialize():   get_parent() = root, is_inside_tree() = FALSE,
##                       _ready() not run, its Array field empty
##   after 1 frame:      is_inside_tree() = true, _ready() run, field populated
##
## So an autoload that builds its data in _ready() answers with an EMPTY
## collection to every test, while lint reports every script compiled clean.
## The project that found this shipped three tests that iterate such a
## collection: an empty one satisfies all three, and they passed. That is why
## the assertion counter below exists as well as this await - the two halves of
## the same failure.
##
## Worse than uniformly-empty is what happened before: the runner awaits inside
## _run_single_test(), so the FIRST test that happened to await a frame flushed
## the notifications, and every test after it saw populated autoloads while
## every test before it saw empty ones. Suite behaviour depended on test order.
##
## One frame, before discovery, makes it deterministic. Note this is also why
## eval.gd's "--script mode never instantiates autoloads" was wrong: they are
## instantiated, they are just not ready yet.
func _await_autoloads() -> void:
	var names: Array[String] = _configured_autoloads()
	_autoloads_total = names.size()
	if _autoloads_total == 0:
		return

	await process_frame

	for name: String in names:
		var node: Node = root.get_node_or_null(NodePath(name))
		if node != null and node.is_node_ready():
			_autoloads_ready += 1
		else:
			_autoloads_unready.append(name)


## Autoload names from project.godot, in declaration order (which is the order
## Godot instantiates them in, so it is also dependency order).
func _configured_autoloads() -> Array[String]:
	var out: Array[String] = []
	for prop: Dictionary in ProjectSettings.get_property_list():
		var key: String = str(prop.get("name", ""))
		if key.begins_with("autoload/"):
			var name: String = key.substr("autoload/".length())
			if name != "":
				out.append(name)
	return out


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


## Selector text for the always-printed `Selected:` line. Reads "no selector" on
## a bare run, where _selector_description()'s "selection" is phrased to sit
## inside the zero-match error instead.
func _selector_label() -> String:
	if _filter == "" and _file_filter == "":
		return "no selector"
	return _selector_description()


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
			_record_load_failure(script_path, "Failed to load script")
			continue

		# A script with a parse error still loads as a non-null GDScript, and
		# calling new() on it raises a runtime error that aborts the *calling*
		# function - which would silently end the whole run and still report
		# "all passed". Guard with can_instantiate(), and instantiate from a
		# helper so any surviving error only aborts that helper.
		if not script.can_instantiate():
			_record_load_failure(script_path, "Script failed to compile (see stderr)")
			continue

		var test_obj: RefCounted = _instantiate_test(script)
		if test_obj == null:
			_record_load_failure(script_path, "Failed to instantiate")
			continue

		# Inject assertion helper script reference
		var runner_script: GDScript = get_script() as GDScript
		test_obj.set("_T", runner_script)

		var asserting: Dictionary = _methods_calling_assertions(script)

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

			await _run_single_test(
				test_obj, method_name, script_path, bool(asserting.get(method_name, false))
			)


## {method_name: bool} - does this method's own body contain a _T.assert_* call?
##
## The discriminator for a vacuous pass. The runner can only count the
## assertions IT provided, so "0 assertions executed" alone does not prove a
## test checked nothing: a project may hand-roll its failure strings, and
## calling that a failure would be the runner overclaiming. But a method whose
## body plainly calls _T.assert_* and then executes NONE of them did not check
## what it was written to check - the usual cause being a loop over a
## collection that turned out to be empty, which is exactly how three
## findmyballs tests passed against an autoload holding no data.
##
## Source-based because it is the only way to tell the two apart. Sliced per
## method (a top-level `func` at column 0 ends the previous one) rather than per
## script, so one hand-rolled test in an otherwise asserting file is not
## accused of anything.
func _methods_calling_assertions(script: GDScript) -> Dictionary:
	var out: Dictionary = {}
	var source: String = script.source_code
	if source == "":
		return out  # no source (exported build): claim nothing
	var current: String = ""
	for line: String in source.split("\n"):
		if line.begins_with("func ") or line.begins_with("static func "):
			var head: String = line.substr(line.find("func ") + 5)
			current = head.split("(")[0].strip_edges()
			out[current] = false
		elif current != "" and line.contains("_T.assert"):
			out[current] = true
	return out


# Isolated so a runtime error inside new() aborts only this call, leaving
# _run_all_tests free to carry on with the remaining scripts.
func _instantiate_test(script: GDScript) -> RefCounted:
	return script.new() as RefCounted


## A script that cannot load is a broken gate for whatever it would have run.
## Whole-suite run (no selector): exit 2 - nothing proves that file's tests
## pass. Selector active and the file OUTSIDE it: recorded as a discovery
## error, printed, but never scored - the exit code belongs to the selected
## set (G-055). A SELECTED file failing to load still poisons the run.
func _record_load_failure(script_path: String, error: String) -> void:
	var selector_active: bool = _filter != "" or _file_filter != ""
	if selector_active and not _script_matches_selectors(script_path):
		_discovery_errors.append({"script": script_path, "error": error})
		return
	_errors.append({"script": script_path, "error": error})
	_selected_load_failures.append(script_path)
	_runner_error = true


## File-level selection for scripts whose methods cannot be enumerated because
## they failed to load. --file matches exactly as in _is_selected; --filter can
## only be compared against the FILENAME - a file that will not compile has no
## method names to offer. Both active combine with AND, like _is_selected.
func _script_matches_selectors(script_path: String) -> bool:
	var file_name: String = script_path.get_file().to_lower()
	if _file_filter != "":
		var want: String = _file_filter.to_lower()
		var matched: bool = (
			file_name == want
			or file_name == want + ".gd"
			or script_path.to_lower().contains(want)
		)
		if not matched:
			return false
	if _filter != "":
		if not file_name.contains(_filter.to_lower()):
			return false
	return true


func _run_single_test(
	test_obj: RefCounted,
	method_name: String,
	script_path: String,
	expects_assertions: bool,
) -> void:
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
		"assertions": 0,
	}
	_results.append(result)
	_failed += 1

	var start_time: int = Time.get_ticks_msec()
	var assertions_before: int = _assertions_run

	# Run the test - assertion failures come back as the return value
	var raw_result: Variant = await test_obj.call(method_name)

	result["elapsed_ms"] = Time.get_ticks_msec() - start_time
	var assertions_used: int = _assertions_run - assertions_before
	result["assertions"] = assertions_used

	var error_msg: String = "" if raw_result == null else str(raw_result)
	if error_msg != "":
		result["message"] = error_msg
	elif expects_assertions and assertions_used == 0:
		# Returned the success value having executed none of the assertions it
		# is written around. Not a pass: nothing was checked.
		result["status"] = "VACUOUS"
		result["message"] = (
			"returned pass having executed 0 of its _T.assert_* calls - "
			+ "nothing was verified (an empty collection satisfies every loop over it)"
		)
		_vacuous += 1
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
			"vacuous": _vacuous,
			"assertions": _assertions_run,
			"test_files": _test_files,
			"test_dir": _test_dir,
			"autoloads_total": _autoloads_total,
			"autoloads_ready": _autoloads_ready,
			"autoloads_unready": _autoloads_unready,
			"filter": _filter,
			"file": _file_filter,
			"selection_error": _selection_error,
			"selected_load_failures": _selected_load_failures,
			"errors": _errors,
			"discovery_errors": _discovery_errors,
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
		var icon: String = "[PASS]"
		if status == "VACUOUS":
			icon = "[VACU]"
		elif status != "PASS":
			icon = "[FAIL]"
		var test_name: String = result["test"]
		var elapsed: int = result.get("elapsed_ms", 0)
		print("  %s %s (%dms)" % [icon, test_name, elapsed])
		if status != "PASS":
			print("         %s" % result.get("message", ""))

	for err: Dictionary in _errors:
		print("  [ERR]  %s: %s" % [err["script"], err["error"]])

	if not _discovery_errors.is_empty():
		print("")
		print("  Discovery errors: %d (unselected; not affecting this run's exit)" % _discovery_errors.size())
		for derr: Dictionary in _discovery_errors:
			print("    [SKIP] %s: %s" % [derr["script"], derr["error"]])

	print("")
	print("-" .repeat(60))
	var total: int = _passed + _failed + _skipped
	print("  Total: %d  |  Passed: %d  |  Failed: %d  |  Skipped: %d" % [total, _passed, _failed, _skipped])
	# Unconditional (G-135). On a filtered run this is the ratio the selector cut
	# the suite down to; on a bare run it is the discovery denominator, which is
	# the number that says whether the whole suite was even found.
	print("  Selected: %d of %d discovered  (%s)" % [_selected, _discovered, _selector_label()])
	# Denominators, both of them. A run that verified nothing must never be
	# reportable as a clean one, so say what was actually looked at.
	if _autoloads_total > 0:
		var autoload_note: String = ""
		if not _autoloads_unready.is_empty():
			autoload_note = "  NOT READY: %s" % ", ".join(_autoloads_unready)
		print("  Autoloads: %d of %d ready%s" % [
			_autoloads_ready, _autoloads_total, autoload_note,
		])
	print("  Assertions: %d executed" % _assertions_run)
	# The suite as a standing asset. Printed last of the denominators because it
	# is the one a fresh session should read first: it says how much checking
	# previous sessions left behind for this one to extend.
	print("  Suite: %d test script(s) in %s" % [_test_files, _test_dir])
	print("-" .repeat(60))

	if _selection_error != "" and not _selected_load_failures.is_empty():
		# The selector was right; the script it named never loaded, so it
		# contributed 0 to _discovered and the selector "matched nothing" (gh#10).
		# Selector-syntax advice here sends the reader to re-check the spelling
		# while the real cause sits 60 lines up in a parse backtrace.
		print("  SELECTED NOTHING - %d selected test script(s) FAILED TO COMPILE (see [ERR] above);" % _selected_load_failures.size())
		print("  a selector cannot match a script that did not load. Fix the parse error")
		print("  first: %s (exit 2)" % ", ".join(_selected_load_failures))
	elif _selection_error != "":
		print("  SELECTED NOTHING - %s (exit 2)" % _selection_error)
		print("  Nothing was verified. --filter matches method names and test script")
		print("  filenames; --file matches the script path. Run without selectors to")
		print("  see what exists.")
	elif _runner_error:
		print("  RUNNER ERROR - the suite did not run to completion (exit 2)")
	elif total == 0:
		# Belt and braces: every path that runs nothing already sets _runner_error
		# above, so this line should be unreachable. It exists so that no future
		# path can reach "ALL TESTS PASSED" with an empty tally.
		print("  NOTHING RAN - 0 of %d discovered test(s) executed, so nothing was verified" % _discovered)
	elif _vacuous > 0:
		print("  %d TEST(S) VERIFIED NOTHING - each returned pass without executing" % _vacuous)
		print("  any of its own _T.assert_* calls. The usual cause is a loop over a")
		print("  collection that was empty, which satisfies every assertion inside it.")
		if _failed > _vacuous:
			print("  (%d other test(s) also failed.)" % (_failed - _vacuous))
	elif _failed == 0:
		print("  ALL TESTS PASSED")
	else:
		print("  SOME TESTS FAILED")
	print("")


# ============= Assertion Helpers (static) =============
# These are called by test scripts via the _T reference.

static func assert_eq(actual: Variant, expected: Variant, context: String = "") -> String:
	_assertions_run += 1
	if actual == expected:
		return ""
	var msg: String = "Expected %s but got %s" % [str(expected), str(actual)]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_true(condition: bool, context: String = "") -> String:
	_assertions_run += 1
	if condition:
		return ""
	var msg: String = "Expected true but got false"
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_false(condition: bool, context: String = "") -> String:
	_assertions_run += 1
	if not condition:
		return ""
	var msg: String = "Expected false but got true"
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_float_eq(actual: float, expected: float, tolerance: float = 0.001, context: String = "") -> String:
	_assertions_run += 1
	if absf(actual - expected) <= tolerance:
		return ""
	var msg: String = "Expected %.6f but got %.6f (tolerance: %.6f)" % [expected, actual, tolerance]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_gt(actual: Variant, threshold: Variant, context: String = "") -> String:
	_assertions_run += 1
	if actual > threshold:
		return ""
	var msg: String = "Expected %s > %s" % [str(actual), str(threshold)]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


static func assert_gte(actual: Variant, threshold: Variant, context: String = "") -> String:
	_assertions_run += 1
	if actual >= threshold:
		return ""
	var msg: String = "Expected %s >= %s" % [str(actual), str(threshold)]
	if context != "":
		msg = "%s: %s" % [context, msg]
	return msg


# ============= Headless Scene/UI Helpers (static) =============
# Called by test scripts via the _T reference. instantiate_scene() and
# instantiate_ui() are coroutines, so they must be awaited:
#
#   var ui: Control = await _T.instantiate_ui("res://scenes/hud.tscn", Vector2i(640, 360))
#   var err: String = _T.assert_eq(ui.size, Vector2(640, 360), "hud fills the viewport")
#   _T.free_ui(ui)
#   return err
#
# Why they exist: a test script is a RefCounted and never enters the scene tree,
# so a Control instantiated inside one has no viewport to anchor against. `size`
# stays (0, 0), get_global_rect() is empty, and _ready() - and therefore every
# @onready var - never fires. instantiate_scene() puts the scene into a real
# SubViewport of a known size (entering a live tree is what fires _ready() for
# the whole subtree, so no propagate_notification(NOTIFICATION_READY) hack is
# needed), then lets the main loop tick so Godot's deferred layout pass actually
# resolves anchors, offsets, container sorting and minimum sizes.
#
# instantiate_scene() hosts ANY root - Node2D, plain Node, Control - so a
# gameplay scene gets the same treatment: _ready() fires, @onready resolves,
# groups register with the tree, timers and physics tick when frames advance.
# instantiate_ui() is the same call under its historical name (kept because
# existing tests use it); free_ui() tears down either.
#
# dispatch_events(viewport, events) pushes InputEvents through the hosted
# viewport synchronously with NO frame in between - all of them land inside one
# frame, which is what lets a test distinguish event-scoped handling (one
# reaction per event) from frame-scoped handling (coalesced until the next
# process pass). Get the viewport from a hosted node via node.get_viewport().
#
# stub_siblings(node, siblings) builds the sibling neighbourhood a script
# reaches for at _ready() time (get_node("../X")) without loading its real
# scene; pre-add groups with sib.add_to_group("g") before the call - groups set
# on a node outside any tree persist and register when the parent is hosted.
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


## Instantiates ANY scene - Node2D, plain Node or Control root alike - into a
## real, sized SubViewport and lets the tree settle. `scene` may be a
## PackedScene, a res:// path to one, or an already built Node (handy for a
## tree assembled in code, e.g. the parent from stub_siblings()). Returns the
## scene root, ready to assert on, or null if the scene could not be loaded.
## Always await it, and always pair it with free_ui().
static func instantiate_scene(scene: Variant, viewport_size: Vector2i = Vector2i(1152, 648)) -> Node:
	var tree: SceneTree = Engine.get_main_loop() as SceneTree
	if tree == null:
		push_error("instantiate_scene: no SceneTree main loop available")
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
			push_error("instantiate_scene: could not load a PackedScene from %s" % str(scene))
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


## Historical name for instantiate_scene(), kept verbatim for existing tests:
## identical behavior, identical signature, still a coroutine, still paired
## with free_ui().
static func instantiate_ui(scene: Variant, viewport_size: Vector2i = Vector2i(1152, 648)) -> Node:
	return await instantiate_scene(scene, viewport_size)


## Pushes each InputEvent in `events` through viewport.push_input(),
## synchronously, with NO frame yielded in between: every event lands inside
## the same frame. Not a coroutine - by the time it returns, every _input /
## _gui_input / _unhandled_input reaction has already run. Use the hosted
## SubViewport (node.get_viewport() on anything from instantiate_scene()).
static func dispatch_events(viewport: Viewport, events: Array) -> void:
	if viewport == null:
		push_error("dispatch_events: viewport is null")
		return
	for ev: Variant in events:
		if ev is InputEvent:
			viewport.push_input(ev)
		else:
			push_error("dispatch_events: skipped non-InputEvent entry %s" % str(ev))


## Builds the sibling neighbourhood `node` expects at _ready() time without
## loading its real scene: creates a plain Node parent, adds each
## {name: Node} entry from `siblings` under that name, then adds `node` LAST,
## so the siblings already exist when node._ready() fires on hosting. Returns
## the parent - the CALLER owns and frees it: free_ui(parent) works on any
## node (Control host or not); parent.free() is equivalent. The parent is NOT
## added to any tree; pass it to instantiate_scene() when _ready() / @onready
## must actually run. To populate a group for one test, call
## sib.add_to_group("X") BEFORE hosting - groups set outside a tree persist
## and register with the SceneTree on entry.
static func stub_siblings(node: Node, siblings: Dictionary) -> Node:
	var parent: Node = Node.new()
	parent.name = "StubSiblingHost"
	for sib_name: Variant in siblings:
		var sib: Node = siblings[sib_name]
		sib.name = str(sib_name)
		parent.add_child(sib)
	parent.add_child(node)
	return parent


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


## The widest line of `label.text`, in px, under the label's own resolved theme
## font/size (gh#20 / plant-tower-defense:G-033).
##
## `Control.get_minimum_size()` is NOT this measurement on a Label with
## `clip_text` or a non-default `text_overrun_behavior`: it reports the clip
## stub (~1px), not the text, so a width assertion written the obvious way
## ("does this text fit its column?") over any clipping/trimming Label passes
## unconditionally - the same shape as `ui_text_trimmed`'s own check in
## `dev_tools.gd`, which measures the same way rather than trusting the
## Control's own minimum size. Per line, not the whole string:
## `get_string_size()` lays a string holding "\n" out as ONE line.
static func text_width(label: Label) -> float:
	var font: Font = label.get_theme_font("font")
	if font == null:
		return 0.0
	var font_size: int = label.get_theme_font_size("font_size")
	if font_size <= 0:
		font_size = label.get_theme_default_font_size()
	var widest: float = 0.0
	for line: String in label.text.split("\n"):
		widest = maxf(widest, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x)
	return widest
