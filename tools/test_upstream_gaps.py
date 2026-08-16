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


if __name__ == "__main__":
    unittest.main()
