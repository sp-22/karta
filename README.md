# Karta

**I stopped clicking.** Hold a key, say what you want, and your Mac does it.

<p align="center"><img src="docs/media/karta-demo.gif" alt="Karta: I stopped clicking. I just say it, my Mac does it." width="900"></p>

Karta works in your real apps and your signed-in browser, not a sandbox. It plans a task once, does each step itself,
and checks the screen after every step before it moves on. A task is only "done" when the result is on screen.

> **Early, and moving fast.** Karta drives your real mouse and keyboard. It handles everyday tasks well and still
> misses some. Anything hard to undo (send, delete, pay, post) waits for your yes.

## What you can say

| You say | Karta |
|---|---|
| "Find three good cold brew videos on YouTube and tell me their titles" | Searches, reads the results, says the titles out loud |
| "Email me saying Karta test: lunch? in Gmail" | Writes the email, reads it back, asks "Send it?" |
| "Make a calendar event on Friday from 3 to 4 pm" | Creates it in Calendar and checks it is there |
| "Find three articles about remote team rituals and email me the links" | Searches the web, then writes and sends the email |
| "Take the top 3 Hacker News stories and put them in a reminder list called Reading" | Web to Reminders, one item per story |
| "Find the cheapest flight Bangalore to Delhi next Friday, don't book" | Looks it up and tells you; never books |
| "What's on this page, in two lines?" | Reads the page you are on and answers |
| "Start Claude Code in my repo and add a hello line to the README" | Opens Claude Code in a new Terminal window and tells you when it finishes |
| "Tell me when report.txt appears in Downloads" · "In 5 minutes, remind me to stretch" | Watches and reminders that speak up when it is time |

- **It talks back.** Questions get a spoken answer.
- **It keeps the thread.** "Summarize the second one." "Email that to me." Follow-ups refer to what it just did.
- **It learns your tasks.** A task it has done and checked once is saved; next time it replays in seconds with no AI calls.
- **It runs on your Mac.** The screen is read locally (accessibility tree and Apple Vision OCR), small choices are made
  by a local model, and speech is transcribed locally. The planner is Claude, through your Claude Code sign-in.

## Quick start

