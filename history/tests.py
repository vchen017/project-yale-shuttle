"""Tests for the history import (#22) and our predictions (#23). Nothing
here reaches the network: the importer tests feed it parsed rows directly,
the prediction tests build synthetic history in the database, and the
command tests mock history.sources, the app's only network module."""

import datetime as dt
import json
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase

from history import importer, predictions, sources
from history.models import (
    Announcement,
    BusPosition,
    Eta,
    Poll,
    RouteStatus,
    RouteStopChange,
    RouteStopList,
    RouteSyncState,
)

UTC = dt.UTC
ET = predictions.ET
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
            # Route 19's short name is 15 characters: longer than the old
            # max_length=10, which PostgreSQL rejects (SQLite doesn't check).
            _route_status(at(0), 19, "Brown Connector", "Brown Connector", True),
            _route_status(at(5), 10, "Purple - West Campus", "PW", True),
            _route_status(at(5), 4, "Blue - Weekend Daytime", "BW", False),
            _route_status(at(5), 19, "Brown Connector", "Brown Connector", True),
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


# ----- The routes.json walk (#57) -----
#
# Fixtures follow what the recorder actually committed around Nov 13, 2026:
# the evening before, routes 3 (Red) and 19 (Brown Connector) still have
# their old stop lists; at 10:30 UTC stop 1 (100 Church Street South)
# joins both; five minutes later stop 14 (Amistad / Church St South)
# leaves route 3 — the change issue #57 exists for.

ROUTE_3_STOPS = [
    121, 115, 45, 75, 30, 31, 41, 126, 36, 128, 120, 3, 147, 27, 11, 146, 49,
    48, 104, 113, 4, 42, 98, 38, 39, 72, 117, 13, 14,
]
ROUTE_19_STOPS = [145, 147, 4, 42, 98, 121, 115, 172, 47]

SHA_PRE = "f521fda000000000000000000000000000000000"
SHA_1030 = "8fdaa48900000000000000000000000000000000"
SHA_1035 = "dd29a46400000000000000000000000000000000"
AT_PRE = dt.datetime(2026, 11, 12, 23, 45, tzinfo=UTC)
AT_1030 = dt.datetime(2026, 11, 13, 10, 30, tzinfo=UTC)
AT_1035 = dt.datetime(2026, 11, 13, 10, 35, tzinfo=UTC)

ROUTE_COMMITS = [(SHA_PRE, AT_PRE), (SHA_1030, AT_1030), (SHA_1035, AT_1035)]


def routes_payload(route_3_stops, route_19_stops):
    """A routes.json as the walk reads it, for just the two routes we track."""
    return [
        {"id": 3, "name": "Red - Weekday Daytime", "stops": route_3_stops},
        {"id": 19, "name": "Brown Connector", "stops": route_19_stops},
    ]


ROUTE_PAYLOADS = {
    SHA_PRE: routes_payload(ROUTE_3_STOPS, ROUTE_19_STOPS),
    SHA_1030: routes_payload(
        [1] + ROUTE_3_STOPS, ROUTE_19_STOPS[:5] + [1] + ROUTE_19_STOPS[5:]
    ),
    # Five minutes later only route 3 changes, losing stop 14; route 19's
    # list is committed again, identical, and must record nothing.
    SHA_1035: routes_payload(
        [1] + ROUTE_3_STOPS[:-1], ROUTE_19_STOPS[:5] + [1] + ROUTE_19_STOPS[5:]
    ),
}


