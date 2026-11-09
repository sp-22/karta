"""Calendar events read through EventKit, read-only, with EventKit faked (no test asks for calendar access).

- macos.calendar_events: one day across all calendars, "HH:MM Title" and "all day: Title", sorted; full access asked
  once a process, its answer waited for with a timeout; no access is None;
- the collect: a day asked for ("today", "tomorrow", a weekday, a date) reads the calendar; an empty day is a fact
  saying so; no access reads the screen as before; the trace and the ledger show a count, never a title.
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta

import pytest

from karta.base import macos
from karta.harness import tell
from karta.harness.decide import FITS
from karta.harness.tools import calendar_day, day_words
from karta.harness.types import Item, Screen
from tests.test_harness_tools import FakeEnv, FakeLaya, ch, toolbox

DAY = date(2026, 10, 28)
NOT_DETERMINED, DENIED, FULL = 0, 2, 3


class Stamp:
    def __init__(self, at: datetime):
        self.at = at

    def timeIntervalSince1970(self) -> float:
        return self.at.timestamp()


class Event:
    def __init__(self, title, at: datetime | None = None):
        self._title, self._at = title, at

    def title(self):
        return self._title

    def isAllDay(self) -> bool:
        return self._at is None

    def startDate(self):
        return Stamp(self._at)


def fake_eventkit(monkeypatch, status=FULL, events=(), answer=True):
    """An EventKit module whose store serves `events`; `answer` is the user's reply to the prompt (None: no reply)."""
    seen: dict = {"requests": 0, "query": None}

    class Store:
        @staticmethod
        def authorizationStatusForEntityType_(kind):
            return seen.get("status", status)

        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def requestFullAccessToEventsWithCompletion_(self, done):
            seen["requests"] += 1
            if answer is not None:
                seen["status"] = FULL if answer else DENIED
                done(answer, None)

        def predicateForEventsWithStartDate_endDate_calendars_(self, start, end, calendars):
            seen["query"] = (start.timeIntervalSince1970(), end.timeIntervalSince1970(), calendars)
            return "query"

        def eventsMatchingPredicate_(self, query):
            return list(events)

    kit = types.ModuleType("EventKit")
    kit.EKEventStore = Store
    kit.EKEntityTypeEvent = 0
    kit.EKAuthorizationStatusNotDetermined, kit.EKAuthorizationStatusFullAccess = NOT_DETERMINED, FULL
    monkeypatch.setitem(sys.modules, "EventKit", kit)
    monkeypatch.setattr(macos, "_calendar", {"store": None, "asked": False})
    monkeypatch.setattr(macos, "CALENDAR_ANSWER_S", 0.2)
    return seen


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute)


# ---------------------------------------------------------------- reading the calendar


def test_a_days_events_across_all_calendars_sorted_all_day_first(monkeypatch):
    events = [Event("Pottery class", at(17, 30)), Event("Dentist", at(9, 15)), Event("Harvest fair"), Event(None, at(12))]
    seen = fake_eventkit(monkeypatch, events=events)
    assert macos.calendar_events(DAY) == ["all day: Harvest fair", "09:15 Dentist", "12:00 untitled", "17:30 Pottery class"]
    start, end, calendars = seen["query"]
    assert (start, end, calendars) == (at(0).timestamp(), at(0, day=DAY + timedelta(days=1)).timestamp(), None)
    assert seen["requests"] == 0  # access was there already: nothing asked


def test_an_event_that_began_the_day_before_counts_from_midnight(monkeypatch):
    fake_eventkit(monkeypatch, events=[Event("Night shift", at(22, day=DAY - timedelta(days=1)))])
    assert macos.calendar_events(DAY) == ["00:00 Night shift"]


def test_full_access_is_asked_once_and_its_answer_waited_for(monkeypatch):
    seen = fake_eventkit(monkeypatch, status=NOT_DETERMINED, events=[Event("Dentist", at(9))])
    assert macos.calendar_events(DAY) == ["09:00 Dentist"]
    assert macos.calendar_events(DAY) == ["09:00 Dentist"] and seen["requests"] == 1


