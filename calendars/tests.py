"""Connecting a calendar over HTTP (#25): the address form, the refresh, and
what the hourly job does. The calendar's server is mocked at
calendars.ical.urlopen, so no test reaches the network; the parsing itself is
covered in test_import.py."""

import datetime as dt
from unittest import mock
from urllib.error import URLError
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Calendar, Event

User = get_user_model()
ET = ZoneInfo("America/New_York")
ADDRESS = "https://calendar.example.com/rider.ics"


def fixture_ics(now=None):
    """A small calendar with events near now — imports look a week ahead, so a
    fixed date would fall out of the window one day. A one-off lecture two
    hours from now, and a daily class repeating all week."""
    now = now or timezone.now()

    def utc(when):
        return when.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lecture = now + dt.timedelta(hours=2)
    daily = (now + dt.timedelta(hours=4)).replace(minute=0, second=0, microsecond=0)
    return "".join([
        "BEGIN:VCALENDAR\r\n",
        "VERSION:2.0\r\n",
        "PRODID:-//Test//EN\r\n",
        "BEGIN:VEVENT\r\n",
        "UID:lecture@yale\r\n",
        "SUMMARY:MGT 656 Lecture\r\n",
        f"DTSTART:{utc(lecture)}\r\n",
        f"DTEND:{utc(lecture + dt.timedelta(minutes=75))}\r\n",
        "LOCATION:Watson Center\r\n",
        "END:VEVENT\r\n",
        "BEGIN:VEVENT\r\n",
        "UID:section@yale\r\n",
        "SUMMARY:Stats section\r\n",
        f"DTSTART:{utc(daily)}\r\n",
        "RRULE:FREQ=DAILY;COUNT=5\r\n",
        "END:VEVENT\r\n",
        "END:VCALENDAR\r\n",
    ]).encode()


