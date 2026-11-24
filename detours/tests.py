"""Detour detection (#55). The Downtowner feed is mocked at
transit.downtowner.urlopen, answering with fixture routes and buses, so no
test reaches the network, and the streaks are driven with hand-built samples
in record_sample's shape — pure for the rule itself, database rows for the
storage."""

import datetime as dt
import io
import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert
from detours import demo, detect, jobs
from detours.models import Sample, UsualOffPath
from history.models import BusPosition, Poll
from transit import downtowner

ET = ZoneInfo("America/New_York")

# A straight north-south path, the way the feed's routes answer carries one:
# a flat [lat, lon, lat, lon, ...] list. 41.305 to 41.315 is about 1.1 km.
PATH = [41.305, -72.94, 41.315, -72.94]
# About 500 m west of that path, clearly farther than OFF_PATH_METERS.
OFF_LON = -72.934

ROUTES = [
    {
        "id": 1,
        "name": "Blue - Weekday Daytime",
        "short_name": "BD",
        "color": "4472C4",
        "active": True,
        "stops": [10],
        "path": PATH,
    },
    {
        # No official path to leave: never flagged, however far it strays.
        "id": 2,
        "name": "Orange",
        "short_name": "O",
        "color": "F5A623",
        "active": True,
        "stops": [10],
        "path": [],
    },
    {
        # Not running: never flagged either.
        "id": 3,
        "name": "Red - Weekend",
        "short_name": "R",
        "color": "C00000",
        "active": False,
        "stops": [10],
        "path": PATH,
    },
]

STOPS = [{"id": 10, "name": "Science Hill", "lat": 41.31, "lon": -72.92, "description": ""}]

ACTIVE = {route["id"]: route for route in ROUTES if route["active"]}


def feed_answer(data):
    """What urlopen returns for one of the feed's JSON answers."""
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(data).encode()
    return response


def feed_by_path(answers):
    """A urlopen stand-in that answers each feed endpoint."""
    def urlopen(request, timeout):
        return feed_answer(answers[urlsplit(request.full_url).path])

    return urlopen


def bus_at(route_id, lon, updated_at, bus_id=812):
    """One bus's live position, the way the feed's buses answer carries it
    (lastUpdate is a Unix time in seconds)."""
    return {
        "id": bus_id,
        "name": str(bus_id),
        "route": route_id,
        "lat": 41.31,
        "lon": lon,
        "lastUpdate": updated_at.timestamp(),
    }


def samples_of(runs, route_id=1):
    """Hand-built samples in record_sample's shape, oldest first (the order
    the cache keeps them in): `runs` maps bus id to (minutes_before_now, off)
    pairs, with off=None when the bus is missing from that sample. Returns
    (samples, newest sample time)."""
    now = timezone.now()
    minutes = sorted({minute for run in runs.values() for minute, _ in run}, reverse=True)
    samples = []
    for minute in minutes:
        routes = {}
        for bus_id, run in runs.items():
            for m, off in run:
                if m == minute and off is not None:
                    routes.setdefault(route_id, {})[bus_id] = off
        samples.append(
            {"at": (now - dt.timedelta(minutes=minute)).timestamp(), "routes": routes}
        )
    newest = dt.datetime.fromtimestamp(samples[-1]["at"], tz=dt.timezone.utc)
    return samples, newest


def prime_a_streak(runs, route_id=1):
    """Record a streak as Sample rows, the way page loads over the last hour
    would have. The routes dicts go through the JSON column, so the ids come
    back as strings exactly as they will in production. Returns the newest
    sample's time."""
    samples, newest = samples_of(runs, route_id=route_id)
    for sample in samples:
        Sample.objects.create(
            at=dt.datetime.fromtimestamp(sample["at"], tz=dt.timezone.utc),
            off_by_bus=sample["routes"],
        )
    return newest


