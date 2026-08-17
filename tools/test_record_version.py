#!/usr/bin/env python3
"""tools/record_version.py --check's git awareness (bead 1kh).

--check once printed `version check OK: 0.18.0 stamped in 12 shipped file(s)` while
HEAD was 0.17.0 and 25 files were dirty: every stamp, hash and doc agreed with each
other and with nothing in git history. Both states are planted here (H-035): a tree
whose HEAD ships an older version must WARN; a clean tree at the recorded version
must stay quiet; a tree without git must stay quiet too (stated, never gated).

Usage:
    python tools/test_record_version.py
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import record_version  # noqa: E402


class _Result:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _fake_git(head_version, dirty_lines):
    def run(cmd):
        if cmd[:2] == ["git", "status"]:
            return _Result(0, "\n".join(dirty_lines) + ("\n" if dirty_lines else ""))
        if cmd[:2] == ["git", "show"]:
            return _Result(0, json.dumps({"version": head_version}))
        raise AssertionError("unexpected git call %r" % (cmd,))
    return run


class GitReleaseState(unittest.TestCase):
    def test_cut_but_uncommitted_release_warns_naming_both_versions(self):
        line = record_version.git_release_state(
            "0.18.0", run=_fake_git("0.17.0", [" M templates/tools/devtools.py"] * 25))
        self.assertTrue(line.startswith("WARNING:"), line)
        self.assertIn("0.18.0", line)
        self.assertIn("HEAD ships 0.17.0", line)
        self.assertIn("25 tracked file(s)", line)

    def test_clean_tree_at_recorded_version_is_quiet(self):
        self.assertEqual(record_version.git_release_state("0.18.0", run=_fake_git("0.18.0", [])), "")

    def test_dirty_tree_at_committed_version_is_quiet(self):
        # Ordinary post-release editing (a log entry, a doc fix) - not the defect.
        self.assertEqual(record_version.git_release_state(
            "0.18.0", run=_fake_git("0.18.0", [" M log-devtools.md"])), "")

    def test_no_git_is_quiet_not_fatal(self):
        def run(cmd):
            raise OSError("no git binary")
        self.assertEqual(record_version.git_release_state("0.18.0", run=run), "")
        self.assertEqual(record_version.git_release_state(
            "0.18.0", run=lambda cmd: _Result(128, "")), "")

    def test_real_git_answers_for_this_checkout(self):
        # Whatever this checkout's state is, the real path must return a str, not raise.
        line = record_version.git_release_state(record_version.plugin_version())
        self.assertIsInstance(line, str)
        head = subprocess.run(["git", "show", "HEAD:.claude-plugin/plugin.json"],
                              cwd=str(REPO_ROOT), capture_output=True, text=True)
        if head.returncode == 0:
            head_v = json.loads(head.stdout)["version"]
            if head_v != record_version.plugin_version():
                self.assertIn("HEAD ships %s" % head_v, line)


if __name__ == "__main__":
    unittest.main()
