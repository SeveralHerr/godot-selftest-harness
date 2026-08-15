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
                     project, both must exit 0; assert the runner's
                     denominators (autoloads ready, assertions executed)
                     and PLANT a vacuous test to prove exit 1
  stage 5  bridge    launch the scratch game headless and drive verbs
                     over the real file bus with the real devtools.py,
                     including the validate_ui baseline round-trip
                     against PLANTED findings and a genuinely PAUSED tree
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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"

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
# Stage 1.5: reach classification (pure Python - no Godot, no project)


def stage_reach():
    """verify_ledger's reach buckets, checked by planting each case.

    Reach is the one number in the ledger anybody acts on, and the way it fails is
    silent: a file that demonstrably ran gets scored a miss, the ratio sags, and
    readers learn to discount the field instead of reporting the bug. That is what
    happened to headless tools - `lint_project.gd` was charged as unreached by the
    very runs that had just executed it - so each bucket here is asserted against a
    planted path rather than trusted.
    """
    sys.path.insert(0, str(TEMPLATES / "tools"))
    try:
        import verify_ledger as VL
    except ImportError as exc:
        return fail("stage 1.5 reach: cannot import verify_ledger (%s)" % exc)

    tmp = Path(tempfile.mkdtemp(prefix="harness-reach-"))
    try:
        # A real file on disk for every candidate: split_reach calls .exists() and
        # would otherwise excuse the whole set as `deleted`, which passes vacuously.
        planted = [
            "player/player.gd",          # game code, never observed -> unreached
            "tools/lint_project.gd",     # headless runner            -> headless_tools
            "tools/generate_art.gd",     # project's own generator    -> headless_tools
            "test/unit/test_thing.gd",   # unit test                  -> test_scripts
            "toolsy/decoy.gd",           # NOT under tools/           -> unreached
            "notes.md",                  # wrong suffix               -> not_applicable
        ]
        for rel in planted:
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# planted\n", encoding="utf-8")
        gone = "world/deleted.gd"  # in the diff, absent from disk -> deleted
        changed = set(planted) | {gone}

        s = VL.split_reach(changed, set(), set(), tmp, {})
        expected = {
            "headless_tools": ["tools/generate_art.gd", "tools/lint_project.gd"],
            "test_scripts": ["test/unit/test_thing.gd"],
            "deleted": [gone],
            "unreached": ["player/player.gd", "toolsy/decoy.gd"],
        }
        for bucket, want in expected.items():
            got = sorted(s.get(bucket) or [])
            if got != sorted(want):
                return fail("stage 1.5 reach: %s was %s, expected %s"
                            % (bucket, got, sorted(want)))
        # `toolsy/` staying unreached is the over-reach control: a prefix match that
        # ignored segment boundaries would excuse it and quietly inflate every ratio.
        if "toolsy/decoy.gd" in (s.get("headless_tools") or []):
            return fail("stage 1.5 reach: toolsy/ was excused as a headless dir - the "
                        "prefix match is not respecting segment boundaries")
        if "notes.md" not in (s.get("not_applicable") or []):
            return fail("stage 1.5 reach: a .md file did not land in not_applicable")
        # Excused, never credited: an excused file must not inflate `reached` either.
        if set(s.get("reached") or []) & set(s.get("headless_tools") or []):
            return fail("stage 1.5 reach: a headless tool was folded into reached")

        # Opting out must be possible, or a project whose tools/ IS game code is stuck.
        off = VL.split_reach(changed, set(), set(), tmp, {"reach_headless_dirs": []})
        if "tools/lint_project.gd" not in (off.get("unreached") or []):
            return fail("stage 1.5 reach: reach_headless_dirs=[] did not put tools/ "
                        "back in unreached - the key is decoration")

        # A row written before headless_tools existed must still read. stats() does
        # .get on the key; a row missing it must not crash or silently change meaning.
        legacy = {k: v for k, v in VL._sub_reach(s).items() if k != "headless_tools"}
        if (legacy.get("headless_tools") or []) != []:
            return fail("stage 1.5 reach: legacy-row simulation is not actually missing "
                        "the key - the backward-compat check proves nothing")
        # moving-in:G-003: "cannot tell" must not look like "nothing to check".
        # `tmp` is a plain directory with no .git, which is the real condition (a
        # project can legitimately have no VCS). Planted from both sides, because a
        # split that returned the unavailable shape for EVERY input would satisfy
        # the first assertion alone.
        unavailable = VL.split_reach(None, set(), set(), tmp, {})
        if not unavailable.get("changed_unavailable"):
            return fail("stage 1.5 reach: split_reach(None) did not set "
                        "changed_unavailable - a checkout with no git repository "
                        "still scores as a real 0/0")
        if unavailable.get("reached") is not None:
            return fail("stage 1.5 reach: split_reach(None) returned reached=%r, "
                        "expected None. An empty list reads as a clean sweep."
                        % (unavailable.get("reached"),))
        if s.get("changed_unavailable"):
            return fail("stage 1.5 reach: a REAL changed set was flagged "
                        "changed_unavailable - the flag is stuck on and the "
                        "unavailable assertion above proves nothing")
        empty = VL.split_reach(set(), set(), set(), tmp, {})
        if empty.get("changed_unavailable"):
            return fail("stage 1.5 reach: a genuine empty changed set (git present, "
                        "nothing changed) was reported as unavailable - the two "
                        "zeros must stay distinguishable")
        line_unavailable = VL._reach_line(VL._sub_reach(unavailable))
        line_empty = VL._reach_line(VL._sub_reach(empty))
        if line_unavailable == line_empty:
            return fail("stage 1.5 reach: the no-VCS line and the real-zero line "
                        "read identically (%r) - a reader cannot tell them apart"
                        % (line_unavailable,))

        print("stage 1.5 reach: buckets correct (%d headless, %d test, %d deleted, "
              "%d unreached), toolsy/ not excused, opt-out works, legacy rows parse, "
              "no-VCS distinct from a real zero"
              % (len(s["headless_tools"]), len(s["test_scripts"]),
                 len(s["deleted"]), len(s["unreached"])))
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        sys.path.remove(str(TEMPLATES / "tools"))


# --------------------------------------------------------------------------
# Stage 2: scratch project


def _coverage_fixture(root, with_ui_check):
    """A minimal scaffolded-looking project, with or without a UI-layout check.

    The docstring in test_math.gd names the exact tokens that prove ui_layout
    coverage, in a comment. A detector that greps raw source instead of blanking
    comments reports COVERED on fixture A, which is the false-COVERED this whole
    tool exists to make impossible.
    """
    (root / "test" / "unit").mkdir(parents=True, exist_ok=True)
    (root / "addons" / "godot_selftest").mkdir(parents=True, exist_ok=True)
    (root / "project.godot").write_text("[application]\n", encoding="utf-8")
    (root / "addons" / "godot_selftest" / "devtools_config.json").write_text(
        json.dumps({"test_dir": "res://test/unit", "scan_root": "res://"}),
        encoding="utf-8")
    (root / "test" / "unit" / "test_math.gd").write_text(
        "extends RefCounted\n"
        "## Trap: this comment names _T.instantiate_ui( and get_global_rect()\n"
        "## and asserts ui.size, and none of it is ever called.\n"
        "var _T\n"
        "func test_a() -> String:\n"
        "\treturn _T.assert_eq(2 + 2, 4, \"math\")\n",
        encoding="utf-8")
    if with_ui_check:
        (root / "test" / "unit" / "test_hud.gd").write_text(
            "extends RefCounted\n"
            "var _T\n"
            "func test_hud() -> String:\n"
            "\tvar ui: Control = await _T.instantiate_ui(_hud(), Vector2i(640, 360))\n"
            "\tvar e: String = _T.assert_eq(ui.size, Vector2(640, 360), \"fills\")\n"
            "\t_T.free_ui(ui)\n"
            "\treturn e\n"
            "func _hud() -> PackedScene:\n"
            "\treturn PackedScene.new()\n",
            encoding="utf-8")


