# Conversational scheduling assistant implementation plan

Build a Streamlit chat app backed by one Python scheduling agent. The first milestone is a founder opening the app, asking “What’s on my calendar tomorrow?”, and seeing their live Cal.com agenda in local time. Add booking, cancellation, and rescheduling through the same chat. [Take-home requirements](REQUIREMENTS.md)

The build order and iteration rounds below describe the original plan. The shipped app, dated test results, and completed recording are documented in the [implementation status](IMPLEMENTATION_STATUS.md).

## Agent design

The app uses the **OpenAI Agents SDK** with `gpt-6-luna` by default. The original baseline used `gpt-5.4-mini`. The agent receives the transcript, current local date and time, and structured details such as the selected opening, attendee, and pending changes. It can read the agenda, search slots, and choose a tool for one appointment or several. Python validates the calendar data, prepares previews and applies confirmed changes. A clear single cancellation can proceed immediately. Langfuse receives saved evaluation results through a separate publishing step. [Scheduling code](scheduling.py) · [Multi-meeting tools](meeting_batch.py) · [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents)

For “move my 3 pm to later today,” the agent finds the original booking and asks for later openings. If it offers 4 and 4:30, “the second one” must resolve to the stored 4:30 slot for that booking. The assistant shows the original and proposed times. After confirmation, Python submits the reschedule and remembers the returned UID so “cancel it” can target the moved meeting.

All four actions use the same calendar and conversation. Keep one agent and keep the Cal.com functions independent of its SDK. The choice of framework affects how we run and inspect tool calls. Python still has to resolve which meeting “the Friday one” means and prevent duplicate writes.

