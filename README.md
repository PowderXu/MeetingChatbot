# Conversational scheduling assistant

A Streamlit app for viewing your weekly schedule and managing Cal.com meetings in plain English. It uses the OpenAI Agents SDK to interpret each message and the live Cal.com API to check openings and make changes.

[Watch the 1-minute 45-second demo](scheduling-assistant-demo.mp4). Follow two candidate interviews from the first conflicting time requests through booking, rescheduling, and an agenda check. The final exchange shows how to rebook an interview cancelled by mistake.

## Setup

From the repository root, install the dependencies using Python 3.13.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a `.env` file in the repository root with your API keys.

```dotenv
OPENAI_API_KEY=your_openai_api_key
CAL_API_KEY=your_cal_api_key
```

If your Cal.com account has exactly one visible 30-minute event type, the app selects it automatically. Otherwise, add `CAL_EVENT_TYPE_ID` with the numeric ID of the event type to use. You can also set `FOUNDER_TIME_ZONE` (default `America/New_York`) and `OPENAI_MODEL` (default `gpt-6-luna`).

## Run

```sh
streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Keep the terminal running and open the [local chatbot](http://127.0.0.1:8501/). The weekly schedule above the chat shows upcoming meetings for the configured event type in your calendar time zone. Use **Next week** to browse ahead or ask “What’s on my calendar tomorrow?” in chat. **Refresh** clears the conversation and returns to the current week. Bookings persist in Cal.com.

For a new booking, give the attendee's name, email and meeting time. The chatbot defaults to your calendar time zone. Specify another zone when needed. Review the meeting details, then reply “Confirm” to book, enter a correction, or reply “No thanks” to discard it.

If the same name and email already appear on an upcoming meeting, the preview shows the existing time before confirmation. With several matches it shows their count and the next three starts. You can confirm an additional booking as usual.

To move a meeting, review the original and proposed times, then reply “Confirm”. You can change the proposed date or decline the move. If a requested time is unavailable, the chatbot names any overlapping booking visible in your Cal.com agenda and tells you when it cannot verify the cause.

You can book, move, or cancel several appointments in one conversation. Try “Keep Tuesday’s interview and cancel both Monday interviews because we already caught up.” Review the affected meetings and shared reason before confirming. The assistant reports each outcome separately. A clear request to cancel one meeting can take effect immediately. If Cal.com requires a reason, the assistant asks for it.

## Tests

Run `RUN_LIVE_TESTS=0 python -m pytest -q` for local checks. For manual scenarios, live API tests, and the Langfuse prompt comparison, see the [testing guide](TESTING.md).

See the [verification results](IMPLEMENTATION_STATUS.md) for dated live runs, first-attempt scores, and reruns after fixes.
