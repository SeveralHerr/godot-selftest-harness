#!/usr/bin/env python
"""Bump the harness version stamps. Usage: bump_version.py <old> <new>

Only two shapes are rewritten:

    # harness-version: X.Y.Z          (the stamp comment)
    HARNESS_VERSION = "X.Y.Z"         (and `const HARNESS_VERSION: String = "X.Y.Z"`)

plus `"version"` in .claude-plugin/plugin.json.

Everything else that mentions the old version is left alone on purpose. Lines like
"reachable while paused since 0.12.0" are historical facts about when a behavior
landed; a blind repo-wide replace rewrites them into lies, and that is the way this
step has actually gone wrong.

Prints one line per file. Every file must report `stamp=1 const=1` - a 0 means the
file drifted out of the expected shape and wants a look before the release continues.
Exits 1 if any file is short, so this is safe to chain.
"""

import importlib.util
import json
import pathlib
import re
import sys

PLUGIN_JSON = ".claude-plugin/plugin.json"


def shipped_files(root):
    """The files to stamp, derived from record_version.py rather than duplicated.

    A second copy of this list is how a new tool gets shipped unstamped: capture.gd
    was added to record_version.py's SHIPPED and missed here, and the bump silently
    left it a version behind until --check caught it. There is exactly one list now,
    and it lives with the checker that enforces it.
    """
    spec = importlib.util.spec_from_file_location(
        "_record_version", root / "tools" / "record_version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    files = ["templates/" + rel for rel in mod.SHIPPED]
    # Runnable copies this repo keeps outside templates/ and requires to stay
    # byte-identical to them, so they have to bump in the same pass.
    files += sorted(mod.MIRRORED.keys())
    return files


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    old, new = argv[1], argv[2]
    for label, value in (("old", old), ("new", new)):
        if not re.fullmatch(r"\d+\.\d+\.\d+", value):
            print("error: %s version %r is not X.Y.Z" % (label, value))
            return 2
    if old == new:
        print("error: old and new versions are identical")
        return 2

    root = pathlib.Path(__file__).resolve().parents[3]
    if not (root / PLUGIN_JSON).exists():
        print("error: run this from the plugin repo (no %s at %s)" % (PLUGIN_JSON, root))
        return 2

    stamp = re.compile(r"^(#\s*harness-version:\s*)%s\s*$" % re.escape(old), re.M)
    const = re.compile(
        r"""(HARNESS_VERSION(?:\s*:\s*String)?\s*=\s*["'])%s(["'])""" % re.escape(old))
    # Both stamp shapes already carrying `new`: how an already-bumped file reads.
    at_new = re.compile(
        r"""(?:^#\s*harness-version:\s*|HARNESS_VERSION(?:\s*:\s*String)?\s*=\s*["'])"""
        + re.escape(new), re.M)

    try:
        files = shipped_files(root)
    except Exception as exc:  # a broken record_version.py must stop the release
        print("error: cannot read SHIPPED from tools/record_version.py (%s)" % exc)
        return 2

    short = []
    for rel in files:
        path = root / rel
        if not path.exists():
            print("%-52s MISSING" % rel)
            short.append(rel)
            continue
        text = path.read_text(encoding="utf-8")
        text2, n_stamp = stamp.subn(lambda m: m.group(1) + new, text)
        text2, n_const = const.subn(lambda m: m.group(1) + new + m.group(2), text2)
        if text2 != text:
            path.write_text(text2, encoding="utf-8")
        if n_stamp == 1 and n_const == 1:
            print("%-52s stamp=1 const=1" % rel)
            continue
        # Already carrying the new version is not a failure - it is what re-running
        # after a partial bump looks like, which is exactly when someone needs this
        # output to be readable. Only an unexpected shape is worth stopping for.
        if len(at_new.findall(text)) >= 2:
            print("%-52s already at %s" % (rel, new))
            continue
        print("%-52s stamp=%d const=%d   <-- CHECK THIS FILE" % (rel, n_stamp, n_const))
        short.append(rel)

    pj = root / PLUGIN_JSON
    raw = pj.read_text(encoding="utf-8")
    bumped = raw.replace('"version": "%s"' % old, '"version": "%s"' % new)
    pj.write_text(bumped, encoding="utf-8")
    ok_json = json.loads(bumped).get("version") == new
    print("%-52s %s" % (PLUGIN_JSON, "version=%s" % new if ok_json else "NOT BUMPED"))
    if not ok_json:
        short.append(PLUGIN_JSON)

    if short:
        print("\n%d file(s) did not bump cleanly - fix before running "
              "record_version.py --record:\n  %s" % (len(short), "\n  ".join(short)))
        return 1
    print("\nbumped %s -> %s. Next: python tools/record_version.py --record" % (old, new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
