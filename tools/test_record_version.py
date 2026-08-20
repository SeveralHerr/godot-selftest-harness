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
import tempfile
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


class ClosuresAreEvidenced(unittest.TestCase):
    """H-020: a gap closed in THIS version must name what proves it."""

    def _write(self, body):
        d = tempfile.mkdtemp()
        p = Path(d) / "log-devtools.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_unevidenced_close_in_this_version_is_a_problem(self):
        log = self._write(
            "  - [H-999] status: fixed | fixed-in: 0.62.0 | seen: 1\n")
        problems, (n, n_prose) = record_version.check_closures_are_evidenced("0.62.0", log)
        self.assertEqual((n, n_prose), (1, 0))
        self.assertEqual(len(problems), 1)
        self.assertIn("H-999", problems[0])
        self.assertIn("verified-by", problems[0])

    def test_evidenced_close_passes_and_is_counted(self):
        log = self._write(
            "  - [H-998] status: fixed | fixed-in: 0.62.0 | verified-by: stage 6 row "
            "what_drew | seen: 1\n")
        problems, (n, n_prose) = record_version.check_closures_are_evidenced("0.62.0", log)
        self.assertEqual((problems, n, n_prose), ([], 1, 0))

    def test_older_closures_are_not_retrofitted(self):
        """The history is what it is; demanding evidence for it would invent it."""
        log = self._write("  - [H-997] status: fixed | fixed-in: 0.55.0 | seen: 1\n")
        problems, (n, n_prose) = record_version.check_closures_are_evidenced("0.62.0", log)
        self.assertEqual((problems, n, n_prose), ([], 0, 0))

    def test_status_resolves_from_the_last_line_not_every_line(self):
        """The log is append-only: `open` -> `open` -> `fixed` on separate lines. A
        per-line scan reads every fixed gap as open forever - the gh#63 defect."""
        log = self._write(
            "  - [H-996] status: open | seen: 1\n"
            "  - [H-996] status: open | seen: 2\n"
            "  - [H-996] status: fixed | fixed-in: 0.62.0 | seen: 2\n")
        problems, (n, _p) = record_version.check_closures_are_evidenced("0.62.0", log)
        self.assertEqual(n, 1)
        self.assertIn("H-996", problems[0])

    def test_a_reopened_gap_is_not_counted_as_closed(self):
        """The inverse: last line wins in BOTH directions."""
        log = self._write(
            "  - [H-995] status: fixed | fixed-in: 0.62.0 | seen: 1\n"
            "  - [H-995] status: open | seen: 2 | note: reopened, the fix was partial\n")
        problems, (n, n_prose) = record_version.check_closures_are_evidenced("0.62.0", log)
        self.assertEqual((problems, n, n_prose), ([], 0, 0))

    def test_a_missing_log_is_a_problem_not_a_silent_zero(self):
        problems, _counts = record_version.check_closures_are_evidenced(
            "0.62.0", Path(tempfile.mkdtemp()) / "nope.md")
        self.assertEqual(len(problems), 1)
        self.assertIn("missing", problems[0])

    def test_prose_only_closures_are_counted_separately(self):
        """H-077: 'no mechanical check exists' is legal for a documentation fix and is
        unfalsifiable, so the ratio is the only thing that can show the field decaying
        into a spelling exercise. No single entry can."""
        log = self._write(
            "  - [H-990] status: fixed | fixed-in: 0.63.0 | verified-by: prose only - no "
            "mechanical check exists | seen: 1\n"
            "  - [H-991] status: fixed | fixed-in: 0.63.0 | verified-by: stage 5 control "
            "| seen: 1\n")
        problems, (n, n_prose) = record_version.check_closures_are_evidenced("0.63.0", log)
        self.assertEqual((problems, n, n_prose), ([], 2, 1))


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


class DocLinks(unittest.TestCase):
    """H-036: a relative link to a file that does not exist is named; fences and URLs skipped."""

    def test_planted_dead_link_is_named_and_live_ones_are_not(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "REFERENCE.md").write_text("ok\n", encoding="utf-8")
            (root / "README.md").write_text(
                "See [ref](REFERENCE.md) and [gone](MISSING.md) and [web](https://x.y/z).\n"
                "```\n[example](nope.md)\n```\n", encoding="utf-8")
            problems, checked = record_version.check_doc_links(root, ["README.md"])
        self.assertEqual(checked, 2)
        self.assertEqual(problems, ["README.md: link target MISSING.md does not exist"])


if __name__ == "__main__":
    unittest.main()
