# Agent rules

Karta drives a real computer. When you work on it, you are almost always on the maintainer's own Mac, often while
they are using it.

## Never take over the machine without explicit approval

The rule is about this machine's screen, input, and apps, not about running commands. Ask first,
every time, before anything that would use or take them over:

- running the project on this machine: `karta` (a dry run too: it maps the Mac and reads the screen), `karta-hud`,
  or any script that imports the platform adapter (`karta.base`) or `karta.harness.mac` and calls it
- anything that moves the mouse, presses keys, clicks, scrolls, or types
- AppleScript or `osascript`, `open`, or any command that launches, activates, or quits an app
  or opens a URL or file
- launching Chrome or any browser, and anything that talks to a browser over CDP
- screen capture of any kind, and the microphone
- any Docker container, and requests to local model servers (Ollama, LM Studio, `whisper-server`) or any other
  local service

Propose the exact command and wait. Approval covers that command once, not the kind of command
from then on. If you run subagents, give them this rule word for word.

Everything else needs no approval: `git`, `uv`, ruff, the offline tests, and ordinary CLI
commands such as `gh` or read-only `gcloud` queries. Commands that create or delete cloud
resources, or spend money, still get a yes first.

## Tests stay off the machine

`tests/conftest.py` makes every call that would reach the machine refuse during tests: input
events, AppleScript, screen capture, `open`, and accessibility actions. The pointer reads as
mid-screen. Do not weaken or bypass that guard. A test that needs one of those calls patches it
itself. When you add a new call that reaches the machine, add it to the guard in the same change.

Run the tests with `HF_HUB_OFFLINE=1 uv run --quiet --no-sync pytest -q tests/`. Never run a plain `uv sync`.

## Live runs and privacy

- Live runs answer risky questions no by default (`yes n | karta … --act`). Never type into the terminal running
  your session: open a fresh Terminal window for terminal tasks. Afterwards, list the test data the maintainer
  should clean up (notes, events, reminders, carts, browser profiles, windows).
- Never print or save tab titles, messages or contacts; site hosts only. Use made-up names in tests and docs.
- Never commit secrets (`.env`), models or `demo/` (screen recordings). Push only to the maintainer's own Karta repository, and only when asked.
- Before launching subagents that change code, commit locally first, and have each agent commit only its own
  files, so every agent's change is a visible diff.

## Everything else

How Karta works: `docs/architecture.md` and `docs/harness.md`. See `CONTRIBUTING.md` for how the project works and what a pull request
needs.
