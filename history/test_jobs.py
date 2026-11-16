"""Tests for the history import's scheduled job and its GitHub token (#40).

In their own file, so they don't collide with open pull requests that add to
history/tests.py.
"""

import io
from unittest import mock

from django.test import SimpleTestCase, override_settings

from history import jobs, sources


class ImportHistoryJobTests(SimpleTestCase):
    @mock.patch("history.jobs.call_command")
    def test_imports_the_last_three_recorded_days_and_returns_the_summary(self, call_command):
        def fake_import(*args, stdout, **kwargs):
            stdout.write("2026-11-14: 288 polls imported\n")

        call_command.side_effect = fake_import
        summary = jobs.import_history()
        call_command.assert_called_once()
        self.assertEqual(call_command.call_args.args, ("import_history",))
        self.assertEqual(call_command.call_args.kwargs["days"], 3)
        self.assertEqual(summary, "2026-11-14: 288 polls imported")


class GitHubTokenTests(SimpleTestCase):
    def headers_for(self, url):
        with mock.patch("history.sources.urlopen", return_value=io.BytesIO(b"")) as urlopen:
            sources._text_stream(url)
        return dict(urlopen.call_args.args[0].header_items())

    @override_settings(GITHUB_TOKEN="test-token")
    def test_api_calls_send_the_token(self):
        headers = self.headers_for("https://api.github.com/repos/o/r/contents/data?ref=main")
        self.assertEqual(headers["Authorization"], "Bearer test-token")

    @override_settings(GITHUB_TOKEN="test-token")
    def test_file_downloads_dont_send_it(self):
        headers = self.headers_for("https://raw.githubusercontent.com/o/r/main/data/gaps.csv")
        self.assertNotIn("Authorization", headers)

    @override_settings(GITHUB_TOKEN="")
    def test_without_a_token_calls_go_out_signed_out(self):
        headers = self.headers_for("https://api.github.com/repos/o/r/contents/data?ref=main")
        self.assertNotIn("Authorization", headers)
