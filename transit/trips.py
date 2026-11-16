"""A shuttle trip between two places (story #5): the stop to walk to, the stop
to get off at, and the walk at each end.

    plan((41.3112, -72.9290), (41.3137, -72.9311), served_stops()) -> Plan

The plan takes the pair of stops on a common route that needs the least
walking: from the start to the first stop, and from the last stop to where the
rider is going. Each end considers its nearest few stops. We don't know how
long each ride takes, so the walking decides.
"""

import math
from dataclasses import dataclass

WALK_METERS_PER_MINUTE = 80  # the same pace as the home page's map
NEAREST_STOPS = 6  # considered at each end
AT_THE_STOP_METERS = 50  # closer than this, the rider is at the stop: no walk


@dataclass
class Plan:
    board_at: dict  # the stop to get on at, as in served_stops()
    get_off_at: dict  # the stop to get off at
    walk_to_stop: int  # minutes, from the start to board_at; 0 when the start is the stop
    walk_from_stop: int  # minutes, from get_off_at to the destination; 0 when it's the stop
    walk_all_the_way: int  # minutes, to compare

    @property
    def walking_is_quicker(self):
        """Walking straight there is no longer than walking to and from the stops."""
        return self.walk_all_the_way <= self.walk_to_stop + self.walk_from_stop


def plan(origin, destination, stops):
    """The trip from origin to destination, both (latitude, longitude), or
    None when no route links a stop near one to a stop near the other."""
    best = None
    for start, to_start in _nearest(origin, stops):
        for end, from_end in _nearest(destination, stops):
            if start["id"] == end["id"] or not set(start["routes"]) & set(end["routes"]):
                continue
            if best is None or to_start + from_end < best[0]:
                best = (to_start + from_end, start, end, to_start, from_end)
    if best is None:
        return None
    _, start, end, to_start, from_end = best
    return Plan(
        board_at=start,
        get_off_at=end,
        walk_to_stop=0 if to_start < AT_THE_STOP_METERS else walk_minutes(to_start),
        walk_from_stop=0 if from_end < AT_THE_STOP_METERS else walk_minutes(from_end),
        walk_all_the_way=walk_minutes(meters_between(origin, destination)),
    )


def walk_minutes(meters):
    return max(1, round(meters / WALK_METERS_PER_MINUTE))


def meters_between(a, b):
    """The distance between two (latitude, longitude) points, in meters."""
    (lat1, lon1), (lat2, lon2) = a, b
    rad = math.pi / 180
    h = (
        math.sin((lat2 - lat1) * rad / 2) ** 2
        + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin((lon2 - lon1) * rad / 2) ** 2
    )
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _nearest(point, stops):
    """[(stop, meters)]: the stops nearest the point, nearest first."""
    by_distance = sorted(
        ((stop, meters_between(point, (stop["lat"], stop["lon"]))) for stop in stops),
        key=lambda pair: pair[1],
    )
    return by_distance[:NEAREST_STOPS]
