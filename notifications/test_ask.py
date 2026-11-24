"""Tests for asking the trip agent from the when-to-leave page (#148). The live
feed and the LLM are mocked: nothing here reaches the network."""

import dataclasses
import datetime as dt
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from assistant.decide import DONT_NOTIFY
from assistant.openrouter import OpenRouterError
from calendars import plans
from calendars.models import Calendar, Event
from places.models import Place
from transit.downtowner import FeedUnavailable

from . import ask, trips
from .models import AgentAnswer
from .test_trips import NETWORK, SET_UP, START, Agent, Bus, a_plan

User = get_user_model()

ASKED = START  # 9:45 AM Eastern
BUS_AT = START + dt.timedelta(minutes=12)  # the Blue reaches Grove St at 9:57 AM


def planned(event, home, **kwargs):
    """What plan_event gives here: a_plan, with a time to leave so the
    when-to-leave page lists it as rideable, for an event with a location;
    location unknown for one without."""
    if not event.location:
        return plans.EventPlan(event=event, building=None)
    return dataclasses.replace(a_plan(event, home), leave_by=event.start - dt.timedelta(minutes=30))


class LooksEverythingUp:
    """Stands in for the agent: reads every tool's source, then stays quiet."""

    def __call__(self, request, data, now):
        try:
            data.official_minutes(request)
        except FeedUnavailable:
            pass
        data.predicted_minutes(request)
        data.crowding(request)
        return DONT_NOTIFY


def model_replies(*replies):
    """Patches the agent's model with these replies, one per model turn."""
    return mock.patch("assistant.agent.openrouter.chat_message", side_effect=list(replies))


class Rider:
    """A rider with a Home and a calendar holding one lecture 40 minutes out,
    and no device: asking needs no push notifications."""

    def make_rider(self):
        self.rider = User.objects.create_user("rider-one", password="not-a-real-one")
        Place.objects.create(user=self.rider, name="Home", lat=41.31, lon=-72.93, is_home=True)
        self.calendar = Calendar.objects.create(user=self.rider, ics_url="https://calendar.example.com/a.ics")
        self.event = Event.objects.create(
            calendar=self.calendar, uid="econ-101", title="Econ 101",
            start=START + dt.timedelta(minutes=40), location="Watson Center",
        )
        self.agent = Agent()
        self.bus = Bus(lambda: ASKED, BUS_AT)

    def answer(self, answer, now=ASKED, decide=None):
        """Run the agent for a saved question here, rather than in its thread."""
        with mock.patch.object(trips.downtowner, "etas", side_effect=self.bus.etas):
            return ask.answer(answer.pk, now=now, decide=decide)


