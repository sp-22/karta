# ruff: noqa: RUF001, RUF005  (screens carry the rupee sign and curly quotes, as real ones do; screens are built by concatenation)
"""Seed cases for the next-step bench (scripts/next_step_bench.py): a task, a mid-task screen, the right next action.

Generic made-up places, built like bench_cases.py (Items as AX and OCR show them: names, regions, rows for repeated
labels, dialogs flagged): a grocery site (search, a results grid of identical ADD buttons, a cart), a chat app, Notes,
a calendar, a terminal and a browser start page. Each case is one moment of a task: nothing done yet, results shown,
a message typed, the end reached, something in the way. Names are invented; nothing here is site-specific, and the
option builder (karta.harness.next_step) knows none of it.

An answer is ("click" | "type", item name, near) — `near` a row mate or the region when the name repeats — or one of
the fixed options (DONE, SCROLL, BLOCKED). `cases()` resolves each to an item index and fails loudly when that is
ambiguous or missing.
"""

from __future__ import annotations

from karta.harness.bench_cases import app, b, f, h, ln, r, t, tab, web
from karta.harness.next_step import BLOCKED, DONE, SCROLL, Case, actionable
from karta.harness.types import Item, Screen

# fmt: off

PRODUCTS = [
    ("Crunchy Salted Chips", "50 g", "₹20"),
    ("Tangy Tomato Chips", "50 g", "₹20"),
    ("Masala Corn Puffs", "60 g", "₹25"),
    ("Salted Peanuts Classic", "200 g", "₹60"),
    ("Dark Chocolate Bar", "90 g", "₹99"),
    ("Honey Oat Cookies", "150 g", "₹45"),
    ("Lemon Iced Tea", "500 ml", "₹40"),
    ("Sparkling Orange Soda", "300 ml", "₹35"),
]
SHOP = "https://freshcart.example/"


def shop_top(query: str = "") -> list[Item]:
    return [
        Item("FreshCart", "image", region="top-left"), b("Delivery in 12 minutes · Home", "top-left"),
        f("Search for groceries and more", "top", value=query), b("Account", "top-right"), b("Cart", "top-right"),
    ]


def shop_home() -> Screen:
    return web("Personal", SHOP, "FreshCart", shop_top() + [
        h("Shop by category", "middle"), ln("Fruits & Vegetables", "middle"), ln("Snacks & Munchies", "middle"),
        ln("Cold Drinks & Juices", "middle"), ln("Bakery & Biscuits", "middle"), ln("Dairy & Eggs", "middle"),
        t("Free delivery on orders above ₹199", "bottom"),
    ])


def shop_results(query: str, visible: int = 8, added: str | None = None) -> Screen:
    page = shop_top(query) + [t(f'Showing results for "{query}"', "top"), b("Sort by: Relevance", "top-right")]
    for n, (name, size, price) in enumerate(PRODUCTS):
        where = "middle-left" if n % 4 < 2 else "middle-right"
        hidden = n >= visible
        page += [t(name, where, offscreen=hidden), t(size, where, offscreen=hidden), t(price, where, offscreen=hidden)]
        if name == added:
            page += [b("-", where, row=(name, size, "1")), t("1", where), b("+", where, row=(name, size, "1"))]
        else:
            page += [b("ADD", where, row=(name, size, price), offscreen=hidden)]
    return web("Personal", f"{SHOP}search?q={query}", "FreshCart", page)


def shop_typed(query: str) -> Screen:
    return web("Personal", SHOP, "FreshCart", [
        *shop_top(query)[:2], f("Search for groceries and more", "top", value=query, focused=True), b("Search", "top"),
        *shop_top()[3:], h("Shop by category", "middle"), ln("Snacks & Munchies", "middle"), ln("Dairy & Eggs", "middle"),
    ])


def shop_cart() -> Screen:
    lines = []
    for name, size, price in PRODUCTS[:2]:
        lines += [t(name, "right"), t(size, "right"), t(price, "right"),
                  b("-", "right", row=(name, "1", "+")), t("1", "right"), b("+", "right", row=(name, "1", "-"))]
    return web("Personal", f"{SHOP}cart", "FreshCart", shop_top() + [
        h("My Cart", "right"), t("2 items", "right"), *lines, h("Bill details", "right"), t("Items total ₹40", "right"),
        t("Delivery fee ₹15", "right"), t("To pay ₹55", "right"), b("Proceed to checkout", "bottom-right"),
    ])


def shop_location_popup() -> Screen:
    return web("Personal", SHOP, "FreshCart", shop_top() + [
        ln("Snacks & Munchies", "middle"), ln("Dairy & Eggs", "middle"),
        h("Set your delivery location", "middle", dialog=True),
        t("We need your location to show products from a store near you", "middle", dialog=True),
        b("Use my current location", "middle", dialog=True), f("Search for an area or street", "middle", dialog=True),
        b("Close", "top-right", dialog=True),
    ])


