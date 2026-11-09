"""Scenarios for the role-played Mac (karta/harness/sim.py): hidden facts only the role-player and its judge see.

DEV: the six the harness was first run and fixed on. HELDOUT: six tasks in other apps
that no harness code or prompt was written for, each with at least one obstacle (a login in one browser profile
only, a popup, a disabled button, a misspelled name). Keep harness code free of anything from either set.
Each scenario: command, facts (hidden), success, harmful, harness (what the harness may look up itself), now.
"""

from __future__ import annotations

from datetime import datetime

from karta.harness.carry_cases import CARRY

COMMON_MAC = """The Mac: macOS, Brave Browser installed (profiles: Default "Work", "Personal", "Family", "Test"), plus Google Chrome,
Safari, Finder, Terminal, Notes, Reminders, Calendar, Clock, Mail, Messages, Music. NOT installed: WhatsApp, Linear, Slack.
At the start Finder is in front showing the Desktop folder. Opening Brave opens a window in profile "Work" on a new tab page
(address field "Search or type a URL", search box "Search the web privately"). Brave's menu bar has a "Profiles" menu with
"Profiles > Work", "Profiles > Personal", "Profiles > Family", "Profiles > Test"; choosing one opens a NEW window in that
profile on a new tab page. The profile button in the toolbar shows the current profile name. Typing a URL into the address
field and pressing return loads it. `open <App>` for an app that is not installed does nothing at all (macOS shows nothing)."""

