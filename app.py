from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
from agents.exceptions import AgentsException
from dotenv import load_dotenv
from openai import OpenAIError

from cal_client import CalendarError, instant
from scheduling import Conversation, chat_turn, new_conversation


load_dotenv()
st.set_page_config(page_title="Scheduling assistant", page_icon="📅")
st.html(Path(__file__).parent / ".streamlit" / "theme-menu.html", unsafe_allow_javascript=True)
st.html("""
<style>
.stChatMessage:has(> [data-testid="stChatMessageAvatarUser"]) {
    margin-inline-start: auto;
    width: fit-content;
    max-width: 82%;
    flex-direction: row-reverse;
    overflow-wrap: anywhere;
    border-radius: 1.2rem;
}
.st-key-weekly_meetings {
    max-height: 15rem;
    overflow-y: auto;
}
</style>
""")


def refresh_chat():
    st.session_state.conversation = Conversation(st.session_state.conversation.calendar)
    st.session_state.chat_draft = ""
    st.session_state.week_offset = 0
    st.session_state.pop("weekly_agenda", None)


def change_week(step):
    st.session_state.week_offset += step


def refresh_weekly_agenda(calendar, monday):
    previous = st.session_state.get("weekly_agenda")
    previous_error = st.session_state.get("agenda_error")
    try:
        bookings = calendar.agenda(str(monday), str(monday + timedelta(days=6)))
        error = None
    except CalendarError as failure:
        bookings = previous[1] if previous and previous[0] == monday else None
        error = str(failure)
    snapshot = monday, bookings
    st.session_state.weekly_agenda = snapshot
    st.session_state.agenda_error = error
    return snapshot != previous or error != previous_error


def show_weekly_meetings(bookings, zone, now):
    day = None
    with st.container(key="weekly_meetings", gap="xxsmall"):
        for booking in bookings:
            start = instant(booking["start"])
            if start < now:
                continue
            start = start.astimezone(zone)
            end = instant(booking["end"]).astimezone(zone)
            if day != start.date():
                day = start.date()
                st.markdown(f"**{day:%A, %b} {day.day}**")
            start_time = start.strftime("%I:%M %p").lstrip("0")
            end_time = end.strftime("%I:%M %p").lstrip("0")
            if start.utcoffset() != end.utcoffset():
                start_time += f" {start:%Z}"
                end_time += f" {end:%Z}"
            if end.date() != day:
                end_time = f"{end:%a} {end_time}"
            names = ", ".join(p["name"] for p in booking.get("attendees", []) if p.get("name"))
            status = "  ·  Awaiting approval" if booking["status"] == "pending" else ""
            st.text(f"{start_time}–{end_time}  ·  {names or booking['title']}{status}")
    if day is None:
        st.caption("No upcoming meetings this week.")


try:
    if "conversation" not in st.session_state:
        st.session_state.conversation = new_conversation()
    elif not isinstance(st.session_state.conversation, Conversation):
        # Streamlit can reload the code while a browser session still holds the old instance.
        previous = st.session_state.conversation
        current = new_conversation()
        current.restore_session(previous)
        st.session_state.conversation = current
except (CalendarError, ValueError, KeyError) as error:
    st.error(str(error))
    st.stop()

state = st.session_state.conversation
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    st.title("Scheduling assistant", width="content")
    st.button("Refresh", icon=":material/refresh:", help="Clear this chat and refresh the schedule.", on_click=refresh_chat)

now = datetime.now(state.calendar.zone)
offset = st.session_state.setdefault("week_offset", 0)
monday = now.date() - timedelta(days=now.weekday()) + timedelta(weeks=offset)
sunday = monday + timedelta(days=6)
with st.container(border=True):
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.subheader("Weekly schedule", width="content")
        with st.container(horizontal=True, width="content", gap="xxsmall"):
            st.button("Previous week", icon=":material/chevron_left:", disabled=offset == 0,
                      on_click=change_week, args=(-1,))
            st.button("Next week", icon=":material/chevron_right:", on_click=change_week, args=(1,))
    st.caption(f"{monday:%b} {monday.day}, {monday.year} – {sunday:%b} {sunday.day}, {sunday.year} · {state.calendar.zone.key}")
    snapshot = st.session_state.get("weekly_agenda")
    if snapshot is None or snapshot[0] != monday:
        with st.spinner("Loading schedule…"):
            refresh_weekly_agenda(state.calendar, monday)
    _, bookings = st.session_state.weekly_agenda
    if bookings is not None:
        show_weekly_meetings(bookings, state.calendar.zone, now)
    if error := st.session_state.get("agenda_error"):
        message = "Showing the last loaded schedule." if bookings is not None else "Couldn’t load this week’s meetings."
        st.warning(f"{message} {error}")
        st.button("Retry schedule", on_click=refresh_weekly_agenda, args=(state.calendar, monday))

st.subheader("Chat")
for entry in state.messages:
    with st.chat_message(entry["role"]):
        st.markdown(entry["content"])

prompt = st.chat_input("Ask about your calendar", key="chat_draft")
if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Checking your calendar…"):
                reply = chat_turn(state, prompt)
            st.markdown(reply)
        except (OpenAIError, AgentsException) as error:
            st.error(f"The scheduling agent could not finish this turn ({type(error).__name__}).")
            st.stop()
    # Keep the visible week in place while both the agent and Cal.com are working.
    if refresh_weekly_agenda(state.calendar, monday):
        st.rerun()