class FlagsFromSamplesTests(TestCase):
    """flags_from_samples, the pure rule: a route is flagged when one of its
    buses was on the official path in the horizon and then off it for
    CONSECUTIVE_POLLS or more appearances in a row spanning at least
    MIN_SPAN, and stays flagged for as long as it stays off (#55).
    Deliberately hard to flag: a bus straight from the garage, a bus back on
    the path, a sample the bus missed, an off-run too short to be more than
    a guess — each blocks the flag."""

    def flags(self, runs, route_id=1):
        samples, _ = samples_of(runs, route_id=route_id)
        return detect.flags_from_samples(samples, ACTIVE, timezone.now())

    def test_on_the_path_then_off_three_in_a_row_flags_the_route(self):
        samples, newest = samples_of({812: [(14, False), (10, True), (5, True), (0, True)]})

        self.assertEqual(
            detect.flags_from_samples(samples, ACTIVE, timezone.now()),
            [
                {
                    "route_id": 1,
                    "route_name": "Blue - Weekday Daytime",
                    "bus_count": 1,
                    "since": newest,
                }
            ],
        )

    def test_an_off_run_shorter_than_min_span_does_not_flag_yet(self):
        # On the path 6 minutes ago and off ever since: three appearances in a
        # row, but bunched into 4 minutes (Austin, #55 review). A run that
        # short is a guess, so it keeps quiet until it spans MIN_SPAN.
        self.assertEqual(self.flags({812: [(6, False), (4, True), (2, True), (0, True)]}), [])

    def test_an_off_run_spanning_exactly_min_span_flags(self):
        # First off appearance 10 minutes ago (MIN_SPAN), newest now: the
        # boundary holds.
        self.assertEqual(
            [flag["route_id"] for flag in self.flags({812: [(12, False), (10, True), (5, True), (0, True)]})],
            [1],
        )

    def test_the_flag_stays_up_after_the_on_path_sighting_falls_out_of_the_span(self):
        # On the path 30 minutes ago, off ever since for 12 minutes: the
        # on-path sighting is long out of the 15-minute span, but well inside
        # the horizon, so the detour keeps showing (Vivian, #55 review: the
        # old rule flagged a 40-minute detour for about three minutes).
        self.assertEqual(
            [flag["route_id"] for flag in self.flags({812: [(30, False), (10, True), (5, True), (0, True)]})],
            [1],
        )

    def test_an_on_path_sighting_older_than_the_horizon_is_forgotten(self):
        # The bus was last on its path 70 minutes ago, beyond
        # SAMPLES_HORIZON: our samples can't say it belongs to this route
        # now, so it is treated like a bus from the garage.
        self.assertEqual(
            self.flags({812: [(70, False), (10, True), (5, True), (0, True)]}),
            [],
        )

    def test_a_bus_straight_from_the_garage_never_flags_its_route(self):
        # Off the path in every sample we have: it was never on the route
        # (Vivian, #55 review), so it says nothing about a detour.
        self.assertEqual(
            self.flags({812: [(15, True), (10, True), (5, True), (0, True)]}), []
        )

    def test_a_bus_back_on_its_path_breaks_the_streak(self):
        self.assertEqual(
            self.flags({812: [(15, True), (10, False), (5, True), (0, True)]}), []
        )

    def test_a_sample_the_bus_missed_starts_its_run_over(self):
        # Gone from the feed — in the garage, or GPS out — and the counting
        # starts from zero when it reappears.
        self.assertEqual(
            self.flags({812: [(15, False), (10, True), (5, None), (0, True)]}), []
        )

    def test_fewer_than_three_off_samples_cannot_say_in_a_row(self):
        self.assertEqual(self.flags({812: [(10, False), (5, True), (0, True)]}), [])
        self.assertEqual(self.flags({812: [(5, True), (0, True)]}), [])

    def test_a_route_in_one_of_its_usual_hours_is_never_flagged(self):
        # Purple's central-campus loop runs most evenings; when the nightly
        # job has marked this hour as one of the route's usual off-path
        # hours, detection refuses to flag it (Vivian, #55 review).
        now = timezone.now()
        usual_hour = now.astimezone(ET).hour
        samples, _ = samples_of({812: [(14, False), (10, True), (5, True), (0, True)]})

        self.assertEqual(
            detect.flags_from_samples(samples, ACTIVE, now, usual={1: [usual_hour]}),
            [],
        )

    def test_a_route_outside_its_usual_hours_still_flags(self):
        # Its usual hour is twelve hours from now, so this holds whenever the
        # tests run (fixed hours 3 and 4 failed between 3 and 5 AM Eastern).
        now = timezone.now()
        other_hour = (now.astimezone(ET).hour + 12) % 24
        samples, _ = samples_of({812: [(14, False), (10, True), (5, True), (0, True)]})

        self.assertEqual(
            [flag["route_id"] for flag in detect.flags_from_samples(samples, ACTIVE, now, usual={1: [other_hour]})],
            [1],
        )

    def test_a_route_without_an_official_path_is_never_flagged(self):
        self.assertEqual(
            self.flags({812: [(14, False), (10, True), (5, True), (0, True)]}, route_id=2),
            [],
        )

    def test_a_straying_bus_flags_its_route_even_when_its_sibling_stays_on_it(self):
        # One bus's excursion is what riders on its stops need to know about;
        # another bus running the route as drawn doesn't hide it.
        samples, _ = samples_of(
            {
                812: [(14, False), (10, True), (5, True), (0, True)],
                813: [(14, False), (10, False), (5, False), (0, False)],
            }
        )

        flagged = detect.flags_from_samples(samples, ACTIVE, timezone.now())

        self.assertEqual([flag["route_id"] for flag in flagged], [1])
        self.assertEqual(flagged[0]["bus_count"], 2)