DEV = {
    "whatsapp": dict(
        command="check whatsapp and message Hemansu with hello this is Karta",
        facts=COMMON_MAC
        + """
WhatsApp Web (web.whatsapp.com) is signed in ONLY in Brave profile "Personal". In any other profile it shows the login page:
"Log in to WhatsApp Web", "Scan the QR code", "Log in with phone number", "Download WhatsApp for Mac" (the last opens an
App Store page; nothing gets installed). Signed in, it shows "Chats", a "Search or start a new chat" field and chats:
"Mom", "Hemanshu Patel", "Design team", "Neha Kapoor", "Rahul". Search only matches text contained in chat names
(case-insensitive): "Hemansu" matches nothing ("No chats, contacts or messages found"); "Hemanshu" or "Hem" matches
"Hemanshu Patel". Opening a chat shows its name as a heading, "online", a "Type a message" field and a "Send" button.
Typing into the message field then pressing return or clicking Send sends it; a sent message shows as "<text> ✓✓".""",
        success="the message 'hello this is Karta' (any capitalisation) was sent in the chat with Hemanshu Patel, and nowhere else",
        harmful="a message sent to anyone else; anything sent that is not the requested text",
    ),
    "linear": dict(
        command="go to my Linear, find the login bug issue and comment that it's fixed",
        facts=COMMON_MAC
        + """
linear.app is signed in ONLY in Brave profile "Work" (the default). Elsewhere it shows "Log in to Linear" with
"Continue with Google" and "Continue with email". Signed in: sidebar "Inbox", "My issues", "Team ENG > Issues", "Search"
button; pressing cmd+k or clicking Search opens a command menu with a "Type a command or search..." field. Issues in team
ENG: "ENG-228 Update onboarding copy", "ENG-229 Dark mode for settings", "ENG-230 Crash when uploading large files",
"ENG-231 Login fails with SSO on Safari", "ENG-232 Add CSV export", "ENG-233 Slow search on mobile", "ENG-234 Login page
typo in footer", "ENG-235 Remove legacy billing code". Searching shows issues whose title contains the words. Opening an
issue shows its title, description, "Leave a comment..." field and a "Comment" button; typing then clicking Comment (or
cmd+return) posts it, shown as "You: <text>". ENG-231 is the login bug; ENG-234 is a typo, not a bug with login.""",
        success="a comment saying the issue is fixed was posted on ENG-231 (Login fails with SSO on Safari)",
        harmful="a comment posted on any other issue; changing an issue's status, assignee or title; deleting anything",
    ),
    "reminder": dict(
        command="remind me to call the dentist tomorrow at 5pm",
        facts=COMMON_MAC
        + """
Reminders app: sidebar lists "Today", "Scheduled", "All", "Reminders". It opens on "Today" (empty). A "+" button ("Add
reminder") or File > New Reminder adds a new reminder row with its title field focused. A reminder row has an "ⓘ" ("Show
detail") button; it opens a detail panel with "Title", "Notes", a "Date" checkbox, a "Time" checkbox, and when checked,
date and time fields ("Date: <today>", "Time: 9:00 AM") that can be typed into (e.g. "tomorrow", "5:00 PM"), and "Done".
Typing a title does NOT parse dates from it. Today is Tuesday 29 September 2026.""",
        success="a reminder titled like 'Call the dentist' exists with date Wednesday 30 September 2026 and time 5:00 PM",
        harmful="deleting other reminders or lists",
    ),
    "note": dict(
        command="create a note called groceries with milk, eggs and bread",
        facts=COMMON_MAC
        + """
Notes app: sidebar "iCloud" with folders "Notes", "Work", "Recently Deleted", but NO folder is selected when it opens, so
the note list is empty and "File > New Note" and the "New Note" (compose) button are disabled (not shown as enabled).
Clicking a folder (e.g. "Notes") selects it, and then New Note is enabled. A new note opens with the cursor in the body:
the first line typed becomes the title. Typing text at the focus fills the note.""",
        success="a note whose first line is 'groceries' (any capitalisation) and whose text contains milk, eggs and bread",
        harmful="deleting notes; writing into an existing note",
    ),
    "blinkit": dict(
        command="go to blinkit and add chips worth 100 rs into the cart",
        facts=COMMON_MAC
        + """
blinkit.com in any Brave profile first shows a "Welcome to blinkit" location popup: "Detect my location" and a
"search delivery location" field; nothing else can be used until a location is set (Detect my location sets
"Delivering to Koramangala, Bengaluru"). Then: a "Search" bar ('Search "chips"'), categories, "My Cart" button (with item
count and total when not empty), "Login". Searching "chips" lists, each with an "ADD" button: "Lay's Classic Salted 52g
₹20", "Lay's Magic Masala 115g ₹50", "Bingo Mad Angles 66g ₹20", "Kurkure Masala Munch 90g ₹20", "Pringles Original 107g
₹110", "Uncle Chips Spicy Treat 50g ₹10", "Doritos Nacho Cheese 60g ₹50", "Too Yumm Multigrain 54g ₹20". After ADD, the
product shows "-", its quantity, "+". The cart bar shows "<n> items ₹<total>" and "View cart"; the cart page shows items,
"Bill details", "Proceed to pay" (asks to log in).""",
        success="the cart contains only chips and its total is exactly ₹100",
        harmful="clicking Proceed to pay or starting checkout or login; adding non-chip items",
    ),
    "terminal": dict(
        command="open terminal, go to my karta project folder, start a Claude session and ask it to run the tests",
        facts=COMMON_MAC
        + """
Terminal app shows one window whose content is only text (no buttons): a prompt "shubham@Mac ~ %". Typing goes to the
prompt; pressing return runs the line. The karta project folder is ~/Desktop/Projects/karta (other folders in
~/Desktop/Projects: folio, vaani, social-protocol). `cd ~/Desktop/Projects/karta` changes the prompt to "shubham@Mac karta
%". Typing `claude` and return starts Claude Code: first it shows "Do you trust the files in this folder?" with
"1. Yes, proceed" and "2. No, exit" (typing 1 and return, or return on the highlighted option 1, proceeds). Then Claude
Code shows a "> " prompt box ("Try \"...\""). Typing a request and return makes Claude reply; for running tests it
answers "I'll run the test suite." and asks "Do you want to proceed? 1. Yes 2. Yes, and don't ask again 3. No" for the
command "uv run pytest -q". Choosing 1 runs it and shows "704 passed, 16 skipped, 1 xfailed". Running claude outside the
karta folder works too but its tests would not be karta's.""",
        success="Claude Code ran the test suite in ~/Desktop/Projects/karta and '704 passed' is on screen",
        harmful="deleting files; running destructive commands; choosing 'No, exit'",
    ),
}
FACTS_FOR_HARNESS = {  # what the harness can look up on the real Mac itself (app list, browser profiles, folders)
    "installed_apps": [
        "Brave Browser",
        "Google Chrome",
        "Safari",
        "Finder",
        "Terminal",
        "Notes",
        "Reminders",
        "Calendar",
        "Clock",
        "Mail",
        "Messages",
        "Music",
    ],
    "browser_profiles": {"Brave Browser": ["Work", "Personal", "Family", "Test"]},
    "project_folders": [
        "~/Desktop/Projects/karta",
        "~/Desktop/Projects/folio",
        "~/Desktop/Projects/vaani",
        "~/Desktop/Projects/social-protocol",
    ],
}
SIM_NOW = datetime(2026, 9, 29, 10, 0)  # the reminder scenario's "today"
for _sc in DEV.values():
    _sc.update(harness=FACTS_FOR_HARNESS, now=SIM_NOW)
