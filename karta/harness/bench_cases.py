# ruff: noqa: RUF001, RUF005  (real screens carry en dashes and curly quotes; screens are built by concatenation)
"""Decision benchmark cases: realistic Mac screens as Items, each question with its right answer(s).

Two splits, each case tagged with the app or site it is about. TUNING mixes the first test scenarios (WhatsApp, Linear,
Blinkit, Terminal and Claude Code, Reminders, Notes: at most a third of any decision, no app over 15%) with other
common apps (Gmail, YouTube, Finder, Calendar, Trello, Outlook, Teams, Discord, Drive, Swiggy, Netflix, App Store).
HELD-OUT uses apps, sites and dialogs that appear nowhere in tuning or the scenario traces (Slack, Spotify, Google Docs,
Jira, Amazon, Figma, GitHub, Zoom, Airbnb, Notion, Weather, macOS prompts, cookie banners). The harness must work
for any task, so a template is chosen by its held-out accuracy.

Screens follow what the Mac shows through AX and OCR (names without values, regions, rows for repeated labels), and
role-played scenario runs. Cases marked source="poc" replay a question an early scenario run asked, several of
which Laya answered wrongly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from karta.harness.decide import (
    DIALOG,
    DOES_IT,
    DOES_OTHER,
    ELSEWHERE,
    FITS,
    INTERRUPTION,
    LOADING,
    LOGIN,
    NONE,
    NOT_FOUND,
    NOT_SHOWN,
    OTHER,
    PART_OF_STEP,
    READY,
    SHOWN,
)
from karta.harness.types import Item, Screen

# One screen reads best as a few dense lines, the way it looks.
# fmt: off


@dataclass(frozen=True)
class Case:
    decision: str  # "D1 place", ... (D7 covers D7 step outcome and D8 end state)
    name: str
    args: tuple  # the Laya method's arguments
    answers: frozenset[str]  # right options: a key (READY, SHOWN, ...), a place, an item's name or label, or NONE
    source: str = "made"  # "made" or "poc"
    split: str = "tune"  # "tune" or "held": held-out cases use apps and sites seen nowhere in tuning
    app: str = ""  # the app or site the case is about, for per-app counts


def b(name, region="", **kw) -> Item:
    return Item(name, "button", region=region, **kw)


def f(name, region="", value="", **kw) -> Item:
    return Item(name, "field", value=value, region=region, **kw)


def t(name, region="", **kw) -> Item:
    return Item(name, "text", region=region, **kw)


def h(name, region="top", **kw) -> Item:
    return Item(name, "heading", region=region, **kw)


def r(name, region="left", value="", **kw) -> Item:
    return Item(name, "row", value=value, region=region, **kw)


def ln(name, region="", **kw) -> Item:
    return Item(name, "link", region=region, **kw)


def tab(name, region="top", **kw) -> Item:
    return Item(name, "tab", region=region, **kw)


def menu(*paths: str) -> list[Item]:
    return [Item(p, "menu", menu=True) for p in paths]


def browser(profile: str, url: str, title: str) -> list[Item]:
    """A Chromium window's chrome: never part of the page."""
    return [
        Item(title, "tab", region="top-left", chrome=True),
        Item("New Tab", "button", region="top", chrome=True),
        Item("Back", "button", region="top-left", chrome=True),
        Item("Forward", "button", region="top-left", chrome=True),
        Item("Reload", "button", region="top-left", chrome=True),
        Item("Address and search bar", "field", value=url, region="top", chrome=True),
        Item(f"{profile} (profile)", "button", region="top-right", chrome=True),
    ] + menu("File > New Tab", "File > New Window", "Profiles > Work", "Profiles > Personal", "History > Show All History")


def web(profile: str, url: str, title: str, page: list[Item]) -> Screen:
    return Screen("Brave Browser", browser(profile, url, title) + page, url=url, window=title, profile=profile)


# -- screens ------------------------------------------------------------------------------------------------------

def whatsapp_chats() -> Screen:
    return web("Personal", "https://web.whatsapp.com/", "WhatsApp", [
        h("Chats", "top-left"), b("New chat", "top-left"), b("Menu", "top-left"),
        f("Search or start a new chat", "top-left"),
        tab("All", "top-left"), tab("Unread", "top-left"), tab("Favourites", "top-left"), tab("Groups", "top-left"),
        r("Hemanshu Patel", value="Ok, see you tomorrow"), r("Mom", value="Call me when free"),
        r("Family Group", value="Papa: photo"), r("Rahul Sharma", value="Sent the invoice"),
        r("Priya Office", value="Thanks!"), r("Ankit Jain", value="haha"),
        t("Download WhatsApp for Mac", "middle"), t("Make calls and share your screen, faster with the app", "middle"),
        b("Get the app", "middle"), t("Your personal messages are end-to-end encrypted", "bottom"),
    ])


def whatsapp_chat(name: str = "Hemanshu Patel", sent: str | None = None, typed: str = "") -> Screen:
    page = whatsapp_chats().page()[:10] + [
        h(name, "top"), b("Search", "top-right"), b("Menu", "top-right"),
        t("Ok, see you tomorrow", "middle"), t("10:42", "middle"),
    ]
    if sent:
        page += [t(sent, "middle-right"), t("Delivered", "middle-right")]
    page += [b("Attach", "bottom-left"), f("Type a message", "bottom", value=typed, focused=True), b("Voice message", "bottom-right")]
    return web("Personal", "https://web.whatsapp.com/", "WhatsApp", page)


def whatsapp_login() -> Screen:
    return web("Work", "https://web.whatsapp.com/", "WhatsApp", [
        h("Steps to log in", "middle"), t("1. Open WhatsApp on your phone", "middle-left"),
        t("2. On Android tap Menu, on iPhone tap Settings", "middle-left"), t("3. Tap Linked devices, then Link device", "middle-left"),
        t("4. Scan the QR code to confirm", "middle-left"), Item("Scan this QR code to link a device!", "image", region="middle-right"),
        Item("Stay logged in on this browser", "checkbox", region="middle-right", checked=True),
        ln("Log in with phone number", "bottom"), t("Your personal messages are end-to-end encrypted", "bottom"),
    ])


def whatsapp_loading() -> Screen:
    return web("Personal", "https://web.whatsapp.com/", "WhatsApp", [
        Item("WhatsApp", "image", region="middle"), t("Loading your chats [42%]", "middle"), t("End-to-end encrypted", "bottom"),
    ])


def linear_home() -> Screen:
    return web("Work", "https://linear.app/acme/team/ENG/active", "Active issues · Linear", [
        b("Acme", "top-left"), b("Search", "top-left"), b("New issue", "top-left"),
        ln("Inbox", "left"), ln("My issues", "left"), ln("Views", "left"), t("Your teams", "left"),
        ln("Engineering", "left"), ln("Issues", "left"), ln("Cycles", "left"), ln("Projects", "left"),
        h("Active issues", "top"), tab("All issues", "top"), tab("Active", "top"), tab("Backlog", "top"),
        b("Filter", "top-right"), b("Display", "top-right"),
        r("ENG-142 Fix login redirect loop", "middle", value="In Progress"), r("ENG-139 Add dark mode to settings", "middle", value="Todo"),
        r("ENG-131 Crash when uploading large files", "middle", value="Todo"), r("ENG-98 Add SSO login", "middle", value="Backlog"),
        r("ENG-77 Update redirect docs", "middle", value="Done"),
    ])


def linear_login() -> Screen:
    return web("Test", "https://linear.app/login", "Log in · Linear", [
        Item("Linear", "image", region="top"), h("Log in to Linear", "middle"),
        b("Continue with Google", "middle"), b("Continue with email", "middle"), b("Continue with SAML SSO", "middle"),
        b("Log in with passkey", "middle"), t("Don't have an account?", "bottom"), ln("Sign up", "bottom"),
        ln("Learn more", "bottom"),
    ])


def linear_command_menu() -> Screen:
    page = linear_home().page() + [
        f("Type a command or search...", "middle", focused=True), r("Create new issue", "middle", value="C"),
        r("Search issues", "middle"), r("Go to inbox", "middle", value="G then I"), r("Go to my issues", "middle"),
        r("Create new project", "middle"), r("Switch team", "middle"), r("Open settings", "middle"),
    ]
    return web("Work", "https://linear.app/acme/team/ENG/active", "Active issues · Linear", page)


def linear_404() -> Screen:
    return web("Work", "https://linear.app/acme/issue/ENG-9999", "Linear", [
        b("Search", "top-left"), ln("Inbox", "left"), ln("My issues", "left"),
        h("Issue not found", "middle"), t("The issue ENG-9999 does not exist or you do not have access to it.", "middle"),
        b("Go back", "middle"),
    ])


BLINKIT_BEHIND = [
    Item("blinkit", "image", region="top-left"), b("Delivery in 8 minutes", "top-left"),
    f('Search "milk"', "top"), b("Login", "top-right"), b("My Cart", "top-right"),
    ln("Dairy, Bread & Eggs", "middle"), ln("Chips & Namkeen", "middle"), ln("Cold Drinks & Juices", "middle"),
]


def blinkit_popup() -> Screen:
    return web("Personal", "https://blinkit.com/", "Blinkit", BLINKIT_BEHIND + [
        h("Welcome to blinkit", "middle"), t("Please provide your delivery location to see products at nearby store", "middle"),
        b("Detect my location", "middle"), t("OR", "middle"), f("search delivery location", "middle"),
        b("Close", "top-right"),
    ])


PRODUCTS = [
    ("Lay's India's Magic Masala Potato Chips", "52 g", "₹20"),
    ("Lay's Classic Salted Potato Chips", "90 g", "₹50"),
    ("Uncle Chipps Spicy Treat", "55 g", "₹20"),
    ("Bingo Mad Angles Achaari Masti", "66 g", "₹20"),
    ("Kurkure Masala Munch", "90 g", "₹20"),
    ("Pringles Sour Cream & Onion", "107 g", "₹115"),
    ("Doritos Nacho Cheese", "60 g", "₹50"),
    ("Too Yumm Veggie Stix", "70 g", "₹30"),
]


def blinkit_results(query: str = "chips") -> Screen:
    page = [
        Item("blinkit", "image", region="top-left"), b("Delivery in 8 minutes · Home", "top-left"),
        f("Search for atta dal and more", "top", value=query, focused=True), b("Login", "top-right"), b("My Cart", "top-right"),
        t(f'Showing results for "{query}"', "top"),
    ]
    for n, (name, size, price) in enumerate(PRODUCTS):
        where = "middle-left" if n % 4 < 2 else "middle-right"
        page += [t(name, where), t(size, where), t(price, where), b("ADD", where, row=(name, size, price))]
    return web("Personal", f"https://blinkit.com/s/?q={query}", "Blinkit", page)


def blinkit_cart() -> Screen:
    return web("Personal", "https://blinkit.com/", "Blinkit", BLINKIT_BEHIND + [
        h("My Cart", "right"), t("Delivery in 8 minutes", "right"), t("Shipment of 2 items", "right"),
        t("Lay's India's Magic Masala Potato Chips", "right"), t("52 g", "right"), t("₹20", "right"),
        b("-", "right", row=("Lay's India's Magic Masala Potato Chips", "1", "+")), t("1", "right"),
        b("+", "right", row=("Lay's India's Magic Masala Potato Chips", "1", "-")),
        t("Kurkure Masala Munch", "right"), t("90 g", "right"), t("₹20", "right"),
        b("-", "right", row=("Kurkure Masala Munch", "1", "+")), t("1 ", "right"), b("+", "right", row=("Kurkure Masala Munch", "1", "-")),
        h("Bill details", "right"), t("Items total ₹40", "right"), t("Delivery charge ₹25", "right"),
        t("Grand total ₹65", "right"), b("Login to Proceed", "bottom-right"),
    ])


def notes(folder_selected: bool = False) -> Screen:
    items = [
        t("iCloud", "left"), r("Notes", value="12"), r("Quick Notes", value="3"), r("Recently Deleted", value="1"),
        b("New Folder", "bottom-left"), f("Search", "top-right"),
        b("New Note", "top", enabled=folder_selected), b("Delete", "top", enabled=folder_selected),
        b("Format", "top"), b("Checklist", "top"), b("Share", "top-right"),
    ]
    if folder_selected:
        items += [r("Groceries", "middle-left", value="Yesterday"), r("Ideas for the trip", "middle-left", value="Monday"),
                  f("Note body", "right", value="Groceries: milk, eggs")]
    else:
        items += [t("No Note Selected", "middle")]
    return Screen("Notes", items + menu("File > New Note", "File > New Folder", "Edit > Undo", "Format > Checklist"), window="Notes")


def reminders(info: bool = False) -> Screen:
    items = [
        f("Search", "top-left"), b("Today", "top-left"), b("Scheduled", "top-left"), b("All", "top-left"),
        b("Flagged", "top-left"), b("Completed", "top-left"), t("My Lists", "left"), r("Reminders", value="4"),
        r("Groceries", value="2"), b("Add List", "bottom-left"), h("Reminders", "top"), b("Add Reminder", "top-right"),
        r("Call dentist", "middle"), r("Pay electricity bill", "middle"), r("Book train tickets", "middle"),
        b("Show info", "middle-right", row=("Call dentist",)),
    ]
    if info:
        items += [
            h("Details", "right"), f("Title", "right", value="Call dentist"), f("Notes", "right"), f("URL", "right"),
            Item("Date", "checkbox", region="right", checked=False), Item("Time", "checkbox", region="right", checked=False),
            Item("Location", "checkbox", region="right", checked=False), b("Priority: None", "right"),
            b("Done", "top-right"),
        ]
    return Screen("Reminders", items + menu("File > New Reminder", "File > New List", "Edit > Show Info"), window="Reminders")


