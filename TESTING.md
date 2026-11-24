# Testing the scheduling assistant

Run the commands from the repository root with the app’s virtual environment active. [Local setup](README.md#setup)

## Local checks

Run `RUN_LIVE_TESTS=0 python -m pytest -q` for checks that use no live APIs. Pytest skips the live conversations and prompt comparison in this mode.

Use `python -m pytest --collect-only -q` to list the current suite without contacting either API. On November 23, 2026, pytest collected 62 local checks, 102 API-enabled application checks, and 56 prompt-comparison trials (220 total). One conversation can have multiple pytest entries for different phrasings or outcomes. The 14 wording trials described below are a separate comparison. [Dated run results](IMPLEMENTATION_STATUS.md)

## Manual testing

Open the app to see upcoming meetings for the current week. Use **Next week** or ask “What’s on my calendar tomorrow?” in chat. To book, try “Can I get half an hour with Maya tomorrow morning?” Choose an opening and provide her email when asked. The attendee defaults to your calendar time zone. Review the local time and confirm. Try “move it to Wednesday at the same time” and check the weekly agenda before and after confirming. **Refresh** clears the chat and returns to this week. Calendar changes persist in Cal.com.

If you request a length the event does not offer, reply in chat with one of the listed lengths, such as “60 minutes.” After cancelling, try “Book that meeting again on Tuesday at the same time for one hour.” The assistant checks the full interval and asks you to confirm the new invitation.

Watch the [recorded two-interview demo](scheduling-assistant-demo.mp4) or repeat the shorter [acceptance run](IMPLEMENTATION_PLAN.md#acceptance-run). The plan also explains the [evaluation and scoring criteria](IMPLEMENTATION_PLAN.md#evaluation-framework-and-scoring).

## Round 1 live checks

The opt-in suite uses real OpenAI and Cal.com. Choose a personal, auto-confirming event type with a 30-minute default and a controlled attendee inbox. On the chat account, you can temporarily disable booking emails in **Settings → My account → Notifications**. For this setup, use an attendee inbox that stays separate from your own, including forwarding, and set `CAL_TEST_HOST_EMAIL_MUTED=1` for that run. The flag records your confirmation of the account setting. The suite reads event-level settings from Cal.com. Custom workflows and connected calendars may send their own messages. [Cal.com notification preferences](https://cal.com/help/bookings/notification-preferences)

```sh
export RUN_LIVE_TESTS=1
export CAL_TEST_EVENT_TYPE_ID=YOUR_TEST_EVENT_TYPE_ID
export CAL_TEST_INBOX=YOUR_CONTROLLED_ATTENDEE_INBOX
CAL_TEST_HOST_EMAIL_MUTED=1 python -m pytest -q -x tests/test_round_one.py
```

If the event already disables host emails, omit the per-run flag. If it also disables attendee emails, you can use the host address for the attendee. Cal.com requires an accepted organization membership to set these event-level options on a personal event type. A separate Cal.com test account is another option. Keep the app’s `CAL_API_KEY` set, supply the test account’s `CAL_TEST_API_KEY`, and use its host inbox as `CAL_TEST_INBOX`. [Cal.com event settings](https://cal.com/docs/api-reference/v2/event-types/update-an-event-type)

One test covers the six Cal.com API contract cases. Four tests start fresh Streamlit chats and exercise B1, V1, C1, and R1. While debugging, run just the affected case, for example `python -m pytest -q tests/test_round_one.py::test_r1_reschedule_later`. The tests read Cal.com independently after each change and verify cleanup. B1 and V1 use tomorrow when it is open and isolated. Otherwise they anchor the test chat clock so “tomorrow” refers to the next isolated live test day. V1 also requires an opening the following day. Run pytest with `-ra` to see any skipped cases. [Round 1 baseline results](IMPLEMENTATION_STATUS.md#round-1-baseline)

## Round 2 conversation checks

Use the same environment as Round 1, a private Mailosaur inbox, and a test event offering both 30 and 60 minutes. Run the cases sequentially. Each conversation uses the chat function called by Streamlit. The tests seed real bookings, observe the agent’s requests, read Cal.com independently, and cancel bookings belonging to that run.

```sh
RUN_LIVE_TESTS=1 CAL_TEST_HOST_EMAIL_MUTED=1 python -m pytest -v -ra tests/test_round_two.py
```

The injected scenarios exercise stale availability, a calendar outage, a lost response after Cal.com committed a booking, and an invalid UID passed to a scheduling tool. [Current conversation tests](tests/test_round_two.py) · [Round 2 results](IMPLEMENTATION_STATUS.md#round-2-results)

B4, R5 and the duration-choice test temporarily create hidden event types for custom fields, host approval, and configurable meeting lengths, then remove them. R5 uses Cal.com’s attendee reschedule endpoint for the move and the founder’s token for agenda verification. V2 uses the Kathmandu display zone so a normal New York opening crosses local midnight. B7 books 9 am New York openings on opposite sides of a daylight-saving transition. [Reschedule endpoint](https://cal.com/docs/api-reference/v2/bookings/reschedule-a-booking)

### Multiple appointments in one conversation

Run the same live setup against an event offering 30 and 60 minutes. The fixture finds an empty future week and anchors the chat clock so “Monday” and “tomorrow” refer to that week. It generates separate attendee addresses on the configured Mailosaur server. B11 and the R6 partial-failure variant create a real competing booking immediately after the first successful write. [Executable conversations](tests/test_multiple_meetings.py)

```sh
RUN_LIVE_TESTS=1 CAL_TEST_HOST_EMAIL_MUTED=1 python -m pytest -v -ra tests/test_multiple_meetings.py
```

To run all application checks together, use the same live settings and run `python -m pytest -q -ra --ignore=tests/test_prompt_comparison.py`. Run the [56 prompt trials](#run-the-comparison-on-your-own-calendar) separately to save their results.

C9 also covers a shared cancellation reason in the first message, mixed-case “COnfirm”, and repeat confirmation. Run it on its own with `python -m pytest -q tests/test_multiple_meetings.py::test_cancel_week_with_shared_reason` using the live settings above. [Expected conversation](tests/cancellation.md)

The ten additional conversations start with H4–H6. Each test reads Cal.com independently before and after the user's decision. H8 uses a preview-only conversation because the chat supports one invitee per appointment.

| Case | User's decision | What the test checks |
| --- | --- | --- |
| H4 | “Yes, but make Hellen’s 3 instead.” | Show the revised time and wait for fresh approval. |
| H5 | “Go ahead with Maya. I’m still waiting to hear from Hellen.” | Approve Maya alone, then recover Hellen’s draft when she replies. |
| H6 | “Before I confirm, what else do I have that afternoon?” | Show the booked meeting in that window and preserve both proposed meetings. |
| H1 | “Please finalize both appointments.” | Submit both previously reviewed bookings once. |
| H2 | “Tuesday at ten in the morning. I’ll give you Thursday’s time next.” | Ask only for Thursday’s start and preserve the attendee and length. |
| H3 | “Tuesday at 10 AM and Thursday at the same time.” | Mention an existing upcoming booking with that attendee once. |
| H7 | “Cancel my meeting with Alex Lee.” | Distinguish two Alex Lees, then cancel only the one identified by email. |
| H8 | “Together. I want Maya and Hellen on the same call.” | Explain the one-invitee limit and make no bookings. |
| H9 | “Move Maya to Friday and cancel Hellen’s Thursday interview.” | Show both actions, wait for approval, and verify each result. |
| H10 | “Move my three Monday interviews to Wednesday, except the one with Leo.” | Identify Leo as staying on Monday and verify that only the other two move. |

H5 also runs with “Go ahead with Maya on Wednesday. I’m still waiting to hear from Hellen.” Cal.com must show Maya on Wednesday and Hellen unbooked. H10 includes an unrelated Monday appointment alongside the three interviews. The test verifies that its ID and time stay the same.

### Coverage of the 14 comparison trials

The earlier comparison repeated seven scenarios twice with the same wording and a simulated calendar. The live versions use the configured test event, future dates, and private Mailosaur addresses. Each scenario has a comparison-style request and a fresh wording. Agenda and availability share a conversation. Ambiguous and targeted cancellation also share a conversation.

| Comparison scenario | Earlier coverage | What the live regression checks |
| --- | --- | --- |
| Read one attendee’s dated agenda | V1 lists a whole day. V3 checks one attendee’s local time. | Return Maya and her start time while excluding another attendee on the same day. |
| Book one meeting | B1 books a selected time. B4 checks the purpose field. | Two people sharing a time zone produce one preview, with the correct contact, time and purpose, followed by one booking. |
| Cancel a named meeting | C1 checks the target UID and an unrelated booking. | Select the specified time when the attendee has two meetings. Preserve the other meeting and a third attendee’s booking. |
| Move one meeting | R1 and R2 cover selection, time conversion and confirmation. | Interpret the two clocks as the original and destination for one meeting. Check the UID before and after confirmation. |
| Clarify an ambiguous cancellation | C2 asks which of two meetings the user means. | A request to cancel one of Maya’s two meetings must prompt for a choice before any write. |
| Check an occupied slot | The Hellen/Maya move regression explains an overlap. | A read-only availability question checks Cal.com and leaves the agenda and previews untouched. |
| Report a rejected booking | B6 catches a competing booking during the final availability check. | Another attendee takes the slot immediately before the real booking POST. Check Cal.com’s rejection, the reply, and who actually holds the slot. |

The related Friday test asks to move two separate calls to Thursday. The Hellen/Maya tests cover reusing the clock from a booked meeting and from an unconfirmed preview. [Executable conversations](tests/test_multiple_meetings.py)

## Round 3 prompt comparison

You can review the original scores for all seven scenarios and read why some conversations failed without using API keys. [Round 3 results](IMPLEMENTATION_STATUS.md#round-3-results)

### Run the comparison on your own calendar

Follow the README setup with your own OpenAI and Cal.com keys. Add the following to the repository-root `.env`, replacing both placeholders. Use a personal 30-minute test event that accepts bookings automatically and an attendee address you control, such as your own private Mailosaur inbox. The comparison uses only the inbox address. [Local setup](README.md#setup)

```dotenv
CAL_TEST_EVENT_TYPE_ID=your_numeric_event_type_id
CAL_TEST_INBOX=your_test_attendee_address
```

If you muted host booking emails in your Cal.com account preferences, also set `CAL_TEST_HOST_EMAIL_MUTED=1` in that file. The flag confirms the setting. If the test event already disables host emails, omit the flag. The suite creates, moves and cancels real bookings, so choose an attendee inbox that does not forward invitations to your everyday address. [Round 1 live calendar setup](#round-1-live-checks)

For the “tomorrow” scenarios the fixture looks for an empty future test day and anchors the chat clock to the preceding day when needed. The agenda case needs three nonoverlapping openings and another opening the following day. The ambiguous cancellation case needs 10 am openings on its test Thursday and Friday in the founder’s time zone.

Preview the 56 trial IDs without calling either API.

```sh
python -m pytest --collect-only -q tests/test_prompt_comparison.py
```

Run the full comparison sequentially. Pytest drives the chat directly, so you can leave Streamlit and Langfuse stopped.

```sh
mkdir -p .langfuse-local
export ROUND3_RESULTS="$(mktemp -d .langfuse-local/comparison-XXXXXXXX)"
RUN_LIVE_TESTS=1 python -m pytest -v -ra tests/test_prompt_comparison.py \
  -o junit_family=legacy --junitxml="$ROUND3_RESULTS/junit.xml"
```

Each saved prompt gets the same seven scenarios, two opening phrasings and two repetitions. Match trial IDs such as `B1-p1-r1-round1` and `B1-p1-r1-round2` to compare the same request. Read the verdicts in `junit.xml` and the conversations and calendar calls in the per-trial JSON files. Pytest checks the resulting Cal.com state independently. A skipped trial is recorded as unexercised. A rerun measures both snapshots on your current code, model and calendar, so the totals can differ from the original run. [Round 1 prompt](tests/prompts/round1.txt) · [Round 2 prompt](tests/prompts/round2.txt)

### Compare the runs in your own Langfuse dashboard

Start a local Langfuse instance, create a project and generate its API keys in the project settings. Copy the project ID from the dashboard URL after `/project/`. The original run used Langfuse server 4.83.0 and Python SDK 5.2.0. [Langfuse Docker Compose guide](https://langfuse.com/self-hosting/deployment/docker-compose)

Create the private file `.langfuse-local/.env` with these four unquoted values. Use the URL where you open your own dashboard.

```dotenv
NEXTAUTH_URL=http://localhost:3000
LANGFUSE_INIT_PROJECT_ID=your_project_id
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=your_project_public_key
LANGFUSE_INIT_PROJECT_SECRET_KEY=your_project_secret_key
```

Install the evaluation SDK in its own environment and publish the completed run. Use the shell where you set `ROUND3_RESULTS`, or set it again to the directory containing your `junit.xml`. Publishing imports saved results without repeating OpenAI calls or sending new invitations.

```sh
python3.13 -m venv .langfuse-local/venv
.langfuse-local/venv/bin/python -m pip install langfuse==5.2.0
.langfuse-local/venv/bin/python tests/publish_prompt_comparison.py "$ROUND3_RESULTS"
```

Open the printed Round 1 link and select the matching Round 2 run for a side-by-side comparison. Both links are also saved in `$ROUND3_RESULTS/langfuse.json`. Compare `calendar_checks_passed` for exercised trials and inspect the transcript and calendar calls for disagreements. A failed conversation assertion is a result worth publishing. For live latency, use `turns[].seconds` and `turns[].openai_seconds`.

### Reopen the original installation

On the machine that hosted the original run, open the local project on port 3307. If its VM is stopped, run `LIMA_HOME="$PWD/.langfuse-local/lima" .langfuse-local/runtime/bin/limactl start lf` from the repository root. The owner login is `owner@livex.local` and its generated password is stored locally in `LANGFUSE_INIT_USER_PASSWORD`. This localhost link opens the dashboard on that machine. [Original local project](http://localhost:3307/project/scheduling/experiments)