def ics_answer(data=fixture_ics()):
    """A urlopen stand-in serving a calendar file, as transit's tests do."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = data
    return mock.Mock(return_value=response)


def connect(user, address=ADDRESS):
    return services.save_url(user, address)


class ServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rider", password="not-the-real-password")

    def test_connecting_creates_one_calendar_and_replacing_updates_it(self):
        first = connect(self.user)
        self.assertEqual(Calendar.objects.filter(user=self.user).count(), 1)
        second = connect(self.user, "https://calendar.example.com/other.ics")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.ics_url, "https://calendar.example.com/other.ics")

    def test_disconnecting_deletes_the_events_but_not_the_demo_calendar(self):
        calendar = connect(self.user)
        services.refresh(calendar, data=fixture_ics())
        demo = Calendar.objects.create(user=self.user, ics_url="", is_demo=True)
        Event.objects.create(calendar=demo, uid="demo@yale", title="Demo event", start=timezone.now())
        services.remove(self.user)
        self.assertFalse(Calendar.objects.filter(user=self.user, is_demo=False).exists())
        self.assertTrue(Event.objects.filter(calendar=demo).exists())

    def test_a_refresh_imports_and_the_next_one_replaces(self):
        calendar = connect(self.user)
        imported = services.refresh(calendar, data=fixture_ics())
        self.assertEqual(imported, 6)  # the lecture, and five repeats of the section
        self.assertEqual(calendar.last_error, "")
        self.assertIsNotNone(calendar.last_imported_at)
        changed = fixture_ics().replace(b"SUMMARY:MGT 656 Lecture", b"SUMMARY:Renamed lecture")
        self.assertEqual(services.refresh(calendar, data=changed), 6)
        titles = set(Event.objects.filter(calendar=calendar).values_list("title", flat=True))
        self.assertEqual(titles, {"Renamed lecture", "Stats section"})

    def test_a_failed_refresh_keeps_the_old_events_and_says_why(self):
        calendar = connect(self.user)
        services.refresh(calendar, data=fixture_ics())
        self.assertEqual(services.try_refresh(calendar, data=b"<html>no calendar here</html>"), 0)
        calendar.refresh_from_db()
        self.assertIn("calendar we can read", calendar.last_error)
        self.assertEqual(Event.objects.filter(calendar=calendar).count(), 6)

    def test_the_hourly_job_refreshes_each_calendar_and_skips_the_demo_one(self):
        good = connect(self.user)
        other = User.objects.create_user("someone-else", password="not-it")
        broken = connect(other, "https://calendar.example.com/broken.ics")
        demo = Calendar.objects.create(user=self.user, ics_url="", is_demo=True)
        Event.objects.create(calendar=demo, uid="demo@yale", title="Demo event", start=timezone.now())

        def fake_urlopen(url, timeout):
            if "broken" in url:
                raise URLError("down")
            return ics_answer()()

        with mock.patch("calendars.ical.urlopen", side_effect=fake_urlopen):
            summary = services.refresh_all()
        self.assertIn("2 calendars", summary)
        self.assertIn("6 events imported", summary)
        self.assertIn("1 failed", summary)
        self.assertEqual(Event.objects.filter(calendar=good).count(), 6)
        broken.refresh_from_db()
        self.assertIn("couldn't reach", broken.last_error)
        self.assertEqual(Event.objects.filter(calendar=demo).count(), 1)  # untouched

    def test_upcoming_is_the_next_day_for_this_rider_only(self):
        calendar = connect(self.user)
        services.refresh(calendar, data=fixture_ics())
        demo = Calendar.objects.create(user=self.user, ics_url="", is_demo=True)
        Event.objects.create(calendar=demo, uid="demo@yale", title="Demo event", start=timezone.now() + dt.timedelta(hours=1))
        stranger = User.objects.create_user("someone-else", password="not-it")
        stranger_calendar = connect(stranger)
        services.refresh(stranger_calendar, data=fixture_ics())
        soon = services.upcoming(self.user)
        self.assertEqual(
            {event.title for event in soon},
            {"MGT 656 Lecture", "Stats section", "Demo event"},
        )
        # A refreshed calendar holds a week of events; the next 24 hours don't.
        self.assertTrue(all(event.start <= timezone.now() + dt.timedelta(hours=24) for event in soon))


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rider", password="not-the-real-password")
        self.client.force_login(self.user)

    def test_a_rider_who_is_not_logged_in_is_asked_to(self):
        self.client.logout()
        response = self.client.get(reverse("calendar"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_fresh_rider_sees_the_connect_form(self):
        response = self.client.get(reverse("calendar"))
        self.assertContains(response, "Calendar address (ICS)")

    def test_connecting_reads_the_calendar_and_shows_the_events(self):
        with mock.patch("calendars.ical.urlopen", new_callable=ics_answer) as urlopen:
            response = self.client.post(reverse("calendar"), {"ics_url": ADDRESS})
        self.assertEqual(response.status_code, 302)
        urlopen.assert_called_once()
        self.assertEqual(urlopen.call_args.args[0], ADDRESS)
        calendar = services.connection(self.user)
        self.assertEqual(Event.objects.filter(calendar=calendar).count(), 6)
        page = self.client.get(reverse("calendar")).content.decode()
        self.assertIn("Imported 6 upcoming events", page)
        self.assertIn("MGT 656 Lecture", page)
        self.assertIn("Connected", page)

    def test_an_address_that_cant_be_read_still_connects_but_says_so(self):
        with mock.patch("calendars.ical.urlopen", side_effect=URLError("down")):
            response = self.client.post(reverse("calendar"), {"ics_url": ADDRESS})
        self.assertEqual(response.status_code, 302)
        calendar = services.connection(self.user)
        self.assertIsNotNone(calendar)  # the address itself was saved
        self.assertIn("couldn't reach", calendar.last_error)
        page = self.client.get(reverse("calendar")).content.decode()
        self.assertIn("read events yet", page)  # the apostrophe is HTML-escaped on the page

    def test_an_address_that_isnt_an_address_is_refused(self):
        response = self.client.post(reverse("calendar"), {"ics_url": "not a web address"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Enter a valid URL", status_code=400)

    def test_replacing_the_address_keeps_the_one_calendar(self):
        with mock.patch("calendars.ical.urlopen", new_callable=ics_answer):  # no real address is read
            self.client.post(reverse("calendar"), {"ics_url": ADDRESS})
            self.client.post(reverse("calendar"), {"ics_url": "https://calendar.example.com/new.ics"})
        calendars = Calendar.objects.filter(user=self.user, is_demo=False)
        self.assertEqual(calendars.count(), 1)
        self.assertEqual(calendars.get().ics_url, "https://calendar.example.com/new.ics")

    def test_disconnecting_takes_the_calendar_and_its_events_away(self):
        calendar = connect(self.user)
        services.refresh(calendar, data=fixture_ics())
        response = self.client.post(reverse("calendar-disconnect"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Calendar.objects.filter(user=self.user, is_demo=False).count(), 0)
        self.assertEqual(Event.objects.filter(calendar=calendar).count(), 0)

    def test_disconnecting_is_a_post_thing(self):
        response = self.client.get(reverse("calendar-disconnect"))
        self.assertEqual(response.status_code, 405)

    def test_another_riders_events_stay_off_your_page(self):
        stranger = User.objects.create_user("someone-else", password="not-it")
        calendar = connect(stranger)
        services.refresh(calendar, data=fixture_ics())
        page = self.client.get(reverse("calendar")).content.decode()
        self.assertNotIn("MGT 656 Lecture", page)
