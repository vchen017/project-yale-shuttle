"""The tools the notification agent can call (issue #28).

Each tool is defined the way an MCP server describes one (a name, a
description and a JSON `inputSchema`), so the same list could be served over
MCP later. Here they run in-process: OpenRouter's function-calling carries the
model's request to `ToolBox.call`, and the answer goes back as JSON.

Tools are bound to ONE trip when the ToolBox is made, and take no arguments.
The model can't ask about another route, stop or rider, so a confused or
manipulated model has nothing else to read.

Where the numbers come from is a `DataSource`, passed in. `LiveData` reads the
live feed, our predictions and the crowd reports. The scheduled job (#29) is
meant to hand in a source built from data it already loaded, so checking a trip
every 5 minutes doesn't touch the database each time.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from transit.downtowner import FeedUnavailable

from .decide import _local


@dataclass(frozen=True)
class TripRequest:
    """One upcoming trip, as the agent is asked about it. The arrival times
    aren't in here: the agent has to ask the tools for them. Datetimes are aware."""

    route: str  # the route's name, for the model
    stop: str  # the stop's name, for the model
    needed_at: datetime  # when the rider needs to be at their destination
    walking_minutes: int  # walk to the stop
    route_id: int | None = None  # for LiveData; numeric ids, never short names
    stop_id: int | None = None


class DataSource:
    """What the tools read. A method returns None when it has nothing."""

    def official_minutes(self, trip):
        """Minutes until the next bus, by the Downtowner. Raises
        transit.downtowner.FeedUnavailable when the feed is down."""
        raise NotImplementedError

    def predicted_minutes(self, trip):
        """Minutes until the next bus, by our own prediction (#23)."""
        raise NotImplementedError

    def spare_minutes(self, trip):
        """The minutes to spare at the stop that the time to leave for the
        bus predicted_minutes gave should build in (#155). 0 by default."""
        return 0

    def crowding(self, trip):
        """{"current": label or None, "typical_now": label or None}, from riders' reports."""
        raise NotImplementedError


class LiveData(DataSource):
    """The real thing: the live feed, our predictions and crowd reports."""

    def official_minutes(self, trip):
        from transit import downtowner

        times = [
            arrival["minutes"]
            for arrival in downtowner.etas(trip.stop_id)
            if arrival["route_id"] == trip.route_id and arrival["minutes"] >= 0
        ]
        return min(times) if times else None

    def predicted_minutes(self, trip):
        from history.predictions import predict_minutes

        # The number the pages show (#153): the feed's countdown corrected by what the recorded
        # history says about countdowns that size (#117), or history's guess when the feed has none.
        return predict_minutes(trip.route_id, trip.stop_id)

    def crowding(self, trip):
        from crowding.services import crowding

        route = {"id": trip.route_id, "name": trip.route, "label": trip.route, "color": "00356b"}
        (answer,) = crowding([route])
        typical = answer.typical_now
        return {"current": answer.current_label, "typical_now": typical.label if typical else None}


def _definition(name, description):
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }


TOOLS = [
    _definition(
        "get_trip",
        "The trip you're deciding about: the route, the stop, when the rider needs to be at their "
        "destination, how many minutes they walk to the stop, and the time now.",
    ),
    _definition(
        "get_official_arrival",
        "When the Downtowner says this route's next bus reaches the stop, and the time the rider "
        "must leave for the stop to meet it, and how many minutes after the bus reaches the stop the "
        "rider has to be at their destination. Says so when the live feed is down or no bus is coming.",
    ),
    _definition(
        "get_our_prediction",
        "When our own prediction says the next bus reaches the stop, the time the rider must leave "
        "to meet it, and the minutes from its arrival to the rider's deadline. It's the live "
        "countdown corrected by what our recorded history says about countdowns like it, or, when "
        "the feed lists no bus, a typical wait for this time of day. The time to leave keeps "
        "minutes_to_spare at the stop, which our history says riders need to make the bus. "
        "Says so when we have no prediction.",
    ),
    _definition(
        "get_crowding",
        "How crowded this route is: the level riders reported recently and the usual level for "
        "this hour. Either may be missing.",
    ),
]


