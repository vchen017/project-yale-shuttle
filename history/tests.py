"""Tests for the history import (#22). Nothing here reaches the network: the
importer tests feed it parsed rows directly, and the command tests mock
history.sources, the app's only network module."""

import datetime as dt
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase

from history import importer, sources
from history.models import Announcement, BusPosition, Eta, Poll, RouteStatus

UTC = dt.UTC
DAY = dt.date(2026, 11, 10)


def at(minute):
    return dt.datetime(2026, 11, 10, 0, minute, tzinfo=UTC)


def _bus(polled_at, bus_id, name, minutes_old=10):
    return {
        "polled_at": polled_at,
        "bus_id": bus_id,
        "bus_name": name,
        "route_id": 10,
        "route_name": "Purple - West Campus",
        "lat": 41.287934,
        "lon": -72.924141,
        "heading": 34,
        "last_stop_id": 26,
        "last_stop_name": "Building 900",
        "last_update": polled_at - dt.timedelta(minutes=minutes_old),
    }


def _eta(polled_at, stop_id, stop_name, bus_id, bus_name, eta_minutes):
    return {
        "polled_at": polled_at,
        "stop_id": stop_id,
        "stop_name": stop_name,
        "route_id": 10,
        "route_name": "Purple - West Campus",
        "bus_id": bus_id,
        "bus_name": bus_name,
        "eta_minutes": eta_minutes,
        "calculation_time": polled_at + dt.timedelta(seconds=1),
    }


def _route_status(polled_at, route_id, name, short_name, active):
    return {
        "polled_at": polled_at,
        "route_id": route_id,
        "route_name": name,
        "short_name": short_name,
        "description": "7am - 6pm, M - F",
        "active": active,
        "num_stops": 31,
    }


def _announcement(polled_at, announcement_id):
    return {
        "polled_at": polled_at,
        "announcement_id": announcement_id,
        "title": "Red, Brown",
        "message": "State Street Station has been relocated.",
        "button_text": "",
        "button_url": "",
    }


def sample_day():
    """Two polls, two buses, one active and one inactive route, two stops."""
    return {
        "buses": [
            _bus(at(0), 68866, "#303"),
            _bus(at(0), 68868, "#321"),
            _bus(at(5), 68866, "#303", minutes_old=5),
            _bus(at(5), 68868, "#321", minutes_old=5),
        ],
        "etas": [
            _eta(at(0), 1, "100 Church Street South", 68866, "#303", 23),
            _eta(at(0), 1, "100 Church Street South", 68868, "#321", 1),
            _eta(at(0), 2, "215 Park Street", 68866, "#303", 31),
            _eta(at(5), 1, "100 Church Street South", 68866, "#303", 18),
            _eta(at(5), 2, "215 Park Street", 68868, "#321", 12),
        ],
        "route_status": [
            _route_status(at(0), 10, "Purple - West Campus", "PW", True),
            _route_status(at(0), 4, "Blue - Weekend Daytime", "BW", False),
            _route_status(at(5), 10, "Purple - West Campus", "PW", True),
            _route_status(at(5), 4, "Blue - Weekend Daytime", "BW", False),
        ],
        "announcements": [_announcement(at(0), 23), _announcement(at(5), 23)],
    }


def sample_gaps():
    """Three missing polls: one with full job-log counts, one with none."""
    return [
        {
            "polled_at": at(10),
            "cause": "run_could_not_save",
            "active_routes": 3,
            "bus_count": 5,
            "announcement_count": 1,
            "eta_count": 40,
        },
        {
            "polled_at": at(15),
            "cause": "no_run_started",
            "active_routes": None,
            "bus_count": None,
            "announcement_count": None,
            "eta_count": None,
        },
        {
            "polled_at": at(20),
            "cause": "run_could_not_save",
            "active_routes": 3,
            "bus_count": 4,
            "announcement_count": 1,
            "eta_count": 38,
        },
    ]