def stage_coverage():
    """coverage_check.py must go quiet only when the project really covers a class.

    Both directions, because only the positive one is what a broken detector
    passes: a tool that reports every class UNCHECKED looks exactly like a
    correct one on fixture A, and a tool that reports every class COVERED looks
    exactly like a correct one on fixture B. Running only one of them proves
    nothing at all ([H-035]).

    Needs no Godot and never opens a project - the tool's own premise - so it
    runs under --static-only.
    """
    tool = TEMPLATES / "tools" / "coverage_check.py"
    if not tool.exists():
        return fail("stage 1.6 coverage: %s is missing" % tool)

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for with_ui, want in ((False, "UNCHECKED"), (True, "COVERED")):
            root = Path(tmp) / ("B" if with_ui else "A")
            _coverage_fixture(root, with_ui)
            proc = subprocess.run(
                [sys.executable, str(tool), "--project", str(root),
                 "--only", "ui_layout", "--json"],
                capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                ok = fail("stage 1.6 coverage: fixture %s exited %d (advisory runs "
                          "must exit 0)\n%s\n%s"
                          % ("B" if with_ui else "A", proc.returncode,
                             proc.stdout[-2000:], proc.stderr[-2000:]))
                continue
            try:
                report = json.loads(proc.stdout)
            except ValueError as exc:
                return fail("stage 1.6 coverage: --json not parseable: %s" % exc)

            cls = [c for c in report.get("classes", []) if c.get("id") == "ui_layout"]
            if len(cls) != 1:
                ok = fail("stage 1.6 coverage: --only ui_layout returned %d class(es)"
                          % len(cls))
                continue
            status = cls[0].get("status", "")
            covered = status.startswith("covered")
            if covered != with_ui:
                ok = fail("stage 1.6 coverage: fixture %s reported ui_layout %r. "
                          "%s" % ("B" if with_ui else "A", status,
                                  "The docstring naming instantiate_ui/get_global_rect "
                                  "flipped it - comments are not coverage."
                                  if not with_ui else
                                  "A real _T.instantiate_ui() call was not detected."))
                continue

            if with_ui:
                # The evidence line is the whole design: a COVERED with no
                # file:line is a verdict nobody can check.
                ev = cls[0].get("evidence") or []
                if not ev or not ev[0].get("location"):
                    ok = fail("stage 1.6 coverage: fixture B reported COVERED with no "
                              "evidence location - a verdict with no file:line is "
                              "exactly the unfalsifiable output this tool exists to "
                              "replace")
                    continue
                print("stage 1.6 coverage: fixture B -> %s, evidence %s (%s)"
                      % (status, ev[0]["location"], ev[0].get("token", "")))
            else:
                print("stage 1.6 coverage: fixture A -> %s, and the docstring trap "
                      "did not flip it" % status)

        # Fixture C: the shipped seed test, alone and unmodified. It really does
        # call _T.instantiate_ui() and assert ui.size - on a two-node HUD it
        # builds in code - so a detector that only greps for the token marks
        # EVERY freshly scaffolded project ui_layout-covered on day one. Two real
        # projects were credited to `test_example.gd:42` before this was fixed.
        seed = TEMPLATES / "test" / "unit" / "test_selftest.gd"
        if not seed.exists():
            ok = fail("stage 1.6 coverage: %s is missing - the seeded-test fixture "
                      "cannot be built" % seed)
        else:
            root = Path(tmp) / "C"
            _coverage_fixture(root, False)
            (root / "test" / "unit" / "test_math.gd").unlink()
            shutil.copy2(seed, root / "test" / "unit" / "test_selftest.gd")
            proc = subprocess.run(
                [sys.executable, str(tool), "--project", str(root),
                 "--only", "ui_layout", "--json"],
                capture_output=True, text=True, timeout=120)
            try:
                report = json.loads(proc.stdout)
            except ValueError as exc:
                return fail("stage 1.6 coverage: fixture C --json not parseable: %s" % exc)
            cls = [c for c in report.get("classes", []) if c.get("id") == "ui_layout"]
            status = cls[0].get("status", "") if cls else "?"
            if status != "unchecked":
                ok = fail("stage 1.6 coverage: the shipped seed test ALONE reported "
                          "ui_layout %r. The harness's own example is not the "
                          "project's coverage - every scaffolded project would read "
                          "as covered on day one." % status)
            elif not (cls[0].get("weak_evidence") or []):
                ok = fail("stage 1.6 coverage: seed-only fixture reported UNCHECKED "
                          "but printed no weak signal, so a reader cannot tell it "
                          "from a project with no UI test at all")
            else:
                print("stage 1.6 coverage: the shipped seed alone -> unchecked, with "
                      "the seeded call named as a weak signal")

        # --strict is the opt-in gate; it must actually gate.
        root = Path(tmp) / "A"
        proc = subprocess.run(
            [sys.executable, str(tool), "--project", str(root),
             "--only", "ui_layout", "--strict"],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 1:
            ok = fail("stage 1.6 coverage: --strict on an UNCHECKED class exited %d, "
                      "expected 1" % proc.returncode)
        else:
            print("stage 1.6 coverage: --strict on an UNCHECKED class -> exit 1")
    return ok


def stage_assemble(scratch, user_dir_name):
    # The project's own files first (project.godot, the fixture scene), then the
    # REAL installer over them. This stage used to copy templates/ by hand, which
    # made it a third definition of "installed" beside the slash command and every
    # benchmark rig (gh#9 / H-047); now `scaffold_install.py full` is the one
    # definition and this check exercises it, autoload wiring included.
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
            "[display]",
            "",
            # Stated rather than left to default, because check_canvas_layer_space()
            # asserts these exact numbers. Headless has no window, so the UI verbs
            # fall back to this designed size -- without the fallback the root
            # viewport is 64x64 and every Control wider than 64px "overflows".
            "window/size/viewport_width=1152",
            "window/size/viewport_height=648",
            "",
        ]), encoding="utf-8")

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
        "\t# Planted UI defects for check_ui_baseline(): a Label resting at alpha 0",
        "\t# (verbatim the finding that stalled findmyballs) and an 8x8 Button. A",
        "\t# baseline round-trip over ZERO findings passes against an implementation",
        "\t# that does nothing at all, so having real findings IS the check.",
        "\tvar ghost := Label.new()",
        '\tghost.name = "Ghost"',
        '\tghost.text = "resting at alpha 0"',
        "\tghost.modulate.a = 0.0",
        "\tadd_child(ghost)",
        "\tvar tiny := Button.new()",
        '\ttiny.name = "Tiny"',
        "\ttiny.size = Vector2(8, 8)",
        "\tadd_child(tiny)",
        "\t# A Theme on the button, so check_set_state_dotted() has a real Resource",
        "\t# sub-property to write through (gh#1).",
        "\tvar theme := Theme.new()",
        "\ttheme.default_font_size = 16",
        "\tbutton.theme = theme",
        "\t# gh#2: a HUD on a CanvasLayer with scale 0.6, which is an ordinary way",
        "\t# to build a resolution-independent UI. 'Inside' sits at x=1200 in LAYER",
        "\t# units -- 720 on a 1152-wide screen, comfortably on it. 'Outside' sits",
        "\t# at x=3000 -- 1800 on screen, genuinely past the right edge. Measured",
        "\t# with get_global_rect() BOTH read as overflowing; measured through the",
        "\t# canvas transform only the second does, and having the second is what",
        "\t# stops a check that reports nothing from looking correct.",
        "\tvar hud_layer := CanvasLayer.new()",
        '\thud_layer.name = "ScaledHud"',
        "\thud_layer.scale = Vector2(0.6, 0.6)",
        "\tadd_child(hud_layer)",
        "\tvar inside := Button.new()",
        '\tinside.name = "ScaledInside"',
        '\tinside.text = "Inside"',
        "\tinside.position = Vector2(1200, 40)",
        "\tinside.size = Vector2(300, 60)",
        "\thud_layer.add_child(inside)",
        "\tvar outside := Button.new()",
        '\toutside.name = "ScaledOutside"',
        '\toutside.text = "Outside"',
        "\toutside.position = Vector2(3000, 40)",
        "\toutside.size = Vector2(300, 60)",
        "\thud_layer.add_child(outside)",
        "\t# moving-in:G-002/G-006: a 3D prop for `aabb`, planted with the exact trap",
        "\t# the verb exists to avoid. The box is 0.2 on a side; the OmniLight3D",
        "\t# beside it has range 5.0, so ITS aabb is a 10-unit cube. A walk that",
        "\t# includes Light3D -- and a naive one does, because Light3D IS a",
        "\t# VisualInstance3D -- measures this prop at 10 units instead of 0.2.",
        "\t# Asserting only that the box is found would pass against a verb that",
        "\t# merges everything, so the light is the planted defect (H-035).",
        "\tvar prop := Node3D.new()",
        '\tprop.name = "Prop3D"',
        "\tadd_child(prop)",
        "\tvar box := MeshInstance3D.new()",
        '\tbox.name = "PropBox"',
        "\tvar box_mesh := BoxMesh.new()",
        "\tbox_mesh.size = Vector3(0.2, 0.2, 0.2)",
        "\tbox.mesh = box_mesh",
        "\tprop.add_child(box)",
        "\tvar lamp := OmniLight3D.new()",
        '\tlamp.name = "PropLamp"',
        "\tlamp.omni_range = 5.0",
        "\tprop.add_child(lamp)",
        "",
        "",
        "## Lets check_paused_bridge() pause the tree over the bus, so the",
        "## PROCESS_MODE_ALWAYS fix is checked by its effect, not by reading the",
        "## property back.",
        "func harness_set_paused(on: bool) -> void:",
        "\tget_tree().paused = on",
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
    # Shader fixtures for the lint shader pass. All three shapes it claims to
    # cover are present so the clean run has a real denominator to report: a
    # plain .gdshader, one that pulls in a .gdshaderinc, and a Shader embedded
    # in a .tres. Without these the pass prints "Shaders: none found" and a
    # broken pass is indistinguishable from a working one.
    shaders = scratch / "shaders"
    shaders.mkdir(parents=True, exist_ok=True)
    (shaders / "plain.gdshader").write_text(
        "shader_type canvas_item;\n"
        "uniform float amount : hint_range(0.0, 1.0) = 0.5;\n"
        "void fragment() {\n"
        "\tCOLOR = vec4(amount, 0.0, 0.0, 1.0);\n"
        "}\n",
        encoding="utf-8")
    (shaders / "lib.gdshaderinc").write_text(
        "float harness_tint() {\n"
        "\treturn 0.75;\n"
        "}\n",
        encoding="utf-8")
    (shaders / "with_include.gdshader").write_text(
        "shader_type spatial;\n"
        '#include "res://shaders/lib.gdshaderinc"\n'
        "void fragment() {\n"
        "\tALBEDO = vec3(harness_tint());\n"
        "}\n",
        encoding="utf-8")
    (shaders / "embedded.tres").write_text(
        "\n".join([
            "[gd_resource type=\"ShaderMaterial\" load_steps=2 format=3]",
            "",
            "[sub_resource type=\"Shader\" id=\"Shader_harness\"]",
            'code = "shader_type canvas_item;',
            "void fragment() {",
            "\tCOLOR = vec4(0.0, 1.0, 0.0, 1.0);",
            "}",
            '"',
            "",
            "[resource]",
            'shader = SubResource("Shader_harness")',
            "",
        ]), encoding="utf-8")

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import scaffold_install  # noqa: E402  (the installer under test)
    rc = scaffold_install.install_full(REPO_ROOT, scratch, {}, hook=False)
    if rc != 0:
        return fail("scaffold_install.py full returned %d on the scratch project" % rc)
    project_text = (scratch / "project.godot").read_text(encoding="utf-8")
    if project_text.count(scaffold_install.AUTOLOAD_LINE) != 1:
        return fail("full did not wire the DevTools autoload exactly once:\n" + project_text)
    for rel in scaffold_install.SHIPPED_FILES + [
            "devtools_ext/commands.gd", "test/unit/test_selftest.gd", "CLAUDE.md",
            "log-devtools.md", scaffold_install.CONFIG_REL]:
        if not (scratch / rel).is_file():
            return fail("full did not install %s" % rel)
    print("stage 2 assemble: scratch project at %s (installed by scaffold_install.py full: "
          "%d shipped files, config, devtools_ext, test seed, CLAUDE.md, log, autoload)"
          % (scratch, len(scaffold_install.SHIPPED_FILES)))
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

    ok = check_engine_skew(scratch, cache) and ok
    return ok