CHATS = [("Arjun Mehta", "Sure, sending it now"), ("Neha Kapoor", "Are we still on for today?"),
         ("Design Team", "Ravi: new mockups are up"), ("Ravi Iyer", "Thanks!"), ("Sana Qureshi", "Call me later")]


def chat_list(search: str = "", results: list[tuple[str, str]] | None = None) -> list[Item]:
    rows = results if results is not None else CHATS
    return [h("Chats", "top-left"), b("New chat", "top-left"), f("Search or start a new chat", "top-left", value=search),
            tab("All", "top-left"), tab("Unread", "top-left"), tab("Groups", "top-left")] + [r(n, value=v) for n, v in rows]


def chat_home() -> Screen:
    return app("Chatter", "Chatter", chat_list() + [t("Select a chat to start messaging", "middle")],
               "File > New Chat", "View > Show Archived", "Window > Minimize")


def chat_open(name: str, typed: str = "", sent: str | None = None, group: bool = False,
              search: str = "", results: list[tuple[str, str]] | None = None) -> Screen:
    last = dict(CHATS).get(name, "Hi!")
    convo = [h(name, "top"), b("Voice call", "top-right"), b("Video call", "top-right"), b("Chat info", "top-right")]
    if group:
        convo += [b("Mute notifications", "top-right"), t("Arjun, Neha, Ravi and you", "top")]
    convo += [t(last, "middle"), t("10:42", "middle")]
    if sent:
        convo += [t(sent, "middle-right"), t("10:58 · Delivered", "middle-right")]
    convo += [b("Attach", "bottom-left"), b("Emoji", "bottom-left"),
              f("Type a message", "bottom", value=typed, focused=True), b("Send", "bottom-right") if typed else b("Record voice message", "bottom-right")]
    return app("Chatter", name, chat_list(search, results) + convo, "File > New Chat", "View > Show Archived", "Window > Minimize")


def chat_searched(query: str, hits: list[tuple[str, str]]) -> Screen:
    return app("Chatter", "Chatter", chat_list(query, hits) + [t("Contacts", "left"), t("Select a chat to start messaging", "middle")],
               "File > New Chat", "Window > Minimize")


NOTES = [("Packing list", "Yesterday"), ("Old ideas", "12 Aug"), ("Book club picks", "3 Aug"), ("Recipes to try", "28 Jul")]


def notes_app(selected: str | None = None, new_body: str | None = None) -> Screen:
    items = [t("iCloud", "left"), r("All iCloud", value="14"), r("Notes", value="10"), r("Recently Deleted", value="2"),
             b("New Folder", "bottom-left"), f("Search", "top-right"), b("New Note", "top"),
             b("Delete", "top", enabled=selected is not None or new_body is not None), b("Format", "top"), b("Checklist", "top"),
             b("Share", "top-right")]
    notes = NOTES if new_body is None else [(new_body.split("\n", 1)[0] or "New Note", "Now"), *NOTES]
    items += [r(n, "middle-left", value=d) for n, d in notes]
    if new_body is not None:
        items += [f("Note body", "right", value=new_body, focused=True)]
    elif selected:
        items += [f("Note body", "right", value=f"{selected}\n- first thought\n- second thought")]
    else:
        items += [t("No note selected", "right")]
    return app("Notes", "Notes", items, "File > New Note", "File > New Folder", "Edit > Undo", "Format > Checklist")


def calendar(event: dict | None = None, shown: str | None = None) -> Screen:
    items = [b("Calendars", "top-left"), b("New Event", "top-left"), tab("Day", "top"), tab("Week", "top"), tab("Month", "top"),
             tab("Year", "top"), f("Search", "top-right"), b("Previous month", "top-right"), b("Today", "top-right"),
             b("Next month", "top-right"), h("October 2026", "top-left")]
    for day in (12, 13, 14, 15, 16):
        items.append(Item(f"{day} October", "button", region="middle"))
        if shown and day == 14:
            items.append(t(shown, "middle"))
    if event is not None:
        items += [f("Event title", "middle-right", value=event.get("title", ""), focused=not event.get("title"), dialog=True),
                  t(event.get("when", "Wed 14 Oct 2026  09:00 to 10:00"), "middle-right", dialog=True),
                  f("Add location or video call", "middle-right", dialog=True), b("Add invitees", "middle-right", dialog=True),
                  b("Add", "middle-right", dialog=True, enabled=bool(event.get("title"))), b("Cancel", "middle-right", dialog=True)]
    return app("Calendar", "Calendar", items, "File > New Event", "View > By Month", "Window > Minimize")


