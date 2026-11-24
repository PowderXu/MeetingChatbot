"""Shared live calendar fixtures and conversation checks."""

import os
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from time import sleep
from uuid import uuid4
from unittest.mock import patch

import httpx
import pytest
from dotenv import load_dotenv
from streamlit.testing.v1 import AppTest

from cal_client import CalClient, CalendarError, instant, utc_stamp
from scheduling import new_conversation
import scheduling


load_dotenv(".env")


@contextmanager
def chat_clock_for_tomorrow(day, zone):
    """Keep 'tomorrow' relative to the chosen isolated live day if actual tomorrow is occupied."""
    now = datetime.now(zone)
    if day == now.date() + timedelta(days=1):
        yield
        return
    anchor = datetime.combine(day - timedelta(days=1), time(7), zone)

    class ChatClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)

    with patch.object(scheduling, "datetime", ChatClock):
        yield


class LiveRun:
    def __init__(self, calendar, email, label):
        self.calendar = calendar
        token = uuid4().hex[:8]
        self.name = f"{label} {token}"
        # Separate fresh Round 2 setups from recently cancelled slots and their
        # cached availability. The Round 1 tomorrow scenarios need day one.
        self.days_ahead = 1 if label == "Round One" else 2 + int(token[:2], 16) % 20
        self.attendee = {"name": self.name, "email": email, "timeZone": calendar.zone.key}
        self.owned_uids = set()
        self.calls = []
        self.test_days = set()
        self.move_queries = []
        self.slot_queries = []
        self.cancel_reasons = {}
        self.pages = []
        self.test_contacts = {}
        self.additional_contacts = set()

    def openings(self, minimum=4):
        first = datetime.now(self.calendar.zone).date() + timedelta(days=self.days_ahead)
        start = datetime.combine(first, time.min, self.calendar.zone)
        groups = defaultdict(list)
        for slot in self.calendar.slots(start, start + timedelta(days=14)):
            stamp = instant(slot["start"]).astimezone(self.calendar.zone)
            if not groups[stamp.date()] or stamp >= instant(groups[stamp.date()][-1]["end"]):
                groups[stamp.date()].append(slot)
        for day, slots in groups.items():
            if len(slots) >= minimum and not self.calendar.agenda(str(day), str(day)):
                return day, slots[:minimum]
        pytest.fail("No empty live test day with enough available slots.")

    def agenda(self, day, **kwargs):
        return self.calendar.agenda(str(day), str(day), **kwargs)

    def seed(self, slot, alias):
        return self.seed_booking(instant(slot["start"]), {**self.attendee, "name": f"{self.name} {alias}"})

    def seed_booking(self, start, attendee, fields=None, minutes=None):
        """Set up one test booking. Only a definite rate-limit rejection is retryable."""
        args = (utc_stamp(start), attendee, fields, minutes)
        try:
            return self.calendar.create_booking(*args)
        except CalendarError as error:
            if error.status_code != 429 or error.uncertain:
                raise
            # The test transport waited through the cooldown. Check the exact
            # opening and the agenda before making one more fixture request.
            end = start + timedelta(minutes=minutes or self.calendar.event_durations()[0])
            slots = self.calendar.slots(start, end, duration_minutes=minutes)
            day = start.astimezone(self.calendar.zone).date()
            if not any(instant(s["start"]) == start for s in slots) or any(
                    instant(b["start"]) < end and start < instant(b["end"]) for b in self.agenda(day)):
                raise
        return self.calendar.create_booking(*args)

    def owned_bookings(self, day):
        return [entry for entry in self.agenda(day) if any(
            self.owns_attendee(p)
            for p in entry["attendees"]
        )]

    def owns_attendee(self, person):
        address = person["email"].casefold()
        if self.test_contacts:
            return self.test_contacts.get(person["name"]) == address or (person["name"], address) in self.additional_contacts
        return person["name"].startswith(self.name) and address == self.attendee["email"].casefold()


def mailbox_for_comparison(address):
    local, _, domain = address.strip().casefold().partition("@")
    local = local.partition("+")[0]
    if domain in {"gmail.com", "googlemail.com"}:
        return f"{local.replace('.', '')}@gmail.com"
    return f"{local}@{domain}"


def check_test_event(host, event, inbox, *, same_account, host_preferences_muted=False):
    if event.get("ownerId") != host["id"] or event.get("teamId"):
        pytest.fail("Choose a personal test event type owned by the configured Cal.com host.")
    if not same_account:
        if host["email"].casefold() != inbox:
            pytest.fail("For a separate test account, use its dedicated inbox for the test attendee.")
        return
    settings = event.get("emailSettings") or {}
    if settings.get("disableEmailsToHosts") is not True and not host_preferences_muted:
        pytest.fail("Mute booking emails for the host before running the live tests.")
    if settings.get("disableEmailsToAttendees") is not True:
        if mailbox_for_comparison(inbox) == mailbox_for_comparison(host["email"]):
            pytest.fail("Use a separate attendee inbox. Attendee invitations could reach the host address.")


