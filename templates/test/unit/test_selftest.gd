extends RefCounted

## THIS PROJECT'S SELFTEST — add to it, don't start a new one beside it.
##
## When you verify a change, the checks you write belong here. Not in a scratch
## script, not in the chat transcript. A check left here is re-run by /verify on
## every subsequent change and inherited by the next session; the same check
## written into a throwaway is worth one run and then gone. `run_tests.gd` prints
## `Suite: N test script(s)` so a fresh session can see how much prior checking
## it is standing on.
##
## Split into more `test_*.gd` files under this directory once a single file gets
## unwieldy — the runner discovers all of them. The two sanity tests below exist
## so a fresh install has something that passes; delete them once real checks
## replace them, but keep the file.
##
## The headless runner (tools/run_tests.gd) auto-discovers every test_*.gd file
## under the configured test_dir, instantiates it, calls setup() before each
## test (and teardown() after, if present), and runs every method whose name
## begins with "test_". It injects the runner as `_T`, giving you the assertion
## helpers below. A test method returns "" on success, or a non-empty failure
## message (produced by the _T.assert_* helpers) on failure.
##
## Available assertions (all return "" on pass, a message on fail):
##   _T.assert_eq(actual, expected, context)
##   _T.assert_true(condition, context)
##   _T.assert_false(condition, context)
##   _T.assert_gt(actual, threshold, context)
##   _T.assert_gte(actual, threshold, context)
##   _T.assert_float_eq(actual, expected, tolerance, context)
##
## Headless UI helpers (see test_ui_layout_resolves_headlessly below):
##   await _T.instantiate_ui(scene, viewport_size)  -> Node, layout resolved
##   _T.free_ui(node)                               -> tear-down, no orphans
##
## Test methods may be coroutines: if a method awaits, the runner awaits it too.
## Methods that don't await are unaffected.
##
## What does NOT belong here: anything needing a live, playing game — real input,
## physics over time, a scene mid-transition. Those are the bridge's job
## (`python tools/devtools.py`), driven by /verify Phase 4.

var _T  # assertion helper injected by run_tests.gd

func setup() -> void:
	# Runs before each test method. Build fresh fixtures here.
	pass

func test_arithmetic_sanity() -> String:
	return _T.assert_eq(2 + 2, 4, "basic arithmetic")

func test_boolean_sanity() -> String:
	return _T.assert_true(true, "true should be true")

func test_ui_layout_resolves_headlessly() -> String:
	# Without _T.instantiate_ui() every assertion below would read (0, 0): a
	# Control that never enters a tree has no viewport to anchor against.
	var ui: Control = await _T.instantiate_ui(_build_hud_scene(), Vector2i(640, 360))
	if ui == null:
		return "instantiate_ui returned null"

	var err: String = _T.assert_eq(ui.size, Vector2(640, 360), "root fills the viewport")
	if err == "":
		var bar: Control = ui.get_node("Bar")
		err = _T.assert_eq(bar.size, Vector2(640, 48), "bar spans the bottom edge")
		if err == "":
			err = _T.assert_eq(bar.get_global_rect().end.y, 360.0, "bar is flush with the bottom")

	_T.free_ui(ui)
	return err

# Not named test_*, so the runner ignores it. Builds a two-node HUD in code so
# the example needs no .tscn file on disk.
func _build_hud_scene() -> PackedScene:
	var root := Control.new()
	root.name = "Root"
	root.anchor_right = 1.0
	root.anchor_bottom = 1.0

	var bar := ColorRect.new()
	bar.name = "Bar"
	bar.anchor_top = 1.0
	bar.anchor_right = 1.0
	bar.anchor_bottom = 1.0
	bar.offset_top = -48.0
	root.add_child(bar)
	bar.owner = root

	var scene := PackedScene.new()
	scene.pack(root)
	root.free()
	return scene
