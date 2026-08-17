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
##   - autoloads, by node path. --script mode DOES instantiate them and DOES
##     parent them to root; what it has not done by the time _initialize() runs
##     is step the tree, so _ready() has not fired and an autoload that builds
##     its state there still looks empty. This runner awaits one frame first, so
##     `get_autoload("PlayerManager").level` reads what the game would read.
##     (The autoload's bare NAME is still not an identifier here - GDScript only
##     binds those when the engine loads a scene - hence the accessor.)
##
## Live world state is still not in scope: this opens the project, it does not
## play the game, so anything that depends on a running scene belongs on the
## DevTools bridge instead (get-state / run-method).
##
## Exit codes: 0 result printed | 1 parse or execute failure | 2 no --expr.

# harness-version: 0.43.0
const HARNESS_VERSION: String = "0.43.0"


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

	# One frame so autoloads finish entering the tree and _ready() runs. They are
	# already parented to root at this point, but the tree has not been stepped,
	# so an autoload that builds its state in _ready() would answer empty - the
	# same trap run_tests.gd hit (findmyballs:G-002). Cheap, and it makes
	# get_autoload() below worth having.
	await process_frame

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
	# Base instance is this SceneTree, which is what makes get_autoload() below
	# callable from an expression (Expression resolves a bare call against its
	# base). Previously null, so nothing was callable at all.
	var result: Variant = e.execute(values, self, true)
	if e.has_execute_failed():
		print("EXECUTE ERROR: %s" % e.get_error_text())
		quit(1)
		return
	print(str(result))
	quit(0)


## Reaches an autoload by name, for use inside an evaluated expression:
##   eval.gd -- --expr 'get_autoload("BallCatalog").all_balls().size()'
##
## Autoloads are children of root in --script mode, but GDScript only binds
## their bare names as identifiers when the engine loads a scene - which this
## mode does not do. So the node is reachable and the name is not, and an
## accessor closes the gap. _initialize() awaits a frame before any of this, so
## whatever _ready() built is already there.
func get_autoload(autoload_name: String) -> Node:
	return root.get_node_or_null(NodePath(str(autoload_name)))
