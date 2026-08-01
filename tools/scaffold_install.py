#!/usr/bin/env python3
"""Install harness files and patch devtools_config.json without destroying edits.

Used by `/scaffold-godot-harness` (steps 3, 4 and 7). It exists because both of
those steps used to guess, and both guessed wrong in the same direction:

* **Files.** Step 4 backed up on any byte difference, so a plain version bump of
  the harness's own files left `lint_project.gd.bak`, `run_tests.gd.bak` and
  `devtools.py.bak` in a real project - untracked noise protecting edits that did
  not exist, which scaffold can never clean up because it cannot tell its own
  leftovers from a file the user made. A backup is only worth making when the
  file on disk is something the *project* wrote.
* **Config.** Step 7 merged by "is this value the default?", which cannot tell a
  key the project deliberately set back to the default from one nobody touched.

Both are answered by recording what scaffold last wrote:

    addons/godot_selftest/.harness_manifest.json   sha256 per installed file
    devtools_config.json -> "_scaffold_defaults"   the values scaffold proposed

plus `harness_history.json` in the plugin, which holds the sha256 of every file
of every released version - so even the *first* upgrade onto this scheme can
recognize a pristine 0.4.0 file and skip the pointless backup.

Usage:
    python tools/scaffold_install.py files  --project ROOT [--plugin-root DIR] REL...
    python tools/scaffold_install.py config --project ROOT [--plugin-root DIR]
                                            [--set key=json-value ...]

Both modes are idempotent and print a line per file/key describing what they did.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

MANIFEST_REL = "addons/godot_selftest/.harness_manifest.json"
CONFIG_REL = "addons/godot_selftest/devtools_config.json"
SCAFFOLD_DEFAULTS_KEY = "_scaffold_defaults"


def sha256(path):
    """Hash of the file's content with line endings normalized to LF.

    Every file this touches is text. Hashing raw bytes would make the whole
    pristine check useless on Windows: git's core.autocrlf=true hands the plugin
    and the target project CRLF copies of the same file, so a byte hash recorded
    on one machine matches nothing on another - and the failure mode is silent,
    it just backs up every file exactly as before. A file that differs only in
    line endings is not an edit the project made.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def plugin_version(plugin_root):
    return json.loads((plugin_root / ".claude-plugin" / "plugin.json")
                      .read_text(encoding="utf-8"))["version"]


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def known_hashes(plugin_root, rel):
    """Every sha256 this file has had in a released version."""
    history = load_json(plugin_root / "harness_history.json", {})
    out = set()
    for files in history.values():
        if isinstance(files, dict) and rel in files:
            out.add(files[rel])
    return out


def install_files(plugin_root, project, rels):
    version = plugin_version(plugin_root)
    manifest_path = project / MANIFEST_REL
    manifest = load_json(manifest_path, {})
    recorded = manifest.get("files", {})

    backed_up = []
    for rel in rels:
        src = plugin_root / "templates" / rel
        dst = project / rel
        if not src.is_file():
            print("  ! %s: missing from the plugin templates - skipped" % rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)

        if not dst.exists():
            shutil.copyfile(src, dst)
            print("  + %s installed" % rel)
        else:
            src_hash, dst_hash = sha256(src), sha256(dst)
            if src_hash == dst_hash:
                print("  = %s already current (%s)" % (rel, version))
            else:
                # Pristine == byte-identical to what scaffold last wrote here, or to
                # any released version of this file. Either way the project never
                # touched it and there is nothing to protect.
                prior = recorded.get(rel, {}).get("sha256")
                pristine = (dst_hash == prior) or (dst_hash in known_hashes(plugin_root, rel))
                if pristine:
                    shutil.copyfile(src, dst)
                    was = recorded.get(rel, {}).get("version", "an earlier version")
                    print("  ^ %s updated from %s (unmodified - no backup needed)" % (rel, was))
                else:
                    shutil.copyfile(dst, dst.with_suffix(dst.suffix + ".bak"))
                    shutil.copyfile(src, dst)
                    backed_up.append(rel)
                    print("  ! %s was MODIFIED locally -> saved as %s.bak, then updated"
                          % (rel, rel))

        recorded[rel] = {"version": version, "sha256": sha256(dst)}

    manifest["harness_version"] = version
    manifest["files"] = recorded
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")

    if backed_up:
        print("\n  %d file(s) had local edits and were backed up: %s"
              % (len(backed_up), ", ".join(backed_up)))
        print("  Diff each .bak against the new file, port anything worth keeping into")
        print("  devtools_ext/commands.gd (or upstream it), then delete the .bak.")
    return 0


