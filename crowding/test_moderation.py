"""Moderating crowd reports (#16): admins list them newest first, filter by
route, and remove bad ones, which stop counting toward crowding. Riders
can't open the moderation page. The Downtowner feed is mocked at
transit.downtowner.urlopen and `timezone.now` is frozen, so no test reaches
the network."""

from unittest import mock
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import CrowdReport
from .services import crowding
from .tests import NOW, ROUTES, Level, ReportsMixin, at, feed

CHANGELIST = reverse("admin:crowding_crowdreport_changelist")
BLUE = {"id": 1, "name": "Blue - Weekday Daytime", "label": "BD", "color": "4472C4"}


@mock.patch("django.utils.timezone.now", return_value=NOW)
@mock.patch("transit.downtowner.urlopen")
class ModerationTests(ReportsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("admin@example.com", password="an-admin-password-1")

    def act(self, action, *reports):
        return self.client.post(
            CHANGELIST, {"action": action, "_selected_action": [r.pk for r in reports]}, follow=True
        )

    def test_admins_see_reports_newest_first_with_rider_route_level_and_time(self, urlopen, now):
        older = self.add(Level.FULL, at(minutes_ago=50), user=self.rider)
        newer = self.add(Level.EMPTY, at(minutes_ago=5), route_id=15)
        self.client.force_login(self.admin)

        response = self.client.get(CHANGELIST)

        self.assertEqual(list(response.context["cl"].result_list), [newer, older])
        for column in ["Created at", "Rider", "Route", "Level", "Status"]:
            self.assertContains(response, column)
        self.assertContains(response, "rider@example.com")
        self.assertContains(response, "Blue - Weekday Daytime (route 1)")
        self.assertContains(response, "Full (can&#x27;t board)")
        self.assertContains(response, "2:35 p.m.")  # shown in Eastern: 7:35 PM UTC

    def test_admins_filter_by_route_id(self, urlopen, now):
        blue = self.add(Level.FULL, at(minutes_ago=5))
        self.add(Level.EMPTY, at(minutes_ago=5), route_id=16)  # Blue West: also a "Blue"
        self.client.force_login(self.admin)

        response = self.client.get(CHANGELIST, {"route": "1"})

        self.assertEqual(list(response.context["cl"].result_list), [blue])
        self.assertContains(response, "Blue West (route 16)")  # the other choice in the filter

    def test_a_renamed_route_is_one_choice_with_both_names(self, urlopen, now):
        self.add(Level.FULL, at(minutes_ago=5))
        CrowdReport.objects.create(user=self.other, route_id=1, route_name="Blue", level=Level.FULL, created_at=at(days_ago=3))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(CHANGELIST), "Blue / Blue - Weekday Daytime (route 1)")

    def test_removing_a_report_stops_it_counting_toward_crowding(self, urlopen, now):
        bad = self.add(Level.FULL, at(minutes_ago=5))
        self.add(Level.EMPTY, at(minutes_ago=10))
        for days_ago in (1, 2, 3):
            self.add(Level.EMPTY, at(days_ago=days_ago, hour=14))
        self.add(Level.FULL, at(days_ago=4, hour=14))  # a bad one in the typical pattern too
        bad_typical = CrowdReport.objects.get(level=Level.FULL, created_at=at(days_ago=4, hour=14))
        before = crowding([BLUE], now=NOW)[0]
        self.assertEqual((before.current_level, before.current_count), (Level.STANDING, 2))  # 1.5 rounds up

        self.client.force_login(self.admin)
        response = self.act("remove_reports", bad, bad_typical)

        self.assertContains(response, "Removed 2 reports. They no longer count toward crowding.")
        bad.refresh_from_db()
        self.assertEqual((bad.removed_at, bad.removed_by), (NOW, self.admin))
        after = crowding([BLUE], now=NOW)[0]
        self.assertEqual((after.current_level, after.current_count), (Level.EMPTY, 1))
        self.assertEqual([(h.hour, h.level, h.count) for h in after.by_hour], [(14, Level.EMPTY, 4)])

    def test_removed_reports_stay_listed_as_removed_and_can_be_restored(self, urlopen, now):
        bad = self.add(Level.FULL, at(minutes_ago=5))
        self.client.force_login(self.admin)
        self.act("remove_reports", bad)

        removed = self.client.get(CHANGELIST, {"status": "removed"})
        self.assertEqual(list(removed.context["cl"].result_list), [bad])
        self.assertContains(removed, "Removed Nov 18, 2:40 PM")
        self.assertEqual(list(self.client.get(CHANGELIST, {"status": "counted"}).context["cl"].result_list), [])

        response = self.act("restore_reports", bad)
        self.assertContains(response, "Restored 1 report. It counts toward crowding again.")
        bad.refresh_from_db()
        self.assertIsNone(bad.removed_at)
        self.assertEqual(crowding([BLUE], now=NOW)[0].current_count, 1)

    def test_removing_twice_keeps_the_first_removal(self, urlopen, now):
        bad = self.add(Level.FULL, at(minutes_ago=5))
        CrowdReport.objects.filter(pk=bad.pk).update(removed_at=at(minutes_ago=1), removed_by=self.rider)
        self.client.force_login(self.admin)
        self.assertContains(self.act("remove_reports", bad), "Those reports were already removed.")
        bad.refresh_from_db()
        self.assertEqual(bad.removed_by, self.rider)

    def test_the_crowding_page_leaves_removed_reports_out(self, urlopen, now):
        urlopen.side_effect = feed()
        bad = self.add(Level.FULL, at(minutes_ago=5))
        self.client.force_login(self.admin)
        self.act("remove_reports", bad)
        response = self.client.get(reverse("crowding"))
        self.assertIsNone(next(r for r in response.context["routes"] if r.route_id == 1).current_level)

    def test_with_the_feed_down_routes_with_only_removed_reports_dont_show(self, urlopen, now):
        urlopen.side_effect = URLError("down")
        bad = self.add(Level.FULL, at(days_ago=1), route_id=16)
        self.add(Level.EMPTY, at(days_ago=1))
        CrowdReport.objects.filter(pk=bad.pk).update(removed_at=NOW)
        response = self.client.get(reverse("crowding"))
        self.assertEqual([r.route_id for r in response.context["routes"]], [1])

    def test_theres_no_bulk_delete_but_deleting_a_rider_still_works(self, urlopen, now):
        self.add(Level.FULL, at(minutes_ago=5), user=self.rider)
        self.client.force_login(self.admin)
        actions = [name for name, _ in self.client.get(CHANGELIST).context["action_form"].fields["action"].choices]
        self.assertNotIn("delete_selected", actions)
        self.assertIn("remove_reports", actions)

        # Deleting a rider's account deletes their reports, which needs delete permission on reports.
        url = reverse("admin:accounts_user_delete", args=[self.rider.pk])
        self.assertFalse(self.client.get(url).context["perms_lacking"])
        self.client.post(url, {"post": "yes"})
        self.assertFalse(CrowdReport.objects.filter(user=self.rider).exists())

    def test_riders_cant_open_the_moderation_page_or_remove_reports(self, urlopen, now):
        report = self.add(Level.FULL, at(minutes_ago=5))
        for who in (None, self.rider):
            if who:
                self.client.force_login(who)
            response = self.client.get(CHANGELIST)
            self.assertRedirects(response, f"{reverse('admin:login')}?next={CHANGELIST}", fetch_redirect_response=False)
            self.client.post(CHANGELIST, {"action": "remove_reports", "_selected_action": [report.pk]})
        report.refresh_from_db()
        self.assertIsNone(report.removed_at)

    def test_the_crowding_page_links_staff_to_moderation(self, urlopen, now):
        urlopen.side_effect = feed(ROUTES)
        self.client.force_login(self.rider)
        self.assertNotContains(self.client.get(reverse("crowding")), "Moderate crowd reports")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("crowding")), f'href="{CHANGELIST}"')
