import os
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

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


class HomeTests(TestCase):
    def test_home_page_is_public(self):
        self.assertContains(self.client.get("/"), "Yale Shuttle")