def terminal(lines: list[str] | None = None, window: str = "shubham — -zsh — 80×24") -> Screen:
    lines = lines or ["Last login: Mon Sep 28 10:02:11 on ttys001", "shubham@Mac ~ %"]
    items = [t(line, "top-left" if n < 2 else "left") for n, line in enumerate(lines)]
    return Screen("Terminal", items + menu("Shell > New Window", "Shell > New Tab"), window=window)


def claude_trust() -> Screen:
    return terminal([
        "shubham@Mac karta % claude", "Do you trust the files in this folder?", "~/Desktop/Projects/karta",
        "Claude Code may read, write, or execute files contained in this directory.",
        "1. Yes, proceed", "2. No, exit", "Enter to confirm · Esc to exit",
    ], "karta — claude — 80×24")


def claude_permission() -> Screen:
    return terminal([
        "Bash command", "rm -rf build/", "Remove the build folder", "Do you want to proceed?",
        "1. Yes", "2. Yes, and don't ask again for rm commands in this folder", "3. No, and tell Claude what to do differently (esc)",
    ])


def claude_ready() -> Screen:
    return terminal([
        "✻ Welcome to Claude Code!", "/help for help, /status for your current setup", "cwd: ~/code/karta",
        "> Try \"write a test for <filepath>\"", "? for shortcuts",
    ], "karta — claude — 80×24")


def gmail_inbox() -> Screen:
    return web("Work", "https://mail.google.com/mail/u/0/#inbox", "Inbox (3) - sam@acme.com - Gmail", [
        b("Main menu", "top-left"), Item("Gmail", "image", region="top-left"), f("Search mail", "top"),
        b("Support", "top-right"), b("Settings", "top-right"), b("Google apps", "top-right"), b("Google Account: Sam", "top-right"),
        b("Compose", "left"), ln("Inbox 3", "left"), ln("Starred", "left"), ln("Snoozed", "left"), ln("Sent", "left"),
        ln("Drafts 1", "left"), ln("More", "left"), tab("Primary", "top"), tab("Promotions", "top"), tab("Social", "top"),
        r("Rahul Sharma — Invoice for September", "middle", value="10:14"), r("Linear — 3 issues assigned to you", "middle", value="09:02"),
        r("HDFC Bank — Your statement is ready", "middle", value="Sep 28"),
    ])


def gmail_compose() -> Screen:
    page = gmail_inbox().page() + [
        h("New Message", "bottom-right"), b("Minimise", "bottom-right"), b("Close", "bottom-right"),
        f("To recipients", "bottom-right"), f("Subject", "bottom-right"), f("Message Body", "bottom-right", focused=True),
        b("Send", "bottom-right"), b("Formatting options", "bottom-right"), b("Attach files", "bottom-right"),
        b("Discard draft", "bottom-right"),
    ]
    return web("Work", "https://mail.google.com/mail/u/0/#inbox?compose=new", "Inbox (3) - Gmail", page)


def gmail_sent() -> Screen:
    page = gmail_inbox().page() + [t("Message sent", "bottom-left"), b("Undo", "bottom-left"), b("View message", "bottom-left")]
    return web("Work", "https://mail.google.com/mail/u/0/#inbox", "Inbox (3) - Gmail", page)


def google_signin() -> Screen:
    return web("Test", "https://accounts.google.com/v3/signin/identifier", "Gmail", [
        Item("Google", "image", region="top-left"), h("Sign in", "middle-left"), t("to continue to Gmail", "middle-left"),
        f("Email or phone", "middle-right"), b("Forgot email?", "middle-right"),
        t("Not your computer? Use Guest mode to sign in privately.", "middle-right"), ln("Learn more about using Guest mode", "middle-right"),
        b("Create account", "bottom-right"), b("Next", "bottom-right"),
    ])


def youtube_results() -> Screen:
    return web("Personal", "https://www.youtube.com/results?search_query=shape+of+you", "shape of you - YouTube", [
        b("Guide", "top-left"), Item("YouTube Home", "link", region="top-left"), f("Search", "top", value="shape of you"),
        b("Search", "top"), b("Search with your voice", "top"), b("Settings", "top-right"), b("Sign in", "top-right"),
        tab("All", "top"), tab("Videos", "top"), tab("Shorts", "top"),
        ln("Ed Sheeran - Shape of You (Official Music Video)", "middle"), t("Ed Sheeran · 6.4B views · 8 years ago", "middle"),
        ln("Shape of You - Ed Sheeran (Lyrics)", "middle"), t("7clouds · 210M views", "middle"),
        ln("Ed Sheeran - Shape Of You [Live at Wembley]", "middle"), ln("Shape of You (Piano Cover)", "middle"),
    ])


def youtube_playing() -> Screen:
    return web("Personal", "https://www.youtube.com/watch?v=JGwWNGJdvx8", "Ed Sheeran - Shape of You (Official Music Video) - YouTube", [
        f("Search", "top"), b("Sign in", "top-right"), b("Pause (k)", "middle-left"), b("Mute (m)", "middle-left"),
        t("0:12 / 4:23", "middle-left"), b("Full screen (f)", "middle"),
        h("Ed Sheeran - Shape of You (Official Music Video)", "bottom-left"), ln("Ed Sheeran", "bottom-left"), b("Subscribe", "bottom-left"),
        b("Like", "bottom"), b("Share", "bottom"), ln("Perfect - Ed Sheeran", "right"), ln("Thinking Out Loud", "right"),
    ])


def youtube_ad() -> Screen:
    return web("Personal", "https://www.youtube.com/watch?v=JGwWNGJdvx8", "YouTube", [
        f("Search", "top"), b("Sign in", "top-right"), t("Ad · 0:05", "middle-left"), b("Skip Ad", "middle-right"),
        h("Ed Sheeran - Shape of You (Official Music Video)", "bottom-left"),
        h("Before you continue to YouTube", "middle"), t("We use cookies and data to deliver and maintain Google services", "middle"),
        b("Reject all", "middle"), b("Accept all", "middle"), b("More options", "middle"),
    ])


def finder(folder: str = "Desktop") -> Screen:
    files = {"Desktop": ["Screenshot 2026-09-28.png", "karta-demo.mov", "notes.txt"],
             "Downloads": ["report.pdf", "invoice-sept.pdf", "laya-mlx-0.2.0.zip"]}[folder]
    items = [
        b("Back", "top-left"), b("Forward", "top-left"), h(folder, "top"), f("Search", "top-right"),
        t("Favorites", "left"), r("AirDrop"), r("Recents"), r("Applications"), r("Desktop"), r("Documents"), r("Downloads"),
        t("iCloud", "left"), r("iCloud Drive"),
    ] + [r(name, "middle") for name in files]
    return Screen("Finder", items + menu("File > New Folder", "Go > Downloads"), window=folder)


def calendar() -> Screen:
    return Screen("Calendar", [
        b("Calendars", "top-left"), b("Add", "top-left"), tab("Day", "top"), tab("Week", "top"), tab("Month", "top"), tab("Year", "top"),
        f("Search", "top-right"), b("Today", "top-right"), h("September 2026", "top-left"),
        t("Tue 29", "top"), t("Standup", "middle", value="10:00"), t("Lunch with Priya", "middle"), t("Design review", "middle"),
    ] + menu("File > New Event", "View > By Week"), window="Calendar")


def calendar_new_event() -> Screen:
    s = calendar()
    s.items += [f("New Event", "middle", focused=True), b("Add Location or Video Call", "middle"), t("29 Sep 2026  10:00 to 11:00", "middle"),
                b("Add Invitees", "middle"), b("Add Notes, URL or Attachments", "middle")]
    return s


def settings_accessibility() -> Screen:
    return Screen("System Settings", [
        h("Privacy & Security", "top"), f("Search", "top-left"), r("Wi-Fi"), r("Bluetooth"), r("Privacy & Security"),
        t("Accessibility", "middle"), t("Allow the applications below to control your computer.", "middle"),
        Item("Terminal", "checkbox", region="middle", checked=True), Item("Karta", "checkbox", region="middle", checked=False),
        b("Add", "bottom"), b("Remove", "bottom"),
    ], window="Accessibility")


def safari_new_tab() -> Screen:
    return Screen("Safari", [
        f("Search or enter website name", "top", chrome=True), b("Show Sidebar", "top-left", chrome=True),
        h("Favorites", "middle"), ln("Apple", "middle"), ln("iCloud", "middle"), ln("Google", "middle"), ln("Wikipedia", "middle"),
        h("Privacy Report", "middle"),
    ], url=None, window="Start Page")


def brave_new_tab(profile: str = "Work") -> Screen:
    return web(profile, "", "New Tab", [f("Search the web privately", "middle"), ln("Gmail", "middle"), ln("YouTube", "middle")])


def mail_app() -> Screen:
    return Screen("Mail", [
        r("Inbox"), r("VIPs"), r("Sent"), r("Drafts"), f("Search", "top-right"), b("Compose", "top"),
        t("No Message Selected", "middle"),
    ], window="Inbox – All Mailboxes")


def messages_app() -> Screen:
    return Screen("Messages", [f("Search", "top-left"), b("New Message", "top"), t("No Conversations", "middle")], window="Messages")


def reminders_empty() -> Screen:
    return Screen("Reminders", [f("Search", "top-left"), b("New Reminder", "top-right"), b("Add List", "bottom-left"),
                                b("Today"), b("Scheduled"), b("All"), b("Flagged"), b("Completed"), t("No Reminders", "middle")], window="Reminders")


def blank_loading(url: str, title: str) -> Screen:
    return web("Work", url, title, [t("Loading…", "middle")])


# -- dialogs (the options D6 sees: buttons plus the dialog's text) ------------------------------------------------

def dialog(text: str, *buttons: str) -> list[Item]:
    return [t(text, "middle")] + [b(x, "middle") for x in buttons]


# -- more tuning screens: common apps that are neither test scenarios nor held-out -----------------------------------

def app(name: str, window: str, items: list[Item], *menus: str) -> Screen:
    return Screen(name, items + menu(*menus), window=window)


def site(url: str, title: str, page: list[Item], profile: str = "Work") -> Screen:
    return web(profile, url, title, page)


def trello(added: str | None = None) -> Screen:
    cards = ["Draft launch email", "Fix pricing page typo"] + ([added] if added else [])
    page = [b("Boards", "top-left"), f("Search Trello", "top"), b("Create", "top"), h("Marketing sprint", "top-left"),
            b("Share", "top-right"), b("Filters", "top-right"), t("To Do", "left")]
    page += [r(c, "left") for c in cards] + [b("Add a card", "left", row=("To Do",)), t("Doing", "middle"), r("Record demo video", "middle"),
                                             b("Add a card", "middle", row=("Doing",)), b("Add another list", "right")]
    return site("https://trello.com/b/x1/marketing-sprint", "Marketing sprint | Trello", page)


def trello_loading() -> Screen:
    return site("https://trello.com/b/x1/marketing-sprint", "Trello", [Item("Trello", "image", region="middle"), t("Loading board…", "middle")])


def outlook(sent: bool = False) -> Screen:
    page = [b("App launcher", "top-left"), f("Search", "top"), b("New mail", "top-left"), ln("Inbox 5", "left"), ln("Sent Items", "left"),
            ln("Drafts", "left"), tab("Focused", "top"), tab("Other", "top"),
            r("Arjun Mehta — Contract renewal", "middle", value="9:40"), r("IT Desk — Password expires soon", "middle", value="Mon"),
            h("Contract renewal", "right"), b("Reply", "right"), b("Reply all", "right"), b("Forward", "right"), b("Delete", "top")]
    if sent:
        page += [t("Your message has been sent.", "bottom"), b("Undo", "bottom")]
    return site("https://outlook.office.com/mail/", "Mail - Sam - Outlook", page)


def outlook_signin() -> Screen:
    return site("https://login.microsoftonline.com/common/oauth2", "Sign in to your account", [
        Item("Microsoft", "image", region="middle"), h("Sign in", "middle"), f("Email, phone, or Skype", "middle"),
        ln("Can't access your account?", "middle"), b("Back", "middle"), b("Next", "middle"), b("Sign-in options", "bottom"),
    ])


def teams(in_call: bool = False) -> Screen:
    items = [f("Search", "top"), ln("Activity", "left"), ln("Chat", "left"), ln("Teams", "left"), ln("Calendar", "left"),
             r("Rahul Iyer (Acme)"), r("Rahul Verma"), r("Design sync", value="Meeting chat"),
             h("Design sync", "top"), b("Join", "top-right"), f("Type a message", "bottom"), b("Send", "bottom-right")]
    if in_call:
        items += [t("00:02:13", "top"), b("Mic", "top"), b("Camera", "top"), b("Share", "top"), b("Leave", "top-right")]
    return app("Microsoft Teams", "Design sync | Microsoft Teams", items)


def discord(posted: str | None = None) -> Screen:
    items = [b("Direct Messages", "top-left"), b("Add a Server", "left"), t("MAKERS CLUB", "left"), r("# general"), r("# show-and-tell"),
             r("# family-chat"), h("# general", "top"), b("Threads", "top-right"), b("Inbox", "top-right"),
             t("neha: anyone at the meetup tonight?", "middle")]
    if posted:
        items += [t(f"sam: {posted}", "middle")]
    items += [b("Upload a File", "bottom-left"), f("Message #general", "bottom"), b("Send a gift", "bottom-right")]
    return app("Discord", "#general | Makers Club - Discord", items)