def check_engine_skew(scratch, cache):
    """The index-vs-project engine mismatch warning must FIRE (H-032).

    Its first version never did: `godot_version` in config is bare ("4.7.1")
    while an index's engine is the binary's banner ("Godot Engine v4.7.1..."),
    and a regex anchored at the start matched one and never the other. It
    reported clean on a real mismatch, which is indistinguishable from having no
    check at all - the same shape as [H-031], caught the same way, by planting.
    """
    config_path = scratch / "addons" / "godot_selftest" / "devtools_config.json"
    original = config_path.read_text(encoding="utf-8")
    try:
        def run_with(version):
            config = json.loads(original)
            if version is None:
                config.pop("godot_version", None)
            else:
                config["godot_version"] = version
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            return _run_name_check(scratch, cache, [])

        mismatch = run_with("4.0.0")
        if "WARNING:" not in mismatch.stdout or "declares Godot 4.0" not in mismatch.stdout:
            return fail("stage 2.5 names: a project declaring Godot 4.0 against a "
                        "newer index must warn. Got:\n%s" % mismatch.stdout.strip())
        # The index's own version, however it is spelled, must NOT warn.
        engine = re.search(r"engine index: \S+ \S+ v?(\d+\.\d+)", mismatch.stdout)
        if engine:
            same = run_with(engine.group(1))
            if "WARNING:" in same.stdout:
                return fail("stage 2.5 names: matching engine version must not warn:\n%s"
                            % same.stdout.strip())
        unset = run_with(None)
        if "WARNING:" in unset.stdout:
            return fail("stage 2.5 names: an unset godot_version must not warn "
                        "(unknown is not a mismatch):\n%s" % unset.stdout.strip())
        print("stage 2.5 names: engine skew warns on a mismatch, stays quiet on a "
              "match and when unset")
        return True
    finally:
        config_path.write_text(original, encoding="utf-8")


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
            if name == "tests":
                ok = check_test_denominators(proc.stdout, scratch) and ok
            if name == "lint":
                ok = check_shader_denominator(proc.stdout) and ok
    if ok:
        ok = stage_vacuous_control(godot, scratch) and ok
        ok = stage_runner_controls(godot, scratch) and ok
        ok = stage_shader_control(godot, scratch) and ok
        ok = stage_capture(godot, scratch) and ok
    return ok


def check_shader_denominator(out):
    """The shader pass must name what it compiled, not merely stay quiet.

    stage_assemble plants four shaders in three shapes; a pass that scanned
    nothing prints "Shaders: none found" and is otherwise indistinguishable
    from a clean one, which is the [H-035] shape.
    """
    m = re.search(r"Shaders: (\d+) of (\d+) compiled OK \((\d+) file, (\d+) embedded\)", out)
    if not m:
        return fail("lint printed no `Shaders: N of M` line over a scratch project "
                    "holding four shaders - the shader pass is not running\n%s" % out)
    passed, total, files, embedded = (int(g) for g in m.groups())
    if passed != total:
        return fail("lint reports %d of %d shaders compiling on the pristine scratch "
                    "project - the fixtures themselves are broken" % (passed, total))
    if files < 2 or embedded < 1:
        return fail("shader pass saw %d file / %d embedded shader(s); the fixtures "
                    "provide 2 files and 1 embedded, so a shape is being missed"
                    % (files, embedded))
    if ".gdshaderinc skipped" not in out:
        return fail("shader pass did not report the planted .gdshaderinc as skipped - "
                    "an include file it silently passed would be a false green")
    print("stage 4 lint: shaders %d of %d compiled (%d file, %d embedded), include skipped"
          % (passed, total, files, embedded))
    return True


def stage_shader_control(godot, scratch):
    """Positive control: plant a shader that cannot compile.

    A broken shader is the failure the pass exists for and nothing else in the
    harness sees it - the scene holding it still loads. The scratch project
    lints clean with or without a working pass, so planting the defect is the
    only thing that tells the two apart.
    """
    planted = scratch / "shaders" / "harness_shader_control.gdshader"
    planted.write_text(
        "shader_type canvas_item;\n"
        "void fragment() {\n"
        "\tCOLOR = vec4(harness_control_undefined_symbol, 0.0, 0.0, 1.0);\n"
        "}\n",
        encoding="utf-8")
    try:
        proc = run_godot(godot, scratch, ["--script", "res://tools/lint_project.gd"])
        out = proc.stdout
        if proc.returncode != 1:
            return fail("planted uncompilable shader exited %d, expected 1 - a shader "
                        "that fails to compile is being reported as clean\n%s"
                        % (proc.returncode, out))
        if "harness_shader_control.gdshader" not in out:
            return fail("planted shader failed the run without the report naming it\n%s" % out)
        # --no-shaders must actually turn the pass off, or the flag is decoration
        # and the pass cannot be escaped by a project that needs to.
        off = run_godot(godot, scratch, ["--script", "res://tools/lint_project.gd",
                                         "--", "--no-shaders"])
        if off.returncode != 0:
            return fail("--no-shaders still failed on the planted shader (exit %d) - "
                        "the flag does not skip the pass\n%s" % (off.returncode, off.stdout))
        if "Shaders:" in off.stdout:
            return fail("--no-shaders still printed a `Shaders:` line")
        print("stage 4 lint: shader control fired (exit 1 naming the planted file; "
              "--no-shaders exits 0 and prints nothing)")
        return True
    finally:
        planted.unlink(missing_ok=True)
        (planted.parent / (planted.name + ".uid")).unlink(missing_ok=True)


