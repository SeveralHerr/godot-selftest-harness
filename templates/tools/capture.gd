@tool
extends SceneTree

## Standalone scene screenshotter for the godot_selftest harness.
## Run: godot --path . --script res://tools/capture.gd -- --scene res://ui/hud.tscn --out shot.png
##
## Captures one scene to a PNG without the DevTools bridge and without playing the
## game. The bridge's `screenshot` verb needs a *running* session it can talk to;
## this needs neither, so it works on a project that has never launched, on one
## scene in isolation, and in a fresh worktree.
##
## NOT HEADLESS, and this is the whole sharp edge. Under --headless Godot loads the
## dummy rendering driver, `root.get_texture()` returns null, and a capture written
## from it would be a 0-byte file or a blank image - the "well-formed zeros" failure
## this harness exists to make impossible. So a headless run exits 2 (you captured
## nothing) naming the fix, rather than producing a file that looks like a result.
## A real display is required; on a CI box that means a virtual one (xvfb-run and
## friends).
##
## Flags (after the bare `--`, read via OS.get_cmdline_user_args()):
##   --scene <res://path>  Scene to capture. Default: the project's main scene.
##   --out <path>          Output PNG. res:// and user:// resolve; a bare or
##                         absolute path is written as given. Default:
##                         user://screenshots/capture_<scene>_<timestamp>.png
##   --frames N            Frames to step before capturing (default 3). Two is the
##                         floor for anything Control-shaped: the first frame is
##                         where @onready runs and containers get their size, and a
##                         capture taken before that is a legitimately-rendered
##                         picture of an unfinished layout. Raise it for tweens,
##                         particles, or an animation you want settled.
##   --size WxH            Resize the window before capturing (e.g. --size 1920x1080).
##   --fail-on-uniform     Exit 1 if the capture is a single flat colour. Off by
##                         default because a solid-colour scene is legal; on when a
##                         caller is using the capture as evidence something drew.
##
## Every run prints what it looked at - scene, size, frames stepped, distinct
## colours sampled and the output path - because "capture written" over a blank
## image is the report lying about what it did.
##
## Exit codes:
##   0  captured
##   1  ran but could not produce a capture: scene missing or unloadable, save
##      failed, or --fail-on-uniform on a flat image
##   2  could not run at all: headless (no renderer), or a malformed flag

# harness-version: 0.22.0
const HARNESS_VERSION: String = "0.22.0"

const EXIT_OK: int = 0
const EXIT_FAILED: int = 1
const EXIT_CANNOT_RUN: int = 2

