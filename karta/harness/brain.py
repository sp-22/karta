"""The brain's jobs in harness v2: plan the task in tool calls once, rescue one failing step, and (only when code and
Laya cannot tell) check the end state once.

The brain writes ONE TOOL PER LINE ("click New event", "write see you at 6 | where: message box"); with a brain
that streams lines (karta.brain.claude_code.ClaudeCodeBrain.stream_lines) each line is parsed as it arrives and the
loop starts the first tool before the plan is finished. A brain with only `.json(system, user)` gets the same tools in
a JSON prompt, and a JSON reply in line mode is still read (the fallback parser). Rescues use the same format, but are
read whole: the loop compares a rescue with the plans already tried before running any of it.

The brain gets a small, task-relevant slice of the Mac (brain_facts: the date, the places the task names, the folders
it names), never the whole map: the harness keeps that for places_for, open tabs and profiles.

The prompts carry what the runs taught: click visible controls, open_place only for apps and sites, facts about
this Mac, never invented personal details, rescues that start from what is done, a named service's own
site, an end state that holds the task's deciding detail. Examples come from apps outside every test set.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from karta.harness import content as content_mod
from karta.harness import contract as contract_mod
from karta.harness import expect as expect_mod
from karta.harness import facts as facts_mod
from karta.harness import view as view_mod
from karta.harness.types import Item, MacFacts, Screen, ToolResult

_INTRO = """You plan a Mac task for an automation harness. You never pick clicks: you write TOOL calls, and the harness
carries each one out with code and its own small local model."""

_LINES = """Write ONE TOOL CALL PER LINE and nothing else (no numbering, no prose, no code fence); the harness starts each
line as soon as you finish it:
<tool> <main text> | <key>: <value> | <key>: <value>
Then a last line: done: <2 to 6 words that will be on screen only once the WHOLE task is done> (not after a tell)
When the task needs a personal detail nobody gave (an address, a phone number, a card, a password), add a line
ask: <one short question for the user> and plan only the steps that do not need it. Use only personal details given in this message (the task, the user
profile lines): never an email address, name or number you know from anywhere else.
Tools (nothing else exists):
open_place <2 to 6 words: the app or site> | ready: <a word or two on screen only when it is open and signed in> | site: <the website's host>
    e.g. open_place Listwick board | ready: Backlog | site: listwick.example      e.g. open_place Reminders | ready: Today
    Only for an app or a website: one installed on this Mac (see the facts) or a website you know; omit site for an
    app. Never for a button, a field, a folder or a page inside an app. The harness tries every browser profile itself
    and moves on from a login page.
find <what to type into the search box> | meaning: <the item wanted> | alt: <other search texts, comma separated>
    e.g. find Odile | meaning: the conversation with Odile | alt: Odile, Odil, Odi
    Opens search (a visible search box, else the visible Search button), searches, and opens the item that matches.
    Always give alternatives: spelling variants of a name the user may have misspoken or speech-to-text misheard, a
    shorter form (the first 3 or 4 letters of a name), a synonym.
click <2 to 6 words naming a visible control>          e.g. click New event      e.g. click the Archive folder
    | near: <the item it belongs to> when the same control repeats in a list   e.g. click Play | near: Evening Tide
write <exact text> | where: <the field>                 e.g. write see you at 6 | where: message box   (replaces its text)
type <exact text>    Types at the keyboard focus: a terminal prompt, a document's body; \\n ends a line. Never for a
    field that has a name (use write or fill).
fill <field>: <value> | <field>: <value>               e.g. fill Start date: 14 October 2026 | Start time: 10:00 | All day: off
    A form or a detail panel; a checkbox takes on or off. Dates and times go in their own fields, never in a title.
answer <the option wanted>                              e.g. answer Allow once      A dialog, a popup or a terminal's numbered choice.
submit | expect: <words on screen once it is done>      e.g. submit | expect: see you at 6
    Sends, posts or saves what was written, by its visible button or return. The harness asks the user first when it
    cannot be undone.
reach <amount> | of: <what to add>                     e.g. reach $30 | of: printer paper
    A total worth an amount: once the items are listed (after a find), the harness reads their prices and the running
    total and adds or removes them until the total is the amount. Never click Add yourself for an amount.
back   ·   scroll down   ·   scroll up   ·   wait <seconds>
collect <what to take from the screen> | count: <1 to 10> | as: <a one-word name>
    e.g. collect links to kite plans | count: 4 | as: kites      e.g. collect the unpaid parking fines | as: fines
    Reads what the screen shows once the step before it brought it up (links keep their web addresses, ads are skipped)
    and keeps it under that name. Without count: everything on screen that fits, up to 10. Say "links" when the web
    addresses are wanted, "code" for a one-time code, "price" for an amount. To list search results without opening
    one: write the search | where: search box, submit, then collect.
{name} in the text of a later write, type or fill puts what was collected there: one per line ("title — address") in
    a box of several lines, comma-separated in a one-line field.   e.g. write Worth a look:\\n{kites} | where: description
tell <the user's question> | from: <name> | as: list or answer
    e.g. tell which parking fines are unpaid | from: fines | as: list
    The LAST step of a question about the screen: says the answer aloud from what was collected. list reads the items
    out; answer words a short reply. A task that only asks needs nothing else changed or sent.
ONLY THESE UI ACTIONS: never a key, never a keyboard shortcut (there is no press tool). Click the visible control; a
terminal line ends with \\n in type; a web address: write <the address> | where: address bar, then submit.
Checkpoints: end a step with | then: <2 to 6 words that show only once THAT step worked, or part of the web address it
reaches> on every step that opens, loads or changes something (never on a step that only focuses or types: what the
text leads to goes on the step that sends it). The harness checks each one, knows which steps are done,
and skips ahead when a later checkpoint already shows.   e.g. submit | expect: Open orders | then: shopwell.example/orders
    e.g. click New event | then: Add title      e.g. submit | expect: see you at 6 | then: see you at 6"""

_JSON = """Reply with only JSON:
{"steps": [<tool call>, ...], "done_shows": "<2 to 6 words that will be on screen only once the WHOLE task is done>"}
When the task needs a personal detail nobody gave (an address, a phone number, a card, a password), add
"ask": "<one short question for the user>" and plan only the steps that do not need it. Use only personal details given in this message (the task, the user
profile lines): never an email address, name or number you know from anywhere else.
Tools (nothing else exists):
- {"tool": "open_place", "what": "<2 to 6 words: the app or site, e.g. 'Chatterbox rooms', 'Listwick board', 'Reminders'>",
   "ready_shows": "<a word or two on screen only when it is open and signed in, e.g. 'Rooms', 'Backlog'>",
   "site": "<the website's host when a website serves it, e.g. 'listwick.example', 'chatterbox.example'; omit for an app>"}
    Only for an app or a website: one installed on this Mac (see the facts) or a website you know. Never for a button, a
    field, a folder or a page inside an app. The harness tries every browser profile itself and moves on from a login page.
- {"tool": "find", "text": "<what to type into the search box>", "meaning": "<the item wanted, e.g. 'the board named Roadmap'>",
   "alternatives": ["<other search texts to try if nothing matches>"]}
    Opens search (a visible search box, else the visible Search button), searches, and opens the item that matches.
    Always give alternatives: spelling variants of a name the user may have misspoken or speech-to-text misheard, a
    shorter form (the first 3 or 4 letters of a name), a synonym.
- {"tool": "click", "target": "<2 to 6 words naming a visible control, e.g. 'New event', 'the Archive folder'>",
   "near": "<the list item it belongs to, when the control repeats: e.g. 'Evening Tide'; omit otherwise>"}
- {"tool": "write", "text": "<exact text>", "where": "<the field, e.g. 'event title', 'reply box'>"}  Replaces its text.
- {"tool": "type", "text": "<exact text>"}  Types at the keyboard focus: a terminal prompt, a document's body. Use \\n between
    lines. Never for a field that has a name (use write or fill).
- {"tool": "fill", "fields": {"<field name>": "<value>", ...}}  A form or a detail panel; a checkbox takes true or false.
    Dates and times go in their own fields, never in a title.
- {"tool": "answer", "choice_meaning": "<the option wanted, e.g. 'Allow once', 'Keep current settings'>"}  A dialog, a
    popup or a terminal's numbered choice.
- {"tool": "submit", "expect": "<words on screen once it is done, e.g. the text just posted>"}  Sends, posts or saves what
    was written, by its visible button or return. The harness asks the user first when it cannot be undone.
- {"tool": "reach", "amount": "<e.g. '$30'>", "of": "<what to add, e.g. 'printer paper'>"}  A total worth an amount:
    once the items are listed (after a find), the harness reads their prices and the running total and adds or
    removes them until the total is the amount. Never click Add yourself for an amount.
- {"tool": "back"}  ·  {"tool": "scroll", "direction": "down"}  ·  {"tool": "wait", "seconds": 2}
- {"tool": "collect", "what": "<what to take from the screen, e.g. 'links to kite plans'>", "count": <1
   to 10, or omit for everything that fits, up to 10>, "as": "<a one-word name, e.g. 'kites'>"}  Reads what the screen
    shows once the step before it brought it up (links keep their web addresses, ads are skipped) and keeps it under that
    name. Say "links" when the web addresses are wanted, "code" for a one-time code, "price" for an amount. To list
    search results without opening one: write the search into the search box, submit, then collect.
    "{name}" in the text of a later write, type or fill puts what was collected there: one per line ("title — address") in
    a box of several lines, comma-separated in a one-line field. e.g. {"tool": "write", "text": "Worth a look:\\n{kites}", "where": "description"}
- {"tool": "tell", "question": "<the user's question>", "from": "<a collected name>", "as": "list" or "answer"}  The LAST
    step of a question about the screen: says the answer aloud from what was collected (list reads the items out; answer
    words a short reply). Then no "done_shows" is needed.
ONLY THESE UI ACTIONS: never a key, never a keyboard shortcut (there is no press tool). Click the visible control; a
terminal line ends with \\n in type; a web address: write it with "where": "address bar", then submit.
Checkpoints: add "then": "<2 to 6 words that show only once THAT step worked, or part of the web address it reaches>"
to every step that opens, loads or changes something (never on a step that only focuses or types: what the text
leads to goes on the step that sends it). The harness checks each one, knows which steps are done, and
skips ahead when a later checkpoint already shows. e.g. {"tool": "submit", "expect": "Open orders", "then": "shopwell.example/orders"}"""

_RULES = """Rules:
- Use the facts: the places the task can use on this Mac, its folders, the date. An app that is not installed cannot be
  used; a website can, in any browser. Once the named app is open, stay in it.
- A task that names an online service plans that service's own website, not a generic app that may not be set up
  with it.
- Only UI actions: click what is visible, write into fields. Never a key or a keyboard shortcut; the harness uses keys
  itself where an app needs them.
- A control that appears only after another action (a field after clicking "New", a button enabled only after choosing
  an option) needs that click first.
- "Create", "make", "add" or "new" means a NEW item even when one with the same name is already listed: never open
  or edit an existing item instead.
- Never invent personal details or choices the user did not make: take the option the page itself offers as the default,
  or ask.
- Keep the app or site, the control, the field, the meaning and the option to 2 to 6 words.
- The end state (done) is concrete text that NEWLY appears once the task is done and holds the task's deciding detail:
  the total or count asked for ("$45.00", "3 items"), the recipient with the text sent, the result a command prints
  ("passed", "0 errors"), the new item's title with its date. Never the task restated, and never text you typed
  yourself (a command, a search, a message before it is sent).
