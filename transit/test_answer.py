"""The trip page's one answer, ride time and map (#131). The Downtowner feed
is mocked, at transit.answer.downtowner.etas for the unit tests and at
transit.downtowner.urlopen for the page's, so no test reaches the network. In
its own file, so it doesn't collide with other pull requests."""

import datetime as dt
import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import answer as answers
from .next_buses import Board, Row
from .test_next_buses import ROUTES, feed
from .test_trips import GROVE, UNION
from .trips import Plan

# 5:00 PM UTC is noon in New Haven (EST), so clock times are easy to read.
NOW = dt.datetime(2026, 11, 19, 17, 0, tzinfo=dt.timezone.utc)
BLUE = Row(route_id=1, name="Blue - Weekday Daytime", label="Blue", color="4472C4")
BOARD = Board(running=True, rows=[BLUE])


def eta(minutes, bus_id=812, route_id=1):
    return {"route_id": route_id, "bus_id": bus_id, "bus_name": str(bus_id), "minutes": minutes}


def plan(walk_to=6, walk_from=3, walk_all=40):
    return Plan(board_at=GROVE, get_off_at=UNION, walk_to_stop=walk_to, walk_from_stop=walk_from, walk_all_the_way=walk_all)


def etas_by_stop(at_grove, at_union):
    """A stand-in for downtowner.etas that answers per stop."""
    return mock.patch(
        "transit.answer.downtowner.etas",
        side_effect=lambda stop_id: {GROVE["id"]: at_grove, UNION["id"]: at_union}[stop_id],
    )


def ride(at_grove, at_union, ours=None, **plan_kwargs):
    """The answer for a trip whose board shows the feed's countdowns at Grove
    (and our prediction, when `ours` is given)."""
    official = sorted(e["minutes"] for e in at_grove if e["route_id"] == 1)
    board = Board(running=True, rows=[Row(1, BLUE.name, BLUE.label, BLUE.color, official=official, ours=ours)])
    with etas_by_stop(at_grove, at_union):
        return answers.answer(plan(**plan_kwargs), board, 40, NOW)


