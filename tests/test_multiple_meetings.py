"""Single- and multiple-appointment conversations with isolated Mailosaur test contacts."""
import asyncio
import json
from datetime import datetime, time, timedelta

import pytest
from agents.tool_context import ToolContext

import scheduling
from cal_client import CalendarError, instant, utc_stamp
from live_support import ask, live, time_in_reply, ui


with_private_contacts = pytest.mark.parametrize("live", ["multiple"], indirect=True)


@pytest.fixture
def week(live, monkeypatch, case):
    zone = live.calendar.zone
    today = datetime.now(zone).date()
    monday = today + timedelta(days=7 - today.weekday())
    move_slots = [(0, 9, 30, 30), (0, 14, 0, 60), (2, 9, 30, 30), (2, 14, 0, 60), (4, 10, 0, 30)]
    two_tuesday = [(1, 10, 0, 30), (1, 14, 0, 30)]
    required = {
        "H4": [*two_tuesday, (1, 15, 0, 30)], "H5": two_tuesday,
        "H5-edit": [*two_tuesday, (2, 10, 0, 30)],
        "H6": [*two_tuesday, (1, 16, 0, 30)], "H1": two_tuesday,
        "H2": [(d, 10, 0, 30) for d in (1, 3)], "H3": [(d, 10, 0, 30) for d in (1, 3, 4)],
        "H7": two_tuesday, "H8": two_tuesday, "H9": [(d, 10, 0, 30) for d in (0, 3, 4)],
        "H10": [(0, 16, 0, 30), *((d, h, 0, 30) for d in (0, 2) for h in (9, 10, 14))],
        "single-preview": [(1, h, m, 30) for h, m in ((9, 0), (10, 0), (11, 0), (13, 30), (14, 0))],
        "friday-calls": [(d, h, 0, 30) for d in (3, 4) for h in (10, 14)],
        "reference-time": [(1, 13, 0, 30), (2, 13, 0, 30), (2, 13, 0, 60)],
        "B8": [(1, 10, 0, 30), (3, 10, 0, 30)], "B9": [(1, 10, 0, 30), (1, 14, 0, 60), (1, 15, 0, 60)],
        "B10": [(1, 10, 0, 60), (1, 10, 30, 30), (1, 11, 0, 30)],
        "B11": [(1, 10, 0, 30), (3, 14, 0, 30), (3, 15, 0, 30)],
        "B12": [(1, 14, 0, 30), (1, 14, 30, 30), (1, 15, 0, 30), (1, 15, 30, 30)],
        "R6": move_slots, "R6-race": [*move_slots, (2, 15, 0, 60)], "R7": move_slots,
        "R8": [(d, 10, 0, 30) for d in (0, 2, 3, 4)],
        "C7": [(0, 9, 30, 30), (1, 14, 0, 30), (0, 10, 0, 30)],
        "C8": [(3, h, 0, 30) for h in (9, 11, 14)],
        "C9": [(1, 13, 0, 30), (2, 13, 0, 30)],
    }[case if not case.startswith("C8") else "C8"]
    for offset in range(6):
        start = datetime.combine(monday + timedelta(weeks=offset), time.min, zone)
        end = start + timedelta(days=5)
        if live.calendar.agenda(str(start.date()), str(end.date())):
            continue
        openings = {n: {instant(slot["start"]) for slot in live.calendar.slots(start, end, duration_minutes=n)}
                    for n in {row[3] for row in required}}
        if all(when(start, d, h, m) in openings[n] for d, h, m, n in required):
            break
    else:
        pytest.fail("No isolated live week with the required openings")

    class WeekClock(datetime):
        @classmethod
        def now(cls, tz=None):
            anchor = start.replace(hour=7)
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)
    monkeypatch.setattr(scheduling, "datetime", WeekClock)
    return start


def when(week, day, hour, minute=0):
    return week + timedelta(days=day, hours=hour, minutes=minute)


def seed(live, name, start, minutes=30, fields=None, *, setup=True):
    contact = {"name": name, "email": live.test_contacts[name], "timeZone": live.calendar.zone.key}
    if setup:
        return live.seed_booking(start, contact, fields, minutes)["uid"]
    return live.calendar.create_booking(utc_stamp(start), contact, fields, duration_minutes=minutes)["uid"]


def records(live, week):
    entries = live.calendar.agenda(str(week.date()), str((week + timedelta(days=4)).date()))
    return {b["uid"]: (b["attendees"][0]["name"], b["attendees"][0]["email"], instant(b["start"]),
                       scheduling.meeting_minutes(b)) for b in entries}


def writes(live):
    return [path for method, path in live.calls if method == "POST"]


def contacts(live):
    return f"Maya’s email is {live.test_contacts['Maya']}. Hellen’s is {live.test_contacts['Hellen']}."


@with_private_contacts
@pytest.mark.parametrize("case", ["reference-time"])
def test_book_at_another_attendees_clock_time(live, ui, week):
    tuesday, wednesday = when(week, 1, 13), when(week, 2, 13)
    email = live.test_contacts["Maya Chen"]
    live.test_contacts["Hellen Li"] = email
    hellen = seed(live, "Hellen Li", tuesday)
    live.calls.clear()
    assert "Hellen" in ask(ui, "What’s on my calendar this week?")
    ask(ui, "I need to book a meeting with Maya Chen this Wensday")
    state = ui.session_state["conversation"]
    assert state.draft and state.draft.attendee_name == "Maya Chen"
    ask(ui, f"same as Hellen, her email is {email}")
    reply = ask(ui, "I mean same as Hellen's time")
    proposal = state.booking_proposal
    assert proposal and "Confirm this booking" in reply, reply
    assert proposal.start == wednesday and proposal.minutes == 30
    assert (proposal.attendee["name"], proposal.attendee["email"]) == ("Maya Chen", email)
    assert "Wednesday" in reply and time_in_reply(reply, wednesday) and not writes(live), reply
    reply = ask(ui, "Actually, make Maya's meeting Tuesday at 1 PM instead.")
    assert "Hellen Li" in reply and "overlap" in reply.lower() and not state.booking_proposal, reply
    assert not writes(live) and set(records(live, week)) == {hellen}


def preview(live, ui, message, starts):
    before = writes(live)
    reply = ask(ui, message)
    for start in starts:
        assert time_in_reply(reply, start) and any(start.strftime(fmt) in reply
               for fmt in ("%A, %b %d", "%A, %B %d")), reply
    assert "confirm" in reply.lower(), reply
    assert writes(live) == before, reply
    return reply