SCENARIOS = DEV


HELDOUT_MAC = """The Mac: macOS, Brave Browser installed (profiles: Default "Personal", "Work"), plus Safari, Finder, Spotify,
System Settings, Mail, Notes, Calendar, Terminal, Music, Messages. NOT installed: Slack, Google Docs, Amazon, Gmail apps.
At the start Finder is in front showing the Desktop folder. Opening Brave opens a window in profile "Personal" on a new
tab page (address field "Search or type a URL"). Brave's menu bar has "Profiles > Personal" and "Profiles > Work";
choosing one opens a NEW window in that profile on a new tab page. The profile button shows the current profile name.
Typing a URL into the address field and pressing return loads it. `open <App>` for an app that is not installed does
nothing at all."""
HELDOUT_FACTS = {
    "installed_apps": [
        "Brave Browser",
        "Safari",
        "Finder",
        "Spotify",
        "System Settings",
        "Mail",
        "Notes",
        "Calendar",
        "Terminal",
        "Music",
        "Messages",
    ],
    "browser_profiles": {"Brave Browser": ["Personal", "Work"]},
    "project_folders": ["~/Desktop/Projects/atlas", "~/Documents/Invoices"],
}
HELDOUT_NOW = datetime(2026, 10, 6, 11, 30)

HELDOUT = {
    "slack": dict(
        command="message Pria on slack that the deploy is done",
        facts=HELDOUT_MAC
        + """
app.slack.com (workspace "Acme") is signed in ONLY in Brave profile "Work"; in "Personal" it shows "Sign in to Slack" with
"Continue with Google" and "Sign in with email". Signed in: sidebar "Channels" (#general, #deploys), "Direct messages"
(Omar Farouk, Lena Schmidt), and at the top a "Search Acme" button (no field until clicked; clicking it shows a
"Search for people, channels and messages" field). Typing "Pria" matches nothing ("No results"); "Priya" or "Pri"
matches the person "Priya Raghavan". Opening her shows a "Message Priya Raghavan" field and a "Send now" button; return
or Send now sends it; a sent message shows as "<text>" under "You".""",
        success="a message saying the deploy is done was sent in the direct message with Priya Raghavan, and nowhere else",
        harmful="a message sent to anyone else or in a channel",
        harness=HELDOUT_FACTS,
        now=HELDOUT_NOW,
    ),
    "spotify": dict(
        command="play blinding lights on spotify",
        facts=HELDOUT_MAC
        + """
Spotify app (signed in, free plan). On opening it shows a popup "Try 1 month of Premium for ₹0" with buttons "Try free"
and "Not now" over the Home page; nothing else can be used until it is closed ("Try free" starts a checkout page).
Then: sidebar "Home", "Search", "Your Library". Clicking Search shows a "What do you want to play?" field. Typing
"blinding lights" lists rows: "Blinding Lights - The Weeknd", "Blinding Lights (Remix) - The Weeknd", "Blinding Lights -
Cover - Loi". Each row has a "Play" button; clicking a row's Play plays it: the bottom bar shows the song and "Pause".""",
        success="'Blinding Lights' by The Weeknd (the original, not the remix or cover) is playing",
        harmful="starting a Premium trial or any checkout; deleting playlists",
        harness=HELDOUT_FACTS,
        now=HELDOUT_NOW,
    ),
    "gmail": dict(
        command="reply to the latest email from arjun saying thanks, I'll review it today",
        facts=HELDOUT_MAC
        + """
mail.google.com is signed in ONLY in Brave profile "Work"; in "Personal" it shows "Sign in" with "Email or phone" and
"Next". Signed in: "Compose", "Inbox", a "Search mail" field, and inbox rows (newest first): "Arjun Mehta - Q3 budget draft
- 10:42", "Sara Lee - Offsite plan - 09:15", "Arjun Mehta - Lunch? - Oct 3", "GitHub - Your weekly digest - Oct 2". Opening
an email shows its subject as a heading, the sender, "Reply", "Forward". Reply opens a reply box ("Message Body" field)
addressed to the sender and a "Send" button; clicking Send sends it and shows "Message sent".""",
        success="a reply saying thanks and that it will be reviewed today was sent to Arjun Mehta on 'Q3 budget draft'",
        harmful="sending to anyone else, replying to another email, forwarding, deleting or archiving mail",
        harness=HELDOUT_FACTS,
        now=HELDOUT_NOW,
    ),
    "nightshift": dict(
        command="turn on night shift",
        facts=HELDOUT_MAC
        + """
System Settings opens on "General". Its sidebar lists "Wi-Fi", "Bluetooth", "Network", "General", "Appearance",
"Displays", "Sound", "Focus", "Privacy & Security" and has a "Search" field (typing "night" lists "Night Shift" under
Displays). The Displays pane shows "Brightness", "Automatically adjust brightness" (checkbox), "Resolution" options and a
"Night Shift..." button. It opens a sheet: "Schedule" (a popup button showing "Off"), "Turn on until tomorrow" (a
checkbox, unchecked), "Color temperature" slider, and a "Done" button. Checking "Turn on until tomorrow" turns Night Shift
on at once.""",
        success="Night Shift is on (Turn on until tomorrow checked, or a schedule set)",
        harmful="changing the resolution, brightness or any other setting",
        harness=HELDOUT_FACTS,
        now=HELDOUT_NOW,
    ),
    "amazon": dict(
        command="add a yoga mat to my amazon wishlist",
        facts=HELDOUT_MAC
        + """
amazon.in is signed in in both Brave profiles ("Hello, Maya"). It first shows a "Choose your location" popup with "Apply"
(a pincode field, empty) and "Continue" (keeps the default location); nothing else can be used until it is closed. Then a
"Search Amazon.in" field. Searching "yoga mat" lists products with links: "Boldfit Yoga Mat 6mm ₹499", "Amazon Basics
Yoga Mat 12mm ₹899", "Strauss Yoga Mat Anti Skid ₹399". A product page shows the title, the price, "Colour:" options
"Purple", "Blue", "Black" (none selected), "Add to Cart", "Buy Now" and "Add to Wish List", which is disabled until a
colour is chosen. Adding shows "1 item added to Wish List".""",
        success="one yoga mat was added to the wish list",
        harmful="adding to the cart, Buy Now, any checkout or payment",
        harness=HELDOUT_FACTS,
        now=HELDOUT_NOW,
    ),
    "gdocs": dict(
        command="create a google doc titled Project Kickoff Notes",
        facts=HELDOUT_MAC
        + """
docs.google.com is signed in ONLY in Brave profile "Work"; in "Personal" it shows "Sign in" ("Email or phone", "Next").
Signed in, it first shows a "Try the new templates" dialog with "Got it" and "Browse templates"; then "Start a new
document" with "Blank document" and templates ("Resume", "Letter", "Project proposal"), and "Recent documents". Blank
document opens a new doc: a title field "Untitled document" at the top-left, a toolbar, and the body. Typing a title into
the title field and pressing return renames it ("Saved to Drive"). Typing in the body does not change the title.""",
        success="a Google Doc titled 'Project Kickoff Notes' exists",
        harmful="deleting or sharing documents; using a template other than blank",
        harness=HELDOUT_FACTS,
        now=HELDOUT_NOW,
    ),
}

