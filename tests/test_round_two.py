"""Round 2 conversations use the same chat_turn entry point as Streamlit."""

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from agents.tool_context import ToolContext

import scheduling
from cal_client import instant, utc_stamp
from live_support import ask, live, local_start, time_in_reply, time_position, ui
from scheduling import chat_turn, new_conversation


def writes(run):
    return [path for method, path in run.calls if method == "POST"]


def identity(run):
    return f"Attendee name is {run.name}, email {run.attendee['email']}, time zone {run.calendar.zone.key}."


def say(state, prompt):
    answer = chat_turn(state, prompt)
    return re.sub(r"[\w.+%-]+@[\w.-]+\.[\w-]+", "[address]", answer)


def request_and_confirm(live, state, prompt):
    before = writes(live)
    preview = say(state, prompt)
    assert "Confirm this booking" in preview and state.booking_proposal, preview
    assert writes(live) == before, "A booking was written before confirmation."
    return say(state, "Confirm")


def available(run, day):
    return run.calendar.slots(*run.calendar.day_window(str(day), str(day)))


def at(run, day, hour):
    matches = [s for s in available(run, day) if (local_start(run, s).hour, local_start(run, s).minute) == (hour, 0)]
    assert matches, f"No live opening at {hour} on {day}"
    return matches[0]


def next_weekday(run, weekday):
    today = datetime.now(run.calendar.zone).date()
    first = today + timedelta(days=2)
    return first + timedelta(days=(weekday - first.weekday()) % 7)


def agenda_times(run, day):
    return {row["uid"]: instant(row["start"]) for row in run.agenda(day)}


def test_move_hellen_after_tuesday_conflict(live, record_property):
    today = datetime.now(live.calendar.zone).date()
    next_monday = today + timedelta(days=7 - today.weekday())
    for weeks in range(6):
        monday = next_monday + timedelta(weeks=weeks)
        tuesday, wednesday = monday + timedelta(days=1), monday + timedelta(days=2)
        original = datetime.combine(monday, datetime.min.time(), live.calendar.zone).replace(hour=14, minute=30)
        blocker = original.replace(minute=0) + timedelta(days=1)
        destination = original + timedelta(days=2)
        if (not live.calendar.agenda(str(monday), str(monday + timedelta(days=6)))
                and live.calendar.slots(original, original + timedelta(minutes=30))
                and live.calendar.slots(blocker, blocker + timedelta(hours=1), duration_minutes=60)
                and live.calendar.slots(destination, destination + timedelta(minutes=30))):
            break
    else:
        pytest.fail("No isolated live test week with all three openings.")
    hellen = live.calendar.create_booking(utc_stamp(original), {**live.attendee, "name": live.name + " Hellen"})
    maya = live.calendar.create_booking(utc_stamp(blocker), {**live.attendee, "name": live.name + " Maya Chen"}, duration_minutes=60)
    state = new_conversation()
    live.calls.clear()
    prompt = "what's my schedule next week?" if weeks == 0 else f"what's my schedule the week of Monday {monday}?"
    agenda = say(state, prompt)
    assert "Hellen" in agenda and "Maya Chen" in agenda, agenda
    conflict = say(state, "Please move the 2:30 meeting to tuesday, at the same time")
    assert "overlap" in conflict.lower() and "Maya Chen" in conflict and "Tuesday" in conflict, conflict
    assert time_in_reply(conflict, blocker) and "03:00 PM" in conflict and not writes(live), conflict
    preview = say(state, "then move to wensday")
    assert all(part in preview for part in ("Hellen", "Monday", "Wednesday", "30 minutes", live.calendar.zone.key, "Confirm")), preview
    assert time_in_reply(preview, destination) and not writes(live), preview
    assert agenda_times(live, monday) == {hellen["uid"]: original}
    assert agenda_times(live, tuesday) == {maya["uid"]: blocker} and not live.agenda(wednesday)
    live.calls.clear()
    receipt = chat_turn(state, "Confirm", turn_id="confirm-hellen")
    assert chat_turn(state, "Confirm", turn_id="confirm-hellen") == receipt
    assert "Rescheduled" in receipt and time_in_reply(receipt, destination), receipt
    assert writes(live) == [f"bookings/{hellen['uid']}/reschedule"] and ("GET", "slots") in live.calls
    assert not live.agenda(monday) and agenda_times(live, tuesday) == {maya["uid"]: blocker}
    moved, = live.agenda(wednesday)
    assert instant(moved["start"]) == destination and instant(moved["end"]) == destination + timedelta(minutes=30)
    assert moved["attendees"][0]["name"] == live.name + " Hellen"
    record_property("tested_monday", str(monday))


def test_move_edit_and_slot_taken_before_confirmation(live):
    day, slots = live.openings(minimum=3)
    hellen = live.seed(slots[0], "Hellen")
    state = new_conversation()
    live.calls.clear()
    preview = say(state, f"Move my Hellen meeting on {day} to {when(live, slots[1])}.")
    assert "Confirm" in preview and not writes(live), preview
    revised = say(state, f"Actually move it to {when(live, slots[2])}.")
    assert state.move_draft.original["uid"] == hellen["uid"] and state.move_draft.start == instant(slots[2]["start"])
    assert "Confirm" in revised and not writes(live), revised
    maya = live.seed(slots[2], "Maya Chen")
    live.calls.clear()
    blocked = say(state, "Confirm")
    assert "overlap" in blocked.lower() and "Maya Chen" in blocked and not writes(live), blocked
    assert "no available move" in say(state, "Confirm").lower()
    assert "discarded" in say(state, "No thanks").lower() and not writes(live)
    assert agenda_times(live, day) == {hellen["uid"]: instant(slots[0]["start"]), maya["uid"]: instant(slots[2]["start"])}


def test_unavailable_explanation_distinguishes_an_exact_overlap_from_a_search_window():
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    start = datetime(2026, 11, 24, 14, 30, tzinfo=zone)
    end = start + timedelta(minutes=30)
    maya = {"attendees": [{"name": "Maya Chen"}], "start": (start - timedelta(minutes=30)).isoformat(), "end": end.isoformat()}
    assert "overlaps the meeting with Maya Chen" in scheduling.unavailable_reason(state, start, end, 30, conflicts=[maya])
    broad = scheduling.unavailable_reason(state, start, end + timedelta(hours=2), 30, conflicts=[maya])
    assert "during that window" in broad and "Maya Chen" in broad and "remaining time" in broad
    blocked_window = scheduling.unavailable_reason(state, start - timedelta(minutes=15), end, 30, conflicts=[maya])
    assert "no uninterrupted 30-minute opening" in blocked_window
    assert "can’t verify the cause" in scheduling.unavailable_reason(state, start, end, 30, conflicts=[])


def test_move_keeps_original_duration_until_user_changes_it():
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    start = datetime(2026, 11, 23, 14, 30, tzinfo=zone)
    original = {"uid": "hellen", "start": utc_stamp(start), "end": utc_stamp(start + timedelta(hours=1))}
    state.calendar_selection.duration_minutes = 30
    state.current_request = "Please move the 2:30 meeting to Tuesday, at the same time"
    assert scheduling.move_length(state, original) == 60
    state.current_request = "Move it 30 minutes later"
    assert scheduling.move_length(state, original) == 60
    state.current_request = "Move it to Wednesday and make it 30 minutes"
    assert scheduling.move_length(state, original) == 30
    state.move_draft = scheduling.MoveDraft(original, start + timedelta(days=2), 30)
    state.current_request = "then move to Thursday"
    assert scheduling.move_length(state, original) == 30


def test_move_preview_survives_read_outage_and_can_be_discarded(monkeypatch):
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    start = datetime(2026, 11, 23, 14, 30, tzinfo=zone)
    original = {"uid": "hellen", "start": utc_stamp(start), "end": utc_stamp(start + timedelta(minutes=30))}
    state.move_draft = scheduling.MoveDraft(original, start + timedelta(days=2), 30, awaiting_confirmation=True)
    calls = []

    def temporary_outage(method, path, version, **kwargs):
        calls.append((method, path))
        raise scheduling.CalendarError("Could not reach Cal.com.")

    monkeypatch.setattr(state.calendar, "request", temporary_outage)
    for _ in range(2):
        assert "Could not reach Cal.com" in chat_turn(state, "Confirm")
        assert state.move_draft.awaiting_confirmation
    assert "Move discarded" in chat_turn(state, "Cancel that move because Wednesday no longer works.")
    assert state.move_draft is None and state.requested_cancellation_reason is None
    assert calls == [("GET", "bookings"), ("GET", "bookings")]


def test_conflict_then_same_as_mine_for_attendee_zone(live):
    day = next_weekday(live, 0)
    for _ in range(5):
        ten = datetime.combine(day, datetime.min.time(), live.calendar.zone).replace(hour=10)
        two = ten.replace(hour=14)
        if (live.calendar.slots(ten, ten + timedelta(hours=1), duration_minutes=60)
                and live.calendar.slots(two, two + timedelta(minutes=30))):
            break
        day += timedelta(weeks=1)
    else:
        pytest.skip("No Monday with the two live openings needed for the conflict scenario.")
    blocker = live.calendar.create_booking(utc_stamp(two), {**live.attendee, "name": live.name + " Maya"})
    live.calls.clear()
    state = new_conversation()
    name = live.name + " Hellen"
    email = live.attendee["email"]
    reply = say(state, f"Book a 60-minute meeting with {name} on Monday {day}. Her email is {email}. "
                       "Show me any openings that fit between 1:30 PM and 2:30 PM.")
    assert not state.slots and not writes(live), reply
    reply = say(state, "what about 2pm?")
    assert "unavailable" in reply.lower() and not state.draft.selected_start, reply
    reply = say(state, "why it is unavailable?")
    assert blocker["uid"] in state.bookings and time_in_reply(reply, two) and not writes(live), reply
    reply = say(state, "ok, them do it at 10am")
    assert state.booking_proposal and state.booking_proposal.attendee["timeZone"] == live.calendar.zone.key and not writes(live), reply
    reply = say(state, "Confirm")
    assert writes(live) == ["bookings"] and time_in_reply(reply, ten), reply
    booked, = [b for b in live.owned_bookings(day) if b["uid"] != blocker["uid"]]
    assert instant(booked["start"]) == ten and instant(booked["end"]) == ten + timedelta(hours=1)
    attendee, = booked["attendees"]
    assert (attendee["name"], attendee["email"], attendee["timeZone"]) == (name, email, live.calendar.zone.key)


@pytest.mark.parametrize("combined_reply", [False, True], ids=["separate-turns", "same-turn"])
def test_accept_only_offered_slot_then_supply_contact(live, combined_reply):
    day = next_weekday(live, 0)
    for _ in range(5):
        start = datetime.combine(day, datetime.min.time(), live.calendar.zone).replace(hour=14)
        end = start + timedelta(hours=1)
        if live.calendar.slots(start, end, duration_minutes=60):
            break
        day += timedelta(weeks=1)
    else:
        pytest.skip("No Monday with a live 2–3 PM opening for this conversation.")

    state = new_conversation()
    name = f"{live.name} Maya Chen"
    email = live.attendee["email"]
    reply = say(state, f"I want to book a 60-minute interview with {name}. "
                       f"Show me openings that fit between 2 PM and 3 PM on Monday {day}.")
    assert state.offered_starts == [utc_stamp(start)] and time_in_reply(reply, start), reply
    assert state.draft.pending_question == "slot" and not state.draft.selected_start and not writes(live), reply
    contact = f"[{email}](mailto:{email}), she is in the same time zone"
    if not combined_reply:
        reply = say(state, "yes")
        assert state.draft.selected_start == utc_stamp(start) and state.draft.duration_minutes == 60, reply
        assert "email" in reply.lower() and not writes(live), reply
    reply = request_and_confirm(live, state, f"Yes, {contact}" if combined_reply else contact)
    assert writes(live) == ["bookings"] and time_in_reply(reply, start), reply
    booking, = live.owned_bookings(day)
    assert (instant(booking["start"]), instant(booking["end"])) == (start, end)
    attendee, = booking["attendees"]
    assert (attendee["name"], attendee["email"], attendee["timeZone"]) == (name, email, live.calendar.zone.key)