def check_bookings(live, week, expected):
    current = list(records(live, week).values())
    assert len(current) == len(expected)
    for name, start, minutes in expected:
        assert (name, live.test_contacts[name], start, minutes) in current


def compete_after_first_write(live, ui, monkeypatch, operation, contested):
    """Create the competing appointment for real immediately after the first calendar receipt."""
    calendar = ui.session_state["conversation"].calendar
    actual = getattr(calendar, operation)
    raced = []

    def write_then_compete(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not raced:
            raced.append(seed(live, "Leo", contested, setup=False))
        return result
    monkeypatch.setattr(calendar, operation, write_then_compete)
    return raced


@pytest.mark.parametrize("case", ["B8", "B9", "B10", "B11", "B12"])
@with_private_contacts
def test_multiple_bookings(live, ui, week, monkeypatch, case):
    tue = when(week, 1, 10)
    thu = when(week, 3, 10)
    requests = {
        "B8": "I need half an hour with Maya Chen on Tuesday at 10 in the morning, and another half hour with her Thursday at the same time.",
        "B9": "Put Maya Chen down for Tuesday at 10 AM for half an hour. Then give Hellen an hour at 2 PM that afternoon.",
        "B10": "Can you book Maya for an hour at 10 tomorrow morning and Hellen for half an hour at 10:30?",
        "B11": "Book Maya on Tuesday at 10 AM and Hellen on Thursday at 2 PM, half an hour each.",
        "B12": "Can you get Maya and Hellen on my calendar Tuesday afternoon?",
    }
    first = ask(ui, requests[case])
    assert not writes(live), first
    if case == "B8":
        assert "email" in first.lower(), first
        preview(live, ui, f"Her email is {live.test_contacts['Maya']}.", [tue, thu])
        expected = [("Maya Chen", tue, 30), ("Maya Chen", thu, 30)]
    elif case == "B9":
        preview(live, ui, contacts(live), [tue, tue.replace(hour=14)])
        preview(live, ui, "Actually, make Hellen’s 3 PM. Maya’s time is fine.", [tue, tue.replace(hour=15)])
        expected = [("Maya Chen", tue, 30), ("Hellen", tue.replace(hour=15), 60)]
    elif case == "B10":
        assert "overlap" in first.lower() and "Maya" in first and "Hellen" in first, first
        ask(ui, "Oh, put Hellen at 11 then.")
        preview(live, ui, contacts(live), [tue, tue.replace(hour=11)])
        expected = [("Maya", tue, 60), ("Hellen", tue.replace(hour=11), 30)]
    elif case == "B11":
        contested = thu.replace(hour=14)
        preview(live, ui, contacts(live), [tue, contested])
        raced = compete_after_first_write(live, ui, monkeypatch, "create_booking", contested)
        assert not records(live, week)
        partial = ask(ui, "Confirm both.")
        assert raced and "Maya" in partial and "Hellen" in partial and "unavailable" in partial.lower(), partial
        check_bookings(live, week, [("Maya", tue, 30), ("Leo", contested, 30)])
        retry = preview(live, ui, "Okay, try 3 PM for Hellen. Leave Maya where she is.", [thu.replace(hour=15)])
        assert "Maya" not in retry, retry
        expected = [("Maya", tue, 30), ("Hellen", thu.replace(hour=15), 30), ("Leo", contested, 30)]
    else:
        assert "separate" in first.lower() and "?" in first, first
        options = ask(ui, "Two separate interviews, half an hour each. Back to back if you can. Maya first.")
        pairs = []
        slots = live.calendar.slots(tue.replace(hour=12), tue.replace(hour=17), duration_minutes=30)
        starts = {instant(s["start"]).astimezone(live.calendar.zone) for s in slots}
        for start in sorted(starts):
            follow = start + timedelta(minutes=30)
            if follow in starts and time_in_reply(options, start) and time_in_reply(options, follow):
                pairs.append((start, follow))
        assert len(pairs) >= 2 and not writes(live), options
        ask(ui, "The first pair works.")
        a, b = pairs[0]
        preview(live, ui, contacts(live), [a, b])
        expected = [("Maya", a, 30), ("Hellen", b, 30)]
    if case != "B11":
        assert not writes(live) and not records(live, week)
    confirmation = {"B8": "Yes, book both.", "B9": "That looks right. Book both.", "B10": "Book both.",
                    "B11": "Confirm.", "B12": "Book them."}[case]
    result = ask(ui, confirmation)
    created_now = 1 if case == "B11" else 2
    assert result.lower().count("booked the meeting") == created_now, result
    check_bookings(live, week, expected)
    assert writes(live).count("bookings") == len(expected)
    if case == "B8":
        before = writes(live)
        assert ask(ui, "Yes, book both.") == result and writes(live) == before


@pytest.mark.parametrize("case", ["R6", "R6-race", "R7", "R8"])
@with_private_contacts
def test_multiple_moves(live, ui, week, monkeypatch, case):
    maya = when(week, 0, 10 if case == "R8" else 9, 0 if case == "R8" else 30)
    hellen = when(week, 0, 14)
    wed_maya, wed_hellen = maya + timedelta(days=2), hellen + timedelta(days=2)
    first = seed(live, "Maya", maya)
    control = seed(live, "Maya" if case == "R8" else "Control", when(week, 4, 10))
    if case != "R8":
        second = seed(live, "Hellen", hellen, 60)
    if case == "R7":
        blocker = seed(live, "Leo", wed_hellen)
    originals = records(live, week)
    live.calls.clear()
    ask(ui, "What's on my calendar this week?")
    if case == "R8":
        preview(live, ui, "Move my Monday 10 with Maya to Wednesday at the same time.", [maya, wed_maya])
        ask(ui, "Confirm.")
        active = records(live, week)
        moved, = [uid for uid, row in active.items() if row[2] == wed_maya]
        thu = wed_maya + timedelta(days=1)
        preview(live, ui, "Actually, Thursday works better. Move the one we just changed, same time.", [wed_maya, thu])
        assert records(live, week) == active
        ask(ui, "Confirm.")
        assert writes(live) == [f"bookings/{uid}/reschedule" for uid in (first, moved)]
        check_bookings(live, week, [("Maya", thu, 30), ("Maya", when(week, 4, 10), 30)])
    else:
        if case == "R7":
            reply = ask(ui, "Move Maya and Hellen to Wednesday, same times.")
            assert "Leo" in reply and "overlap" in reply.lower() and not writes(live), reply
            revised = preview(live, ui, "Okay, just move Maya. Leave Hellen on Monday.", [maya, wed_maya])
            assert "Hellen" not in revised, revised
        else:
            reply = preview(live, ui, "Can you move both my Monday meetings to Wednesday? Same times as before.",
                            [maya, hellen, wed_maya, wed_hellen])
            assert "Monday" in reply and "Wednesday" in reply and "Hellen" in reply and "Maya" in reply
        if case == "R6-race":
            raced = compete_after_first_write(live, ui, monkeypatch, "reschedule_booking", wed_hellen)
        assert records(live, week) == originals and not writes(live)
        result = ask(ui, "Confirm." if case == "R7" else "Yes, move them both.")
        assert f"bookings/{first}/reschedule" in writes(live) and "Rescheduled" in result, result
        expected = [("Maya", wed_maya, 30), ("Control", when(week, 4, 10), 30)]
        if case == "R6":
            assert writes(live) == [f"bookings/{uid}/reschedule" for uid in (first, second)]
            expected.append(("Hellen", wed_hellen, 60))
        else:
            expected.extend([("Hellen", hellen, 60), ("Leo", wed_hellen, 30)])
            assert records(live, week)[second] == originals[second]
            if case == "R6-race":
                assert raced and "unavailable" in result.lower() and "Hellen" in result, result
                check_bookings(live, week, expected)
                retry = preview(live, ui, "Try Wednesday at 3 PM for Hellen instead.", [wed_hellen.replace(hour=15)])
                assert "Maya" not in retry
                before = writes(live)
                assert "Rescheduled" in ask(ui, "Confirm.")
                assert writes(live)[len(before):] == [f"bookings/{second}/reschedule"]
                expected.remove(("Hellen", hellen, 60))
                expected.append(("Hellen", wed_hellen.replace(hour=15), 60))
            else:
                assert records(live, week)[blocker] == originals[blocker]
        check_bookings(live, week, expected)
    assert records(live, week)[control] == originals[control]


@pytest.mark.parametrize("case", ["C7", "C8-confirm", "C8-abandon"])
@with_private_contacts
def test_direct_cancellations(live, ui, week, case):
    if case == "C7":
        a, b, other = when(week, 0, 9, 30), when(week, 1, 14), when(week, 0, 10)
    else:
        a, b, other = (when(week, 3, h) for h in (9, 11, 14))
    fields = live.calendar.meeting_fields({"purpose": "Candidate interview"}) if case.startswith("C8") else None
    targets = [seed(live, "Maya", a, fields=fields), seed(live, "Hellen", b, fields=fields)]
    control = seed(live, "Leo", other, fields=fields)
    originals = records(live, week)
    live.calls.clear()
    if case == "C7":
        reply = preview(live, ui, "Cancel my Monday 9:30 with Maya and my Tuesday 2 o’clock with Hellen. I'll be out of town.", [a, b])
        assert "out of town" in reply.lower(), reply
    else:
        agenda = ask(ui, "What interviews do I have on Thursday?")
        assert all(time_in_reply(agenda, start) for start in (a, b, other)), agenda
        ambiguous = ask(ui, "Cancel both of them.")
        assert "?" in ambiguous and not writes(live), ambiguous
        reply = preview(live, ui, "I meant the two in the morning. We filled the role.", [a, b])
        assert "filled the role" in reply.lower(), reply
    assert not writes(live) and records(live, week) == originals
    if case == "C8-abandon":
        ask(ui, "Actually, never mind.")
        assert not writes(live) and records(live, week) == originals
    else:
        result = ask(ui, "Yes, cancel both." if case == "C7" else "Yes, those two.")
        assert result.lower().count("cancelled") >= 2, result
        assert writes(live) == [f"bookings/{uid}/cancel" for uid in targets]
        assert records(live, week) == {control: originals[control]}


@with_private_contacts
@pytest.mark.parametrize("case", ["C9"])
def test_cancel_week_with_shared_reason(live, ui, week):
    starts = [when(week, 1, 13), when(week, 2, 13)]
    live.test_contacts["Hellen Li"] = live.test_contacts["Hellen"]
    uids = [seed(live, name, start) for name, start in zip(("Hellen Li", "Maya Chen"), starts)]
    live.calls.clear()
    reason = "I have already caught up with them"
    reply = preview(live, ui, f"ancel all the meetings this week, because {reason}", starts)
    assert f"Cancellation reason · {reason}" in reply and "Hellen Li" in reply and "Maya Chen" in reply, reply
    assert set(records(live, week)) == set(uids)
    result = ask(ui, "COnfirm")
    assert result.count("Cancelled ·") == 2, result
    expected = {f"bookings/{uid}/cancel": reason for uid in uids}
    assert live.cancel_reasons == expected
    assert sorted(writes(live)) == sorted(expected) and not records(live, week)
    assert ask(ui, "Confirm") == result and len(writes(live)) == 2


@pytest.mark.parametrize("missing_or_paraphrased", [None, "", "They have already met."])
def test_cancel_preview_can_repair_reason_on_the_same_agent_turn(missing_or_paraphrased, monkeypatch):
    from zoneinfo import ZoneInfo
    from meeting_batch import RequestedMeeting, batch_turn, prepare_meetings

    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    state.current_request = "ancel all my calls this week, because I’ve already caught up with them"
    monkeypatch.setattr(state.calendar, "cancel_booking", lambda *args: pytest.fail("Unconfirmed cancellation"))
    start = datetime(2026, 12, 1, 13, tzinfo=zone)
    rows = []
    for uid, name in (("a", "Hellen Li"), ("b", "Maya Chen")):
        state.bookings[uid] = {"uid": uid, "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat(),
                               "attendees": [{"name": name}]}
        rows.append(RequestedMeeting(name=name, booking_uid=uid, email=None, attendee_zone=None,
                                     local_start=None, minutes=None).model_dump())
        start += timedelta(days=1)

    def tool_call(reason):
        args = json.dumps({"action": "cancel", "items": rows, "cancellation_reason": reason})
        ctx = ToolContext(state, tool_name="prepare_meetings", tool_call_id="preview", tool_arguments=args)
        return asyncio.run(prepare_meetings.on_invoke_tool(ctx, args))

    assert "retry" in tool_call(missing_or_paraphrased)
    assert not state.turn_reply and not state.batch.preview
    assert "complete preview" in batch_turn(state, "COnfirm", start)
    single = json.dumps({"booking_uid": "a", "reason": None})
    ctx = ToolContext(state, tool_name="cancel_booking", tool_call_id="single", tool_arguments=single)
    assert "shared preview" in asyncio.run(scheduling.cancel_booking.on_invoke_tool(ctx, single))
    reply = tool_call("because i've already caught up with them")
    assert "Cancellation reason · I’ve already caught up with them" in reply
    assert state.batch.preview[0].reason == "I’ve already caught up with them"


def test_shared_reason_correction_replaces_or_withdraws_earlier_text():
    from meeting_batch import verified_cancellation_reason
    turns = ["Cancel both because I am traveling.", "their reasons are: Already caught up"]
    for incorrect in (None, "I am traveling."):
        with pytest.raises(ValueError, match="retry"):
            verified_cancellation_reason(incorrect, turns, cancel_only=True)
    assert verified_cancellation_reason("already caught up", turns, cancel_only=True) == "Already caught up"
    turns.append("Actually, leave the reason blank.")
    assert verified_cancellation_reason("Already caught up", turns, cancel_only=True) is None


def test_batch_booking_preview_uses_the_named_contacts(monkeypatch):
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, RequestedMeeting, build_preview

    calendar = scheduling.CalClient("", ZoneInfo("America/New_York"), 30)
    state = scheduling.Conversation(calendar, batch=MeetingBatch("book", 0))
    state.current_request = ("Book an Intro with Maya Chen, maya@example.test, at 10 AM "
                             "and another with Nora Hall (nora@example.test) at 2 PM on December 1.")
    maya = RequestedMeeting(name="Intro", email="maya@example.test", attendee_zone=calendar.zone.key,
                            local_start="2026-12-01T10:00", minutes=30, booking_uid=None)
    nora = maya.model_copy(update={"name": "Nora Hall", "email": "nora@example.test", "local_start": "2026-12-01T14:00"})
    monkeypatch.setattr(calendar, "event_durations", lambda: (30, [30]))
    monkeypatch.setattr(calendar, "upcoming_meetings_with", lambda name, email: [])
    monkeypatch.setattr(calendar, "slots", lambda start, end, **kwargs: [{"start": utc_stamp(start), "end": utc_stamp(end)}])

    reply = build_preview(state, [maya, nora], None)
    assert "Maya Chen · maya@example.test" in reply and "Nora Hall · nora@example.test" in reply
    assert [(p.attendee["name"], p.attendee["email"]) for p in state.batch.preview] == [
        ("Maya Chen", "maya@example.test"), ("Nora Hall", "nora@example.test")]

    state.messages.append({"role": "user", "content": state.current_request})
    state.current_request = "Actually, her name is Maya Cheng. Same email."
    corrected = maya.model_copy(update={"name": "Maya Cheng"})
    assert "Maya Cheng · maya@example.test" in build_preview(state, [corrected, nora], None)
    state.current_request = "Move Maya's time to 11 AM."
    wrong_name = maya.model_copy(update={"name": "Intro", "local_start": "2026-12-01T11:00"})
    assert "Maya Cheng · maya@example.test" in build_preview(state, [wrong_name, nora], None)


def test_one_row_sent_to_the_batch_tool_keeps_the_single_draft():
    from meeting_batch import RequestedMeeting, prepare_meetings
    draft = scheduling.BookingDraft(attendee_name="Maya", attendee_email="maya@example.test")
    state = scheduling.Conversation(calendar=None, draft=draft)
    row = RequestedMeeting(name="Maya", email=draft.attendee_email, local_start="2026-12-01T10:00",
                           attendee_zone=None, minutes=30, booking_uid=None)
    args = json.dumps({"action": "book", "items": [row.model_dump()], "cancellation_reason": None})
    ctx = ToolContext(state, tool_name="prepare_meetings", tool_call_id="one-row", tool_arguments=args)
    result = asyncio.run(prepare_meetings.on_invoke_tool(ctx, args))
    assert "single-meeting" in result and not state.turn_reply  # The same agent turn can recover.
    assert state.batch is None and state.draft is draft and state.last_write_receipt is None


def test_partial_retry_cannot_restore_a_successful_attendee_or_write_without_preview():
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, RequestedMeeting, batch_turn, build_preview
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    item = RequestedMeeting(name="Hellen", email="hellen@example.test", attendee_zone=None, booking_uid=None,
                            local_start="2026-12-03T15:00", minutes=30)
    state.batch = MeetingBatch("book", 0, items=[item], retry_only=True)
    state.current_request = "Try three for Hellen."
    assert "remaining meeting" in build_preview(state, [item.model_copy(update={"name": "Maya"}), item], None)
    assert not state.batch.preview
    assert "complete preview" in batch_turn(state, "Confirm both.", datetime.now(zone))


def test_cancelling_a_booking_preview_cannot_create_invitations():
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, batch_turn
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    state.batch = MeetingBatch("book", 0)
    start = datetime(2026, 12, 1, 10, tzinfo=zone)
    state.batch.preview = [scheduling.BookingProposal(start, 30, {"name": "Maya"}, {})]
    assert "Discarded" in batch_turn(state, "Cancel both.", start)
    assert state.batch is None and state.last_write_receipt is None


def test_time_only_edit_preserves_verified_rows_even_if_planner_swaps_addresses():
    from meeting_batch import RequestedMeeting, carry_booking_details
    maya = RequestedMeeting(name="Maya Chen", email="maya@example.test", attendee_zone="America/New_York",
                            local_start="2026-12-01T10:00", minutes=30, booking_uid=None)
    hellen = maya.model_copy(update={"name": "Hellen", "email": "hellen@example.test",
                                     "local_start": "2026-12-01T14:00", "minutes": 60})
    wrong_maya = maya.model_copy(update={"name": "Maya", "email": hellen.email, "local_start": "2026-12-01T11:00"})
    new_hellen = hellen.model_copy(update={"email": maya.email, "local_start": "2026-12-01T15:00"})
    for proposed in ([wrong_maya, new_hellen], [new_hellen]):
        fixed = carry_booking_details([maya, hellen], proposed, "Actually, make Hellen’s 3 PM. Maya’s time is fine.")
        assert fixed == [maya, hellen.model_copy(update={"local_start": "2026-12-01T15:00"})]


def test_two_clocks_require_a_batch_and_a_narrowing_changes_only_the_preview():
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, RequestedMeeting, build_preview
    request = "Cancel my 9:30 and 10:00 meetings on Monday."
    assert scheduling.has_multiple_cancellation_targets(request)
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    state.batch = MeetingBatch("cancel", 0)
    items = []
    for uid, name, hour, minute in [("first", "Maya", 9, 30), ("second", "Leo", 10, 0)]:
        start = datetime(2026, 11, 30, hour, minute, tzinfo=zone)
        state.bookings[uid] = {"uid": uid, "title": "Meeting", "weekday": "Monday", "attendees": [{"name": name}],
                               "start": start.isoformat(), "end": (start + timedelta(minutes=30)).isoformat()}
        items.append(RequestedMeeting(name=name, booking_uid=uid, email=None, attendee_zone=None, local_start=None, minutes=None))
    state.current_request = request
    assert "Confirm" in build_preview(state, items, None)
    state.messages = [{"role": "user", "content": request}]
    state.current_request = "their reasons are: Already caught up"
    assert "Cancellation reason · Already caught up" in build_preview(state, items, "Already caught up")
    state.messages.append({"role": "user", "content": state.current_request})
    state.current_request = "Actually, leave the reason blank."
    reply = build_preview(state, items, "Already caught up")
    assert "Confirm" in reply and "Cancellation reason" not in reply
    assert [r["uid"] for r in state.batch.preview[0].cancel] == ["first", "second"]
    state.messages.append({"role": "user", "content": state.current_request})
    state.current_request = "Actually, just the 9:30 one."
    state.batch.preview.clear()
    assert "Which" in build_preview(state, items[1:], None) and not state.batch.preview
    assert "Confirm" in build_preview(state, items[:1], None)
    assert [r["uid"] for r in state.batch.preview[0].cancel] == ["first"]
    state.batch.items = []
    state.batch.preview.clear()
    state.messages[0]["content"] = "Cancel both of them."
    assert "Which" in build_preview(state, items[1:], None) and not state.batch.preview
    state.batch.items = items
    state.current_request = "Actually, just Leo."
    assert "Which" in build_preview(state, items[:1], None) and not state.batch.preview
    assert "Confirm" in build_preview(state, items[1:], None)


@with_private_contacts
@pytest.mark.parametrize("case", ["C7"], ids=["two-clocks-and-a-correction"])
@pytest.mark.parametrize("protect_other", [False, True], ids=["narrow", "keep-other"])
def test_two_clocks_then_cancel_only_one(live, ui, week, protect_other):
    first = when(week, 0, 9, 30)
    second = when(week, 0, 10)
    target = seed(live, "Maya", first)
    keep = seed(live, "Hellen", second)
    original = records(live, week)
    live.calls.clear()
    ask(ui, "What's on my calendar this week?")
    preview(live, ui, "Cancel my 9:30 and 10:00 meetings on Monday.", [first, second])
    if protect_other:
        revised = preview(live, ui, "Please keep the 10:00 AM meeting and cancel the 9:30 AM one because I'll be out of town.",
                          [first, second])
        protected, cancelled = revised.split("**Cancel**")
        assert time_in_reply(protected, second) and time_in_reply(cancelled, first) and "Hellen" not in cancelled, revised
    else:
        revised = preview(live, ui, "Actually, just the 9:30 one. I'll be out of town.", [first])
        assert "Hellen" not in revised, revised
    assert "out of town" in revised.lower(), revised
    assert records(live, week) == original and not writes(live)
    assert "Cancelled" in ask(ui, "Confirm.")
    assert writes(live) == [f"bookings/{target}/cancel"]
    assert records(live, week) == {keep: original[keep]}


@with_private_contacts
@pytest.mark.parametrize("case", ["H4", "H5", "H6", "H1"])
def test_booking_decision_at_preview(live, ui, week, case):
    maya, hellen = when(week, 1, 10), when(week, 1, 14)
    if case == "H6":
        seed(live, "Leo", when(week, 1, 16))
    original = records(live, week)
    live.calls.clear()
    preview(live, ui, "Book Maya Tuesday at 10 AM and Hellen Tuesday at 2 PM, half an hour each. " + contacts(live),
            [maya, hellen])
    if case == "H4":
        hellen = hellen.replace(hour=15)
        revised = preview(live, ui, "Yes, but make Hellen’s 3 instead.", [maya, hellen])
        assert not time_in_reply(revised, hellen.replace(hour=14)), revised
    if case == "H5":
        revised = preview(live, ui, "Go ahead with Maya. I’m still waiting to hear from Hellen.", [maya])
        assert "Hellen" in revised and not time_in_reply(revised, hellen), revised
        assert records(live, week) == original
        result = ask(ui, "Confirm.")
        assert result.lower().count("booked the meeting") == 1, result
        check_bookings(live, week, [("Maya", maya, 30)])
        preview(live, ui, "Hellen got back to me. Let's go ahead with her original time.", [hellen])
    if case == "H6":
        agenda = ask(ui, "Before I confirm, what else do I have that afternoon?")
        assert "Leo" in agenda and time_in_reply(agenda, when(week, 1, 16)), agenda
        assert not writes(live) and records(live, week) == original
    if case != "H5":
        assert not writes(live) and records(live, week) == original
    result = ask(ui, "Please finalize both appointments." if case == "H1" else "Confirm.")
    assert result.lower().count("booked the meeting") == (1 if case == "H5" else 2), result
    expected = [("Maya", maya, 30), ("Hellen", hellen, 30)]
    if case == "H6":
        expected.append(("Leo", when(week, 1, 16), 30))
    assert writes(live) == ["bookings", "bookings"]
    check_bookings(live, week, expected)


@with_private_contacts
@pytest.mark.parametrize("case", ["H2", "H3"])
def test_two_appointments_one_attendee(live, ui, week, case):
    tue, thu = when(week, 1, 10), when(week, 3, 10)
    if case == "H3":
        seed(live, "Maya Chen", when(week, 4, 10))
    original = records(live, week)
    live.calls.clear()
    first = f"Book two meetings with Maya Chen, half an hour each. Her email is {live.test_contacts['Maya Chen']}. "
    if case == "H2":
        reply = ask(ui, first + "Tuesday at ten in the morning. I'll give you Thursday's time next.")
        assert ("thursday" in reply.lower() or str(thu.date()) in reply) and "?" in reply, reply
        assert all(term not in reply.lower() for term in ("length", "how long", "duration", "email address")), reply
        message = "Give Thursday the Tuesday start as well."
    else:
        message = first + "Tuesday at 10 AM and Thursday at the same time."
    reply = preview(live, ui, message, [tue, thu])
    if case == "H3":
        assert reply.lower().count("already have an upcoming meeting") == 1 and "Fri" in reply, reply
    assert not writes(live) and records(live, week) == original
    result = ask(ui, "Confirm.")
    assert result.lower().count("booked the meeting") == 2, result
    expected = [("Maya Chen", tue, 30), ("Maya Chen", thu, 30)]
    if case == "H3":
        expected.append(("Maya Chen", when(week, 4, 10), 30))
    check_bookings(live, week, expected)


@with_private_contacts
@pytest.mark.parametrize("case", ["H7"])
def test_two_people_named_alex(live, ui, week):
    a, b = when(week, 1, 10), when(week, 1, 14)
    first_email, second_email = live.test_contacts["Maya"], live.test_contacts["Hellen"]
    live.additional_contacts.update({("Alex Lee", first_email), ("Alex Lee", second_email)})
    uids = [live.seed_booking(start, {"name": "Alex Lee", "email": address,
            "timeZone": live.calendar.zone.key})["uid"] for start, address in ((a, first_email), (b, second_email))]
    original = records(live, week)
    live.calls.clear()
    ask(ui, "What's on my calendar this week?")
    reply = ask(ui, "Cancel my meeting with Alex Lee.")
    assert "?" in reply and all(time_in_reply(reply, x) for x in (a, b)), reply
    assert not writes(live) and records(live, week) == original
    result = ask(ui, f"Yes, cancel Alex at {second_email} because we finished early.")
    if not writes(live) and "confirm" in result.lower():
        result = ask(ui, "Confirm.")
    assert "cancelled" in result.lower() and writes(live) == [f"bookings/{uids[1]}/cancel"], result
    assert records(live, week) == {uids[0]: original[uids[0]]}


@with_private_contacts
@pytest.mark.parametrize("case", ["H8"])
def test_shared_call_choice(live, ui, week):
    reply = ask(ui, "Can you get Maya and Hellen on my calendar Tuesday afternoon?")
    assert "separate" in reply.lower() and "?" in reply, reply
    reply = ask(ui, "Together. I want Maya and Hellen on the same call.")
    # This version of the app supports one invitee per appointment.
    assert "one attendee per appointment" in reply.lower() and "shared call" in reply.lower(), reply
    assert not writes(live) and not records(live, week)


@with_private_contacts
@pytest.mark.parametrize("case", ["H9"])
def test_move_and_cancel_in_one_request(live, ui, week):
    mon, thu, fri = [when(week, d, 10) for d in (0, 3, 4)]
    maya, hellen = seed(live, "Maya", mon), seed(live, "Hellen", thu)
    original = records(live, week)
    live.calls.clear()
    ask(ui, "What's my schedule this week?")
    reply = preview(live, ui, "Move Maya to Friday and cancel Hellen’s Thursday interview. Keep Maya’s time. Hellen can't attend.",
                    [mon, thu, fri])
    assert any("Move" in line and "Maya" in line for line in reply.splitlines()), reply
    assert any("Cancel" in line and "Hellen" in line for line in reply.splitlines()), reply
    assert not writes(live) and records(live, week) == original
    result = ask(ui, "Confirm.")
    assert "rescheduled" in result.lower() and "cancelled" in result.lower(), result
    assert sorted(writes(live)) == sorted([f"bookings/{maya}/reschedule", f"bookings/{hellen}/cancel"])
    check_bookings(live, week, [("Maya", fri, 30)])


@with_private_contacts
@pytest.mark.parametrize("case", ["H10"])
def test_move_all_except_leo(live, ui, week):
    names = ["Maya", "Leo", "Hellen"]
    old = [when(week, 0, h) for h in (9, 10, 14)]
    fields = live.calendar.meeting_fields({"purpose": "Candidate interview"})
    uids = [seed(live, name, start, fields=fields) for name, start in zip(names, old)]
    control = seed(live, "Control", when(week, 0, 16))
    original = records(live, week)
    live.calls.clear()
    ask(ui, "Show my interviews this week.")
    wed = [old[i] + timedelta(days=2) for i in (0, 2)]
    reply = preview(live, ui, "Move my three Monday interviews to Wednesday, except the one with Leo. Same times.",
                    [old[0], old[2], *wed])
    assert any("Leo" in line and any(term in line.lower() for term in ("stay", "keep", "unchanged")) for line in reply.splitlines()), reply
    assert not writes(live) and records(live, week) == original
    result = ask(ui, "Confirm.")
    assert result.lower().count("rescheduled") == 2, result
    assert set(writes(live)) == {f"bookings/{uids[i]}/reschedule" for i in (0, 2)}
    assert records(live, week)[uids[1]] == original[uids[1]]
    assert records(live, week)[control] == original[control]
    check_bookings(live, week, [("Maya", wed[0], 30), ("Leo", old[1], 30), ("Hellen", wed[1], 30),
                                ("Control", when(week, 0, 16), 30)])


def test_mixed_actions_require_a_matching_attendee_for_each_original():
    from meeting_batch import mixed_targets

    state = scheduling.Conversation(calendar=None)
    for uid, name, day, weekday in [("maya", "Maya", 1, "Tuesday"), ("hellen", "Hellen", 3, "Thursday")]:
        state.bookings[uid] = {"attendees": [{"name": name}], "start": f"2026-12-{day:02d}T10:00:00-05:00",
                               "weekday": weekday, "duration_minutes": 30}
    assert mixed_targets(state, "Move Maya to Friday and cancel Hellen’s Thursday interview.") == {"maya": "move", "hellen": "cancel"}
    assert mixed_targets(state, "Move Maya to Friday and cancel Zoe’s Thursday interview.") == {}
    assert mixed_targets(state, "Move Maya to Friday and cancel Maya’s Tuesday interview.") == {}
    state.bookings["maya"]["attendees"] = [{"name": "Alex Lee", "email": "lee@example.test"}]
    assert mixed_targets(state, "Move Alex Chen’s Tuesday interview to Friday and cancel Hellen’s Thursday interview.") == {}
    assert mixed_targets(state, "Move Alex Lee at other@example.test to Friday and cancel Hellen’s Thursday interview.") == {}
    assert mixed_targets(state, "Move Alex Lee to Friday and cancel Hellen’s Thursday interview.") == {"maya": "move", "hellen": "cancel"}


def test_failed_selected_booking_keeps_the_other_appointment_on_hold(monkeypatch):
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, RequestedMeeting, commit

    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    maya = RequestedMeeting(name="Maya", email="maya@example.test", attendee_zone=zone.key,
                            local_start="2026-12-01T10:00", minutes=30, booking_uid=None)
    hellen = maya.model_copy(update={"name": "Hellen", "email": "hellen@example.test", "local_start": "2026-12-01T14:00"})
    plan = scheduling.BookingProposal(datetime(2026, 12, 1, 10, tzinfo=zone), 30, {"name": "Maya"}, {})
    batch = state.batch = MeetingBatch("book", 0, items=[maya], preview=[plan], held=[hellen])
    monkeypatch.setattr(scheduling, "confirm_booking", lambda state, plan: "That time is unavailable.")
    result = commit(state, batch)
    assert "unavailable" in result and "Still pending · Hellen" in result
    assert batch.items == [maya] and batch.held == [hellen] and not batch.preview


@with_private_contacts
@pytest.mark.parametrize("case", ["H5-edit"])
def test_held_booking_with_weekday_edit(live, ui, week):
    tue, wed, hellen = when(week, 1, 10), when(week, 2, 10), when(week, 1, 14)
    preview(live, ui, "Book Maya Tuesday at 10 AM and Hellen Tuesday at 2 PM, half an hour each. " + contacts(live), [tue, hellen])
    reply = preview(live, ui, "Go ahead with Maya on Wednesday. I'm still waiting to hear from Hellen.", [wed])
    assert "On hold · Hellen" in reply and "Tuesday" not in reply, reply
    assert not writes(live) and not records(live, week)
    result = ask(ui, "Confirm.")
    assert result.lower().count("booked the meeting") == 1, result
    check_bookings(live, week, [("Maya", wed, 30)])


@pytest.mark.parametrize("message", [
    "What else is on my calendar that afternoon? Hold off on Hellen.",
    "What else is on my calendar that afternoon? I'm still waiting to hear from Hellen.",
    "I'm still waiting on Hellen. What else do I have that afternoon?",
])
def test_agenda_question_with_a_hold_invalidates_the_previous_confirmation(message):
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, batch_turn
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 12, 1, 8, tzinfo=zone)
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    state.batch = MeetingBatch("book", 0, preview=[scheduling.BookingProposal(now, 30, {"name": name}, {})
                                                   for name in ("Maya", "Hellen")])
    answer = batch_turn(state, message, now)
    assert "hold" in answer.lower() and not state.batch.preview
    assert "complete preview" in batch_turn(state, "Confirm.", now) and state.last_write_receipt is None