def check_test_denominators(out, scratch):
    """The runner must state what it looked at, not just that it passed.

    Four numbers, each of which has at some point been the difference between a
    real pass and a green-looking nothing: how many tests were selected, how many
    autoloads were actually ready, how many assertions executed, and how many test
    scripts the project has accumulated.
    """
    ok = True
    m = re.search(r"Autoloads: (\d+) of (\d+) ready", out)
    if not m:
        ok = fail("run_tests printed no `Autoloads:` line - the scratch project has a "
                  "DevTools autoload, so the readiness check is not running")
    elif m.group(1) != m.group(2):
        ok = fail("run_tests reports %s of %s autoloads ready; _await_autoloads() is "
                  "not stepping the tree" % (m.group(1), m.group(2)))
    else:
        print("stage 4 tests: autoloads %s of %s ready" % (m.group(1), m.group(2)))

    m = re.search(r"Assertions: (\d+) executed", out)
    if not m:
        ok = fail("run_tests printed no `Assertions:` line")
    elif m.group(1) == "0":
        ok = fail("run_tests executed 0 assertions over the seeded suite - the counter "
                  "is not wired to the _T.assert_* helpers")
    else:
        print("stage 4 tests: %s assertion(s) executed" % m.group(1))

    # `Suite: N test script(s)` is the inherited-coverage reading a fresh session
    # is told to act on, so it has to be the real file count and not a constant.
    # Checked against an independently computed truth (the files on disk) rather
    # than against itself: a `Suite: 1` hardcoded, or wired to _selected instead
    # of the discovery list, passes any assertion that only looks for the line.
    on_disk = len(list((scratch / "test" / "unit").glob("test_*.gd")))
    m = re.search(r"Suite: (\d+) test script\(s\) in (\S+)", out)
    if not m:
        ok = fail("run_tests printed no `Suite:` line - a session cannot see how much "
                  "checking previous sessions left it")
    elif int(m.group(1)) != on_disk:
        ok = fail("run_tests reports `Suite: %s` but %d test_*.gd file(s) are on disk "
                  "in the scratch project - the count is not the discovery list"
                  % (m.group(1), on_disk))
    else:
        print("stage 4 tests: Suite: %s test script(s) in %s (matches %d on disk)"
              % (m.group(1), m.group(2), on_disk))
    return ok


def stage_vacuous_control(godot, scratch):
    """Positive control: plant a test that asserts inside a loop over nothing.

    This is the findmyballs shape - three real tests passed exactly this way
    against an autoload holding no data. The scratch suite passes clean either
    way, so without planting the defect a broken detector and a working one look
    identical, which is [H-030] in the stage that would otherwise re-learn it.
    """
    planted = scratch / "test" / "unit" / "test_vacuous_control.gd"
    planted.write_text(
        "extends RefCounted\n"
        "var _T\n"
        "\n"
        "func _empty() -> Array:\n"
        "\treturn []\n"
        "\n"
        "func test_asserts_over_an_empty_collection() -> String:\n"
        "\tfor item in _empty():\n"
        "\t\tvar e: String = _T.assert_true(item != null, \"never runs\")\n"
        "\t\tif e != \"\":\n"
        "\t\t\treturn e\n"
        "\treturn \"\"\n"
        "\n"
        "func test_handrolled_needs_no_helper() -> String:\n"
        "\tif 2 + 2 != 4:\n"
        "\t\treturn \"arithmetic broke\"\n"
        "\treturn \"\"\n",
        encoding="utf-8")
    try:
        proc = run_godot(godot, scratch, ["--script", "res://tools/run_tests.gd"])
        out = proc.stdout
        if proc.returncode == 0:
            return fail("planted vacuous test still exited 0 - a test that executes "
                        "none of its own assertions is being reported as a pass")
        if "[VACU] test_asserts_over_an_empty_collection" not in out:
            return fail("planted vacuous test was not flagged [VACUOUS]\n%s" % out)
        if "[VACU] test_handrolled_needs_no_helper" in out:
            return fail("a hand-rolled test with no _T.assert_* call was flagged "
                        "vacuous - the source discriminator is over-reaching")
        print("stage 4 tests: vacuous control fired (exit %d, [VACU] on the empty-loop "
              "test only)" % proc.returncode)
        return True
    finally:
        planted.unlink(missing_ok=True)


def stage_runner_controls(godot, scratch):
    """Two more positive controls for run_tests.gd, each planting the defect it
    claims to detect (H-035).

    1. gh#10: a test script that does not compile, selected by --file. The verdict
       must blame the compile failure, not the selector.
    2. H-029: no class cache (never-imported project). The runner must refuse with
       exit 2 naming --import, not report a pass over tests that could not run.
    """
    ok = True
    broken = scratch / "test" / "unit" / "test_broken_control.gd"
    broken.write_text(
        "extends RefCounted\n"
        "var _T\n"
        "\n"
        "func test_never_reached() -> String:\n"
        "\tvar err := _T.assert_true(true, \"x\")  # := on an untyped call: cannot infer\n"
        "\treturn err\n",
        encoding="utf-8")
    try:
        proc = run_godot(godot, scratch, ["--script", "res://tools/run_tests.gd",
                                          "--", "--file", "test_broken_control.gd"])
        out = proc.stdout
        if proc.returncode != 2:
            ok = fail("--file on a script that fails to compile exited %d, expected 2\n%s"
                      % (proc.returncode, out))
        elif "FAILED TO COMPILE" not in out or "test_broken_control.gd" not in out.split("SELECTED NOTHING")[-1]:
            ok = fail("--file on an uncompilable script did not blame the compile "
                      "failure by name in the verdict (gh#10)\n%s" % out)
        elif "--filter matches method names" in out:
            ok = fail("--file on an uncompilable script still printed selector-syntax "
                      "advice - the verdict points at the wrong cause (gh#10)\n%s" % out)
        else:
            print("stage 4 tests: uncompilable --file target -> verdict names the compile "
                  "failure, not the selector (exit 2)")
    finally:
        broken.unlink(missing_ok=True)

    cache = scratch / ".godot" / "global_script_class_cache.cfg"
    if not cache.exists():
        return fail("expected %s to exist after --import; cannot run the H-029 control" % cache)
    hidden = cache.with_suffix(".cfg.hidden")
    cache.rename(hidden)
    try:
        proc = run_godot(godot, scratch, ["--script", "res://tools/run_tests.gd"])
        out = proc.stdout
        if proc.returncode != 2 or "never been imported" not in out or "--import" not in out:
            ok = fail("run_tests.gd with no class cache exited %d; expected 2 with a line "
                      "naming --import (H-029)\n%s" % (proc.returncode, out))
        else:
            print("stage 4 tests: no class cache -> exit 2 naming --import (H-029 control)")
    finally:
        hidden.rename(cache)
    return ok


