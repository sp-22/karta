# Real-life tasks: the live check

Requests worded the way people actually speak, across a working day. Run them live with the maintainer's go-ahead,
recorded (`--record ~/karta-everyday`).

Safety for test runs:
- Anything sent goes only to the test address (a test address of your own).
- Claude Code runs only in `~/karta-scratch-repo`.
- Every key action is asked up front.
- Clean up afterwards: test notes, reminders, events, drafts, stickies and watches.

For each task: **Done when** is what must be true; **False done** is what must never be reported as done. Record:
- done or not;
- whether the answer was correct (checked by hand);
- brain calls;
- time;
- any key action asked or wrongly done.

## A. One request, several steps (scripted, typed mode)

1. **"Summarize this page for me."** (a long article open in the browser)
   Done when: 3–5 sentences that match the article, spoken.
   False done: a summary of the site's menu or ads; another page.
2. **"What's this email about, and is there anything I need to do?"** (an email open in Mail or Gmail)
   Done when: the gist plus the action items the email really contains, spoken. The consent question was asked once.
   False done: invented deadlines or tasks.
3. **"Read my latest email and draft a short reply saying I'll get back by Friday."**
   Done when: a reply draft to the sender holding that message. Nothing sent.
   False done: a reply to the wrong email; text typed into the subject; any send.
4. **"Find three good articles about remote team rituals and email me the links with one line on each."**
   (to the test address)
   Done when: a sent email with 3 real article links, a line on each, read back to you before sending.
   False done: ad or search-engine links; lines that don't match the articles; sent without reading back.
5. **"What's on my calendar tomorrow, and do I have a gap of an hour before 3?"**
   Done when: tomorrow's events read correctly from Calendar, and the gap answer agrees with them.
   False done: today's events; an invented gap.
6. **"Check the price of AirPods Pro on Amazon and on Flipkart and tell me which is cheaper."**
   Done when: both prices taken from the product's own row or page, and the cheaper one named.
   False done: an accessory's price; one site's price reported as the comparison.
7. **"Make a note called Standup with what's on my calendar today."**
   Done when: a note titled Standup listing today's events as Calendar shows them.
   False done: an empty note; another day's events.
8. **"Take the top 3 Hacker News stories and put them in a reminder list called Reading."**
   Done when: 3 reminders with the real top-3 titles, in that list.
   False done: points or "comments" lines as titles; stories 4–6.
9. **"Look up the weather in Bangalore this weekend and add an all-day event 'Pack umbrella' on Saturday if it's going to rain."**
   Done when: the event is created only if the forecast says rain, and the decision is spoken.
   False done: an event created without rain in the forecast; the wrong day.
10. **"Search my Downloads for the latest PDF and tell me what it's about."**
    Done when: the newest PDF found (by date) and its content summarized.
    False done: an older PDF; a summary of the file name only.
11. **"Open Slack and tell me if anyone mentioned me today."** (web or app, if installed and signed in)
    Done when: mentions read from the mentions view, or an honest "none", with consent asked.
    False done: invented mentions.
12. **"Write a short LinkedIn-style post about what Karta does, but don't post it, just put it in a note."**
    Done when: a note holding the composed text. Nothing posted.
    False done: anything posted; an empty note.

## B. Conversations: follow-ups in one session (voice mode, with the maintainer)

13. "What are the top stories on Hacker News?" → "Summarize the second one." → "Email that to me."
    Done when: the summary is of story 2, and the email (to the test address) holds that summary, read back first.
14. "Find a pasta recipe on any site." → "Add the ingredients to a reminder list called Groceries." → "How long does it take?"
    Done when: the ingredients come from that recipe, and the time answer comes from the same page.
15. "Who emailed me last?" → "Reply to him that I'm on it." (draft only, then "send it" → it reads back and asks)
    Done when: the reply goes to that sender, read back before sending (sent only to the test address in tests).
16. "Open my notes from yesterday." → "Turn them into three bullet points." → "Put that in today's standup note."

## C. Running things in parallel (voice mode)

17. "Add milk and eggs to my Blinkit cart." → while it runs: "What are you doing?"
    Done when: it speaks the job status without stopping the task.
18. Start a long task, then "cancel that".
    Done when: it stops between steps, closes anything it opened, and says "Stopped as you asked."
19. "Pause." → "Resume."
    Done when: the task carries on from where it was, with no repeated step.
20. Say a second task while one runs.
    Done when: it says "I'll do that after the current task" and runs it next.

## D. In the background: watches, schedules, delegation (voice mode)

21. **"Start Claude Code in karta-scratch-repo to add a line to the README saying hello."**
    Done when: it asks up front, opens a new Terminal window running claude, says "needs you" when Claude Code asks
    permission, and "finished" with its last line when done.
    Follow-up: "What did it do?"
22. **"Tell me when the file report.txt appears in Downloads."** (then create it by hand)
    Done when: it speaks within about 15 s to 1 min of the file appearing, and costs at most 1 brain call.
23. **"Tell me when my build finishes."** (a Terminal running `sleep 60; echo BUILD OK`)
    Done when: it says the build finished, and whether it passed, from the output.
24. **"In 5 minutes, remind me to stretch."**
    Done when: it is spoken about 5 minutes later.
25. **"What are you watching?" → "Stop watching Downloads."**
    Done when: it lists the watches, then removes that one.
26. **"Every weekday at 9, tell me what's on my calendar."** (check that it's stored, then cancel it)
    Done when: it's stored, "what's scheduled?" lists it, and "cancel the 9 o'clock" removes it.

## E. Messy, real phrasing (scripted)

27. "uh can you check if I got any mail from the bank this week"
28. "put 'call mom' on my list for tomorrow evening"
29. "whats the time in new york rn"
30. "find me a cheap flight bangalore to delhi next friday and just tell me the cheapest, don't book"
    Done when: the cheapest fare that the site actually showed is spoken, and nothing is booked.
    False done: a booking step; an invented fare.

## Clean up afterwards

The Standup, Karta and LinkedIn notes; the Reading and Groceries lists; the Pack umbrella event; drafts and test
emails; watches and schedules ("what are you watching?", then cancel them); and `~/karta-scratch-repo`.
