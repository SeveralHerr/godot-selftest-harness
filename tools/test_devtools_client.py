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
import run_tests as run_tests_py  # noqa: E402
import import_check  # noqa: E402
import verify_ledger  # noqa: E402
import contextlib, io


@contextlib.contextmanager
def captured_stderr():
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = old



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

    def test_always_on_snapshot_is_a_recovery_point_not_an_auto_revert(self):
        # plant-tower-defense:G-050: taken without --snapshot-userstate -> quit must
        # NOT put it back on its own, but restore-userstate (force) must.
        devtools.userstate_snapshot(self.project, self.user_dir, ["*.save"], restore_on_quit=False)
        (self.user_dir / "highscore.save").write_text("36074", encoding="utf-8")
        self.assertIsNone(devtools.userstate_restore(self.project, "quit"),
                          "an unarmed snapshot must not revert a legitimate run")
        self.assertEqual((self.user_dir / "highscore.save").read_text(encoding="utf-8"), "36074")
        self.assertIsNotNone(devtools.userstate_restore(self.project, "restore-userstate", force=True))
        self.assertEqual((self.user_dir / "highscore.save").read_text(encoding="utf-8"), "orig")

    def test_previous_snapshot_is_kept_beside_the_current_one(self):
        devtools.userstate_snapshot(self.project, self.user_dir, ["*.save"], restore_on_quit=False)
        (self.user_dir / "highscore.save").write_text("second-launch", encoding="utf-8")
        devtools.userstate_snapshot(self.project, self.user_dir, ["*.save"], restore_on_quit=False)
        prev = devtools._userstate_dir(self.project).with_name(devtools.USERSTATE_DIR + "_prev")
        self.assertEqual((prev / "highscore.save").read_text(encoding="utf-8"), "orig")
        self.assertEqual((devtools._userstate_dir(self.project) / "highscore.save").read_text(encoding="utf-8"),
                         "second-launch")


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


class SharedUserDirWarningCase(unittest.TestCase):
    """H-067: launch names a user:// last used by a game from another checkout."""

    def _run(self, owner_project, my_project):
        with tempfile.TemporaryDirectory(prefix="shared-") as td:
            user_dir = Path(td) / "user"
            user_dir.mkdir()
            devtools._owner_file_path(user_dir).write_text(
                json.dumps({"pid": 1, "project_path": owner_project}), encoding="utf-8")
            with captured_stderr() as buf:
                devtools._warn_shared_user_dir(user_dir, Path(my_project))
            return buf.getvalue()

    def test_other_checkout_is_named(self):
        out = self._run(r"C:/games/plant-tower-defense/", r"C:/tmp/plantcopy")
        self.assertIn("DIFFERENT checkout", out)
        self.assertIn("use_custom_user_dir=true", out)

    def test_same_checkout_is_silent(self):
        self.assertEqual(self._run(r"C:/games/plant/", r"C:\games\plant"), "")


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


class MixedRunsCase(unittest.TestCase):
    """plant-tower-defense:G-051b: one results file, two runs."""
    ONE = ("  Run: 7f3a1c pid 100 started\n[PASS] a\n"
           "  Total: 519  |  Passed: 519  |  Failed: 0  |  Skipped: 0\n"
           "  Run: 7f3a1c pid 100 finished\n")
    OTHER = ("  Run: 0b22e9 pid 200 started\n[FAIL] b\n"
             "  Total: 516  |  Passed: 513  |  Failed: 3  |  Skipped: 0\n"
             "  Run: 0b22e9 pid 200 finished\n")

    def test_single_run_is_not_mixed(self):
        self.assertEqual(run_tests_py.mixed_runs(self.ONE), "")

    def test_two_nonces_refused_naming_both(self):
        msg = run_tests_py.mixed_runs(self.ONE + self.OTHER)
        self.assertIn("2 distinct runs", msg)
        self.assertIn("7f3a1c pid 100", msg)
        self.assertIn("0b22e9 pid 200", msg)
        self.assertIn("MIXTURE", msg)

    def test_interleaved_output_still_counted_by_nonce(self):
        # A surviving process appends mid-file, not neatly after.
        text = self.ONE.replace("[PASS] a\n", "[PASS] a\n" + self.OTHER)
        self.assertIn("2 distinct runs", run_tests_py.mixed_runs(text))

    def test_pre_nonce_runner_two_totals_refused(self):
        # A half-refreshed install: old runner (no Run: line), two Total: lines.
        strip = lambda t: "\n".join(l for l in t.splitlines() if "Run:" not in l) + "\n"
        self.assertIn("2 `Total:` lines", run_tests_py.mixed_runs(strip(self.ONE) + strip(self.OTHER)))
        self.assertEqual(run_tests_py.mixed_runs(strip(self.ONE)), "")