class ImportDayTests(TestCase):
    def test_imports_all_four_tables_and_marks_polls_recorded(self):
        stats = importer.import_day(DAY, sample_day())

        self.assertEqual(stats.polls_added, 2)
        self.assertEqual(Poll.objects.count(), 2)
        self.assertFalse(Poll.objects.exclude(status=Poll.Status.RECORDED).exists())
        self.assertEqual(BusPosition.objects.count(), 4)
        self.assertEqual(Eta.objects.count(), 5)
        self.assertEqual(RouteStatus.objects.count(), 6)
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
        # The 15-character short name survives the round trip.
        brown = RouteStatus.objects.get(poll=first, route_id=19)
        self.assertEqual(brown.short_name, "Brown Connector")

    def test_rerunning_the_same_day_adds_no_duplicates(self):
        importer.import_day(DAY, sample_day())
        stats = importer.import_day(DAY, sample_day())

        self.assertEqual(stats.polls_added, 0)
        self.assertEqual(stats.polls_skipped, 2)
        self.assertEqual(Poll.objects.count(), 2)
        self.assertEqual(BusPosition.objects.count(), 4)
        self.assertEqual(Eta.objects.count(), 5)
        self.assertEqual(RouteStatus.objects.count(), 6)
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

    def call_command_with_fixtures(self, commits=(), payloads=None):
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
            ),
            mock.patch(
                "history.management.commands.import_history.sources.list_route_commits",
                return_value=list(commits),
            ),
            mock.patch(
                "history.management.commands.import_history.sources.read_routes_json",
                side_effect=lambda sha: (payloads or {})[sha],
            ) as read_routes_json,
        ):
            call_command("import_history", stdout=out)
        return out, read_routes_json

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
            mock.patch(
                "history.management.commands.import_history.sources.list_route_commits",
                return_value=[],
            ),
        ):
            call_command("import_history", "--days", "1", stdout=out)

        read_day.assert_called_once_with(DAY)

    def test_the_command_walks_the_routes_json_history(self):
        out, read_routes_json = self.call_command_with_fixtures(
            commits=ROUTE_COMMITS, payloads=ROUTE_PAYLOADS
        )

        self.assertEqual(RouteStopList.objects.count(), 5)  # route 3 ×3 (pre, +1, -14), route 19 ×2 (pre, +1)
        self.assertEqual(RouteStopChange.objects.count(), 3)
        printed = "\n".join(str(call.args[0]) for call in out.write.call_args_list)
        self.assertIn("3 new commits walked", printed)
        self.assertIn("5 stop-list versions stored", printed)
        self.assertIn("3 changes recorded", printed)
        # All three commits fetched, in order, nothing else.
        self.assertEqual(
            [call.args[0] for call in read_routes_json.call_args_list],
            [SHA_PRE, SHA_1030, SHA_1035],
        )
        change = RouteStopChange.objects.get(route_id=3, changed_at=AT_1035)
        self.assertEqual(change.added_stop_ids, [])
        self.assertEqual(change.removed_stop_ids, [14])

    def test_rerunning_the_command_walks_nothing_new(self):
        self.call_command_with_fixtures(commits=ROUTE_COMMITS, payloads=ROUTE_PAYLOADS)
        out, read_routes_json = self.call_command_with_fixtures(
            commits=ROUTE_COMMITS, payloads=ROUTE_PAYLOADS
        )

        read_routes_json.assert_not_called()
        self.assertEqual(RouteStopList.objects.count(), 5)
        self.assertEqual(RouteStopChange.objects.count(), 3)
        printed = "\n".join(str(call.args[0]) for call in out.write.call_args_list)
        self.assertIn("0 new commits walked", printed)

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