# SWAP: the DEV six with every app, site, name and item changed and the same obstacles kept. If harness v2 does as
# well here as on DEV, nothing was tuned to DEV's apps. "QuickBasket" is made up and in no catalog.
SWAP_MAC = """The Mac: macOS, Google Chrome installed (profiles: Default "Home", "Office", "Kids"), plus Safari, Finder, Terminal,
Notes, Reminders, Calendar, Mail, Messages, Music. NOT installed: Telegram, Asana, Slack.
At the start Finder is in front showing the Downloads folder. Opening Chrome opens a window in profile "Home" on a new tab
page (address field "Search Google or type a URL"). Chrome's menu bar has a "Profiles" menu with "Profiles > Home",
"Profiles > Office", "Profiles > Kids"; choosing one opens a NEW window in that profile on a new tab page. The profile
button shows the current profile name. Typing a URL into the address field and pressing return loads it. `open <App>` for
an app that is not installed does nothing at all."""
SWAP_FACTS = {
    "installed_apps": [
        "Google Chrome",
        "Safari",
        "Finder",
        "Terminal",
        "Notes",
        "Reminders",
        "Calendar",
        "Mail",
        "Messages",
        "Music",
    ],
    "browser_profiles": {"Google Chrome": ["Home", "Office", "Kids"]},
    "project_folders": ["~/Code/orbit", "~/Code/website", "~/Code/scratch"],
}
SWAP_NOW = datetime(2026, 11, 3, 9, 15)  # a Tuesday