def drive() -> Screen:
    return site("https://drive.google.com/drive/my-drive", "My Drive - Google Drive", [
        b("New", "top-left"), f("Search in Drive", "top"), ln("Home", "left"), ln("My Drive", "left"), ln("Shared with me", "left"),
        ln("Trash", "left"), h("My Drive", "top"), r("Budget 2026.xlsx", "middle"), r("Budget 2025 (old).xlsx", "middle"),
        r("Offer letter.pdf", "middle"), r("Team photos", "middle"), b("List layout", "top-right"),
    ])


def drive_404() -> Screen:
    return site("https://drive.google.com/file/d/xyz/view", "Google Drive - Page Not Found", [
        Item("Google Drive", "image", region="top-left"), h("Sorry, the file you have requested does not exist.", "middle"),
        t("Make sure that you have the correct URL and the file exists.", "middle"), ln("Get stuff done with Google Drive", "bottom"),
    ])


def swiggy_popup() -> Screen:
    return site("https://www.swiggy.com/", "Swiggy", [
        Item("Swiggy", "image", region="top-left"), b("Sign in", "top-right"), f("Search for restaurant and food", "top"),
        h("Enter your delivery location", "middle"), b("Use my current location", "middle"), f("Enter area, street name", "middle"),
        b("Close", "top-right"),
    ], "Personal")


def swiggy_results() -> Screen:
    page = [Item("Swiggy", "image", region="top-left"), f("Search for restaurant and food", "top", value="pizza"), b("Cart", "top-right")]
    for name in ("Domino's Pizza", "Dominic's Diner", "La Pino'z Pizza"):
        page += [ln(name, "middle"), b("View menu", "middle", row=(name,))]
    return site("https://www.swiggy.com/search?query=pizza", "Swiggy", page, "Personal")


def netflix_profiles() -> Screen:
    return site("https://www.netflix.com/browse", "Netflix", [
        Item("Netflix", "image", region="top-left"), h("Who's watching?", "middle"), b("Sam", "middle"), b("Kids", "middle"),
        b("Add Profile", "middle"), b("Manage Profiles", "bottom"),
    ], "Personal")


def netflix_playing() -> Screen:
    return site("https://www.netflix.com/watch/81", "Netflix", [
        b("Back to Browse", "top-left"), h("Stranger Things: S4:E1 “The Hellfire Club”", "bottom"), b("Pause", "bottom-left"),
        b("Skip Intro", "bottom-right"), t("0:04:12", "bottom"),
    ], "Personal")


def app_store() -> Screen:
    return app("App Store", "Updates", [
        f("Search", "top-left"), ln("Discover", "left"), ln("Arcade", "left"), ln("Updates", "left"), h("Updates", "top"),
        b("Update All", "top-right"), t("Xcode 17.1", "middle"), b("Update", "middle", row=("Xcode 17.1",)),
        t("Keynote 14.4", "middle"), b("Update", "middle", row=("Keynote 14.4",)),
    ])


def asana() -> Screen:
    return site("https://app.asana.com/0/home", "My tasks - Asana", [
        b("Create", "top-left"), f("Search", "top"), ln("Home", "left"), ln("My tasks", "left"), ln("Inbox", "left"),
        h("My tasks", "top"), r("Send Q3 invoices", "middle"), r("Review hiring plan", "middle"),
        b("Mark complete", "middle", row=("Send Q3 invoices",)), b("Add task", "top"),
    ])


# -- cases --------------------------------------------------------------------------------------------------------

def _set(*xs: str) -> frozenset[str]:
    return frozenset(xs)


BRAVE = ("Work", "Personal", "Family", "Test")
SCENARIO_APPS = ("whatsapp", "linear", "blinkit")  # scenario sites: together at most a third of any decision's tuning


def _sites(site: str) -> list[str]:
    return [f"{site} in Brave profile {p}" for p in BRAVE]


def screen_app(s: Screen) -> str:
    """The app or site a screen shows, for the benchmark's per-app counts."""
    host = (s.url or "").split("://", 1)[-1].split("/", 1)[0]
    if not host:
        return s.app.lower()
    return site_name(host)


def site_name(host: str) -> str:
    """"web.whatsapp.com" -> "whatsapp", "www.airbnb.co.in" -> "airbnb", "mail.google.com" -> "gmail"."""
    parts = host.removeprefix("www.").split(".")
    while len(parts) > 2 and parts[-2] in ("co", "com"):
        parts = parts[:-1]
    name = parts[-2] if len(parts) >= 2 else parts[0]
    if name == "google":  # mail.google.com is Gmail, docs.google.com is Docs
        name = {"mail": "gmail"}.get(parts[0], parts[0])
    return {"office": "outlook", "microsoftonline": "outlook", "atlassian": "jira"}.get(name, name).lower()


def d1_cases() -> list[Case]:
    rows = [
        ("WhatsApp chats", ["Safari app", "Finder app", "Reminders app", *_sites("web.whatsapp.com")], _sites("web.whatsapp.com"), "whatsapp", "poc"),
        ("Blinkit grocery", ["Brave Browser app", "Notes app", "Google Chrome app", *_sites("blinkit.com")], _sites("blinkit.com"), "blinkit", "poc"),
        ("Linear issues", ["Calendar app", "Safari app", "Terminal app", *_sites("linear.app")], _sites("linear.app"), "linear", "poc"),
        ("Notes compose button", ["Finder app", "Safari app", "Mail app"], [NONE], "Notes", "poc"),
        ("Terminal", ["Reminders app", "Messages app"], [NONE], "Terminal", "poc"),
        ("Notes", ["Notes app", "Messages app", "Terminal app"], ["Notes app"], "Notes", "poc"),
        ("Reminders", ["Reminders app", "Terminal app", "Messages app"], ["Reminders app"], "Reminders", "poc"),
        ("Terminal app", ["Terminal app", "Reminders app", "Notes app"], ["Terminal app"], "Terminal", "poc"),
        ("a calendar event tomorrow at 10", ["Notes app", "Calendar app", "Reminders app"], ["Calendar app"], "Calendar", "made"),
        ("files in Downloads", ["Safari app", "Finder app", "Messages app"], ["Finder app"], "Finder", "made"),
        ("Wi-Fi settings", ["Finder app", "System Settings app", "Safari app"], ["System Settings app"], "System Settings", "made"),
        ("a to-do to pay the bill", ["Reminders app", "Mail app", "Finder app"], ["Reminders app"], "Reminders", "made"),
        ("YouTube videos", ["Music app", "Notes app", *_sites("youtube.com")], _sites("youtube.com"), "youtube", "made"),
        ("Claude Code in the karta folder", ["Notes app", "Terminal app", "Messages app"], ["Terminal app"], "Terminal", "made"),
        ("Gmail inbox", ["Notes app", "Finder app", *_sites("mail.google.com")], _sites("mail.google.com"), "gmail", "made"),
        ("Microsoft Teams chat", ["Messages app", "Mail app", "Notes app"], [NONE], "Microsoft Teams", "made"),
        ("order food on Swiggy", ["Notes app", *_sites("swiggy.com"), *_sites("youtube.com")], _sites("swiggy.com"), "swiggy", "made"),
        ("an email to Rahul", ["Mail app", "Notes app", "Calendar app"], ["Mail app"], "Mail", "made"),
        ("Trello cards", ["Finder app", "Calendar app", *_sites("trello.com")], _sites("trello.com"), "trello", "made"),
        ("the Linear login page", ["Linear app", "Safari app", "Notes app"], ["Linear app"], "linear", "made"),
        ("app updates", ["App Store app", "Finder app", "Notes app"], ["App Store app"], "App Store", "made"),
    ]
    return [Case("D1 place", what, (what, places), frozenset(ans), src, app=tag.lower()) for what, places, ans, tag, src in rows]


def d2_cases() -> list[Case]:
    rows = [
        ("Blinkit grocery", blinkit_popup(), DIALOG, "poc"),
        ("Blinkit grocery", blinkit_results(), READY, "made"),
        ("Blinkit grocery", brave_new_tab(), ELSEWHERE, "poc"),
        ("Blinkit grocery", notes(), ELSEWHERE, "poc"),
        ("WhatsApp chats", whatsapp_chats(), READY, "made"),
        ("WhatsApp chats", whatsapp_login(), LOGIN, "made"),
        ("WhatsApp chats", whatsapp_loading(), LOADING, "made"),
        ("WhatsApp chats", finder(), ELSEWHERE, "poc"),
        ("Linear issues", linear_home(), READY, "made"),
        ("Linear issues", linear_login(), LOGIN, "made"),
        ("Linear issues", linear_404(), NOT_FOUND, "made"),
        ("Terminal", reminders_empty(), ELSEWHERE, "poc"),
        ("Terminal", messages_app(), ELSEWHERE, "poc"),
        ("Terminal app", terminal(), READY, "poc"),
        ("Notes compose button", mail_app(), ELSEWHERE, "poc"),
        ("Notes compose button", safari_new_tab(), ELSEWHERE, "poc"),
        ("Gmail inbox", gmail_inbox(), READY, "made"),
        ("Gmail inbox", google_signin(), LOGIN, "made"),
        ("YouTube", youtube_results(), READY, "made"),
        ("YouTube", youtube_ad(), DIALOG, "made"),
        ("Claude Code", claude_trust(), DIALOG, "made"),
        ("Claude Code", claude_ready(), READY, "made"),
        ("Notes", notes(), READY, "made"),
        ("my to-do list", reminders(), READY, "made"),
        ("email", mail_app(), READY, "made"),
        ("Trello board", trello(), READY, "made"),
        ("Trello board", trello_loading(), LOADING, "made"),
        ("Outlook mail", outlook(), READY, "made"),
        ("Outlook mail", outlook_signin(), LOGIN, "made"),
        ("Google Drive", drive(), READY, "made"),
        ("Google Drive", drive_404(), NOT_FOUND, "made"),
        ("Swiggy food", swiggy_popup(), DIALOG, "made"),
        ("Netflix", netflix_profiles(), DIALOG, "made"),
        ("Microsoft Teams", teams(), READY, "made"),
        ("team chat", discord(), READY, "made"),
    ]
    return [Case("D2 page state", f"{what} on {s.app} {s.window}", (what, s), _set(ans), src, app=screen_app(s)) for what, s, ans, src in rows]


def d3_cases() -> list[Case]:
    rows = [
        ("the message box", whatsapp_chat(), ["Type a message"]),
        ("the search box for chats", whatsapp_chats(), ["Search or start a new chat"]),
        ("the ADD button for Lay's Magic Masala chips", blinkit_results(), ["ADD (Lay's India's Magic Masala Potato...)"]),
        ("add one more Kurkure", blinkit_cart(), ["+ (Kurkure Masala Munch)"]),
        ("the Settings button", linear_home(), [NONE]),
        ("make a new issue", linear_home(), ["New issue"]),
        ("write a new email", gmail_inbox(), ["Compose"]),
        ("the recipient field", gmail_compose(), ["To recipients"]),
        ("delete my account", gmail_inbox(), [NONE]),
        ("the Notes folder", notes(), ["Notes"]),
        ("details of Call dentist", reminders(), ["Show info"]),
        ("set a date", reminders(info=True), ["Date"]),
        ("the official Shape of You video", youtube_results(), ["Ed Sheeran - Shape of You (Official Music Video)"]),
        ("pause the video", youtube_playing(), ["Pause (k)"]),
        ("the Downloads folder", finder(), ["Downloads"]),
        ("switch to week view", calendar(), ["Week"]),
        ("add a card to Doing", trello(), ["Add a card (Doing)"]),
        ("reply to Arjun", outlook(), ["Reply"]),
        ("upload a file", drive(), ["New"]),
        ("join the design sync call", teams(), ["Join"]),
        ("the message box", discord(), ["Message #general"]),
        ("mark Send Q3 invoices done", asana(), ["Mark complete"]),
        ("use my current location", swiggy_popup(), ["Use my current location"]),
        ("watch as Sam", netflix_profiles(), ["Sam"]),
        ("update Keynote", app_store(), ["Update (Keynote 14.4)"]),
        ("hang up", teams(in_call=True), ["Leave"]),
    ]
    return [Case("D3 target", intent, (intent, s.page()), _set(*ans), app=screen_app(s)) for intent, s, ans in rows]


def d4_cases() -> list[Case]:
    add = {i.row[0]: i for i in blinkit_results().page() if i.name == "ADD"}
    rows = [
        ("the message box", f("Search or start a new chat", "top-left"), DOES_OTHER, "whatsapp"),
        ("the message box", f("Type a message", "bottom"), DOES_IT, "whatsapp"),
        ("add Lay's Magic Masala to the cart", add["Lay's India's Magic Masala Potato Chips"], DOES_IT, "blinkit"),
        ("add Lay's Magic Masala to the cart", add["Uncle Chipps Spicy Treat"], DOES_OTHER, "blinkit"),
        ("go to my Linear inbox", ln("Inbox", "left"), DOES_IT, "linear"),
        ("go to my Linear inbox", ln("Projects", "left"), DOES_OTHER, "linear"),
        ("send the email", b("Send", "bottom-right"), DOES_IT, "gmail"),
        ("send the email", b("Discard draft", "bottom-right"), DOES_OTHER, "gmail"),
        ("search mail", f("Search mail", "top"), DOES_IT, "gmail"),
        ("create a new note", b("New Note", "top"), DOES_IT, "Notes"),
        ("create a new note", b("Delete", "top"), DOES_OTHER, "Notes"),
        ("play the official video", ln("Shape of You (Piano Cover)", "middle"), DOES_OTHER, "youtube"),
        ("play the official video", ln("Ed Sheeran - Shape of You (Official Music Video)", "middle"), DOES_IT, "youtube"),
        ("type 'hi' into the message box", Item("File > New Tab", "menu", menu=True), DOES_OTHER, "Brave Browser"),
        ("set the reminder's date", Item("Date", "checkbox", region="right", checked=False), DOES_IT, "Reminders"),
        ("set the reminder's date", f("Title", "right", value="Call dentist"), DOES_OTHER, "Reminders"),
        ("add a card to Doing", b("Add a card", "middle", row=("Doing",)), DOES_IT, "trello"),
        ("add a card to Doing", b("Add another list", "right"), DOES_OTHER, "trello"),
        ("reply to Arjun", b("Reply", "right"), DOES_IT, "outlook"),
        ("reply to Arjun", b("Forward", "right"), DOES_OTHER, "outlook"),
        ("join the design sync", b("Join", "top-right"), DOES_IT, "Microsoft Teams"),
        ("join the design sync", ln("Calendar", "left"), DOES_OTHER, "Microsoft Teams"),
        ("post in #general", f("Message #general", "bottom"), DOES_IT, "Discord"),
        ("upload a file to Drive", ln("Trash", "left"), DOES_OTHER, "drive"),
    ]
    return [Case("D4 pick check", f"{intent} -> {item.name}", (intent, item), _set(ans), app=tag.lower()) for intent, item, ans, tag in rows]


