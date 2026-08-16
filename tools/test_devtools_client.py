"""Engine-free unit tests for templates/tools/devtools.py's process bookkeeping.

These pin the parts of the client that had no test at all and were wrong for
years without anyone measuring them: `pid_alive` on Windows read a DEAD pid as
alive from the day it was written (H-055), and `quit` could only ever see the
one pid the owner file named while a wrapper sibling and an earlier abandoned
engine kept answering the bus (gh#14.1). Every case here plants the condition
with a real child process rather than asserting on a pid nothing owns.

Run: python -m unittest discover -s tools
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "templates" / "tools"))
import devtools  # noqa: E402


def _sleeper(seconds=30):
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(%d)" % seconds],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class PidLiveness(unittest.TestCase):
    def test_child_alive_then_dead(self):
        child = _sleeper()
        try:
            self.assertTrue(devtools.pid_alive(child.pid))
        finally:
            child.kill()
            child.wait()
        deadline = time.time() + 5
        while time.time() < deadline and devtools.pid_alive(child.pid):
            time.sleep(0.05)
        self.assertFalse(devtools.pid_alive(child.pid),
                         "a killed, reaped child still reads alive (H-055 regression)")

    def test_started_unix_is_now_for_a_fresh_child(self):
        child = _sleeper()
        try:
            started = devtools._pid_started_unix(child.pid)
            if started is None:
                self.skipTest("process start time not readable on this platform")
            self.assertLess(abs(started - time.time()), 10.0,
                            "creation time %r is not within 10s of now" % started)
        finally:
            child.kill()
            child.wait()


class LaunchLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="harness-ledger-"))
        (self.tmp / ".devtools").mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ledger(self):
        return devtools._launch_ledger_path(self.tmp)

    def test_live_child_is_a_verified_survivor_and_dead_one_is_not(self):
        live = _sleeper()
        dead = _sleeper()
        try:
            devtools._ledger_record(self.tmp, live.pid, "launcher")
            devtools._ledger_record(self.tmp, dead.pid, "engine")
            dead.kill()
            dead.wait()
            deadline = time.time() + 5
            while time.time() < deadline and devtools.pid_alive(dead.pid):
                time.sleep(0.05)
            rows = devtools._ledger_survivors(self.tmp)
            pids = [r["pid"] for r in rows]
            self.assertIn(live.pid, pids, "the live child was not reported as a survivor")
            self.assertNotIn(dead.pid, pids, "a dead pid was reported as a survivor")
            row = next(r for r in rows if r["pid"] == live.pid)
            if devtools._pid_started_unix(live.pid) is not None:
                self.assertTrue(row["verified"], "start-time verification did not hold "
                                "for a process we just started")
            self.assertEqual(row["role"], "launcher")
        finally:
            live.kill()
            live.wait()

    def test_recycled_pid_is_not_ours(self):
        """A ledger row whose recorded start time is far from the live process's
        creation time is a recycled pid: someone else's process. Never reported."""
        child = _sleeper()
        try:
            if devtools._pid_started_unix(child.pid) is None:
                self.skipTest("process start time not readable on this platform")
            self._ledger().write_text(json.dumps({
                "pid": child.pid, "role": "engine",
                "started_unix": time.time() - 3600, "started_verified": True,
            }) + "\n", encoding="utf-8")
            self.assertEqual(devtools._ledger_survivors(self.tmp), [],
                             "a pid whose creation time does not match the record was "
                             "reported as our survivor - that is how a stranger's process "
                             "gets killed")
        finally:
            child.kill()
            child.wait()

    def test_exclude_and_kill(self):
        a = _sleeper()
        b = _sleeper()
        try:
            devtools._ledger_record(self.tmp, a.pid, "launcher")
            devtools._ledger_record(self.tmp, b.pid, "engine")
            rows = devtools._ledger_survivors(self.tmp, exclude=(a.pid,))
            self.assertEqual([r["pid"] for r in rows], [b.pid])
            if not rows[0]["verified"]:
                self.skipTest("cannot verify start time here; auto-kill is disabled by design")
            gone = devtools._kill_survivors(rows)
            self.assertEqual(gone, [b.pid])
            b.wait(timeout=5)
            self.assertTrue(devtools.pid_alive(a.pid), "the excluded pid was killed too")
        finally:
            for c in (a, b):
                try:
                    c.kill()
                    c.wait(timeout=5)
                except Exception:
                    pass

    def test_kill_hint_names_both_shells_on_windows(self):
        hint = devtools._kill_hint([123, 456])
        self.assertIn("123", hint)
        if sys.platform == "win32":
            self.assertIn("Stop-Process", hint)
            self.assertIn("taskkill", hint)
            self.assertIn("F:/", hint, "the MSYS mangling symptom must be named (gh#12)")
        else:
            self.assertIn("kill -9", hint)


class VerbArgScan(unittest.TestCase):
    def test_keys_are_read_off_the_handler_body(self):
        src = (
            'func _ready() -> void:\n'
            '\tDevTools.register_command("place_plant", _cmd_place_plant)\n'
            '\tDevTools.register_command("reset", Callable(self, "_cmd_reset"))\n'
            '\n'
            '## args["decoy"] in a doc comment is not a read\n'
            'func _cmd_place_plant(args: Dictionary) -> Dictionary:\n'
            '\tvar id := StringName(str(args.get("plant", "corn")))\n'
            '\tvar cell := Vector2i(int(args.get("x", 0)), int(args.get("y", 0)))\n'
            '\tif args.has("force"):\n'
            '\t\tpass\n'
            '\treturn {}\n'
            '\n'
            'func _cmd_reset(_a: Dictionary) -> Dictionary:\n'
            '\treturn {"success": true}\n'
        )
        got = devtools._scan_verb_args(src)
        self.assertEqual(got["place_plant"], ["plant", "x", "y", "force"])
        self.assertEqual(got["reset"], [])


if __name__ == "__main__":
    unittest.main()