class RouteHistoryTests(TestCase):
    """The walk over the recorder's routes.json versions (#57)."""

    def walk(self):
        return importer.import_route_history(
            ROUTE_COMMITS, lambda sha: ROUTE_PAYLOADS[sha]
        )

    def test_the_walk_records_versions_and_changes(self):
        stats = self.walk()

        self.assertEqual(stats.commits_seen, 3)
        self.assertEqual(stats.versions_added, 5)
        self.assertEqual(stats.changes_added, 3)
        self.assertEqual(RouteStopList.objects.count(), 5)
        # Route 3: first seen with the old list; 10:30 gains stop 1 (100
        # Church Street South); 10:35 loses stop 14 (Amistad / Church St South).
        first_red = RouteStopList.objects.get(route_id=3, commit_sha=SHA_PRE)
        self.assertEqual(first_red.stop_ids, ROUTE_3_STOPS)
        self.assertEqual(first_red.route_name, "Red - Weekday Daytime")
        self.assertEqual(RouteStopChange.objects.filter(route_id=3, changed_at=AT_PRE).count(), 0)
        gain = RouteStopChange.objects.get(route_id=3, changed_at=AT_1030)
        self.assertEqual(gain.added_stop_ids, [1])
        self.assertEqual(gain.removed_stop_ids, [])
        loss = RouteStopChange.objects.get(route_id=3, changed_at=AT_1035)
        self.assertEqual(loss.added_stop_ids, [])
        self.assertEqual(loss.removed_stop_ids, [14])
        # Route 19: first seen with the old list, then stop 1 at 10:30; its
        # identical 10:35 commit records nothing.
        self.assertEqual(RouteStopList.objects.filter(route_id=19).count(), 2)
        brown = RouteStopChange.objects.get(route_id=19)
        self.assertEqual(brown.changed_at, AT_1030)
        self.assertEqual(brown.added_stop_ids, [1])
        # The sync state stops at the newest commit.
        state = RouteSyncState.objects.get()
        self.assertEqual(state.last_commit_sha, SHA_1035)
        self.assertEqual(state.last_commit_at, AT_1035)

    def test_current_stops_follows_the_newest_version(self):
        self.assertIsNone(predictions.current_stops(3))  # nothing walked yet
        self.walk()
        self.assertEqual(predictions.current_stops(3), set([1] + ROUTE_3_STOPS[:-1]))
        self.assertNotIn(14, predictions.current_stops(3))

    def test_an_interrupted_walk_is_idempotent(self):
        self.walk()
        # A crash before the last save leaves the state at the first commit;
        # reprocessing both commits adds nothing.
        state = RouteSyncState.objects.get()
        state.last_commit_sha, state.last_commit_at = SHA_1030, AT_1030
        state.save()

        stats = self.walk()

        self.assertEqual(stats.versions_added, 0)
        self.assertEqual(stats.changes_added, 0)
        self.assertEqual(RouteStopList.objects.count(), 5)
        self.assertEqual(RouteStopChange.objects.count(), 3)

    def test_commits_at_or_before_the_state_are_never_fetched(self):
        fetched = []
        importer.import_route_history(
            ROUTE_COMMITS, lambda sha: fetched.append(sha) or ROUTE_PAYLOADS[sha]
        )
        fetched.clear()
        # A commit older than the state, as a re-listed history would repeat.
        old = ("aaaa000000000000000000000000000000000000", AT_1030 - dt.timedelta(minutes=5))
        stats = importer.import_route_history(
            [old] + ROUTE_COMMITS, lambda sha: fetched.append(sha) or ROUTE_PAYLOADS[sha]
        )

        self.assertEqual(stats.commits_seen, 0)
        self.assertEqual(fetched, [])

    def test_a_pure_reorder_is_a_change_with_no_adds_or_removes(self):
        first = [("c" * 40, AT_1030), ("d" * 40, AT_1035)]
        payloads = {
            first[0][0]: routes_payload(ROUTE_3_STOPS, ROUTE_19_STOPS),
            first[1][0]: routes_payload(list(reversed(ROUTE_3_STOPS)), ROUTE_19_STOPS),
        }

        stats = importer.import_route_history(first, lambda sha: payloads[sha])

        self.assertEqual(stats.changes_added, 1)
        change = RouteStopChange.objects.get(route_id=3, changed_at=AT_1035)
        self.assertEqual(change.added_stop_ids, [])
        self.assertEqual(change.removed_stop_ids, [])


class StopServingTests(TestCase):
    """Predictions only trust stops the route still serves (#57)."""

    def test_a_stop_the_route_no_longer_serves_gets_no_prediction(self):
        seed_history()  # arrivals for route 1, stop 100
        RouteStopList.objects.create(
            route_id=1,
            route_name="Test route",
            stop_ids=[200],  # stop 100 has been dropped
            first_seen=AT_1030,
            commit_sha=SHA_1030,
        )

        self.assertIsNone(
            predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12))
        )
        # A stop still on the list predicts as usual.
        ours = predictions.predict_minutes(1, 200, now=eastern(dt.date(2026, 11, 5), 12))
        self.assertIsNone(ours)  # stop 200 itself has no arrival history

    def test_a_currently_served_stop_predicts_as_before(self):
        seed_history()
        RouteStopList.objects.create(
            route_id=1,
            route_name="Test route",
            stop_ids=[100, 200],
            first_seen=AT_1030,
            commit_sha=SHA_1030,
        )

        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 7))
        self.assertIsNotNone(ours)
        self.assertGreaterEqual(ours, 4)
        self.assertLessEqual(ours, 8)

    def test_without_a_walk_everything_counts_as_served(self):
        seed_history()
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 7))
        self.assertIsNotNone(ours)

    def test_evaluation_skips_pairs_the_route_no_longer_serves(self):
        seed_history()
        RouteStopList.objects.create(
            route_id=1,
            route_name="Test route",
            stop_ids=[200],
            first_seen=AT_1030,
            commit_sha=SHA_1030,
        )

        result = predictions.evaluate(holdout_modulus=3, stride=1, now=eastern(dt.date(2026, 11, 10), 12))

        self.assertEqual(result["queries"], 0)
        self.assertGreater(result["not_served"], 0)
        self.assertIsNone(result["ours_mae"])