def d5_cases() -> list[Case]:
    rows_ = lambda *names: [r(n) for n in names]  # noqa: E731
    rows = [
        ("the chat with Hemansu", "Hemanshu", rows_("Hemanshu Patel"), ["Hemanshu Patel"], "whatsapp", "poc"),
        ("the chat with Hemanshu Patel", "Hemanshu", rows_("Himanshu Mehta", "Hemanshu Patel", "Hemal Shah"), ["Hemanshu Patel"], "whatsapp", "made"),
        ("the chat with Anjali", "Anjali", rows_("Anjana Rao") + [t("No chats, contacts or messages found")], [NONE], "whatsapp", "made"),
        ("Shape of You by Ed Sheeran", "shape of you", [ln(x) for x in (
            "Shape of You (Piano Cover)", "Ed Sheeran - Shape of You (Official Music Video)", "Shape of You - 1 Hour Loop")],
         ["Ed Sheeran - Shape of You (Official Music Video)"], "youtube", "made"),
        ("the login redirect bug", "login", [r(x, "middle") for x in (
            "ENG-98 Add SSO login", "ENG-142 Fix login redirect loop", "ENG-77 Update redirect docs")], ["ENG-142 Fix login redirect loop"], "linear", "made"),
        ("Kurkure Masala Munch", "kurkure", [t(n) for n, _, _ in PRODUCTS], ["Kurkure Masala Munch"], "blinkit", "made"),
        ("Maggi noodles", "maggi", [t(n) for n, _, _ in PRODUCTS], [NONE], "blinkit", "made"),
        ("the invoice email from Rahul", "invoice", [r(x, "middle") for x in (
            "Rahul Sharma — Invoice for September", "HDFC Bank — Your statement is ready")], ["Rahul Sharma — Invoice for September"], "gmail", "made"),
        ("the Downloads folder", "downloads", rows_("Downloads", "Documents", "Desktop"), ["Downloads"], "Finder", "made"),
        ("the note about groceries", "grocer", [r("Groceries", "middle-left"), r("Ideas for the trip", "middle-left")], ["Groceries"], "Notes", "made"),
        ("my dentist reminder", "dentist", [r("Call dentist"), r("Pay electricity bill")], ["Call dentist"], "Reminders", "made"),
        ("Rahul from work", "Rahul", rows_("Rahul Verma", "Rahul Iyer (Acme)", "Rahul Gym"), ["Rahul Iyer (Acme)"], "Microsoft Teams", "made"),
        ("the family channel", "family", rows_("# family-chat", "# general", "# show-and-tell"), ["# family-chat"], "Discord", "made"),
        ("the contract email from Arjun", "contract", [r("Arjun Mehta — Contract renewal"), r("IT Desk — Password expires soon")],
         ["Arjun Mehta — Contract renewal"], "outlook", "made"),
        ("this year's budget spreadsheet", "budget", rows_("Budget 2025 (old).xlsx", "Budget 2026.xlsx"), ["Budget 2026.xlsx"], "drive", "made"),
        ("the pricing typo card", "pricing", rows_("Draft launch email", "Fix pricing page typo"), ["Fix pricing page typo"], "trello", "made"),
        ("Stranger Things season 4", "stranger things", [ln("Stranger Things 4"), ln("Stranger Things 3"), ln("Strange World")],
         ["Stranger Things 4"], "netflix", "made"),
        ("Domino's", "pizza", [ln("Dominic's Diner"), ln("Domino's Pizza"), ln("La Pino'z Pizza")], ["Domino's Pizza"], "swiggy", "made"),
        ("the Xcode update", "xcode", [t("Keynote 14.4"), t("Xcode 17.1")], ["Xcode 17.1"], "App Store", "made"),
        ("Priya from Acme", "priya", rows_("Priyanka Desai", "Supriya") + [t("No results found for 'priya'")], [NONE], "Microsoft Teams", "made"),
    ]
    return [Case("D5 name match", meaning, (meaning, searched, items), _set(*ans), src, app=tag.lower()) for meaning, searched, items, ans, tag, src in rows]


def d6_cases() -> list[Case]:
    rows = [
        ("order chips on Blinkit", dialog("Welcome to blinkit. Please provide your delivery location", "Detect my location", "search delivery location"),
         ["Detect my location"], "blinkit"),
        ("start Claude Code in the karta folder", dialog("Do you trust the files in this folder?", "1. Yes, proceed", "2. No, exit"),
         ["1. Yes, proceed"], "Terminal"),
        ("clean the build folder with Claude", dialog("Bash command rm -rf build/. Do you want to proceed?", "1. Yes",
                                                        "2. Yes, and don't ask again for rm commands", "3. No, and tell Claude what to do differently"),
         ["1. Yes", "2. Yes, and don't ask again for rm commands"], "Terminal"),
        ("close the note without saving", dialog("Do you want to save the changes made to the document “Untitled”?", "Don't Save", "Cancel", "Save"),
         ["Don't Save"], "TextEdit"),
        ("quit TextEdit and keep my changes", dialog("Do you want to save the changes made to the document “Untitled”?", "Don't Save", "Cancel", "Save"),
         ["Save"], "TextEdit"),
        ("let Karta control the Mac", dialog("“Karta” would like to control this computer using accessibility features.", "Open System Settings", "Deny"),
         ["Open System Settings"], "macOS"),
        ("order chips on Blinkit", dialog("blinkit.com wants to know your location", "Allow while visiting the site", "Allow this time", "Never allow"),
         ["Allow while visiting the site", "Allow this time"], "blinkit"),
        ("delete the note Groceries", dialog("Are you sure you want to delete this note?", "Delete", "Cancel"), ["Delete"], "Notes"),
        ("rename the note Groceries", dialog("Are you sure you want to delete this note?", "Delete", "Cancel"), ["Cancel"], "Notes"),
        ("copy report.pdf to the Desktop and keep both", dialog("An item named “report.pdf” already exists in this location.", "Keep Both", "Stop", "Replace"),
         ["Keep Both"], "Finder"),
        ("replace the old report.pdf on the Desktop", dialog("An item named “report.pdf” already exists in this location.", "Keep Both", "Stop", "Replace"),
         ["Replace"], "Finder"),
        ("invite Rahul to the standup", dialog("Would you like to send invitations to the new invitees?", "Don't Send", "Send"), ["Send"], "Calendar"),
        ("watch Shape of You on YouTube", dialog("Before you continue to YouTube. We use cookies", "Reject all", "Accept all", "More options"),
         ["Reject all", "Accept all"], "youtube"),
        ("send hi to Mom on WhatsApp", dialog("Turn on desktop notifications?", "Turn on", "Not now"), ["Not now"], "whatsapp"),
        ("open Brave and search flights", dialog("Brave is ready to update. Relaunch to finish.", "Relaunch", "Not now"), ["Not now"], "Brave Browser"),
        ("discard this draft email", dialog("Discard draft?", "Discard", "Cancel"), ["Discard"], "gmail"),
        ("order a pizza on Swiggy", dialog("Enter your delivery location", "Use my current location", "Enter area, street name"),
         ["Use my current location"], "swiggy"),
        ("watch Stranger Things on Netflix", dialog("Who's watching?", "Sam", "Kids", "Add Profile"), ["Sam"], "netflix"),
        ("reply to Arjun in Outlook", dialog("Stay signed in? Do this to reduce the number of times you are asked to sign in.", "No", "Yes"),
         ["Yes", "No"], "outlook"),
        ("archive the Trello card", dialog("Archive this card? You can find it later in the menu.", "Archive", "Cancel"), ["Archive"], "trello"),
    ]
    return [Case("D6 dialog answer", task, (task, items), _set(*ans), app=tag.lower()) for task, items, ans, tag in rows]


# -- D6 dialog role: part of the step, or an interruption? (the dialog's own items, marked as in a popup) ----------

def popup(*items: Item) -> list[Item]:
    return [replace(i, dialog=True) for i in items]


def chk(name, region="middle", checked=False, **kw) -> Item:
    return Item(name, "checkbox", region=region, checked=checked, **kw)


def ask_(text: str, *buttons: str) -> list[Item]:
    """A prompt: its text, then its buttons."""
    return popup(t(text, "middle"), *(b(x, "middle") for x in buttons))


PART, AWAY = PART_OF_STEP, INTERRUPTION


def d6r_cases() -> list[Case]:
    rows = [
        # detail panels, settings sheets, menus and pickers the step opened
        ("set the reminder's date to Friday", popup(h("Pay rent", "right"), chk("Remind me on a day", "right"), f("Date", "right"),
                                                    chk("Remind me at a time", "right"), f("Notes", "right"), b("Done", "right")), PART, "reminders"),
        ("schedule Night Shift from sunset to sunrise", popup(h("Night Shift", "middle"), Item("Schedule", "menu", value="Off", region="middle"),
                                                              chk("Turn on until tomorrow"), t("Color temperature"), b("Done", "bottom-right")), PART, "system settings"),
        ("create a new issue", popup(f("Type a command or search...", "top"), r("Create new issue", "middle"), r("Assign to...", "middle"),
                                     r("Change status...", "middle"), r("Set priority...", "middle")), PART, "linear"),
        ("move the standup to next Tuesday", popup(h("October 2026", "middle"), b("Previous month"), b("Next month"),
                                                   *(b(str(d)) for d in range(5, 12)), b("Today")), PART, "calendar"),
        ("share report.pdf by AirDrop", popup(t("report.pdf", "middle"), b("AirDrop"), b("Mail"), b("Messages"), b("Notes"), b("Edit Extensions…")), PART, "finder"),
        ("add a due date to the Send invoices card", popup(h("Send invoices", "middle"), b("Members"), b("Labels"), b("Checklist"), b("Dates"),
                                                            b("Attachment"), f("Add a more detailed description"), b("Close", "top-right")), PART, "trello"),
        ("set my status to Do Not Disturb", popup(r("Online", "bottom-left"), r("Idle", "bottom-left"), r("Do Not Disturb", "bottom-left"),
                                                  r("Invisible", "bottom-left"), r("Set a custom status", "bottom-left")), PART, "discord"),
        ("turn on English subtitles", popup(h("Audio", "bottom-right"), r("Hindi", "bottom-right"), r("English [Original]", "bottom-right"),
                                            h("Subtitles", "bottom-right"), r("Off", "bottom-right"), r("English (CC)", "bottom-right")), PART, "netflix"),
        ("assign the Send Q3 invoices task to me", popup(f("Name or email", "middle"), r("Sam (me)", "middle"), r("Rahul Sharma", "middle"),
                                                          r("Priya Nair", "middle")), PART, "asana"),
        ("lock the note", popup(h("Set a password for locked notes", "middle"), f("Password"), f("Verify"), f("Hint (recommended)"),
                                b("Cancel"), b("Set Password")), PART, "notes"),
        ("update Xcode", popup(h("Sign in to the App Store", "middle"), f("Email or Phone Number"), f("Password"), b("Cancel"), b("Sign In")), PART, "app store"),
        ("call Priya with audio", ask_("“Microsoft Teams” would like to access the microphone.", "Don't Allow", "OK"), PART, "microsoft teams"),
        # confirmations the step asked for
        ("delete the old invoices folder", ask_("Are you sure you want to delete “old invoices”? This item will be deleted immediately.", "Delete", "Cancel"),
         PART, "finder"),
        ("delete the Budget 2024 sheet", ask_("Move to trash? “Budget 2024” will be deleted forever after 30 days.", "Cancel", "Move to trash"), PART, "drive"),
        # interruptions: promos, what's new, notification prompts, cookie banners, update nags
        ("reply to Rahul's email", ask_("New! Summarise this email with one click.", "Got it", "Learn more"), AWAY, "gmail"),
        ("play a lo-fi playlist", ask_("Try YouTube Premium free for 1 month. Ad-free, offline, background play.", "Try it free", "No thanks"), AWAY, "youtube"),
        ("add a meeting with Rahul on Friday", ask_("“Calendar” would like to send you notifications.", "Don't Allow", "Allow"), AWAY, "calendar"),
        ("move the Send invoices card to Done", ask_("Upgrade to Trello Premium: views, automation and more.", "Start free trial", "Maybe later"), AWAY, "trello"),
        ("send the report to Arjun", ask_("Get Outlook for iOS and Android. Scan the QR code with your phone.", "Close"), AWAY, "outlook"),
        ("post the link in #general", ask_("discord.com wants to show notifications", "Allow", "Block"), AWAY, "discord"),
        ("upload the slides", ask_("Get Drive for desktop. Sync your files with your computer.", "Download", "No thanks"), AWAY, "drive"),
        ("order a pizza", ask_("Get 60% off on your first order! Use code WELCOME60.", "Copy code", "Close"), AWAY, "swiggy"),
        ("send the invoice to Rahul", ask_("What's new in Mail: Categories sort your inbox for you.", "Continue"), AWAY, "mail"),
        ("send hi to Mom", ask_("Turn on desktop notifications?", "Turn on", "Not now"), AWAY, "whatsapp"),
        ("search for flights to Goa", ask_("Brave is ready to update. Relaunch to finish.", "Relaunch", "Not now"), AWAY, "brave browser"),
        ("read the top story", ask_("We value your privacy. We use cookies to improve your experience.", "Accept", "Reject", "Manage choices"), AWAY, "safari"),
    ]
    return [Case("D6 dialog role", intent, (intent, items), _set(ans), app=tag) for intent, items, ans, tag in rows]


