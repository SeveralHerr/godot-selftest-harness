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
    --triage           List the destination's OPEN pooled gaps grouped by project,
                       oldest first, marking those logged against a harness more
                       than --older-than minor releases behind this one as STALE.
                       Reads only; sources are optional with this flag.
    --mark-unverified ID [ID ...]
                       Rewrite the named pooled gaps' status lines to
                       `status: unverified | stale-since: <this version>` (H-069).
                       Explicit ids only, after a session has re-read them: an
                       age-based bulk mark was tried first and would have relabelled
                       real, still-wanted requests (asset conformance, collider
                       planes) as "not re-checked". Combine with --dry-run to preview.
    A `status: fixed-upstream` gap (0.48.0) is one a project has seen fixed in a
    release it does not run yet - plant ran 0.38.0 on purpose after gh#43 while the
    fix for its G-058 sat in 0.44.0 - and it is skipped like `fixed`: the pool already
    holds it as fixed. It flips to `fixed` when the project refreshes.
    --older-than N     Minor releases behind HARNESS_VERSION that --triage flags
                       as STALE (default 15). A flag for the reader, never a rewrite.

`unverified` is the honest third state between open and fixed: the gap was logged
against templates that have since been rewritten and nobody has re-checked its
mechanism. It is not a claim that it is fixed. An `unverified` id is still known to
the dedupe, so re-pooling the project's still-open copy does not re-append it; a
project that sees it again bumps `seen:` as usual, and a session that re-checks it
sets `open` or `fixed` by hand.

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

# harness-version: 0.59.0
HARNESS_VERSION = "0.59.0"

DEFAULT_DEST = "log-devtools.md"

_GAP_RE = re.compile(r"^- Gap\b")
# A "no gaps this turn" line is the format's required explicit statement that the
# harness had nothing missing - it is an absence marker, not a gap, and must never
# be pooled upstream. Match on the normalized first line of the block. Sessions
# write it several ways - `no gaps this turn`, `no new gap.`, `no new gaps -`,
# `none this turn` - and 0.31.0 pooled two `**no new gap.**` bullets from a project
# as `auto-<hash>` OPEN gaps because the pattern only knew the first spelling
# (H-063). Anything that opens by declaring an absence is an absence marker.
_NO_GAP_RE = re.compile(
    r"^-\s*Gap[^:]*:\s*[\*_`\s]*(no(\s+new|\s+harness)?\s+gaps?(?![a-z])"
    r"|none\s+(this|new|to\s+report)|nothing\s+(new|missing|to\s+report))(?![a-z])",
    re.IGNORECASE)
