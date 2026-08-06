#!/usr/bin/env python3
"""Pool open gaps from one or more project logs into the harness repo's log.

The harness improves from evidence, and until this script existed the only
transport for that evidence was a human pasting text between two repositories.
That transport had never once run: six gaps sat in a game's log for a full
release cycle while the harness repo's log recorded nothing about them.

So this is deliberately boring. There is no PR, no review step, no filtering by
importance. It appends open gaps to a destination log, deduped by id, and bumps
`seen:` when an id shows up again. It never edits the source, never deletes
anything from the destination, and running it twice on unchanged input does
nothing the second time.

Usage
-----
From a project, pushing its gaps up to the harness repo:

    python3 tools/upstream_gaps.py log-devtools.md \\
        --into /path/to/godot-selftest-harness/log-devtools.md

From the harness repo, pulling from several projects at once:

    python3 tools/upstream_gaps.py ../game-a/log-devtools.md ../game-b/log-devtools.md

Options:
    --into PATH        Destination log (default: ./log-devtools.md)
    --project NAME     Namespace for the source's ids (default: the source
                       file's parent directory name)
    --include-fixed    Also carry over `fixed` / `wontfix` gaps (default: open only)
    --dry-run          Print what would happen; write nothing

Ids
---
Gap ids are only unique within one log, so two games can each own a `G-007`.
Every id is therefore qualified with its project on the way up:
`gather:G-007`. That qualified id is the dedupe key, and it is stable, so the
destination entry can always be traced back to the log it came from.

A gap with no id line at all still travels: it gets `auto-<hash>` derived from
its own text, which is stable across runs for as long as the text is.
"""

import argparse
import datetime
import hashlib
import re
import sys
from pathlib import Path

# harness-version: 0.10.0
HARNESS_VERSION = "0.10.0"

DEFAULT_DEST = "log-devtools.md"