@override_settings(**SET_UP)
@mock.patch.object(trips.plans, "plan_event", side_effect=planned)
@mock.patch.object(ask, "network", return_value=NETWORK)
class AskTests(Rider, TestCase):
    def setUp(self):
        self.make_rider()

    def ask(self, now=ASKED):
        """Ask about the lecture at `now`: the answer, and how many threads
        would start for the agent once the request commits."""
        with self.captureOnCommitCallbacks() as threads:
            answer = ask.start(self.rider, self.event, now=now)
        return answer, len(threads)

    def test_a_rider_with_no_device_gets_the_agents_answer(self, network, plan_event):
        answer, threads = self.ask()
        self.assertEqual((answer.status, threads), (AgentAnswer.ASKING, 1))
        answer = self.answer(answer, decide=self.agent)
        self.assertEqual(answer.status, AgentAnswer.ANSWERED)
        self.assertEqual(self.agent.asked, [ASKED])
        # The bus comes in 12 minutes and the walk is 5: leave by 9:52, told 5 minutes before.
        self.assertTrue(answer.notify)
        self.assertEqual(answer.notify_at, START + dt.timedelta(minutes=2))
        self.assertIn("Leave by", answer.message)
        self.assertEqual(answer.looked_up, {"route": "Blue", "stop": "Grove St", "official": BUS_AT.isoformat()})
        self.assertEqual(
            answer.looked_up_text, "What it looked up: the Downtowner says the next Blue bus reaches Grove St at 9:57 AM."
        )
        self.assertEqual(AgentAnswer.objects.get().pk, answer.pk)  # saved, where the page finds it

    def test_it_notes_everything_the_agent_looked_up(self, network, plan_event):
        answer = self.answer(self.ask()[0], decide=LooksEverythingUp())
        self.assertEqual((answer.status, answer.notify, answer.message), (AgentAnswer.ANSWERED, False, ""))
        self.assertEqual(
            answer.looked_up_text,
            "What it looked up: the Downtowner says the next Blue bus reaches Grove St at 9:57 AM; "
            "we had no prediction of our own; no crowding reports.",
        )

    def test_the_real_agent_is_asked_strictly_and_its_tool_reads_are_noted(self, network, plan_event):
        official = {"id": "a", "type": "function", "function": {"name": "get_official_arrival", "arguments": "{}"}}
        with model_replies(
            {"role": "assistant", "content": None, "tool_calls": [official]},
            {"role": "assistant", "content": json.dumps({"notify": False, "notify_at": None, "message": ""})},
        ):
            answer = self.answer(self.ask()[0])
        self.assertEqual((answer.status, answer.notify), (AgentAnswer.ANSWERED, False))
        self.assertEqual(answer.looked_up["official"], BUS_AT.isoformat())
        self.assertNotIn("predicted", answer.looked_up)  # it never asked for our prediction

    def test_asking_again_within_five_minutes_shows_the_same_answer(self, network, plan_event):
        first = self.answer(self.ask()[0], decide=self.agent)
        again, threads = self.ask(now=ASKED + dt.timedelta(minutes=4))
        self.assertEqual((again.pk, again.status, threads), (first.pk, AgentAnswer.ANSWERED, 0))
        later, threads = self.ask(now=ASKED + AgentAnswer.REUSE)
        self.assertEqual((later.pk, later.status, threads), (first.pk, AgentAnswer.ASKING, 1))
        self.assertEqual((later.message, later.looked_up), ("", {}))  # the old answer goes while it asks

    def test_an_answer_on_its_way_is_not_asked_for_twice(self, network, plan_event):
        first, threads = self.ask()
        second, more = self.ask(now=ASKED + dt.timedelta(seconds=20))
        self.assertEqual((second.pk, second.status, threads + more), (first.pk, AgentAnswer.ASKING, 1))

    def test_an_ask_whose_thread_died_says_so_and_can_be_asked_again(self, network, plan_event):
        lost, _ = self.ask()
        with mock.patch("django.utils.timezone.now", return_value=ASKED + AgentAnswer.GIVE_UP):
            self.assertEqual((lost.is_asking, lost.is_failed), (False, True))
            self.assertEqual(lost.failure, "it took too long to answer")
            self.assertTrue(lost.can_ask_again)
        again, threads = self.ask(now=ASKED + AgentAnswer.GIVE_UP)
        self.assertEqual((again.status, threads), (AgentAnswer.ASKING, 1))

    @override_settings(OPENROUTER_API_KEY="")
    def test_without_an_openrouter_key_it_says_so_at_once(self, network, plan_event):
        answer, threads = self.ask()
        self.assertEqual((answer.status, answer.reason, threads), (AgentAnswer.FAILED, ask.NOT_SET_UP, 0))

    def test_when_openrouter_fails_it_says_the_agent_couldnt_answer(self, network, plan_event):
        with self.assertLogs("notifications.ask", "WARNING"):
            answer = self.answer(self.ask()[0], decide=mock.Mock(side_effect=OpenRouterError("timed out")))
        self.assertEqual((answer.status, answer.reason), (AgentAnswer.FAILED, "the AI service didn't answer"))

    def test_an_answer_that_doesnt_fit_is_not_shown_as_a_no(self, network, plan_event):
        prose = {"role": "assistant", "content": "Leave soon!"}
        with model_replies(prose, prose), self.assertLogs("assistant.agent", "WARNING"):
            answer = self.answer(self.ask()[0])
        self.assertEqual((answer.status, answer.reason), (AgentAnswer.FAILED, "its answer didn't make sense"))

    def test_a_broken_ask_never_raises(self, network, plan_event):
        with self.assertLogs("notifications.ask", "ERROR"):
            answer = self.answer(self.ask()[0], decide=mock.Mock(side_effect=RuntimeError("bug")))
        self.assertEqual((answer.status, answer.reason), (AgentAnswer.FAILED, "something went wrong"))

    def test_a_trip_more_than_two_hours_out_waits_until_its_buses_are_the_next_ones(self, network, plan_event):
        self.event.start = START + dt.timedelta(hours=3)  # 12:45 PM
        self.event.save()
        answer = self.answer(self.ask()[0], decide=self.agent)
        self.assertEqual(
            answer.reason, "it's more than 2 hours away, and the agent looks at the next buses: ask from 10:45 AM"
        )
        self.assertEqual(self.agent.asked, [])

    def test_a_slow_answer_never_lands_on_a_newer_question(self, network, plan_event):
        first, _ = self.ask()
        asked_again = []

        def slow(request, data, now):
            # While the agent thinks, the rider gives up waiting and asks again.
            asked_again.append(self.ask(now=ASKED + AgentAnswer.GIVE_UP)[0])
            return self.agent(request, data, now)

        self.answer(first, decide=slow)
        newer = AgentAnswer.objects.get(pk=asked_again[0].pk)
        self.assertEqual((newer.status, newer.asked_at), (AgentAnswer.ASKING, ASKED + AgentAnswer.GIVE_UP))
        self.assertEqual(newer.message, "")  # the old answer went nowhere

    def test_an_answer_for_a_disconnected_calendar_goes_nowhere(self, network, plan_event):
        answer, _ = self.ask()
        self.calendar.delete()  # disconnecting deletes the calendar, and its answers with it
        self.assertIsNone(self.answer(answer, decide=self.agent))
        self.assertFalse(AgentAnswer.objects.exists())

    def test_with_nothing_to_ask_about_it_says_why(self, network, plan_event):
        answer, _ = self.ask()
        self.assertEqual(self.answer(answer, now=self.event.start).reason, "the event has started")

        network.side_effect = FeedUnavailable("down")
        self.assertEqual(self.answer(answer).reason, "live shuttle times are unavailable right now")
        network.side_effect = None

        self.bus = Bus(lambda: ASKED, START - dt.timedelta(minutes=1))  # the feed lists no bus, and no history
        self.assertEqual(
            self.answer(answer).reason, "there's no bus time to go by: the feed lists no bus and we have no prediction"
        )

        plan_event.side_effect = lambda event, home, **kwargs: plans.EventPlan(event=event, building={"name": "X"})
        self.assertEqual(self.answer(answer).reason, "there's no bus to catch for this trip right now")

        Place.objects.all().delete()
        self.assertEqual(self.answer(answer).reason, "there's no Home to plan the trip from")

        self.event.delete()  # the answer outlives it: it names the event, as a refresh leaves it
        self.assertEqual(self.answer(answer).reason, "the event isn't on your calendar anymore")


