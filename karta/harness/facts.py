"""Facts: what code knows without asking a model.

No site catalog: the brain names sites, memory keeps the places that worked. Login pages, dialogs and permission
prompts are read from structure (a password field, AX dialog roles, the process that owns a prompt), never from a
word on the page.

Everything here is read-only: app bundle names, each Chromium browser's `Local State` (profile names only), folder
names. Never cookies, history, passwords or file contents.
"""

from __future__ import annotations

import functools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from karta.harness.types import Item, MacFacts, Outcome, Screen


@dataclass(frozen=True)
class Place:
    name: str  # for Laya and logs: "chat.example in Brave Browser, profile Personal", "Notes app"
    app: str  # the app to bring to the front
    url: str | None = None
    profile: str | None = None
    tab: tuple[int, int] | None = None  # (window, tab), 1-based, of a tab already open on this site: switch to it


@dataclass(frozen=True)
class Tab:
    """One open browser tab. Read only to compare its host with a wanted site; never logged or sent anywhere."""

    browser: str
    window_index: int  # 1-based, in the browser's own window order (front first)
    tab_index: int  # 1-based within the window
    url: str
    title: str = ""
    profile: str | None = None  # unknown from AppleScript (None) unless the caller knows it


# ------------------------------------------------------------------ this Mac

APP_DIRS = (
    "/Applications",
    "/System/Applications",
    "/System/Library/CoreServices",
)  # Finder lives in CoreServices  # and ~/Applications; sub-folders (Utilities) one level down
CHROMIUM = {  # browser app -> its data folder under ~/Library/Application Support (holds `Local State`)
    "Brave Browser": "BraveSoftware/Brave-Browser",
    "Google Chrome": "Google/Chrome",
    "Microsoft Edge": "Microsoft Edge",
    "Arc": "Arc/User Data",
    "Chromium": "Chromium",
    "Vivaldi": "Vivaldi",
}
SINGLE_PROFILE = ("Safari", "Firefox")  # one profile as far as the harness can tell: opened without a profile
FOLDER_DEPTH = 4  # levels under the home folder ("~/Desktop/Projects/karta/src" is the fourth)
FOLDERS_PER_LEVEL = 20  # the most recently changed folders kept at each level
MAX_FOLDERS = 80
SKIP_FOLDERS = {"Library", "Applications", "node_modules", "__pycache__", "venv", "build", "dist", "target"}


def installed_apps(dirs: tuple[str | Path, ...] | None = None, home: Path | None = None) -> list[str]:
    """App bundle names in the app folders (and one level of sub-folders such as Utilities), sorted, no duplicates."""
    home = home or Path.home()
    roots = [Path(d) for d in (dirs if dirs is not None else (*APP_DIRS, home / "Applications"))]
    names: set[str] = set()
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if child.suffix == ".app":
                names.add(child.stem)
            elif child.is_dir() and not child.name.startswith("."):
                try:
                    names.update(c.stem for c in child.iterdir() if c.suffix == ".app")
                except OSError:
                    continue
    return sorted(names, key=str.lower)


def chromium_profiles(local_state: Path) -> list[str]:
    """Profile names from a Chromium `Local State` file, the last used first; [] when it cannot be read."""
    try:
        profile = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {})
        cache = profile.get("info_cache") or {}
        listed = [d for d in profile.get("profiles_order") or [] if d in cache]
    except (OSError, ValueError, AttributeError, TypeError):
        return []
    order = listed + [d for d in cache if d not in listed]
    last = profile.get("last_used")
    if last in order:
        order.remove(last)
        order.insert(0, last)
    names = [str(cache[d].get("name") or d) for d in order if isinstance(cache[d], dict)]
    return list(dict.fromkeys(names))


def default_browser() -> str | None:
    """The app LaunchServices opens web links with ("Brave Browser"), or None when it cannot tell."""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        url = NSWorkspace.sharedWorkspace().URLForApplicationToOpenURL_(NSURL.URLWithString_("https://example.com"))
        return Path(str(url.path())).stem if url is not None else None
    except Exception:  # no AppKit, no answer: the browsers keep their catalog order
        return None


MESSAGE_SCHEMES = ("mailto:", "sms:", "imessage:")  # the platform's URL schemes of mail and messages


@functools.lru_cache(maxsize=1)
def message_apps() -> frozenset[str]:
    """The apps LaunchServices has registered to open a mail or message link (MESSAGE_SCHEMES): a mail or messaging
    app, by a platform fact, never by its name. Read once per process; empty when it cannot tell."""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        ws = NSWorkspace.sharedWorkspace()
        urls = [u for s in MESSAGE_SCHEMES for u in (ws.URLsForApplicationsToOpenURL_(NSURL.URLWithString_(s)) or [])]
        return frozenset(Path(str(u.path())).stem for u in urls if u is not None)
    except Exception:  # no AppKit (or an older macOS): no app counts by this fact
        return frozenset()


