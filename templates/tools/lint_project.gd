@tool
extends SceneTree

# Combined linter: UID check, duplicate resource-id check, script compile check,
# and scene configuration warnings for the files under the configured scan_root.
#
# Run headless: godot --headless --path . --script res://tools/lint_project.gd
# With args:    godot --headless --path . --script res://tools/lint_project.gd -- --all --json
#
# Flags (everything after the bare `--` is read via OS.get_cmdline_user_args()):
#   --scene <path>        Lint only this scene (repeatable).
#   --all                 Lint every scene under scan_root (this is the default).
#   --json                Emit the full result dictionary as JSON.
#   --uids-only           Skip the scene-configuration pass.
#   --warnings-only       Skip the UID / duplicate-id pass.
#   --strict              Count warnings towards the exit code.
#   --fail-on-warn        Alias for --strict, kept for older callers.
#   --baseline-write <p>  Write the current finding keys to <p> as JSON, exit 0.
#   --baseline <p>        Compare against a written baseline: findings print as
#                         NEW or PRE-EXISTING, and only NEW ones affect the exit code.
#   --find-orphans        Opt-in heuristic scan for public functions nothing but
#                         the tests calls. Warnings only - never fails the run,
#                         not even under --strict.
#
# Exit codes. This script always calls quit() with its own finding count, so the
# code is the lint verdict and never Godot's shutdown noise about leaked RIDs or
# ObjectDB instances:
#   0  no errors
#   1  lint errors found (or warnings, under --strict)
#   2  the linter could not run: unreadable/invalid config, unopenable scan_root,
#      missing flag argument, or an unreadable/invalid baseline file
#
# Scope rules (applied to every pass):
#   - A directory containing a .gdignore file is skipped, exactly as Godot skips it.
#   - Any res://addons/<name>/ directory containing a plugin.cfg is treated as
#     vendored third-party code and exempted from ALL checks (reported once as
#     `VENDORED (skipped): addons/<name>`) - unless scan_root points inside it,
#     which is an explicit request to lint it.
#
# Script compile check: every discovered .gd is load()ed, which compiles it
# without instantiating (a @tool script's static initializers may still run).
# A script that fails to compile is an ERROR finding, rule `script_parse_failed`:
# lint green now means every script in scope can at least parse, i.e. the project
# can boot. If MANY scripts fail at once, suspect a stale global class cache -
# run `godot --headless --path . --import` before believing the findings.
#
# Global class cache check: every top-level `class_name` in the scanned scripts is
# compared against res://.godot/global_script_class_cache.cfg. Two rules:
#   class_cache_stale    ERROR - a class is declared but the cache does not know
#                        it. This is what produces a flood of `Could not find type
#                        "X" in the current scope` parse errors in files nobody
#                        touched, so it prints FIRST, above the compile results it
#                        explains. Fix: godot --headless --path . --import
#   class_cache_missing  advisory - no cache file at all (a fresh clone that has
#                        never been imported). Reported once, not once per class:
#                        that is one condition, not N findings.
#
# String-literal reference check, rule `string_ref_unresolved`: the name inside
# has_method("x") / has_signal("x") / emit_signal("x") / call("x") /
# call_deferred("x") / connect("x", ...) is flagged when no `func x` and no
# `signal x` exists anywhere in the project and ClassDB knows no such engine
# member. Advisory - a text scan cannot see a name built at runtime, so it must
# never gate a run.
#
# NodePath findings come in three rules:
#   nodepath_unresolved       ERROR - the path stays inside nodes this .tscn
#                             declares and lands nowhere: a real dangling path.
#   nodepath_crosses_instance advisory - the path enters an instanced sub-scene,
#                             whose internals a static pass cannot see. Reported,
#                             never counted, not even under --strict.
#   nodepath_empty            warning - a NodePath-like property left empty.
#
# Baseline workflow - two commands, no git shell-out, so it cannot hang:
#   1. at the merge-base:  godot ... lint_project.gd -- --baseline-write lint_baseline.json
#   2. on the working tree: godot ... lint_project.gd -- --baseline lint_baseline.json
# A finding key is "file|rule|subject" and deliberately carries no line numbers,
# so a finding survives an unrelated edit to the same file.
#
# Windows note: the plain (non-console) Godot .exe writes nothing to a PowerShell
# console. Redirect to a file and read it back. Every line this script emits goes
# to stdout via print(), so one redirect captures the whole report.

# harness-version: 0.10.0
## Harness revision these files were copied from. Printed in the header of every run so
## a lint result, and any gap logged from it, can name the version it was produced on.
const HARNESS_VERSION: String = "0.10.0"

const CONFIG_PATH: String = "res://addons/godot_selftest/devtools_config.json"
const DEFAULT_SCAN_ROOT: String = "res://"
const DEFAULT_TEST_DIR: String = "res://test/unit"

## Where the editor records every registered global class. Written through
## ConfigFile as a single root-section key:
##   list=Array[Dictionary]([{"base": &"Node", "class": &"Foo", "icon": "",
##   "language": &"GDScript", "path": "res://foo.gd"}, ...])
const CLASS_CACHE_PATH: String = "res://.godot/global_script_class_cache.cfg"

## The one command that rebuilds the class cache. Named in every finding about it,
## because the whole point of the check is that the fix travels with the symptom.
const IMPORT_HINT: String = "run `godot --headless --path . --import`"

## GDScript-level names ClassDB does not necessarily carry: script virtuals the
## engine calls by name, and the iterator protocol. Belt and braces on top of the
## ClassDB lookup in _engine_member_names() - a virtual missing from the method
## list would otherwise be a permanent false positive in every project.
const EXTRA_ENGINE_NAMES: Array[String] = [
	"_init", "_ready", "_process", "_physics_process", "_enter_tree", "_exit_tree",
	"_input", "_unhandled_input", "_unhandled_key_input", "_shortcut_input",
	"_gui_input", "_draw", "_notification", "_to_string", "_get", "_set",
	"_get_property_list", "_property_can_revert", "_property_get_revert",
	"_validate_property", "_integrate_forces", "_get_configuration_warnings",
	"_can_drop_data", "_drop_data", "_get_drag_data", "_make_custom_tooltip",
	"_has_point", "_structured_text_parser", "_get_minimum_size",
	"_iter_init", "_iter_next", "_iter_get",
]