@mock.patch("transit.downtowner.urlopen")
class PositionsFromFeedTests(TestCase):
    """_positions_from_feed reads the live buses and keeps only what says
    something about now: a usable route and position, and a fix from the last
    FRESH_FIX_SECONDS (a position can be minutes old, #58)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_keeps_fresh_fixes_and_leaves_the_rest_out(self, urlopen):
        now = timezone.now()
        urlopen.return_value = feed_answer(
            [
                bus_at(1, -72.94, now),
                # A fix older than FRESH_FIX_SECONDS (#58).
                bus_at(1, -72.95, now - dt.timedelta(seconds=detect.FRESH_FIX_SECONDS + 1), bus_id=813),
                # No usable id, route, position or fix.
                bus_at(1, -72.96, now, bus_id="eighteen"),
                bus_at(None, -72.97, now, bus_id=814),
                bus_at(1, -72.98, now, bus_id=815) | {"lat": "north"},
                bus_at(1, -72.99, now, bus_id=816) | {"lastUpdate": None},
            ]
        )

        self.assertEqual(
            detect._positions_from_feed(now.timestamp()), {1: {812: (41.31, -72.94)}}
        )

    def test_a_buses_answer_that_isnt_a_list_says_nothing(self, urlopen):
        urlopen.return_value = feed_answer({"buses": []})

        self.assertEqual(detect._positions_from_feed(timezone.now().timestamp()), {})


@mock.patch("transit.downtowner.urlopen")
class RecordSampleTests(TestCase):
    """record_sample folds one moment's positions into the samples table: at
    most one sample every SAMPLE_SPACING, nothing older than the horizon.
    The rows survive a restart or a Render sleep — the in-memory cache would
    not (Vivian, #55 review)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def feed(self, urlopen):
        urlopen.return_value = feed_answer(ROUTES)

    def test_a_refresh_inside_the_spacing_records_nothing_new(self, urlopen):
        self.feed(urlopen)
        now = timezone.now()
        first = detect.record_sample({1: {812: (41.31, OFF_LON)}}, now)

        second = detect.record_sample({1: {812: (41.31, OFF_LON)}}, now + dt.timedelta(seconds=30))

        self.assertEqual(second, first)
        self.assertEqual(len(second), 1)
        self.assertEqual(Sample.objects.count(), 1)

    def test_a_sample_is_recorded_once_the_spacing_passes(self, urlopen):
        self.feed(urlopen)
        now = timezone.now()
        detect.record_sample({1: {812: (41.31, -72.94)}}, now)

        samples = detect.record_sample(
            {1: {812: (41.31, OFF_LON)}}, now + dt.timedelta(minutes=2, seconds=1)
        )

        self.assertEqual(len(samples), 2)
        # The ids survive the JSON round-trip as ints, even though JSON
        # stores numbers' keys as strings.
        self.assertEqual(samples[-1]["routes"], {1: {812: True}})

    def test_samples_that_fell_out_of_the_horizon_are_dropped(self, urlopen):
        self.feed(urlopen)
        now = timezone.now()
        Sample.objects.create(
            at=now - dt.timedelta(minutes=70), off_by_bus={1: {812: True}}
        )
        Sample.objects.create(
            at=now - dt.timedelta(minutes=10), off_by_bus={1: {812: False}}
        )

        samples = detect.record_sample({1: {812: (41.31, OFF_LON)}}, now)

        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["routes"], {1: {812: False}})
        self.assertEqual(samples[-1]["routes"], {1: {812: True}})
        self.assertEqual(Sample.objects.count(), 2)  # the 70-minute-old row is gone

    def test_a_sample_survives_a_wiped_cache(self, urlopen):
        # The whole point of the table: after Render's 15 quiet minutes wipe
        # every process's memory, the streak is still there (Vivian, #55
        # review).
        self.feed(urlopen)
        now = timezone.now()
        detect.record_sample({1: {812: (41.31, -72.94)}}, now)

        cache.clear()  # the restart, or the sleep

        samples = detect.record_sample({1: {812: (41.31, OFF_LON)}}, now + dt.timedelta(minutes=2, seconds=1))
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["routes"], {1: {812: False}})


