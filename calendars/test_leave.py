"""The when-to-leave page (#8): what each of the next 24 hours' events shows,
and the demo seed that exists so graders see the page populated. The feed is
mocked at calendars.views.network and the live board at
calendars.plans.next_buses, so no test reaches the network; building matching
runs against our own buildings list, as the real page does."""

import datetime as dt
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from places.models import Place
from transit.downtowner import FeedUnavailable
from transit.next_buses import Board, Row
from transit.test_trips import SCIENCE_HILL, SERVED, UNION

from .demo import seed as seed_demo
from .models import Calendar, Event

User = get_user_model()
ROUTES = {
    1: {"id": 1, "name": "Blue - Weekday Daytime", "label": "Blue", "color": "00356b", "path": []},
    2: {"id": 2, "name": "Orange - Weekday Daytime", "label": "Orange", "color": "f19c0e", "path": []},
}


def feed():
    return {"running": True, "routes": list(ROUTES.values()), "stops": SERVED}


def row(ours=8):
    """A live board row: the Blue, ours N minutes out, the feed saying 9."""
    return Row(route_id=1, name="Blue - Weekday Daytime", label="Blue", color="00356b", official=[9], ours=ours)


class LeavePageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rider", password="not-the-real-password")
        self.client.force_login(self.user)
        # By Union Station, as in the trip tests: the stop is at Home's door.
        self.home = Place.objects.create(user=self.user, name="Home", lat=41.2971, lon=-72.9261, is_home=True)
        self.calendar = Calendar.objects.create(user=self.user, ics_url="https://calendar.example.com/rider.ics")
        self.lecture = Event.objects.create(
            calendar=self.calendar,
            uid="lecture@yale",
            title="MGT 656 lecture",
            start=timezone.now() + dt.timedelta(hours=3),
            location="Watson Center, 60 Sachem Street",
        )

    def go(self, rows=None):
        """The page with the feed stubbed; the live board's rows optional."""
        with (
            mock.patch("calendars.views.network", return_value=feed()),
            mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=rows or [])) as board_mock,
        ):
            response = self.client.get(reverse("when-to-leave"))
        return response, board_mock

    def test_a_rider_who_is_not_logged_in_is_asked_to(self):
        self.client.logout()
        response = self.client.get(reverse("when-to-leave"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_get_thing(self):
        response = self.client.post(reverse("when-to-leave"))
        self.assertEqual(response.status_code, 405)

    def test_a_rideable_event_shows_the_route_the_stop_and_when_to_leave(self):
        page, board_mock = self.go()
        self.assertEqual(page.status_code, 200)
        board_mock.assert_not_called()  # three hours out, there's nothing live to ask
        content = page.content.decode()
        self.assertIn("MGT 656 lecture", content)
        self.assertIn("Blue - Weekday Daytime", content)
        self.assertIn("Union Station", content)
        self.assertIn("/stops/30/", content)  # the stop links to its next-buses page
        self.assertIn("Science Hill", content)
        self.assertIn("Leave around", content)
        self.assertIn("an estimate", content)
        self.assertNotIn("Location unknown", content)

    def test_an_event_soon_shows_the_live_bus(self):
        self.lecture.start = timezone.now() + dt.timedelta(minutes=20)
        self.lecture.save(update_fields=["start"])
        page, board_mock = self.go([row(ours=8)])
        board_mock.assert_called_once_with(UNION["id"], SCIENCE_HILL["id"])
        content = page.content.decode()
        self.assertIn("Our guess: the bus is about 8 min away", content)
        self.assertIn("Leave by", content)
        self.assertIn("#00356b", content)  # the route's colour on its badge

    def test_without_a_prediction_the_feed_s_countdown_shows(self):
        self.lecture.start = timezone.now() + dt.timedelta(minutes=20)
        self.lecture.save(update_fields=["start"])
        feed_only = Row(route_id=1, name="Blue - Weekday Daytime", label="Blue", color="00356b", official=[9], ours=None)
        page, _ = self.go([feed_only])
        self.assertIn("The feed says the bus is 9 min away", page.content.decode())

    def test_the_events_are_ordered_by_when_to_leave_not_by_start(self):
        now = timezone.now()
        # The colloquium starts sooner, but the lecture's estimate says leave at
        # :33 while the colloquium's bus is 40 minutes out (:40) — so the
        # lecture is the one to head out for first.
        Event.objects.create(
            calendar=self.calendar,
            uid="colloquium@yale",
            title="Colloquium",
            start=now + dt.timedelta(minutes=44),
            location="Watson Center, 60 Sachem Street",
        )
        self.lecture.start = now + dt.timedelta(minutes=50)
        self.lecture.save(update_fields=["start"])
        page, board_mock = self.go([row(ours=40)])
        board_mock.assert_called_once()  # only the colloquium is inside the live window
        content = page.content.decode()
        self.assertLess(content.index("MGT 656 lecture"), content.index("Colloquium"))

    def test_a_walking_plan_says_so(self):
        self.home.lat, self.home.lon = 41.3156, -72.9238  # Watson Center's own corner
        self.home.save(update_fields=["lat", "lon"])
        page, board_mock = self.go()
        board_mock.assert_not_called()
        content = page.content.decode()
        self.assertIn("walk from Home", content)
        self.assertNotIn("Board at the", content)

    def test_unmatched_and_empty_locations_are_listed_as_unknown(self):
        now = timezone.now()
        Event.objects.create(
            calendar=self.calendar,
            uid="brunch@yale",
            title="Brunch",
            start=now + dt.timedelta(hours=2),
            location="New Haven Green",
        )
        Event.objects.create(
            calendar=self.calendar,
            uid="pick-up@yale",
            title="Pick-up basketball",
            start=now + dt.timedelta(hours=4),
            location="",
        )
        page, _ = self.go()
        content = page.content.decode()
        self.assertIn("Location unknown", content)
        self.assertIn("one of the Yale buildings we know", content)
        self.assertIn("The event has no location", content)

    def test_without_a_home_the_events_wait_with_a_hint(self):
        Place.objects.filter(user=self.user).delete()
        page, board_mock = self.go()
        board_mock.assert_not_called()
        content = page.content.decode()
        self.assertIn("Set your Home", content)
        self.assertIn("Watson Center", content)  # the building still shows
        self.assertNotIn("Leave around", content)
        self.assertNotIn("Board at the", content)

    def test_when_the_feed_is_down_events_still_show_without_times(self):
        Event.objects.create(
            calendar=self.calendar,
            uid="brunch@yale",
            title="Brunch",
            start=timezone.now() + dt.timedelta(hours=2),
            location="New Haven Green",
        )
        with (
            mock.patch("calendars.views.network", side_effect=FeedUnavailable("down")),
            mock.patch("calendars.plans.next_buses") as board_mock,
        ):
            page = self.client.get(reverse("when-to-leave"))
        self.assertEqual(page.status_code, 200)
        board_mock.assert_not_called()
        content = page.content.decode()
        self.assertIn("Live shuttle times are unavailable", content)
        self.assertIn("MGT 656 lecture", content)  # the events, and their buildings…
        self.assertIn("Watson Center", content)  # …are all still there
        self.assertNotIn("Leave around", content)


class DemoSeedTests(TestCase):
    """The demo data exists so graders see this page populated (#8)."""

    def setUp(self):
        self.user = User.objects.create_user("rider@example.com", password="not-the-real-password")
        self.client.force_login(self.user)
        self.accounts = {"admin": self.user, "rider": self.user}

    def seed(self):
        seed_demo(self.accounts, StringIO())

    def go(self):
        with (
            mock.patch("calendars.views.network", return_value=feed()),
            mock.patch("calendars.plans.next_buses", return_value=Board(running=True, rows=[])),
        ):
            return self.client.get(reverse("when-to-leave")).content.decode()

    def test_seeding_fills_the_page_and_is_safe_to_rerun(self):
        self.seed()
        self.seed()  # twice: one calendar, three events, one Home
        self.assertEqual(Calendar.objects.filter(user=self.user, is_demo=True).count(), 1)
        self.assertEqual(Event.objects.filter(calendar__user=self.user).count(), 3)
        self.assertEqual(Place.objects.filter(user=self.user, is_home=True).count(), 1)
        content = self.go()
        self.assertIn("MGT 656 lecture", content)
        self.assertIn("Board at the", content)  # the demo Home makes the lecture rideable
        self.assertIn("Location unknown", content)
        self.assertIn("Pick-up basketball", content)
        self.assertIn("The event has no location", content)

    def test_a_riders_own_home_is_never_replaced(self):
        Place.objects.create(user=self.user, name="Home", lat=41.0, lon=41.0, is_home=True)
        self.seed()
        home = Place.objects.get(user=self.user, is_home=True)
        self.assertEqual((home.lat, home.lon), (41.0, 41.0))

    def test_a_place_called_home_that_isnt_the_home_stays_alone(self):
        Place.objects.create(user=self.user, name="Home", lat=41.0, lon=41.0)  # their office, say
        self.seed()
        self.assertFalse(Place.objects.filter(user=self.user, is_home=True).exists())
        self.assertEqual(Place.objects.filter(user=self.user, name="Home").count(), 1)
        self.assertEqual(Event.objects.filter(calendar__user=self.user).count(), 3)  # events still demo