class ImportTmpSweepCase(unittest.TestCase):
    """plant-tower-defense:G-044 (7th): a crashed --import strands <asset>.import*.tmp."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / "assets" / "audio").mkdir(parents=True)
        (self.project / ".godot" / "imported").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sweeps_only_import_temporaries_outside_dot_godot(self):
        keep = [self.project / "assets" / "audio" / "q.ogg",
                self.project / "assets" / "audio" / "q.ogg.import",
                self.project / "assets" / "notes.tmp",
                self.project / ".godot" / "imported" / "q.ogg-abc.sample.tmp"]
        gone = [self.project / "assets" / "audio" / "q.ogg.import.tmp",
                self.project / "assets" / "audio" / "r.png.import-1234.TMP"]
        for f in keep + gone:
            f.write_text("x", encoding="utf-8")
        swept = import_check.sweep_import_tmp(self.project)
        self.assertEqual(sorted(swept), ["assets/audio/q.ogg.import.tmp",
                                         "assets/audio/r.png.import-1234.TMP"])
        for f in keep:
            self.assertTrue(f.exists(), f)
        for f in gone:
            self.assertFalse(f.exists(), f)

    def test_clean_tree_sweeps_nothing(self):
        (self.project / "assets" / "audio" / "q.ogg.import").write_text("x", encoding="utf-8")
        self.assertEqual(import_check.sweep_import_tmp(self.project), [])


class UserstateRewrittenIdenticallyCase(unittest.TestCase):
    """gh#39: `content changed` vs `rewritten identically` (mtime moved, bytes same)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".devtools").mkdir(parents=True)
        self.user_dir = Path(self._tmp.name) / "user"
        self.user_dir.mkdir()
        (self.user_dir / "highscore.save").write_text("v6 308 5008", encoding="utf-8")
        (self.user_dir / "keys.save").write_text("m0", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_identical_rewrite_is_named_as_such_and_real_change_is_not(self):
        devtools.userstate_stat_take(self.project, self.user_dir)
        f = self.user_dir / "highscore.save"
        f.write_text("v6 308 5008", encoding="utf-8")   # same bytes, new mtime
        os.utime(f, (time.time() + 5, time.time() + 5))
        (self.user_dir / "keys.save").write_text("m0 cb1", encoding="utf-8")
        changed, created, deleted, _ = devtools.userstate_stat_diff(self.project)
        self.assertEqual(created, [])
        self.assertEqual(deleted, [])
        self.assertEqual(len(changed), 2, changed)
        self.assertTrue(changed[0].startswith("highscore.save (rewritten identically"), changed)
        self.assertEqual(changed[1], "keys.save")


class UserstateUnmatchedAndBridgeOwnedCase(unittest.TestCase):
    """moving-in:G-063 / plant G-054 (2nd): the patterns' blind spots are named, the
    bridge's own files are never snapshotted or removed, and the survivor path keeps
    the snapshot instead of silently dropping the restore."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        (self.project / ".devtools").mkdir(parents=True)
        self.user_dir = Path(self._tmp.name) / "user"
        self.user_dir.mkdir()
        (self.user_dir / "highscore.save").write_text("orig", encoding="utf-8")
        (self.user_dir / "settings.cfg").write_text("[a]\nx=1", encoding="utf-8")
        (self.user_dir / "ui_findings_baseline.json").write_text("{}", encoding="utf-8")
        (self.user_dir / "devtools_owner.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_unmatched_names_are_listed_against_the_patterns(self):
        self.assertEqual(devtools.userstate_unmatched(
            ["settings.cfg", "highscore.save (rewritten identically - x)", "keys.save"], ["*.save"]),
            ["settings.cfg"])
        self.assertEqual(devtools.userstate_unmatched(["settings.cfg"], devtools.USERSTATE_DEFAULT_GLOBS), [])

    def test_default_globs_cover_cfg_but_never_bridge_files(self):
        m = devtools.userstate_snapshot(self.project, self.user_dir, devtools.USERSTATE_DEFAULT_GLOBS)
        self.assertEqual(sorted(m["files"]), ["highscore.save", "settings.cfg"])
        # a baseline written mid-run must survive quit's "remove created" sweep
        (self.user_dir / "signal_findings_baseline.json").write_text("{}", encoding="utf-8")
        (self.user_dir / "run_created.json").write_text("{}", encoding="utf-8")
        devtools.userstate_restore(self.project, "quit")
        self.assertTrue((self.user_dir / "signal_findings_baseline.json").exists())
        self.assertFalse((self.user_dir / "run_created.json").exists())

    def test_survivor_keeps_snapshot_and_gone_restores(self):
        devtools.userstate_snapshot(self.project, self.user_dir, ["*.save"])
        (self.user_dir / "highscore.save").write_text("36074", encoding="utf-8")
        with captured_stderr() as err:
            devtools._quit_userstate_finish(self.project, [4242])
        self.assertIn("KEPT", err.getvalue())
        self.assertIn("4242", err.getvalue())
        self.assertEqual((self.user_dir / "highscore.save").read_text(encoding="utf-8"), "36074")
        self.assertTrue((devtools._userstate_dir(self.project) / devtools.USERSTATE_MANIFEST).exists())
        devtools._quit_userstate_finish(self.project, [])
        self.assertEqual((self.user_dir / "highscore.save").read_text(encoding="utf-8"), "orig")

    def test_quit_with_no_snapshot_says_so(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            devtools.userstate_restore(self.project, "quit")
        self.assertIn("no snapshot to restore", buf.getvalue())


class ChangedFunctionsCase(unittest.TestCase):
    """gh#38 / moving-in:G-060: the changed functions inside a reached file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)
        self.src = self.root / "unpack_ui.gd"
        self.src.write_text(
            "extends Control\n\nconst BUDGET := 12\nvar _search_left := BUDGET\n\n"
            "func _ready() -> void:\n\tpass\n\n"
            "func _process(_d: float) -> void:\n\tif _search_left > 0:\n\t\t_search_left -= 1\n\n"
            "static func helper() -> int:\n\treturn 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_names_only_the_functions_whose_lines_changed(self):
        text = self.src.read_text(encoding="utf-8")
        text = text.replace("_search_left -= 1", "_search_left -= 2")   # inside _process
        text = text.replace("const BUDGET := 12", "const BUDGET := 6")  # top-level
        self.src.write_text(text, encoding="utf-8")
        got = verify_ledger.changed_functions(self.root, {"unpack_ui.gd"})
        self.assertEqual(got, {"unpack_ui.gd": ["<top-level>", "_process"]})

    def test_unchanged_file_is_absent_and_untracked_file_lists_every_func(self):
        self.assertEqual(verify_ledger.changed_functions(self.root, {"unpack_ui.gd"}), {})
        new = self.root / "fresh.gd"
        new.write_text("extends Node\n\nfunc a() -> void:\n\tpass\n\nfunc b() -> void:\n\tpass\n",
                       encoding="utf-8")
        self.assertEqual(verify_ledger.changed_functions(self.root, {"fresh.gd"}), {"fresh.gd": ["a", "b"]})


if __name__ == "__main__":
    unittest.main()
