# The harness (karta/harness/)

The single source of truth for how Karta carries out a task. `karta/app.py` runs every task through
`loop.run_task`. The rest of the system (processes, Laya, the brain, the screen, voice, data, install) is in
`architecture.md`.

## Principle

Karta is not LLM computer use. **Code computes facts, Laya makes narrow choices, and the harness verifies and
loops. The brain splits the task into tool calls once and rescues a step that is wrong in the plan; a step that only
missed mechanically is repaired locally, by code and Laya, first: up to 3 local tries (a wait, another way, Laya's
next-best), then at most 2 looks, 5 tries a step in all.** By default (`KARTA_PLAN=auto`, below) the plan is
blind and a step whose check still misses gets one look at the numbered view of the screen at that checkpoint, instead
of a blind rescue. Under per-page planning (`KARTA_PLAN=page`, below) the brain plans each page once, from a numbered
view of it, instead of the whole task blind. A change is judged by task success, brain calls
per task (target 1; a question whose answer the brain words, at most 2), rescues and local repairs per task, harmful
actions (target 0) and time. No tool calls the brain mid-task: what one step reads, a later step uses through code.

**"Done" means the result is right, not that the steps ran**: the plan's first line declares what the task must
achieve, its contract (an answer of a type, or an outcome), and code checks that contract at the end; memory and pins
learn only from a run whose declared contract was met (below, "The contract").

