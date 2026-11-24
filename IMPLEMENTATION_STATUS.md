# Implementation status

## Current status · November 23, 2026

The four take-home actions are implemented in Streamlit. The weekly agenda stays visible above the chat while the assistant checks Cal.com. The completed browser recording shows two conflicting interview requests, booking both interviews, moving one, viewing the agenda, cancelling the other and rebooking it after a mistake. [Watch the demo (1:45)](scheduling-assistant-demo.mp4)

The current suite collects **220 pytest checks**. The local-only run passed **62** and skipped **158** by configuration. Those 158 comprise 102 API-enabled application checks and 56 archived-prompt trials. The full live run below used an earlier version of the suite. [Testing guide](TESTING.md#local-checks)

### November 23 live evaluation

| Run on `gpt-6-luna` | First attempt | Verification after fixes |
| --- | --- | --- |
| Application suite | 46/46 local and 79/84 API-enabled checks passed, including all four core actions. | All five initially failing checks passed on fresh fixtures. |
| Saved Round 1 and Round 2 prompts | Round 1 passed 26/28 and Round 2 passed 22/28. | All eight initially failing trials passed on fresh fixtures. These reruns are separate from the original scores. |
| Stability sample | H2, H5 and H10 were selected for repeated testing after fixes. | Each passed three fresh repetitions on that revision (9/9). |

A later live replay covered “cancel all the meetings this week, because I have already caught up with them.” The preview showed both meetings and their shared reason. Confirming sent the reason to Cal.com for each cancellation, and a repeated confirmation made no additional changes. [Cancellation case C9](tests/cancellation.md#live-conversations-for-cancelling-several-meetings)

The sections below preserve the earlier November 22 results, including the comparison on `gpt-5.4-mini`.

## Round 1 baseline

The complete suite passed all 14 pytest tests in 68.44 seconds using `gpt-5.4-mini` and `America/New_York`. The live API lifecycle covered CT1–CT6, four live Streamlit conversations covered B1, V1, R1 and C1, and nine local checks covered recipient safeguards and time-range evaluation. The private Mailosaur inbox had no forwarding rules and received the first booking confirmation and its cleanup cancellation. The SHA-256 prefix of the static agent instructions was `d26d920dfc3f`.

## Round 2 results

The remaining 22 cases passed across 24 scored scenarios using `gpt-5.4-mini` and the private Mailosaur attendee inbox. Twenty scenarios ran fully live and four injected a single failure. The table gives the final result for each scenario, including focused reruns with new bookings.

| Action | Fully live | Injected failure |
| --- | --- | --- |
| Booking, B2–B7 | 6/6 | 1/1, stale refresh after a real conflict |
| Viewing, V2–V5 | 4/4 | — |
| Cancellation, C2–C5 | 4/4 | — |
| Rescheduling, R2–R5 | 4/4 | — |
| Failure handling, X1–X4 | 2/2, invalid token and overlapping delivery | 3/3, outage, lost receipt, invalid UID |

The wrong-target cases led to Python checks for the original attendee, date and time. The slot-race cases now refresh availability and suppress the rejected start. Attendee-zone replies use times converted from the stored instant. A lost create receipt triggers an agenda lookup by event type, attendee and start. The real attendee reschedule stayed pending until host approval. Cal.com issued a new booking UID in R4, which the follow-up cancellation used.

The combined run passed 40 of 42 checks. It exposed two remaining wording issues in R4 and B7. Both live cases and their two local regression checks passed on fresh data. A further B7 rerun exposed an intermittent one-hour error in the model’s slot query. Python now builds an exact founder-local window from the supplied date and clock using the founder’s IANA zone. B7, B5, B1 and R1 passed after this change. R2 also passed with the shortened follow-up “Thursday, please.” All four Round 1 Streamlit conversations and the six API contracts passed in the combined run. A final Cal.com audit found zero active test bookings and zero temporary event types. Mailosaur displayed both confirmation and cancellation messages. The static agent instructions had SHA-256 prefix `e15a17ae76a1`. At the end of Round 2, the prompt comparison and recorded browser run were still ahead.

## Round 3 results

On November 22, 2026, both historical prompts ran against the same scheduling code and `gpt-5.4-mini`. Each row includes two phrasings run twice per prompt. The harness alternated prompt order, set up fresh Mailosaur attendees on the real calendar, and read Cal.com after each write. The pass counts below include the first completed outcome for every trial. Cal.com rate-limited some fixture requests. Those interrupted trials resumed after cooldown, and all 56 conversations were scored. [Compare both prompts in local Langfuse](http://localhost:3307/project/scheduling/experiments/results?baseline=8acdb2730f50975150127b760f566094&c=328193b625de754ceea29267db04f09e)

| Conversation | Round 1 | Round 2 |
| --- | ---: | ---: |
| B1 · Book an exact time, then supply the attendee email | 1/4 | 2/4 |
| V1 · View tomorrow’s agenda | 4/4 | 4/4 |
| C1 · Cancel one identified meeting | 4/4 | 4/4 |
| R1 · Move a meeting to a later opening | 4/4 | 4/4 |
| C2 · Choose between two matching meetings before cancelling | 4/4 | 4/4 |
| B6 · Recover when someone takes the chosen slot | 4/4 | 4/4 |
| R4 · Cancel the replacement booking after a move | 3/4 | 4/4 |
| **Total** | **24/28** | **26/28** |

On this run we selected the Round 2 prompt. It passed three pairs that Round 1 missed and missed one pair that Round 1 passed. Four observations per scenario give limited evidence about either prompt’s underlying success rate. In B1, Round 1 sometimes interpreted “intro” as a custom meeting purpose. Round 2 sometimes asked again for a name already supplied or tried to find an existing meeting for a new booking. In the failed R4 trial, Round 1 offered the requested destination and asked the user to choose it again. The calendar showed no wrong-target or duplicate write in these failed conversations.

Median live turn time was 2.77 seconds across 46 turns for Round 1 and 2.48 seconds across 47 turns for Round 2. Median OpenAI time per turn was 1.90 and 1.84 seconds respectively. Median total Cal.com request time per turn was 1.06 and 1.11 seconds. The failed conversations sometimes ended early, so these figures describe the observed runs.

### Verification after fixes

The application now carries the original booking request through contact collection, checks the supplied attendee address before writing, and distinguishes a request to book or move to an exact time from a request to browse openings. An ambiguous name-only cancellation starts with a live lookup. A same-day move uses the original meeting’s local date and end time. The experiments above retain the outcomes measured before the subsequent booking and exact-move fixes.

All 47 distinct application checks passed across the suite and focused reruns. These include 15 local checks and 32 API-enabled checks, four of which inject a specific failure. All four core Streamlit conversations passed. A subsequent live run found that “Thursday, please, to [date] at 2 pm” could lose the link between the original meeting and the destination. The agent now validates that exact local start against the selected booking. R1–R5 all passed on fresh fixtures after the change. Four further B1 conversations passed across both phrasings after preserving the user’s literal attendee name and checking optional company information. The corrected-email and real custom-field tests also passed. A contact-change case switched the attendee and corrected the time while supplying the email. A separate live check caught a move five hours late when the model interpreted London time as New York time. Python now resolves the explicit city or IANA zone for an exact destination, including a zone supplied on the preceding move request. The weekday-selection cases retain the unrelated booking on the same weekday and check the moved booking’s UTC instant.

The final independent Cal.com audit found zero active test bookings over the next 366 days and zero temporary event types. The private Mailosaur inbox showed confirmation, reschedule, and cancellation messages from Round 3.