def pace_calendar_requests(monkeypatch):
    """Leave room for the whole scenario and its cleanup in the live API quota."""
    transport = httpx.request

    def paced(method, url, **kwargs):
        if not url.startswith("https://api.cal.com/v2/"):
            return transport(method, url, **kwargs)
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = transport(method, url, **kwargs)
                break
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt + 1 == attempts:
                    raise
        remaining = response.headers.get("x-ratelimit-remaining-default")
        reset = response.headers.get("x-ratelimit-reset-default", "60")
        if response.status_code == 429:
            # Booking writes can have their own cooldown even with general quota left.
            wait = response.headers.get("retry-after") or response.headers.get("retry-after-default") or "60"
            sleep(max(float(reset), float(wait)) + 1)
            if method == "GET":
                return transport(method, url, **kwargs)
            return response
        if remaining and (int(remaining) == 0 or (url.endswith("/me") and int(remaining) < 30)):
            sleep(float(reset) + 1)
        return response

    monkeypatch.setattr(httpx, "request", paced)


@pytest.fixture
def live(monkeypatch, request):
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Live API tests are opt-in")
    app_key = os.getenv("CAL_API_KEY")
    key = os.getenv("CAL_TEST_API_KEY") or app_key
    event_id = os.getenv("CAL_TEST_EVENT_TYPE_ID")
    email = os.getenv("CAL_TEST_INBOX", "").strip().casefold()
    if not (key and event_id and email):
        pytest.fail("Set CAL_TEST_EVENT_TYPE_ID, CAL_TEST_INBOX, and a Cal.com API key before live writes.")
    profile = getattr(request, "param", None)
    if profile in {"maya-name-correction", "maya-cancellations", "multiple"}:
        # Keep the transcript's literal names while isolating this run from the user's Maya bookings.
        domain = email.partition("@")[2]
        if not domain.endswith(".mailosaur.net"):
            pytest.skip("This exact-name replay needs a unique address on the configured Mailosaur server.")
        email = f"maya-{uuid4().hex}@{domain}"
    monkeypatch.setenv("CAL_API_KEY", key)
    monkeypatch.setenv("CAL_EVENT_TYPE_ID", event_id)
    calendar = new_conversation().calendar
    pace_calendar_requests(monkeypatch)
    host = calendar.request("GET", "me", "2024-08-13")["data"]
    same_account = True
    host_preferences_muted = os.getenv("CAL_TEST_HOST_EMAIL_MUTED") == "1"
    if app_key and key != app_key:
        app_host = CalClient(app_key, calendar.zone).request("GET", "me", "2024-08-13")["data"]
        same_account = app_host["id"] == host["id"]
        if not same_account and mailbox_for_comparison(app_host["email"]) == mailbox_for_comparison(host["email"]):
            pytest.fail("The separate test account delivers mail to the everyday inbox.")
    event = calendar.request("GET", f"event-types/{calendar.event_type_id}", "2026-06-12")["data"]
    check_test_event(host, event, email, same_account=same_account,
                     host_preferences_muted=host_preferences_muted)
    run = LiveRun(calendar, email, "Round Two" if request.module.__name__.endswith("two") else "Round One")
    real_request = CalClient.request
    if profile == "multiple":
        run.test_contacts = {name: f"{name.split()[0].lower()}-{uuid4().hex}@{domain}"
                             for name in ("Maya Chen", "Hellen", "Leo", "Control")}
        run.test_contacts["Maya"] = run.test_contacts["Maya Chen"]
    elif profile in {"maya-name-correction", "maya-cancellations"}:
        run.name = run.attendee["name"] = "Maya" if profile == "maya-cancellations" else "Maya Chen"
    elif profile:
        body = {"title": run.name, "slug": run.name.lower().replace(" ", "-"),
                "lengthInMinutes": 30, "hidden": True}
        if profile in {"fields", "lengths"}:
            body["bookingFields"] = [{"field": "custom", "type": "shortText", "slug": slug,
                                      "label": slug.title(), "required": False} for slug in ("purpose", "company")]
            if profile == "lengths":
                body["lengthInMinutesOptions"] = [30, 45]
        elif profile == "pending":
            body["confirmationPolicy"] = {"type": "always", "blockUnconfirmedBookingsInBooker": True}
        else:
            raise ValueError(f"Unknown event profile {profile}")
        temp = calendar.request("POST", "event-types", "2026-06-12", body=body)["data"]
        temp_path = f"event-types/{temp['id']}"
        request.addfinalizer(lambda: real_request(calendar, "DELETE", temp_path, "2026-06-12"))
        assert temp["hidden"] and temp["slug"] == body["slug"]
        check_test_event(host, temp, email, same_account=same_account, host_preferences_muted=host_preferences_muted)
        calendar.event_type_id = temp["id"]
        monkeypatch.setenv("CAL_EVENT_TYPE_ID", str(temp["id"]))

    def tracked_request(self, method, path, version, *, params=None, body=None):
        # Observe genuine HTTP traffic from both the UI and direct client. A test
        # run may only modify the bookings it created with its controlled inbox.
        run.calls.append((method, path))
        if path == "slots" and params.get("bookingUidToReschedule"):
            run.move_queries.append((params["eventTypeId"], params["bookingUidToReschedule"]))
        if method == "POST":
            if path == "bookings":
                assert body["eventTypeId"] == calendar.event_type_id
                assert run.owns_attendee(body["attendee"]), "Unexpected test attendee or address"
            else:
                assert path.split("/")[1] in run.owned_uids, "Unexpected live booking target"
            if "start" in body:
                run.test_days.add(instant(body["start"]).astimezone(calendar.zone).date())
            if path.endswith("/cancel"):
                run.cancel_reasons[path] = body.get("cancellationReason", "")
        payload = real_request(self, method, path, version, params=params, body=body)
        if (method, path) == ("GET", "slots"):
            count = sum(len(group) for group in payload["data"].values())
            run.slot_queries.append((params["start"], params["end"], count))
        if (method, path) == ("GET", "bookings"):
            run.pages.append(payload["pagination"])
        if method == "POST" and not path.endswith("/cancel"):
            run.owned_uids.add(payload["data"]["uid"])
        return payload

    monkeypatch.setattr(CalClient, "request", tracked_request)
    try:
        yield run
    finally:
        # The live agenda also finds replacement UIDs after a lost move response.
        for day in run.test_days:
            for entry in run.owned_bookings(day):
                uid = entry["uid"]
                run.owned_uids.add(uid)
                try:
                    calendar.cancel_booking(uid, "Scheduling chatbot test cleanup")
                except CalendarError as error:
                    if error.status_code != 429 or error.uncertain:
                        raise
                    # The transport waited for the cooldown. Retry only this still-active test booking.
                    if any(row["uid"] == uid for row in run.owned_bookings(day)):
                        calendar.cancel_booking(uid, "Scheduling chatbot test cleanup")
            assert not run.owned_bookings(day), "Test bookings still active after cleanup"


