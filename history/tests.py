"""Tests for the history import (#22) and our predictions (#23). Nothing
here reaches the network: the importer tests feed it parsed rows directly,
the prediction tests build synthetic history in the database and mock the
live feed they read, and the command tests mock history.sources."""

import datetime as dt
import json
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from history import importer, predictions, sources
from history.models import (
    Announcement,
    BusPosition,
    Calibration,
    Eta,
    Poll,
    RouteStatus,
    RouteStopChange,
    RouteStopList,
    RouteSyncState,
)
from transit import downtowner

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

    def test_every_routes_current_stops_in_one_query_agree(self):
        """The accuracy scoring reads every route's stop list at once (#17);
        it must pick the same version current_stops() picks, route by route."""
        self.assertEqual(predictions.current_stops_by_route(), {})  # nothing walked yet
        self.walk()
        # Two versions first seen at the same moment: the later row wins.
        for sha, stop_ids in (("e" * 40, [5, 6]), ("f" * 40, [5])):
            RouteStopList.objects.create(
                route_id=7, route_name="Orange", stop_ids=stop_ids, first_seen=AT_1035, commit_sha=sha
            )

        with self.assertNumQueries(1):
            current = predictions.current_stops_by_route()

        self.assertEqual(set(current), {3, 7, 19})
        for route_id in (3, 7, 19):
            self.assertEqual(current[route_id], predictions.current_stops(route_id))
        self.assertEqual(current[7], {5})

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


def add_eta_poll(moment, bus_id, eta_minutes, route_id=1, stop_id=100):
    """One bus's ETA at one stop in one poll, like the feed serves it."""
    poll, _ = Poll.objects.get_or_create(
        polled_at=moment, defaults={"status": Poll.Status.RECORDED}
    )
    Eta.objects.create(
        poll=poll,
        polled_at=moment,
        stop_id=stop_id,
        stop_name="Test stop",
        route_id=route_id,
        route_name="Test route",
        bus_id=bus_id,
        bus_name=f"#{bus_id}",
        eta_minutes=eta_minutes,
        calculation_time=moment,
    )


class LiveLayerTests(TestCase):
    """A bus counting down in the fresh polls is the prediction, and it
    ticks: the official app's whiplash ("20 minutes away", and 6 five
    minutes later) is what this layer exists to never do. Far out, where the
    countdown and the history agree the bus is half an hour out or more, the
    history answers instead (#154)."""

    def setUp(self):
        cache.clear()  # no countdown corrections another test left cached

    def tearDown(self):
        cache.clear()

    def test_a_counting_down_bus_is_the_prediction(self):
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12), bus_id=9, eta_minutes=7)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 2))
        self.assertEqual(ours, 5.0)  # the countdown run out: 12:07, asked at 12:02

    def test_the_countdown_ticks_between_polls(self):
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12), bus_id=9, eta_minutes=7)
        first = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 2))
        second = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 4))
        self.assertEqual(first, 5.0)
        self.assertEqual(second, 3.0)  # two minutes passed, the answer dropped two

    def test_the_soonest_of_several_buses_wins(self):
        noon = eastern(dt.date(2026, 11, 5), 12)
        add_eta_poll(noon, bus_id=9, eta_minutes=9)
        add_eta_poll(noon, bus_id=11, eta_minutes=4)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 2))
        self.assertEqual(ours, 2.0)

    def test_a_new_poll_can_raise_the_countdown(self):
        # The feed revises buses farther out too — 16% of poll pairs in the
        # Nov 17 audit — so the answer follows the freshest countdown in
        # either direction. Holding the earliest promise instead (the old
        # never-rise rule) told riders a bus was coming that then ran late
        # 56% of the time (#117), so it is gone.
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12), bus_id=9, eta_minutes=5)
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12, 5), bus_id=9, eta_minutes=12)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 6))
        self.assertEqual(ours, 11.0)  # 12:17 promised, asked at 12:06

    def test_an_arrival_that_comes_due_reads_zero_then_defers_to_history(self):
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12), bus_id=9, eta_minutes=3)
        due = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 3))
        gone = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 7))
        self.assertEqual(due, 0.0)  # arrived about now
        # Minutes later the expired countdown is gone; the answer is the
        # history layer's, which learned the arrival at 12:03 from that
        # same poll — not a frozen "0" held open forever.
        self.assertEqual(gone, 3.0)

    def test_a_stale_poll_falls_through_to_history(self):
        seed_history()
        # The 12:00 poll is 16 minutes old at 12:16 — too old to trust, so
        # the recorded history answers, not a frozen countdown.
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 16))
        self.assertIsNotNone(ours)
        self.assertGreaterEqual(ours, 4)
        self.assertLessEqual(ours, 8)

    def test_a_countdown_far_out_is_historys_where_history_agrees(self):
        # Both sources say the bus is half an hour out — the feed 33, the
        # usual wait for this hour 52.5 (buses at 16:35 and 18:05 on the
        # training days) — so the history answers (#154), the one far regime
        # the measured record has it ahead of the corrected countdown in.
        for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)):
            add_eta_poll(eastern(day, 16, 35), bus_id=31, eta_minutes=0)
            add_eta_poll(eastern(day, 18, 5), bus_id=32, eta_minutes=0)
        add_eta_poll(eastern(dt.date(2026, 11, 5), 17), bus_id=9, eta_minutes=40)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 17, 7))
        self.assertEqual(ours, 52.5)  # the history's, not the countdown's 33

    def test_a_countdown_a_minute_shy_of_far_out_stays_the_countdown(self):
        # 29 minutes is the countdown's land even against a usual wait of
        # 52.5: the switch lives at HISTORY_AT (#154), not near it.
        for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)):
            add_eta_poll(eastern(day, 16, 35), bus_id=31, eta_minutes=0)
            add_eta_poll(eastern(day, 18, 5), bus_id=32, eta_minutes=0)
        add_eta_poll(eastern(dt.date(2026, 11, 5), 17), bus_id=9, eta_minutes=36)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 17, 7))
        self.assertEqual(ours, 29.0)  # the countdown, seven minutes aged

    def test_a_far_out_countdown_stays_when_history_disagrees(self):
        # The feed promises a bus 33 minutes out; the route's usual wait at
        # that hour is 7. The disagreement keeps the countdown (#154): the
        # far promises history argues with are the ones the corrections fit
        # was measured on.
        for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)):
            add_arrivals(day, headway_minutes=12, first_hour=7, last_hour=19)
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12), bus_id=9, eta_minutes=40)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 7))
        self.assertEqual(ours, 33.0)

    def test_far_out_without_history_the_countdown_answers(self):
        # No arrivals to learn from: the history has nothing to agree with,
        # and a countdown is still better than nothing (#154).
        add_eta_poll(eastern(dt.date(2026, 11, 5), 17), bus_id=9, eta_minutes=40)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 17, 7))
        self.assertEqual(ours, 33.0)

    def test_nothing_after_now_is_used(self):
        add_eta_poll(eastern(dt.date(2026, 11, 5), 12, 10), bus_id=9, eta_minutes=7)
        self.assertIsNone(
            predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12, 2))
        )