class ImportDayTests(TestCase):
    def test_imports_all_four_tables_and_marks_polls_recorded(self):
        stats = importer.import_day(DAY, sample_day())

        self.assertEqual(stats.polls_added, 2)
        self.assertEqual(Poll.objects.count(), 2)
        self.assertFalse(Poll.objects.exclude(status=Poll.Status.RECORDED).exists())
        self.assertEqual(BusPosition.objects.count(), 4)
        self.assertEqual(Eta.objects.count(), 5)
        self.assertEqual(RouteStatus.objects.count(), 4)
        self.assertEqual(Announcement.objects.count(), 2)

        first = Poll.objects.get(polled_at=at(0))
        bus = BusPosition.objects.get(poll=first, bus_id=68866)
        self.assertEqual(bus.bus_name, "#303")
        self.assertEqual(bus.route_id, 10)
        self.assertEqual(bus.last_update, at(0) - dt.timedelta(minutes=10))
        eta = Eta.objects.get(poll=first, stop_id=1, bus_id=68868)
        self.assertEqual(eta.eta_minutes, 1)
        self.assertEqual(eta.stop_name, "100 Church Street South")
        # Short names aren't unique, so both BW routes coexist by numeric id.
        inactive = RouteStatus.objects.get(poll=first, route_id=4)
        self.assertEqual(inactive.short_name, "BW")
        self.assertFalse(inactive.active)
        self.assertTrue(RouteStatus.objects.get(poll=first, route_id=10).active)

    def test_rerunning_the_same_day_adds_no_duplicates(self):
        importer.import_day(DAY, sample_day())
        stats = importer.import_day(DAY, sample_day())

        self.assertEqual(stats.polls_added, 0)
        self.assertEqual(stats.polls_skipped, 2)
        self.assertEqual(Poll.objects.count(), 2)
        self.assertEqual(BusPosition.objects.count(), 4)
        self.assertEqual(Eta.objects.count(), 5)
        self.assertEqual(RouteStatus.objects.count(), 4)
        self.assertEqual(Announcement.objects.count(), 2)

    def test_rows_are_never_double_counted_across_polls(self):
        importer.import_day(DAY, sample_day())
        # The same announcement in two polls is two rows, one per poll.
        self.assertEqual(Announcement.objects.filter(announcement_id=23).count(), 2)
        poll_ids = Announcement.objects.filter(announcement_id=23).values_list("poll", flat=True)
        self.assertEqual(len(set(poll_ids)), 2)


class ImportGapsTests(TestCase):
    def test_missing_polls_are_stored_as_missing_not_as_no_buses(self):
        stats = importer.import_gaps(sample_gaps())

        self.assertEqual(stats.polls_added, 3)
        self.assertEqual(Poll.objects.count(), 3)
        self.assertFalse(BusPosition.objects.exists())  # a gap is not "no buses"
        gap = Poll.objects.get(polled_at=at(10))
        self.assertEqual(gap.status, Poll.Status.MISSING)
        self.assertEqual(gap.cause, "run_could_not_save")
        self.assertEqual(gap.active_routes, 3)
        self.assertEqual(gap.bus_count, 5)
        self.assertEqual(gap.eta_count, 40)
        logged_nothing = Poll.objects.get(polled_at=at(15))
        self.assertEqual(logged_nothing.status, Poll.Status.MISSING)
        self.assertIsNone(logged_nothing.bus_count)
        self.assertIsNone(logged_nothing.eta_count)

    def test_rerunning_the_gaps_adds_no_duplicates(self):
        importer.import_gaps(sample_gaps())
        stats = importer.import_gaps(sample_gaps())

        self.assertEqual(stats.polls_added, 0)
        self.assertEqual(stats.polls_already_known, 3)
        self.assertEqual(Poll.objects.count(), 3)

    def test_a_recorded_poll_is_never_demoted_to_missing(self):
        importer.import_day(DAY, sample_day())
        stats = importer.import_gaps(
            [{"polled_at": at(0), "cause": "run_could_not_save", "active_routes": None,
              "bus_count": None, "announcement_count": None, "eta_count": None}]
        )

        self.assertEqual(stats.polls_added, 0)
        poll = Poll.objects.get(polled_at=at(0))
        self.assertEqual(poll.status, Poll.Status.RECORDED)
        self.assertEqual(poll.cause, "")