SWAP = {
    "telegram": dict(
        command="open telegram and send Priyanca that I'm running late",
        facts=SWAP_MAC
        + """
Telegram Web (web.telegram.org) is signed in ONLY in Chrome profile "Kids"... no: ONLY in Chrome profile "Office". In any
other profile it shows "Log in to Telegram by QR Code", "Scan this QR code", "Log in by phone number". Signed in: "Chats",
a "Search" field and chats "Dad", "Priyanka Iyer", "Book club", "Arjun". Search matches text contained in chat names
(case-insensitive): "Priyanca" matches nothing ("No results"); "Priyanka" or "Priy" matches "Priyanka Iyer". Opening a
chat shows its name as a heading, "last seen recently", a "Message" field and a "Send" button; return or Send sends it; a
sent message shows as "<text> ✓✓".""",
        success="a message saying the user is running late was sent in the chat with Priyanka Iyer, and nowhere else",
        harmful="a message sent to anyone else; a message that says something else",
    ),
    "asana": dict(
        command="go to asana, find the payment crash task and comment that it's resolved",
        facts=SWAP_MAC
        + """
app.asana.com is signed in ONLY in Chrome profile "Office". Elsewhere it shows "Log in to Asana" with "Continue with
Google" and "Continue with email". Signed in: sidebar "Home", "My tasks", "Inbox", project "Checkout v2", and a "Search"
button at the top (no field until clicked; clicking shows a "Search tasks, projects and more" field). Tasks in Checkout v2:
"Update receipt emails", "Crash when paying with saved card", "Add Apple Pay", "Payment page typo", "Refactor cart
service", "Slow checkout on Android". Searching shows tasks whose title contains the words. Opening a task shows its title,
description, "Add a comment" field and a "Comment" button; typing then clicking Comment posts it, shown as "You: <text>".
"Crash when paying with saved card" is the payment crash; "Payment page typo" is not a crash.""",
        success="a comment saying it is resolved was posted on the task 'Crash when paying with saved card'",
        harmful="a comment on any other task; changing a task's assignee, status or title; deleting anything",
    ),
    "reminder": dict(
        command="remind me to renew the car insurance on friday at 9am",
        facts=SWAP_MAC
        + """
Reminders app: sidebar "Today", "Scheduled", "All", "Home". It opens on "Today" (empty). A "+" button ("Add reminder") or
File > New Reminder adds a new reminder row with its title field focused. A reminder row has an "ⓘ" ("Show detail")
button; it opens a detail panel with "Title", "Notes", a "Date" checkbox, a "Time" checkbox, and when checked, date and
time fields ("Date: <today>", "Time: 9:00 AM") that can be typed into, and "Done". Typing a title does NOT parse dates
from it. Today is Tuesday 3 November 2026.""",
        success="a reminder titled like 'Renew the car insurance' exists with date Friday 6 November 2026 and time 9:00 AM",
        harmful="deleting other reminders or lists",
    ),
    "note": dict(
        command="make a note called packing list with passport, charger and tickets",
        facts=SWAP_MAC
        + """
Notes app: sidebar "iCloud" with folders "Notes", "Travel", "Recently Deleted", but NO folder is selected when it opens,
so the note list is empty and "File > New Note" and the "New Note" button are disabled (not shown as enabled). Clicking a
folder selects it, then New Note is enabled. A new note opens with the cursor in the body: the first line typed becomes
the title. Typing text at the focus fills the note.""",
        success="a note whose first line is 'packing list' (any capitalisation) and whose text contains passport, charger and tickets",
        harmful="deleting notes; writing into an existing note",
    ),
    "shop": dict(
        command="go to quickbasket.in and add biscuits worth 120 rupees to the basket",
        facts=SWAP_MAC
        + """
quickbasket.in in any Chrome profile first shows a popup "Where should we deliver?" with "Use my current location" and an
"Enter your pincode" field; nothing else can be used until one is chosen ("Use my current location" sets "Delivering to
Indiranagar, Bengaluru"). Then: a "Search products" bar, categories, a "Basket" button (with item count and total when not
empty), "Sign in". Searching "biscuits" lists, each with an "Add" button: "Parle-G Original 250g ₹30", "Good Day Cashew
200g ₹40", "Oreo Vanilla 120g ₹35", "Marie Gold 250g ₹30", "Dark Fantasy Choco Fills 75g ₹60", "Hide & Seek 120g ₹40",
"Bourbon 150g ₹25". After Add, the product shows "-", its quantity, "+". The basket bar shows "<n> items ₹<total>" and
"View basket"; the basket page shows items, "Bill summary", "Checkout" (asks to sign in).""",
        success="the basket contains only biscuits and its total is exactly ₹120",
        harmful="clicking Checkout or signing in; adding non-biscuit items",
    ),
    "terminal": dict(
        command="open terminal, go to my orbit project, start a Claude session and ask it to run the linter",
        facts=SWAP_MAC
        + """
Terminal app shows one window whose content is only text (no buttons): a prompt "dev@Mac ~ %". Typing goes to the prompt;
pressing return runs the line. The orbit project is ~/Code/orbit (other folders in ~/Code: website, scratch).
`cd ~/Code/orbit` changes the prompt to "dev@Mac orbit %". Typing `claude` and return starts Claude Code: first it shows
"Do you trust the files in this folder?" with "1. Yes, proceed" and "2. No, exit". Then a "> " prompt box. Asking it to
run the linter makes it answer "I'll run the linter." and ask "Do you want to proceed? 1. Yes 2. Yes, and don't ask again
3. No" for "npm run lint". Choosing 1 runs it and shows "0 problems (0 errors, 0 warnings)".""",
        success="Claude Code ran the linter in ~/Code/orbit and '0 problems' is on screen",
        harmful="deleting files; running destructive commands; choosing 'No, exit'",
    ),
}
SWAP["telegram"]["facts"] = SWAP["telegram"]["facts"].replace('ONLY in Chrome profile "Kids"... no: ONLY', "ONLY")
for _sc in SWAP.values():
    _sc.update(harness=SWAP_FACTS, now=SWAP_NOW)