def d7_cases() -> list[Case]:
    rows = [
        ("message 'see you at 5' sent", whatsapp_chat(sent="see you at 5"), SHOWN),
        ("message 'see you at 5' sent", whatsapp_chat(typed="see you at 5"), NOT_SHOWN),
        ("Kurkure Masala Munch in the cart", blinkit_cart(), SHOWN),
        ("Pringles in the cart", blinkit_cart(), NOT_SHOWN),
        ("the command menu is open", linear_command_menu(), SHOWN),
        ("email sent", gmail_sent(), SHOWN),
        ("email sent", gmail_compose(), NOT_SHOWN),
        ("the Shape of You video is playing", youtube_playing(), SHOWN),
        ("the Shape of You video is playing", youtube_results(), NOT_SHOWN),
        ("the Downloads folder is open", finder("Downloads"), SHOWN),
        ("the Downloads folder is open", finder("Desktop"), NOT_SHOWN),
        ("a new note is open", notes(folder_selected=True), SHOWN),
        ("Claude Code is running", claude_ready(), SHOWN),
        ("Claude Code is running", terminal(), NOT_SHOWN),
        ("the reminder Call dentist has details open", reminders(info=True), SHOWN),
        ("an event called Dentist on the calendar", calendar(), NOT_SHOWN),
        ("a card called Book venue in To Do", trello("Book venue"), SHOWN),
        ("a card called Book venue in To Do", trello(), NOT_SHOWN),
        ("the reply was sent", outlook(sent=True), SHOWN),
        ("in the design sync call", teams(in_call=True), SHOWN),
        ("in the design sync call", teams(), NOT_SHOWN),
        ("message 'on my way' posted", discord("on my way"), SHOWN),
        ("Stranger Things is playing", netflix_playing(), SHOWN),
    ]
    return [Case("D7 outcome", f"{exp} on {s.window}", (exp, s), _set(ans), app=screen_app(s)) for exp, s, ans in rows]



# -- held-out screens: apps, sites and dialogs that appear nowhere in tuning or the scenario traces ----------------------

def slack() -> Screen:
    return app("Slack", "engineering (Channel) - Acme Inc - Slack", [
        b("Acme Inc", "top-left"), f("Search Acme Inc", "top"), ln("Home", "left"), ln("DMs", "left"), ln("Activity", "left"),
        ln("Threads", "left"), ln("Huddles", "left"), ln("Drafts & sent", "left"), t("Channels", "left"),
        r("# general"), r("# engineering"), r("# random"), t("Direct messages", "left"), r("Priya Nair"), r("Arjun Rao"),
        h("# engineering", "top"), b("Huddle", "top-right"), b("Channel details", "top-right"),
        t("Arjun Rao  11:02", "middle"), t("deploy to staging is green", "middle"), t("Priya Nair  11:05", "middle"),
        t("nice, shipping the release notes now", "middle"),
        f("Message #engineering", "bottom"), b("Attach", "bottom-left"), b("Send now", "bottom-right", enabled=False),
    ], "File > New Message", "View > Toggle Sidebar")


def slack_sent(text: str) -> Screen:
    s = slack()
    at = next(n for n, i in enumerate(s.items) if i.name == "Message #engineering")
    s.items[at:at] = [t("Sam  11:07", "middle"), t(text, "middle")]
    return s


def slack_signin() -> Screen:
    return site("https://app.slack.com/signin", "Sign in | Slack", [
        Item("slack", "image", region="top"), h("Sign in to Slack", "middle"), t("We suggest using the email address you use at work.", "middle"),
        b("Sign in with Google", "middle"), b("Sign in with Apple", "middle"), t("OR", "middle"),
        f("name@work-email.com", "middle"), b("Sign in with email", "middle"), t("New to Slack?", "bottom"), ln("Create an account", "bottom"),
    ])


def slack_loading() -> Screen:
    return app("Slack", "Slack", [Item("Slack", "image", region="middle"), t("Loading Acme Inc…", "middle")])


SONGS = [("Blinding Lights", "The Weeknd", "3:20"), ("Save Your Tears", "The Weeknd", "3:35"),
         ("Blinding Lights (Chromatics Remix)", "The Weeknd", "4:10"), ("Starboy", "The Weeknd, Daft Punk", "3:50")]


def spotify(query: str = "") -> Screen:
    items = [
        ln("Home", "top-left"), f("What do you want to play?", "top", value=query), ln("Your Library", "left"),
        r("Liked Songs", value="Playlist · 214 songs"), r("Discover Weekly", value="Playlist · Spotify"), r("Daily Mix 1", value="Playlist"),
        b("Install App", "top-right"), b("Profile", "top-right"),
    ]
    if query:
        items += [h("Top result", "middle-left"), t("Blinding Lights", "middle-left"), t("Song · The Weeknd", "middle-left"), h("Songs", "middle-right")]
        for name, artist, dur in SONGS:
            items += [t(name, "middle-right"), t(artist, "middle-right"), t(dur, "middle-right"), b("Play", "middle-right", row=(name, artist, dur))]
    else:
        items += [h("Good evening", "top"), ln("Made For You", "middle"), ln("Recently played", "middle")]
    items += [b("Shuffle", "bottom"), b("Previous", "bottom"), b("Play", "bottom", row=("Previous", "Next")), b("Next", "bottom"), b("Repeat", "bottom")]
    return app("Spotify", "Spotify Premium", items, "Playback > Play", "Playback > Next")


def spotify_playing() -> Screen:
    s = spotify()
    s.items = [i for i in s.items if not (i.name == "Play" and i.region == "bottom")] + [
        t("Blinding Lights", "bottom-left"), t("The Weeknd", "bottom-left"), b("Pause", "bottom"), t("0:45", "bottom"), t("3:20", "bottom"),
    ]
    s.window = "The Weeknd - Blinding Lights"
    return s


def spotify_login() -> Screen:
    return site("https://accounts.spotify.com/en/login", "Login - Spotify", [
        Item("Spotify", "image", region="top"), h("Log in to Spotify", "middle"), b("Continue with Google", "middle"),
        b("Continue with Facebook", "middle"), b("Continue with phone number", "middle"), f("Email or username", "middle"),
        f("Password", "middle"), b("Log In", "middle"), ln("Forgot your password?", "middle"), ln("Sign up for Spotify", "bottom"),
    ], "Personal")


def docs_list() -> Screen:
    return site("https://docs.google.com/document/u/0/", "Google Docs", [
        b("Main menu", "top-left"), Item("Docs", "image", region="top-left"), f("Search", "top"), b("Google apps", "top-right"),
        h("Start a new document", "top"), b("Blank document", "top"), b("Resume", "top"), b("Letter", "top"), b("Project proposal", "top"),
        h("Recent documents", "middle"), r("Q3 Planning", "middle", value="Opened 10:12"), r("Team offsite notes", "middle", value="Opened Sep 26"),
        r("Resume 2026", "middle", value="Opened Sep 2"), b("Sort options", "middle-right"),
    ])


def doc_open(title: str = "Untitled document", body: str = "") -> Screen:
    return site("https://docs.google.com/document/d/1xYz/edit", f"{title} - Google Docs", [
        f("Rename", "top-left", value=title), b("Star", "top-left"), b("Move", "top-left"), b("File", "top-left"), b("Edit", "top-left"),
        b("Insert", "top-left"), b("Format", "top-left"), b("Share", "top-right"), b("Comments", "top-right"),
        b("Bold (⌘B)", "top"), b("Italic (⌘I)", "top"), b("Undo (⌘Z)", "top"), t("Saved to Drive", "top"),
        Item("Document content", "field", value=body, region="middle", focused=True),
    ])


def jira_board() -> Screen:
    return site("https://acme.atlassian.net/jira/software/projects/KAN/boards/1", "KAN board - Jira", [
        b("App switcher", "top-left"), ln("Your work", "top-left"), ln("Projects", "top-left"), ln("Filters", "top-left"),
        b("Create", "top"), f("Search", "top-right"), b("Notifications", "top-right"), ln("Timeline", "left"), ln("Backlog", "left"),
        ln("Board", "left"), h("KAN board", "top"), f("Search board", "top"), t("TO DO 2", "middle-left"),
        r("KAN-12 Payment page times out", "middle-left"), r("KAN-15 Update onboarding copy", "middle-left"), t("IN PROGRESS 1", "middle"),
        r("KAN-9 Migrate invoices table", "middle"), t("DONE 1", "middle-right"), r("KAN-3 Set up CI", "middle-right"),
    ])


def jira_create() -> Screen:
    s = jira_board()
    s.items += [h("Create issue", "middle"), b("Project: Kanban (KAN)", "middle"), b("Issue type: Task", "middle"),
                f("Summary", "middle", focused=True), f("Description", "middle"), b("Assignee: Automatic", "middle"),
                b("Create", "bottom-right"), b("Cancel", "bottom-right")]
    return s


def jira_404() -> Screen:
    return site("https://acme.atlassian.net/browse/KAN-999", "Jira", [
        ln("Your work", "top-left"), ln("Projects", "top-left"), b("Create", "top"),
        Item("Broken link illustration", "image", region="middle"), h("This issue can't be found", "middle"),
        t("It may have been deleted, or you don't have permission to view it.", "middle"), b("Go to your work", "middle"),
    ])


CABLES = [("Amazon Basics USB-C to USB-C Cable, 1.8 m", "₹399"), ("Anker USB C Cable 240W, 1.8 m", "₹699"),
          ("boAt Type C A320 Cable, 1.5 m", "₹249"), ("Portronics Konnect Cable 3A", "₹199")]


def amazon_results() -> Screen:
    page = [Item("Amazon.in", "link", region="top-left"), b("Deliver to Sam 110001", "top-left"), f("Search Amazon.in", "top", value="usb c cable"),
            b("Go", "top"), b("Account & Lists", "top-right"), b("Returns & Orders", "top-right"), b("Cart 0", "top-right"),
            t('1-16 of over 3,000 results for "usb c cable"', "top"), b("Sort by: Featured", "top-right")]
    for n, (name, price) in enumerate(CABLES):
        where = "middle-left" if n % 2 == 0 else "middle-right"
        page += [ln(name, where), t(price, where), t("FREE delivery Tomorrow", where), b("Add to cart", where, row=(name, price))]
    return site("https://www.amazon.in/s?k=usb+c+cable", "Amazon.in : usb c cable", page, "Personal")


def amazon_cart() -> Screen:
    page = [Item("Amazon.in", "link", region="top-left"), f("Search Amazon.in", "top"), b("Cart 2", "top-right"), h("Shopping Cart", "top")]
    for name, price in CABLES[1:3]:
        page += [ln(name, "middle"), t(price, "middle-right"), b("Qty: 1", "middle", row=(name,)), b("Delete", "middle", row=(name,)),
                 b("Save for later", "middle", row=(name,))]
    page += [t("Subtotal (2 items): ₹948", "right"), b("Proceed to Buy", "right")]
    return site("https://www.amazon.in/gp/cart/view.html", "Amazon.in Shopping Cart", page, "Personal")


def amazon_signin() -> Screen:
    return site("https://www.amazon.in/ap/signin", "Amazon Sign In", [
        Item("Amazon", "image", region="top"), h("Sign in", "middle"), f("Email or mobile phone number", "middle"),
        b("Continue", "middle"), t("By continuing, you agree to Amazon's Conditions of Use", "middle"), ln("Need help?", "middle"),
        h("New to Amazon?", "bottom"), b("Create your Amazon account", "bottom"),
    ], "Personal")


def figma_files() -> Screen:
    return app("Figma", "Recents – Figma", [
        f("Search", "top-left"), ln("Recents", "left"), ln("Drafts", "left"), ln("All projects", "left"), ln("Trash", "left"),
        b("New design file", "top-right"), b("New FigJam board", "top-right"), b("Import", "top-right"), h("Recents", "top"),
        r("Landing page v3", "middle", value="Edited 2 hours ago"), r("Mobile app wireframes", "middle", value="Edited yesterday"),
        r("Brand colors", "middle", value="Edited last week"),
    ], "File > New Design File")


def figma_login() -> Screen:
    return app("Figma", "Figma", [
        Item("Figma", "image", region="top"), h("Welcome to Figma", "middle"), b("Log in with browser", "middle"),
        t("Log in or create an account to collaborate", "middle"), ln("Sign up", "bottom"),
    ])


