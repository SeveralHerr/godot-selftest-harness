# Devtools / `/verify` Gaps Log

Running log of gaps in the `/verify` workflow or the devtools harness, and the smallest
improvement that would have closed each one.

**Why this file exists.** The harness can only be improved from evidence, and the evidence
is perishable — the moment a workaround is found, the friction that forced it is forgotten.
This log is the harness's feedback channel: entries here are what later get upstreamed into
`godot-selftest-harness` itself, so a gap logged in one game becomes a fixed feature for
every game.

**Append a new entry at the end of every response** (a `Stop` hook in
`.claude/settings.json` reminds you when a code change lands without one). An honest
"no gaps this turn" line is a real entry — it is what makes the absence of a gap
distinguishable from a forgotten log.

## Format

```markdown
## YYYY-MM-DD — <what the response did>

- Gap: **<what was missing>** — <evidence: the command run, the output it gave, the
  workaround used instead>
  - Improvement: <the smallest change that would have closed it>
```

Guidelines that make an entry useful later:

- **Quote the evidence.** `devtools.py: error: unrecognized arguments: --property scale`
  is actionable; "get-state was awkward" is not.
- **Say what you did instead.** The workaround is the measure of the gap's cost.
- **Prefer the smallest fix.** "Add `--property` (repeatable) to `get-state`" beats
  "improve state inspection".
- **Note recurrences.** A gap that bites a second time is a stronger signal than a new
  one — say so rather than writing a fresh entry as if it were novel.
- **Log closures too.** When a gap gets fixed, record that, and record whether the fix
  actually paid off on the next run.

---

<!-- Entries below, newest at the bottom. -->