- You plan before anything is read: never write a link, a code, an amount or a list you have not seen. collect it once
  it is on screen and use {name} where it goes.
- A question about what an app shows ("what is due", "which ... are open") opens it, brings the items up, collects them
  and ends with tell; it changes and sends nothing.
- "Known about" lines are what worked on this Mac before: name controls and fields as they say, plan the way they
  say, and never a pitfall's first name. "About the user" lines are the user's own details: use them instead of
  asking.
- Use as few steps as the task needs."""

_RESCUE_LINES = """
A step failed. You get what is done (with each result), the failed step, the honest reason, the steps still to do
after it, the failures so far, the places already found, the fixes already tried, and the screen as it is now.
Mechanical misses (text that did not stick, a page that came late, a field or control not found yet, an unsure pick,
a dialog in the way) were already repaired locally; you get what was tried. A rescue is for a step that is wrong in
the plan: a step missing before it, steps in the wrong order, or a target that means the wrong thing.
Fix THAT STEP ONLY: reply with the steps that get it done from this screen, one per line in the same format. The
harness then carries on with the steps still to do, so never repeat them, and never redo a done step (a place found
stays open and is reused). Never send a fix already tried, and never the failed step again in other words (the same
tool on the same target is refused): change the approach (another control to click, a step it needs first, another
spelling, a dialog to answer).
If the failed step is not needed any more (the screen already shows what it was for), reply only: skip: <why>
Only if the steps still to do cannot work from this screen, start with a line replan: <why>, then ALL remaining steps.
If the screen shows the task is done, reply only: done: <what shows it>
If it cannot be done here, reply only: stop: <one honest line>
When the fix is something worth knowing about this site or app next time, add a line: learned: <one short line of know-how>
The task's contract is fixed by its plan: never write a contract line."""

_RESCUE_JSON = """
A step failed. You get what is done (with each result), the failed step, the honest reason, the steps still to do
after it, the failures so far, the places already found, the fixes already tried, and the screen as it is now.
Mechanical misses (text that did not stick, a page that came late, a field or control not found yet, an unsure pick,
a dialog in the way) were already repaired locally; you get what was tried. A rescue is for a step that is wrong in
the plan: a step missing before it, steps in the wrong order, or a target that means the wrong thing.
Fix THAT STEP ONLY: "steps" are the steps that get it done from this screen; the harness then carries on with the steps
still to do, so never repeat them, and never redo a done step (a place found stays open and is reused). Never send a
fix already tried, and never the failed step again in other words (the same tool on the same target is refused):
change the approach (another control to click, a step it needs first, another spelling, a dialog to answer).
If the failed step is not needed any more, reply {"steps": [], "skip": "<why>"}. Only if the steps still to do cannot
work from this screen, add "replan": "<why>" and give ALL remaining steps. If the screen shows the task is done, reply
{"steps": [], "done_shows": "<what shows it>"}. If it cannot be done here, reply {"steps": [], "stop": "<one honest
line>"}. When the fix is something worth knowing about this site or app next time, add "learned": "<one short
line of know-how>"."""

# ---------------------------------------------------------------- the v2 plan prompt (KARTA_PROMPT=v2)
# A step says what KIND of change it causes, from a closed list, never words it guesses the app will show: the brain
# plans before it has seen any page. Words inside an expectation come only from the task, the text the plan types, a
# host, or a name on the screen it is given. Examples come from made-up apps.

_INTRO_V2 = """You plan a Mac task for an automation harness. You write tool calls; the harness carries out each one
with code and a small local model, and checks each step by the kind of change it made on screen."""

_EXPECTS = """Expectations. You have not seen the app, so never guess the words it will show. A click, answer or submit says the
KIND of change it causes, one of:
  page_on(<host or app>)   item_opened   item_opened(<its name>)   results_for(<the search text>)   new_item
  new_item(<the task's words it holds>)   item_gone   item_gone(<its name>)   editor_opened   dialog_opened
  dialog_closed   window_opened   list_changed   toggled   toggled(<its name>)   focus_moved   none
Inside ( ) only: the task's own words, text the plan types, a host, or a name exactly as the screen below shows it.
page_on only for opening another site or app; a page, result or row of the site or app in front: item_opened.
A control that makes or opens something (New, Add, Create, Compose, Reply): new_item, editor_opened, dialog_opened or
window_opened. Delete, archive, remove: item_gone. A filter, sort, tab or view: list_changed. A switch, a checkbox,
Play: toggled. A click on a field: focus_moved. none only when nothing on screen can change.
open_place, find, write, type, fill and collect check themselves: give them no expect.
done: the expectation that proves the task's goal, its deciding detail inside ( ): new_item(<its title or name>) for
anything made or saved (a note, an event, a file, a document; a draft: close it and open its list first),
item_gone(<its name>), results_for(<the search>), page_on(<host>), toggled(<its name>), dialog_closed for a message or
form sent; field_holds(<text the plan wrote>) only for text typed into a document's body that is itself the result.
Never none."""

_CONTRACT = """The contract: what the task must achieve, checked by the harness at the end; the task is done only when it
holds. One of:
  an answer: list(<N>, titles|items|links|events|prices|names)   value(number|time|date|price|word)
    comparison(<A> vs <B> on <measure>)   decision   summary(of <what is read>)
  an outcome: created(<kind> named <its name>)   changed(<setting> to <value>)   sent(to <recipient>)
    opened(<the thing>)   deleted(<the thing>)
N only when the task asks for a number of them. A question's contract is an answer: it ends at its tell. A search, a
page or an item to show: opened(<what it shows>). A switch, a checkbox, or a control that pins, stars, bookmarks or
follows: changed(<the setting> to on|off). Words in ( ) only the task's own, or text the plan types."""

_LINES_V2 = """Reply with ONE TOOL CALL PER LINE and nothing else (no numbering, no prose, no code fence):
First line: contract: <what the task must achieve> (below).
<tool> <main text> | <key>: <value> | <key>: <value>
Last line: done: <the expectation that holds once the WHOLE task is done>; a question ends at its tell, no done.
Write the plan once, top to bottom: the harness runs each line as it arrives, so never revise or repeat it. Work
out a date or anything else silently, or on a line starting with # before the steps.
A personal detail nobody gave (an address, a phone number, a card, a password): a line ask: <one short question>,
and plan only the steps that do not need it. Use only personal details given in this message (the task, the user
profile lines): never an email address, name or number you know from anywhere else.

Tools (nothing else exists):
open_place <the app or site, 2 to 6 words> | site: <the website's host; omit for an app>
find <text to search for> | meaning: <the item wanted> | alt: <other spellings or shorter forms, comma separated>
    Searches with the visible search box or Search control and opens the item that matches.
click <the control's name, 2 to 6 words> | near: <the row it belongs to, when it repeats> | expect: <change>
write <exact text> | where: <the field>          replaces the field's text
type <exact text>      at the keyboard focus: a terminal prompt, a document's body; \\n ends a line
fill <field>: <value> | <field>: <value>          a form; a checkbox takes on or off; dates and times in their own fields
answer <the option wanted> | expect: <change>     a dialog, a popup, a terminal's numbered choice
submit | expect: <change>      sends, posts, saves or searches what was written; the harness asks the user first when it
    cannot be undone
reach <amount> | of: <what to add>                adds listed items until the total is the amount
back  ·  scroll down  ·  scroll up  ·  wait <seconds>
collect <what to take from the screen> | count: <1 to 10> | as: <one-word name>
    reads what the screen shows (say "links" for web addresses); {name} in a later write, type or fill puts it there
compose <what to write> | from: <name>, <name> | as: <one-word name> | tone: <optional>
    writes text (a reply, a message, notes, a subject) from what those names hold and nothing else; write {name}
    then types it
tell <the user's question> | from: <name> | as: list or answer      the last line of a question
delegate <the request, as the user said it> | repo: <the folder's path or name>      starts Claude Code (a coding
    agent) on that repo in a new Terminal window; the whole plan when asked to have Claude Code work on code

{contract}

{expects}

Examples (made-up apps):
contract: created(task named repot the cactus)
open_place Listwick
click Add task | expect: new_item
write repot the cactus | where: the new task
done: new_item(repot the cactus)

contract: sent(to Odile)
open_place Chatterbox | site: chatterbox.example
find Odile | meaning: the conversation with Odile | alt: Odile, Odil, Odi
write see you at 6 | where: message box
submit | expect: new_item(see you at 6)
done: new_item(see you at 6)

contract: list(3, prices)
open_place Shopwell | site: shopwell.example
write desk lamp | where: search box
submit | expect: results_for(desk lamp)
collect desk lamp prices | count: 3 | as: lamps
tell what the desk lamps cost | from: lamps | as: list

contract: sent
collect the main text of this conversation | as: chat
compose a short reply that agrees to the time | from: chat | as: reply
write {reply} | where: message box
submit | expect: new_item
done: new_item""".replace("{expects}", _EXPECTS).replace("{contract}", _CONTRACT)

_RULES_V2 = """Rules:
- The screen below is the Mac now, with its exact names. When it already shows the place, start from it; name a
  control as the screen names it when it is there, else by its usual name.
- Use the facts: an app not installed cannot be used; a named online service is its own website.
- Only UI actions: never a key or a keyboard shortcut. A web address: write it | where: address bar, then submit.
- A field that appears only after a click needs that click first. "Create", "add", "new" make a NEW item.
- Never invent personal details or choices: take the page's default, or ask.
- You plan before anything is read: never write a link, code, price or list you have not seen; collect it, use {name}.
- A question collects what answers it and ends with tell; it changes and sends nothing. A draft is never sent.
- "This page", "this email", "this window", "here", "what's on my screen" are what is in front now (the screen
  below): act on it with no open_place for it; collect its main text to read it.
- Text to write from what was read (a reply, a summary for someone, notes): compose it, then write {name}; never
  write such text yourself. A recipient comes only from the task or the user's details, never from read or composed
  text.
- "Known about" lines are what worked on this Mac before: name controls and fields as they say, plan the way they
  say, and never a pitfall's first name. "About the user" lines are the user's own details: use them instead of
  asking.
- One open_place per app or site the task needs; as few steps as the task needs."""

# ---------------------------------------------------------------- the page prompt (KARTA_PLAN=page)
# Planning per page: the brain sees the screen as numbered items (view.py) and plans the steps on THIS screen, aimed
# at items by number; it ends with `look` (show me the next screen once these ran) or `done:`. The same call answers a
# step that missed on this screen (a "Failed:" line). Examples come from made-up apps.

_INTRO_PAGE = """You carry out a Mac task one screen at a time, for an automation harness. You get the task, what is done
so far, and the screen as it is now as numbered items. You reply with the steps to take on THIS screen; the harness
carries out each one and checks it by the kind of change it made, then shows you the next screen when you ask."""