def stage_capture(godot, scratch):
    """capture.gd: the headless refusal, a real capture, and the flat-image control.

    The refusal is the part that must never regress. Under --headless the viewport
    texture is null, so a capture tool that does not check would write a blank file
    (or a 0-byte one) and report success - a picture of nothing is indistinguishable
    from a picture of a broken scene, and it would be believed.

    The windowed half needs a real display. Where there is none it reports SKIPPED,
    because a capture stage that quietly passes on a machine that cannot render is
    the same lie one step further out.
    """
    shot = scratch / "harness_capture.png"
    shot.unlink(missing_ok=True)
    proc = run_godot(godot, scratch, ["--script", "res://tools/capture.gd",
                                      "--", "--out", str(shot)])
    if proc.returncode != 2:
        return fail("capture.gd under --headless exited %d, expected 2 - it must "
                    "refuse where there is no renderer, not write a blank image\n%s"
                    % (proc.returncode, proc.stdout))
    if shot.exists():
        return fail("capture.gd refused under --headless but still wrote %s - the "
                    "refusal has to come before the file is created" % shot.name)
    print("stage 4 capture: headless refused (exit 2, no file written)")

    # Windowed. No --headless, so this needs a display; anything else is SKIPPED.
    def windowed(extra, timeout=GODOT_TIMEOUT):
        return subprocess.run([str(godot), "--path", str(scratch)] + extra,
                              capture_output=True, text=True, timeout=timeout)

    try:
        real = windowed(["--script", "res://tools/capture.gd", "--", "--out", str(shot)])
    except subprocess.TimeoutExpired:
        print("stage 4 capture: windowed capture SKIPPED (timed out - no display?)")
        return True
    if "could not run" in real.stdout and "headless" in real.stdout:
        print("stage 4 capture: windowed capture SKIPPED (no display server available)")
        return True
    if real.returncode != 0:
        return fail("windowed capture.gd exited %d\n%s" % (real.returncode, real.stdout))
    if not shot.exists() or shot.stat().st_size == 0:
        return fail("windowed capture.gd exited 0 but produced no usable file")
    m = re.search(r"(\d+) distinct colour\(s\) sampled", real.stdout)
    if not m:
        return fail("capture.gd printed no distinct-colour count - the run cannot say "
                    "whether anything actually drew\n%s" % real.stdout)
    if int(m.group(1)) < 2:
        return fail("capture of the fixture scene sampled %s colour(s) - it rendered "
                    "nothing, or the sampler is broken" % m.group(1))
    colours = int(m.group(1))

    # Positive control for the flat-image detector. Without a scene that IS blank, a
    # detector hard-wired to report "plenty of colours" passes every check above.
    blank = scratch / "harness_capture_blank.tscn"
    blank.write_text('[gd_scene format=3]\n\n[node name="Blank" type="Node2D"]\n',
                     encoding="utf-8")
    try:
        flat = windowed(["--script", "res://tools/capture.gd", "--",
                         "--scene", "res://harness_capture_blank.tscn",
                         "--out", str(scratch / "harness_capture_blank.png"),
                         "--fail-on-uniform"])
        if flat.returncode != 1:
            return fail("a scene that draws nothing exited %d under --fail-on-uniform, "
                        "expected 1 - the flat-image check is not firing\n%s"
                        % (flat.returncode, flat.stdout))
        if "single flat colour" not in flat.stdout:
            return fail("blank capture was not reported as flat\n%s" % flat.stdout)
    finally:
        blank.unlink(missing_ok=True)
        (scratch / "harness_capture_blank.png").unlink(missing_ok=True)
        shot.unlink(missing_ok=True)
    print("stage 4 capture: windowed capture OK (%d colours), flat control fired "
          "(exit 1 on a scene that drew nothing)" % colours)
    return True


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
         ["global_rect", "size_source", "canvas_scale"]),
        ("aabb", {"node_path": "/root/Main/Prop3D"}, True,
         "moving-in:G-002/G-006: merged world-space AABB. merged_count==1 is the "
         "assertion that matters -- the prop carries an OmniLight3D whose own AABB "
         "is a 10-unit cube, so a walk that fails to exclude Light3D reports 2 here "
         "and a box 50x too large",
         ["min", "max", "size", "center", "top_y", "bottom_y",
          "merged_count", "merged", "excluded", "node_transform"],
         {"merged_count": 1}),
        ("aabb", {"node_path": "/root/Main"}, False,
         "a Node2D has no 3D geometry: must fail loudly, never report a zero box "
         "(a zero AABB at the origin is indistinguishable from a real measurement "
         "of a small object at the origin)"),
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
         "gather:G-129: the fixture's four Buttons ('Go', the planted 8x8 'Tiny', "
         "and the scaled-HUD pair) must all be found; three are reachable and "
         "'ScaledOutside' is not, because it renders at x=1800 of 1152. That the "
         "reachable count is 3 and not 4 is the gh#2 assertion in this table: a "
         "count of 4 means the off-screen test stopped firing, and 2 means the "
         "CanvasLayer scale is being ignored again",
         ["controls", "count", "reachable", "viewport"], {"count": 4, "reachable": 3}),
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
        ("validate_ui", {}, False,
         "envelope only: success depends on baseline state, and check_ui_baseline() "
         "tests that semantics properly",
         ["issues", "baseline_in_use", "new_count", "pre_existing_count"]),
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


_FINDINGS_KEYS = ("findings", "counts", "checks_run", "checks_skipped", "viewport",
                  "baseline_in_use", "new_count", "pre_existing_count",
                  "geometry_trustworthy", "geometry_caveat")
_FINDINGS_CHECKS = ("ui_layout", "ui_reachable", "signal_unconnected",
                    "performance", "scene_validation")


def check_findings_aggregate(client, scratch):
    """`findings` must carry its own denominator and actually find the plants.

    A consolidated report is the easiest place in the system for a check to go
    quiet: five checks collapse to one exit code, and a check that silently
    stopped running looks exactly like a check that passed. So this asserts three
    separate things, none of which a do-nothing implementation can fake.

    stage_assemble already plants the UI defects (a Label at alpha 0, an 8x8
    Button), so `ui_layout` having findings is the positive control - and it is
    checked BEFORE check_ui_baseline writes those findings off as pre-existing.
    """
    reply = client.send_command(scratch, "findings", {}, timeout=60.0)
    data = reply.get("data") or {}

    missing = [k for k in _FINDINGS_KEYS if k not in data]
    if missing:
        return fail("findings reply is missing data key(s) %s - the GDScript and "
                    "Python halves have drifted, which is the seam this whole stage "
                    "exists for" % ", ".join(missing))

    ran = list(data["checks_run"])
    skipped = [s.get("check") for s in data["checks_skipped"]]
    if sorted(ran) != sorted(_FINDINGS_CHECKS):
        return fail("findings ran %r, expected all of %r on a healthy scratch "
                    "project (skipped: %r). A check missing from checks_run did not "
                    "run, and its findings cannot be in the report."
                    % (sorted(ran), sorted(_FINDINGS_CHECKS), skipped))

    # A check that ran and found nothing must be 0, never absent: absent is the
    # encoding for "did not run", and collapsing the two is how a consolidated
    # report starts lying.
    absent = [c for c in ran if c not in data["counts"]]
    if absent:
        return fail("findings ran %r but counts has no entry for them - a check that "
                    "found nothing must report 0, or 'clean' and 'never ran' become "
                    "the same output" % absent)

    ui = data["counts"].get("ui_layout", 0)
    if ui == 0:
        return fail("findings reports 0 ui_layout findings on a project with planted "
                    "UI defects (a Label at alpha 0 and an 8x8 Button). Either the "
                    "plants stopped being planted or the aggregate stopped calling "
                    "validate_ui; either way this stage would otherwise pass on an "
                    "implementation that returns an empty list")

    sources = {f.get("source") for f in data["findings"]}
    if not sources <= set(_FINDINGS_CHECKS):
        return fail("findings carry unknown source(s) %r - every finding must be "
                    "attributable to a declared check"
                    % sorted(sources - set(_FINDINGS_CHECKS)))

    # The skip path. --no-scenes must remove scene_validation from the denominator
    # and say so by name, not just quietly shrink the number.
    # The bus key is `scenes: false` - the client's --no-scenes flag inverts it.
    reply2 = client.send_command(scratch, "findings", {"scenes": False}, timeout=60.0)
    d2 = reply2.get("data") or {}
    ran2 = list(d2.get("checks_run", []))
    skipped2 = [s.get("check") for s in d2.get("checks_skipped", [])]
    if "scene_validation" in ran2:
        return fail("findings --no-scenes still ran scene_validation")
    if "scene_validation" not in skipped2:
        return fail("findings --no-scenes dropped scene_validation from checks_run "
                    "without naming it in checks_skipped - a check that vanishes "
                    "silently is the failure this key exists to prevent (skipped: %r)"
                    % skipped2)

    print("stage 5 bridge: findings ran %d of %d checks, %d finding(s) incl. %d "
          "ui_layout on the planted defects; --no-scenes -> %d checks with "
          "scene_validation named as skipped"
          % (len(ran), len(_FINDINGS_CHECKS), len(data["findings"]), ui, len(ran2)))

    # H-051: this stage runs HEADLESS, which is exactly the condition under which
    # a screen-position verdict is not what a player sees (the window is 64x64;
    # a node the game centres from window size sits 368px off). The aggregate
    # must say so, and the flag must survive the trip through the aggregate -
    # findings re-shapes each check's reply and would otherwise drop it. The
    # planted ScaledOutside button is a geometry finding (ui_overflow), so a
    # per-finding caveat must be present on at least one.
    if data.get("geometry_trustworthy") is not False or not str(data.get("geometry_caveat", "")):
        return fail("findings headless reports geometry_trustworthy=%r, caveat=%r - a "
                    "headless run must flag its screen geometry as not-what-a-player-sees "
                    "(H-051)" % (data.get("geometry_trustworthy"), data.get("geometry_caveat")))
    geometry_hits = [f for f in data["findings"] if f.get("caveat")]
    if not geometry_hits:
        return fail("findings headless carries the aggregate caveat but no per-finding "
                    "`caveat` on any geometry finding; the planted ScaledOutside overflow "
                    "should have one (H-051)")
    if any(f.get("caveat") for f in data["findings"] if f.get("code") in ("ui_transparent", "small_tap_target")):
        return fail("findings stamped the headless caveat on a non-geometry finding "
                    "(ui_transparent / small_tap_target are the same headless or windowed)")
    print("stage 5 bridge: findings headless -> geometry_trustworthy=false, %d of %d finding(s) "
          "carry the H-051 caveat (geometry codes only)" % (len(geometry_hits), len(data["findings"])))
    return True


