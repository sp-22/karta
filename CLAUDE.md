@AGENTS.md

## How the code and system work: `docs/`

Two files, the single source of truth for how Karta works. Read the relevant one before changing code.

| File | What's in it |
|---|---|
| `docs/architecture.md` | the whole system: processes and the flow from a task to done, Laya, the brain, reading the screen, voice and the HUD, data on disk and the memory database, install, code layout, testing |
| `docs/harness.md` | how a task is carried out (`karta/harness/`): the plan format, the loop and ladder, checkpoints and the ledger, places, tools, try-verify-next, waiting for the screen, memory, safety, known limits |

## Keep them current (always)

- Any change to how the code or system works (a design, a flow, a rule the code enforces, a file location, a
  schema, a command, a default, a known limit fixed or found) updates the matching section **in the same commit**.
- Describe how things work now, not history: no dates, run results or progress notes. Delete what stopped being
  true. Add a section rather than a new file.
- Never write secrets, real people's names or the user's screen content (messages, tabs, contacts).