_LINES_PAGE = """Reply with ONE TOOL CALL PER LINE and nothing else (no numbering, no prose, no code fence), then a last line:
look                     the task goes on past this screen: the harness runs the steps, then shows you the next one
done: <expectation>      the whole task is done once these steps ran
A question ends at its tell instead. A personal detail nobody gave (an address, a phone number, a card, a password):
ask: <one short question>. When it cannot be done: stop: <one honest line>. Use only personal details given in this
message (the task, the user profile lines): never an email address, name or number you know from anywhere else. A
thought, a date worked out: only on a line starting with # before the steps.
On the task's first screen ("Done so far: (nothing yet)"), the first line is contract: <what the task must achieve>
(below); never again after that.

Tools (nothing else exists); #n is item n of the screen below, exactly that item:
click #n | expect: <change>          a button, link, row, tab, checkbox or menu command ("Menu > Command")
write #n "<exact text>"              replaces field n's text
fill #n:"<value>" #m:"<value>"       several fields of a form; a checkbox takes on or off; dates and times in their own
submit | expect: <change>            sends, saves or searches what was written last; the harness asks the user first
    when it cannot be undone
type <exact text>                    at the keyboard focus: a terminal prompt, a document's body; \\n ends a line
answer <the option wanted> | expect: <change>      a terminal's numbered choice
open_place <the app or site> | site: <the website's host; omit for an app>      another app or site
find <text to search for> | meaning: <the item wanted> | alt: <other spellings or shorter forms, comma separated>
collect #n, #m | what: <what they are> | as: <one-word name>      those items, as they are
collect <what to take> | count: <1 to 10> | as: <one-word name>  what on the screen fits (say "links" for addresses)
    {name} in a later write, type or fill puts what was collected there
compose <what to write> | from: <name>, <name> | as: <one-word name> | tone: <optional>      text (a reply,
    a message, notes) written from what those names hold and nothing else; write {name} then types it
tell <the user's question> | from: <name> | as: list or answer      the last line of a question
delegate <the request, as the user said it> | repo: <the folder's path or name>      starts Claude Code (a coding
    agent) on that repo in a new Terminal window; the whole plan when asked to have Claude Code work on code
reach <amount> | of: <what to add>   ·   back   ·   scroll down   ·   scroll up   ·   wait <seconds>

The screen: items are grouped (focused, dialog, fields, controls, rows, menu commands, text, interface); rows and
text are what the page or app shows, interface is its own frame (navigation, banners, a form's labels), never an
answer; "@" is where one is; (host) is the site a link leads to, when not this one; [ocr] was read off the pixels,
its kind unknown, listed under the row it is drawn in; [N words] is content hidden for privacy (#a..#b is a run of
such rows, one number each); "holds N chars" is a field's text, hidden.

{contract}

{expects}"""

_RULES_PAGE = """Rules:
- Plan only what this screen offers, by number; never name a control that is not listed. A step that opens another
  page, app or window is the last one on this screen (only collect or tell may follow it: they read what it opens),
  then look. When what the task needs next is not here (another page, still loading, further down, a control that
  shows only after another step), take the step that gets there, then look; with none to take: look: <why>.
- "Done so far" is done: never redo it. A "Failed:" line is a step that missed on this screen: take another way (another
  item, a step it needs first); never the same step on the same item again. With "Still to do after it", reply only
  the steps that get the failed one done, with no look: the harness then carries on with those.
- Use the facts: an app not installed cannot be used; a named online service is its own website.
- Only UI actions: never a key or a keyboard shortcut. "Create", "add", "new" make a NEW item. A draft is never sent.
- Never invent personal details or choices: take the page's default, or ask. Never write a link, code, price or list
  you have not been shown; collect it and use {name}.
- A question collects what answers it and ends with tell; it changes and sends nothing.
- "Known about" lines are what worked on this Mac before; "About the user" lines are the user's own details: use them
  instead of asking.

Examples (made-up apps). Screen: app: Listwick ... controls: #4 button 'Add task' @top-right ... ; task "add a task
repot the cactus in listwick":
contract: created(task named repot the cactus)
click #4 | expect: new_item
look
The next screen shows focused: #1 field 'New task' [focused]:
write #1 "repot the cactus"
submit | expect: new_item(repot the cactus)
done: new_item(repot the cactus)

Screen: app: Finder; task "what do desk lamps cost on shopwell":
contract: list(prices)
open_place Shopwell | site: shopwell.example
look
The next screen shows fields: #2 field 'Search products' @top:
write #2 "desk lamp"
submit | expect: results_for(desk lamp)
collect desk lamp prices | count: 3 | as: lamps
tell what the desk lamps cost | from: lamps | as: list"""

PAGE_SYS = f"{_INTRO_PAGE}\n{_LINES_PAGE}\n{_RULES_PAGE}".replace(
    "{expects}", _EXPECTS.replace("You have not seen the app, so never guess the words it will show. ", "")
).replace("{contract}", _CONTRACT)

# ---------------------------------------------------------------- the look prompt (KARTA_PLAN=auto)
# Planning blind once, looking only when it matters: a step whose check missed (after the local repairs), or a place the
# plan never named, gets one call with the numbered view of the screen at that point (view.py), the step and what it
# expected, what actually changed, and the plan's steps still to do. The brain fixes that step by number; then the
# plan's rest carries on (`continue`), or its steps replace the rest. Examples come from made-up apps.

_INTRO_LOOK = """You fix one step of a Mac task for an automation harness. The task was planned before any screen was seen;
the harness runs the plan step by step and checks each step by the kind of change it made. Now a step missed its check,
or the plan reached a screen it never named. You get the task, what is done so far, that step (what it expected and
what actually changed), the plan's steps still to do, and the screen as it is now as numbered items."""

_LINES_LOOK = """Reply with the steps that get that step done on THIS screen, ONE TOOL CALL PER LINE and nothing else (no
numbering, no prose, no code fence), aimed at items by number, then a last line:
continue                 the plan's steps still to do carry on after yours, as they are
look                     your steps lead to another screen that still needs steps before the plan's rest: the
                         harness runs them, then shows you that screen
done: <expectation>      instead of the plan's steps still to do: write the steps that replace them all after yours
                         (on a later screen name a control by its usual name, never a number), then this line
When that step is not needed any more (the screen shows what it was for already), reply only: continue
A personal detail nobody gave (an address, a phone number, a card, a password): ask: <one short question>. When it
cannot be done: stop: <one honest line>. Use only personal details given in this message (the task, the user profile
lines): never an email address, name or number you know from anywhere else. A thought: only on a line starting with #
before the steps.

Tools (nothing else exists); #n is item n of the screen below, exactly that item:
click #n | expect: <change>          a button, link, row, tab, checkbox or menu command ("Menu > Command")
write #n "<exact text>"              replaces field n's text
fill #n:"<value>" #m:"<value>"       several fields of a form; a checkbox takes on or off; dates and times in their own
submit | expect: <change>            sends, saves or searches what was written last; the harness asks the user first
    when it cannot be undone
type <exact text>                    at the keyboard focus: a terminal prompt, a document's body; \\n ends a line
answer <the option wanted> | expect: <change>      a terminal's numbered choice
open_place <the app or site> | site: <the website's host; omit for an app>      another app or site
find <text to search for> | meaning: <the item wanted> | alt: <other spellings or shorter forms, comma separated>
collect #n, #m | what: <what they are> | as: <one-word name>      those items, as they are
collect <what to take> | count: <1 to 10> | as: <one-word name>  what on the screen fits (say "links" for addresses)
    {name} in a later write, type or fill puts what was collected there
compose <what to write> | from: <name>, <name> | as: <one-word name> | tone: <optional>      text (a reply,
    a message, notes) written from what those names hold and nothing else; write {name} then types it
tell <the user's question> | from: <name> | as: list or answer      the last line of a question
delegate <the request, as the user said it> | repo: <the folder's path or name>      starts Claude Code (a coding
    agent) on that repo in a new Terminal window; the whole plan when asked to have Claude Code work on code
reach <amount> | of: <what to add>   ·   back   ·   scroll down   ·   scroll up   ·   wait <seconds>

The screen: items are grouped (focused, dialog, fields, controls, rows, menu commands, text, interface); rows and
text are what the page or app shows, interface is its own frame (navigation, banners, a form's labels), never an
answer; "@" is where one is; (host) is the site a link leads to, when not this one; [ocr] was read off the pixels,
its kind unknown, listed under the row it is drawn in; [N words] is content hidden for privacy (#a..#b is a run of
such rows, one number each); "holds N chars" is a field's text, hidden.

{expects}"""

_RULES_LOOK = """Rules:
- Every line is a tool call, continue, look, done:, ask:, stop: or a thought starting with #; never a sentence.
- A "Failed:" line is a step that missed: it says what it expected and what happened instead (nothing changed, or
  another kind of change, named). "Failed: (end of plan)" means the task's contract does not hold yet: take the
  steps that make it hold (the contract is fixed; a done: line never changes it). An "Arrived:" line is a screen the plan never named: when the plan's rest fits it,
  reply only: continue.
- Aim at this screen's items by number; never name a control that is not listed. A step that opens another page, app
  or window is the last numbered one (only collect or tell may follow it).
- "Done so far" is done: never redo it. Never the failed step again on the same item: take another way (another item,
  a step it needs first, a dialog to answer). Never repeat the plan's steps still to do when you end with continue.
- Use the facts: an app not installed cannot be used; a named online service is its own website.
- Only UI actions: never a key or a keyboard shortcut. "Create", "add", "new" make a NEW item. A draft is never sent.
- Never invent personal details or choices: take the page's default, or ask. Never write a link, code, price or list
  you have not been shown; collect it and use {name}.
- "Known about" lines are what worked on this Mac before; "About the user" lines are the user's own details: use them
  instead of asking.

Example (a made-up app). Failed: click Add task | expect: new_item -> nothing changed. Still to do after it: write
repot the cactus | where: the new task. Screen: ... #3 button 'New' @top-left ... #7 menu 'Task > New Task':
click #7 | expect: editor_opened
continue"""

LOOK_SYS = f"{_INTRO_LOOK}\n{_LINES_LOOK}\n{_RULES_LOOK}".replace(
    "{expects}", _EXPECTS.replace("You have not seen the app, so never guess the words it will show. ", "")
)

PLAN_SYS = f"{_INTRO}\n{_LINES}\n{_RULES}"
PLAN_SYS_V2 = f"{_INTRO_V2}\n{_LINES_V2}\n{_RULES_V2}"
RESCUE_SYS_V2 = PLAN_SYS_V2 + _RESCUE_LINES
RESCUE_SYS = PLAN_SYS + _RESCUE_LINES
PLAN_SYS_JSON = f"{_INTRO}\n{_JSON}\n{_RULES}"
RESCUE_SYS_JSON = PLAN_SYS_JSON + _RESCUE_JSON

_END = """You check whether a task on a Mac is finished, from what its screen shows now. {reply}
Say yes only when the screen shows the task's end state with its deciding detail (the total or count asked for, the
recipient and the text sent, the result a command printed, the new item with its details). Text the assistant typed
that still waits to be sent or run (in a message box, a search box, a command line) is not the result; a document or
note it typed into is."""
END_SYS = _END.format(reply="Reply with exactly one line:\nyes: <why, in a few words>      or      no: <why, in a few words>")
END_SYS_JSON = _END.format(reply='Reply with only JSON: {"done": true or false, "why": "<a few words>"}')