_GAP_RE = re.compile(r"^- Gap\b")
# A "no gaps this turn" line is the format's required explicit statement that the
# harness had nothing missing - it is an absence marker, not a gap, and must never
# be pooled upstream. Match on the normalized first line of the block.
_NO_GAP_RE = re.compile(r"^-\s*Gap[^:]*:\s*\**no(\s+new)?\s+gaps?\s+this\s+turn\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"^##\s+(?P<text>.*)$")
_FENCE_RE = re.compile(r"^\s*```")
_ID_LINE_RE = re.compile(r"^(?P<indent>\s*)-\s*\[(?P<id>[^\]]+)\]\s*(?P<fields>.*)$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SEEN_RE = re.compile(r"(seen:\s*)(\d+)")


def _parse_fields(text):
    """`status: open | seen: 2 | harness: 0.4.0` -> {"status": "open", ...}."""
    out = {}
    for part in text.split("|"):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        out[key.strip().lower()] = value.strip()
    return out


def _auto_id(lines):
    """Stable synthetic id for a gap that never got a real one."""
    body = " ".join(l.strip() for l in lines)
    return "auto-" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:6]


def parse_gaps(text):
    """Every gap block in a log, in file order.

    Fenced code blocks are skipped entirely, so the `- Gap: **<what was
    missing>**` template inside the Format section is not mistaken for a real
    gap - it is the first thing in every one of these files.
    """
    lines = text.split("\n")
    gaps = []
    heading = ""
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue

        m = _HEADING_RE.match(line)
        if m:
            heading = m.group("text").strip()
            i += 1
            continue

        if not _GAP_RE.match(line):
            i += 1
            continue

        # The gap owns every following blank or indented line: its evidence,
        # its fenced output, its status line and its improvements.
        block = [line]
        j = i + 1
        block_fence = False
        while j < len(lines):
            nxt = lines[j]
            if _FENCE_RE.match(nxt):
                block_fence = not block_fence
                block.append(nxt)
                j += 1
                continue
            if block_fence or nxt.strip() == "" or nxt[:1].isspace():
                block.append(nxt)
                j += 1
                continue
            break
        while block and block[-1].strip() == "":
            block.pop()

        gap = {
            "heading": heading,
            "heading_date": (_DATE_RE.search(heading).group(0) if _DATE_RE.search(heading) else ""),
            "lines": block,
            "id": "",
            "id_index": -1,
            "fields": {},
        }
        for k, bline in enumerate(block):
            im = _ID_LINE_RE.match(bline)
            if im:
                gap["id"] = im.group("id").strip()
                gap["id_index"] = k
                gap["fields"] = _parse_fields(im.group("fields"))
                break
        if not gap["id"]:
            gap["id"] = _auto_id(block)
        # "no gaps this turn" entries are absence markers required by the log
        # format; they carry no id and nothing to fix, so they never upstream.
        if not _NO_GAP_RE.match(block[0]):
            gaps.append(gap)
        i = j
    return gaps


def dest_entries(text):
    """{qualified id: line number} for every gap already in the destination."""
    out = {}
    in_fence = False
    for n, line in enumerate(text.split("\n")):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _ID_LINE_RE.match(line)
        if m:
            out.setdefault(m.group("id").strip(), n)
    return out


def _seen(fields):
    try:
        return int(fields.get("seen", "1"))
    except ValueError:
        return 1


def _render(gap, qualified_id, source_label):
    """The gap's own text, with its status line rewritten for the destination."""
    fields = gap["fields"]
    parts = ["status: %s" % fields.get("status", "open")]
    if fields.get("fixed-in"):
        parts.append("fixed-in: %s" % fields["fixed-in"])
    parts.append("seen: %d" % _seen(fields))
    if fields.get("harness"):
        parts.append("harness: %s" % fields["harness"])
    parts.append("source: %s" % source_label)

    if gap["id_index"] >= 0:
        indent = _ID_LINE_RE.match(gap["lines"][gap["id_index"]]).group("indent")
    else:
        indent = "  "
    status_line = "%s- [%s] %s" % (indent, qualified_id, " | ".join(parts))

    out = list(gap["lines"])
    if gap["id_index"] >= 0:
        out[gap["id_index"]] = status_line
    else:
        out.insert(1, status_line)
    return out


def upstream(source, dest_path, project, include_fixed, dry_run):
    text = source.read_text(encoding="utf-8")
    gaps = parse_gaps(text)

    dest_text = dest_path.read_text(encoding="utf-8") if dest_path.exists() else ""
    dest_lines = dest_text.split("\n")
    known = dest_entries(dest_text)

    appended, bumped, present, skipped = [], [], [], []
    new_blocks = []
    harness_versions = set()

    # Collapse repeat sightings within this one source first. A recurrence is
    # supposed to bump `seen:` on the original entry, but a log written before
    # that rule (or by a hand that forgot it) carries the same id twice with
    # different evidence. Upstreaming both would put one id on two destination
    # entries and make every later bump ambiguous, so the first block wins and
    # the highest `seen:` of the group rides with it.
    selected = {}
    for gap in gaps:
        status = gap["fields"].get("status", "open").lower()
        if status not in ("open", "") and not include_fixed:
            skipped.append("%s (status: %s)" % (gap["id"], status))
            continue
        key = "%s:%s" % (project, gap["id"])
        if key in selected:
            first = selected[key]
            merged = max(_seen(first["fields"]), _seen(gap["fields"]), first["sightings"] + 1)
            first["fields"]["seen"] = str(merged)
            first["sightings"] += 1
        else:
            gap = dict(gap)
            gap["fields"] = dict(gap["fields"])
            gap["sightings"] = 1
            selected[key] = gap

    for qualified, gap in selected.items():
        if gap["fields"].get("harness"):
            harness_versions.add(gap["fields"]["harness"])

        source_label = "%s %s" % (project, gap["heading_date"] or source.name)

        if qualified in known:
            n = known[qualified]
            m = _SEEN_RE.search(dest_lines[n])
            dest_seen = int(m.group(2)) if m else 1
            src_seen = _seen(gap["fields"])
            if m and src_seen > dest_seen:
                dest_lines[n] = _SEEN_RE.sub(
                    lambda mm: "%s%d" % (mm.group(1), src_seen), dest_lines[n], count=1
                )
                bumped.append("%s seen %d -> %d" % (qualified, dest_seen, src_seen))
            else:
                present.append("%s (seen: %d)" % (qualified, dest_seen))
            continue

        new_blocks.append(_render(gap, qualified, source_label))
        appended.append(qualified)

    if new_blocks:
        today = datetime.date.today().isoformat()
        version = ", ".join(sorted(harness_versions)) or "unknown"
        header = [
            "",
            "## %s - Upstreamed %d open gap(s) from %s (harness %s)"
            % (today, len(new_blocks), project, version),
            "",
            "Pooled by `tools/upstream_gaps.py` from `%s`. Gap text is the project's,"
            % source,
            "verbatim; only the id line is rewritten (qualified with the project name so",
            "ids from two projects cannot collide, plus a `source:` back-pointer).",
            "",
        ]
        body = []
        for block in new_blocks:
            body.extend(block)
            body.append("")
        while dest_lines and dest_lines[-1].strip() == "":
            dest_lines.pop()
        dest_lines.extend(header + body)

    changed = bool(new_blocks or bumped)
    if changed and not dry_run:
        dest_path.write_text("\n".join(dest_lines).rstrip("\n") + "\n", encoding="utf-8")

    return {
        "appended": appended,
        "bumped": bumped,
        "present": present,
        "skipped": skipped,
        "changed": changed,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Append a project log's open gaps to the harness repo's gaps log.",
    )
    ap.add_argument("sources", nargs="+", metavar="SOURCE_LOG",
                    help="Project log-devtools.md file(s) to read")
    ap.add_argument("--into", default=DEFAULT_DEST, metavar="PATH",
                    help="Destination log (default: ./%s)" % DEFAULT_DEST)
    ap.add_argument("--project", metavar="NAME",
                    help="Id namespace for the source (default: its parent directory name)")
    ap.add_argument("--include-fixed", action="store_true",
                    help="Also carry over fixed/wontfix gaps (default: open only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change; write nothing")
    args = ap.parse_args()

    dest_path = Path(args.into).expanduser().resolve()
    if not dest_path.parent.is_dir():
        print("error: destination directory does not exist: %s" % dest_path.parent,
              file=sys.stderr)
        return 1

    total_new = 0
    for raw in args.sources:
        source = Path(raw).expanduser().resolve()
        if not source.is_file():
            print("error: no such log: %s" % source, file=sys.stderr)
            return 1
        if source == dest_path:
            print("error: refusing to upstream %s into itself" % source, file=sys.stderr)
            return 1

        project = args.project or source.parent.name
        project = re.sub(r"[^A-Za-z0-9_.-]", "-", project).strip("-") or "project"

        r = upstream(source, dest_path, project, args.include_fixed, args.dry_run)
        total_new += len(r["appended"])

        print("%s -> %s  [%s]" % (source, dest_path, project))
        for q in r["appended"]:
            print("  + %s appended" % q)
        for b in r["bumped"]:
            print("  ~ %s" % b)
        for p in r["present"]:
            print("  = %s already upstreamed" % p)
        for s in r["skipped"]:
            print("  - %s skipped" % s)
        if not (r["appended"] or r["bumped"] or r["present"] or r["skipped"]):
            print("  (no gaps found - check the log's format)")

    if args.dry_run:
        print("\ndry run: nothing written.")
    elif total_new:
        print("\n%d gap(s) appended to %s. Review before committing." % (total_new, dest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