class AnswerTests(SimpleTestCase):
    def test_it_leads_with_when_to_leave_for_which_bus_and_how_early_they_will_be(self):
        result = ride([eta(10)], [eta(22)], walk_to=6)
        self.assertEqual(result.kind, answers.RIDE)
        self.assertEqual(
            result.headline, "Leave in 2 min for the 12:10 PM Blue - Weekday Daytime, to be at the stop 2 min early"
        )

    def test_the_buffer_is_taken_off_the_time_to_leave(self):
        result = ride([eta(20)], [eta(32)], walk_to=6)
        self.assertEqual((result.leave_in, result.early_minutes), (20 - 6 - answers.BUFFER_MINUTES, answers.BUFFER_MINUTES))

    def test_a_bus_too_close_for_the_whole_buffer_means_leave_now_and_a_smaller_margin(self):
        result = ride([eta(7)], [eta(19)], walk_to=6)
        self.assertEqual((result.leave_in, result.early_minutes), (0, 1))
        self.assertEqual(
            result.headline, "Leave now for the 12:07 PM Blue - Weekday Daytime, to be at the stop 1 min early"
        )

    def test_a_bus_that_arrives_as_they_do_is_still_offered_with_no_margin(self):
        result = ride([eta(6)], [eta(18)], walk_to=6)
        self.assertEqual((result.leave_in, result.early_minutes), (0, 0))
        self.assertEqual(
            result.headline, "Leave now for the 12:06 PM Blue - Weekday Daytime, to be at the stop just as it arrives"
        )

    def test_the_ride_is_the_same_buss_gap_between_the_two_stops(self):
        result = ride([eta(10)], [eta(22)], walk_to=6, walk_from=3)
        self.assertEqual(result.ride_minutes, 12)
        self.assertEqual(result.arrive_at, NOW + dt.timedelta(minutes=10 + 12 + 3))

    def test_another_bus_at_the_far_stop_is_not_this_buss_ride(self):
        result = ride([eta(10, bus_id=812)], [eta(14, bus_id=900), eta(25, bus_id=812)])
        self.assertEqual(result.ride_minutes, 15)

    def test_a_countdown_at_the_far_stop_from_before_the_bus_boards_is_ignored(self):
        # The same bus 4 minutes from the far stop, 10 from ours: it passes the
        # far stop first (it's coming round the loop), so that's not the ride.
        result = ride([eta(10)], [eta(4), eta(30)])
        self.assertEqual(result.ride_minutes, 20)

    def test_a_bus_the_far_stop_doesnt_show_gives_no_ride_time_and_no_guess(self):
        result = ride([eta(10)], [eta(22, bus_id=999)])
        self.assertEqual(result.kind, answers.RIDE)
        self.assertIsNone(result.ride_minutes)
        self.assertIsNone(result.arrive_at)

    def test_a_bus_without_an_id_gives_no_ride_time(self):
        self.assertIsNone(ride([eta(10, bus_id=None)], [eta(22)]).ride_minutes)

    def test_it_names_the_bus_it_picked_for_a_shared_trip_to_follow(self):
        self.assertEqual(ride([eta(3, bus_id=1), eta(12, bus_id=2)], [eta(20, bus_id=2)], walk_to=8).bus_id, 2)
        self.assertIsNone(ride([eta(10, bus_id=None)], [eta(22)]).bus_id)  # the feed named none (#10)

    def test_it_skips_a_bus_the_rider_cant_reach_in_time(self):
        # An 8-minute walk: the bus in 3 minutes leaves before they're there.
        result = ride([eta(3, bus_id=1), eta(12, bus_id=2)], [eta(20, bus_id=2)], walk_to=8)
        self.assertEqual(result.bus_minutes, 12)
        self.assertEqual(result.leave_in, 2)
        self.assertEqual(result.ride_minutes, 8)  # the second bus's own ride

    def test_at_the_stop_there_is_nothing_to_leave_for(self):
        result = ride([eta(6)], [eta(18)], walk_to=0)
        self.assertEqual(result.headline, "The 12:06 PM Blue - Weekday Daytime reaches your stop in 6 min")

    def test_only_buses_on_a_route_that_links_both_stops_count(self):
        with etas_by_stop([eta(7, route_id=2), eta(15, route_id=1)], [eta(27, route_id=1)]):
            board = Board(running=True, rows=[Row(1, BLUE.name, BLUE.label, BLUE.color, official=[15])])
            result = answers.answer(plan(), board, 40, NOW)
        self.assertEqual(result.route_id, 1)
        self.assertEqual(result.bus_minutes, 15)

    def test_the_board_rows_expected_time_is_the_one_it_plans_around(self):
        # Our prediction (9) says sooner than the feed's countdown (10): the
        # answer follows the board's one number, not the raw feed.
        result = ride([eta(10)], [eta(22)], ours=9, walk_to=6)
        self.assertEqual(result.bus_minutes, 9)
        self.assertEqual(result.bus_at, NOW + dt.timedelta(minutes=9))
        self.assertEqual(result.ride_minutes, 12)  # a duration: still the feed's gap

    def test_when_the_expected_time_is_too_soon_the_feeds_later_buses_follow_it(self):
        result = ride([eta(4, bus_id=1), eta(15, bus_id=2)], [eta(25, bus_id=2)], ours=3, walk_to=6)
        self.assertEqual(result.bus_minutes, 15)
        self.assertEqual(result.ride_minutes, 10)

    def test_a_prediction_with_no_feed_countdown_still_answers_but_not_the_ride(self):
        result = ride([], [], ours=12, walk_to=6)
        self.assertEqual(result.kind, answers.RIDE)
        self.assertEqual(result.bus_minutes, 12)
        self.assertIsNone(result.ride_minutes)

    def test_no_catchable_bus_says_so_and_what_walking_costs(self):
        result = ride([eta(2)], [], walk_to=6)
        self.assertEqual(result.kind, answers.NO_BUS)
        self.assertEqual(result.headline, "No bus to catch right now")
        self.assertEqual(result.detail, "Walking the whole way takes about 40 min.")

    def test_no_running_route_on_the_board_means_no_bus(self):
        with etas_by_stop([eta(10)], [eta(22)]):
            result = answers.answer(plan(), Board(running=False, rows=[]), 40, NOW)
        self.assertEqual(result.kind, answers.NO_BUS)

    def test_no_plan_says_no_shuttle_links_the_places(self):
        result = answers.answer(None, None, 25, NOW)
        self.assertEqual(result.kind, answers.NO_ROUTE)
        self.assertEqual(result.headline, "No shuttle running now links these places")
        self.assertEqual(result.detail, "Walking the whole way takes about 25 min.")

    def test_clock_times_are_eastern_with_am_and_pm(self):
        self.assertEqual(answers.clock(NOW), "12:00 PM")
        self.assertEqual(answers.clock(NOW + dt.timedelta(hours=7, minutes=5)), "7:05 PM")
        self.assertEqual(answers.clock(NOW - dt.timedelta(hours=12)), "12:00 AM")