TOOLS: dict[str, dict[str, tuple[type | tuple[type, ...], bool]]] = {  # tool -> field -> (type, required)
    "open_place": {"what": (str, True), "ready_shows": (str, False), "site": (str, False)},
    "find": {"text": (str, True), "meaning": (str, True), "alternatives": (list, False)},
    "click": {"target": (str, True), "near": (str, False), "at": (dict, False)},
    "write": {"text": (str, True), "where": (str, True), "at": (dict, False)},
    "type": {"text": (str, True)},
    "fill": {"fields": (dict, True), "at": (list, False)},
    "answer": {"choice_meaning": (str, True), "at": (dict, False)},
    "submit": {"expect": (str, True)},
    "press": {"keys": (str, True)},
    "back": {},
    "scroll": {"direction": (str, False)},
    "wait": {"seconds": ((int, float), False)},
    "reach": {"amount": (str, True), "of": (str, False)},
    "collect": {"what": (str, True), "count": (int, False), "slot": (str, False), "rows": ((str, list), False)},
    "tell": {"question": (str, True), "slot": (str, True), "form": (str, False)},
    "compose": {"what": (str, True), "sources": (list, True), "slot": (str, True), "tone": (str, False)},
    "delegate": {"request": (str, True), "repo": (str, False)},
}
for _spec in TOOLS.values():
    _spec["then"] = (str, False)  # a step's checkpoint: what shows (or the web address reached) once it worked
# one line per tool: the field its main text fills, and its `| key: value` parts (a few spellings of each key)
LINE_FIELDS: dict[str, tuple[str | None, dict[str, str]]] = {
    "open_place": ("what", {"ready": "ready_shows", "ready_shows": "ready_shows", "site": "site"}),
    "find": ("text", {"meaning": "meaning", "alt": "alternatives", "alts": "alternatives", "alternatives": "alternatives"}),
    "click": ("target", {"near": "near", "row": "near"}),
    "write": ("text", {"where": "where", "in": "where", "field": "where"}),
    "type": ("text", {}),
    "fill": (None, {}),
    "answer": ("choice_meaning", {}),
    "submit": ("expect", {"expect": "expect"}),
    "press": ("keys", {}),
    "back": (None, {}),
    "scroll": ("direction", {}),
    "wait": ("seconds", {}),
    "reach": ("amount", {"of": "of"}),
    "collect": ("what", {"count": "count", "n": "count", "as": "slot", "slot": "slot", "into": "slot", "what": "what"}),
    "tell": ("question", {"from": "slot", "slot": "slot", "as": "form", "form": "form"}),
    "compose": ("what", {"from": "sources", "as": "slot", "tone": "tone"}),
    "delegate": ("request", {"repo": "repo", "in": "repo"}),
}
# the JSON names of the fields a Python keyword or a nicer word holds ("as" is the slot of a collect, the form of a tell)
ALIASES = {
    "collect": {"as": "slot", "into": "slot"},
    "tell": {"from": "slot", "as": "form"},
    "compose": {"from": "sources", "as": "slot"},
}
SLOT = re.compile(r"^(?:[a-z][a-z0-9_]{0,30}|@\d{1,4})$")  # a name, or a session ref (@7: session.py)
SLOT_REF = re.compile(r"(?<![$\w{])\{([A-Za-z][A-Za-z0-9_]{0,30}|@\d{1,4})\}(?!\})")  # {kites}, {@7}; never ${HOME}, {{x}}
DEFAULT_SLOT = "items"
MAX_COLLECT = 10
FORMS = ("answer", "list")
SLOT_TOOLS = ("collect", "tell", "compose")  # steps that change nothing on screen: no checkpoint, never skipped over
for _main, _keys in LINE_FIELDS.values():
    _keys.update({"then": "then", "checkpoint": "then"})
META = {
    "done": "done_shows",
    "done_shows": "done_shows",
    "ask": "ask",
    "stop": "stop",
    "learned": "learned",
    "skip": "skip",
    "replan": "replan",
    "use": "use",
    "look": "look",
    "continue": "carry",
    "contract": "contract",
}
PLAN_TEXT = ("done_shows", "ask", "stop", "learned", "skip", "replan", "use", "look", "carry", "contract")
BULLET = re.compile(r"^(?:[-*•]|\d+[.)]|step \d+:)\s*", re.I)
HEAD = re.compile(r"^([A-Za-z][A-Za-z_]*)(\s*:\s*|\s+|$)(.*)$", re.S)
KEY = re.compile(r"^\s*([A-Za-z][A-Za-z_]{0,20})\s*:\s*(.*)$", re.S)
ON = {"on", "true", "yes", "checked"}
OFF = {"off", "false", "no", "unchecked"}
SCREEN_LINES = 30
TEXT_KINDS = ("heading", "text", "image", "other")  # the plan digest's text: what is no control, field or row
END_LINES = 40
PLACE_LINES = 5
PROMPTS = ("v1", "v2")
PLANS = ("auto", "page", "blind")  # KARTA_PLAN: blind once, a look at the page on a miss; per page; or blind only
DEFAULT_PLAN = "auto"
PAGE_MODEL = "sonnet"  # KARTA_PAGE_MODEL: the page calls' model (Haiku went 88% on the seen-page picks, Sonnet 97%)
NUMBER = re.compile(r"^#(\d+)\b")  # a step's target as an item of the page view: "#12"
NUMBERED_FILL = re.compile(r'#(\d+)\s*:\s*(?:"((?:[^"\\]|\\.)*)"|(\S+))')  # fill #3:"Ana" #4:on
EXPECT_TYPES = expect_mod.TYPES  # v2's closed list of expectations (expect.py verifies each by structure)
ACTING = ("click", "answer", "submit")  # the steps that declare the kind of change they cause
# The v2 step grammar (strict: a line that does not match it is prose, never a step). A name (a control, an option, a
# place) is a few words, never a sentence; a step's `|` parts are its own keys; a tool that takes no words takes none.
PROSE = "prose"
NAME_WORDS = 8  # at most this many words in a click's, an answer's or an open_place's name
NAMED = ("click", "answer", "open_place")  # tools whose main text is a name (or #n)
KEYED = ("type", "write", "fill")  # tools whose text may hold a `|` (a shell pipe): any part is their text
SENTENCE_END = re.compile(r"(?<!\.)[.!?;]$")  # a sentence's last mark ("Save as..." and "Export…" are names)
WAIT_TEXT = re.compile(r"^\d+(?:\.\d+)?\s*(?:s|sec|secs|seconds?)?$", re.I)


def plan_mode(value: str | None = None) -> str:
    """How the brain plans: `value`, else KARTA_PLAN: "auto" (the whole task once, blind; a step whose check misses,
    or a place the plan never named, gets a look: the numbered view of the screen then), "page" (each page from a
    numbered view of it, view.py) or "blind" (the whole task before any page is seen; a miss gets a blind rescue)."""
    mode = (value or os.environ.get("KARTA_PLAN") or DEFAULT_PLAN).strip().lower()
    return mode if mode in PLANS else DEFAULT_PLAN


def prompt_version(value: str | None = None) -> str:
    """The plan prompt in use: `value`, else KARTA_PROMPT: v2 (typed expectations), the default, or v1 (checkpoint
    words), kept for one release."""
    version = (value or os.environ.get("KARTA_PROMPT") or "v2").strip().lower()
    return version if version in PROMPTS else "v2"


def parse_expect(text: str) -> tuple[str, str, str | None]:
    """A v2 expectation, `type` or `type(arg)`: (type, arg, None), or ("", "", why it is not one)."""
    got, error = expect_mod.parse(text)
    return (got.kind, got.arg, None) if got is not None else ("", "", error)


@dataclass
class Plan:
    steps: list[dict]
    done_shows: str = ""
    ask: str = ""
    stop: str = ""
    learned: str = ""  # know-how the rescue found, kept in memory once the task succeeds
    skip: str = ""  # a rescue: the failed step is not needed any more (why)
    replan: str = ""  # a rescue: the steps still to do cannot work from here, so these steps replace them all (why)
    use: str = ""  # a plan: a pinned task to replay with other values ("<id> | p1: <value> | ...", recipes.py)
    look: str = ""  # a page plan: the task goes on past this screen; look again once its steps ran (the why, or "look")
    carry: str = ""  # a look (KARTA_PLAN=auto): the plan's steps still to do carry on after this reply's ("continue")
    errors: list[str] = field(default_factory=list)
    prose: list[str] = field(default_factory=list)  # lines read as prose, not steps (ignored; noted in the trace)
    pinned: object = None  # the recipes.Hit a `use:` line (or a replay) bound: its steps are this plan's
    contract: str = ""  # what the task must achieve (contract.py), as code wrote it back: the plan's first line


def check_step(step: object, v2: bool | None = None) -> tuple[dict | None, str | None]:
    """A tool call, cleaned, or why it cannot be one. `v2` (default: the prompt in use): a step may carry `expects`, one
    expectation of EXPECT_TYPES, and a submit's words (`expect`) are optional."""
    if not isinstance(step, dict):
        return None, f"not a tool call: {step!r}"
    tool = step.get("tool")
    if tool not in TOOLS:
        return None, f"unknown tool {tool!r} (tools: {', '.join(TOOLS)})"
    v2 = prompt_version() == "v2" if v2 is None else v2
    spec = TOOLS[tool]
    if v2:
        spec = {**spec, "expects": (str, False)} | ({"expect": (str, False)} if tool == "submit" else {})
    step = {ALIASES.get(tool, {}).get(k, k): v for k, v in step.items()}
    if tool == "collect" and isinstance(step.get("count"), str) and step["count"].strip().isdigit():
        step["count"] = int(step["count"].strip())
    if tool == "tell" and not step.get("slot"):
        return None, "tell: missing from: <the name a collect kept the items as>"
    if tool == "compose":
        if not step.get("slot"):
            return None, "compose: missing as: <a new one-word name for the text>"
        if isinstance(step.get("sources"), str):  # "from: message, @3": the names it writes from
            step["sources"] = [s.strip().strip("{}").lower() for s in re.split(r"[,\s]+", step["sources"]) if s.strip("{} ")]
        if not step.get("sources"):
            return None, "compose: missing from: <the collected names it writes from>"
    unknown = sorted(set(step) - {"tool", *spec})
    if unknown:
        return None, f"{tool}: unknown field(s) {unknown} (fields: {sorted(spec)})"
    clean: dict = {"tool": tool}
    for name, (kind, required) in spec.items():
        value = step.get(name)
        if value is None or value == "":
            if required:
                return None, f"{tool}: missing {name!r}"
            continue
        if not isinstance(value, kind) or isinstance(value, bool):
            return None, f"{tool}: {name!r} must be {getattr(kind, '__name__', 'a number')}"
        clean[name] = value
    if "expects" in clean:
        kind, arg, error = parse_expect(clean["expects"])
        if error:
            return None, f"{tool}: {error}"
        clean["expects"] = f"{kind}({arg})" if arg else kind
    if v2:  # a step proves itself by its kind of change, never by words guessed at
        clean.pop("then", None)
        clean.pop("ready_shows", None)
    if v2 and tool == "submit" and not clean.get("expect"):
        clean["expect"] = parse_expect(clean.get("expects", ""))[1]  # the harness's submit still reads words
    if tool == "find":
        clean["alternatives"] = [str(a) for a in clean.get("alternatives", []) if str(a).strip()]
    if tool == "fill":
        if not clean["fields"] or not all(isinstance(k, str) for k in clean["fields"]):
            return None, "fill: 'fields' must map field names to values"
        clean["fields"] = {k: v if isinstance(v, bool) else str(v) for k, v in clean["fields"].items()}
    if tool == "scroll" and clean.get("direction", "down") not in ("down", "up"):
        return None, "scroll: 'direction' is down or up"
    if tool == "press":  # the brain plans UI actions only; keys are the harness's own business
        return None, (
            f"press {clean['keys']!r}: no keys or keyboard shortcuts, only UI actions: click the visible "
            "control; a terminal line ends with \\n in type; a web address: write it | where: address bar, then submit"
        )
    if tool == "wait":
        clean["seconds"] = min(10.0, max(0.5, float(clean.get("seconds", 2))))
    if tool in SLOT_TOOLS:
        clean.pop("then", None)  # it reads, it changes nothing: its proof is what it took (or said)
        slot = str(clean.get("slot") or (DEFAULT_SLOT if tool == "collect" else "")).strip().strip("{}").lower()
        if not SLOT.match(slot):
            return None, f"{tool}: {'from' if tool == 'tell' else 'as'} must be one short word, not {slot!r}"
        if tool in ("collect", "compose") and slot.startswith("@"):
            return None, f"{tool}: as is a new name, never a session ref ({slot})"
        clean["slot"] = slot
    if tool == "collect" and "count" in clean and not 1 <= clean["count"] <= MAX_COLLECT:
        return None, f"collect: count is 1 to {MAX_COLLECT}, not {clean['count']}"
    if tool == "compose":
        bad = next((s for s in clean["sources"] if not SLOT.match(str(s))), None)
        if bad is not None:
            return None, f"compose: from is collected names (one short word each, or @n), not {bad!r}"
    if tool == "delegate":
        from karta.harness.delegate import check_request

        why = check_request(clean["request"])
        if why:
            return None, f"delegate: {why}"
        clean["request"] = clean["request"].strip()
    if tool == "tell":
        form = str(clean.get("form") or "answer").strip().lower()
        if form not in FORMS:
            return None, f"tell: as is list or answer, not {form!r}"
        clean["form"] = form
    return clean, None