def check_geometry_caveat_and_hide(client, scratch):
    """node-bounds must say its rect is headless geometry (H-051); screenshot
    --hide must refuse a path it cannot hide, and accept a CanvasLayer (gh#5).

    Screenshot cannot capture headless, so the hide check stops at the collect
    step: an unhideable path returns the refusal BEFORE any capture is attempted
    (distinct message), while a CanvasLayer path gets past it (and then fails on
    the capture, which is the headless-only part). Both branches are asserted so
    a `--hide` that silently ignores everything cannot pass.
    """
    reply = client.send_command(scratch, "get_node_bounds", {"node_path": "/root/Main/Go"})
    data = reply.get("data") or {}
    if data.get("geometry_trustworthy") is not False or not data.get("geometry_caveat"):
        return fail("node-bounds headless: geometry_trustworthy=%r caveat=%r; a headless rect "
                    "must carry the H-051 caveat" % (data.get("geometry_trustworthy"),
                                                     data.get("geometry_caveat")))
    if "HEADLESS" not in str(data["geometry_caveat"]):
        return fail("node-bounds caveat does not name HEADLESS: %r" % data["geometry_caveat"])

    bad = client.send_command(scratch, "screenshot", {"hide": ["/root/Main/NoSuchNode"]})
    if bad.get("success") or "matched nothing it could hide" not in str(bad.get("message", "")):
        return fail("screenshot --hide on a missing node did not refuse: %r" % bad)
    bad2 = client.send_command(scratch, "screenshot", {"hide": ["/root/Main/Prop3D"]})
    if bad2.get("success") or "no visibility" not in str(bad2.get("message", "")):
        return fail("screenshot --hide on a Node3D (no `visible`) did not refuse by class: %r" % bad2)
    layer = client.send_command(scratch, "screenshot", {"hide": ["/root/Main/ScaledHud"]})
    if "matched nothing it could hide" in str(layer.get("message", "")):
        return fail("screenshot --hide on a CanvasLayer was refused as unhideable - the "
                    "gh#5 fix (CanvasLayer counts) is not in effect: %r" % layer)
    print("stage 5 bridge: node-bounds headless carries the H-051 caveat; screenshot --hide "
          "refuses a missing node and a Node3D by name, accepts a CanvasLayer")
    return True


def check_ui_baseline(client, scratch):
    """validate_ui's NEW/PRE split, against a project that HAS findings.

    The stock scratch project reports zero UI issues, and an empty finding set
    round-trips through any baseline implementation whatsoever - including one
    that does nothing. stage_assemble plants two real defects (a Label resting
    at alpha 0, which is verbatim the finding that stalled findmyballs, and an
    8x8 Button) so the transition is actually exercised.
    """
    def call(args=None, timeout=20.0):
        return client.send_command(scratch, "validate_ui", args or {}, timeout=timeout)

    first = call()
    issues = (first.get("data") or {}).get("issues", [])
    n = len(issues)
    if n == 0:
        return fail("validate_ui found 0 issues on a project with planted UI defects - "
                    "either the defects stopped being planted or the checks stopped "
                    "running; either way the baseline test below proves nothing")
    d = first["data"]
    if d.get("baseline_in_use") is not False or d.get("new_count") != n:
        return fail("with no baseline every finding must gate: in_use=%r new=%r of %d"
                    % (d.get("baseline_in_use"), d.get("new_count"), n))
    if first.get("success"):
        return fail("validate_ui reported success with %d ungated findings" % n)
    if not all(i.get("path") and i.get("baseline") == "new" for i in issues):
        return fail("every finding needs a node path and baseline=new: %r" % issues[:2])

    wrote = call({"baseline_write": True})
    if not ((wrote.get("data") or {}).get("baseline_written") and wrote.get("success")):
        return fail("baseline_write: %r" % wrote.get("message"))

    after = call()
    da = after["data"]
    if da.get("new_count") != 0 or da.get("pre_existing_count") != n:
        return fail("after baseline_write, known findings must be pre-existing, not "
                    "NEW: new=%r pre=%r of %d"
                    % (da.get("new_count"), da.get("pre_existing_count"), n))
    if not after.get("success"):
        return fail("a fully-baselined run must pass: %r" % after.get("message"))

    ignored = call({"use_baseline": False})
    if (ignored.get("data") or {}).get("new_count") != n:
        return fail("--no-baseline must re-report every finding")

    # The baseline stays written. The contract table's validate_ui row runs
    # after this and asserts the envelope only, precisely because success now
    # depends on baseline state rather than on the verb alone.
    codes = sorted({i["code"] for i in issues})
    print("stage 5 bridge: validate_ui baseline %d finding(s) %s -> NEW, written, "
          "-> PRE, run passes" % (n, codes))
    return True


def check_canvas_layer_space(client, scratch):
    """Screen-space rects for Controls on a scaled CanvasLayer (gh#2).

    Control.get_global_rect() stops at the CanvasLayer, so a HUD on a scaled
    layer reports rects in layer units while the viewport is measured in pixels.
    One project got 55 false ui_overflow findings that way and had to baseline 53
    of them, which is the outcome the baseline feature exists to prevent.

    stage_assemble plants ScaledInside (x=1200 in layer units = 720 on screen)
    and ScaledOutside (x=3000 = 1800, genuinely off it). Asserting only that the
    first is clean would pass against a check that reports nothing at all, so the
    second is asserted to still fire.

    Also asserts the viewport these are measured against. Stage 5 runs headless,
    where root.size is 64x64 -- against which EVERY Control here overflows, and
    every assertion below would be meaningless.
    """
    hud = "/root/Main/ScaledHud"
    bounds = client.send_command(
        scratch, "get_node_bounds", {"node_path": hud + "/ScaledInside"}, timeout=15.0)
    if not bounds.get("success"):
        return fail("get_node_bounds on the scaled HUD: %s" % bounds.get("message"))
    rect = (bounds.get("data") or {}).get("global_rect") or {}
    # 1200 * 0.6 = 720, 300 * 0.6 = 180. Layer units would read 1200 / 300.
    if abs(rect.get("x", -1) - 720.0) > 1.0 or abs(rect.get("w", -1) - 180.0) > 1.0:
        return fail("node_bounds ignored the CanvasLayer scale: got x=%r w=%r, "
                    "expected 720/180 (1200/300 means the canvas transform is "
                    "not applied)" % (rect.get("x"), rect.get("w")))

    # use_baseline False so this does not depend on whether check_ui_baseline has
    # already written one.
    ui = client.send_command(scratch, "validate_ui", {"use_baseline": False}, timeout=20.0)
    issues = (ui.get("data") or {}).get("issues", [])
    overflow = [i for i in issues if i.get("code") == "ui_overflow"]
    flagged = {i.get("path", "") for i in overflow}
    if any("ScaledInside" in p for p in flagged):
        return fail("validate_ui flagged ScaledInside as overflowing; it renders at "
                    "720..900 of 1152. Rects are being read in CanvasLayer units.\n"
                    "  %s" % next(i["message"] for i in overflow if "ScaledInside" in i["path"]))
    if not any("ScaledOutside" in p for p in flagged):
        return fail("validate_ui did NOT flag ScaledOutside, which renders at "
                    "1800 of 1152 - the overflow check is not firing at all, so "
                    "ScaledInside coming back clean proves nothing")

    reach = client.send_command(scratch, "reachable_ui", {}, timeout=20.0)
    controls = {c["path"]: c for c in (reach.get("data") or {}).get("controls", [])}
    inside = next((c for p, c in controls.items() if "ScaledInside" in p), None)
    outside = next((c for p, c in controls.items() if "ScaledOutside" in p), None)
    if inside is None or outside is None:
        return fail("reachable_ui did not report the planted HUD buttons (saw %s)"
                    % sorted(controls))
    if not inside["on_screen"]:
        return fail("reachable_ui called ScaledInside OFF-SCREEN at rect %r - a "
                    "visible, clickable button reported unreachable" % (inside["rect"],))
    if outside["on_screen"]:
        return fail("reachable_ui called ScaledOutside on-screen; it is at x=1800 "
                    "of 1152, so the off-screen test is not firing")

    vp = (reach.get("data") or {}).get("viewport") or {}
    if int(vp.get("w", 0)) != 1152 or int(vp.get("h", 0)) != 648:
        return fail("UI verbs measured against a %sx%s viewport, expected the "
                    "project's designed 1152x648. Headless has no window, so "
                    "root.size is 64x64 and every check above is vacuous."
                    % (vp.get("w"), vp.get("h")))

    print("stage 5 bridge: canvas-layer space OK (scaled HUD: inside 720..900 clean, "
          "outside 1800 still flagged, viewport %sx%s not 64x64)"
          % (int(vp["w"]), int(vp["h"])))
    return True