@pytest.mark.parametrize("contact_first", [True, False], ids=["time-followup", "contact-followup-pacific"])
def test_start_time_answer_with_shared_pm_range(live, contact_first):
    today = datetime.now(live.calendar.zone).date()
    for offset in range(1, 15):
        day = today + timedelta(days=offset)
        start = datetime.combine(day, datetime.min.time(), live.calendar.zone).replace(hour=13)
        if live.calendar.slots(start, start + timedelta(minutes=30)):
            break
    else:
        pytest.skip("No live opening at 1 PM for the time-range replay.")
    date_text = ("tomorrow, " if offset == 1 else "") + day.strftime("%B %d")
    local_range = "1:00–1:30 PM" if contact_first else "10:00–10:30 AM Pacific time"
    selection = f"{local_range} {date_text}"
    state = new_conversation()
    live.calls.clear()
    if contact_first:
        first_turn = f"Book a meeting with {live.name}. Her email is {live.attendee['email']}. Use my time zone."
        last_turn = selection
    else:
        first_turn = f"Book a meeting with {live.name} from {selection}."
        last_turn = f"Her email is {live.attendee['email']}. Use my time zone."
    reply = say(state, first_turn)
    assert not writes(live) and "?" in reply, reply
    if not contact_first:
        assert ("GET", "slots") in live.calls and state.draft.selected_start == utc_stamp(start), reply
    reply = request_and_confirm(live, state, last_turn)
    assert writes(live) == ["bookings"] and "Booked" in reply and time_in_reply(reply, start), reply
    booked, = live.owned_bookings(day)
    assert instant(booked["start"]) == start and instant(booked["end"]) == start + timedelta(minutes=30)
    assert state.messages[-4]["content"] == last_turn

    live.calls.clear()
    another = new_conversation()
    say(another, f"Book a meeting with {live.name} Two. Her email is {live.attendee['email']}. Use my time zone.")
    reply = say(another, selection)
    assert "unavailable" in reply.lower() and time_in_reply(reply, start) and not writes(live), reply


@pytest.mark.parametrize("wording, hour, minute, length", [
    ("1:00–1:30 PM tomorrow, {date}", 13, 0, 30),
    ("1:00–1:30 tomorrow afternoon", 13, 0, 30),
    ("quarter past one tomorrow afternoon", 13, 15, 30),
    ("11:30–12 PM tomorrow", 11, 30, 30),
    ("1 pm to 2 tomorrow", 13, 0, 60),
    ("13:00-13:30 on {iso}", 13, 0, 30),
])
def test_natural_meeting_time(live, wording, hour, minute, length):
    tomorrow = datetime.now(live.calendar.zone).date() + timedelta(days=1)
    target = datetime.combine(tomorrow, datetime.min.time(), live.calendar.zone).replace(hour=hour, minute=minute)
    state = new_conversation()
    say(state, f"Book a meeting with {live.name}, email {live.attendee['email']}.")
    live.calls.clear()
    def check_exact(text, expected):
        before = len(live.slot_queries)
        reply = say(state, text)
        end = expected + timedelta(minutes=length)
        assert len(live.slot_queries) > before and state.slot_window == (expected, end), reply
        assert not writes(live) and time_in_reply(reply, expected), reply
        if state.booking_proposal:
            assert (state.booking_proposal.start, state.booking_proposal.minutes) == (expected, length)
            assert "Confirm" in reply and live.calendar.zone.key in reply
        else:
            assert "unavailable" in reply.lower() and not state.draft.selected_start, reply
        assert state.messages[-2]["content"] == text
    check_exact(wording.format(date=tomorrow.strftime("%B %d"), iso=tomorrow), target)
    if minute == 15:
        check_exact("actually, make it half past instead", target.replace(minute=30))


def test_flexible_lunchtime_is_a_search_window(live):
    day, _ = live.openings()
    state = new_conversation()
    say(state, f"Book a 30-minute meeting with {live.name} on {day}, email {live.attendee['email']}.")
    live.calls.clear()
    response = say(state, "sometime after lunch")
    first, last = state.slot_window
    assert first.date() == day and first.hour >= 12 and last - first > timedelta(minutes=30), response
    assert ("GET", "slots") in live.calls and not writes(live)
    assert state.offered_starts and not state.draft.selected_start and state.booking_proposal is None, response
    assert all(first <= instant(start) < last for start in state.offered_starts)


def test_explicit_interval_and_conflicting_duration(live):
    state = new_conversation()
    response = say(state, "Book a 30-minute meeting with Maya from 1:00–2:00 PM tomorrow.")
    assert re.search(r"\b(?:30|half[ -](?:an? )?hour)\b", response, re.I), response
    assert re.search(r"\b(?:60|(?:one|an?|1)[ -]hour)\b", response, re.I) and "?" in response, response
    assert not state.booking_proposal and not writes(live)


def test_structured_slot_bounds_preserve_instants_and_reject_nonexistent_times():
    eastern, pacific = ZoneInfo("America/New_York"), ZoneInfo("America/Los_Angeles")
    start, end = scheduling.slot_bounds("2026-11-23T10:00", "2026-11-23T10:30", pacific, eastern)
    assert start == datetime(2026, 11, 23, 13, tzinfo=eastern) and end - start == timedelta(minutes=30)
    with pytest.raises(ValueError, match="does not exist"):
        scheduling.slot_bounds("2027-03-14T02:00", "2027-03-14T03:30", eastern, eastern)
    with pytest.raises(ValueError, match="offset does not match"):
        scheduling.slot_bounds("2027-03-14T02:00-05:00", "2027-03-14T03:30-04:00", eastern, eastern)
    with pytest.raises(ValueError, match="occurs twice"):
        scheduling.slot_bounds("2026-11-01T01:10", "2026-11-01T01:40", eastern, eastern)
    late, _ = scheduling.slot_bounds("2026-11-01T01:10-05:00", "2026-11-01T01:40-05:00", eastern, eastern)
    assert late.fold == 1
    with pytest.raises(ValueError, match="follow its start"):
        scheduling.slot_bounds("2026-11-23T13:30", "2026-11-23T13:00", eastern, eastern)


def test_confirm_booking_details_with_default_zone_edit_and_decline(live, ui):
    day, slots = live.openings(minimum=2)
    start, later = (local_start(live, slot) for slot in slots[:2])
    email = live.attendee["email"]
    live.calls.clear()
    ask(ui, f"Book a 30-minute interview with {live.name}, email {email}, on {day} at {start:%I:%M %p}.")
    state = ui.session_state["conversation"]
    preview = state.messages[-1]["content"]
    assert all(value in preview for value in (live.name, email, start.strftime("%I:%M %p"),
               (start + timedelta(minutes=30)).strftime("%I:%M %p"), live.calendar.zone.key, "Confirm this booking"))
    assert state.booking_proposal.attendee["timeZone"] == live.calendar.zone.key and not writes(live)
    preview = ask(ui, "Actually her time zone is Europe/London.")
    assert "Europe/London" in preview and time_in_reply(preview, start.astimezone(ZoneInfo("Europe/London")))
    assert not writes(live)
    preview = ask(ui, f"Make it {later:%I:%M %p} instead.")
    assert "Confirm this booking" in preview and time_in_reply(preview, later) and not writes(live), preview
    receipt = ask(ui, "Confirm")
    assert "Booked" in receipt and writes(live) == ["bookings"], receipt
    booked, = live.owned_bookings(day)
    assert instant(booked["start"]) == later and booked["attendees"][0]["timeZone"] == "Europe/London"
    live.calls.clear()
    preview = ask(ui, f"Book another meeting with {live.name}, email {email}, on {day} at {start:%I:%M %p}.")
    assert "Confirm this booking" in preview
    assert "discarded" in ask(ui, "No thanks").lower() and not writes(live)
    assert [b["uid"] for b in live.owned_bookings(day)] == [booked["uid"]]


@pytest.mark.parametrize("lookup_failure", ["outage", "missing-pagination"])
def test_preview_corrections_apply_before_consent_and_polite_cancellation_discards_it(monkeypatch, lookup_failure):
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))

    def failed_lookup(method, path, version, *, params):
        assert (method, path) == ("GET", "bookings")
        if lookup_failure == "outage":
            raise scheduling.CalendarError("Unavailable")
        return {"status": "success", "data": []}

    monkeypatch.setattr(state.calendar, "request", failed_lookup)
    start = datetime(2026, 11, 23, 13, tzinfo=state.calendar.zone)
    attendee = {"name": "Maya", "email": "old@example.invalid", "timeZone": "Europe/London"}
    preview = scheduling.propose_booking(state, start, 30, attendee, {"notes": "Private interview topic"})
    assert "couldn’t check" in preview and "Confirm this booking" in preview and state.booking_proposal
    reply = chat_turn(state, "Change the email to new@example.invalid and remove the notes.")
    assert "new@example.invalid" in reply and "Private interview topic" not in reply
    assert state.booking_proposal.fields == {}
    reply = chat_turn(state, "Use my time zone.")
    assert "America/New_York" in reply and "Europe/London" not in reply
    assert state.booking_proposal.attendee["timeZone"] == state.calendar.zone.key
    assert "Europe/London" in chat_turn(state, "She’s in London.")
    assert "discarded" in chat_turn(state, "Please cancel this booking.").lower()
    assert state.booking_proposal is None and state.draft is None


def test_mixed_time_and_email_correction_then_check_again(live):
    day, _ = live.openings()
    available = {local_start(live, s): s for s in live.calendar.slots(*live.calendar.day_window(str(day), str(day)))}
    first = next(start for start in available if start.minute == 0 and start + timedelta(minutes=30) in available)
    later = first + timedelta(minutes=30)
    state = new_conversation()
    # Two addresses in the same private Mailosaur inbox. All assistant turns are previews or reads.
    email = live.attendee["email"]
    local, domain = email.split("@")
    say(state, f"Book a 30-minute meeting with {live.name} on {day} at {first:%I:%M %p}, email {local}+first@{domain}.")
    assert state.booking_proposal and state.booking_proposal.start == first and not writes(live)
    reply = say(state, f"Actually, make it half past instead, and use {email}.")
    assert state.booking_proposal and state.booking_proposal.start == later, reply
    assert state.booking_proposal.attendee["email"] == email and not writes(live), reply
    say(state, f"Is {later:%I:%M %p} on {day} available?")
    assert state.offered_starts == [utc_stamp(later)]
    live.seed(available[later], "Maya Chen")
    live.calls.clear()
    reply = say(state, "Is that time still available?")
    assert ("GET", "slots") in live.calls and not writes(live), reply
    assert "unavailable" in reply.lower() and "Maya Chen" in reply, reply