def slot_refs(step: dict) -> set[str]:
    """The collected names a step's text uses: {kites} in a write, a type or a fill's values."""
    texts = [step.get("text")] if step.get("tool") in ("write", "type") else []
    if step.get("tool") == "fill":
        texts = [v for v in step.get("fields", {}).values() if isinstance(v, str)]
    return {m.lower() for t in texts if isinstance(t, str) for m in SLOT_REF.findall(t)}


def slot_texts(step: dict) -> list[str]:
    """The text a step types (a write, type or find's text, a fill's values): words an expectation may use."""
    if step.get("tool") == "fill":
        return [str(v) for v in step.get("fields", {}).values() if isinstance(v, str)]
    return [str(step["text"])] if step.get("tool") in ("write", "type", "find") and step.get("text") else []


def check_slots(step: dict, known: set[str], told: bool) -> str | None:
    """Across steps: a {name} or a tell needs an earlier collect of that name; nothing comes after a tell or a
    delegate."""
    if told:
        return f"{step['tool']}: nothing comes after tell or delegate (each is the last step)"
    # typed at the focus (a terminal, code) braces are often just text: there only a name collected counts
    missing = sorted(slot_refs(step) - known) if step["tool"] != "type" or known else []
    if missing:
        return f"{step['tool']}: {{{missing[0]}}} needs an earlier step: collect <what> | as: {missing[0]}"
    if step["tool"] == "tell" and step["slot"] not in known:
        return f"tell: nothing is collected as {step['slot']!r} before it (collect <what> | as: {step['slot']})"
    unknown = [s for s in step.get("sources", []) if s not in known] if step["tool"] == "compose" else []
    if unknown:
        return f"compose: nothing is collected as {unknown[0]!r} before it (collect <what> | as: {unknown[0]})"
    return None


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def foreign_detail(step: dict, given: str) -> str | None:
    """An email address the step would type that Karta never gave the brain (not in the task, the user profile lines
    or anything else in its prompt). The brain's CLI knows the signed-in account, and that is not Karta's to use."""
    text = " ".join(
        str(v) for k, v in step.items() if k not in ("tool", "then", "expects", "at", "rows") and isinstance(v, (str, dict))
    )
    if {m.lower() for m in EMAIL.findall(text)} - {m.lower() for m in EMAIL.findall(given)}:
        return "an email address nobody gave (not in the task or the user profile): ask the user for it instead"
    return None


def numbered(step: dict, view) -> tuple[dict, str | None]:
    """A page plan's `#n` targets (view.View) as the items they are: the step keeps each item's identity (`at`; a
    collect's `rows`) and its name as the view showed it (never hidden content), never the number. (the step, why it
    cannot be: a number not on the screen given)."""

    def item(text) -> tuple[int, str | None]:
        m = NUMBER.match(str(text).strip())
        if not m:
            return 0, None
        n = int(m[1])
        return n, None if view.item(
            n
        ) is not None else f"{step['tool']}: #{n} is not on the screen given (#1 to #{len(view.items)})"

    out, tool = dict(step), step["tool"]
    key = {"click": "target", "write": "where", "answer": "choice_meaning"}.get(tool)
    if key:
        n, error = item(step.get(key, ""))
        if error or not n:
            return step, error
        out[key], out["at"] = view.labels[n - 1], view_mod.identity(view.item(n), view.screen)
    elif tool == "fill":
        fields, ats = {}, []
        for name, value in step["fields"].items():
            n, error = item(name)
            if error:
                return step, error
            label = view.labels[n - 1] if n else name
            while label in fields:  # two fields shown alike: each keeps its own key
                label += " "
            fields[label] = value
            ats.append(view_mod.identity(view.item(n), view.screen) if n else {})
        if any(ats):
            out["fields"], out["at"] = fields, ats
    elif tool == "collect" and isinstance(step.get("rows"), str):
        nums = [int(n) for n in re.findall(r"#(\d+)", step["rows"])]
        missing = [n for n in nums if view.item(n) is None]
        if not nums or missing:
            return step, f"collect: #{(missing or [0])[0]} is not on the screen given (#1 to #{len(view.items)})"
        out["rows"] = [view_mod.identity(view.item(n), view.screen) for n in dict.fromkeys(nums)]
    return out, None


WORD = re.compile(r"[a-z][a-z'-]+")


def _words(text: str) -> set[str]:
    return {w.strip("'-").removesuffix("'s") for w in WORD.findall(str(text).lower())}


def guessed(arg: str, given: str, typed: list[str] = ()) -> str | None:
    """A word inside an expectation's ( ) that is in neither the prompt (the task, the facts, the screen) nor the
    plan's own typing, nor a host: a guess at what the app will show. None when every word has a source (numbers and
    one- or two-letter words are neutral)."""
    if facts_mod.HOST.fullmatch(arg.strip()):
        return None
    pool = _words(given) | _words(" ".join(typed))
    stems = {w.rstrip("s") for w in pool}
    return next((w for w in sorted(_words(arg)) if len(w) > 2 and w not in pool and w.rstrip("s") not in stems), None)


def _same_place(where: str, here: str) -> bool:
    if not where or not here:
        return False
    if facts_mod.HOST.fullmatch(where.strip()) and facts_mod.HOST.fullmatch(here.strip()):
        return facts_mod.site_of(where) == facts_mod.site_of(here)
    a, b = norm_words(where), norm_words(here)
    return bool(a) and (a == b or a in b or b in a)


def norm_words(text: str) -> str:
    return " ".join(str(text).casefold().split())


def typed_step(step: dict, here: str, given: str, typed: list[str]) -> tuple[dict, str | None]:
    """v2: a step's expectation against where the plan is (`here`: the site or app it is in) and what the brain was
    given. page_on is only for opening another site or app: on the place the plan is in already it would be true
    before the step, so it becomes item_opened (a page or row of that place). Words inside ( ) that came from nowhere
    are a guess and are dropped where the type stands without them. (the step, why it changed or None)."""
    got = expect_mod.of(step)
    if got is None or step["tool"] not in ACTING:
        return step, None
    kind, note = got.kind, None
    if kind == "page_on" and _same_place(got.arg, here):
        kind, note = "item_opened", f"{step['tool']}: page_on({got.arg}) is where the plan is already: item_opened instead"
        got = expect_mod.Expect(kind)
    word = guessed(got.arg, given, typed) if got.arg and kind != "page_on" else None
    if word and EXPECT_TYPES[kind] is None:
        note = f"{step['tool']}: {got} guesses {word!r} (not the task's, typed or on screen): {kind} without words"
        got = expect_mod.Expect(kind)
    if note is None:
        return step, None
    out = {**step, "expects": str(got)}
    if step["tool"] == "submit":
        out["expect"] = got.arg
    return out, note


def typed_done(text: str, given: str, typed: list[str]) -> tuple[str, str | None]:
    """v2: the done line is an expectation of the list (never none): (the done, why it was changed or refused)."""
    got, error = expect_mod.parse(text)
    if got is None:
        return "", f"done: {error}"
    if got.kind == "none":
        return "", "done: none proves nothing: the expectation that holds once the whole task is done"
    word = guessed(got.arg, given, typed) if got.arg and got.kind != "page_on" else None
    if word and EXPECT_TYPES[got.kind] is None:
        return got.kind, f"done: {got} guesses {word!r}: {got.kind} without words"
    return str(got), None


def typed_contract(text: str, given: str, typed: list[str]) -> tuple[str, str | None]:
    """The plan's contract line (contract.py): (the contract as code writes it, why it was changed or refused). A
    thing's name inside ( ) that is in neither the prompt nor the plan's typing is a guess: dropped, the kind stands."""
    got, error = contract_mod.parse(text)
    if got is None:
        return "", error
    word = guessed(got.name, given, typed) if got.kind in ("created", "deleted", "opened") and got.name else None
    if word and got.kind == "opened":  # what a view shows, in the task's own words: the guessed ones dropped
        kept = " ".join(w for w in got.name.split() if not guessed(w, given, typed))
        return str(replace(got, name=kept)), f"contract: {got} guesses {word!r}: {replace(got, name=kept)} instead"
    if word:  # a made thing's name is whole or none
        return str(replace(got, name="")), f"contract: {got} guesses {word!r}: {got.kind} without the name instead"
    return str(got), None


def parse_plan(reply: object, v2: bool | None = None) -> Plan:
    """A JSON reply (the fallback format)."""
    if not isinstance(reply, dict):
        return Plan([], errors=["the reply is not a JSON object"])
    raw = reply.get("steps")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return Plan([], errors=["'steps' must be a list of tool calls"])
    steps, errors = [], []
    for n, step in enumerate(raw, 1):
        clean, error = check_step(step, v2)
        if error:
            errors.append(f"step {n}: {error}")
        else:
            steps.append(clean)
    text = {k: str(reply.get(k) or "").strip() for k in PLAN_TEXT}
    if text["contract"]:
        got, error = contract_mod.parse(text["contract"])
        text["contract"] = str(got) if got is not None else ""
        errors += [error] if error else []
    return Plan(steps, errors=errors, **text)


def _value(text: str) -> str | bool:
    low = text.strip().lower()
    return True if low in ON else False if low in OFF else text.strip()


def _prose(tool: str, text: str, stray: bool) -> str | None:
    """Why a v2 line of a known tool does not match its grammar (and so is prose), or None: a name is a few words,
    never a sentence; a `|` part is one of its keys; a submit takes no words but its expectation; back takes none,
    scroll a direction, wait a number, reach an amount."""
    bare = text.strip().strip("\"'").strip()
    if stray and tool not in KEYED:
        return f"{tool}: a | part that is none of its keys"
    if tool == "submit":
        said = bare[len("expect:") :].strip() if bare.lower().startswith("expect:") else bare
        return None if not said or not parse_expect(said)[2] else "submit: words that are no expectation"
    if tool == "back" and bare:
        return "back: takes no words"
    if tool == "scroll" and bare.lower() not in ("", "down", "up"):
        return "scroll: only down or up"
    if tool == "wait" and bare and not WAIT_TEXT.match(bare):
        return "wait: only a number of seconds"
    if tool == "reach" and not re.search(r"\d", bare):
        return "reach: an amount has a number"
    if tool in NAMED and not NUMBER.match(bare) and (len(bare.split()) > NAME_WORDS or SENTENCE_END.search(bare)):
        return f"{tool}: a sentence, not a name"
    if tool == "collect" and SENTENCE_END.search(bare):
        return "collect: a sentence"
    return None


