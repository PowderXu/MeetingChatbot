"""Cal.com v2 requests used by the scheduling assistant."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx


BOOKING_VERSION = "2026-02-25"
LIST_VERSION = "2026-05-01"
SLOTS_VERSION = "2024-09-04"


class CalendarError(Exception):
    def __init__(self, message: str, *, uncertain: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


def utc_stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A time zone is required.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Cal.com returned a time without a UTC offset.")
    return parsed


class CalClient:
    def __init__(self, key: str, zone: ZoneInfo, event_type_id: int | None = None):
        self.key = key
        self.zone = zone
        self.event_type_id = event_type_id

    def request(self, method, path, version, *, params=None, body=None):
        try:
            response = httpx.request(
                method, f"https://api.cal.com/v2/{path}",
                headers={"Authorization": f"Bearer {self.key}", "cal-api-version": version},
                params=params, json=body, timeout=20,
            )
        except httpx.RequestError as error:
            raise CalendarError("Could not reach Cal.com.", uncertain=method == "POST") from error

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if response.is_success and payload.get("status") == "success" and "data" in payload:
            return payload
        error = payload.get("error") or {}
        detail = error.get("message") if isinstance(error, dict) else None
        raise CalendarError(
            f"Cal.com returned HTTP {response.status_code}: {detail or 'Unexpected response'}",
            uncertain=method == "POST" and (response.status_code >= 500 or response.is_success),
            status_code=response.status_code,
        )

    def standard_event_type(self) -> int | None:
        options = self.request("GET", "event-types", "2026-06-12")["data"]
        matches = [item["id"] for item in options if item.get("lengthInMinutes") == 30
                   and not item.get("hidden")]
        return matches[0] if len(matches) == 1 else None

    def event_durations(self) -> tuple[int, list[int]]:
        event = self.request("GET", f"event-types/{self.event_type_id}", "2026-06-12")["data"]
        default = event["lengthInMinutes"]
        return default, sorted({default, *(event.get("lengthInMinutesOptions") or [])})

    def day_window(self, first_day: str, last_day: str):
        first = date.fromisoformat(first_day)
        last = date.fromisoformat(last_day)
        if last < first:
            raise ValueError("The end date must be on or after the start date.")
        return (datetime.combine(first, time.min, self.zone),
                datetime.combine(last + timedelta(days=1), time.min, self.zone))

    def agenda(self, first_day: str, last_day: str, *, page_size=100, status: str | None = None):
        start, end = self.day_window(first_day, last_day)
        # beforeEnd filters ends, so include overnight meetings and filter their starts below.
        params = {"afterStart": utc_stamp(start - timedelta(seconds=1)),
                  "beforeEnd": utc_stamp(end + timedelta(days=1)), "limit": page_size}
        statuses = {"cancelled"} if status == "cancelled" else {"accepted", "pending"}
        if self.event_type_id is not None:
            params["eventTypeId"] = self.event_type_id
        if status is not None:
            params["status"] = status
        found = {}
        for booking in self._bookings(params):
            if (start <= instant(booking["start"]) < end
                    and booking["status"] in statuses
                    and (self.event_type_id is None or booking["eventTypeId"] == self.event_type_id)):
                found[booking["uid"]] = booking
        return sorted(found.values(), key=lambda entry: instant(entry["start"]))

    def upcoming_meetings_with(self, name: str, email: str):
        """Find future active bookings for this name and email across event types."""
        now = datetime.now(timezone.utc)
        email = email.strip().casefold()
        name = " ".join(name.split()).casefold()
        params = {"attendeeEmail": email, "afterStart": utc_stamp(now), "limit": 100}
        matches = []
        for booking in self._bookings(params):
            if booking["status"] not in {"accepted", "pending"} or instant(booking["start"]) <= now:
                continue
            if any(" ".join((person.get("name") or "").split()).casefold() == name
                   and (person.get("email") or "").strip().casefold() == email
                   for person in booking.get("attendees", [])):
                matches.append(booking)
        return sorted(matches, key=lambda b: instant(b["start"]))

    def _bookings(self, params):
        """Iterate through every page of a filtered Cal.com booking query."""
        cursors = set()
        while True:
            result = self.request("GET", "bookings", LIST_VERSION, params=params)
            pagination = result.get("pagination")
            if (not isinstance(result.get("data"), list) or not isinstance(pagination, dict)
                    or not isinstance(pagination.get("hasMore"), bool)):
                raise CalendarError("Cal.com returned an incomplete booking list.")
            yield from result["data"]
            if not pagination["hasMore"]:
                return
            cursor = pagination.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise CalendarError("Cal.com returned an unusable pagination cursor.")
            cursors.add(cursor)
            params["cursor"] = cursor

    def slots(self, start: datetime, end: datetime, booking_uid: str | None = None,
              duration_minutes: int | None = None):
        if self.event_type_id is None or end <= start:
            raise ValueError("Select an event type and a window with a later end time.")
        params = {"eventTypeId": self.event_type_id, "start": utc_stamp(start),
                  "end": utc_stamp(end), "timeZone": self.zone.key, "format": "range"}
        if booking_uid:
            params["bookingUidToReschedule"] = booking_uid
        if duration_minutes is not None:
            params["duration"] = duration_minutes
        groups = self.request("GET", "slots", SLOTS_VERSION, params=params)["data"]
        slots = [slot for group in groups.values() for slot in group
                 if start <= instant(slot["start"]) and instant(slot["end"]) <= end]
        return sorted(slots, key=lambda slot: instant(slot["start"]))

    def meeting_fields(self, supplied: dict[str, str], *, previous: dict | None = None):
        """Map meeting details to the visible text questions on this event."""
        if not supplied and not previous:
            return {}
        event = self.request("GET", f"event-types/{self.event_type_id}", "2026-06-12")["data"]
        fields = {field["slug"]: field for field in event["bookingFields"]
                  if field.get("field") == "custom" and field.get("type") in {"shortText", "longText"}
                  and not field.get("hidden")}
        notes_slug = next((field["slug"] for field in event["bookingFields"]
                           if field.get("field") == "notes" and not field.get("hidden")), None)
        answers = {slug: value for slug, value in (previous or {}).items()
                   if (slug in fields or slug == notes_slug) and isinstance(value, str) and value}
        for name, value in supplied.items():
            matches = [slug for slug, field in fields.items()
                       if name in (slug.casefold(), field["label"].casefold())]
            if len(matches) == 1:
                answers[matches[0]] = value
            elif not matches and notes_slug:
                line = f"{name.title()}: {value}"
                answers[notes_slug] = "\n".join(filter(None, [answers.get(notes_slug), line]))
            else:
                raise ValueError(f"This event needs a configured {name} question to save that detail.")
        return answers

    def create_booking(self, start: str, attendee: dict, fields: dict | None = None,
                       duration_minutes: int | None = None):
        if self.event_type_id is None:
            raise ValueError("An event type is required to create a booking.")
        body = {"eventTypeId": self.event_type_id, "start": start, "attendee": attendee}
        if fields:
            body["bookingFieldsResponses"] = fields
        if duration_minutes is not None:
            body["lengthInMinutes"] = duration_minutes
        return self.request("POST", "bookings", BOOKING_VERSION, body=body)["data"]

    def cancel_booking(self, uid: str, reason: str | None = None):
        body = {"cancellationReason": reason} if reason else {}
        return self.request("POST", f"bookings/{uid}/cancel", BOOKING_VERSION, body=body)["data"]

    def reschedule_booking(self, uid: str, start: str, duration_minutes: int | None = None):
        body = {"start": start}
        if duration_minutes is not None:
            body["lengthInMinutes"] = duration_minutes
        return self.request("POST", f"bookings/{uid}/reschedule", BOOKING_VERSION,
                            body=body)["data"]