_HEADING_RE = re.compile(r"^##\s+(?P<text>.*)$")
_FENCE_RE = re.compile(r"^\s*```")
_ID_LINE_RE = re.compile(r"^(?P<indent>\s*)-\s*\[(?P<id>[^\]]+)\]\s*(?P<fields>.*)$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SEEN_RE = re.compile(r"(seen:\s*)(\d+)")
# gh#47.2 / moving-in:G-065: a repeat sighting is often written as
# `- Gap: **[G-025] every engine-side gate ...** - status: fixed (RECONCILED ...) |
# seen: 3 | harness: 0.16.0` - the id in the TITLE and the fields on the wrapped
# paragraph, not on a `- [G-025] status:` list item of their own. The parser read
# only the list-item form and minted an `auto-` id for each: one gap seen twice
# arrived as two new gaps, and a `status: fixed` sighting arrived as open.
_TITLE_ID_RE = re.compile(r"^\s*-\s*Gap[^:]*:\s*[*`\s]*\[(?P<id>[A-Za-z]+-\d+[a-z]?)\]")
_INLINE_STATUS_RE = re.compile(r"\bstatus:\s*(?P<status>[a-z][a-z-]*)")


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
            tm = _TITLE_ID_RE.match(block[0])
            if tm:
                gap["id"] = tm.group("id")
                gap["id_from_title"] = True
                # Fields live somewhere in the paragraph: read them from the first
                # line that carries `status:` (title line included), pipe-split.
                for k, bline in enumerate(block):
                    sm = _INLINE_STATUS_RE.search(bline)
                    if sm:
                        tail = bline[bline.index("status:"):]
                        gap["fields"] = _parse_fields(tail)
                        gap["fields"]["status"] = sm.group("status")
                        # seen: may sit in bold (**seen: 2**) or on the next line
                        rest = " ".join(block[k:k + 3])
                        sn = _SEEN_RE.search(rest)
                        if sn:
                            gap["fields"]["seen"] = sn.group(2)
                        break
        if not gap["id"]:
            gap["id"] = _auto_id(block)
            gap["minted"] = True
        # "no gaps this turn" entries are absence markers required by the log
        # format; they carry no id and nothing to fix, so they never upstream.
        # 0.58.0: an entry with NO id line of its own whose body says "no gap here" /
        # "no gaps this turn" is an absence marker too - a project wrote "the ledger's
        # `blocked` result works exactly as designed ... **No gap here**" as a success
        # note under the Gap: heading and it arrived minted as a gap.
        body_says_none = gap.get("minted") and re.search(
            r"\bno gaps? (here|this turn|this session)\b", " ".join(block), re.I)
        if not _NO_GAP_RE.match(block[0]) and not body_says_none:
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


_FILED_RE = re.compile(r"(?:filed upstream|upstream|dup-of)\s*[:=]?\s*(?:as\s+)?"
                       r"(?:SeveralHerr/godot-selftest-harness)?#(\d+)", re.I)


def _filed_upstream(gap):
    """`gh#NN` when the gap's own lines say it was filed upstream, else ""."""
    fields = gap["fields"]
    for key in ("filed upstream", "dup-of", "upstream"):
        v = fields.get(key, "")
        m = re.search(r"#(\d+)", v)
        if m:
            return "gh#" + m.group(1)
    for line in gap["lines"]:
        m = _FILED_RE.search(line)
        if m:
            return "gh#" + m.group(1)
    return ""


def gaps_by_source(dest_text):
    """{source-project: open-gap count} over a pooled log (H-028: 84% of gaps came
    from one game and nothing surfaced the concentration). Harness-native H- gaps
    count under "harness"."""
    counts = {}
    for line in dest_text.splitlines():
        m = _ID_LINE_RE.match(line)
        if not m:
            continue
        fields = _parse_fields(m.group("fields"))
        status = fields.get("status", "open").split()[0]
        if status not in ("open", "unverified"):
            continue
        gid = m.group("id")
        proj = gid.split(":", 1)[0] if ":" in gid else "harness"
        key = proj if status == "open" else proj + " (unverified)"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _minor(version):
    """`0.41.0` -> 41; anything unparseable -> None."""
    m = re.match(r"\s*(\d+)\.(\d+)", str(version or ""))
    if not m:
        return None
    return int(m.group(1)) * 1000 + int(m.group(2))


def triage(dest_text, older_than=15, current=None):
    """[{id, project, harness, seen, stale, line_no}] for every OPEN pooled gap in
    a log (H-069). `stale` when its `harness:` is more than `older_than` minor
    releases behind `current` (HARNESS_VERSION by default); an unparseable or
    missing `harness:` is listed as `?` and never flagged (unknown is not old).
    Harness-native ids (no project prefix) are listed but never stale-flagged."""
    cur = _minor(current or HARNESS_VERSION)
    out = []
    for i, line in enumerate(dest_text.splitlines()):
        m = _ID_LINE_RE.match(line)
        if not m:
            continue
        fields = _parse_fields(m.group("fields"))
        if fields.get("status", "open").split()[0] != "open":
            continue
        gid = m.group("id")
        project = gid.split(":", 1)[0] if ":" in gid else ""
        hv = fields.get("harness", "")
        mv = _minor(hv)
        stale = bool(project) and mv is not None and cur is not None and cur - mv > older_than
        # 0.53.0: the Gap title, so a triage session can decide without grepping.
        title = ""
        lines = dest_text.splitlines()
        for back in range(i - 1, max(-1, i - 40), -1):
            gm = _GAP_RE.match(lines[back])
            if gm:
                title = re.sub(r"^\s*-\s*Gap[^:]*:\s*", "", lines[back]).strip("* `").strip()
                break
        out.append({"id": gid, "project": project or "harness", "harness": hv or "?",
                    "seen": _seen(fields), "stale": stale, "line_no": i, "title": title[:90]})
    return out


def mark_unverified(dest_text, ids, current=None):
    """Rewrite the named OPEN pooled gaps' status lines. Returns (new_text, [ids
    marked]). Only `status: open` becomes `status: unverified`; every other field on
    the line is kept and `stale-since: <current>` is appended after it. Ids not found,
    not open, or harness-native (no project prefix) are left alone."""
    lines = dest_text.splitlines()
    marked = []
    wanted = set(ids)
    for row in triage(dest_text, 10 ** 6, current):
        if row["id"] not in wanted or ":" not in row["id"]:
            continue
        line = lines[row["line_no"]]
        new = re.sub(r"status:\s*open\b", "status: unverified | stale-since: %s"
                     % (current or HARNESS_VERSION), line, count=1)
        if new != line:
            lines[row["line_no"]] = new
            marked.append(row["id"])
    return "\n".join(lines) + ("\n" if dest_text.endswith("\n") else ""), marked


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
    # H-044 (0.41.0): the two intake paths - a project's own G-NNN and this repo's
    # gh#NN - used to meet only in a human's memory. A project that filed its gap
    # upstream says so on the id line (`filed upstream: gh#40`); carry that across
    # as `dup-of: gh#40` so the pooled entry names the issue it duplicates and the
    # release turn closes both at once instead of one twice.
    filed = _filed_upstream(gap)
    if filed:
        parts.append("dup-of: %s" % filed)

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
    # that rule (or by a hand that forgot it) carries the same id twice. Two
    # shapes, told apart by the second block's own `seen:` field:
    #   * a RECURRENCE (`seen: 2` or more on the later block) is the same gap
    #     sighted again - the first block wins and the highest `seen:` rides
    #     with it, so one id never lands on two destination entries;
    #   * a COLLISION (both blocks `seen: 1`) is two different gaps that were
    #     handed the same number by two sessions - moving-in's second `G-027`
    #     (a `first-frame` verb) was silently dropped that way for a release
    #     (H-059). The later block is kept under a suffixed id (`G-027b`) and
    #     the run says so, rather than pooling the first and losing the second.
    selected = {}
    suffixed = []
    for gap in gaps:
        status = gap["fields"].get("status", "open").lower()
        if status not in ("open", "") and not include_fixed:
            skipped.append("%s (status: %s)" % (gap["id"], status))
            continue
        key = "%s:%s" % (project, gap["id"])
        if key in selected and _seen(gap["fields"]) <= 1 and gap["id_index"] >= 0:
            base = key
            for suffix in "bcdefghijklmnopqrstuvwxyz":
                key = base + suffix
                if key not in selected:
                    break
            gap = dict(gap)
            gap["fields"] = dict(gap["fields"])
            gap["sightings"] = 1
            selected[key] = gap
            suffixed.append("%s -> %s (same id, different evidence, both seen: 1)"
                            % (base, key))
        elif key in selected:
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
        appended.append(qualified + (" (id read from the Gap title)" if gap.get("id_from_title") else "")
                        + (" (minted: no id anywhere in the entry)" if gap.get("minted") else ""))

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
        "suffixed": suffixed,
        "changed": changed,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Append a project log's open gaps to the harness repo's gaps log.",
    )
    ap.add_argument("sources", nargs="*", metavar="SOURCE_LOG",
                    help="Project log-devtools.md file(s) to read")
    ap.add_argument("--into", default=DEFAULT_DEST, metavar="PATH",
                    help="Destination log (default: ./%s)" % DEFAULT_DEST)
    ap.add_argument("--project", metavar="NAME",
                    help="Id namespace for the source (default: its parent directory name)")
    ap.add_argument("--include-fixed", action="store_true",
                    help="Also carry over fixed/wontfix gaps (default: open only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change; write nothing")
    ap.add_argument("--triage", action="store_true",
                    help="List the destination's open pooled gaps by project, oldest first, "
                         "flagging STALE ones (H-069); reads only")
    ap.add_argument("--mark-unverified", nargs="+", metavar="ID", default=None,
                    help="Rewrite the named pooled gaps to status: unverified | stale-since: "
                         + HARNESS_VERSION + " (explicit ids, never H-NNN); --dry-run previews")
    ap.add_argument("--older-than", type=int, default=15, metavar="N",
                    help="Minor releases behind %s that count as stale (default 15)" % HARNESS_VERSION)
    args = ap.parse_args()

    dest_path = Path(args.into).expanduser().resolve()
    if not dest_path.parent.is_dir():
        print("error: destination directory does not exist: %s" % dest_path.parent,
              file=sys.stderr)
        return 1

    if args.triage or args.mark_unverified:
        if not dest_path.is_file():
            print("error: no such log: %s" % dest_path, file=sys.stderr)
            return 1
        text = dest_path.read_text(encoding="utf-8")
        rows = triage(text, args.older_than)
        by_proj = {}
        for r in rows:
            by_proj.setdefault(r["project"], []).append(r)
        for proj in sorted(by_proj, key=lambda k: (-len(by_proj[k]), k)):
            rs = sorted(by_proj[proj], key=lambda r: (_minor(r["harness"]) or -1, r["id"]))
            stale_n = sum(1 for r in rs if r["stale"])
            print("%s: %d open, %d STALE (harness more than %d minor releases behind %s)"
                  % (proj, len(rs), stale_n, args.older_than, HARNESS_VERSION))
            for r in rs:
                print("  %s %s  harness %s  seen %d  %s" % (
                    "STALE" if r["stale"] else "     ", r["id"], r["harness"], r["seen"], r.get("title", "")))
        if args.mark_unverified:
            new_text, marked = mark_unverified(text, args.mark_unverified)
            missing = sorted(set(args.mark_unverified) - set(marked))
            if missing:
                print("\nnot marked (not found, not open, or harness-native): %s"
                      % ", ".join(missing))
            if args.dry_run:
                print("\ndry run: would mark %d gap(s) unverified: %s"
                      % (len(marked), ", ".join(marked)))
            elif marked:
                dest_path.write_text(new_text, encoding="utf-8")
                print("\nmarked %d gap(s) `status: unverified | stale-since: %s`: %s"
                      % (len(marked), HARNESS_VERSION, ", ".join(marked)))
            else:
                print("\nnothing to mark.")
        if not args.sources:
            return 0
    elif not args.sources:
        ap.error("at least one SOURCE_LOG is required (or --triage / --mark-unverified)")

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
        for s in r["suffixed"]:
            print("  ! %s" % s)
        if not (r["appended"] or r["bumped"] or r["present"] or r["skipped"]):
            print("  (no gaps found - check the log's format)")

    if args.dry_run:
        print("\ndry run: nothing written.")
    elif total_new:
        print("\n%d gap(s) appended to %s. Review before committing." % (total_new, dest_path))
    try:
        by_src = gaps_by_source(dest_path.read_text(encoding="utf-8"))
    except OSError:
        by_src = {}
    if by_src:
        total = sum(by_src.values())
        ranked = sorted(by_src.items(), key=lambda kv: (-kv[1], kv[0]))
        print("open gaps in %s by source: %s (%d total%s)" % (
            dest_path.name,
            ", ".join("%s %d" % kv for kv in ranked), total,
            "; %s carries %d%%" % (ranked[0][0], round(100.0 * ranked[0][1] / total))
            if total and ranked[0][1] * 2 > total else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