@override_settings(**SET_UP)
@mock.patch("django.utils.timezone.now", return_value=ASKED)
@mock.patch.object(trips.plans, "plan_event", side_effect=planned)
@mock.patch.object(ask, "network", return_value=NETWORK)
@mock.patch("calendars.views.network", return_value=NETWORK)
class AskPageTests(Rider, TestCase):
    """The button on the when-to-leave page, and the endpoints it calls."""

    def setUp(self):
        self.make_rider()
        self.client.force_login(self.rider)

    def form(self):
        return {"calendar": self.calendar.pk, "uid": self.event.uid, "start": self.event.start.isoformat()}

    def post(self, data, javascript=True):
        headers = {"HTTP_ACCEPT": "application/json"} if javascript else {}
        with self.captureOnCommitCallbacks() as threads:
            response = self.client.post(reverse("agent-ask"), data, **headers)
        return response, len(threads)

    def page(self):
        return self.client.get(reverse("when-to-leave")).content.decode()

    def test_the_page_asks_then_polls_until_the_agent_answers(self, *mocks):
        self.assertIn("Ask the trip agent", self.page())
        response, threads = self.post(self.form())
        body = response.json()
        self.assertEqual((response.status_code, body["status"], threads), (200, "asking", 1))
        self.assertIn("Asking the trip agent", body["html"])
        answer = AgentAnswer.objects.get()
        poll = reverse("agent-answer", args=[answer.pk])
        self.assertIn(f'data-agent-poll="{poll}"', body["html"])

        self.answer(answer, decide=self.agent)
        body = self.client.get(poll, HTTP_ACCEPT="application/json").json()
        self.assertEqual(body["status"], "answered")
        self.assertIn("It would notify you at <strong>9:47 AM</strong>", body["html"])
        self.assertIn("reaches Grove St in 12 minutes", body["html"])
        self.assertNotIn("data-agent-poll", body["html"])
        self.assertNotIn("Ask again", body["html"])  # for five minutes the answer stands
        self.assertIn("reaches Grove St in 12 minutes", self.page())  # and the page shows it on a reload

    def test_without_javascript_the_form_comes_back_to_the_event(self, *mocks):
        response, threads = self.post(self.form(), javascript=False)
        self.assertRedirects(
            response, f"{reverse('when-to-leave')}#event-{self.event.pk}", fetch_redirect_response=False
        )
        self.assertEqual(threads, 1)
        self.assertIn("Reload the page in a minute", self.page())

    def test_only_events_with_a_bus_to_catch_get_the_button(self, *mocks):
        Event.objects.create(
            calendar=self.calendar, uid="pickup", title="Pick-up basketball", start=START + dt.timedelta(hours=2)
        )
        page = self.page()
        self.assertIn("Pick-up basketball", page)
        self.assertEqual(page.count("Ask the trip agent"), 1)  # the lecture's, not the game's

    def test_a_trip_more_than_two_hours_out_says_when_it_can_be_asked(self, *mocks):
        Event.objects.create(
            calendar=self.calendar, uid="seminar", title="Seminar", start=START + dt.timedelta(hours=3),
            location="Watson Center",
        )
        page = self.page()
        self.assertIn("You can ask the trip agent about this trip from 10:45 AM, two hours before it starts.", page)
        self.assertEqual(page.count("Ask the trip agent"), 1)  # the lecture's, 40 minutes out

    def test_a_failed_answer_says_why_and_offers_another_try(self, *mocks):
        reason = "the AI service didn't answer"
        AgentAnswer.objects.create(
            user=self.rider, calendar=self.calendar, event_uid=self.event.uid, event_start=self.event.start,
            asked_at=ASKED, status=AgentAnswer.FAILED, reason=reason,
        )
        page = self.page()
        self.assertIn(f"The trip agent couldn't answer: {escape(reason)}.", page)
        self.assertIn("Try again", page)

    def test_an_event_gone_since_the_page_loaded_says_so(self, *mocks):
        form = self.form()
        self.event.delete()
        response, threads = self.post(form)
        self.assertEqual((response.status_code, threads), (404, 0))
        response, _ = self.post(form, javascript=False)
        self.assertRedirects(response, reverse("when-to-leave"), fetch_redirect_response=False)

    def test_another_riders_event_and_answer_are_not_found(self, *mocks):
        answer = ask.start(self.rider, self.event)
        self.client.force_login(User.objects.create_user("rider-two", password="not-it"))
        response, threads = self.post(self.form())
        self.assertEqual((response.status_code, threads), (404, 0))
        self.assertEqual(self.client.get(reverse("agent-answer", args=[answer.pk])).status_code, 404)

    def test_asking_needs_a_log_in(self, *mocks):
        self.client.logout()
        response, threads = self.post(self.form(), javascript=False)
        self.assertEqual((response.status_code, threads), (302, 0))
        self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(AgentAnswer.objects.exists())
