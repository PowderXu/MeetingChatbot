# Viewing scheduled events

Run V1–V5 with real OpenAI and Cal.com. Create the scenario’s bookings through Cal.com and take a direct agenda snapshot before starting the chat. Viewing should cause zero booking writes. [Shared test setup](../IMPLEMENTATION_PLAN.md#test-setup)

| Case | Conversation and setup | Pass criteria |
| --- | --- | --- |
| V1 · Tomorrow’s agenda | Create two intros for tomorrow in reverse time order. Create and cancel a third, then create one for the following day. Ask “What’s on my calendar tomorrow?” | List tomorrow’s two active intros in time order with local time and attendee. Exclude the cancelled and following-day meetings. |
| V2 · Local midnight | Use a host availability window that spans midnight in the founder’s zone. Book one 30-minute meeting starting before local midnight and ending after it, and one starting the next day. Ask for the first date’s agenda. | Include the first meeting and exclude the second. Cal.com’s `beforeEnd` filters by *end* time, so verify the live response covers the crossing meeting before filtering by local start date. [List filters](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings) |
| V3 · Host and attendee zones differ | Book a live intro with the founder in New York and Maya in Los Angeles. Ask when it starts, then “What time is that for Maya?” | Convert the stored UTC start into each IANA time zone and verify both replies refer to the same instant. [Cal.com issue 17](https://github.com/calcom/cal.diy/issues/17) |
| V4 · More than one page | Create three meetings on the requested date. Set the live list request’s page size to `limit=2` for the case. Ask for the full agenda. | Fetch the next page with the real cursor. Show all three meetings once, including the meeting returned only on page two. [List pagination](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings) |
| V5 · Empty day | Pick a future local date with no Cal.com bookings in the test account. Ask “Am I free on [date]?” | Say there are no Cal.com bookings for that day after a successful live list response. |

## Weekly schedule above the chat

The local Streamlit tests cover week navigation, preservation of chat history, exclusion of past starts, pending-booking labels, retry after a failed read, and keeping the last loaded week visible while chat is processing. Live B1 also checks the panel before confirmation and after booking, then redraws the page and checks that Cal.com received no extra request. [Executable UI checks](test_round_one.py)