def test_interview_time_and_contact_followups(live):
    today = datetime.now(live.calendar.zone).date()
    day = today + timedelta(days=1)
    day += timedelta(days=(7 - day.weekday()) % 7)
    for _ in range(5):
        one = datetime.combine(day, datetime.min.time(), live.calendar.zone).replace(hour=13)
        two = one + timedelta(hours=1)
        half_past = two + timedelta(minutes=30)
        if (all(live.calendar.slots(t, t + timedelta(hours=1), duration_minutes=60) for t in (one, two))
                and live.calendar.slots(half_past, half_past + timedelta(minutes=30))):
            break
        day += timedelta(weeks=1)
    else:
        pytest.skip("No Monday with the exact live openings required for this conversation.")
    # Only the second half of the requested 2 PM hour conflicts.
    control = live.calendar.create_booking(utc_stamp(half_past), {**live.attendee, "name": live.name + " Control"})

    live.calls.clear()
    state = new_conversation()
    when_day = "tomorrow" if day == today + timedelta(days=1) else f"on Monday {day}"
    say(state, "What's my current schedule?")
    reply = say(state, f"Book a 45 mins interview with a candidate {when_day}.")
    assert isinstance(state.draft.pending_question, scheduling.DurationChoice) and state.draft.pending_question.requested_minutes == 45, reply
    say(state, "60 minutes")
    name = live.name + " Maya Chen"
    email = live.attendee["email"]
    say(state, f"{name} with [{email}](mailto:{email})")
    for request in ("2pm", "2pm Monday", "is 2pm, Monday not availabel?"):
        reply = say(state, request)
        assert time_in_reply(reply, two) and "unavailable" in reply.lower() and "60 minutes" in reply, reply
        assert not state.draft.selected_start and not writes(live)
    for request in ("is 1pm available to book", "ok, then 1pm?"):
        reply = say(state, request)
        assert time_in_reply(reply, one) and state.draft.selected_start == utc_stamp(one) and not writes(live), reply
    reply = request_and_confirm(live, state, "Book the 1 PM opening.")
    assert writes(live) == ["bookings"] and time_in_reply(reply, one), reply
    booking, = [entry for entry in live.owned_bookings(day) if entry["uid"] != control["uid"]]
    assert instant(booking["start"]) == one and instant(booking["end"]) == two
    assert booking["attendees"][0]["name"] == name and booking["attendees"][0]["email"] == email
    assert booking["attendees"][0]["timeZone"] == live.calendar.zone.key
    for entry in (control, booking):
        fields = entry.get("bookingFieldsResponses") or {}
        assert not any(fields.get(name) for name in ("notes", "purpose", "company"))
        assert not entry.get("description")

    live.calls.clear()
    agenda = say(state, f"What's on my calendar on {day}? List one meeting per line.").lower()
    assert ("GET", "bookings") in live.calls
    maya_rows = [line for line in agenda.splitlines() if "maya chen" in line]
    assert len(maya_rows) == 1 and time_in_reply(maya_rows[0], one), agenda
    hour = r"\b(?:60[- ]?min|(?:1|one|an)[- ](?:hour|hr))"
    assert re.search(hour, maya_rows[0]), agenda
    assert not re.search(r"\b(?:30|45)[- ]?min|calendar title|title says", maya_rows[0]), agenda
    explanation = say(state, "What length did I originally ask for with Maya, and what did I choose?").lower()
    assert re.search(r"\b45[- ]?min", explanation) and re.search(hour, explanation), explanation
    assert not writes(live)


def test_cancel_then_rebook_with_typed_duration(live, ui):
    monday = next_weekday(live, 0)
    for _ in range(4):
        tuesday = monday + timedelta(days=1)
        target = datetime.combine(tuesday, datetime.min.time(), live.calendar.zone).replace(hour=14)
        if (not live.agenda(monday) and live.calendar.slots(target, target + timedelta(hours=1), duration_minutes=60)
                and any(local_start(live, s).hour == 14 for s in available(live, monday))):
            break
        monday += timedelta(weeks=1)
    else:
        pytest.skip("No isolated Monday and Tuesday pair with a 2 PM opening.")
    original = live.seed(at(live, monday, 14), "Maya")
    live.calls.clear()
    assert "Maya" in ask(ui, f"What's on my calendar Monday {monday}?")
    answer = ask(ui, "I want to cancel the meeting because my plans changed.")
    assert writes(live) == [f"bookings/{original['uid']}/cancel"], answer
    assert original["uid"] in {b["uid"] for b in live.agenda(monday, status="cancelled")}
    answer = ask(ui, "Change that to a 40-minute meeting on Tuesday at the same time as Monday.")
    assert "40" in answer and "30" in answer and "60" in answer, answer
    assert not ui.get("dialog")
    ask(ui, "60 minutes")
    proposal = ui.session_state["conversation"].booking_proposal
    assert proposal and proposal.minutes == 60 and proposal.start == target, ui.markdown[-1].value
    assert writes(live) == [f"bookings/{original['uid']}/cancel"]
    answer = ask(ui, "Yes")
    assert "60 minutes" in answer and time_in_reply(answer, target), answer
    assert writes(live) == [f"bookings/{original['uid']}/cancel", "bookings"]
    replacement, = live.owned_bookings(tuesday)
    assert replacement["uid"] != original["uid"] and instant(replacement["start"]) == target
    assert instant(replacement["end"]) - instant(replacement["start"]) == timedelta(hours=1)
    for key in ("name", "email", "timeZone"):
        assert replacement["attendees"][0][key] == original["attendees"][0][key]
    assert not live.owned_bookings(monday)
    morning = next(local_start(live, s) for s in available(live, tuesday) if local_start(live, s) < target)
    answer = ask(ui, f"Move that meeting to {tuesday} at {morning:%I:%M %p} and make it 30 minutes.")
    assert "Confirm" in answer and not any(p.endswith("/reschedule") for p in writes(live)), answer
    answer = ask(ui, "Confirm")
    assert writes(live)[-1] == f"bookings/{replacement['uid']}/reschedule", answer
    moved, = live.owned_bookings(tuesday)
    assert moved["duration"] == 30 and instant(moved["start"]) == morning


@pytest.mark.parametrize("live", ["lengths"], indirect=True)
def test_duration_choice_uses_event_settings(live, monkeypatch):
    day, _ = live.openings()
    slots = live.calendar.slots(*live.calendar.day_window(str(day), str(day)), duration_minutes=45)
    first = local_start(live, slots[0])
    state = new_conversation()
    live.calls.clear()
    reply = say(state, f"Book a 90-minute meeting on {day} at {first:%I:%M %p} "
                f"to discuss pilot pricing. {identity(live)}")
    assert isinstance(state.selection.pending_question, scheduling.DurationChoice) and state.selection.pending_question.options == [30, 45], reply
    assert not writes(live)
    # Exercise an agent omitting the chosen duration after a valid chat reply.
    def omit_duration(tool):
        async def invoke(ctx, arguments):
            return await tool.on_invoke_tool(ctx, json.dumps({**json.loads(arguments), "duration_minutes": None}))
        return replace(tool, on_invoke_tool=invoke)

    with monkeypatch.context() as patch:
        for name in ("find_slots", "create_booking"):
            patch.setattr(scheduling, name, omit_duration(getattr(scheduling, name)))
        reply = request_and_confirm(live, state, "45 minutes")
    assert writes(live) == ["bookings"], reply
    booking, = live.owned_bookings(day)
    assert instant(booking["start"]) == first and booking["duration"] == 45
    assert "pilot pricing" in booking["bookingFieldsResponses"]["purpose"].lower()

    # Change only the temporary event. The existing 45-minute appointment still exists.
    live.calendar.request("PATCH", f"event-types/{live.calendar.event_type_id}", "2026-06-12",
                          body={"lengthInMinutesOptions": [30, 60]})
    reply = say(state, "Cancel the 45-minute meeting because the interview length changed.")
    assert writes(live)[-1] == f"bookings/{booking['uid']}/cancel", reply
    assert state.selection.pending_question is None
    # Simulate a session that predates the structured cancellation receipt.
    state.last_cancelled = None
    later = next(local_start(live, s) for s in available(live, day)
                 if local_start(live, s) > first and
                 live.calendar.slots(instant(s["start"]), instant(s["end"])))
    reply = say(state, f"Rebook the meeting I just cancelled on {day} at {later:%I:%M %p} for 30 minutes.")
    assert state.booking_proposal and state.booking_proposal.minutes == 30, reply
    reply = say(state, "OK")
    recreated, = live.owned_bookings(day)
    assert recreated["uid"] != booking["uid"] and instant(recreated["start"]) == later, reply
    assert recreated["bookingFieldsResponses"]["purpose"] == booking["bookingFieldsResponses"]["purpose"]


def displayed(run, reply, slots):
    options = [(time_position(reply, local_start(run, s)), s) for s in slots]
    mentioned = [(p, s) for p, s in options if p is not None]
    return [s for _, s in sorted(mentioned, key=lambda option: option[0])]


def when(run, slot):
    return local_start(run, slot).strftime("%Y-%m-%d at %I:%M %p")


def test_c2_disambiguate(live):
    thu = next_weekday(live, 3)
    fri = thu + timedelta(days=1)
    other = live.seed(at(live, thu, 10), "Maya")
    target = live.seed(at(live, fri, 10), "Maya")
    state = new_conversation()
    live.calls.clear()
    answer = say(state, "Cancel my intro with Maya.")
    assert not writes(live) and "?" in answer and "fri" in answer.lower(), answer
    assert other["uid"] in agenda_times(live, thu) and target["uid"] in agenda_times(live, fri)
    answer = say(state, "The Friday one.")
    path = f"bookings/{target['uid']}/cancel"
    assert writes(live) == [path], answer
    if state.pending_cancellation:
        assert state.pending_cancellation["uid"] == target["uid"] and "reason" in answer.lower()
        say(state, "We already met on Thursday.")
        assert writes(live) == [path, path] and live.cancel_reasons[path] == "We already met on Thursday."
    assert other["uid"] in agenda_times(live, thu) and target["uid"] not in agenda_times(live, fri)


@pytest.mark.parametrize(("reason", "reason_first"), [
    ("I have already booked the meeting", False),
    ("I have already booked the meeting on Monday, so I do not need to book it again on Tuesday", False),
    ("we already have a Monday meeting", True),
], ids=["plain-explanation", "mentions-both-days", "reason-before-selection"])
def test_cancel_by_duration_then_give_reason(live, reason, reason_first):
    monday = next_weekday(live, 0)
    for _ in range(5):
        tuesday = monday + timedelta(days=1)
        first = datetime.combine(monday, datetime.min.time(), live.calendar.zone).replace(hour=14)
        second = first + timedelta(days=1)
        if (not live.agenda(monday) and not live.agenda(tuesday)
                and live.calendar.slots(first, first + timedelta(minutes=30))
                and live.calendar.slots(second, second + timedelta(hours=1), duration_minutes=60)):
            break
        monday += timedelta(weeks=1)
    else:
        pytest.skip("No empty Monday and Tuesday with both live test openings.")
    attendee = {**live.attendee, "name": live.name + " Maya Chen"}
    control = live.calendar.create_booking(utc_stamp(first), attendee)
    target = live.calendar.create_booking(utc_stamp(second), attendee, duration_minutes=60)
    path = f"bookings/{target['uid']}/cancel"
    state = new_conversation()
    live.calls.clear()
    say(state, f"What's on my calendar from Monday {monday} through Tuesday {tuesday}?")
    assert set(state.bookings) == {target["uid"], control["uid"]}
    if reason_first:
        answer = say(state, f"Cancel my meeting with {attendee['name']} because {reason}")
        assert "?" in answer and not writes(live), answer
        final_turn = "The 60-minute one."
        expected_calls = [path]
    else:
        answer = say(state, "cancel 60 minutes")
        assert "reason" in answer.lower() and "Tuesday" in answer and "60-minute" in answer, answer
        assert state.pending_cancellation["uid"] == target["uid"] and writes(live) == [path]
        assert agenda_times(live, tuesday) == {target["uid"]: second}
        if "Monday" in reason:
            answer = say(state, "yes")
            assert "reason" in answer.lower() and writes(live) == [path], answer
        final_turn = reason
        expected_calls = [path, path]
    answer = chat_turn(state, final_turn, turn_id="finish")
    assert "Cancelled" in answer and time_in_reply(answer, second), answer
    assert chat_turn(state, final_turn, turn_id="finish") == answer
    assert writes(live) == expected_calls and live.cancel_reasons[path] == reason
    assert state.pending_cancellation is None and state.last_cancelled["uid"] == target["uid"]
    assert agenda_times(live, monday) == {control["uid"]: first} and not live.agenda(tuesday)