@pytest.mark.parametrize("include_incomplete_row", [False, True])
def test_one_known_row_in_a_two_day_request_is_kept_for_clarification(include_incomplete_row):
    from zoneinfo import ZoneInfo
    from meeting_batch import MeetingBatch, RequestedMeeting, build_preview
    zone = ZoneInfo("America/New_York")
    state = scheduling.Conversation(scheduling.CalClient("", zone))
    state.current_request = "Two meetings with Maya, half an hour each. Tuesday at 10. What about Thursday?"
    state.batch = MeetingBatch("book", 0)
    tue = RequestedMeeting(name="Maya", email="maya@example.test", attendee_zone=zone.key,
                           local_start="2026-12-01T10:00", minutes=30, booking_uid=None)
    rows = [tue, tue.model_copy(update={"local_start": None})] if include_incomplete_row else [tue]
    reply = build_preview(state, rows, None)
    assert "start time" in reply and "Thursday" in reply and "length" not in reply
    assert state.batch.partial_booking == tue and not state.batch.preview


@with_private_contacts
@pytest.mark.parametrize("case", ["single-preview"])
@pytest.mark.parametrize("wording", ["comparison", "fresh"])
@pytest.mark.parametrize("reject", [False, True], ids=["accepted", "declined"])
def test_one_booking_with_shared_zone(live, ui, week, monkeypatch, wording, reject, case):
    start = when(week, 1, 13, 30)
    email = live.test_contacts["Maya Chen"]
    if wording == "comparison":
        message = (f"Book a 30-minute Intro (event type {live.calendar.event_type_id}) with Maya Chen, {email}, "
                   "for project planning on Tuesday at half past one in the afternoon. "
                   "Her time zone and mine are both America/New_York.")
    else:
        message = (f"Maya Chen ({email}) and I are both on Eastern time. Please pencil in a half-hour "
                   "for the two of us this Tuesday at 1:30 PM to discuss project planning.")
    reply = preview(live, ui, message, [start])
    state = ui.session_state["conversation"]
    plan = state.booking_proposal
    assert state.batch is None and plan and plan.start == start and plan.minutes == 30, reply
    assert (plan.attendee["name"], plan.attendee["email"]) == ("Maya Chen", email)
    assert any("project planning" in value.lower() for value in plan.fields.values()), reply
    rejections = []
    if reject:
        # Race the real POST after the assistant's final checks, so Cal.com itself rejects it.
        create = state.calendar.create_booking

        def create_after_competitor(*args, **kwargs):
            seed(live, "Control", start)
            try:
                return create(*args, **kwargs)
            except CalendarError as error:
                rejections.append(error.status_code)
                raise

        monkeypatch.setattr(state.calendar, "create_booking", create_after_competitor)
    reply = ask(ui, "Confirm")
    if reject:
        assert rejections and rejections[0] in (400, 409, 422), reply
        assert "unavailable" in reply.lower() and state.last_write_receipt is None, reply
        check_bookings(live, week, [("Control", start, 30)])
    else:
        assert writes(live) == ["bookings"] and "booked" in reply.lower(), reply
        check_bookings(live, week, [("Maya Chen", start, 30)])


