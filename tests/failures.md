# Failure handling across actions

Use real OpenAI and Cal.com wherever the test driver can create the condition. For the injected variants, change only the named response or arguments and keep the rest of the conversation live. Score live and injected runs separately. [Shared test setup](../docs/IMPLEMENTATION_PLAN.md#test-setup)

| Case | Conversation and setup | Pass criteria |
| --- | --- | --- |
| X1 · Read fails | Ask “What’s on tomorrow?” **Live:** supply an invalid Cal.com token to this test client only. **Injected outage:** use normal credentials and return HTTP 503 at the calendar transport for this turn’s list requests, including retries. A provider outage cannot be scheduled. | Say the calendar could not be read. Distinguish authentication or service failure from V5’s successful empty day. Zero writes. Restore normal transport for subsequent turns. |
| X2 · Write outcome is unknown | **Injected lost response:** forward the create to real Cal.com, let it succeed, then raise a timeout before the client receives that one response. Allow all subsequent calls through. Ask “Did that go through?” | The agent uses a live lookup to reconcile the attendee, event type, and start time. Exactly one create reached Cal.com. Confirm a uniquely identified booking or explain uncertainty if the lookup is ambiguous. Independently read back the committed booking and clean it up. |
| X3 · Same turn is delivered twice | Deliver “Book the second one” twice concurrently with the same turn ID. Then deliver “Confirm” twice with a shared confirmation-turn ID. Both services remain connected. | The first pair returns the same preview with zero writes. The confirmation pair returns the same receipt for one live create. A later, separately confirmed meeting can still be booked. |
| X4 · Model supplies an invalid target | Obtain a real cancellation tool call, then replace its booking UID with an unknown UID before Python validates it. | Reject the invalid selection before any Cal.com write. Show valid choices or ask for clarification. Verify the live agenda is unchanged. |

B6 also tests one injected stale availability response after a real conflict. Its setup and assertions live with the [booking cases](booking.md).
