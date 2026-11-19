import io
import json
from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from assistant import decide, evals, openrouter
from assistant.decide import DONT_NOTIFY, Trip, decide_notification

EST = timezone(timedelta(hours=-5))
TRIP = Trip(
    route="Blue",
    stop="Grove Street",
    needed_at=datetime(2026, 11, 16, 9, 0, tzinfo=EST),
    walking_minutes=10,
    official_arrival=datetime(2026, 11, 16, 8, 45, tzinfo=EST),
    predicted_arrival=datetime(2026, 11, 16, 8, 50, tzinfo=EST),
)
GOOD = {"notify": True, "notify_at": "2026-11-16T08:35:00-05:00", "message": "Leave by 8:40."}


def reply(data):
    """What urlopen returns for an OpenRouter answer carrying this content."""
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": data}}]}).encode())


@override_settings(OPENROUTER_API_KEY="test-key", OPENROUTER_MODEL="test/model")
@mock.patch("assistant.openrouter.urlopen")
class OpenRouterTests(SimpleTestCase):
    def test_sends_the_key_and_model_and_returns_the_reply(self, urlopen):
        urlopen.return_value.__enter__.return_value = reply("hello")
        self.assertEqual(openrouter.chat([{"role": "user", "content": "hi"}]), "hello")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "test/model")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])

    @override_settings(OPENROUTER_API_KEY="")
    def test_no_key_means_no_request(self, urlopen):
        with self.assertRaises(openrouter.OpenRouterError):
            openrouter.chat([])
        urlopen.assert_not_called()

    def test_network_and_http_failures_become_one_error(self, urlopen):
        for failure in (
            URLError("down"),
            TimeoutError(),
            HTTPError("u", 429, "slow down", {}, None),
        ):
            urlopen.side_effect = failure
            with self.assertRaises(openrouter.OpenRouterError):
                openrouter.chat([])

    def test_an_answer_that_is_not_json_or_has_no_message_is_an_error(self, urlopen):
        for body in (b"<html>oops</html>", b"{}", b'{"choices": []}'):
            urlopen.return_value.__enter__.return_value = io.BytesIO(body)
            with self.assertRaises(openrouter.OpenRouterError):
                openrouter.chat([])

    def test_the_key_never_appears_in_the_error(self, urlopen):
        urlopen.side_effect = URLError("Bearer test-key rejected")
        with self.assertRaises(openrouter.OpenRouterError) as caught:
            openrouter.chat([])
        self.assertNotIn("test-key", str(caught.exception))


@mock.patch("assistant.decide.openrouter.chat")
class DecideTests(SimpleTestCase):
    def test_a_good_answer_becomes_a_decision_in_utc(self, chat):
        chat.return_value = json.dumps(GOOD)
        decision = decide_notification(TRIP)
        self.assertTrue(decision.notify)
        self.assertEqual(decision.notify_at, datetime(2026, 11, 16, 13, 35, tzinfo=timezone.utc))
        self.assertEqual(decision.notify_at.utcoffset(), timedelta(0))
        self.assertEqual(decision.message, "Leave by 8:40.")

    def test_a_no_is_a_no(self, chat):
        chat.return_value = '{"notify": false, "notify_at": null, "message": ""}'
        self.assertEqual(decide_notification(TRIP), DONT_NOTIFY)

    def test_the_prompt_has_the_trip_in_eastern_time(self, chat):
        chat.return_value = json.dumps(GOOD)
        utc = decide_replace(TRIP, needed_at=TRIP.needed_at.astimezone(timezone.utc))
        decide_notification(utc)  # given in UTC, shown to the model in Eastern
        system, user = chat.call_args.args[0]
        self.assertEqual(system["role"], "system")
        for expected in ("Blue", "Grove Street", "2026-11-16T09:00-05:00", "10 minutes",
                         "2026-11-16T08:45-05:00", "2026-11-16T08:50-05:00",
                         "Leave for the stop by: 2026-11-16T08:40-05:00"):
            self.assertIn(expected, user["content"])

    def test_a_missing_prediction_is_said_so(self, chat):
        chat.return_value = json.dumps(GOOD)
        decide_notification(decide_replace(TRIP, predicted_arrival=None))
        self.assertIn("no prediction", chat.call_args.args[0][1]["content"])

    def test_answers_that_dont_fit_count_as_dont_notify_and_are_logged(self, chat):
        bad_answers = {
            "not json": "Sure! You should leave at 8:40.",
            "a list": "[]",
            "missing key": json.dumps({"notify": True, "message": "hi"}),
            "extra key": json.dumps({**GOOD, "why": "because"}),
            "notify as a string": json.dumps({**GOOD, "notify": "yes"}),
            "no message": json.dumps({**GOOD, "message": "  "}),
            "message too long": json.dumps({**GOOD, "message": "x" * 161}),
            "time as a number": json.dumps({**GOOD, "notify_at": 1763300000}),
            "time that isn't a time": json.dumps({**GOOD, "notify_at": "soon"}),
            "time without an offset": json.dumps({**GOOD, "notify_at": "2026-11-16T08:35:00"}),
            "after the rider must be there": json.dumps({**GOOD, "notify_at": "2026-11-16T09:30:00-05:00"}),
            "no content at all": None,
        }
        for name, answer in bad_answers.items():
            with self.subTest(name):
                chat.return_value = answer
                with self.assertLogs("assistant.decide", "WARNING"):
                    self.assertEqual(decide_notification(TRIP), DONT_NOTIFY)

    def test_openrouter_trouble_counts_as_dont_notify_and_is_logged(self, chat):
        chat.side_effect = openrouter.OpenRouterError("down")
        with self.assertLogs("assistant.decide", "WARNING"):
            self.assertEqual(decide_notification(TRIP), DONT_NOTIFY)

    def test_strict_mode_lets_an_outage_through_instead_of_hiding_it(self, chat):
        chat.side_effect = openrouter.OpenRouterError("down")
        with self.assertRaises(openrouter.OpenRouterError):
            decide_notification(TRIP, strict=True)


