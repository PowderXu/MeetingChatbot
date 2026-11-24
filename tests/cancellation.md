# Cancelling

Run the cancellation conversations with real OpenAI and Cal.com. Create matching meetings for the specified dates with controlled attendees and save their UIDs. After each turn, query Cal.com to verify both the intended target and any booking that should remain active. [Shared test setup](../docs/IMPLEMENTATION_PLAN.md#test-setup)

| Case | Conversation and setup | Pass criteria |
| --- | --- | --- |
| C1 · One clear match | “Cancel Thursday’s intro with Maya because she’s traveling.” Maya and Leo have separate intros. | Cancel Maya’s UID once with the supplied reason. A fresh agenda omits Maya and still shows Leo. |
| C2 · Ambiguous match | “Cancel my intro with Maya.” Two future intros match. “The Friday one.” | Show distinguishing dates or times and make zero writes on the first turn. Target only Friday’s UID. If Cal.com requires a reason, accept it in a follow-up and verify that Thursday’s meeting is still active. |
| C3 · No match | “Cancel my 4 pm with Leo.” There is a Leo intro at 3 pm and a Maya intro at 4 pm. | Explain that no booking matches both details. Leave both bookings active. |
| C4 · User changes their mind | While the bot is clarifying which intro to cancel, the user says “Never mind, keep both.” | Clear the pending cancellation and leave both bookings active. A later “the Friday one” by itself cannot complete the abandoned cancellation. |
| C5 · Already cancelled elsewhere | The bot displays Maya’s live meeting. Cancel it with a separate real Cal.com request before sending “cancel that one.” | Explain its existing cancellation using a fresh lookup or Cal.com’s already-cancelled response. Make at most one agent cancellation attempt and keep unrelated meetings intact. [Cal.com issue 24125](https://github.com/calcom/cal.diy/issues/24125) |
| C6 · Keep Tuesday and cancel both Mondays | “What's ny schedule next week?” → “Please just keep the Tuesday meeting, they are all the same person.” → “Confirm.” | The existing live regression uses Maya at 9:30 AM Monday, Maya Chen at 10 AM Monday, and Maya Chen at 2 PM Tuesday. Preview both cancellations and protect Tuesday. Exercise two successful cancellations and a real HTTP rejection for the second test request. Show the outcome for each meeting. Repeating “confirm” sends no additional writes. [Executable conversation](test_round_two.py) |

## Live conversations for cancelling several meetings

[Executable cancellation cases](test_multiple_meetings.py) cover C7, both the confirmation and abandonment endings of C8, and the shared reason in C9.

| Case | What the user says, in turn order | What should happen |
| --- | --- | --- |
| C7 · Cancel two named meetings directly | With Maya at 9:30 AM Monday, Leo at 10 AM Monday, and Hellen at 2 PM Tuesday, say “Cancel my Monday 9:30 with Maya and my Tuesday 2 o’clock with Hellen. I'll be out of town.” → “Yes, cancel both.” | Preview Maya and Hellen and show the supplied reason. Send zero cancellations before approval. Afterwards both named meetings are cancelled once and Leo’s Monday 10 AM booking is still active. This request has no “keep” clause. |
| C8 · “Both” after three are listed | With three interviews on Thursday at 9 AM, 11 AM and 2 PM, ask “What interviews do I have on Thursday?” → “Cancel both of them.” → “I meant the two in the morning. We filled the role.” | Ask which two and make zero writes. Preview only 9 and 11 AM and show the reason. From that preview test two endings in separate runs. “Yes, those two” cancels just the morning interviews. “Actually, never mind” leaves all three active. |
| C9 · Shared reason in the first message | With Hellen Li on Tuesday and Maya Chen on Wednesday, say “ancel all the meetings this week, because I have already caught up with them”, then “COnfirm”. | Show both meetings and the shared reason before writing. Send that same reason on each cancellation, verify both outcomes, and send no additional cancellations on a repeated confirmation. |

The C7 correction variant starts with “Cancel my 9:30 and 10:00 meetings on Monday.” After the two-meeting preview, say “Actually, just the 9:30 one. I'll be out of town.” Confirm the revised preview and verify in Cal.com that 10 AM is still booked.
