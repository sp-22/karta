# Architecture

The single source of truth for how Karta's system fits together. How a task is carried out step by step is in
`harness.md`.

Karta does a spoken or typed task on the user's own Mac. **Laya** (a small local decision model) and **code**
carry out each step; the **brain** (an LLM through the Claude Code CLI) plans the steps and rescues a failed one.
Every step is checked against what the screen shows.

## Processes

| Process | What | Talks to |
|---|---|---|
| `karta` (karta/app.py) | the program: Mac map, Laya, the brain, the harness; typed or push-to-talk. In voice mode four kinds of thread: the main one listens, one screen worker runs screen jobs one at a time, a pool of at most 4 runs background jobs (`karta/jobs.py`, below), and one watch thread reads watches and starts schedules (`karta/watches.py`, below) | everything below |
| `karta-hud` (karta/hud.py) | AppKit panel: status line, Listening pill, a pill per live job, blue edge glow; started by `karta start` and `karta --hud` when none is running (`app.start_hud`: its UDP port taken = running), as a child that exits when its parent goes (`KARTA_PARENT_PID`) | events over UDP, 127.0.0.1:47811 (`karta.core.events`) |
| `whisper-server` (Homebrew whisper.cpp) | speech to text, model kept loaded | HTTP on 127.0.0.1:8178 (`karta/voice/stt.py`) |
| `claude` CLI | the brain; one warm spare process waits for the next call | stdin/stdout, stream-json (`karta/brain/claude_code.py`) |
| `claude` in Terminal | a delegated session: Claude Code on one of the user's repos, in a new Terminal window, the user's own permissions; Karta never types into it | its `Stop` / `Notification` hooks append to a file Karta reads (harness.md, "Delegate") |
| `karta-mcp` (karta/mcp_server.py) | optional: Karta's tools for a coding agent | MCP over stdin/stdout (below) |

Commands: `karta setup` (install and check, below), `karta start` (HUD + push-to-talk, acting for real; Ctrl-C or
a terminate stops all of it, and the HUD exits when its parent goes), `karta "<task>"` (dry run by default;
`--act`, `--hud` (starts the HUD when it is not running), `--voice`, `--say` (also say aloud how a typed task
ended), `--record`, `--trace`, `--budget` (seconds per task, default `loop.BUDGET_S` 120, brain time included)).

## What the terminal shows

