#!/usr/bin/env python3
"""Matrix tests for tools/scaffold_install.py — the install/refresh brain.

Every scaffold bug in the gaps log was a *refresh* bug, not a fresh-install bug
(H-008), and the ownership rule in patch_config took three attempts to get right
(H-010). This file pins both down with a temp-project matrix:

  fresh install            files land, manifest written, config fully owned
  pristine upgrade         file at an OLDER released hash -> silent overwrite, no .bak
  CRLF pristine upgrade    same, with CRLF line endings on disk (the H-008 case)
  locally-edited file      -> .bak created, file updated, reported MODIFIED
  edited config key        -> kept, dropped from owned
  key-reverted-to-default  -> STAYS project-owned (the sticky rule)
  double-run idempotence   second run leaves the tree byte-identical
  CLI guard                no project.godot -> exit 1

Most cases run against a synthetic plugin root (controlled content and history);
one integration case runs the real templates end-to-end. Pure Python, no Godot.

Usage:
    python tools/test_scaffold.py            # run everything
    python tools/test_scaffold.py -v         # unittest verbosity
"""

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import scaffold_install  # noqa: E402


def lf_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def tree_snapshot(root: Path) -> dict:
    """{relative path: raw sha256} for every file under root."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@contextlib.contextmanager
def captured_stdout():
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old


class SyntheticPluginCase(unittest.TestCase):
    """Matrix cases against a plugin root this test fully controls."""

    OLD_CONTENT = b"# tool v1\nprint('old')\n"
    NEW_CONTENT = b"# tool v2\nprint('new')\n"
    TOOL_REL = "tools/thing.py"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="scafftest-")
        base = Path(self._tmp.name)

        # Synthetic plugin: one template tool at "v2", one config template,
        # and a history that says version 0.1.0 shipped the tool at "v1".
        self.plugin = base / "plugin"
        (self.plugin / ".claude-plugin").mkdir(parents=True)
        (self.plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "synth", "version": "0.2.0"}), encoding="utf-8")
        tmpl_tool = self.plugin / "templates" / self.TOOL_REL
        tmpl_tool.parent.mkdir(parents=True)
        tmpl_tool.write_bytes(self.NEW_CONTENT)
        tmpl_cfg = self.plugin / "templates" / scaffold_install.CONFIG_REL
        tmpl_cfg.parent.mkdir(parents=True)
        tmpl_cfg.write_text(json.dumps(
            {"alpha": 1, "beta": "default", "gamma": True}), encoding="utf-8")
        (self.plugin / "harness_history.json").write_text(json.dumps(
            {"0.1.0": {self.TOOL_REL: lf_sha256(self.OLD_CONTENT)}}), encoding="utf-8")

        self.project = base / "project"
        self.project.mkdir()
        (self.project / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def install(self, rels=None):
        with captured_stdout() as buf:
            rc = scaffold_install.install_files(
                self.plugin, self.project, rels or [self.TOOL_REL])
        return rc, buf.getvalue()

    def config(self, overrides=None):
        with captured_stdout() as buf:
            rc = scaffold_install.patch_config(self.plugin, self.project, overrides or {})
        return rc, buf.getvalue()

    def read_config(self):
        return json.loads(
            (self.project / scaffold_install.CONFIG_REL).read_text(encoding="utf-8"))

    def baks(self):
        return sorted(p.relative_to(self.project).as_posix()
                      for p in self.project.rglob("*.bak"))

    # -- files matrix ----------------------------------------------------

    def test_fresh_install(self):
        rc, out = self.install()
        self.assertEqual(rc, 0)
        self.assertIn("+ %s installed" % self.TOOL_REL, out)
        self.assertEqual((self.project / self.TOOL_REL).read_bytes(), self.NEW_CONTENT)
        manifest = json.loads(
            (self.project / scaffold_install.MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(manifest["harness_version"], "0.2.0")
        self.assertEqual(manifest["files"][self.TOOL_REL]["sha256"],
                         lf_sha256(self.NEW_CONTENT))
        self.assertEqual(self.baks(), [])

    def test_pristine_upgrade_from_released_hash_no_bak(self):
        # The file on disk matches an OLDER released hash, with no manifest at
        # all (a pre-manifest install). This is the H-008 upgrade case.
        dst = self.project / self.TOOL_REL
        dst.parent.mkdir(parents=True)
        dst.write_bytes(self.OLD_CONTENT)
        rc, out = self.install()
        self.assertEqual(rc, 0)
        self.assertIn("unmodified - no backup needed", out)
        self.assertEqual(dst.read_bytes(), self.NEW_CONTENT)
        self.assertEqual(self.baks(), [])

    def test_pristine_upgrade_with_crlf_no_bak(self):
        # Same file, CRLF on disk (autocrlf checkout). Still pristine; the LF
        # normalization in sha256() is what makes the check work on Windows.
        dst = self.project / self.TOOL_REL
        dst.parent.mkdir(parents=True)
        dst.write_bytes(self.OLD_CONTENT.replace(b"\n", b"\r\n"))
        rc, out = self.install()
        self.assertEqual(rc, 0)
        self.assertIn("unmodified - no backup needed", out)
        self.assertEqual(self.baks(), [])

    def test_locally_edited_file_gets_bak(self):
        dst = self.project / self.TOOL_REL
        dst.parent.mkdir(parents=True)
        dst.write_bytes(b"# the project changed this\n")
        rc, out = self.install()
        self.assertEqual(rc, 0)
        self.assertIn("MODIFIED locally", out)
        self.assertEqual(dst.read_bytes(), self.NEW_CONTENT)
        self.assertEqual(self.baks(), [self.TOOL_REL + ".bak"])
        self.assertEqual((self.project / (self.TOOL_REL + ".bak")).read_bytes(),
                         b"# the project changed this\n")

    def test_already_current_is_a_noop(self):
        self.install()
        before = tree_snapshot(self.project)
        rc, out = self.install()
        self.assertEqual(rc, 0)
        self.assertIn("already current", out)
        self.assertEqual(tree_snapshot(self.project), before)

    # -- config matrix ---------------------------------------------------

    def test_fresh_config_all_owned(self):
        rc, _ = self.config({"beta": "detected"})
        self.assertEqual(rc, 0)
        cfg = self.read_config()
        self.assertEqual(cfg["beta"], "detected")
        record = cfg[scaffold_install.SCAFFOLD_DEFAULTS_KEY]
        self.assertEqual(sorted(record["owned"]), ["alpha", "beta", "gamma"])

    def test_edited_key_becomes_project_owned(self):
        self.config()
        cfg = self.read_config()
        cfg["beta"] = "my value"
        (self.project / scaffold_install.CONFIG_REL).write_text(
            json.dumps(cfg), encoding="utf-8")

        rc, out = self.config({"beta": "newer default"})
        self.assertEqual(rc, 0)
        self.assertIn('= beta kept as "my value"', out)
        cfg = self.read_config()
        self.assertEqual(cfg["beta"], "my value")
        self.assertNotIn("beta", cfg[scaffold_install.SCAFFOLD_DEFAULTS_KEY]["owned"])

    def test_reverted_to_default_stays_project_owned(self):
        # Edit beta (drops from owned), then set it BACK to the shipped default.
        # The sticky rule: it must stay project-owned, not silently reclaimed.
        self.config()
        cfg = self.read_config()
        cfg["beta"] = "my value"
        (self.project / scaffold_install.CONFIG_REL).write_text(
            json.dumps(cfg), encoding="utf-8")
        self.config()  # beta now recorded as project-owned

        cfg = self.read_config()
        cfg["beta"] = "default"  # identical to the template value again
        (self.project / scaffold_install.CONFIG_REL).write_text(
            json.dumps(cfg), encoding="utf-8")

        rc, out = self.config({"beta": "0.3.0 default"})
        self.assertEqual(rc, 0)
        cfg = self.read_config()
        self.assertEqual(cfg["beta"], "default",
                         "a key set back to the default must NOT be reclaimed")
        self.assertNotIn("beta", cfg[scaffold_install.SCAFFOLD_DEFAULTS_KEY]["owned"])

    def test_new_template_key_is_added_and_owned(self):
        self.config()
        tmpl = self.plugin / "templates" / scaffold_install.CONFIG_REL
        data = json.loads(tmpl.read_text(encoding="utf-8"))
        data["delta"] = "added in 0.2.0"
        tmpl.write_text(json.dumps(data), encoding="utf-8")

        rc, out = self.config()
        self.assertEqual(rc, 0)
        self.assertIn("+ delta", out)
        cfg = self.read_config()
        self.assertEqual(cfg["delta"], "added in 0.2.0")
        self.assertIn("delta", cfg[scaffold_install.SCAFFOLD_DEFAULTS_KEY]["owned"])

    def test_second_set_call_keeps_the_first_calls_detected_value(self):
        # gh#7 / e8g, planted as a gate: /scaffold-godot-harness calls `config`
        # more than once per run with different --set keys. The second call
        # proposes the shipped default for every key it was not passed, and used
        # to reset the value the first call had just detected. Same version, no
        # override -> a scaffold-owned key is kept, not reverted.
        self.config({"beta": "C:/detected/godot.exe"})
        rc, out = self.config({"alpha": 7})
        self.assertEqual(rc, 0)
        self.assertIn('= beta kept as "C:/detected/godot.exe" (scaffold-owned, not passed to this call)', out)
        cfg = self.read_config()
        self.assertEqual(cfg["beta"], "C:/detected/godot.exe",
                         "a second config call must not clobber the first call's --set")
        self.assertEqual(cfg["alpha"], 7)
        # Still scaffold-owned: an explicit later override may change it.
        self.assertIn("beta", cfg[scaffold_install.SCAFFOLD_DEFAULTS_KEY]["owned"])
        rc, out = self.config({"beta": "D:/other/godot.exe"})
        self.assertEqual(self.read_config()["beta"], "D:/other/godot.exe")

    def test_version_bump_updates_owned_defaults_but_never_to_empty(self):
        # A new harness version may ship a changed default (alpha 1 -> 2): an
        # owned key follows it. But a detected value must not be reset to the
        # empty shipped default just because the version moved.
        tmpl = self.plugin / "templates" / scaffold_install.CONFIG_REL
        data = json.loads(tmpl.read_text(encoding="utf-8"))
        data["path"] = ""
        tmpl.write_text(json.dumps(data), encoding="utf-8")
        self.config({"path": "C:/detected.exe"})

        data["alpha"] = 2
        tmpl.write_text(json.dumps(data), encoding="utf-8")
        (self.plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "synth", "version": "0.3.0"}), encoding="utf-8")
        rc, out = self.config()
        self.assertEqual(rc, 0)
        cfg = self.read_config()
        self.assertEqual(cfg["alpha"], 2, "an owned key follows a changed shipped default")
        self.assertEqual(cfg["path"], "C:/detected.exe",
                         "a version bump must not reset a detected value to the empty default")
        self.assertIn("^ alpha: 1 -> 2", out)

    def test_invalid_config_json_is_refused(self):
        path = self.project / scaffold_install.CONFIG_REL
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")
        with captured_stdout():
            rc = scaffold_install.patch_config(self.plugin, self.project, {})
        self.assertEqual(rc, 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "{ not json")

    # -- idempotence over the whole surface ------------------------------

    def test_double_run_is_idempotent(self):
        self.install()
        self.config({"beta": "detected"})
        before = tree_snapshot(self.project)
        self.install()
        self.config({"beta": "detected"})
        self.assertEqual(tree_snapshot(self.project), before,
                         "a second identical run must not change any byte")


class FormatBlockCase(unittest.TestCase):
    """format-block mode: refresh the log's Format section, never its entries."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="scaffmt-")
        base = Path(self._tmp.name)
        self.plugin = base / "plugin"
        (self.plugin / "templates").mkdir(parents=True)
        (self.plugin / "templates" / "log-devtools.md").write_text(
            "%s\n# Log\n\nNEW FORMAT v2\n%s\n\n<!-- Entries below. -->\n"
            % (scaffold_install.FORMAT_BEGIN, scaffold_install.FORMAT_END),
            encoding="utf-8")
        self.project = base / "project"
        self.project.mkdir()
        (self.project / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        self.log = self.project / "log-devtools.md"

    def tearDown(self):
        self._tmp.cleanup()

    def refresh(self):
        with captured_stdout() as buf:
            rc = scaffold_install.refresh_format_block(self.plugin, self.project)
        return rc, buf.getvalue()

    def test_marked_log_refreshes_format_and_keeps_entries(self):
        self.log.write_text(
            "%s\n# Log\n\nOLD FORMAT v1\n%s\n\n<!-- Entries below. -->\n"
            "\n## 2026-08-01 - a precious project entry\n\n- Value: **warranted**\n"
            % (scaffold_install.FORMAT_BEGIN, scaffold_install.FORMAT_END),
            encoding="utf-8")
        rc, out = self.refresh()
        self.assertEqual(rc, 0)
        self.assertIn("refreshed", out)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("NEW FORMAT v2", text)
        self.assertNotIn("OLD FORMAT v1", text)
        self.assertIn("a precious project entry", text)

    def test_marker_less_log_left_untouched(self):
        original = "# Pre-0.8.0 log\n\n## 2026-07-01 - entry\n"
        self.log.write_text(original, encoding="utf-8")
        rc, out = self.refresh()
        self.assertEqual(rc, 0)
        self.assertIn("left untouched", out)
        self.assertEqual(self.log.read_text(encoding="utf-8"), original)

    def test_absent_log_is_a_noop(self):
        rc, out = self.refresh()
        self.assertEqual(rc, 0)
        self.assertIn("absent", out)
        self.assertFalse(self.log.exists())

    def test_refresh_is_idempotent(self):
        self.log.write_text(
            "%s\nOLD\n%s\nentries\n"
            % (scaffold_install.FORMAT_BEGIN, scaffold_install.FORMAT_END),
            encoding="utf-8")
        self.refresh()
        first = self.log.read_text(encoding="utf-8")
        rc, out = self.refresh()
        self.assertEqual(rc, 0)
        self.assertIn("already current", out)
        self.assertEqual(self.log.read_text(encoding="utf-8"), first)


class RealPluginCase(unittest.TestCase):
    """One integration pass with the actual repo templates and history."""

    REAL_FILES = [
        "addons/godot_selftest/dev_tools.gd",
        "addons/godot_selftest/scene_validator.gd",
        "tools/lint_project.gd",
        "tools/run_tests.gd",
        "tools/devtools.py",
        "tools/check_devtools_log.py",
        "tools/upstream_gaps.py",
        "tools/verify_ledger.py",
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="scaffreal-")
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()
        (self.project / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_install_and_refresh_of_real_templates(self):
        with captured_stdout() as buf:
            rc = scaffold_install.install_files(REPO_ROOT, self.project, self.REAL_FILES)
        self.assertEqual(rc, 0, buf.getvalue())
        for rel in self.REAL_FILES:
            self.assertTrue((self.project / rel).is_file(), rel)
        with captured_stdout():
            rc2 = scaffold_install.patch_config(REPO_ROOT, self.project, {})
        self.assertEqual(rc2, 0)
        before = tree_snapshot(self.project)
        with captured_stdout():
            scaffold_install.install_files(REPO_ROOT, self.project, self.REAL_FILES)
            scaffold_install.patch_config(REPO_ROOT, self.project, {})
        self.assertEqual(tree_snapshot(self.project), before)
        self.assertEqual(sorted(p.name for p in self.project.rglob("*.bak")), [])


class FullInstallCase(unittest.TestCase):
    """`full` against the real templates: the one definition of installed (gh#9)."""

    PROJECT_GODOT = "\n".join([
        "config_version=5",
        "",
        "[application]",
        "",
        'config/name="game"',
        'run/main_scene="res://scenes/main.tscn"',
        "",
        "[autoload]",
        "",
        'GameState="*res://autoload/game_state.gd"',
        'Audio="*res://autoload/audio.gd"',
        "",
        "[display]",
        "",
        "window/size/viewport_width=800",
        "",
    ])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="scafffull-")
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()
        (self.project / "project.godot").write_text(self.PROJECT_GODOT, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def full(self, overrides=None, **kw):
        with captured_stdout() as buf:
            rc = scaffold_install.install_full(REPO_ROOT, self.project, overrides or {}, **kw)
        return rc, buf.getvalue()

    def test_full_installs_everything_and_devtools_autoload_is_last(self):
        rc, out = self.full(hook=True, hook_python="python")
        self.assertEqual(rc, 0, out)
        for rel in scaffold_install.SHIPPED_FILES:
            self.assertTrue((self.project / rel).is_file(), rel)
        self.assertTrue((self.project / "devtools_ext" / "commands.gd").is_file())
        self.assertTrue((self.project / "devtools_ext" / "commands.example.gd").is_file())
        self.assertTrue((self.project / "test" / "unit" / "test_selftest.gd").is_file())
        self.assertTrue((self.project / "test" / "sequences" / "smoke.json").is_file())
        self.assertTrue((self.project / "log-devtools.md").is_file())
        claude = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(scaffold_install.CLAUDE_BEGIN, claude)
        settings = json.loads((self.project / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertTrue(any(scaffold_install.HOOK_MARKER in h["command"]
                            for e in settings["hooks"]["Stop"] for h in e["hooks"]))
        cfg = json.loads((self.project / scaffold_install.CONFIG_REL).read_text(encoding="utf-8"))
        self.assertEqual(cfg["main_scene"], "res://scenes/main.tscn", "main_scene is detected")

        # THE rule (H-048): DevTools after every autoload the project declared,
        # inside [autoload], and nothing else in project.godot touched.
        text = (self.project / "project.godot").read_text(encoding="utf-8")
        lines = text.split("\n")
        i_auto = lines.index("[autoload]")
        i_disp = lines.index("[display]")
        i_dev = lines.index(scaffold_install.AUTOLOAD_LINE)
        i_game = lines.index('GameState="*res://autoload/game_state.gd"')
        i_audio = lines.index('Audio="*res://autoload/audio.gd"')
        self.assertTrue(i_auto < i_game < i_audio < i_dev < i_disp,
                        "DevTools must be LAST in [autoload]:\n" + text)
        self.assertIn('config/name="game"', text)
        self.assertIn("window/size/viewport_width=800", text)

    def test_full_without_autoload_section_creates_one(self):
        (self.project / "project.godot").write_text(
            'config_version=5\n\n[application]\n\nconfig/name="g"\n', encoding="utf-8")
        rc, out = self.full(hook=False)
        self.assertEqual(rc, 0, out)
        text = (self.project / "project.godot").read_text(encoding="utf-8")
        self.assertIn("[autoload]\n\n" + scaffold_install.AUTOLOAD_LINE, text)
        self.assertFalse((self.project / ".claude" / "settings.json").exists())

    def test_full_is_idempotent_and_never_clobbers_project_owned_files(self):
        self.full(hook=True, hook_python="python")
        # The project makes the files its own.
        ext = self.project / "devtools_ext" / "commands.gd"
        ext.write_text("# my verbs\n", encoding="utf-8")
        claude = self.project / "CLAUDE.md"
        claude.write_text("# My game\n\nrules\n\n" + claude.read_text(encoding="utf-8"), encoding="utf-8")
        (self.project / "test" / "unit" / "test_mine.gd").write_text("extends RefCounted\n", encoding="utf-8")
        log = self.project / "log-devtools.md"
        log.write_text(log.read_text(encoding="utf-8") + "\n## my entry\n- Gap: x\n", encoding="utf-8")
        before = tree_snapshot(self.project)

        rc, out = self.full(hook=True, hook_python="python")
        self.assertEqual(rc, 0, out)
        after = tree_snapshot(self.project)
        self.assertEqual(after, before, "a second full run must change no byte")
        self.assertEqual(ext.read_text(encoding="utf-8"), "# my verbs\n")
        self.assertTrue(claude.read_text(encoding="utf-8").startswith("# My game"))
        self.assertEqual(claude.read_text(encoding="utf-8").count(scaffold_install.CLAUDE_BEGIN), 1)
        self.assertIn("## my entry", log.read_text(encoding="utf-8"))
        text = (self.project / "project.godot").read_text(encoding="utf-8")
        self.assertEqual(text.count("DevTools="), 1)
        settings = json.loads((self.project / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(1 for e in settings["hooks"]["Stop"] for h in e["hooks"]
                             if scaffold_install.HOOK_MARKER in h["command"]), 1)

    def test_full_refuses_malformed_settings_json_and_leaves_it(self):
        settings = self.project / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("{ not json", encoding="utf-8")
        rc, out = self.full(hook=True, hook_python="python")
        self.assertEqual(rc, 1)
        self.assertEqual(settings.read_text(encoding="utf-8"), "{ not json")
        # Everything else still landed - one failed step does not abort the install.
        self.assertTrue((self.project / "tools" / "devtools.py").is_file())
        self.assertIn(scaffold_install.AUTOLOAD_LINE,
                      (self.project / "project.godot").read_text(encoding="utf-8"))

    def test_full_cli_round_trip(self):
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "scaffold_install.py"), "full",
             "--project", str(self.project), "--no-hook", "--set", "hud_layer_name=Hud"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        cfg = json.loads((self.project / scaffold_install.CONFIG_REL).read_text(encoding="utf-8"))
        self.assertEqual(cfg["hud_layer_name"], "Hud")
        self.assertEqual(cfg["main_scene"], "res://scenes/main.tscn")


class CliGuardCase(unittest.TestCase):
    def test_missing_project_godot_exits_1(self):
        with tempfile.TemporaryDirectory(prefix="scaffcli-") as tmp:
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "scaffold_install.py"),
                 "files", "tools/devtools.py", "--project", tmp],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("no project.godot", proc.stderr)


if __name__ == "__main__":
    unittest.main()