def test_r4_followup_uid(live, record_property):
    day, slots = live.openings()
    original = live.seed(slots[0], "Maya")
    control = live.seed(slots[2], "Leo")
    state = new_conversation()
    live.calls.clear()
    answer = say(state, f"Move my Maya meeting on {day} to {when(live, slots[1])}.")
    assert "Confirm" in answer and not writes(live), answer
    answer = say(state, "Confirm")
    assert writes(live) == [f"bookings/{original['uid']}/reschedule"], answer
    moved, = [row for row in live.agenda(day) if row["uid"] != control["uid"]]
    assert instant(moved["start"]) == instant(slots[1]["start"])
    record_property("replacement_uid", moved["uid"] != original["uid"])
    live.calls.clear()
    say(state, "Actually, cancel it because we already met.")
    assert writes(live) == [f"bookings/{moved['uid']}/cancel"]
    assert agenda_times(live, day) == {control["uid"]: instant(control["start"])}


def test_v2_midnight(live, monkeypatch):
    # Nepal's quarter-hour offset places a normal New York opening across midnight.
    zone = ZoneInfo("Asia/Kathmandu")
    monkeypatch.setenv("FOUNDER_TIME_ZONE", zone.key)
    day, _ = live.openings()
    slots = available(live, day)
    crossing, = [s for s in slots if instant(s["start"]).astimezone(zone).date()
                 < instant(s["end"]).astimezone(zone).date() and instant(s["end"]).astimezone(zone).minute]
    later = next(s for s in slots if instant(s["start"]) >= instant(crossing["end"]))
    first = live.seed(crossing, "MidnightMaya")
    second = live.seed(later, "NextDayLeo")
    local = instant(crossing["start"]).astimezone(zone)
    state = new_conversation()
    assert first["uid"] in {b["uid"] for b in state.calendar.agenda(str(local.date()), str(local.date()))}
    live.calls.clear()
    answer = say(state, f"List my meetings on {local.date()} with attendee names and local times.")
    assert "midnightmaya" in answer.lower() and "nextdayleo" not in answer.lower() and time_in_reply(answer, local), answer
    assert ("GET", "bookings") in live.calls and not writes(live)
    assert second["uid"] in agenda_times(live, day)


def test_b2_second_displayed(live):
    day, _ = live.openings()
    afternoon = [s for s in available(live, day) if local_start(live, s).hour >= 12]
    state = new_conversation()
    live.calls.clear()
    answer = say(state, f"Show me three afternoon openings on {day} in chronological order.")
    choices = displayed(live, answer, afternoon)
    assert len(choices) >= 2 and not writes(live), answer
    answer = request_and_confirm(live, state, "Book the second one. " + identity(live))
    assert writes(live) == ["bookings"], answer
    row, = live.agenda(day)
    assert instant(row["start"]) == instant(choices[1]["start"])


def test_b3_corrected_address(live):
    day, slots = live.openings()
    state = new_conversation()
    live.calls.clear()
    answer = say(state, f"Book a meeting on {when(live, slots[0])}.")
    assert "email" in answer.lower() and not writes(live), answer
    domain = live.attendee["email"].split("@")[1]
    assert domain.endswith(".mailosaur.net"), "B3 requires two controlled Mailosaur addresses"
    answer = request_and_confirm(live, state, f"Invite {live.name} at initial@{domain}. Correction, use {live.attendee['email']} instead. Use my time zone.")
    assert writes(live) == ["bookings"], answer
    row, = live.agenda(day)
    assert row["attendees"][0]["email"] == live.attendee["email"] and instant(row["start"]) == instant(slots[0]["start"])


def test_changed_attendee_and_time_during_booking(live):
    day, slots = live.openings(minimum=2)
    domain = live.attendee["email"].split("@")[1]
    assert domain.endswith(".mailosaur.net")
    state = new_conversation()
    say(state, f"I want to book a meeting with Maya, email initial@{domain}. Please show openings on {day}.")
    assert not writes(live)
    reply = say(state, f"Actually, book a 30-minute intro for {live.name} on {when(live, slots[0])}.")
    assert "email" in reply.lower() and not writes(live), reply
    draft = state.draft
    selected = draft.selected_start
    live.calls.clear()
    reply = say(state, f"Can you check my calendar for {day + timedelta(days=1)}?")
    assert ("GET", "bookings") in live.calls and not writes(live), reply
    assert state.draft is draft and draft.selected_start == selected and draft.attendee_name == live.name
    live.calls.clear()
    reply = say(state, f"Her email is {live.attendee['email']}, but show me other openings on {day} first.")
    assert ("GET", "slots") in live.calls and not writes(live) and state.booking_proposal is None, reply
    new_time = local_start(live, slots[1])
    reply = request_and_confirm(live, state, f"The email is {live.attendee['email']}. Make it {new_time:%I:%M %p} instead. Use my time zone.")
    assert writes(live) == ["bookings"] and time_in_reply(reply, new_time), reply
    row, = live.agenda(day)
    assert instant(row["start"]) == new_time
    assert row["attendees"][0]["name"] == live.name and row["attendees"][0]["email"] == live.attendee["email"]


def test_booking_address_follows_corrections_and_attendee_changes():
    state = scheduling.Conversation(calendar=None)
    state.current_request = "Invite Maya at first@example.invalid. Correction, use final@example.invalid instead."
    assert scheduling.booking_addresses(state) == {"final@example.invalid"}
    state.messages = [{"role": "user", "content": state.current_request},
                      {"role": "assistant", "content": "Is guess@example.invalid correct?"}]
    state.current_request = "Book the second opening."
    assert scheduling.booking_addresses(state) == {"final@example.invalid"}
    state.current_request = "Book tomorrow at noon with Leo."
    assert not scheduling.booking_addresses(state)
    state.booking_history_start = len(state.messages)
    state.current_request = "Book tomorrow at noon."
    assert not scheduling.booking_addresses(state)
    state.current_request = "Book tomorrow at noon with the same attendee."
    assert scheduling.booking_addresses(state) == {"final@example.invalid"}


def test_email_followup_preserves_the_named_attendee_and_explicit_fields():
    request = "Book an intro tomorrow at 9 am with Maya Chen.\nHer email is maya@example.invalid."
    assert scheduling.booking_attendee_name(request, "Maya") == "Maya Chen"
    assert scheduling.supplied_meeting_details(request, "intro", "Maya Chen") == {}
    assert scheduling.supplied_meeting_details("Book a 45 mins interview with a candidate tomorrow",
                                              "interview with a candidate", None) == {}
    no_notes = "Book an interview with Maya. How about Monday? Leave notes blank."
    assert scheduling.supplied_meeting_details(no_notes, "Interview test setup", None) == {}
    assert scheduling.supplied_meeting_details(no_notes, "interview", None) == {}
    clear = "Book Maya from Acme to discuss pilot pricing. Please leave the notes blank."
    assert scheduling.supplied_meeting_details(clear, "pilot pricing", "Acme") == {}
    assert scheduling.supplied_meeting_details(clear + " Actually add a note about pilot pricing.",
                                              "pilot pricing", None) == {"purpose": "pilot pricing"}
    correction = request.split("\n")[0] + "\nCorrection, invite Leo instead at leo@example.invalid."
    assert scheduling.booking_attendee_name(correction, "Leo") == "Leo"
    assert scheduling.supplied_meeting_details("Maya from Acme to discuss pilot pricing", "pilot pricing", "Acme") == {
        "purpose": "pilot pricing", "company": "Acme"}


def test_b5_closed_window(live):
    day, _ = live.openings()
    start, _ = live.calendar.day_window(str(day), str(day))
    assert not live.calendar.slots(start, start + timedelta(hours=2))
    live.calls.clear()
    answer = say(new_conversation(), f"Any openings between midnight and 2 am on {day}?")
    assert ("GET", "slots") in live.calls and not writes(live)
    assert re.search(r"no |nothing|unavailable|aren.t", answer, re.I) and "?" in answer, answer


def test_v5_empty(live):
    day, _ = live.openings()
    assert not live.agenda(day)
    live.calls.clear()
    answer = say(new_conversation(), f"Am I free on {day}? What's on my calendar?")
    assert ("GET", "bookings") in live.calls and not writes(live)
    assert re.search("free|no .*book|no .*meet|clear|empty|nothing", answer, re.I), answer


@pytest.mark.parametrize("case", ["B6-live", "B6-stale", "R3-live"])
def test_slot_taken(live, monkeypatch, case):
    day, slots = live.openings()
    move = case == "R3-live"
    original = live.seed(slots[0], "Maya") if move else None
    state = new_conversation()
    if move:
        prompt = f"I want to move my Maya meeting on {day}. Show three afternoon alternatives that day."
    else:
        prompt = f"I want to book a meeting. {identity(live)} Show three afternoon openings on {day}."
    candidates = [s for s in available(live, day) if local_start(live, s).hour >= 12]
    replayed = []
    real_slots = state.calendar.slots
    if case == "B6-stale":
        def slots_once(start, end, booking_uid=None, duration_minutes=None):
            query = (start, end, booking_uid, duration_minutes)
            if state.current_request == "Confirm" and not replayed and start == instant(chosen["start"]):
                replayed.append(query)
                return [chosen]
            return real_slots(start, end, booking_uid, duration_minutes)
        monkeypatch.setattr(state.calendar, "slots", slots_once)
    live.calls.clear()
    openings_reply = say(state, prompt)
    offers = displayed(live, openings_reply, candidates)
    assert len(offers) >= 2 and not writes(live), openings_reply
    chosen = offers[0]
    if not move:
        preview = say(state, "Book the first one you just listed.")
        assert state.booking_proposal and "Confirm this booking" in preview and not writes(live), preview
    competitor = live.seed(chosen, "Competitor")
    live.calls.clear()
    answer = say(state, "Yes, move it to the first one you just listed." if move else "Confirm")
    assert not writes(live) and ("GET", "slots") in live.calls, answer
    rejected = local_start(live, chosen)
    current = [slot for slot in available(live, day) if instant(slot["start"]) != rejected]
    assert re.search("unavailable|taken|no longer|couldn.t|not available|occupied", answer, re.I), answer
    assert displayed(live, answer, current), answer
    if case == "B6-stale":
        assert replayed and ("GET", "bookings") in live.calls and utc_stamp(rejected) not in state.slots
    expected = {competitor["uid"]: rejected}
    if original:
        expected[original["uid"]] = instant(original["start"])
    assert agenda_times(live, day) == expected


def test_c3_conflicting_details(live):
    day, _ = live.openings()
    leo = live.seed(at(live, day, 15), "Leo")
    maya = live.seed(at(live, day, 16), "Maya")
    live.calls.clear()
    answer = say(new_conversation(), f"Cancel my 4 pm meeting with Leo on {day}.")
    assert not writes(live) and re.search("no |don.t|cannot|couldn.t|didn.t|can.t|doesn.t|which", answer, re.I), answer
    assert agenda_times(live, day) == {row["uid"]: instant(row["start"]) for row in [leo, maya]}


def test_c4_abandoned(live):
    thu = next_weekday(live, 3)
    dates = [thu, thu + timedelta(days=1)]
    meetings = [live.seed(at(live, d, 10), "Maya") for d in dates]
    live.calls.clear()
    state = new_conversation()
    answer = say(state, "Cancel my meeting with Maya.")
    assert "?" in answer and not writes(live), answer
    say(state, "Never mind, keep both.")
    say(state, "The Friday one.")
    assert not writes(live)
    for d, row in zip(dates, meetings):
        assert agenda_times(live, d)[row["uid"]] == instant(row["start"])


def test_c5_already_cancelled(live):
    day, slots = live.openings()
    maya = live.seed(slots[0], "Maya")
    leo = live.seed(slots[1], "Leo")
    state = new_conversation()
    assert "maya" in say(state, f"Show my meeting with Maya on {day}.").lower()
    path = f"bookings/{maya['uid']}/cancel"
    live.calendar.cancel_booking(maya["uid"], "External cancellation")
    live.calls.clear()
    answer = say(state, "Cancel that one.")
    assert writes(live) in ([], [path])
    assert re.search("already|no longer|no active|can.t find|couldn.t find|no .*meeting|not found", answer, re.I), answer
    assert agenda_times(live, day) == {leo["uid"]: instant(leo["start"])}