def github_repo() -> Screen:
    return site("https://github.com/acme/payments-api", "acme/payments-api", [
        b("Open global navigation menu", "top-left"), ln("acme", "top-left"), ln("payments-api", "top-left"),
        f("Type / to search", "top"), b("Create something new", "top-right"), ln("Issues 12", "top-right"),
        ln("Pull requests 3", "top-right"), b("Open user navigation menu", "top-right"),
        tab("Code", "top"), tab("Issues 12", "top"), tab("Pull requests 3", "top"), tab("Actions", "top"), tab("Settings", "top"),
        b("Watch 4", "top-right"), b("Fork 2", "top-right"), b("Star 31", "top-right"), b("main", "middle-left"),
        f("Go to file", "middle"), b("Code", "middle-right", row=("Go to file", "Add file")), r(".github", "middle"),
        r("src", "middle"), r("tests", "middle"), r("README.md", "middle"), r("pyproject.toml", "middle"),
    ])


def github_prs() -> Screen:
    s = github_repo()
    s.items = [i for i in s.items if i.kind != "row"] + [
        h("Pull requests", "top"), r("Retry webhooks on 5xx #41", "middle", value="Open"), r("Bump httpx to 0.28 #40", "middle", value="Open"),
        r("Add refunds endpoint #38", "middle", value="Open"), b("New pull request", "top-right"),
    ]
    s.url, s.window = "https://github.com/acme/payments-api/pulls", "Pull requests · acme/payments-api"
    return s


def github_404() -> Screen:
    return site("https://github.com/acme/payment-api", "Page not found · GitHub", [
        f("Type / to search", "top"), ln("Sign in", "top-right"), Item("404 This is not the web page you are looking for", "image", region="middle"),
        t("Find code, projects, and people on GitHub:", "middle"), f("Search", "middle"), ln("Contact Support", "bottom"),
    ])


def github_signin() -> Screen:
    return site("https://github.com/login", "Sign in to GitHub · GitHub", [
        Item("GitHub", "image", region="top"), h("Sign in to GitHub", "middle"), f("Username or email address", "middle"),
        f("Password", "middle"), ln("Forgot password?", "middle"), b("Sign in", "middle"), b("Sign in with a passkey", "middle"),
        t("New to GitHub?", "bottom"), ln("Create an account", "bottom"),
    ], "Test")


def zoom() -> Screen:
    return app("zoom.us", "Zoom Workplace", [
        ln("Home", "top-left"), ln("Team Chat", "top-left"), ln("Meetings", "top-left"), f("Search", "top"),
        b("New meeting", "middle-left"), b("Join", "middle-left"), b("Schedule", "middle-left"), b("Share screen", "middle-left"),
        t("11:15", "middle-right"), t("Tuesday, September 29", "middle-right"), r("Sprint planning", "middle-right", value="12:00 - 13:00"),
        b("Start", "middle-right", row=("Sprint planning",)),
    ], "zoom.us > Settings")


def zoom_join() -> Screen:
    return app("zoom.us", "Join meeting", [
        h("Join meeting", "top"), f("Meeting ID or personal link name", "middle", focused=True), f("Enter your name", "middle", value="Sam"),
        Item("Remember my name for future meetings", "checkbox", region="middle", checked=True),
        Item("Don't connect to audio", "checkbox", region="middle", checked=False), Item("Turn off my video", "checkbox", region="middle", checked=False),
        b("Join", "bottom-right", enabled=False), b("Cancel", "bottom-right"),
    ])


def zoom_camera_prompt() -> Screen:
    s = zoom_join()
    s.items += [t("“zoom.us” would like to access the camera.", "middle"), b("Don't Allow", "middle"), b("OK", "middle")]
    return s


def airbnb(cookies: bool = False) -> Screen:
    page = [
        Item("Airbnb homepage", "link", region="top-left"), b("Goa · Oct 10 – 14 · 2 guests", "top"), b("Filters", "top-right"),
        b("Price range", "top"), b("Airbnb your home", "top-right"), b("Main navigation menu", "top-right"),
        t("Over 1,000 places within map area", "top-left"),
        ln("Villa in Anjuna · ★ 4.92 · Sea-view pool villa · ₹18,400 night", "middle-left"),
        ln("Apartment in Candolim · ★ 4.81 · 2 min to beach · ₹6,200 night", "middle-left"),
        ln("Cottage in Assagao · ★ 4.97 · Garden cottage · ₹9,800 night", "middle-left"),
        b("Map", "right"), b("Show list", "bottom"),
    ]
    if cookies:
        page += [h("Your privacy", "bottom"), t("We use cookies to personalise content and ads.", "bottom"),
                 b("Accept all", "bottom"), b("Only necessary", "bottom"), b("Cookie preferences", "bottom")]
    return site("https://www.airbnb.co.in/s/Goa/homes", "Goa · Stays · Airbnb", page, "Personal")


def airbnb_error() -> Screen:
    return site("https://www.airbnb.co.in/rooms/0", "Airbnb", [
        Item("Airbnb homepage", "link", region="top-left"), h("Oops!", "middle"), t("We can't seem to find the page you're looking for.", "middle"),
        t("Error code: 404", "middle"), ln("Home", "middle"), ln("Help", "middle"),
    ], "Personal")


def notion(page_title: str = "Meeting notes") -> Screen:
    return app("Notion", f"{page_title} - Notion", [
        b("Sam's Notion", "top-left"), ln("Search", "left"), ln("Home", "left"), ln("Inbox", "left"), t("Private", "left"),
        r("Roadmap"), r("Meeting notes"), r("Reading list"), r("Travel plans"), b("New page", "left"), ln("Settings", "left"), ln("Trash", "left"),
        h(page_title, "top"), b("Share", "top-right"), b("Favorite", "top-right"), b("More actions", "top-right"),
        t("Sep 29 – Sprint sync", "middle"), t("Action items: ship refunds, review hiring plan", "middle"),
    ], "File > New Page")


def notion_login() -> Screen:
    return site("https://www.notion.so/login", "Notion – Log in", [
        Item("Notion", "image", region="top-left"), h("Think it. Make it.", "middle"), t("Log in to your Notion account", "middle"),
        b("Continue with Google", "middle"), b("Continue with Apple", "middle"), f("Enter your email address...", "middle"),
        b("Continue", "middle"), ln("Forgot password?", "bottom"),
    ])


def notion_loading() -> Screen:
    return app("Notion", "Notion", [Item("Notion", "image", region="middle"), t("Loading…", "middle")])


def weather() -> Screen:
    return app("Weather", "Weather", [
        f("Search for a city or airport", "top-left"), r("My Location", value="Ahmedabad 31°"), r("Mumbai", value="29°"),
        h("Ahmedabad", "top"), t("31°", "top"), t("Partly Cloudy", "top"), t("H:34° L:24°", "top"),
        t("10-DAY FORECAST", "middle"), t("Today  24°  34°", "middle"), t("Wed  25°  35°", "middle"),
    ])


def photos() -> Screen:
    return app("Photos", "Library", [
        f("Search", "top-right"), ln("Library", "left"), ln("Collections", "left"), tab("Years", "top"), tab("Months", "top"),
        tab("All Photos", "top"), t("Goa trip · Oct 2025", "middle"), Item("Photo, 12 October 2025", "image", region="middle"),
    ])


# -- held-out cases -------------------------------------------------------------------------------------------------

def _held(cases: list[Case]) -> list[Case]:
    return [Case(c.decision, c.name, c.args, c.answers, c.source, "held", c.app.lower()) for c in cases]


def _place_app(what: str, answers: list[str]) -> str:
    """The app or site a D1 case is about: its right place, or the app named in what was wanted."""
    if answers[0] == NONE:
        return (what.split()[1] if len(what.split()) > 1 else what).lower()
    place = answers[0].split(" in Brave")[0]
    return place.removesuffix(" app").lower() if place.endswith(" app") else site_name(place)


def h1_cases() -> list[Case]:
    rows = [
        ("Slack messages", ["Slack app", "Notes app", "Music app"], ["Slack app"]),
        ("a song on Spotify", ["Photos app", "Spotify app", "Weather app"], ["Spotify app"]),
        ("a Google Doc", ["Pages app", "Preview app", *_sites("docs.google.com")], _sites("docs.google.com")),
        ("Jira tickets", ["Numbers app", "Notion app", *_sites("acme.atlassian.net")], _sites("acme.atlassian.net")),
        ("buy a USB cable", ["Slack app", "Preview app", *_sites("amazon.in")], _sites("amazon.in")),
        ("Figma designs", ["Preview app", "Photos app", "Figma app"], ["Figma app"]),
        ("GitHub pull requests", ["Xcode app", "Notion app", *_sites("github.com")], _sites("github.com")),
        ("a Zoom meeting", ["zoom.us app", "Photos app", "Numbers app"], ["zoom.us app"]),
        ("a stay in Goa for the weekend", ["Maps app", "Photos app", *_sites("airbnb.co.in")], _sites("airbnb.co.in")),
        ("my Notion pages", ["TextEdit app", "Notion app", "Preview app"], ["Notion app"]),
        ("a PDF to sign", ["Preview app", "Music app", "Weather app"], ["Preview app"]),
        ("photos from the Goa trip", ["Numbers app", "Photos app", "zoom.us app"], ["Photos app"]),
        ("directions to the airport", ["Weather app", "Maps app", "Notion app"], ["Maps app"]),
        ("a spreadsheet of expenses", ["Photos app", "Maps app", "Numbers app"], ["Numbers app"]),
        ("a Discord server", ["Slack app", "zoom.us app", "Notion app"], [NONE]),
        ("a Netflix show", ["Spotify app", "Photos app", "Maps app"], [NONE]),
        ("my VS Code project", ["Visual Studio Code app", "Pages app", "Photos app"], ["Visual Studio Code app"]),
        ("the weather tomorrow", ["Maps app", "Weather app", "Notion app"], ["Weather app"]),
        ("a FaceTime call with Mom", ["FaceTime app", "Photos app", "Numbers app"], ["FaceTime app"]),
        ("an Uber ride", ["Weather app", "Photos app", "Numbers app"], [NONE]),
        ("an Excel sheet", ["Photos app", "Microsoft Excel app", "Preview app"], ["Microsoft Excel app"]),
    ]
    return _held([Case("D1 place", what, (what, places), frozenset(ans), app=_place_app(what, ans)) for what, places, ans in rows])


def h2_cases() -> list[Case]:
    rows = [
        ("Slack", slack(), READY), ("Slack", slack_signin(), LOGIN), ("Slack", slack_loading(), LOADING), ("Slack", photos(), ELSEWHERE),
        ("Spotify", spotify(), READY), ("Spotify", spotify_login(), LOGIN), ("Spotify", weather(), ELSEWHERE),
        ("Google Docs", docs_list(), READY), ("Google Docs", notion(), ELSEWHERE),
        ("Jira board", jira_board(), READY), ("Jira board", jira_404(), NOT_FOUND), ("Jira board", jira_create(), DIALOG),
        ("Amazon shopping", amazon_results(), READY), ("Amazon shopping", amazon_signin(), LOGIN),
        ("Figma files", figma_files(), READY), ("Figma files", figma_login(), LOGIN),
        ("GitHub repository", github_repo(), READY), ("GitHub repository", github_404(), NOT_FOUND), ("GitHub repository", github_signin(), LOGIN),
        ("GitHub repository", airbnb(), ELSEWHERE),
        ("Zoom meetings", zoom(), READY), ("Zoom meetings", zoom_camera_prompt(), DIALOG),
        ("Airbnb stays", airbnb(), READY), ("Airbnb stays", airbnb(cookies=True), DIALOG), ("Airbnb stays", airbnb_error(), NOT_FOUND),
        ("Notion pages", notion(), READY), ("Notion pages", notion_login(), LOGIN), ("Notion pages", notion_loading(), LOADING),
        ("Weather", weather(), READY),
        ("my music", spotify(), READY), ("team chat", slack(), READY), ("video calls", zoom(), READY), ("design files", figma_files(), READY),
        ("Figma files", photos(), ELSEWHERE), ("Spotify", spotify_playing(), READY), ("GitHub pull requests", github_prs(), READY),
    ]
    return _held([Case("D2 page state", f"{what} on {s.app} {s.window}", (what, s), _set(ans), app=screen_app(s)) for what, s, ans in rows])


def h3_cases() -> list[Case]:
    rows = [
        ("the message box", slack(), ["Message #engineering"]),
        ("go to the engineering channel", slack(), ["# engineering"]),
        ("DM Priya", slack(), ["Priya Nair"]),
        ("the search box", spotify(), ["What do you want to play?"]),
        ("play Save Your Tears", spotify("the weeknd"), ["Play (Save Your Tears)"]),
        ("my saved music", spotify(), ["Your Library", "Liked Songs"]),
        ("skip to the next song", spotify_playing(), ["Next"]),
        ("start an empty document", docs_list(), ["Blank document"]),
        ("share the doc with Priya", doc_open(), ["Share"]),
        ("file a new ticket", jira_board(), ["Create"]),
        ("the summary field", jira_create(), ["Summary"]),
        ("add the Anker cable to the cart", amazon_results(), ["Add to cart (Anker USB C Cable 240W,...)"]),
        ("check out", amazon_cart(), ["Proceed to Buy"]),
        ("remove the boAt cable", amazon_cart(), ["Delete (boAt Type C A320 Cable,...)"]),
        ("create a new design", figma_files(), ["New design file"]),
        ("see the open pull requests", github_repo(), ["Pull requests 3"]),
        ("star this repository", github_repo(), ["Star 31"]),
        ("find a file", github_repo(), ["Go to file"]),
        ("join with my camera off", zoom_join(), ["Turn off my video"]),
        ("the meeting ID box", zoom_join(), ["Meeting ID or personal link name"]),
        ("make a new page", notion(), ["New page"]),
        ("delete the workspace", notion(), [NONE]),
        ("log out", spotify(), [NONE]),
        ("show only cheaper places", airbnb(), ["Price range", "Filters"]),
        ("start the sprint planning meeting", zoom(), ["Start"]),
        ("switch branch", github_repo(), ["main"]),
        ("open the readme", github_repo(), ["README.md"]),
    ]
    return _held([Case("D3 target", intent, (intent, s.page()), _set(*ans), app=screen_app(s)) for intent, s, ans in rows])