def browser_profiles(apps: list[str], home: Path, default: str | None = None) -> dict[str, list[str]]:
    """Each installed browser -> its profile names ([] for a single-profile browser); the default browser first."""
    found: dict[str, list[str]] = {}
    for browser, folder in CHROMIUM.items():
        if browser in apps:
            found[browser] = chromium_profiles(home / "Library" / "Application Support" / folder / "Local State")
    for browser in SINGLE_PROFILE:
        if browser in apps:
            found[browser] = []
    if default in found:
        found = {default: found[default], **{b: p for b, p in found.items() if b != default}}
    return found


FIELD, RECORD = "\x1f", "\x1e"  # unit and record separators: no title or URL holds them
TAB_SCRIPT = """
if application "{app}" is running then
  set out to ""
  tell application "{app}"
    set wi to 0
    repeat with w in windows
      set wi to wi + 1
      try
        set ti to 0
        repeat with t in tabs of w
          set ti to ti + 1
          set out to out & wi & (character id 31) & ti & (character id 31) & (URL of t) & (character id 31) & ({title} of t) & (character id 30)
        end repeat
      end try
    end repeat
  end tell
  return out
end if
"""
TAB_BROWSERS = {**{b: "title" for b in CHROMIUM}, "Safari": "name"}  # AppleScript's word for a tab's title


def _run_osascript(script: str) -> str:
    """karta.core.mac_places' osascript runner, imported only here (it loads AppKit); "" off a Mac or on any failure."""
    try:
        from karta.core.mac_places import _osascript

        return _osascript(script)
    except Exception:
        return ""


def open_tabs(browsers: list[str], hosts: set[str] | None = None, run=None) -> list[Tab]:
    """The tabs open in these browsers (Chromium ones and Safari; Firefox cannot be read), read-only over AppleScript.

    A browser that is not running is skipped, never launched. With `hosts`, only tabs on those hosts are kept (compare
    as facts._host does: "mail.example.com"); nothing is logged. `run` replaces osascript in tests. Chromium's
    AppleScript does not tell the profile of a window, so `profile` is None."""
    run = run or _run_osascript
    readable = [b for b in browsers if b in TAB_BROWSERS]
    if not readable:
        return []
    scripts = [TAB_SCRIPT.format(app=b.replace('"', ""), title=TAB_BROWSERS[b]) for b in readable]
    with ThreadPoolExecutor(max_workers=len(scripts)) as pool:  # one osascript per browser, side by side
        outs = list(pool.map(run, scripts))
    tabs: list[Tab] = []
    for browser, out in zip(readable, outs, strict=True):
        for record in out.split(RECORD):
            parts = record.strip("\n").split(FIELD)
            if len(parts) < 3 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
                continue
            url = parts[2].strip()
            if not url.startswith(("http://", "https://")) or (hosts is not None and _host(url) not in hosts):
                continue
            title = parts[3].strip() if len(parts) > 3 else ""
            tabs.append(Tab(browser, int(parts[0]), int(parts[1]), url, title))
    return tabs


def notable_folders(home: Path) -> list[str]:
    """The folders under the home folder the user changed most recently, level by level (FOLDER_DEPTH levels, the
    FOLDERS_PER_LEVEL most recent of each), home-relative ("~/Desktop/Projects/karta"); names only, capped. No folder is
    preferred by its name: wherever the user works (Desktop, Documents, ~/Developer, ~/Code) is what changed."""
    out: list[str] = []
    level = [home]
    for _ in range(FOLDER_DEPTH):
        level = sorted((c for d in level for c in _subdirs(d)), key=_mtime, reverse=True)[:FOLDERS_PER_LEVEL]
        out += ["~/" + c.relative_to(home).as_posix() for c in level]
    return list(dict.fromkeys(out))[:MAX_FOLDERS]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _subdirs(path: Path) -> list[Path]:
    try:
        dirs = [
            c
            for c in path.iterdir()
            if c.is_dir()
            and not c.is_symlink()
            and not c.name.startswith(".")
            and not c.suffix  # a bundle (an app, a library package), never a folder of the user's
            and c.name not in SKIP_FOLDERS
        ]
        return sorted(dirs, key=lambda c: -c.stat().st_mtime)
    except OSError:
        return []


def mac_facts(
    home: Path | str | None = None,
    app_dirs: tuple[str | Path, ...] | None = None,
    default: str | None = None,
    now: datetime | None = None,
) -> MacFacts:
    """This Mac, read-only: installed apps, each Chromium browser's profile names (from its Local State file),
    notable folders, now. Never reads cookies, history or passwords. The parameters are for tests (a fake home);
    `default` is the default browser, asked of LaunchServices when not given."""
    home = Path(home) if home is not None else Path.home()
    apps = installed_apps(app_dirs, home)
    browsers = browser_profiles(apps, home, default if default is not None else default_browser())
    return MacFacts(apps, browsers, notable_folders(home), now or datetime.now())


# ------------------------------------------------------------------ places