class FeedLiveLayerTests(TestCase):
    """The feed itself (transit.etas, cached 30 seconds) is the live layer's
    first source for calls about right now. Its outage and its silences fall
    through to the recorded polls and then history, never to a crash — and a
    simulated `now` never reads it, because the feed's countdown is about
    the present."""

    def test_the_feed_answers_calls_about_right_now(self):
        feed = [
            {"route_id": 2, "bus_id": 5, "bus_name": "#5", "minutes": 9},
            {"route_id": 1, "bus_id": 4, "bus_name": "#4", "minutes": -2},
            {"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": 4},
            {"route_id": 1, "bus_id": 8, "bus_name": "#8", "minutes": 11},
        ]
        with mock.patch("transit.downtowner.etas", return_value=feed) as etas:
            ours = predictions.predict_minutes(1, 100)
        self.assertEqual(ours, 4.0)  # our route's soonest bus; the other
        # route's 9 and our own negative, already-arrived listing don't win
        etas.assert_called_once_with(100)

    def test_a_route_absent_from_the_feed_uses_the_recorded_polls(self):
        add_eta_poll(timezone.now() - dt.timedelta(minutes=2), bus_id=9, eta_minutes=7)
        feed = [{"route_id": 2, "bus_id": 5, "bus_name": "#5", "minutes": 3}]
        with mock.patch("transit.downtowner.etas", return_value=feed):
            ours = predictions.predict_minutes(1, 100)
        self.assertEqual(ours, 5.0)  # the recorded countdown, aged two minutes

    def test_a_feed_outage_falls_through_to_the_recorded_polls(self):
        add_eta_poll(timezone.now() - dt.timedelta(minutes=2), bus_id=9, eta_minutes=7)
        outage = downtowner.FeedUnavailable("The Downtowner feed is unavailable")
        with mock.patch("transit.downtowner.etas", side_effect=outage):
            ours = predictions.predict_minutes(1, 100)
        self.assertEqual(ours, 5.0)

    def test_a_simulated_now_never_reads_the_feed(self):
        with mock.patch("transit.downtowner.etas") as etas:
            ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 5), 12))
        etas.assert_not_called()
        self.assertIsNone(ours)  # no recorded polls either: nothing to say

    def test_a_far_off_feed_countdown_is_historys_where_history_agrees(self):
        # The live layer's first source follows the same rule (#154): the
        # feed counts a bus 33 minutes out, the usual wait for the hour is
        # 52.5, and the history's answer wins the far-out agreement.
        for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)):
            add_eta_poll(eastern(day, 16, 35), bus_id=31, eta_minutes=0)
            add_eta_poll(eastern(day, 18, 5), bus_id=32, eta_minutes=0)
        feed = [{"route_id": 1, "bus_id": 4, "bus_name": "#4", "minutes": 33}]
        with mock.patch.object(
            predictions.timezone, "now", return_value=eastern(dt.date(2026, 11, 5), 17, 7)
        ), mock.patch("transit.downtowner.etas", return_value=feed):
            ours = predictions.predict_minutes(1, 100)
        self.assertEqual(ours, 52.5)