@with_private_contacts
@pytest.mark.parametrize("case", ["single-preview"])
@pytest.mark.parametrize("wording", ["comparison", "fresh"])
def test_named_agenda_and_occupied_slot_are_read_only(live, ui, week, wording, case):
    target, control = when(week, 1, 10), when(week, 1, 11)
    maya = seed(live, "Maya Chen", target)
    other = seed(live, "Leo", control)
    email = live.test_contacts["Maya Chen"]
    if wording == "comparison":
        agenda = f"List Maya Chen's meetings on Tuesday. Her email is {email}. Give the title and local start time in New York."
        availability = f"Is 10:00–10:30 AM on Tuesday available for a 30-minute Intro with Maya Chen, {email}? Only check availability."
    else:
        agenda = f"Which Tuesday appointment belongs to Maya Chen ({email})? Tell me what it's called and when it starts here."
        availability = "We're both free Friday, but could you first check whether Tuesday at ten AM is open for half an hour? Just checking."
    live.calls.clear()
    reply = ask(ui, agenda)
    assert "Maya" in reply and time_in_reply(reply, target) and "Leo" not in reply, reply
    assert ("GET", "bookings") in live.calls and not writes(live)
    live.calls.clear()
    reply = ask(ui, availability)
    assert ("GET", "slots") in live.calls and time_in_reply(reply, target), reply
    assert any(word in reply.lower() for word in ("overlap", "unavailable", "not available", "occupied")), reply
    state = ui.session_state["conversation"]
    assert not writes(live) and not state.batch and not state.booking_proposal and not state.move_draft
    assert set(records(live, week)) == {maya, other}


