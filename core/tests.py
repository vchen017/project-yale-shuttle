import os
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

PASSWORDS = {"SEED_ADMIN_PASSWORD": "test-admin-pass-1", "SEED_RIDER_PASSWORD": "test-rider-pass-2"}


@mock.patch.dict(os.environ, PASSWORDS)
class SeedTests(TestCase):
    def seed(self):
        call_command("seed", stdout=StringIO())

    def test_creates_the_grader_accounts_and_is_safe_to_rerun(self):
        self.seed()
        self.seed()
        User = get_user_model()
        self.assertEqual(User.objects.count(), 2)
        self.assertTrue(User.objects.get(username="admin@example.com").is_staff)
        self.assertFalse(User.objects.get(username="rider@example.com").is_staff)
        self.assertTrue(self.client.login(username="admin@example.com", password="test-admin-pass-1"))
        self.assertTrue(self.client.login(username="rider@example.com", password="test-rider-pass-2"))

    def test_a_rerun_picks_up_a_changed_password(self):
        self.seed()
        with mock.patch.dict(os.environ, {"SEED_RIDER_PASSWORD": "a-new-pass-3"}):
            self.seed()
        self.assertTrue(self.client.login(username="rider@example.com", password="a-new-pass-3"))

    def test_refuses_to_run_without_passwords(self):
        with mock.patch.dict(os.environ, {"SEED_RIDER_PASSWORD": ""}):
            with self.assertRaisesMessage(CommandError, "SEED_RIDER_PASSWORD"):
                self.seed()


class PageTests(TestCase):
    def test_home_page_pitches_the_app_to_visitors(self):
        response = self.client.get("/")
        self.assertContains(response, "Never miss the Yale shuttle again.")
        self.assertContains(response, 'href="/signup/"')

    def test_home_page_greets_a_logged_in_rider(self):
        self.client.force_login(get_user_model().objects.create_user("rider@example.com"))
        self.assertContains(self.client.get("/"), "Welcome back, <strong>rider@example.com</strong>")

    def test_unknown_pages_get_a_friendly_404(self):
        self.assertContains(self.client.get("/no-such-page/"), "This stop doesn't exist", status_code=404)

    def test_server_errors_get_a_branded_page(self):
        self.assertIn("Something went wrong", render_to_string("500.html"))

    def test_admin_pages_say_yale_shuttle_in_yale_blue(self):
        response = self.client.get("/admin/login/")
        self.assertContains(response, "Yale Shuttle admin")
        self.assertContains(response, "--secondary: #00356b;")
        self.assertContains(response, "core/favicon.svg")

    def test_pages_have_link_previews_and_icons(self):
        response = self.client.get("/")
        self.assertContains(response, '<meta property="og:title" content="Yale Shuttle: never miss the Yale shuttle again">')
        self.assertContains(response, '<meta property="og:image" content="http://testserver/static/core/og-image.png">')
        self.assertContains(response, '<i class="bi bi-clock-history"></i>')

    def test_favicon_ico_points_to_our_icon(self):
        response = self.client.get("/favicon.ico")
        self.assertRedirects(response, "/static/core/favicon.svg", status_code=301, fetch_redirect_response=False)


class HealthTests(SimpleTestCase):
    """/healthz, for Render's health check and the promote workflow. SimpleTestCase
    fails on any database query, which proves it stays off the database."""

    def test_reports_ok_and_the_commit_render_is_running(self):
        with mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": "4231b6a9f0c1d2e3b4a5"}):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "commit": "4231b6a"})

    def test_is_never_cached(self):
        response = self.client.get("/healthz")
        self.assertIn("no-cache", response["Cache-Control"])

    def test_works_outside_render_with_no_commit(self):
        with mock.patch.dict(os.environ, {"RENDER_GIT_COMMIT": ""}):
            response = self.client.get("/healthz")
        self.assertEqual(response.json(), {"status": "ok", "commit": ""})

    def test_only_answers_reads(self):
        self.assertEqual(self.client.post("/healthz").status_code, 405)
