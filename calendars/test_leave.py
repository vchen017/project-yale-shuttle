"""The when-to-leave page (#8): what each of the next 24 hours' events shows,
the routes the recorded history says run at an event's weekday and hour, and
the demo seed that exists so graders see the page populated. The feed is
mocked at calendars.views.network and the live board at
calendars.plans.next_buses, so no test reaches the network; building matching
runs against our own buildings list, as the real page does."""

import datetime as dt
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from history.models import Poll, RouteStatus
from places.models import Place
from transit.downtowner import FeedUnavailable
from transit.next_buses import Board, Row
from transit.test_trips import SCIENCE_HILL, SERVED, UNION

from . import demo
from .demo import seed as seed_demo
from .jobs import refresh_calendars
from .models import Calendar, Event
from .plans import routes_running_at

User = get_user_model()
ET = ZoneInfo("America/New_York")
ROUTES = {
    1: {"id": 1, "name": "Blue - Weekday Daytime", "label": "Blue", "color": "00356b", "path": []},
    2: {"id": 2, "name": "Orange - Weekday Daytime", "label": "Orange", "color": "f19c0e", "path": []},
}


def feed():
    return {"running": True, "routes": list(ROUTES.values()), "stops": SERVED}


def row(ours=8):
    """A live board row: the Blue, ours N minutes out, the feed saying 9."""
    return Row(route_id=1, name="Blue - Weekday Daytime", label="Blue", color="00356b", official=[9], ours=ours)


def et(*fields):
    """An aware datetime in New York time, as the tests write their moments."""
    return dt.datetime(*fields, tzinfo=ET)


def poll(when, active_by_route):
    """One recorded poll, with a RouteStatus row per named route saying
    whether it was active."""
    poll = Poll.objects.create(polled_at=when)
    RouteStatus.objects.bulk_create(
        RouteStatus(
            poll=poll,
            polled_at=when,
            route_id=route_id,
            route_name=f"Route {route_id}",
            short_name=f"R{route_id}",
            num_stops=10,
            active=active,
        )
        for route_id, active in active_by_route.items()
    )
    return poll


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

    def test_a_night_event_plans_on_the_routes_running_then_not_now(self):
        """Vivian, #126 review: the 9 PM lecture checked in the afternoon
        rides the route that runs at 9 PM, not the daytime one in the feed
        now. The recorded polls say the night Orange runs then; the daytime
        Blue, which the feed shows running at checking time, does not."""
        start = timezone.now() + dt.timedelta(hours=6)
        self.lecture.start = start
        self.lecture.save(update_fields=["start"])
        local = timezone.localtime(start)
        for days in (7, 14, 21):
            # The same weekday, the same New York clock time, weeks back.
            then = dt.datetime.combine(local.date() - dt.timedelta(days=days), local.timetz())
            poll(then, {1: False, 2: True})
        page, board_mock = self.go()
        board_mock.assert_not_called()  # six hours out, nothing live to ask
        content = page.content.decode()
        self.assertIn("Orange - Weekday Daytime", content)
        self.assertNotIn("Blue - Weekday Daytime", content)
        self.assertIn("Leave around", content)

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