@with_private_contacts
@pytest.mark.parametrize("case", ["single-preview"])
@pytest.mark.parametrize("wording", ["comparison", "fresh"])
def test_two_times_one_move(live, ui, week, wording, case):
    original, destination = when(week, 1, 10), when(week, 1, 14)
    uid = seed(live, "Maya Chen", original)
    control = seed(live, "Leo", when(week, 1, 11))
    live.calls.clear()
    email = live.test_contacts["Maya Chen"]
    if wording == "comparison":
        message = (f"Move Maya Chen's 30-minute meeting from 10:00 AM to 2:00 PM on Tuesday. "
                   f"Both times are America/New_York. Her email is {email}.")
    else:
        message = (f"Maya Chen ({email}) and I want our Tuesday ten o'clock call four hours later. "
                   "We're both in New York. Can you shift it?")
    reply = preview(live, ui, message, [original, destination])
    state = ui.session_state["conversation"]
    move = state.move_draft
    assert not state.batch and move and move.awaiting_confirmation, reply
    assert move.original["uid"] == uid and move.start == destination and move.minutes == 30
    result = ask(ui, "Confirm")
    assert writes(live) == [f"bookings/{uid}/reschedule"] and "rescheduled" in result.lower(), result
    assert control in records(live, week)
    check_bookings(live, week, [("Maya Chen", destination, 30), ("Leo", when(week, 1, 11), 30)])


