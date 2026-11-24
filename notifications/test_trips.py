"""Tests for the trip watch (#29). The live feed, the LLM and the push service
are mocked: nothing here reaches the network."""

import datetime as dt
import json
import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assistant.decide import DONT_NOTIFY, Decision
from calendars import plans
from calendars.models import Calendar, Event
from jobs.registry import all_jobs
from places.models import Place
from transit import trips as transit_trips
from transit.downtowner import FeedUnavailable

from . import trips
from .models import PushSubscription, TripNotification
from .tests import AUTH, KEYS, P256DH, push_error

User = get_user_model()

START = dt.datetime(2026, 11, 20, 14, 45, tzinfo=dt.timezone.utc)  # a run at 9:45 AM Eastern
WALK = 5  # minutes from Home to the stop
BLUE = {"id": 1, "name": "Blue", "label": "Blue", "color": "00356b", "path": []}  # as network() gives routes
NETWORK = {"stops": [], "routes": [BLUE]}
SET_UP = {**KEYS, "OPENROUTER_API_KEY": "test-openrouter-key"}


class FakeTime:
    """The watch's clock: it moves only when the watch sleeps."""

    def __init__(self, start):
        self.now = start
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += dt.timedelta(seconds=seconds)


def a_plan(event, home, **kwargs):
    """What plan_event gives for every event here: board the Blue route at
    Grove St, a 5-minute walk from Home."""
    ride = transit_trips.Plan(
        board_at={"id": 10, "name": "Grove St", "lat": 41.3127, "lon": -72.9279, "routes": [1]},
        get_off_at={"id": 20, "name": "Science Hill", "lat": 41.3160, "lon": -72.9220, "routes": [1]},
        walk_to_stop=WALK, walk_from_stop=3, walk_all_the_way=30,
    )
    building = {"name": "Watson Center", "lat": 41.3156, "lon": -72.9238}
    return plans.EventPlan(event=event, building=building, plan=ride, route_names=["Blue"])


class Bus:
    """The feed's countdown for the Blue bus at Grove St: when it reaches the
    stop, which a test can move."""

    def __init__(self, clock, arrives):
        self.clock, self.arrives = clock, arrives

    def etas(self, stop_id):
        minutes = round((self.arrives - self.clock()).total_seconds() / 60)
        return [{"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": minutes}] if minutes >= 0 else []


def inline(target, user):
    """Stands in for trips._in_background in the connect-page tests: the first
    check runs at once, in the test's thread and database connection."""
    trips.first_check(user)


class Agent:
    """Stands in for the LLM agent: notify 5 minutes before the time to leave,
    by the feed's countdown, as the real one would be asked to."""

    def __init__(self):
        self.asked = []

    def __call__(self, request, data, now):
        self.asked.append(now)
        minutes = data.official_minutes(request)
        if minutes is None:
            return DONT_NOTIFY
        leave_by = now + dt.timedelta(minutes=minutes - request.walking_minutes)
        return Decision(
            notify=True, notify_at=max(leave_by - dt.timedelta(minutes=5), now),
            message=f"The Blue bus reaches Grove St in {minutes} minutes. Leave by {leave_by:%H:%M}.",
        )


