"""Round 1 contract and Streamlit baseline."""

import re
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import streamlit as st
from openai import OpenAIError
from streamlit.testing.v1 import AppTest

from cal_client import CalClient, CalendarError, instant, utc_stamp
from live_support import ask, chat_clock_for_tomorrow, check_test_event, live, local_start, time_in_reply, ui
from scheduling import Conversation, DurationChoice


def test_refresh_starts_an_empty_chat(monkeypatch):
    calendar = CalClient("", ZoneInfo("America/New_York"), event_type_id=1)
    monkeypatch.setattr(calendar, "agenda", lambda first, last: [])
    session = Conversation(calendar)
    session.messages.append({"role": "user", "content": "Book a 40-minute meeting"})
    session.selection.pending_question = DurationChoice(session.messages[0]["content"], 40, [30, 60])
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state.conversation = session
    app.run()
    assert len(app.chat_message) == 1 and len(app.chat_input) == 1

    next(b for b in app.button if b.label == "Refresh").click().run()
    assert not app.exception and not app.chat_message and not app.error
    fresh = app.session_state.conversation
    assert fresh.calendar is calendar and fresh.messages == [] and fresh.selection.pending_question is None
    assert len(app.chat_input) == 1


def test_weekly_schedule_navigation_preserves_chat(monkeypatch):
    calendar = CalClient("", ZoneInfo("America/New_York"), event_type_id=1)
    now = datetime.now(calendar.zone)
    monday = now.date() - timedelta(days=now.weekday())
    next_monday = monday + timedelta(weeks=1)
    reads = []

    def agenda(first, last):
        reads.append((first, last))
        if first == str(monday):
            start, name = now - timedelta(hours=1), "Past attendee"
        else:
            start, name = datetime.combine(next_monday, time(9, 30), calendar.zone), "Maya Chen"
        return [{"start": utc_stamp(start), "end": utc_stamp(start + timedelta(minutes=30)),
                 "attendees": [{"name": name}], "status": "pending"}]

    monkeypatch.setattr(calendar, "agenda", agenda)
    session = Conversation(calendar, messages=[{"role": "user", "content": "Move my Tuesday meeting"}])
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state.conversation = session
    app.run()
    assert not app.exception and not app.text
    assert "No upcoming meetings this week." in [item.value for item in app.caption]
    assert next(b for b in app.button if b.label == "Previous week").disabled
    next(b for b in app.button if b.label == "Next week").click().run()
    assert [item.value for item in app.text] == ["9:30 AM–10:00 AM  ·  Maya Chen  ·  Awaiting approval"]
    assert app.chat_message[0].markdown[0].value == "Move my Tuesday meeting"
    app.run()
    assert reads == [(str(day), str(day + timedelta(days=6))) for day in (monday, next_monday)]
    next(b for b in app.button if b.label == "Previous week").click().run()
    assert not app.exception and not app.text and len(app.chat_input) == 1


def test_weekly_schedule_can_retry_without_clearing_chat(monkeypatch):
    calendar = CalClient("", ZoneInfo("America/New_York"), event_type_id=1)

    def offline(first, last):
        raise CalendarError("Could not reach Cal.com.")

    monkeypatch.setattr(calendar, "agenda", offline)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state.conversation = Conversation(calendar, messages=[{"role": "user", "content": "Hello"}])
    app.run()
    assert "Could not reach Cal.com" in app.warning[0].value and len(app.chat_input) == 1
    monkeypatch.setattr(calendar, "agenda", lambda first, last: [])
    next(b for b in app.button if b.label == "Retry schedule").click().run()
    assert not app.warning and not app.exception and app.chat_message[0].markdown[0].value == "Hello"

    # Even a verified empty week is different from having no calendar data.
    def agent_offline(state, prompt):
        raise OpenAIError("Unavailable")

    monkeypatch.setattr("scheduling.chat_turn", agent_offline)
    monkeypatch.setattr(calendar, "agenda", offline)
    app.chat_input[0].set_value("What's on tomorrow?").run()
    assert app.error and not app.exception
    app.run()
    assert not app.warning and "No upcoming meetings this week." in [item.value for item in app.caption]