class RouteSourcesTests(TestCase):
    """The routes.json network reads, mocked like every other source."""

    def test_list_route_commits_paginates_and_sorts_oldest_first(self):
        # The API answers newest first, 100 per page; the last page is short.
        page_one = [
            {
                "sha": f"{i:040x}",
                "commit": {"author": {"date": f"2026-11-{13 - i // 50:02d}T00:00:00Z"}},
            }
            for i in range(100)
        ]
        page_two = [
            {"sha": "b" * 40, "commit": {"author": {"date": "2026-11-10T00:00:00Z"}}},
            {"sha": "a" * 40, "commit": {"author": {"date": "2026-11-09T00:00:00Z"}}},
        ]
        with mock.patch(
            "history.sources._text_stream",
            side_effect=[StringIO(json.dumps(page_one)), StringIO(json.dumps(page_two))],
        ):
            commits = sources.list_route_commits()

        self.assertEqual(len(commits), 102)
        self.assertEqual(commits[0], ("a" * 40, dt.datetime(2026, 11, 9, tzinfo=UTC)))
        dates = [committed_at for _, committed_at in commits]
        self.assertEqual(dates, sorted(dates))

    def test_read_routes_json_fetches_the_version_at_that_commit(self):
        payload = routes_payload(ROUTE_3_STOPS, ROUTE_19_STOPS)
        with mock.patch(
            "history.sources._text_stream", return_value=StringIO(json.dumps(payload))
        ) as stream:
            routes = sources.read_routes_json(SHA_1030)

        self.assertEqual(routes, payload)
        url = stream.call_args.args[0]
        # The historical version, not the branch tip: the sha is the ref.
        self.assertIn(f"/{SHA_1030}/data/routes.json", url)


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


# ----- Predictions (#23) -----
#
# The synthetic history covers six real days (Wed Nov 4 to Mon Nov 9, 2026,
# all EST, no DST in the way). Weekdays run every 12 minutes, weekends every
# 20. Whatever days evaluate() holds out get a different service pattern
# instead (a bus every minute, 7-8 am), so if any held-out day leaked into
# training, the predictions on it would collapse towards zero and the
# error assertions below would catch it.


def WEEK_DAYS():
    return [dt.date(2026, 11, day) for day in (4, 5, 6, 9)]


def WEEKEND_DAYS():
    return [dt.date(2026, 11, day) for day in (7, 8)]


def eastern(day, hour, minute=0):
    """A moment on the Eastern clock, as it is stored: UTC."""
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=ET).astimezone(UTC)


def add_arrivals(day, headway_minutes, first_hour, last_hour, route_id=1, stop_id=100):
    """eta_minutes=0 rows every `headway_minutes` from first_hour to last_hour ET.

    A different bus each time, like a real rotation: the same bus showing
    eta 0 again within five minutes would look like one bus dwelling, and
    be merged into a single arrival.
    """
    minute = first_hour * 60
    last_minute = last_hour * 60
    bus_number = 0
    while minute <= last_minute:
        moment = eastern(day, minute // 60, minute % 60)
        bus_number += 1
        poll = Poll.objects.create(polled_at=moment, status=Poll.Status.RECORDED)
        Eta.objects.create(
            poll=poll,
            polled_at=moment,
            stop_id=stop_id,
            stop_name="Test stop",
            route_id=route_id,
            route_name="Test route",
            bus_id=500 + bus_number,
            bus_name=f"#{500 + bus_number}",
            eta_minutes=0,
            calculation_time=moment,
        )
        minute += headway_minutes


def seed_history():
    for day in WEEK_DAYS():
        if day.toordinal() % 3 == 0:  # what evaluate() will hold out
            add_arrivals(day, headway_minutes=1, first_hour=7, last_hour=8)
        else:
            add_arrivals(day, headway_minutes=12, first_hour=7, last_hour=19)
    for day in WEEKEND_DAYS():
        add_arrivals(day, headway_minutes=20, first_hour=8, last_hour=22)


class PredictMinutesTests(TestCase):
    def test_predicts_the_wait_for_a_regular_headway(self):
        seed_history()
        # Noon on a regular weekday, buses every 12 minutes: walking up at a
        # random minute means waiting 0-12; the median is about 6.
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 7))
        self.assertIsNotNone(ours)
        self.assertGreaterEqual(ours, 4)
        self.assertLessEqual(ours, 8)

    def test_weekend_service_gets_its_own_prediction(self):
        seed_history()
        saturday = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 7), 10))
        self.assertIsNotNone(saturday)
        self.assertGreaterEqual(saturday, 8)  # a 20-minute headway, not 12
        self.assertLessEqual(saturday, 13)

    def test_no_history_means_no_prediction(self):
        self.assertIsNone(predictions.predict_minutes(1, 100))
        seed_history()
        self.assertIsNone(predictions.predict_minutes(1, 999))  # an unknown stop

    def test_only_history_before_now_is_used(self):
        seed_history()
        # Asking before any of it was recorded: nothing to learn from yet.
        before = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 2), 12))
        self.assertIsNone(before)

    def test_after_the_last_bus_predicts_nothing(self):
        seed_history()
        # 9 PM on a weekday, hours after the last recorded arrival: the
        # neighbouring buckets are all empty, so there is no prediction —
        # a route that isn't running has no wait to borrow from the whole
        # day. Hours *before* the first bus is different: the samples before
        # the day's first arrival are real, so 3 AM still predicts hours.
        self.assertIsNone(predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 21)))
        early = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 3))
        self.assertGreaterEqual(early, 120)


