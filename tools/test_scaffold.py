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