# Apple's own apps named by everyday words ("send a message", "a note about"): never offered when a site is known.
COMMON_WORD_APPS = {
    "notes", "messages", "mail", "calendar", "reminders", "music", "photos", "maps", "clock", "contacts", "books",
    "news", "stocks", "weather", "preview", "podcasts", "shortcuts", "home", "freeform", "passwords", "phone", "tips",
    "finder", "terminal", "console", "calculator", "dictionary", "games", "journal", "image playground",
}  # fmt: skip
PLACE_NAME = re.compile(r"^(?P<host>\S+) in (?P<browser>.+?)(?:, profile (?P<profile>.+?))?(?:, open tab \d+\.\d+)?$")


def _spans(text: str, name: str) -> list[tuple[int, int]]:
    """Where `text` names `name` as a word or phrase (singular or plural): "the reminder" names Reminders."""
    stem = name.lower().strip()
    variants = {stem, stem[:-1] if stem.endswith("s") and len(stem) > 4 else stem}
    return [m.span() for v in variants if len(v) >= 3 for m in re.finditer(rf"(?<![\w.]){re.escape(v)}s?(?!\w)", text.lower())]


def _says(text: str, name: str) -> bool:
    return bool(_spans(text, name))


def _app_names(app: str) -> list[str]:
    """How a person names an app: "Brave Browser" -> brave browser, brave; "Microsoft Outlook" -> outlook."""
    names = [app]
    short = re.sub(r"^(google|microsoft|apple)\s+", "", app, flags=re.I)
    short = re.sub(r"\s+(browser|desktop|app)$", "", short, flags=re.I)
    if short.lower() != app.lower():
        names.append(short)
    return names


def _names_app(text: str, app: str) -> bool:
    return any(_says(text, n) for n in _app_names(app))


def named_apps(text: str, apps) -> list[str]:
    """The apps `text` names, the longest name winning: an app is named only where its words are not part of a
    longer app's name that `text` also says ("ink pad" names Ink Pad, never Pads), in the given order."""
    spans = {a: [sp for n in _app_names(a) for sp in _spans(text, n)] for a in apps}

    def inside(sp: tuple[int, int], other: tuple[int, int]) -> bool:
        return other[0] <= sp[0] and sp[1] <= other[1] and other[1] - other[0] > sp[1] - sp[0]

    others = [sp for a in spans for sp in spans[a]]
    return [a for a in apps if any(not any(inside(sp, o) for o in others) for sp in spans[a])]


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


def app_place(app: str) -> Place:
    return Place(f"{app} app", app)


def site_place(url: str, browser: str, profile: str | None) -> Place:
    name = f"{_host(url)} in {browser}" + (f", profile {profile}" if profile else "")
    return Place(name, browser, url, profile)


def tab_place(tab: Tab, service_url: str) -> Place:
    """A tab already open on the site: named like the site's place plus its position (for logs and Laya). Memory keeps
    it as the site's place (memory.place_value: tabs move), and switching to it finds the tab again by its address in
    the tabs read at that moment (Toolbox.open_tab), never by this position."""
    base = site_place(service_url, tab.browser, tab.profile)
    return Place(f"{base.name}, open tab {tab.window_index}.{tab.tab_index}", tab.browser, tab.url, tab.profile,
                 (tab.window_index, tab.tab_index))  # fmt: skip


def place_named(name: str, facts: MacFacts) -> Place | None:
    """The Place a remembered name stands for, when this Mac still has it (the app installed, the profile there).
    A remembered open tab stands for its site (the tab itself may be gone)."""
    if name.endswith(" app"):
        app = name[: -len(" app")]
        return app_place(app) if app in facts.installed_apps else None
    m = PLACE_NAME.match(name)
    if not m or m["browser"] not in facts.browsers:
        return None
    if m["profile"] and m["profile"] not in facts.browsers[m["browser"]]:
        return None
    return site_place(f"https://{m['host']}/", m["browser"], m["profile"])


HOST = re.compile(r"(https?://)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?::\d{1,5})?)(/\S*)?", re.I)


def host_of(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "").lower()).split("/")[0]


def site_of(url: str) -> str:
    """The site a host belongs to, without its subdomains ("studio.example.com" -> "example.com"); a two-letter
    country zone keeps one more label ("shop.example.co.uk" -> "example.co.uk")."""
    labels = host_of(url).split(":")[0].split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3 else 2
    return ".".join(labels[-keep:])


def on_host(url: str, wanted: str) -> bool:
    """`url` is on the wanted address's host or a subdomain of it ("www." aside), never a sibling: a page on
    "www.example.com/search" is not on "mail.example.com", while "mail.example.com" is on "example.com"."""
    host, want = host_of(url).split(":")[0], host_of(wanted).split(":")[0]
    return bool(host) and bool(want) and (host == want or host.endswith("." + want))


def site_url(site: str) -> str | None:
    """A site the brain names ("app.example.com", "https://team.example/board") as an https URL, or None when it is
    not shaped like a web host (a word, a phrase, a javascript: or file: link)."""
    m = HOST.fullmatch(site.strip())
    return f"{(m[1] or 'https://').lower()}{m[2].lower()}{m[3] or '/'}" if m else None