const EXIT_OK: int = 0
const EXIT_LINT_ERRORS: int = 1
const EXIT_LINTER_FAILED: int = 2

# Every finding: {file, rule, subject, severity, message, advisory}
# "advisory" findings are reported but never counted, even under --strict.
var _findings: Array[Dictionary] = []
var _ident_re: RegEx = null
## res://addons/<name> directories holding a plugin.cfg: vendored third-party
## code, exempt from every pass. Filled once in _initialize().
var _vendored: Array[String] = []


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var scenes: PackedStringArray = []
	var all_scenes := false
	var json := false
	var strict := false
	var uids_only := false
	var warnings_only := false
	var find_orphans := false
	var baseline_read := ""
	var baseline_write := ""
	var arg_error := ""

	for i in args.size():
		match args[i]:
			"--scene":
				if i + 1 < args.size():
					scenes.append(args[i + 1])
				else:
					arg_error = "--scene needs a scene path"
			"--all":
				all_scenes = true
			"--json":
				json = true
			"--fail-on-warn", "--strict":
				strict = true
			"--uids-only":
				uids_only = true
			"--warnings-only":
				warnings_only = true
			"--find-orphans":
				find_orphans = true
			"--baseline":
				if i + 1 < args.size():
					baseline_read = args[i + 1]
				else:
					arg_error = "--baseline needs a path to a baseline JSON file"
			"--baseline-write":
				if i + 1 < args.size():
					baseline_write = args[i + 1]
				else:
					arg_error = "--baseline-write needs an output path"

	if arg_error != "":
		_abort(arg_error)
		return

	# Config: a missing file is fine (defaults apply), a malformed one is not.
	var config := {}
	if FileAccess.file_exists(CONFIG_PATH):
		var cfg_text := FileAccess.get_file_as_string(CONFIG_PATH)
		var parsed: Variant = JSON.parse_string(cfg_text)
		if typeof(parsed) != TYPE_DICTIONARY:
			_abort("%s is not a valid JSON object" % CONFIG_PATH)
			return
		config = parsed

	var scan_root := _norm_path(str(config.get("scan_root", DEFAULT_SCAN_ROOT)))
	if scan_root == "":
		scan_root = DEFAULT_SCAN_ROOT
	var test_dir := _norm_path(str(config.get("test_dir", DEFAULT_TEST_DIR)))
	if DirAccess.open(scan_root) == null:
		_abort("scan_root '%s' could not be opened" % scan_root)
		return

	# Vendored addons are computed before any scan so every pass skips them.
	_vendored = _detect_vendored_addons(scan_root)

	if all_scenes or scenes.is_empty():
		scenes = _find_all_scenes(scan_root)

	# Paths exempt from the missing-sidecar check. Defaults cover the files the
	# scaffolder copies in: it cannot generate a valid .uid (the ids are engine
	# assigned), so without this every fresh install would report findings that are
	# nobody's fault - and a gate that cries wolf on install day gets ignored.
	var uid_ignore: Array = config.get("uid_check_ignore", ["res://addons/", "res://tools/"])

	var results := {
		"uids": {
			"mismatches": [],
			"missing_sidecars": [],
			"had_error": false
		},
		"warnings": {
			"by_scene": [],
			"had_warn": false,
			"had_error": false
		},
		"duplicate_ids": []
	}

	# UID + duplicate-id check over all .tscn/.tres in scan_root, unless warnings-only
	if not warnings_only:
		var uid_ok := true
		for path in _scan(scan_root, ["tscn", "tres"]):
			var ok_one: bool = _check_uid_one(path, results["uids"])
			if not ok_one:
				uid_ok = false
			_check_duplicate_ids(path, results["duplicate_ids"])
		if not uid_ok:
			results["uids"]["had_error"] = true
		var gd_files := _collect_gd_files(scan_root, test_dir)
		_check_missing_uid_sidecars(gd_files, uid_ignore, results["uids"])
		# Before the compile pass on purpose: a stale class cache is the CAUSE of
		# the parse-error cascade that pass reports, and a cause diagnosed after
		# its own symptoms is a cause nobody reads.
		_check_class_cache(gd_files, uid_ignore, results)
		_check_scripts_compile(gd_files, uid_ignore, results)
		_check_string_refs(scan_root, gd_files, uid_ignore, results)

	# Scene configuration warnings for selected scenes, unless uids-only
	if not uids_only:
		for p in scenes:
			var entry = {"scene": p}
			var ps: PackedScene = load(p)
			if ps == null:
				entry["warnings"] = [{"path": ".", "messages": ["Scene failed to load (may need import cache)"]}]
				results["warnings"]["had_warn"] = true
				results["warnings"]["by_scene"].append(entry)
				_add_finding(p, "scene_load_failed", "", "warning", "Scene failed to load (may need import cache)")
				continue

			var state := ps.get_state()
			var warnings: Array = []
			if state != null:
				var node_count := state.get_node_count()
				# Own paths (get_node_path(i, false)), i.e. ".", "./Child", ... -
				# the set a NodePath property's target must land in to resolve.
				# (This pass previously collected PARENT paths and resolved
				# against them, which flagged perfectly good paths like a root
				# script's "World/TileMap" - the permanent-false-positive bug
				# G-052 existed to kill.)
				var path_set := {}
				# Own paths of nodes that are instances of another scene. A path
				# that lands under one of these enters territory this .tscn does
				# not declare, so it cannot be verified statically.
				var instance_roots := {}
				for i in range(node_count):
					var own := String(state.get_node_path(i, false))
					path_set[own] = true
					if state.get_node_instance(i) != null or state.get_node_instance_placeholder(i) != "":
						instance_roots[own] = true

				for ni in range(node_count):
					var node_abs_path := String(state.get_node_path(ni, false))
					var prop_cnt := state.get_node_property_count(ni)
					for pidx in range(prop_cnt):
						var p_name := String(state.get_node_property_name(ni, pidx))
						var p_val: Variant = state.get_node_property_value(ni, pidx)
						if _is_nodepath_like_property(p_name, p_val):
							var p_str := String(p_val)
							var subject := "%s.%s" % [node_abs_path, p_name]
							if p_str == "":
								var empty_msg := "SceneState: NodePath-like property '%s' is empty" % p_name
								warnings.append({"path": node_abs_path, "messages": [empty_msg]})
								_add_finding(p, "nodepath_empty", subject, "warning", empty_msg)
							else:
								var resolved: String = _resolve_relative_nodepath(node_abs_path, p_str)
								var unresolved := (resolved != "") and (not _path_set_has_relaxed(path_set, resolved))
								if unresolved:
									if _crosses_instance(resolved, instance_roots):
										# Advisory: the target lives inside an instanced
										# sub-scene, which SceneState cannot see into.
										# Never counted, not even under --strict.
										var adv_msg := "SceneState: '%s' NodePath %s enters instanced sub-scene (-> %s) - not statically checkable (advisory)" % [p_name, p_str, resolved]
										warnings.append({"path": node_abs_path, "messages": [adv_msg]})
										_add_finding(p, "nodepath_crosses_instance", subject, "warning", adv_msg, true)
									else:
										# The whole path stays inside nodes this scene
										# declares and lands nowhere: a real dangling path.
										var msg := "SceneState: '%s' NodePath unresolved: %s (-> %s) - dangling within this scene" % [p_name, p_str, resolved]
										warnings.append({"path": node_abs_path, "messages": [msg]})
										_add_finding(p, "nodepath_unresolved", subject, "error", msg)

			if warnings.size() > 0:
				results["warnings"]["had_warn"] = true
			entry["warnings"] = warnings
			results["warnings"]["by_scene"].append(entry)

	# Opt-in orphan scan. Advisory only: it is a heuristic and never gates a run.
	if find_orphans:
		results["orphans"] = _find_orphans(scan_root, test_dir)

	# Baseline: split findings into what the baseline already knew about and what is new.
	var baseline_keys := {}
	if baseline_read != "":
		var loaded: Variant = _load_baseline(baseline_read)
		if loaded == null:
			return
		baseline_keys = loaded
	var new_findings: Array[Dictionary] = []
	var old_findings: Array[Dictionary] = []
	for f in _findings:
		if baseline_keys.has(_finding_key(f)):
			old_findings.append(f)
		else:
			new_findings.append(f)

	# --baseline-write records the whole finding set and never fails the run.
	if baseline_write != "":
		_write_baseline(baseline_write)
		return

	# Output
	results["vendored"] = _vendored
	if json:
		results["harness_version"] = HARNESS_VERSION
		results["findings"] = _findings
		if baseline_read != "":
			results["baseline"] = {"path": baseline_read, "new": new_findings, "pre_existing": old_findings}
		print(JSON.stringify(results, "  "))
	else:
		# Header first: every lint result is evidence, and evidence that cannot name the
		# version it came from cannot be told apart from a regression later.
		print("lint: godot-selftest-harness %s | scan_root %s" % [HARNESS_VERSION, scan_root])
		for v in _vendored:
			print("VENDORED (skipped): %s" % String(v).trim_prefix("res://"))
		if not warnings_only:
			# Class cache above everything else: when it is stale it is the single
			# cause of every parse error printed below it, and burying the cause
			# under 135 lines of its own symptoms is the whole gap.
			var cc: Dictionary = results.get("class_cache", {})
			for cline in cc.get("messages", []):
				print(str(cline))
			var sc: Dictionary = results.get("scripts", {"checked": 0, "failed": []})
			if sc["failed"].is_empty():
				print("Scripts: %d compiled OK" % int(sc["checked"]))
			else:
				for fs in sc["failed"]:
					print("ERROR: %s: %s" % [fs["path"], fs["message"]])
				if sc["failed"].size() >= 3:
					print("hint: many scripts failing together usually means a stale class cache - run `godot --headless --path . --import` and re-lint")
			# "UIDs: OK" is only printed when BOTH passes are clean. It used to print
			# while a script sat there with no sidecar at all, which is the report
			# lying about what it checked.
			if results["uids"]["mismatches"].is_empty() and results["uids"]["missing_sidecars"].is_empty():
				print("UIDs: OK")
			else:
				for m in results["uids"]["mismatches"]:
					print("%s: uid mismatch for %s -> file has %s, expected %s" % [m.path, m.res_path, m.file_uid, m.expected_uid])
				for s in results["uids"]["missing_sidecars"]:
					print("WARN: %s: %s" % [s.path, s.message])
			for d in results["duplicate_ids"]:
				print("ERROR: %s: %s" % [d.path, d.message])
			var sr: Dictionary = results.get("string_refs", {})
			for u in sr.get("unresolved", []):
				print("ADVISORY: %s: %s" % [u["file"], u["message"]])
		if not uids_only:
			for r in results["warnings"]["by_scene"]:
				if "error" in r:
					print("%s: %s" % [r.scene, r.error])
				elif r.warnings.is_empty():
					print("%s: OK" % r.scene)
				else:
					for w in r.warnings:
						print("%s | %s: %s" % [r.scene, w.path, ", ".join(w.messages)])
		if find_orphans:
			for o in results["orphans"]:
				print("WARN: %s: %s" % [o.file, o.message])
		if baseline_read != "":
			_print_baseline_groups(baseline_read, baseline_keys.size(), new_findings, old_findings)

	# Exit code: driven by this script's own findings, never by Godot's shutdown noise.
	var counted: Array[Dictionary] = new_findings if baseline_read != "" else _findings
	var errors := 0
	var warns := 0
	for f in counted:
		if f["advisory"]:
			continue
		if f["severity"] == "error":
			errors += 1
		else:
			warns += 1

	var exit_code := EXIT_OK
	if errors > 0 or (strict and warns > 0):
		exit_code = EXIT_LINT_ERRORS
	if not json:
		var scope := "new " if baseline_read != "" else ""
		print("lint: %d %serror(s), %d %swarning(s) -> exit %d" % [errors, scope, warns, scope, exit_code])
	quit(exit_code)


