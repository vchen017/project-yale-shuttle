"""Tests for the trip watch (#29). The live feed, the LLM and the push service
are mocked: nothing here reaches the network."""

import datetime as dt
import json
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
from .tests import AUTH, KEYS, P256DH

User = get_user_model()

START = dt.datetime(2026, 11, 20, 14, 45, tzinfo=dt.timezone.utc)  # a run at 9:45 AM Eastern
WALK = 5  # minutes from Home to the stop
BLUE = {"id": 1, "name": "Blue"}
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
        board_at={"id": 10, "name": "Grove St", "routes": [1]},
        get_off_at={"id": 20, "name": "Science Hill", "routes": [1]},
        walk_to_stop=WALK, walk_from_stop=3, walk_all_the_way=30,
    )
    return plans.EventPlan(event=event, building={"name": "Watson Center"}, plan=ride, route_names=["Blue"])


class Bus:
    """The feed's countdown for the Blue bus at Grove St: when it reaches the
    stop, which a test can move."""

    def __init__(self, clock, arrives):
        self.clock, self.arrives = clock, arrives

    def etas(self, stop_id):
        minutes = round((self.arrives - self.clock()).total_seconds() / 60)
        return [{"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": minutes}] if minutes >= 0 else []


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
             mock.patch.object(trips, "next_run_at", side_effect=lambda now: now + dt.timedelta(hours=1)):
            response = self.client.post(reverse("calendar"), {"ics_url": "https://calendar.example.com/b.ics"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(agent.asked), 1)
        webpush.assert_called_once()  # due before the next hourly run: sent now
        self.assertEqual(TripNotification.objects.get().event_title, "Seminar")

    def test_a_failing_check_never_breaks_the_page(self, network, plan_event, webpush):
        now = timezone.now()
        with mock.patch("calendars.services.ical.fetch", return_value=self.ics(now + dt.timedelta(minutes=35))), \
             mock.patch.object(trips, "load_trips", side_effect=RuntimeError("boom")), \
             self.assertLogs("notifications.trips", "ERROR"):
            response = self.client.post(reverse("calendar"), {"ics_url": "https://calendar.example.com/b.ics"})
        self.assertEqual(response.status_code, 302)
        webpush.assert_not_called()


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
