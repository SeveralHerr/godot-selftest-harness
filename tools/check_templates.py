#!/usr/bin/env python3
"""Validate the shipped templates before they ship (H-005, H-013, H-001).

Nothing else in this repo runs the templates: a syntax error in dev_tools.gd
reaches a user's game before anything notices, and 0.4.0 shipped three wire
mismatches at once because each bridge half was tested against a fake of the
other. This script is the release gate:

  stage 1  static    py_compile every .py, json.load every .json        (no Godot)
  stage 2  assemble  build a scratch Godot project from templates/      (no Godot)
  stage 2.5 names    run name_check.py on the scratch project BEFORE
                     --import, with and without an engine index, and
                     plant a known-bad file to prove it still says no
  stage 3  parse     `godot --check-only` every template .gd
  stage 4  runners   lint_project.gd + run_tests.gd on the scratch
                     project, both must exit 0
  stage 5  bridge    launch the scratch game headless and drive verbs
                     over the real file bus with the real devtools.py
  stage 6  contract  (--full) exercise EVERY generic verb once and
                     assert the reply envelope; new verbs MUST add a row

The scratch project gets a unique `custom_user_dir_name`, so its bus can never
collide with a real game on the same machine (a concurrent session driving
another project is the normal condition here, not the exception).

Usage:
    python tools/check_templates.py             # stages 1-5
    python tools/check_templates.py --full      # + the every-verb contract table
    python tools/check_templates.py --godot PATH
    python tools/check_templates.py --static-only

Exit codes: 0 all run stages passed; 1 a stage failed; 2 could not run at all.
"""

import argparse
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"

# Template files that are content for humans, not part of the runnable project.
SKIP_COPY = {"CLAUDE.harness.md", "log-devtools.md"}

GODOT_TIMEOUT = 180          # seconds, per subprocess call
BOOT_TIMEOUT = 30            # seconds to wait for the bridge to answer ping


def fail(msg):
    print("FAIL: %s" % msg)
    return False


# --------------------------------------------------------------------------
# Godot resolution