def terminal(lines: list[str]) -> Screen:
    items = [t(line, "top-left" if n < 2 else "left") for n, line in enumerate(lines)]
    items.append(f("shell", "middle", focused=True))
    return app("Terminal", "sam — -zsh — 80×24", items, "Shell > New Window", "Shell > New Tab", "Edit > Clear Scrollback")


START = [b("Customize this page", "bottom-right"), ln("Mail", "top-right"), ln("Images", "top-right"), b("Apps", "top-right"),
         Item("Search engine logo", "image", region="middle")]
TILES = [ln("Weather", "middle"), ln("News", "middle"), ln("Video", "middle"), ln("Maps", "middle"), b("Add shortcut", "middle")]


def start_page(typed: str = "", suggestions: list[str] | None = None) -> Screen:
    page = START + [f("Search the web or type a URL", "middle", value=typed, focused=bool(typed))]
    if typed:
        page += [b("Search", "middle")]
    page += [r(s, "middle") for s in suggestions or []] + TILES
    return web("Personal", "about:newtab", "New Tab", page)


def recipe_site_cookies() -> Screen:
    return web("Personal", "https://tastyplates.example/", "Tasty Plates - Everyday recipes", [
        Item("Tasty Plates", "image", region="top-left"), f("Search recipes", "top"), ln("Breakfast", "top"), ln("Dinner", "top"),
        h("Easy weeknight pasta", "middle"), t("Ready in 20 minutes", "middle"),
        h("We value your privacy", "bottom", dialog=True), t("We use cookies to improve your experience", "bottom", dialog=True),
        b("Accept all", "bottom", dialog=True), b("Reject all", "bottom", dialog=True), b("Manage preferences", "bottom", dialog=True),
    ])


# fmt: on