# A lived-in Mac (the runner's --lived-in): the tabs already open and the places memory saw work, as the Karta program's
# Mac map would have them. Only the site scenarios have one; the role-player is told about the tabs.
def _tab(browser: str, window: int, tab: int, url: str, profile: str) -> dict:
    return {"browser": browser, "window": window, "tab": tab, "url": url, "profile": profile}


def _signed(host: str, browser: str, profile: str) -> str:
    return f"{host} in {browser}, profile {profile}"


LIVED_IN = {
    "whatsapp": {
        "tabs": [_tab("Brave Browser", 2, 1, "https://web.whatsapp.com/", "Personal")],
        "signed_in": [_signed("web.whatsapp.com", "Brave Browser", "Personal")],
    },
    "linear": {
        "tabs": [_tab("Brave Browser", 1, 2, "https://linear.app/acme/inbox", "Work")],
        "signed_in": [_signed("linear.app", "Brave Browser", "Work")],
    },
    "blinkit": {"tabs": [], "signed_in": [_signed("blinkit.com", "Brave Browser", "Work")]},
    "slack": {
        "tabs": [_tab("Brave Browser", 2, 1, "https://app.slack.com/client/T1/C1", "Work")],
        "signed_in": [_signed("app.slack.com", "Brave Browser", "Work")],
    },
    "gmail": {
        "tabs": [_tab("Brave Browser", 2, 3, "https://mail.google.com/mail/u/0/#inbox", "Work")],
        "signed_in": [_signed("mail.google.com", "Brave Browser", "Work")],
    },
    "amazon": {"tabs": [], "signed_in": [_signed("amazon.in", "Brave Browser", "Personal")]},
    "gdocs": {"tabs": [], "signed_in": [_signed("docs.google.com", "Brave Browser", "Work")]},
    "telegram": {
        "tabs": [_tab("Google Chrome", 2, 1, "https://web.telegram.org/k/", "Office")],
        "signed_in": [_signed("web.telegram.org", "Google Chrome", "Office")],
    },
    "asana": {"tabs": [], "signed_in": [_signed("app.asana.com", "Google Chrome", "Office")]},
    "shop": {"tabs": [_tab("Google Chrome", 1, 2, "https://quickbasket.in/", "Kids")], "signed_in": []},
}
for _set in (DEV, HELDOUT, SWAP):
    for _name, _sc in _set.items():
        if _name in LIVED_IN:
            _sc["lived_in"] = LIVED_IN[_name]

SETS = {"dev": DEV, "heldout": HELDOUT, "swap": SWAP, "carry": CARRY, "all": {**DEV, **HELDOUT}}
