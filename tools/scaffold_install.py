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
* **UIDs.** Step 4 installed `.gd` files with no `.uid` sidecar beside them, and
  lint could not say so: `uid_check_ignore` defaults to exactly the two paths
  scaffold writes into (`moving-in:G-004`). `files` mode now mints a sidecar for
  any `.gd` that lacks one, never rewrites an existing one, and names every id it
  minted so the summary can carry what lint will not.

Both are answered by recording what scaffold last wrote:

    addons/godot_selftest/.harness_manifest.json   sha256 per installed file
    devtools_config.json -> "_scaffold_defaults"   the values scaffold proposed

plus `harness_history.json` in the plugin, which holds the sha256 of every file
of every released version - so even the *first* upgrade onto this scheme can
recognize a pristine 0.4.0 file and skip the pointless backup.

Usage:
    python tools/scaffold_install.py full   --project ROOT [--plugin-root DIR]
                                            [--set key=json-value ...] [--no-hook]
                                            [--hook-python NAME]
    python tools/scaffold_install.py files  --project ROOT [--plugin-root DIR] REL...
    python tools/scaffold_install.py config --project ROOT [--plugin-root DIR]
                                            [--set key=json-value ...]
    python tools/scaffold_install.py format-block --project ROOT [--plugin-root DIR]

All modes are idempotent and print a line per file/key describing what they did.
`format-block` refreshes the harness-authored Format section of an installed
log-devtools.md in place (between the BEGIN/END markers) without touching the
project's entries; a marker-less (pre-0.8.0) log is left alone.