`karta/console.py`: a KARTA banner in Mac blue with the version and mode, then one line per event as it happens:
time, tag, text. Tags: `map` (one line of counts at start: the map itself never prints), `ready`, `task`, `plan` (each plan line), `step`, `pick` (Laya's pick and confidence),
`retry`, `check` (the ledger: ✓/✗ per step), `place` (a move to another place, with its reason), `rescue`, `says` (a question's answer), `mic`, `speech`, `asks` /
`answer`, `idle`, `done`. It
subscribes to `karta.core.events`, like the HUD. At the end of a task, the RESULT summary (brain calls, Laya
decisions, actions, rescues, local repairs, time, screen reads). Every line also goes to `logs/karta-<date>.log`, with the full
trace; `--trace` prints the trace in the terminal too. Colour only on a terminal, never with `NO_COLOR`.

## From a task to done

1. **Hear**: hold Right Option → record → whisper → `app.take`: the answer to a job's question, a control phrase, a
   schedule or a watch ("Watches and schedules" below), or a new job (`clean_goal`; "Jobs" below). Or type it: typed mode runs one task at a time, as before.
2. **Map**: built once at start (`facts.mac_map`); tabs and running apps re-read before each task
   (`refresh_tabs`). The brain gets only a task-relevant slice (`brain.brain_facts`): hosts, never tab titles.
3. **Plan**: a pinned task (the exact command, a routine's name, or its template with values code binds from the command) skips this and replays its recipe with no brain
   call. By default (`KARTA_PLAN=auto`) the plan is the blind one below, and a step whose check misses (or a place the
   plan never named) gets one look: the numbered view of the screen then, the step, what it expected and what
   changed, and the plan's rest; its `#n` steps fix that step and the rest carries on (`continue`). Under `KARTA_PLAN=page` the brain plans one page at a time from a numbered view of it (`view.py`), aiming at
   items by number and ending with `look` or `done:`; the first page call understands the request. Otherwise
   (`blind`) the brain streams one tool call per line for the whole task; when pinned templates share a word with the task it
   sees up to 5 of them (templates, never values) and may answer `use: r1 | p1: … | p2: …` to replay one with new
   values (`recipes.py`, `harness.md`).
   When the task points back ("email that to …", "summarise it"), the plan also gets the session's block: ids and
   titles of what recent tasks produced, never values (`harness/session.py`; harness.md, "From an earlier task").
   A task about what is in front ("summarise this page", "reply to this email") acts on the front window: no
   open_place for it (code drops one that opens the place in front).
4. **Carry out** each step with code and Laya, verify the kind of change it declared (typed checks), record it in the
   ledger (`harness.md`). A `compose` step is one more brain call that writes text (a reply, a message) from collected
   facts only, typed later by code; a send that holds composed text reads it out and asks first, whatever was said up
   front; a mail or messaging app's text goes to the brain only after the user's once-per-app yes (harness.md,
   "Carrying data between steps"). A `delegate` step starts Claude Code on a repo in a new Terminal window, asked
   first with the exact folder and request; the task ends there and a background job hears back (harness.md,
   "Delegate").
5. **Repair** a mechanical miss locally, up to 3 tries in this order: a wait for a late change, the same action
   another way (retype, the menu command, the focused field, a dialog's way out), Laya's next-best candidates; only
   then the brain: a **rescue**, at most 2 per task (under auto and page planning one look at the screen, told what
   missed, at most 2 per step), 5 tries a step in all; the rest of the plan is kept.
6. **Done** only when the task's **contract** holds: the plan's first line declares what the task must achieve (an
   answer of a type, such as `list(3, titles)` or `comparison(A vs B on price)`, or an outcome, such as
   `created(note named X)` or `changed(Wi-Fi to off)`), and code checks it at the end: an answer by what was said and
   the content items it came from, an outcome by structure on the screen. A run with no contract line falls back to its
   done line. Only a run whose declared contract was met goes to memory and is pinned, each with that contract as its
   proof; a pinned replay checks it again and unpins itself when it fails (`harness.md`, "The contract").
7. **Or it gives up**, at the first give-up rule it hits (120 s, a step out of its 5 tries, 4 steps tried with none
   passing or changing the screen: `harness.md`, "Ending a task"), and says so in one line built by code: "I couldn't finish: I got to
   Postbox but the To field wouldn't take the address." (`TaskResult.said`).

## Laya, the local decision model

- `laya-mlx` 0.2.0; weights `convaiinnovations/laya` on Hugging Face (Apache 2.0). Karta uses the English model
  (~850 MB; the repo also holds multilingual and typed-decisions variants). MLX, so Apple silicon only. Loaded by
  `karta/core/matcher.py` → `karta/decider/laya.py` (`load_agent`, `cached_embed`).
- Ranks up to 16 short options for one question in ~25–100 ms. 512-token context, options sharing ~192 tokens
  (`screen.shortlist`, `fits_laya`). Also gives embeddings, used to shortlist. It can't write text, order steps or
  judge task progress.
- Questions live in `karta/harness/decide.py`, keyed by name in `gates.json` and on `Choice.decision`:
  `D1 place`, `D2 page state`, `D3 target`, `D4 pick check`, `D5 name match`, `D6 dialog role`,
  `D6 dialog answer`, `D7 outcome`, `D9 collect` (is this item one of what a collect takes) (and `N1 next step`,
  bench only; `D1 place` is kept for the bench too: places are ordered by evidence, not by Laya). Labels are
  semantic, never true/false.
- The meaning questions the word lists once stood in for reuse these, no new decision: a sign-in page with no password
  field (D2's kind of screen alone, `Laya.screen_kind`, risky tier), the button that closes an interruption (D6 dialog
  answer for "close it, agreeing to nothing"), the button that sends a form and a stepper's add or remove (D3), the
  running total (D5), an empty or not-set-up place and a search that found nothing (D7), an ad and an accessory (D9),
  a web page that is a mailbox or a conversation, before its text goes to the brain (D2's private kind of page,
  `Laya.private_screen`: harness.md, "Consent for mail and messages"); asked of any screen before a prompt shows it
  (`Toolbox.for_brain`), cached per host.
  Their gates are the decisions' own; a new use is judged on held-out apps like the rest (`bench/held-out.md`).
- It acts only when sure: the top answer clears a floor and leads the runner-up by a margin (fitted by
  `scripts/decision_bench.py`); reversible actions use a moderate gate and are verified at once, irreversible ones
  a ~95%-precision gate plus the user's yes. Code is asked first; Laya never decides what code can tell.
- Measured: zero-shot 0.36 vs fine-tuned 0.77 on an earlier bench; identical "ADD" buttons in a grid 0.15–0.21;
  choosing the next action alone 35–41% (`scripts/next_step_bench.py`), which is why the brain plans and Laya only
  picks within a step. A fine-tuned or distilled Laya (a LoRA adapter, trained on outcome-verified decisions
  collected with `--record`, `scripts/laya_dataset.py`) is the path to more; not built.

## The brain

- `ClaudeCodeBrain` runs `claude` with Sonnet: tools off (`--tools ""`), no MCP, Karta's own `--system-prompt`, low
  effort, thinking off, stream-json, one warm spare process per system prompt and model (a request may name its
  model: page calls and looks use `KARTA_PAGE_MODEL`, default `sonnet`; `karta` warms the page or look prompt's spare at start). First plan line in ~1.2–1.5 s. It uses the user's
  Claude sign-in. `LLMError` is defined there.
- `karta/harness/brain.py` `HarnessBrain` wraps any brain: `stream_plan` / `plan`, `rescue`, `page_plan`
  (`KARTA_PLAN=page`: `PAGE_SYS`, the screen as `view.py`'s numbered view), `look` (`KARTA_PLAN=auto`, the default:
  `LOOK_SYS`, a missed step with the numbered view at its checkpoint), `end_check`; prompts
  `PLAN_SYS_V2` and `RESCUE_SYS_V2` (the default: typed expectations), `PLAN_SYS`, `RESCUE_SYS`, `END_SYS` (v1,
  `KARTA_PROMPT=v1`, and the JSON variants for brains that don't stream lines). Line format: `harness.md`.
- Roughly 2–2.5k input and 80–200 output tokens per plan, 2.5–3.5k input per rescue: about a cent a call at
  Sonnet list prices. Rescues were most of the calls, so a mechanical miss is repaired locally first (harness.md,
  "Local repair") and a task gets at most 2 rescues.

## Reading the screen

- `MacHarnessEnv` (karta/harness/mac.py) implements `HarnessEnv`: `screen()`, click, set_text, type_at_focus,
  type_keys (key by key, never a paste: the local repair's; given the field, it replaces its text as set_text does), press, open_app, open_url, focus_tab, scroll, `scroll_top`, back, wait, `changed_since`, `wait_change`. Nothing is
  typed blind (`mac.focus_holds`): before `set_text`, `type_keys` and `type_at_focus` the system-wide
  AXFocusedUIElement must be the field, inside it (its editor) or the box it sits in (for `type_at_focus`: anything
  editable of the app in front); a field that lost it is clicked once more and checked again, else nothing is typed
  (`MacHarnessEnv.refused`, in the trace). `set_text` focuses by AX, clicks the field too in a browser (its AX focus can
  leave the keyboard elsewhere), pastes straight into an empty field, and replaces old text by selecting it through
  Accessibility (AXSelectedTextRange, read back; a native plain field's AXValue emptied), select all only as the last
  resort (the app's Edit menu command of that key equivalent by AX, else the key) and only while the field still holds
  the focus after it (in a combo box it moves the focus to the suggestion list); a field emptied with delete must read
  empty after it. A field off screen is scrolled into view by its own AXScrollToVisible before any click on it, and so
  is an off-screen control that refuses AXPress (never clicked at a spot that is not it).
- **Keys: Accessibility first, the key as the fallback, then checked** (`MacHarnessEnv.press`; `pressed` says how,
  `refused` why not, and `Toolbox.do` returns False on a refusal so a chain of keys stops there):
  - ⌘L in a browser: its address field by structure (`mac.address_field`: the widest enabled field of the browser's
    chrome) given the focus as any field is (`focus_holds`) and its text selected by AX; else the menu command or the
    key, and then the focus must be in something editable.
  - escape: AXCancel of the menu, popover, sheet or dialog the focus is in (climbing its ancestors; never a field, where
    AXCancel clears a search box), else that sheet's or dialog's cancel button (AXCancelButton) by AXPress; with no
    focused element, the front window's sheet. Only when that changed the screen; else the key.
  - return (`press("return")`): one key, never with a second way after it, since it may send. Only a return that
    sends nothing (a search box, the address bar: `confirm_or_return`) tries AXConfirm on the focused element first,
    when it offers it, and falls back to the key when the screen did not change within `CONFIRM_WAIT` (1 s). Return
    and tab go only to a focus in the app in front: with none there nothing is pressed.
  - a control that may send, pay or commit (`click_once`, harness.md "Keys"): exactly one way, AXPress when it has a
    pressable element (a refused press is not followed by a click: it may have gone through), else one real click;
    never `click`'s AXPress-then-click fallback.
  - any ⌘ shortcut: the app's one enabled menu command with that key equivalent (`AXMenuItemCmdChar`, read from the
    whole menu bar, a browser's History menu included), pressed by AX; else the key. Back (`back`) is ⌘[, so History >
    Back or Go > Back.
  - opening an address in the browser in front (`open_url`): ⌘L as above, the paste only to an editable focus, the
    focused field holding the address's host, then return (`confirm_or_return`); each step only when the one before went right.
  - scrolling: the front window's largest scroll area moved by its vertical scroll bar's AXValue (a step of
    `SCROLL_PAGE` of what shows, from its frame against its contents', read back), no pointer moved; else wheel
    events over the window's centre (`scrolled` says which). `scroll_top` sets that bar's AXValue to 0 when it is
    above 0 (a collect reads a list from its top: harness.md, "Carrying data between steps").
  - typing keeps the paste (⌘V). The user's clipboard is saved whole (every item, every type: rich text, an image, a
    file), Karta's text goes on it marked transient and concealed (clipboard managers keep none of it), and the
    clipboard is put back once the paste landed (the focused element's value changed) or `fast.PASTE_WAIT` (0.5 s)
    passed, never sooner than 0.1 s, also when the paste raised, and never over something copied meanwhile (the
    pasteboard's change count). `macos.clear_field` empties the focus by AX (its value, else its text selected and
    one delete), never select all.
- A read merges the bounded Accessibility walk (`karta.base.ax_walk`) with Apple Vision OCR of the
  front window (`karta/core/fast.py`); where both see the same thing, AX wins. Icon-only buttons get names from
  their description, help text and identifier (`Item.hint`: named only so, never collected as content).
  Cached until the next action: ~0.06–0.1 s cached, 0.5–1.2 s for a
  page that just changed. Each app Karta reads is asked once to expose web content (`AXManualAccessibility`),
  not only the one in front at start. The walk drops a repeat (same role, label and frame) only for labelled
  controls: nameless layout boxes share frames (a full-screen browser's page group has its window's frame).
- The walk reads each node's attributes in one call (`macos.ax_batch`, `AXUIElementCopyMultipleAttributeValues`, over
  `mac.WALK_ATTRS`; `mac.ax_reader` caches it for the read), and an item's details and a dialog's in one more; a
  label's extras (placeholder, help, identifier) are read singly, only for a control without a title. Each walked
  node carries its ancestors (`AxNode.path`, `Ref.path`), so code can tell which items share a row or card.
- OCR (`macos.recognize_text`) is Vision's accurate level with language correction off (on interface text it
  doubles the time and corrects nothing). Vision returns a table column by column; lines are joined into blocks only
  as prose (`perception.merge_blocks`: never a table cell, a line with text far off to its side on its row, onto the
  cell above, `table_cells`), and items are ordered by rows (`reading_order`: middles within half a line of the
  row's first), then left to right, so a price stays beside its name. Vision's document request (tables as rows and
  cells) is not used: about twice an OCR pass, and web rows come from the AX tree.
- A day's calendar events (`MacHarnessEnv.calendar_events`, `macos.calendar_events`) are read through EventKit, not
  the screen: one day across all calendars, read-only. Full access is asked for once a process (the system's prompt,
  its answer waited for at most `macos.CALENDAR_ANSWER_S` 60 s while the run loop turns); without it, None, and the
  collect reads the screen (harness.md, collect).
- A page's main text (`MacHarnessEnv.page_text`, `ax_walk.page_text`) is read from the front window's AXWebArea on
  demand, never on every read: static text in document order, off-screen included, minus what the content model calls
  interface (`content.node_class`: controls, bars, landmarks around the article, an editor or form holding a field),
  link lists and the site's learned boilerplate (harness.md, collect and "Interface and content").
- **Interface or content** (`content.classify`, harness.md "Interface and content"): each read is classified once,
  at the end of `MacHarnessEnv._read` (and a system prompt's read), and the read teaches the site's boilerplate
  (`content.BOOK`, digests only, kept in the memory database as kind `boilerplate`). Every item carries
  `Item.role_class`; every reader of content takes it.
- The walk (`mac.walk_app`) starts at the app but takes only its focused window, the strip windows that window is made
  of (a full-screen browser shows its tab bar and toolbar as short windows of their own, edge to edge with the page's),
  the app's other windows that are no standard window (dialogs, floating panels, popups) and its menu bar; another
  standard window of the app (one behind, the other half of a split view) is not read (`ax_walk.front_windows`). With no
  focused window known, every window is read. A control is on screen only inside the scroll area and web area that
  show it, not merely on the display: a small window's page past its edge, or a page scrolled past its viewport, is off
  screen (kept as an off-screen control when pressable). A popover is judged against the display.
- `Item`: name, kind, value, region, row, focused, enabled, checked, source, offscreen, menu, chrome, dialog, panel,
  cancels, secret, search, default, shortcut, browser_ui, url (a link's own address, AXURL, read for links only),
  multiline (an AXTextArea), hint (named only by a tooltip, a developer id or a subrole's description: an AXRoleDescription
  names an item only when it has a subrole, "close button"; a role's own description is the role again, in the app's
  language), role_class (`interface` or `content`, the content model's; no part of equality). `Screen.page()` leaves
  out menus, chrome and browser_ui. Each AX item's ancestors are read once per screen
  read (`mac.mark_tree`, `around`: one batched read of `ANCESTOR_ATTRS` per ancestor, shared through a cache), and say:
  `dialog` = inside a sheet, dialog window or web dialog role (never by its localized role description); `panel` = that
  dialog does not block the page (`mac.blocks`: a web dialog role without aria-modal, AXModal: a side cart, a drawer);
  `cancels` = the dialog's own AXCancelButton or AXCloseButton, or a close-button subrole; `default` = its window's or
  sheet's AXDefaultButton (the one Return presses); `search` = an AXSearchField (role or subrole: native, web
  type=search and role=searchbox), or anything inside the page's search landmark (AXLandmarkSearch); in a browser,
  outside the AXWebArea = the browser's own: `chrome` (toolbar, address bar, tab strip, tab search), or `browser_ui` in
  a dialog (never a panel). Text read off the pixels is chrome above the page's top edge. For the content model,
  each AX item's `Ref` gets the landmark subroles around it (outermost first), the nearest editor (a popover, sheet,
  dialog, web dialog or form: its key), whether it is in a toolbar or menu bar, and whether it is in the web page;
  text read off the pixels gets them from the frames its centre lies in (`mac.place_text`). `secret` = a password field
  (AXSecureTextField role or subrole). `Screen.busy` = something says it is still loading (the web area's AXLoaded
  false, or AXElementBusy, aria-busy, on it or around any item). `Screen.lang` = the language it shows: the page's
  AXLanguage (read with its AXURL in one call), else the app's bundle localization (`mac.app_language`), else the
  user's first preferred language. `Screen.windows` = the app's standard windows open (AXWindows of subrole
  AXStandardWindow) and `Screen.window_at` = where its focused window is drawn: a new window under the same title
  shows by them (both in the fingerprint when known). Menu commands come whole, "Menu > Command", each with its key equivalent
  (`shortcut`, "cmd+f": AXMenuItemCmdChar and AXMenuItemCmdModifiers, read in the same batched call as its title). A
  window's profile is the known profile name its title ends with (`mac.profile_from_title`), and a profile's window
  is opened from the menu that lists most of the known profile names (`profile_menu_item`), never by a menu's
  localized title. A repeated label carries its row mates.
- A system prompt in front (`facts.SYSTEM_PROMPTERS`: UserNotificationCenter, CoreServicesUIAgent, SecurityAgent and
  the like, a process that puts up privacy, security and file-access alerts for another app) is read from that
  process instead of the app (`MacHarnessEnv._system_prompt`): its window's items, all `dialog`; the Screen's app is
  the prompter's name.
- OCR text inside the HUD's frame (hud-frame.json) is dropped; AX items under it stay. The front app is read from
  NSWorkspace with a run-loop pump (a long-lived CLI otherwise sees a stale front app).
- The Mac map (`facts.mac_map`, `MacMap`): installed apps (incl. CoreServices), browser profiles from Local State
  (names only), the default browser, open tabs by AppleScript, signed-in places from memory, folders (`notable_folders`: under the home folder, the most recently changed at each
  of four levels, by no folder's name). Never a
  browser's cookies, passwords, history or any other profile data: where a site is signed in is judged only from
  the live tabs, the places memory proved, and the page on screen. Also in facts.py: `places_for`,
  `rank_by_evidence` (the order places are tried in: harness.md, Places), `is_login_page` (a password field, by
  structure), `shows` (loose OCR matching, never our own typing), `blocking_dialog` (a dialog that blocks, never a
  panel), `modal_items`, `in_the_way` (the blocking dialog's own items, prose too), `system_prompt` (a system prompter
  in front), `dialogs` (how many are open), `dismiss_option` (a dialog's own cancel or close, by structure) and
  `content_rows`. There is no site catalog: the brain names sites (`site:`), memory keeps the places that worked, and a
  service's own Mac app is any installed app the task names. Whatever a page means (it asks to sign in with no password
  field, it is empty or not set up, a search found nothing, an item is an ad or an accessory, which button closes an
  interruption or sends a form) is a Laya question (harness.md), never a word list.

## The MCP server (karta-mcp)

`karta/mcp_server.py`, optional extra `mcp` (the official `mcp` SDK, FastMCP, stdio). A coding agent (Claude Code,
Cursor, Codex) is the brain; Karta is the hands, eyes and verifier. At start it loads the Mac map, memory and Laya
once (like `karta`), with a `MacHarnessEnv`; one `Session` keeps one `Toolbox` across calls, so a submit knows the
field typed into before it. No `ClaudeCodeBrain` runs inside it.

- Tools: `screen()` (`screen_text`: app, window, site host only, up to 120 items as `kind 'name' = 'value' @region
  [flags]`, dialog items first, menu commands last), `click(target, near)`, `type_text(text, into)` (`write`, or
  `type` at the focus), `press_submit(expect)`, `press(keys)`, `open(app_or_url)` (`open_place`, a site when the
  text is host-shaped; tabs re-read first), `scroll`, `back`, `check(expect)` (code: `facts.shows`, then a field's
  name or text; then Laya's outcome question). Each action runs the Toolbox tool and returns the outcome, its
  detail, the actions done and what changed (`changes`: app, site, window, items new and gone, or "nothing
  changed").
- No `task` tool: a whole task needs a planning brain and the caller already is one, so it composes the primitives.
- Safety: `risk.risky` on the named target is checked before anything runs, and the Toolbox's own gate answers with
  the call's `confirm` (a vague target that resolves to "Send" stops there). Without `confirm=true` the answer is
  `needs confirmation` with the exact action and nothing is done; `confirm=true` allows the gates of that one call.
  `KARTA_MCP_DRY=1`: actions only say what they would do (a click names the item it would press).
- stdout is the MCP channel: loading and every tool call redirect prints to stderr. Untested live: the server
  against the real screen; tests use the fakes (tests/test_mcp_server.py), and import `mcp` before the machine
  guard (its type hints need the real `subprocess.Popen`).

## Voice and the HUD

- **Push-to-talk** (karta/voice/hotkey.py): nothing listens until Right Option (keycode 61) is held. A listen-only
  Quartz event tap watches that key, and only whether another key went down during the hold, never which (Input
  Monitoring); it re-enables itself if macOS disables it. Records while held (0.6–30 s) and on release hands the words to
  `app.take`, unless the hold was a shortcut (Option plus another key) or silent (RMS under 0.006, where whisper would
  make words up); those are ignored. Transcribes with `whisper-server` (`ggml-base.en.bin`).
  A task with key actions asks once before acting, in plain words (harness.md, Safety), and risky steps not in that
  list ask again: hold the key and say yes; only a clear yes counts (`said_yes`). Typed mode asks y/N.
- **Jobs** (karta/jobs.py, voice mode only): every spoken task is a job (`Job`: a number from 1 per process, a
  title of its first 5 words, a lane, a state: queued, running, asking, paused, watching, done, failed, cancelled),
  and Karta keeps listening while jobs run. `Jobs` holds the table (`list`, `live`, `status`) and two lanes:
  - **screen**: one worker thread runs screen jobs strictly one at a time, in the order asked; a new one while the
    screen is busy is queued and said so ("I'll do that after the current task."). The env a screen job runs on is
    `Jobs.guard`'s `Guard`: any thread but the screen worker's is refused (`ScreenDenied`).
  - **background**: a pool of at most 4 threads runs work that never touches the screen (`Jobs.background(title,
    work, kind)`: `work(job) -> said` gets its Job and nothing else; `Jobs.say(job, line)` says a line while it runs).
    A task that started Claude Code (`TaskResult.delegated`) gets one per session, kind `delegate`, shown watching:
    it listens for the session's hook events and says when it needs the user and when it finished (harness.md,
    "Delegate"). A watch's one setup call runs as one too (kind `think`).
  Each spoken input goes, in order (`app.take`), to the job that has waited longest for an answer (its confirm or
  ask posts the question and blocks on it; the HUD and terminal show "Job 2 asks: …" when other jobs are live), then
  to the control grammar (`Jobs.control`, whole utterances only, no brain call): `stop` (the screen job), `stop
  everything`, `pause`, `resume` / `go on`, `cancel <that | the second one | job 2 | words of its title>` (an ordinal
  counts the live jobs in the order "what are you doing" lists them), `what are you doing?` (read from the table:
  "Job 1 is running: … Job 2 is queued: …", then "I also have 1 watch and 2 schedules."), and, for watches and
  schedules, `what are you watching?` (also "what's scheduled", "list my reminders"), `stop watching [the build]`
  and a cancel that names no job but a watch or schedule ("cancel the 9 o'clock brief", "forget the reminder to call
  mom": its title's words and a schedule's hour); a cancel naming neither is a task. Anything else is a new job.
  Cancel is a clean stop: `run_task(stop=job.stopping)` sees it at its next step boundary, ends with
  `GAVE_UP_STOPPED` ("Stopped as you asked."), closes a dialog the task opened (`cancel_opened`) and pins nothing
  (harness.md, "Ending a task"). `Guard` is the backstop: after a cancel the screen may still be read, but an action
  before the harness has seen the stop raises `Stopped` (a BaseException, so it leaves `run_task` as Ctrl-C does),
  never mid-action; once it has (`Job.halting`), its clean-up may act. A paused job waits at its next screen read with
  `run_task`'s `clock` (`Job.clock`) stopped, and on resume the env is read afresh (`wait(0)`). A cancelled job's open question resolves
  as no. A stop is acknowledged ("Stopping job 1.") and the job then says nothing more.
- **Watches and schedules** (karta/watches.py, karta/when.py; voice mode only, and only while `karta start` runs:
  no daemon, no heartbeat). One rule: **code watches, the brain only reads a change**.
  - **Hearing one** (`Watches.hear`, from `app.take` after the control grammar): a schedule is read by code with no
    brain call (`when.parse`): a recurrence (every day / morning / evening, every weekday or weekend, every
    <weekday>[s] or "mondays and fridays", every N minutes or hours, at most every `MIN_EVERY_S` 15 minutes) with a
    clock ("at 9", "6:30 pm", "noon", "7 in the evening"; with no am/pm, 7–11 are morning and 1–6 afternoon), or a
    one-off ("at 6 pm today", "tomorrow at 9", "in 20 minutes", "on Friday at 3"; a date code does not read, from the
    platform's date detector, `when.detected`). The phrase must lead what was said (a recurrence may also end it, and
    "remind me" may put it anywhere), so "make an event tomorrow at 3 pm" stays a task. "Remind me to X" is said at
    the time ("Reminder: call your mom."); anything else is a task run then. A watch starts with "tell me / let me
    know / notify me when", "watch for", "keep an eye on", "keep watching", "tell me before" ("tell me if …" and
    "watch …" are tasks); its setup is ONE brain call (`WATCH_SYS`, run as a background job) that writes one line,
    `watch <probe> | on: <what> | until: <condition> [| ask: <question>] [| then: run <task>] [| repeat: yes] [| for:
    <hours or none>]`, checked by code (`parse_watch`: a probe or condition outside the table, or `stop:`, is
    refused) and bound by code (`bind`: `front` becomes the id of the frontmost Terminal window that is not Karta's
    own tty, a path made absolute, a web address checked public). Its first reading is the baseline.
  - **Probes** (`Probes`, read-only, nothing on the screen touched): `terminal` (AppleScript `busy` and `contents` of
    the window's selected tab, the last 40 lines; conditions `idle`: not busy after it was or after its text moved,
    `contains(<text>)`, `changed`), `file` (stat; a folder's names: `changed`, `appears[(<glob>)]`), `mail` (Mail's
    AppleScript, the newest 25 inbox messages' id, sender and subject; never starts Mail: `new[(<words>)]` from a
    sender or any), `calendar` (EventKit, `macos.calendar_events`: `soon`, an event starting within N minutes, default
    10, once per event), `page` (one plain GET of a public http(s) address with urllib: no cookie store, no stored
    sign-in, a local name, a private or loopback address, a user part or a redirect to one refused, at most 1 MB:
    `changed`, `contains(<text>)`), `tab` (an open tab's title on a host, through the tab reader: `changed`,
    `contains`). Each condition is code and updates the watch's state so one change fires once.
  - **Firing**: the change is said in code's words through the speech queue ("New mail from Riya Shah: Q3 numbers.",
    "Standup starts in 9 minutes.", "The Terminal window went quiet: <its last line>") and kept as a session ref of
    kind `watch` (`Session.keep`), and `then: run <task>` submits that task as a screen job. The brain is called only
    when the watch has an `ask` (terminal, page, tab, mail) and the condition fired: one short call (`JUDGE_SYS`,
    the question and at most 4000 characters of the text) whose one line is said instead ("Your build passed.").
  - **Cost cap**: at most `MAX_WATCH_CALLS` 3 reading calls per watch and `WATCH_CALLS_DAY` 20 per day across all
    watches (the day's count is in `jobs.sqlite`, so a restart does not reset it; the call is counted before it is
    made); past either, the change is said in code's words. A mail or tab text goes to the brain only when memory
    holds the user's yes for that app (the consent row, read-only on the watch thread's own connection); otherwise
    code's words. Setting a watch up is its one call; a schedule costs none to set up (its task costs what a task
    costs: a pinned one replays with no call).
  - **Backoff and expiry**: an unchanged reading steps the next check along `LADDER` 15 s → 1 min → 5 min; a changed
    one goes back to 15 s (so a calendar notice may come up to 5 minutes later than its lead). A watch ends after
    `WATCH_S` 8 h ("I stopped watching for …: 8 hours passed.") unless its line says `for:`; a one-off watch ends when
    it fires, a `repeat: yes` one keeps going. A watched Terminal window that closes ends it ("… is gone.").
  - **Schedules when due**: `next_at` is written first (never twice), then a reminder is said, or the task is
    submitted as a normal screen job ("Starting your scheduled task: …"; queued while the screen is busy), so its key
    actions ask up front by voice like any task.
  - **Restart** (`Watches.arm`, when `karta start` begins): every row of `jobs.sqlite` is re-armed with its baseline;
    a watch reads again at once (a change while Karta was off counts; a closed Terminal window ends it); a schedule
    missed by under `LATE_S` 1 h runs once, an older miss is skipped and said ("I missed your 9 AM task while I was
    off: give me a brief."), and an expired watch is said and dropped.
- **The speech queue** (`jobs.Speaker`): what jobs and control replies say goes through one queue, in order, one line
  at a time, never while a job's question waits for its answer, the talk key is held, or a tell's answer (the
  run's `speech`) is still being said. A job's end line is prefixed by its number when other jobs were live
  alongside it ("Job 2: Done."); a lone job's is not. A new press of the talk key stops the line being said;
  queued lines wait their turn.
- **Speaking answers** (karta/voice/speak.py, karta/harness/tell.py): a question is answered out loud with the
  built-in `say`, voice `Tara` (en_IN) or `KARTA_VOICE`; a voice that is not installed falls back to the system
  default, and `speak` never blocks or crashes (`Speech.stop/wait/speaking`; the text goes as an argument, never
  through a shell). `word_answer` writes the words: a plain readout is built by code from the facts (titles only,
  no URLs), otherwise one short brain call through `HarnessBrain` answers in at most 2 sentences from the facts
  alone; no facts says so without a call, and a brain failure falls back to the plain readout.
- **Saying how a task ended** (`app.end_of_task`): every run ends with `TaskResult.said`, built by code with no
  brain call (`tell.failure_line`, `tell.done_line`): a failure is what was done and the one reason ("I couldn't
  find a search box on that page.", "I stopped after 120 seconds."), a success with nothing to answer "Done." or
  "Done: <its end state>" (3–5 words). The terminal prints it (`KARTA: …`) and the `done` event carries it (`said`).
  In voice mode (`karta start`, `--voice`) it is said aloud through the speech queue (Tara); typed, only with `--say`. A
  question's answer was said by its tell step, so nothing more is said. A new press of the talk key stops it, as it
  does an answer.
- **The HUD** (karta/hud.py): a glass panel at the bottom centre (520×58 pt, 96 pt up). Events: `heard`,
  `planning`, `step`, `step_done`, `replan`, `done` (with `answer` for a question, and `said`: a failure's line is
  shown, "⊘ Couldn't finish", and kept up long enough to read), `listening`, `level`,
  `transcribing`, `idle`, `pick`, `retry`, `tell` (an answer being said: shown, and kept up long enough to read).
  `jobs` (voice mode): a row of pills above the status lines, one per live job ("▶ 1 summarise this page  ·  … 2
  open my mail": ▶ running, ? asking, ⏸ paused, … queued, ◷ watching); the panel grows 20 pt upwards for it (its
  frame file follows) and stays up while any job is live.
  A new press of the talk key, or a new typed task, stops an answer still being said (`Karta.hush`); in voice mode a
  job starting does not cut the last one's answer short.
  The Listening pill shows 7 bars following the mic, then "Transcribing…". A soft Mac-blue glow fades 34 pt in
  from the screen edges (alpha 0.45) only while a task runs. No text in the glow; everything is click-through.

## Data on disk

Everything stays on the Mac, under `~/Library/Application Support/karta/` unless noted:

| File | What |
|---|---|
| `harness-memory.sqlite` | the memory database (below) |
| `session.sqlite` | recent tasks (`harness/session.py`, mode 600): each task's words, said line, place and its refs' `@n kind title`, 14 days, at most 2000, FTS5 over task and titles; never a value (those stay in RAM, the last 8 tasks). `Karta` keeps one `Session` for typed and voice runs alike |
| `pins.json` | pinned tasks (`recipes.py`, mode 600, `version` 2): template, last values, the steps that worked with their proofs, the contract the run met, the last 3 versions; at most 50, deleted after 180 days unused. A file from before contracts drops its recipes with none on load. Templates go to the brain; values and collected data never do |
| `models/ggml-base.en.bin` | whisper's speech model, 148 MB (a clone's `models/` copy is used first if present) |
| `setup.json` | what the last `karta setup` found (`ok`, `todo`); `karta start` refuses until `todo` is empty |
| `snapshots/*.json` | `--record` runs: the plan prompt in use, every screen read, the actions and change waits in order, the brain's replies, each with the context its request was given (memory's block, the profile lines with the user's own e-mail addresses and names as their key only, the pinned templates: `snapshots.prompt_context`; a replay ignores it), Laya's questions (instructions, their placeholders' values with each line cut to 80 characters, the index of the screen read each was asked against, options, probabilities), memory's reads, the user's answers, the Mac map, the ledger (`karta/harness/snapshots.py`; web addresses without query or fragment). `scripts/laya_dataset.py` turns them into JSONL to hand-label (question, options, screen text, Laya's choice, probabilities, an empty `label`), kept local |
| `scrub-salt` | this Mac's salt for scrubbing a recording into a replay test (`scripts/promote_run.py`) |
| `hud-frame.json` | the HUD panel's frame, so the screen reader skips its text |
| `jobs.sqlite` | voice mode's watches and schedules (`karta/watches.py`, mode 600): `watches(id, kind, title, spec, next_at, created, expires, calls, state)` (the watch line or the schedule's time and task, the baseline state: fingerprints, a Terminal window's last lines, seen mail ids) and `brain_calls(day, n)` (reading calls today, older days deleted) |
| `delegate/<time>-<hex>/` | one per delegated Claude Code session (mode 700): `settings.json` (its Stop and Notification hooks, passed with `--settings`) and `events.jsonl` (what those hooks appended: their JSON input, which may hold Claude Code's last message). Not cleaned up yet |
| `logs/karta-<date>.log` | every terminal log line, plus each task's full trace and result |
| `~/.cache/huggingface/hub/models--convaiinnovations--laya` | Laya's weights |

```sql
create table harness_facts (
  kind text, app text, key text, value text,      -- kind: place | alias | know-how | element | pitfall | profile | consent | boilerplate
  good integer default 0, bad integer default 0,  -- bad is no longer written
  updated real,
  how text default '',                            -- element: "tool|text went in|sent by|change"; pitfall: the right name
  proof text default '',                          -- the contract the run that wrote it met ("created(note named X)")
  primary key (kind, app, key, value)
);
pragma user_version = 3;  -- 1: places without a tab position; 2: one browser in use;
                          -- 3: what a run teaches keeps its proof; rows learned before contracts dropped
```
`app` is where the fact holds: the page's exact host for a web page ("mail.example.com"; "example.com" for
www.example.com), the app for a native app, never a browser; "" for places, the profile (the user's own e-mail addresses and names from tasks proven done,
the browser in use, the default browser) and app-less aliases. A pitfall's key is the intent, its value the name
that did not work. A consent row (app: a mail or messaging app, or a web page's host; key `brain`; value yes or no) is
the user's answer to "May I send text from <app> to Claude to summarise it?", one per app, written when they answer.
A boilerplate row (app: a site's exact host or an app; key: a 16-hex digest of a short string it showed identically on
two of its pages; value "") is the content model's, learned from reads (harness.md, "Interface and content"), never
the text itself. A place's `value` is a site in a browser and profile, or
an app, never an open tab's position. Rows are written only from a task whose declared contract was met, with that
contract in `proof`; a remembered row that fails is deleted at once (`harness.md`, Memory); boilerplate rows are the
one kind written from reads. Caps per site or app (place 5 per intent, alias 50, know-how 12, element 40, pitfall 12, consent 1, boilerplate 120; profile 12 in all),
stale after 60 days, deleted after 180, at most 5000 rows. A file without the `how` or `proof` column gets it on open
(a read-only open reads "" for `how`). Opening a file from before version 3 drops every element, pitfall, alias and
know-how row with no proof (learned from Karta's own judgement of success, never a contract), merges tab-position
places into their site's (before 1) and keeps one `browser in use`, the latest (before 2).
`scripts/karta_memory.py show | forget | clear` reads and edits it; `pins | unpin <id or name>` lists (templates only)
and removes pinned tasks in `pins.json`.

## Install

```
curl -fsSL https://raw.githubusercontent.com/sp-22/karta/main/install.sh | sh   # or ./install.sh from a clone
# Apple silicon check → uv if missing → `uv tool install` (the clone, else git+https://github.com/sp-22/karta) → `karta setup`
```
- The package is `karta` (pyproject, MIT; LICENSE carries the copyright of the code `karta/base/` came from).
  Laya, voice and EventKit (the calendar, read-only) are normal dependencies on a Mac; the `laya`/`voice` extras stay, empty, for old sync commands.
  Installed commands: `karta`, `karta-hud`; `karta-mcp` with the `mcp` extra (`uv sync --extra mcp`). Hatch packages `karta` only (gates.json included). Mac only.
- `karta setup` (karta/setup_mac.py) checks and fixes, re-runnable: Apple silicon → permissions for the app Karta
  runs in (Accessibility, Screen Recording, Input Monitoring, Microphone; opens the Settings pane when missing;
  Calendar listed as optional, never asked for there and never a thing left to do: the first collect of a day's
  events asks) →
  whisper.cpp via Homebrew and the speech model (downloaded with progress) → Laya (loaded; downloaded the first
  time) → the Claude Code CLI → a first Mac map. Writes setup.json.
- Permissions belong to the terminal today; a `Karta.app` bundle would get its own. The repository is
  github.com/sp-22/karta (`origin`, branch `main`).

## Code layout

- `karta/app.py` (the command), `karta/jobs.py` (voice mode's job table, lanes and speech queue), `karta/watches.py`
  (watches and schedules: probes, conditions, the cost cap, `jobs.sqlite`), `karta/when.py` (a schedule's time
  phrase, by code), `karta/setup_mac.py`, `karta/hud.py`, `karta/harness/` (see `harness.md`),
  `karta/brain/claude_code.py`, `karta/voice/` (hotkey, stt, config).
- `karta/core/`: helpers on the live path: fast (screenshots, paste, front app), events (HUD), matcher (loads
  Laya), mac_env, mac_places (running apps, the tab reader's AppleScript), env, safety. `karta/decider/laya.py`: loads Laya, caches embeddings.
- `karta/base/`: the Mac layer under everything. `platform_adapter.py` (`desktop`, the one way to the Mac, which
  forwards to `macos.py` unless `using(adapter)` swaps in another, such as `fast.FastDesktop`; `Desktop` is the
  surface an adapter provides), `macos.py` (Quartz input, AX, AppleScript, Vision OCR; the abort corner; every posted click, key and scroll
  carries its modifier flags explicitly, zero when none, so nothing typed or clicked after a chord acts as if
  Command were held),
  `ax_walk.py` (the bounded accessibility-tree walk; each item keeps its ancestors as (role, identity), its
  `path`; a page's text, read with the content model's `node_class` given in), `perception.py` (capture, OCR with the read region and the
  changed-tile cache, block merging, the AX item source and its merge with OCR), `models.py` (`Item`, `AxNode`,
  `Field`, `Screen`, `Abort`), `config.py` (OCR floor, item cap, abort corner), `timing.py`
  (phase stopwatches).
- `scripts/`: `karta_harness_sim.py` + `sim_scenarios.py` (the simulator), `decision_bench.py`,
  `next_step_bench.py`, `capture_screens.py`, `karta_memory.py`, `promote_run.py` (a recorded run into a replay
  test), `laya_dataset.py` (recorded Laya questions as JSONL to label), `prompt_bench.py` (the plan prompt bench; `pairs` and `ab`, memory with and without its block), `expect_bench.py` (typed checks on recorded runs), `contract_check.py` (the outcome contract on recorded runs: each
  case's recorded ending beside the contract's verdict, numbers and kinds only), `real_check.py <blind|auto|page>` (six misses on made-up apps fixed by the real brain through the claude CLI, fakes for the screen and Laya: done and brain calls per case). `tests/`: offline tests; `tests/replay/`: replay cases. `bench/everyday.md`: the fixed live task set (dev);
  `bench/held-out.md`: the held-out live set.
  `demo/`: local recordings, never committed.

## Testing

- Offline: `HF_HUB_OFFLINE=1 uv run --quiet --no-sync pytest -q tests/`. `tests/conftest.py` blocks every call that
  would touch the machine (input, AppleScript, capture, `open`, AX actions, EventKit, the clipboard; any process; a
  delegated session's folder goes to a temporary one), and runs a test marked `v1` (a test
  that writes v1 plans with `then:` words) with `KARTA_PROMPT=v1` and one marked `blind` (about the blind plan and
  its rescue) with `KARTA_PLAN=blind`; the rest plan in the default (auto). Harness tests use fakes (`FakeEnv`,
  `FakeLaya`, `FakeBrain` in tests/test_harness_tools.py).
- `tests/test_contract.py`: the outcome contract on made-up apps (list(3, titles) rejects an author's link, a banner
  line and a form's labels; a comparison without both prices is not done; created by a count that rose; changed by the
  switch's state, its pane open not enough; a look cannot weaken it; a pinned replay whose contract fails unpins
  itself; pins from before contracts dropped; nothing remembered or pinned from a run with no contract).
- `tests/test_session.py`: session memory on made-up apps (a `{@n}` bound and typed, "summarise it" from the last
  page, no block without a reference, expiry, no value in the prompt or on disk, a code never kept, a session
  address through `foreign_detail`) and the clean stop (a dialog the task opened closed, `GAVE_UP_STOPPED`, through
  the job queue too).
- `tests/test_delegate.py`: delegate on fakes (osascript, ps and the window check stubbed; Claude Code never runs):
  the request as one quoted word whatever it holds, control characters and options refused, repo resolution and a
  non-git folder refused, the up-front question's wording, a yes opens one new window and nothing else, a no starts
  nothing, the launch check, hook inputs into spoken lines, the watching job's lifecycle and its session ref.
- `tests/test_jobs.py`: the job queue on fake jobs and envs (one screen job at a time, a queued job after the current
  one, the control grammar, cancel between steps through the real `run_task` on the fakes, pause, status wording, the
  speech queue's order).
- `tests/test_watches.py`: each probe on fakes (Terminal, a folder and a file on a temporary path, Mail, the
  calendar, a public page, a tab), public addresses only, the AppleScript replies parsed, the cost cap per watch and
  per day, mail's consent, backoff, expiry, 23 time phrases and the tasks that name a time, restart re-arming and
  missed runs, voice control, and a scheduled task's key action asking through the real `run_task`.
- Benches load the real Laya but no screen: `scripts/decision_bench.py` (bench_cases.py),
  `scripts/next_step_bench.py` (next_step_cases.py), `python -m karta.harness.carry_cases` (collect, `{name}` and
  tell on scripted Macs: brain calls per task against the gate; tests/test_harness_carry.py runs it with the fakes),
  and `python -m karta.harness.repair_cases` (one mechanical miss per made-up app: brain calls, rescues and local
  repairs per task; tests/test_harness_repair.py runs it with the fakes). Each case holds its plan in both formats
  and runs the one of the prompt in use (`KARTA_PROMPT`); their tests run the carry, pin and repair benches under
  both. Under `KARTA_PLAN=auto` a bench's scripted rescue answers its look (`sim.LookingBrain`: numbered by the view,
  then `continue`; a place the plan never named gets `continue`, counted in `arrivals`, outside the gate). Under
  `KARTA_PLAN=page` the benches answer each page call through `sim.PagedBrain`: the same scripted plan,
  page by page, as a planner reading each view would (its controls and fields by number, `look` after a step that
  leaves the page); a bench's gate is then its own plus one call per look.
- `scripts/expect_bench.py` runs the typed verifier (`karta/harness/expect.py`) over recorded runs' before and after
  screens (`--record` files, default `~/karta-everyday`) against hand labels (default
  `karta-research/designs/typed-checks/labels.json`: a run's stamp, an action number, the expectation, whether the
  step really worked) and reports agreement, false "met" and the known false dones; numbers only, never content.
- The simulator (`scripts/karta_harness_sim.py`, sets dev / heldout / swap) has a Sonnet role-player play the Mac
  and a judge score the run; it spends brain calls.
- **Replay tests** (`karta/harness/replay.py`, `tests/test_replay.py`): a run recorded with `--record` runs through
  `loop.run_task` again offline, everything served from the file, planning in the format the run used (the file's
`prompt`, v1 for runs recorded before it was kept). `ReplayEnv` (a HarnessEnv) gives the recorded
  screens in order as actions happen, with the change waits the recorded env had; each action must be the next
  recorded one, else the replay stops with the actions that ran and those that should have. `ReplayBrain` hands back
  the recorded replies by request kind (plan, rescue, end, tell; a page call takes a recorded plan or rescue reply, a look a rescue or page reply);
  a request the run never made is a problem, answered
  with a stop. Laya is the real `decide.Laya` over the recorded probabilities (in option order) and embedding
  similarities, or the real model with `replay(record, laya="real")` (offline, `HF_HUB_OFFLINE=1`). Memory's reads
  and the user's answers come from the file; a question the run never asked is a divergence (except D2's private
  kind of page in a run recorded before it was asked: answered "another kind of page", as it ran). Each case,
  `tests/replay/<name>/run.json` + `expected.json` (`done`, at most `brain_calls`, optional `reason_has`, and
  `xfail` with the reason it fails today), must take the recorded actions and reach its outcome, and show no content
  (`leaks`).
- Promoting a run: `karta "<task>" --act --record DIR` (live, with approval), then
  `uv run python scripts/promote_run.py DIR/<file>.json <name> [--expect done|failed] [--why "..."]`. The scrubber
  replaces every word of content (page text, titles, names, emails, messages, typed text, a Laya question's
  placeholder values, plan lines, web paths and queries; numbers of three digits or more) by a stand-in fixed by the word and this Mac's salt, so one word is one
  stand-in throughout and checkpoints still match; it keeps `replay.LEXICON` (interface and harness words, frozen),
  each site's own domain (not a subdomain), app names, and every kind, region, flag and probability. A false done is
  promoted with `--expect failed`. Hand-built seeds: `tests/replay/_seeds.py` (sent words on the page before a send
  that fails, a stale read right after opening a site, a full-screen browser read through the real AX walk).
- Reader conditions: `tests/test_ax_conditions.py` (full screen, split view, several windows, a small window, a
  scrolled page and list, nested same-frame groups, no windows) on synthetic AX trees through `mac.walk_app`.
- `tests/test_generalisation.py`: product code (karta/harness without its benches, karta/base, karta/core), its
  comments and every rendered prompt (the plan, rescue and end prompts, tell's, and the questions decide.py renders)
  name nothing the benches, `bench/everyday.md` or `bench/held-out.md` name (derived: the benches' apps and hosts, the
  docs' names, hosts and quoted phrases; the platform's own names and the words of a registered safety, protocol or
  platform list are allowed; a common English word, by the system's /usr/share/dict/words, is no name), and no real
  host; every module-level word list is registered in `karta/harness/wordlists.py`, and the tailored ones only get
  fewer. `tests/test_language.py`: the safety outcomes in German and Spanish equal English's.
- Live checks on the Mac need the user's approval each time (AGENTS.md); `bench/everyday.md` is the fixed set of
  everyday tasks and hard conditions to run (the dev set); `bench/held-out.md` is the held-out set (12 tasks on apps
  and sites Karta was never built on, three of them with the app in German), never used while developing. `--record` saves what happened, and the ledger, `RESULT` and `screen:`
  lines show where steps and time went.
