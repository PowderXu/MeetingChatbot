"""Paired prompt experiment. Run explicitly with RUN_LIVE_TESTS and ROUND3_RESULTS."""

import hashlib
import json
import os
import re
from pathlib import Path
from time import perf_counter

import pytest

import scheduling
import test_round_one as baseline
import test_round_two as regression
from cal_client import CalClient, CalendarError
from live_support import live, ui


# Change the wording of the opening request and preserve its scheduling facts.
CASES = {
    "B1": (baseline.test_b1_book_exact_time, "Book a 30-minute intro", "Please book a half-hour intro"),
    "V1": (baseline.test_v1_tomorrows_agenda, "What's on my calendar tomorrow?", "Show tomorrow's scheduled meetings."),
    "C1": (baseline.test_c1_cancel_one_match, "Cancel my intro with Maya", "Please cancel my meeting with Maya"),
    "R1": (baseline.test_r1_reschedule_later, "Move my ", "Please reschedule my "),
    "C2": (regression.test_c2_disambiguate, "Cancel my intro with Maya.", "I'd like to cancel my meeting with Maya."),
    "B6": (regression.test_slot_taken, "I want to book a meeting.", "I'm looking for a time to book a meeting."),
    "R4": (regression.test_r4_followup_uid, "Move my Maya meeting", "Please reschedule my Maya meeting"),
}
VERSIONS = {"round1": "d26d920dfc3f", "round2": "e15a17ae76a1"}


def trial_ids():
    for case in CASES:
        for phrasing in (1, 2):
            for repeat in (1, 2):
                # Alternate which prompt sees the calendar first within a pair.
                versions = list(VERSIONS)
                if (phrasing + repeat) % 2:
                    versions.reverse()
                for version in versions:
                    yield f"{case}-p{phrasing}-r{repeat}-{version}"


@pytest.mark.skipif(not os.getenv("ROUND3_RESULTS"), reason="Prompt comparison is opt-in")
@pytest.mark.parametrize("trial", list(trial_ids()))
def test_prompt_trial(trial, live, request, monkeypatch, record_property):
    case, phrasing, repeat, version = trial.split("-")
    scenario, original, alternate = CASES[case]
    prompt = (Path(__file__).parent / "prompts" / f"{version}.txt").read_text()
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    assert digest.startswith(VERSIONS[version])
    monkeypatch.setattr(scheduling, "INSTRUCTIONS", prompt)
    live.days_ahead = 1 if case in {"B1", "V1"} else 3 + 4 * int(phrasing[1]) + int(repeat[1])
    record_property("trial", trial)
    evidence = {"trial": trial, "prompt_sha256": digest, "model": os.getenv("OPENAI_MODEL", scheduling.DEFAULT_MODEL),
                "turns": []}
    active_turn = None
    real_chat = scheduling.chat_turn
    real_request = CalClient.request
    real_response = scheduling.OpenAIResponsesModel.get_response

    async def timed_model(self, *args, **kwargs):
        start = perf_counter()
        try:
            return await real_response(self, *args, **kwargs)
        finally:
            if active_turn is not None:
                active_turn["openai_seconds"] += perf_counter() - start

    def observed_request(self, method, path, version, **kwargs):
        if active_turn is None:
            return real_request(self, method, path, version, **kwargs)
        call = {"method": method, "path": path}
        active_turn["calendar"].append(call)
        start = perf_counter()
        try:
            result = real_request(self, method, path, version, **kwargs)
            if path == "slots":
                call["available_starts"] = [s["start"] for slots in result["data"].values() for s in slots]
            if method == "POST":
                call["booking"] = {k: result["data"].get(k) for k in ("uid", "start", "status")}
            return result
        except CalendarError as error:
            call["http_status"] = error.status_code
            raise
        finally:
            call["seconds"] = round(perf_counter() - start, 3)

    def observed_chat(state, message, *args, **kwargs):
        nonlocal active_turn
        if not evidence["turns"] and phrasing == "p2":
            assert message.startswith(original), "Update the alternate phrasing for the changed scenario"
            message = alternate + message[len(original):]
        turn = {"user": redact(message), "calendar": [], "openai_seconds": 0.0}
        evidence["turns"].append(turn)
        active_turn = turn
        start = perf_counter()
        try:
            answer = real_chat(state, message, *args, **kwargs)
            turn["assistant"] = redact(answer)
            return answer
        finally:
            turn["seconds"] = round(perf_counter() - start, 3)
            turn["openai_seconds"] = round(turn["openai_seconds"], 3)
            active_turn = None

    monkeypatch.setattr(scheduling, "chat_turn", observed_chat)
    monkeypatch.setattr(regression, "chat_turn", observed_chat)
    monkeypatch.setattr(CalClient, "request", observed_request)
    monkeypatch.setattr(scheduling.OpenAIResponsesModel, "get_response", timed_model)
    try:
        if case == "B6":
            scenario(live, monkeypatch, "B6-live")
        elif case == "R4":
            scenario(live, record_property)
        elif case == "C2":
            scenario(live)
        else:
            scenario(live, request.getfixturevalue("ui"))
    finally:
        path = Path(os.environ["ROUND3_RESULTS"]) / f"{trial}.json"
        path.write_text(json.dumps(evidence, indent=2))


def redact(text):
    return re.sub(r"[\w.+%-]+@[\w.-]+\.[\w-]+", "[address]", text)