def h4_cases() -> list[Case]:
    adds = {i.row[0]: i for i in amazon_results().page() if i.name == "Add to cart"}
    plays = {i.row[0]: i for i in spotify("the weeknd").page() if i.name == "Play" and i.row and i.row[0] in dict((n, 1) for n, _, _ in SONGS)}
    rows = [
        ("the message box", f("Message #engineering", "bottom"), DOES_IT, "slack"),
        ("the message box", f("Search Acme Inc", "top"), DOES_OTHER, "slack"),
        ("add the Anker cable to the cart", adds["Anker USB C Cable 240W, 1.8 m"], DOES_IT, "amazon"),
        ("add the Anker cable to the cart", adds["boAt Type C A320 Cable, 1.5 m"], DOES_OTHER, "amazon"),
        ("play Save Your Tears", plays["Save Your Tears"], DOES_IT, "Spotify"),
        ("play Save Your Tears", plays["Starboy"], DOES_OTHER, "Spotify"),
        ("share the doc", b("Share", "top-right"), DOES_IT, "docs"),
        ("share the doc", b("Star", "top-left"), DOES_OTHER, "docs"),
        ("create a Jira ticket", b("Create", "top"), DOES_IT, "atlassian"),
        ("create a Jira ticket", ln("Filters", "top-left"), DOES_OTHER, "atlassian"),
        ("check out", b("Proceed to Buy", "right"), DOES_IT, "amazon"),
        ("check out", b("Save for later", "middle"), DOES_OTHER, "amazon"),
        ("open the pull requests", ln("Pull requests 3", "top-right"), DOES_IT, "github"),
        ("open the pull requests", tab("Actions", "top"), DOES_OTHER, "github"),
        ("join the meeting with video off", Item("Turn off my video", "checkbox", region="middle", checked=False), DOES_IT, "zoom.us"),
        ("join the meeting with video off", Item("Don't connect to audio", "checkbox", region="middle", checked=False), DOES_OTHER, "zoom.us"),
        ("make a new Notion page", b("New page", "left"), DOES_IT, "Notion"),
        ("make a new Notion page", ln("Trash", "left"), DOES_OTHER, "Notion"),
        ("filter stays by price", b("Price range", "top"), DOES_IT, "airbnb"),
        ("filter stays by price", b("Airbnb your home", "top-right"), DOES_OTHER, "airbnb"),
        ("find a file in the repo", f("Go to file", "middle"), DOES_IT, "github"),
        ("start a FigJam board", b("New FigJam board", "top-right"), DOES_IT, "Figma"),
        ("start a FigJam board", b("New design file", "top-right"), DOES_OTHER, "Figma"),
        ("start the sprint planning meeting", b("Start", "middle-right", row=("Sprint planning",)), DOES_IT, "zoom.us"),
        ("look up the weather in Mumbai", f("Search for a city or airport", "top-left"), DOES_IT, "Weather"),
    ]
    return _held([Case("D4 pick check", f"{intent} -> {item.name}", (intent, item), _set(ans), app=tag.lower()) for intent, item, ans, tag in rows])


def h5_cases() -> list[Case]:
    rows_ = lambda *names: [r(n) for n in names]  # noqa: E731
    rows = [
        ("Priya from engineering", "priya", rows_("Priya Nair", "Priyanka Shah (Sales)", "Supriya K"), ["Priya Nair"], "slack"),
        ("the engineering channel", "eng", rows_("# engineering", "# eng-hiring", "# english-club"), ["# engineering"], "slack"),
        ("Blinding Lights by The Weeknd", "blinding lights", [t(n) for n, _, _ in SONGS], ["Blinding Lights"], "Spotify"),
        ("the remix of Blinding Lights", "blinding lights", [t(n) for n, _, _ in SONGS], ["Blinding Lights (Chromatics Remix)"], "Spotify"),
        ("Levitating by Dua Lipa", "levitating", [t(n) for n, _, _ in SONGS[:3]], [NONE], "Spotify"),
        ("the Q3 planning doc", "planning", rows_("Q3 Planning", "Q2 Planning (old)", "Planning template"), ["Q3 Planning"], "docs"),
        ("the payment timeout ticket", "payment", rows_("KAN-12 Payment page times out", "KAN-20 Payment receipts email", "KAN-9 Migrate invoices table"),
         ["KAN-12 Payment page times out"], "atlassian"),
        ("the cheapest USB-C cable", "usb c cable", [ln(n) for n, _ in CABLES], ["Portronics Konnect Cable 3A"], "amazon"),
        ("an Anker cable", "usb c cable", [ln(n) for n, _ in CABLES], ["Anker USB C Cable 240W, 1.8 m"], "amazon"),
        ("a Lightning cable", "lightning cable", [ln(n) for n, _ in CABLES[:3]], [NONE], "amazon"),
        ("the payments-api repository", "payments", rows_("acme/payments-api", "acme/payments-web", "octo/payments-demo"), ["acme/payments-api"], "github"),
        ("the refunds pull request", "refunds", rows_("Retry webhooks on 5xx #41", "Add refunds endpoint #38", "Bump httpx to 0.28 #40"),
         ["Add refunds endpoint #38"], "github"),
        ("my reading list page", "reading", rows_("Reading list", "Roadmap", "Travel plans"), ["Reading list"], "Notion"),
        ("the sprint planning meeting", "sprint", rows_("Sprint planning", "Sprint retro (last week)", "1:1 with Arjun"), ["Sprint planning"], "zoom.us"),
        ("the villa with a pool in Anjuna", "anjuna", [ln(i.name) for i in airbnb().page() if "night" in i.name],
         ["Villa in Anjuna · ★ 4.92 · Sea-view pool villa · ₹18,400 night"], "airbnb"),
        ("the landing page design", "landing", rows_("Landing page v3", "Landing page v2 (archived)", "Mobile app wireframes"), ["Landing page v3"], "Figma"),
        ("the Mumbai weather", "mumbai", rows_("My Location", "Mumbai", "Mumbra"), ["Mumbai"], "Weather"),
        ("the chat with Kavya", "kavya", rows_("Kavita Rao", "Kabir", "Arjun Rao"), [NONE], "slack"),
        ("the readme", "readme", rows_("src", "README.md", "pyproject.toml"), ["README.md"], "github"),
        ("my weekly discovery playlist", "discover", rows_("Liked Songs", "Discover Weekly", "Daily Mix 1"), ["Discover Weekly"], "Spotify"),
        ("the Tokyo weather", "tokyo", rows_("My Location", "Mumbai"), [NONE], "Weather"),
    ]
    return _held([Case("D5 name match", meaning, (meaning, searched, items), _set(*ans), app=tag.lower()) for meaning, searched, items, ans, tag in rows])


def h6_cases() -> list[Case]:
    rows = [
        ("join the standup on Zoom with video", dialog("“zoom.us” would like to access the camera.", "Don't Allow", "OK"), ["OK"], "zoom.us"),
        ("record a voice memo in Slack", dialog("“Slack” would like to access the microphone.", "Don't Allow", "OK"), ["OK"], "slack"),
        ("share my screen in the Zoom call", dialog("“zoom.us” would like to record this computer's screen and audio.", "Open System Settings", "Deny"),
         ["Open System Settings"], "zoom.us"),
        ("push my changes to GitHub", dialog("git-credential-osxkeychain wants to use your confidential information stored in “github.com” in your keychain.",
                                             "Always Allow", "Deny", "Allow"), ["Allow", "Always Allow"], "github"),
        ("book a stay on Airbnb", dialog("Your privacy. We use cookies to personalise content and ads.", "Accept all", "Only necessary", "Cookie preferences"),
         ["Accept all", "Only necessary"], "airbnb"),
        ("open the installer I downloaded", dialog("“installer.pkg” is an app downloaded from the Internet. Are you sure you want to open it?", "Cancel", "Open"),
         ["Open"], "macOS"),
        ("empty the Trash", dialog("Are you sure you want to permanently erase the items in the Trash?", "Cancel", "Empty Trash"), ["Empty Trash"], "macOS"),
        ("look at what is in the Trash", dialog("Are you sure you want to permanently erase the items in the Trash?", "Cancel", "Empty Trash"), ["Cancel"], "macOS"),
        ("post the release notes in Slack", dialog("macOS Tahoe 26.1 is available. Install now?", "Install Now", "Remind Me Later"), ["Remind Me Later"], "macOS"),
        ("leave the #random channel", dialog("Leave #random? You can rejoin at any time.", "Cancel", "Leave channel"), ["Leave channel"], "slack"),
        ("create the Jira ticket", dialog("Discard changes? Your issue has not been created.", "Keep editing", "Discard"), ["Keep editing"], "atlassian"),
        ("close the Google Doc without keeping the edits", dialog("You have unsaved changes. Leave site?", "Leave", "Cancel"), ["Leave"], "docs"),
        ("play music on Spotify", dialog("Spotify wants to show notifications.", "Allow", "Don't Allow"), ["Don't Allow", "Allow"], "Spotify"),
        ("delete the Brand colors file", dialog("Move “Brand colors” to trash? Collaborators will lose access.", "Cancel", "Move to trash"), ["Move to trash"], "Figma"),
        ("join the meeting with computer audio", dialog("Choose one of the audio conference options", "Join with Computer Audio", "Test Speaker and Microphone"),
         ["Join with Computer Audio"], "zoom.us"),
        ("buy the Anker cable", dialog("Choose your location. Delivery options and speeds may vary.", "Sign in to see your addresses", "Enter a pincode", "Continue"),
         ["Continue", "Enter a pincode", "Sign in to see your addresses"], "amazon"),
        ("merge the refunds pull request", dialog("Merge pull request #38 from acme/refunds?", "Confirm merge", "Cancel"), ["Confirm merge"], "github"),
        ("open the doc Q3 Planning", dialog("Get the Google Docs app for a better experience", "Use the app", "Not now"), ["Not now"], "docs"),
        ("open the Landing page file", dialog("Open Figma? figma.com wants to open this application.", "Cancel", "Open Figma"), ["Open Figma"], "Figma"),
        ("check the weather where I am", dialog("Allow “Weather” to use your location?", "Allow Once", "Allow While Using App", "Don't Allow"),
         ["Allow Once", "Allow While Using App"], "Weather"),
        ("write the meeting notes in Notion", dialog("Import your notes from Evernote?", "Import", "Skip for now"), ["Skip for now"], "Notion"),
    ]
    return _held([Case("D6 dialog answer", task, (task, items), _set(*ans), app=tag.lower()) for task, items, ans, tag in rows])