@override_settings(**SET_UP)
@mock.patch("notifications.push.webpush")
@mock.patch.object(trips.plans, "plan_event", side_effect=a_plan)
@mock.patch.object(trips, "network", return_value=NETWORK)
class WatchTests(TestCase):
    def setUp(self):
        self.rider = User.objects.create_user("rider-one", password="not-a-real-one")
        Place.objects.create(user=self.rider, name="Home", lat=41.31, lon=-72.93, is_home=True)
        self.calendar = Calendar.objects.create(user=self.rider, ics_url="https://calendar.example.com/a.ics")
        PushSubscription.objects.create(
            user=self.rider, endpoint="https://fcm.googleapis.com/fcm/send/abc", p256dh=P256DH, auth=AUTH
        )
        self.clock = FakeTime(START)
        self.agent = Agent()

    def lecture(self, minutes_away=40, uid="econ-101"):
        return Event.objects.create(
            calendar=self.calendar, uid=uid, title="Econ 101",
            start=START + dt.timedelta(minutes=minutes_away), location="Watson Center",
        )

    def watch(self, bus):
        with mock.patch.object(trips.downtowner, "etas", side_effect=bus.etas):
            return trips.watch(clock=self.clock, sleep=self.clock.sleep, decide=self.agent)

    def test_nothing_coming_up_ends_the_run_at_once(self, network, plan_event, webpush):
        Event.objects.create(  # three hours out: the next runs' business
            calendar=self.calendar, uid="later", title="Later", start=START + dt.timedelta(hours=3)
        )
        summary = self.watch(Bus(self.clock, START))
        self.assertEqual(summary, "No trips in the next 2 hours with notifications on")
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(self.agent.asked, [])
        network.assert_not_called()  # it never even reads the feed

    def test_riders_without_notifications_on_have_no_trips(self, network, plan_event, webpush):
        PushSubscription.objects.all().delete()
        self.lecture()
        with self.assertNumQueries(1):
            self.assertEqual(trips.load_trips(START), [])

    def test_a_trip_gets_one_notification_at_the_time_the_agent_picks(self, network, plan_event, webpush):
        self.lecture(minutes_away=40)  # 10:25 AM: the bus at 10:10, so leave by 10:05
        summary = self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=25)))

        webpush.assert_called_once()
        payload = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(payload["title"], "Econ 101")
        self.assertIn("Leave by 15:05", payload["body"])
        self.assertEqual(payload["url"], reverse("when-to-leave"))
        sent = TripNotification.objects.get()
        self.assertEqual(sent.sent_at, START + dt.timedelta(minutes=15))  # 5 minutes before leaving
        self.assertEqual((sent.sequence, sent.devices), (0, 1))
        self.assertEqual(sent.leave_by, START + dt.timedelta(minutes=20))
        self.assertIn("1 notification(s) sent", summary)

    def test_a_delay_of_5_minutes_or_more_sends_one_more(self, network, plan_event, webpush):
        self.lecture(minutes_away=60)
        bus = Bus(self.clock, arrives=START + dt.timedelta(minutes=30))

        def running_late(stop_id):
            if self.clock() >= START + dt.timedelta(minutes=22):
                bus.arrives = START + dt.timedelta(minutes=37)  # 7 minutes late now
            return bus.etas(stop_id)

        with mock.patch.object(trips.downtowner, "etas", side_effect=running_late):
            trips.watch(clock=self.clock, sleep=self.clock.sleep, decide=self.agent)

        sent = list(TripNotification.objects.order_by("sequence"))
        self.assertEqual([notification.sequence for notification in sent], [0, 1])
        self.assertEqual(sent[1].leave_by - sent[0].leave_by, dt.timedelta(minutes=7))
        self.assertEqual(webpush.call_count, 2)

    def test_a_smaller_change_sends_nothing_more(self, network, plan_event, webpush):
        self.lecture(minutes_away=60)
        bus = Bus(self.clock, arrives=START + dt.timedelta(minutes=30))

        def a_little_late(stop_id):
            if self.clock() >= START + dt.timedelta(minutes=22):
                bus.arrives = START + dt.timedelta(minutes=33)
            return bus.etas(stop_id)

        with mock.patch.object(trips.downtowner, "etas", side_effect=a_little_late):
            trips.watch(clock=self.clock, sleep=self.clock.sleep, decide=self.agent)

        self.assertEqual(TripNotification.objects.count(), 1)
        webpush.assert_called_once()

    def test_a_rerun_never_sends_one_twice(self, network, plan_event, webpush):
        self.lecture(minutes_away=40)
        self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=25)))
        self.clock.now = START + dt.timedelta(minutes=16)  # rerun just after it went out
        self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=25)))
        self.assertEqual(TripNotification.objects.count(), 1)
        webpush.assert_called_once()

    def test_overlapping_runs_send_it_once(self, network, plan_event, webpush):
        self.lecture(minutes_away=40)
        first, second = trips.load_trips(START), trips.load_trips(START)  # loaded before either sends
        decision = Decision(notify=True, notify_at=START, message="Leave now for the Blue bus.")
        for run in (first, second):
            run[0].decision, run[0].decided_on = decision, START + dt.timedelta(minutes=5)
        self.assertEqual(trips.send_due(first, START), 1)
        self.assertEqual(trips.send_due(second, START), 0)  # the record was taken: it stands down
        webpush.assert_called_once()
        self.assertEqual(second[0].sent, [START + dt.timedelta(minutes=5)])  # and caught up

    def test_a_notification_no_device_received_is_tried_again(self, network, plan_event, webpush):
        self.lecture(minutes_away=40)  # due at 10:00, as above
        webpush.side_effect = [OSError("timed out"), None]  # the push service is out of reach once
        with self.assertLogs("notifications.trips", "ERROR"):
            summary = self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=25)))
        self.assertEqual(webpush.call_count, 2)
        sent = TripNotification.objects.get()
        self.assertEqual((sent.sequence, sent.devices), (0, 1))
        self.assertEqual(sent.sent_at, START + dt.timedelta(minutes=20))  # the next check, 5 minutes on
        self.assertIn("1 notification(s) sent", summary)

    def test_a_rider_whose_devices_are_all_gone_isnt_asked_again(self, network, plan_event, webpush):
        self.lecture(minutes_away=40)
        webpush.side_effect = push_error(410)  # the rider turned notifications off
        self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=25)))
        webpush.assert_called_once()
        self.assertFalse(PushSubscription.objects.exists())
        self.assertFalse(TripNotification.objects.exists())  # nobody received it
        self.assertEqual(len(self.agent.asked), 1)

    def test_checks_touch_no_database(self, network, plan_event, webpush):
        self.lecture(minutes_away=40)
        loaded = trips.load_trips(START)
        data = trips.watch_data(loaded, START)
        bus = Bus(self.clock, arrives=START + dt.timedelta(minutes=25))
        with mock.patch.object(trips.downtowner, "etas", side_effect=bus.etas), self.assertNumQueries(0):
            for minutes in (0, 5, 10):
                trips.check(loaded, START + dt.timedelta(minutes=minutes), data, decide=self.agent)

    def test_the_agent_is_asked_only_once_the_time_to_leave_is_near(self, network, plan_event, webpush):
        self.lecture(minutes_away=100)  # the bus at 11:15, leave by 11:10: 85 minutes away
        self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=90)))
        self.assertGreaterEqual(self.agent.asked[0] - START, trips.CHECK_EVERY * 11)  # once it's 30 minutes off

    def test_the_feed_countdown_comes_before_our_history_prediction(self, network, plan_event, webpush):
        """Ayush's case on #142: on a quiet route our history prediction is the
        usual wait for the hour, 40 minutes, while the feed sees a bus 8
        minutes out. The feed times the trip, so the agent is asked in time."""
        self.lecture(minutes_away=40)
        loaded = trips.load_trips(START)
        data = trips.watch_data(loaded, START)
        bus = Bus(self.clock, arrives=START + dt.timedelta(minutes=8))
        with mock.patch.object(trips.downtowner, "etas", side_effect=bus.etas), \
             mock.patch.object(data.history, "minutes", return_value=40):
            self.assertEqual(trips._request(loaded[0], data)[1], START + dt.timedelta(minutes=3))
            self.assertEqual(trips.check(loaded, START, data, decide=self.agent), 1)

    def test_without_a_countdown_our_history_prediction_times_the_trip(self, network, plan_event, webpush):
        self.lecture(minutes_away=60)
        loaded = trips.load_trips(START)
        data = trips.watch_data(loaded, START)
        with mock.patch.object(trips.downtowner, "etas", side_effect=FeedUnavailable("down")), \
             mock.patch.object(data.history, "minutes", return_value=12):
            self.assertEqual(trips._request(loaded[0], data)[1], START + dt.timedelta(minutes=7))

    def test_a_bus_the_rider_cant_walk_to_in_time_is_skipped(self, network, plan_event, webpush):
        """Austin's case on #142: the soonest bus is 3 minutes out and the walk
        is 8, so the time to leave comes from the next bus, 15 minutes out, and
        the agent's tool reads that one too."""
        trip = trips.Trip(
            user_id=self.rider.pk, calendar_id=self.calendar.pk, uid="econ-101", start=START + dt.timedelta(hours=1),
            title="Econ 101", stop_id=10, stop_name="Grove St", routes={1: "Blue"}, walk_to_stop=8, subscriptions=[],
        )
        history = mock.Mock()
        history.minutes.return_value = None
        data = trips.WatchData(history, {}, START)
        buses = [
            {"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": 3},
            {"route_id": 1, "bus_id": 8, "bus_name": "#8", "minutes": 15},
        ]
        with mock.patch.object(trips.downtowner, "etas", return_value=buses):
            request, leave_by = trips._request(trip, data)
            self.assertEqual(leave_by, START + dt.timedelta(minutes=7))
            self.assertEqual(data.official_minutes(request), 15)
        with mock.patch.object(trips.downtowner, "etas", return_value=buses[:1]):
            self.assertEqual(trips._request(trip, data), (None, None))  # no bus they can make, and no history

    def test_routes_are_compared_by_the_countdown_where_the_feed_has_one(self, network, plan_event, webpush):
        """Blue and Red both link the stop to the event. The feed sees Red's
        bus 6 minutes out and no Blue bus, whose usual wait is 10 minutes. Red
        wins, though its usual wait is 30."""
        trip = trips.Trip(
            user_id=self.rider.pk, calendar_id=self.calendar.pk, uid="econ-101", start=START + dt.timedelta(hours=1),
            title="Econ 101", stop_id=10, stop_name="Grove St", routes={1: "Blue", 2: "Red"}, walk_to_stop=WALK,
            subscriptions=[],
        )
        history = mock.Mock()
        history.minutes.side_effect = lambda route_id, stop_id, now: {1: 10, 2: 30}[route_id]
        red = [{"route_id": 2, "bus_id": 9, "bus_name": "#9", "minutes": 6}]
        with mock.patch.object(trips.downtowner, "etas", return_value=red):
            request, leave_by = trips._request(trip, trips.WatchData(history, {}, START))
        self.assertEqual((request.route, leave_by), ("Red", START + dt.timedelta(minutes=1)))

    def test_a_watch_hands_over_when_the_next_run_cancels_it(self, network, plan_event, webpush):
        self.lecture(minutes_away=90)
        self.clock.sleep = mock.Mock(side_effect=KeyboardInterrupt)  # the workflow cancelling the run
        summary = self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=80)))
        self.assertTrue(summary.endswith("then handed over to the next run"), summary)

    def test_without_the_keys_it_fails_where_admins_look(self, network, plan_event, webpush):
        self.lecture()
        with override_settings(OPENROUTER_API_KEY=""), self.assertRaisesMessage(
            ImproperlyConfigured, "can't notify anyone without OPENROUTER_API_KEY"
        ):
            self.watch(Bus(self.clock, arrives=START + dt.timedelta(minutes=25)))
        webpush.assert_not_called()

    def test_with_the_feed_down_there_are_no_trips_to_plan(self, network, plan_event, webpush):
        self.lecture()
        network.side_effect = FeedUnavailable("down")
        self.assertEqual(trips.load_trips(START), [])

    def test_the_real_agent_reads_the_live_countdown_through_its_tools(self, network, plan_event, webpush):
        """The agent itself, with only the LLM mocked: it asks its tool for the
        live countdown, which the watch answers from the feed."""
        self.lecture(minutes_away=40)
        loaded = trips.load_trips(START)
        data = trips.watch_data(loaded, START)
        answer = json.dumps({
            "notify": True, "notify_at": (START + dt.timedelta(minutes=10)).isoformat(),
            "message": "The Blue bus reaches Grove St at 10:10. Leave by 10:05.",
        })
        replies = [
            {"content": None, "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "get_official_arrival", "arguments": "{}"}}
            ]},
            {"content": answer},
        ]
        bus = Bus(self.clock, arrives=START + dt.timedelta(minutes=25))
        with mock.patch.object(trips.downtowner, "etas", side_effect=bus.etas), \
             mock.patch("assistant.openrouter.chat_message", side_effect=replies):
            self.assertEqual(trips.check(loaded, START, data), 1)
        self.assertEqual(loaded[0].decision.notify_at, START + dt.timedelta(minutes=10))