def test_access_refused_or_never_answered_is_none_and_never_asked_again(monkeypatch):
    seen = fake_eventkit(monkeypatch, status=NOT_DETERMINED, answer=False)
    assert macos.calendar_events(DAY) is None and macos.calendar_events(DAY) is None and seen["requests"] == 1
    seen = fake_eventkit(monkeypatch, status=NOT_DETERMINED, answer=None)  # the prompt left unanswered: the timeout
    assert macos.calendar_events(DAY) is None and macos.calendar_events(DAY) is None and seen["requests"] == 1
    seen = fake_eventkit(monkeypatch, status=DENIED)
    assert macos.calendar_events(DAY) is None and seen["requests"] == 0


def test_the_guard_refuses_the_real_eventkit():
    with pytest.raises(RuntimeError, match="real machine"):
        macos.calendar_events(DAY)


# ---------------------------------------------------------------- which collect reads it, and for which day


def test_the_day_is_resolved_by_the_platform_and_only_events_ask_for_it():
    today = date.today()
    assert calendar_day("events", "what's on my calendar today", None) == today
    assert calendar_day("meetings tomorrow", "", None) == today + timedelta(days=1)
    weekday = calendar_day("events", f"what do I have on {(today + timedelta(days=3)):%A}", None)
    assert weekday is not None and weekday.weekday() == (today + timedelta(days=3)).weekday()
    assert calendar_day("events", "what's on my calendar", None) is None  # no day: the screen
    assert calendar_day("links", "send me today's links", None) is None
    assert calendar_day("events", "events near me today", "https://tickets.example/") is None  # the page's own events
    assert calendar_day("calendar events", "today", "https://diary.example/") == today


def test_day_words():
    assert [day_words(DAY + timedelta(days=n), DAY) for n in (0, 1, 2, 9)] == [
        "today",
        "tomorrow",
        "on Friday",
        "on 6 November",
    ]


class CalendarEnv(FakeEnv):
    def __init__(self, events):
        super().__init__({"a": Screen("Diary", [Item("Kiln firing 10:00", "text", source="ocr")])}, "a")
        self.events, self.asked = events, []

    def calendar_events(self, day):
        self.asked.append(day)
        return self.events


def test_a_collect_of_todays_events_reads_the_calendar_and_shows_only_a_count():
    env = CalendarEnv(["all day: Harvest fair", "09:15 Dentist"])
    tb = toolbox(env, FakeLaya(), task="what's on my calendar today")
    result = tb.tool_collect("events", 0, "events")
    assert result.ok and env.asked == [date.today()]
    assert [c.text() for c in tb.slots["events"]] == ["all day: Harvest fair", "09:15 Dentist"]
    assert "2 calendar event(s) today" in result.detail
    for line in [result.detail, *tb.trace]:
        assert "Dentist" not in line and "Harvest" not in line
    said, used = tell.word_answer("what's on my calendar today", [c.text() for c in tb.slots["events"]], None, plain=True)
    assert "Dentist" in said and not used


def test_an_empty_day_is_a_fact_saying_so():
    tb = toolbox(CalendarEnv([]), FakeLaya(), task="what's on my calendar today")
    assert tb.tool_collect("events", 0, "events").ok
    facts = [c.text() for c in tb.slots["events"]]
    assert facts == ["Nothing on your calendar today"]
    assert tell.word_answer("what's on my calendar today", facts, None, plain=True)[0].startswith(
        "Nothing on your calendar today"
    )


def test_no_access_reads_the_screen_as_before():
    env = CalendarEnv(None)
    tb = toolbox(env, FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))), task="what's on my calendar today")
    assert tb.tool_collect("events", 0, "events").ok and env.asked
    assert [c.title for c in tb.slots["events"]] == ["Kiln firing 10:00"]