class WalkingIsQuickerTests(SimpleTestCase):
    """The notice beside the headline: walking from now arrives no later than the bus."""

    def trip(self, wait, ride, walk_to=3, walk_from=0, walk_all=19):
        """A trip whose bus is `wait` minutes from the stop and rides for `ride`."""
        return answer_for(wait, ride, walk_to=walk_to, walk_from=walk_from, walk_all=walk_all)

    def test_the_live_sample_walking_is_slower_so_no_notice(self):
        # 4 wait + 14 ride = 18 min by bus against 19 on foot.
        result = self.trip(wait=4, ride=14)
        self.assertFalse(result.walking_is_quicker)

    def test_a_short_hop_walking_is_quicker(self):
        # 6 + 5 + 3 = 14 min by bus against 12 on foot.
        result = self.trip(wait=6, ride=5, walk_to=4, walk_from=3, walk_all=12)
        self.assertTrue(result.walking_is_quicker)
        self.assertEqual(result.walk_arrive_at, NOW + dt.timedelta(minutes=12))
        self.assertLess(result.walk_arrive_at, result.arrive_at)

    def test_a_tie_shows_the_notice(self):
        result = self.trip(wait=6, ride=6, walk_to=4, walk_from=3, walk_all=15)
        self.assertEqual(result.arrive_at, result.walk_arrive_at)
        self.assertTrue(result.walking_is_quicker)

    def test_one_minute_slower_on_foot_is_no_notice(self):
        result = self.trip(wait=6, ride=6, walk_to=4, walk_from=3, walk_all=16)
        self.assertFalse(result.walking_is_quicker)

    def test_it_never_replaces_the_bus_headline(self):
        # Walking (8) beats even the two walks to and from the stops (5 + 5):
        # the bus is still the headline, with the notice beside it.
        result = self.trip(wait=9, ride=6, walk_to=5, walk_from=5, walk_all=8)
        self.assertEqual(result.kind, answers.RIDE)
        self.assertTrue(result.walking_is_quicker)
        self.assertIn("Leave", result.headline)

    def test_without_a_ride_time_there_is_nothing_to_compare(self):
        with etas_by_stop([eta(10)], [eta(22, bus_id=999)]):
            result = answers.answer(plan(walk_all=5), Board(True, [Row(1, BLUE.name, BLUE.label, BLUE.color, official=[10])]), 5, NOW)
        self.assertIsNone(result.ride_minutes)
        self.assertFalse(result.walking_is_quicker)

    def test_without_a_bus_to_catch_there_is_no_notice(self):
        with etas_by_stop([eta(1)], []):
            result = answers.answer(plan(walk_to=6, walk_all=5), BOARD, 5, NOW)
        self.assertEqual(result.kind, answers.NO_BUS)
        self.assertFalse(result.walking_is_quicker)

    def test_the_notice_always_agrees_with_the_two_arrival_times_shown(self):
        # Every combination: the notice is on exactly when walking arrives no
        # later than the bus, by the times the page prints.
        for wait in range(3, 25, 3):
            for ride in range(2, 20, 3):
                for walk_from in (0, 2, 5):
                    for walk_all in range(5, 45, 4):
                        with self.subTest(wait=wait, ride=ride, walk_from=walk_from, walk_all=walk_all):
                            result = self.trip(wait=wait, ride=ride, walk_to=2, walk_from=walk_from, walk_all=walk_all)
                            self.assertEqual(result.arrive_at, NOW + dt.timedelta(minutes=wait + ride + walk_from))
                            self.assertEqual(result.walk_arrive_at, NOW + dt.timedelta(minutes=walk_all))
                            self.assertEqual(result.walking_is_quicker, walk_all <= wait + ride + walk_from)
                            gap = (result.arrive_at - result.walk_arrive_at) / dt.timedelta(minutes=1)
                            self.assertEqual(result.walking_is_quicker, gap >= 0)