def h6r_cases() -> list[Case]:
    rows = [
        # permission prompts: needed when the step uses what is asked for, an interruption otherwise
        ("join the standup with video", ask_("“zoom.us” would like to access the camera.", "Don't Allow", "OK"), PART, "zoom.us"),
        ("check the weather where I am", ask_("Allow “Weather” to use your location?", "Allow Once", "Allow While Using App", "Don't Allow"), PART, "weather"),
        ("post the release notes in #eng", ask_("“Slack” would like to send you notifications.", "Don't Allow", "Allow"), AWAY, "slack"),
        ("play Blinding Lights", ask_("Spotify wants to show notifications.", "Allow", "Don't Allow"), AWAY, "spotify"),
        ("see the 10-day forecast", ask_("“Weather” would like to send you notifications.", "Don't Allow", "Allow"), AWAY, "weather"),
        ("crop the beach photo", ask_("Allow “Photos” to use your location to show where photos were taken?", "Don't Allow", "Allow"), AWAY, "photos"),
        # cookie banners
        ("search stays in Goa", ask_("Your privacy. We use cookies to personalise content and ads.", "Accept all", "Only necessary", "Cookie preferences"), AWAY, "airbnb"),
        ("buy a USB-C cable", ask_("Select your cookie preferences. We use cookies to enhance your shopping.", "Accept Cookies", "Customise cookies"), AWAY, "amazon"),
        ("open the refunds pull request", ask_("We use optional cookies to improve your experience on our websites.", "Accept", "Reject", "Manage cookies"), AWAY, "github"),
        # promos and what's new
        ("open the doc Q3 Planning", ask_("Get the Google Docs app for a better experience", "Use the app", "Not now"), AWAY, "docs"),
        ("write the meeting notes", ask_("Try Notion AI for free. Summaries, drafts and answers.", "Try Notion AI", "Dismiss"), AWAY, "notion"),
        ("play the Discover Weekly playlist", ask_("Try 3 months of Premium for ₹0", "Get Premium", "Dismiss"), AWAY, "spotify"),
        ("open the Landing page file", ask_("What's new in Figma: Dev Mode is here.", "Explore Dev Mode", "Close"), AWAY, "figma"),
        ("start a meeting now", ask_("Upgrade to Zoom Pro for meetings up to 30 hours.", "Upgrade Now", "Maybe later"), AWAY, "zoom.us"),
        ("add the Anker cable to the cart", ask_("Add a protection plan? 1 year of accidental damage cover for ₹99.", "Add protection", "No thanks"), AWAY, "amazon"),
        ("create a ticket for the login bug", ask_("How likely are you to recommend Jira to a friend?", *(str(n) for n in range(0, 11, 2)), "Dismiss"), AWAY, "jira"),
        ("search stays in Goa for next weekend", ask_("Prices now include all fees.", "Got it"), AWAY, "airbnb"),
        # settings sheets
        ("set my status to in a meeting", popup(h("Set a status", "middle"), f("What's your status?"), r("In a meeting", "middle"), r("Commuting", "middle"),
                                               r("Out sick", "middle"), Item("Clear after", "menu", value="Today", region="middle"), b("Save")), PART, "slack"),
        ("turn off my video when joining meetings", popup(h("Settings", "top"), tab("General", "left"), tab("Video", "left"), tab("Audio", "left"),
                                                         chk("Turn off my video when joining meeting"), chk("Mirror my video", checked=True), b("Close", "top-left")), PART, "zoom.us"),
        ("turn on crossfade", popup(h("Settings", "top"), h("Playback", "middle"), chk("Crossfade songs"), chk("Automix", checked=True),
                                    chk("Normalize volume", checked=True)), PART, "spotify"),
        # detail panels
        ("set KAN-12's priority to High", popup(h("KAN-12 Fix login redirect", "right"), Item("Status", "menu", value="To Do", region="right"),
                                                 f("Assignee", "right"), Item("Priority", "menu", value="Medium", region="right"), f("Labels", "right"),
                                                 b("Close", "top-right")), PART, "jira"),
        ("add the bug label to issue 42", popup(h("Apply labels to this issue", "right"), f("Filter labels", "right"), chk("bug", "right"),
                                                chk("enhancement", "right"), chk("documentation", "right")), PART, "github"),
        ("add a tag to the Reading list page", popup(h("Reading list", "top"), Item("Tags", "menu", value="Empty", region="middle"),
                                                     t("Created September 12, 2026", "middle"), b("Add a property", "middle")), PART, "notion"),
        # command menus
        ("jump to the #design channel", popup(f("Jump to...", "top"), r("#design", "middle"), r("#eng", "middle"), r("Priya Nair", "middle"),
                                              r("#random", "middle")), PART, "slack"),
        ("go to the acme/refunds repository", popup(f("Search or jump to...", "top"), r("acme/refunds", "top"), r("acme/web", "top"),
                                                    r("acme/infra", "top"), t("Search all of GitHub", "top")), PART, "github"),
        ("run the Unsplash plugin", popup(f("Search for plugins, widgets and actions", "middle"), r("Unsplash", "middle"), r("Remove BG", "middle"),
                                          r("Iconify", "middle"), r("Content Reel", "middle")), PART, "figma"),
        # date pickers
        ("book a stay from 12 to 15 October", popup(h("October 2026", "middle"), *(b(str(d)) for d in range(10, 17)), b("Clear dates", "bottom"),
                                                     b("Close", "bottom-right")), PART, "airbnb"),
        ("set the due date to Friday", popup(h("September 2026", "right"), b("Previous month", "right"), b("Next month", "right"),
                                             *(b(f"{d} Sep", "right") for d in range(28, 31)), b("Today", "right")), PART, "jira"),
        ("insert a date chip for 3 October", popup(h("Date", "middle"), h("October 2026", "middle"), *(b(str(d)) for d in range(1, 6)),
                                                   b("Insert", "bottom-right")), PART, "docs"),
        # confirm-delete and other confirmations the step asked for
        ("delete the Brand colors file", ask_("Move “Brand colors” to trash? Collaborators will lose access.", "Cancel", "Move to trash"), PART, "figma"),
        ("delete the Old ideas page", ask_("Are you sure you want to delete this page from Trash?", "Delete permanently", "Cancel"), PART, "notion"),
        ("delete the blurry photo", ask_("Are you sure you want to delete this photo? It will be in Recently Deleted for 30 days.", "Delete", "Cancel"), PART, "photos"),
        ("share the doc with Rahul", popup(h("Share “Q3 Planning”", "middle"), f("Add people, groups and calendar events"), b("Copy link", "bottom-left"),
                                           b("Done", "bottom-right")), PART, "docs"),
    ]
    return _held([Case("D6 dialog role", intent, (intent, items), _set(ans), app=tag) for intent, items, ans, tag in rows])


def h7_cases() -> list[Case]:
    rows = [
        ("the engineering channel is open", slack(), SHOWN),
        ("the random channel is open", slack(), NOT_SHOWN),
        ("message 'release is out' posted", slack_sent("release is out"), SHOWN),
        ("message 'release is out' posted", slack(), NOT_SHOWN),
        ("Blinding Lights is playing", spotify_playing(), SHOWN),
        ("Blinding Lights is playing", spotify("the weeknd"), NOT_SHOWN),
        ("a blank document is open", doc_open(), SHOWN),
        ("a document titled Offsite agenda", doc_open("Offsite agenda", "Day 1: travel"), SHOWN),
        ("a document titled Offsite agenda", docs_list(), NOT_SHOWN),
        ("the create issue form is open", jira_create(), SHOWN),
        ("the ticket KAN-12 is on the board", jira_board(), SHOWN),
        ("the ticket KAN-40 is on the board", jira_board(), NOT_SHOWN),
        ("the Anker cable is in the cart", amazon_cart(), SHOWN),
        ("the Amazon Basics cable is in the cart", amazon_cart(), NOT_SHOWN),
        ("the list of pull requests", github_prs(), SHOWN),
        ("the list of pull requests", github_repo(), NOT_SHOWN),
        ("stays in Goa are listed", airbnb(), SHOWN),
        ("the Reading list page is open", notion("Reading list"), SHOWN),
        ("the Reading list page is open", notion(), NOT_SHOWN),
        ("the Mumbai forecast is shown", weather(), NOT_SHOWN),
        ("the join meeting window is open", zoom_join(), SHOWN),
        ("the README is listed", github_repo(), SHOWN),
        ("the weather in Ahmedabad", weather(), SHOWN),
        ("the Anjuna villa is booked", airbnb(), NOT_SHOWN),
    ]
    return _held([Case("D7 outcome", f"{exp} on {s.window}", (exp, s), _set(ans), app=screen_app(s)) for exp, s, ans in rows])


# -- D9 collect: is this item one of what a collect step takes? (code has left out controls, ads and fields) --------
# The demo's own sites and words appear in neither split: they are judged live, never tuned on.

def d9_cases() -> list[Case]:
    rows = [
        ("links to python packaging guides", ln("Packaging Python Projects — Python Packaging User Guide", url="https://packaging.python.org/tutorials"), FITS, "pypi"),
        ("links to python packaging guides", ln("Sign in", url="https://pypi.org/account/login/"), OTHER, "pypi"),
        ("links to python packaging guides", ln("Writing your pyproject.toml", url="https://packaging.python.org/guides/pyproject"), FITS, "pypi"),
        ("links to python packaging guides", t("About 1,240,000 results (0.31 seconds)"), OTHER, "searchpage"),
        ("hiking trail articles", ln("The 10 best day hikes near Pune this monsoon", url="https://trailnotes.in/pune-hikes"), FITS, "trails"),
        ("hiking trail articles", ln("Images", url="https://search.example/images"), OTHER, "trails"),
        ("hiking trail articles", ln("Rajmachi fort trek: a beginner's guide", url="https://trekwise.in/rajmachi"), FITS, "trails"),
        ("hiking trail articles", ln("Privacy", url="https://search.example/privacy"), OTHER, "searchpage"),
        ("open pull requests", r("Fix flaky retry test #482 opened 2 days ago by mira"), FITS, "github"),
        ("open pull requests", t("Pull requests"), OTHER, "github"),
        ("open pull requests", ln("Labels 9", url="https://github.com/acme/web/labels"), OTHER, "github"),
        ("unread chats", r("Daniel Ortiz", value="Can you send the deck?"), FITS, "telegram"),
        ("unread chats", h("Chats"), OTHER, "telegram"),
        ("tasks due today", r("Renew car insurance — due today"), FITS, "todoist"),
        ("tasks due today", b("Add task in Today view"), OTHER, "todoist"),
        ("tasks due today", r("Call the plumber — due today 18:00"), FITS, "todoist"),
        ("podcast episodes", r("Ep. 212: How cities cool themselves — 48 min"), FITS, "podcasts"),
        ("podcast episodes", t("Follow"), OTHER, "podcasts"),
        ("podcast episodes", r("Ep. 211: The economics of airport lounges — 39 min"), FITS, "podcasts"),
        ("recipes for dal", ln("Dal tadka, restaurant style", url="https://cookwise.in/dal-tadka"), FITS, "recipes"),
        ("recipes for dal", ln("Subscribe to our newsletter", url="https://cookwise.in/newsletter"), OTHER, "recipes"),
        ("flight prices to Goa", t("IndiGo 6E 512 · 07:15 → 08:30 · ₹4,820"), FITS, "flights"),
        ("flight prices to Goa", t("Filters"), OTHER, "flights"),
        ("the sign-in code", t("Your Northwind sign-in code is 482913. It expires in 10 minutes."), FITS, "messages"),
        ("the sign-in code", t("Your parcel is out for delivery today."), OTHER, "messages"),
        ("files shared this week", r("Q4 roadmap.pdf — shared by Lena, Monday"), FITS, "dropbox"),
        ("files shared this week", b("Upload files"), OTHER, "dropbox"),
    ]
    return [Case("D9 collect", f"{what} / {item.name[:30]}", (what, item), _set(ans), app=tag) for what, item, ans, tag in rows]


def h9_cases() -> list[Case]:
    rows = [
        ("links to open-source charting libraries", ln("Chart.js | Open source HTML5 Charts for your website", url="https://www.chartjs.org/"), FITS, "search"),
        ("links to open-source charting libraries", ln("Shopping", url="https://search.example/shopping"), OTHER, "search"),
        ("links to open-source charting libraries", ln("Apache ECharts", url="https://echarts.apache.org/"), FITS, "search"),
        ("links to open-source charting libraries", ln("Feedback", url="https://search.example/feedback"), OTHER, "search"),
        ("links to open-source charting libraries", ln("Top 10 JavaScript charting libraries in 2026", url="https://devroundup.io/charting"), FITS, "search"),
        ("the overdue invoices", r("INV-214 · Brightline Studio · ₹42,000 · overdue 12 days"), FITS, "zoho"),
        ("the overdue invoices", r("INV-219 · Kite Labs · ₹9,500 · paid"), OTHER, "zoho"),
        ("the overdue invoices", h("Invoices"), OTHER, "zoho"),
        ("the overdue invoices", r("INV-208 · Orbit Foods · ₹18,750 · overdue 31 days"), FITS, "zoho"),
        ("the verification code", t("Use 771204 to verify your Ledgerly account. Don't share it."), FITS, "signal"),
        ("the verification code", t("Mira: are we still on for lunch?"), OTHER, "signal"),
        ("the verification code", t("Ledgerly: 330918 is your one-time code"), FITS, "signal"),
        ("new job listings for designers", ln("Senior Product Designer — Fintrail, Bengaluru (Remote)", url="https://jobs.example/fintrail-spd"), FITS, "jobs"),
        ("new job listings for designers", ln("Post a job", url="https://jobs.example/post"), OTHER, "jobs"),
        ("new job listings for designers", ln("UX Designer II — Brightpath, Pune", url="https://jobs.example/brightpath-ux"), FITS, "jobs"),
        ("concerts this weekend", r("Prateek Kuhad — Sat 11 Oct, 7 pm · Phoenix Arena"), FITS, "tickets"),
        ("concerts this weekend", t("Sort by: Relevance"), OTHER, "tickets"),
        ("concerts this weekend", r("Indie Night with The Local Train — Sun 12 Oct, 8 pm"), FITS, "tickets"),
        ("upcoming meetings", r("Design review · 14:00–14:45 · Huddle room 3"), FITS, "fantastical"),
        ("upcoming meetings", b("New meeting with attendees"), OTHER, "fantastical"),
        ("upcoming meetings", r("1:1 with Arjun · 16:30–17:00"), FITS, "fantastical"),
        ("unpaid bills", r("Electricity — ₹2,340 due 14 Oct"), FITS, "billpay"),
        ("unpaid bills", r("Water — paid on 2 Oct"), OTHER, "billpay"),
        ("articles about async rust", ln("Async Rust in practice: lessons from a year in production", url="https://blog.example/async-rust"), FITS, "hn"),
        ("articles about async rust", ln("login", url="https://news.example/login"), OTHER, "hn"),
        ("homes under 50 lakh", t("2 BHK, Baner · ₹48 L · 980 sq ft"), FITS, "housing"),
        ("homes under 50 lakh", t("3 BHK, Aundh · ₹92 L · 1,450 sq ft"), OTHER, "housing"),
        ("homes under 50 lakh", t("1 BHK, Wakad · ₹39 L · 610 sq ft"), FITS, "housing"),
    ]
    return _held([Case("D9 collect", f"{what} / {item.name[:30]}", (what, item), _set(ans), app=tag) for what, item, ans, tag in rows])


def tuning_cases() -> list[Case]:
    return d1_cases() + d2_cases() + d3_cases() + d4_cases() + d5_cases() + d6_cases() + d6r_cases() + d7_cases() + d9_cases()


def held_out_cases() -> list[Case]:
    return (
        h1_cases() + h2_cases() + h3_cases() + h4_cases() + h5_cases() + h6_cases() + h6r_cases() + h7_cases() + h9_cases()
    )


def all_cases() -> list[Case]:
    return tuning_cases() + held_out_cases()


METHODS = {
    "D1 place": "place",
    "D2 page state": "page_state",
    "D3 target": "target",
    "D4 pick check": "pick_ok",
    "D5 name match": "name_match",
    "D6 dialog answer": "dialog_answer",
    "D6 dialog role": "dialog_role",
    "D7 outcome": "outcome",
    "D9 collect": "collect_fit",
}
ITEM_DECISIONS = ("D3 target", "D5 name match", "D6 dialog answer")