def parse_line(line: str, v2: bool | None = None) -> tuple[str, object]:
    """One line of a plan: ("step", tool call), ("meta", (field, text)), ("error", why), ("prose", why) or ("skip",
    None). Under v2 a line is a step only when it matches the step grammar exactly (a tool's name in lower case, its
    main text, its own `|` keys: `_prose`); anything else is prose, never a step.

    `<tool> <main text> | <key>: <value> | ...`. A `|` part that is not one of the tool's keys belongs to the main text
    (a shell pipe in `type ls | grep x`); `\\n` in typed text is a line break; `click: Save` reads as `click Save`.
    `v2` (default: the prompt in use): `| expect: <type>` on any step is its expectation (`expects`); on a submit, an
    expect that is not one of EXPECT_TYPES stays its words."""
    v2 = prompt_version() == "v2" if v2 is None else v2
    s = line.strip()
    if not s or s.startswith("```") or s.startswith("#"):
        return "skip", None
    s = BULLET.sub("", s.strip("`").strip()).strip()
    if s.lower().rstrip(".") == "look":  # a page plan's last line: look again once these steps ran
        return "meta", ("look", "look")
    if s.lower().rstrip(".") == "continue":  # a look's last line: the plan's steps still to do carry on
        return "meta", ("carry", "continue")
    head = HEAD.match(s)
    if not head:
        return (PROSE if v2 else "error"), f"not a tool line: {line.strip()[:80]!r}"
    word, sep, rest = head[1].lower(), head[2], head[3].strip()
    if word in META and ":" in sep:
        return "meta", (META[word], rest.strip("\"'").strip())
    word = word.replace("-", "_")
    if word not in LINE_FIELDS:
        return (PROSE if v2 else "error"), f"unknown tool {word!r} in {line.strip()[:80]!r} (tools: {', '.join(TOOLS)})"
    if v2 and head[1] != head[1].lower():  # "Submit the form once…": a sentence, never a tool call
        return PROSE, f"a sentence, not a tool call (a tool is written in lower case): {line.strip()[:80]!r}"
    main_field, keys = LINE_FIELDS[word]
    if v2 and word != "submit":
        keys = {**keys, "expect": "expects", "expects": "expects"}
    step: dict = {"tool": word}
    parts = rest.split("|") if rest else []
    if word == "fill":
        fields: dict[str, str | bool] = {}
        for part in parts:
            if re.fullmatch(r"\s*(?:" + NUMBERED_FILL.pattern + r"\s*)+", part):  # fill #3:"Ana" #4:"Lee"
                for m in NUMBERED_FILL.finditer(part):
                    fields[f"#{m[1]}"] = _value(m[2] if m[2] is not None else m[3])
                continue
            kv = part.split(":", 1)
            if len(kv) != 2 or not kv[0].strip():
                return "error", f"fill: {part.strip()!r} is not '<field>: <value>'"
            if kv[0].strip().lower() in ("then", "checkpoint"):
                step["then"] = kv[1].strip()
                continue
            if v2 and kv[0].strip().lower() in ("expect", "expects"):
                step["expects"] = kv[1].strip()
                continue
            fields[kv[0].strip()] = _value(kv[1])
        step["fields"] = fields
    else:
        main: list[str] = []
        stray = False  # a `|` part that is none of the tool's keys
        for n, part in enumerate(parts):
            kv = KEY.match(part) if (n or not main_field) else None
            key = kv[1].lower() if kv else ""
            if kv and key in keys:
                step[keys[key]] = kv[2].strip()
            else:
                main.append(part)
                stray = stray or bool(n and part.strip())
        text = "|".join(main).strip()
        why = _prose(word, text, stray) if v2 else None
        if why:
            return PROSE, f"{why}, not a tool call: {line.strip()[:80]!r}"
        if word == "write" and "where" not in step and NUMBER.match(text):  # write #7 "text"
            step["where"], text = NUMBER.match(text)[0], text[NUMBER.match(text).end() :].strip()
            text = text[1:-1] if len(text) > 1 and text[0] == text[-1] == '"' else text
        if word == "collect" and text.startswith("#"):  # collect #11, #14 | what: ... : those items
            step["rows"], text = text, ""
            step.setdefault("what", "the items chosen")
        text = text.replace("\\n", "\n") if word in ("write", "type") else text.strip("\"'")
        if word == "submit" and text.lower().startswith("expect:"):
            text = text[len("expect:") :].strip()
        if v2 and word == "submit":
            said = step.pop("expect", None) if main_field == "expect" and "expect" in step else None
            for words in (said, text if text else None):
                if words is not None and not parse_expect(words)[2]:
                    step["expects"] = words
                elif words is not None:
                    step["expect"] = words
            text = ""
        if main_field and text:
            step.setdefault(main_field, text)
        if "alternatives" in step:
            step["alternatives"] = [a.strip().strip("\"'") for a in str(step["alternatives"]).split(",") if a.strip()]
        if word == "wait" and "seconds" in step:
            number = re.search(r"\d+(\.\d+)?", str(step["seconds"]))
            step["seconds"] = float(number[0]) if number else 2.0
        if word == "scroll":
            step["direction"] = (str(step.get("direction") or "down").split() or ["down"])[0].lower()
        if word == "collect" and "count" in step:
            number = re.search(r"\d+", str(step["count"]))
            step["count"] = int(number[0]) if number else step["count"]
    clean, error = check_step(step, v2)
    return ("error", error) if error else ("step", clean)


def _json_object(text: str) -> dict | None:
    start = text.find("{")
    decoder = json.JSONDecoder()
    while start >= 0:
        try:
            obj, _ = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return None


def screen_text(screen: Screen, n: int = SCREEN_LINES) -> str:
    if screen.withheld:  # mail or messages without the user's yes: its structure only (view.Structure)
        hide = view_mod.Structure(screen)
        lines = [f"- {hide.name(i)!r} {i.kind}" for i in screen.page()[:n] if not i.offscreen]
        head = f"Front app: {screen.app}" + (f" ({facts_mod.host_of(screen.url)})" if screen.url else "")
        head += f"\n{view_mod.WITHHELD_NOTE} {hide.count} rows."
    else:
        lines = [f"- {i.describe()}" for i in screen.page()[:n]]
        head = (
            f"Front app: {screen.app}"
            + (f" ({screen.url})" if screen.url else "")
            + (f", window {screen.window!r}" if screen.window else "")
        )
    return head + ("\nScreen:\n" + "\n".join(lines) if lines else "\nScreen: (nothing readable)")


def screen_digest(screen: Screen) -> str:
    """The screen for the v2 plan prompt: its exact names grouped by what they are (what is focused, a popup, the
    fields with their text, the controls, the rows, the text), so the brain can name what is there and knows the rest
    is unseen."""
    page = content_mod.classify(screen).page()  # rows and text: the content model's content (content.py)
    hide = view_mod.Structure(screen) if screen.withheld else None  # mail or messages without a yes: structure only
    head = f"Front app: {screen.app}" + (f", profile {screen.profile}" if screen.profile else "")
    if hide is not None:
        page = [i for i in page if not i.offscreen]
        head += (f"\nSite: {facts_mod.host_of(screen.url)}" if screen.url else "") + (
            "\nWindow: [its title]" if screen.window else ""
        )
        head += f"\n{view_mod.WITHHELD_NOTE}"
    else:
        head += (f"\nAddress: {screen.url}" if screen.url else "") + (f"\nWindow: {screen.window!r}" if screen.window else "")

    def name(i: Item) -> str:
        if hide is not None:
            shown = hide.name(i)
            bits = [hide.value(i)] if hide.value(i) else []
            bits += ["disabled"] if not i.enabled else []
            bits += ["checked" if i.checked else "unchecked"] if i.checked is not None else []
            return (shown if shown.startswith(("row ", "[")) else repr(shown)) + (f" ({', '.join(bits)})" if bits else "")
        bits = [f"has {i.value!r}" if i.kind == "field" else i.value] if i.value else []
        bits += ["disabled"] if not i.enabled else []
        bits += ["checked" if i.checked else "unchecked"] if i.checked is not None else []
        return repr(i.name) + (f" ({', '.join(bits)})" if bits else "")

    groups = [
        ("Focused", [i for i in page if i.focused], 2),
        ("In a popup", [i for i in page if i.dialog], 10),
        ("Fields", [i for i in page if i.kind == "field" and not i.dialog], 10),
        ("Controls", [i for i in page if i.kind in ("button", "link", "tab", "checkbox", "menu") and not i.dialog], 24),
        ("Rows", [i for i in page if i.kind == "row" and not i.dialog and not content_mod.interface(i)], 10),
        ("Text", [i for i in page if i.kind in TEXT_KINDS and not i.dialog and not content_mod.interface(i)], 10),
        ("Interface", [i for i in page if i.kind in ("row", *TEXT_KINDS) and not i.dialog and content_mod.interface(i)], 10),
    ]
    lines = [f"{label}: " + (f"{len(items)}: " if hide is not None and label == "Rows" else "")
             + ", ".join(name(i) for i in items[:cap]) + (f" (+{len(items) - cap} more)" if len(items) > cap else "")
             for label, items, cap in groups if items]  # fmt: skip
    body = "\n".join(lines) if lines else "(nothing readable)"
    return f"The screen now (exact names; anything not listed is not seen yet):\n{head}\n{body}"


def step_key(step: dict) -> str:
    return json.dumps(step, sort_keys=True, ensure_ascii=False)


def step_line(step: dict) -> str:
    """A tool call written back as its plan line (for traces and the brain's context)."""
    tool = step.get("tool", "?")
    typed = [f"expect: {step['expects']}"] if step.get("expects") else []  # v2: the kind of change it causes
    if tool == "fill":
        return "fill " + " | ".join(
            [
                *(f"{k}: {('on' if v else 'off') if isinstance(v, bool) else v}" for k, v in step.get("fields", {}).items()),
                *([f"then: {step['then']}"] if step.get("then") else []),
                *typed,
            ]
        )
    main_field, keys = LINE_FIELDS.get(tool, (None, {}))
    if tool == "submit" and typed:  # its words are the expectation's own argument
        main_field = None
    main = str(step.get(main_field, "")).replace("\n", "\\n") if main_field else ""
    names: dict[str, str] = {}
    for key, name in keys.items():
        names.setdefault(name, key)
    extra = [
        f"{key}: {', '.join(step[name]) if isinstance(step[name], list) else step[name]}"
        for name, key in names.items()
        if name != main_field and step.get(name) not in (None, "", []) and not (typed and name == "expect")
    ]
    return " | ".join([f"{tool} {main}".strip(), *extra, *typed])


# ---------------------------------------------------------------- the brain's slice of the Mac


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