def decide_replace(trip, **changes):
    from dataclasses import replace
    return replace(trip, **changes)


class EvaluationSetTests(SimpleTestCase):
    """The evaluation set, run like tests: OpenRouter is mocked with each case's good answer."""

    def test_there_are_at_least_five_cases_with_clear_answers_both_ways(self):
        self.assertGreaterEqual(len(evals.CASES), 5)
        self.assertEqual({c["expect"] for c in evals.CASES}, {True, False, None})

    def test_every_case_passes_with_the_mocked_model(self):
        for case in evals.CASES:
            with self.subTest(case["name"]):
                with mock.patch("assistant.decide.openrouter.chat", return_value=case["mock_reply"]) as chat:
                    decision = decide_notification(case["trip"])
                chat.assert_called_once()
                ok, why = evals.check(case, decision)
                self.assertTrue(ok, why)

    def test_the_scoring_catches_wrong_decisions(self):
        quiet = next(c for c in evals.CASES if c["expect"] is False)
        loud = next(c for c in evals.CASES if c["expect"] is True)
        either = next(c for c in evals.CASES if c["expect"] is None)
        on_time = decide.Decision(True, decide.leave_by(loud["trip"]), "hi")

        self.assertFalse(evals.check(quiet, decide.Decision(True, decide.leave_by(quiet["trip"]), "hi"))[0])
        self.assertFalse(evals.check(loud, DONT_NOTIFY)[0])
        self.assertTrue(evals.check(loud, on_time)[0])
        self.assertTrue(evals.check(quiet, DONT_NOTIFY)[0])
        # judgment calls accept either answer...
        self.assertTrue(evals.check(either, DONT_NOTIFY)[0])
        self.assertTrue(evals.check(either, decide.Decision(True, decide.leave_by(either["trip"]), "hi"))[0])

    def test_a_notification_after_the_leave_by_time_always_fails(self):
        for case in (c for c in evals.CASES if decide.leave_by(c["trip"]) is not None):
            with self.subTest(case["name"]):
                late = decide.Decision(True, decide.leave_by(case["trip"]) + timedelta(minutes=1), "hi")
                self.assertFalse(evals.check(case, late)[0])


class EvaluateCommandTests(SimpleTestCase):
    @override_settings(OPENROUTER_API_KEY="")
    def test_it_needs_a_key(self):
        with mock.patch("assistant.openrouter.urlopen") as urlopen:
            with self.assertRaises(CommandError):
                call_command("evaluate_assistant", stdout=io.StringIO())
        urlopen.assert_not_called()

    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_an_unreachable_model_is_an_error_not_a_score(self):
        out = io.StringIO()
        with mock.patch("assistant.openrouter.urlopen", side_effect=URLError("no route")):
            with self.assertRaisesRegex(CommandError, "nothing was evaluated"):
                call_command("evaluate_assistant", stdout=out)
        self.assertNotIn("PASS", out.getvalue())

    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_it_scores_every_case(self):
        replies = iter(c["mock_reply"] for c in evals.CASES)
        out = io.StringIO()
        with mock.patch("assistant.decide.openrouter.chat", side_effect=lambda *a, **k: next(replies)):
            call_command("evaluate_assistant", stdout=out)
        self.assertIn(f"{len(evals.CASES)}/{len(evals.CASES)} passed", out.getvalue())