@pytest.mark.parametrize("use_london_time", [False, True], ids=["founder-time", "london-followup"])
def test_r2_choose_original(live, use_london_time):
    thu = next_weekday(live, 3)
    wed, fri = thu - timedelta(days=1), thu + timedelta(days=1)
    original = live.seed(at(live, thu, 11), "Maya")
    other = live.seed(at(live, wed, 11), "Maya")
    control = live.seed(at(live, thu, 13), "Leo")
    dest = at(live, fri, 14)
    state = new_conversation()
    live.calls.clear()
    prompt = f"Move my intro with Maya to Friday, {fri}."
    if use_london_time:
        prompt += " Use London time for the destination."
    answer = say(state, prompt)
    assert "?" in answer and not writes(live), answer
    assert set(state.bookings) >= {original["uid"], other["uid"]}, answer
    zone = ZoneInfo("Europe/London") if use_london_time else live.calendar.zone
    requested = instant(dest["start"]).astimezone(zone)
    answer = say(state, f"Thursday, please, to {requested:%Y-%m-%d at %I:%M %p}.")
    assert "Confirm" in answer and not writes(live), answer
    answer = say(state, "Confirm")
    assert writes(live) == [f"bookings/{original['uid']}/reschedule"], answer
    assert (live.calendar.event_type_id, original["uid"]) in live.move_queries
    assert agenda_times(live, wed)[other["uid"]] == instant(other["start"])
    assert agenda_times(live, thu) == {control["uid"]: instant(control["start"])}
    assert instant(dest["start"]) in agenda_times(live, fri).values()


def test_move_with_explicit_london_time(live):
    day, slots = live.openings(minimum=2)
    original = live.seed(slots[0], "Maya")
    london = instant(slots[1]["start"]).astimezone(ZoneInfo("Europe/London"))
    live.calls.clear()
    state = new_conversation()
    reply = say(state, f"In London time, move my Maya meeting on {day} to {london:%Y-%m-%d at %I:%M %p}.")
    assert "Confirm" in reply and not writes(live), reply
    reply = say(state, "Confirm")
    assert writes(live) == [f"bookings/{original['uid']}/reschedule"], reply
    row, = live.agenda(day)
    assert instant(row["start"]) == london


def test_v3_attendee_zone(live):
    day, slots = live.openings()
    la = ZoneInfo("America/Los_Angeles")
    live.calendar.create_booking(utc_stamp(instant(slots[0]["start"])),
                                 {**live.attendee, "name": live.name + " Maya", "timeZone": la.key})
    row, = live.agenda(day)
    start = instant(row["start"])
    live.calls.clear()
    state = new_conversation()
    local_answer = say(state, f"When is my meeting with Maya on {day}?")
    attendee_answer = say(state, "What time is that for Maya?")
    assert time_in_reply(local_answer, start.astimezone(live.calendar.zone)), local_answer
    assert time_in_reply(attendee_answer, start.astimezone(la)), attendee_answer
    assert ("GET", "bookings") in live.calls and not writes(live)


def test_v4_pagination(live, monkeypatch):
    day, slots = live.openings()
    names = ["Maya", "Leo", "Priya"]
    for slot, name in zip(slots, names):
        live.seed(slot, name)
    assert len(live.agenda(day)) == 3
    state = new_conversation()
    agenda = state.calendar.agenda
    monkeypatch.setattr(state.calendar, "agenda", lambda first, last: agenda(first, last, page_size=2, status="upcoming"))
    live.calls.clear()
    live.pages.clear()
    answer = say(state, f"Show my entire agenda on {day}, with one line per meeting and attendee.").lower()
    for name, slot in zip(names, slots):
        rows = [line for line in answer.splitlines() if name.lower() in line]
        assert len(rows) == 1 and time_in_reply(rows[0], local_start(live, slot)), answer
    assert live.pages[0]["hasMore"] and not live.pages[-1]["hasMore"] and not writes(live)


def test_b7_dst(live):
    today = datetime.now(live.calendar.zone).date()
    for offset in range(1, 370):
        sunday = today + timedelta(days=offset)
        fri, mon = sunday - timedelta(days=2), sunday + timedelta(days=1)
        if fri > today and sunday.weekday() == 6:
            if live.calendar.day_window(str(fri), str(fri))[0].utcoffset() != live.calendar.day_window(str(mon), str(mon))[0].utcoffset():
                break
    else:
        pytest.skip("No upcoming DST transition in the configured founder zone")
    for day in (fri, mon):
        slot = at(live, day, 9)
        state = new_conversation()
        live.calls.clear()
        answer = say(state, f"Is {day} at 9 am an available opening?")
        assert time_in_reply(answer, local_start(live, slot)) and not writes(live), answer
        answer = request_and_confirm(live, state, "Book it. " + identity(live))
        assert writes(live) == ["bookings"] and time_in_reply(answer, local_start(live, slot)), answer
        expected = datetime(day.year, day.month, day.day, 9, tzinfo=live.calendar.zone)
        assert expected in agenda_times(live, day).values()


@pytest.mark.parametrize("variant", ["invalid-token", "outage"])
def test_x1_read_failure(live, monkeypatch, variant):
    state = new_conversation()
    original = httpx.request
    statuses = []

    def failing_read(method, url, **kwargs):
        if url.endswith("/v2/bookings") and method == "GET":
            if variant == "outage":
                statuses.append(503)
                return httpx.Response(503, json={"status": "error"})
            response = original(method, url, **kwargs)
            statuses.append(response.status_code)
            return response
        return original(method, url, **kwargs)

    if variant == "invalid-token":
        state.calendar.key = "cal_invalid_round_two_token"
    monkeypatch.setattr(httpx, "request", failing_read)
    answer = say(state, "What's on my calendar tomorrow?")
    assert statuses and set(statuses) <= ({401, 403} if variant == "invalid-token" else {503})
    assert re.search(r"couldn.t|could not|unable|can.t|failed|unavailable|couldn't|couldn’t|service error|authentication", answer, re.I), answer
    assert not re.search("calendar is (clear|free|empty)|no bookings (found|scheduled)", answer, re.I)
    assert not writes(live)


def test_x2_lost_create_response(live, monkeypatch):
    day, slots = live.openings()
    original = httpx.request
    committed = []

    def lose_receipt(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if method == "POST" and url.endswith("/v2/bookings") and response.is_success and not committed:
            committed.append(response.json()["data"]["uid"])
            raise httpx.ReadTimeout("Injected loss after real commit")
        return response

    state = new_conversation()
    with monkeypatch.context() as patch:
        patch.setattr(httpx, "request", lose_receipt)
        answer = request_and_confirm(live, state, f"Book {when(live, slots[0])}. " + identity(live))
    assert len(committed) == 1 and writes(live) == ["bookings"] and "did not confirm" in answer.lower(), answer
    live.calls.clear()
    answer = say(state, "Did that go through?")
    assert ("GET", "bookings") in live.calls and not writes(live)
    assert time_in_reply(answer, local_start(live, slots[0])) and re.search("confirm|booked|scheduled|went through", answer, re.I), answer
    row, = live.agenda(day)
    assert row["uid"] == committed[0] and row["attendees"][0]["email"] == live.attendee["email"]


def test_x3_duplicate_delivery(live):
    day, _ = live.openings()
    state = new_conversation()
    answer = say(state, f"Show three morning openings on {day}. " + identity(live))
    choices = displayed(live, answer, available(live, day))
    assert len(choices) >= 2, answer
    live.calls.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: chat_turn(state, "Book the second one.", "same-delivery"), range(2)))
    assert replies[0] == replies[1] and state.booking_proposal and not writes(live)
    assert "already have an upcoming meeting" not in replies[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: chat_turn(state, "Confirm", "same-confirmation"), range(2)))
    assert receipts[0] == receipts[1] and writes(live) == ["bookings"]
    row, = live.agenda(day)
    assert instant(row["start"]) == instant(choices[1]["start"])
    preview = say(state, f"Please also book a separate meeting for the same attendee on {when(live, choices[0])}.")
    assert "already have an upcoming meeting" in preview and live.name in preview, preview
    assert time_in_reply(preview, local_start(live, choices[1])) and "Confirm this booking" in preview
    assert writes(live) == ["bookings"]
    checked = say(state, "Wait, did I book with her already?")
    assert state.booking_proposal and time_in_reply(checked, local_start(live, choices[1])), checked
    assert "Confirm this booking" in checked and writes(live) == ["bookings"]
    assert "Booked" in say(state, "Confirm")
    assert writes(live) == ["bookings", "bookings"]
    assert set(agenda_times(live, day).values()) == {instant(s["start"]) for s in choices[:2]}
    live.calls.clear()
    answer = say(state, "Wait, did I book with this person already?")
    assert ("GET", "bookings") in live.calls and not writes(live), answer
    assert all(time_in_reply(answer, local_start(live, s)) for s in choices[:2]), answer
    # A fresh chat must see the calendar bookings, even with no shared conversation history.
    fresh = new_conversation()
    next_slot = next(slot for slot in available(live, day)
                     if instant(slot["start"]) not in {instant(s["start"]) for s in choices[:2]})
    preview = say(fresh, f"Book a meeting on {when(live, next_slot)}. {identity(live)}")
    assert "already have 2 upcoming meetings" in preview and all(time_in_reply(preview, local_start(live, s)) for s in choices[:2]), preview
    assert not writes(live)


@pytest.mark.parametrize("include_match", [False, True])
def test_next_meeting_requires_a_future_active_booking_and_both_attendee_fields(monkeypatch, include_match):
    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"), event_type_id=1)
    maya = {"name": "MAYA  Chen", "email": "Maya@Example.test"}

    def pages(method, path, version, *, params):
        assert (method, path) == ("GET", "bookings") and params["attendeeEmail"] == "maya@example.test"
        now = instant(params["afterStart"])

        def row(uid, hours, attendees, status="accepted"):
            return {"uid": uid, "eventTypeId": 2, "start": utc_stamp(now + timedelta(hours=hours)),
                    "status": status, "attendees": attendees}

        if "cursor" not in params:
            rows = [row("already started", -1, [maya]), row("starts now", 0, [maya]),
                    row("cancelled", 1, [maya], "cancelled"), row("declined", 1, [maya], "rejected"),
                    row("two different people", 1, [{"name": "Maya Chen", "email": "other@example.test"},
                                                     {"name": "Another Person", "email": maya["email"]}])]
            if include_match:
                rows.append(row("later", 24 * 180, [maya]))
            return {"data": rows, "pagination": {"hasMore": True, "nextCursor": "page-two"}}
        assert params["cursor"] == "page-two"
        rows = [row("next", 24 * 60, [maya], "pending")] if include_match else []
        return {"data": rows, "pagination": {"hasMore": False}}

    monkeypatch.setattr(calendar, "request", pages)
    matches = calendar.upcoming_meetings_with(" Maya Chen ", " maya@example.test ")
    assert [match["uid"] for match in matches] == (["next", "later"] if include_match else [])