**Proof is state, preferably state our action changed**: each step declares the kind of change it causes, and code
verifies that kind from what changed since just before it (below, "Typed checks"): the web address (or its change),
a field's value, the focused field, a dialog or window that opened (one more dialog than before counts, even when it
shares its title with one open already: `facts.dialogs`), an item new or gone since then (`facts.new_items`). Words
never prove a step; they take part only as an expectation's argument, taken from the task, the plan's typing or the
screen. (The v1 format's end state, below, still reads words, only where they are new since the last change.)

Built for any task, not the test scenarios. **Facts go to code, by structure the platform guarantees** (AX roles and
subroles, the default and cancel buttons, the search landmark, aria-busy, the address, focus, values, what appeared);
**meaning goes to Laya** (one of its existing questions, under its gates); **words learned from our runs are
removed**. No scenario, site or app words in product code, its comments or prompts (examples come from made-up apps
no bench uses); the brain names sites and there is no site catalog (memory keeps the places that worked); Laya's
templates are tuned on one split and judged on held-out apps. Held by `tests/test_generalisation.py`: the names of
the benches, `bench/everyday.md` and `bench/held-out.md` (derived from them, never hand-kept) and real hosts never
appear in product code, comments or rendered prompts; every word list left is registered with its category and
reason in `karta/harness/wordlists.py` (safety, protocol, platform, format), and the count of `tailored` ones only
ever goes down (`TAILORED_CEILING`). `bench/held-out.md` is the live check: a change that raises the dev set and
leaves the held-out set flat is tailoring.

## Modules

| Module | Owns |
|---|---|
| `types.py` | the contract: Item, Screen, Outcome, ToolResult, Choice, MacFacts, HarnessEnv |
| `screen.py` | items Laya can read: rows for repeated labels, OCR clean-up, the shortlist, the 512-token guard |
| `mac.py` | the real Mac as a HarnessEnv: AX + OCR reads, off-screen controls, dialogs, pixel change detection |
| `facts.py` | the Mac map (apps, browser profiles, folders, open tabs), places, login and dialog detection, what changed between two reads |
| `content.py` | the content model: each item of a read interface or content, once, by structure and the site's learned boilerplate; every reader of content takes that label (below, "Interface and content") |
| `match.py` | code-first matching of a named target to on-screen items |
| `decide.py`, `gates.json` | Laya's questions and the confidence policy fitted per decision |
| `brain.py` | plan / rescue / page / look / end-check prompts, the streamed line parser, `#n` targets as items (`numbered`), the brain's map slice |
| `view.py` | the page view: one read as numbered items (grouped, capped, privacy-filtered by code), an item's identity, and finding it again on a later read |
| `expect.py` | typed checks: each kind of change a v2 step declares, verified from what changed on screen (below, "Typed checks") |
| `contract.py` | the outcome contract: what the task must achieve, declared by the plan's first line and checked by code at the end; the one rule for done, for what a question's answer must be, and for what memory and pins learn from (below, "The contract") |
| `tools.py` | the Toolbox: each tool, carried out by code and Laya |
| `loop.py` | `run_task`: the ladder, each step's typed check and the ledger, skip-ahead, the place lock, the end state |
| `retry.py` | try, verify, next |
| `repair.py` | local repair: a step's mechanical miss fixed by code and Laya before any brain rescue |
| `progress.py` | the task's state graph: which action was tried on which screen and where it led; dead ends never repeated (below, "Progress: the state graph") |
| `risk.py` | which actions need the user's yes |
| `delegate.py` | the delegate step: Claude Code started on a repo in a new Terminal window, heard back from by its hooks (below, "Delegate") |
| `wordlists.py` | every word list product code keeps, with its category and reason; the tailored ones' ceiling |
| `memory.py` | successes on disk, failures in RAM |
| `session.py` | what the last tasks produced, as `@n` refs a later task points at ("email that to …"); values in RAM only (below, "From an earlier task") |
| `recipes.py` | pinning: the path that worked, saved as a recipe and replayed (below, "Pinning") |
| `sim.py` | a Mac a second model role-plays, for the offline suite; `ScriptedMac`, a fixed script for offline benches |
| `carry_cases.py` | the carry bench: three held-out collect shapes on scripted Macs, brain calls per task and the gate |
| `pin_cases.py` | the pin bench: pin then replay (exact, bound by code, same shape, a site change, by name), brain calls per run and the gate |
| `repair_cases.py` | the repair bench: one made-up app per kind of mechanical miss, fixed with no rescue; a wrong plan with one |
| `snapshots.py` | the opt-in run recorder (`karta --record`); each Laya question with its placeholders' values and the screen read it was asked against (`scripts/laya_dataset.py` makes them a JSONL to label) |
| `next_step.py`, `next_step_cases.py`, `bench_cases.py` | offline benches for Laya's decisions |
| `prompt_bench.py` | the plan prompt stress bench: 40 tasks on made-up screens, the real brain, each plan scored (below, "The v2 plan") |

## The plan

The brain writes one tool call per line, streamed; each step starts as soon as its line is complete. The plan format
in use is v2 (below, "The v2 plan": a step says the kind of change it causes); the v1 format, below, stays behind
`KARTA_PROMPT=v1` for one release:

```
<tool> <main text> | <key>: <value> | then: <checkpoint>
done: <2–6 words on screen only once the whole task is done>
```
The brain plans before anything is read, so it never writes a link, a code or a list itself: it plans a `collect`
once the items are on screen and puts `{name}` where they go (below, "Carrying data between steps").
Meta lines: `ask:`, `stop:`, `learned:`, in a rescue `skip:` / `replan:`, and in a plan `use:` (a pinned task with
new values, the whole reply: below, "Pinning"). In the v1 format `then:` is a step's checkpoint words, which the
harness no longer checks (a step proves itself by its declared kind of change; a v1 step, which declares none, by its
tool); its `done:` words are its end state (below).
`brain.check_step` validates each step and `brain.check_slots` the plan across steps as it streams (a `{name}` in a
write or fill, or a tell, needs an earlier `collect … | as: name`; nothing comes after a tell; a rescue may use the
names already collected); a bad reply gets one repair ask. **The v2 step grammar is strict** (blind, auto's looks and
page calls alike: `brain._prose`): a line is a step only when it is a tool's name written in lower case, its main
text, and only that tool's own `| key:` parts (any part is text only for type, write and fill); a click's, answer's
or open_place's name is at most `NAME_WORDS` (8) words or a `#n`, never ending like a sentence (`.`, `!`, `?`, `;`; an
ellipsis is a name's); a submit takes no words but its expectation; back takes none, scroll only down or up, wait a
number, reach an amount with a number; a collect's what is no sentence. Anything else ("Submit the form once…", "click
the Pay button and then close it.", an unknown word) is prose: `parse_line` returns `("prose", why)`, the line is
ignored and kept in `Plan.prose` (the trace's "ignored as prose"), never a step, so prose never reaches the risk gate
as an action. Prose beside real steps costs nothing; a reply whose only "steps" were prose has no steps and gets the
one repair ask (told to write tool calls, never sentences), unless it ended on purpose (`continue`, `look`, `stop:`,
`ask:`, `skip:`). A known tool refused by its own rules (a `press`, a write with no `where`) stays an error. The v1
format keeps its looser parser. The brain plans **UI actions only**:
a `press` step is rejected (keys are the harness's own, e.g. ⌘L for the address bar, done by Accessibility first:
architecture.md, "Keys"); a web address goes through
`write … | where: address bar` then `submit`.

### The v2 plan (the default; v1 behind `KARTA_PROMPT=v1` for one release)

The brain plans before it has seen any page, so a `then:` of words is a guess at what the app will show ("Add title",
"Message sent"), and a guess the app never shows is a false miss. Under v2 (`brain.prompt_version`: `KARTA_PROMPT`,
v2 when unset; `HarnessBrain(brain, prompt=...)`; the line format only: a brain with only `.json` gets and answers the
v1 JSON prompt) the plan prompt is `PLAN_SYS_V2`, and a click, answer or
submit says the **kind** of change it causes instead, from a closed list (`brain.EXPECT_TYPES`):

```
click <control> | expect: <type>        answer <option> | expect: <type>        submit | expect: <type>
page_on(<host or app>) · item_opened · item_opened(<name>) · results_for(<search text>) · new_item · new_item(<text>)
item_gone · item_gone(<name>) · editor_opened · dialog_opened · dialog_closed · window_opened · list_changed
toggled · toggled(<name>) · focus_moved · none        (field_holds(<text>) in done only)
done: <one of the same>                   # <a note>   (a line the parser skips: dates worked out)
```
Words inside `( )` may only be the task's own, text the plan types, a host, or a name exactly as the given screen
shows it. open_place, find, write, type, fill and collect get no expect (they check themselves); open_place has no
`ready:`, and a v2 step keeps no `then:` (`check_step` drops both: a step proves itself by its kind of change).
`parse_line(line, v2)` puts the type in the step's `expects`; `check_step(step, v2)` rejects a type outside the list
(`expect.TYPES`), a missing or extra `( )`, or more than 80 characters in it. A submit's `expect` that is not a type
stays its words; with a type, `expect` is the type's argument (or ""), since the submit tool reads words. The plan's first line is its contract (below, "The contract"):
`contract: <answer or outcome>`, written back by code in its own form (`brain.typed_contract`: a thing's name the task
never said is a guess, dropped where the kind stands). As the plan
streams, `brain.typed_step` checks each expectation against where the plan is (`here`: the site or app in front when
it was asked for, followed through its open_place steps and page_on expectations) and what the prompt gave:
page_on is only for opening another site or app, so page_on of the place the plan is in (true before the click as
well) becomes item_opened; a word inside `( )` found nowhere in the prompt (task, facts, screen) or the plan's own
typing (`brain.guessed`) is a guess and is dropped where the type stands without words. The done line must be a
type, never none (`brain.typed_done`; a guessed word dropped the same way); otherwise it is refused. Each change is
noted in the plan's errors, but is no reason for a repair ask (`brain.changed_only`). The screen goes to the brain
grouped by what each name is (`brain.screen_digest`: focused, in a popup, fields with their text, controls, rows,
text), each name exact, and "anything not listed is not seen yet". The prompt says which type fits which control
(an opening control, delete, a filter or view, a switch, a field), and that done proves the task's goal: a draft or
a saved form shows where it is listed (new_item of its title), field_holds only when the typed text is the result
itself. The rescue under v2 is the v2 plan prompt plus the same rescue lines. The loop verifies each `expects`
by type (below, "Typed checks", and the loop's steps 3, 5 and the end state).
Design: `karta-research/designs/A-typed-expectations.md`.

The prompt stress bench (`prompt_bench.py`, `scripts/prompt_bench.py run|report`) gives the real brain the same
context a run does (`brain_facts` of a made-up Mac map, the starting screen) for 40 tasks (native apps, web, across
apps, questions, risky sends; most on apps and sites never developed on), N times each, and scores each plan by code:
validity, each checkpoint's words by source (address, field value, typed, task echo, on screen, **guessed**: in
neither the task, the plan's typing, its places nor the screen), grounding of the first step when the place is in
front, places beyond the task's, the risky step present and after its text, drafts never sent, no keys, no made-up
address or number, mid-stream revisions, misfit or weak types, stability across the N plans, steps, latency and
tokens; a v2 expectation the parser changed (page_on where the plan was made item_opened, guessed words dropped) is
counted apart and does not make a plan invalid. Records go to `karta-research/designs/prompt-stress/<label>.jsonl`. Its memory pairs (`PAIRS`,
`scripts/prompt_bench.py pairs|pairs-report`): a teacher task's proven run leaves memory rows (written through
memory's API), and a different task on the same app is planned with and without the block `loop.know_how` renders
from them; scored by the control targets that name a remembered name, the guessed ones, a pitfall's wrong name used,
the guessed checkpoint share and, on the profile pair, a known e-mail used or asked for. Most pairs are made-up apps
and sites. The memory A/B bench (`prompt_bench.AB`, `scripts/prompt_bench.py ab --label <label>`): six of the pairs,
all on made-up apps, three plans each with and without the block, at most 40 brain calls (`Budget`), scored as above
plus plan validity and the looks a plan would need, simulated (`memory_score` "looks": a control target that names
nothing the app shows, its remembered names, `Pair.ui` or the starting screen, nor the task's words, misses its check
and costs a look).

## Auto planning (KARTA_PLAN=auto, the default)

`KARTA_PLAN` (`brain.plan_mode`, `HarnessBrain.mode`): `auto` (`DEFAULT_PLAN`), `page` or `blind`; a v1 or JSON-only
brain plans blind. Auto keeps the blind plan's one call when the steps pass their checks, and shows the brain the page
only where a blind plan goes wrong: at a checkpoint that missed, where the names the plan guessed are not the
screen's.

- **The first call** is the blind v2 plan (above, "The v2 plan"): memory's block, the user's profile, the starting
  screen, the pinned templates (`use:`), streamed and run as it arrives.
- **A missed check is one look** (`loop.look_call`, `HarnessBrain.look`, `brain.LOOK_SYS`): after the local repairs
  (below, "Local repair"), in the rescue's place. The brain gets the task, `brain_facts`, memory's block for where the
  screen is, the session's failures, "Done so far", what was collected, `Failed: <step line>`, `Expected: <type>`,
  `What happened: <outcome> (<the check's verdict: nothing changed, or the other kinds of change, named>)`, the local
  repairs tried, the plan's steps still to do and the numbered view (`view.build`, cap 150) of the screen at the
  checkpoint. It answers steps on that screen aimed by `#n` (resolved to identities as a page plan's, `brain.numbered`:
  no Laya target pick or pick check for them), then `continue` (the plan's rest carries on after them, `with_rest`; a
  reply with no last line is read the same way; `continue` alone means the step is not needed: ↷), `look` (its steps
  lead to another screen that needs steps before the rest: `loop.LOOK` in the queue looks there once they ran), or
  steps that replace the rest (named on later screens) and `done: <type>`; or `stop:` / `ask:`. Parsed as `Plan.carry`;
  the stream stops reading at `continue`, as at `look`; a look that ends with one of these never gets a repair ask for
  a stray sentence. A look is one of the step's tries (`MAX_STEP_TRIES`) and counts in `TaskResult.rescues`; at
  most `MAX_LOOKS` (8) a task, with `MAX_STALLS`, and at most `MAX_STEP_LOOKS` (2) for one step that missed (the
  steps its looks' fixes stand in for count with it, the end of the plan's too, and so does a look at where a look's
  steps led): then the step fails with its own reason (`GAVE_UP_LOOKS`), no third call. Every look gets what was
  tried on its screen (below, "Progress: the state graph"), and a reply that opens with what missed there is refused
  with no call; a fix aimed by number at another item than the one that missed is a new action, whatever the items
  are called.
- **A stale number gets a look again**: a look's `#n` item gone or no longer one item fails as `tools.STALE`; the
  local repair waits and tries once more, then the miss is a look like any other, never a guess. A look's numbered
  steps still queued when a step lands on another page (`view.page_key`, a dialog opened or closed) are dropped and
  that page gets a look (`Arrived:` with the dropped lines).
- **A place the plan never named gets one look**: after a step passes, a site (its registrable domain) or native app in
  front that the task did not start on and no `open_place` (its app or host) or `page_on(...)` of the plan names (the
  whole plan is read first) gets a look with `Arrived: after <step>, the screen is <place>, a place the plan never
  named` and the rest; `continue` when the rest fits. Once per place; never for a browser with no page, a system prompt
  or a pinned replay. Otherwise no look while steps pass.
- **Pins**: a proven auto run pins its steps as they ran: a look's steps by their identities, and a blind click or
  answer with the identity of the control it pressed (`loop.pinned_as`: `view.identity` of `Toolbox.last_press`; never
  a row, link, text or other content); a replay resolves them with no call (below, "Pinning").
- **Model and warm spare**: looks go to `KARTA_PAGE_MODEL`; `karta` warms the look prompt's spare at start.

## Per-page planning (KARTA_PLAN=page)

### The page view (view.py)

`view.build(screen, task, typed)` turns one read into the numbered view a page call sees, by structure only:
- **Dropped:** off-screen items, the browser's chrome except its address field (shown as its host only, never a path
  or query), nameless items and nameless layout rows (`Item.hint` rows), exact repeats (same kind, name, region and
  row).
- **Tiers,** taken in order until `CAP` (150): the focused field; a dialog's items (and the browser's own bubbles);
  fields; controls (button, link, checkbox, tab); rows; menu commands ("Menu > Command"; the app's own menu and the
  last menu, Help, after the others: the menu bar's order); then text, headings, images and the rest; then the other
  interface. Rows and text are the content model's content only ("Interface and content", below): an item it calls
  interface that is no control, field or menu command (a banner's text, a form's label, a sidebar's row) is listed
  last, under `interface`, still a target.
- **OCR:** an item read only off the pixels is marked `[ocr]` and listed right after the item read off the
  accessibility tree whose row it is drawn in (on its line to its left, else the nearest above in its column within
  `ROW_GAP_LINES` of its lines), taking that item's tier, so the cap never drops it as loose text. Without frames (a
  recording) it stays in the text tier, still marked.
- **Shown** grouped (focused, dialog, fields, controls, rows, menu commands, text, interface), numbered 1..n in display order:
  `#n kind 'name' = value [focused, disabled, on, off] @region (host)`. A link to another site shows its host beside
  its cut name; one of several controls alike shows the row it is in (`Item.row`).
- **Privacy, by code:** a field's text shows only when it is the task's or text Karta typed, else `(holds N chars)`
  (a password field never); on a personal surface (a native app, or a host whose first label is mail, accounts,
  calendar or messages: `PERSONAL_HOSTS`) content longer than `SHAPE_WORDS` (4) words shows only as `[N words]` (on
  such a site its controls too), the window title is hidden, and a run of `FOLD_RUN` (3) such rows folds into one
  line `#a..#b N rows, [k words]` (each keeps its number); names are cut to `NAME_CHARS` (48); e-mail addresses are
  masked unless the task holds them. A mail or messaging screen without the user's yes shows its structure only
  (below, "Consent for mail and messages").
- **Numbers are one read's.** `View.items[n - 1]` is the Item #n and `View.labels[n - 1]` its name as shown. A step
  keeps the item's identity (`view.identity`: name, kind, region, its AX roles from `Ref.path`, and among items of its
  name and kind which one, `nth` of `of`), never the number; `view.resolve` finds it on a later read: the one of that
  name and kind; of several, by its roles, then its region, then the same one of its namesakes while there are as
  many as before. Gone, or no longer one item: None, never a guess.
- `view.page_key`: a page is its app, profile and web address without the fragment, else its app and window title.

### The page call (brain.py `PAGE_SYS`, `HarnessBrain.page_plan`; loop.py)

`KARTA_PLAN=page` plans per page; `blind` plans the whole task before any page is seen and rescues a miss blind;
`auto`, the default, is above. Page planning needs the v2 line format (`HarnessBrain.page`; a v1 or JSON-only brain
plans blind).

- **What the brain gets** (`brain._page_context`): the task, `brain_facts`, memory's block for where the screen is
  (`loop.know_how`, with the user's profile), the session's failures, "Done so far" (each passed step's plan line:
  the brain's own words, a `#n` item as the view showed it), what was collected (names and counts), the step that
  missed (below) and the view. On a task's first call also the pinned templates (`use:`, as the blind plan). The
  first call is the one that understands the request: there is no plan call before it.
- **What it answers** (`PAGE_SYS`): the steps on THIS screen, one per line, aimed at items by number:
  `click #n | expect: <type>`, `write #n "text"`, `fill #n:"value" #m:"value"`, `collect #n, #m | what: … | as: name`,
  plus the v2 tools that name no item (open_place, find, type, submit, collect by `what`, tell, reach, back, scroll,
  wait) and the v2 expectations; then `look` (the task goes on past this screen) or `done: <type>`; a question ends
  at its tell; `ask:` and `stop:` as before. `parse_line` reads `look`/`look: <why>` (`Plan.look`; lines after it are
  not read) and the numbered forms; a named target still parses and runs as a v2 step (code first, then Laya).
- **Numbers become items as the reply streams** (`brain.numbered`): `#n` must be on the view (else an error and the
  one repair ask); the step keeps `at` (`view.identity`; a fill a list, one per field; a collect `rows`) and the
  item's name as the view showed it as its target words. The number itself is never kept.
- **Carried out exactly** (`Toolbox.click_at`, `numbered_item`; `tool_write`, `tool_fill`, `tool_answer` with
  `at`): the item is found again by its identity on the screen now (re-read while the page may still be coming) and
  acted on; no Laya target pick (D3), no pick check (D4), no row-mate guess in `press_item`, no next-best retry
  (`retry.why_not`: an exact pick), and the local repair never swaps it for Laya's options or the focused field
  (`repair.missing`, `repair.options`). The risk gate is the same as any click's (a send, payment or delete asks the
  user; a permission prompt is still the user's). The browser's address field written by number goes through the
  address bar's own way in (its field by AX, else ⌘L). A dialog in the way is not cleared when the step's item is inside it. Gone, or no longer
  one item: the step fails "no longer on screen as the page showed it" (`tools.STALE`), the local repair waits for a
  change and tries the step once more, then a look, never a guess.
- **Look once per page** (loop.py): the page plan streams in and runs as it arrives. It ends with `look`: once its
  steps ran, the screen now gets a page call. A step that lands on a new page (`view.page_key` changed, or a dialog
  opened or closed: `facts.dialogs`) drops the steps still queued from that page's view, when any targets a number,
  and looks at the new page; steps naming no item (collect, tell) run on. A page plan with no step on a screen that
  has not changed after a short wait stops with its `look:` reason instead of looking again.
- **A missed check is one look** (it takes the rescue's place): after the local repair, the screen now gets a page
  call with `Failed: <step line> -> <reason>` and what was tried locally, read whole; it counts as one of the step's
  tries (`MAX_STEP_TRIES`) and in `TaskResult.rescues`. Its steps replace what was queued from the old view
  (a reply with neither `look` nor `done:` is a fix: the old steps that name no number carry on after it). The
  `Failed:` line carries what was tried on this screen, and a reply that opens with what missed there is refused, as
  a look's (below, "Progress: the state graph"). `MAX_STALLS` holds.
- **A miss in a pinned path** (an exact, named, bound or same-shape replay): the look's `Failed:` line also lists the
  recipe's steps still to do ("Still to do after it"), the brain answers with the steps that get the failed one done,
  and the recipe's rest carries on after them (`with_rest`), as a rescue's self-heal; the path that ran becomes the
  recipe's next version once proven.
- **Bounds:** `MAX_LOOKS` (8) page calls per task, besides the budget, the retry cap and the no-progress rule.
- **Model:** page calls go to `KARTA_PAGE_MODEL` (default `sonnet`, `brain.PAGE_MODEL`) through the same warm pool
  (`ClaudeCodeBrain.stream_lines(..., model=)`: one spare per system prompt and model); `karta` warms the page
  prompt's spare at start.
- **Pins:** a proven page run pins its steps as they ran: their `at` identities (name, kind, region, roles, which of
  its namesakes) and target words, never a number; only the name may become a `{pN}` value (`recipes.KEPT`). A
  replay resolves them by identity with no call; a step that misses on replay gets a look, as above.
- **Collect** keeps its path; `collect #n, #m` takes exactly those items (title, address, and a value `what` asks
  for), found again by identity, with no Laya question; none of them left: the usual collect reads the page.

## Typed checks (expect.py)

A v2 step's `expect` names the KIND of change it causes; `expect.verify(expect, before, after)` checks that kind
from what changed between the screen just before the step and the screen after it, by structure only: the address
(its site, its page and query), the app, profile and window title, the page's own dialogs (`facts.dialogs`; a
browser's own prompt is the browser asking), the field that holds the keyboard focus, fields' values, the items new
or gone since (`facts.new_items`), the count of a list's rows (or files), a checkbox's or switch's checked state.
Words take part only as a type's argument, which comes from the task, the plan's typing, a host or the screen.

| Type | Met when |
|---|---|
| `page_on(<host or app>)` | that host (or a subdomain of it) or app is in front now and was not before |
| `item_opened`, `item_opened(<name>)` | the same site at another address, or the same app showing a window of another title; after a click on a link, a page on another site too (a content link leads off); with a name, it shows in the title or what is new |
| `results_for(<text>)` | the page moved on (another address or title) and the text is in its address, title or a field |
| `new_item`, `new_item(<text>)` | the same view (same place and address; no more went than half of what it showed or 3, no more came than it showed: a page that loaded or was read afresh is no new item) lists one more row or file than it had, or shows a new item (never a field: that may be our typing) holding the text; with text, also a new row in the same place, address and window after the list was redrawn, when the text was nowhere before. A row renamed in place is no new item (`expect._renamed`: a row went from the very spot the new one is drawn at; with no frames, the rows' count did not change). **Where it is listed**: with text the plan typed this task (a draft's subject, a form's title), a new row holding it in another view of the same place (another address or window title: the list that keeps saved drafts) |
| `item_gone`, `item_gone(<text>)` | the same view lists a row or file fewer, or nothing holds the text any more |
| `editor_opened` | a field of a name not there before appeared, empty or focused, or an empty field took the focus |
| `dialog_opened` / `dialog_closed` | one more / one fewer page dialog, or a dialog's items all new / all gone (the browser's own notice too); closed also when a notice went (its control and more gone, no control new; a dismissal, never on a submit or the end state) or the form that held text is gone |
| `window_opened` | one more of the app's standard windows (`Screen.windows`), another of its windows in front (the focused window drawn elsewhere, `Screen.window_at`: a new window cascades under the same title), an app's window of another title (no web address), or the browser's own new page |
| `list_changed` | the same place: rows added or removed, a tab or view selected, or 3 or more items changed |
| `toggled`, `toggled(<name>)` | a checkbox or switch (that one) flipped, or a toggle button renamed itself in place ("Pin" became "Unpin": the one control that went and the one that came, at the same frame, or with no frames one name inside the other), nothing opening |
| `focus_moved` | a field took the keyboard focus |
| `field_holds(<text>)` | a document's own text newly holds it: a field of several lines, in no dialog, with no other field (but a search box) holding text; one field of a form is no proof the thing exists |
| `none` | always; proves nothing (never a done, never a skip) |

The verdict is **met** (with its proof), **nothing changed**, or **a different change**, named as the kinds that did
happen ("expected new_item, but dialog_opened"); a change of no listed kind is "something else changed"
(`expect.UNNAMED`), which may still be on its way. `expect.COMPATIBLE` lets close kinds pass, for an expectation
without words: the opening kinds (editor_opened, new_item, dialog_opened, window_opened) prove each other, except
that a dialog or window with no field to type into never proves a new item; list_changed accepts results, an item
added or gone, or an item opened. On a submit, the form closing and the item it sent appearing prove each other.

Validated offline on recorded runs (`scripts/expect_bench.py`: each recorded action's before and after screens
through the verifier, against hand labels kept outside the repo; it prints numbers only).

## The loop and the ladder (loop.py)

Per step, in order:
1. Drift: a login page, or another app or profile mid-task → back to the place that worked.
2. A dialog that blocks the page is cleared first, unless the step acts inside it (Tools, "A dialog in the way"); a
   panel (a side cart, a drawer) is never cleared: the step acts on the page.
3. **Skip-ahead** (`met_since`): a later step's expectation with words (the task's: they name what must be there)
   met by the state now, since the plan was made, marks every step up to it done; a step on the way with an
   expectation with words must be met too. A kind with no words (a dialog, a list that changed) could be an earlier
   step's doing, so it never skips. Never past a step that does work (write, type, fill, submit, answer, collect,
   tell): what it writes, sends, reads or says is needed whatever the screen shows, so only the steps that get there
   are skipped.
4. The tool runs (code first, Laya for choices) and returns an `Outcome` with an honest reason.
5. Its **expectation** is verified by type (`typed_check`, `expect.verify` from the screen just before the step, or
   before the retried click, to now, re-reading while the screen still changes): met (✓ with the proof), nothing
   changed (`Outcome.NO_CHANGE`, also when the tool itself saw no change), or a different change
   (`Outcome.UNEXPECTED`, named: "expected new_item, but dialog_opened"). A step with no expectation (open_place,
   find, write, type, fill, collect; any v1 step) is proven by its tool. A submit under a type reports "sent" once
   the screen changed and its text left the field (`Toolbox.by_type`), and the type proves the rest; on a submit the
   form closing and the text it sent appearing prove each other. A pinned replay's step with no expectation must
   also reach the address it reached before.
6. A wrong Laya pick → **try, verify, next** (below).
7. The **ledger** (`TaskResult.ledger`) records ✓ (with its proof), ✗ or ↷ (skipped).

A typed miss climbs the same ladder: **nothing changed** or **a different change** goes to the local repair (a wait
for a late change, another way, the next-best, below), never to the brain directly; a different change is undone
when that is safe (`retry.undo`, by what changed) before another click; then a look told exactly what changed (the
reason names the expected and the observed kinds).

**The try policy, per step that missed**: up to `repair.LOCAL_TRIES` (3) local tries, always in this order, each a
fraction of a look's time and cost: 1. wait and read again (a late change); 2. the same action another way; 3. Laya's
next-best candidates for the step's target (try, verify, next), each verified by the step's typed expectation. A rung
that does not fit the miss is skipped and costs no try. Only then a look, at most `MAX_STEP_LOOKS` (2); then the
step stops with its spoken reason. `MAX_STEP_TRIES` (5) counts them all for one step (below, "Ending a task"); a step
with fewer tries left climbs only that far. A step that sends, answers or commits only ever gets the wait.

A failing step climbs the ladder: option elimination inside the tool → **the local ladder** (below, "Local repair":
the wait, another way, the next-best by try, verify, next) → the next place (inside `open_place`) → back to the place that worked (it counts as a local repair) → **a
brain rescue of that step only** → ask the user. The rescue gets what is done, the reason, the steps still to do,
the screen, the session notes and the local repairs tried on the step that missed, and replies with steps that fix
the failed one; the rest of the plan is kept (`with_rest`) unless it says `replan:`. A rescue is for a step that is
wrong in the plan (a step missing before it, the wrong order, a target that means the wrong thing), not a mechanical
miss. Repeats are the state graph's (below, "Progress: the state graph"): an action that missed on a screen never
runs there again, a rescue that opens with one is refused with no call, and the rescue is shown what was tried on the
screen with its local tries.

Under auto and per-page planning the rescue rung is a look at the screen (above, "Auto planning" and "The page call").

A task is done only when its **end state** shows (`typed_end`): the `done:` expectation verified by type, each kind
judged from the right screen (`loop.end_refs`; `before_of` keeps the screen just before each step that passed),
re-reading while the screen still changes:
- **an event** (`dialog_closed`, `dialog_opened`, `item_gone`, `new_item`, `editor_opened`, `window_opened`,
  `item_opened`, `list_changed`, `focus_moved`): from just before the step that should have caused it (a passed step
  whose own expectation is that kind or a close one: `expect.COMPATIBLE`, a submit's form going and what it sent
  appearing), then just before the last step that acted, then just before the last step that changed something
  (write, type, fill, submit, answer), then the start. A dialog the task opened and closed is never judged against
  the start screen, where it did not exist yet;
- **a state** (`page_on`, `field_holds`, `toggled`, `results_for`, `loop.STATE_KINDS`): on the screen now against the
  start ("it changed"), then from the same steps as an event (a switch the start screen did not show: from just
  before the click that flipped it);
- **the last step's own proof**: when the last step that acted (only collects after it) met the same expectation by
  its own check (the same kind, and the same words when the done line has words), that proof is the end state's
  (`loop.reused`);
- **words at the start prove nothing**: for `new_item`, `item_opened`, `field_holds` and `results_for` with words
  (`loop.WORDS_KINDS`), when the start screen in the same place already showed them (a line left from an earlier
  message, a field or address that held them), only the start itself may be the reference, and no step's own proof
  stands (`loop.old_words`).
With no `done:` line, the last step that acts or changes something must have passed by its own expectation. Pure code: no Laya question and no brain check. The words fallback is only for a v1 plan
(`end_state`, a `done:` of words): code first (never Karta's own typing; never words
on screen at the start; words only new since the screen just before the last step that changed something (write,
type, fill, submit, answer): a "sent" line left from an earlier message is no proof this one went; a content field
holding the new text counts), then Laya's outcome question and then one brain check, both on what changed since
that screen, when code cannot tell.
Never because the plan ran out; a last step (or done line) of kind `none` proves nothing. **This typed end state is the
end check only of a run with no contract line** (its derived `done` contract, below, "The contract"); a declared
contract is checked by its own kind instead (`loop.end_check`). **An answer contract holds only by its answer, said,
never by an end state or a bare "Done."**: when the plan runs out with no answer said (a look's steps replaced the
rest and its tell with a `done:` line), the tell is added again from the name collected last (`Toolbox.last_slot`:
what a look read the answer into), else its own; it runs and climbs the ladder like any tell (the same tell told from
other items is no repeat), bounded by its own tries (`MAX_STEP_TRIES`). With nothing to tell from, the task stops not
done: "what I found doesn't answer it".

Bounds: the give-up rules (below, "Ending a task"), `MAX_RESCUES` 2, `MAX_STALLS` 2 (rescues in a row with no step
passing), `MAX_DRIFTS` 3, `tools.MAX_MOVES` 2 places left per task (then the user). A **locked Mac** stops the task at once,
before the plan and between steps, with "the Mac is locked" (`loop.mac_locked`: `MacHarnessEnv.locked`, the session's
`CGSSessionScreenIsLocked` flag from `CGSessionCopyCurrentDictionary`, or `loginwindow` in front); nothing waits out
the budget. `TaskResult.rescues` counts brain rescues and `TaskResult.repairs` the steps a local repair fixed. The last rung,
asking the user, is used at most once a task and never past a give-up rule.
`TaskResult` also carries `answer` (what a tell said) and `speech` (its `Speech`, stopped by the next press of the
talk key), `steps` (per step: reads, read_s, wait_s, act_s, laya_s, tries), `timings`
(`env_s` = full reads + waiting on the real Mac), and `brain` (per request, with output tokens).

- **A pin is whole:** a run whose submit had nothing written before it (the text got there some other way) is never
  pinned (`recipes.whole`), so a replay never presses return into an empty box. A question's pin answers it by its
  contract: an answer contract holds only by a tell.
- **A tool that raises fails its step, never the run.** An unexpected error in a tool becomes `UNEXPECTED` with
  "an internal error in <tool>: <kind>" and the last lines of the traceback in the trace; the abort corner (`Abort`)
  still stops the run at once.

## The contract (contract.py)

The plan's first line declares what the task must achieve; code checks it; the brain only declares it. One mechanism
for done, for what a question's answer must be, and for what memory and pins learn from.

```
contract: list(<N>, titles|items|links|events|prices|names)    an answer: N distinct items of that kind (no N: one or more)
contract: value(number|time|date|price|word)                    an answer: a value of that kind, in a fact it cites
contract: comparison(<A> vs <B> on <measure>)                   an answer: both values said, each from its own row, a side named
contract: decision                                              an answer: yes or no first, cited
contract: summary(of <what is read>)                            an answer: a cited summary
contract: created(<kind> named <name>)    an outcome: a new item holding the name, or a list's count that rose
contract: changed(<setting> to <value>)   an outcome: the control's own state is the value (its pane open is not enough)
contract: sent(to <recipient>)            an outcome: the send's own proof
contract: opened(<the thing>)             an outcome: the thing's own view is in front
contract: deleted(<the thing>)            an outcome: it is gone, or a list's count fell
```
The plan prompt (v2, and the page prompt's first call) says which fits which task: a question's is an answer; a
search, a page or an item to show is `opened`; a switch, a checkbox or a control that pins, stars, bookmarks or follows
is `changed(<setting> to on|off)`; N only when the task asks for a number.

- **Declared once, never weakened**: `brain.typed_contract` reads the line (count words as numbers: "three" is 3); the
  loop fixes the contract the task's first plan declared (`loop.contract_now`; a pinned replay's or a `use:` line's
  is its recipe's) and never changes it: a look's or rescue's own `contract:` or `done:` line is not taken as the
  check (a done line still tells the loop that the look's steps replace the rest). Their prompts say the contract is
  fixed; a look at the end of the plan is told `Expected: the contract <c>` with why it does not hold.
- **No contract line**: one is derived by code from the plan (`contract.derived`): `answer` (any cited answer, the
  not-found line excluded) for a plan or look that holds a tell or a task ending with a question mark, else `done`, the
  plan's done line checked as before (above, the typed end state; a v1 plan's words). A derived contract is checked the
  same way but `declared` is false: nothing is learned from the run.
- **Answers** (`loop.tell`, `contract.answer_check`), each against what was collected (`tools.Collected`, each item
  with the `role` the content model gave it where it was read: `content.roles`, below "Interface and content"):
  - `list`: the collected items of its kind (`contract.fits`): titles and names a result's own title, never an
    author's or category's link, a counter, a banner line or a form's label; events an event read from the calendar or
    a title; links an address of their own; prices a money value; items any content. The distinct first N are read
    out by code with no brain call (whatever the tell's `as:`); fewer than N: the tell fails, "only k of the N titles
    asked for".
  - `value`: a value of that kind in the answer, held by a fact it cites (a price by its amount, said with or without
    its symbol; a time or number by its digits).
  - `comparison`: code's own comparison goes first in the facts (`contract.comparison_line`: each side's first priced
    item, the lowest and the highest), across every name collected; the answer must say a value of each side, each
    taken from a collected item that names that side (`tools.named_fit`) in a row of its own, and name a side.
  - `decision`: the answer starts with yes or no. `summary`: it cites what it read.
  - Never from the page's frame: a fact read off interface (a banner, a label) is no content.
  The tell's one wording call is told the shape the contract asks for (`contract.hint`, in the same call). An answer
  that does not meet its contract fails the tell (`Outcome.NOT_FOUND`, "the answer does not meet the contract …"):
  the name it read is marked to be replaced by its next collect, and the ladder takes it.
- **Outcomes** (`loop.outcome`), by structure on the screen now and the reads before it (the start and the screen
  just before each step that passed), re-reading while it still changes:
  - `created`: an item holding the name where no earlier read of the same app or site had one (`contract.appeared`;
    never a field: our own typing), then the typed `new_item(<name>)` and `new_item`, then a list's count that rose
    (`contract.counted`: an item whose name holds one whole number, never a time or a date, the same name with a bigger
    number: "All, 14 reminders" now "All, 15 reminders"). `deleted`: `item_gone(<name>)`, `item_gone`, or a count that
    fell.
  - `changed`: a control named the setting (never plain text), or one a step of the plan proved it flipped (its
    toggled check) named in a word of the setting ("Use dark appearance" for "dark mode"), in the asked state
    (`contract.state`: a switch or checkbox on or off; else a control showing the value, or a selected option named
    so); such a flipped control that renamed itself in place ("Pin" became "Unpin") holds the change.
  - `opened`: another view than the start (its address, fragment included, its window title, a window or dialog
    more, or a heading new: a reading pane) titled as what was opened: its window title, a heading or its address (a
    search's words in its query, its host) holding the contract's words, or a title that is part of the name of the
    content item a step opened (a row's name holds its subject).
  - `sent`: the last click, answer or submit passed by its own expectation of a send's kind (`expect.SENT`: the form
    closed, or what it sent is listed), else the first plan's done line of that kind; a recipient that is an e-mail
    address must have been written by a step.
  A contract that does not hold fails the end of the plan with why; the ladder takes it (the wait, then a look told the
  contract), and the task ends not done with that reason: never "Done.".
- **What is learned** (`loop.run_task`): memory (`loop.remember`) and pins (`RecipeBook.after`) only from a run done with
  its **declared** contract met; each row and recipe keeps that contract as its proof (memory's `proof` column; a
  recipe's `contract`, values as `{pN}`). A run with no contract line keeps nothing ("memory: nothing kept (no
  contract was declared and met)"). A pinned replay is checked by its recipe's contract like any run, and a replay
  whose contract was checked and did not hold unpins its recipe at once ("pinned task r3 failed its contract (…):
  unpinned"); a replay that failed otherwise counts a failure as before (`BENCH`).
- Re-checked offline on recordings: `scripts/contract_check.py` (each case a recording, the contract its plan declares,
  and what is judged: the items an answer said, by their roles on the reads they came from; a comparison answer; an
  outcome on the recorded reads), numbers and kinds only.

## Ending a task (loop.py, tell.py)

A task ends done (its end state shows, or its question is answered), or it **gives up** at the first of these rules
it hits; the constants are together at the top of `loop.py`:

| Rule | Constant | Counts |
|---|---|---|
| out of time | `BUDGET_S` 120 s | the wall clock since the task began, brain time included (the plan streams while steps run); the user's own time answering a question (`confirm`, `ask`) and a role-played Mac's time are left out. Checked between steps, before a rescue, and inside try-next and local repair |
| a step out of tries | `MAX_STEP_TRIES` 5 | per step that missed: each local rung that ran (the wait, another way, the next-best: at most `repair.LOCAL_TRIES` 3, fewer when the step has fewer left), each look or brain rescue, each re-run (back to the place that worked then the step again, the step again after the user's reply). Steps are counted by what they say, so a fix that repeats the step counts with it; a rescue's other steps are a new approach and count their own tries from zero (the rescue itself is one of the failed step's); the user is not asked to go on once a step and the steps its rescues stand in for have 5 tries between them (`chain`). At 5 the task stops; no look is asked for a step with no try left. Reset when the step itself passes (✓, or ↷ by a local repair or a rescue's skip) |
| looks at one step | `MAX_STEP_LOOKS` 2 | auto and per-page planning: the looks at one step that missed, the end of the plan included (its fixes' misses count for it); a third miss fails the step with its reason, no brain call |
| stopped | `GAVE_UP_STOPPED` | the user said stop (`run_task(stop=…)`, voice mode's `Job.stopping`): checked at the top of each loop iteration, before the budget, and inside try-next, local repair and before a look or rescue (`in_time`); never mid-action. A dialog the task opened is closed (`cancel_opened`), no question is asked, nothing is pinned, and it says "Stopped as you asked." (`STOPPED_SAID`) |
| no progress | `progress.STILL` 4 | steps tried in a row with no progress (`Progress.still`): no step's expectation met ✓, and no screen state reached that the task had not seen before (where the step and its local tries left the screen, so a wrong pick undone is none); any step, a refused repeat included (the clicks and undos inside one step are its tries, above). A ↷ is no progress: a rescue that waves one failing step after another through stops here |
| nothing untried | `GAVE_UP_TRIED` | a look or rescue that was shown what was tried on its screen answers with nothing untried there (its first step missed there already, or no step and no question): the task stops with the list, "I tried 'A', 'B' and 'C' on this page and none worked" (`Progress.exhausted`: the last three that missed), and the user is not asked to go on |

A rescue whose verdict is `stop:` (at once: no question for the user after it, unless the brain itself asked one), a
told look or rescue with nothing untried, no rescue left, a locked Mac, or the user saying no also end the task, not done. **The
user's no** (`Outcome.REFUSED`: a risky yes/no or a permission answered no) ends the task at once: no local repair, no
rescue and no question; a dialog the task opened (one not there when the task began) is then closed by its own
cancel control (`loop.cancel_opened`, `facts.dismiss_option`), so nothing is left half-done. The user is never asked a question to carry on past a rule (`TaskResult.gave_up`
says which; the trace says "not asking the user"): in voice mode Karta stops and says why instead. A risky yes/no
(`confirm`) is not that question: it is asked whenever a step needs it.

**The line it ends with** (`TaskResult.said`), built by code from the ledger and the last failure, never by a brain
call: what was done (the last step that passed, in words: `tell.step_words`) and the one reason
(`tell.reason_words`, from the failed step's tool and Outcome; a rescue's `stop:` words are used as they are),
at most 30 words:
- "I couldn't finish: I got to Postbox but the To field wouldn't take the address."
- "I couldn't find a search box on that page." (nothing passed yet)
- "I stopped after 120 seconds." / "I stopped after 120 seconds: I got to Postbox, but got no further."
- "I couldn't do it: clicking East got me nowhere." · "I stopped: the Mac is locked."
A success with nothing to answer ends "Done." or "Done: <done_shows>" when that is 3 to 5 words; a question's line is
its answer. A contract that did not hold ends with its reason, never "Done.". `TaskResult.failed_step` and `failed_why` name the step it stopped on (its plan line) and its last
reason. How the line is shown and said (voice mode, `--say`): `architecture.md`, "Voice and the HUD".

## Places

- `open_place` picks from the Mac map (`facts.places_for`) and tries the places **in the order the evidence gives**
  (`facts.rank_by_evidence`, pure): a tab open on the site now (the live tab list); the place memory has for the
  intent, then the other places memory saw work; an app the task names (a service's own app is just an installed app it names; the
  longest name wins, `facts.named_apps`: words inside a longer app's name the text also says never name the shorter
  app); the other
  profiles of the browser with a tab on the site, then of the browser the user is using (`Toolbox.using`: the
  browser in front when the task started, else the default browser); the default browser's other profiles; every
  other browser. Places that failed this session go last. No Laya question orders them. Only the user's own words
  narrow the browsers ("in chrome" in the task); a browser the brain writes into `what` does not. A place that
  names an app ("Brave Browser") opens the app, not a site the task mentions.
- **Ready by code** (`Toolbox.ready`): a place is ready when it is in front (`showing`: its app and profile, a site's
  address on the site), the page has loaded (no `Screen.busy`, never a loading line's words, after the change wait), it is no
  login page and nothing blocks it (a blocking dialog is cleared first). The brain's ready words passing early are
  welcome but never needed. Laya's page state (D2) is asked only when code cannot tell (the place is not in front,
  e.g. another profile's window), and only its sure "ready" passes; any other answer fails the open in place, never a
  reason to leave (a dialog or another place are code's facts). A place in front with no password field gets one
  question, D2's kind of screen alone (`Toolbox.asks_sign_in`, `Laya.screen_kind`): only a "login screen" Laya is sure
  of under the risky tier makes it a login page (a weak signal by design: a header that offers "Log in" never moves
  a place). Nor when the plan's next step types into a field that page shows, by its words or its #n item
  (`Toolbox.fills_here`: a one-time code, an account to sign in with): that page is the place the task needs. Under
  `KARTA_PLAN=page` the next step is not written yet as the place opens, so a sign-in page there still moves the place. Never for a native app (no web address, `tools.native`): it is a login page only by its password field,
  and Laya's "login" verdict on it, in that question or in the page state, is ignored (a terminal's "Last login"
  line is no sign-in page).
- **A login page, by structure** (`facts.is_login_page`): a password field on the page (AXSecureTextField, role or
  subrole), in any language, whatever it is called. A page that asks to sign in without one (an account field, sign-in
  buttons only, a code to scan) is meaning: Laya's sure page state as the place opens (above). A page opened just now
  that landed on another host than the one asked for (`tools.redirected`: its address left the page it was on) is
  followed: it is judged by the page it landed on, a login page only by the rules above, and otherwise that host is
  the place for the rest of the task (`Toolbox.sent_on`). A tab switch that lands on another site, or the browser's own
  error page (no web address), never reached the place. Drift mid-task and the return to a place read the password
  field only.
- **Moving only on proof** (`Toolbox.place_verdict`): a place is left for the next only when it did not open (its
  app never came to the front, or its page never reached the site), shows a login page (by structure, above),
  shows a page that failed to load (known by its address: the browser's own error page is no address on the site, so
  it never reached the site; its words are never read), or is an app with no account set up (`Toolbox.empty_place`, only when the plan goes on to find
  something: no content rows by structure, `facts.content_rows`, and Laya sure, D7 over the page, that it says no
  account is set up or nothing is listed yet; a site that opened with no login page is signed in, so an empty view
  there is still the place). Anything else that opened (still loading, Laya's
  "something else", an unsure Laya) is **settled in place** (`settle_in`): a wait and a re-read, then the site's root address
  typed into the address bar of the same tab (`open_root`: a leftover compose or settings view of the right site);
  only a login or error page then lets it go. Still not ready: the step fails with that reason and the place is
  kept (locked) for the rescue, never left. Every move logs one reason line (trace, terminal `place`, the `move`
  event): "moved to Brave Browser Work: login page in Personal". At most `MAX_MOVES` (2) per task; then the user
  is asked where to open it (`Toolbox.must_ask`; the loop skips the rescue for it) and the count starts again
  after the answer. After a move the next place is always opened, even when its app and site seem in front (a
  window's profile is not always known).
- A web place is ready only once the page itself is there (`Toolbox.arrive`, for an opened address and a tab switch
  alike): the address bar and the document's own address change before the page does, so the read right after
  opening can carry the new address over the last page's content, and its words can name the new site. After
  opening, the pixel-change wait (below, "Waiting for the screen") from the picture of the page before, then
  re-reads (`rechecks`, bounded) until the address is on the site (`on_site`: the asked host or a subdomain of it,
  never a sibling subdomain, `facts.on_host`: a search page of a domain is not its mail host) and the content is not
  the last page's (`still_old`): most of the page's items equal the same app's last read of another site, or, when
  that page is unknown (the browser came from behind another app), page text read only off the pixels while the
  page exposes no element of its own. A page that still shows the last one after the waits is the wrong place,
  never ready on the old page's words; one read only off the pixels is taken once the waits are over (a browser
  that exposes no page elements).
- An open tab is found by its address, never its position: switching to one re-reads the browsers' tabs at that
  moment (`Toolbox.open_tab`, `read_tabs`) and takes the tab of that browser (and profile, when known) on the site;
  with none there any more, the site is opened instead. After the switch the place counts only once the page's
  address is on the site (`arrive`, `on_site`); a switch that lands on another site is a wrong place, never read as
  the site. `showing` too is by address (`on_site`), never by words or a substring of the address.
- **Place lock**: once found, a place is final for the task; a later `open_place` for the same app or site only
  brings back that tab (found again by its address) and profile, never the ladder again (`Toolbox.found_place`). The
  lock never stands in for another app that both the step and the task name (`Toolbox.named_apps`): a place found as
  the wrong app is no reason not to open the one asked for.
  A brain rescue cannot move either, enforced in code: an `open_place` a rescue sent (`Toolbox.from_rescue`, the
  loop marks each step a rescue sent) is kept to the locked browser and profile (`stay_in`): another browser or
  profile is dropped, the locked browser named as an app is the locked place, and a site opens in the locked
  browser and profile; with nothing left it fails, never moves.
- A tab the user already has open on the site is trusted: it is ready by code once loaded, whatever view it shows.

## Tools

`open_place(what, ready_shows, site)` · `find(text, meaning, alternatives)` · `click(target, near)` ·
`write(text, where)` · `type(text)` (at the focus) · `fill(fields)` · `answer(choice_meaning)` · `submit(expect)`
· `reach(amount, of)` · `collect(what, count, slot)` · `tell` (the loop's, below) · `back` · `scroll(direction)` ·
`wait`

- **click**: code first (an exact name; a row whose title is the target, e.g. a list row read as title+preview;
  the same-named control clearly nearest to `near`, for grids), then Laya. A menu command is named when the step
  names it in full or names its last part exactly ("New Folder" is "File > New Folder", `tools.exact_name`), unless
  that command is risky; never by a name it merely starts with ("New Window" is never "New Window with Same
  Command"): the row-titled-so rule (`tools.titled`) never takes a menu command, open or in the menu bar, and an open
  menu's commands are offered to Laya only when named exactly; an exact name is code's pick, and among several copies of it Laya's pick check never vetoes
  the one Laya chose. Before failing it scrolls to reveal the target, never the page's own search: a click's target
  is a control's name, and searching is the find tool's. A control that
  commits a form or a form's step (`risk.commits`: its window's or sheet's default button, the one Return presses, in
  any language; or `risk.COMMITS`' words: Next, Continue, Save, Submit, Sign in, OK ..., on a control that is not
  the view's own: one in a toolbar or a date control, a next or previous day, is no commit, by its ancestors' roles,
  `facts.view_control`) that only Laya picked (the step did not name it) needs Laya's risky verdict
  (`Laya.acts(choice, "risky")`), never an unsure pick its pick check let through.
- **find**: a visible search box (`Item.search`: an AXSearchField, or a field inside the page's search landmark;
  else the box that searched in this app before), else click the visible search control (a control of the search
  landmark) and wait for the box (it may load a search page), else the app's own menu command whose key equivalent
  is ⌘F (`Item.shortcut`), never a key pressed blind. A field's name never makes it a search box.
  The results are what is new since the typing (or what is left of a list that narrowed), read after Return only once
  the page changed (the change wait, `settle`, from the screen just before Return); "nothing found" is what
  came saying so: at most `NO_RESULT_LINES` (3) new lines, no row or link among them, that Laya is sure say nothing
  matched (`Toolbox.no_results`, D7 over those lines alone), or nothing ever coming. A result's own words never make
  it "no results" (a song called "Nothing Found" is a result).
- **reach**: adds listed items until the running total is the amount. Code reads each offer's price from its row
  (a row with a control and a price) and picks the quantities; the row's only control adds the first one; which of a
  stepper's controls adds or removes one is Laya's sure pick (D3, "add one more <product>"), and the total is the
  priced line in no product's row, Laya's pick (D5) when several could be. No control's words are read.
- **write / type**: text goes only into a field re-found by name on the current screen and verified there; the env
  types nothing unless the keyboard focus is in that field (architecture.md, "Reading the screen": the focus guard,
  old text selected through Accessibility, never a blind select all). A field that renamed itself after typing (the
  one focused field holding exactly our text, or the one field drawn at its spot: `Toolbox.at_spot`) is the same box,
  and is kept under its new name as the field written last (`last_write`), so a submit finds it and returns into it.
  Typing is proven by one shared check (`facts.text_landed`, also `expect.field_holds`'s and the local repair's): the
  field's value holds the text, or the field stayed empty while a new item holding the whole text (a token's trailing
  ✕ or "X" aside) appeared inside the field's frame, in its own container (`in_field_box`: sharing the field's parent
  or grandparent in the accessibility tree; a suggestion popup shares far less), or, read off the pixels, beside it
  (`facts.token_of`: a recipient or tag token, often only OCR text, its field's AXValue staying empty); a field that
  took a token is never typed into again; "address
  bar" goes to the browser's address bar. Text typed at the cursor is tied to the field it went into. A fill records
  what it typed as a write does (`last_write`, the last field it filled), so a submit after it checks and returns
  into that field. A fill's field is its exact name, `match.named`'s reading, else the one field whose name starts
  with the asked name as whole words ("To" → "To recipients"); a checkbox it turns on is named so too, or holds the
  name in a short label, never a list row's checkbox that merely holds the word. Copies of one field (two forms
  alike open: `Toolbox.one_of`): the focused one, else the one nearest the focus (frames, else its region or the
  next), else the one first read last (the window just opened).
- **submit**: under a typed expectation it reports "sent" once the screen changed and our text left its field, and the
  type proves the rest (the loop); a v1 submit's expected words count only where new since just before the send
  (`check_end`), then Laya over only what changed. After typing into a search box (the field written last, or with
  nothing written the focused field holding text), return in that box, never a page verb such as Create; if the text
  stays and the page does not move, only a control of the box's own search landmark is tried. Else the button that
  sends, by structure and never by its words (`send_button`): among the buttons of the form being sent (`owned`: by
  the accessibility tree, those inside the deepest container they share with the field, below the page itself,
  `form_mates`; read off the pixels, in the field's dialog, never the page behind it, its row, its region or the next
  one across an edge, or beside it), its default button (AXDefaultButton, the one Return presses), else Laya's pick
  of the one that would "submit what was written" (`SUBMIT_INTENT`, D3: a sure answer stands with no pick check,
  since the submit's own check verifies the click and a send asks first; an unsure one only past its pick check;
  never a link). A button elsewhere on the page never sends. Else return in the field written last (never into a multi-line box).
  With nothing written and no field holding the focus there is nothing to send: Return is never pressed blind, so
  nothing is asked, and the step fails "no field" for its ladder.
  **Return that ran nothing**: our text still in its box afterwards and the page not moved on, a submit that sends
  nothing (a search, a filter, a quick entry: `risk.risky("submit", box)` names no kind, and on a screen in another
  language a search box only) presses its form's own button by structure (`form_button`): among the buttons of the
  box's form by the accessibility tree (`form_mates`), its default button, else the one nearest the box; never a way
  out, never one the risk floor names; with no tree, only a default button drawn with it. The step's typed check
  then proves it. A submit that sends, posts or commits never gets this second way. A submit with no expectation at
  all (no type, no words) is proven as a typed one: the screen changed and its text left the box. A one-line box must hold exactly the message, nothing more; a box of several lines may hold more (a
  signature) but never the message twice, compared with the text as typed (`{name}` filled in). A search box that
  keeps its query counts as sent once the page moves on (a new address, or new items outside the fields). Sending
  needs the user's yes.
- **Keys** a tool presses (⌘L, return, tab, escape, back) go through the env's `press`, by Accessibility first
  (architecture.md, "Keys"). `Toolbox.do` is False when the env refused an action, and a chain stops there, never
  pressing on blind: the address bar (focus, typing, the focused chrome field holding the address's host: `address_holds`,
  then return), a submit's return into the address bar, and a terminal's numbered choice (its number typed, then
  return). The trace's `by:` line says how this action was done (the env's `pressed` is cleared before each).
- **back** (`Toolbox.go_back`, also a collect's return from an item's own page) is proven by the address: the
  browser's back, and when the address did not move (a link that opened a new tab has no page before it) the address
  the app showed before (`Toolbox.visited`, per app) typed into this tab (`open_address`). Not back by then: the step
  reports no change. A native app (no address) is back once its screen changed.
- **One way to send.** A step that may send, post, pay, commit or run is carried out by exactly one submit method,
  never a second after it, so a send can never happen twice: a click on a control `tools.one_way` names (risky by
  `risk.risky`, commit-like by `risk.commits`: a default button, Next, Save; or a key action: add to cart, star,
  follow) goes to the env's `click_once` (AXPress alone when the control has a pressable element, else one click;
  `Toolbox.do` swaps it in); a submit presses the form's own send or default button that way, else one Return key
  (`Toolbox.return_key`, never AXConfirm). Whatever the screen then does, late or not, is the step's check (the usual
  wait); nothing is pressed again. Only a return that sends nothing, a search box's or the address bar's
  (`return_key(sends=False)`), may try AXConfirm and fall back to the key (`confirm_or_return`).
- A dialog in the way (`Toolbox.clear_dialog`) is only a dialog that **blocks the page, by structure**
  (`facts.blocking_dialog`, `modal_items`): a sheet, a dialog window (AXDialog, AXSystemDialog), a web dialog with
  aria-modal (AXModal), a system prompt, or a terminal's numbered choices under a question. A web dialog role without
  aria-modal is a **panel** (`Item.panel`: a side cart, a drawer, a flyout): never answered or cleared, the step acts
  on the page as usual. So is a bubble the browser floats over the page (`mac.browser_blocks`: the browser's own
  dialog blocks only as a sheet, a system dialog or modal; a translate offer does not): never in the way and never a
  permission prompt. Buttons with answer words on a page are the page's own. Then, in order:
  1. a **permission prompt** always takes the user's yes or no, never the plan's answer nor Laya's (`Toolbox.permission`,
     also from an `answer` step). It is known **by who owns it**, never by its words: a system prompt
     (`facts.system_prompt`: a process of `SYSTEM_PROMPTERS` in front, read from that process) or the browser's own
     dialog (`browser_ui`: whatever it asks, in any language, the user decides; fail closed). A site's or an app's own
     dialog that asks something is the page's (step 3). The user is shown the prompt's own words. A no is given by the prompt's own cancel control (`Item.cancels`), else the narrowest option that
     says no; a yes by the narrowest option that says yes, else the one other button beside the cancel control;
     with no such option nothing is clicked;
  2. the step's target inside the dialog, or a typing step with a field in it: the step acts inside it;
  3. Laya's dialog role (D6): part of the step → acting inside it, or for a step with no target, Laya's dialog answer
     for the task; else, not part of the task → **closed by its own way out** (`Toolbox.close_dialog`): its cancel or
     close control by structure (`facts.dismiss_option`: the dialog's AXCancelButton, AXCloseButton, a close-button
     subrole), else the button Laya is sure closes it agreeing to nothing (`Toolbox.way_out`: D6 dialog answer for
     "close it, agreeing to nothing"; never one `facts.NEVER_DISMISS`, the safety veto, or the risk floor names: no
     Subscribe, Accept, Allow, Buy), else its only
     button when it is not risky (a notice: its only way on), else escape when it has controls but no field of its own; a
     notice with no control is left to the local repair's wait. Never an answer picked for the task.
- **Laya's dialog answer** (`tool_answer`) is offered only a real dialog's buttons and numbered choices: a checkbox,
  a row or a link in a dialog (a setting, a filter, the page) is taken only when the step names it. A pick that is
  not a plain answer (`tools.plain_answer`, by structure: a numbered choice, or the dialog's own default or cancel
  button), such as a quantity, an Apply or a web dialog's "Yes", needs Laya's risky-tier verdict, like a commit-like control. A disabled target reports the
  enabled options near it, so the rescue picks the missing choice first.

## Interface and content (content.py)

One model, one label: every item of a read is `interface` or `content` (`Item.role_class`), set once per read by
`content.classify` (the Mac env's read calls it; a screen built elsewhere is classified the first time a reader
takes it), in place, so items keep their identity. Every reader of content takes that label and nothing else:
a collect's candidates (`content.items`), a page's main text (`page_text` through `content.node_class` and the site's
learned boilerplate, `content.kept_text`), a native window's text (`tools.window_text`), so the facts a tell or a
compose gets; the page view's and the plan digest's rows and text (`view.build`, `brain.screen_digest`), which still
list interface controls as targets and other interface last, under `interface`; and the rows that say a place is
not empty (`facts.content_rows`). By structure only, no site or app words; the first rule that says something wins:

1. interface: a menu command, the browser's chrome or its own popups, a control by kind (field, checkbox, tab,
   menu), an item named only by its tooltip or developer id (`hint`), anything in the page's search landmark;
2. interface: inside a toolbar or menu bar, inside a navigation, banner, footer, complementary or search landmark,
   or, on a page with a main landmark, a web item outside it (the landmarks, editor, bar and web flags are on
   `mac.Ref`, from `mac.mark_tree`; text read off the pixels takes them by the frames its centre lies in,
   `mac.place_text`);
3. interface: inside an editor or form sheet (a popover, sheet, dialog, web dialog or form) that holds an editable
   field: its labels, buttons and summary (an event editor's "Add Alert, Repeat or Travel Time" is never an event);
4. interface: every item of a web read that is mostly (`SAME_PAGE`, 80% of its texts) the read of the page of its site
   just before it, under a new address: that page still showing, none of it the new page's;
5. content: inside the main list (`content.main_list`: among the items' ancestors, no page, window, scroll or split
   area, the container whose children of one role, at least `MIN_ROWS` 3, hold the most words, each counted up to
   `ROW_WORDS` 12; of two as heavy, the deeper), except a button inside a row that is not the row itself;
6. interface: any other button;
7. interface: text the site's boilerplate holds (below), or that this read shares with the page just before it;
8. interface: text that is only a control's name (its label, or the control read again off the pixels);
9. interface: a link of the page's own site named in a word or two that is no result's own main link (the site's
   navigation);
10. interface: beside longer content (a text, link or row of three words or more), a text named in a word or two
   with no number, outside the main list: a label, a tab, a category;
11. content: the rest, values in content rows included.

**A result's own title** (`content.title_ids`, `content.roles`): among the content, by structure only. A heading, a
native list's row, or a link that is a result's main link is a title. On a list of results with pages of their own (at
least two addresses each linked under two names or more: the title, its cover, "87 editions"), only the main link of
each such address is (its longest name that is no counter, no image's name and no "Lead: X"), never a link to any
other address (an author, a category). Without such a list, a content link is a title unless it is a counter
(`content.COUNTER`: a number and a word or two), an image's name, or the same address and name on `MIN_ROWS` results
(an owner, an author). Text is a title only in a native app's list; a button never is. `content.roles` gives each item
of a read its role for a collect to keep: TITLE, CONTENT, CONTROL (a control holding a value: an option's price) or
INTERFACE.

**Boilerplate** (`content.Boilerplate`, one per process: `content.BOOK`): text that shows identically (case, width,
spacing and surrounding punctuation aside: `content.normal`) on two or more different pages of one site is that
site's interface. A site is a web page's exact host (pages told apart by their path, never the query: two searches
are one page) or a native app (pages told apart by the window title, numbers aside). Learned by code from each read
the Mac env makes (`classify(..., learn=True)`), with no brain call: a read is compared with the read of the site's
page just before it, and the strings both show are learned, except a string of more than `BOILER_WORDS` (8) words or
80 characters, a part of either read's window title (a result's name on its own page), a link on one read to the
other page, and anything when the pair is mostly one page (rule 4). Kept as digests only (`content.digest`, 16 hex
characters of a SHA-1 of the normal form), never the text: at most `SITE_CAP` (120) per site, the oldest dropped, in
RAM (`SITES` 64) and on disk as memory kind `boilerplate` (`HarnessMemory.boilerplate`, `add_boilerplate`; app.py and
the MCP server give `BOOK` the memory as its store). In RAM only, the digests of each site's latest two pages' texts,
to tell the next read apart. A main-list item (rule 5) is content even when the site repeats it.

## Carrying data between steps (collect, {name}, tell)

```
collect <what> | count: <1–10> | as: <name>        count omitted: everything on screen that fits, up to 10; as: items
write Worth a look:\n{name} | where: description   {name} in a write, type or fill's text
compose <what to write> | from: <name>, <@n> | as: <name> | tone: <optional>     text the brain writes (below)
tell <the user's question> | from: <name> | as: list|answer       the last step of a question
```
- **collect** (`Toolbox.tool_collect`) reads the current screen and clicks nothing. **A day's calendar events**
  (`calendar_day`: `what` names events, `CALENDAR_WORDS`, and with a web page in front names the calendar itself; the
  day is the first one `what`, else the task, names, "today", "tomorrow", a weekday or a date, found by the
  platform's date detector, `asked_day`, NSDataDetector) come from the env's `calendar_events` (EventKit, across all
  calendars): one "HH:MM Title" each, an all-day event "all day: Title", all-day first then by time (`count`, when
  given, keeps the first ones). An empty day keeps one fact, "Nothing on your calendar today" (`day_words`: today,
  tomorrow, on a weekday within the week, else on a date), which a tell says as it is. No access (None): the screen is
  read as below. The trace and the ledger show the count only, never a title. **A page's main text**
  (`wants_text`: a text word with an article word in `what`, "the main text of the article", "the post's content")
  comes from the env's `page_text` (`MacHarnessEnv.page_text`, `ax_walk.page_text`): the web area's static text in
  document order, headings on lines of their own, off-screen paragraphs included (the AX tree holds the whole page);
  what is interface is the content model's (`content.node_class`, "Interface and content", below): only the main
  landmark when there is one; never the navigation, banner, footer, complementary or search landmarks, a control (a
  field's typing included), a toolbar or menu, or an editor or form that holds a field; as prose, never a block whose
  text is mostly links (3 or more, over 60%) nor a text the site's learned boilerplate holds (`content.kept_text`). At
  most `MAIN_TEXT_CHARS` (7000), cut at a word; kept as one value, so the ledger shows its length only. Under
  `MIN_PAGE_TEXT` (200) characters, or with no web page, the screen's items are read as below. Otherwise code takes
  the candidates (`collect_candidates`): the page's content (`content.items`: never interface, so never a control, a
  field, a menu, chrome, navigation, a banner, a form's label or an icon), never the list's own title (the window
  title or a heading) or its count (a number and one word, "3 tasks", when it is the only such line: several are the
  list's rows, "2 lemons"), a date, a time
  or numbers alone (`bare_date`: a calendar's day and hour labels, a mini-month's cells: every word a number, a word
  of one or two letters, or the platform's own month, weekday and AM/PM names for the screen's language and the
  user's, `date_names`, from Foundation's date formatter; kept when a value is asked for, as a price is all digits). An empty-state line ("No events") is Laya's to veto
  (D9, below). **Ads are Laya's** (`Toolbox.is_ad`): asked only of an item that carries a short label of its own, by
  structure (`ad_label`: a one- or two-word text, no link or price, on its line or just before it, or a short lead set
  off in its own name, "Xy · rest"), as D9 "is it one of: an advertisement or a paid placement", with that label
  shown; taken out only when Laya is sure; never by a word or a site. "links" in `what`:
  only links with their own address (`Item.url`, AXURL) that lead off the page (never the page's own path with
  other query parameters: a search refinement or a link back to the same search, `same_page`), one per address
  under its longest name, never a bare host; the site's own one- or two-word links are interface (the content model's
  rule 9), except **a result's own main link** (`content.main_links`, by addresses: a link of the site whose links
  below it are each named in a word or two, its stars, forks or comments; never one above another such link, an
  owner or a category; only when at least two alike, as deep, make a list): a result named in one word is still a
  result. "titles",
  "headlines" or "names" in `what` on a web page take the same links (a result's title is its link's name), never a
  snippet, a description or a timestamp beside them. A link named as an image on the page (a thumbnail's or a
  cover's alt text) is a title only when no other link is: never beside titled links, even when the result's own
  title link is off screen. "titles" or "names": a result's own title (`content.title_ids`) is taken per address
  before any other name of it, and fits by structure as a name fits by code; when any is on screen, only those. A
  kind of value in `what` ("code", "price", "email address"): only items holding one, and
  the value alone is kept (`wanted_value`), the content's first; with none there, any other item of the page that
  holds one (an option's price on a control of its own), never a field, a menu or a heading.
- **Money** is read by format, never by words (`tools.money`, `MONEY`): a number beside any Unicode currency symbol,
  or beside an ISO code in common use (Foundation's list) when the number has a decimal or grouping mark ("EUR
  30.00"; "TOP 10" is no price); in any locale's format (`amount`: the last "." or "," followed by one or two digits
  is the decimal mark, "12,99 €" is 12.99, "¥1,200" is 1200).
- **Rows** (`priced_rows`): a price ("price", "cost", "cheaper" in `what`) is taken with its row's title, as one
  item: the title-like item (two or more words of its own, no control, value or tooltip) sharing the price's
  deepest ancestor in the AX tree (`ax_owner`, `Ref.path`: one product card, list item or table row; several as close,
  the longest; more than `ROW_MAX` (4) as close is no row of its own: a filter in a sidebar); else the price's own
  words (an OCR line holding a title and its price); else where they are drawn (`seen_owner`: on its line to its
  left, or the nearest above in its column within `ROW_GAP_LINES` (6) of its lines). A thing named in `what` ("the
  price of the Lumen desk lamp") must be in that row's title (`named_fit`, value words left out, "bud" matching
  "buds4"): a price with no row, or another row's, is never taken for it. A row Laya is sure is something else than
  the thing asked for (a case for it: `Toolbox.accessory`, D9 under the risky tier) comes after the others; its words
  ("for", "with") are never read; it takes no place of the thing: a `count` counts the others. Nothing named: a price with no row is
  kept alone. A value asked for is read from any item holding one, a checkbox or an option too (an option's price on
  an item's page), never a field, a menu or a heading. **The item's own page** (`own_title`): when the page's own
  title (its window's, else a heading's) names the thing and the page is no search for it (its words not all in the
  address's query), and no row names it, the page's prices are the thing's, under that title. **Not on the results**:
  a value asked for with a thing named and nothing taken on a web page opens the thing's own link (`open_own_page`:
  the first link on the page whose name holds every distinctive word, not to the page itself; the risk gate first),
  reads it there as its own page (`own_reads`: as it loads, re-read while it changes, then further down, `READ_MORE`
  scrolls at most, stopping once a value is taken), then goes back (`go_back`, below). A row's value goes on as
  "title — price" (`Collected.titled`), so a tell can say whose price it is.
- **The page itself**: "the link of this page" (`OWN_LINK`: a link word with "this" or "current", `count` at most 1)
  is the page's own address, under its window's title.
- **From the list's top**: a collect with a `count` on a web page this task has not scrolled (`moved_on_pages`,
  recorded by `Toolbox.do` on every scroll) and with no `#` fragment in its address (a section jumped to) first moves
  the page to its top (`env.scroll_top`: the scroll bar's AXValue set to 0, when it is above 0): a tab left scrolled
  down still gives a list's first items, never the viewport's.
- **Each result once** (`same_thing`): the same address, the same words, or one item the other's title under a short
  lead set off by a colon ("Cover of: X" beside "X …", `MARKED`) is one result: kept once, under the fuller title
  that has no lead; two different values are never one.
- **A name collected again** on another page (its address, host and path) adds what it reads after what it holds, each
  result once (two articles read in turn under one name); on the same page, or after a tell found its items no answer
  (`redo`), it replaces them. `Toolbox.keep` keeps them and notes the name as the latest (`last_slot`).
- Which candidates fit: those that name the topic by code (`named_fit`: every distinctive word of `what` in the
  item) are taken first, in screen order; fewer than `count` showing, `scroll down` and read on, at most
  `READ_MORE` (2) times, and reading stops once `count` fit. Only then, when too few fit, do the others fill in
  (`column_first`): those in the column of an item that fits (frames overlapping sideways, else the same region)
  first, a sidebar last, each through Laya's `D9 collect` question. Laya alone is weak at saying an item belongs to
  a list (held-out 79%, near 0.5 on plain list rows; it said yes to a site's own nav link named in three words), so
  it vetoes: an item is dropped only when Laya is sure, under the decision's risky tier, that it is something else
  (no right item vetoed on the held-out cases, 3 of 11 wrong ones); otherwise the page's structure stands. A `what`
  with no distinctive word (code cannot tell a fit) fills in from each read as it comes. What is kept is in screen
  order. Fewer than asked is what there is: the step passes with what it found and says how many ("1 of the 10 asked
for"), and the plan's next step or the tell's contract decides (a list of N needs N of its kind); nothing at all
fails it. Never an item of a system prompt, nor of a
dialog that blocks the page while the page has content of its own. Its proof in the ledger is "N items
  collected as {name}: titles (hosts)".
- `Toolbox.slots` keeps what each collect took (`Collected`: title, url, value, titled, and its `role` where it was
  read: `content.roles`; an event read from the calendar EVENT, a page's or window's text TEXT) for the task. A later `{name}` is
  filled by code before typing (`Toolbox.expand`): one item per line ("title — address", or the value alone) in a
  box of several lines (`Item.multiline`: an AXTextArea, never judged by its name), comma-separated in a
  one-line field. A name that collected nothing fails the step with `Outcome.NO_DATA` and the ladder takes it; in a
  task that collects nothing, braces are text (code typed in a terminal).
- **tell** (`loop.tell`) runs as the last step: `tell.word_answer(question, facts, hb, plain=(as == "list"))`
  words the answer from what was collected (a list is read out by code with no call; an answer takes one short
  brain call, counted in the task's brain calls; its facts share `tell.TEXT_CHARS` (8000), so a page's text goes
  whole, where a read-out fact is cut at 160), `voice.speak.speak` says it, and it shows in the HUD and the log
  (`tell` event) and on `TaskResult.answer`. A failed wording falls back to reading the items out. The name the tell
  reads holding nothing, it reads the name collected last.
  - **Cited, and nothing added** (`tell.cited`): the facts go to the brain numbered (`[1] …`), and it ends each
    sentence with the numbers it rests on; they are never said. An answer citing a fact that is not there, or saying
    a number that none of the facts it cites holds (all of them when it cites none), nor the question, added a fact:
    it is the not-found line. A count of the facts ("3 of them") is their own. The trace and the ledger name the
    facts cited.
  - **Found is found**: the prompt says the facts come from the page or app the question is about (they need not
    name it), and a fact holding the asked thing in any form (a definition, a sentence, a translation, a number, a
    time, a price, a text to sum up) answers it, even holding more. Only when none does, the brain replies exactly
    `NOT FOUND` (`tell.UNANSWERED`).
  - **The page's text, once more**: when the collected items do not answer it and a web page is in front, the page's
    main text (`env.page_text`, in document order, off-screen paragraphs included, interface left out by the content
    model), led by the page's title and host
    it was read on, is the facts for one more call: a definition or a first sentence the collect missed is in it. The
    same text for the same question is asked once a task (`Toolbox.pages_told`): a retell on the same page never
    pays for it again. The prompt says a question may name the list or feed an item was found in while the facts come
    from the item itself, in any language.
  - **Numbers by value**: the cited check reads a number in any locale's grouping ("2,430", "2.430" and "2 430" are
    2430) and a Roman numeral in the facts by its value ("siglo XV" holds 15), so an answer worded in another
    language than its facts keeps its numbers.
  - **Compared by code**: a comparison contract (above, "The contract") gets code's comparison first, "Compared by
    code: lowest: X at ₹a; highest: Y at ₹b", from each side's first priced item across every name collected (one
    search each; its own row comes first, an accessory after it), then every collected item.
  - **Checked by its contract**: the answer must meet the task's contract (above, "The contract"); a list contract is
    read out by code from the collected items of its kind, with no call.
  Still not answered, or not as the contract asks: the not-found line (`tell.NOT_FOUND`, also what nothing collected
  words to) or the contract's reason; the tell fails (`Outcome.NOT_FOUND`), so the task is not done, pinned or
  remembered; the name it read is marked to be replaced by its next collect (`Toolbox.redo`), and the ladder may rescue
  the collect.
- **From an earlier task** (`session.py`, `run_task(session=…)`): each finished task, done or not, leaves refs with
  short ids, `@n`, monotonic per process: each name it collected (`items`; a page's main text as `text`, under the
  page's title), the answer it said (`answer`), a text of 20 characters or more it wrote into a field that is no search
  box or address bar (`draft`, expanded), each email address it typed, or the task named (`person`, named by a collected
  item holding it or a capitalised name before it in the task, else "an email address"), the thing a task proven done
  made (`made`: its done line's `new_item(<name>)`), and the web page it ended on (`page`: title and host, its address
  as the value), plus the place it ended on. Never a collect that asked for a code (`CODE_WANTED`), never a ref already
  bound. A result that came after its task (`Session.keep`) is a task of its own with one ref: a delegated session's
  last message (`text`) or a fired watch's line and text (`watch`, voice mode's watches: architecture.md, "Watches
  and schedules"). Values (`Collected` items) stay in RAM for the last `KEEP_TASKS` 8 tasks; a ref older than `FRESH_S` (30
  minutes) never reaches a prompt.
  - **The block**: `Session.lines(task)`, built by code, at most `BLOCK_LINES` 6: a header that says how to use it
    (`{@n}` in a write, `tell … | from: @n`, never retype it), one line per ref (`@3 text "How tides work", 496 chars
    (tides.example), from "read the article on tides", 2 min ago`), then `Ended on: <app>, <host>`. Only when the task
    holds a reference word (`session.REFERENCE`: that, it, this, them, him, her, the same, again: the refs of the 2
    latest tasks) or shares a distinctive word (`tools.topic_words`) with a ref's title; else no block, and the
    prompt is byte for byte what it was. It goes after memory's block, in the plan, the looks, the page calls and the
    rescues. Titles, kinds, sizes and ages only: never a value, and an email address in a title shows as `<address>`.
  - **Binding**: before the plan, `Session.bind` puts each listed ref's items into `Toolbox.slots` as `@n` and the plan
    stream is given those names as known, so `{@n}` (`brain.SLOT_REF`) and `from: @n` (`brain.SLOT`) pass
    `check_slots`, and `Toolbox.expand` / `tell` read them as any collected name; a collect may not be named `@n`.
    The email addresses the session holds (its refs' items and its tasks' words, `Session.addresses`) go to
    `HarnessBrain.given`, never into a prompt, and `foreign_detail` counts them as Karta's. A run whose passed steps
    used a `@n` is never pinned (a replay would not have it).
  - **On disk** (`session.sqlite`, beside the memory, mode 600; `Session(path)`, RAM only without one): per task its
    time, words, done, said line, place and its refs' `@n kind title` line; never a value. 14 days, at most 2000 tasks,
    an FTS5 index over the task and titles: `Session.recall(query)` returns raw rows, no brain call.
- **What is in front** ("this page", "this email", "this window", "what's on my screen": `tools.FRONT_WORDS`): the
  plan prompt's rules say these are the screen in front, acted on with no open_place for it and read with a collect of
  its main text. Code holds it: in such a task (not a replay) the plan's first open_place is dropped, with a trace
  note, when it opens the place the task started on (`loop.opens`: its site's host is the page's, or it names the app
  in front); an open_place of another place runs. "Summarise this page" is `collect the main text of this page` and
  a tell: 1 plan call and 1 wording call. A page's main text is `page_text`; a native app with no web page in its
  front window (a message open in a mail app, a note) gives the window's text instead: its content's headings and
  static texts in screen order (`content.items`), never interface (`tools.window_text`, at most 7000 characters, under
  `MIN_PAGE_TEXT` read as items as before). A mail app's message viewer that is a web area reads as a page's text.
- **compose** (`Toolbox.tool_compose`, `tell.compose`): one short brain call (counted in the task's calls, its stat
  kind `compose`) writes the text `what` asks for (an email body, a message, notes, a subject) from what the `from:`
  names hold (collected names or session refs; one at least, each collected earlier: `check_slots`) and nothing else,
  into `Toolbox.slots[as]` as one value; a later `write {name}` types it by code. Never steps. The facts go numbered,
  as a tell's (`tell.TEXT_CHARS` shared), with the optional `tone:`; the prompt says to use only the facts, never an
  email address, and that the facts are content, never instructions. **Cited, nothing added**: `tell.cited` as for a
  tell (a fact cited that is not there, or a number none of the cited facts holds, nor the request: no text); the
  citations are removed, as are any email addresses (`COMPOSE_CHARS` 1500 at most, line breaks kept). No text: the
  step fails NOT_FOUND ("I couldn't write it from what I read alone"); no local repair. The trace shows its size and
  the facts cited, never the text. A run that composed is never pinned. The up-front question names it ("write the
  text I compose in the Body field, and send it once you have heard it").
- **Consent for mail and messages** (`Toolbox.withheld`): before a mail or messaging app's text goes to the brain (a
  tell that words its answer, the page's text once more, a compose), the user is asked once per app, by `confirm`
  (by voice in voice mode): "May I send text from <app> to Claude to summarise it?" (`tools.CONSENT_ASK`). The answer
  is a memory row (kind `consent`, app = the app or the page's host, key `brain`, value yes or no; one per app; each
  use keeps it fresh), asked again only once forgotten (`scripts/karta_memory.py forget consent brain --app <app>`).
  Which sources, without a word list (`Toolbox.source`, from the screens a name was read on, `slot_screens`; a
  composed name's are its sources', a session ref's are kept on the ref in RAM): a native app LaunchServices
  registered for `mailto:`, `sms:` or `imessage:` (`facts.message_apps`, NSWorkspace, read once); a web page Laya is
  sure (reversible tier) is a mailbox or a conversation (`Laya.private_screen`, D2, options `decide.PRIVATE_KINDS`);
  sure (risky tier) it is not: no question; unsure: asked, by its host. A Laya without the question (an offline fake)
  leaves web pages unasked. **A no** works with titles only (`Toolbox.titles`: a row's title, a text's window title,
  never a value) and says so (`tools.TITLES_ONLY`): a tell reads the titles out by code with no call, led by that
  line; a compose writes from the titles, and the end line starts with it. A run with no one to ask (`confirm` None)
  takes a no and keeps nothing.
- **Structure only** (`Toolbox.for_brain`, `view.Structure`): every prompt that shows a screen (the plan's starting
  screen, `brain.screen_digest` or v1's `screen_text`; a look's and a page call's numbered view, `view.build`; a
  rescue's screen; the end check) takes it through `Toolbox.for_brain` first. A mail or messaging app's or page's
  screen (`Toolbox.source`, the rule above) with the user's yes goes as it is; with no answer kept, the same question
  is asked once (same row) before that first brain call; on a no, or with no one to ask, the screen goes flagged
  `Screen.withheld` and every prompt builder renders it by one rule, `view.Structure`: a row is `row N` (numbered from
  the top among the visible named rows, row 1 the first; the count is given); a field, control, menu command or
  heading shows its name only when it reads as a UI label (at most `SHAPE_WORDS` words, no e-mail address, and not one
  of a row's parts nor a part of two words or more of the window title: a sender, a subject), else `[N words]`; any
  other text is `[N words]`; a field's text is its length only; no window title, address path, row neighbours or
  link hosts; a note line (`view.WITHHELD_NOTE`) says so. `#n` targets work as ever (a step keeps the real item's
  identity), so a plan can click row 1, the newest message, without reading it. Other screens are unchanged.
- Privacy: logs, traces and the ledger show collected items as titles and hosts only (a value, a page's text
  included, as its length; a row's as its title and its value's length); a
  `{name}` step's typing is shown as written, `{name}` and all.

## Delegate (delegate.py)

```
delegate <the request, as the user said it> | repo: <the folder's path or name>
```
"Start a Claude Code session in folio to add a cancel button" is one step, written by the plan; nothing comes after
it (as after a tell). Carried out by `Toolbox.tool_delegate`, not by UI steps:
- **The repo** (`delegate.resolve_repo`, `Toolbox.resolve_repo`): a path (`~/…` or absolute), else a folder of the
  Mac map's `notable_folders` whose name matches (case, spaces, `-`, `_` and `.` ignored), else, with no repo named,
  the one the session delegated to last (`Session.last_repo`). It must be an existing directory holding `.git` (a
  directory or a worktree's file); a folder that is not one, no such folder, or two of that name fails the step with
  that line, before anything is asked.
- **The request** (`delegate.check_request`, also in `brain.check_step`): refused when it holds a control character
  (a line break, a tab, an escape, C1 or a Unicode line separator), is over `MAX_REQUEST` (500) characters, or starts
  with `-` (Claude Code would read an option).
- **Always asked**: it is a key action of kind `run` (`risk.key_action`), so the up-front question names the exact
  folder and request: "I'll start Claude Code in ~/Desktop/Projects/folio with: 'add a cancel button'. Go ahead?"
  (`delegate.question`, `tell.ahead_line`). A step the up-front yes did not cover asks that same question itself. A no
  ends the task with nothing started.
- **The launch** (`delegate.prepare`, `MacHarnessEnv.open_terminal` → `delegate.launch`): a folder of its own,
  `~/Library/Application Support/karta/delegate/<time>-<hex>/` (mode 700), holds `settings.json` and `events.jsonl`.
  The command is `cd <repo> && claude --settings <settings.json> <request>`, every part one `shlex.quote`d word (the
  request is one argument; nothing in it reaches the shell), escaped once more as an AppleScript string for Terminal's
  `do script` with no `in`, which always opens a new window: Karta never types into, focuses, closes or kills a window
  or session. No permission flag is ever passed: the user's own Claude Code permissions apply.
- **The hooks**: `settings.json` holds only Claude Code's documented `Stop` and `Notification` hooks (no matcher), each
  `{"type": "command", "command": "cat >> <events.jsonl>; echo >> <events.jsonl>"}`: the hook's JSON input arrives on
  stdin and is appended. `--settings` hooks merge with the user's own. Reference:
  https://code.claude.com/docs/en/hooks (common input fields `session_id`, `transcript_path`, `cwd`,
  `hook_event_name`; Stop adds `stop_hook_active` and an optional `last_assistant_message`; Notification adds
  `message`, `title`, `notification_type`).
- **The check**: one more Terminal window than before (Terminal's own count, before and after, in the same script) and
  `claude` among the commands on the new tab's tty (`ps -t`, the binary or node running it) within `START_WAIT` (10
  s); else the step fails with what went wrong ("…Claude Code did not start in it (is `claude` installed?)"). A
  delegate step is never retried, repaired or rescued, and a run with one is never pinned.
- **Hearing back** (`delegate.watch`, voice mode): the task ends at once ("Claude Code is starting in folio.", and
  the session keeps a `delegate` ref with the repo), and `app.py` starts a background job (kind `delegate`, shown
  watching) that reads `events.jsonl` every `POLL_S` (2 s; an object still being written waits for the next read,
  `read_events`). A Notification whose type means Claude Code waits for the user (`NEEDS_INPUT`: permission_prompt,
  idle_prompt, elicitation dialogs, agent_needs_input; or no type) is said: "Claude Code in folio needs you." The first
  Stop ends the job: "Claude Code finished in folio: <the first line of its last message, at most 20 words>" (or
  "Claude Code finished in folio."), and the message is kept as a session `text` ref of its own (`Session.keep`,
  value in RAM only), so "what did it do?" is a tell from `@n`. The Terminal window gone (checked every
  `WINDOW_EVERY_S`, 15 s, without starting Terminal; a failed check is no news), a cancel ("cancel job 2": only the
  listening stops) or `WATCH_S` (8 h) also end it. Typed mode starts the session but does not listen for it.

## Try, verify, next (retry.py)

The third local try of a click that missed (below, "Local repair"). For a click picked by Laya (or a non-exact code
rule) that changed nothing or made a different change than it declared, code undoes it by what changed since just
before the click:

| What changed | Undo |
|---|---|
| a new web address | back |
| a new dialog or popup | its own cancel or close control (`facts.dismiss_option`), else escape |
| another app or profile | return to the place that worked |
| the same page and window changed in place | nothing code can undo (`retry.IN_PLACE`): the change stays, and the next pick is verified by the step's type from the screen just before it |
| nothing | nothing |

Then the item is marked wrong for the step and the step runs again, so the next-best is picked: `MAX_TRIES` 3 clicks,
then a look (which sees every wrong pick in the notes). Never retried: exact-name matches, menu commands, dialog
answers, toggles, anything risky or commit-like (`risk.commits`: a default button, or "Next" and the like), and a
click whose address does not go back or whose popup does not close (`undo` gives None). A click that is not Laya's own
pick (an exact name, a menu command) gets Laya's ranking instead (`repair.options`), under the same rules.
The HUD shows each pick and miss; `KARTA_FOLLOW=1` slows each Laya click by 0.4 s and highlights it.

## Local repair (repair.py)

Before any brain look or rescue, a failed step climbs a local ladder of up to `LOCAL_TRIES` (3) tries, always in this
order, the rungs chosen by its Outcome and reason (`repair.kind_of`): **1. wait** (a late change), **2. another
way** (the same action done otherwise), **3. the next-best** (Laya's other candidates). A rung that does not fit is
skipped and costs no try; each that ran is one of the step's `MAX_STEP_TRIES` (the loop passes the tries the step has
left as `room`). Every rung is bounded (the pixel-change waits, never fixed sleeps on the real Mac), verified by
state, traced as `local try n/3: <rung>`, and what it tried goes to the step's look when it still misses:

| Miss | 1. wait | 2. another way | 3. the next-best |
|---|---|---|---|
| nothing changed, something of no listed kind, or a different change (`expected`; also a click with no expectation whose own check missed) | wait for a late change (`CHANGE_TIMEOUT`; 1 s after a different change, which may be on its way to the declared one) and verify the type again (none for a click with no expectation) | a click: the same command by its other route (`repair.other_route`: the menu bar's command its target names for a control on the page, the control named so for a menu command), the first click undone first | a click: try, verify, next for Laya's own pick (`retry.try_next`), else Laya's ranking now (`repair.options`); a page changed in place stays and the next is verified by its type |
| text did not stick (a write or fill: "typed X but 'F' holds …") | wait and read the field again (a value shown late) | (a) a real click on the field, the copy that takes the AX focus when several of one name are stacked at one spot, then the paste (`set_text`: its old text selected through Accessibility; skipped when the step itself did exactly that on its only copy); (b) a click, then the text key by key into that field (`type_keys(text, field)`: the focus made sure and its old text selected the same way, never a blind select all; one line, at most `MAX_KEYS` 120 characters); (c) tab, so a token field turns what was typed into a token, only while that field holds the focus. Each verified by the shared check (`Toolbox.landed`, `facts.text_landed`); the first that works stops it, then a fill goes on with its other fields | none: text goes only into the field the step names |
| no field or control found | wait for a change, then the step again (when the screen changed) | a write or fill: the focused field when it fits (code's reading of its name, else a sure pick check); a click: the menu bar's command its target names | a click with an expectation: Laya's ranked options |
| Laya unsure, no pick | as above | as above | a click with an expectation: Laya's own ranking (`Toolbox.last_unsure`), best first |
| a collect that found nothing (`collect`) | wait for the page to change, then read again | scroll down, then read again (not for a collect with a count, which scrolls on by itself, nor rows a look named by number) | none |
| a dialog in the way the dismiss rules could not close | wait for it to go (a notice) | escape, only for a dialog with no field of its own (the browser's own notice first); then the step again once none of its items is left | none |
| the done check (the end of the plan) | wait for a late change and check again, by code | none | none |
| another app, profile or a login page in front | the loop's own rung: back to the place that worked | | |

Laya's options (`repair.options`) are never a risky or commit-like control, nor one the pick check is sure does
something else; each is clicked, verified by the step's expectation (a click without one is never guessed), undone
when wrong (`retry.undo`; a page changed in place stays), next, `MAX_TRIES` at most. A step a look aimed by number
(`at`, `rows`) is never swapped for another item. Never repaired: a step the user refused, a permission, a stuck
repeat, a step with no collected data, a tell, and `open_place` (it has its own ladder). Every rung that runs the step
again or presses a candidate is an edge of the state graph and never repeats what missed on the same screen
(`progress.run_once`, `progress.pressed`: a candidate already tried there is skipped as "tried here already"). **The single-shot rule**: a
step that sends, answers or commits (`repair.risky_step`: submit, answer, reach, a risky or commit-like click) only
ever gets the wait: it is never run again, done another way or swapped for another candidate; try, verify, next
never clicks again after a risky or commit-like item. A repair never types into a box that already holds the text (it
reads the box after its click; the keys replace the old text, selected through Accessibility). A repaired step is ✓
"repaired locally (how)"; `TaskResult.steps` keeps each step's `local_tries`.

## Progress: the state graph (progress.py)

One mechanism remembers, per task, which action was tried on which screen and where it led (`Progress`, made by
`run_task` and kept on the toolbox as `tools.progress`), so the loop never walks into the same dead end twice.

- **A node is a screen's state** (`progress.state_of`): its place (app, profile and web address without the fragment;
  else the app and window title) and its visible structure: each item's kind and name, a field empty or holding text,
  a switch's state, a control enabled or not, inside a dialog or not, and the count of dialogs. Digits fold to one mark
  (a clock, a counter, a badge: "Inbox (3)" and "Inbox (4)" are one state); the focus, field values, busy flags, the
  window's position, items off screen, menu commands, browser chrome and text read only off the pixels are left out.
  `Screen.fingerprint` stays what the waits compare (any change at all); it is no node: it holds values and the focus.
- **An edge is an action and where it led** (`Progress.record`): the action (`progress.action_of`) is the tool, the
  item it acted on (`view.identity`'s name, kind, AX roles and which of its namesakes: a numbered step's `at`, or the
  control a blind click pressed and the field a write typed into) and, for a blind step, its target's words (a kind
  word counting as its family, "box" = "field": the same words but for kind words is the same target, two kinds that
  differ are not), and its text (what it types or finds; for a tool with no target, its arguments: what a collect
  reads, a compose writes, the keys, the direction; a tell's is its question and what it tells from).
  A numbered item and a blind target that never resolved do not compare: aiming at an item seen is a new approach.
- **Never the same dead end twice**: an action that missed on a state is never run again on that state
  (`Progress.tried`): a plan or look step (it fails `STUCK`, "… was tried on this screen already", with no action,
  and climbs to a look), a local repair's step run again (`progress.run_once`), another way's or the next-best's
  candidate (`progress.pressed`), and try, verify, next records each click. A step that worked is a known path, not
  a dead end, and may be walked again (after an undo, a page through); a wait is never a repeat.
- **Looks and rescues are shown the graph**: a look's `why`, a page call's `Failed:` and a blind rescue's local tries
  get "Already tried on this screen (do not send these again…)" with each edge as `click 'Keep' (button) -> nothing
  changed` (`Progress.here`, the last 8): the target's name as resolved (a content item longer than 4 words as
  `a row [N words]`), never the text typed or read.
- **A proposal is checked before it runs**: a look's or rescue's reply whose first step missed on this screen
  already, or that has no step (all dropped as done) and no question, is refused locally, with no call. When that
  look was shown the tried list, nothing untried is left and the task stops (`GAVE_UP_TRIED`, the said line lists
  what missed); else the failed step goes again (refused there when it is the same dead end) and the next look is told.
- **Progress** is a step's expectation met, or a state the task had not seen before; the no-progress rule
  (`progress.STILL`) counts the steps without either.

## Waiting for the screen (mac.py)

No fixed sleeps on the real Mac. After an action `changed_since` watches a thumbnail of the front window (or page)
every 50 ms (~15 ms each); once it differs from the pre-action picture and holds still 120 ms (cap 1.5 s), one full
read. If pixels never move, one full read at the timeout (`CHANGE_TIMEOUT` 3.5 s), since focus or value changes may
not show. Re-reads while a target or result isn't there yet use `wait_change`. Envs without it (the simulator,
test fakes) keep the fixed backoff.

## Memory (memory.py)

`HarnessMemory` keeps **contract-verified successes only**: the place that worked for an intent, name aliases, the item an
intent meant with how it worked, pitfalls, know-how from a rescue's `learned:` line, and the user's profile; each row
keeps the contract that proved it (the `proof` column, below). `SessionNotes` keep this process's failures in RAM for rescue
prompts and to steer Laya away from options that failed; never persisted. Schema and files: `architecture.md`.
`HarnessMemory.pins` is the pinned tasks' book (below), in `pins.json` beside the memory file; none for a memory in RAM.

- **Keyed by where it is true** (`memory.key_of`, `memory.host_key`): know-how, elements, pitfalls and aliases belong to the page's exact host for a web page ("mail.example.com";
  "www." and a port dropped), and to the app for a native app. Never to a browser: a browser showing no web page keeps
  and gets nothing. A lookup (`HarnessMemory.hosts`) reads the exact host's rows, and its subdomains' only when the
  exact host keeps nothing (a task naming "example.com" gets what "app.example.com" keeps, as a page on a subdomain is
  on the site: `tools.on_site`); never a parent's or a sibling's: a mail host and a search page of one domain never
  share facts. A tip learned on one site is never served on another in the same browser. The step-time key
  (`tools.memory_key`, `Toolbox.here`) is still the registrable domain: `loop.remember` keeps what such a key names
  under the one host of that domain the run read or opened (`Toolbox.seen`, `left_behind`, its places), and nowhere
  when the run saw several; a step-time pick reads the domain's own rows, else its subdomains'.
- **How it worked** (`loop.way`, at each ✓): beside an element, its `how` column holds `tool|text went in|sent
  by|change`, built by code from the proven step: a click and the kind of change it caused (`loop.change_of`:
  another site, `opened page /<first path segment>` when that is a plain word the task does not hold, else "opened
  another page", a dialog, a new focused field, a new row); a write or fill and how its text went in (pasted, pasted
  after a click, typed key by key, a chip, a chip by Tab: the step's result and its local repair's rung); a submit
  adds to the field written last how it was sent (Return, or the button clicked) and the change. A pick whose item
  is content (a row, link, text, heading or image) keeps no way.
- **Pitfalls** ("not X but Y"): within one run proven done, a pick found wrong (`retry.try_next`) before the one
  that worked for the same intent. Kept per site or app (`memory.add_pitfall`); never one whose wrong name is the
  intent itself (a failed step's target: the plan's words, not a name read on screen; a wrong pick that bears the
  intent's own name goes with them); an element that later works
  under X's name for that intent deletes the pitfall.
- **The user's profile** (kind `profile`, app ""): their own e-mail addresses and names as they gave them in a task
  proven done ("my email is …", "my name is …", or a reply to a question asking for theirs: `loop.own_details`),
  never read off a screen; the browser and profile the places used and the default browser, one value each (the
  latest replaces the others). At most 12 rows; shown and
  forgotten with `scripts/karta_memory.py show --kind profile` / `forget profile <key> --value <v>`. A `--record`
  snapshot keeps the profile lines only in each brain request's context (`snapshots.prompt_context`, beside memory's
  block and the pinned templates the request was given, to measure memory's effect), the user's own e-mail addresses
  and names as their key only.
- **The memory block** (`loop.know_how` → `memory.brief` + `memory.profile`) goes into `brain._context` for the plan
  and every rescue, under v1 and v2: for where the screen is, the sites the task names (by their host),
  the native apps it names (in the singular too: "a reminder" names Reminders) and the remembered places whose
  intent's words are all in the task, at most 3 places with 8 lines each: "Known about <site or app> (worked in N
  proven runs):", then `opens in: <browser, profile>` (its places), each element with a way (`search box: type into
  "Search" (pasted), then Return → opened page /results`), its pitfalls, its know-how; then "About the user (from
  tasks proven done):" with the profile rows and the aliases whose name the task says, at most 6 lines
  (`memory.PROFILE_LINES`). Built by code in a fixed order (fresh, most
  used, then by key) and wording. Elements kept before the `how` column serve picks at step time only. A rescue gets
  the block of the page where the step failed. Both plan prompts have one rule for it: name controls as the block
  says, never a pitfall's first name, use the user's details instead of asking.
- **Places by identity**: a remembered place is a site in a browser and profile ("example.com in Safari, profile
  Work") or an app ("Notes app"), never an open tab's position (`memory.place_value` strips one from old rows; a
  stored position is never used). The open tab is found again by its address (Places, above).
- **Know-how where the fix acted** (`loop.remember`): a rescue's `learned:` line is kept under the site or app it
  was learned on only when the run acted there (picked a control or opened a place); else under the last place the
  run acted on, never under an app that was only in front when the task started.
- **Saved only from a contract met** (`loop.remember(…, proof=)`): places, aliases, elements, pitfalls, know-how and
  the profile are written only when the task ends done with its **declared** contract met (above, "The contract"),
  the rule pinning uses; each row keeps that contract as its proof (`HarnessMemory.proving`, written into the `proof`
  column). A run with no contract line (a derived one) saves nothing, nor does a done only the brain's end check saw, nor
  a run that does not finish, its rescue's `learned:` line included. So memory never learns from Karta's own judgement
  of success: no false done is saved.
- **A remembered fact that fails is deleted at once**, on disk and noted in the session (`memory.failed`): a
  remembered place that does not open, a remembered alias whose search finds nothing, a remembered element whose
  click did nothing, and any remembered element, alias or place the current step used (`Toolbox.used`) when that step
  then fails (`Toolbox.forget_used`, from the loop's ✗). A row's `how` is replaced by the latest proven way.
- **Never kept as an element** (`memory.unfit_element`): an item of the browser's chrome (the `chrome` or
  `browser_ui` flag, by structure, never by its name), a web address or the site's own bare name, no name or the word
  None, a name read only off the pixels (only real accessible names are kept; an exact one may be short), and an
  intent of more than 4 words that is not the item's own name (a plan's description, "the field that holds the
  title").
- **A site's boilerplate** (kind `boilerplate`, app = the site's exact host or the app, key = a digest, value ""):
  learned from reads, not from proven runs ("Interface and content", above); never in the memory block; capped at 120
  per site, the oldest going first, aged out as the rest.
- Capped per site or app, aged out. A file from before contracts (`user_version` below 3, `HarnessMemory._migrate`,
  once) drops every element, pitfall, alias and know-how row with no proof (`memory.LEARNED`: they were learned from
  Karta's own judgement of success); its places merge an open tab's position into their site's (before 1) and its
  profile keeps one browser in use (before 2). Places re-check themselves on use (one that does not open is deleted).

## Pinning (recipes.py)

The first time, Karta figures a task out; after that it is pinned. A **recipe** is the path that worked, saved by
code once a task ends done with its **declared contract met** (above, "The contract"), and it keeps that contract as
its proof. A run with no contract line, or a done only the brain's end check saw, is never pinned; nor is a task the
user's reply changed, or one that types into a password or code field anything but a collected `{name}`.

What a recipe holds (`Recipe`, one entry of `pins.json`):

```
id "r3" · template "email {p1} {p2} links from findit to {p3}" · params [p1, p2, p3]
values {p1: "2", p2: "sourdough starter", p3: "mira@example.com"}   the last proven run's: an exact or named replay types these
steps [...]          the ✓ ledger steps in the order they ran (a rescue's fix as it ran; failed tries and ↷ steps left out),
                     values as {pN}, collected data as {name}: collected values are never stored
proofs [...]         per step: {kind: expect | reached, type} | address, at: host/path | field (where) | collected | answered | ran
done_shows · contract (what the run met, values as {pN}) · name (a routine's, or "") · version · history (the last 3
versions, newest first) · fails (in a row) · uses · created · used
```

**Values** (`extract`): only what a step types or counts creates one (a write, type or find's text, a fill's values,
a reach's amount, a collect's count). Code takes the longest runs of such a value's words that are also a run of the
task's words (case-insensitive; "three" = "3"; small words trimmed off the ends; a run needs a word of 3+ letters or
a digit). A run holding another splits around it ("2 sourdough starter" in a subject → "2" and "sourdough starter"),
keeping a leftover that still means something. Values are numbered in task order; the template is the task with
each run replaced, and every string of every step (meanings, expectations, the done line) gets the same
replacement, never a tool, slot, form or site. A find whose text became a value keeps only alternatives that are
values. Examples:
- "email 2 sourdough starter links from findit to mira@example.com" → `email {p1} {p2} links from findit to {p3}`
- "add oat milk and eggs to my groceries list in Listly" → `add {p1} and {p2} to my groceries list in Listly`
- "post standup moved to 11 in the team channel on Chatterbox" → `post {p1} in the team channel on Chatterbox`
  (the submit's `expect` becomes `{p1}`; "team channel", which nothing typed, stays)

**Page plans and looks**: a step a page plan or a look aimed by number is kept with its item's identity (`at`, `rows`;
view.identity) and the name the view showed, never the number, and replays by that identity (above, "The page call");
under auto a blind click or answer keeps the identity of the control it pressed too (above, "Auto planning").

**Typed only**: a run that plans in the typed format (v2: `RecipeBook.typed`, set from the run's prompt) never
replays, offers, binds or pins a recipe in the old words format (`recipes.is_typed`: a click, answer or submit with no
`expects`, a step with `then:` words, or a done line that is no type); a v1 run keeps its own.

**Types, not words**: a recipe keeps each step's expectation and the done line as types
(`recipes.typed`, `typed_text`), never `then:` words; inside `( )` it keeps only words that are a value ({pN}) or
the task's own. Any other word (a date the brain worked out, "Sunday 11 October") is dropped where the type stands
without words, so a replay on another day never looks for that run's words. Replays verify each step by its type.

**Four ways back in**, each through the same loop, ladder, checks, gates, place lock and drift and dialog rules:
- **Exact**: the same command, normalised (case, spaces, closing punctuation, a leading "please") as a recipe's
  template filled with its values → `recipes.Replay` stands in for the plan stream: **0 brain calls**.
- **Named**: "call that my <name>", "name it <name>", "save / pin / keep / remember this as <name>" name the last
  pinned task (0 calls, no Mac); "<name>", "do / run / start / replay / redo [my] <name> [again]" replays it with its
  last values (0 calls), as its full command (the rescue prompt sees that, not the name). Names compare without
  filler (my, the, thing, routine, task, again): "my morning thing" = "morning". A name moves to the newest routine
  given it. "forget that" / "unpin that" removes the last pinned task; "forget <name>" removes that routine, and is an
  ordinary task when no routine has that name ("forget this network").
- **Bound by code** (`RecipeBook.bound`, `recipes.shaped`): the command is a recipe's template with other values,
  and code binds every one from the command's own words → the recipe replays with them: **0 brain calls**
  (`TaskResult.replayed` "bound"). The template's words (at least 2 that are not small words, so "play {p1}" never
  binds) are aligned in order against the command's, each value taking a word or more, and each value must be of
  the kind the recipe's last value was (`recipes.kind_of`): a number by its format ("3", "three", "12.50"), a date or
  time when the platform's date detector (`recipes.date_spans`, NSDataDetector) covers all of it, else text. The
  kinds choose among the ways to align; more than one way left, a kind that differs, a date inside a text value that
  had none (a due date the recipe never set), the same values as before, a command that speaks of a secret
  (`recipes.SECRET`), two recipes that fit, or a stray word of the run it was pinned from (as for `use:`) leaves it
  to the brain, which may still answer `use:`. Every value is a slice of the command, never anything else. A send,
  post, payment or delete still gets the one question up front, as on every replay.
- **Same shape**: when recipes share a word with the task, the plan prompt (user message, not the system prompt) lists
  up to 5 as `- <id>: <template>`, never their values, and the brain may reply with one line,
  `use: <id> | p1: <value> | p2: <value>` (JSON: `{"use": "..."}`). `RecipeBook.bind_use` checks it: a known,
  offered recipe; every value given once, nothing else, one line of at most 200 characters with no braces; each
  bound step valid by `check_step` (a count must be a number); and no literal word the recipe types (a write, type or
  find's text, a fill's values; `recipes.typed_words`) that is a word of the task it was pinned from but not of the
  new task (a stray word of that run's values; the plan's own words stay). A bad line is an error, so the existing one repair ask
  plans normally. **1 brain call** (the plan call, with a short reply). A `use:` line is the whole reply: nothing after
  it is read, and one after steps is an error.

**Checks on replay**: every step is verified as in any run, and the run's end by the recipe's contract (with this
replay's values): a pin re-checks itself on every use. A replay whose contract was checked and did not hold unpins
its recipe at once, logged in the trace and the result ("pinned task r3 failed its contract (…): unpinned"); a recipe
with no contract is never replayed, offered or bound (`RecipeBook.usable`), and a `pins.json` from before contracts
(no `version` 2) drops every recipe with none when it loads, once (`RecipeBook.dropped`). On an exact or named replay (never a bound or
same-shape one: other values reach other addresses), a click, submit, answer or
back with no expectation of its own must also reach the address it reached before (`Hit.checks`: host/path without
query; addresses with digits are not used); a miss is a failed step.

**Self-heal**: a step that fails on replay climbs the normal ladder; a brain rescue fixes that step only (`with_rest`
keeps the recipe's remaining steps). When the task then ends done and proven, the path that ran becomes the recipe's
next version and the previous one goes to `history` (3 kept, `RecipeBook.rollback`). A replay that ends not done
counts a failure (not when the user said no to a step; one whose contract did not hold is unpinned instead); after
`BENCH` (2) in a row the recipe is neither replayed nor
offered: its command is planned afresh, and a proven success resets it ("r1 works again").

**Storage**: `pins.json` beside the memory file (mode 600, written whole through a temp file), on this Mac only, never
sent anywhere: the brain sees templates only. At most 50 recipes (named ones kept first, then the most recently used);
one unused 180 days is deleted. `last` is the recipe pinned or replayed last ("that").

**Seen**: the trace (`pinned: exact replay of r1 v2`, `pin: …`), the terminal (`pin` lines: `replay`, `pinned` events)
and the HUD ("Pinned · replaying", then "✓ Done · pinned" or "✓ Done · pinned v2"). `TaskResult.replayed` (exact,
named, bound, same shape) and `TaskResult.pinned` (what pinning did).

## Safety

- Risk checks fail closed: a safe word (Cancel, Preview) exempts a control only when it is the WHOLE label ("Cancel
  order" asks), and return in a box with no real name counts as a send, except in a native app's list-item editor
  the plan just wrote (a field inside a list, table or outline row, by its ancestors' roles: `facts.in_list_row`; a new
  reminder or row), where Return commits the row and sends nothing.
- The risk floor's words are English, so on a screen in another language (`Screen.lang`, `Toolbox.foreign`) it fails
  closed (`Toolbox.foreign_risk`): Return in any field but a search field or the browser's own counts as a send; a
  click or a dialog answer asks unless Laya is sure, under the risky tier, that it does not send, pay, buy, delete or
  post (D4, "send, pay, buy, delete or post something"); a search control never asks. `tests/test_language.py` holds
  the safety outcomes to English's in German and Spanish: the risk floor asks, permission prompts go to the user,
  dialogs are told from panels, a password field is a login page, a send asks.
- Anything hard to undo (send, post, pay, purchase, delete, a command that runs or re-runs something: Run, Execute,
  "…with Same Command"; a dialog answer that confirms one) needs the user's yes (`risk.risky`, `Toolbox.gate`); in
  voice mode only a clear spoken yes counts. A send also needs the exact message.
- **One question up front** (`loop.ask_ahead`): before the first step that acts (anything but getting there,
  `loop.PREPARES`: open_place, scroll, wait, back, which run while the plan streams), the plan is read whole; when it
  has key actions (`risk.key_action`: send, post, pay or buy, add to cart, delete, star or follow, run a command: a
  `type` of one line ending in a line break; a submit only into a box named as a message, comment or post box) the
  user is asked once, in plain words built by code (`tell.ahead_line`: "I'll open Mailbox, write 'hi' in the message
  field, and send it. Go ahead?"), by voice in voice mode, y/N typed. A yes covers exactly those steps
  (`Toolbox.approved`: that step, that kind of action); the gate still asks for anything risky not in the list (a
  rescue's or look's new steps, a dialog's button the plan never named). A no ends the task before anything is done on
  the page. A pinned replay is asked the same way.
- **A send that holds composed text is never covered by the up-front yes**: typing a composed name (`Toolbox.expand`)
  marks its text unsent (`Toolbox.unsent`); before the submit that sends it (whatever its control is named), or at the
  gate of any send, post or comment while it is unsent, Karta asks with the whole text (`tell.composed_line`: 'Here
  is what I wrote: "…". Send it?', said aloud in voice mode, printed when typed). A yes covers that send step only
  (`heard_for`); a no ends the task with nothing sent. Recipients never come from composed text: compose removes
  every email address from it, and an address a step types must be the task's, the profile's or a session ref's
  (`brain.foreign_detail`).
- **A delegated Claude Code session** is always asked about up front with its exact folder and request, opens only a
  new Terminal window (never types into one), passes no permission flag and the request as one quoted argument with no
  control characters; Karta only reads its hook events and never acts on the session (above, "Delegate").
- **Watches** (karta/watches.py) only read: Terminal's `busy` and `contents`, stat, Mail's inbox headers, EventKit,
  a tab's title, and a plain GET of a public address (no cookies, nothing private, redirects checked). A mail or tab
  text reaches the brain only with the user's kept yes for that app, at most 3 reading calls a watch and 20 a day. A
  scheduled task is a normal screen job: its key actions ask up front, by voice.
- Permission prompts (location, camera, notifications, microphone, any the system or the browser puts up, in any
  language), known by who owns them (Tools, above), always take the user's answer; never Laya's or the plan's.
- The browser's own dialogs (`browser_ui`) are hands-off: only a plain way out (Cancel, Close, Not now), else the
  user answers it as a permission.
- A menu command is clicked only when named in full or by its exact last part, and a risky one only in full. A found place is never left for another profile or browser;
  a place is left while searching only on proof (a login page, a page that failed to load), twice a task at most.
- "Create / make / add / new" never edits an existing item. A focused document body is never typed over as a search.
- The brain never invents personal details (it takes the page's safe default or asks). It runs isolated from the
  user's Claude Code (no settings, CLAUDE.md, AGENTS.md or auto-memory), and a step that would type an email address
  Karta never gave it (`brain.foreign_detail`: not in the task, the user profile or the session's refs) is rejected, since the CLI still
  knows the signed-in account. Never a path that types a
  password. Dry run is the default; Karta acts only with `--act` (or `karta start`).
- A pinned replay runs every step through the same tools and gates: a send, payment or delete asks for the yes on
  every replay.
- The recorder, memory and pins stay on the Mac; the brain sees only a task-relevant slice of the map and the screen
  (and pinned templates, never their values).

## Known limits

- The content model, untested live: the landmark, editor and toolbar marks (`mac.mark_tree`) and the main list were
  checked on made-up trees only; recordings keep no AX paths, so a replay classifies by kind, address and boilerplate
  alone. Text read only off the pixels on a page whose tree is not there yet (a page still loading) has no structure:
  only boilerplate, a control's name and the label rule place it. A result's own title is told apart from its
  author's or category's link by the addresses its links share (`content.title_ids`); a list whose results each have
  one link only and an author linked on fewer than `MIN_ROWS` of them still takes that author's link as a title.

- Delegate, untested live: the AppleScript (`do script`, the tab's tty, the window id), the `ps` check and Claude Code
  running the `--settings` hooks were checked with fakes only. Terminal only (not iTerm or another terminal app). A
  repo is found only by a path or among the Mac map's notable folders; two of one name fail rather than ask. Only the
  first Stop is heard (a later turn in the same session is not), and typed mode does not listen at all. Claude Code's
  own permission prompts are the user's to answer in its window: Karta only says that it needs them.

- Watches and schedules, untested live: the Terminal and Mail AppleScript, EventKit's events and the public GET were
  checked with fakes only. A watch's setup prompt is new and not on the prompt bench. Mail reads the newest 25
  messages of the unified inbox; a watched Terminal window is its selected tab. The calendar probe reads today only.
  A schedule does not wait for the user to be idle before its task takes the screen. Watches show no HUD pill.

- Per-page planning, untested live: the view and the page calls were checked on recorded screens and made-up apps
  only. An OCR item is listed under its row only when the env gives frames (a recording keeps none); an OCR item read
  differently on the next read is gone, so its step looks again. A dialog opening or closing counts as a new page,
  so a plan that clicks into a dialog it then fills costs a look. A personal native app shows its fields' names whole
  (a list's name in a field can be the user's). A named target in a page plan runs as a v2 step (code, then Laya).

- Pinning: a recipe proves its contract on every replay but not the steps' meaning between them; a reworded command needs the brain to see the likeness; code binds only a command that keeps the
  template's words in order. A value is
  found only when the steps typed it word for word from the task, so a value the brain rephrased (a date it
  reformatted, a name it spelled out) stays literal and a same-shape replay types the old one; a stray word shared by
  a typed text and the task can become a value of its own. A pinned question whose answer is worded still makes its one
  wording call. Proofs other than the address are not re-checked beyond the step's own expectation. Replays of recorded
  runs (replay.py) get no pins, so a recorded pinned replay would diverge there.

- Typed checks (expect.py): the right kind of change in the wrong place passes (a new row in another list proves
  `new_item`; a task's words that also show elsewhere in a view prove `new_item(<words>)`; an older draft or form
  with the same typed title, listed where they are kept, proves a new one saved), and changes the step did
  not cause count too (a chat that receives a message, a feed that lazy-loads). A view counts as the same when no
  more than half of it (or 3 items) went and no more came than it showed; a large list redrawn in place falls
  outside. OCR that reads a line differently between two reads makes it look new. `field_holds` proves only a
  document's own text, so a document's title typed into a one-line field needs `new_item(<its name>)`: the window
  newly named so. A command typed into a terminal is proven by the terminal holding it, not by what it printed (its
  output is unknown at plan time). The verifier was validated on recordings with hand labels (scripts/expect_bench.py)
  and on made-up apps; its rules were refined after a first pass over those labels.
- v1 (`KARTA_PROMPT=v1`): its steps' `then:` words are not checked (a step is proven by its tool) and it never skips
  ahead; its end state still reads words new since the last change, then Laya, then one brain check. A confirmation
  line identical to one already there (the same toast again) is not new: a real send then needs Laya's or the
  brain's read of what changed.
- collect drops links to the page's own path with other query parameters, so on an item page addressed by a query
  (`/item?id=1`) the related items at `/item?id=2` are not collected.
- Try, verify, next skips in-place page changes, which most web clicks are, so it rarely fires on real sites.
- Keys by Accessibility, proven on made-up elements only (tests/test_keys.py), untested live: which search fields and
  address bars offer AXConfirm and whether it acts as Return does (a step that may send never uses it); whether a
  send button's AXPress ever reports an error yet goes through (then the step reports no change and nothing is
  pressed again); AXCancel on menus and popovers and AXCancelButton on sheets; each browser's address field
  read as a chrome field (else the menu command or ⌘L); History > Back and File > Open Location found by their key
  equivalents; which apps expose a vertical scroll bar whose AXValue scrolls (a browser's page may not: the wheel);
  AXScrollToVisible on off-screen fields; whether a paste's landing shows in the focused element's value before the
  clipboard goes back (an app that reads it later than `PASTE_WAIT` would paste the user's clipboard).
- Local repair, untested live: typing key by key, tab to make a token, and escape for the browser's own notice. A
  token field whose value AX never shows is proven only by its token appearing; a token that shows a contact's name
  instead of the typed address is not seen. The focus guard and the AX selection were proven on made-up elements only:
  an app that exposes no system-wide focused element, or none of its field's ancestors, gets nothing typed. With the real Laya, 8 of the repair
  bench's 10 miss cases end done with no rescue: it will not take a field named otherwise for the asked one, nor
  guess an icon it ranks below "none".
- Checked on made-up screens and recorded reads only, untested live: a result's main link told by its sub-pages'
  addresses (a site whose results have no sub-page links keeps dropping a result named in a word or two); back by
  the address before when a link opened a new tab (that tab stays open on the page before); an item's own page read
  further down. A site whose results show no price at all leaves only the first matching item's own page to read.
- The local ladder has nothing for a tell whose collected items and page text (a web page's only) do not answer the
  question, a collect that read nothing that fits (its wait only; a value on a web page gets its item's own page
  once), a type with nothing collected, or a done check that missed (its wait only): these go to a look after at
  most one try. Untested live: `scroll_top` on each browser's page (a page whose scroll bar has no AXValue is read
  where it is), a form's own button found by the tree on real search forms, and how often the brain cites. A wait rung that sees no change costs the full `CHANGE_TIMEOUT` on the real Mac.
  The other route and the in-place next-best are untested live.
- Session memory: checked on made-up apps with a fake brain only; the plan prompt teaches nothing about `{@n}` beyond
  the block's header line. A ref's items are titles the user saw (a collected row's words reach the prompt as its
  title). While a block is shown, braces typed at the focus are read as collected names. A recorded run keeps the block
  in its prompt but not the session, so its replay cannot bind `{@n}`. `recall` has no voice or command entry yet.
- "This page", compose and consent: checked on made-up apps with a fake brain and a fake Laya only. Laya's private
  kind of page (`private_screen`) has no gate of its own (D2's) and is untested on real mailboxes, chats and articles;
  an unsure answer asks once per host. A native app counts only when it is registered for `mailto:`, `sms:` or
  `imessage:` (a chat app that registers none is not asked about). Structure only tells a UI label from content by
  shape and by the rows' and window title's own words: a short subject or sender shown only as a lone control or
  heading (no row, no window title holding it) still reads as a label; a one-word subject in the window title shows.
  Memory's know-how lines and the steps already done are not filtered. A compose that writes nothing still gets
  the look a failed step gets. Composed text is read out whole, however long (at most 1500 characters).
- An icon-only or hidden search control is not always found.
- Clicks that change nothing visible wait the full 3.5 s; browser changes outside the page area are seen late.
- Native date and time pickers aren't driven; fields named only "text field" are hard to target.
- Terminal pagers (`less`, `git log`) hold the screen and the loop doesn't leave them.
- The recorder doesn't save per-step timings.
- The contract, checked on made-up apps and recordings only, untested live: whether the brain declares the fitting kind
  (an export or a download whose file the screen never shows has no outcome to prove it, so it ends not done); a
  `created` with no name is proven by any list that grows or count that rises in the app, and a count the step did not
  cause (an unread count) proves it too; `changed` reads a control named the setting, or one the plan flipped sharing a
  word with it (another flipped control so named passes), and a toggle button that renamed itself is taken as the
  change whatever the direction; `opened` needs the view's title, a heading or the address to hold the thing's words or
  a part of the opened item's name; `sent` is the send's own proof, as before (the right kind of change in the wrong
  place passes). An answer's facts that a collect read off an item it replaced (a priced row's) keep no role, and pass
  as content. A remembered item that passed its contract but is wrong for its meaning is caught only when the step that
  uses it fails (then deleted). Remove one with `scripts/karta_memory.py forget alias "<key>" --app "<site or app>"`.
- Switching to an open tab re-reads every browser's tabs (one AppleScript per browser); a recorded run keeps only
  the last tab read, so a replay serves it to every re-read.
- collect cannot tell items apart by a detail Laya does not judge (an overdue invoice from a paid one in the same
  list): the plan has to bring up the filtered view first. Ads are asked of Laya only for an item with a short label of
  its own beside or just before it; an ad block with one label over several ads, or an ad whose label is an image, is
  taken as content. The meaning questions reused for the old word lists (ads, accessories, empty places, no results,
  a sign-in page without a password field, an interruption's way out, a form's send button) keep their decisions'
  gates; untested live in other languages, since the English Laya model reads them.
- A collected text item is its whole line; a spoken list keeps each item's part before " — " (tell.py), so a time
  written after a dash is not said.
- Untested live: AXURL on real pages, multi-line typing into a web compose body, collect on a real results page,
  `page_text` and the AX row of a price on real pages (recordings keep no frames or tree, so rows are checked on
  made-up screens only).
- A page's text comes from the AX tree, so a virtualised list (only its visible rows in the DOM) and canvas text are
  missing from it; a recorded run does not keep it, so its replay reads the screen's items instead.
- Calendar events through EventKit, untested live: the access prompt's answer arriving through the run loop, and
  which calendars an account's sync exposes. A question with no day ("what's on my calendar") reads the screen, and
  so does any calendar question without access. A recorded run keeps no calendar read, so its replay reads the screen.
- OCR: two table cells closer than `CELL_GAP_LINES` (8) of their heights to each other join with the cells below
  them like prose (`perception.merge_blocks`).
- Places: the ranking knows a tab's browser but not its profile (Chromium's AppleScript does not say), so a tab
  place has no profile and "the other profiles of its browser" include the tab's own. A page that failed to load is
  known only by its address (untested live: which address each browser's AXWebArea reports on its error page; one
  that keeps the site's address is ready by code, and the next step's expectation catches it). A sign-in page read only off the pixels counts when a line is exactly "Sign in"
  (or "Log in") beside a line that is exactly a sign-in field's label ("Email or phone", "Password"); a sign-in step
  worded otherwise is not one (the next step's expectation catches it). A page that is ready by code but shows a
  leftover view of the site (a compose or settings page) is worked in as it is; the next step's expectation catches
  it. Reloading the site's root is typed into the address bar of the tab in front, so a place whose window lost the
  front to another of the same browser reloads that one. Untested live: the settle in place, the move cap's
  question, the lock check, `Screen.busy` (AXElementBusy / AXLoaded on each browser's web area).
- The structural facts that replaced word rules are read off synthetic AX trees in tests, untested live: the search
  landmark (AXLandmarkSearch) and AXSearchField on real sites in Chromium, Safari and Firefox; aria-busy as
  AXElementBusy around items; AXLanguage on a web area (without it a browser's page takes the browser's own interface
  language, so a German site in an English browser reads as English and the risk floor does not fail closed there);
  an app's language from its bundle (NSBundle preferredLocalizations); AXMenuItemCmdChar on every app's Find command;
  AXDefaultButton on sheets and dialogs; the address a browser's error page reports; a profile name at the end of a
  Chromium window title; Firefox's page under an AXWebArea (else its page would read as chrome). The meaning
  questions reused for word lists keep their decisions' gates, measured on English screens only.
- A window moved or resized between two reads reads as another window in front (`window_opened`); AXWindows and
  the focused window's frame are untested live per app. The up-front question knows only the plan in hand when the
  first acting step comes: under per-page planning that is the first page's plan, so a later page's send asks when it
  comes; a key action is read off the plan's words (a target named otherwise asks at the gate, or not at all when it is
  no risk: an unlisted star or follow).
- Untested live, read off synthetic trees and made-up screens only: the ancestor roles a list row's editor and a
  toolbar's or date control's next and previous are told by (AXOutlineRow, AXRow, AXCell, AXToolbar, AXDateField: a
  native app may draw its day navigation elsewhere, and then its "next" still asks Laya's risky verdict); which
  attributes a browser's bubble (a translate offer) carries, so that it reads as a panel; a toggle button's frame
  staying put as it renames itself; a redirect to another host followed as the place (a signed-in page reached
  through another host's address). A recording keeps no tree or frames, so a replay cannot show these.
- Dialogs: a site's modal drawn without aria-modal is a panel, so it is not cleared; a click on the page behind it
  then acts as usual (AXPress) or misses and is repaired. Which processes put up system prompts
  (`SYSTEM_PROMPTERS`), and that each comes to the front as its own app, is untested live on each macOS version.
