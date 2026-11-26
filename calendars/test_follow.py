"""Following the rider's bus through a trip (#163): calendars.follow, with
the feed mocked at transit.downtowner. Nothing here reaches the network."""

import datetime as dt
import time
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from transit.downtowner import FeedUnavailable

from . import follow

ROUTE, BUS, BOARD, GET_OFF = 1, 812, 30, 10
# The Blue's stops in loop order, as the feed's routes answer lists them: the bus comes
# from 45 and 40 to 30, then 25 and 20 to 10, the stop to get off at.
ROUTES = [{"id": ROUTE, "name": "Blue - Weekday Daytime", "stops": [45, 40, 30, 25, 20, 10, 50]}]


class FollowTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.at = {}  # stop -> the bus's minutes there, as the feed's ETAs give them
        self.last_stop, self.seconds_ago = 45, 20  # two stops before the one to get on at
        self.progress = follow.Progress()
        for patch in (
            mock.patch("transit.downtowner.etas", side_effect=self.etas),
            mock.patch("transit.downtowner.buses", side_effect=self.buses),
            mock.patch("transit.downtowner.routes", return_value=ROUTES),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def etas(self, stop_id):
        minutes = self.at.get(stop_id)
        return [] if minutes is None else [{"route_id": ROUTE, "bus_id": BUS, "bus_name": "812", "minutes": minutes}]

    def buses(self):
        return [{"id": BUS, "name": "812", "route": ROUTE, "lat": 41.30, "lon": -72.93, "lastStop": self.last_stop,
                 "lastUpdate": time.time() - self.seconds_ago}]

    def trip(self, board=None, get_off=None, later=0, correct=None, late=None):
        """The trip `later` minutes on, the feed timing the bus `board` minutes
        from the first stop and `get_off` from the second (None: not listed)."""
        self.at = {BOARD: board, GET_OFF: get_off}
        return follow.follow(
            ROUTE, BUS, BOARD, GET_OFF, self.progress, self.minutes(later), correct=correct, late=late
        )

    def minutes(self, later):
        return self.now + dt.timedelta(minutes=later)

    def test_a_bus_on_its_way_to_the_first_stop_is_coming(self):
        trip = self.trip(board=4, get_off=12)
        self.assertEqual((trip.phase, trip.to_board, trip.to_get_off, trip.step), (follow.COMING, 4, 12, 0))
        self.assertEqual(trip.get_off_at, self.minutes(12))
        self.assertEqual(self.progress.board_at, self.minutes(4))
        self.assertEqual((trip.bus["lat"], trip.bus["lon"], trip.bus["age_minutes"]), (41.30, -72.93, None))

    def test_at_the_first_stop_it_is_boarding(self):
        trip = self.trip(board=0, get_off=8)
        self.assertEqual((trip.phase, trip.step), (follow.BOARDING, 1))

    def test_past_the_first_stop_it_is_riding(self):
        # The stop dropped off the bus's list: it's timed only to the second one now.
        self.trip(board=2, get_off=10)
        trip = self.trip(get_off=6, later=4)
        self.assertEqual((trip.phase, trip.to_get_off, trip.step), (follow.RIDING, 6, 2))
        self.assertTrue(self.progress.boarded)

    def test_the_stop_before_the_second_makes_it_next(self):
        self.last_stop = 20  # the stop right before 10 on the route
        trip = self.trip(get_off=1)
        self.assertEqual((trip.phase, trip.step), (follow.NEXT_STOP, 2))

    def test_an_old_position_s_last_stop_isnt_trusted_for_next(self):
        self.last_stop, self.seconds_ago = 20, 4 * 60
        trip = self.trip(get_off=1)
        self.assertEqual(trip.phase, follow.RIDING)
        self.assertEqual(trip.bus["age_minutes"], 4)

    def test_at_the_second_stop_it_is_get_off(self):
        self.trip(get_off=3)
        trip = self.trip(get_off=0, later=3)
        self.assertEqual((trip.phase, trip.step), (follow.GET_OFF, 3))

    def test_past_the_second_stop_it_is_off(self):
        self.trip(get_off=3)
        trip = self.trip(later=4)  # not timed there any more, once it was due
        self.assertEqual((trip.phase, trip.step), (follow.OFF, 4))
        self.assertTrue(self.progress.off)

    def test_its_next_loop_never_reads_as_coming_again(self):
        # The recorded polls list a bus's next loop: once the rider is off, the
        # same bus is on its way to their first stop again.
        self.trip(board=3, get_off=9)
        self.trip(get_off=4, later=5)
        self.trip(get_off=0, later=9)
        for later, board, get_off in ((12, 20, 30), (20, 12, 22), (31, 1, 11)):
            self.assertEqual(self.trip(board=board, get_off=get_off, later=later).phase, follow.OFF)

    def test_a_next_loop_after_a_gap_means_the_bus_has_passed(self):
        # The page was closed during the ride: half an hour on, the bus has
        # moved on and is coming round again. Its predicted time jumped a
        # whole loop, so it's past.
        self.trip(board=5, get_off=15)
        self.last_stop = 10  # its GPS went round past both stops meanwhile
        trip = self.trip(board=25, get_off=35, later=30)
        self.assertEqual(trip.phase, follow.OFF)
        self.assertTrue(self.progress.boarded)

    def test_a_held_bus_is_still_coming(self):
        # Held at a stop, its countdowns jump later, and its GPS shows it
        # hasn't moved. Replayed, reading that as "passed" told riders they
        # were off before their bus had come.
        self.trip(board=5, get_off=11)
        trip = self.trip(board=9, get_off=15, later=5)
        self.assertEqual((trip.phase, trip.to_board), (follow.COMING, 9))
        self.assertFalse(self.progress.boarded)

    def test_a_bus_delayed_after_moving_on_a_little_is_still_coming(self):
        # It moved a stop (45 to 40), and its countdowns jumped 10 minutes
        # later: a delay, since it didn't go past the stop to get on at.
        self.trip(board=6, get_off=21)
        self.last_stop = 40
        trip = self.trip(board=11, get_off=26, later=5)
        self.assertEqual((trip.phase, trip.to_board), (follow.COMING, 11))

    def test_moving_past_a_stop_counts_though_its_last_stop_skipped_it(self):
        self.trip(board=3, get_off=12)
        self.last_stop = 25  # 45 to 25: it went past 30, and its last stop skipped it
        trip = self.trip(board=0, get_off=7, later=4)  # its countdown there still says 0
        self.assertEqual(trip.phase, follow.RIDING)
        self.assertTrue(self.progress.boarded)

    def test_crossing_a_stop_goes_forward_round_the_loop(self):
        order = [1, 2, 3, 4]
        self.assertTrue(follow._crossed(order, 1, 3, 2))
        self.assertTrue(follow._crossed(order, 1, 3, 3))  # it served it
        self.assertFalse(follow._crossed(order, 1, 3, 4))
        self.assertTrue(follow._crossed(order, 3, 2, 4))  # round the end of the list
        self.assertIsNone(follow._crossed(order, 1, 9, 2))  # a stop the loop doesn't list
        self.assertFalse(follow._crossed([5, 6, 7, 6, 8], 5, 6, 7))  # the shortest way, past a stop served twice

    def test_its_gps_serving_a_stop_moves_the_trip_on(self):
        self.trip(board=1, get_off=8)
        self.last_stop = BOARD
        self.assertEqual(self.trip(board=0, get_off=7, later=1).phase, follow.RIDING)  # at the stop: getting on
        self.last_stop = GET_OFF
        self.assertEqual(self.trip(get_off=0, later=8).phase, follow.OFF)

    def test_a_late_bus_is_still_coming(self):
        self.trip(board=5, get_off=15)
        trip = self.trip(board=4, get_off=14, later=4)  # a few minutes behind, not a whole loop
        self.assertEqual((trip.phase, trip.to_board), (follow.COMING, 4))

    def test_a_bus_the_feed_stops_timing_is_lost_until_it_was_due(self):
        self.trip(board=5, get_off=12)
        self.assertEqual(self.trip(later=2).phase, follow.LOST)  # due at the first stop in 3: still coming, untimed
        self.assertEqual(self.trip(later=9).phase, follow.LOST)  # past the first stop by now; the second is due at 12
        self.assertTrue(self.progress.boarded)
        self.assertEqual(self.trip(later=12).phase, follow.OFF)

    def test_never_timed_at_all_is_lost(self):
        trip = self.trip()
        self.assertEqual((trip.phase, trip.step), (follow.LOST, 0))
        self.assertFalse(self.progress.boarded)

    def test_the_minutes_shown_are_corrected_and_the_phases_read_the_feed(self):
        # #153: the pages show the corrected countdown; above RAW_MAX it's
        # corrected (here by -2), at or below it the feed's stands.
        def correct(route_id, minutes):
            return minutes - 2 if minutes > 5 else minutes

        trip = self.trip(board=4, get_off=12, correct=correct)
        self.assertEqual((trip.phase, trip.to_board, trip.to_get_off), (follow.COMING, 4, 10))
        self.assertEqual(trip.get_off_at, self.minutes(10))  # "There by" counts from the corrected time
        self.assertEqual(self.progress.get_off_at, self.minutes(12))  # the phases keep the feed's

    def test_there_by_adds_the_arrival_cushion(self):
        # #174: how much later than the corrected time the bus may reach the stop.
        def late(route_id, minutes):
            return 3

        coming = self.trip(board=4, get_off=12, late=late)
        self.assertEqual((coming.get_off_at, coming.get_off_by), (self.minutes(12), self.minutes(15)))
        riding = self.trip(get_off=6, later=6, late=late)
        self.assertEqual(riding.get_off_by, self.minutes(6 + 6 + 3))
        self.assertEqual(self.trip(get_off=5, later=7).get_off_by, self.minutes(12))  # no fit: the time itself

    def test_without_a_bus_it_follows_the_route_s_soonest(self):
        with mock.patch("transit.downtowner.etas", return_value=[
            {"route_id": ROUTE, "bus_id": 900, "bus_name": "900", "minutes": 9},
            {"route_id": ROUTE, "bus_id": 901, "bus_name": "901", "minutes": 3},
            {"route_id": 2, "bus_id": 902, "bus_name": "902", "minutes": 1},
        ]):
            trip = follow.follow(ROUTE, None, BOARD, GET_OFF, self.progress, self.now)
        self.assertEqual((trip.phase, trip.to_board, trip.bus), (follow.COMING, 3, None))

    def test_the_feed_down_raises_and_leaves_the_progress_alone(self):
        self.trip(board=5, get_off=12)
        before = self.progress.as_dict()
        with mock.patch("transit.downtowner.etas", side_effect=FeedUnavailable("down")):
            with self.assertRaises(FeedUnavailable):
                self.trip(get_off=6, later=6)
        self.assertEqual(self.progress.as_dict(), before)

    def test_a_stop_served_twice_has_two_stops_before_it(self):
        routes = [{"id": 7, "stops": [1, 2, 3, 2, 4]}, "not a route"]
        self.assertEqual(follow._stops_before(routes, 7, 2), {1, 3})
        self.assertEqual(follow._stops_before(routes, 8, 2), set())

    def test_the_progress_survives_a_session(self):
        self.trip(get_off=6)
        restored = follow.Progress.from_dict(self.progress.as_dict())
        self.assertEqual(restored, self.progress)
        self.assertEqual(follow.Progress.from_dict(None), follow.Progress())
        self.assertEqual(follow.Progress.from_dict({"board_at": "not a time"}).board_at, None)
