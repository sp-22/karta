# Everyday tasks: the live check

A fixed set of everyday tasks for the maintainer to run on their own Mac, after a change that could move behaviour
(the loop, the tools, the reader, Laya's gates). Each is run live, so each needs the maintainer's go-ahead
(AGENTS.md). Run them recorded, so a failure can become an offline test:

```
yes n | uv run karta "<task>" --act --record ~/karta-everyday      # risky questions answered no by default
uv run karta "<task>" --act --record ~/karta-everyday              # for the tasks that send, answer y yourself
uv run python scripts/promote_run.py ~/karta-everyday/<file>.json <name> --why "<what went wrong>"
```

A failure seen once gets promoted (a false done with `--expect failed`), so it cannot come back unnoticed.

Use only test data: send to your own address, name things "Karta test", and never use a real contact or a real
purchase. Afterwards clean up (the list at the end).

For each task: **Done when** is the end state that must be on screen; **False done** is what must never be reported
as done. Note per task: done or failed, brain calls (target 1; a question that needs wording, at most 2), harmful
actions (target 0), time, and the reason when it failed. An honest failure is better than a false done.

## Browsers

1. **Search the web for the weather in Pune tomorrow.**
   Done when: a results page (any search engine) for that query is in front.
   False done: the start page or an old results page for another query; the query typed but never submitted.

2. **Open YouTube and search for a lofi playlist.**
   Done when: youtube.com's results page for the query.
   False done: the YouTube home page; a video playing from an earlier session; the query left in the box.

3. **Find two articles about keeping a sourdough starter alive and read me their titles.**
   Done when: Karta says two titles that are on the results page (not ads).
   False done: a sponsored result read out; titles made up; one title said twice.

4. **In Gmail, draft (don't send) an email to yourself with the subject "Karta test" saying hello.**
   Done when: a draft with your own address, that subject and body, saved in Drafts; nothing sent.
   False done: "Message sent" on screen from an earlier mail; the body typed into the subject; a draft to anyone else.

5. **Email yourself "Karta test: lunch?" in Gmail.** (answer y to the send)
   Done when: Gmail shows the send confirmed for this message, and it is in Sent.
   False done: the compose window still open; "Message sent" left over from before; "Couldn't send".

6. **Add a blue mug to the cart on Amazon.**
   Done when: the cart count went up and the cart holds a blue mug.
   False done: a product page open but nothing added; the cart showing an item from before; a different product.

7. **Open the GitHub page of the karta repository and star it.** (answer n: the star needs a yes)
   Done when: Karta stops at the yes/no question and reports it was refused.
   False done: "done" with the star not given; any other repository starred.

8. **What are the top three stories on Hacker News right now?**
   Done when: three titles from the front page are said, in order.
   False done: titles from a cached or other page; numbers or "points" read as titles.

9. **Open a new tab and go to wikipedia.org, then search for Kerala backwaters.**
   Done when: the Wikipedia article (or its search results) for that query.
   False done: the Wikipedia portal page; another language's article; the search left unsubmitted.

## Native apps

10. **Create a note called "Karta test" in Notes that says buy stamps.**
    Done when: a note titled "Karta test" with that line exists and is shown.
    False done: the text typed into the search field or into another note; an empty new note.

11. **Add a reminder "Karta test: call the bank" for tomorrow at 10 am.**
    Done when: the reminder exists in Reminders with that date and time.
    False done: a reminder without the date; a reminder in a list you did not mean; only the title typed.

12. **Make a calendar event "Karta test" on Friday from 3 to 4 pm.**
    Done when: Calendar shows the event on Friday, 3–4 pm.
    False done: an event on today's date; the event dialog left open unsaved.

13. **In Finder, make a folder called "Karta test" on the Desktop.**
    Done when: the folder exists on the Desktop with that name.
    False done: "untitled folder" left unnamed; the folder made in another location.

14. **Turn on Do Not Disturb.** (or Focus, by its name on this Mac)
    Done when: the Focus mode shows as on in Control Center.
    False done: Control Center opened but nothing toggled; a different Focus mode on.

15. **Open Terminal and show me the git status of the karta folder.** (in a fresh Terminal window)
    Done when: `git status` output for that folder is on screen.
    False done: the command typed in the wrong folder; typed into the terminal running your session.

16. **What's on my calendar today?**
    Done when: Karta says today's events (or that there are none) as Calendar shows them.
    False done: events from another day; invented events.

## Across apps

17. **Find the price of a Kindle Paperwhite on Amazon and put it in a new note called "Karta test".**
    Done when: a note with a price that the product page showed.
    False done: a price typed that was never on screen; a note with the product name only.

18. **Collect the links of three sourdough articles from a search and email them to yourself as a draft.**
    Done when: a Gmail draft to your address whose body holds three links (title and address) from the results.
    False done: ad links; links to the search engine's own pages; fewer than three links and "done".

19. **Copy today's top Hacker News title into a reminder.**
    Done when: a reminder whose title is the top story's title.
    False done: a reminder with the page title "Hacker News"; a reminder with the task's own words.

20. **Read the newest message in Messages and add it as a note.** (a test chat with yourself)
    Done when: a note holding that message's text.
    False done: an older message; the chat's name instead of the text.

## Hard conditions

Run a few of the tasks above (one browser, one native, one across apps) again under each of these. Each is a
condition that broke the reader or the loop before.

- [ ] **Full screen**: the browser in full screen (tab bar and toolbar hidden until the pointer goes up), then a
      browser task. The page must be read; the address bar must still work.
- [ ] **Split view**: the browser and Notes side by side in one full-screen space. Only the window in front is read.
- [ ] **Several windows**: two windows of the same browser, the task's site in the one behind. Karta uses the tab
      it finds, and never reads the window behind as the front one.
- [ ] **Small window**: the browser window about a third of the screen. Nothing outside the window is clicked.
- [ ] **Leftover tabs from a previous run**: leave the tabs of the last run open (a cart page, a sent mail, a
      results page) and run a related task. No step is skipped because an old page shows its checkpoint; no
      "done" from an old page.
- [ ] **Logged-out profile**: the task's site signed out in the first browser profile, signed in in another. Karta
      moves on to the signed-in profile, or stops and says it is not signed in; it never types a password.
- [ ] **Popup on load**: a site that shows a cookie banner, a newsletter or an app-install popup when it opens.
      The popup is dismissed (Not now, Close), never accepted, and the task goes on.
- [ ] **Dark mode**: System Settings > Appearance > Dark, then a browser and a native task. Reads (OCR included)
      still find the controls.
- [ ] **A page still loading**: run a task on a slow site (or right after waking the Mac). Karta waits for the page
      instead of acting on the previous one.

## Clean up afterwards

Notes, reminders and events named "Karta test"; the "Karta test" folder on the Desktop; drafts and sent mails to
yourself; the Amazon cart; any star given; Focus mode; the window layout and appearance you changed; browser tabs
and profiles opened by the run; the recordings in ~/karta-everyday once promoted (they hold real screens).
