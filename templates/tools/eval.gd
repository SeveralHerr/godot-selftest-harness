@tool
extends SceneTree

## Headless expression evaluator for the godot_selftest harness (G-054).
## Run: godot --headless --path . --script res://tools/eval.gd -- --expr "1 + 2"
##
## Evaluates one Godot Expression against the project and prints the result.
## In scope:
##   - pure expressions: math, ternaries, built-in types and their methods
##     (Vector2(3, 4).length()), @GlobalScope functions (clamp, deg_to_rad, ...)
##   - global classes (class_name): every identifier in the expression that
##     names a registered class is bound to its loaded script, so constants and
##     STATIC functions resolve - `Balance.xp_for_level(3)`, and
##     `MyClass.new().method()` for instance behavior.
## NOT in scope, honestly: autoloads. --script mode never instantiates them, so
## `PlayerManager.level` cannot be answered here - ask the running game over
## the DevTools bridge instead (get-state / run-method).
##
## Exit codes: 0 result printed | 1 parse or execute failure | 2 no --expr.

# harness-version: 0.10.0
const HARNESS_VERSION: String = "0.10.0"


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var expr_text := ""
	for i in args.size():
		if args[i] == "--expr" and i + 1 < args.size():
			expr_text = args[i + 1]
	if expr_text == "":
		print("usage: godot --headless --path . --script res://tools/eval.gd -- --expr \"<expression>\"")
		print("eval: no --expr given -> exit 2")
		quit(2)
		return

	# Bind only the global classes the expression actually mentions: loading
	# every registered class in a big project is slow, and load() can run a
	# @tool script's static initializers.
	var names: PackedStringArray = []
	var values: Array = []
	var ident := RegEx.new()
	ident.compile("[A-Za-z_][A-Za-z0-9_]*")
	var mentioned := {}
	for m in ident.search_all(expr_text):
		mentioned[m.get_string(0)] = true
	for entry in ProjectSettings.get_global_class_list():
		var cls := String(entry.get("class", ""))
		if cls != "" and mentioned.has(cls):
			var s: Resource = load(String(entry.get("path", "")))
			if s != null:
				names.append(cls)
				values.append(s)

	var e := Expression.new()
	if e.parse(expr_text, names) != OK:
		print("PARSE ERROR: %s" % e.get_error_text())
		quit(1)
		return
	var result: Variant = e.execute(values, null, true)
	if e.has_execute_failed():
		print("EXECUTE ERROR: %s" % e.get_error_text())
		quit(1)
		return
	print(str(result))
	quit(0)