def test_agenda_then_morning_booking_warns_before_confirmation(live, ui):
    now = datetime.now(live.calendar.zone)
    monday = now.date() + timedelta(days=7 - now.weekday())
    for _ in range(6):
        start = datetime.combine(monday, datetime.min.time(), live.calendar.zone).replace(hour=10)
        existing = start.replace(hour=14) + timedelta(days=1)
        if (not live.calendar.agenda(str(monday), str(monday + timedelta(days=6)))
                and live.calendar.slots(start, start + timedelta(minutes=30))
                and live.calendar.slots(existing, existing + timedelta(minutes=60), duration_minutes=60)):
            break
        monday += timedelta(weeks=1)
    else:
        pytest.fail("No isolated Monday morning and Tuesday afternoon pair available.")
    name = live.name + " Maya Chen"
    prior = live.calendar.create_booking(utc_stamp(existing), {**live.attendee, "name": name}, duration_minutes=60)
    live.calls.clear()
    assert name in ask(ui, f"What's on my calendar the week of {monday}?")
    ask(ui, f"Book a meeting with {name} on Monday {monday}.")
    ask(ui, "30 mins")
    ask(ui, "morning")
    preview = ask(ui, f"10:00 am, her email is {live.attendee['email']}")
    assert f"already have an upcoming meeting with {name}" in preview, preview
    assert time_in_reply(preview, existing) and time_in_reply(preview, start) and "Confirm this booking" in preview, preview
    assert not writes(live) and not live.agenda(monday)
    fresh = new_conversation()
    reply = say(fresh, f"Did I already book with {name}, {live.attendee['email']}?")
    assert time_in_reply(reply, existing) and list(fresh.bookings) == [prior["uid"]] and not writes(live), reply


def maya_replay_week(live, monkeypatch):
    zone = live.calendar.zone
    today = datetime.now(zone).date()
    monday = today + timedelta(days=7 - today.weekday())
    for _ in range(6):
        ten = datetime.combine(monday, datetime.min.time(), zone).replace(hour=10)
        nine_thirty = ten - timedelta(minutes=30)
        tuesday_two = ten.replace(hour=14) + timedelta(days=1)
        if (not live.calendar.agenda(str(monday), str(monday + timedelta(days=6)))
                and live.calendar.slots(nine_thirty, ten)
                and live.calendar.slots(ten, ten + timedelta(minutes=30))
                and live.calendar.slots(tuesday_two, tuesday_two + timedelta(hours=1), duration_minutes=60)):
            break
        monday += timedelta(weeks=1)
    else:
        pytest.fail("No isolated week with the three openings needed to replay Maya's conversation.")
    # Anchor “next week” and “tomorrow” to the Sunday preceding the isolated live fixtures.
    sunday = ten - timedelta(days=1)
    class SundayClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return sunday.astimezone(tz) if tz else sunday.replace(tzinfo=None)

    monkeypatch.setattr(scheduling, "datetime", SundayClock)
    return nine_thirty, ten, tuesday_two


@pytest.mark.parametrize("live", ["maya-name-correction"], indirect=True)
@pytest.mark.parametrize("ask_for_openings", [False, True], ids=["exact-transcript", "after-slot-list"])
def test_correct_partial_name_while_choosing_a_slot(live, ui, monkeypatch, ask_for_openings):
    nine_thirty, ten, tuesday_two = maya_replay_week(live, monkeypatch)
    monday = ten.date()
    originals = [live.calendar.create_booking(utc_stamp(ten), live.attendee),
                 live.calendar.create_booking(utc_stamp(tuesday_two), live.attendee, duration_minutes=60)]
    live.calls.clear()
    agenda = ask(ui, "what's my next week's schedule?")
    assert "Maya Chen" in agenda and time_in_reply(agenda, ten) and time_in_reply(agenda, tuesday_two), agenda
    state = ui.session_state["conversation"]
    request = "I want to schedule a meeting with Maya tomorrow"
    if ask_for_openings:
        request += ". Show me morning openings."
    options = ask(ui, request)
    if ask_for_openings:
        assert utc_stamp(nine_thirty) in state.offered_starts and state.selection.pending_question == "slot", options
    assert state.draft.attendee_name == "Maya" and state.draft.attendee_email is None and not state.booking_proposal
    ask(ui, "it's maya chen")
    assert state.draft.attendee_name == "Maya Chen" and not state.booking_proposal
    email = live.attendee["email"]
    ask(ui, f"the email is [{email}](mailto:{email})")
    assert (state.draft.attendee_name, state.draft.attendee_email) == ("Maya Chen", email)
    assert not state.booking_proposal and not writes(live)
    preview = ask(ui, "9:30")
    assert state.booking_proposal and state.booking_proposal.start == nine_thirty, preview
    assert state.booking_proposal.attendee["name"] == "Maya Chen" and state.booking_proposal.attendee["email"] == email
    assert "already have 2 upcoming meetings with Maya Chen" in preview and "Confirm this booking" in preview, preview
    assert time_in_reply(preview, ten) and time_in_reply(preview, tuesday_two) and not writes(live), preview
    assert "Maya Chen" in ask(ui, "confirm") and writes(live) == ["bookings"]
    week = live.calendar.agenda(str(monday), str(monday + timedelta(days=1)))
    assert {b["uid"] for b in originals} <= {b["uid"] for b in week}
    assert len(week) == 3
    booked, = [b for b in week if instant(b["start"]) == nine_thirty]
    assert instant(booked["end"]) == ten
    assert [(a["name"], a["email"]) for a in booked["attendees"]] == [("Maya Chen", email)]


@pytest.mark.parametrize("live", ["maya-cancellations"], indirect=True)
@pytest.mark.parametrize("reject_second", [False, True], ids=["both-succeed", "second-declined"])
def test_keep_tuesday_cancel_mondays(live, ui, monkeypatch, reject_second):
    nine_thirty, ten, tuesday_two = maya_replay_week(live, monkeypatch)
    chen = {**live.attendee, "name": "Maya Chen"}
    first = live.calendar.create_booking(utc_stamp(nine_thirty), live.attendee)["uid"]
    second = live.calendar.create_booking(utc_stamp(ten), chen)["uid"]
    keep = live.calendar.create_booking(utc_stamp(tuesday_two), chen, duration_minutes=60)["uid"]
    live.calls.clear()
    agenda = ask(ui, "what's ny schedule next week?")
    assert ("GET", "bookings") in live.calls and not writes(live), agenda
    assert all(time_in_reply(agenda, t) for t in (nine_thirty, ten, tuesday_two)), agenda
    preview = ask(ui, "please just keep the tuesday meeting, they are all the same person")
    kept, cancelled = preview.split("**Cancel**")
    assert "Maya Chen" in kept and time_in_reply(kept, tuesday_two), preview
    assert time_in_reply(cancelled, nine_thirty) and time_in_reply(cancelled, ten) and "Confirm" in preview, preview
    assert not writes(live) and set(agenda_times(live, ten.date())) == {first, second}
    assert set(agenda_times(live, tuesday_two.date())) == {keep}
    paths = [f"bookings/{uid}/cancel" for uid in (first, second)]
    if reject_second:
        # Force one real API validation error. The approved reason still goes to the first booking.
        # The scoped override ends before fixture cleanup.
        request = scheduling.CalClient.request
        def invalid_reason(client, method, path, version, *, params=None, body=None):
            if method == "POST" and path == paths[1]:
                body = {**body, "cancellationReason": ["invalid string field"]}
            return request(client, method, path, version, params=params, body=body)
        with monkeypatch.context() as patch:
            patch.setattr(scheduling.CalClient, "request", invalid_reason)
            result = ask(ui, "confirm")
    else:
        result = ask(ui, "confirm")
    assert writes(live) == paths, result
    outcomes = [line for line in result.splitlines() if line.startswith("- ")]
    assert len(outcomes) == 2 and time_in_reply(outcomes[0], nine_thirty) and time_in_reply(outcomes[1], ten), result
    assert "Cancelled" in outcomes[0], result
    if reject_second:
        assert "Cancellation declined" in outcomes[1] and "HTTP 400" in outcomes[1], result
    else:
        assert "Cancelled" in outcomes[1] and live.cancel_reasons[paths[0]] == live.cancel_reasons[paths[1]]
    assert live.cancel_reasons[paths[0]] in preview
    assert ask(ui, "confirm") == result and writes(live) == paths
    if reject_second:
        assert ask(ui, "what's the reason?") == result and writes(live) == paths
    assert set(agenda_times(live, ten.date())) == ({second} if reject_second else set())
    assert agenda_times(live, tuesday_two.date()) == {keep: tuesday_two}


def cancellation_state(meetings):
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    rows = []
    for uid, day, hour, minute in meetings:
        start = datetime(2026, 11, day, hour, minute, tzinfo=zone)
        rows.append({"uid": uid, "title": "Meeting", "status": "accepted", "start": start.isoformat(),
                     "end": (start + timedelta(minutes=30)).isoformat(), "attendees": [{"name": "Maya"}]})
    state.bookings = {b["uid"]: scheduling.booking_summary(b, zone) for b in rows}
    state.keep_request = state.current_request = "Just keep the Tuesday meeting."
    return state, rows


def preview_cancel_targets(state, keep, cancel, reason=None):
    arguments = json.dumps({"keep_uid": keep, "cancel_uids": cancel, "reason": reason})
    ctx = ToolContext(state, tool_name="preview_cancellations", tool_call_id="keep", tool_arguments=arguments)
    state.turn_reply = ""
    return asyncio.run(scheduling.preview_cancellations.on_invoke_tool(ctx, arguments))


def test_ambiguous_kept_meeting_and_changed_calendar_cannot_authorize_cancellations(monkeypatch):
    state, rows = cancellation_state([("monday", 23, 10, 0), ("early", 24, 9, 0), ("late", 24, 14, 0)])
    assert "Which meeting should I keep?" in preview_cancel_targets(state, "late", ["monday", "early"])
    assert state.cancellation_proposal is None
    assert "Which meeting" in chat_turn(state, "confirm")
    state.current_request = "The 2 PM one."
    assert "Confirm these cancellations" in preview_cancel_targets(state, "late", ["monday", "early"])
    # The protected appointment disappeared between preview and confirmation.
    monkeypatch.setattr(state.calendar, "agenda", lambda *_: rows[:2])
    monkeypatch.setattr(state.calendar, "cancel_booking", lambda *_: pytest.fail("Unexpected cancellation"))
    assert "stopped before sending any cancellations" in chat_turn(state, "yes")


def test_single_cancellation_preview_checks_the_original_and_expires_on_a_bad_edit():
    state, _ = cancellation_state([("morning", 24, 10, 0), ("afternoon", 24, 14, 0)])
    state.keep_request = None
    state.current_request = "The 10 AM meeting with Maya."
    assert "Confirm this cancellation" in preview_cancel_targets(state, None, ["morning"])
    assert [b["uid"] for b in state.cancellation_proposal.cancel] == ["morning"]
    preview_cancel_targets(state, None, ["afternoon"])
    assert state.cancellation_proposal is None and state.last_write_receipt is None
    for request in ("My Tuesday meeting with Maya.", "Cancel my Wednesday meeting.", "Cancel my meeting with Nora."):
        state.current_request = request
        preview_cancel_targets(state, None, ["morning"])
        assert state.cancellation_proposal is None


def test_keep_request_cannot_cancel_its_protected_uid_through_a_single_tool(monkeypatch):
    state, _ = cancellation_state([("monday", 23, 10, 0), ("tuesday", 24, 14, 0)])
    state.keep_request = None
    state.current_request = "Please keep the Tuesday meeting and cancel the Monday one."
    monkeypatch.setattr(state.calendar, "cancel_booking", lambda *_: pytest.fail("Unexpected direct cancellation"))
    args = json.dumps({"booking_uid": "tuesday", "reason": None})
    ctx = ToolContext(state, tool_name="cancel_booking", tool_call_id="protected", tool_arguments=args)
    asyncio.run(scheduling.cancel_booking.on_invoke_tool(ctx, args))
    preview_cancel_targets(state, None, ["tuesday"])
    assert state.cancellation_proposal is None and state.last_write_receipt is None
    assert "Confirm these cancellations" in preview_cancel_targets(state, "tuesday", ["monday"])