def answer_for(wait, ride, *, walk_to, walk_from, walk_all):
    """The answer for one bus `wait` minutes from the boarding stop, riding `ride`."""
    board = Board(running=True, rows=[Row(1, BLUE.name, BLUE.label, BLUE.color, official=[wait])])
    with etas_by_stop([eta(wait)], [eta(wait + ride)]):
        return answers.answer(plan(walk_to=walk_to, walk_from=walk_from, walk_all=walk_all), board, walk_all, NOW)


class RouteSegmentTests(SimpleTestCase):
    # A loop: four corners going round, [lat, lon] flattened as the feed has it.
    PATH = [41.30, -72.93, 41.31, -72.93, 41.31, -72.92, 41.30, -72.92]
    A = {"lat": 41.30, "lon": -72.93}
    B = {"lat": 41.31, "lon": -72.92}

    def test_the_ride_runs_from_one_stop_to_the_next_along_the_path(self):
        segment = answers.route_segment(self.PATH, self.A, self.B)
        self.assertEqual(segment, [(41.30, -72.93), (41.30, -72.93), (41.31, -72.93), (41.31, -72.92), (41.31, -72.92)])

    def test_a_stop_earlier_on_the_path_means_riding_round_the_loop(self):
        segment = answers.route_segment(self.PATH, self.B, self.A)
        self.assertEqual(segment[1:-1], [(41.31, -72.92), (41.30, -72.92), (41.30, -72.93)])

    def test_a_route_with_no_usable_path_has_no_segment(self):
        self.assertEqual(answers.route_segment([], self.A, self.B), [])
        self.assertEqual(answers.route_segment([41.30, -72.93], self.A, self.B), [])


def feed_by_stop(etas_by_stop, routes):
    """The test feed, but answering each stop's ETAs separately."""
    base = feed(routes=routes)

    def urlopen(request, timeout):
        parts = urlsplit(request.full_url)
        if parts.path != "/routes_eta.php":
            return base(request, timeout)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        stop = parse_qs(parts.query)["stop"][0]
        response.read.return_value = json.dumps(etas_by_stop[stop]).encode()
        return response

    return urlopen


def feed_etas(stop, *etas):
    return {"etas": {stop: {"etas": [{"route": r, "bus_id": b, "bus_name": str(b), "avg": m} for r, b, m in etas]}}}


# Route 1 runs Science Hill (10), Grove (20), Union (30); the path is the stops'.
PATHED = [
    {**route, "path": [41.316, -72.922, 41.313, -72.925, 41.297, -72.926]} if route["id"] == 1 else route
    for route in ROUTES
]
TRIP = {"from": "Grove", "from_at": "41.3130,-72.9250", "to": "Union", "to_at": "41.2970,-72.9260"}