class RunningAtTests(TestCase):
    """routes_running_at: the routes the recorded polls say run at the event's
    weekday and hour, and None when our history has nothing to say."""

    def test_three_weeks_of_polls_agree_on_what_runs(self):
        # Thursday evenings 21:00 New York time, three weeks back: the Orange
        # active in every poll, the Blue in none — so tonight's 9 PM event
        # rides the night route.
        now, start = et(2026, 11, 19, 15, 0), et(2026, 11, 19, 21, 0)
        for days in (7, 14, 21):
            evening = start - dt.timedelta(days=days)
            for minutes in (-15, -10, -5, 0, 5, 10, 15):
                poll(evening + dt.timedelta(minutes=minutes), {1: False, 2: True})
        self.assertEqual(routes_running_at(start, now), {2})

    def test_a_route_active_in_half_the_polls_runs_and_a_minority_does_not(self):
        # One Thursday evening, seven polls: the Orange in all seven, the
        # Green in only two — a blip, not a schedule.
        now, start = et(2026, 11, 19, 15, 0), et(2026, 11, 19, 21, 0)
        evening = start - dt.timedelta(days=7)
        minutes_there = (-15, -10, -5, 0, 5, 10, 15)
        for minutes in minutes_there:
            poll(
                evening + dt.timedelta(minutes=minutes),
                {1: False, 2: True, 3: minutes in (-10, 5)},
            )
        self.assertEqual(routes_running_at(start, now), {2})

    def test_a_weekday_with_no_recorded_polls_says_no_history(self):
        # Our Thursday-evening history has nothing to say about Friday
        # morning, so the caller plans on the routes running now.
        now = et(2026, 11, 19, 15, 0)
        poll(et(2026, 11, 12, 21, 0), {1: True, 2: True})
        self.assertIsNone(routes_running_at(et(2026, 11, 20, 8, 0), now))

    def test_polls_but_no_active_routes_means_nothing_runs(self):
        # 3 AM: the polls exist, and every route in them was inactive — an
        # empty set, so the plan won't invent a ride.
        now, start = et(2026, 11, 19, 15, 0), et(2026, 11, 19, 3, 0)
        for days in (7, 14, 21):
            poll(start - dt.timedelta(days=days), {1: False, 2: False})
        self.assertEqual(routes_running_at(start, now), set())


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

    def test_the_hourly_job_moves_started_events_to_their_next_occurrence(self):
        """The seed anchors once per deploy; the job keeps the page filled
        between deploys, after tonight's events have started."""
        self.seed()
        now = timezone.now()
        Event.objects.filter(calendar__user=self.user).update(
            start=now - dt.timedelta(hours=2), end=now - dt.timedelta(hours=1)
        )
        self.assertEqual(demo.reanchor(now), 3)
        starts = {event.title: event.start for event in Event.objects.filter(calendar__user=self.user)}
        self.assertEqual(timezone.localtime(starts["MGT 656 lecture"]).time(), dt.time(16, 0))
        for title, start in starts.items():
            self.assertGreater(start, now, title)
        self.assertEqual(demo.reanchor(now), 0)  # safe to run twice

    def test_the_job_reports_the_refresh_and_the_reanchoring_together(self):
        self.seed()
        now = timezone.now()
        Event.objects.filter(calendar__user=self.user).update(start=now - dt.timedelta(hours=2))
        summary = refresh_calendars()
        self.assertIn("0 calendars", summary)  # the demo calendar is never fetched
        self.assertIn("3 demo events re-anchored", summary)
        self.assertTrue(Event.objects.filter(calendar__user=self.user, start__gt=now).exists())

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

    def test_demo_events_sit_at_fixed_new_york_times_always_upcoming(self):
        """Vivian, #126 review: events anchored to now vanished a few hours
        after each deploy, leaving the evening page empty. They now sit at
        fixed New York times, re-anchored each seed to the next occurrence,
        so whatever hour the deploy went out, something is always coming up."""
        now = timezone.now()
        self.seed()
        starts = {event.title: event.start for event in Event.objects.filter(calendar__user=self.user)}
        self.assertEqual(timezone.localtime(starts["MGT 656 lecture"]).time(), dt.time(16, 0))
        self.assertEqual(timezone.localtime(starts["Brunch with the cohort"]).time(), dt.time(17, 30))
        self.assertEqual(timezone.localtime(starts["Pick-up basketball"]).time(), dt.time(19, 0))
        for title, start in starts.items():
            self.assertGreater(start, now, title)
            self.assertLess(start, now + dt.timedelta(hours=25), title)  # today's, else tomorrow's

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
