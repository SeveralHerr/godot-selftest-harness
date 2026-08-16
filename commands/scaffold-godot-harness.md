---
description: Install the Godot self-test harness (file-based DevTools bridge, headless lint/test runners, and a registry extension) into the Godot 4.x project in the current directory.
---

# Scaffold the Godot self-test harness

You are installing the **godot-selftest-harness** into a Godot 4.x project. The
harness is a game-agnostic, file-based automation bridge plus headless lint/test
runners. This command copies template assets, wires an autoload, writes a config
file with values detected from the target project, and runs a smoke check.

Template assets live under `${CLAUDE_PLUGIN_ROOT}/templates/`. Never bake the
project name, paths, or other project-specific values into copied files — always
put detected values into `devtools_config.json` (step 7).

Every step below must be **idempotent**: re-running this command on an
already-scaffolded project must not corrupt it, duplicate autoload lines, or
clobber project-authored files.

**One installer, not thirteen hand-runs.** Steps 3–10 are performed by a single call
to `tools/scaffold_install.py full` (step 3). It is the one definition of "installed"
— `check_templates.py` exercises the same code, and any automation (CI, a benchmark, a
grader) can call it with no LLM in the loop (gh#9). The prose in steps 4–10 stays as
the specification of what `full` did, the report you write from its output, and the
without-Python fallback. Do **not** re-run those steps by hand after `full` unless the
Python interpreter probe in step 1 failed.

Work through the steps in order. Report a short summary at the end.

---

## Step 1 — Resolve and validate the target project

1. Determine the target project root: use the first argument to this command if
   one was given (`$ARGUMENTS`), otherwise use the current working directory.
   Resolve it to an absolute path and call it `ROOT`.
2. Confirm `ROOT/project.godot` exists. If not, **abort** with:
   `No project.godot found at <ROOT>. Run this from a Godot project directory, or pass the project path as an argument.`
3. Confirm the project is Godot **4.x**. Read `project.godot` and check the
   `config/features` array (e.g. `config/features=PackedStringArray("4.6", ...)`).
   If the feature list shows a 3.x version, or no 4.x tag is present, **abort**
   with a clear message naming the detected version and that this harness
   requires Godot 4.x.

```bash
ROOT="${1:-$PWD}"
ROOT="$(cd "$ROOT" 2>/dev/null && pwd)" || { echo "Path not found"; exit 1; }
test -f "$ROOT/project.godot" || { echo "No project.godot at $ROOT"; exit 1; }
grep -n 'config/features' "$ROOT/project.godot" || echo "WARN: no config/features line; verify this is Godot 4.x"
```

4. **Resolve a Python interpreter now** — steps 3, 4, 7 and 9 all need it.
   **Probe by executing, never with `command -v`.** On Windows, `command -v python3`
   succeeds against the Microsoft Store *App execution alias* stub, which then refuses
   to run (`Python was not found; run without arguments to install…`). Existence is not
   executability:

   ```bash
   PY=""
   for c in python3 python py; do
     if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
   done
   [ -z "$PY" ] && echo "WARN: no working Python. Falling back to plain copies: files will be backed up on any difference and devtools_config.json must be merged by hand. devtools.py needs Python too, so fix this before using the bridge."
   echo "Python: ${PY:-none}"
   ```

   If no Python is found, use the `cp`-based fallbacks noted in steps 3, 4 and 7 and
   say clearly in the summary which safeguards were skipped.

5. **On a refresh, work on a branch.** A re-scaffold rewrites a dozen files across
   the project. Landing that straight on the working branch means it gets discovered
   afterwards as unexplained churn in `git status` rather than read as a diff. So when
   this is a **refresh** (the harness is already installed) of a **git repo** with a
   **clean working tree**, do it on `harness/refresh-<version>`:

   ```bash
   VER="$("$PY" -c "import json,os;print(json.load(open(os.environ['P']))['version'])" 2>/dev/null)"
   if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
     CUR="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
     if [ ! -f "$ROOT/addons/godot_selftest/dev_tools.gd" ]; then
       echo "Fresh install on $CUR - no refresh branch needed."
     elif [ -n "$(git -C "$ROOT" status --porcelain -- . ':!.beads')" ]; then
       # ':!.beads' (gh#25.2): a beads-tracked project auto-exports
       # .beads/issues.jsonl / interactions.jsonl on every `bd update --claim`,
       # so a fan-out session's tree reads dirty from that churn alone almost
       # every time a refresh runs during real work - degrading this branch
       # protection to "apply directly on current branch" in exactly the
       # sessions most likely to want a reviewable diff. Real code WIP still
       # trips this check; beads' own export does not.
       echo "WARN: uncommitted changes on $CUR. Staying here so the refresh is not mixed"
       echo "      into your work-in-progress; review with 'git diff' before committing."
     elif case "$CUR" in harness/refresh-*) true;; *) false;; esac; then
       echo "Already on $CUR - continuing here."
     else
       BR="harness/refresh-$VER"
       git -C "$ROOT" checkout -b "$BR" 2>/dev/null || git -C "$ROOT" checkout "$BR"
       echo "Refreshing on branch $BR (from $CUR)."
     fi
   else
     echo "Not a git repo - the refresh will not be reviewable as a diff."
   fi
   ```

   Set `P="${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json"` before running it. Record
   the branch name (and the branch you came from) for the summary in step 13.

   **Never commit on the user's behalf**, here or anywhere in this command. The branch
   exists so the change can be *read*; whether it lands is theirs to decide.

6. **On a refresh, confirm this plugin install is not OLDER than what's already
   installed (gh#25.1).** `${CLAUDE_PLUGIN_ROOT}` is a cached copy — a stale
   marketplace cache can sit behind the project's own installed version for hours.
   Nothing downstream checks this: step 4's pristine-file test only asks "does this
   match *a* released version," so a real, current file matches
   `harness_history.json` and gets silently overwritten with an OLDER one, no
   backup, no warning, and `full` reports it as a clean refresh. Check before
   running `full`, not after:

   ```bash
   MANIFEST="$ROOT/addons/godot_selftest/.harness_manifest.json"
   if [ -f "$MANIFEST" ] && [ -n "$PY" ]; then
     INSTALLED_VER="$("$PY" -c "import json;print(json.load(open('$MANIFEST')).get('harness_version',''))" 2>/dev/null)"
     PLUGIN_VER="$("$PY" -c "import json;print(json.load(open('${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json')).get('version',''))" 2>/dev/null)"
     if [ -n "$INSTALLED_VER" ] && [ -n "$PLUGIN_VER" ]; then
       OLDER="$("$PY" -c "
a = tuple(int(x) for x in '$PLUGIN_VER'.split('.'))
b = tuple(int(x) for x in '$INSTALLED_VER'.split('.'))
print('yes' if a < b else 'no')" 2>/dev/null)"
       if [ "$OLDER" = "yes" ]; then
         echo "ABORT: this plugin install is $PLUGIN_VER, but the project already has"
         echo "  $INSTALLED_VER installed. Running 'full' now would DOWNGRADE every"
         echo "  shipped file with no warning (step 4's pristine check can't tell newer"
         echo "  from older, only 'released' from 'project-edited')."
         echo "  Update the plugin source this command is running from (refresh its"
         echo "  marketplace cache, or pull the repo if sideloaded), restart, then"
         echo "  re-invoke this command. NOT proceeding with step 3."
       fi
     fi
   fi
   ```

   Compare as version tuples, not strings — `"0.9.0" > "0.10.0"` lexically, wrongly.
   Absent a manifest (fresh install) or an unreadable version on either side, this
   check has nothing to compare and is silently skipped — that is the normal case,
   not a failure. If it prints ABORT, stop: do not run step 3's installer.

## Step 2 — Parse project identity (for reporting only)

Read these keys from `project.godot` (do **not** write them into any file):

- `application/config/name` — the project name.
- `application/config/use_custom_user_dir` (bool, may be absent → false).
- `application/config/custom_user_dir_name` (string, may be absent).
- `run/main_scene` — used in step 7.

Use these to compute and later report the exact `user://` directory the Python
client polls (see step 13). The default per-platform Godot userdata dir is:

- macOS: `~/Library/Application Support/Godot/app_userdata/<name>/`
- Linux: `~/.local/share/godot/app_userdata/<name>/`
- Windows: `%APPDATA%\Godot\app_userdata\<name>\`

If `use_custom_user_dir` is true, the path is instead
`.../Godot/app_userdata/<custom_user_dir_name>/` (or a fully custom location on
some platforms). Keep this for the final report only; the config file never
stores the name.

## Step 3 — Run the installer (`full`): addon core, tools, config, extension, tests, CLAUDE.md, log, hook, autoload

One call does steps 3–10. Detect `hud_layer_name` first (step 7 says how: the name of
the first `CanvasLayer` in the main scene, falling back to the first `CanvasLayer`
anywhere under `scan_root`, else `"HUD"`) and pass it; `main_scene` is detected by the
installer from `run/main_scene`, resolving a `uid://` value to its `res://` path
itself (gh#19.1) so this detection can open the right scene. Pass `--hook-python "$PY"` so the
`Stop` hook runs the interpreter that actually executes on this machine (step 1's
probe), not the Store alias.

```bash
"$PY" "${CLAUDE_PLUGIN_ROOT}/tools/scaffold_install.py" full --project "$ROOT" \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  --hook-python "$PY" \
  --set hud_layer_name=<detected>
```

**Its first line is `[version] ...`, and it is the one line to read before anything
else (gh#32).** The installer compares the plugin root's version with the version the
project already runs (`_scaffold_defaults.harness_version`, or the installed
`# harness-version:` stamp on an older install) and says which of four things this
call is: `fresh install of X`, `already at X - this is a same-version refresh, not an
upgrade`, `upgrade Y -> X`, or `DOWNGRADE Y -> X` — the last is **refused (exit 2,
nothing touched)** unless `--allow-downgrade` is passed, because a pristine file is
overwritten without a `.bak` and a backwards refresh leaves no trace at all. Both of the
non-upgrade cases exist because the skill loads from a plugin cache pinned at ONE
version and every path in this file is interpolated from that root: a user who wants a
newer release than `${CLAUDE_PLUGIN_ROOT}` holds needs `/plugin marketplace update
godot-selftest-harness` then `/plugin update godot-selftest-harness`, or may point
`--plugin-root` at a newer clone. **Carry the `[version]` line into the summary
verbatim**; `[full] harness: <transition>` at the end repeats it.

It prints one line per file/key/step. Read it and carry into the summary: every
`.bak` it created (step 4), every `.uid` it minted (step 4), which config keys it kept
as project-owned (step 7), whether `commands.gd` / `CLAUDE.md` / `log-devtools.md`
already existed (steps 5, 8, 9), and how the autoload landed (`present` / `appended
last` / `section created`, step 10). It leaves for later steps only what needs a
running engine or a scene reader: the Godot binary (step 11) and the import + lint
smoke check (step 12).

Without Python: fall through steps 3–10 below by hand, and say in the summary that
pristine-file detection, `.uid` minting and the ownership-tracking config merge were
skipped.

### What `full` did for the addon core (was step 3)

Installed `addons/godot_selftest/dev_tools.gd` and `scene_validator.gd` into
`res://addons/godot_selftest/` (`devtools_config.json` is merged, not copied — step 7).
Equivalent to:

```bash
"$PY" "${CLAUDE_PLUGIN_ROOT}/tools/scaffold_install.py" files --project "$ROOT" \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  addons/godot_selftest/dev_tools.gd \
  addons/godot_selftest/scene_validator.gd
```

Without Python: `mkdir -p "$ROOT/addons/godot_selftest"` and `cp` the two files
from `${CLAUDE_PLUGIN_ROOT}/templates/addons/godot_selftest/`.

The validator is namespaced (`GodotSelftestSceneValidator`) to avoid `class_name`
collisions. See step 4 for what the installer does about existing files.

## Step 4 — Install the tool scripts (done by `full` in step 3)

Same installer, for `res://tools/` — `full` installs exactly this list (it is
`SHIPPED_FILES` in `scaffold_install.py`, the one list `record_version.py` also stamps):

```bash
"$PY" "${CLAUDE_PLUGIN_ROOT}/tools/scaffold_install.py" files --project "$ROOT" \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  tools/lint_project.gd tools/run_tests.gd tools/eval.gd tools/capture.gd tools/devtools.py \
  tools/check_devtools_log.py tools/upstream_gaps.py tools/verify_ledger.py \
  tools/import_check.py tools/name_check.py tools/coverage_check.py
chmod +x "$ROOT/tools/devtools.py" 2>/dev/null || true
```

**Back up only what the project actually edited.** The installer records the sha256
of everything it writes in `addons/godot_selftest/.harness_manifest.json`, and the
plugin ships `harness_history.json` with the hashes of every previously released
version. A file that matches either is a copy this harness put there and nobody
touched, so it is overwritten silently. Only a file that matches neither is backed up
to `<file>.bak`, and the installer says so loudly.

This matters because the old rule — back up on any byte difference — meant a plain
version bump left `lint_project.gd.bak`, `run_tests.gd.bak` and `devtools.py.bak`
sitting in a real project as untracked noise, protecting edits that did not exist.
Scaffold could never clean those up either, because it had no way to tell its own
leftovers from a file the user made. Hashes are compared with line endings normalized,
so a CRLF checkout is not mistaken for an edit.

Report every `.bak` it does create: those are real local edits about to be replaced,
and they usually belong in `devtools_ext/commands.gd` or upstream in the plugin.

**`.uid` sidecars are minted here, and only this summary will mention them**
(`moving-in:G-004`). Every `.gd` the installer writes gets a `<file>.gd.uid` beside it
if it has none — a real `uid://…`, minted offline with the same ResourceUID encoding
`devtools.py new-uid` uses. This used to be skipped on the grounds that ids are engine
assigned, which left `tools/capture.gd` landing on a 0.16.0 refresh with no sidecar next
to three siblings that had one; it reads as drift on the next refresh and the project's
own lint cannot report it, because `uid_check_ignore` defaults to `res://addons/` and
`res://tools/` — precisely where scaffold writes. Lint prints `UIDs: OK` either way. Do
not "fix" that by widening `uid_check_ignore`: the same list gates the class-cache,
compile, shader and string-ref passes, and opening it drags all four across the addon.

An **existing** `.uid` is never rewritten and never backed up. A uid is an identity —
regenerating one per refresh would break every scene and `preload` pointing at the
script — so this is a no-op on the second run, and the sidecars are deliberately absent
from `.harness_manifest.json` (a recorded hash would make a later refresh treat the
project's own id as a stale harness file and overwrite it). Projects declaring Godot
older than 4.4 get none, and the installer says so.

Without Python: `cp` each file, backing up on any difference (the old behavior), and
say in the summary that pristine-file detection **and** `.uid` minting were skipped.
Mint them afterwards with `python tools/devtools.py new-uid --write <file>.gd` — it
refuses to overwrite an existing one, so it is safe to run over the whole set.

## Step 5 — Create the registry extension (never overwrite) (done by `full` in step 3)

`res://devtools_ext/commands.gd` is where the project registers its own debug
verbs. Create it from the stub **only if it does not already exist** — never
overwrite a project's real extension. Also copy the reference example alongside
it (safe to refresh).

```bash
mkdir -p "$ROOT/devtools_ext"
if [ ! -f "$ROOT/devtools_ext/commands.gd" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/devtools_ext/commands.gd" "$ROOT/devtools_ext/commands.gd"
  echo "Created devtools_ext/commands.gd from stub."
else
  echo "devtools_ext/commands.gd already exists — left untouched."
fi
cp "${CLAUDE_PLUGIN_ROOT}/templates/devtools_ext/commands.example.gd" "$ROOT/devtools_ext/commands.example.gd"
# These land OUTSIDE lint's default uid_check_ignore, so mint their sidecars
# (plant:G-002; `full` does this itself). new-uid --write refuses an existing one.
for f in commands.gd commands.example.gd; do
  [ -f "$ROOT/devtools_ext/$f.uid" ] || "$PY" "$ROOT/tools/devtools.py" new-uid --write "$ROOT/devtools_ext/$f"
done
```

## Step 6 — Seed the project's selftest and a sequence example (only if empty) (done by `full` in step 3)

Create `res://test/unit/` and copy `test_selftest.gd` **only if the test dir is
missing or empty** (do not litter a project that already has tests). Always copy
`test/sequences/smoke.json` as a schema example (safe to refresh).

The seeded file is named `test_selftest.gd`, not `test_example.gd`, and its header
says *add to this* rather than *delete this*. That naming is the point: a model
asked to verify a change will write a selftest whether or not one exists, and a
file that reads as disposable gets a throwaway written beside it instead of being
extended. Installs predating 0.19.0 keep their `test_example.gd` — the dir is
non-empty, so this step leaves it alone, which is the correct behavior.

```bash
mkdir -p "$ROOT/test/unit" "$ROOT/test/sequences"
if [ -z "$(ls -A "$ROOT/test/unit" 2>/dev/null)" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/test/unit/test_selftest.gd" "$ROOT/test/unit/test_selftest.gd"
  echo "Seeded test/unit/test_selftest.gd — this project's selftest; extend it, don't replace it"
else
  echo "test/unit already has files — left untouched."
fi
cp "${CLAUDE_PLUGIN_ROOT}/templates/test/sequences/smoke.json" "$ROOT/test/sequences/smoke.json"
# plant:G-002 - the seed is outside uid_check_ignore too
[ -f "$ROOT/test/unit/test_selftest.gd" ] && [ ! -f "$ROOT/test/unit/test_selftest.gd.uid" ]   && "$PY" "$ROOT/tools/devtools.py" new-uid --write "$ROOT/test/unit/test_selftest.gd"
```

## Step 7 — Write `devtools_config.json` with detected values (done by `full` in step 3; `config --set` for anything detected later)

Write `res://addons/godot_selftest/devtools_config.json` from the shipped schema plus
the values detected below, preserving anything the project customized:

```bash
"$PY" "${CLAUDE_PLUGIN_ROOT}/tools/scaffold_install.py" config --project "$ROOT" \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  --set main_scene=res://<detected>.tscn \
  --set hud_layer_name=<detected>
```

`--set` takes `key=value`, JSON-parsed when it parses (so `--set fps_min=60` is a
number). Pass only the keys you actually detected; everything else comes from the
shipped schema below.

**How it decides what to overwrite.** It records what it left behind in a
`_scaffold_defaults` block inside the config — the values plus the list of keys
scaffold still owns. A key that still holds what scaffold last wrote gets updated to
the new default; a key the project has edited is kept and becomes **project-owned
permanently**, even if its value later happens to equal the default again. That last
part is the whole point: judging by "is this still the default value?" cannot tell a
key nobody touched from one deliberately set back to the default, so a project that
set `hud_layer_name` to `"HUD"` on purpose would have it silently re-detected away on
the next refresh. New keys are always added; keys the project invented are never
removed. Re-running changes nothing.

On the very first run against a pre-0.5.0 install there is no record yet, so it falls
back to "matches the shipped default → mine, otherwise yours" for that one run, and
records the outcome. Report which keys it kept and which it updated.

Without Python: write the schema below by hand if the file is absent; if it exists,
add only missing keys and change nothing else.

Detect:

- `main_scene` — from `run/main_scene` in `project.godot`. The installer resolves this
  itself now (gh#19.1) — Godot 4.4+ writes it as `uid://…` by default, and `full`
  already maps that back to the owning `.tscn`'s `res://` path before it reaches
  `devtools_config.json`, so treat the value `full` reports as the scene to open, not
  the raw config line.
- `hud_layer_name` — best effort: open that main scene and use the **name of the
  first `CanvasLayer`** you find. If the main scene has no `CanvasLayer` at all (the
  HUD is instantiated at runtime rather than present in the scene file — common),
  grep `scan_root` for `type="CanvasLayer"` across every `.tscn` and use the first
  name found. Only default to `"HUD"` if neither search finds one.
- `extension_script`, `validator_script`, `test_dir`, `scan_root` — the defaults
  below (they match where steps 3–6 placed things).

```json
{
  "validator_script": "res://addons/godot_selftest/scene_validator.gd",
  "extension_script": "res://devtools_ext/commands.gd",
  "hud_layer_name": "HUD",
  "test_dir": "res://test/unit",
  "scan_root": "res://",
  "uid_check_ignore": ["res://addons/", "res://tools/"],
  "name_check_extra_types": [],
  "name_check_ignore": [],
  "reach_aliases": {},
  "reach_headless_dirs": ["tools/"],
  "fps_min": 30,
  "orphan_max": 0,
  "orphan_growth_max": 20,
  "safe_area_inset": { "left": 0, "top": 0, "right": 0, "bottom": 0 },
  "main_scene": "",
  "entry_hook": { "node_path": "", "method": "" },
  "entry_points": {},
  "mute": true,
  "log_files": ["log-devtools.md"],
  "log_check_globs": [],
  "log_check_block": false,
  "log_check_dated_entry": true,
  "log_check_value": true
}
```

Notes on the newer keys, so a patch of an existing config doesn't get them wrong:

- `name_check_extra_types` and `name_check_ignore` both default to empty, and empty is
  the right answer for a fresh install — do **not** try to detect values for them.
  `name_check_extra_types` exists for types a GDExtension registers at runtime, which
  `--dump-extension-api` cannot see and `name_check.py` would therefore call unknown;
  a project only learns which ones it needs by running the checker and reading the
  false positives. Adding guesses here would suppress real findings from day one.
- `orphan_max` is retained for compatibility but is **not** the gate — `0` is
  unreachable (a real project reports dozens of orphans on a fresh launch). `/verify`
  gates on `orphan_growth_max`, growth vs. the session baseline.
- `safe_area_inset` all-zero **disables** the safe-area check, so scaffolding adds no
  new findings to an existing project. Populate it only where an overlay, notch, or
  rounded corner actually eats the viewport edge.
- `entry_points` is optional; each entry is `{scene, node_path, method, args, match}`
  and lets `/verify` reach a scene the single `entry_hook` can't (a boss room, a shop).
- The `log_*` keys drive the `Stop` hook from step 9.

Set `main_scene` and `hud_layer_name` from detection; leave the rest at defaults
unless the project already customized them.

The `_scaffold_defaults` block the installer writes alongside these keys is
bookkeeping, not configuration — every consumer ignores unknown keys. It is worth
committing (it is what makes the next refresh non-destructive); deleting it just
resets the tracking to the first-run heuristic.

## Step 8 — Install/refresh the CLAUDE.md guidance section (done by `full` in step 3)

Create or update `<ROOT>/CLAUDE.md` so future Claude sessions know the harness
exists and how to drive it. The full section body — including its delimiter
markers — lives in `${CLAUDE_PLUGIN_ROOT}/templates/CLAUDE.harness.md`. Its first
and last lines are exactly:

```
<!-- BEGIN godot-selftest-harness -->
<!-- END godot-selftest-harness -->
```

Apply this **merge strategy** (fully idempotent — re-running never duplicates the
section and never clobbers the user's own `CLAUDE.md` content):

1. **No `CLAUDE.md`** → create it containing exactly the template contents.
2. **Exists with the BEGIN marker** → replace everything between the BEGIN and
   END markers (inclusive) with the current template contents.
3. **Exists without the marker** → append a blank line plus the template
   contents to the end; leave all existing content untouched.

```bash
SECTION="${CLAUDE_PLUGIN_ROOT}/templates/CLAUDE.harness.md"
CLAUDE_MD="$ROOT/CLAUDE.md"
BEGIN='<!-- BEGIN godot-selftest-harness -->'
END='<!-- END godot-selftest-harness -->'

if [ ! -f "$CLAUDE_MD" ]; then
  # Case 1 — absent: create from template (includes its own markers)
  cp "$SECTION" "$CLAUDE_MD"
  echo "Created CLAUDE.md with harness guidance section."
elif grep -qF "$BEGIN" "$CLAUDE_MD"; then
  # Case 2 — marker present: replace the marked block (inclusive) in place
  tmp="$(mktemp)"
  awk -v begin="$BEGIN" -v end="$END" -v repl="$SECTION" '
    $0 == begin {
      skipping = 1
      while ((getline line < repl) > 0) print line
      close(repl)
      next
    }
    skipping && $0 == end { skipping = 0; next }
    skipping { next }
    { print }
  ' "$CLAUDE_MD" > "$tmp" && mv "$tmp" "$CLAUDE_MD"
  echo "Refreshed harness guidance section in CLAUDE.md."
else
  # Case 3 — exists, no marker: append (never rewrite existing content)
  printf '\n' >> "$CLAUDE_MD"
  cat "$SECTION" >> "$CLAUDE_MD"
  echo "Appended harness guidance section to CLAUDE.md."
fi
```

The template section is deliberately **lean and reference-style** (a pointer /
cheat-sheet, not a manual) because `CLAUDE.md` is always-on, per-session context.
Keep the full procedures in `/verify`, this command, and `REFERENCE.md`.

## Step 9 — Install the devtools gaps log + its `Stop` hook (done by `full` in step 3)

The harness improves from evidence, and the evidence is perishable: the moment a
workaround is found, the friction that forced it is forgotten. `log-devtools.md`
is where each session records what `/verify` or the devtools couldn't do, so those
gaps can later be upstreamed into the harness itself.

Three pieces, all idempotent:

**9a. Seed the log, or refresh its Format section** — an existing log's *entries* are
never touched, but the harness-authored Format section between the
`<!-- BEGIN godot-selftest-harness-format -->` / `<!-- END godot-selftest-harness-format -->`
markers is refreshed in place (same mechanism as the `CLAUDE.md` block in step 8), so a
format change — a new verdict, a new status field — reaches every install instead of being
frozen at whatever version first seeded the file. A pre-marker log (seeded before 0.8.0)
has no markers to find; leave it untouched and say so — never guess at where its format
section ends.

```bash
if [ ! -f "$ROOT/log-devtools.md" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/log-devtools.md" "$ROOT/log-devtools.md"
  echo "Created log-devtools.md"
else
  "$PY" "${CLAUDE_PLUGIN_ROOT}/tools/scaffold_install.py" format-block \
    --project "$ROOT" --plugin-root "${CLAUDE_PLUGIN_ROOT}"
fi
```

**9b. The hook script** — `tools/check_devtools_log.py` was already copied in step 4.
It is a Claude Code `Stop` hook: it asks git whether this session changed Godot code
without also changing `log-devtools.md`, and if so prints a `systemMessage` reminder.
It is written in Python (not shell) because the harness already requires Python 3 for
`devtools.py`, and this way the hook works identically on Windows, macOS, and Linux.
It always exits 0 — a reminder must never break a session.

**9c. Wire the `Stop` hook into `<ROOT>/.claude/settings.json`.** Merge, never
overwrite: a project may already have hooks. Reuse the `$PY` resolved in step 1 (the
shell does not persist between tool calls, so re-run that probe if it is empty).

Remember why that probe executes each candidate rather than using `command -v`: on
Windows the Microsoft Store *App execution alias* stub satisfies `command -v python3`
and then refuses to run, and a hook wired to it fails silently on every turn.

```bash
[ -z "$PY" ] && for c in python3 python py; do
  if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -z "$PY" ] && echo "WARN: no working Python found — skipping the Stop hook (devtools.py also needs it)."

[ -n "$PY" ] && ROOT="$ROOT" PY="$PY" "$PY" - <<'PYEOF'
import json, os, pathlib
root = pathlib.Path(os.environ["ROOT"])
py = os.environ["PY"]
settings = root / ".claude" / "settings.json"
marker = "check_devtools_log.py"
cmd = 'cd "${CLAUDE_PROJECT_DIR:-.}" && %s tools/check_devtools_log.py' % py

data = {}
if settings.exists():
    text = settings.read_text(encoding="utf-8").strip()
    data = json.loads(text) if text else {}

stop = data.setdefault("hooks", {}).setdefault("Stop", [])
if any(marker in h.get("command", "") for e in stop for h in e.get("hooks", [])):
    print("Devtools-log Stop hook already installed — no change.")
else:
    stop.append({"hooks": [{
        "type": "command",
        "command": cmd,
        "timeout": 10,
        "statusMessage": "Checking devtools log...",
    }]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Installed the devtools-log Stop hook in .claude/settings.json")
PYEOF
```

If the project's `.claude/settings.json` is malformed JSON, **stop and report it**
rather than overwriting — that file may hold permissions the user depends on.

The hook is **advisory** by default (it warns; it does not fail or restart the turn).
A project that finds the warning easy to ignore can set `"log_check_block": true` in
`devtools_config.json` to make it a blocking `Stop` instead. Step 8's `CLAUDE.md`
section carries the matching instruction — the hook only reminds; the convention
itself lives in `CLAUDE.md`.

## Step 10 — Wire the DevTools autoload (idempotent) (done by `full` in step 3)

Add the DevTools autoload to `project.godot` **only if it is not already
present**. Find the `[autoload]` section; if there is no `DevTools=` line,
append exactly:

```ini
DevTools="*res://addons/godot_selftest/dev_tools.gd"
```

The leading `*` marks it as an autoload singleton (enabled). Add it **last** in
the `[autoload]` block so that any game autoloads the extension's handlers depend
on are already initialized before DevTools loads and calls
`register_commands()`.

- If there is no `[autoload]` section at all, create one and add the line.
- **Never** touch, reorder, or create any game autoload — only add/verify the
  `DevTools` line.
- **Warn** the user: editing `project.godot` while the project is open in the
  Godot editor can cause the editor to overwrite your change on save. Close the
  editor (or re-open the project) after scaffolding.

```bash
if grep -q '^DevTools=' "$ROOT/project.godot"; then
  echo "DevTools autoload already present — no change."
else
  echo "Add this line to the [autoload] section of project.godot (last):"
  echo '  DevTools="*res://addons/godot_selftest/dev_tools.gd"'
fi
```

## Step 11 — Detect the Godot binary and record it

Resolve the Godot binary in this priority order:

1. `$GODOT_BIN` if set and executable.
2. `godot_bin` already recorded in `devtools_config.json` (a previous run found it).
3. `/Applications/Godot.app/Contents/MacOS/Godot` (macOS).
4. `which godot` / `where godot`.
5. **Windows well-known locations** — a first-time Windows scaffold used to end here
   with a warning and a skipped smoke check (gap `gather:G-027`); these globs are why
   it no longer does:
   - `~/Documents/Godot_v*_win64.exe` (the zip-download convention)
   - `/c/Program Files/Godot*/Godot*.exe`
   - `$LOCALAPPDATA/Programs/Godot/Godot*.exe`

**When the glob matches more than one binary, pick by the project's declared engine
version, never by glob order** (`findmyballs:G-001`). A machine with 4.5.1, 4.6.1 and
4.7.1 side by side gave a project whose `config/features` reads
`PackedStringArray("4.7", "Forward Plus")` the **4.5.1** binary, and recording that in
`godot_bin` would have pinned every later `/verify` two minor versions behind the
project. Match `X.Y` out of `config/features` against the filename; failing a match,
take the **highest** version rather than the first hit.

The resolved version is recorded as `godot_version` alongside `godot_bin`, which is
what lets `name_check.py` notice it is about to answer from a cached index built by a
different engine (`H-032`) without launching anything to find out.

```bash
GODOT=""
if [ -n "$GODOT_BIN" ] && [ -x "$GODOT_BIN" ]; then GODOT="$GODOT_BIN"; fi
if [ -z "$GODOT" ]; then
  GODOT="$("$PY" -c "import json,sys; print(json.load(open('$ROOT/addons/godot_selftest/devtools_config.json')).get('godot_bin',''))" 2>/dev/null)"
  [ -x "$GODOT" ] || GODOT=""
fi
if [ -z "$GODOT" ] && [ -x "/Applications/Godot.app/Contents/MacOS/Godot" ]; then
  GODOT="/Applications/Godot.app/Contents/MacOS/Godot"
fi
if [ -z "$GODOT" ] && command -v godot >/dev/null 2>&1; then
  GODOT="$(command -v godot)"
  # Tier 4 can return a shell WRAPPER, not a binary (gh#7): `~/bin/godot` was a
  # `#!/bin/sh` script exec-ing the real .exe. It runs fine from this shell, but
  # `godot_bin` is executed by Windows Python (devtools.py, name_check.py), which
  # can neither run a `#!` script nor resolve an MSYS `/c/...` path - the same
  # trap as the Store alias in step 1: resolvable HERE is not executable THERE.
  # Resolve a wrapper to its exec target, and put any MSYS path through cygpath.
  if head -c 2 "$GODOT" 2>/dev/null | grep -q '#!'; then
    TARGET="$(sed -n 's/^[[:space:]]*exec[[:space:]]\+"\?\([^"[:space:]]*\)"\?.*/\1/p' "$GODOT" | head -1)"
    if [ -n "$TARGET" ] && [ -x "$TARGET" ]; then
      echo "note: $GODOT is a shell wrapper; recording its exec target $TARGET instead"
      GODOT="$TARGET"
    else
      echo "WARN: $GODOT is a shell script, not a binary, and its exec target could not be"
      echo "      read. Python cannot run it. Set GODOT_BIN to the real executable."
      GODOT=""
    fi
  fi
  case "$GODOT" in /[a-zA-Z]/*) command -v cygpath >/dev/null 2>&1 && GODOT="$(cygpath -m "$GODOT")";; esac
fi
if [ -z "$GODOT" ]; then
  # The project's own X.Y, e.g. "4.7", from config/features in project.godot.
  WANT="$(sed -n 's/.*config\/features=PackedStringArray(\"\([0-9]\+\.[0-9]\+\)\".*/\1/p' \
          "$ROOT/project.godot" 2>/dev/null | head -1)"
  CANDS=""
  for cand in "$HOME"/Documents/Godot_v*_win64.exe \
              "/c/Program Files/Godot"*/Godot*.exe \
              "${LOCALAPPDATA:-/c/nonexistent}"/Programs/Godot/Godot*.exe; do
    [ -x "$cand" ] && CANDS="$CANDS
$cand"
  done
  # Prefer a filename carrying the project's X.Y; otherwise the highest version.
  # `sort -V` puts 4.10 after 4.9, which a lexical sort does not.
  if [ -n "$WANT" ]; then
    GODOT="$(printf '%s\n' "$CANDS" | grep -F "_v${WANT}." | sort -Vr | head -1)"
  fi
  [ -x "$GODOT" ] || GODOT="$(printf '%s\n' "$CANDS" | sed '/^$/d' | sort -Vr | head -1)"
  [ -x "$GODOT" ] || GODOT=""
  if [ -n "$GODOT" ] && [ -n "$WANT" ] && ! printf '%s' "$GODOT" | grep -qF "_v${WANT}."; then
    echo "WARN: project declares Godot $WANT but the best available binary is $GODOT."
    echo "      Install $WANT or set GODOT_BIN; a version-skewed gate reports on the wrong engine."
  fi
fi
if [ -n "$GODOT" ]; then
  echo "Godot binary: $GODOT"
  # Ask the binary what it is rather than trusting the filename.
  GVER="$("$GODOT" --version 2>/dev/null | head -1 | sed 's/^\([0-9]\+\.[0-9]\+\(\.[0-9]\+\)\?\).*/\1/')"
  echo "Godot version: ${GVER:-unknown}"
  # Record both so /verify, later scaffold runs and name_check.py skip the probe.
  # Uses the config mechanism from step 7, so a hand-edited value stays project-owned.
  # ONE invocation (gh#7). Each `config` call proposes the shipped default for every
  # scaffold-owned key it was not passed; two calls here used to reset the godot_bin
  # the first had just written back to "" - silently, because step 12 uses $GODOT
  # rather than reading it back. scaffold_install.py 0.20.0+ also refuses that
  # revert on its own, but the doc must not rely on the guard.
  set -- --set "godot_bin=$GODOT"
  [ -n "$GVER" ] && set -- "$@" --set "godot_version=$GVER"
  "$PY" "${CLAUDE_PLUGIN_ROOT}/tools/scaffold_install.py" config --project "$ROOT" \
    --plugin-root "${CLAUDE_PLUGIN_ROOT}" "$@"
  # Read it back. The one value this step DETECTS is the one nothing downstream
  # in this run consumes from the file, so a wrong write here surfaces only when
  # /verify cannot find an engine.
  REC="$("$PY" -c "import json; print(json.load(open('$ROOT/addons/godot_selftest/devtools_config.json')).get('godot_bin',''))")"
  [ "$REC" = "$GODOT" ] || echo "ERROR: godot_bin read back as '$REC', expected '$GODOT' - the config write did not stick."
else
  echo "WARN: no Godot binary found. Set GODOT_BIN or add godot_bin to devtools_config.json."
fi
```

## Step 12 — Smoke check

First seed the engine API index that `name_check.py` resolves against. This is the
only time the dump is needed on this machine — it runs in a temp directory with no
project, caches under the user's cache dir, and is then shared by every clone and
worktree, so a later parallel session never pays for it:

```bash
"$PY" "$ROOT/tools/name_check.py" -p "$ROOT" --refresh-api || \
  echo "WARN: could not dump the engine API. name_check.py still runs, but its
        engine-name checks will report as SKIPPED until --refresh-api succeeds."
```

Then import, and only then lint. The import registers the `class_name` step 3 just
installed; without it a fresh scaffold **always** reports
`class_cache_stale ... "GodotSelftestSceneValidator" ... absent from
.godot/global_script_class_cache.cfg`, which reads as a defect in the thing just
installed (gh#7). `run_tests.gd` refuses outright on a never-imported project for the
same reason (H-029). Redirect to a file — the Windows build often prints nothing to
the console — and read it back:

```bash
"$GODOT" --headless --path "$ROOT" --import > /tmp/godot_import.log 2>&1
grep -iE "SCRIPT ERROR|Parse Error" /tmp/godot_import.log && echo "import reported parse errors above"
"$GODOT" --headless --path "$ROOT" --script res://tools/lint_project.gd
```

Surface any parser errors or load failures verbatim. A clean exit (code 0) plus
per-scene `OK` / `UIDs: OK` output means the install parses. If it fails, report
the error and stop before claiming success.

**`.bak` cleanup (gap `gather:G-020`).** Once the smoke check passes, list any `.bak`
files *this run* created (step 4 reports them as it backs up). For each, check whether
the backed-up content matches a released version of that file
(`harness_history.json` via the hash check in `tools/scaffold_install.py` semantics):
a `.bak` whose content is pristine-by-history protected nothing — offer its deletion
in the step 13 summary as a safe cleanup. A `.bak` holding real local edits is
**never** deleted by scaffold; it stays until the user ports the edits and removes it
themselves. Stale `.bak`s otherwise read as drift on the next refresh.

## Step 13 — Print next steps

**First, if this was a refresh in a git repo, show it as a diff.** An upgrade that is
only visible as leftover junk in `git status` a week later is not reviewable:

```bash
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "Branch: $(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  git -C "$ROOT" status --short
  echo "---"
  git -C "$ROOT" diff --stat
fi
```

Summarize what that shows, in plain terms:

- **the version transition, first** — the installer's `[full] harness: ...` line
  (`fresh install of X` / `already at X` / `upgrade Y -> X`). `updated from Y` appears
  per file; `Y -> X` appears only here, and it is the one line a reader wants (gh#32).
  If it says `already at X`, say plainly that nothing was upgraded and why (the plugin
  cache is pinned at X);
- which harness files changed, and from which version to which (step 4's report);
- any `.bak` files created — **name them individually**. Each is a local edit about to
  be replaced, and it belongs either in `devtools_ext/commands.gd` or upstream in the
  plugin. Nothing else in this command produces a `.bak`, so if there are none, say so;
- any `.uid` sidecars minted (step 4), **named individually with their ids**, and the
  fact that they need committing alongside the `.gd`. This is the only place they get
  reported: `uid_check_ignore` covers `res://addons/` and `res://tools/`, so the
  project's own lint prints `UIDs: OK` whether or not they exist (`moving-in:G-004`).
  Say "none needed — every installed .gd already had one" when nothing was minted, so
  an unreported mint and a silent installer do not look alike;
- which `devtools_config.json` keys were updated versus kept as project-owned (step 7);
- whether `project.godot`, `CLAUDE.md` or `.claude/settings.json` were touched.

Then tell the user how to finish, and stop:

```
Review:  git diff <base-branch>...HEAD
Keep:    git checkout <base-branch> && git merge --no-ff harness/refresh-<version>
Discard: git checkout <base-branch> && git branch -D harness/refresh-<version>
```

Do **not** commit, merge, push, or delete a `.bak` yourself.

Then report a summary that includes:

1. **Register a command.** Open `res://devtools_ext/commands.gd` and register
   project verbs inside `register_commands(dev)`:

   ```gdscript
   extends RefCounted

   func register_commands(dev: Node) -> void:
       dev.register_command("spawn_thing", _spawn_thing)

   func _spawn_thing(args: Dictionary) -> Dictionary:
       var n := int(args.get("count", 1))
       # ... do the thing against the running game ...
       return { "success": true, "message": "spawned %d" % n, "data": { "count": n } }
   ```

   Handlers must return exactly `{ "success": bool, "message": String, "data": Dictionary }`.
   Registrations are last-writer-wins, so a project verb may override a generic one.

2. **Launch + ping.** Start the game (optionally muted) and confirm the bridge:

   ```bash
   "$GODOT" --path "$ROOT" [--mute] &
   sleep 5 && python3 "$ROOT/tools/devtools.py" ping
   python3 "$ROOT/tools/devtools.py" list-commands   # discover registered verbs
   ```

3. **Userdata directory.** Tell the user the exact `user://` path the Python
   client polls for command/result files (computed in step 2), e.g. on macOS:
   `~/Library/Application Support/Godot/app_userdata/<name>/`.

   If step 2 found `use_custom_user_dir` **true**, say so — a custom user dir is
   the usual reason a running game looks dead to the client.

   **Running more than one instance.** The bus is one command/result file pair, so
   two instances in the same `user://` answer each other's commands. Give each a
   session id — the game via `-- --devtools-session <id>` (or
   `GODOT_DEVTOOLS_SESSION`), the client via `--session <id>`:

   ```bash
   "$GODOT" --path "$ROOT" --mute -- --devtools-session a &
   python3 "$ROOT/tools/devtools.py" --session a ping
   ```

   That separates the buses only. Screenshots, UI baselines and saves still share
   the directory, and `--import` still races on one `.godot/` cache — for full
   isolation combine `--session` with a per-instance `GODOT_USERDATA` (or
   `use_custom_user_dir` + `custom_user_dir_name` per worker).

4. **Project CLAUDE.md.** The project's `CLAUDE.md` now documents the harness
   (between the `godot-selftest-harness` markers) so future sessions know it
   exists and how to drive it — re-running scaffold refreshes that section.

5. **Devtools gaps log.** `log-devtools.md` now exists and `CLAUDE.md` instructs
   every response to append an entry naming what `/verify` or the devtools
   couldn't do, each with a `- [G-NNN] status: open | seen: 1 | harness: X.Y.Z`
   line and a suggested fix. A `Stop` hook reminds when code changes land
   without an entry **dated today**. These entries are the harness's improvement
   pipeline, and they only pay off once they reach it:

   ```bash
   python3 tools/upstream_gaps.py log-devtools.md \
       --into /path/to/godot-selftest-harness/log-devtools.md
   ```

   Deduped by id and safe to re-run. Suggest it whenever the log has open gaps.

Also mention: run **`/verify`** (from this plugin) to execute the full runtime
validation workflow (lint → headless tests → launch → ping → validate-all →
sequence → performance → quit).

---

## What this installs

- `res://addons/godot_selftest/` — the DevTools core (`dev_tools.gd`), the
  namespaced scene validator (`scene_validator.gd`), `devtools_config.json`, and
  `.harness_manifest.json` (what this scaffold wrote, and its hashes — how the next
  refresh tells a stale copy from a file you edited; commit it).
- `res://tools/` — `lint_project.gd` (headless UID + scene lint),
  `run_tests.gd` (headless unit test runner), `devtools.py` (Python CLI client),
  `check_devtools_log.py` (the `Stop`-hook logging reminder), `upstream_gaps.py`
  (pools this project's open gaps into the harness repo's log), `verify_ledger.py`
  (records what each `/verify` run reached; `stats` reads the history back),
  `import_check.py` (runs `--import` and fails on the parse errors Godot prints
  while still exiting 0), and `name_check.py` (resolves every name the scripts
  mention against the project's own declarations and a cached engine API index —
  the one gate that never opens the project, so N agents can run it at once), and
  `coverage_check.py` (reports which classes of defect this project's tests never
  ask about — also engine-free and parallel-safe).
  Each installed `.gd` also gets a `.uid` sidecar if it arrived without one.
- `res://devtools_ext/commands.gd` — your project's command registry extension
  (plus `commands.example.gd` for reference).
- `res://test/unit/test_selftest.gd` and `res://test/sequences/` — the project's
  selftest (the documented home for every check a session writes, re-run by
  `/verify` on every change) and a smoke sequence example.
- A `DevTools` autoload line in `project.godot`.
- `<ROOT>/CLAUDE.md` — a lean, reference-style harness guidance section wrapped in
  `<!-- BEGIN godot-selftest-harness -->` / `<!-- END godot-selftest-harness -->`
  markers. Created if absent, refreshed in place if the markers exist, or appended
  if a `CLAUDE.md` already exists without them (never clobbering existing content).
- `<ROOT>/log-devtools.md` — the devtools/`/verify` gaps log (seeded only if
  absent), plus a `Stop` hook entry in `<ROOT>/.claude/settings.json` that reminds
  when code changes without a log entry. Existing hooks are merged, never replaced.
- `<ROOT>/.devtools/verify-runs.jsonl` — appended by `/verify` Phase 5, one line per
  run. Not created at scaffold time (an empty ledger and an unused harness should not
  look alike). Commit it; do not gitignore `.devtools/`, or the measurement is lost.