@with_private_contacts
@pytest.mark.parametrize("case", ["friday-calls"])
@pytest.mark.parametrize("message", [
    "Move both of my Friday calls back to Thursday. Keep each one's time of day.",
    "My Friday calls need to happen a day earlier. Same time for each, please.",
], ids=["both-calls", "a-day-earlier"])
def test_move_both_friday_calls(live, ui, week, case, message):
    originals = [when(week, 4, h) for h in (10, 14)]
    destinations = [when(week, 3, h) for h in (10, 14)]
    uids = {seed(live, name, start) for name, start in zip(("Maya", "Hellen"), originals)}
    live.calls.clear()
    reply = preview(live, ui, message, [*originals, *destinations])
    state = ui.session_state["conversation"]
    assert state.batch and {p.original["uid"]: p.start for p in state.batch.preview} == {
        uid: row[2] - timedelta(days=1) for uid, row in records(live, week).items() if uid in uids}, reply
    assert not state.move_draft and set(records(live, week)) == uids and not writes(live)


@with_private_contacts
@pytest.mark.parametrize("case", ["reference-time"])
@pytest.mark.parametrize("split_contact", [False, True], ids=["same-time-followup", "same-email-followup"])
def test_use_time_from_hellens_unconfirmed_preview(live, ui, week, case, split_contact):
    start = when(week, 2, 13)
    email = live.test_contacts["Hellen"]
    reply = preview(live, ui, f"Book an hour with Hellen on Wednesday at 1 PM. Her email is {email}.", [start])
    state = ui.session_state["conversation"]
    assert state.booking_proposal.minutes == 60 and state.booking_proposal.attendee["email"] == email, reply
    if split_contact:
        question = ask(ui, "Book a new 30-minute meeting with Maya Chen on Wednesday at the same time as Hellen.")
        assert "email" in question.lower() and state.booking_proposal is None, question
        reply = ask(ui, "Use the same email address as Hellen.")
    else:
        ask(ui, "Make a new 30-minute appointment on Wednesday for Maya Chen using the same email address as Hellen.")
        reply = ask(ui, "I mean same as Hellen's time.")
    plan = state.booking_proposal
    assert plan and plan.start == start and plan.minutes == 30 and "Confirm this booking" in reply, reply
    assert (plan.attendee["name"], plan.attendee["email"]) == ("Maya Chen", email)
    assert not state.batch and not writes(live) and not records(live, week)