def test_week_stays_visible_through_chat_and_refresh(monkeypatch):
    calendar = CalClient("", ZoneInfo("America/New_York"), event_type_id=1)
    start = datetime.now(calendar.zone) + timedelta(minutes=5)
    bookings = [{"start": utc_stamp(start), "end": utc_stamp(start + timedelta(minutes=30)),
                 "status": "accepted", "attendees": [{"name": "Jordan Park"}]}]
    events = []
    real_text = st.text

    def display_row(row):
        if "Jordan Park" in row:
            events.append("show Jordan")
        return real_text(row)

    def agenda(first, last):
        events.append("read calendar")
        return bookings.copy()

    def chat_turn(state, prompt):
        events.append(prompt)
        bookings.clear()
        state.messages.extend([{"role": "user", "content": prompt}, {"role": "assistant", "content": "Done"}])
        return "Done"

    monkeypatch.setattr(st, "text", display_row)
    monkeypatch.setattr(calendar, "agenda", agenda)
    monkeypatch.setattr("scheduling.chat_turn", chat_turn)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state.conversation = Conversation(calendar)
    app.run()
    events.clear()
    app.chat_input[0].set_value("cancel Jordan").run()
    assert not app.exception
    assert events == ["show Jordan", "cancel Jordan", "read calendar"]
    assert not app.text and app.chat_message[-1].markdown[0].value == "Done"
    assert "No upcoming meetings this week." in [item.value for item in app.caption]


@pytest.mark.parametrize(("mute_host", "mute_attendee"), [(None, None), (False, False), (True, False), (False, True)])
def test_shared_account_blocks_unmuted_recipients(mute_host, mute_attendee):
    host = {"id": 42, "email": "host@example.invalid"}
    event = {"ownerId": 42, "emailSettings": {
        "disableEmailsToHosts": mute_host, "disableEmailsToAttendees": mute_attendee,
    }}
    with pytest.raises(pytest.fail.Exception, match="host|attendee"):
        check_test_event(host, event, host["email"], same_account=True)


def test_shared_account_accepts_muted_event():
    host = {"id": 42, "email": "host@example.invalid"}
    event = {"ownerId": 42, "emailSettings": {
        "disableEmailsToHosts": True, "disableEmailsToAttendees": True,
    }}
    check_test_event(host, event, "attendee@example.invalid", same_account=True)


def test_muted_host_can_use_a_separate_attendee_inbox():
    host = {"id": 42, "email": "host@example.invalid"}
    event = {"ownerId": 42}
    with pytest.raises(pytest.fail.Exception, match="Mute booking emails"):
        check_test_event(host, event, "attendee@example.invalid", same_account=True)
    check_test_event(host, event, "attendee@example.invalid", same_account=True, host_preferences_muted=True)


@pytest.mark.parametrize("inbox", ["first.last@gmail.com", "firstlast+test@googlemail.com"])
def test_muted_host_cannot_invite_its_own_inbox(inbox):
    host = {"id": 42, "email": "first.last@gmail.com"}
    with pytest.raises(pytest.fail.Exception, match="separate attendee inbox"):
        check_test_event(host, {"ownerId": 42}, inbox, same_account=True, host_preferences_muted=True)