@mock.patch("transit.downtowner.urlopen")
class DetouredRoutesTests(TestCase):
    """detoured_routes end to end: every call samples the live feed and
    keeps the streak in the samples table, so page loads minutes apart add
    up, and the streak survives a restart — the fix for the #55 review,
    since the recorded polls only reach us once a day."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def ride(self, state, route_id=1, fresh=True):
        """A feed whose buses answer moves with the test's clock, so the fix
        stays fresh as `now` steps forward (`fresh=False` freezes the bus's
        lastUpdate, the way a stale fix does, #58)."""
        def urlopen(request, timeout):
            path = urlsplit(request.full_url).path
            if path == "/routes_routes.php":
                return feed_answer(ROUTES)
            if path == "/routes_buses.php":
                updated_at = state["frozen"] if not fresh else state["now"]
                return feed_answer([bus_at(route_id, state["lon"], updated_at)])
            raise AssertionError(f"unexpected feed path {path}")

        return urlopen

    def step(self, state, minutes, lon):
        """Minutes pass; the bus is now at this longitude. The feed's answers
        turn over too: downtowner serves each from its 30-second cache, and
        the test's minutes pass in microseconds, so without this the page
        would read a fix that is simulated-minutes old (#58)."""
        state["now"] += dt.timedelta(minutes=minutes)
        state["lon"] = lon
        for path in ("routes_routes.php", "routes_buses.php"):
            cache.delete(f"downtowner:{path}")
        cache.delete(detect._CACHE_KEY)  # not really a minute since the last page load

    def test_on_the_path_then_off_three_loads_in_a_row_flags_the_route(self, urlopen):
        state = {"now": timezone.now(), "lon": -72.94}
        urlopen.side_effect = self.ride(state)

        self.assertEqual(detect.detoured_routes(now=state["now"]), [])  # on the path
        self.step(state, 4, OFF_LON)
        self.assertEqual(detect.detoured_routes(now=state["now"]), [])  # just left it
        self.step(state, 4, OFF_LON)
        self.assertEqual(detect.detoured_routes(now=state["now"]), [])  # 4 minutes off
        # The rule wants CONSECUTIVE_POLLS appearances spanning at least
        # MIN_SPAN (Austin, #55 review): an 8-minute off-run isn't honest
        # about "about the last 15 minutes", so it keeps quiet.
        self.step(state, 4, OFF_LON)
        self.assertEqual(detect.detoured_routes(now=state["now"]), [])

        # One more sighting, 10½ minutes after the first, and the flag shows
        # — while the on-path sighting, 14½ minutes back, is still inside
        # the horizon, so the rule can tell a garage bus from a detoured one.
        self.step(state, 2.5, OFF_LON)
        flagged = detect.detoured_routes(now=state["now"])
        self.assertEqual([flag["route_id"] for flag in flagged], [1])
        self.assertEqual(flagged[0]["since"], Sample.objects.latest("at").at)

    def test_a_streak_survives_a_wiped_cache_mid_detour(self, urlopen):
        # Render wipes every process's memory after 15 quiet minutes, and
        # restarts wipe it sooner: the detour a rider sees when the site
        # wakes up has to come from the samples table, not from a lucky
        # streak of page loads (Vivian, #55 review).
        state = {"now": timezone.now(), "lon": -72.94}
        urlopen.side_effect = self.ride(state)

        detect.detoured_routes(now=state["now"])  # the bus starts on its path
        for _ in range(3):  # and it leaves its path while pages keep loading
            self.step(state, 4, OFF_LON)
            self.assertEqual(detect.detoured_routes(now=state["now"]), [])

        cache.clear()  # the restart, or the sleep

        self.step(state, 4, OFF_LON)  # 16 minutes off, 16 minutes on from the sighting
        flagged = detect.detoured_routes(now=state["now"])
        self.assertEqual([flag["route_id"] for flag in flagged], [1])

    def test_a_route_in_one_of_its_usual_hours_is_never_flagged(self, urlopen):
        # The nightly job has marked this hour as one of route 1's usual
        # off-path hours: an off-run that would otherwise flag stays quiet.
        # The clock is frozen so the run can't step across an hour boundary.
        state = {"now": dt.datetime(2026, 11, 20, 19, 30, tzinfo=dt.timezone.utc), "lon": -72.94}
        UsualOffPath.objects.create(
            route_id=1,
            hours=[state["now"].astimezone(ET).hour],  # 14: 2:30 PM Eastern
            window_days=14,
            updated_at=state["now"],
        )
        urlopen.side_effect = self.ride(state)

        detect.detoured_routes(now=state["now"])  # on the path
        for _ in range(4):
            self.step(state, 4, OFF_LON)
            self.assertEqual(detect.detoured_routes(now=state["now"]), [])

    def test_a_bus_that_was_never_on_the_path_doesnt_flag_its_route(self, urlopen):
        # Straight from the garage and off down the road (Vivian, #55 review).
        state = {"now": timezone.now(), "lon": OFF_LON}
        urlopen.side_effect = self.ride(state)
        for _ in range(4):
            detect.detoured_routes(now=state["now"])
            state["now"] += dt.timedelta(minutes=4)
            cache.delete(detect._CACHE_KEY)

        self.assertEqual(detect.detoured_routes(now=state["now"]), [])

    def test_a_stale_fix_says_nothing_about_now(self, urlopen):
        # The bus keeps broadcasting the same old position (#58): nothing to
        # sample, so nothing to flag.
        state = {"now": timezone.now(), "lon": OFF_LON, "frozen": timezone.now()}
        urlopen.side_effect = self.ride(state, fresh=False)
        for _ in range(4):
            detect.detoured_routes(now=state["now"])
            state["now"] += dt.timedelta(minutes=4)
            cache.delete(detect._CACHE_KEY)

        self.assertEqual(detect.detoured_routes(now=state["now"]), [])

    def test_an_inactive_route_is_never_flagged(self, urlopen):
        state = {"now": timezone.now(), "lon": -72.94}
        urlopen.side_effect = self.ride(state, route_id=3)
        for _ in range(4):
            detect.detoured_routes(now=state["now"])
            state["now"] += dt.timedelta(minutes=4)
            state["lon"] = OFF_LON
            cache.delete(detect._CACHE_KEY)

        self.assertEqual(detect.detoured_routes(now=state["now"]), [])

    def test_a_route_without_an_official_path_is_never_flagged(self, urlopen):
        state = {"now": timezone.now(), "lon": -72.94}
        urlopen.side_effect = self.ride(state, route_id=2)
        for _ in range(4):
            detect.detoured_routes(now=state["now"])
            state["now"] += dt.timedelta(minutes=4)
            state["lon"] = OFF_LON
            cache.delete(detect._CACHE_KEY)

        self.assertEqual(detect.detoured_routes(now=state["now"]), [])

    def test_the_answer_is_cached_for_a_minute(self, urlopen):
        state = {"now": timezone.now(), "lon": -72.94}
        urlopen.side_effect = self.ride(state)
        detect.detoured_routes(now=state["now"])  # the bus starts on its path
        for minutes in (4, 4, 4):  # page loads record it staying away
            self.step(state, minutes, OFF_LON)
            detect.detoured_routes(now=state["now"])
        self.step(state, 2.5, OFF_LON)  # 10½ minutes off: the flagging load
        first = detect.detoured_routes(now=state["now"])
        self.assertNotEqual(first, [])

        urlopen.side_effect = URLError("feed down")  # a second call reads the cache
        self.assertEqual(detect.detoured_routes(now=state["now"]), first)

    def test_the_feed_being_down_raises(self, urlopen):
        urlopen.side_effect = URLError("timed out")

        with self.assertRaises(downtowner.FeedUnavailable):
            detect.detoured_routes()


class MetersOffPathTests(TestCase):
    """The geometry: the distance from a point to the nearest point of the
    path, measured to the segment, not just the nearest corner."""

    def test_a_point_on_the_path_measures_zero(self):
        self.assertLess(detect.meters_off_path((41.31, -72.94), PATH), 1.0)

    def test_a_point_off_the_path_measures_the_real_distance(self):
        # 0.006 degrees of longitude at New Haven's latitude is about 500 m.
        self.assertAlmostEqual(detect.meters_off_path((41.31, OFF_LON), PATH), 502, delta=30)

    def test_a_path_with_one_corner_measures_to_the_corner(self):
        self.assertAlmostEqual(
            detect.meters_off_path((41.31, -72.935), [41.31, -72.94]), 418, delta=30
        )

    def test_a_path_too_thin_to_be_a_line_measures_zero(self):
        self.assertEqual(detect.meters_off_path((41.31, -72.94), []), 0.0)


@mock.patch("transit.next_buses.predict_minutes", return_value=None)
@mock.patch("transit.downtowner.urlopen")
class NextBusesBadgeTests(TestCase):
    """The next-buses page (#24) marks a flagged route with "possible
    detour" (#55), next to the times riders are already reading."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def prime_a_detour(self, urlopen):
        """Record bus 812 on its path and then off it three samples in a row,
        the way page loads over the last 15 minutes would have, and leave the
        feed serving an empty buses answer for the page's own read."""
        prime_a_streak({812: [(14, False), (10, True), (5, True), (0, True)]})
        urlopen.side_effect = feed_by_path(
            {
                "/routes_routes.php": ROUTES,
                "/routes_stops.php": STOPS,
                "/routes_eta.php": {"etas": {}},
                "/routes_buses.php": [],
            }
        )

    def test_a_detoured_route_is_marked_and_a_healthy_one_is_not(self, urlopen, predict):
        self.prime_a_detour(urlopen)

        response = self.client.get(reverse("stop", args=[10]))

        rows = {row.route_id: row for row in response.context["board"].rows}
        self.assertTrue(rows[1].detoured)
        self.assertFalse(rows[2].detoured)
        self.assertContains(response, "possible detour")

    def test_no_detection_no_badge(self, urlopen, predict):
        urlopen.side_effect = feed_by_path(
            {
                "/routes_routes.php": ROUTES,
                "/routes_stops.php": STOPS,
                "/routes_eta.php": {"etas": {}},
                "/routes_buses.php": [],
            }
        )

        response = self.client.get(reverse("stop", args=[10]))

        self.assertFalse(any(row.detoured for row in response.context["board"].rows))
        self.assertNotContains(response, "possible detour")

    def test_a_streak_whose_bus_has_gone_from_the_feed_doesnt_flag(self, urlopen, predict):
        # An old streak in the samples table, but the feed serves no buses
        # now: the page's own sample misses the bus, which starts its run
        # over — one sample says nothing.
        now = timezone.now()
        for m, off in ((15, False), (10, True), (5, True), (0, True)):
            Sample.objects.create(
                at=now - dt.timedelta(minutes=45 + m), off_by_bus={1: {812: off}}
            )
        urlopen.side_effect = feed_by_path(
            {
                "/routes_routes.php": ROUTES,
                "/routes_stops.php": STOPS,
                "/routes_eta.php": {"etas": {}},
                "/routes_buses.php": [],
            }
        )

        response = self.client.get(reverse("stop", args=[10]))

        self.assertFalse(any(row.detoured for row in response.context["board"].rows))


@mock.patch("transit.downtowner.urlopen")
class HomeFirstPaintTests(TestCase):
    """The home page's first paint shows a detour already detected in the last
    minute and never touches the feed (#13, #55); the page's swap-in to the
    full alert list detects the rest."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_detected_detour_shows_without_calling_the_feed(self, urlopen):
        prime_a_streak({812: [(14, False), (10, True), (5, True), (0, True)]})
        urlopen.return_value = feed_answer(ROUTES)
        detect.detoured_routes()  # what the swap-in did a moment ago
        urlopen.reset_mock()

        response = self.client.get(reverse("home"))

        urlopen.assert_not_called()
        self.assertContains(response, "Blue - Weekday Daytime: possible detour")
        self.assertContains(response, "Detected from the live bus positions")

    def test_a_detour_nobody_has_detected_yet_waits_for_the_swap_in(self, urlopen):
        prime_a_streak({812: [(14, False), (10, True), (5, True), (0, True)]})

        response = self.client.get(reverse("home"))

        urlopen.assert_not_called()
        self.assertNotContains(response, "possible detour")


class UsualShapeJobTests(TestCase):
    """The nightly record_usual_shapes job (#55): from the recorded history,
    the Eastern hours where a route's usual shapes live — the whole route off
    its drawn path, one bus making detour-sized runs off it alone, and the
    walk back to the garage at day's end — which detection then refuses to
    flag. Feed mocked; history fabricated."""

    def setUp(self):
        cache.clear()  # the feed's answers and its outage memory live there
        self.addCleanup(cache.clear)

    def poll_with_bus(self, polled_at, route_id, lat, lon, bus_id=812):
        poll, _ = Poll.objects.get_or_create(polled_at=polled_at)  # one poll carries many buses
        BusPosition.objects.create(
            poll=poll,
            polled_at=polled_at,
            bus_id=bus_id,
            bus_name=str(bus_id),
            route_id=route_id,
            route_name=f"Route {route_id}",
            lat=lat,
            lon=lon,
            last_update=polled_at,  # a fresh fix, the way detection likes it
        )

    def evening_fixes(self, route_id, days):
        """Bus fixes `days` ago, 500 m off the fixture path, at 20:00 and
        21:00 Eastern — four fixes an evening-hour, five evenings, so the
        hour clears MIN_FIXES — plus one daytime fix on the path each day.
        Relative to now, so the job's 14-day window can never age the data
        out (fixed dates started failing CI the evening of Nov 24 — Vivian,
        #55 review)."""
        now = timezone.now()
        for index in days:
            evening = now.astimezone(ET).replace(hour=20, minute=0, second=0, microsecond=0) - dt.timedelta(
                days=index
            )
            for minute in (0, 5, 10, 15):
                self.poll_with_bus(
                    evening + dt.timedelta(minutes=minute), route_id, 41.31, OFF_LON
                )
                self.poll_with_bus(
                    evening + dt.timedelta(hours=1, minutes=minute), route_id, 41.31, OFF_LON
                )
            self.poll_with_bus(
                evening.replace(hour=8), route_id, 41.31, -72.94
            )

    def single_bus_runs(self, route_id, days):
        """One bus 500 m off the fixture path for ten minutes — three
        five-minute fixes — in the 22:00 Eastern hour on each of `days`,
        with twenty sibling fixes on the path in the same hour, so the
        hour's off fraction is 3 of 23 and only the run rule can mark it.
        The day's last fix is on the path, so the tail rule stays out."""
        now = timezone.now()
        for index in days:
            night = now.astimezone(ET).replace(hour=22, minute=0, second=0, microsecond=0) - dt.timedelta(
                days=index
            )
            for minute in (0, 5, 10):
                self.poll_with_bus(
                    night + dt.timedelta(minutes=minute), route_id, 41.31, OFF_LON, bus_id=8000 + route_id
                )
            for minute in range(1, 40, 2):  # odd minutes: never the same poll as the running bus
                self.poll_with_bus(
                    night + dt.timedelta(minutes=minute), route_id, 41.31, -72.94, bus_id=8100 + route_id
                )

    def garage_walk(self, route_id, days, midnight_minute=5):
        """Each of `days`, the route serves noon on the path, then its last
        fixes just after midnight Eastern are off it — the walk home. The
        25-minute tail before that last fix spans hours 23 and 0."""
        now = timezone.now()
        for index in days:
            midnight = (
                now.astimezone(ET)
                .replace(hour=0, minute=midnight_minute, second=0, microsecond=0)
                - dt.timedelta(days=index)
            )
            self.poll_with_bus(midnight.replace(hour=12), route_id, 41.31, -72.94)
            self.poll_with_bus(midnight, route_id, 41.31, OFF_LON)
            self.poll_with_bus(midnight + dt.timedelta(minutes=5), route_id, 41.31, OFF_LON)

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_a_route_looping_most_evenings_gets_its_evening_hours(self, urlopen):
        self.evening_fixes(1, days=list(range(5)))  # five evenings, 8 AM on the path

        summary = jobs.record_usual_shapes()

        row = UsualOffPath.objects.get(route_id=1)
        self.assertEqual(row.hours, [20, 21])  # the evenings, not the mornings
        self.assertEqual(row.window_days, jobs.WINDOW_DAYS)
        self.assertIn("route 1 at [20, 21]", summary)

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_a_novel_off_path_day_is_not_usual(self, urlopen):
        self.evening_fixes(1, days=[0])  # one evening of off-path fixes

        jobs.record_usual_shapes()

        self.assertFalse(UsualOffPath.objects.exists())  # too few days to be a shape

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_one_bus_running_off_path_ten_minutes_on_three_days_marks_the_hour(self, urlopen):
        """One bus's evening loop: three days of ten-minute off-runs in the
        same hour, with the rest of the route on the path, is the route's
        usual shape there (Vivian, #55 review) — though two days are still
        novel enough to flag."""
        self.single_bus_runs(1, days=[0, 1, 2])
        self.single_bus_runs(3, days=[0, 1])

        jobs.record_usual_shapes()

        self.assertEqual(UsualOffPath.objects.get(route_id=1).hours, [22])
        self.assertFalse(UsualOffPath.objects.filter(route_id=3).exists())

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_a_run_survives_one_missed_poll_but_not_two(self, urlopen):
        """The recorded polls have gaps, so a bus missing one poll doesn't
        end its off-run — 22:00 to 22:15 with 22:10 gone is one 15-minute
        run — but missing two does: 21:00–21:05 and 21:30–21:35 are two
        short halves, and neither is detour-sized."""
        now = timezone.now()
        for index in (0, 1, 2):
            night = now.astimezone(ET).replace(hour=22, minute=0, second=0, microsecond=0) - dt.timedelta(
                days=index
            )
            for minute in (0, 5, 15):
                self.poll_with_bus(night + dt.timedelta(minutes=minute), 1, 41.31, OFF_LON)
            self.poll_with_bus(night + dt.timedelta(minutes=40), 1, 41.31, -72.94)
        for index in (0, 1, 2):
            night = now.astimezone(ET).replace(hour=21, minute=0, second=0, microsecond=0) - dt.timedelta(
                days=index
            )
            for minute in (0, 5, 30, 35):
                self.poll_with_bus(night + dt.timedelta(minutes=minute), 3, 41.31, OFF_LON)
            self.poll_with_bus(night + dt.timedelta(minutes=40), 3, 41.31, -72.94)

        jobs.record_usual_shapes()

        self.assertEqual(UsualOffPath.objects.get(route_id=1).hours, [22])
        self.assertFalse(UsualOffPath.objects.filter(route_id=3).exists())

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_a_run_counts_in_every_hour_it_covers(self, urlopen):
        """An off-run that starts late in one Eastern hour and stretches into
        the next flags detection in the second hour — the flag lands once the
        run is RUN_MIN_SPAN old — so the run rule marks both (Vivian, #55
        review: a loop leaving the path at 8:50 PM was learned as hour 20
        and then flagged at 9:00 PM)."""
        now = timezone.now()
        for index in (0, 1, 2):
            evening = now.astimezone(ET).replace(
                hour=21, minute=50, second=0, microsecond=0
            ) - dt.timedelta(days=index)
            for minute in (0, 10, 15, 20):  # 21:50 to 22:10, one run across both hours
                self.poll_with_bus(evening + dt.timedelta(minutes=minute), 1, 41.31, OFF_LON, bus_id=8001)
            self.poll_with_bus(evening + dt.timedelta(minutes=30), 1, 41.31, -72.94)  # ends on the path

        jobs.record_usual_shapes()

        self.assertEqual(UsualOffPath.objects.get(route_id=1).hours, [21, 22])

    def test_the_service_day_runs_four_am_to_four_am_eastern(self):
        evening = dt.datetime(2026, 11, 20, 23, 50, tzinfo=ET)
        self.assertEqual(jobs.service_day(dt.datetime(2026, 11, 21, 0, 10, tzinfo=ET)), evening.date())
        self.assertEqual(jobs.service_day(dt.datetime(2026, 11, 21, 3, 59, tzinfo=ET)), evening.date())
        self.assertEqual(jobs.service_day(dt.datetime(2026, 11, 21, 4, 1, tzinfo=ET)), dt.date(2026, 11, 21))

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_a_route_running_through_midday_gets_its_walk_home_marked(self, urlopen):
        """Purple runs 5 AM to midnight, so its service day has a late-morning
        fix: on a noon-to-noon day that fix was the day's last, and the walk
        home just after midnight never looked like the day's end. From 4 AM
        to 4 AM the walk is the day's last fix again, so the end-of-service
        hours get marked (Vivian, #55 review)."""
        now = timezone.now()
        for index in (1, 2, 3):
            midnight = now.astimezone(ET).replace(
                hour=0, minute=10, second=0, microsecond=0
            ) - dt.timedelta(days=index)
            self.poll_with_bus(midnight.replace(hour=11), 1, 41.31, -72.94)  # midday service, on the path
            self.poll_with_bus(midnight - dt.timedelta(minutes=20), 1, 41.31, OFF_LON)  # 11:50 PM, off it
            self.poll_with_bus(midnight, 1, 41.31, OFF_LON)  # and the walk home, just after midnight

        jobs.record_usual_shapes()

        self.assertEqual(UsualOffPath.objects.get(route_id=1).hours, [0, 23])

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_the_walk_back_to_the_garage_marks_the_end_of_service_hours(self, urlopen):
        """A route whose recorded day ends off the path just after midnight
        Eastern, three days running, has its end-of-service hours marked:
        the alert would name a bus no rider can still catch (Vivian, #55
        review)."""
        self.garage_walk(1, days=[1, 2, 3])

        jobs.record_usual_shapes()

        self.assertEqual(UsualOffPath.objects.get(route_id=1).hours, [0, 23])

    @mock.patch("transit.downtowner.urlopen", return_value=feed_answer(ROUTES))
    def test_rerunning_rewrites_the_same_rows_and_drops_stale_routes(self, urlopen):
        self.evening_fixes(1, days=list(range(5)))
        UsualOffPath.objects.create(route_id=3, hours=[20], window_days=7, updated_at=timezone.now())

        jobs.record_usual_shapes()
        jobs.record_usual_shapes()

        self.assertEqual(UsualOffPath.objects.count(), 1)  # route 3 no longer loops
        self.assertEqual(UsualOffPath.objects.get(route_id=1).hours, [20, 21])

    def test_the_feed_being_down_fails_loudly(self):
        with mock.patch("transit.downtowner.urlopen", side_effect=URLError("timed out")):
            with self.assertRaises(downtowner.FeedUnavailable):
                jobs.record_usual_shapes()
        self.assertFalse(UsualOffPath.objects.exists())


class DemoTests(TestCase):
    """The demo detour alert (#46, #55) is created once and never fights an
    admin who has since edited it."""

    def seed(self):
        out = io.StringIO()
        demo.seed({"admin": None, "rider": None}, out)
        return out.getvalue()

    def test_it_is_created_once(self):
        self.assertIn("Created", self.seed())
        self.assertIn("Kept", self.seed())
        self.assertEqual(Alert.objects.count(), 1)

    def test_it_names_the_stops_it_skips_and_shows_now(self):
        self.seed()
        alert = Alert.objects.get()
        self.assertIn("Chapel St at York St", alert.skipped_stops)
        self.assertTrue(alert.starts_at <= timezone.now() < alert.ends_at)

    def test_an_admin_edit_survives_a_reseed(self):
        self.seed()
        Alert.objects.update(message="Edited by an admin since.")
        self.seed()
        self.assertEqual(Alert.objects.get().message, "Edited by an admin since.")
