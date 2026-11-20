"""Detour detection (#55). The Downtowner feed is mocked at
transit.downtowner.urlopen, answering with fixture routes and buses, so no
test reaches the network, and the streaks are driven with hand-built samples
in record_sample's shape, so no test touches the database."""

import datetime as dt
import io
import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert
from detours import demo, detect
from transit import downtowner

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
    """Put a recorded streak in the cache, the way page loads over the last
    15 minutes would have. Returns the newest sample's time."""
    samples, newest = samples_of(runs, route_id=route_id)
    cache.set(detect._SAMPLES_KEY, samples, detect.SAMPLES_SECONDS)
    return newest


class FlagsFromSamplesTests(TestCase):
    """flags_from_samples, the pure rule: a route is flagged when one of its
    buses was on the official path and then off it for CONSECUTIVE_POLLS or
    more appearances in a row spanning at least MIN_SPAN (#55). Deliberately
    hard to flag: a bus straight from the garage, a bus back on the path, a
    sample the bus missed, an off-run too short to be honest about "15
    minutes" — each blocks the flag."""

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
        # row, but bunched into 4 minutes (Austin, #55 review). The alert says
        # "about the last 15 minutes", so the run has to earn that first.
        self.assertEqual(self.flags({812: [(6, False), (4, True), (2, True), (0, True)]}), [])

    def test_an_off_run_spanning_exactly_min_span_flags(self):
        # First off appearance 10 minutes ago (MIN_SPAN), newest now: the
        # boundary holds, so the wording stays true from the minute it shows.
        self.assertEqual(
            [flag["route_id"] for flag in self.flags({812: [(12, False), (10, True), (5, True), (0, True)]})],
            [1],
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

    def test_samples_past_the_span_stop_counting(self):
        # The bus was on its path 20 minutes ago; that sample has fallen out
        # of SAMPLE_SPAN, so this is three off-path samples and nothing else —
        # a bus from the garage, not a detour.
        self.assertEqual(
            self.flags({812: [(20, False), (10, True), (5, True), (0, True)]}), []
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
    """record_sample folds one moment's positions into the cached samples: at
    most one sample every SAMPLE_SPACING, nothing older than the span."""

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

    def test_a_sample_is_recorded_once_the_spacing_passes(self, urlopen):
        self.feed(urlopen)
        now = timezone.now()
        detect.record_sample({1: {812: (41.31, -72.94)}}, now)

        samples = detect.record_sample(
            {1: {812: (41.31, OFF_LON)}}, now + dt.timedelta(minutes=2, seconds=1)
        )

        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[-1]["routes"], {1: {812: True}})

    def test_samples_that_fell_out_of_the_span_are_dropped(self, urlopen):
        self.feed(urlopen)
        now = timezone.now()
        old = {"at": (now - dt.timedelta(minutes=20)).timestamp(), "routes": {1: {812: True}}}
        keep = {"at": (now - dt.timedelta(minutes=10)).timestamp(), "routes": {1: {812: False}}}
        cache.set(detect._SAMPLES_KEY, [old, keep], detect.SAMPLES_SECONDS)

        samples = detect.record_sample({1: {812: (41.31, OFF_LON)}}, now)

        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0], keep)
        self.assertEqual(samples[-1]["routes"], {1: {812: True}})


@mock.patch("transit.downtowner.urlopen")
class DetouredRoutesTests(TestCase):
    """detoured_routes end to end: every call samples the live feed and keeps
    the streak in the cache, so page loads minutes apart add up — the fix for
    the #55 review, since the recorded polls only reach us once a day."""

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
        # SAMPLE_SPAN, so the rule can tell a garage bus from a detoured one.
        self.step(state, 2.5, OFF_LON)
        flagged = detect.detoured_routes(now=state["now"])
        self.assertEqual([flag["route_id"] for flag in flagged], [1])
        newest_sample = cache.get(detect._SAMPLES_KEY)[-1]
        self.assertEqual(
            flagged[0]["since"],
            dt.datetime.fromtimestamp(newest_sample["at"], tz=dt.timezone.utc),
        )

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

    def test_a_streak_that_has_fallen_out_of_the_span_doesnt_flag(self, urlopen, predict):
        # The same three off-path samples, but from half an hour ago: the
        # page's own sample is all it has, and one sample says nothing.
        now = timezone.now()
        stale = [
            {"at": (now - dt.timedelta(minutes=45 + m)).timestamp(), "routes": {1: {812: off}}}
            for m, off in ((15, False), (10, True), (5, True), (0, True))
        ]
        cache.set(detect._SAMPLES_KEY, stale, detect.SAMPLES_SECONDS)
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