def test_live_calcom_contract(live):
    """CT1–CT6 with one persistent control and a create → move → cancel lifecycle."""
    c = live.calendar
    day, slots = live.openings()
    a, b, control_slot = [utc_stamp(instant(s["start"])) for s in slots[:3]]
    closed = datetime.combine(day, time.min, c.zone)
    # A one-second window cannot hold this 30-minute event.
    empty = c.request("GET", "slots", "2024-09-04",
                      params={"eventTypeId": c.event_type_id, "start": utc_stamp(closed),
                              "end": utc_stamp(closed + timedelta(seconds=1)), "format": "range"})
    assert empty["data"] == {}
    assert all(instant(s["end"]) - instant(s["start"]) == timedelta(minutes=30) for s in slots)

    control = c.create_booking(control_slot, {**live.attendee, "name": live.name + " Control"})
    original = c.create_booking(a, live.attendee)
    assert original["status"] == "accepted"
    assert original["attendees"][0]["email"].casefold() == live.attendee["email"].casefold()
    assert original["duration"] == 30
    live.pages.clear()
    items = live.agenda(day, page_size=1, status="upcoming")
    assert len(live.pages) == 2 and live.pages[0]["hasMore"] and live.pages[0]["nextCursor"]
    assert not live.pages[-1]["hasMore"] and live.pages[-1].get("nextCursor") is None
    assert {item["uid"] for item in items} == {original["uid"], control["uid"]}
    booked, = [item for item in items if item["uid"] == original["uid"]]
    assert instant(booked["start"]) == instant(a) and booked["attendees"][0]["name"] == live.name

    with pytest.raises(CalendarError) as conflict:
        c.create_booking(a, {**live.attendee, "name": live.name + " Competitor"})
    assert not conflict.value.uncertain and re.search("availab|already|conflict|occupied", str(conflict.value), re.I)
    assert len(live.agenda(day)) == 2

    start, end = c.day_window(str(day), str(day))
    alternatives = c.slots(start, end, original["uid"])
    assert instant(b) in {instant(s["start"]) for s in alternatives}
    moved = c.reschedule_booking(original["uid"], b)
    items = live.agenda(day)
    assert {item["uid"] for item in items} == {control["uid"], moved["uid"]}
    assert instant(moved["start"]) == instant(b)
    assert moved["duration"] == original["duration"]
    assert moved["attendees"][0]["email"].casefold() == live.attendee["email"].casefold()

    c.cancel_booking(moved["uid"], "Round 1 cancellation check")
    remaining = live.agenda(day)
    assert [(item["uid"], instant(item["start"])) for item in remaining] == [(control["uid"], instant(control_slot))]


def test_evaluate_agenda_time_ranges():
    value = datetime(2026, 11, 23, 9, 0)
    assert time_in_reply("9:00–9:30 AM", value)
    assert not time_in_reply("9:00–9:30 AM", value.replace(minute=30))
    assert not time_in_reply("9:00 AM to 9:30 AM", value.replace(minute=30))
    assert not time_in_reply("09:00–09:30", value.replace(minute=30))
    assert not time_in_reply("09:00–09:30 PM", value)
    assert time_in_reply("11:30–12:00 PM", value.replace(hour=11, minute=30))


def test_b1_book_exact_time(live, ui):
    day, _ = live.openings(minimum=1)
    today = datetime.now(live.calendar.zone).date()
    this_monday = today - timedelta(days=today.weekday())
    ui.session_state.week_offset = (day - this_monday).days // 7
    ui.run()
    with chat_clock_for_tomorrow(day, live.calendar.zone):
        book_exact_tomorrow(live, ui, day)


def book_exact_tomorrow(live, ui, day):
    # After cancellation, a wide search can still disagree with Cal.com's
    # exact-time lookup. Use a start both live responses currently offer.
    candidates = live.calendar.slots(*live.calendar.day_window(str(day), str(day)))
    for slot in candidates:
        first = local_start(live, slot)
        exact = live.calendar.slots(first, instant(slot["end"]))
        if any(local_start(live, option) == first for option in exact):
            break
    else:
        pytest.skip("Cal.com has no confirmed 30-minute opening tomorrow.")
    live.calls.clear()
    live.slot_queries.clear()
    reply = ask(ui, f"Book a 30-minute intro tomorrow at {first:%I:%M %p} with {live.name}.")
    assert "email" in reply.casefold(), (reply, live.slot_queries)
    assert all(method == "GET" for method, _ in live.calls)
    reply = ask(ui, f"The attendee's email is {live.attendee['email']}. Use my time zone.")
    assert "Confirm this booking" in reply and all(method == "GET" for method, _ in live.calls)
    assert not any(live.name in item.value for item in ui.text)
    reply = ask(ui, "Confirm")
    assert [path for method, path in live.calls if method == "POST"] == ["bookings"], (reply, live.slot_queries)
    assert ("GET", "slots") in live.calls
    assert "booked" in reply.casefold() and time_in_reply(reply, first)
    assert any(live.name in item.value and time_in_reply(item.value, first) for item in ui.text)
    live.calls.clear()
    ui.run()
    assert not live.calls, "Redrawing Streamlit repeated a calendar operation"
    created, = live.agenda(day)
    assert instant(created["start"]) == first and created["status"] == "accepted"
    assert created["attendees"][0]["name"] == live.name
    assert created["attendees"][0]["email"].casefold() == live.attendee["email"].casefold()