def places_for(
    what: str,
    task: str,
    facts: MacFacts,
    remembered: str | None = None,
    site: str | None = None,
    tabs: list[Tab] | None = None,
) -> list[Place]:
    """Where `what` could be done: a tab already open on the wanted site first, then the remembered place, an installed
    app the task names, the site in each browser profile. Never unrelated apps; a missing app is never offered.

    `site` is a host or URL the brain names ("app.example.com"): any site works, and there is no catalog: with no
    site, only apps are offered. A site that is not host-shaped is ignored. `what` speaks first: the apps it names win
    over the task's (task "run the build in Terminal" with what="Terminal" is the Terminal app). When a site is known,
    browsers and Apple's everyday-word apps (Notes, Messages) are not offered as apps; a browser the task names narrows
    the sites to it; the default browser (first in facts.browsers) comes first.

    `tabs` are the open tabs (open_tabs on the real Mac, a list the simulation makes up; None: none known). Only their
    hosts are compared with the wanted site, here; a tab of another site never leaves this function. One tab per
    browser and profile is offered, in the browsers' order, before everything else."""
    what_l, task_l = what.lower(), task.lower()
    url = site_url(site) if site else None
    installed = facts.installed_apps
    places: list[Place] = []
    for text in (what_l, task_l):
        named = [
            a for a in named_apps(text, list(installed)) if not (url and (a in facts.browsers or a.lower() in COMMON_WORD_APPS))
        ]
        if named:
            places += [app_place(a) for a in named]
            break
    # only the user's own words narrow the browsers: a browser the brain wrote into `what` is no evidence of where the
    # user is signed in (rank_by_evidence orders the places instead)
    named_browsers = [b for b in facts.browsers if _names_app(task_l, b)]
    for browser in (named_browsers or list(facts.browsers)) if url else []:
        for profile in facts.browsers[browser] or [None]:
            places.append(site_place(url, browser, profile))
    if remembered:
        known_place = next((p for p in places if p.name == remembered), None) or place_named(remembered, facts)
        if known_place is not None:
            places.insert(0, known_place)
    open_now: list[Place] = []
    for tab in (tabs or []) if url else []:  # opening an app ("Brave Browser") never jumps to a tab
        if tab.browser not in (named_browsers or facts.browsers) or _host(tab.url) != _host(url):
            continue
        if not any(p.app == tab.browser and p.profile == tab.profile for p in open_now):
            open_now.append(tab_place(tab, url))
    unique: dict[str, Place] = {}
    for p in open_now + places:
        unique.setdefault(p.name, p)
    return list(unique.values())


def rank_by_evidence(
    places: list[Place],
    remembered: str | None = None,
    signed_in: list[str] | tuple[str, ...] = (),
    using: str | None = None,
    default: str | None = None,
) -> list[Place]:
    """The places in the order the evidence gives, best first; pure (no Mac, no model):

    1. a tab open on the site now (the live tab list);
    2. the place remembered for this intent, then the other places memory saw work (proven, so signed in then);
    3. an app (a service's own app, or the one the task names): it is not a browser profile;
    4. the other profiles of the browsers that have a tab open on the site, then of the browser the user is using
       (`using`: the browser in front when the task started, else the default browser);
    5. the default browser's other profiles;
    6. every other browser.
    Within a group the given order stands (places_for: browsers in the map's order, each browser's profiles the last
    used first)."""
    tabs = [p for p in places if p.tab is not None]
    known = [p for p in places if p.tab is None and remembered and p.name == remembered]
    known += [p for p in places if p.tab is None and p.name in signed_in and p not in known]
    apps = [p for p in places if p.url is None and p not in known]
    head = tabs + known + apps
    browsers = list(dict.fromkeys([p.app for p in tabs] + [b for b in (using or default, default) if b]))
    rest = [p for p in places if p not in head]
    ordered = [p for b in browsers for p in rest if p.app == b]
    return head + ordered + [p for p in rest if p not in ordered]


def not_installed(app: str, facts: MacFacts) -> bool:
    """An app the Mac does not have (Finder is always there, though it has no bundle in the app folders)."""
    return app != "Finder" and app not in facts.installed_apps


# ------------------------------------------------------------------ the Mac map, built at start


