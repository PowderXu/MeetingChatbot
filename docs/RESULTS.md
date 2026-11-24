# Scheduling assistant evaluation results

On November 23, 2026, all four core conversations passed on their first attempt with real OpenAI and Cal.com calls. Across the application suite and a separate experiment comparing two archived prompts, **173 of 186 checks passed on the first attempt**. After fixes, all 13 initially failing checks passed on fresh fixtures.

The suite has since grown to **220 collected checks**. The latest local-only run passed all 62 local checks and skipped the 102 API-enabled application checks and 56 prompt trials. The live scores below belong to the earlier revision. [Testing guide](TESTING.md)

## November 23 application results

The run used `gpt-6-luna`, the configured Cal.com test event or a temporary event, and private Mailosaur attendee addresses. Tests checked calendar state after writes. Selected failure cases injected a single fault, such as losing the response to a committed booking. [Test setup and scoring](IMPLEMENTATION_PLAN.md#evaluation-framework-and-scoring)

| Checks | First attempt | Fresh rerun after fixes |
| --- | ---: | ---: |
| Local application checks | 46/46 | |
| API-enabled application checks | 79/84 | 5/5 initially failing checks |
| Archived-prompt trials | 48/56 | 8/8 initially failing trials |

| Application failure | What changed before the rerun |
| --- | --- |
| Moving Hellen after Tuesday’s conflict | “Then move to Wednesday” keeps the original meeting selected and produces a preview for confirmation. |
| Supplying an email after choosing a Pacific time | The draft keeps the checked start while the agent collects the attendee’s display time zone. |
| Requesting 30 minutes but giving a one-hour interval | The test accepts equivalent wording when the assistant asks the user to resolve the duration conflict. |
| Keeping Tuesday and cancelling two Monday meetings | The agent looks up the requested week using explicit Monday–Sunday dates. The rerun verified which Monday meeting remained after a partial cancellation. |
| Two attendees named Alex | The test setup checks Cal.com after an explicit rate-limit rejection and waits before retrying the setup booking. |

## November 23 prompt comparison

Each saved prompt ran seven scenarios with two phrasings and two repetitions per phrasing. The harness alternated prompt order on the same model and application revision. Scores below are first attempts. [Comparison harness](../tests/test_prompt_comparison.py) · [Round 1 prompt](../tests/prompts/round1.txt) · [Round 2 prompt](../tests/prompts/round2.txt)

| Scenario | Round 1 | Round 2 |
| --- | ---: | ---: |
| B1 · Book an exact time | 2/4 | 0/4 |
| V1 · View tomorrow’s agenda | 4/4 | 4/4 |
| C1 · Cancel one matching meeting | 4/4 | 3/4 |
| R1 · Move to a later opening | 4/4 | 4/4 |
| C2 · Clarify an ambiguous cancellation | 4/4 | 4/4 |
| B6 · Handle a slot taken after preview | 4/4 | 3/4 |
| R4 · Cancel the replacement booking after a move | 4/4 | 4/4 |
| **Total** | **26/28** | **22/28** |

Six failed trials stopped after showing availability instead of preparing the requested exact-time booking. One cancellation lost its target. In the remaining trial, Cal.com continued offering a slot after a competing booking took it and accepted a second booking. The fixes carry the exact time into the preview, look up the cancellation target, and check the agenda again before creating the confirmed booking. All eight failed trials passed on replay.

Across 127 conversational turns, the median response took 4.32 seconds and the 95th percentile took 16.85 seconds. These times include model calls, calendar calls, and quota waits during the turn. The timings exclude fixture setup and cleanup.

## Stability and follow-up checks

Of 14 scenario–phrasing pairs per prompt, Round 1 passed both repetitions for 12 pairs and one repetition for two pairs. Round 2 passed both for ten pairs, one for two pairs, and neither for two pairs.

After the fixes, all 16 affected live checks passed. Three multi-turn scenarios then ran three times each on fresh fixtures. This sample measures repeatability for those three conversations. [Multi-meeting scenarios](TESTING.md#multiple-appointments-in-one-conversation)

| Conversation on the updated revision | Fresh repetitions passed |
| --- | ---: |
| H2 · Book two meetings with Maya and supply the second time later | 3/3 |
| H5 · Approve Maya’s meeting while holding Hellen’s invitation | 3/3 |
| H10 · Move the Monday interviews except Leo’s | 3/3 |

Later live runs on single- and multiple-meeting previews passed 17/17 and 9/9 checks respectively. The runs overlap. They covered fresh wording, reusing the time or email from Hellen’s preview when booking another attendee, and narrowing two proposed cancellations to one. A separate C9 replay verified that both cancellations used the user’s shared reason and that a second confirmation sent no extra writes. [Executable conversations](../tests/test_multiple_meetings.py) · [Cancellation cases](../tests/cancellation.md)

## November 22 baseline and iteration history

The first three rounds used `gpt-5.4-mini` and the application revision available that day.

### Round 1 baseline

The complete suite passed all 14 pytest tests in 68.44 seconds using `gpt-5.4-mini` and `America/New_York`. The live API lifecycle covered CT1–CT6, four live Streamlit conversations covered B1, V1, R1 and C1, and nine local checks covered recipient safeguards and time-range evaluation. The private Mailosaur inbox had no forwarding rules and received the first booking confirmation and its cleanup cancellation. The SHA-256 prefix of the static agent instructions was `d26d920dfc3f`.

### Round 2 results

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

### Round 3 results

On November 22, 2026, both historical prompts ran against the same scheduling code and `gpt-5.4-mini`. Each row includes two phrasings run twice per prompt. The harness alternated prompt order, set up fresh Mailosaur attendees on the real calendar, and read Cal.com after each write. The pass counts below include the first completed outcome for every trial. Cal.com rate-limited some fixture requests. Those interrupted trials resumed after cooldown, and all 56 conversations were scored. [How to reproduce the comparison](TESTING.md#run-the-comparison-on-your-own-calendar)

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

#### Verification after fixes

The application now carries the original booking request through contact collection, checks the supplied attendee address before writing, and distinguishes a request to book or move to an exact time from a request to browse openings. An ambiguous name-only cancellation starts with a live lookup. A same-day move uses the original meeting’s local date and end time. The experiments above retain the outcomes measured before the subsequent booking and exact-move fixes.

All 47 distinct application checks passed across the suite and focused reruns. These include 15 local checks and 32 API-enabled checks, four of which inject a specific failure. All four core Streamlit conversations passed. A subsequent live run found that “Thursday, please, to [date] at 2 pm” could lose the link between the original meeting and the destination. The agent now validates that exact local start against the selected booking. R1–R5 all passed on fresh fixtures after the change. Four further B1 conversations passed across both phrasings after preserving the user’s literal attendee name and checking optional company information. The corrected-email and real custom-field tests also passed. A contact-change case switched the attendee and corrected the time while supplying the email. A separate live check caught a move five hours late when the model interpreted London time as New York time. Python now resolves the explicit city or IANA zone for an exact destination, including a zone supplied on the preceding move request. The weekday-selection cases retain the unrelated booking on the same weekday and check the moved booking’s UTC instant.

The final independent Cal.com audit found zero active test bookings over the next 366 days and zero temporary event types. The private Mailosaur inbox showed confirmation, reschedule, and cancellation messages from Round 3.