def patch_config(plugin_root, project, overrides):
    template = load_json(plugin_root / "templates" / CONFIG_REL, {})
    if not template:
        print("error: could not read the template devtools_config.json", file=sys.stderr)
        return 1

    # What scaffold proposes this run: the shipped schema plus detected values.
    proposed = dict(template)
    proposed.update(overrides)

    path = project / CONFIG_REL
    existing = load_json(path, None) if path.exists() else None
    if existing is None and path.exists():
        print("error: %s exists but is not valid JSON. Fix or move it; refusing to "
              "overwrite a file that may hold project settings." % path, file=sys.stderr)
        return 1

    if existing is None:
        merged = dict(proposed)
        owned = set(proposed)
        for key in sorted(proposed):
            print("  + %s = %s" % (key, json.dumps(proposed[key])))
    else:
        merged = dict(existing)
        last, owned = read_record(existing)
        for key, value in proposed.items():
            if key not in merged:
                merged[key] = value
                owned.add(key)
                print("  + %s = %s (new key)" % (key, json.dumps(value)))
                continue

            # Decide who owns this key before touching it.
            if key in last:
                # Sticky: once the project has edited a key, it owns it for good.
                # This is what a "is it still the default?" test cannot get right -
                # a value deliberately set BACK to the default reads as untouched.
                scaffold_owns = key in owned and merged[key] == last[key]
                reason = ("project-owned" if key not in owned
                          else "edited since the last scaffold - now project-owned")
            else:
                # No record: the first run of this scheme against an older install.
                # Matching the shipped default is the only evidence available that
                # nobody has touched it.
                scaffold_owns = merged[key] == template.get(key)
                reason = "differs from the shipped default - now project-owned"

            if scaffold_owns:
                owned.add(key)
                if merged[key] != value:
                    print("  ^ %s: %s -> %s" % (key, json.dumps(merged[key]), json.dumps(value)))
                    merged[key] = value
            else:
                owned.discard(key)
                print("  = %s kept as %s (%s)" % (key, json.dumps(merged[key]), reason))

    # Record what scaffold LEAVES IN THE FILE, plus which keys it may still update.
    # Values alone are not enough: a preserved custom value that happens to equal the
    # proposal would read as untouched on the next run and get clobbered then.
    merged[SCAFFOLD_DEFAULTS_KEY] = {
        "note": "Written by /scaffold-godot-harness. 'values' is what scaffold last left "
                "here and 'owned' lists the keys it may still update; edit a key and it "
                "becomes yours permanently. Safe to commit; delete to reset the tracking.",
        "harness_version": plugin_version(plugin_root),
        "owned": sorted(k for k in owned if k in merged),
        "values": {k: merged[k] for k in proposed if k in merged},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print("  wrote %s" % path)
    return 0


def read_record(existing):
    """(values scaffold last left, keys scaffold still owns) from _scaffold_defaults."""
    record = existing.get(SCAFFOLD_DEFAULTS_KEY)
    if not isinstance(record, dict):
        return {}, set()
    if "values" in record and isinstance(record["values"], dict):
        return record["values"], set(record.get("owned") or [])
    # A flat {key: value} block from an early build: treat every recorded key as owned.
    flat = {k: v for k, v in record.items() if not k.startswith("_")}
    return flat, set(flat)


def parse_set(pairs):
    out = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise SystemExit("--set expects key=value, got %r" % raw)
        key, _, value = raw.partition("=")
        try:
            out[key.strip()] = json.loads(value)
        except ValueError:
            out[key.strip()] = value  # bare strings are allowed
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["files", "config"])
    ap.add_argument("rels", nargs="*", metavar="REL",
                    help="files mode: template-relative paths to install")
    ap.add_argument("--project", required=True, help="Target Godot project root")
    ap.add_argument("--plugin-root", default=str(Path(__file__).resolve().parent.parent),
                    help="Plugin root (defaults to this script's repo)")
    ap.add_argument("--set", dest="sets", action="append", metavar="KEY=VALUE",
                    help="config mode: a detected value, e.g. --set main_scene=res://main.tscn")
    args = ap.parse_args()

    plugin_root = Path(args.plugin_root).expanduser().resolve()
    project = Path(args.project).expanduser().resolve()
    if not (project / "project.godot").is_file():
        print("error: no project.godot at %s" % project, file=sys.stderr)
        return 1

    if args.mode == "files":
        if not args.rels:
            print("error: name at least one file to install", file=sys.stderr)
            return 1
        return install_files(plugin_root, project, args.rels)
    return patch_config(plugin_root, project, parse_set(args.sets))


if __name__ == "__main__":
    sys.exit(main())
