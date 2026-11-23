"""The setup checklist on the notifications page (#11): what a rider still
needs before we can tell them when to leave for a trip. In its own file so it
doesn't collide with other pull requests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from calendars.models import Calendar
from places.models import Place

from .models import PushSubscription
from .views import setup_steps


class SetupStepsTests(TestCase):
    def setUp(self):
        self.rider = get_user_model().objects.create_user("rider", password="pw-for-tests-1")
        self.client.force_login(self.rider)

    def device(self):
        return PushSubscription.objects.create(
            user=self.rider, endpoint="https://push.example/1", p256dh="k", auth="a"
        )

    def steps(self):
        return {step["title"]: step for step in setup_steps(self.rider, self.rider.push_subscriptions.all())}

    def test_a_new_rider_has_all_three_still_to_do_in_order(self):
        steps = setup_steps(self.rider, self.rider.push_subscriptions.all())
        self.assertEqual([s["title"] for s in steps], ["Notifications on", "Calendar connected", "Home saved"])
        self.assertEqual([s["done"] for s in steps], [False, False, False])

    def test_each_step_is_done_by_its_own_thing(self):
        self.device()
        self.assertEqual([s["done"] for s in setup_steps(self.rider, self.rider.push_subscriptions.all())], [True, False, False])
        Calendar.objects.create(user=self.rider, ics_url="https://example.com/a.ics")
        self.assertTrue(self.steps()["Calendar connected"]["done"])
        Place.objects.create(user=self.rider, name="Home", lat=41.3, lon=-72.9, is_home=True)
        self.assertTrue(self.steps()["Home saved"]["done"])

    def test_a_saved_place_that_isnt_home_doesnt_count(self):
        Place.objects.create(user=self.rider, name="Library", lat=41.3, lon=-72.9)
        self.assertFalse(self.steps()["Home saved"]["done"])

    def test_only_this_riders_calendar_and_home_count(self):
        other = get_user_model().objects.create_user("other", password="pw-for-tests-1")
        Calendar.objects.create(user=other, ics_url="https://example.com/b.ics")
        Place.objects.create(user=other, name="Home", lat=41.3, lon=-72.9, is_home=True)
        self.assertFalse(self.steps()["Calendar connected"]["done"])
        self.assertFalse(self.steps()["Home saved"]["done"])

    def test_a_step_that_isnt_done_links_to_where_to_do_it(self):
        steps = self.steps()
        self.assertEqual(steps["Calendar connected"]["url"], reverse("calendar"))
        self.assertEqual(steps["Home saved"]["url"], reverse("my-places"))


class SetupPageTests(TestCase):
    def setUp(self):
        self.rider = get_user_model().objects.create_user("rider", password="pw-for-tests-1")
        self.client.force_login(self.rider)

    def test_the_page_lists_what_is_left_with_links(self):
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, "To hear about your trips")
        self.assertContains(response, 'data-step="todo"', count=3)
        self.assertContains(response, 'href="/calendars/"')
        self.assertContains(response, 'href="/places/"')
        self.assertNotContains(response, "You're set.")

    def test_when_everything_is_done_it_says_what_to_expect(self):
        PushSubscription.objects.create(user=self.rider, endpoint="https://push.example/1", p256dh="k", auth="a")
        Calendar.objects.create(user=self.rider, ics_url="https://example.com/a.ics")
        Place.objects.create(user=self.rider, name="Home", lat=41.3, lon=-72.9, is_home=True)
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, 'data-step="done"', count=3)
        self.assertNotContains(response, 'data-step="todo"')
        self.assertContains(response, "You're set.")
        self.assertContains(response, "5 minutes or more")

    def test_the_page_still_needs_a_log_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("notifications")).status_code, 302)


class HomeCopyTests(TestCase):
    def test_the_home_page_no_longer_says_the_schedule_is_coming_next(self):
        response = self.client.get("/")
        self.assertNotContains(response, "Coming next")
        self.assertContains(response, 'href="/calendars/"')
        self.assertContains(response, 'href="/notifications/"')
