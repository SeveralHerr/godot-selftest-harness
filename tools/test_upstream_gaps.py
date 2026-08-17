"""Tests for tools/upstream_gaps.py (the template under templates/tools/ is the
source of truth; tools/upstream_gaps.py is its byte-identical mirror, and
record_version.py --check enforces that).

Two behaviours, each of which shipped wrong once:

* H-063 (0.31.0): a `- Gap: **no new gap.**` bullet was pooled upstream as an
  OPEN `auto-<hash>` gap because the absence-marker pattern only knew the
  spelling `no gaps this turn`.
* H-059 (0.22.0): two DIFFERENT gaps sharing one id in a source log pooled as
  one, and the second was silently the one that was not there.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upstream_gaps  # noqa: E402


def _gap(idn, text, seen=1, status="open"):
    return (
        "- Gap: **%s**\n"
        "  evidence line\n"
        "  - [%s] status: %s | seen: %d | harness: 0.31.0\n"
        "  - Improvement: something.\n" % (text, idn, status, seen)
    )


class NoGapMarkers(unittest.TestCase):
    def test_every_absence_spelling_is_skipped(self):
        spellings = [
            "- Gap: no gaps this turn.",
            "- Gap: **no new gap.** The harness finding went upstream as a comment.",
            "- Gap: **No new gaps** - the two verbs used answered cleanly.",
            "- Gap: none this turn; every verb answered.",
            "- Gap (harness): nothing new to report.",
            "- Gap: _no harness gaps_ this session.",
        ]
        text = "## 2026-08-16 - t\n\n" + "\n\n".join(spellings) + "\n\n" + _gap("G-001", "real")
        gaps = upstream_gaps.parse_gaps(text)
        self.assertEqual([g["id"] for g in gaps], ["G-001"], [g["lines"][0] for g in gaps])

    def test_a_real_gap_starting_with_no_is_kept(self):
        # "no way to ..." / "no verb ..." are the commonest real gap openings.
        text = "## 2026-08-16 - t\n\n" + _gap("G-002", "no way to aim the camera at a node") \
            + "\n" + _gap("G-003", "no verb reports what the ray hit") \
            + "\n" + _gap("G-004", "None of the three gates caught it")
        gaps = upstream_gaps.parse_gaps(text)
        self.assertEqual([g["id"] for g in gaps], ["G-002", "G-003", "G-004"])


class DuplicateSourceIds(unittest.TestCase):
    def _run(self, source_text):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "log-devtools.md"
            dst = Path(td) / "dest.md"
            src.write_text(source_text, encoding="utf-8")
            dst.write_text("# dest\n", encoding="utf-8")
            r = upstream_gaps.upstream(src, dst, "proj", False, False)
            return r, dst.read_text(encoding="utf-8")

    def test_collision_is_suffixed_not_dropped(self):
        text = "## 2026-08-16 - t\n\n" + _gap("G-027", "reach grades the dirty set") \
            + "\n" + _gap("G-027", "a first-frame verb")
        r, dest = self._run(text)
        self.assertEqual(r["appended"], ["proj:G-027", "proj:G-027b"])
        self.assertIn("a first-frame verb", dest)
        self.assertEqual(len(r["suffixed"]), 1, r["suffixed"])

    def test_recurrence_collapses_and_bumps_seen(self):
        text = "## 2026-08-16 - t\n\n" + _gap("G-033", "clip_text min size") \
            + "\n" + _gap("G-033", "[G-033] seen: 2 - bit again", seen=2)
        r, dest = self._run(text)
        self.assertEqual(r["appended"], ["proj:G-033"])
        self.assertEqual(r["suffixed"], [])
        self.assertIn("seen: 2", dest)
        self.assertNotIn("bit again", dest)


class FiledUpstreamCrossRef(unittest.TestCase):
    """H-044: a gap the project filed upstream arrives naming the issue it duplicates."""

    def test_filed_upstream_becomes_dup_of(self):
        text = ("## 2026-08-16 - t\n\n- Gap: **bridge wrote the real save**\n  evidence\n"
                "  - [G-054] status: open | seen: 2 | harness: 0.38.0 | filed upstream: gh#40\n"
                "  - Improvement: default it.\n")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "log-devtools.md"
            dst = Path(td) / "dest.md"
            src.write_text(text, encoding="utf-8")
            dst.write_text("# dest\n", encoding="utf-8")
            upstream_gaps.upstream(src, dst, "plant", False, False)
            out = dst.read_text(encoding="utf-8")
        self.assertIn("[plant:G-054] status: open | seen: 2 | harness: 0.38.0 | source: plant", out)
        self.assertIn("| dup-of: gh#40", out)

    def test_no_reference_no_dup_of(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "log-devtools.md"
            dst = Path(td) / "dest.md"
            src.write_text("## t\n\n" + _gap("G-001", "plain"), encoding="utf-8")
            dst.write_text("# dest\n", encoding="utf-8")
            upstream_gaps.upstream(src, dst, "plant", False, False)
            self.assertNotIn("dup-of", dst.read_text(encoding="utf-8"))


class GapsBySource(unittest.TestCase):
    """H-028: the concentration of open gaps by project is surfaced, not implied."""

    def test_counts_open_only_and_names_harness_native(self):
        text = ("- [gather:G-001] status: open | seen: 1\n"
                "- [gather:G-002] status: fixed | fixed-in: 0.9.0 | seen: 1\n"
                "- [gather:G-003] status: open | seen: 3\n"
                "- [plant:G-050] status: open | seen: 1\n"
                "- [H-068] status: open | seen: 1 | harness: 0.39.0\n"
                "- [H-001] status: wontfix | seen: 1\n")
        self.assertEqual(upstream_gaps.gaps_by_source(text), {"gather": 2, "plant": 1, "harness": 1})


class TitleEmbeddedIds(unittest.TestCase):
    """gh#47.2 / moving-in:G-065: a repeat sighting carries its id in the Gap title and
    its fields on the wrapped paragraph; that must not mint an auto- id."""
    TEXT = ("## 2026-08-17 - t\n\n"
            "- Gap: **[G-025] every engine-side gate claims the bus** - status: fixed (RECONCILED\n"
            "  cycle 62) | **seen: 3** | harness: 0.16.0. Bit again this turn.\n"
            "  - Improvement: unchanged.\n\n"
            "- Gap: **[G-044] again** - status: open | seen: 2 | harness: 0.21.0\n"
            "  - Improvement: still.\n\n"
            "- Gap: **`aabb` reports nothing for lights** - no id anywhere.\n"
            "  - Improvement: mint one.\n")

    def test_title_id_and_inline_status_are_read(self):
        gaps = {g["id"]: g for g in upstream_gaps.parse_gaps(self.TEXT)}
        self.assertIn("G-025", gaps)
        self.assertEqual(gaps["G-025"]["fields"]["status"], "fixed")
        self.assertEqual(gaps["G-025"]["fields"]["seen"], "3")
        self.assertIn("G-044", gaps)
        self.assertEqual(gaps["G-044"]["fields"]["status"], "open")
        self.assertEqual(gaps["G-044"]["fields"]["seen"], "2")
        minted = [g for g in gaps if g.startswith("auto-")]
        self.assertEqual(len(minted), 1, gaps.keys())

    def test_upstream_skips_the_fixed_sighting_and_names_the_title_read(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "log-devtools.md"
            dst = Path(td) / "dest.md"
            src.write_text(self.TEXT, encoding="utf-8")
            dst.write_text("# dest\n", encoding="utf-8")
            r = upstream_gaps.upstream(src, dst, "moving-in", False, False)
            out = dst.read_text(encoding="utf-8")
        self.assertTrue(any(a.startswith("moving-in:G-044 (id read from the Gap title)") for a in r["appended"]), r)
        self.assertTrue(any("G-025" in sk for sk in r["skipped"]), r["skipped"])
        self.assertIn("[moving-in:G-044] status: open | seen: 2 | harness: 0.21.0 | source: moving-in", out)
        self.assertNotIn("moving-in:auto-", out.split("aabb")[0])


class Triage(unittest.TestCase):
    """H-069: the pooled log's open set is listed by age, and only explicit ids move."""
    TEXT = ("- [gather:G-001] status: open | seen: 1 | harness: 0.8.0\n"
            "- [gather:G-002] status: open | seen: 2 | harness: 0.30.0\n"
            "- [plant:auto-abc123] status: open | seen: 1\n"
            "- [plant:G-050] status: fixed | fixed-in: 0.39.0 | seen: 1 | harness: 0.36.0\n"
            "- [H-016] status: open | seen: 1 | harness: 0.7.0\n")

    def test_stale_flag_is_age_based_project_only_and_unknown_is_not_old(self):
        rows = {r["id"]: r for r in upstream_gaps.triage(self.TEXT, older_than=15, current="0.41.0")}
        self.assertEqual(sorted(rows), ["H-016", "gather:G-001", "gather:G-002", "plant:auto-abc123"])
        self.assertTrue(rows["gather:G-001"]["stale"])
        self.assertFalse(rows["gather:G-002"]["stale"])
        self.assertFalse(rows["plant:auto-abc123"]["stale"], "unknown version is not old")
        self.assertEqual(rows["plant:auto-abc123"]["harness"], "?")
        self.assertFalse(rows["H-016"]["stale"], "harness-native gaps are never stale-flagged")

    def test_mark_unverified_rewrites_only_named_open_project_gaps(self):
        new, marked = upstream_gaps.mark_unverified(
            self.TEXT, ["gather:G-001", "H-016", "plant:G-050", "nope:G-9"], current="0.42.0")
        self.assertEqual(marked, ["gather:G-001"])
        self.assertIn("- [gather:G-001] status: unverified | stale-since: 0.42.0 | seen: 1 | harness: 0.8.0", new)
        self.assertIn("- [H-016] status: open", new)
        self.assertIn("- [plant:G-050] status: fixed", new)
        by = upstream_gaps.gaps_by_source(new)
        self.assertEqual(by, {"gather": 1, "gather (unverified)": 1, "plant": 1, "harness": 1})


if __name__ == "__main__":
    unittest.main()