class CalibrationTests(TestCase):
    """The countdown corrections (#117): how the fit builds its ladder, what
    the stored rows make predict_minutes answer, and the nightly command
    that writes them. With nothing stored — a fresh database, or the job
    yet to run — the raw countdown answers uncorrected."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()  # the stored fit must never leak into other tests

    def test_countdown_buckets_slice_by_size(self):
        self.assertEqual(predictions.countdown_bucket(0), 0)
        self.assertEqual(predictions.countdown_bucket(4.9), 0)
        self.assertEqual(predictions.countdown_bucket(5), 1)
        self.assertEqual(predictions.countdown_bucket(19.5), 3)
        self.assertEqual(predictions.countdown_bucket(20), 4)
        self.assertEqual(predictions.countdown_bucket(45), 5)

    def test_the_fit_defers_thin_routes_to_the_bucket_median(self):
        # Route 1's bucket earned MIN_CALIBRATION_SAMPLES residuals, route
        # 7's did not: the route row keeps the taught median, while route 7's
        # residuals pool into the all-routes row for the bucket and drag it.
        residuals = {
            (1, 1): [2.0] * 10 + [3.0] * 10,
            (7, 1): [-1.0] * 19,  # thin: under MIN_CALIBRATION_SAMPLES
        }
        fit = predictions._fit_corrections(residuals)
        self.assertEqual(fit.by_route, {(1, 1): 2.5})
        self.assertEqual(fit.by_bucket, {1: 2.0})
        self.assertEqual(fit.overall, 2.0)
        # An empty fit — no residuals at all — corrects nothing.
        empty = predictions._fit_corrections({})
        self.assertEqual(empty.by_route, {})
        self.assertEqual(empty.by_bucket, {})
        self.assertEqual(empty.overall, 0.0)

    def test_stored_rows_build_the_ladder_and_an_empty_table_answers_raw(self):
        Calibration.objects.create(route_id=1, bucket=1, delta_minutes=2.0, samples=30)
        Calibration.objects.create(route_id=None, bucket=2, delta_minutes=1.5, samples=40)
        Calibration.objects.create(
            route_id=None, bucket=predictions.OVERALL_BUCKET, delta_minutes=0.5, samples=70
        )
        fit = predictions.stored_corrections()
        self.assertEqual(fit.by_route, {(1, 1): 2.0})
        self.assertEqual(fit.by_bucket, {2: 1.5})
        self.assertEqual(fit.overall, 0.5)
        Calibration.objects.all().delete()
        cache.clear()  # the cached fit would otherwise outlive its table
        empty = predictions.stored_corrections()
        self.assertEqual(empty.by_route, {})
        self.assertEqual(empty.by_bucket, {})
        self.assertEqual(empty.overall, 0.0)

    def test_a_small_countdown_answers_raw_even_where_the_fit_speaks(self):
        # The audit measured the imminent regime as near-perfect raw and a
        # learned correction there as pure damage (#117), so RAW_MAX
        # overrules every rung of the ladder.
        corrections = predictions.Corrections(by_route={(1, 0): -3.0}, by_bucket={0: -3.0}, overall=-3.0)
        self.assertEqual(predictions._fused_countdown(1, 4.0, corrections), 4.0)

    def test_a_larger_countdown_gets_its_correction_and_never_lands_below_zero(self):
        corrections = predictions.Corrections(by_route={(1, 1): 3.0}, by_bucket={1: 1.0}, overall=0.5)
        self.assertEqual(predictions._fused_countdown(1, 8.0, corrections), 11.0)  # the route row
        self.assertEqual(predictions._fused_countdown(7, 8.0, corrections), 9.0)  # the bucket row
        self.assertEqual(predictions._fused_countdown(7, 12.0, corrections), 12.5)  # the overall
        # A correction that would promise a negative wait reads "boarding now".
        spent = predictions.Corrections(by_route={}, by_bucket={1: -10.0}, overall=0.0)
        self.assertEqual(predictions._fused_countdown(1, 8.0, spent), 0.0)
        self.assertIsNone(predictions._fused_countdown(1, None, corrections))

    def test_loaded_corrections_correct_a_countdown_as_predict_minutes_does(self):
        # The trip watch loads them once and corrects the feed's countdown itself (#153).
        corrections = predictions.Corrections(by_route={(1, 1): 3.0}, by_bucket={1: 1.0}, overall=0.5)
        for countdown in (4.0, 8.0, 40.0, None):
            self.assertEqual(
                corrections.correct(1, countdown), predictions._fused_countdown(1, countdown, corrections)
            )
        self.assertEqual(corrections.correct(1, 8.0), 11.0)

    def test_cushions_keep_the_target_share_of_riders_on_time(self):
        # #155. Twenty residuals of -10 to +9 minutes for route 1's 10-15
        # minute countdowns: their median, -0.5, corrects them, which leaves
        # errors of -9.5 to +9.5. Leaving 6.5 minutes earlier keeps 18 of 20
        # within the 2-minute slack, 90%; in whole minutes that's 7, and 95%.
        residuals = {(1, 2): [float(value) for value in range(-10, 10)]}
        corrections = predictions._fit_corrections(residuals)
        self.assertEqual(corrections.by_route[(1, 2)], -0.5)
        self.assertEqual(corrections.cushion_by_route[(1, 2)], 6.5)
        self.assertEqual(corrections.cushion(1, 12), 7)
        errors = [-0.5 - value for value in residuals[(1, 2)]]
        self.assertEqual(sum(error - 7 <= predictions.ON_TIME_SLACK for error in errors) / len(errors), 0.95)

    def test_a_raw_countdowns_cushion_ignores_the_delta_and_a_thin_route_takes_the_buckets(self):
        # Below 5 minutes the countdown answers raw: a bus 3 minutes early
        # needs 1 minute to spare, beyond the slack. Route 2 is too thin for
        # its own rung and takes the bucket's: 8 minutes early at its 90th
        # percentile, over both routes, less the slack.
        corrections = predictions._fit_corrections({(1, 0): [-3.0] * 25, (2, 0): [-8.0] * 5})
        self.assertEqual(corrections.cushion(1, 3), 1)
        self.assertNotIn((2, 0), corrections.cushion_by_route)
        self.assertEqual(corrections.cushion(2, 3), 6)

    def test_no_fit_and_rows_from_before_the_cushions_leave_nothing_or_fall_back(self):
        self.assertEqual(predictions.Corrections(by_route={}, by_bucket={}, overall=0.0).cushion(1, 12), 0)
        cache.clear()
        Calibration.objects.create(route_id=1, bucket=2, delta_minutes=1.0, samples=25)  # fitted before #155
        Calibration.objects.create(route_id=None, bucket=2, delta_minutes=1.0, samples=40, cushion_minutes=2.2)
        corrections = predictions.stored_corrections()
        self.assertEqual(corrections.delta(1, 12), 1.0)
        self.assertEqual(corrections.cushion(1, 12), 3)  # route 1's row has none: the bucket's, rounded up

    def test_arrival_cushions_keep_there_by_on_time(self):
        # #174, the other tail of #155's example: corrected by their median,
        # -0.5, the twenty residuals leave buses up to 9.5 minutes later than
        # the corrected time. Adding 6.5 minutes keeps 18 of 20 within the
        # 2-minute slack, 90%; in whole minutes that's 7, and 95%.
        residuals = {(1, 2): [float(value) for value in range(-10, 10)]}
        corrections = predictions._fit_corrections(residuals, own_bus=residuals)
        self.assertEqual(corrections.late_by_route[(1, 2)], 6.5)
        self.assertEqual(corrections.late(1, 12), 7)
        later = [value + 0.5 for value in residuals[(1, 2)]]
        self.assertEqual(sum(minutes - 7 <= predictions.ON_TIME_SLACK for minutes in later) / len(later), 0.95)

    def test_a_raw_countdowns_arrival_cushion_and_a_thin_routes_take_the_buckets(self):
        # Below 5 minutes the countdown answers raw: a bus 3 minutes later
        # than it said needs 1 minute added, beyond the slack. Route 2 is too
        # thin for its own rung: the bucket's 90th percentile, 8, less the slack.
        own_bus = {(1, 0): [3.0] * 25, (2, 0): [8.0] * 5}
        corrections = predictions._fit_corrections(own_bus, own_bus=own_bus)
        self.assertEqual(corrections.late(1, 3), 1)
        self.assertNotIn((2, 0), corrections.late_by_route)
        self.assertEqual(corrections.late(2, 3), 6)

    def test_each_bus_is_measured_against_its_own_arrival(self):
        # #174. Bus 9 says 12 minutes at noon and reaches the stop at 12:14:
        # 2 minutes late. Bus 8 says 6 minutes at noon, and the polls missed
        # its arrival (no countdown of 5 or less): its next one, a loop later,
        # would read as 35 minutes late, so it teaches nothing. The correction
        # fit's residuals, the stop's next arrival whichever bus it is, would
        # have read bus 8's 6 minutes against bus 9's arrival: 8 minutes late.
        add_eta_poll(eastern(DAY, 12), bus_id=9, eta_minutes=12)
        add_eta_poll(eastern(DAY, 12, 10), bus_id=9, eta_minutes=4)  # there at 12:14
        add_eta_poll(eastern(DAY, 12), bus_id=8, eta_minutes=6)
        add_eta_poll(eastern(DAY, 12, 40), bus_id=8, eta_minutes=1)  # its next loop, at 12:41
        own = predictions._own_bus_residuals(eastern(DAY, 0), eastern(DAY, 23))
        self.assertEqual(own[(1, 2)], [2.0])  # 12 minutes, the 10-15 bucket: bus 9, 2 minutes late
        self.assertEqual(own[(1, 0)], [0.0, 0.0])  # each bus's last countdown, run out
        self.assertNotIn((1, 1), own)  # bus 8's 6 minutes taught nothing

    def test_rows_from_before_the_arrival_cushions_fall_back(self):
        self.assertEqual(predictions.Corrections(by_route={}, by_bucket={}, overall=0.0).late(1, 12), 0)
        cache.clear()
        Calibration.objects.create(route_id=1, bucket=2, delta_minutes=1.0, samples=25, cushion_minutes=1.0)  # before #174
        Calibration.objects.create(
            route_id=None, bucket=2, delta_minutes=1.0, samples=40, cushion_minutes=2.2, arrival_cushion_minutes=3.1
        )
        self.assertEqual(predictions.stored_corrections().late(1, 12), 4)  # the bucket's, rounded up
        # Rows cached before #174 deployed, four values each, still load.
        cache.set("history.calibration", [(1, 2, 1.0, 1.0)], 600)
        corrections = predictions.stored_corrections()
        self.assertEqual((corrections.delta(1, 12), corrections.cushion(1, 12), corrections.late(1, 12)), (1.0, 1, 0))

    def test_the_stored_fit_corrects_predict_minutes_until_nothing_is_stored(self):
        add_eta_poll(eastern(DAY, 12), bus_id=9, eta_minutes=8)
        Calibration.objects.create(route_id=None, bucket=1, delta_minutes=3.0, samples=25)
        self.assertEqual(predictions.predict_minutes(1, 100, now=eastern(DAY, 12, 2)), 9.0)
        Calibration.objects.all().delete()
        cache.clear()
        # Nothing stored: the raw countdown answers, so a fresh deploy
        # degrades safely while the nightly job has yet to run.
        self.assertEqual(predictions.predict_minutes(1, 100, now=eastern(DAY, 12, 2)), 6.0)

    def test_the_fit_command_stores_the_ladder_and_is_safe_to_run_twice(self):
        for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)):
            for hour in (8, 9, 10, 11, 12, 13, 14):  # seven approaches a day
                add_eta_poll(eastern(day, hour), bus_id=9, eta_minutes=12)
                add_eta_poll(eastern(day, hour, 9), bus_id=9, eta_minutes=4)  # arrives :13
        stdout = StringIO()
        call_command("fit_calibration", stdout=stdout)
        rows = {(row.route_id, row.bucket): row.delta_minutes for row in Calibration.objects.all()}
        self.assertEqual(
            rows,
            {
                (1, 2): 1.0,  # 21 taught residuals, all +1: the countdown ran long
                (1, 0): 0.0,  # the arrival-eta countdowns were spot on
                (None, 2): 1.0,
                (None, 0): 0.0,
                (None, predictions.OVERALL_BUCKET): 0.5,  # the mixed overall median
            },
        )
        self.assertIn("42 residuals", stdout.getvalue())
        # Every bus came when its corrected countdown said: nothing to spare (#155).
        self.assertEqual({row.cushion_minutes for row in Calibration.objects.all()}, {0.0})
        # Nor later: no arrival cushion either (#174).
        self.assertEqual({row.arrival_cushion_minutes for row in Calibration.objects.all()}, {0.0})
        self.assertIn("overall arrival cushion 0.0 min", stdout.getvalue())
        call_command("fit_calibration", stdout=StringIO())
        self.assertEqual(Calibration.objects.count(), 5)  # a rerun replaces, never duplicates

    def test_the_fit_command_on_an_empty_history_stores_nothing(self):
        stdout = StringIO()
        call_command("fit_calibration", stdout=stdout)
        self.assertEqual(Calibration.objects.count(), 0)

    def test_the_fit_command_drops_the_stored_corrections_cache(self):
        # A page read warms the 10-minute corrections cache; the nightly fit
        # must be answered at once, not when the cache happens to expire.
        add_eta_poll(eastern(dt.date(2026, 11, 3), 12), bus_id=9, eta_minutes=8)
        self.assertEqual(predictions.stored_corrections().overall, 0.0)  # the cache is warm
        self.assertIsNotNone(cache.get("history.calibration"))

        call_command("fit_calibration", stdout=StringIO())

        self.assertIsNone(cache.get("history.calibration"))


class ConditionedHistoryTests(TestCase):
    """When no poll is fresh but a bus did come recently, the wait shrinks
    with the elapsed time — the history layer's own countdown."""

    def test_the_wait_shrinks_with_elapsed_time(self):
        seed_history()
        # Tuesday Nov 10 is not a seeded day: its only arrival is the one
        # we add, so the live layer goes quiet minutes later and the
        # conditioned history answers. Weekday headways are 12 minutes,
        # so 7 minutes after a bus the truth is 5 — and the interpolation
        # between the sampled elapsed points recovers exactly that.
        add_eta_poll(eastern(dt.date(2026, 11, 10), 12), bus_id=9, eta_minutes=0)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 10), 12, 7))
        self.assertEqual(ours, 5.0)

    def test_each_day_type_conditions_on_its_own_service(self):
        seed_history()
        # Sunday headways are 20 minutes: 7 minutes after the bus, 13 remain.
        add_eta_poll(eastern(dt.date(2026, 11, 15), 12), bus_id=9, eta_minutes=0)
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 15), 12, 7))
        self.assertEqual(ours, 13.0)

    def test_an_arrival_hours_old_defers_to_the_buckets(self):
        seed_history()
        add_eta_poll(eastern(dt.date(2026, 11, 10), 12), bus_id=9, eta_minutes=0)
        # An hour later, unseen buses may have come and gone: conditioning
        # on a stale elapsed time would be a lie, so the walk-up median —
        # about 6 on this schedule — answers instead.
        ours = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 10), 13, 1))
        self.assertGreaterEqual(ours, 4)
        self.assertLessEqual(ours, 8)


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
        # 10:07, not 10:00: at 10:00 a bus is due that minute, and the live
        # layer rightly says so (see LiveLayerTests); 10:07 asks the
        # history layer about Saturday service.
        saturday = predictions.predict_minutes(1, 100, now=eastern(dt.date(2026, 11, 7), 10, 7))
        self.assertIsNotNone(saturday)
        self.assertGreaterEqual(saturday, 8)  # a 20-minute headway, not 12
        self.assertLessEqual(saturday, 13)

    def test_no_history_means_no_prediction(self):
        # The feed has nothing for the route either, so every layer is out
        # of data.
        with mock.patch("transit.downtowner.etas", return_value=[]):
            self.assertIsNone(predictions.predict_minutes(1, 100))
        seed_history()
        with mock.patch("transit.downtowner.etas", return_value=[]):
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