@mock.patch("transit.next_buses.predict_minutes", return_value=None)
@mock.patch("transit.downtowner.urlopen")
class TripPageAnswerTests(TestCase):
    def setUp(self):
        cache.clear()

    def stub(self, urlopen, at_grove, at_union):
        urlopen.side_effect = feed_by_stop({"20": feed_etas("20", *at_grove), "30": feed_etas("30", *at_union)}, PATHED)

    def test_the_answer_comes_before_everything_else_on_the_page(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        html = self.client.get(reverse("trip"), TRIP).content.decode()
        html = html[html.index("<main") :]
        answer = html.index("trip-answer-headline")
        for later in ("Grove to Union", 'data-trip-map', "trip-steps", "Next buses at"):
            self.assertLess(answer, html.index(later), later)

    def test_it_shows_the_ride_the_arrival_and_the_whole_trip(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        response = self.client.get(reverse("trip"), TRIP)
        answer = response.context["answer"]
        self.assertEqual(answer.kind, "ride")
        self.assertEqual(answer.ride_minutes, 12)
        self.assertContains(response, "<dd>12 min</dd>", html=True)
        self.assertContains(response, "Arrive at Union")

    def test_a_logged_in_rider_can_share_the_bus_with_a_friend(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        self.client.force_login(get_user_model().objects.create_user("night-rider", password="not-it"))
        response = self.client.get(reverse("trip"), TRIP)
        self.assertContains(response, f'action="{reverse("share-create")}"')
        for name, value in (("route_id", 1), ("bus_id", 812), ("board_stop_id", 20), ("get_off_stop_id", 30)):
            self.assertContains(response, f'name="{name}" value="{value}"')  # the bus and stops, nothing of theirs (#10)

    def test_a_rider_not_logged_in_is_asked_to_log_in_to_share(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        response = self.client.get(reverse("trip"), TRIP)
        self.assertContains(response, "to share this bus with a friend")
        self.assertNotContains(response, reverse("share-create"))

    def test_without_a_ride_time_it_says_so_instead_of_guessing(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [])
        response = self.client.get(reverse("trip"), TRIP)
        self.assertContains(response, "The feed doesn't say yet how long this ride takes.")
        self.assertNotContains(response, "Arrive at Union")

    def test_walking_is_quicker_shows_a_notice_beside_the_bus_answer(self, urlopen, predict):
        # Grove to Union is about 22 min on foot; a bus in 30 minutes can't beat that.
        self.stub(urlopen, [(1, 812, 30)], [(1, 812, 42)])
        response = self.client.get(reverse("trip"), TRIP)
        self.assertTrue(response.context["answer"].walking_is_quicker)
        self.assertContains(response, "Walking is quicker: there by")
        self.assertContains(response, "reaches your stop in 30 min")  # the bus is still the headline

    def test_no_notice_when_the_bus_is_quicker(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 2)], [(1, 812, 8)])
        response = self.client.get(reverse("trip"), TRIP)
        self.assertFalse(response.context["answer"].walking_is_quicker)
        self.assertNotContains(response, "Walking is quicker")
        self.assertContains(response, "Walking the whole way takes about")

    def test_the_map_gets_the_places_the_stops_and_the_stretch_of_route(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        data = self.client.get(reverse("trip"), TRIP).context["map_data"]
        self.assertEqual(data["origin"], [41.313, -72.925])
        self.assertEqual(data["board"]["name"], "Grove Street")
        self.assertEqual(data["get_off"]["name"], "Union Station")
        self.assertEqual(data["route"]["color"], "4472C4")
        self.assertEqual(data["route"]["path"][0], [41.313, -72.925])
        self.assertEqual(data["route"]["path"][-1], [41.297, -72.926])

    def test_the_maps_data_is_in_the_page_for_its_script(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)], [(1, 812, 22)])
        self.assertContains(self.client.get(reverse("trip"), TRIP), '<script id="trip-map-data" type="application/json">')

    def test_with_no_bus_to_ride_the_map_still_shows_the_two_places(self, urlopen, predict):
        self.stub(urlopen, [], [])
        data = self.client.get(reverse("trip"), TRIP).context["map_data"]
        self.assertEqual(data["destination"], [41.297, -72.926])
        self.assertNotIn("route", data)


class RowExpectedTests(SimpleTestCase):
    """Row.expected is the one number the answer plans around."""

    def test_our_prediction_when_we_have_one_else_the_feeds_countdown_else_none(self):
        row = Row(route_id=1, name="Blue", label="Blue", color="4472C4", official=[10, 20], ours=9)
        self.assertEqual(row.expected, 9)
        row.ours = None
        self.assertEqual(row.expected, 10)
        row.official = []
        self.assertIsNone(row.expected)


@mock.patch("transit.next_buses.predict_minutes", return_value=None)
@mock.patch("transit.downtowner.urlopen")
class TripPageDetailsTests(TestCase):
    """The steps and the next-buses board sit in collapsed details (#133)."""

    def setUp(self):
        cache.clear()

    def stub(self, urlopen, at_grove):
        urlopen.side_effect = feed_by_stop({"20": feed_etas("20", *at_grove), "30": feed_etas("30")}, PATHED)

    def test_the_steps_and_the_board_are_there_but_closed(self, urlopen, predict):
        self.stub(urlopen, [(1, 812, 10)])
        html = self.client.get(reverse("trip"), TRIP).content.decode()
        self.assertEqual(html.count('<details class="trip-details'), 2)
        self.assertNotIn(" open>", html)
        self.assertIn("<span>Steps</span>", html)
        self.assertIn("<span>Next buses at Grove Street</span>", html)
        self.assertIn("Take the shuttle to", html)  # the content is in the page, just folded

    def test_with_no_bus_to_catch_the_board_starts_open(self, urlopen, predict):
        self.stub(urlopen, [])
        html = self.client.get(reverse("trip"), TRIP).content.decode()
        self.assertIn('<details class="trip-details" open>', html)
        self.assertEqual(html.count(" open>"), 1)  # only the board
