"""Tests for the data-quality report and its spot-check (#74). In their own file,
so they don't collide with other pull requests that add to history/tests.py."""

import datetime as dt
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from history import quality

T0 = dt.datetime(2026, 11, 15, 14, 0, tzinfo=dt.UTC)


def at(minutes, seconds=0):
    return T0 + dt.timedelta(minutes=minutes, seconds=seconds)


def eta(minutes, eta_minutes, route=1, stop=7, bus=50, stop_name="Grove / Temple", route_name="Blue"):
    return {
        "polled_at_utc": at(minutes).isoformat(),
        "route_id": str(route),
        "route_name": route_name,
        "stop_id": str(stop),
        "stop_name": stop_name,
        "bus_id": str(bus),
        "eta_minutes": "" if eta_minutes is None else str(eta_minutes),
    }


def position(minutes, last_stop, fix_minutes=None, route=1, bus=50, route_name="Blue"):
    fix = at(minutes if fix_minutes is None else fix_minutes)
    return {
        "polled_at_utc": at(minutes).isoformat(),
        "route_id": str(route),
        "route_name": route_name,
        "bus_id": str(bus),
        "last_stop_id": str(last_stop),
        "last_update_utc": fix.isoformat(),
    }


class InferredArrivalTests(SimpleTestCase):
    def test_a_countdown_run_out_is_one_arrival_however_long_the_bus_dwells(self):
        rows = [eta(0, 12), eta(5, 4), eta(10, 0), eta(15, 0), eta(20, None)]
        self.assertEqual(quality.inferred_arrivals(rows), {(1, 7, 50): [at(9)]})

    def test_each_bus_route_and_stop_is_kept_apart(self):
        rows = [eta(5, 2), eta(5, 3, bus=51), eta(5, 1, stop=8)]
        self.assertEqual(set(quality.inferred_arrivals(rows)), {(1, 7, 50), (1, 7, 51), (1, 8, 50)})


class GpsVisitTests(SimpleTestCase):
    def test_a_new_last_stop_between_two_polls_is_a_visit_in_that_window(self):
        rows = [position(0, 6), position(5, 7, fix_minutes=4.5)]
        self.assertEqual(quality.gps_visits(rows), [(1, 7, 50, at(0), at(4, 30))])

    def test_no_visit_across_a_missed_poll_a_route_change_or_a_stale_fix(self):
        for rows in [
            [position(0, 6), position(10, 7)],  # a poll missed in between
            [position(0, 6), position(5, 7, route=2)],  # started another route
            [position(0, 6), position(5, 7, fix_minutes=-1)],  # GPS older than the last poll
            [position(0, 6), position(5, 6)],  # still at the same stop
        ]:
            with self.subTest(rows=rows):
                self.assertEqual(quality.gps_visits(rows), [])


class SpotCheckTests(SimpleTestCase):
    def test_offsets_are_zero_inside_the_window_and_signed_outside(self):
        self.assertEqual(quality.offset_minutes(at(3), at(0), at(5)), 0)
        self.assertEqual(quality.offset_minutes(at(-2), at(0), at(5)), -2)
        self.assertEqual(quality.offset_minutes(at(8), at(0), at(5)), 3)

    def test_visits_are_matched_to_the_nearest_inferred_arrival(self):
        visits = [
            (1, 7, 50, at(0), at(5)),  # inferred at 3: inside
            (1, 8, 50, at(0), at(5)),  # inferred at 8: 3 minutes late
            (1, 9, 50, at(0), at(5)),  # nothing inferred: missed
            (1, 4, 50, at(0), at(5)),  # no ETAs for this stop at all: not counted
        ]
        arrivals = {(1, 7, 50): [at(-30), at(3)], (1, 8, 50): [at(8)], (1, 9, 50): [at(40)]}
        offsets, missed = quality.spot_check(visits, arrivals, {(1, 7), (1, 8), (1, 9)})
        self.assertEqual(offsets, [0, 3])
        self.assertEqual(missed, [visits[2]])
        summary = quality.summarize_offsets(offsets, missed)
        self.assertEqual(
            (summary["visits"], summary["inferred"], summary["inside"], summary["late"]), (3, 2, 1, 1)
        )

    def test_staleness_is_the_share_of_positions_older_than_each_limit(self):
        # Positions 1, 11 and 4 minutes old when polled.
        rows = [position(0, 6, fix_minutes=-1), position(5, 6, fix_minutes=-6), position(10, 6, fix_minutes=6)]
        positions, shares = quality.gps_staleness(rows)["Blue"]
        self.assertEqual(positions, 3)
        self.assertAlmostEqual(shares[3], 2 / 3)
        self.assertAlmostEqual(shares[10], 1 / 3)


class CommandTests(SimpleTestCase):
    def test_reports_from_github_reads_without_touching_the_network(self):
        files = {
            "data/2026-11-15/etas.csv": [eta(0, 12), eta(5, 4)],
            "data/2026-11-15/buses.csv": [position(5, 6), position(10, 7, fix_minutes=9)],
        }
        gaps = [{"polled_at": at(0), "cause": "no_run"}]
        out = StringIO()
        with (
            mock.patch("history.sources.read_rows", side_effect=lambda path: iter(files[path])),
            mock.patch("history.sources.read_gaps", return_value=gaps),
        ):
            call_command("data_quality", days=1, until="2026-11-15", stdout=out, stderr=StringIO())
        report = out.getvalue()
        self.assertIn("| 2026-11-15 | 1 | no_run (1) |", report)
        self.assertIn("| Blue | 2 | 1 | 2 |", report)
        self.assertIn("put a bus at a new stop 1 times. For 1 of them (100%)", report)
        self.assertIn("Grove / Temple: GPS says the bus got there between", report)
