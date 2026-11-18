"""Reading an ICS file (#25): the parsing only, with the bytes passed
straight to `ical.parse`, so nothing here touches the network. In its own
file, so it doesn't collide with other pull requests."""

import datetime as dt
from unittest import mock
from urllib.error import URLError
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from . import ical

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

# CRLF line endings and the folding of a long line are part of the format.
FIXTURE = (
    b"BEGIN:VCALENDAR\r\n"
    b"VERSION:2.0\r\n"
    b"PRODID:-//Test//EN\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:lecture@yale\r\n"
    b"SUMMARY:MGT 656 Lecture\r\n"
    b"DTSTART;TZID=America/New_York:20261120T130000\r\n"
    b"DTEND;TZID=America/New_York:20261120T141500\r\n"
    b"LOCATION:Watson Center\\, Room 202\r\n"
    b"RRULE:FREQ=WEEKLY;COUNT=3\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:office-hours@yale\r\n"
    b"SUMMARY:Office hours with a summary line long enough that it has to be fo\r\n"
    b" lded across two lines the way RFC 5545 asks\r\n"
    b"DTSTART:20261120T223000Z\r\n"
    b"DTEND:20261120T233000Z\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:study-group@yale\r\n"
    b"SUMMARY:Study group\r\n"
    b"DTSTART:20261121T190000\r\n"
    b"DURATION:PT1H\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:recess@yale\r\n"
    b"SUMMARY:Fall recess begins\r\n"
    b"DTSTART;VALUE=DATE:20261121\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

# A fortnight that catches every occurrence the fixture describes.
WINDOW = (dt.datetime(2026, 11, 19, tzinfo=ET), dt.datetime(2026, 12, 5, tzinfo=ET))


def parse(data=FIXTURE, window=WINDOW):
    return ical.parse(data, *window)


class ParseTests(SimpleTestCase):
    def test_the_first_event_with_its_eastern_times_and_escaped_location(self):
        events = parse()
        self.assertEqual(events[0]["uid"], "lecture@yale")
        self.assertEqual(events[0]["title"], "MGT 656 Lecture")
        self.assertEqual(events[0]["start"], dt.datetime(2026, 11, 20, 13, 0, tzinfo=ET))
        self.assertEqual(events[0]["end"], dt.datetime(2026, 11, 20, 14, 15, tzinfo=ET))
        self.assertEqual(events[0]["location"], "Watson Center, Room 202")

    def test_a_repeat_becomes_one_row_per_occurrence_with_the_same_uid(self):
        events = [event for event in parse() if event["uid"] == "lecture@yale"]
        self.assertEqual([event["start"] for event in events], [
            dt.datetime(2026, 11, 20, 18, 0, tzinfo=UTC),
            dt.datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
            dt.datetime(2026, 12, 4, 18, 0, tzinfo=UTC),
        ])

    def test_every_stored_datetime_is_time_zone_aware(self):
        for event in parse():
            self.assertIsNotNone(event["start"].tzinfo)
            self.assertIsNotNone(event["end"].tzinfo)

    def test_a_folded_line_is_read_as_one(self):
        events = [event for event in parse() if event["uid"] == "office-hours@yale"]
        self.assertEqual(
            events[0]["title"],
            "Office hours with a summary line long enough that it has to be folded across two lines the way RFC 5545 asks",
        )
        self.assertEqual(events[0]["start"], dt.datetime(2026, 11, 20, 22, 30, tzinfo=UTC))

    def test_a_duration_becomes_an_end_and_a_floating_time_is_eastern(self):
        events = [event for event in parse() if event["uid"] == "study-group@yale"]
        self.assertEqual(events[0]["start"], dt.datetime(2026, 11, 21, 19, 0, tzinfo=ET))
        self.assertEqual(events[0]["end"], dt.datetime(2026, 11, 21, 20, 0, tzinfo=ET))

    def test_an_all_day_event_is_midnight_eastern(self):
        events = [event for event in parse() if event["uid"] == "recess@yale"]
        self.assertEqual(events[0]["start"], dt.datetime(2026, 11, 21, tzinfo=ET))

    def test_events_come_back_soonest_first(self):
        self.assertEqual([event["start"] for event in parse()], sorted(event["start"] for event in parse()))

    def test_an_unreadable_file_raises(self):
        with self.assertRaises(ical.CalendarUnavailable):
            parse(b"<html><body>not a calendar</body></html>")

    def test_a_missing_start_drops_one_event_and_keeps_the_rest(self):
        broken = FIXTURE.replace(b"DTSTART;TZID=America/New_York:20261120T130000\r\n", b"")
        events = parse(broken)
        # Soonest first: the recess's midnight Eastern beats the 7 PM study group.
        self.assertEqual([event["uid"] for event in events], ["office-hours@yale", "recess@yale", "study-group@yale"])

    def test_events_outside_the_window_are_left_out(self):
        # The weekly lecture's second occurrence, and nothing else, falls in this window.
        events = parse(
            FIXTURE,
            (dt.datetime(2026, 11, 26, tzinfo=ET), dt.datetime(2026, 11, 28, tzinfo=ET)),
        )
        self.assertEqual([event["uid"] for event in events], ["lecture@yale"])


class FetchTests(SimpleTestCase):
    def answer(self, data=FIXTURE):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = data
        return mock.Mock(return_value=response)

    def test_the_file_comes_back_with_a_timeout(self):
        with mock.patch("calendars.ical.urlopen", new_callable=self.answer) as urlopen:
            self.assertEqual(ical.fetch("https://example.com/calendar.ics"), FIXTURE)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], ical.TIMEOUT)

    def test_a_server_that_doesnt_answer_raises(self):
        with mock.patch("calendars.ical.urlopen", side_effect=URLError("down")):
            with self.assertRaises(ical.CalendarUnavailable):
                ical.fetch("https://example.com/calendar.ics")