## Grid of sample points for the flat-image check. A capture is evidence that
## something drew; scanning every pixel of a 4K frame to learn that costs far more
## than the answer is worth, and 64x64 spread over the image is plenty to tell a
## rendered scene from a cleared buffer.
const SAMPLE_STEPS: int = 64


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var scene_path := ""
	var out_path := ""
	var frames := 3
	var size := Vector2i.ZERO
	var fail_on_uniform := false

	var i := 0
	while i < args.size():
		match args[i]:
			"--scene":
				if i + 1 >= args.size():
					_abort("--scene needs a res:// path")
					return
				scene_path = args[i + 1]
				i += 1
			"--out":
				if i + 1 >= args.size():
					_abort("--out needs a path")
					return
				out_path = args[i + 1]
				i += 1
			"--frames":
				if i + 1 >= args.size():
					_abort("--frames needs a count")
					return
				if not args[i + 1].is_valid_int():
					_abort("--frames %s is not an integer" % args[i + 1])
					return
				frames = maxi(1, args[i + 1].to_int())
				i += 1
			"--size":
				if i + 1 >= args.size():
					_abort("--size needs WxH, e.g. 1920x1080")
					return
				var parsed := _parse_size(args[i + 1])
				if parsed == Vector2i.ZERO:
					_abort("--size %s is not WxH with positive integers" % args[i + 1])
					return
				size = parsed
				i += 1
			"--fail-on-uniform":
				fail_on_uniform = true
			_:
				_abort("unknown flag %s" % args[i])
				return
		i += 1

	# The guard that matters. Checked before anything else does work, so the message
	# is the first thing printed rather than the last line after a wasted load.
	if DisplayServer.get_name() == "headless":
		_abort("running headless, which has no renderer - root.get_texture() is null "
			+ "and any file written here would be blank. Drop --headless (a real "
			+ "display is required; use a virtual one such as xvfb-run on CI).")
		return

	if scene_path == "":
		scene_path = str(ProjectSettings.get_setting("application/run/main_scene", ""))
		if scene_path == "":
			_abort("no --scene given and the project declares no main scene")
			return

	if not ResourceLoader.exists(scene_path):
		_fail("scene '%s' does not exist" % scene_path)
		return
	var packed: PackedScene = load(scene_path)
	if packed == null:
		_fail("scene '%s' failed to load" % scene_path)
		return
	# A scene whose script does not compile still load()s (the same trap as an
	# unparseable shader), so instantiation is the real test, not the load.
	if not packed.can_instantiate():
		_fail("scene '%s' loaded but cannot be instantiated - check its script parses"
			% scene_path)
		return

	if size != Vector2i.ZERO:
		DisplayServer.window_set_size(size)
		root.size = size

	var instance: Node = packed.instantiate()
	root.add_child(instance)

	for _f in range(frames):
		await process_frame

	var tex := root.get_texture()
	if tex == null:
		_fail("viewport texture is null despite display server '%s' - nothing was "
			% DisplayServer.get_name() + "rendered")
		return
	var img: Image = tex.get_image()
	if img == null or img.is_empty():
		_fail("captured image is empty after %d frame(s)" % frames)
		return

	if out_path == "":
		out_path = "user://screenshots/capture_%s_%s.png" % [
			scene_path.get_file().get_basename(),
			Time.get_datetime_string_from_system().replace(":", "-"),
		]
	var abs_out := ProjectSettings.globalize_path(out_path)
	var dir := abs_out.get_base_dir()
	if dir != "":
		DirAccess.make_dir_recursive_absolute(dir)

	var err := img.save_png(abs_out)
	if err != OK:
		_fail("save_png('%s') failed with error %d" % [abs_out, err])
		return

	var distinct := _distinct_sampled_colours(img)
	print("capture: godot-selftest-harness %s | scene %s" % [HARNESS_VERSION, scene_path])
	print("capture: %dx%d, %d frame(s) stepped, %d distinct colour(s) sampled -> %s"
		% [img.get_width(), img.get_height(), frames, distinct, abs_out])
	if distinct <= 1:
		# Loud, and never silently: a flat capture is exactly what a broken scene, a
		# capture taken too early, and a working solid-colour splash all produce.
		print("WARNING: the capture is a single flat colour. That is legal for a "
			+ "solid scene, and it is also what a scene that drew nothing looks "
			+ "like. Raise --frames, or check the scene actually renders.")
		if fail_on_uniform:
			print("capture: uniform image under --fail-on-uniform -> exit %d" % EXIT_FAILED)
			quit(EXIT_FAILED)
			return
	quit(EXIT_OK)


func _parse_size(text: String) -> Vector2i:
	var parts := text.to_lower().split("x")
	if parts.size() != 2 or not parts[0].is_valid_int() or not parts[1].is_valid_int():
		return Vector2i.ZERO
	var w := parts[0].to_int()
	var h := parts[1].to_int()
	if w <= 0 or h <= 0:
		return Vector2i.ZERO
	return Vector2i(w, h)


## Distinct colours over a sampling grid. Quantised to 8 levels per channel so
## gradient noise and compression dither do not read as "lots of colours" and mask
## an otherwise blank frame.
func _distinct_sampled_colours(img: Image) -> int:
	var w := img.get_width()
	var h := img.get_height()
	var step_x := maxi(1, w / SAMPLE_STEPS)
	var step_y := maxi(1, h / SAMPLE_STEPS)
	var seen := {}
	var y := 0
	while y < h:
		var x := 0
		while x < w:
			var c := img.get_pixel(x, y)
			var key := "%d,%d,%d,%d" % [
				int(c.r * 8.0), int(c.g * 8.0), int(c.b * 8.0), int(c.a * 8.0)
			]
			seen[key] = true
			x += step_x
		y += step_y
	return seen.size()


func _fail(reason: String) -> void:
	print("CAPTURE ERROR: %s" % reason)
	print("capture: no image produced -> exit %d" % EXIT_FAILED)
	quit(EXIT_FAILED)


func _abort(reason: String) -> void:
	print("CAPTURE ERROR: %s" % reason)
	print("capture: could not run -> exit %d" % EXIT_CANNOT_RUN)
	quit(EXIT_CANNOT_RUN)
