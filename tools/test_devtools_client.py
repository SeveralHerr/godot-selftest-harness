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


class ForeignProjectOwner(unittest.TestCase):
    """plant-tower-defense:G-018: a live owner from a sibling git worktree shares
    the project name, user:// and bus; only its project_path differs."""

    def _owner(self, project_path, alive=True, polling=True):
        child = _sleeper()
        self.addCleanup(lambda: (child.kill(), child.wait()))
        return {"present": True, "pid": child.pid if alive else 999999999,
                "alive": alive, "polling": polling, "poll_age": 0.5,
                "path": Path("owner.json"), "project_path": project_path}

    def test_same_dir_spellings_are_not_foreign(self):
        here = Path(__file__).resolve().parent
        for spelling in (str(here), str(here).replace(os.sep, "/") + "/",
                         str(here).upper() if sys.platform == "win32" else str(here)):
            self.assertIsNone(devtools.foreign_project_owner(self._owner(spelling), here),
                              "spelling %r read as a different checkout" % spelling)

    def test_other_checkout_live_and_polling_is_foreign(self):
        here = Path(__file__).resolve().parent
        other = str(here.parent / "worktree-of-this")
        self.assertEqual(devtools.foreign_project_owner(self._owner(other), here), other)

    def test_dead_or_stale_or_legacy_owner_is_left_to_liveness_logic(self):
        here = Path(__file__).resolve().parent
        other = str(here.parent / "worktree-of-this")
        self.assertIsNone(devtools.foreign_project_owner(self._owner(other, alive=False), here))
        self.assertIsNone(devtools.foreign_project_owner(self._owner(other, polling=False), here))
        legacy = self._owner(None)
        self.assertIsNone(devtools.foreign_project_owner(legacy, here),
                          "an owner file written before 0.22.0 has no project_path and proves nothing")

    def test_send_command_refuses_before_writing(self):
        """The refusal must happen BEFORE the command file is written - sending
        the verb to the foreign game is the harm."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "mine"
            project.mkdir()
            (project / "project.godot").write_text(
                'config_version=5\n[application]\nconfig/name="ForeignTest"\n', encoding="utf-8")
            udir = Path(tmp) / "userdata"
            udir.mkdir()
            child = _sleeper()
            self.addCleanup(lambda: (child.kill(), child.wait()))
            (udir / "devtools_owner.json").write_text(json.dumps({
                "pid": child.pid, "start_unix": 1.0, "last_poll_unix": time.time(),
                "project": "ForeignTest", "project_path": str(Path(tmp) / "theirs") + "/",
            }), encoding="utf-8")
            old = devtools._USERDATA_OVERRIDE if hasattr(devtools, "_USERDATA_OVERRIDE") else None
            try:
                devtools._USERDATA_OVERRIDE = str(udir)
                with self.assertRaises(devtools.ForeignInstanceError) as ctx:
                    devtools.send_command(project, "ping", {}, timeout=1.0)
                self.assertIn("DIFFERENT checkout", str(ctx.exception))
                self.assertFalse((udir / "devtools_commands.json").exists(),
                                 "the command was written despite the foreign owner")
            finally:
                devtools._USERDATA_OVERRIDE = old


class UserstateSnapshotCase(unittest.TestCase):
    """plant-tower-defense:G-047: launch --snapshot-userstate / quit restore."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="userstate-")
        root = Path(self._tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.user_dir = root / "user"
        self.user_dir.mkdir()
        (self.user_dir / "highscore.save").write_text("orig", encoding="utf-8")
        (self.user_dir / "keep.cfg").write_text("cfg", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_restores_and_removes_created(self):
        m = devtools.userstate_snapshot(self.project, self.user_dir, ["*.save"])
        self.assertEqual(m["files"], ["highscore.save"])
        # the run mutates one and creates one
        (self.user_dir / "highscore.save").write_text("mutated", encoding="utf-8")
        (self.user_dir / "new.save").write_text("created", encoding="utf-8")
        (self.user_dir / "keep.cfg").write_text("cfg2", encoding="utf-8")  # not snapshotted
        r = devtools.userstate_restore(self.project, "test")
        self.assertIsNotNone(r)
        self.assertEqual((self.user_dir / "highscore.save").read_text(encoding="utf-8"), "orig")
        self.assertFalse((self.user_dir / "new.save").exists(), "created file must be removed")
        self.assertEqual((self.user_dir / "keep.cfg").read_text(encoding="utf-8"), "cfg2",
                         "a file outside the patterns is never touched")
        self.assertFalse(devtools._userstate_dir(self.project).exists(), "snapshot consumed")
        self.assertIsNone(devtools.userstate_restore(self.project, "again"), "idempotent")

    def test_no_snapshot_is_a_quiet_none(self):
        self.assertIsNone(devtools.userstate_restore(self.project, "nothing"))


class UserstateStatCase(unittest.TestCase):
    """gh#33 (a): quit names which user:// files a run wrote, always on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ustat-")
        root = Path(self._tmp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.user_dir = root / "user"
        self.user_dir.mkdir()
        (self.user_dir / "highscore.save").write_text("orig", encoding="utf-8")
        (self.user_dir / "settings.cfg").write_text("cfg", encoding="utf-8")
        (self.user_dir / "devtools_owner.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_diff_names_changed_created_deleted_and_ignores_bridge_files(self):
        self.assertEqual(devtools.userstate_stat_take(self.project, self.user_dir), 3)
        time.sleep(0.05)
        (self.user_dir / "highscore.save").write_text("mutated!", encoding="utf-8")
        (self.user_dir / "new.save").write_text("x", encoding="utf-8")
        (self.user_dir / "settings.cfg").unlink()
        (self.user_dir / "devtools_owner.json").write_text('{"pid": 1}', encoding="utf-8")
        changed, created, deleted, user_dir = devtools.userstate_stat_diff(self.project)
        self.assertEqual(changed, ["highscore.save"])
        self.assertEqual(created, ["new.save"])
        self.assertEqual(deleted, ["settings.cfg"])
        self.assertEqual(user_dir, self.user_dir)
        self.assertIsNone(devtools.userstate_stat_diff(self.project), "record is consumed")

    def test_untouched_run_reports_nothing_changed(self):
        devtools.userstate_stat_take(self.project, self.user_dir)
        changed, created, deleted, _ = devtools.userstate_stat_diff(self.project)
        self.assertEqual((changed, created, deleted), ([], [], []))


class SceneTreeCountCase(unittest.TestCase):
    """moving-in:G-056: the trailing N node(s) line counts nodes, not JSON lines."""

    def test_counts_nodes_not_lines(self):
        data = {"root": {"name": "Sfx", "path": "/root/Sfx", "type": "Node",
                         "children": [
                             {"name": "SfxAmbient_rain", "path": "/root/Sfx/SfxAmbient_rain",
                              "type": "AudioStreamPlayer", "children": []},
                             {"name": "Other", "path": "/root/Sfx/Other", "children": []}]}}
        self.assertEqual(devtools._count_tree_nodes(data), 3)
        # a grep -ci ambient over the JSON would say 2 for the single node above
        text = json.dumps(data, indent=2)
        self.assertEqual(sum(1 for l in text.splitlines() if "ambient" in l.lower()), 2)


if __name__ == "__main__":
    unittest.main()