@with_private_contacts
@pytest.mark.parametrize("case", ["single-preview"])
@pytest.mark.parametrize("wording", ["comparison", "fresh"])
def test_cancel_one_of_two_with_the_same_attendee(live, ui, week, wording, case):
    first, second, other = (when(week, 1, h) for h in (10, 14, 11))
    target = seed(live, "Maya Chen", first)
    keep = seed(live, "Maya Chen", second)
    control = seed(live, "Leo", other)
    before = records(live, week)
    live.calls.clear()
    email = live.test_contacts["Maya Chen"]
    if wording == "comparison":
        ambiguous = f"Cancel one of my Tuesday meetings with Maya Chen ({email})."
        selected = f"Cancel Maya Chen's Tuesday meeting at 10:00 AM New York time. Her email is {email}."
    else:
        ambiguous = f"Maya Chen ({email}) has two calls with me Tuesday and I need to drop one."
        selected = "It's the ten in the morning with Maya that we're calling off. The afternoon one stays."
    reply = ask(ui, ambiguous)
    state = ui.session_state["conversation"]
    assert "?" in reply and not writes(live) and not state.batch and records(live, week) == before, reply
    reply = ask(ui, selected + " Because we already covered the agenda.")
    if state.cancellation_proposal:
        assert [row["uid"] for row in state.cancellation_proposal.cancel] == [target] and not writes(live), reply
        reply = ask(ui, "Confirm.")
    assert "cancelled" in reply.casefold() and writes(live) == [f"bookings/{target}/cancel"], reply
    assert records(live, week) == {key: before[key] for key in (keep, control)}


@with_private_contacts
@pytest.mark.parametrize("case", ["single-preview"])
def test_missing_spoken_time_leaves_both_meetings_active(live, ui, week, case):
    seed(live, "Maya Chen", when(week, 1, 9))
    seed(live, "Maya Chen", when(week, 1, 14))
    before = records(live, week)
    live.calls.clear()
    ask(ui, "What calls do I have on Tuesday?")
    reply = ask(ui, "Cancel the ten in the morning with Maya on Tuesday. The afternoon one stays.")
    state = ui.session_state["conversation"]
    assert not state.cancellation_proposal and not (state.batch and state.batch.preview), reply
    assert not writes(live) and records(live, week) == before, reply