`full` (0.20.0, gh#9 / H-047 / H-048) is the ONE definition of a complete install,
usable with no LLM in the loop: files -> config -> devtools_ext/ -> test/ seed ->
CLAUDE.md merge -> log-devtools.md seed/refresh -> Stop hook -> DevTools autoload
(appended LAST in [autoload], so a project's own autoloads are ready before the
extension registers). Before it existed, `/scaffold-godot-harness` (prose),
`check_templates.py::stage_assemble` and every benchmark rig each carried their own
notion of "installed", and the autoload-ordering rule lived only as a sentence in
the slash command - which is how an A/B rig put DevTools FIRST. Both the slash
command and check_templates now call this. What `full` does NOT do: detect
`hud_layer_name` (needs a scene reader) or the Godot binary (step 11) - pass those
with --set, or run `config --set` afterwards; the merge keeps what is already there.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import uuid
from pathlib import Path

MANIFEST_REL = "addons/godot_selftest/.harness_manifest.json"
CONFIG_REL = "addons/godot_selftest/devtools_config.json"
SCAFFOLD_DEFAULTS_KEY = "_scaffold_defaults"
LOG_REL = "log-devtools.md"
FORMAT_BEGIN = "<!-- BEGIN godot-selftest-harness-format -->"
FORMAT_END = "<!-- END godot-selftest-harness-format -->"

# Files copied verbatim into a target project, relative to templates/. The single
# list: record_version.py stamps and hashes exactly these, and `full` installs
# exactly these. Add a shipped file here and nowhere else.
SHIPPED_FILES = [
    "addons/godot_selftest/dev_tools.gd",
    "addons/godot_selftest/scene_validator.gd",
    "tools/lint_project.gd",
    "tools/run_tests.gd",
    "tools/eval.gd",
    "tools/capture.gd",
    "tools/devtools.py",
    "tools/check_devtools_log.py",
    "tools/upstream_gaps.py",
    "tools/verify_ledger.py",
    "tools/import_check.py",
    "tools/name_check.py",
    "tools/coverage_check.py",
]

AUTOLOAD_LINE = 'DevTools="*res://addons/godot_selftest/dev_tools.gd"'
CLAUDE_BEGIN = "<!-- BEGIN godot-selftest-harness -->"
CLAUDE_END = "<!-- END godot-selftest-harness -->"
HOOK_MARKER = "check_devtools_log.py"


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


def _uid_encoder(plugin_root):
    """Godot's ResourceUID text encoding, taken from the shipped client.

    Imported rather than reimplemented. `templates/tools/devtools.py` already holds
    that encoding, verified against `ResourceUID.id_to_text()` on a real engine, and
    a second copy of it here is exactly the two-halves-drift this repo keeps getting
    bitten by - a wrong id would be silent until an import much later. devtools.py
    guards its entry point, so importing it runs no CLI.
    """
    path = plugin_root / "templates" / "tools" / "devtools.py"
    spec = importlib.util.spec_from_file_location("_harness_devtools_uid", path)
    if spec is None or spec.loader is None:
        raise ImportError("no loadable spec for %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.uid_to_text


_FEATURES_RE = re.compile(r"config/features\s*=\s*PackedStringArray\(([^)]*)\)")
_XY_RE = re.compile(r'"(\d+)\.(\d+)')


def project_engine_xy(project):
    """(major, minor) from project.godot's config/features, or None if unreadable."""
    try:
        text = (project / "project.godot").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    features = _FEATURES_RE.search(text)
    if not features:
        return None
    xy = _XY_RE.search(features.group(1))
    return (int(xy.group(1)), int(xy.group(2))) if xy else None


def ensure_uid_sidecars(plugin_root, project, gd_paths):
    """Give every installed `.gd` a `.uid` sidecar, minting one where there is none.

    `moving-in:G-004`. Scaffold installed `.gd` files with no sidecar and nothing
    caught it: `uid_check_ignore` defaults to `res://addons/` and `res://tools/`,
    which is precisely where scaffold writes, so lint printed `UIDs: OK` straight
    over the hole and the missing file read as drift on the next refresh. The
    comment that set that default said the scaffolder "cannot generate a valid .uid
    (the ids are engine assigned)" - true when it was written, no longer true since
    `devtools.py new-uid` reimplemented ResourceUID's encoding and started minting
    ids with no engine at all. So mint them here rather than widening lint's ignore
    list, which is shared by the class-cache, compile, shader and string-ref passes
    and would drag all four across the addon on install day.

    **An existing sidecar is never rewritten.** A uid is an identity; regenerating
    one per refresh would break every scene and `preload` that references the
    script - a far worse bug than the missing file. That is also what makes this
    idempotent: on the second run every sidecar already exists and nothing is
    written.

    Returns [(project-relative path, uid)] for the ones actually created, so the
    caller can name them in a summary the project's own lint will never print.
    """
    wanted = [p for p in gd_paths if not Path(str(p) + ".uid").exists()]
    if not wanted:
        # Not silence (gh#18/gh#19.3): step 13 asks the caller to say "none
        # needed" versus "installer said nothing", and lint's UIDs: OK covers
        # both cases identically (uid_check_ignore excludes addons/ and tools/,
        # exactly where scaffold writes) - so this print is the only witness.
        print("  = .uid sidecars: none needed (%d installed .gd file(s) already had one)"
              % len(gd_paths))
        return []

    # .uid sidecars arrived in Godot 4.4; older projects have no importer for them
    # and would just collect six unreferenced files. Only skip on a version we can
    # actually read - an unparseable project.godot means assume current, not skip.
    xy = project_engine_xy(project)
    if xy is not None and xy < (4, 4):
        print("  = .uid sidecars skipped: project declares Godot %d.%d (uids arrive in 4.4)"
              % xy)
        return []

    try:
        uid_to_text = _uid_encoder(plugin_root)
    except Exception as exc:  # noqa: BLE001 - report it, never install silently short
        print("  ! could not load the uid encoder from templates/tools/devtools.py (%s);"
              % exc)
        print("    installed .gd files have no .uid sidecar. Mint them with:")
        print("      python tools/devtools.py new-uid --write <file>.gd")
        return []

    existing = set()
    for sidecar in project.rglob("*.uid"):
        try:
            existing.add(sidecar.read_text(encoding="utf-8").strip())
        except OSError:
            continue

    made = []
    for target in wanted:
        for _attempt in range(1000):
            # 63-bit, matching ResourceUID::create_id()'s 0x7FFF... mask.
            text = uid_to_text(uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF)
            if text not in existing:
                break
        else:
            print("  ! could not mint an unused uid for %s in 1000 attempts - skipped"
                  % target.name)
            continue
        existing.add(text)
        sidecar = Path(str(target) + ".uid")
        sidecar.write_text(text, encoding="utf-8")
        rel = target.relative_to(project).as_posix()
        made.append((rel + ".uid", text))
        print("  + %s.uid minted %s (no sidecar shipped with it)" % (rel, text))
    return made


def install_files(plugin_root, project, rels):
    version = plugin_version(plugin_root)
    manifest_path = project / MANIFEST_REL
    manifest = load_json(manifest_path, {})
    recorded = manifest.get("files", {})

    backed_up = []
    installed_gd = []
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
        if dst.suffix == ".gd":
            installed_gd.append(dst)

    # Deliberately NOT recorded in the manifest: a .uid holds a value this project
    # owns, not one the harness shipped, and a recorded hash would let a later
    # refresh judge it "pristine" and overwrite the identity (moving-in:G-004).
    minted = ensure_uid_sidecars(plugin_root, project, installed_gd)

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
    if minted:
        # Say this out loud. The project's own lint cannot: uid_check_ignore covers
        # res://addons/ and res://tools/, so it reports `UIDs: OK` whether or not
        # these exist. Silence here is what made moving-in:G-004 cost a session.
        print("\n  %d .uid sidecar(s) minted (lint's uid_check_ignore covers these paths,"
              % len(minted))
        print("  so it would have reported UIDs: OK either way). Commit them:")
        for rel, text in minted:
            print("    %s  %s" % (rel, text))
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
        record = existing.get(SCAFFOLD_DEFAULTS_KEY)
        recorded_version = record.get("harness_version") if isinstance(record, dict) else None
        version_moved = recorded_version != plugin_version(plugin_root)
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
                    # Owning a key is permission to update it, not an instruction
                    # to. Every `config` call proposes the shipped default for
                    # each key it was NOT passed, and /scaffold-godot-harness
                    # calls `config` more than once per run (steps 7 and 11 set
                    # different keys) - so the second call used to reset the
                    # `godot_bin` the first had just detected straight back to ""
                    # (gh#7, e8g). Rewrite only when this call carries the key
                    # explicitly, or when the harness version has moved on since
                    # the record was written (a shipped default may have changed)
                    # - and even then never from a real value back to an empty
                    # one, because "reset to empty" is never what a refresh means.
                    explicit = key in overrides
                    empties = value in ("", None, [], {}) and merged[key] not in ("", None, [], {})
                    if explicit or (version_moved and not empties):
                        print("  ^ %s: %s -> %s" % (key, json.dumps(merged[key]), json.dumps(value)))
                        merged[key] = value
                    else:
                        print("  = %s kept as %s (scaffold-owned, not passed to this call)"
                              % (key, json.dumps(merged[key])))
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


def refresh_format_block(plugin_root, project):
    """Refresh the marked Format section of an installed log-devtools.md (H-009).

    The log's *entries* belong to the project and are never touched. The Format
    section between the markers is harness-authored and used to be frozen at
    whatever version first seeded the file - a new verdict or status field could
    never reach an existing install. Same mechanism as the CLAUDE.md block:
    replace the marked span, inclusive, in place. A log without markers predates
    this scheme; guessing where its format section ends would risk eating
    entries, so it is reported and left alone.
    """
    template = (plugin_root / "templates" / LOG_REL).read_text(encoding="utf-8")
    t_begin, t_end = template.find(FORMAT_BEGIN), template.find(FORMAT_END)
    if t_begin < 0 or t_end < 0:
        print("error: the template log has no format markers - plugin is broken",
              file=sys.stderr)
        return 1
    block = template[t_begin:t_end + len(FORMAT_END)]

    path = project / LOG_REL
    if not path.exists():
        print("  = %s absent; nothing to refresh (step 9a seeds it)" % LOG_REL)
        return 0
    text = path.read_text(encoding="utf-8")
    begin, end = text.find(FORMAT_BEGIN), text.find(FORMAT_END)
    if begin < 0 or end < 0:
        print("  = %s has no format markers (seeded before 0.8.0) - left untouched"
              % LOG_REL)
        return 0
    updated = text[:begin] + block + text[end + len(FORMAT_END):]
    if updated == text:
        print("  = %s format section already current" % LOG_REL)
    else:
        path.write_text(updated, encoding="utf-8")
        print("  ^ %s format section refreshed (entries untouched)" % LOG_REL)
    return 0


# --------------------------------------------------------------------------
# `full`: the one definition of a complete install (gh#9 / H-047 / H-048)


def _copy_template(plugin_root, project, rel, *, only_if_absent):
    src = plugin_root / "templates" / rel
    dst = project / rel
    if not src.is_file():
        print("  ! %s: missing from the plugin templates - skipped" % rel)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and only_if_absent:
        print("  = %s already exists - left untouched" % rel)
        return False
    existed = dst.exists()
    if existed and sha256(src) == sha256(dst):
        print("  = %s already current" % rel)
        return False
    shutil.copyfile(src, dst)
    print("  %s %s %s" % ("^" if existed else "+", rel, "refreshed" if existed else "installed"))
    return True


def create_extension(plugin_root, project):
    """devtools_ext/: the stub the project OWNS (never overwritten) + the example.

    Both get a .uid sidecar minted (plant:G-002): these land OUTSIDE lint's
    default `uid_check_ignore` (`res://addons/`, `res://tools/`), so a fresh
    install used to fail its own step-12 smoke check with three missing-sidecar
    warnings. `ensure_uid_sidecars` never rewrites an existing one.
    """
    _copy_template(plugin_root, project, "devtools_ext/commands.gd", only_if_absent=True)
    _copy_template(plugin_root, project, "devtools_ext/commands.example.gd", only_if_absent=False)
    ensure_uid_sidecars(plugin_root, project, [
        p for p in (project / "devtools_ext" / "commands.gd",
                    project / "devtools_ext" / "commands.example.gd") if p.is_file()])
    return 0


def seed_tests(plugin_root, project):
    """test/unit/test_selftest.gd only when the dir is missing or empty; the
    sequence example always (it is a schema example, safe to refresh)."""
    unit = project / "test" / "unit"
    unit.mkdir(parents=True, exist_ok=True)
    if any(unit.iterdir()):
        print("  = test/unit already has files - left untouched")
    else:
        _copy_template(plugin_root, project, "test/unit/test_selftest.gd", only_if_absent=True)
    _copy_template(plugin_root, project, "test/sequences/smoke.json", only_if_absent=False)
    seed = unit / "test_selftest.gd"
    if seed.is_file():
        ensure_uid_sidecars(plugin_root, project, [seed])  # plant:G-002
    return 0


def merge_claude_md(plugin_root, project):
    """The delimited harness section: create / replace-in-place / append.
    Never rewrites anything outside the markers."""
    section = (plugin_root / "templates" / "CLAUDE.harness.md").read_text(encoding="utf-8")
    if CLAUDE_BEGIN not in section or CLAUDE_END not in section:
        print("error: templates/CLAUDE.harness.md has no BEGIN/END markers - plugin is broken",
              file=sys.stderr)
        return 1
    path = project / "CLAUDE.md"
    if not path.exists():
        path.write_text(section, encoding="utf-8")
        print("  + CLAUDE.md created with the harness section")
        return 0
    text = path.read_text(encoding="utf-8")
    begin, end = text.find(CLAUDE_BEGIN), text.find(CLAUDE_END)
    if begin >= 0 and end >= 0:
        block = section[section.find(CLAUDE_BEGIN):section.find(CLAUDE_END) + len(CLAUDE_END)]
        updated = text[:begin] + block + text[end + len(CLAUDE_END):]
        if updated == text:
            print("  = CLAUDE.md harness section already current")
        else:
            path.write_text(updated, encoding="utf-8")
            print("  ^ CLAUDE.md harness section refreshed (rest untouched)")
        return 0
    path.write_text(text.rstrip("\n") + "\n\n" + section, encoding="utf-8")
    print("  ^ CLAUDE.md: harness section appended (existing content untouched)")
    return 0


def seed_log(plugin_root, project):
    """log-devtools.md: seed if absent, else refresh only its Format section."""
    if not (project / LOG_REL).exists():
        return 0 if _copy_template(plugin_root, project, LOG_REL, only_if_absent=True) else 1
    return refresh_format_block(plugin_root, project)


def wire_stop_hook(project, python_name):
    """Merge the devtools-log Stop hook into .claude/settings.json. Never
    overwrites: a project may already have hooks, and a malformed file may hold
    permissions the user depends on - that is reported, not replaced."""
    settings = project / ".claude" / "settings.json"
    data = {}
    if settings.exists():
        text = settings.read_text(encoding="utf-8").strip()
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            print("error: %s is not valid JSON; refusing to overwrite it. Fix it and re-run, "
                  "or wire the Stop hook by hand." % settings, file=sys.stderr)
            return 1
    stop = data.setdefault("hooks", {}).setdefault("Stop", [])
    if any(HOOK_MARKER in h.get("command", "") for e in stop for h in e.get("hooks", [])):
        print("  = devtools-log Stop hook already installed")
        return 0
    stop.append({"hooks": [{
        "type": "command",
        "command": 'cd "${CLAUDE_PROJECT_DIR:-.}" && %s tools/check_devtools_log.py' % python_name,
        "timeout": 10,
        "statusMessage": "Checking devtools log...",
    }]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("  + devtools-log Stop hook installed in .claude/settings.json (%s)" % python_name)
    return 0


_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


def wire_autoload(project):
    """Add the DevTools autoload to project.godot, LAST in [autoload].

    Last, so every game autoload the extension's handlers depend on is ready
    before DevTools loads and calls register_commands(). This rule used to exist
    only as prose in the slash command; the first automated installer written
    against it put DevTools FIRST (a `text.replace("[autoload]", ...)`), which
    boots and answers `ping` and fails only when a project verb touches an
    autoload that is not there yet (gh#9). Never touches, reorders or removes a
    game autoload. Idempotent: a present `DevTools=` line is left alone.
    Returns "present" | "appended" | "created".
    """
    path = project / "project.godot"
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if any(re.match(r"^\s*DevTools\s*=", ln) for ln in lines):
        print("  = DevTools autoload already present")
        return "present"

    start = None
    for i, ln in enumerate(lines):
        m = _SECTION_RE.match(ln)
        if m and m.group(1).strip() == "autoload":
            start = i
            break

    if start is None:
        body = text.rstrip("\n")
        body += ("\n\n" if body else "") + "[autoload]\n\n" + AUTOLOAD_LINE + "\n"
        path.write_text(body, encoding="utf-8")
        print("  + [autoload] section created with DevTools")
        return "created"

    # The section runs to the next header or EOF; insert after its last
    # non-blank line so DevTools follows every autoload the project declared.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _SECTION_RE.match(lines[j]):
            end = j
            break
    last = start
    for j in range(start + 1, end):
        if lines[j].strip():
            last = j
    lines.insert(last + 1, AUTOLOAD_LINE)
    path.write_text("\n".join(lines), encoding="utf-8")
    print("  + DevTools autoload appended last in [autoload]")
    return "appended"


_MAIN_SCENE_RE = re.compile(r'^run/main_scene\s*=\s*"([^"]*)"', re.M)
_TSCN_UID_RE = re.compile(r'\buid="([^"]+)"')


def detect_main_scene(project):
    """`res://...` for every consumer downstream, never a raw `uid://` (gh#19.1).

    Godot 4.4+ writes `run/main_scene` as a uid by default. Every reader of
    `devtools_config.json`'s `main_scene` (the CLI, the docs, step 7's own
    hud_layer_name detection) expects a `res://` path, so resolve it here once
    rather than let a uid leak into config and confuse every consumer after it.
    """
    try:
        text = (project / "project.godot").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = _MAIN_SCENE_RE.search(text)
    if not m:
        return ""
    value = m.group(1)
    if not value.startswith("uid://"):
        return value
    # A .tscn carries its own id in the gd_scene header's uid= attribute; a .gd
    # (main scene can be a script) carries it in a .uid sidecar instead.
    for scene in project.rglob("*.tscn"):
        try:
            head = scene.open(encoding="utf-8", errors="replace").readline()
        except OSError:
            continue
        um = _TSCN_UID_RE.search(head)
        if um and um.group(1) == value:
            return "res://" + scene.relative_to(project).as_posix()
    for sidecar in project.rglob("*.uid"):
        try:
            if sidecar.read_text(encoding="utf-8").strip() == value:
                return "res://" + sidecar.with_suffix("").relative_to(project).as_posix()
        except OSError:
            continue
    print("  ! run/main_scene is %s and no .tscn or .uid sidecar in the project claims "
          "that id; recording it unresolved" % value)
    return value


def install_full(plugin_root, project, overrides, *, hook=True, hook_python=None):
    """Everything a working install needs, in the order the slash command does it."""
    steps = [
        ("files", lambda: install_files(plugin_root, project, SHIPPED_FILES)),
    ]
    detected = {}
    if "main_scene" not in overrides:
        main_scene = detect_main_scene(project)
        if main_scene:
            detected["main_scene"] = main_scene
    merged_overrides = dict(detected)
    merged_overrides.update(overrides)
    steps += [
        ("config", lambda: patch_config(plugin_root, project, merged_overrides)),
        ("devtools_ext", lambda: create_extension(plugin_root, project)),
        ("test seed", lambda: seed_tests(plugin_root, project)),
        ("CLAUDE.md", lambda: merge_claude_md(plugin_root, project)),
        ("log-devtools.md", lambda: seed_log(plugin_root, project)),
    ]
    if hook:
        name = hook_python or ("python" if sys.platform == "win32" else "python3")
        steps.append(("Stop hook", lambda: wire_stop_hook(project, name)))
    steps.append(("autoload", lambda: 0 if wire_autoload(project) else 1))

    worst = 0
    for label, fn in steps:
        print("[full] %s" % label)
        rc = fn()
        if rc:
            print("  ! %s step failed (rc %s)" % (label, rc), file=sys.stderr)
            worst = max(worst, rc)
    if detected:
        print("[full] detected: %s" % ", ".join("%s=%s" % kv for kv in sorted(detected.items())))
    print("[full] not done here: hud_layer_name detection, Godot binary (step 11), --import "
          "+ lint smoke check (step 12). Pass --set for the first; run `config --set` for the "
          "second.")
    return worst


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
    ap.add_argument("mode", choices=["full", "files", "config", "format-block"])
    ap.add_argument("rels", nargs="*", metavar="REL",
                    help="files mode: template-relative paths to install")
    ap.add_argument("--project", required=True, help="Target Godot project root")
    ap.add_argument("--plugin-root", default=str(Path(__file__).resolve().parent.parent),
                    help="Plugin root (defaults to this script's repo)")
    ap.add_argument("--set", dest="sets", action="append", metavar="KEY=VALUE",
                    help="config/full mode: a detected value, e.g. --set main_scene=res://main.tscn")
    ap.add_argument("--no-hook", action="store_true",
                    help="full mode: do not wire the Stop hook into .claude/settings.json")
    ap.add_argument("--hook-python", default=None, metavar="NAME",
                    help="full mode: interpreter name the Stop hook runs (default: python on "
                         "Windows, python3 elsewhere). Use the name that EXECUTES on this "
                         "machine - the Store alias stub satisfies `command -v` and then refuses.")
    args = ap.parse_args()

    plugin_root = Path(args.plugin_root).expanduser().resolve()
    project = Path(args.project).expanduser().resolve()
    if not (project / "project.godot").is_file():
        print("error: no project.godot at %s" % project, file=sys.stderr)
        return 1

    if args.mode == "full":
        return install_full(plugin_root, project, parse_set(args.sets),
                            hook=not args.no_hook, hook_python=args.hook_python)
    if args.mode == "files":
        if not args.rels:
            print("error: name at least one file to install", file=sys.stderr)
            return 1
        return install_files(plugin_root, project, args.rels)
    if args.mode == "format-block":
        return refresh_format_block(plugin_root, project)
    return patch_config(plugin_root, project, parse_set(args.sets))


if __name__ == "__main__":
    sys.exit(main())
