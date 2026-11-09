# Contributing

How Karta works is in [docs/architecture.md](docs/architecture.md) and [docs/harness.md](docs/harness.md). Read them before changing the code, and update them in the same change when how something works changes.

## Ground rules

- Code decides facts, and Laya makes narrow choices. Anything a model would have to work out (dates, a login page,
  whether a field holds the text, a price) is computed in code and handed over as state. Laya is never asked what
  code can tell.
- Laya's options are short, mutually exclusive, and semantic (never true/false). Two options that mean the same
  thing split the vote and read as low confidence.
- The brain plans and rescues. It never clicks, never plans a key or shortcut, and never invents personal details.
  A change that moves work to the brain, or adds brain calls, should say why.
- Build for any task, not the test scenarios. No scenario words in product code or prompts; examples come from
  unrelated apps. Judge a change by held-out apps and scenarios.
- Anything hard to undo needs the user's yes. Never add a path that types a password.
- Fewer brain calls is the main goal; speed matters as much as success. The harness must know which steps are
  done (checkpoints), so a failure never redoes finished steps or replans from the top.
- Failures are never persisted across runs (session notes only); successes are, with caps and decay.
- Never give the brain the whole Mac map: a task-relevant slice, hosts only, never tab titles.
- No new dependencies without the maintainer's yes. Voice is push-to-talk: nothing listens or acts until the key
  is held and released.
- Platform calls live in `karta/base/macos.py` only. Everything else reaches it through
  `karta.base.platform_adapter.desktop`, and a new adapter call goes into the `Desktop` protocol and
  macos.py together. The accessibility-tree walk is in `karta/base/ax_walk.py`.

## A task the harness cannot do

Write it as an offline test first: a fake env in `tests/test_harness_*.py` (see `tests/test_harness_loop.py`), or a
scenario for the role-played Mac in `scripts/sim_scenarios.py`, run with `scripts/karta_harness_sim.py`. The
simulator uses the real brain and Laya, so it costs brain calls. Then fix the harness, not the scenario, in a way
that holds for apps the scenario never names.

## Before a pull request

```
uv run ruff check . && uv run ruff format .
HF_HUB_OFFLINE=1 uv run --quiet --no-sync pytest -q tests/
```

CI runs the same on macOS.

When a change alters what Laya or the brain sees, add a note to the PR: which case or recorded run, which step,
and what the decision was before and after.