@override_settings(**SET_UP)
@mock.patch("notifications.push.webpush")
@mock.patch.object(trips.plans, "plan_event", side_effect=a_plan)
@mock.patch.object(trips, "network", return_value=NETWORK)
class FirstCheckTests(TestCase):
    """A calendar connected mid-hour: its trips in the next 2 hours get their
    first check in that request, not at the next hourly run."""

    def setUp(self):
        self.rider = User.objects.create_user("rider-two", password="not-a-real-one")
        Place.objects.create(user=self.rider, name="Home", lat=41.31, lon=-72.93, is_home=True)
        PushSubscription.objects.create(
            user=self.rider, endpoint="https://fcm.googleapis.com/fcm/send/def", p256dh=P256DH, auth=AUTH
        )
        self.client.force_login(self.rider)

    def ics(self, start):
        stamp = start.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
            f"BEGIN:VEVENT\r\nUID:seminar-1\r\nDTSTAMP:{stamp}\r\nDTSTART:{stamp}\r\n"
            "SUMMARY:Seminar\r\nLOCATION:Watson Center\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )

    def test_a_trip_added_mid_hour_gets_its_first_check_in_the_request(self, network, plan_event, webpush):
        now = timezone.now()
        bus = [{"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": 20}]
        agent = Agent()
        with mock.patch("calendars.services.ical.fetch", return_value=self.ics(now + dt.timedelta(minutes=35))), \
             mock.patch.object(trips.downtowner, "etas", return_value=bus), \
             mock.patch.object(trips, "decide_with_tools", side_effect=agent), \
             mock.patch.object(trips, "next_run_at", side_effect=lambda now: now + dt.timedelta(hours=1)), \
             mock.patch.object(trips, "_in_background", side_effect=inline), \
             self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("calendar"), {"ics_url": "https://calendar.example.com/b.ics"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(agent.asked), 1)
        webpush.assert_called_once()  # due before the next hourly run: sent now
        self.assertEqual(TripNotification.objects.get().event_title, "Seminar")

    def test_a_failing_check_never_breaks_the_page(self, network, plan_event, webpush):
        now = timezone.now()
        with mock.patch("calendars.services.ical.fetch", return_value=self.ics(now + dt.timedelta(minutes=35))), \
             mock.patch.object(trips, "load_trips", side_effect=RuntimeError("boom")), \
             mock.patch.object(trips, "_in_background", side_effect=inline), \
             self.assertLogs("notifications.trips", "ERROR"), \
             self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("calendar"), {"ics_url": "https://calendar.example.com/b.ics"})
        self.assertEqual(response.status_code, 302)
        webpush.assert_not_called()

    def test_the_page_doesnt_wait_for_the_first_check(self, network, plan_event, webpush):
        """Austin's point on #142: asking the agent can take longer than the 30
        seconds a request gets, so the connect page only starts the check."""
        now = timezone.now()
        with mock.patch("calendars.services.ical.fetch", return_value=self.ics(now + dt.timedelta(minutes=35))), \
             mock.patch.object(trips, "first_check") as first_check, \
             mock.patch.object(trips, "_in_background") as in_background, \
             self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("calendar"), {"ics_url": "https://calendar.example.com/b.ics"})
        self.assertEqual(response.status_code, 302)
        in_background.assert_called_once_with(trips._first_check_and_close, self.rider)
        first_check.assert_not_called()  # not in the request

    def test_the_first_check_runs_in_a_thread_of_its_own_and_closes_its_connection(
        self, network, plan_event, webpush
    ):
        ran_in, closed = [], threading.Event()
        with mock.patch.object(trips, "first_check", side_effect=lambda user: ran_in.append(threading.current_thread())), \
             mock.patch.object(trips.connections, "close_all", side_effect=closed.set):
            with self.captureOnCommitCallbacks(execute=True):
                trips.first_check_soon(self.rider)
                self.assertEqual(ran_in, [])  # nothing before the commit
            self.assertTrue(closed.wait(5))
        self.assertEqual(len(ran_in), 1)
        self.assertIsNot(ran_in[0], threading.current_thread())


class ScheduleTests(TestCase):
    def test_the_job_runs_hourly_and_takes_over(self):
        job = all_jobs()["notify_trips"]
        self.assertEqual(job.schedule, trips.SCHEDULE)
        self.assertTrue(job.takes_over)
        self.assertGreater(job.timeout, trips.WATCH)

    def test_the_next_run_is_the_next_45_past(self):
        self.assertEqual(trips.next_run_at(START), START + dt.timedelta(hours=1))
        self.assertEqual(
            trips.next_run_at(START + dt.timedelta(minutes=20)), START + dt.timedelta(minutes=60)
        )