@dataclass
class MacMap:
    """What Karta knows about this Mac at the start of a task, read-only: MacFacts (installed apps, browser
    profiles, folders, now), the default browser, the
    running apps, the open tabs, and the places memory saw work (so signed in then).

    Tabs stay in this object: only their hosts leave it (summary, open_hosts), never titles or full URLs. Pass `tabs`
    to places_for to reuse an open tab."""

    facts: MacFacts
    default_browser: str | None = None
    running: list[str] = field(default_factory=list)
    tabs: list[Tab] = field(default_factory=list)
    signed_in: list[str] = field(default_factory=list)  # remembered place names that worked
    tabs_read: float = 0.0  # when the tabs were read (time.time())

    def open_hosts(self) -> list[tuple[str, str, str | None]]:
        """(host, browser, profile) of each open site, once each, in tab order."""
        seen = [(_host(t.url), t.browser, t.profile) for t in self.tabs]
        return list(dict.fromkeys(seen))

    def summary(self, max_open: int = 15, max_known: int = 10) -> str:
        """Short lines for the brain; hosts only for tabs."""
        f = self.facts
        lines = [f"Now: {f.now:%A %d %B %Y, %H:%M}", f"Installed: {', '.join(f.installed_apps)}"]
        if self.default_browser:
            lines.append(f"Default browser: {self.default_browser}")
        lines += [f"{b} profiles: {', '.join(p)}" for b, p in f.browsers.items() if p]
        if self.running:
            lines.append(f"Running: {', '.join(self.running)}")
        hosts = self.open_hosts()
        if hosts:
            shown = [f"{h} ({b}{', ' + p if p else ''})" for h, b, p in hosts[:max_open]]
            more = f", and {len(hosts) - max_open} more" if len(hosts) > max_open else ""
            lines.append(f"Open: {', '.join(shown)}{more}")
        if self.signed_in:
            lines.append(f"Known signed in: {'; '.join(self.signed_in[:max_known])}")
        if f.folders:
            lines.append(f"Folders: {', '.join(f.folders)}")
        return "\n".join(lines)


_STATIC: dict[tuple, tuple[MacFacts, str | None]] = {}  # apps, profiles, folders, default browser: once per process


def running_apps() -> list[str]:
    """The apps with a Dock presence, from NSWorkspace (read-only); [] off a Mac."""
    try:
        from karta.core.mac_places import running_apps as read

        return list(read())
    except Exception:
        return []


def refresh_tabs(mac: MacMap, run=None, running: list[str] | None = None) -> MacMap:
    """Re-read the running apps and the open tabs (cheap: once per task); the rest of the map is kept. A tab's profile
    is known only when its browser has exactly one profile. `run` and `running` are for tests."""
    return _read_tabs(mac, run, running)


def _read_tabs(mac: MacMap, run, running: list[str] | None) -> MacMap:
    mac.running = running if running is not None else running_apps()
    browsers = [b for b in mac.facts.browsers if not mac.running or b in mac.running]
    tabs = open_tabs(browsers, run=run)
    only = {b: p[0] for b, p in mac.facts.browsers.items() if len(p) == 1}
    mac.tabs = [Tab(t.browser, t.window_index, t.tab_index, t.url, t.title, only.get(t.browser)) for t in tabs]
    mac.tabs_read = time.time()
    return mac


def mac_map(
    memory=None,
    refresh_tabs: bool = True,
    *,
    home: Path | str | None = None,
    app_dirs: tuple[str | Path, ...] | None = None,
    default: str | None = None,
    now: datetime | None = None,
    run=None,
    running: list[str] | None = None,
) -> MacMap:
    """The Mac map (read-only: never opens, launches or probes an app or site). Installed apps, profiles, folders
    and the default browser are read once per process (per home); the tabs and running apps each call unless
    `refresh_tabs` is False. `memory` (a HarnessMemory) gives the known signed-in places. The keyword parameters are
    for tests: a fake home, app folders, default browser, clock, osascript runner and running apps."""
    cache_key = (str(home), app_dirs, default)
    if cache_key not in _STATIC:
        facts = mac_facts(home, app_dirs, default=default if default is not None else "", now=now)
        chosen = default if default is not None else default_browser()
        if chosen in facts.browsers:
            facts.browsers = {chosen: facts.browsers[chosen], **{b: p for b, p in facts.browsers.items() if b != chosen}}
        _STATIC[cache_key] = (facts, chosen)
    cached, chosen = _STATIC[cache_key]
    facts = MacFacts(list(cached.installed_apps), dict(cached.browsers), list(cached.folders), now or datetime.now())
    mac = MacMap(facts, chosen, signed_in=list(memory.places()) if memory is not None else [])
    if refresh_tabs:
        _read_tabs(mac, run, running)
    return mac


# ------------------------------------------------------------------ what the page shows


def _text(item: Item) -> str:
    return item.name if item.kind == "field" else f"{item.name} {item.value}".strip()


def is_login_page(screen: Screen) -> bool:
    """A sign-in page, by structure: a password field on the page (AXSecureTextField, role or subrole: native, and a web
    type=password), in any language. Whether a page with no password field asks you to sign in before you can use it
    (an account field, sign-in buttons only, a code to scan) is meaning: Laya's page state, asked as a place opens and
    taken only when it is sure (Toolbox.asks_sign_in). A sign-in link or a word on the page never counts."""
    return any(i.kind == "field" and i.secret for i in screen.page())


# ------------------------------------------------------------------ where items sit: frames and the tree


def _path(item: Item) -> tuple:
    return tuple(getattr(item.ref, "path", ()) or ())


def _shared(a: tuple, b: tuple) -> int:
    """How many ancestors two paths share, from the outermost in."""
    return next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))