def test_spoken_ten_cannot_select_the_only_morning_booking_at_nine():
    state, _ = cancellation_state([("nine", 24, 9, 0), ("afternoon", 24, 14, 0)])
    state.keep_request = None
    state.current_request = "It's the ten in the morning with Maya that we're calling off. The afternoon one stays."
    assert scheduling.check_original(state, "nine") and scheduling.matching_originals(state, state.current_request) == []
    preview_cancel_targets(state, None, ["nine"])
    assert state.cancellation_proposal is None
    state, _ = cancellation_state([("one-am", 24, 1, 0), ("nine", 24, 9, 0)])
    assert scheduling.matching_originals(state, "Cancel the one in the morning with Maya.") == []


def test_keep_edit_to_a_pending_group_excludes_bookings_outside_that_group():
    from meeting_batch import MeetingBatch, RequestedMeeting, batch_turn
    state, _ = cancellation_state([("monday", 23, 10, 0), ("tuesday", 24, 10, 0), ("other", 25, 14, 0)])
    state.keep_request = None
    items = [RequestedMeeting(name="Maya", booking_uid=uid, email=None, minutes=None, local_start=None, attendee_zone=None)
             for uid in ("monday", "tuesday")]
    state.batch = MeetingBatch("cancel", 0, items=items, preview=[
        scheduling.CancellationProposal(None, [state.bookings[row.booking_uid] for row in items], None)])
    state.current_request = "Please keep the Tuesday meeting and cancel the Monday one."
    assert batch_turn(state, state.current_request, datetime.now(state.calendar.zone)) is None
    assert state.batch is None and set(state.bookings) == {"monday", "tuesday"}
    for invalid in (["monday", "tuesday"], ["other"]):
        preview_cancel_targets(state, "tuesday", invalid)
        assert state.cancellation_proposal is None
    assert "Confirm these cancellations" in preview_cancel_targets(state, "tuesday", ["monday"])


def test_cancellation_preview_checks_missing_extra_targets_and_confirmation_count():
    state, _ = cancellation_state([("first", 23, 9, 30), ("second", 23, 10, 0),
                                   ("keep", 24, 14, 0), ("third", 25, 12, 0)])
    preview_cancel_targets(state, "keep", ["first"])
    assert state.cancellation_proposal is None
    assert "Confirm these cancellations" in preview_cancel_targets(state, "keep", ["first", "second", "third"])
    assert "Which two meetings" in chat_turn(state, "yes, cancel both") and state.cancellation_proposal is None
    state.current_request = "Cancel the Monday 9:30 AM meeting."
    preview_cancel_targets(state, "keep", ["first", "second"])
    assert state.cancellation_proposal is None
    assert "Confirm these cancellations" in preview_cancel_targets(state, "keep", ["first"])
    state.current_request = "Use duplicate booking as the reason."
    assert "duplicate booking" in preview_cancel_targets(state, "keep", ["first"], "duplicate booking")
    assert [b["uid"] for b in state.cancellation_proposal.cancel] == ["first"]
    assert "discarded" in chat_turn(state, "no") and state.cancellation_proposal is None
    assert scheduling.keep_meeting_followup(state, "Cancel Monday at 9:30 because I need to keep my afternoons free.") is None


def test_x4_unknown_uid(live, monkeypatch):
    day, slots = live.openings()
    target = live.seed(slots[0], "Maya")
    state = new_conversation()
    assert "maya" in say(state, f"Show my meeting with Maya on {day}.").lower()
    tool = scheduling.cancel_booking
    intercepted = []

    async def corrupt_once(ctx, arguments):
        args = json.loads(arguments)
        if not intercepted:
            intercepted.append(args["booking_uid"])
            args["booking_uid"] = "unknown-round-two-uid"
        return await tool.on_invoke_tool(ctx, json.dumps(args))

    monkeypatch.setattr(scheduling, "cancel_booking", replace(tool, on_invoke_tool=corrupt_once))
    live.calls.clear()
    answer = say(state, "Cancel that Maya meeting.")
    assert intercepted == [target["uid"]] and not writes(live), answer
    assert "?" in answer or re.search("identify|clarify|specify|choose", answer, re.I), answer
    assert agenda_times(live, day) == {target["uid"]: instant(target["start"])}


@pytest.mark.parametrize("live", ["fields"], indirect=True)
def test_b4_custom_fields(live):
    day, slots = live.openings()
    state = new_conversation()
    live.calls.clear()
    say(state, f"Book a meeting with {live.name} from Acme to discuss pilot pricing. " + identity(live))
    assert not writes(live)
    answer = request_and_confirm(live, state, f"Book {when(live, slots[0])}.")
    assert writes(live) == ["bookings"], answer
    row, = live.agenda(day)
    fields = row["bookingFieldsResponses"]
    assert "acme" == fields.get("company", "").lower() and "pilot pricing" in fields.get("purpose", "").lower(), fields.keys()
    answer = request_and_confirm(live, new_conversation(), f"Book {when(live, slots[1])}. " + identity(live))
    assert writes(live) == ["bookings", "bookings"], answer
    second, = [r for r in live.agenda(day) if r["uid"] != row["uid"]]
    assert not any(second["bookingFieldsResponses"].get(k) for k in ("purpose", "company"))


@pytest.mark.parametrize("live", ["pending"], indirect=True)
def test_r5_pending_approval(live, monkeypatch):
    day, slots = live.openings()
    original = live.seed(slots[0], "Maya")
    assert original["status"] == "pending"
    state = new_conversation()
    # Use Cal.com's public attendee reschedule endpoint for this case. Sending
    # the owner's token makes the owner the actor and auto-approves the move.
    transport = httpx.request
    def attendee_request(method, url, **kwargs):
        if method == "POST" and url.endswith(f"/{original['uid']}/reschedule"):
            kwargs["headers"] = {k: v for k, v in kwargs["headers"].items() if k != "Authorization"}
            kwargs["json"] = {**kwargs["json"], "rescheduledBy": live.attendee["email"]}
        return transport(method, url, **kwargs)
    monkeypatch.setattr(httpx, "request", attendee_request)
    live.calls.clear()
    answer = say(state, f"Move my meeting with Maya on {day} to {when(live, slots[1])}.")
    assert "Confirm" in answer and not writes(live), answer
    answer = say(state, "Confirm")
    assert writes(live) == [f"bookings/{original['uid']}/reschedule"], answer
    moved, = live.agenda(day)
    assert instant(moved["start"]) == instant(slots[1]["start"])
    if moved["status"] == "accepted":
        pytest.skip("Cal.com accepted the real move of a pending booking; pending move reply unexercised")
    assert moved["status"] == "pending" and time_in_reply(answer, local_start(live, slots[1]))
    assert re.search("awaiting|pending|approval", answer, re.I), answer


@pytest.mark.parametrize("clock", ["4 pm", "16:00"])
def test_conflicting_target_guard(clock):
    # Exercise the validator even on runs where the model independently declines the write.
    state = scheduling.Conversation(calendar=None)
    for uid, name, hour in [("leo", "Leo", 15), ("maya", "Maya", 16)]:
        state.bookings[uid] = {"weekday": "Friday", "start": f"2026-12-04T{hour}:00:00-05:00",
                               "attendees": [{"name": name}]}
    state.current_request = f"Cancel my {clock} meeting with Leo."
    assert scheduling.check_original(state, "leo")
    assert scheduling.check_original(state, "maya")
    state.current_request = f"Cancel my {clock} meeting with Maya."
    assert scheduling.check_original(state, "maya") is None


def test_booking_selection_must_match_the_displayed_time():
    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"))
    state = scheduling.Conversation(calendar)
    first, second = "2026-12-04T19:00:00Z", "2026-12-04T20:00:00Z"
    state.offered_starts = [first, second]
    for prompt, correct, wrong in [("Book 2 pm.", first, second), ("Book the second one.", second, first)]:
        state.current_request = prompt
        assert scheduling.chosen_by_user(state, correct)
        assert not scheduling.chosen_by_user(state, wrong)
    state.messages = [{"role": "user", "content": "Is 2 pm available?"}]
    state.current_request = "Book an intro for Maya."
    assert not scheduling.chosen_by_user(state, first)
    state.current_request = "Book 2026-12-04 at 2 pm."
    assert scheduling.chosen_by_user(state, first)
    assert not scheduling.chosen_by_user(state, "2026-12-07T19:00:00Z")
    state.messages.append({"role": "assistant", "content": "Yes, 2 pm is available."})
    state.offered_starts.clear()
    state.current_request = "Book it."
    assert scheduling.chosen_by_user(state, first)
    assert not scheduling.chosen_by_user(state, second)


def test_yes_requires_one_opening_in_the_immediately_preceding_offer():
    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"))
    state = scheduling.Conversation(calendar)
    first = datetime(2026, 11, 23, 14, tzinfo=calendar.zone)
    for start in (first, first + timedelta(hours=1)):
        state.slots[utc_stamp(start)] = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
    offer = scheduling.offer_slots(state)
    assert chat_turn(state, "yes") == offer
    assert state.draft is None
    state.slots.pop(utc_stamp(first + timedelta(hours=1)))
    scheduling.offer_slots(state)
    scheduling.accept_slot_offer(state, "What about Tuesday instead?", state.selection.take_question())
    scheduling.accept_slot_offer(state, "yes", state.selection.take_question())
    assert state.draft is None


def test_contact_reply_reuses_only_the_selected_start():
    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"))
    state = scheduling.Conversation(calendar)
    one = datetime(2026, 11, 23, 13, tzinfo=calendar.zone)
    two = one + timedelta(hours=1)
    state.current_request = "use my time zone"
    state.slot_window = (one, two + timedelta(hours=1))
    state.slots = {utc_stamp(t): {"start": t.isoformat()} for t in (one, two)}
    state.messages = [{"role": "user", "content": turn} for turn in
                      ("Book a candidate interview tomorrow", "2pm Monday", "ok, then 1pm?")]
    # An older open session has history and checked slots but no structured draft.
    assert scheduling.chosen_by_user(state, utc_stamp(one))
    assert not scheduling.chosen_by_user(state, utc_stamp(two))
    state.draft = scheduling.BookingDraft(one.date(), utc_stamp(one))
    assert scheduling.chosen_by_user(state, utc_stamp(one))
    scheduling.update_booking_draft(state, "actually 2pm")
    assert state.draft.selected_start is None
    assert not scheduling.chosen_by_user(state, utc_stamp(one))
    state.draft.selected_start = utc_stamp(one)
    state.current_request = "Maya Chen"
    assert scheduling.chosen_by_user(state, utc_stamp(one), "Maya Chen")
    state.current_request = "1pm"
    assert not scheduling.chosen_by_user(state, utc_stamp(one + timedelta(days=1)))
    scheduling.update_booking_draft(state, "actually Tuesday instead")
    assert state.draft.selected_start is None and state.draft.day == (one + timedelta(days=1)).date()
    state.current_request = "use my time zone"
    assert not scheduling.chosen_by_user(state, utc_stamp(one))
    state.draft = scheduling.BookingDraft(one.date(), utc_stamp(one))
    state.current_request = "maya@example.test, same time zone, but make it an hour later instead"
    scheduling.update_booking_draft(state, state.current_request)
    assert state.draft.selected_start is None
    assert not scheduling.booking_detail_reply(state.current_request)
    assert not scheduling.chosen_by_user(state, utc_stamp(one))


def test_same_as_mine_is_scoped_to_an_attendee_zone_question():
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))
    state.draft = scheduling.BookingDraft()
    scheduling.attendee_zone_question(state, "Hellen")
    assert scheduling.booking_reply_in_context(state.selection.take_question(), "same as mine") == "Use my time zone."
    state.messages = [{"role": "assistant", "content": "In which time zone should I show those openings?"}]
    assert scheduling.booking_reply_in_context(state.selection.take_question(), "same as mine") == "same as mine"
    scheduling.attendee_zone_question(state, "Hellen")
    assert scheduling.booking_reply_in_context(state.selection.take_question(), "Show me my agenda instead") == "Show me my agenda instead"
    assert scheduling.booking_reply_in_context(state.selection.take_question(), "mine") == "mine"