def resolve_godot(explicit):
    """$GODOT_BIN -> --godot flag -> well-known Windows/macOS/PATH locations."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("GODOT_BIN")
    if env:
        candidates.append(Path(env))
    home = Path.home()
    for pattern, base in (
        ("Godot_v*_win64.exe", home / "Documents"),
        ("Godot*.exe", Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Godot"),
        ("Godot*.exe", Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Godot"),
    ):
        if base and base.is_dir():
            candidates.extend(sorted(base.glob(pattern), reverse=True))
    mac = Path("/Applications/Godot.app/Contents/MacOS/Godot")
    if mac.exists():
        candidates.append(mac)
    which = shutil.which("godot")
    if which:
        candidates.append(Path(which))
    for c in candidates:
        if c and c.is_file():
            return c
    return None


def run_godot(godot, project, extra, timeout=GODOT_TIMEOUT):
    cmd = [str(godot), "--headless", "--path", str(project)] + extra
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------
# Stage 1: static


def stage_static():
    ok = True
    py_files = sorted(list(TEMPLATES.rglob("*.py")) + list((REPO_ROOT / "tools").glob("*.py")))
    for p in py_files:
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as exc:
            ok = fail("py_compile %s: %s" % (p.relative_to(REPO_ROOT), exc))
    json_files = sorted(list(TEMPLATES.rglob("*.json"))
                        + [REPO_ROOT / ".claude-plugin" / "plugin.json",
                           REPO_ROOT / ".claude-plugin" / "marketplace.json",
                           REPO_ROOT / "harness_history.json"])
    for p in json_files:
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            ok = fail("json %s: %s" % (p.relative_to(REPO_ROOT), exc))
    print("stage 1 static: %d .py compiled, %d .json parsed%s"
          % (len(py_files), len(json_files), "" if ok else " (WITH FAILURES)"))
    return ok


# --------------------------------------------------------------------------
# Stage 2: scratch project


def stage_assemble(scratch, user_dir_name):
    for src in TEMPLATES.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(TEMPLATES)
        if rel.name in SKIP_COPY:
            continue
        dst = scratch / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    # Fixture script for the contract table: a typed-argument method (run_method
    # coercion rows) and a painted TileMapLayer child named "Cells" (tilemap_cells /
    # tilemap_region rows). Lives under res://tools/ so the default uid_check_ignore
    # exempts it from the missing-.uid-sidecar lint.
    fixture = scratch / "tools" / "harness_check_fixture.gd"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text("\n".join([
        "extends Node2D",
        "",
        "## Harness-check fixture (written by check_templates.py stage_assemble).",
        "",
        "var presses: int = 0",
        "",
        "",
        "func _ready() -> void:",
        "\tvar layer := TileMapLayer.new()",
        '\tlayer.name = "Cells"',
        "\tvar tile_set := TileSet.new()",
        "\ttile_set.tile_size = Vector2i(16, 16)",
        "\tvar source := TileSetAtlasSource.new()",
        "\tvar image := Image.create_empty(32, 16, false, Image.FORMAT_RGBA8)",
        "\tsource.texture = ImageTexture.create_from_image(image)",
        "\tsource.texture_region_size = Vector2i(16, 16)",
        "\tsource.create_tile(Vector2i(0, 0))",
        "\tsource.create_tile(Vector2i(1, 0))",
        "\tvar source_id := tile_set.add_source(source)",
        "\tlayer.tile_set = tile_set",
        "\t# Atlas (0,0): two components -- {(0,0),(1,0)} and {(5,5)}. Atlas (1,0): one cell.",
        "\tlayer.set_cell(Vector2i(0, 0), source_id, Vector2i(0, 0))",
        "\tlayer.set_cell(Vector2i(1, 0), source_id, Vector2i(0, 0))",
        "\tlayer.set_cell(Vector2i(5, 5), source_id, Vector2i(0, 0))",
        "\tlayer.set_cell(Vector2i(3, 3), source_id, Vector2i(1, 0))",
        "\tadd_child(layer)",
        "\t# A real BaseButton for the `press` row, and a Sprite2D for the",
        "\t# non-Control node_bounds row.",
        "\tvar button := Button.new()",
        '\tbutton.name = "Go"',
        '\tbutton.text = "Go"',
        "\tbutton.position = Vector2(32, 32)",
        "\tbutton.size = Vector2(160, 56)",
        "\tbutton.pressed.connect(_on_go)",
        "\tadd_child(button)",
        "\tvar sprite := Sprite2D.new()",
        '\tsprite.name = "Blip"',
        "\tvar sprite_image := Image.create_empty(8, 8, false, Image.FORMAT_RGBA8)",
        "\tsprite.texture = ImageTexture.create_from_image(sprite_image)",
        "\tadd_child(sprite)",
        "",
        "",
        "func _on_go() -> void:",
        "\tpresses += 1",
        "",
        "",
        "## A pure ramp for the `curve` rows: ramp(1..5) is 2,4,6,8,10 (sum 30).",
        "func ramp(day: int) -> int:",
        "\treturn day * 2",
        "",
        "",
        "func take_vec(v: Vector2) -> String:",
        '\treturn "%.1f,%.1f" % [v.x, v.y]',
        "",
        "",
        "## A `-> void`: its null return must be distinguishable from an abort.",
        "func returns_nothing() -> void:",
        "\tpass",
        "",
    ]), encoding="utf-8")

    (scratch / "main.tscn").write_text(
        "\n".join([
            "[gd_scene load_steps=2 format=3]",
            "",
            '[ext_resource type="Script" path="res://tools/harness_check_fixture.gd" id="1_fixture"]',
            "",
            '[node name="Main" type="Node2D"]',
            'script = ExtResource("1_fixture")',
            "",
        ]), encoding="utf-8")
    (scratch / "project.godot").write_text(
        "\n".join([
            "config_version=5",
            "",
            "[application]",
            "",
            'config/name="harness_check"',
            'run/main_scene="res://main.tscn"',
            'config/features=PackedStringArray("4.3")',
            "config/use_custom_user_dir=true",
            'config/custom_user_dir_name="%s"' % user_dir_name,
            "",
            "[autoload]",
            "",
            'DevTools="*res://addons/godot_selftest/dev_tools.gd"',
            "",
        ]), encoding="utf-8")
    print("stage 2 assemble: scratch project at %s" % scratch)
    return True


# --------------------------------------------------------------------------
# Stage 3: parse-check


def stage_parse(godot, scratch):
    ok = True
    scripts = sorted(scratch.rglob("*.gd"))
    for p in scripts:
        rel = "res://" + p.relative_to(scratch).as_posix()
        proc = run_godot(godot, scratch, ["--check-only", "--script", rel])
        if proc.returncode != 0:
            ok = fail("parse %s (exit %d)\n%s" % (rel, proc.returncode,
                                                  (proc.stderr or proc.stdout).strip()))
    print("stage 3 parse: %d scripts checked%s"
          % (len(scripts), "" if ok else " (WITH FAILURES)"))
    return ok


# --------------------------------------------------------------------------
# Stage 2.5: the static name checker
#
# Runs before --import on purpose. name_check.py's whole claim is that it needs no
# .godot/, and the only way to check that claim is to run it on a project that has
# never had one. It also gets a private GODOT_SELFTEST_CACHE so the developer's real
# cached index cannot make a broken --refresh-api look like it worked.


def _run_name_check(scratch, cache, args, godot=None):
    cmd = [sys.executable, str(scratch / "tools" / "name_check.py"),
           "-p", str(scratch)] + args
    if godot:
        cmd += ["--godot", str(godot)]
    env = dict(os.environ, GODOT_SELFTEST_CACHE=str(cache))
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)


def stage_names(scratch, godot, cache):
    ok = True

    # (a) No index at all: the engine half must report as SKIPPED, not silently pass.
    proc = _run_name_check(scratch, cache, [])
    if proc.returncode != 0:
        ok = fail("stage 2.5 names: clean scratch with no index exited %d\n%s\n%s"
                  % (proc.returncode, proc.stdout.strip(), proc.stderr.strip()))
    elif "engine index: NONE" not in proc.stdout or "SKIPPED:" not in proc.stdout:
        ok = fail("stage 2.5 names: with no cached index the run must say the engine "
                  "checks were SKIPPED. Got:\n%s" % proc.stdout.strip())
    else:
        print("stage 2.5 names: no index -> exit 0, engine checks reported SKIPPED")

    # (b) --require-api must turn that same state into a hard 2, not a quiet pass.
    proc = _run_name_check(scratch, cache, ["--require-api"])
    if proc.returncode != 2:
        ok = fail("stage 2.5 names: --require-api with no index exited %d, expected 2"
                  % proc.returncode)

    # (c) Dump the index. This must not create a .godot/ anywhere.
    proc = _run_name_check(scratch, cache, ["--refresh-api"], godot)
    if proc.returncode != 0:
        ok = fail("stage 2.5 names: --refresh-api exited %d\n%s\n%s"
                  % (proc.returncode, proc.stdout.strip(), proc.stderr.strip()))
        return ok
    if (scratch / ".godot").exists():
        ok = fail("stage 2.5 names: --refresh-api created %s. The tool's entire premise "
                  "is that it never touches the import cache." % (scratch / ".godot"))
    index_files = list(Path(cache).glob("engine_api_*.json.gz"))
    if not index_files:
        ok = fail("stage 2.5 names: --refresh-api exited 0 but wrote no index into %s"
                  % cache)
    else:
        print("stage 2.5 names: --refresh-api -> %s (%d KB), no .godot/ created"
              % (index_files[0].name, index_files[0].stat().st_size // 1024))

    # (d) With the index, the shipped templates must be name-clean. A finding here is a
    # real defect in a template, not a false positive to explain away.
    proc = _run_name_check(scratch, cache, [])
    if proc.returncode != 0:
        ok = fail("stage 2.5 names: the shipped templates do not resolve cleanly "
                  "(exit %d)\n%s" % (proc.returncode, proc.stdout.strip()))
    else:
        print("stage 2.5 names: templates resolve clean against the engine index")

    # (e) Positive control. A checker that reports clean on everything is
    # indistinguishable from one that is not running at all, and stage 4 of the 0.4.0
    # release was green for exactly that reason.
    planted = scratch / "tools" / "harness_check_badnames.gd"
    planted.write_text("\n".join([
        "extends Node",
        "class_name HarnessCheckBadNames",
        "",
        "const Gone = preload(\"res://tools/definitely_not_here.gd\")",
        "",
        "var typo: Vecor2 = Vector2.ZERO",
        "",
        "func probe() -> void:",
        "\tprint(Node.NOTIFICATION_NOT_A_THING)",
        "",
    ]), encoding="utf-8")
    try:
        proc = _run_name_check(scratch, cache, ["--json"])
        if proc.returncode != 1:
            ok = fail("stage 2.5 names: planted bad names exited %d, expected 1\n%s"
                      % (proc.returncode, proc.stdout.strip()))
        else:
            try:
                rules = {f["rule"] for f in json.loads(proc.stdout)["findings"]}
            except (ValueError, KeyError) as exc:
                rules = set()
                ok = fail("stage 2.5 names: --json output not parseable: %s" % exc)
            expected = {"missing_preload", "unknown_type", "unknown_member"}
            if not expected <= rules:
                ok = fail("stage 2.5 names: planted file should trigger %s, got %s"
                          % (sorted(expected), sorted(rules)))
            else:
                print("stage 2.5 names: planted bad names -> exit 1, rules %s"
                      % sorted(expected))
    finally:
        planted.unlink(missing_ok=True)

    return ok


# --------------------------------------------------------------------------
# Stage 4: headless runners


def stage_runners(godot, scratch):
    ok = True
    for script, name in (("res://tools/lint_project.gd", "lint"),
                         ("res://tools/run_tests.gd", "tests")):
        proc = run_godot(godot, scratch, ["--script", script])
        if proc.returncode != 0:
            ok = fail("%s exited %d\n--- stdout ---\n%s\n--- stderr ---\n%s"
                      % (name, proc.returncode, proc.stdout.strip(), proc.stderr.strip()))
        else:
            tail = [l for l in proc.stdout.strip().splitlines() if l.strip()]
            print("stage 4 %s: exit 0 (%s)" % (name, tail[-1] if tail else "no output"))
    return ok


# --------------------------------------------------------------------------
# Stage 5/6: the live bridge


def load_client(scratch):
    """Import the *installed* devtools.py from the scratch project."""
    spec = importlib.util.spec_from_file_location(
        "scratch_devtools", scratch / "tools" / "devtools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wait_for_ping(client, scratch):
    deadline = time.time() + BOOT_TIMEOUT
    last = None
    while time.time() < deadline:
        try:
            reply = client.send_command(scratch, "ping", {}, timeout=5.0)
            if isinstance(reply, dict):
                return reply
        except Exception as exc:  # BridgeError while booting
            last = exc
        time.sleep(0.5)
    raise RuntimeError("bridge never answered ping within %ds: %s" % (BOOT_TIMEOUT, last))


def contract_rows():
    """(action, args, must_succeed, note[, required_data_keys]).

    Every generic verb appears once. must_succeed=False rows still assert the
    reply ENVELOPE (id echo, success bool, message str, data dict) - they are
    verbs whose success depends on a display or on prior state we do not
    guarantee headlessly.

    The optional 5th element names data keys the CLIENT reads by name. This is
    the check that would have caught 0.4.0's three simultaneous wire mismatches:
    an envelope can be perfectly well-formed while the key the client prints
    from has been renamed out from under it. Add the keys whenever you add a
    `data.get("...")` to devtools.py.

    The optional 6th element is a {data_key: expected_value} dict, asserted
    exactly. Use it where a row is claiming an EFFECT rather than a shape - a
    note saying "this proves the button's callable ran" proves nothing unless
    the count is actually read back.
    """
    return [
        ("harness_version", {}, True, "", ["harness_version", "handlers"]),
        ("list_commands", {}, True, ""),
        ("scene_tree", {"depth": 5}, True, ""),
        ("validate_scene", {"path": "res://main.tscn"}, True, ""),
        ("validate_all", {}, True, "60s budget"),
        ("get_state", {"node_path": "/root/Main", "properties": ["visible"]}, True, ""),
        ("set_state", {"node_path": "/root/Main", "property": "visible", "value": False}, True, ""),
        ("run_method", {"node_path": "/root/Main", "method": "is_visible", "args": []}, True, "",
         ["result", "returned_null", "declared_return", "node_path", "method"]),
        ("set_state", {"node_path": "/root/Main", "property": "visible", "value": True}, True, "",
         ["property", "value", "read_back", "coerced"]),
        ("run_method", {"node_path": "/root/Main", "method": "take_vec", "args": [[3, 4]]}, True,
         "G-016: [x, y] JSON arg coerced to the declared Vector2 param"),
        ("run_method", {"node_path": "/root/Main", "method": "take_vec", "args": ["nope"]}, False,
         "impossible coercion: must fail loudly, never call with a wrong arg"),
        ("run_method", {"node_path": "Main", "method": "get_class", "args": []}, True,
         "G-010: bare path retried under /root"),
        ("set_state", {"node_path": "/root/Main", "property": "position", "value": [8, 6]}, True,
         "G-035: [x, y] coerced to the property's Vector2, then read back"),
        ("set_state", {"node_path": "/root/Main", "property": "position", "value": "12,7"}, True,
         "gather:G-137: the bare 'x,y' string form the CLI can always pass",
         ["read_back"], {"read_back": {"x": 12.0, "y": 7.0}}),
        ("set_state", {"node_path": "/root/Main", "property": "position", "value": "(4, 2)"}, True,
         "gather:G-137: parenthesised tuple form",
         ["read_back"], {"read_back": {"x": 4.0, "y": 2.0}}),
        ("run_method", {"node_path": "/root/Main", "method": "take_vec", "args": ["3,4"]}, True,
         "gather:G-137: the same string tuple reaches a typed method parameter",
         ["result"], {"result": "3.0,4.0"}),
        ("set_state", {"node_path": "/root/Main", "property": "position", "value": {"x": 0, "y": 0}}, True,
         "restore; dict vector form"),
        ("run_method", {"node_path": "/root/Main", "method": "returns_nothing", "args": []}, True,
         "gather:G-096: a -> void call must be distinguishable from an abort",
         ["returned_null", "declared_return"], {"returned_null": True, "declared_return": "Nil"}),
        ("performance", {"reset_baseline": True}, True, ""),
        ("input_key", {"key": "E"}, True, "G-049: raw InputEventKey by keycode name"),
        ("input_state", {}, True, "G-021: polled pressed/strength, all project actions"),
        ("input_state", {"actions": ["ui_accept"]}, True, ""),
        ("step_time", {"seconds": 0.1, "hold": "ui_accept"}, True,
         "G-084: action held across the step, released at the end"),
        ("tilemap_cells", {"node_path": "/root/Main/Cells"}, True, "G-032: cells as data"),
        ("tilemap_cells", {"node_path": "/root/Main/Cells", "rect": [0, 0, 3, 3]}, True,
         "rect clip in cell coordinates"),
        ("tilemap_region", {"node_path": "/root/Main/Cells", "atlas": [0, 0]}, True,
         "G-065: flood-filled components of one atlas coord"),
        ("scripts_seen", {}, True, "G-074b: script census since launch", ["scripts"]),
        ("ping", {}, True, "gather:G-115: the bus dir and user:// are reported separately",
         ["session", "pid", "bus_dir", "user_dir"]),
        ("canvas_scale", {"node_path": "/root/Main"}, True,
         "G-073/G-075: accumulated scale + effective filter"),
        ("canvas_scale", {"node_path": "/root"}, False,
         "root Window is not a CanvasItem; envelope only"),
        ("set_resolution", {"width": 1280, "height": 720}, False,
         "G-017: headless may clamp/ignore; envelope only, read-back is honest"),
        ("set_feature", {"query": True}, True, "G-033: read the flags without writing"),
        ("clear_nodes", {"group": "harness_check_no_such_group"}, True, "empty selector match"),
        ("clear_nodes", {"class": "harness_check_no_such_class", "via_method": "die"}, True,
         "gather:G-123: removal through the game's own path, not queue_free()",
         ["count", "via", "skipped"]),
        ("find_nodes", {"class": "TileMapLayer"}, True,
         "gather:G-109: identify a node by predicate instead of one probe per child",
         ["nodes", "count", "truncated"]),
        ("find_nodes", {"class": "Button", "properties": ["text"], "where": {"text": "Go"}}, True,
         "property predicate + reported property",
         ["nodes", "count"]),
        ("press", {"node_path": "/root/Main/Go"}, True,
         "gather:G-119: emit `pressed` on a real BaseButton",
         ["node_path", "type", "disabled", "button_pressed"]),
        ("run_method", {"node_path": "/root/Main", "method": "get", "args": ["presses"]}, True,
         "the press above actually reached the connected callable",
         ["result"], {"result": 1}),
        ("press", {"node_path": "/root/Main/Cells"}, False,
         "not a button and has no button child: must refuse, not silently no-op"),
        ("raycast", {"from": [0, 0], "to": [64, 64]}, True,
         "gather:G-136: what a collision mask would actually hit",
         ["clear", "mask", "mask_names"]),
        ("raycast", {"from": [0, 0]}, False, "missing `to`: envelope only"),
        ("get_node_bounds", {"node_path": "/root/Main/Blip"}, True,
         "gather:G-120: a screen rect for a Sprite2D, not just a Control",
         ["global_rect", "size_source"]),
        ("get_state", {"node_path": "/root/Main/Blip",
                       "properties": ["texture.resource_local_to_scene"]}, True,
         "gather:G-110/G-117: --property walks one hop into a Resource"),
        ("canvas_scale", {"node_path": "/root/Main/Blip"}, True,
         "gather:G-105: which canvas a node renders into",
         ["canvas_layer", "canvas_layer_path"]),
        ("sample_pixels", {"rect": [0, 0, 8, 8]}, False,
         "gather:G-121: headless has no framebuffer; envelope only"),
        ("curve", {"node_path": "/root/Main", "method": "ramp", "from": 1, "to": 5}, True,
         "gather:G-127: a ramp read as data instead of evaluated by hand",
         ["points", "min", "max", "sum"], {"min": 2, "max": 10, "sum": 30.0}),
        ("curve", {"node_path": "/root/Main", "method": "ramp", "from": 1, "to": 100000}, False,
         "a typo'd range must be refused, not wedge the single-command bus"),
        ("scene_tree", {"root": "/root/Main/Go", "depth": 2, "properties": ["text"]}, True,
         "gather:G-119/G-109: a subtree, with a property reported per node"),
        ("reachable_ui", {}, True,
         "gather:G-129: the fixture's one Button must be found and reachable",
         ["controls", "count", "reachable", "viewport"], {"count": 1, "reachable": 1}),
        ("input_press", {"action": "ui_accept"}, True, ""),
        ("input_release", {"action": "ui_accept"}, True, ""),
        ("input_tap", {"action": "ui_accept"}, True, ""),
        ("input_actions", {"include_builtin": True}, True, ""),
        ("input_clear", {}, True, ""),
        ("input_sequence", {"steps": [{"type": "wait_frames", "frames": 2}]}, True,
         "fire-and-forget; asserts dispatch only"),
        ("touch_press", {"index": 0, "position": [64, 64]}, True, ""),
        ("touch_drag", {"index": 0, "to": [96, 96], "steps": 2}, True, ""),
        ("touch_list", {}, True, ""),
        ("touch_release", {"index": 0}, True, ""),
        ("touch_clear", {}, True, ""),
        ("set_feature", {"touchscreen": True}, True, ""),
        ("set_feature", {"touchscreen": False}, True, ""),
        ("set_game_speed", {"scale": 2.0}, True, ""),
        ("set_game_speed", {"scale": 1.0}, True, ""),
        ("step_time", {"seconds": 0.2}, True, ""),
        ("wait_frames", {"count": 5}, True, ""),
        ("get_node_bounds", {"node_path": "/root/Main"}, False,
         "Node2D has no rect headlessly; envelope only"),
        ("validate_ui", {}, True, ""),
        ("get_ui_snapshot", {}, True, ""),
        ("save_ui_baseline", {}, True, ""),
        ("ui_snapshot_diff", {}, True, "baseline saved by the previous row"),
        ("screenshot", {"filename": "check_templates.png"}, False,
         "headless capture is display-dependent; envelope only"),
    ]


def check_envelope(action, reply):
    problems = []
    if not isinstance(reply, dict):
        return ["reply is not a dict: %r" % (reply,)]
    for key, kind in (("success", bool), ("message", str), ("data", dict)):
        if key not in reply:
            problems.append("missing %r" % key)
        elif not isinstance(reply[key], kind):
            problems.append("%r is %s, expected %s"
                            % (key, type(reply[key]).__name__, kind.__name__))
    if reply.get("action") not in (None, action):
        problems.append("action echoed as %r" % reply.get("action"))
    return problems


def stage_bridge(godot, scratch, full):
    client = load_client(scratch)
    out_log = scratch / "game_stdout.log"
    err_log = scratch / "game_stderr.log"
    proc = subprocess.Popen(
        [str(godot), "--headless", "--path", str(scratch)],
        stdout=out_log.open("w", encoding="utf-8"),
        stderr=err_log.open("w", encoding="utf-8"))
    ok = True
    try:
        time.sleep(1.0)
        if proc.poll() is not None:
            raise RuntimeError(
                "game exited immediately (code %s)\n--- stderr ---\n%s"
                % (proc.returncode, err_log.read_text(encoding="utf-8")[-2000:]))
        ping = wait_for_ping(client, scratch)
        print("stage 5 bridge: ping answered (%s)" % ping.get("message", ""))

        # Smoke: one verb end-to-end beyond ping (H-013's minimum).
        tree = client.send_command(scratch, "scene_tree", {"depth": 3}, timeout=10.0)
        if not tree.get("success"):
            ok = fail("scene_tree over the live bus: %s" % tree.get("message"))
        listing = client.send_command(scratch, "list_commands", {}, timeout=10.0)
        if not listing.get("success"):
            ok = fail("list_commands over the live bus: %s" % listing.get("message"))

        if full:
            passed = 0
            for row in contract_rows():
                action, args, must_succeed, note = row[:4]
                required = row[4] if len(row) > 4 else []
                expect = row[5] if len(row) > 5 else {}
                timeout = 90.0 if action == "validate_all" else 15.0
                try:
                    reply = client.send_command(scratch, action, args, timeout=timeout)
                except Exception as exc:
                    ok = fail("contract %s: no reply (%s)" % (action, exc))
                    continue
                problems = check_envelope(action, reply)
                if must_succeed and not reply.get("success"):
                    problems.append("success=false: %s" % reply.get("message"))
                if must_succeed and required:
                    data = reply.get("data") or {}
                    absent = [k for k in required if k not in data]
                    if absent:
                        problems.append(
                            "data is missing key(s) the client reads by name: %s "
                            "(present: %s)" % (", ".join(absent), ", ".join(sorted(data))))
                if expect:
                    data = reply.get("data") or {}
                    for key, want in expect.items():
                        if data.get(key) != want:
                            problems.append("data[%r] is %r, expected %r"
                                            % (key, data.get(key), want))
                if problems:
                    ok = fail("contract %s: %s" % (action, "; ".join(problems)))
                else:
                    passed += 1
            print("stage 6 contract: %d/%d rows passed"
                  % (passed, len(contract_rows())))

        quit_reply = client.send_command(scratch, "quit", {"exit_code": 0}, timeout=10.0)
        if not quit_reply.get("success"):
            ok = fail("quit: %s" % quit_reply.get("message"))
        proc.wait(timeout=15)
    except Exception as exc:
        ok = fail("bridge stage: %s" % exc)
        for label, log in (("stdout", out_log), ("stderr", err_log)):
            try:
                text = log.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if text:
                print("--- game %s (tail) ---\n%s" % (label, text[-1500:]))
        print("game process: %s"
              % ("still alive" if proc.poll() is None else "exited %s" % proc.returncode))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    return ok


# --------------------------------------------------------------------------


def user_data_dir(name):
    """Where Godot puts user:// for a custom_user_dir_name project."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    return Path.home() / ".local" / "share" / name


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", action="store_true",
                    help="run the every-verb contract table (stage 6)")
    ap.add_argument("--godot", help="Godot binary (else $GODOT_BIN, then well-known paths)")
    ap.add_argument("--static-only", action="store_true",
                    help="run only stage 1 (no Godot needed)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch project and user dir for inspection")
    args = ap.parse_args()

    if not stage_static():
        return 1
    if args.static_only:
        return 0

    godot = resolve_godot(args.godot)
    if godot is None:
        print("WARNING: no Godot binary found ($GODOT_BIN, --godot, Documents, "
              "Program Files, PATH). Stages 2-6 SKIPPED - the templates were "
              "NOT executed. This is not a pass.")
        return 2
    print("godot: %s" % godot)

    token = uuid.uuid4().hex[:8]
    user_dir_name = "harness_check_%s" % token
    tmp = tempfile.mkdtemp(prefix="harness-check-")
    scratch = Path(tmp) / "project"
    scratch.mkdir()
    ok = True
    try:
        ok = stage_assemble(scratch, user_dir_name) and ok
        # Before --import: name_check.py claims it needs no .godot/, and the only
        # honest way to check that is to run it on a project that has never had one.
        ok = stage_names(scratch, godot, Path(tmp) / "api-cache") and ok
        # First import builds .godot/ so later runs resolve class caches.
        imp = run_godot(godot, scratch, ["--import"])
        if imp.returncode != 0:
            print("note: --import exited %d (often benign on a bare project)"
                  % imp.returncode)
        ok = stage_parse(godot, scratch) and ok
        ok = stage_runners(godot, scratch) and ok
        ok = stage_bridge(godot, scratch, args.full) and ok
    finally:
        if args.keep:
            print("kept: %s (user dir %s)" % (scratch, user_data_dir(user_dir_name)))
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(user_data_dir(user_dir_name), ignore_errors=True)

    print("check_templates: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