def frame_of(item: Item) -> dict | None:
    """The item's frame in screen points (top-left origin), when the env knows it."""
    try:
        x, y, w, h = (float(getattr(item.ref, k)) for k in "xywh")
    except (AttributeError, TypeError, ValueError):
        return None
    return {"x": x, "y": y, "w": w, "h": h} if w > 0 and h > 0 else None


def beside(item: Item, other: Item) -> bool:
    """Two items next to each other on screen: in one row, or (frames known) on the same line or the next one and
    not far apart sideways; else, without frames, in the same region."""
    if (item.row and other.name in item.row) or (other.row and item.name in other.row):
        return True
    a, b = frame_of(item), frame_of(other)
    if a is not None and b is not None:
        tall = max(a["h"], b["h"])
        gap = max(a["x"] - (b["x"] + b["w"]), b["x"] - (a["x"] + a["w"]), 0.0)
        return abs((a["y"] + a["h"] / 2) - (b["y"] + b["h"] / 2)) <= 2 * tall and gap <= 20 * tall
    return bool(item.region) and item.region == other.region


def in_field_box(item: Item, box: Item) -> bool:
    """`item` sits in the field's own container: it shares the field's parent or grandparent in the accessibility tree
    (a token beside its input); a popup of suggestions shares far less. Read off the pixels (no tree): beside it."""
    a, b = _path(item), _path(box)
    if a and b:
        return _shared(a, b) >= len(b) - 2
    return beside(item, box)


def inside(item: Item, box: Item) -> bool:
    """`item` is drawn inside `box`'s frame (its centre within it); False when either frame is unknown."""
    a, b = frame_of(item), frame_of(box)
    if a is None or b is None:
        return False
    x, y = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    return b["x"] <= x <= b["x"] + b["w"] and b["y"] <= y <= b["y"] + b["h"]


# ------------------------------------------------------------------ did typed text land


def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("\u2019", "'").split()).strip(" .…:")


def holds(value: str, text: str) -> bool:
    """A field holds `text` (spacing, case and a trailing ellipsis aside)."""
    v, t = norm(value), norm(text)
    return bool(t) and (v == t or t in v)


def text_landed(field: Item, text: str, before: Screen | None, after: Screen) -> Item | None:
    """The one check that typing worked (Toolbox.put and its local repair, expect.field_holds): what shows `text`
    typed into `field` (`field` as read on `after`), or None. The field itself when its value holds the text; else a
    token the field made of it (`token_of`)."""
    if holds(field.value or "", text):
        return field
    return token_of(field, text, before, after)


def token_of(field: Item, text: str, before: Screen | None, after: Screen) -> Item | None:
    """A field that turns what was typed into a token (a recipient, a tag, a label) and empties itself, its value
    often never showing it: an item new since `before` (no field, menu command or chrome), holding the whole text (a
    token's trailing remove mark aside, ✕, "X"), inside the field's frame, or in the field's own container (sharing
    its parent or grandparent in the accessibility tree), or, read off the pixels, beside it. A suggestion list that
    popped up elsewhere is no token. One line of text only; never by app or site."""
    if field.multiline or "\n" in text.strip() or not norm(text) or (field.value or "").strip():
        return None
    want = norm(text)
    return next(
        (
            i
            for i in new_items(after, before)
            if i.kind != "field"
            and not i.menu
            and not i.chrome
            and want in norm(f"{i.name} {i.value}")
            and (inside(i, field) or in_field_box(i, field))
        ),
        None,
    )


def _words(text: str) -> list[str]:
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) > 2]


OCR_FOLD = str.maketrans("0157", "olst")  # OCR slips inside words: "Pate1" -> "patel"


def _fold(word: str) -> str:
    return word.translate(OCR_FOLD) if any(c.isalpha() for c in word) else word


def _near(a: str, b: str) -> bool:
    """Equal after OCR folding, or one letter apart in a word of five or more letters."""
    a, b = _fold(a), _fold(b)
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def _plain(text: str) -> str:
    return " ".join(_fold(w) for w in "".join(c.lower() if c.isalnum() else " " for c in text).split())


PROMPT_ECHO = re.compile("^.*[$%\u276f>#]\\s*")  # a shell prompt before what we typed: "~/karta % claude"


def _ours(item: Item, typed: set[str], held: set[str]) -> bool:
    """Text the harness typed that is still ours: sitting in a field (or read off one by OCR), or echoed at a prompt."""
    text = _plain(_text(item))
    if not text:
        return False
    if text in held:
        return True
    echo = _plain(PROMPT_ECHO.sub("", item.name, count=1)) if PROMPT_ECHO.match(item.name) else ""
    return bool(echo) and echo in typed


