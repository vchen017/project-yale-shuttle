"""The hourly check that the AI answers (#159)."""

import json
import logging
from unittest import mock

from django.test import TestCase, override_settings

from assistant import evals, openrouter
from jobs import runner
from jobs.models import JobRun
from jobs.registry import all_jobs

QUIET = {"role": "assistant", "content": evals.QUIET}
OFFICIAL_CALL = {
    "id": "call-1", "type": "function", "function": {"name": "get_official_arrival", "arguments": "{}"},
}


def check(test):
    """Run the check through the runner, as the job clock's runs do. Returns
    the run, and what the runner logged, tracebacks included: on GitHub, that
    log is the workflow's, which anyone can read."""
    with test.assertLogs("jobs.runner", "INFO") as logs:
        run = runner.run(all_jobs()["check_ai"], trigger="test")
    return run, "\n".join(logging.Formatter().format(record) for record in logs.records)


def last_line(run):
    """What /admin/ → Scheduled jobs shows as a failed run's summary."""
    return run.output.strip().splitlines()[-1]


@override_settings(OPENROUTER_API_KEY="test-key", OPENROUTER_MODEL="test/model")
class CheckAITests(TestCase):
    def test_it_runs_hourly_between_the_refresh_and_the_trip_watch(self):
        jobs = all_jobs()
        self.assertEqual(jobs["check_ai"].schedule, "43 * * * *")
        self.assertEqual(jobs["refresh_calendars"].schedule, "41 * * * *")

    @mock.patch("assistant.agent.openrouter.chat_message", return_value=QUIET)
    def test_an_answer_is_a_succeeded_run_that_says_what_it_answered(self, chat_message):
        run, _ = check(self)
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        self.assertRegex(run.output, r"^The AI answered in \d+\.\d s \(test/model\): don't notify$")
        # Asked as the trip watch asks: the agent, with its tools.
        self.assertIsNotNone(chat_message.call_args.kwargs["tools"])

    @mock.patch("assistant.agent.LiveData", side_effect=AssertionError("it read the live feed"))
    @mock.patch("assistant.agent.openrouter.chat_message", side_effect=[
        {"role": "assistant", "content": None, "tool_calls": [OFFICIAL_CALL]}, QUIET,
    ])
    def test_its_tools_answer_from_the_fixed_trip_never_the_live_feed(self, chat_message, live_data):
        run, _ = check(self)
        self.assertEqual(run.status, JobRun.Status.SUCCEEDED)
        tool_reply = chat_message.call_args_list[1].args[0][-1]
        self.assertEqual(tool_reply["role"], "tool")
        # The evaluation trip's bus comes 90 minutes before the rider's deadline.
        self.assertEqual(json.loads(tool_reply["content"])["minutes_from_arrival_to_deadline"], 90)

    @mock.patch("assistant.agent.openrouter.chat_message", side_effect=openrouter.OpenRouterError(
        "OpenRouter request failed (HTTPError: Payment Required)"
    ))
    def test_an_openrouter_error_is_a_failed_run_that_says_why(self, chat_message):
        run, _ = check(self)
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertEqual(
            last_line(run),
            "assistant.jobs.NoAnswer: The AI didn't answer (test/model): "
            "OpenRouter request failed (HTTPError: Payment Required)",
        )

    @mock.patch("assistant.agent.openrouter.chat_message", return_value={
        "role": "assistant", "content": '{"notify": "maybe"}',
    })
    def test_an_answer_that_doesnt_fit_is_a_failed_run(self, chat_message):
        with self.assertLogs("assistant.agent", "WARNING"):  # the agent logs what didn't fit
            run, _ = check(self)
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertIn("The AI didn't answer (test/model): keys are ['notify']", last_line(run))

    @override_settings(OPENROUTER_API_KEY="")
    @mock.patch("assistant.openrouter.urlopen")
    def test_a_missing_key_is_a_failed_run(self, urlopen):
        run, _ = check(self)
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertTrue(last_line(run).endswith("OPENROUTER_API_KEY is not set"))
        urlopen.assert_not_called()

    @override_settings(OPENROUTER_API_KEY="sk-or-v1-made-up\nkey")
    @mock.patch("assistant.openrouter.urlopen")
    def test_a_malformed_key_fails_without_its_value_in_the_run_log_or_the_log(self, urlopen):
        run, logged = check(self)
        self.assertEqual(run.status, JobRun.Status.FAILED)
        self.assertTrue(last_line(run).endswith("OPENROUTER_API_KEY has a space or line break in it"))
        for text in (run.output, logged):
            self.assertNotIn("made-up", text)
        urlopen.assert_not_called()