class ArrivalMomentTests(TestCase):
    def test_a_dwelling_bus_is_one_arrival(self):
        t = eastern(dt.date(2026, 11, 5), 10)
        rows = [(t, 1), (t + dt.timedelta(minutes=5), 1), (t + dt.timedelta(minutes=20), 1)]
        moments = predictions._arrival_moments(rows)
        self.assertEqual(moments, [t, t + dt.timedelta(minutes=20)])

    def test_two_buses_at_the_stop_are_one_moment(self):
        t = eastern(dt.date(2026, 11, 5), 10)
        moments = predictions._arrival_moments([(t, 1), (t, 2)])
        self.assertEqual(moments, [t])

    def test_day_types_and_buckets_follow_the_eastern_clock(self):
        # _day_type and _bucket take Eastern-aware moments, like _predict gives them.
        def et(day, hour, minute=0):
            return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=ET)

        self.assertEqual(predictions._day_type(et(dt.date(2026, 11, 7), 10)), "saturday")
        self.assertEqual(predictions._day_type(et(dt.date(2026, 11, 8), 10)), "sunday")
        self.assertEqual(predictions._day_type(et(dt.date(2026, 11, 5), 10)), "weekday")
        self.assertEqual(predictions._bucket(et(dt.date(2026, 11, 5), 0, 29)), 0)
        self.assertEqual(predictions._bucket(et(dt.date(2026, 11, 5), 0, 30)), 1)
        self.assertEqual(predictions._bucket(et(dt.date(2026, 11, 5), 12, 7)), 24)

    def test_held_out_days_are_deterministic(self):
        days = WEEK_DAYS() + WEEKEND_DAYS()
        held = predictions.held_out_days(set(days), 3)
        self.assertEqual(
            held, {day for day in days if day.toordinal() % 3 == 0}
        )
        self.assertTrue(held)  # the fixtures below depend on some being held out


class EvaluateTests(TestCase):
    def test_held_out_days_are_scored_without_leaking_into_training(self):
        seed_history()
        result = predictions.evaluate(holdout_modulus=3, stride=1, now=eastern(dt.date(2026, 11, 10), 12))

        self.assertTrue(result["test_days"])
        self.assertEqual(
            result["test_days"],
            sorted(predictions.held_out_days({dt.date(2026, 11, day) for day in range(4, 10)}, 3)),
        )
        self.assertGreater(result["queries"], 100)
        self.assertEqual(result["no_prediction"], 0)
        # The official ETA is exactly right in this synthetic history, but
        # our prediction is worse on held-out days only because it refused
        # to learn them (a bus every minute) — proof the holdout is honest.
        self.assertIsNotNone(result["official_mae"])
        self.assertLess(result["official_mae"], 0.5)
        self.assertIsNotNone(result["ours_mae"])
        self.assertGreater(result["ours_mae"], 3.5)
        self.assertLess(result["ours_mae"], 12)

    def test_an_empty_history_has_nothing_to_score(self):
        result = predictions.evaluate()
        self.assertEqual(result["queries"], 0)
        self.assertIsNone(result["ours_mae"])
        self.assertIsNone(result["official_mae"])


class EvaluatePredictionsCommandTests(TestCase):
    def test_it_prints_ours_next_to_the_official_eta(self):
        seed_history()
        out = StringIO()
        call_command("evaluate_predictions", "--stride", "1", stdout=out)
        printed = out.getvalue()
        self.assertIn("Held out", printed)
        self.assertIn("our prediction:", printed)
        self.assertIn("official ETA:", printed)

    def test_without_history_it_says_so(self):
        with self.assertRaisesMessage(CommandError, "import_history"):
            call_command("evaluate_predictions")