class HistoryPredictorTests(TestCase):
    """The trip watch's predictions (#29): the history layer, loaded once."""

    def test_it_answers_as_predict_minutes_does_when_nothing_live_is_fresh(self):
        seed_history()
        # Tuesday Nov 10 has no polls of its own, so nothing live is fresh
        # and predict_minutes answers from history, as the predictor does.
        tuesday = dt.date(2026, 11, 10)
        predictor = predictions.HistoryPredictor({(1, 100)}, now=eastern(tuesday, 7))
        for hour, minute in ((7, 0), (9, 7), (12, 7), (18, 30), (21, 0)):
            moment = eastern(tuesday, hour, minute)
            with self.subTest(at=f"{hour}:{minute:02}"):
                self.assertEqual(
                    predictor.minutes(1, 100, moment),
                    predictions.predict_minutes(1, 100, now=moment, use_live_feed=False),
                )

    def test_two_queries_to_load_and_none_to_answer(self):
        seed_history()
        tuesday_noon = eastern(dt.date(2026, 11, 10), 12, 7)
        with self.assertNumQueries(2):
            predictor = predictions.HistoryPredictor({(1, 100), (1, 999)}, now=eastern(dt.date(2026, 11, 10), 7))
        with self.assertNumQueries(0):
            self.assertIsNotNone(predictor.minutes(1, 100, tuesday_noon))
            self.assertIsNone(predictor.minutes(1, 999, tuesday_noon))  # no history there
            self.assertIsNone(predictor.minutes(2, 100, tuesday_noon))  # never loaded


