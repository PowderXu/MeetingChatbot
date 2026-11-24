# Live Cal.com API contract tests

Call the five live endpoints directly through the Python Cal.com client to verify request handling, response parsing, and booking state. The conversation suites exercise those same operations through real OpenAI. [Evaluation plan and API versions](../docs/IMPLEMENTATION_PLAN.md#build-order)

## Setup

- Use a dedicated, auto-confirming, standard one-on-one event type lasting 30 minutes and test attendee addresses you control. Choose a future date in the host’s booking window.
- The executable test starts on an empty day with at least four non-overlapping 30-minute openings. It uses three of those starts for A, B, and a control booking. The control has its own attendee name and shares the controlled test inbox. A one-second window checks the empty-slots response. CT1–CT6 run in order in one pytest test. [Executable lifecycle](test_round_one.py)
- Send the endpoint-specific version headers. Track the UIDs and pagination cursors Cal.com actually issues. Verify UTC instants, attendee identity, duration, booking status, and write outcomes against independent API readbacks.

## Contract cases

| Case | Live operations | Pass criteria |
| --- | --- | --- |
| CT1 · Available and empty windows | Call `GET /v2/slots` with `format=range` for the open and closed windows. | The open response groups slots by date. A and B have parseable `start` and `end` values spanning 30 minutes. The closed window returns HTTP 200 with `data: {}`. [Slots](https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type) |
| CT2 · Create and read back | Book A for the test attendee with `POST /v2/bookings`. Save the returned UID and read the active agenda. | The client parses a successful, accepted booking with a UID, the requested attendee, start, and duration. Exactly one new active meeting appears at A. The control booking keeps its original time and attendee. [Create](https://cal.com/docs/api-reference/v2/bookings/create-a-booking) |
| CT3 · List across pages | List the two active test bookings with `GET /v2/bookings` and `limit=1`. Follow each returned `pagination.nextCursor`. | The first page signals more results. Both records appear exactly once across the pages. The final page has `hasMore=false` and `nextCursor=null`. [List](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings) |
| CT4 · Request an occupied time | Attempt another standard booking at A for a different controlled attendee while CT2’s booking is active, with conflict overrides omitted. Read the agenda again. | Cal.com rejects the write and the client recognizes the availability conflict. A still has exactly one active booking, and the control is intact. [Create](https://cal.com/docs/api-reference/v2/bookings/create-a-booking) |
| CT5 · Move and read back | Fetch openings with `bookingUidToReschedule` set to CT2’s UID. Move that booking to B with `POST /v2/bookings/{bookingUid}/reschedule`. Save the UID in the move response. | B is offered. The active agenda contains exactly one meeting for the test attendee at B and zero at A. The attendee and duration are preserved. The active booking’s UID matches the move response. [Reschedule](https://cal.com/docs/api-reference/v2/bookings/reschedule-a-booking) |
| CT6 · Cancel the moved meeting | Send `POST /v2/bookings/{bookingUid}/cancel` using the UID from CT5. Read the active agenda again. | Cal.com reports successful cancellation. The test attendee has zero active meetings in the test window. The control booking remains active at its original time. [Cancel](https://cal.com/docs/api-reference/v2/bookings/cancel-a-booking) |

## Recording results

Record the request method, endpoint version, HTTP status, relevant response fields, and agenda readback for each live case. If the starting slots changed before the run, mark the affected case inconclusive and repeat with fresh slots.

After the run, cancel any still-active bookings created for the test, including the control. If a write timed out, read the dedicated test agenda to identify the resulting booking before cleanup. Live latency and agent accuracy are recorded in the separate conversation evaluation. [Evaluation framework and scoring](../docs/IMPLEMENTATION_PLAN.md#evaluation-framework-and-scoring)
