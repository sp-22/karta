"""When: a schedule's time phrase, read by code with no brain call ("every weekday at 9, …", "at 6 pm today remind
me to …", "in 20 minutes, …", "check my mail every 30 minutes").

A small recurrence grammar (every day / weekday / weekend / <weekday>[s] / every N minutes or hours) and a clock
("at 9", "9:30 pm", "noon", "9 o'clock") are code; a one-off's day is the platform's date detector (NSDataDetector,
in the user's calendar and time zone: `detect`), with today / tonight / tomorrow / a weekday as code when it finds
none. The phrase must stand at the start or the end of what was said (or anywhere in a "remind me"), so a task that
merely names a time ("make an event tomorrow at 3 pm") stays a task.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS = frozenset(range(5))
WEEKEND = frozenset({5, 6})
EVERY_DAY = frozenset(range(7))
MIN_EVERY_S = 15 * 60.0  # "every N minutes" runs at most this often: each run is a task, and a task may cost a call
PART_OF_DAY = {"morning": (9, 0), "evening": (18, 0), "night": (21, 0), "afternoon": (14, 0)}

_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?"
_PLURAL = r"(?:mon|tues|wednes|thurs|fri|satur|sun)days"
_DAY_LIST = rf"{_DAY}(?:(?:\s*,\s*|\s+and\s+|\s+or\s+|\s*,\s*and\s+){_DAY})*"
_CLOCK = (
    r"(?:at\s+)?(?:(?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?:\s*(?P<ampm>a\.?m\.?|p\.?m\.?))?(?:\s*o'?\s?clock)?"
    r"|(?P<named>noon|midnight))(?:\s+(?:in\s+the\s+)?(?P<part>morning|afternoon|evening|night))?"
)
_EVERY_N = r"every\s+(?P<n>\d+|a|an|one|two|three|four|five|six|ten|fifteen|twenty|thirty|half\s+an?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)"
_RECUR = (
    rf"\b(?:(?:every|each)\s+(?P<every>day|morning|evening|night|afternoon|weekday|weekend|{_DAY_LIST})"
    rf"|(?P<daily>daily|hourly|(?:on\s+)?weekdays|(?:on\s+)?weekends|(?:on\s+)?{_PLURAL}(?:(?:\s*,\s*|\s+and\s+){_PLURAL})*)"
    rf"|(?P<evhour>every\s+hour|every\s+half\s+hour)|{_EVERY_N})\b"
)
_SMALL = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "fifteen": 15}
_SMALL |= {"twenty": 20, "thirty": 30}
_IN = re.compile(
    r"\bin\s+(?P<n>\d+|a|an|one|two|three|five|ten|fifteen|twenty|thirty|half\s+an?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b"
)
_DAYWORD = re.compile(rf"\b(?P<w>today|tonight|tomorrow|this\s+evening|this\s+afternoon|(?:on\s+|next\s+)?{_DAY})\b")
_REMIND = re.compile(r"\bremind\s+me\b(?:\s+(?:to|about|that|of))?", re.I)
_EDGE = r"[\s,;:.!?-]*"


@dataclass
class When:
    """When a schedule runs: `days` at `at` (hour, minute), every `every_s` seconds, or once at `once` (epoch)."""

    days: frozenset[int] = frozenset()
    at: tuple[int, int] | None = None
    every_s: float = 0.0
    once: float = 0.0
    anchor: float = 0.0  # every N: counted from here

    def next_after(self, t: float) -> float | None:
        """The first run strictly after `t`; None when a one-off is past."""
        if self.once:
            return self.once if self.once > t else None
        if self.every_s:
            k = int((t - self.anchor) // self.every_s) + 1
            return self.anchor + max(k, 1) * self.every_s
        if not self.days or self.at is None:
            return None
        day = datetime.fromtimestamp(t).date()
        for i in range(8):
            d = day + timedelta(days=i)
            at = datetime(d.year, d.month, d.day, *self.at).timestamp()
            if at > t and d.weekday() in self.days:
                return at
        return None

    def words(self, now: float | None = None) -> str:
        """How it is said: "every weekday at 9 AM", "every 30 minutes", "today at 6 PM"."""
        if self.every_s:
            minutes = round(self.every_s / 60)
            if minutes % 60 == 0:
                hours = minutes // 60
                return "every hour" if hours == 1 else f"every {hours} hours"
            return f"every {minutes} minutes"
        if self.once:
            when = datetime.fromtimestamp(self.once)
            today = datetime.fromtimestamp(now).date() if now else date.today()
            ahead = (when.date() - today).days
            day = (
                "today"
                if ahead == 0
                else "tomorrow"
                if ahead == 1
                else f"on {when:%A}"
                if 1 < ahead < 7
                else f"on {when.day} {when:%B}"
            )
            return f"{day} at {clock_words(when.hour, when.minute)}"
        at = f" at {clock_words(*self.at)}" if self.at else ""
        if self.days == EVERY_DAY:
            return "every day" + at
        if self.days == WEEKDAYS:
            return "every weekday" + at
        if self.days == WEEKEND:
            return "every weekend day" + at
        names = [DAYS[d].title() for d in sorted(self.days)]
        return "every " + (" and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]) + at

    def to_dict(self) -> dict:
        return {"days": sorted(self.days), "at": list(self.at) if self.at else None, "every_s": self.every_s,
                "once": self.once, "anchor": self.anchor}  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict) -> When:
        at = d.get("at")
        return cls(frozenset(d.get("days") or ()), tuple(at) if at else None, float(d.get("every_s") or 0),
                   float(d.get("once") or 0), float(d.get("anchor") or 0))  # fmt: skip


@dataclass
class Scheduled:
    """A schedule read from what was said: when, the task (or what to remind), and whether it is a reminder."""

    when: When
    task: str
    remind: bool = False
    why: str = ""  # set: it is a schedule, but this is what is wrong with it (said back)


def clock_words(h: int, m: int) -> str:
    """9:00 -> "9 AM", 18:30 -> "6:30 PM", 12:00 -> "noon"."""
    if (h, m) == (12, 0):
        return "noon"
    if (h, m) == (0, 0):
        return "midnight"
    hh = h % 12 or 12
    return f"{hh}{f':{m:02d}' if m else ''} {'AM' if h < 12 else 'PM'}"


def _number(word: str) -> float:
    word = " ".join(word.split())
    if word.startswith("half"):
        return 0.5
    return float(word) if word.isdigit() else float(_SMALL.get(word, 0))


def _seconds(n: str, unit: str) -> float:
    return _number(n) * (3600.0 if unit.startswith("h") else 60.0)


def day_set(text: str) -> frozenset[int]:
    """The weekdays a list names: "monday and thursdays" -> {0, 3}."""
    out = set()
    for word in re.findall(_DAY, text):
        stem = word.rstrip("s")[:3]
        out |= {i for i, d in enumerate(DAYS) if d.startswith(stem)}
    return frozenset(out)


def clock_of(found: re.Match, recurring: bool) -> tuple[int, int] | None:
    """(hour, minute) of a clock match. With no am/pm: a part of the day decides, else 7..11 are morning, 12 noon,
    and 1..6 afternoon (9 -> 9 AM, 6 -> 6 PM)."""
    if found["named"]:
        return (12, 0) if found["named"] == "noon" else (0, 0)
    if found["h"] is None:
        return None
    h, m = int(found["h"]), int(found["m"] or 0)
    if h > 23 or m > 59:
        return None
    ampm = (found["ampm"] or "").replace(".", "")
    part = found["part"] or ""
    if ampm == "pm" or part in ("afternoon", "evening", "night"):
        h = h if h >= 12 else h + 12
    elif ampm == "am" or part == "morning":
        h = 0 if h == 12 else h
    elif 1 <= h <= 6:
        h += 12
    return h, m


def clock_in(text: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """The first clock in `text` that is one ("at 9", "9:30", "6 pm", "noon": a bare number needs "at" or am/pm or
    o'clock), and its span."""
    for found in re.finditer(r"\b" + _CLOCK + r"\b", text):
        whole = found[0]
        if not whole.strip():
            continue
        bare = found["h"] and not (found["m"] or found["ampm"] or "clock" in whole or whole.startswith("at") or found["part"])
        if bare:
            continue
        hm = clock_of(found, False)
        if hm is not None:
            return hm, found.span()
    return None, None


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " + text[b:]
    return text


def _task_words(text: str) -> str:
    text = " ".join(text.split())
    text = re.sub(r"^(?:[,;:.!-]|\s|then\b|and\b|please\b|karta\b)+", "", text, flags=re.I)
    return text.strip(" ,;:.!?-")


def yours(text: str) -> str:
    """What to remind, said back to the user: "call my mom" -> "call your mom"."""
    swap = {"my": "your", "me": "you", "i": "you", "i'm": "you're", "mine": "yours", "myself": "yourself"}
    return " ".join(swap.get(w.lower(), w) for w in text.split())


def parse(text: str, now: float, detect: Callable[[str], datetime | None] | None = None) -> Scheduled | None:
    """A schedule in what was said, by code; None when it is not one. `detect` is the platform date detector
    (`detected`; a fake in tests)."""
    said = " ".join(str(text or "").lower().replace("\u2019", "'").split()).rstrip(".!?")
    lead = re.sub(r"^(?:(?:hey |ok |okay )?karta,? )?(?:please )?", "", said)
    remind = _REMIND.search(lead)
    recur = re.search(_RECUR, lead)
    if recur is not None:
        clock, cspan = clock_in(lead)
        spans = [recur.span()]
        if cspan:
            a, b = sorted([recur.span(), cspan])
            if re.fullmatch(r"[\s,]*", lead[a[1] : b[0]]) or remind:
                spans.append(cspan)
            else:
                clock = None  # a clock apart from the recurrence is the task's ("every day, open the 9:30 deck")
        a, b = min(s[0] for s in spans), max(s[1] for s in spans)
        if remind or re.fullmatch(_EDGE, lead[:a]) or re.fullmatch(_EDGE, lead[b:]):
            return _recurring(lead, recur, clock, spans, remind, now)
        # "delete every monday meeting": the phrase is inside a task
    return _one_off(lead, remind, now, detect)


def _recurring(lead: str, recur: re.Match, clock, spans, remind, now: float) -> Scheduled:
    rest = _cut(lead, spans)
    task, is_remind = _task_of(rest, remind)
    if recur["n"] or recur["evhour"] or recur["daily"] == "hourly":
        every = 3600.0 if (recur["evhour"] == "every hour" or recur["daily"] == "hourly") else 1800.0
        if recur["n"]:
            every = _seconds(recur["n"], recur["unit"])
        when = When(every_s=every, anchor=now)
        if every < MIN_EVERY_S:
            return Scheduled(when, task, is_remind, f"I run a schedule at most every {int(MIN_EVERY_S // 60)} minutes.")
        return Scheduled(when, task, is_remind, "" if task else "What should I do then?")
    word = (recur["every"] or recur["daily"] or "").removeprefix("on ").strip()
    if word in ("day", "daily", "morning", "evening", "night", "afternoon"):
        days = EVERY_DAY
    elif word in ("weekday", "weekdays"):
        days = WEEKDAYS
    elif word in ("weekend", "weekends"):
        days = WEEKEND
    else:
        days = day_set(word)
    clock = clock or PART_OF_DAY.get(word)
    when = When(days=days, at=clock)
    if clock is None:
        return Scheduled(when, task, is_remind, "At what time? Say it again with one, like every weekday at 9.")
    return Scheduled(when, task, is_remind, "" if task else "What should I do then?")


def _task_of(rest: str, remind) -> tuple[str, bool]:
    found = _REMIND.search(rest)
    if found:
        return yours(_task_words(rest[: found.start()] + " " + rest[found.end() :])), True
    return _task_words(rest), bool(remind)


def _one_off(lead: str, remind, now: float, detect) -> Scheduled | None:
    rel = _IN.search(lead)
    clock, cspan = clock_in(lead)
    dayword = _DAYWORD.search(lead)
    spans = [s for s in (rel.span() if rel else None, cspan, dayword.span() if dayword else None) if s]
    if not spans:
        return None
    a, b = min(s[0] for s in spans), max(s[1] for s in spans)
    gap = _cut(lead[a:b], [(s[0] - a, s[1] - a) for s in spans])
    first = re.fullmatch(_EDGE, lead[:a]) or (rel and not clock and not dayword and re.fullmatch(_EDGE, lead[b:]))
    if not remind and (gap.strip(" ,") or not first):
        return None  # a time inside or after a task is the task's ("make an event tomorrow at 3 pm")
    if not (rel or clock):
        return None  # a day alone is no time
    task, is_remind = _task_of(_cut(lead, spans), remind)
    if rel:
        once = now + _seconds(rel["n"], rel["unit"])
    else:
        found = detect(lead) if detect else None
        day = _day_of(dayword["w"] if dayword else "", now)
        named = bool(dayword)
        if found is not None and (dayword or found.date() != datetime.fromtimestamp(now).date()):
            day, named = found.date(), True  # the detector reads dates code does not ("on 12 November")
        if dayword and re.search(r"tonight|evening|afternoon", dayword["w"]) and clock[0] < 12:
            clock = (clock[0] + 12, clock[1])
        once = datetime(day.year, day.month, day.day, *clock).timestamp()
        if once <= now:
            if named:
                return Scheduled(When(once=once), task, is_remind, f"{When(once=once).words(now)} has passed.")
            nxt = day + timedelta(days=1)  # "at 9" said after 9: tomorrow's
            once = datetime(nxt.year, nxt.month, nxt.day, *clock).timestamp()
    when = When(once=once)
    return Scheduled(when, task, is_remind, "" if task else "What should I do then?")


def _day_of(word: str, now: float) -> date:
    today = datetime.fromtimestamp(now).date()
    word = word.removeprefix("on ").removeprefix("next ").strip()
    if word == "tomorrow":
        return today + timedelta(days=1)
    days = day_set(word) if word and word not in ("today", "tonight") and "this" not in word else frozenset()
    if days:
        want = next(iter(days))
        return today + timedelta(days=(want - today.weekday()) % 7 or 7)
    return today


def detected(text: str) -> datetime | None:
    """The first date and time the platform's date detector finds in `text` (NSDataDetector, the user's calendar
    and time zone); None when it finds none, or off a Mac."""
    try:
        from Foundation import NSDataDetector, NSTextCheckingTypeDate

        detector, _ = NSDataDetector.dataDetectorWithTypes_error_(NSTextCheckingTypeDate, None)
        found = detector.firstMatchInString_options_range_(text, 0, (0, len(text)))
    except Exception:
        return None
    if not found or not found.date():
        return None
    return datetime.fromtimestamp(found.date().timeIntervalSince1970())