def names_folder(task: str, folder: str) -> bool:
    """The task names `folder` as a place, not merely its word: "the karta folder", "my karta project", "in karta",
    "cd ~/Code/karta", a path ending in it. "a note called Karta test" does not."""
    name = re.escape(folder.rstrip("/").rsplit("/", 1)[-1].lower())
    text = task.lower()
    if re.search(rf"[~/]\S*{name}\b", text):  # a path to it
        return True
    return bool(
        re.search(
            rf"\b{name}\s+(folder|project|repo|repository|directory|dir)\b"
            rf"|\b(in|into|inside|from|to|under|at|cd)\s+(the\s+|my\s+)?{name}\b(?!\s+(test|note|called|named))",
            text,
        )
    )


def brain_facts(task: str, facts: MacFacts, tabs: list | None = None, cap: int = PLACE_LINES) -> str:
    """What the brain needs of this Mac for `task`, and nothing more: the date, the places the task names (its apps;
    a site it names, with the profiles to try and any tab already open), the folders it names, and the browsers when
    no site is known (for a site the brain names itself). One function, easy to replace."""
    lines = [f"Now: {facts.now:%A %d %B %Y, %H:%M}"]
    site = next((m[0] for m in facts_mod.HOST.finditer(task)), None)  # a site the task names by its host
    places = facts_mod.places_for(task, task, facts, None, site, tabs)
    apps = [p.app for p in places if not p.url]
    sites: dict[str, dict[str, list[str]]] = {}
    for p in places:
        if p.url:
            where = p.app + (f" {p.profile}" if p.profile else "")
            sites.setdefault(_host(p.url), {"open": [], "can": []})["open" if p.tab else "can"].append(where)
    out = [f"{app}: installed app" for app in dict.fromkeys(apps)]
    for host, where in sites.items():
        parts = ([f"open now in {', '.join(dict.fromkeys(where['open']))}"] if where["open"] else []) + (
            [f"can open in {', '.join(dict.fromkeys(where['can']))}"] if where["can"] else []
        )
        out.append(f"{host}: {'; '.join(parts)}")
    lines += out[:cap]
    folders = [f for f in facts.folders if names_folder(task, f)]
    if folders:
        lines.append("Folders the task names: " + ", ".join(folders[:3]))
    if not sites and facts.browsers:
        lines.append(
            "Browsers: " + "; ".join(f"{b} (profiles {', '.join(p)})" if p else b for b, p in list(facts.browsers.items())[:3])
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- reading replies


class Streamed:
    """A plan arriving line by line: iterate it for its steps as each line completes; `plan` fills in as they arrive
    (done_shows, ask, errors). A reply with no valid step and errors gets one repair ask, streamed the same way."""

    def __init__(self, hb: HarnessBrain, system: str, user: str, known=(), use=None, here: str = "", kind: str = "plan",
                 view=None, model: str | None = None):  # fmt: skip
        self.plan = Plan([])
        self.finished = False
        self.known = tuple(known)
        self.use = use  # binds a `use:` line to a pinned task's steps (recipes.RecipeBook.bind_use), when any is offered
        self.here = here  # v2: the site or app in front when the plan was asked for
        self.kind, self.view, self.model = kind, view, model  # a page plan: its view (#n targets) and its model
        self._it = self._run(hb, system, user)

    def __iter__(self) -> Iterator[dict]:
        return self._it

    def _run(self, hb: HarnessBrain, system: str, user: str) -> Iterator[dict]:
        count = 0
        for step in hb._stream(system, user, self.plan, self.kind, self.known, self.use, self.here, self.view, self.model):
            count += 1
            yield step
        ended = any(getattr(self.plan, k) for k in ("carry", "look", "done_shows", "stop", "skip", "ask"))
        said = any(getattr(self.plan, k) for k in ("carry", "look", "stop", "skip", "ask"))  # no steps, on purpose
        prose = self.plan.prose and not said  # a reply whose only "steps" were prose has no steps
        if not count and (self.plan.errors or prose) and not (self.kind == "look" and ended):  # a look may end with no step
            errors = list(self.plan.errors) + [f"{p} (write each step as a tool call, never a sentence)" for p in self.plan.prose]
            fixed = Plan([])
            again = user + _rejected(errors, hb.lines)
            for step in hb._stream(system, again, fixed, "repair", self.known, self.use, self.here, self.view, self.model):
                self.plan.steps.append(step)
                yield step
            for key in PLAN_TEXT:
                setattr(self.plan, key, getattr(fixed, key) or getattr(self.plan, key))
            self.plan.pinned = fixed.pinned or self.plan.pinned
            self.plan.errors = list(self.plan.errors) + fixed.errors
            self.plan.prose += fixed.prose
        self.finished = True

    def close(self) -> None:
        self._it.close()


def _rejected(errors: list[str], lines: bool) -> str:
    again = (
        "Reply again, one tool call per line in the format given, using only the tools and keys listed."
        if lines
        else "Reply again with only valid JSON, using only the tools and fields listed."
    )
    return "\n\nYour last reply was rejected:\n- " + "\n- ".join(errors) + "\n" + again


def _merge(plan: Plan, got: Plan) -> None:
    plan.errors.extend(got.errors)
    for key in PLAN_TEXT:
        if getattr(got, key):
            setattr(plan, key, getattr(got, key))


class HarnessBrain:
    """Wraps any brain: one with `.stream_lines(system, user)` gets the line format, streamed; one with only
    `.json(system, user) -> dict` gets the JSON prompt. Calls are counted here, one per request (a stream closed
    early still counts); brain time is the time spent waiting for its reply."""

    def __init__(self, brain, prompt: str | None = None, plan: str | None = None):
        self.brain = brain
        self.lines = callable(getattr(brain, "stream_lines", None))
        self.prompt = prompt_version(prompt)  # the plan prompt: v1, or v2 (typed expectations, lines only)
        self.v2 = self.prompt == "v2" and self.lines  # a brain with only JSON gets (and answers) the v1 prompt
        self.mode = plan_mode(plan) if self.v2 else "blind"  # KARTA_PLAN; the v1 or JSON format plans blind
        self.page = self.mode == "page"  # plan per page from a numbered view
        self.auto = self.mode == "auto"  # plan blind once; a miss, or a place the plan never named, gets a look
        self.page_model = os.environ.get("KARTA_PAGE_MODEL") or PAGE_MODEL
        self.seconds = 0.0
        self.requests = 0
        self.given = ""  # email addresses Karta gave outside the prompt (a session ref's: run_task sets it per task)
        self.stats: list[dict] = []  # per request: kind, format, first_step_s, total_s, output_tokens

    @property
    def calls(self) -> int:
        return self.requests

    def _source(self, system: str, user: str, model: str | None = None) -> Iterator[str | dict]:
        """The reply's lines. A streamed reply is read by a thread of its own into a queue, so the brain's stream
        keeps pace (and its timeout keeps running on the brain alone) while the harness runs the first tools."""
        if not self.lines:
            yield self.brain.json(system, user)
            return
        lines: queue.Queue[tuple[str, object]] = queue.Queue()

        def pump() -> None:
            try:
                more = {"model": model} if model and _takes_model(self.brain.stream_lines) else {}
                for line in self.brain.stream_lines(system, user, **more):
                    lines.put(("line", line))
            except Exception as error:
                lines.put(("error", error))
            finally:
                lines.put(("end", None))

        threading.Thread(target=pump, daemon=True).start()
        while True:
            kind, value = lines.get()
            if kind == "end":
                return
            if kind == "error":
                raise value  # type: ignore[misc]
            yield value  # type: ignore[misc]

    def _stream(
        self, system: str, user: str, plan: Plan, kind: str, known=(), use=None, here: str = "", view=None, model=None
    ) -> Iterator[dict]:
        """The reply's steps as their lines complete, filling `plan` (steps, done_shows, ask, stop, learned, errors).
        `known`: the names already collected; a step using a name no earlier step collects is an error, as is any
        step after a tell. `use(text) -> (hit, error)`: binds a `use:` line to a pinned task, whose steps are then the
        plan's (a `use:` line is the whole reply: lines after it are not read). v2: each expectation is checked
        against `here` (the site or app the plan is in, followed through its open_place steps) and the prompt
        (typed_step), and the done line must be an expectation (typed_done). `view` (a page plan): its `#n` targets
        become the items they are (numbered); the lines after its `look` are not read."""
        self.requests += 1
        slots, told = set(known), False
        typed: list[str] = []
        stat: dict = {"kind": kind, "format": "lines" if self.lines else "json", "first_step_s": None}
        self.stats.append(stat)
        source = self._source(system, user, model)
        json_text: str | None = None
        waited = 0.0

        def take(step: dict) -> list[dict]:
            nonlocal told, here
            error = check_slots(step, slots, told) or foreign_detail(step, user + "\n" + self.given)
            if not error and view is not None:
                step, error = numbered(step, view)
            if error:
                plan.errors.append(error)
                return []
            if self.v2:
                step, note = typed_step(step, here, user, typed)
                if note:
                    plan.errors.append(note)
                if step["tool"] == "open_place":
                    here = str(step.get("site") or step.get("what") or here)
                elif str(step.get("expects", "")).startswith("page_on("):
                    here = expect_mod.of(step).arg
                typed.extend(slot_texts(step))
            if step["tool"] in ("collect", "compose"):
                slots.add(step["slot"])
            told = told or step["tool"] in ("tell", "delegate")
            plan.steps.append(step)
            stat["first_step_s"] = stat["first_step_s"] if stat["first_step_s"] is not None else round(waited, 2)
            return [step]

        def take_use(text: str) -> list[dict]:
            if plan.steps:
                plan.errors.append("use: is the whole reply, never after steps")
                return []
            hit, error = use(text) if use is not None else (None, "use: no pinned task is offered here; plan the task")
            if error or hit is None:
                plan.errors.append(error or "use: could not be read")
                return []
            plan.pinned, plan.done_shows, plan.contract = hit, hit.done_shows, hit.contract
            return [s for step in hit.steps for s in take(step)]

        while True:
            t = time.perf_counter()
            try:
                piece = next(source)
            except StopIteration:
                waited += time.perf_counter() - t
                break
            except Exception as error:  # a reply that cannot be read: the repair ask gets a turn
                waited += time.perf_counter() - t
                plan.errors.append(f"the reply could not be read: {str(error)[:200]}")
                break
            waited += time.perf_counter() - t
            if plan.pinned is not None or plan.look or plan.carry:
                continue  # a pinned task's steps are the plan; a page plan ends at its look or continue: the rest is not read
            if isinstance(piece, dict):  # a JSON-only brain's whole reply
                got = parse_plan(piece, self.v2)
                _merge(plan, got)
                if got.use:
                    yield from take_use(got.use)
                    continue
                for step in got.steps:
                    yield from take(step)
                continue
            text = str(piece)
            if json_text is not None:
                json_text += "\n" + text
                continue
            bare = text.strip()
            if bare.startswith("{") or bare.lower() in ("```json", "```json{") or bare.startswith("```{"):
                json_text = text  # the JSON fallback: read whole once the reply ends
                continue
            what, value = parse_line(text, self.v2)
            if what == "step":
                yield from take(value)
            elif what == "meta" and value[0] == "use":
                yield from take_use(value[1])
            elif what == "meta" and value[0] == "contract" and value[1]:
                kept, note = typed_contract(value[1], user, typed)
                plan.contract = plan.contract or kept
                if note:
                    plan.errors.append(note)
            elif what == "meta" and value[0] == "done_shows" and value[1] and self.v2:
                done, note = typed_done(value[1], user, typed)
                plan.done_shows = done or plan.done_shows
                if note:
                    plan.errors.append(note)
            elif what == "meta" and value[1]:
                setattr(plan, value[0], value[1])
            elif what == "error":
                plan.errors.append(str(value))
            elif what == PROSE:
                plan.prose.append(str(value))
        self.seconds += waited
        stat["total_s"] = round(waited, 2)
        usage = getattr(self.brain, "last_usage", None)
        if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
            stat["output_tokens"] = usage["completion_tokens"]
        if json_text is not None:
            obj = _json_object(json_text)
            if obj is None:
                plan.errors.append("the reply is neither tool lines nor valid JSON")
            elif plan.pinned is None:
                got = parse_plan(obj, self.v2)
                _merge(plan, got)
                if got.use:
                    yield from take_use(got.use)
                else:
                    for step in got.steps:
                        yield from take(step)

    def _collect(self, system: str, user: str, kind: str, known=(), here: str = "", view=None, model=None) -> Plan:
        plan = Plan([])
        for _ in self._stream(system, user, plan, kind, known, here=here, view=view, model=model):
            pass
        return plan

    def _ask(self, system: str, user: str, kind: str, known=(), here: str = "", view=None, model=None) -> Plan:
        """A whole reply; one repair ask when any part is wrong (the valid steps stand if the repair is no better).
        A v2 expectation the parser changed (typed_step) is noted, not wrong: no repair ask for it alone."""
        plan = self._collect(system, user, kind, known, here, view, model)
        said = plan.steps or any(getattr(plan, k) for k in ("carry", "look", "stop", "skip", "ask"))
        prose = [f"{p} (write each step as a tool call, never a sentence)" for p in plan.prose] if not said else []
        if not [e for e in plan.errors if not changed_only(e)] and not prose:
            return plan
        repaired = self._collect(system, user + _rejected(plan.errors + prose, self.lines), "repair", known, here, view, model)
        return repaired if repaired.steps or not plan.steps else plan

    def _systems(self) -> tuple[str, str]:
        if self.lines and self.v2:
            return PLAN_SYS_V2, RESCUE_SYS_V2
        return (PLAN_SYS, RESCUE_SYS) if self.lines else (PLAN_SYS_JSON, RESCUE_SYS_JSON)

    def stream_plan(
        self, task: str, facts: MacFacts | str, screen: Screen, know_how=(), notes=(), pinned=(), use=None, known=()
    ) -> Streamed:
        """The plan as it streams; the loop runs each step as soon as its line is complete. `pinned`: (id, template)
        of the pinned tasks the brain may reuse with a `use:` line, bound by `use(text) -> (hit, error)`. `known`: the
        names the plan may use before collecting them (the session's @n refs the prompt lists)."""
        user = _context(task, facts, screen, know_how, notes, self.v2) + _pinned(pinned, self.lines)
        return Streamed(self, self._systems()[0], user, known, use=use if pinned else None, here=place_of(screen))

    def plan(self, task: str, facts: MacFacts | str, screen: Screen, know_how=(), notes=()) -> Plan:
        user = _context(task, facts, screen, know_how, notes, self.v2)
        return self._ask(self._systems()[0], user, "plan", here=place_of(screen))

    def rescue(
        self,
        task: str,
        facts: MacFacts | str,
        screen: Screen,
        know_how,
        done: list[tuple[dict, ToolResult]],
        failed: dict,
        result: ToolResult,
        places: dict[str, str],
        tried: list[list[dict]],
        notes=(),
        rest: list[dict] = (),
        collected: dict[str, int] | None = None,
        local: list[str] = (),
    ) -> Plan:
        """Checkpoints: the brain fixes the failed step only; `rest` (the steps still to do) is kept and carried on
        with, unless the reply says replan. `collected`: name -> how many items a collect kept so far (its steps may use
        those names). `local`: the local repairs already tried on this step that missed (repair.py)."""

        def show(step: dict) -> str:
            if step.get("tool") not in TOOLS:
                return str(step.get("tool"))
            return step_line(step) if self.lines else json.dumps(step, ensure_ascii=False)

        user = (
            _context(task, facts, screen, know_how, notes, self.v2)
            + "\nDone:"
            + ("".join(f"\n- {show(s)}  -> {r.detail or r.outcome.value}" for s, r in done) if done else " (nothing yet)")
            + f"\nFailed: {show(failed)}\nReason: {result.outcome.value}"
            + (f" ({result.detail})" if result.detail else "")
            + "\nStill to do after it:"
            + ("".join(f"\n- {show(s)}" for s in rest) if rest else " (nothing: it was the last step)")
            + "\nPlaces found: "
            + ("; ".join(f"{w} -> {p}" for w, p in places.items()) or "(none)")
            + ("\nFixes already tried:" + "".join("\n- " + " ; ".join(show(s) for s in t) for t in tried[-4:]) if tried else "")
            + (
                "\nTried locally on this step (code and Laya), and missed:" + "".join(f"\n- {x}" for x in local[-6:])
                if local
                else ""
            )
            + (
                "\nCollected so far: "
                + ", ".join(f"{{{name}}} ({n} item{'s' if n != 1 else ''})" for name, n in collected.items())
                if collected
                else ""
            )
        )
        return self._ask(self._systems()[1], user, "rescue", tuple(collected or ()), place_of(screen))

    def page_plan(
        self,
        task: str,
        facts: MacFacts | str,
        view,
        know_how=(),
        notes=(),
        done: list[str] = (),
        collected: dict[str, int] | None = None,
        failed: str = "",
        pinned=(),
        use=None,
    ) -> Streamed:
        """One page call (KARTA_PLAN=page), streamed: the steps on the screen `view` (view.View) shows, aimed at its
        items by number, ending with look or done. `done`: the steps done so far, one line each; `collected`: name ->
        count; `failed`: the step that missed on this screen and why (the loop reads that reply whole, a rescue's
        place). `pinned`/`use`: as stream_plan's, offered on a task's first call."""
        user = _page_context(task, facts, view, know_how, notes, done, collected, failed) + _pinned(pinned, self.lines)
        here = place_of(view.screen)
        return Streamed(self, PAGE_SYS, user, tuple(collected or ()), use if pinned else None, here, "page", view,
                        self.page_model)  # fmt: skip

    def look(
        self,
        task: str,
        facts: MacFacts | str,
        view,
        know_how=(),
        notes=(),
        done: list[str] = (),
        collected: dict[str, int] | None = None,
        why: str = "",
        rest: list[str] = (),
    ) -> Streamed:
        """A look (KARTA_PLAN=auto), streamed: the screen `view` (view.View) at a step whose check missed, or at a
        place the plan never named. `why`: its "Failed: ..." or "Arrived: ..." lines; `rest`: the plan's steps still to
        do, one line each. The reply's steps are aimed at the view's items by number and end with continue (the rest
        carries on), look, done (they replace the rest) or stop."""
        user = (
            _page_context(task, facts, view, know_how, notes, done, collected)
            .replace("\nThe screen now:\n", "\n" + why.strip() + "\nStill to do after it (the plan):"
                     + ("".join(f"\n- {line}" for line in rest) if rest else " (nothing: it was the last step)")
                     + "\nThe screen now:\n", 1)
        )  # fmt: skip
        here = place_of(view.screen)
        return Streamed(self, LOOK_SYS, user, tuple(collected or ()), None, here, "look", view, self.page_model)

    def end_check(self, task: str, expect: str, screen: Screen, typed=()) -> tuple[bool | None, str]:
        """One yes/no read of the page lines, asked only when code cannot confirm the end state and Laya is
        unsure. (None, why) when the reply cannot be read."""
        user = (
            f"Task: {task}\nExpected on screen once done: {expect}\n"
            + (f"Typed by the assistant itself (not results): {', '.join(repr(t) for t in typed)}\n" if typed else "")
            + screen_text(screen, END_LINES)
        )
        self.requests += 1
        started = time.perf_counter()
        try:
            if self.lines:
                text = " ".join(self.brain.stream_lines(END_SYS, user))
                m = re.search(r"\b(yes|no)\b\s*[:,.\-]?\s*(.*)", text, re.I | re.S)
                if not m:
                    return None, f"unreadable reply {text[:80]!r}"
                return m[1].lower() == "yes", m[2].strip()[:200]
            reply = self.brain.json(END_SYS_JSON, user)
            done = reply.get("done") if isinstance(reply, dict) else None
            why = str(reply.get("why", "")) if isinstance(reply, dict) else ""
            return (done if isinstance(done, bool) else None), why[:200]
        except Exception as error:
            return None, f"the check failed: {str(error)[:120]}"
        finally:
            spent = time.perf_counter() - started
            self.seconds += spent
            self.stats.append({"kind": "end check", "format": "lines" if self.lines else "json", "total_s": round(spent, 2)})


CHANGED = (" instead", " without words")  # the endings of a note typed_step or typed_done leaves: a change, not an error


def changed_only(error: str) -> bool:
    return error.endswith(CHANGED)


def place_of(screen: Screen) -> str:
    """Where a plan starts, for v2's page_on check: the host of the page in front, else the app."""
    return facts_mod.host_of(screen.url) if screen.url else screen.app


def _pinned(pinned, lines: bool) -> str:
    """The pinned tasks the brain may reuse: templates only (never the values they ran with)."""
    if not pinned:
        return ""
    reply = "use: <id> | p1: <value> | p2: <value>" if lines else '{"use": "<id> | p1: <value> | p2: <value>"}'
    return (
        "\nPinned tasks (paths that worked on this Mac; {p1}, {p2} stand for the values that change):\n"
        + "\n".join(f"- {rid}: {template}" for rid, template in pinned)
        + "\nWhen the task is one of these with only those values changed, reply with only this one line instead of "
        f"a plan:\n{reply}\nGive every value, word for word from the task. Otherwise plan as usual."
    )


def _takes_model(fn) -> bool:
    """A brain whose stream_lines takes a model per request (karta.brain.claude_code's warm pool)."""
    import inspect

    try:
        return "model" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _page_context(task, facts, view, know_how=(), notes=(), done=(), collected=None, failed: str = "") -> str:
    """A page call's message: the task, the Mac's slice, memory's block, the session's failures, what is done (one
    line a step), what was collected (names and counts), the step that missed, and the numbered screen."""
    mac = facts if isinstance(facts, str) else brain_facts(task, facts)
    how = "\n" + "\n".join(know_how) if know_how else ""
    notes_text = "\nFailures so far on this task (newest first):\n" + "\n".join(f"- {n}" for n in notes) if notes else ""
    done_text = "\nDone so far:" + ("".join(f"\n- {line}" for line in done) if done else " (nothing yet)")
    kept = (
        "\nCollected so far: " + ", ".join(f"{{{k}}} ({n} item{'s' if n != 1 else ''})" for k, n in collected.items())
        if collected
        else ""
    )
    miss = f"\nFailed: {failed}" if failed else ""
    return f"Task: {task}\n{mac}{how}{notes_text}{done_text}{kept}{miss}\nThe screen now:\n{view.text}"


def _context(task: str, facts: MacFacts | str, screen: Screen, know_how=(), notes=(), v2: bool = False) -> str:
    mac = facts if isinstance(facts, str) else brain_facts(task, facts)
    how = "\n" + "\n".join(know_how) if know_how else ""  # memory's block (loop.know_how), as it renders it
    failed = "\nFailures so far on this task (newest first):\n" + "\n".join(f"- {n}" for n in notes) if notes else ""
    return f"Task: {task}\n{mac}{how}{failed}\n{screen_digest(screen) if v2 else screen_text(screen)}"