def shows(screen: Screen, words: str, typed: set[str] = frozenset()) -> bool:
    """`words` (or all their distinctive words) are on the page, in something other than a field, a menu
    command, or text the harness typed itself.

    Loose for OCR: "Pate1" is "Patel", one slipped letter in a word of five or more letters passes. Typed text is ours
    while a field still holds it (not sent yet: its OCR copy does not count either) or when a terminal echoes it after
    its prompt; once the field is empty, the same words in the conversation are the result."""
    typed_plain = {_plain(t) for t in typed} - {""}
    page = screen.page()
    held = {_plain(f.value) for f in page if f.kind == "field" and _plain(f.value) in typed_plain}
    texts = [_plain(_text(i)) for i in page if i.kind != "field" and not _ours(i, typed_plain, held)]
    phrase = _plain(words)
    if not phrase:
        return False
    if any(phrase in t for t in texts):
        return True
    wanted = list(dict.fromkeys(_words(words)))
    if not wanted:
        return False
    seen = {w for t in texts for w in _words(t)}
    return all(any(_near(w, s) for s in seen) for w in wanted)


# ------------------------------------------------------------------ what changed: proof is state, not words


def _key(item: Item) -> tuple:
    return _plain(item.name), item.kind, _plain(item.value), item.checked, item.focused


def new_items(now: Screen, before: Screen | None) -> list[Item]:
    """The items on `now` that were not on `before` (by name, kind, value, checked and focus, OCR-folded; one each: a
    second copy of a line that was there once is new). Everything when there is no `before`."""
    if before is None:
        return list(now.items)
    was: dict[tuple, int] = {}
    for i in before.items:
        was[_key(i)] = was.get(_key(i), 0) + 1
    out = []
    for i in now.items:
        k = _key(i)
        if was.get(k, 0) > 0:
            was[k] -= 1
        else:
            out.append(i)
    return out


def since(now: Screen, before: Screen | None) -> Screen:
    """`now` cut down to what changed since `before`: the new items, and the window title only when it is new. Words
    already on screen before an action are no proof the action did anything, so checks read this."""
    if before is None:
        return now
    window = now.window if now.window != before.window else ""
    return Screen(now.app, new_items(now, before), now.url, window, now.profile)


def modal_items(screen: Screen) -> list[Item]:
    """Every item of the dialogs that block the page (a sheet, a dialog window, a modal web dialog), prose too; never
    a panel (a dialog role without aria-modal: a side cart, a drawer, a flyout), which the page's steps act on."""
    return [i for i in screen.items if i.dialog and not i.panel and not i.menu]


def dialogs(screen: Screen) -> int:
    """How many dialogs, sheets or popups are open, by structure: the copies of the most repeated control among the
    items marked `dialog` (two compose windows each have one "Minimize"); 1 when marked items have no control twice."""
    marked = [i for i in screen.items if i.dialog and not i.menu]
    if not marked:
        return 0
    counts: dict[tuple, int] = {}
    for i in marked:
        if i.kind in ACTIONABLE:
            counts[(_plain(i.name), i.kind)] = counts.get((_plain(i.name), i.kind), 0) + 1
    return max(counts.values(), default=1)


def dialog_items(screen: Screen) -> list[Item]:
    """Every item of the open dialogs, sheets and popups (prose too)."""
    return [i for i in screen.items if i.dialog and not i.menu]


def shows_new(now: Screen, before: Screen | None, words: str, typed: set[str] = frozenset()) -> bool:
    """`words` show (`shows`) because of what changed since `before`: in the lines that appeared, or on the page now
    though not before, with at least one of their distinctive words in a line that appeared (a new message under a
    chat's old header), or in a dialog when one more dialog is open than before (a second window of the same name: its
    title was on screen already). Words that showed already before never count, wherever they are now."""
    if before is None:
        return shows(now, words, typed)
    fresh = since(now, before)
    if shows(fresh, words, typed):
        return True
    if dialogs(now) > dialogs(before) and shows(Screen(now.app, dialog_items(now), now.url), words, typed):
        return True
    if not shows(now, words, typed) or shows(before, words, typed):
        return False
    # a distinctive word is one the screen did not show before: read off the screens, in any language, with no list
    return any(shows(fresh, w, typed) for w in dict.fromkeys(_words(words)) if not shows(before, w, typed))


# ------------------------------------------------------------------ dialogs in the way

CHOICE = re.compile("^\\s*[\u276f>\u203a]?\\s*(\\d)[.)]\\s+\\S")  # a numbered choice in a text-only app: "> 1. Yes"
PROSE = ("text", "heading", "other")
ACTIONABLE = ("button", "link", "field", "checkbox", "row", "tab")


SYSTEM_PROMPTERS = (  # macOS processes that put up privacy (TCC), security and file-access prompts for another app
    "UserNotificationCenter",
    "CoreServicesUIAgent",
    "SecurityAgent",
    "universalAccessAuthWarn",
    "tccd",
)


def system_prompt(screen: Screen) -> bool:
    """A prompt the system puts up for another app (a camera, microphone, contacts or files ask; a security alert),
    by who owns it, never by its words: its process (SYSTEM_PROMPTERS) is in front with a window of its own. Its
    answer is always the user's, in any language."""
    return screen.app in SYSTEM_PROMPTERS and any(not i.menu for i in screen.items)