def test_v1_tomorrows_agenda(live, ui):
    day, slots = live.openings(minimum=3)
    with chat_clock_for_tomorrow(day, live.calendar.zone):
        check_tomorrows_agenda(live, ui, day, slots)


def check_tomorrows_agenda(live, ui, day, slots):
    next_day = str(day + timedelta(days=1))
    next_slots = live.calendar.slots(*live.calendar.day_window(next_day, next_day))
    if not next_slots:
        pytest.skip("V1 needs an available opening the following day.")
    live.seed(slots[1], "Leo")
    live.seed(slots[0], "Maya")
    cancelled = live.seed(slots[2], "CancelledAttendee")
    live.calendar.cancel_booking(cancelled["uid"], "V1 test setup")
    live.seed(next_slots[0], "NextDayAttendee")

    live.calls.clear()
    answer = ask(ui, "What's on my calendar tomorrow? List one meeting per line with its attendee and local time.").casefold()
    assert ("GET", "bookings") in live.calls and all(m == "GET" for m, _ in live.calls)
    assert "maya" in answer and "leo" in answer and answer.index("maya") < answer.index("leo")
    for attendee, slot in zip(("maya", "leo"), slots):
        assert any(attendee in line and time_in_reply(line, local_start(live, slot))
                   for line in answer.splitlines()), answer
    assert "cancelledattendee" not in answer and "nextdayattendee" not in answer


def test_r1_reschedule_later(live, ui):
    day, slots = live.openings()
    original = live.seed(slots[0], "Maya")
    control = live.seed(slots[2], "Leo")
    first = local_start(live, slots[0])
    available = live.calendar.slots(*live.calendar.day_window(str(day), str(day)), original["uid"])
    live.calls.clear()
    live.move_queries.clear()
    reply = ask(ui, f"Move my {first:%I:%M %p} meeting with Maya on {day} to later that day. "
                    "Which start times are available? Use AM or PM.")
    assert (live.calendar.event_type_id, original["uid"]) in live.move_queries, (
        live.calls, reply
    )
    assert all(method == "GET" for method, _ in live.calls)
    offered = [local_start(live, slot) for slot in available
               if local_start(live, slot) > first and time_in_reply(reply, local_start(live, slot))]
    assert offered, "The assistant did not present a live later opening"
    chosen = offered[0]
    live.calls.clear()
    reply = ask(ui, f"Move it to {chosen:%I:%M %p}, please.")
    assert "Confirm" in reply and all(method == "GET" for method, _ in live.calls), reply
    reply = ask(ui, "Confirm")
    assert [p for m, p in live.calls if m == "POST"] == [f"bookings/{original['uid']}/reschedule"]
    assert "rescheduled" in reply.casefold() and time_in_reply(reply, chosen)
    remaining = live.agenda(day)
    assert control["uid"] in {entry["uid"] for entry in remaining}
    moved, = [entry for entry in remaining if entry["uid"] != control["uid"]]
    assert instant(moved["start"]) == chosen and moved["duration"] == original["duration"]
    assert moved["attendees"][0]["name"] == original["attendees"][0]["name"]
    assert moved["attendees"][0]["email"] == original["attendees"][0]["email"]


def test_c1_cancel_one_match(live, ui):
    day, slots = live.openings(minimum=2)
    target = live.seed(slots[0], "Maya")
    control = live.seed(slots[1], "Leo")
    live.calls.clear()
    reply = ask(ui, f"Cancel my intro with Maya on {day} because she is traveling.")
    cancel_path = f"bookings/{target['uid']}/cancel"
    assert [p for m, p in live.calls if m == "POST"] == [cancel_path], reply
    assert "travel" in live.cancel_reasons[cancel_path].casefold()
    assert "cancelled" in reply.casefold()
    remaining, = live.agenda(day)
    assert remaining["uid"] == control["uid"] and remaining["start"] == control["start"]