def check_find_nodes_denominator(client, scratch):
    """An empty `--where` must say WHY it is empty (moving-in:G-011).

    That gap was reported as "--where compares only by string equality, so
    mouse_filter=0 matches nothing". It does not reproduce: the widening branch in
    _values_match has carried numeric predicates since 0.8.0, and deleting it
    reproduces the report exactly. What is real is that the two opposite empty
    results printed identically -- "no node has this value" and "nothing here has a
    property by that name" -- so a reader took one for the other and cleared a UI of
    exactly the fault it had.

    So this plants BOTH empty kinds and asserts they read differently, plus the
    positive control the report claimed was broken. Asserting only that a good query
    matches would pass against a verb that says nothing on failure, which is the
    thing being fixed.
    """
    numeric = client.send_command(
        scratch, "find_nodes",
        {"class": "Button", "where": {"mouse_filter": 0}}, timeout=15.0)
    if not numeric.get("success"):
        return fail("find_nodes numeric --where: %s" % numeric.get("message"))
    if int(((numeric.get("data") or {}).get("count") or 0)) < 1:
        return fail(
            "find_nodes --where mouse_filter=0 matched nothing. Godot's JSON parser "
            "makes every number a float, so this predicate arrives as float(0.0) "
            "against an int(0) property -- _values_match's _is_number widening "
            "branch is the only thing that carries it, and it is gone or broken.")

    no_match = client.send_command(
        scratch, "find_nodes",
        {"class": "Button", "where": {"mouse_filter": 99}}, timeout=15.0)
    msg_no_match = no_match.get("message") or ""
    if "0 of" not in msg_no_match or "expose it" not in msg_no_match:
        return fail(
            "find_nodes with a value nothing matches said %r. It must name the "
            "denominator ('0 of N matched on mouse_filter'), or a predicate that "
            "silently matches nothing looks identical to a genuine absence."
            % (msg_no_match,))

    bad_name = client.send_command(
        scratch, "find_nodes",
        {"class": "Button", "where": {"harness_no_such_property": 1}}, timeout=15.0)
    msg_bad_name = bad_name.get("message") or ""
    if "no candidate exposes" not in msg_bad_name:
        return fail(
            "find_nodes with an unresolvable property name said %r. It must say the "
            "name never resolved -- that is the case that catches a typo, a stray "
            "space and a wrong case, which is what the original reporter actually hit."
            % (msg_bad_name,))
    if msg_no_match == msg_bad_name:
        return fail(
            "find_nodes: 'no node has this value' and 'no such property' produced the "
            "identical message (%r). Telling them apart is the entire fix."
            % (msg_no_match,))
    print("stage 5 bridge: find_nodes --where int predicate matched %d (the numeric "
          "widening works), and the two empty results read differently"
          % (int((numeric.get("data") or {}).get("count") or 0),))
    return True


def check_aabb_excludes_lights(client, scratch):
    """`aabb` must measure geometry, not light range (moving-in:G-002/G-006).

    stage_assemble plants Prop3D holding a 0.2-unit BoxMesh and an OmniLight3D with
    range 5.0. Light3D IS a VisualInstance3D, so the obvious subtree walk includes
    it, and an OmniLight3D's AABB is a cube of TWICE its range -- 10 units against a
    0.2-unit box. That is not a rounding error, it is a 50x measurement, and it is
    the bug that made a real project's first furniture audit unreadable: a ceiling
    lamp reported as 7.2 x 7.2 units dragged every top_of()/center_of() computed
    from it.

    Asserting only "the box was found" would pass against a verb that merges
    everything, so this asserts the SIZE (H-035: plant the defect you detect). The
    contract row asserts merged_count == 1 for the same reason from the other side.
    """
    reply = client.send_command(
        scratch, "aabb", {"node_path": "/root/Main/Prop3D"}, timeout=15.0)
    if not reply.get("success"):
        return fail("aabb on the planted 3D prop: %s" % reply.get("message"))
    data = reply.get("data") or {}
    size = data.get("size") or {}
    for axis in ("x", "y", "z"):
        got = size.get(axis)
        if not isinstance(got, (int, float)):
            return fail("aabb data.size.%s is %r, not a number" % (axis, got))
        if abs(got - 0.2) > 0.01:
            return fail(
                "aabb measured the prop at %s=%.3f, expected 0.2. A value near 10 "
                "means the OmniLight3D (range 5.0) was merged as geometry -- "
                "Light3D must be excluded." % (axis, got))
    excluded = data.get("excluded") or []
    if not any("Light" in (e.get("class") or "") for e in excluded):
        return fail(
            "aabb did not report the OmniLight3D in data.excluded (%r). The "
            "exclusion has to be visible, or a suspiciously small merged_count is "
            "mysterious instead of traceable." % (excluded,))
    print("stage 5 bridge: aabb measured the prop at %.3f (the box), not ~10 (the "
          "OmniLight3D's range volume); %d node(s) excluded by name"
          % (size.get("x"), len(excluded)))
    return True


def check_set_state_dotted(client, scratch):
    """Dotted property writes, and the hyphen spelling the docs use (gh#1).

    Object.set/get do not walk dots, so set_state used to write nothing through
    `environment.ambient_light_energy` and then report "unknown property" against
    a name that was correct - while get_state read the same path fine. Every
    negative here is asserted too: a resolver that accepts everything is as broken
    as one that accepts nothing.
    """
    go = "/root/Main/Go"

    def call(action, args, timeout=15.0):
        return client.send_command(scratch, action, args, timeout=timeout)

    wrote = call("set_state", {"node_path": go, "property": "theme.default_font_size",
                              "value": 21})
    if not wrote.get("success"):
        return fail("dotted set_state through a Resource failed: %s" % wrote.get("message"))
    read = call("get_state", {"node_path": go, "properties": ["theme.default_font_size"]})
    if (read.get("data") or {}).get("theme.default_font_size") != 21:
        return fail("set_state reported success but get_state reads %r, not 21 - the "
                    "write landed somewhere else"
                    % (read.get("data") or {}).get("theme.default_font_size"))

    # Negative 1: a leaf that does not exist must still fail, and must blame the
    # Resource rather than the node.
    bogus = call("set_state", {"node_path": go, "property": "theme.no_such_knob",
                               "value": 3})
    if bogus.get("success"):
        return fail("set_state accepted theme.no_such_knob - the read-back check is "
                    "not running on the dotted path")
    if "Theme" not in bogus.get("message", ""):
        return fail("the failure must name the object the write landed on (Theme), "
                    "or it sends the reader hunting for a typo in the node: %s"
                    % bogus.get("message"))

    # Negative 2: a struct component is refused, naming the call that works.
    struct = call("set_state", {"node_path": go, "property": "size.x", "value": 250})
    if struct.get("success"):
        return fail("set_state claimed to write size.x; components of a built-in "
                    "struct cannot be written through a Resource walk")
    if "Set it whole" not in struct.get("message", ""):
        return fail("refusing size.x must name the call that would have worked: %s"
                    % struct.get("message"))

    # The hyphen spelling. commands.gd's own header documents it, the sequence
    # step dispatcher already normalizes it, and the bus used to reject it.
    hyphen = call("scene-tree", {"depth": 1})
    if not hyphen.get("success"):
        return fail("the bus rejected the hyphenated spelling the docs use: %s"
                    % hyphen.get("message"))

    # ...but a verb that is genuinely absent must still fail, with a pointer.
    absent = call("set_stat", {"node_path": go})
    if absent.get("success"):
        return fail("'set_stat' succeeded - hyphen normalization is matching verbs "
                    "that were never registered")
    if "did you mean 'set_state'" not in absent.get("message", ""):
        return fail("an unknown verb close to a real one must suggest it: %s"
                    % absent.get("message"))

    print("stage 5 bridge: set_state dotted write OK (theme.default_font_size -> 21; "
          "bad leaf, struct component and absent verb all still refused; "
          "'scene-tree' accepted)")
    return True


