# Security

Karta drives a real Mac: it reads the screen, clicks and types. A bug that lets it act without asking, act in the
wrong app, or send screen contents somewhere unexpected is a security issue.

**Report privately:** use GitHub's "Report a vulnerability" (Security tab → Advisories), not a public issue.
Please include the task, what Karta did, and your macOS version. You'll get a reply within a few days.

What Karta promises today:
- Anything hard to undo (send, post, pay, delete, buy, run a command) waits for your yes.
- Karta never types a password.
- The screen is read on your Mac. The planner (Claude, through the Claude Code CLI) receives the task and the current
  screen's items, not your files or the whole map of your Mac.