# --- Findings ---
func _add_finding(file: String, rule: String, subject: String, severity: String, message: String, advisory: bool = false) -> void:
	_findings.append({
		"file": _norm_path(file),
		"rule": rule,
		"subject": subject,
		"severity": severity,
		"message": message,
		"advisory": advisory,
	})


# Stable across unrelated edits: no line numbers, only file + rule + subject.
func _finding_key(f: Dictionary) -> String:
	return "%s|%s|%s" % [f["file"], f["rule"], f["subject"]]


func _abort(reason: String) -> void:
	print("LINTER ERROR: %s" % reason)
	print("lint: could not run -> exit %d" % EXIT_LINTER_FAILED)
	quit(EXIT_LINTER_FAILED)


# --- Baseline ---
func _write_baseline(path: String) -> void:
	var keys: Array[String] = []
	for f in _findings:
		var k := _finding_key(f)
		if not keys.has(k):
			keys.append(k)
	keys.sort()
	var out := FileAccess.open(path, FileAccess.WRITE)
	if out == null:
		_abort("could not write baseline to '%s' (error %d)" % [path, FileAccess.get_open_error()])
		return
	out.store_string(JSON.stringify(keys, "  "))
	out.close()
	print("baseline: wrote %d finding key(s) to %s" % [keys.size(), path])
	quit(EXIT_OK)