def check_paused_bridge(client, scratch):
    """The bridge must answer while the tree is paused (findmyballs:G-003).

    Before 0.12.0 every call after a pause timed out, and the error text blamed
    --userdata - so this is checked by pausing for real rather than by reading
    process_mode back, which would confirm the assignment and not the effect.
    """
    def call(action, args=None):
        return client.send_command(scratch, action, args or {}, timeout=15.0)

    if call("ping")["data"].get("paused") is not False:
        return fail("ping must report paused=False on an unpaused game")
    if call("performance").get("data", {}).get("tree_paused") is not False:
        return fail("performance must report tree_paused=False on an unpaused game")

    r = call("run_method", {"node_path": "/root/Main",
                            "method": "harness_set_paused", "args": [True]})
    if not r.get("success"):
        return fail("could not pause the fixture: %s" % r.get("message"))
    try:
        ping = call("ping")
        if not ping.get("success"):
            return fail("bridge stopped answering once the tree was paused - "
                        "PROCESS_MODE_ALWAYS is not in effect")
        if ping["data"].get("paused") is not True:
            return fail("ping answered while paused but reported paused=%r"
                        % ping["data"].get("paused"))
        for action, args in (("scene_tree", {"depth": 1}),
                             ("find_nodes", {"class": "Button"}),
                             ("validate_ui", {})):
            if not call(action, args).get("success") and action != "validate_ui":
                return fail("%s failed while paused" % action)
        # gh#6: performance answers on a paused tree with plausible numbers, and
        # a whole /verify phase validated a frozen game on them. The reply must
        # carry the fact.
        perf = call("performance")
        if perf.get("data", {}).get("tree_paused") is not True:
            return fail("performance on a paused tree reported tree_paused=%r; the reply "
                        "must say the numbers describe a game that is not stepping (gh#6)"
                        % perf.get("data", {}).get("tree_paused"))
        if "PAUSED" not in str(perf.get("message", "")):
            return fail("performance message on a paused tree does not say PAUSED: %r"
                        % perf.get("message"))
        print("stage 5 bridge: paused tree still answers (ping/scene_tree/find_nodes/"
              "validate_ui), ping reports paused=True, performance says tree_paused=True")
    finally:
        call("run_method", {"node_path": "/root/Main",
                            "method": "harness_set_paused", "args": [False]})
    return True


def check_dispatch_reentrancy(client, scratch):
    """A command arriving mid-await must be DEFERRED, not run on top (H-038).

    Plants the defect the guard exists to stop, because there is no other way to
    reach it: send_command is one-at-a-time by construction, so this writes the two
    command files itself with its own timing. Before the guard, _process re-entered
    _check_for_commands during the await and the second command was read, deleted and
    dispatched into the same scene tree - measured as ping answering at 1.26s inside a
    5s step_time, then having its reply overwritten at 5.12s.

    The verdict is read from the GAME's log, not from the shared result file. The
    obvious version of this check watched user://devtools_results.json for the two
    replies in order, and it was wrong in a way worth recording: the second command is
    picked up within one ~100ms poll of the first one's reply being written, so the
    first reply exists on disk for a few milliseconds before being overwritten. Polling
    for it missed roughly half the time and reported the pass as the bug. The log's
    `Executing:` entries are durable and carry timestamps, so the question "was the
    second handler dispatched while the first was still running" is answered by
    subtraction instead of by winning a race.

    Three outcomes are distinguished and only one passes: a gap shorter than the slow
    verb means B ran on top of A (the bug); no B entry at all means the guard ATE the
    deferred command (what checking it after the read/delete would do); a gap of at
    least the slow verb's duration is correct.
    """
    user_data = Path(client.get_user_data_path(scratch))
    commands_name, results_name, log_name = client.bus_filenames()
    commands_path = user_data / commands_name
    results_path = user_data / results_name
    log_path = user_data / log_name

    def dispatches():
        """(action, timestamp) for every command the game has dispatched."""
        out = []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            msg = row.get("message", "")
            if row.get("category") == "command" and msg.startswith("Executing: "):
                out.append((msg[len("Executing: "):], row.get("timestamp", 0.0)))
        return out

    before = len(dispatches())
    if results_path.exists():
        results_path.unlink()
    id_a, id_b = "h038a", "h038b"
    slow_seconds = 3.0

    def put(action, args, ident):
        commands_path.write_text(
            json.dumps({"id": ident, "action": action, "args": args}), encoding="utf-8")

    t0 = time.time()
    put("step_time", {"seconds": slow_seconds}, id_a)
    while commands_path.exists():
        if time.time() - t0 > 10:
            return fail("stage 5 reentrancy: the slow command was never picked up")
        time.sleep(0.02)
    time.sleep(0.5)                      # A is provably mid-await now
    put("ping", {}, id_b)

    # Wait for the deferred command to be consumed, plus a beat for its log line.
    deadline = time.time() + slow_seconds + 20
    while time.time() < deadline:
        if not commands_path.exists() and len(dispatches()) >= before + 2:
            break
        time.sleep(0.05)
    time.sleep(0.2)

    new = dispatches()[before:]
    actions = [a for a, _ in new]
    if actions[:1] != ["step_time"]:
        return fail("stage 5 reentrancy: expected step_time to be dispatched first, "
                    "got %s" % actions)
    if "ping" not in actions:
        return fail(
            "stage 5 reentrancy: the deferred command was EATEN - the game dispatched "
            "%s and never ran the ping that arrived mid-await. The guard must be "
            "checked BEFORE the read and the delete, or pickup consumes the command it "
            "declines to run (H-038)" % actions)
    gap = new[actions.index("ping")][1] - new[0][1]
    if gap < slow_seconds - 0.25:
        return fail(
            "stage 5 reentrancy: the mid-await command was dispatched ON TOP of the "
            "running handler - ping started %.2fs after step_time began, inside a %.1fs "
            "step, so two handlers shared the scene tree and raced their replies onto "
            "the one result file (H-038)" % (gap, slow_seconds))
    print("stage 5 bridge: a command arriving mid-await was deferred, not overlapped "
          "(ping sent 0.5s into a %.0fs step_time was dispatched %.2fs in, i.e. after "
          "it finished)" % (slow_seconds, gap))
    return check_deferred_client(client, scratch)


def check_deferred_client(client, scratch):
    """The CLIENT must survive its own command being deferred (H-038).

    The check above drives raw command files, which proves the game half and nothing
    about devtools.py - and testing one half against a hand-rolled stand-in for the
    other is exactly what let 0.4.0 ship three wire mismatches green. So this one goes
    through the real send_command.

    The guard made a true statement false: the 2s liveness precheck read "command file
    still on disk" as proof that nothing is polling, and a deferred command sits on
    disk on purpose. Unfixed, every command sent during a slow verb dies instantly
    with `game not running` - a confidently wrong diagnosis, which this repo has
    repeatedly paid more for than for silence.

    The slow verb is started by writing its command file directly rather than from a
    second thread. Two concurrent send_commands would be a second CLIENT on a bus that
    documents itself as single-client, and it fails for an unrelated reason: both poll
    the one result file, so whichever reads second finds its reply already consumed and
    times out. That is the documented hazard, not a regression, and a check that trips
    over it is testing the wrong thing.
    """
    user_data = Path(client.get_user_data_path(scratch))
    commands_name, _, _ = client.bus_filenames()
    commands_path = user_data / commands_name
    slow_seconds = 3.0

    commands_path.write_text(json.dumps(
        {"id": "h038slow", "action": "step_time", "args": {"seconds": slow_seconds}}),
        encoding="utf-8")
    picked_up = time.time() + 10
    while commands_path.exists():
        if time.time() > picked_up:
            return fail("stage 5 reentrancy: the slow command was never picked up")
        time.sleep(0.02)

    time.sleep(0.5)                      # the slow verb is in its handler now
    started = time.time()
    try:
        reply = client.send_command(scratch, "ping", {}, timeout=30.0)
    except Exception as exc:
        return fail(
            "stage 5 reentrancy: send_command('ping') raised %s while the game was "
            "alive and busy inside step_time. The 2s liveness precheck is reading a "
            "DEFERRED command as a dead game - it must also require that no handler "
            "is in flight (H-038): %s" % (type(exc).__name__, exc))
    waited = time.time() - started

    if not reply.get("success"):
        return fail("stage 5 reentrancy: the deferred ping came back success=false: %s"
                    % reply.get("message"))
    if waited < 1.0:
        return fail(
            "stage 5 reentrancy: the deferred ping answered in %.2fs, too fast to have "
            "waited out the %.1fs step_time it was sent into - the bridge served two "
            "commands at once" % (waited, slow_seconds))
    print("stage 5 bridge: devtools.py survives its own command being deferred "
          "(ping sent mid-step_time answered after %.1fs instead of failing the 2s "
          "liveness precheck)" % waited)
    return True


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

        # Ahead of check_ui_baseline: this one asserts which findings exist, and
        # reads cleaner before a baseline has been written over them.
        ok = check_canvas_layer_space(client, scratch) and ok
        ok = check_aabb_excludes_lights(client, scratch) and ok
        ok = check_find_nodes_denominator(client, scratch) and ok
        ok = check_set_state_dotted(client, scratch) and ok
        # Before check_ui_baseline for the same reason: it writes a baseline that
        # moves the planted UI findings to pre-existing, and this check needs them
        # gating so a zero here means "the check did not run".
        ok = check_findings_aggregate(client, scratch) and ok
        ok = check_geometry_caveat_and_hide(client, scratch) and ok
        ok = check_ui_baseline(client, scratch) and ok
        ok = check_paused_bridge(client, scratch) and ok
        ok = check_dispatch_reentrancy(client, scratch) and ok

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
    # Needs no Godot and no project, so it runs under --static-only too.
    if not stage_reach():
        return 1
    if not stage_coverage():
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