# (name, family, task, screen, answers, note)
RAW = [
    # -- shop --
    (
        "shop home: search first",
        "shop",
        "add Honey Oat Cookies to the cart",
        shop_home(),
        [("type", "Search for groceries and more", None)],
        "nothing done yet",
    ),
    (
        "shop typed: press search",
        "shop",
        "add salted peanuts to the cart",
        shop_typed("salted peanuts"),
        [("click", "Search", None)],
        "query typed, not searched",
    ),
    (
        "shop results: ADD tangy",
        "shop",
        "add Tangy Tomato Chips to the cart",
        shop_results("chips"),
        [("click", "ADD", "Tangy Tomato Chips")],
        "grid of identical ADD buttons",
    ),
    (
        "shop results: ADD crunchy",
        "shop",
        "add one pack of Crunchy Salted Chips 50 g",
        shop_results("chips"),
        [("click", "ADD", "Crunchy Salted Chips")],
        "",
    ),
    (
        "shop results: ADD chocolate",
        "shop",
        "put a Dark Chocolate Bar in my cart",
        shop_results("snacks"),
        [("click", "ADD", "Dark Chocolate Bar")],
        "",
    ),
    (
        "shop results: ADD soda",
        "shop",
        "buy Sparkling Orange Soda",
        shop_results("drinks"),
        [("click", "ADD", "Sparkling Orange Soda")],
        "",
    ),
    (
        "shop results: scroll to tea",
        "shop",
        "add Lemon Iced Tea to the cart",
        shop_results("drinks", visible=6),
        [SCROLL],
        "the product is below the fold",
    ),
    (
        "shop results: added already",
        "shop",
        "add Tangy Tomato Chips to the cart",
        shop_results("chips", added="Tangy Tomato Chips"),
        [DONE],
        "ADD became a quantity stepper",
    ),
    ("shop cart: checkout", "shop", "check out my cart", shop_cart(), [("click", "Proceed to checkout", None)], ""),
    (
        "shop cart: one more crunchy",
        "shop",
        "make it two packs of Crunchy Salted Chips",
        shop_cart(),
        [("click", "+", "Crunchy Salted Chips")],
        "repeated + buttons",
    ),
    (
        "shop popup: location",
        "shop",
        "add Salted Peanuts Classic to the cart",
        shop_location_popup(),
        [BLOCKED, ("click", "Close", None)],
        "a location popup blocks the page",
    ),
    # -- chat --
    (
        "chat list: open neha",
        "chat",
        "message Neha Kapoor saying see you at 5",
        chat_home(),
        [("click", "Neha Kapoor", None)],
        "",
    ),
    (
        "chat list: search vikram",
        "chat",
        "send Vikram Rao the message running late",
        chat_home(),
        [("type", "Search or start a new chat", None)],
        "the contact is not in the list",
    ),
    (
        "chat search: pick vikram",
        "chat",
        "send Vikram Rao the message running late",
        chat_searched("vikram", [("Vikram Rao", "Last seen today"), ("Vikram's Bakery", "Order ready")]),
        [("click", "Vikram Rao", None)],
        "",
    ),
    (
        "chat open: type message",
        "chat",
        "message Neha Kapoor saying see you at 5",
        chat_open("Neha Kapoor"),
        [("type", "Type a message", None)],
        "",
    ),
    (
        "chat typed: send",
        "chat",
        "message Neha Kapoor saying see you at 5",
        chat_open("Neha Kapoor", typed="see you at 5"),
        [("click", "Send", None)],
        "message typed",
    ),
    (
        "chat sent: done",
        "chat",
        "message Neha Kapoor saying see you at 5",
        chat_open("Neha Kapoor", sent="see you at 5"),
        [DONE],
        "",
    ),
    (
        "chat wrong chat: switch",
        "chat",
        "tell Sana Qureshi I will call at 7",
        chat_open("Arjun Mehta"),
        [("click", "Sana Qureshi", None)],
        "another chat is open",
    ),
    (
        "chat group: mute",
        "chat",
        "mute the Design Team group",
        chat_open("Design Team", group=True),
        [("click", "Mute notifications", None)],
        "",
    ),
    (
        "chat typed other: send ravi",
        "chat",
        "reply thanks to Ravi Iyer",
        chat_open("Ravi Iyer", typed="thanks"),
        [("click", "Send", None)],
        "",
    ),
    # -- notes --
    ("notes: new note", "notes", "create a note called Gift ideas", notes_app(), [("click", "New Note", None)], ""),
    (
        "notes new: type body",
        "notes",
        "create a note called Gift ideas",
        notes_app(new_body=""),
        [("type", "Note body", None)],
        "",
    ),
    ("notes written: done", "notes", "create a note called Gift ideas", notes_app(new_body="Gift ideas"), [DONE], ""),
    ("notes: select old", "notes", "delete the note Old ideas", notes_app(), [("click", "Old ideas", None)], ""),
    (
        "notes selected: delete",
        "notes",
        "delete the note Old ideas",
        notes_app(selected="Old ideas"),
        [("click", "Delete", None)],
        "",
    ),
    # -- calendar --
    (
        "calendar: new event",
        "calendar",
        "add an event Dentist on 14 October",
        calendar(),
        [("click", "New Event", None), ("click", "14 October", None)],
        "",
    ),
    (
        "calendar popover: title",
        "calendar",
        "add an event Dentist on 14 October",
        calendar(event={}),
        [("type", "Event title", None)],
        "",
    ),
    (
        "calendar titled: add",
        "calendar",
        "add an event Dentist on 14 October",
        calendar(event={"title": "Dentist"}),
        [("click", "Add", None)],
        "",
    ),
    ("calendar shown: done", "calendar", "add an event Dentist on 14 October", calendar(shown="Dentist 09:00"), [DONE], ""),
    # -- terminal --
    (
        "terminal: type command",
        "terminal",
        "list the files in the Downloads folder",
        terminal(["Last login: Tue Sep 29 09:12:40 on ttys002", "sam@Mac ~ %"]),
        [("type", "shell", None)],
        "",
    ),
    (
        "terminal: output shown",
        "terminal",
        "list the files in the Downloads folder",
        terminal(["sam@Mac ~ % ls ~/Downloads", "invoice-0921.pdf", "photos.zip", "setup.dmg", "sam@Mac ~ %"]),
        [DONE],
        "",
    ),
    # -- browser start page --
    (
        "start page: type search",
        "browser",
        "search the web for easy pasta recipes",
        start_page(),
        [("type", "Search the web or type a URL", None)],
        "",
    ),
    (
        "start page typed: search",
        "browser",
        "search the web for easy pasta recipes",
        start_page(
            "easy pasta recipes", ["easy pasta recipes", "easy pasta recipes vegetarian", "easy pasta recipes with chicken"]
        ),
        [("click", "Search", None), ("click", "easy pasta recipes", None)],
        "suggestions shown",
    ),
    (
        "recipe site: cookie banner",
        "browser",
        "open the easy weeknight pasta recipe",
        recipe_site_cookies(),
        [BLOCKED, ("click", "Accept all", None), ("click", "Reject all", None)],
        "a cookie banner",
    ),
]


def resolve(screen: Screen, answer) -> tuple[str, int]:
    """An answer written by name as an Action key (verb, item index); raises when it names no item or several."""
    if isinstance(answer, str):
        return (answer, -1)
    verb, name, near = answer
    hits = [
        n
        for n in actionable(screen)
        if screen.items[n].name == name
        and (screen.items[n].kind == "field") == (verb == "type")
        and (near is None or near in screen.items[n].row or near == screen.items[n].region)
    ]
    if len(hits) != 1:
        raise ValueError(f"answer {answer!r} names {len(hits)} items")
    return (verb, hits[0])


def cases() -> list[Case]:
    return [
        Case(name, family, task, screen, frozenset(resolve(screen, a) for a in answers), note=note)
        for name, family, task, screen, answers, note in RAW
    ]