You need an Apple silicon Mac and the [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI, signed in once
with `claude`.

```
curl -fsSL https://raw.githubusercontent.com/sp-22/karta/main/install.sh | sh
karta start         # hold Right Option, say a task, let go
```

Setup asks for the permissions Karta needs and downloads its models (about 1 GB). Details are under
[Install](#install).

## How it works

Karta is not LLM computer use. **Code computes facts, Laya makes narrow choices, and the harness verifies and
loops. The brain only splits the task into tool calls once and rescues a stuck step.**

- **Laya** is a local decision model. It answers one narrow question at a time in tens of milliseconds, such as
  which item is the Search button, whether this page is ready, or which result is the one wanted.
- **The brain** is Claude Code's CLI with Sonnet. It writes the plan one tool per line (`open_place`, `find`,
  `click`, `write`, `submit`, ...). Each step comes with a checkpoint: what the screen shows once the step worked.
- **Code** reads the screen through the accessibility tree and Apple Vision OCR, and settles every fact it can.

When a step fails, the harness tries these in order:

1. Laya's next-best option.
2. Undo a wrong click and try again.
3. The next place.
4. Ask the brain to fix that one step.

A task is done only when its end state is on screen.

How it all works: [docs/architecture.md](docs/architecture.md) and [docs/harness.md](docs/harness.md).

## Install

On an Apple silicon Mac:

```
curl -fsSL https://raw.githubusercontent.com/sp-22/karta/main/install.sh | sh
                    # installs uv if missing, the `karta` command, then runs `karta setup`
                    # (or from a clone: ./install.sh)
karta start         # the HUD and push-to-talk: hold Right Option, say a task, let go
```

`karta setup` checks and fixes what Karta needs, one step at a time, and can be re-run any time:
- **Permissions** for the app Karta runs in (your terminal): Accessibility, Screen Recording, Input Monitoring
  (the talk key) and the Microphone. It opens the Settings pane for any that are missing.
- **whisper.cpp** (installed with [Homebrew](https://brew.sh)) and its speech model (about 150 MB).
- **Laya**, the local decision model (about 850 MB, from Hugging Face, cached after the first run).
- **The brain:** the [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI, signed in once with `claude`.
- A first **map of this Mac**, which stays on this Mac.

Models and memory live in `~/Library/Application Support/karta/`.

## Running it from a clone

For development, run it through uv:

```
uv run karta                                    # map the Mac, load Laya, then type tasks at the prompt
uv run karta "create a note called groceries"   # one task, dry run: plans, never clicks
uv run karta "..." --act                        # do it for real
uv run karta "..." --act --hud                  # show each step in the HUD (start it first: uv run karta-hud &)
uv run karta --voice --act --hud                # push-to-talk: hold Right Option, say the task, let go
uv run karta "..." --record                     # also save each task's screens and actions as local JSON
```

At the prompt, a task that starts with `!` runs for real. `--budget` sets the seconds of harness time per task
(default 120).

- **On start**, Karta maps the Mac (installed apps, browser profiles, folders, open tabs, remembered places), loads
  Laya and warms the brain. It re-reads tabs and running apps before every task.
- **`karta-hud`** is a glass panel near the bottom of the screen. It shows the task, the plan and each step. While
  you speak it shows a Listening pill with voice bars, and while Karta is in control it shows a blue edge glow.
- **Voice** is push-to-talk (`--voice`): hold Right Option and speak. Speech is transcribed locally by whisper.cpp's
  `whisper-server`. Karta asks its own questions the same way.

To stop a live task, press Ctrl-C.

## Use Karta from your coding agent (MCP)

`karta-mcp` makes Karta the hands and eyes of a coding agent (Claude Code, Cursor, Codex) on this Mac. The agent is
the brain: it reads the screen and decides each step; Karta carries the step out with its own tools (code first,
Laya for narrow choices), checks it, and says what changed. It is a proof of concept.

```
claude mcp add karta -- uv run --project ~/Desktop/Projects/karta --extra mcp karta-mcp
```

Cursor (`~/.cursor/mcp.json`; use the absolute path to your clone):

```json
{
  "mcpServers": {
    "karta": {
      "command": "uv",
      "args": ["run", "--project", "/Users/you/Desktop/Projects/karta", "--extra", "mcp", "karta-mcp"]
    }
  }
}
```

Tools: `screen()` (the front app, window, site host and up to 120 items as short lines), `click(target, near)`,
`type_text(text, into)`, `press_submit(expect)`, `press(keys)`, `open(app_or_url)`, `scroll(direction)`, `back()`,
and `check(expect)` (does the screen show it: yes or no, and why). Each action returns its outcome, what was done
and what changed on screen.

- **Hard-to-undo actions never run on the first call.** A send, payment, purchase, delete, post or quit answers
  `needs confirmation` with the exact action; ask the user, then call it again with `confirm=true`.
- **Dry run**: `KARTA_MCP_DRY=1` (e.g. `claude mcp add -e KARTA_MCP_DRY=1 karta -- ...`) answers what each action
  would do and does nothing.
- **Permissions**: the app that runs the agent (your terminal, Cursor) needs Accessibility and Screen Recording in
  System Settings > Privacy & Security, the same as `karta` itself. Laya loads once when the server starts.
- There is no whole-task tool: your agent plans; use `karta "<task>"` for Karta's own planner.

## Where things live

| Path | What |
|---|---|
| `karta/app.py` | The `karta` program: the Mac map, Laya, the brain, the task prompt, voice |
| `karta/hud.py` | `karta-hud`, the on-screen panel |
| `karta/mcp_server.py` | `karta-mcp`, Karta's tools for a coding agent over MCP |
| `karta/harness/` | Harness v2: the loop, tools, Laya's questions, facts, memory, safety ([design](docs/harness.md)) |
| `karta/brain/claude_code.py` | The brain: a warm, stripped `claude` process |
| `karta/voice/` | The push-to-talk hotkey, audio, and local speech to text |
| `karta/core/` | Shared pieces the harness uses: events, fast screen checks, the Laya matcher, safety words |
| `karta/base/` | The Mac layer: platform adapter, input, AX walk, OCR and perception |
| `scripts/` | Offline benches and tools (`decision_bench.py`, `next_step_bench.py`, `karta_memory.py`, ...) |
| `tests/` | Offline tests. Nothing touches the Mac. |

## Tests

```
HF_HUB_OFFLINE=1 uv run --quiet --no-sync pytest -q tests/
uv run ruff check . && uv run ruff format --check .
```

The tests are pure logic. `tests/conftest.py` refuses every call that would reach the machine: input events,
AppleScript, screen capture, `open`, and accessibility actions. `HF_HUB_OFFLINE=1` makes Laya load from the local
cache.

## Privacy and safety

- **It asks before risky steps.** Anything hard to undo (send, post, pay, delete, or a dialog answer that confirms
  one) waits for your yes. Permission prompts always take your answer. Karta never types a password.
- **Dry run by default.** Nothing is clicked or typed without `--act`.
- **Permissions.** Grant your terminal Screen Recording and Accessibility in System Settings > Privacy & Security.
  Voice also needs the microphone. The Right Option hotkey needs Accessibility too. Without Screen Recording,
  captures show only wallpaper. Without Accessibility, clicks are dropped.
- **Data stays local.** Laya, OCR and speech to text run on this Mac. Memory is a local SQLite file and recordings
  are local JSON files. Neither is part of the repo. The brain gets the task, a few task-relevant lines about the
  Mac, and the current screen's items. It never gets the whole map.
- Using the Mac during an `--act` run fights Karta for focus and the cursor.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Agents working on this repo should also read [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE).
