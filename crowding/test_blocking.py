"""Limiting and blocking riders who post bad crowd reports (#19): at most 6
reports an hour, and admins can block and unblock a rider with a reason.
The Downtowner feed is mocked at transit.downtowner.urlopen and
`timezone.now` is frozen, so no test reaches the network."""

import datetime as dt
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import CrowdReport, ReportBlock
from .services import is_blocked
from .tests import NOW, Level, ReportsMixin, at, feed

REPORT = reverse("crowding-report")
BLOCKS = reverse("admin:crowding_reportblock_changelist")


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class HourlyLimitTests(ReportsMixin, TestCase):
    def post(self, route=1, level=Level.SEATS):
        return self.client.post(REPORT, {"route": route, "level": level}, follow=True)

    def fill(self, count, **kwargs):
        for minutes_ago in range(count):
            self.add(Level.SEATS, at(minutes_ago=11 + minutes_ago * 8), user=self.rider, **kwargs)

    def test_the_seventh_report_in_an_hour_is_refused_with_a_message(self, urlopen, now):
        urlopen.side_effect = feed()
        self.fill(6)  # 11 to 51 minutes ago, on route 1
        self.client.force_login(self.rider)

        response = self.post(15)

        self.assertContains(response, "You can send at most 6 reports an hour.")
        self.assertContains(response, "You can report again at 2:49 PM.")  # when the oldest, 51 minutes old, turns an hour old
        self.assertEqual(CrowdReport.objects.filter(user=self.rider).count(), 6)

    def test_six_reports_are_fine_and_the_limit_lifts_as_they_age(self, urlopen, now):
        urlopen.side_effect = feed()
        self.fill(5)
        self.client.force_login(self.rider)
        self.assertContains(self.post(15), "Thanks!")  # the sixth
        self.assertContains(self.post(1), "You can send at most 6 reports an hour.")

        now.return_value = NOW + dt.timedelta(minutes=18)  # the oldest, 43 minutes old, is now over an hour old
        self.assertContains(self.post(1), "Thanks!")

    def test_the_limit_is_per_rider_and_ignores_demo_reports(self, urlopen, now):
        urlopen.side_effect = feed()
        self.fill(6, is_sample=True)
        for minutes_ago in range(6):
            self.add(Level.SEATS, at(minutes_ago=15 + minutes_ago), user=self.other)
        self.client.force_login(self.rider)
        self.assertContains(self.post(15), "Thanks!")

    def test_removed_reports_still_count_toward_the_limit(self, urlopen, now):
        urlopen.side_effect = feed()
        self.fill(6)
        CrowdReport.objects.update(removed_at=NOW)
        self.client.force_login(self.rider)
        self.assertContains(self.post(15), "at most 6 reports an hour")


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class BlockingTests(ReportsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("admin@example.com", password="an-admin-password-1")

    def block(self, user, reason="Posts nonsense"):
        return ReportBlock.objects.create(user=user, reason=reason, blocked_by=self.admin)

    def test_a_blocked_rider_sees_a_message_instead_of_the_form(self, urlopen, now):
        urlopen.side_effect = feed()
        self.block(self.rider)
        self.client.force_login(self.rider)

        response = self.client.get(reverse("crowding"))

        self.assertContains(response, "You can't send crowding reports right now")
        self.assertNotContains(response, 'name="level"')
        self.assertNotContains(response, "Posts nonsense")  # the reason is for admins
        self.client.force_login(self.other)  # other riders still see the form
        self.assertContains(self.client.get(reverse("crowding")), 'name="level"')

    def test_a_blocked_rider_cant_post_a_report(self, urlopen, now):
        urlopen.side_effect = feed()
        self.block(self.rider)
        self.client.force_login(self.rider)

        response = self.client.post(REPORT, {"route": 1, "level": Level.FULL})

        self.assertContains(response, "You can't send crowding reports right now", status_code=403)
        self.assertFalse(CrowdReport.objects.exists())

    def test_admins_block_a_rider_with_a_reason(self, urlopen, now):
        self.client.force_login(self.admin)
        url = reverse("admin:crowding_reportblock_add")

        missing = self.client.post(url, {"user": self.rider.pk, "reason": ""})
        self.assertContains(missing, "This field is required")
        self.assertFalse(is_blocked(self.rider))

        self.client.post(url, {"user": self.rider.pk, "reason": "Said every bus was full, all day"})

        block = ReportBlock.objects.get()
        self.assertEqual((block.user, block.reason, block.blocked_by, block.blocked_at), (self.rider, "Said every bus was full, all day", self.admin, NOW))
        self.assertTrue(is_blocked(self.rider))
        self.assertContains(self.client.get(BLOCKS), "Said every bus was full, all day")

    def test_a_rider_cant_be_blocked_twice(self, urlopen, now):
        self.block(self.rider)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:crowding_reportblock_add"), {"user": self.rider.pk, "reason": "again"})
        self.assertContains(response, "This rider is already blocked.")
        self.assertEqual(ReportBlock.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.block(self.rider)  # the database says so too

    def test_admins_unblock_and_the_rider_can_report_again(self, urlopen, now):
        urlopen.side_effect = feed()
        block = self.block(self.rider)
        self.client.force_login(self.admin)

        response = self.client.post(
            BLOCKS, {"action": "unblock", "_selected_action": [block.pk]}, follow=True
        )

        self.assertContains(response, "Unblocked 1 rider.")
        block.refresh_from_db()
        self.assertEqual((block.unblocked_at, block.unblocked_by), (NOW, self.admin))
        self.assertFalse(is_blocked(self.rider))
        self.assertContains(response, "Unblocked Nov 18, 2:40 PM")
        self.client.force_login(self.rider)
        self.assertContains(
            self.client.post(REPORT, {"route": 1, "level": Level.SEATS}, follow=True), "Thanks!"
        )
        # Unblocking again does nothing; blocking again later makes a new row.
        self.client.force_login(self.admin)
        self.assertContains(
            self.client.post(BLOCKS, {"action": "unblock", "_selected_action": [block.pk]}, follow=True),
            "Those riders weren&#x27;t blocked.",
        )
        self.block(self.rider, "Back at it")
        self.assertEqual(ReportBlock.objects.count(), 2)
        self.assertTrue(is_blocked(self.rider))

    def test_riders_cant_open_the_block_pages_or_unblock_themselves(self, urlopen, now):
        block = self.block(self.rider)
        for who in (None, self.rider):
            if who:
                self.client.force_login(who)
            response = self.client.get(BLOCKS)
            self.assertRedirects(response, f"{reverse('admin:login')}?next={BLOCKS}", fetch_redirect_response=False)
            self.client.post(BLOCKS, {"action": "unblock", "_selected_action": [block.pk]})
        self.assertTrue(is_blocked(self.rider))

    def test_blocks_arent_bulk_deletable_but_deleting_a_rider_still_works(self, urlopen, now):
        self.block(self.rider)
        self.client.force_login(self.admin)
        actions = [name for name, _ in self.client.get(BLOCKS).context["action_form"].fields["action"].choices]
        self.assertEqual(actions[1:], ["unblock"])  # after the "---------" choice

        url = reverse("admin:accounts_user_delete", args=[self.rider.pk])
        self.assertFalse(self.client.get(url).context["perms_lacking"])
        self.client.post(url, {"post": "yes"})
        self.assertFalse(ReportBlock.objects.exists())