class ArrivalEventTests(TestCase):
    def test_a_dwelling_bus_is_one_arrival(self):
        t = eastern(dt.date(2026, 11, 5), 10)
        rows = [
            (t, 1, 0),
            (t + dt.timedelta(minutes=5), 1, 0),
            (t + dt.timedelta(minutes=20), 1, 0),
        ]
        events = predictions._arrival_events(rows)
        self.assertEqual(events, [t, t + dt.timedelta(minutes=20)])

    def test_two_buses_at_the_stop_are_one_moment(self):
        t = eastern(dt.date(2026, 11, 5), 10)
        events = predictions._arrival_events([(t, 1, 0), (t, 2, 0)])
        self.assertEqual(events, [t])

    def test_an_approach_caught_between_polls_is_extrapolated(self):
        # The recorder polls every five minutes: the countdown reaches 4 and
        # the bus is gone by the next poll. The arrival is the countdown run
        # out, not a poll that landed on it.
        t = eastern(dt.date(2026, 11, 5), 10)
        rows = [
            (t - dt.timedelta(minutes=20), 7, 30),
            (t - dt.timedelta(minutes=15), 7, 25),
            (t, 7, 4),
        ]
        events = predictions._arrival_events(rows)
        self.assertEqual(events, [t + dt.timedelta(minutes=4)])

    def test_a_wrap_to_the_next_lap_starts_a_new_approach(self):
        t = eastern(dt.date(2026, 11, 5), 10)
        rows = [
            (t, 7, 1),  # arrives about a minute after this poll
            (t + dt.timedelta(minutes=5), 7, 52),  # next lap, from the top
            (t + dt.timedelta(minutes=40), 7, 3),  # back again
        ]
        events = predictions._arrival_events(rows)
        self.assertEqual(events, [t + dt.timedelta(minutes=1), t + dt.timedelta(minutes=43)])

    def test_a_countdown_that_merely_stops_is_no_arrival(self):
        # A bus taken off the feed mid-approach never reached the stop.
        t = eastern(dt.date(2026, 11, 5), 10)
        rows = [
            (t - dt.timedelta(minutes=10), 7, 30),
            (t - dt.timedelta(minutes=5), 7, 25),
        ]
        self.assertEqual(predictions._arrival_events(rows), [])

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
        # The official ETA is nearly right in this synthetic history — the
        # query stands QUERY_OFFSET past its poll, so an eta of 0 is already
        # a minute or so stale. And the live layer reads the held-out day's
        # own polls, the way production would, so it scores nearly perfect
        # here. The holdout is proven honest by the history layer alone: it
        # is worse only because it refused to learn the held-out days (a bus
        # every minute); if they leaked into training, it would collapse
        # towards zero too.
        self.assertIsNotNone(result["official_mae"])
        self.assertLess(result["official_mae"], 2.0)
        self.assertIsNotNone(result["ours_mae"])
        self.assertIsNotNone(result["history_mae"])
        # On held-out weekdays the conditioned history reads the median
        # residual at elapsed 0 — a 12-minute headway — against an actual
        # of 0 (a bus arrives as the rider opens the app). Had the held-out
        # days leaked into training, it would collapse towards zero.
        self.assertGreater(result["history_mae"], 3.5)
        self.assertLess(result["history_mae"], 12.5)

    def test_the_official_eta_is_scored_as_riders_see_it(self):
        # A revision that separates the two official scores (#116): the bus
        # is promised 25 minutes out at 11:55, but by 12:00 the feed says 15,
        # and the bus does come at 12:15. A rider asking at 12:02 sees the
        # fresher countdown (13 minutes, spot on) — the aged scoring kept
        # quoting the stale promise from 11:55 (18 minutes, 5 off).
        day = next(
            day
            for day in (dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 11, 11))
            if day.toordinal() % 3 == 0  # what evaluate() will hold out
        )
        for hour, minute, eta in [(11, 55, 25), (12, 0, 15), (12, 10, 5)]:
            add_eta_poll(eastern(day, hour, minute), bus_id=9, eta_minutes=eta)
        result = predictions.evaluate(stride=2, now=eastern(day, 23))

        self.assertEqual(result["queries"], 1)  # the 12:17 ask had no next bus
        self.assertEqual(result["official_mae"], 0.0)
        self.assertEqual(result["official_aged_mae"], 5.0)
        # Ours read the revision too (the freshest poll's countdown aged to
        # the ask), and history had no train day to learn from.
        self.assertEqual(result["ours_mae"], 0.0)
        self.assertIsNone(result["history_mae"])
        # The breakdown (#154): the ask at 12:02 saw a countdown of 13, so
        # this is the whole "10-15" bucket, and buckets without a query don't
        # appear at all.
        self.assertEqual(
            result["by_countdown"]["10-15"], {"n": 1, "ours": 0.0, "history": None, "official": 0.0}
        )
        self.assertEqual(list(result["by_countdown"]), ["10-15"])

    def test_the_breakdown_buckets_by_the_countdown_riders_see(self):
        # A bus promised 40 minutes out at 11:59, arriving 12:39 (the 12:36
        # poll's eta 3 is the approach's end): the ask at 12:06 sees a
        # countdown of 33 — the "30+" bucket, not the poll's 40 — and both
        # countdown scorers are spot on there.
        day = next(
            day
            for day in (dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 11, 11))
            if day.toordinal() % 3 == 0  # what evaluate() will hold out
        )
        add_eta_poll(eastern(day, 11, 59), bus_id=9, eta_minutes=40)
        add_eta_poll(eastern(day, 12, 36), bus_id=9, eta_minutes=3)
        result = predictions.evaluate(stride=1, now=eastern(day, 23))

        self.assertEqual(result["queries"], 1)
        self.assertEqual(
            result["by_countdown"]["30+"], {"n": 1, "ours": 0.0, "history": None, "official": 0.0}
        )
        self.assertNotIn("0-5", result["by_countdown"])

    def test_the_breakdown_scores_the_far_out_agreement_as_history(self):
        # The far-out agreement (#154): the held-out ask at 17:07 sees a
        # countdown of 33 and the training days teach a 52.5-minute usual
        # wait, so ours is the history's answer there — scored in the "30+"
        # bucket the rider saw, next to the official's countdown, spot on.
        held_out = next(
            day
            for day in (dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 11, 11))
            if day.toordinal() % 3 == 0  # what evaluate() will hold out
        )
        for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 4), dt.date(2026, 11, 5)):
            # All three sit on the training side of the split.
            self.assertNotEqual(day.toordinal() % 3, held_out.toordinal() % 3)
            add_eta_poll(eastern(day, 16, 35), bus_id=31, eta_minutes=0)
            add_eta_poll(eastern(day, 18, 5), bus_id=32, eta_minutes=0)
        add_eta_poll(eastern(held_out, 17), bus_id=9, eta_minutes=40)
        add_eta_poll(eastern(held_out, 17, 40), bus_id=9, eta_minutes=0)
        result = predictions.evaluate(stride=1, now=eastern(held_out, 23))

        self.assertEqual(result["queries"], 1)  # the 17:47 ask had no next bus in reach
        self.assertEqual(
            result["by_countdown"]["30+"],
            {"n": 1, "ours": 19.5, "history": 19.5, "official": 0.0},
        )
        self.assertNotIn("0-5", result["by_countdown"])

    def test_ours_is_the_countdown_the_fit_corrects(self):
        # The training days teach one correction: a countdown of 8-12 (any
        # poll at :00 says 8-12; the bus reliably comes 13 past) ran one
        # minute long, so bucket 10-15 earns +1.0. The held-out day's query
        # reads a countdown of 13 — the fit, and only the fit, adds that
        # minute to ours; the official shows its raw countdown.
        held_out = next(
            day
            for day in (dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 11, 11))
            if day.toordinal() % 3 == 0  # what evaluate() will hold out
        )
        train_days = [
            day
            for day in (dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4), dt.date(2026, 11, 5))
            if day.toordinal() % 3 != 0  # the training side of the split
        ]
        # Nov 3 shares the held-out day's residue, so three days survive —
        # 15 taught residuals, under the 20 a route row needs, which is what
        # sends the fit down to the all-routes bucket median.
        self.assertEqual(len(train_days), 3)
        for day in train_days:
            for hour in (8, 9, 10, 11, 13):  # five taught approaches a day
                add_eta_poll(eastern(day, hour), bus_id=9, eta_minutes=12)
                add_eta_poll(eastern(day, hour, 9), bus_id=9, eta_minutes=4)  # arrives :13
        for hour, minute, eta in [(11, 55, 25), (12, 0, 15), (12, 10, 5)]:
            add_eta_poll(eastern(held_out, hour, minute), bus_id=9, eta_minutes=eta)
        result = predictions.evaluate(stride=2, now=eastern(held_out, 23))

        self.assertEqual(result["queries"], 1)  # the 12:17 ask had no next bus
        self.assertEqual(result["ours_mae"], 1.0)  # 13 read, 14 answered, 13 true
        self.assertEqual(result["official_mae"], 0.0)  # the raw countdown was right
        self.assertEqual(result["official_aged_mae"], 5.0)

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