# Returns a Dictionary used as a key set, or null after aborting.
func _load_baseline(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		_abort("baseline file '%s' not found (write one first with --baseline-write)" % path)
		return null
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(parsed) != TYPE_ARRAY:
		_abort("baseline file '%s' is not a JSON array of finding keys" % path)
		return null
	var keys := {}
	for k in parsed:
		keys[str(k)] = true
	return keys


func _print_baseline_groups(path: String, key_count: int, new_findings: Array[Dictionary], old_findings: Array[Dictionary]) -> void:
	print("")
	print("baseline: %s (%d key(s))" % [path, key_count])
	if new_findings.is_empty():
		print("NEW: none")
	for f in new_findings:
		print("NEW | %s" % _format_finding(f))
	if old_findings.is_empty():
		print("PRE-EXISTING: none")
	for f in old_findings:
		print("PRE-EXISTING | %s" % _format_finding(f))


func _format_finding(f: Dictionary) -> String:
	var tag: String = "ADVISORY" if f["advisory"] else String(f["severity"]).to_upper()
	return "[%s] %s: %s" % [tag, f["file"], f["message"]]


# --- UID check ---
func _scan(root: String, exts: Array[String]) -> Array[String]:
	var files: Array[String] = []
	var dir := DirAccess.open(root)
	if dir:
		files += _scan_dir(dir, exts)
	return files


func _scan_dir(dir: DirAccess, exts: Array[String]) -> Array[String]:
	var out: Array[String] = []
	if dir == null:
		return out
	dir.list_dir_begin()
	while true:
		var f := dir.get_next()
		if f == "":
			break
		if dir.current_is_dir():
			var full := _norm_path(dir.get_current_dir() + "/" + f)
			# Dot-directories (.godot, .git, .claude worktrees...) are invisible
			# to Godot's importer and must be invisible here too - a worktree
			# under .claude/ holds a full project copy whose every class_name
			# would otherwise "hide a global script class" in the compile pass.
			if not f.begins_with(".") and not _dir_skipped(full):
				out += _scan_dir(DirAccess.open(full), exts)
		else:
			for e in exts:
				if f.ends_with("." + e):
					out.append(_norm_path(dir.get_current_dir() + "/" + f))
	dir.list_dir_end()
	return out


# True for any directory every pass must stay out of: one Godot itself ignores
# (it contains a .gdignore file) or one holding a vendored addon.
func _dir_skipped(dir_path: String) -> bool:
	if FileAccess.file_exists(dir_path + "/.gdignore"):
		return true
	var n := _norm_path(dir_path)
	for v in _vendored:
		if n == v or n.begins_with(v + "/"):
			return true
	return false


# Any res://addons/<name>/ directory with a plugin.cfg is vendored third-party
# code: exempt from all checks, unless scan_root explicitly points inside it.
func _detect_vendored_addons(scan_root: String) -> Array[String]:
	var out: Array[String] = []
	var addons := DirAccess.open("res://addons")
	if addons == null:
		return out
	addons.list_dir_begin()
	while true:
		var f := addons.get_next()
		if f == "":
			break
		if addons.current_is_dir() and not f.begins_with("."):
			var p := "res://addons/" + f
			if FileAccess.file_exists(p + "/plugin.cfg") and not scan_root.begins_with(p):
				out.append(p)
	addons.list_dir_end()
	out.sort()
	return out


func _check_uid_one(p: String, out) -> bool:
	var ok := true
	var text := FileAccess.get_file_as_string(p)
	if text == "":
		out["mismatches"].append({"path": p, "res_path": "", "file_uid": "", "expected_uid": "<failed to read>"})
		_add_finding(p, "read_failed", "", "error", "failed to read file")
		return false
	for line in text.split("\n"):
		if line.begins_with("[ext_resource "):
			var path := _extract(line, "path")
			var uid := _extract(line, "uid")
			if path != "" and uid != "":
				var id: int = ResourceLoader.get_resource_uid(path)
				if id != ResourceUID.INVALID_ID:
					var expected := ResourceUID.id_to_text(id)
					if uid != expected:
						out["mismatches"].append({"path": p, "res_path": path, "file_uid": uid, "expected_uid": expected})
						_add_finding(p, "uid_mismatch", path, "error", "uid mismatch for %s -> file has %s, expected %s" % [path, uid, expected])
						ok = false
	return ok


# --- Missing .uid sidecars ---
# The mismatch pass above only validates sidecars that already exist, so a script
# created outside the editor - which is every script an agent writes - reported
# "UIDs: OK" while having no sidecar at all. The omission then surfaces at review
# time, or as a broken reference on somebody else's machine.
#
# Self-calibrating: Godot only began writing .uid files for scripts in 4.4, and a
# project that has never been imported has none either. If not one .gd in the
# project has a sidecar, the engine or the checkout simply does not do this, and
# flagging every file would be noise rather than a finding - so the check stands
# down entirely.
func _collect_gd_files(scan_root: String, test_dir: String) -> Array[String]:
	var gd_files := _scan(scan_root, ["gd"])
	if test_dir != "" and not test_dir.begins_with(scan_root):
		for p in _scan(test_dir, ["gd"]):
			if not gd_files.has(p):
				gd_files.append(p)
	return gd_files


func _check_missing_uid_sidecars(gd_files: Array[String], ignore: Array, out) -> void:
	var any_sidecar := false
	for p in gd_files:
		if FileAccess.file_exists(p + ".uid"):
			any_sidecar = true
			break
	if not any_sidecar:
		return

	for p in gd_files:
		if _is_uid_ignored(p, ignore):
			continue
		if FileAccess.file_exists(p + ".uid"):
			continue
		var msg := "no .uid sidecar - open the project in the editor to generate one and commit it alongside the script"
		out["missing_sidecars"].append({"path": p, "message": msg})
		_add_finding(p, "uid_sidecar_missing", "", "warning", msg)


func _is_uid_ignored(path: String, ignore: Array) -> bool:
	for prefix in ignore:
		var s := str(prefix)
		if s != "" and path.begins_with(s):
			return true
	return false


# --- Script compile check (G-034) ---
# load() compiles a GDScript without instantiating it. A script that fails to
# load or compile cannot boot the project, so it is a lint ERROR - this is what
# makes "lint green" mean "the project can parse everything it ships". Skipped:
# vendored addons (already out of gd_files) and the addons/ prefixes listed in
# uid_check_ignore - third-party code is not this project's debt.
func _check_scripts_compile(gd_files: Array[String], ignore: Array, results: Dictionary) -> void:
	var checked := 0
	var failed: Array = []
	for p in gd_files:
		if p.begins_with("res://addons/") and _is_uid_ignored(p, ignore):
			continue
		checked += 1
		var res: Resource = load(p)
		var detail := ""
		if res == null:
			detail = "script failed to load (parse error - see stderr)"
		elif res is GDScript:
			var gds: GDScript = res
			# 4.5+ abstract scripts are valid but deliberately non-instantiable.
			var is_abstract: bool = gds.has_method("is_abstract") and gds.is_abstract()
			if not gds.can_instantiate() and not is_abstract:
				detail = "script failed to compile (see stderr)"
		if detail != "":
			failed.append({"path": p, "message": detail})
			_add_finding(p, "script_parse_failed", p, "error", detail)
	results["scripts"] = {"checked": checked, "failed": failed}


# --- Global class cache check (G-090) ---
# A `class_name` the engine has not registered yet costs one Parse Error per
# *referencing* file, not one per missing class: a single absent entry produced
# 135 `Could not find type "X" in the current scope` lines and a test-runner
# exit 2, in files nobody had touched. The case that bites is *receiving* a
# class_name over a pull, rebase or branch switch - you added nothing, so nothing
# in the output suggests re-importing.
#
# Both halves of the comparison are already in hand here: the scanned scripts
# hold the declarations, .godot/global_script_class_cache.cfg holds what the
# engine knows. Same skip rules as the compile pass, so a vendored or ignored
# addon is not this project's debt.
func _check_class_cache(gd_files: Array[String], ignore: Array, results: Dictionary) -> void:
	# GDScript allows at most one top-level `class_name` per file, and it must sit
	# at column 0 - so an anchored search() (not search_all) is exact, and `class
	# Foo:` inner classes and commented-out lines can never match.
	var decl_re := RegEx.new()
	decl_re.compile("(?m)^class_name[ \\t]+([A-Za-z_][A-Za-z0-9_]*)")
	var declared: Array[Dictionary] = []
	for p in gd_files:
		if p.begins_with("res://addons/") and _is_uid_ignored(p, ignore):
			continue
		var m := decl_re.search(FileAccess.get_file_as_string(p))
		if m != null:
			declared.append({"class": m.get_string(1), "path": p})

	var state := {"declared": declared.size(), "state": "ok", "missing": [], "messages": []}
	results["class_cache"] = state
	if declared.is_empty():
		state["state"] = "no_classes"
		return

	# A project that has never been imported is a DIFFERENT condition from a stale
	# one, and it is one condition - so it gets one advisory line, not one finding
	# per declared class. Advisory because a fresh clone is not a defect.
	if not FileAccess.file_exists(CLASS_CACHE_PATH):
		state["state"] = "absent"
		var absent_msg := "no %s - this project has never been imported, so not one of its %d `class_name` declarations is registered; %s before believing any parse error below (advisory)" % [CLASS_CACHE_PATH, declared.size(), IMPORT_HINT]
		state["messages"].append("ADVISORY: %s" % absent_msg)
		_add_finding(CLASS_CACHE_PATH, "class_cache_missing", "", "warning", absent_msg, true)
		return

	var cached := _read_class_cache()
	# Cache present but empty while classes are declared: same single cause, same
	# single line. Fatal, unlike the absent case - the engine WILL fail to resolve.
	if cached.is_empty():
		state["state"] = "empty"
		var empty_msg := "stale class cache: %s registers no classes at all while this project declares %d - %s" % [CLASS_CACHE_PATH, declared.size(), IMPORT_HINT]
		state["messages"].append("ERROR: %s" % empty_msg)
		_add_finding(CLASS_CACHE_PATH, "class_cache_stale", "", "error", empty_msg)
		return

	for d in declared:
		var cls: String = d["class"]
		if cached.has(cls):
			continue
		var msg := "stale class cache: \"%s\" is declared in %s but absent from .godot/global_script_class_cache.cfg - %s" % [cls, d["path"], IMPORT_HINT]
		state["missing"].append({"class": cls, "path": d["path"], "message": msg})
		state["messages"].append("ERROR: %s" % msg)
		# ERROR, not advisory: this is the condition that produced 135 bogus parse
		# errors, and a run that reports it must not exit 0.
		_add_finding(d["path"], "class_cache_stale", cls, "error", msg)
	if state["missing"].is_empty():
		state["messages"].append("Class cache: %d class_name declaration(s) registered" % declared.size())
	else:
		state["state"] = "stale"


## Class names the engine currently has registered, as a key set.
## ConfigFile reads the editor's own format directly; the text fallback exists so
## a truncated or half-written cache degrades into a partial answer instead of
## turning a diagnostic into a linter abort.
func _read_class_cache() -> Dictionary:
	var out := {}
	var cf := ConfigFile.new()
	if cf.load(CLASS_CACHE_PATH) == OK:
		var list: Variant = cf.get_value("", "list", [])
		if typeof(list) == TYPE_ARRAY:
			for e in list:
				if typeof(e) == TYPE_DICTIONARY and e.has("class"):
					out[String(e["class"])] = true
	if not out.is_empty():
		return out
	var text := FileAccess.get_file_as_string(CLASS_CACHE_PATH)
	if text == "":
		return out
	var re := RegEx.new()
	re.compile("\"class\"[ \\t]*:[ \\t]*&?\"([A-Za-z_][A-Za-z0-9_]*)\"")
	for m in re.search_all(text):
		out[m.get_string(1)] = true
	return out


# --- Unresolved string-literal references (G-098) ---
# An orchestrator pinned a panel API in prose: one agent wrote has_method("open"),
# another implemented `func set_open(bool)`. Nothing in lint, the type system or
# the tests connects a string literal in one file to a function name in another,
# so the seam held green until the game ran.
#
# This is the _find_orphans scan pointed the other way: instead of declarations
# nobody references, references that declare nowhere. Always on (not behind
# --find-orphans) because the entire failure mode is that nobody knew to look.
#
# Severity is ADVISORY, deliberately, and the honest reason is `call`: a Callable
# invoked as `cb.call("player")` passes a data string, not a method name, and no
# text scan can tell that from `obj.call("player")`. A noisy new ERROR would teach
# projects to stop reading lint output, which is worse than the gap; an advisory
# that names a real broken seam costs one line.
#
# Residual false-positive sources, after comment stripping and the ClassDB lookup:
#   - `call("literal")` on a Callable, as above - the one that cannot be fixed.
#   - members of a GDExtension or C# class. ClassDB carries a GDExtension class
#     only once the extension is loaded, which a `--script` run may not do.
#   - built-in Variant-type methods (Array.append, String.split, Callable.bind),
#     which live outside ClassDB entirely.
# Names built by concatenation or held in variables simply do not match: no
# finding, no crash, and no claim that they were checked.
#
# Skipped as call SITES (never as declaration sources): the uid_check_ignore
# prefixes, because a harness or third-party runner probing for an optional hook
# by name is not this project's debt, and `*.example.gd`, which is reference
# material that names verbs the reading project is expected not to have yet.
func _check_string_refs(scan_root: String, gd_files: Array[String], ignore: Array, results: Dictionary) -> void:
	# call_deferred before call so the longer verb wins the alternation; the
	# lookbehind keeps `my_call(` and `is_connected(` out; the required CLOSING
	# quote is what stops `connect("ws://host")` matching as the name `ws`.
	var call_re := RegEx.new()
	call_re.compile("(?<![A-Za-z0-9_])(has_method|has_signal|emit_signal|call_deferred|call|connect)[ \\t]*\\([ \\t]*\"([A-Za-z_][A-Za-z0-9_]*)\"")

	var candidates: Array[Dictionary] = []
	var seen := {}
	for p in gd_files:
		if _is_uid_ignored(p, ignore) or p.get_file().ends_with(".example.gd"):
			continue
		var text := _strip_comments(FileAccess.get_file_as_string(p))
		if text == "":
			continue
		for m in call_re.search_all(text):
			var key := "%s|%s" % [p, m.get_string(2)]
			if seen.has(key):
				continue
			seen[key] = true
			candidates.append({"file": p, "verb": m.get_string(1), "name": m.get_string(2)})

	results["string_refs"] = {"checked": candidates.size(), "unresolved": []}
	if candidates.is_empty():
		return

	var declared := _declared_member_names(scan_root, gd_files)
	# Enumerating ClassDB is only worth its cost once something has already failed
	# to resolve against project code, which on a healthy project is never.
	var engine := {}
	var engine_built := false
	for c in candidates:
		var member: String = c["name"]
		if declared.has(member):
			continue
		if not engine_built:
			engine = _engine_member_names()
			engine_built = true
		if engine.has(member):
			continue
		var msg := "unresolved string reference: %s(\"%s\") - no `func %s` or `signal %s` exists anywhere in the project and ClassDB knows no such engine member; check the spelling, or the contract between the two files (advisory)" % [c["verb"], member, member, member]
		results["string_refs"]["unresolved"].append({"file": c["file"], "name": member, "verb": c["verb"], "message": msg})
		_add_finding(c["file"], "string_ref_unresolved", member, "warning", msg, true)


## `text` with GDScript comments removed, so a documented example such as
## `# has_method("x")` in a header block is not read as a call site. Scanned per
## line and per character so a `#` inside a string literal survives; a line that
## opens a `"""` block leaves the quote state unbalanced, which only means its own
## trailing comment is not stripped - never that live code is.
func _strip_comments(text: String) -> String:
	var out: PackedStringArray = []
	for line in text.split("\n"):
		var quote := ""
		var cut := -1
		var i := 0
		while i < line.length():
			var ch := line[i]
			if quote != "":
				if ch == "\\":
					i += 2
					continue
				if ch == quote:
					quote = ""
			elif ch == "\"" or ch == "'":
				quote = ch
			elif ch == "#":
				cut = i
				break
			i += 1
		out.append(line.substr(0, cut) if cut >= 0 else line)
	return "\n".join(out)


## Every `func x(` and `signal x` declared anywhere the project ships, as a key
## set. Over-matching is the safe direction here (it only suppresses findings), so
## no attempt is made to exclude comments or strings.
func _declared_member_names(scan_root: String, gd_files: Array[String]) -> Dictionary:
	var files := {}
	for p in gd_files:
		files[p] = true
	for p in _scan_declaration_sources(scan_root):
		files[p] = true

	var func_re := RegEx.new()
	# No ^ anchor: catches `static func`, inner-class methods at any indent, and
	# an inline annotation such as `@rpc("any_peer") func sync_state():`.
	func_re.compile("(?<![A-Za-z0-9_])func[ \\t]+([A-Za-z_][A-Za-z0-9_]*)[ \\t]*\\(")
	var signal_re := RegEx.new()
	signal_re.compile("(?m)^[ \\t]*signal[ \\t]+([A-Za-z_][A-Za-z0-9_]*)")

	var out := {}
	for fp in files:
		var text := FileAccess.get_file_as_string(String(fp))
		if text == "":
			continue
		for m in func_re.search_all(text):
			out[m.get_string(1)] = true
		for m2 in signal_re.search_all(text):
			out[m2.get_string(1)] = true
	return out


## Recursive .gd walk honouring .gdignore and dot-directories but NOT the
## vendored-addon exemption: a vendored addon is exempt from being reported ON,
## not from being a legitimate place for a signal the project connects by name.
func _scan_declaration_sources(root: String) -> Array[String]:
	var out: Array[String] = []
	var d := DirAccess.open(root)
	if d == null:
		return out
	d.list_dir_begin()
	while true:
		var f := d.get_next()
		if f == "":
			break
		var full := _norm_path(d.get_current_dir() + "/" + f)
		if d.current_is_dir():
			if not f.begins_with(".") and not FileAccess.file_exists(full + "/.gdignore"):
				out += _scan_declaration_sources(full)
		elif f.ends_with(".gd"):
			out.append(full)
	d.list_dir_end()
	return out


## Union of every method and signal name ClassDB carries, across every class.
## no_inheritance=true keeps this to each class's own members - the union over all
## classes is still the complete set, at a fraction of the allocations.
func _engine_member_names() -> Dictionary:
	var out := {}
	for c in ClassDB.get_class_list():
		for m in ClassDB.class_get_method_list(c, true):
			out[String(m.get("name", ""))] = true
		for s in ClassDB.class_get_signal_list(c, true):
			out[String(s.get("name", ""))] = true
	for n in EXTRA_ENGINE_NAMES:
		out[n] = true
	return out


# --- Duplicate resource ids ---
# Text-level on purpose: two branches each adding an [ext_resource] id to the same
# scene produce a duplicate that loads without complaint and binds the wrong
# resource, so loading the scene would not reveal it.
func _check_duplicate_ids(p: String, out: Array) -> void:
	var text := FileAccess.get_file_as_string(p)
	if text == "":
		return  # already reported by the UID pass
	var seen := {}
	var lines := text.split("\n")
	for i in lines.size():
		var line: String = lines[i]
		var kind := ""
		if line.begins_with("[ext_resource "):
			kind = "ext_resource"
		elif line.begins_with("[sub_resource "):
			kind = "sub_resource"
		else:
			continue
		var id := _extract_id(line)
		if id == "":
			continue
		# ExtResource() and SubResource() are separate lookups, so ids collide per kind.
		var key := "%s %s" % [kind, id]
		if not seen.has(key):
			seen[key] = {"kind": kind, "id": id, "lines": []}
		seen[key]["lines"].append(i + 1)
	for key in seen:
		var hit: Dictionary = seen[key]
		var at: Array = hit["lines"]
		if at.size() < 2:
			continue
		var at_text: PackedStringArray = []
		for n in at:
			at_text.append(str(n))
		var where := ", ".join(at_text)
		var msg := "duplicate %s id \"%s\" (lines %s) - binds the wrong resource silently" % [hit["kind"], hit["id"], where]
		out.append({"path": p, "kind": hit["kind"], "id": hit["id"], "lines": at, "message": msg})
		_add_finding(p, "duplicate_resource_id", key, "error", msg)


# --- Orphan API scan (heuristic, opt-in, advisory) ---
# Flags public funcs whose only callers outside their own file live in tests.
# Known false positives: signal callbacks wired in code, virtual overrides,
# call()/callv() by a computed name, and @export/inspector-assigned hooks.
func _find_orphans(scan_root: String, test_dir: String) -> Array:
	var out: Array = []
	var gd_files := _scan(scan_root, ["gd"])
	var identifiers := {}      # file -> {identifier: true}
	var declared_in := {}      # func name -> Array[String] of declaring files
	var decl_re := RegEx.new()
	decl_re.compile("func\\s+([A-Za-z][A-Za-z0-9_]*)\\s*\\(")

	for f in gd_files:
		var text := FileAccess.get_file_as_string(f)
		identifiers[f] = _identifier_set(text)
		if _is_test_path(f, test_dir):
			continue  # test-only funcs are called by name from the runner
		for m in decl_re.search_all(text):
			var fn := m.get_string(1)
			if not declared_in.has(fn):
				declared_in[fn] = []
			if not declared_in[fn].has(f):
				declared_in[fn].append(f)

	# Names mentioned anywhere in a scene/resource file: signal connections, etc.
	var scene_names := {}
	for s in _scan(scan_root, ["tscn", "tres"]):
		for name in _identifier_set(FileAccess.get_file_as_string(s)):
			scene_names[name] = true

	var names: Array = declared_in.keys()
	names.sort()
	for fn in names:
		var owners: Array = declared_in[fn]
		if owners.size() != 1:
			continue  # duplicated name: an override or interface pattern, not an orphan
		if scene_names.has(fn):
			continue
		var owner: String = owners[0]
		var test_callers := 0
		var live_callers := 0
		for f in gd_files:
			if f == owner:
				continue
			if not identifiers[f].has(fn):
				continue
			if _is_test_path(f, test_dir):
				test_callers += 1
			else:
				live_callers += 1
		if live_callers > 0:
			continue
		var msg := ""
		if test_callers > 0:
			msg = "%s() is referenced only from tests (%d file(s)) - heuristic, may be a callback or called by name" % [fn, test_callers]
		else:
			msg = "%s() has no reference outside its own file - heuristic, may be a callback or called by name" % fn
		out.append({"file": owner, "func": fn, "test_callers": test_callers, "message": msg})
		_add_finding(owner, "orphan_api", fn, "warning", msg, true)
	return out


func _identifier_set(text: String) -> Dictionary:
	var out := {}
	if text == "":
		return out
	if _ident_re == null:
		_ident_re = RegEx.new()
		_ident_re.compile("[A-Za-z_][A-Za-z0-9_]*")
	for m in _ident_re.search_all(text):
		out[m.get_string(0)] = true
	return out


func _is_test_path(p: String, test_dir: String) -> bool:
	var n := _norm_path(p)
	if test_dir != "" and n.begins_with(test_dir):
		return true
	return n.contains("/test/") or n.contains("/tests/") or n.get_file().begins_with("test_")


# --- Shared helpers ---
func _norm_path(p: String) -> String:
	return p.replace("res:///", "res://")


func _extract(line: String, key: String) -> String:
	var m := RegEx.new()
	# \b so that looking for `id=` does not match inside `uid=`.
	m.compile("\\b" + key + "=\"([^\"]+)\"")
	var r := m.search(line)
	return r.get_string(1) if r != null else ""


# Godot 4 writes id="1_abc"; older/hand-edited files may use a bare id=1.
func _extract_id(line: String) -> String:
	var quoted := _extract(line, "id")
	if quoted != "":
		return quoted
	var m := RegEx.new()
	m.compile("\\bid=([^\\s\\]]+)")
	var r := m.search(line)
	return r.get_string(1) if r != null else ""


func _find_all_scenes(root_path: String) -> PackedStringArray:
	var out: PackedStringArray = []
	var d := DirAccess.open(root_path)
	if d == null:
		return out
	d.list_dir_begin()
	var name := d.get_next()
	while name != "":
		var full := _norm_path(d.get_current_dir() + "/" + name)
		if d.current_is_dir():
			if not name.begins_with(".") and not _dir_skipped(full):
				out.append_array(_find_all_scenes(full))
		elif name.ends_with(".tscn") or name.ends_with(".scn"):
			out.append(full)
		name = d.get_next()
	d.list_dir_end()
	return out


# Resolves `rel` as Node.get_node() would, starting AT the node whose own path
# is `base_abs` (SceneState convention: "." for the root, "./Child", "./A/B").
# Returns the target's own path in the same convention, or "" for anything that
# cannot be checked statically (a walk that escapes the scene root, or an
# absolute /root/... runtime path) - the caller skips "" rather than flagging it.
func _resolve_relative_nodepath(base_abs: String, rel: String) -> String:
	if rel.begins_with("/"):
		return ""
	# "Path/To/Node:property" - the node part is what must resolve.
	var colon := rel.find(":")
	if colon >= 0:
		rel = rel.substr(0, colon)
	var parts: PackedStringArray = []
	if base_abs != "." and base_abs != "":
		for part in base_abs.split("/"):
			if part != "." and part != "":
				parts.append(part)
	for part in rel.split("/"):
		if part == "." or part == "":
			continue
		elif part == "..":
			if parts.size() == 0:
				return ""
			parts.remove_at(parts.size() - 1)
		else:
			parts.append(part)
	if parts.size() == 0:
		return "."
	return "./" + "/".join(parts)


# True when `resolved` (an own path, "./A/B") lies strictly INSIDE a node that
# is an instance of another scene. The instance root itself IS declared in this
# scene, so a path landing exactly on it resolves normally through path_set;
# only its interior is invisible to a static pass.
func _crosses_instance(resolved: String, instance_roots: Dictionary) -> bool:
	for root in instance_roots:
		var r := String(root)
		if r == ".":
			# The scene root is itself an instance (an inherited scene): nothing
			# the base scene declares is visible here, so no path is checkable.
			return true
		if resolved.begins_with(r + "/"):
			return true
	return false


func _path_set_has_relaxed(path_set: Dictionary, path: String) -> bool:
	if path_set.has(path):
		return true
	if path.begins_with("./"):
		var alt := path.substr(2)
		if path_set.has(alt):
			return true
	else:
		var alt2 := "./" + path
		if path_set.has(alt2):
			return true
	return false


func _is_nodepath_like_property(name: String, value: Variant) -> bool:
	if typeof(value) == TYPE_NODE_PATH:
		return true
	if typeof(value) == TYPE_STRING:
		var lname := name.to_lower()
		if lname.ends_with("_path") or lname.ends_with("path"):
			return true
	return false