class ToolBox:
    """The tools, bound to one trip, one data source and one moment.

    Remembers the arrival times it gave the model, so the code (not the model)
    can work out when the rider has to leave and hold the answer to it.
    """

    def __init__(self, trip, data, now):
        self.trip, self.data, self.now = trip, data, now
        self.official = None  # arrival datetime, once get_official_arrival found one
        self.predicted = None
        self.spare = 0  # minutes to spare at the stop that our prediction's time to leave keeps (#155)

    @property
    def leave_by(self):
        """When the rider must leave for the stop: our prediction wins over the official
        time, as in decide.leave_by, keeping its minutes to spare (#155). None when no
        tool gave an arrival."""
        if self.predicted:
            return self.predicted - timedelta(minutes=self.trip.walking_minutes + self.spare)
        return self.official - timedelta(minutes=self.trip.walking_minutes) if self.official else None

    def call(self, name, arguments):
        """Run a tool and return its result as JSON text. Never raises: a
        problem comes back as {"error": ...}, which the model can work around.
        `arguments` is the model's raw argument text; these tools take none, so
        it only has to be a JSON object (or empty)."""
        try:
            parsed = json.loads(arguments) if arguments and arguments.strip() else {}
        except ValueError:
            return json.dumps({"error": "The arguments weren't valid JSON."})
        if not isinstance(parsed, dict):
            return json.dumps({"error": "The arguments must be a JSON object."})
        handler = {
            "get_trip": self._trip,
            "get_official_arrival": self._official,
            "get_our_prediction": self._prediction,
            "get_crowding": self._crowding,
        }.get(name)
        if handler is None:
            return json.dumps({"error": f"There is no tool called {name!r}."})
        try:
            return json.dumps(handler())
        except FeedUnavailable:
            return json.dumps({"available": False, "reason": "The live shuttle feed is unavailable."})
        except Exception as error:  # a broken source mustn't stop the decision
            return json.dumps({"error": f"That tool failed ({error.__class__.__name__})."})

    def _trip(self):
        trip = self.trip
        return {
            "route": trip.route,
            "stop": trip.stop,
            "rider_needs_to_be_at_destination_by": _local(trip.needed_at),
            "walking_minutes_to_stop": trip.walking_minutes,
            "now": _local(self.now),
        }

    def _arrival(self, minutes, none_reason, spare=0):
        if minutes is None:
            return {"available": False, "reason": none_reason}, None
        arrival = self.now + timedelta(minutes=minutes)
        leave = arrival - timedelta(minutes=self.trip.walking_minutes + spare)
        # Arithmetic is done here, not left to the model: how long after the bus reaches the stop the
        # rider has to be at their destination (negative: the bus is later than that).
        gap = round((self.trip.needed_at - arrival).total_seconds() / 60)
        result = {"available": True, "arrival": _local(arrival), "leave_for_the_stop_by": _local(leave),
                  "minutes_from_arrival_to_deadline": gap}
        if spare:
            result["minutes_to_spare"] = spare
        return result, arrival

    def _official(self):
        result, self.official = self._arrival(
            self.data.official_minutes(self.trip), "The feed lists no bus on the way for this route."
        )
        return result

    def _prediction(self):
        minutes = self.data.predicted_minutes(self.trip)
        self.spare = self.data.spare_minutes(self.trip) if minutes is not None else 0
        result, self.predicted = self._arrival(
            minutes, "We have no prediction for this route at this stop now.", spare=self.spare
        )
        return result

    def _crowding(self):
        answer = self.data.crowding(self.trip)
        if not answer or not (answer.get("current") or answer.get("typical_now")):
            return {"available": False, "reason": "No crowding reports for this route."}
        return {"available": True, "reported_recently": answer.get("current"),
                "usual_for_this_hour": answer.get("typical_now")}
