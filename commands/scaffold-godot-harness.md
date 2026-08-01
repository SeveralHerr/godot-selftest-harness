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

## Step 3 — Install the addon core

Copy the addon templates into `res://addons/godot_selftest/`:

```bash
mkdir -p "$ROOT/addons/godot_selftest"
cp "${CLAUDE_PLUGIN_ROOT}/templates/addons/godot_selftest/dev_tools.gd"        "$ROOT/addons/godot_selftest/dev_tools.gd"
cp "${CLAUDE_PLUGIN_ROOT}/templates/addons/godot_selftest/scene_validator.gd"  "$ROOT/addons/godot_selftest/scene_validator.gd"
# devtools_config.json is written/patched in step 7, not blindly copied.
```

These are addon-owned files and are safe to overwrite on every run (they carry
no project-specific state). The validator is namespaced
(`GodotSelftestSceneValidator`) to avoid `class_name` collisions.

## Step 4 — Install the tool scripts (back up on conflict)

Copy `tools/*` into `res://tools/`. If a target file already exists, back it up
to `<file>.bak` first so a project's own tool of the same name is never lost.

```bash
mkdir -p "$ROOT/tools"
for f in lint_project.gd run_tests.gd devtools.py check_devtools_log.py; do
  src="${CLAUDE_PLUGIN_ROOT}/templates/tools/$f"
  dst="$ROOT/tools/$f"
  if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then
    cp "$dst" "$dst.bak"
    echo "Backed up existing tools/$f -> tools/$f.bak"
  fi
  cp "$src" "$dst"
done
chmod +x "$ROOT/tools/devtools.py" 2>/dev/null || true
```

## Step 5 — Create the registry extension (never overwrite)

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
```

## Step 6 — Seed tests and a sequence example (only if empty)

Create `res://test/unit/` and copy `test_example.gd` **only if the test dir is
missing or empty** (do not litter a project that already has tests). Always copy
`test/sequences/smoke.json` as a schema example (safe to refresh).

```bash
mkdir -p "$ROOT/test/unit" "$ROOT/test/sequences"
if [ -z "$(ls -A "$ROOT/test/unit" 2>/dev/null)" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/test/unit/test_example.gd" "$ROOT/test/unit/test_example.gd"
  echo "Seeded test/unit/test_example.gd"
else
  echo "test/unit already has files — left untouched."
fi
cp "${CLAUDE_PLUGIN_ROOT}/templates/test/sequences/smoke.json" "$ROOT/test/sequences/smoke.json"
```

## Step 7 — Write `devtools_config.json` with detected values

Write `res://addons/godot_selftest/devtools_config.json` using the schema below,
filling in values detected from the target project. If the file already exists,
**patch** it (preserve any keys the project has customized, such as `fps_min`,
`orphan_max`, `mute`, or `entry_hook`) rather than replacing it wholesale.

Detect:

- `main_scene` — from `run/main_scene` in `project.godot`.
- `hud_layer_name` — best effort: open the main scene and use the **name of the
  first `CanvasLayer`** you find. If none is found (or the scene can't be read),
  default to `"HUD"`.
- `extension_script`, `validator_script`, `test_dir`, `scan_root` — the defaults
  below (they match where steps 3–6 placed things).

```json
{
  "validator_script": "res://addons/godot_selftest/scene_validator.gd",
  "extension_script": "res://devtools_ext/commands.gd",
  "hud_layer_name": "HUD",
  "test_dir": "res://test/unit",
  "scan_root": "res://",
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
  "log_check_dated_entry": true
}
```

Notes on the newer keys, so a patch of an existing config doesn't get them wrong:

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

## Step 8 — Install/refresh the CLAUDE.md guidance section

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
Keep the full procedures in `/verify`, this command, and the README.

## Step 9 — Install the devtools gaps log + its `Stop` hook

The harness improves from evidence, and the evidence is perishable: the moment a
workaround is found, the friction that forced it is forgotten. `log-devtools.md`
is where each session records what `/verify` or the devtools couldn't do, so those
gaps can later be upstreamed into the harness itself.

Three pieces, all idempotent:

**9a. Seed the log** — create it only if absent, so an existing log is never
truncated:

```bash
if [ ! -f "$ROOT/log-devtools.md" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/log-devtools.md" "$ROOT/log-devtools.md"
  echo "Created log-devtools.md"
else
  echo "log-devtools.md already exists — left untouched."
fi
```

**9b. The hook script** — `tools/check_devtools_log.py` was already copied in step 4.
It is a Claude Code `Stop` hook: it asks git whether this session changed Godot code
without also changing `log-devtools.md`, and if so prints a `systemMessage` reminder.
It is written in Python (not shell) because the harness already requires Python 3 for
`devtools.py`, and this way the hook works identically on Windows, macOS, and Linux.
It always exits 0 — a reminder must never break a session.

**9c. Wire the `Stop` hook into `<ROOT>/.claude/settings.json`.** Merge, never
overwrite: a project may already have hooks. Detect the Python interpreter first
(`python3` on macOS/Linux, usually `python` on Windows).

**Probe each candidate by actually running it — do not use `command -v`.** On Windows,
`command -v python3` succeeds against the Microsoft Store *App execution alias* stub,
which then refuses to run (`Python was not found; run without arguments to install from
the Microsoft Store…`). Existence is not executability, and a hook wired to that stub
fails silently on every turn:

```bash
PY=""
for c in python3 python py; do
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

## Step 10 — Wire the DevTools autoload (idempotent)

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

## Step 11 — Detect the Godot binary

Resolve the Godot binary in this priority order and record it for `/verify`:

1. `$GODOT_BIN` if set and executable.
2. `/Applications/Godot.app/Contents/MacOS/Godot` (macOS).
3. `which godot`.

If none is found, **warn** that lint/tests/`/verify` cannot run headless until a
binary is available, and note that Windows/Linux users likely need to set
`GODOT_BIN` to their Godot executable path.

```bash
if [ -n "$GODOT_BIN" ] && [ -x "$GODOT_BIN" ]; then GODOT="$GODOT_BIN"
elif [ -x "/Applications/Godot.app/Contents/MacOS/Godot" ]; then GODOT="/Applications/Godot.app/Contents/MacOS/Godot"
elif command -v godot >/dev/null 2>&1; then GODOT="$(command -v godot)"
else GODOT=""; fi
[ -n "$GODOT" ] && echo "Godot binary: $GODOT" || echo "WARN: no Godot binary found. Set GODOT_BIN."
```

## Step 12 — Smoke check

If a Godot binary was found, run the headless linter to confirm the project
loads and the core parses:

```bash
"$GODOT" --headless --path "$ROOT" --script res://tools/lint_project.gd
```

Surface any parser errors or load failures verbatim. A clean exit (code 0) plus
per-scene `OK` / `UIDs: OK` output means the install parses. If it fails, report
the error and stop before claiming success.

## Step 13 — Print next steps

Report a summary that includes:

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

4. **Project CLAUDE.md.** The project's `CLAUDE.md` now documents the harness
   (between the `godot-selftest-harness` markers) so future sessions know it
   exists and how to drive it — re-running scaffold refreshes that section.

5. **Devtools gaps log.** `log-devtools.md` now exists and `CLAUDE.md` instructs
   every response to append an entry naming what `/verify` or the devtools
   couldn't do, plus a suggested fix. A `Stop` hook reminds when code changes
   land without one. Tell the user these entries are the harness's improvement
   pipeline — worth reading periodically and feeding back upstream.

Also mention: run **`/verify`** (from this plugin) to execute the full runtime
validation workflow (lint → headless tests → launch → ping → validate-all →
sequence → performance → quit).

---

## What this installs

- `res://addons/godot_selftest/` — the DevTools core (`dev_tools.gd`), the
  namespaced scene validator (`scene_validator.gd`), and `devtools_config.json`.
- `res://tools/` — `lint_project.gd` (headless UID + scene lint),
  `run_tests.gd` (headless unit test runner), `devtools.py` (Python CLI client),
  `check_devtools_log.py` (the `Stop`-hook logging reminder).
- `res://devtools_ext/commands.gd` — your project's command registry extension
  (plus `commands.example.gd` for reference).
- `res://test/unit/` and `res://test/sequences/` — a seed unit test and a smoke
  sequence example.
- A `DevTools` autoload line in `project.godot`.
- `<ROOT>/CLAUDE.md` — a lean, reference-style harness guidance section wrapped in
  `<!-- BEGIN godot-selftest-harness -->` / `<!-- END godot-selftest-harness -->`
  markers. Created if absent, refreshed in place if the markers exist, or appended
  if a `CLAUDE.md` already exists without them (never clobbering existing content).
- `<ROOT>/log-devtools.md` — the devtools/`/verify` gaps log (seeded only if
  absent), plus a `Stop` hook entry in `<ROOT>/.claude/settings.json` that reminds
  when code changes without a log entry. Existing hooks are merged, never replaced.