def ask(ui, prompt):
    ui.chat_input[0].set_value(prompt).run()
    assert not ui.exception and not ui.error, "Streamlit reported an error"
    reply = ui.session_state["conversation"].messages[-1]["content"]
    # Pytest can print the assertion operands as well as the failure message.
    return re.sub(r"[\w.+%-]+@[\w.-]+\.[\w-]+", "[address]", reply)


@pytest.fixture
def ui(live):
    path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(path), default_timeout=120).run()
    assert not app.exception and not app.error
    return app


def local_start(live, slot):
    return instant(slot["start"]).astimezone(live.calendar.zone)


def time_position(answer, value):
    # In "9:00–9:30 AM" the period applies to the start as well. Remove range
    # ends so a proposed end time cannot be mistaken for another start.
    clock = r"(\d{1,2}(?::\d{2})?)"
    meridiem = r"([ap]\.?m\.?)"
    separator = r"\s*(?:[-–—]|\bto\b)\s*"
    clock_range = rf"(?<![\d:]){clock}\s*{meridiem}?{separator}{clock}\s*{meridiem}"

    def range_start(match):
        start, start_period, end, end_period = match.groups()
        if not start_period:
            start_period = end_period
            # For a 30-minute meeting, 11:30–12:00 PM starts in the morning.
            if int(start.split(":")[0]) % 12 > int(end.split(":")[0]) % 12:
                start_period = "pm" if end_period.lower().startswith("a") else "am"
        return f"{start} {start_period}"

    answer = re.sub(clock_range, range_start, answer, flags=re.I)
    answer = re.sub(rf"(\b\d{{1,2}}:\d{{2}}){separator}\d{{1,2}}:\d{{2}}\b", r"\1", answer, flags=re.I)
    hour = value.hour % 12 or 12
    minutes = f":{value.minute:02d}" if value.minute else "(?::00)?"
    period = "a" if value.hour < 12 else "p"
    pattern = rf"(?<![\d:])(?:0?{hour}{minutes}\s*{period}\.?m\.?|{value:%H:%M}(?!\s*[ap]\.?m))(?!\d)"
    match = re.search(pattern, answer, re.I)
    return match.start() if match else None


def time_in_reply(answer, value):
    return time_position(answer, value) is not None
