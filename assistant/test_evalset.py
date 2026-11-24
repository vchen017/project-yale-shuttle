"""The evaluation set as a whole (#39): its size and coverage, how every case
becomes a request, and what happens to every case when the model's answer is
malformed or the model can't be reached. The model is mocked everywhere, so
no test reaches the network. In its own file so it doesn't collide with other
pull requests."""

import io
import json
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from assistant import decide, evals, openrouter
from assistant.decide import DONT_NOTIFY, decide_notification

from .test_agent import LOOK_EVERYTHING_UP, answers


class CoverageTests(SimpleTestCase):
    def test_there_are_at_least_twenty_cases_with_distinct_names(self):
        self.assertGreaterEqual(len(evals.CASES), 20)
        names = [case["name"] for case in evals.CASES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_situation_is_covered(self):
        by_kind = {kind: [c for c in evals.CASES if c["kind"] == kind] for kind in evals.KINDS}
        for kind, cases in by_kind.items():
            self.assertGreaterEqual(len(cases), 2, f"too few {kind!r} cases")

    def test_there_are_clear_answers_both_ways_and_judgment_calls(self):
        self.assertEqual({c["expect"] for c in evals.CASES}, {True, False, None})

    def test_the_feed_down_cases_have_no_official_time(self):
        for case in (c for c in evals.CASES if c["kind"] == "feed down"):
            self.assertIsNone(case["trip"].official_arrival, case["name"])

    def test_the_no_prediction_cases_have_no_prediction(self):
        for case in (c for c in evals.CASES if c["kind"] == "no prediction"):
            self.assertIsNone(case["trip"].predicted_arrival, case["name"])

    def test_a_late_case_is_really_late_by_our_prediction_or_the_official_time(self):
        for case in (c for c in evals.CASES if c["kind"] == "late"):
            trip = case["trip"]
            arrival = trip.predicted_arrival or trip.official_arrival
            self.assertGreaterEqual(arrival, trip.official_arrival, case["name"])

    def test_the_scoring_rejects_a_notification_when_there_is_no_arrival_time(self):
        nothing = next(c for c in evals.CASES if decide.leave_by(c["trip"]) is None)
        ok, why = evals.check(nothing, decide.Decision(True, nothing["trip"].needed_at, "hi"))
        self.assertFalse(ok)
        self.assertIn("no arrival time", why)

    def test_a_notification_after_the_leave_by_time_fails_in_every_case_that_has_one(self):
        for case in evals.CASES:
            latest = decide.leave_by(case["trip"])
            if latest is None:
                continue
            with self.subTest(case["name"]):
                late = decide.Decision(True, latest + evals.timedelta(minutes=1), "hi")
                self.assertFalse(evals.check(case, late)[0])


class RequestTests(SimpleTestCase):
    """How each case is put to the model."""

    def messages(self, case):
        with mock.patch("assistant.decide.openrouter.chat", return_value=case["mock_reply"]) as chat:
            decide_notification(case["trip"])
        chat.assert_called_once()
        system, user = chat.call_args.args[0]
        self.assertEqual((system["role"], user["role"]), ("system", "user"))
        return system["content"], user["content"]

    def test_every_case_asks_the_model_for_the_agreed_reply_format(self):
        for case in evals.CASES:
            with self.subTest(case["name"]):
                system, _ = self.messages(case)
                self.assertIn(decide.REPLY_FORMAT, system)

    def test_every_case_gives_the_model_its_route_stop_walk_and_deadline_in_eastern_time(self):
        for case in evals.CASES:
            trip = case["trip"]
            with self.subTest(case["name"]):
                _, user = self.messages(case)
                self.assertIn(f"Route: {trip.route}", user)
                self.assertIn(f"Stop: {trip.stop}", user)
                self.assertIn(f"Walking time to the stop: {trip.walking_minutes} minutes", user)
                self.assertIn(f"by: {decide._local(trip.needed_at)}", user)

    def test_missing_times_are_said_so_never_left_blank_or_made_up(self):
        for case in evals.CASES:
            trip = case["trip"]
            with self.subTest(case["name"]):
                _, user = self.messages(case)
                if trip.official_arrival is None:
                    self.assertIn("Official arrival of the next bus: none (the live feed is unavailable)", user)
                else:
                    self.assertIn(f"next bus: {decide._local(trip.official_arrival)}", user)
                if trip.predicted_arrival is None:
                    self.assertIn("Our predicted arrival: none (no prediction)", user)
                else:
                    self.assertIn(f"Our predicted arrival: {decide._local(trip.predicted_arrival)}", user)
                latest = decide.leave_by(trip)
                if latest is None:
                    self.assertIn("Leave for the stop by: none (no arrival time to work from)", user)
                else:
                    self.assertIn(f"Leave for the stop by: {decide._local(latest)}", user)

    def test_our_prediction_beats_the_official_time_in_the_leave_by_time(self):
        for case in (c for c in evals.CASES if c["trip"].predicted_arrival):
            trip = case["trip"]
            with self.subTest(case["name"]):
                self.assertEqual(decide.leave_by(trip), trip.predicted_arrival - evals.timedelta(minutes=trip.walking_minutes))


MALFORMED = {
    "empty": "",
    "prose": "You should probably leave soon.",
    "a JSON array": "[]",
    "null": "null",
    "an empty object": "{}",
    "missing keys": '{"notify": true}',
    "extra keys": '{"notify": false, "notify_at": null, "message": "", "why": "x"}',
    "notify not a boolean": '{"notify": "yes", "notify_at": null, "message": ""}',
    "an empty message": '{"notify": true, "notify_at": "2026-11-16T08:00:00-05:00", "message": "  "}',
    "a message that is too long": json.dumps(
        {"notify": True, "notify_at": "2026-11-16T08:00:00-05:00", "message": "x" * (decide.MAX_MESSAGE_LENGTH + 1)}
    ),
    "a time with no offset": '{"notify": true, "notify_at": "2026-11-16T08:00:00", "message": "hi"}',
    "a time that isn't a time": '{"notify": true, "notify_at": "soon", "message": "hi"}',
    "a time that isn't text": '{"notify": true, "notify_at": 830, "message": "hi"}',
    "a time after the rider's deadline": '{"notify": true, "notify_at": "2026-11-16T23:00:00-05:00", "message": "hi"}',
    "a message that isn't text": '{"notify": true, "notify_at": "2026-11-16T08:00:00-05:00", "message": 7}',
}


class MalformedAnswerTests(SimpleTestCase):
    """The model says something we can't use, for every case: no notification, no crash, a log line."""

    def test_every_case_with_every_malformed_answer_through_the_one_shot_decision(self):
        for case in evals.CASES:
            for what, text in MALFORMED.items():
                with self.subTest(case=case["name"], answer=what):
                    with mock.patch("assistant.decide.openrouter.chat", return_value=text):
                        with self.assertLogs("assistant.decide", level="WARNING"):
                            self.assertEqual(decide_notification(case["trip"]), DONT_NOTIFY)

    def test_every_case_with_every_malformed_answer_through_the_agent(self):
        for case in evals.CASES:
            for what, text in MALFORMED.items():
                with self.subTest(case=case["name"], answer=what):
                    # Prose gets one more turn (tools off) before the agent gives up, so repeat it.
                    replies = [LOOK_EVERYTHING_UP] + [answers(text)] * 3
                    with mock.patch("assistant.agent.openrouter.chat_message", side_effect=replies):
                        with self.assertLogs("assistant.agent", level="WARNING"):
                            self.assertEqual(evals.agent_decide()(case["trip"]), DONT_NOTIFY)

    def test_a_reply_that_isnt_even_a_string_is_not_a_notification(self):
        for case in evals.CASES:
            with self.subTest(case["name"]):
                with mock.patch("assistant.decide.openrouter.chat", return_value=None):
                    with self.assertLogs("assistant.decide", level="WARNING"):
                        self.assertEqual(decide_notification(case["trip"]), DONT_NOTIFY)

    def test_with_no_arrival_time_at_all_even_a_well_formed_notification_is_refused(self):
        nothing = next(c for c in evals.CASES if decide.leave_by(c["trip"]) is None)
        good = '{"notify": true, "notify_at": "2026-11-16T08:00:00-05:00", "message": "Leave now."}'
        with mock.patch("assistant.decide.openrouter.chat", return_value=good):
            with self.assertLogs("assistant.decide", level="WARNING"):
                self.assertEqual(decide_notification(nothing["trip"]), DONT_NOTIFY)


class UnreachableModelTests(SimpleTestCase):
    """Timeouts and outages, for every case, at the HTTP call itself."""

    FAILURES = {
        "a timeout": TimeoutError("timed out"),
        "no route": URLError("no route to host"),
        "a server error": HTTPError("https://openrouter.ai", 500, "boom", {}, None),
        "a rate limit": HTTPError("https://openrouter.ai", 429, "slow down", {}, None),
        "a dropped connection": ConnectionResetError("reset"),
    }

    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_every_failure_for_every_case_is_dont_notify_and_never_raises(self):
        for case in evals.CASES:
            for what, error in self.FAILURES.items():
                with self.subTest(case=case["name"], failure=what):
                    with mock.patch("assistant.openrouter.urlopen", side_effect=error):
                        with self.assertLogs("assistant.decide", level="WARNING"):
                            self.assertEqual(decide_notification(case["trip"]), DONT_NOTIFY)

    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_strict_mode_reports_every_failure_instead_of_scoring_it(self):
        case = evals.CASES[0]
        for what, error in self.FAILURES.items():
            with self.subTest(failure=what):
                with mock.patch("assistant.openrouter.urlopen", side_effect=error):
                    with self.assertRaises(openrouter.OpenRouterError):
                        decide_notification(case["trip"], strict=True)

    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_the_agent_treats_every_failure_the_same_way(self):
        for what, error in self.FAILURES.items():
            with self.subTest(failure=what):
                with mock.patch("assistant.openrouter.urlopen", side_effect=error):
                    with self.assertLogs("assistant.agent", level="WARNING"):
                        self.assertEqual(evals.agent_decide()(evals.CASES[0]["trip"]), DONT_NOTIFY)


class ScoreReportTests(SimpleTestCase):
    """The command reports how many decisions match, so models and prompts can be compared."""

    @override_settings(OPENROUTER_API_KEY="test-key")
    def run_with(self, reply_for):
        replies = iter(reply_for(case) for case in evals.CASES)
        out = io.StringIO()
        with mock.patch("assistant.decide.openrouter.chat", side_effect=lambda *a, **k: next(replies)):
            try:
                call_command("evaluate_assistant", stdout=out)
            except CommandError:
                pass
        return out.getvalue()

    def test_a_model_that_never_notifies_misses_exactly_the_cases_that_need_one(self):
        needs_one = sum(c["expect"] is True for c in evals.CASES)
        output = self.run_with(lambda case: evals.QUIET)
        self.assertIn(f"{len(evals.CASES) - needs_one}/{len(evals.CASES)} passed", output)
        self.assertEqual(output.count("FAIL  "), needs_one)

    def test_a_model_that_always_notifies_misses_the_quiet_cases_it_can_be_told_about(self):
        # The case with no arrival time at all is refused in code, so it passes.
        too_much = sum(c["expect"] is False and decide.leave_by(c["trip"]) is not None for c in evals.CASES)
        early = evals.notify("2026-11-16T05:00:00-05:00", "Heads up.")
        output = self.run_with(lambda case: early)
        self.assertEqual(output.count("FAIL  "), too_much)

    def test_the_good_answers_score_every_case(self):
        output = self.run_with(lambda case: case["mock_reply"])
        self.assertIn(f"{len(evals.CASES)}/{len(evals.CASES)} passed", output)
