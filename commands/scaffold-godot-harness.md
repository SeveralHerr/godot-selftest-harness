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
client polls (see step 11). The default per-platform Godot userdata dir is:

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
for f in lint_project.gd run_tests.gd devtools.py; do
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
  "main_scene": "",
  "entry_hook": { "node_path": "", "method": "" },
  "mute": true
}
```

Set `main_scene` and `hud_layer_name` from detection; leave the rest at defaults
unless the project already customized them.

## Step 8 — Wire the DevTools autoload (idempotent)

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

## Step 9 — Detect the Godot binary

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

## Step 10 — Smoke check

If a Godot binary was found, run the headless linter to confirm the project
loads and the core parses:

```bash
"$GODOT" --headless --path "$ROOT" --script res://tools/lint_project.gd
```

Surface any parser errors or load failures verbatim. A clean exit (code 0) plus
per-scene `OK` / `UIDs: OK` output means the install parses. If it fails, report
the error and stop before claiming success.

## Step 11 — Print next steps

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

Also mention: run **`/verify`** (from this plugin) to execute the full runtime
validation workflow (lint → headless tests → launch → ping → validate-all →
sequence → performance → quit).

---

## What this installs

- `res://addons/godot_selftest/` — the DevTools core (`dev_tools.gd`), the
  namespaced scene validator (`scene_validator.gd`), and `devtools_config.json`.
- `res://tools/` — `lint_project.gd` (headless UID + scene lint),
  `run_tests.gd` (headless unit test runner), `devtools.py` (Python CLI client).
- `res://devtools_ext/commands.gd` — your project's command registry extension
  (plus `commands.example.gd` for reference).
- `res://test/unit/` and `res://test/sequences/` — a seed unit test and a smoke
  sequence example.
- A `DevTools` autoload line in `project.godot`.