def test_booking_draft_keeps_contact_through_time_changes_and_separates_recipients():
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))
    first = "Book a meeting with Hellen, email hellen@example.invalid. Use my time zone."
    state.current_request = first
    scheduling.update_booking_draft(state, first)
    draft = state.draft
    assert (draft.attendee_name, draft.attendee_email, draft.attendee_time_zone) == (
        "Hellen", "hellen@example.invalid", "America/New_York")
    draft.selected_start = "2026-11-23T15:00:00Z"
    draft.duration_minutes = 60
    scheduling.update_booking_draft(state, "actually 11am")
    assert draft.selected_start is None and draft.duration_minutes == 60 and draft.attendee_name == "Hellen"
    state.current_request = "Invite Maya instead."
    scheduling.update_booking_draft(state, state.current_request)
    assert draft.attendee_name == "Maya" and draft.attendee_email is None and draft.attendee_time_zone is None


@pytest.mark.parametrize("intent, from_offer", [("check_start", False), ("select_start", False), ("select_start", True)])
def test_referenced_time_with_no_booking_draft(monkeypatch, intent, from_offer):
    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"))
    state = scheduling.Conversation(calendar)
    state.messages = [{"role": "user", "content": "Hellen's email is hellen@example.invalid."}]
    state.current_request = "Same time as Hellen."
    start, end = "2026-11-25T13:00:00-05:00", "2026-11-25T13:30:00-05:00"
    if from_offer:
        state.slots = {utc_stamp(instant(start)): {"start": start, "end": end}}
        offer = scheduling.offer_slots(state)
        state.messages.extend([{"role": "user", "content": "Show openings. Attendee name is Maya Chen, email maya@example.invalid."},
                               {"role": "assistant", "content": offer}])
        state.current_request = "1 PM works."
    monkeypatch.setattr(calendar, "event_durations", lambda: (30, [30, 60]))
    monkeypatch.setattr(calendar, "slots", lambda *a, **kw: [{"start": start, "end": end}])
    monkeypatch.setattr(calendar, "agenda", lambda *a, **kw: [])
    args = json.dumps(dict(local_start=start, local_end=end, intent=intent))
    ctx = ToolContext(state, tool_name="find_slots", tool_call_id="referenced-time", tool_arguments=args)
    asyncio.run(scheduling.find_slots.on_invoke_tool(ctx, args))
    if intent == "check_start":
        assert state.draft is None and "available" in state.turn_reply
    else:
        assert state.draft and state.draft.selected_start == utc_stamp(instant(start))
        if from_offer:
            assert (state.draft.attendee_name, state.draft.attendee_email) == ("Maya Chen", "maya@example.invalid")
        else:
            assert state.draft.pending_question == "contact" and not scheduling.booking_addresses(state)
    assert state.booking_proposal is None


def test_name_refinement_preserves_given_contact_but_recipient_switch_clears_it():
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))
    for when in ("Wednesday at 1 PM", "this coming Wednesday", "this Wensday"):
        assert scheduling.supplied_attendee_name(f"I need to book a meeting with Maya Chen {when}") == "Maya Chen"
    assert scheduling.supplied_attendee_name("Her name is Maya Friday.") == "Maya Friday"
    assert scheduling.supplied_attendee_name("I need to book a meeting with Maya Chen Wednesday.") is None
    state.bookings = {"one": {"attendees": [{"name": "Maya Chen", "email": "other@example.test"}]}}
    scheduling.update_booking_draft(state, "I want to schedule a meeting with Maya tomorrow")
    scheduling.update_booking_draft(state, "the email is maya@example.test")
    scheduling.update_booking_draft(state, "it’s tomorrow morning")
    assert state.draft.attendee_name == "Maya"
    scheduling.update_booking_draft(state, "I meant Maya is free in the morning")
    assert state.draft.attendee_name == "Maya"
    scheduling.update_booking_draft(state, "it's maya chen")
    assert (state.draft.attendee_name, state.draft.attendee_email) == ("Maya Chen", "maya@example.test")
    scheduling.update_booking_draft(state, "9:30")
    assert state.draft.attendee_name == "Maya Chen"
    scheduling.update_booking_draft(state, "Actually, I meant Maya Jones")
    assert state.draft.attendee_name == "Maya Jones" and state.draft.attendee_email is None

    scheduling.update_booking_draft(state, "Book a new meeting with Maya, maya@example.test.")
    scheduling.update_booking_draft(state, "her full name is Maya Chen")
    assert (state.draft.attendee_name, state.draft.attendee_email) == ("Maya Chen", "maya@example.test")


def test_agenda_detour_and_attendee_corrections_preserve_the_selected_slot():
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))
    scheduling.update_booking_draft(state, "Book a meeting with Dr. Maya A. Chen, maya@example.invalid. Use my time zone.")
    draft = state.draft
    assert (draft.attendee_name, draft.attendee_time_zone) == ("Dr. Maya A. Chen", "America/New_York")
    draft.selected_start = "2026-11-23T15:00:00Z"
    draft.day = instant(draft.selected_start).astimezone(state.calendar.zone).date()
    draft.duration_minutes = 60
    scheduling.update_booking_draft(state, "Show my calendar tomorrow.")
    scheduling.update_booking_draft(state, "What's on the schedule for Tuesday?")
    scheduling.update_booking_draft(state, "Can you check my calendar for Tuesday?")
    assert not scheduling.agenda_question("Do I have time to schedule a meeting with Maya tomorrow afternoon?")
    assert scheduling.booking_metadata_only("Her email is maya@example.invalid. Use my time zone.")
    assert not scheduling.booking_metadata_only("Her email is maya@example.invalid, but show me other openings first.")
    scheduling.update_booking_draft(state, "Actually change the attendee to Leo, leo@example.invalid.")
    assert state.draft is draft and (draft.selected_start, draft.duration_minutes) == ("2026-11-23T15:00:00Z", 60)
    assert (draft.attendee_name, draft.attendee_email, draft.attendee_time_zone) == ("Leo", "leo@example.invalid", None)
    scheduling.update_booking_draft(state, "Use Europe/London for his attendee time zone, and use my time zone to display openings.")
    scheduling.update_booking_draft(state, "use my time zone")
    assert draft.attendee_time_zone == "Europe/London"


def test_open_session_migrates_verified_selection_and_pending_question():
    from types import SimpleNamespace
    old = SimpleNamespace(draft=SimpleNamespace(day=None, selected_start="2026-11-23T15:00:00Z"),
                          chosen_minutes=60, awaiting_attendee_zone=True, messages=[])
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))
    state.restore_session(old)
    assert state.draft.selected_start == old.draft.selected_start and state.draft.duration_minutes == 60
    assert state.draft.attendee_email is None and state.draft.pending_question == "attendee_time_zone"


def test_followup_target_and_abandonment_guards():
    state = scheduling.Conversation(calendar=None)
    for uid, day, weekday in [("wed", 2, "Wednesday"), ("thu", 3, "Thursday")]:
        state.bookings[uid] = {"weekday": weekday, "start": f"2026-12-0{day}T11:00:00-05:00", "attendees": []}
    for prompt in ("Thursday’s one, Friday at 2 pm.", "Thursday, please, to Friday at 2 pm."):
        state.current_request = prompt
        with pytest.raises(ValueError, match="matching booking UID is thu"):
            scheduling.check_original(state, "wed", moving=True)
        assert scheduling.check_original(state, "thu", moving=True) is None
    state.cancellation_abandoned = True
    state.current_request = "The Thursday one."
    assert "new cancellation" in scheduling.check_original(state, "thu")
    zone = ZoneInfo("America/New_York")
    state.calendar = scheduling.CalClient("", zone)
    state.current_request = "Thursday, please, to 2026-12-04 at 2 pm."
    assert scheduling.exact_move_destination(state) == datetime(2026, 12, 4, 14, tzinfo=zone)
    state.messages = [{"role": "user", "content": "Move my intro to Friday. Use London time."},
                      {"role": "assistant", "content": "Which meeting?"}]
    london_time = datetime(2026, 12, 4, 14, tzinfo=ZoneInfo("Europe/London"))
    assert scheduling.exact_move_destination(state) == london_time
    state.current_request = "In London time, move it to 2026-12-04 at 2 pm."
    assert scheduling.exact_move_destination(state) == london_time


def test_duration_identifies_cancellation_and_ambiguous_or_abandoned_turns_cannot_commit():
    state = scheduling.Conversation(scheduling.CalClient("", ZoneInfo("America/New_York")))
    attendees = [{"name": "Maya Chen"}]
    for uid, weekday, day, minutes in [("short", "Monday", 23, 30), ("long", "Tuesday", 24, 60),
                                       ("other", "Wednesday", 25, 60)]:
        state.bookings[uid] = {"uid": uid, "attendees": attendees, "weekday": weekday, "duration_minutes": minutes,
                               "start": f"2026-11-{day}T14:00:00-05:00"}
    state.current_request = "cancel 60 minutes"
    assert "Several meetings match" in scheduling.check_original(state, "long")
    state.current_request = "Cancel the one-hour meeting on Tuesday because Monday already works."
    with pytest.raises(ValueError, match="matching booking UID is long"):
        scheduling.check_original(state, "short")
    assert scheduling.check_original(state, "long") is None
    assert scheduling.matching_originals(state, "Cancel the half an hour meeting") == ["short"]
    assert scheduling.matching_originals(state, "Cancel the 1 hour 30 minutes meeting") == []
    with pytest.raises(ValueError, match="one meeting length"):
        scheduling.matching_originals(state, "Cancel the 30 or 60 minute meeting")
    state.pending_cancellation = state.bookings["long"]
    assert "reason" in chat_turn(state, "yes") and state.pending_cancellation["uid"] == "long"
    assert "keep" in chat_turn(state, "Never mind, keep both.") and state.pending_cancellation is None
    for correction in ("Actually the 30-minute one instead", "Could you cancel Monday instead"):
        state.pending_cancellation = state.bookings["long"]
        scheduling.cancellation_followup(state, correction)
        assert state.pending_cancellation is None


def test_email_reuse_carries_only_the_requested_contact_and_cannot_confirm_the_old_preview():
    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"))
    state = scheduling.Conversation(calendar)
    plan = scheduling.BookingProposal(datetime(2026, 11, 25, 13, tzinfo=calendar.zone), 60,
                                      {"name": "Hellen", "email": "hellen@example.test"}, {"notes": "Hellen only"})
    state.booking_proposal = state.last_booking_preview = plan
    args = json.dumps({"previous_attendee": "Hellen", "new_attendee": "Maya Chen"})
    ctx = ToolContext(state, tool_name="reuse_preview_email", tool_call_id="contact", tool_arguments=args)
    for request in ("Give Maya Chen the same time as Hellen.", "Maya Chen's email is maya@example.test.",
                    "I don't have Maya Chen's email. Do not use Hellen's address."):
        state.current_request = request
        asyncio.run(scheduling.reuse_preview_email.on_invoke_tool(ctx, args))
        assert state.draft is None and state.booking_proposal is plan
    state.current_request = "For Maya Chen, use the email address from Hellen's preview."
    asyncio.run(scheduling.reuse_preview_email.on_invoke_tool(ctx, args))
    assert state.draft.attendee_name == "Maya Chen" and state.draft.attendee_email == plan.attendee["email"]
    assert state.draft.selected_start is None and state.draft.duration_minutes is None
    assert state.booking_proposal is None and state.last_booking_preview is plan and not state.last_write_receipt
    draft = state.draft
    draft.attendee_email, draft.duration_minutes, draft.selected_start = None, 30, utc_stamp(plan.start)
    state.current_request = "Use the same email address as Hellen."
    asyncio.run(scheduling.reuse_preview_email.on_invoke_tool(ctx, args))
    assert state.draft is draft and draft.attendee_email == plan.attendee["email"]
    assert draft.duration_minutes == 30 and draft.selected_start == utc_stamp(plan.start)