def blocking_dialog(screen: Screen) -> list[Item] | None:
    """The controls of a dialog that blocks the page, or None when nothing does. Structure only, nothing about any
    one app or site:
    1. items the env marks inside a dialog that blocks (`modal_items`: an AXSheet, a dialog window, a web dialog with
       aria-modal): their controls (all of them if none acts). A panel (a dialog role that does not block: a side
       cart, a drawer, a flyout) is never one: the step acts on the page as usual;
    2. numbered choices 1, 2, ... under a question in a text-only screen ("1. Yes, proceed" / "2. No, exit"): a
       terminal has no structure of its own.
    A system prompt (`system_prompt`) is in the way whole. Buttons that merely say Allow or OK somewhere on a page are
    the page's own, never a dialog."""
    if system_prompt(screen):
        items = [i for i in screen.items if not i.menu]
        return [i for i in items if i.kind in ACTIONABLE and i.enabled] or items
    marked = modal_items(screen)
    if marked:
        return [i for i in marked if i.kind in ACTIONABLE and i.enabled] or marked
    items = [i for i in screen.items if not i.menu]
    choices = [i for i in items if CHOICE.match(i.name)]
    numbers = [int(CHOICE.match(i.name)[1]) for i in choices]
    if len(choices) >= 2 and numbers[:2] == [1, 2] and any(i.name.rstrip().endswith("?") for i in items if i not in choices):
        return choices
    return None


def in_the_way(screen: Screen) -> list[Item]:
    """The items of what blocks the page, prose too, for reading what it asks: the blocking dialogs' items; a
    text-only app's whole screen (numbered choices under a question). [] when nothing blocks."""
    if not blocking_dialog(screen):
        return []
    if system_prompt(screen):
        return [i for i in screen.items if not i.menu]
    return modal_items(screen) or [i for i in screen.items if not i.menu]


def page_fact(screen: Screen, ready: str = "", typed: set[str] = frozenset()) -> Outcome | None:
    """What code can say about a page without Laya: a permission ask or a dialog in the way, a login page,
    or OK when the brain's ready words show; None when only Laya can tell."""
    if blocking_dialog(screen) is not None:
        asks = system_prompt(screen) or any(i.browser_ui for i in modal_items(screen))  # the system's or the browser's
        return Outcome.NEEDS_PERMISSION if asks else Outcome.BLOCKED_BY_DIALOG
    if is_login_page(screen):
        return Outcome.LOGIN_PAGE
    if ready and shows(screen, ready, typed):
        return Outcome.OK
    return None


# ------------------------------------------------------------------ interruptions and permission asks

NEVER_DISMISS = re.compile(  # words that agree, pay, grant or start something: never a way out of an interruption
    r"\b(try|trial|subscribe|accept|agree|upgrade|buy|purchase|pay|allow|enable|turn on|install|start|join|sign up"
    r"|confirm|yes|continue|delete|remove|send|save|update|download|share)\b"
)
NOT_CLICKABLE = ("field", "checkbox", "heading", "row", "tab", "menu")


LIST_ROW_ROLES = {"AXRow", "AXOutlineRow", "AXCell"}  # a list's, a table's or an outline's row
VIEW_CONTROL_ROLES = {"AXToolbar", "AXDateField"}  # a view's own controls: a toolbar's, a date control's steppers


def ancestor_roles(item: Item) -> set[str]:
    """The roles of the item's ancestors in the accessibility tree (ax_walk keeps them in its path), itself left out;
    empty for an item read off the pixels or with no tree."""
    path = tuple(getattr(item.ref, "path", ()) or ())
    return {e[0] for e in path[:-1] if isinstance(e, tuple) and e}


def in_list_row(item: Item) -> bool:
    """A field inside a list, table or outline row: a native app's list-item editor (a new reminder, a row's title)."""
    return bool(ancestor_roles(item) & LIST_ROW_ROLES)


def view_control(item: Item) -> bool:
    """A control of the view itself, by its ancestors' roles: in a toolbar or a date control (a next or previous day),
    never a form's own button."""
    return bool(ancestor_roles(item) & VIEW_CONTROL_ROLES)


def dismiss_option(items: list[Item]) -> Item | None:
    """The way out of an unrelated interruption, by structure: the dialog's own cancel or close (its AXCancelButton, a
    close button: `Item.cancels`). None without one: which other button closes it without agreeing to anything is
    meaning, Laya's (Toolbox.way_out), and NEVER_DISMISS vetoes its answer. Menus, window chrome (a window's own
    close button) and disabled items are never taken."""
    usable = [i for i in items if i.enabled and not i.menu and not i.chrome and i.kind not in NOT_CLICKABLE]
    return next((i for i in usable if i.cancels and i.dialog), None)


# ------------------------------------------------------------------ open but not usable


def content_rows(screen: Screen) -> list[Item]:
    """The rows a place lists (AXRow, AXCell, an outline row) that are its content (content.items: never a sidebar's
    or a toolbar's), by structure. None listed is the fact; whether that means an empty place or one not set up is
    Laya's (Toolbox.empty_place)."""
    from karta.harness import content

    return [i for i in content.items(screen) if i.kind == "row" and i.name.strip()]