| Agent framework | When I would choose it for this project |
| --- | --- |
| OpenAI Agents SDK | Used for this single-provider app. It runs the tool loop and can send model and tool traces to Langfuse through the documented integration. [Langfuse integration](https://langfuse.com/integrations/frameworks/openai-agents) |
| LangChain | Choose `create_agent` if comparing model providers or using LangChain middleware is part of the goal. The additional provider abstraction has limited value for a demo using one OpenAI model. [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents) |
| Strands Agents | A good fit if the implementer already knows Strands or wants its lifecycle hooks around model and tool calls. Its OpenAI provider accepts the existing key. For this project, the choice comes down to familiarity with the runner and its debugging workflow. [Strands OpenAI provider](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/model-providers/openai/) · [Agent loop and hooks](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/agents/agent-loop/) |

## Build order

1. **Start with the Streamlit chat page.** Create `app.py` with a message input, transcript, and founder time zone. Pass each newly submitted message to one Python function that handles a conversation turn. Keep the booking draft, displayed options, and selected booking in that user's session. The UI and evaluations call the same function.
2. **Connect the first live action, then extend the client.** Wire `list_bookings` to the OpenAI agent and verify tomorrow’s agenda in the browser. Add the remaining Cal.com operations as each action is implemented. Send the founder’s bearer token and the version for each endpoint. Return the booking UID, status, and times to the chat layer.

   | Operation | Endpoint | `cal-api-version` |
   | --- | --- | --- |
   | Find openings | `GET /v2/slots` with `eventTypeId`, UTC `start` and `end`, `timeZone`, and `format=range`. Send `duration` for a selected meeting length. [Slots](https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type) | `2024-09-04` |
   | Book | `POST /v2/bookings` with the event type, selected UTC start, and attendee details. Send `lengthInMinutes` for a selected length. [Create](https://cal.com/docs/api-reference/v2/bookings/create-a-booking) | `2026-02-25` |
   | View or find a booking | `GET /v2/bookings` with the relevant filters. Follow `pagination.nextCursor` when more results are needed. [List](https://cal.com/docs/api-reference/v2/bookings/get-all-bookings) | `2026-05-01` |
   | Cancel | `POST /v2/bookings/{bookingUid}/cancel`. Include a reason when the user gives one. [Cancel](https://cal.com/docs/api-reference/v2/bookings/cancel-a-booking) | `2026-02-25` |
   | Move | Check slots with `bookingUidToReschedule`, then call `POST /v2/bookings/{bookingUid}/reschedule` with the selected UTC start. Keep the UID returned by the move. [Reschedule](https://cal.com/docs/api-reference/v2/bookings/reschedule-a-booking) | `2026-02-25` |

   The client also reads `GET /v2/event-types` for automatic event selection and `GET /v2/event-types/{id}` for supported lengths and booking fields. Both use version `2026-06-12`. [Cal.com client](cal_client.py)

3. **Add booking, cancellation, and rescheduling to the chat.** Resolve relative dates in the founder’s time zone. Offer returned openings, clarify ambiguous matches, and report rejected or pending operations accurately. Scope each write to its submitted turn so redrawing the page cannot repeat it.
4. **Verify and record the browser demo.** Exercise the five endpoints against the live test calendar, run the conversation evaluations, and record the four actions in Streamlit. Document the local launch command in the submission README.

## Test setup

- **Live account and dates.** Use a personal event type with a 30-minute default and a 60-minute option for the full conversation suite. On the founder’s account, mute host booking emails using the account preferences or the event settings. Route attendee invitations to a separate controlled inbox when attendee emails are enabled. Keep custom email workflows and connected-calendar notices away from everyday inboxes. A separate Cal.com test account can use its dedicated inbox for both roles. Choose future openings using the founder’s IANA time zone and actual clock. For “tomorrow” or “later today,” verify that the test window exists on the corresponding local date. [Live test configuration](TESTING.md#round-1-live-checks)
- **Isolate each conversation.** Create the specified starting bookings through Cal.com and record their UIDs, times, and statuses. Keep a fresh chat session per case. Track UIDs from creates and moves, including competing bookings created by the test driver. Clean up only this run’s bookings after the assertions, including on failure. If setup cannot produce the required live state, record the case as unexercised and explain why.
- **Score from independent evidence.** Deliver one user message at a time through the real chat entry point. Capture outgoing tool calls and read the agenda directly from Cal.com after each write. Count agent writes separately from setup, readback, and cleanup. Compare the reply, target UID, UTC instant, and booking status with that evidence.

The linked test files describe the conversations and expected outcomes. Rows with issue links adapt publicly reported Cal.com behavior into chatbot regression tests. Other rows exercise the assignment examples, documented API behavior, or ordinary conversational ambiguity.

### Evaluation framework and scoring

Use **pytest** to drive the scenarios and check outcomes. Use **Langfuse** to compare prompt versions on the same live scenarios, using pytest’s calendar checks as the scoring criteria. Automated live runs use test credentials and make real bookings. Langfuse credentials are needed only for recording experiments. [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html) · [Langfuse dataset experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)

| Run | What it answers | How to score it |
| --- | --- | --- |
| Live Cal.com client checks | Does the client send valid requests and interpret real responses? | Exercise slots, create, list, move, and cancel directly. Verify shapes, status, and agenda changes before testing the model. [Live API contract cases](tests/calendar_contract.md) |
| Live conversation tests with real OpenAI and Cal.com | Does the agent choose the correct operation and accurately describe its outcome? | Start with B1, V1, C1, and R1. Check the active agenda after each change and assert zero agent writes on read-only or ambiguous turns. Measure OpenAI, Cal.com, and end-to-end time on these runs. |
| Targeted failure injection | Does the chatbot recover from conditions that are hard to trigger on demand? | Inject a repeated stale slot response (B6 variant), a service outage (X1 variant), a lost create response after a real commit (X2), or invalid model tool arguments (X4). Keep the rest of the path live. Report these results separately. [Failure cases](tests/failures.md) |

For the first Langfuse comparison, use B1, V1, C1, R1, C2, the live B6 conflict, and R4. Run two phrasings twice per prompt version (28 conversations), recreating equivalent live bookings and recording the actual available times for each run. Hold the model and case criteria fixed. In C2, cancelling a meeting before the user identifies Friday fails even if the final agenda happens to match.

Report passes/attempts per action, unexercised cases, and injected-failure results. A wrong-target write, duplicate write, or false success requires a fix and a rerun. Inspect the tool trace alongside the direct agenda readback to distinguish a model decision error from a client or reporting error.

## Test cases

- [Booking](tests/booking.md) covers B1–B12, including two proposed interviews that overlap.
- [Viewing scheduled events](tests/viewing.md) covers V1–V5 and the weekly schedule UI.
- [Cancelling](tests/cancellation.md) covers C1–C9, including partial success and a shared cancellation reason.
- [Rescheduling](tests/rescheduling.md) covers R1–R8 and the Hellen/Maya conflict followed by a corrected day.
- [Failure handling across actions](tests/failures.md) covers X1–X4, including a lost receipt and duplicate delivery.

The original 26 conversation cases grew into these families, ten additional multi-turn situations (H1–H10), and regressions using fresh wording. Several cases have multiple phrasings or outcomes. Six API contracts run together in one pytest check. The testing guide explains how to count and run the collected tests. [Live Cal.com contracts](tests/calendar_contract.md) · [Testing guide](TESTING.md)

## Frontend

The **Streamlit** page shows a weekly agenda above the chat, with week navigation and the founder’s time zone. It keeps the selected week visible while processing a message and refreshes the agenda after the reply. If a read fails, the page offers a retry and shows the last loaded schedule for that week when available. Chat displays openings in order so the user can choose “the second one.” **Refresh** starts a fresh chat and returns to the current week. [Streamlit app](app.py)

Only a newly submitted message invokes the agent. A Streamlit rerun redraws the stored transcript. Check this by booking once, triggering a redraw, and verifying the calendar still contains exactly one booking. A full page reload starts a fresh conversation because Streamlit session state is tied to the connection. The founder can look up the existing booking again from Cal.com. [Streamlit session state](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state)

**Local launch.** From the repository root, run `streamlit run app.py` and open the browser URL it prints. The server reads the existing OpenAI and Cal.com keys from its environment.

**Optional hosted demo.** In Streamlit Community Cloud, select the GitHub repo, branch, and `app.py`. Use a dedicated demo calendar, store the API keys in the app’s Secrets settings, and limit access to invited reviewers. Verify the live agenda from the hosted chat before including its URL in the README. [Deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy) · [App secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management) · [Reviewer access](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app)

## Real cases behind the design

| Case and what happened | How it informs this take-home |
| --- | --- |
| **Cal.ai scheduling by email.** Rubric Labs built Cal.com’s assistant around six narrowly defined API actions. Its tools worked in UTC, while replies used the reader’s time zone. The team implemented the agent with LangChain. [Builders’ case study](https://rubriclabs.com/work/cal) · [Technical account](https://www.langchain.com/blog/how-to-design-an-agent-for-production) | Adopt the same separation in our OpenAI Agents SDK design. The agent interprets “Thursday afternoon,” Python supplies the event type and calls Cal.com, and the chat shows local times. B1 checks the booked instant and V3 checks the founder’s and attendee’s local times. |
| **Streamlit repeated a chat input on redraw.** In version 1.28.0, a developer reported that calling `st.rerun()` left the previous message active and caused their chat app to loop. The maintainers traced the behavior to input reset handling and fixed it. [Streamlit issue 7629](https://github.com/streamlit/streamlit/issues/7629) | Separate transcript rendering from executing a newly submitted turn. In the browser check, redraw after booking and verify one active meeting. X3 tests the related case of two deliveries of the same turn. |
| **Stripe handled lost responses to completed writes.** Its API uses idempotency keys so a client can retry a charge whose response was lost and get the original result. [Stripe engineering](https://stripe.com/blog/idempotency) | Apply the failure scenario using Cal.com’s available operations. In X2, let the live create succeed, drop its response, then look up the booking by attendee, event type, and time. Verify that exactly one create reached Cal.com. |
| **An accepted booking and a delivered invitation diverged.** A Cal.com API user reported that the booking appeared in their Cal.com dashboard while the external calendar event and confirmation email did not arrive. [Cal.com issue 25009](https://github.com/calcom/cal.diy/issues/25009) | Be precise about what acceptance proves. “Booked in Cal.com” is supported by a fresh agenda read. Claiming that an invitation arrived would require separate evidence. In the final demo, read the agenda after create, move, and cancel. |
| **AutoDS reran evaluations after changes to its Fin agent.** Its CX AI operations lead described grouping evaluations by topic and rerunning the set after each change to detect regressions. [AutoDS account in Intercom’s release report](https://www.intercom.com/blog/announcing-evals-and-releases) | Keep the four core conversations as the round 1 baseline. When round 2 fixes an ambiguous cancellation, round 3 reruns C2, the related rescheduling case R2, and the baseline. A shared booking-selection change must still choose the right meeting for both operations. |

## Delivery pipeline and iteration rounds

**Set up the test calendar → show the first live agenda in Streamlit → add booking, cancellation, and rescheduling → evaluate and fix → run acceptance and submit.**

| Round | Work | Evidence to move on |
| --- | --- | --- |
| 1. Establish the baseline | Connect the existing keys and set the founder’s time zone. Start with “What’s on my calendar tomorrow?” in Streamlit, then implement the three actions that change bookings. Run the six live API contract checks and the four core conversations (V1, B1, C1, R1). | Each core conversation passes with real OpenAI and Cal.com. Independent agenda reads show the expected booking after each change. Keep the case results and prompt version as the baseline. |
| 2. Find and fix failures | Exercise the remaining cases in the original 26-case suite. Prioritize ambiguous cancellation (C2), a slot taken mid-conversation (B6), follow-up after a move (R4), and local midnight (V2). Inject only the hard-to-trigger failures identified in the evaluation plan, such as the lost response in X2. | For each failure, identify the first incorrect decision or API call, fix its cause, and rerun that case with fresh test bookings. Record any case the test account could not exercise. |
| 3. Verify the fixes | Rerun all four core conversations and the cases affected by each change. For example, a fix to booking selection must be checked for both cancellation and rescheduling. If the prompt changed, use Langfuse to compare the baseline and revised prompt on equivalent live scenarios. | Previously failing cases pass, the core actions still pass, and no observed wrong-target write, duplicate write, or false confirmation remains unresolved. |

Repeat rounds 2 and 3 when a rerun exposes a regression. Once the exit criteria hold, proceed to acceptance. A failed acceptance step goes through the same fix-and-rerun loop before recording a fresh end-to-end run.

### Acceptance run

For a minimal repeatable check, use one future meeting, a test attendee you control, and the Streamlit browser UI with real OpenAI and Cal.com. The completed recording extends this sequence to two interviews, conflicting times, and recovery after a mistaken cancellation. [Recorded demo](scheduling-assistant-demo.mp4)

1. Ask for openings, choose one, book it, and ask to see that day’s agenda. Verify directly in Cal.com that exactly one active booking exists for this test meeting.
2. Ask to move it, choose another opening, and list the agenda again. Verify that the meeting is active once at the new time and that its original slot no longer contains that booking.
3. Ask to cancel the moved meeting and check the agenda once more. Verify that the test meeting has zero active bookings and unrelated meetings remain intact.

Record the passing browser conversation. Submit the recording and the public or shared GitHub repository with a README explaining setup and the Streamlit launch command. [Submission requirements](REQUIREMENTS.md#submission)