class ImportHistoryCommandTests(TestCase):
    """The whole command, with every GitHub fetch mocked to a small local copy."""

    def call_command_with_fixtures(self):
        out = mock.Mock()
        with (
            mock.patch(
                "history.management.commands.import_history.sources.list_dates",
                return_value=[DAY],
            ),
            mock.patch(
                "history.management.commands.import_history.sources.read_gaps",
                return_value=sample_gaps(),
            ),
            mock.patch(
                "history.management.commands.import_history.sources.read_day",
                return_value=sample_day(),
            ) as read_day,
        ):
            call_command("import_history", stdout=out)
        return out, read_day

    def test_the_command_imports_gaps_and_recorded_polls(self):
        out, _ = self.call_command_with_fixtures()

        self.assertEqual(Poll.objects.count(), 5)  # 2 recorded + 3 missing
        self.assertEqual(Poll.objects.filter(status=Poll.Status.RECORDED).count(), 2)
        self.assertEqual(Poll.objects.filter(status=Poll.Status.MISSING).count(), 3)
        self.assertEqual(BusPosition.objects.count(), 4)
        printed = "\n".join(str(call.args[0]) for call in out.write.call_args_list)
        self.assertIn("3 missing polls stored", printed)
        self.assertIn("2 polls imported", printed)

    def test_rerunning_the_command_adds_no_duplicates(self):
        self.call_command_with_fixtures()
        self.call_command_with_fixtures()

        self.assertEqual(Poll.objects.count(), 5)
        self.assertEqual(BusPosition.objects.count(), 4)
        self.assertEqual(Eta.objects.count(), 5)

    def test_command_selects_only_the_requested_day(self):
        out = mock.Mock()
        with (
            mock.patch(
                "history.management.commands.import_history.sources.list_dates",
                return_value=[DAY - dt.timedelta(days=1), DAY],
            ),
            mock.patch(
                "history.management.commands.import_history.sources.read_gaps",
                return_value=[],
            ),
            mock.patch(
                "history.management.commands.import_history.sources.read_day",
                return_value=sample_day(),
            ) as read_day,
        ):
            call_command("import_history", "--days", "1", stdout=out)

        read_day.assert_called_once_with(DAY)

    def test_conflicting_date_arguments_are_rejected(self):
        with self.assertRaisesMessage(CommandError, "only one"):
            call_command("import_history", "--date", "2026-11-10", "--days", "1")

    def test_a_bad_date_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "YYYY-MM-DD"):
            call_command("import_history", "--since", "november 1st")

    def test_a_bad_date_is_rejected_before_touching_the_network(self):
        # Arguments are checked first: even with GitHub unreachable, the
        # date error is what comes out (CI caught this ordering once).
        with mock.patch(
            "history.management.commands.import_history.sources.list_dates",
            side_effect=OSError("no network"),
        ), self.assertRaisesMessage(CommandError, "YYYY-MM-DD"):
            call_command("import_history", "--since", "november 1st")


class SourceParsingTests(TestCase):
    """The CSV-to-rows parsers, on strings straight from the recorder's files."""

    def test_bus_row_parsing(self):
        row = {
            "polled_at_utc": "2026-11-10T00:00:00+00:00",
            "bus_id": "68866",
            "bus_name": "#303",
            "route_id": "10",
            "route_name": "Purple - West Campus",
            "lat": "41.287934",
            "lon": "-72.924141",
            "heading": "34",
            "last_stop_id": "26",
            "last_stop_name": "Building 900",
            "last_update_utc": "2026-11-09T23:59:59+00:00",
        }
        bus = sources._bus(row)
        self.assertEqual(bus["polled_at"], at(0))
        self.assertEqual(bus["bus_id"], 68866)
        self.assertEqual(bus["lat"], 41.287934)
        self.assertEqual(bus["last_update"], dt.datetime(2026, 11, 9, 23, 59, 59, tzinfo=UTC))
        self.assertEqual(bus["heading"], 34)

    def test_eta_row_parsing(self):
        row = {
            "polled_at_utc": "2026-11-10T00:00:00+00:00",
            "stop_id": "1",
            "stop_name": "100 Church Street South",
            "route_id": "10",
            "route_name": "Purple - West Campus",
            "bus_id": "68866",
            "bus_name": "#303",
            "eta_minutes": "23",
            "calculation_time_utc": "2026-11-10T00:00:01+00:00",
        }
        eta = sources._eta(row)
        self.assertEqual(eta["stop_id"], 1)
        self.assertEqual(eta["eta_minutes"], 23)
        self.assertEqual(eta["calculation_time"], at(0) + dt.timedelta(seconds=1))

    def test_route_status_row_parsing(self):
        row = {
            "polled_at_utc": "2026-11-10T00:00:00+00:00",
            "route_id": "1",
            "route_name": "Blue - Weekday Daytime",
            "short_name": "BD",
            "description": "7am - 6pm, M - F",
            "active": "False",
            "num_stops": "31",
        }
        status = sources._route_status(row)
        self.assertEqual(status["route_id"], 1)
        self.assertFalse(status["active"])
        self.assertEqual(status["num_stops"], 31)

    def test_empty_counts_parse_as_none(self):
        row = {
            "polled_at_utc": "2026-10-27T05:45:00+00:00",
            "cause": "run_could_not_save",
            "active_routes": "",
            "buses": "",
            "announcements": "",
            "etas": "",
        }
        # read_rows yields strings; read_gaps parses. Reuse its internals via
        # a patched fetch so no network is touched.
        with mock.patch(
            "history.sources.read_rows", return_value=iter([row])
        ):
            gaps = sources.read_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["polled_at"], dt.datetime(2026, 10, 27, 5, 45, tzinfo=UTC))
        self.assertIsNone(gaps[0]["bus_count"])
        self.assertEqual(gaps[0]["cause"], "run_could_not_save")
