"""The trip-notification agent (#28). OpenRouter is mocked at
assistant.openrouter.chat_message (and, for the request itself, at urlopen), the
live feed, predictions and crowd reports are mocked or faked, so no test reaches
the network."""

import io
import json
from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.error import URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from assistant import agent, evals, openrouter
from assistant.decide import DONT_NOTIFY, InvalidAnswer
from assistant.tools import TOOLS, DataSource, LiveData, ToolBox, TripRequest
from transit.downtowner import FeedUnavailable

EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 16, 8, 0, tzinfo=EST)
TRIP = TripRequest(
    route="Blue",
    stop="Grove Street",
    needed_at=datetime(2026, 11, 16, 9, 0, tzinfo=EST),
    walking_minutes=10,
    route_id=1,
    stop_id=20,
)
# The bus is official in 45 minutes (8:45) and ours says 50 (8:50): leave for the stop by 8:40.
GOOD = {"notify": True, "notify_at": "2026-11-16T08:35:00-05:00", "message": "Blue is due 8:50. Leave by 8:40."}
QUIET = {"notify": False, "notify_at": None, "message": ""}


class FakeData(DataSource):
    def __init__(self, official=45, predicted=50, crowding=None, down=False, spare=0):
        self.official, self.predicted, self._crowding, self.down = official, predicted, crowding, down
        self.spare = spare

    def official_minutes(self, trip):
        if self.down:
            raise FeedUnavailable("down")
        return self.official

    def predicted_minutes(self, trip):
        return self.predicted

    def spare_minutes(self, trip):
        return self.spare

    def crowding(self, trip):
        return self._crowding


def call(call_id, name, arguments="{}"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def asks(*calls):
    """A model reply that wants these tools run."""
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def answers(data):
    """A model reply with its final answer."""
    return {"role": "assistant", "content": data if isinstance(data, str) or data is None else json.dumps(data)}


LOOK_EVERYTHING_UP = asks(call("a", "get_trip"), call("b", "get_official_arrival"), call("c", "get_our_prediction"))


def decide(replies, data=None, **kwargs):
    """Run the agent with these model replies, one per model turn. Returns (decision, the mock)."""
    with mock.patch("assistant.agent.openrouter.chat_message", side_effect=replies) as chat:
        decision = agent.decide_with_tools(TRIP, data or FakeData(), now=NOW, **kwargs)
    return decision, chat


class ToolTests(SimpleTestCase):
    def box(self, **data):
        return ToolBox(TRIP, FakeData(**data), NOW)

    def run_tool(self, box, name, arguments="{}"):
        return json.loads(box.call(name, arguments))

    def test_the_tools_are_described_the_way_mcp_describes_them_and_take_no_arguments(self):
        self.assertEqual({t["name"] for t in TOOLS},
                         {"get_trip", "get_official_arrival", "get_our_prediction", "get_crowding"})
        for tool in TOOLS:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")
            # Bound to one trip: there's nothing the model can point at another route or rider with.
            self.assertEqual(tool["inputSchema"]["properties"], {})
            self.assertIs(tool["inputSchema"]["additionalProperties"], False)

    def test_get_trip_gives_the_trip_and_the_time_now_in_eastern(self):
        self.assertEqual(self.run_tool(self.box(), "get_trip"), {
            "route": "Blue", "stop": "Grove Street",
            "rider_needs_to_be_at_destination_by": "2026-11-16T09:00-05:00",
            "walking_minutes_to_stop": 10, "now": "2026-11-16T08:00-05:00",
        })

    def test_an_arrival_comes_with_when_the_rider_must_leave(self):
        box = self.box()
        self.assertEqual(self.run_tool(box, "get_official_arrival"),
                         {"available": True, "arrival": "2026-11-16T08:45-05:00",
                          "leave_for_the_stop_by": "2026-11-16T08:35-05:00",
                          "minutes_from_arrival_to_deadline": 15})
        ours = self.run_tool(box, "get_our_prediction")
        self.assertEqual(ours["leave_for_the_stop_by"], "2026-11-16T08:40-05:00")
        self.assertEqual(ours["minutes_from_arrival_to_deadline"], 10)  # 8:50 bus, needed at 9:00

    def test_a_bus_later_than_the_deadline_has_a_negative_gap(self):
        late = self.run_tool(self.box(predicted=75), "get_our_prediction")  # 9:15 against a 9:00 deadline
        self.assertEqual(late["minutes_from_arrival_to_deadline"], -15)

    def test_leave_by_prefers_our_prediction_and_falls_back_to_the_official_time(self):
        box = self.box()
        self.assertIsNone(box.leave_by)  # nothing looked up yet
        box.call("get_official_arrival", "{}")
        self.assertEqual(box.leave_by, datetime(2026, 11, 16, 8, 35, tzinfo=EST))
        box.call("get_our_prediction", "{}")
        self.assertEqual(box.leave_by, datetime(2026, 11, 16, 8, 40, tzinfo=EST))

    def test_our_time_to_leave_keeps_the_minutes_to_spare(self):
        # #155: the 8:50 bus, a 10-minute walk and 3 minutes to spare at the stop.
        box = ToolBox(TRIP, FakeData(spare=3), NOW)
        ours = json.loads(box.call("get_our_prediction", "{}"))
        self.assertEqual((ours["leave_for_the_stop_by"], ours["minutes_to_spare"]), ("2026-11-16T08:37-05:00", 3))
        self.assertEqual(ours["arrival"], "2026-11-16T08:50-05:00")  # the bus's own time doesn't move
        self.assertEqual(box.leave_by, datetime(2026, 11, 16, 8, 37, tzinfo=EST))
        official = json.loads(box.call("get_official_arrival", "{}"))
        self.assertNotIn("minutes_to_spare", official)  # only our prediction's time keeps it

    def test_no_prediction_and_no_bus_are_said_so(self):
        box = self.box(official=None, predicted=None)
        for name in ("get_official_arrival", "get_our_prediction"):
            result = self.run_tool(box, name)
            self.assertIs(result["available"], False)
            self.assertIn("reason", result)
        self.assertIsNone(box.leave_by)

    def test_a_feed_that_is_down_is_said_so_and_does_not_crash(self):
        result = self.run_tool(self.box(down=True), "get_official_arrival")
        self.assertEqual(result, {"available": False, "reason": "The live shuttle feed is unavailable."})

    def test_crowding(self):
        crowd = {"current": "Packed", "typical_now": "Busy"}
        self.assertEqual(self.run_tool(self.box(crowding=crowd), "get_crowding"),
                         {"available": True, "reported_recently": "Packed", "usual_for_this_hour": "Busy"})
        for nothing in (None, {"current": None, "typical_now": None}):
            self.assertIs(self.run_tool(self.box(crowding=nothing), "get_crowding")["available"], False)

    def test_mistakes_by_the_model_come_back_as_errors_it_can_read(self):
        box = self.box()
        self.assertIn("no tool called", self.run_tool(box, "delete_everything")["error"])
        self.assertIn("valid JSON", self.run_tool(box, "get_trip", "{not json")["error"])
        self.assertIn("JSON object", self.run_tool(box, "get_trip", "[1, 2]")["error"])
        self.assertEqual(self.run_tool(box, "get_trip", "")["route"], "Blue")  # empty arguments are fine

    def test_arguments_that_try_to_point_elsewhere_are_ignored(self):
        asked = self.run_tool(self.box(), "get_trip", json.dumps({"route_id": 99, "rider": "someone else"}))
        self.assertEqual(asked["route"], "Blue")

    def test_a_source_that_breaks_is_an_error_not_a_crash(self):
        data = FakeData()
        data.predicted_minutes = mock.Mock(side_effect=RuntimeError("secret detail"))
        result = json.loads(ToolBox(TRIP, data, NOW).call("get_our_prediction", "{}"))
        self.assertEqual(result, {"error": "That tool failed (RuntimeError)."})  # and says nothing more


class LiveDataTests(SimpleTestCase):
    @mock.patch("transit.downtowner.etas")
    def test_the_official_time_is_the_soonest_bus_on_this_route_at_this_stop(self, etas):
        etas.return_value = [
            {"route_id": 2, "bus_id": 7, "bus_name": "7", "minutes": 1},   # another route
            {"route_id": 1, "bus_id": 8, "bus_name": "8", "minutes": -2},  # already gone
            {"route_id": 1, "bus_id": 9, "bus_name": "9", "minutes": 12},
            {"route_id": 1, "bus_id": 10, "bus_name": "10", "minutes": 30},
        ]
        self.assertEqual(LiveData().official_minutes(TRIP), 12)
        etas.assert_called_once_with(20)

    @mock.patch("transit.downtowner.etas", return_value=[])
    def test_no_bus_on_the_way_is_none(self, etas):
        self.assertIsNone(LiveData().official_minutes(TRIP))

    @mock.patch("history.predictions.predict_minutes", return_value=6.5)
    def test_our_prediction_is_the_one_the_pages_show(self, predict):
        self.assertEqual(LiveData().predicted_minutes(TRIP), 6.5)
        predict.assert_called_once_with(1, 20)  # the feed's countdown, corrected (#153)

    @mock.patch("crowding.services.crowding")
    def test_crowding_is_the_current_and_the_usual_level(self, crowding):
        typical = mock.Mock(label="Busy")
        crowding.return_value = [mock.Mock(current_label="Packed", typical_now=typical)]
        self.assertEqual(LiveData().crowding(TRIP), {"current": "Packed", "typical_now": "Busy"})
        crowding.return_value = [mock.Mock(current_label=None, typical_now=None)]
        self.assertEqual(LiveData().crowding(TRIP), {"current": None, "typical_now": None})


@override_settings(OPENROUTER_API_KEY="test-key", OPENROUTER_MODEL="test/model")
@mock.patch("assistant.openrouter.urlopen")
class ChatMessageTests(SimpleTestCase):
    def reply(self, message):
        return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())

    def test_tools_go_out_in_function_calling_format_and_the_whole_message_comes_back(self, urlopen):
        message = asks(call("x", "get_trip"))
        urlopen.return_value.__enter__.return_value = self.reply(message)
        got = openrouter.chat_message([{"role": "user", "content": "hi"}], tools=TOOLS)

        self.assertEqual(got, message)
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["model"], "test/model")
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual(body["tools"][0]["function"]["name"], "get_trip")
        self.assertEqual(body["tools"][0]["function"]["parameters"], TOOLS[0]["inputSchema"])
        self.assertNotIn("response_format", body)

    def test_without_tools_it_asks_for_a_json_answer(self, urlopen):
        urlopen.return_value.__enter__.return_value = self.reply({"content": "{}"})
        openrouter.chat_message([], tools=None)
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", body)

    def test_the_key_is_sent_as_a_header_never_in_the_body(self, urlopen):
        urlopen.return_value.__enter__.return_value = self.reply({"content": "{}"})
        openrouter.chat_message([{"role": "user", "content": "hi"}], tools=TOOLS)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertNotIn(b"test-key", request.data)

    def test_failures_become_one_error_that_never_shows_the_key(self, urlopen):
        for failure in (URLError("Bearer test-key rejected"), TimeoutError()):
            urlopen.side_effect = failure
            with self.assertRaises(openrouter.OpenRouterError) as caught:
                openrouter.chat_message([], tools=TOOLS)
            self.assertNotIn("test-key", str(caught.exception))
        urlopen.side_effect = None
        for body in (b"<html>", b"{}", b'{"choices": []}', b'{"choices": [{"message": "text"}]}'):
            urlopen.return_value.__enter__.return_value = io.BytesIO(body)
            with self.assertRaises(openrouter.OpenRouterError):
                openrouter.chat_message([], tools=TOOLS)

    @override_settings(OPENROUTER_API_KEY="")
    def test_no_key_means_no_request(self, urlopen):
        with self.assertRaises(openrouter.OpenRouterError):
            openrouter.chat_message([], tools=TOOLS)
        urlopen.assert_not_called()


class AgentTests(SimpleTestCase):
    def test_it_looks_things_up_then_answers(self):
        decision, chat = decide([LOOK_EVERYTHING_UP, answers(GOOD)])

        self.assertTrue(decision.notify)
        self.assertEqual(decision.notify_at, datetime(2026, 11, 16, 13, 35, tzinfo=timezone.utc))  # UTC
        self.assertEqual(decision.message, GOOD["message"])
        self.assertEqual(chat.call_count, 2)

    def test_the_model_is_given_the_tools_and_the_results_of_the_ones_it_called(self):
        decision, chat = decide([LOOK_EVERYTHING_UP, answers(GOOD)])

        first = chat.call_args_list[0]
        self.assertEqual(first.kwargs["tools"], TOOLS)
        self.assertEqual(first.args[0][0]["role"], "system")
        second_messages = chat.call_args_list[1].args[0]
        self.assertEqual([m["role"] for m in second_messages[2:]], ["assistant", "tool", "tool", "tool"])
        results = {m["tool_call_id"]: json.loads(m["content"]) for m in second_messages if m["role"] == "tool"}
        self.assertEqual(results["a"]["route"], "Blue")
        self.assertEqual(results["b"]["arrival"], "2026-11-16T08:45-05:00")
        self.assertEqual(results["c"]["leave_for_the_stop_by"], "2026-11-16T08:40-05:00")

    def test_a_no_is_a_no(self):
        decision, _ = decide([LOOK_EVERYTHING_UP, answers(QUIET)])
        self.assertEqual(decision, DONT_NOTIFY)

    def test_it_can_ask_for_more_in_a_second_round(self):
        replies = [asks(call("a", "get_trip")), asks(call("b", "get_official_arrival"), call("c", "get_crowding")),
                   answers(GOOD)]
        decision, chat = decide(replies, FakeData(crowding={"current": "Packed", "typical_now": None}))
        self.assertTrue(decision.notify)
        self.assertEqual(chat.call_count, 3)

    def test_a_notification_after_the_rider_has_to_leave_is_refused(self):
        # Our prediction (8:50) minus a 10-minute walk: 8:40. 8:41 is too late, though it's before 9:00.
        late = {**GOOD, "notify_at": "2026-11-16T08:41:00-05:00"}
        with self.assertLogs("assistant.agent", "WARNING") as logs:
            decision, _ = decide([LOOK_EVERYTHING_UP, answers(late)])
        self.assertEqual(decision, DONT_NOTIFY)
        self.assertIn("after the rider has to leave", logs.output[0])

    def test_leaving_exactly_on_time_is_fine(self):
        on_time = {**GOOD, "notify_at": "2026-11-16T08:40:00-05:00"}
        self.assertTrue(decide([LOOK_EVERYTHING_UP, answers(on_time)])[0].notify)

    def test_the_time_is_judged_against_what_the_tools_said_not_what_the_model_claims(self):
        # It looked up only the official time (8:45, leave by 8:35), so 8:38 is too late, whatever it says.
        only_official = asks(call("b", "get_official_arrival"))
        late = {**GOOD, "notify_at": "2026-11-16T08:38:00-05:00"}
        with self.assertLogs("assistant.agent", "WARNING"):
            self.assertEqual(decide([only_official, answers(late)])[0], DONT_NOTIFY)

    def test_without_an_arrival_time_it_never_notifies(self):
        cases = {
            "feed down and no prediction": (FakeData(down=True, predicted=None), [LOOK_EVERYTHING_UP, answers(GOOD)]),
            "no bus and no prediction": (FakeData(official=None, predicted=None), [LOOK_EVERYTHING_UP, answers(GOOD)]),
            "never looked anything up": (FakeData(), [answers(GOOD)]),
        }
        for name, (data, replies) in cases.items():
            with self.subTest(name), self.assertLogs("assistant.agent", "WARNING") as logs:
                self.assertEqual(decide(replies, data)[0], DONT_NOTIFY)
                self.assertIn("no tool gave an arrival time", logs.output[0])

    def test_with_the_feed_down_our_prediction_alone_is_enough(self):
        decision, _ = decide([LOOK_EVERYTHING_UP, answers(GOOD)], FakeData(down=True))
        self.assertTrue(decision.notify)

    def test_answers_that_dont_fit_count_as_dont_notify_and_are_logged(self):
        bad = {
            "not json": "Sure! Leave at 8:40.",
            "a list": "[]",
            "missing key": json.dumps({"notify": True, "message": "hi"}),
            "extra key": json.dumps({**GOOD, "why": "because"}),
            "notify as a string": json.dumps({**GOOD, "notify": "yes"}),
            "no message": json.dumps({**GOOD, "message": " "}),
            "message too long": json.dumps({**GOOD, "message": "x" * 161}),
            "time that isn't a time": json.dumps({**GOOD, "notify_at": "soon"}),
            "time without an offset": json.dumps({**GOOD, "notify_at": "2026-11-16T08:35:00"}),
            "after the rider must be there": json.dumps({**GOOD, "notify_at": "2026-11-16T09:30:00-05:00"}),
            "no content": None,
            "empty": "",
        }
        for name, answer in bad.items():
            # A reply that isn't JSON gets one more turn (see below), so give it the same answer twice.
            with self.subTest(name), self.assertLogs("assistant.agent", "WARNING"):
                self.assertEqual(decide([LOOK_EVERYTHING_UP, answers(answer), answers(answer)])[0], DONT_NOTIFY)

    def test_an_answer_in_prose_or_a_code_fence_gets_one_more_turn_with_tools_off(self):
        for first in ("Sure! Leave by 8:40.", "```json\n" + json.dumps(GOOD) + "\n```", None, ""):
            with self.subTest(first):
                decision, chat = decide([LOOK_EVERYTHING_UP, answers(first), answers(GOOD)])
                self.assertTrue(decision.notify)
                self.assertEqual(chat.call_count, 3)
                self.assertIsNone(chat.call_args_list[2].kwargs["tools"])  # so JSON mode is on
                nudge = chat.call_args_list[2].args[0][-1]
                self.assertEqual(nudge["role"], "user")
                self.assertIn("JSON", nudge["content"])

    def test_json_the_first_time_needs_no_second_turn(self):
        self.assertEqual(decide([LOOK_EVERYTHING_UP, answers(GOOD)])[1].call_count, 2)

    def test_the_log_shows_what_the_model_said_when_it_doesnt_fit(self):
        with self.assertLogs("assistant.agent", "WARNING") as logs:
            decide([LOOK_EVERYTHING_UP, answers("Sure! Leave by 8:40."), answers("Still prose.")])
        self.assertIn("not JSON", logs.output[0])
        self.assertIn("Still prose.", logs.output[0])

    def test_mistakes_with_tools_are_handed_back_and_it_can_recover(self):
        replies = [asks(call("a", "teleport"), call("b", "get_trip", "{oops"), call("c", "get_our_prediction")),
                   answers(GOOD)]
        decision, chat = decide(replies)
        self.assertTrue(decision.notify)
        errors = [json.loads(m["content"]) for m in chat.call_args_list[1].args[0] if m["role"] == "tool"]
        self.assertIn("no tool called", errors[0]["error"])
        self.assertIn("valid JSON", errors[1]["error"])

    def test_an_agent_that_never_stops_asking_is_cut_off_and_the_last_turn_has_no_tools(self):
        forever = [asks(call(str(i), "get_trip")) for i in range(agent.MAX_ROUNDS)]
        with self.assertLogs("assistant.agent", "WARNING"):
            decision, chat = decide(forever)
        self.assertEqual(decision, DONT_NOTIFY)
        self.assertEqual(chat.call_count, agent.MAX_ROUNDS)
        self.assertEqual([c.kwargs["tools"] is not None for c in chat.call_args_list],
                         [True] * (agent.MAX_ROUNDS - 1) + [False])

    def test_tool_calls_that_are_not_tool_calls_are_refused(self):
        malformed = {
            "not a list": {"role": "assistant", "content": None, "tool_calls": "get_trip"},
            "no id": asks({"type": "function", "function": {"name": "get_trip", "arguments": "{}"}}),
            "no function": asks({"id": "a"}),
            "name isn't text": asks(call("a", 7)),
            "a string": asks("get_trip"),
        }
        for name, reply in malformed.items():
            with self.subTest(name), self.assertLogs("assistant.agent", "WARNING"):
                self.assertEqual(decide([reply, answers(GOOD)])[0], DONT_NOTIFY)

    def test_arguments_the_model_sent_as_an_object_are_accepted(self):
        reply = asks({"id": "a", "type": "function", "function": {"name": "get_trip", "arguments": {}}})
        decision, chat = decide([reply, asks(call("b", "get_our_prediction")), answers(GOOD)])
        self.assertTrue(decision.notify)

    def test_too_many_calls_at_once_are_answered_but_not_run(self):
        many = asks(*[call(str(i), "get_trip") for i in range(agent.MAX_TOOL_CALLS_PER_ROUND + 2)],
                    )
        decision, chat = decide([many, LOOK_EVERYTHING_UP, answers(GOOD)])
        sent = [m for m in chat.call_args_list[1].args[0] if m["role"] == "tool"]
        self.assertEqual(len(sent), agent.MAX_TOOL_CALLS_PER_ROUND + 2)  # every call has a result
        self.assertIn("Too many", sent[-1]["content"])
        self.assertTrue(decision.notify)

    def test_openrouter_trouble_counts_as_dont_notify_and_is_logged(self):
        for failure in (openrouter.OpenRouterError("down"), openrouter.OpenRouterError("timed out")):
            with self.subTest(str(failure)), self.assertLogs("assistant.agent", "WARNING"):
                self.assertEqual(decide([failure])[0], DONT_NOTIFY)

    def test_trouble_in_the_middle_of_the_conversation_too(self):
        with self.assertLogs("assistant.agent", "WARNING"):
            decision, _ = decide([LOOK_EVERYTHING_UP, openrouter.OpenRouterError("timed out")])
        self.assertEqual(decision, DONT_NOTIFY)

    def test_strict_mode_lets_an_outage_through_instead_of_hiding_it(self):
        with self.assertRaises(openrouter.OpenRouterError):
            decide([openrouter.OpenRouterError("down")], strict=True)

    def test_a_conversation_that_takes_too_long_is_stopped(self):
        clock = iter([0, 1, agent.TOTAL_SECONDS + 1, agent.TOTAL_SECONDS + 2])
        with mock.patch("assistant.agent.monotonic", side_effect=lambda: next(clock)), \
                self.assertLogs("assistant.agent", "WARNING") as logs:
            decision, chat = decide([LOOK_EVERYTHING_UP, answers(GOOD)])
        self.assertEqual(decision, DONT_NOTIFY)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("ran out of time", logs.output[0])

    def test_strict_mode_reports_a_timeout_too(self):
        clock = iter([0, 1, agent.TOTAL_SECONDS + 1])
        with mock.patch("assistant.agent.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(openrouter.OpenRouterError, "ran out of time"):
                decide([LOOK_EVERYTHING_UP, answers(GOOD)], strict=True)

    def test_the_real_request_carries_the_tools_and_the_key_stays_in_the_header(self):
        # End to end through openrouter.chat_message, with only the network mocked.
        replies = [asks(call("a", "get_official_arrival")), answers(QUIET)]
        bodies = iter(json.dumps({"choices": [{"message": reply}]}).encode() for reply in replies)

        def urlopen(request, timeout):
            response = mock.MagicMock()
            response.__enter__.return_value = io.BytesIO(next(bodies))
            return response

        with override_settings(OPENROUTER_API_KEY="test-key"), \
                mock.patch("assistant.openrouter.urlopen", side_effect=urlopen) as network:
            decision = agent.decide_with_tools(TRIP, FakeData(), now=NOW)
        self.assertEqual(decision, DONT_NOTIFY)
        self.assertEqual(network.call_count, 2)
        second = json.loads(network.call_args.args[0].data)
        self.assertEqual(second["messages"][-1]["role"], "tool")
        self.assertNotIn("test-key", json.dumps(second))


class AgentEvaluationTests(SimpleTestCase):
    """The #37 evaluation set, run through the agent: each case's good answer is
    what the mocked model says after it has looked the trip up."""

    def test_every_case_passes_with_the_mocked_model(self):
        for case in evals.CASES:
            with self.subTest(case["name"]):
                replies = [LOOK_EVERYTHING_UP, answers(case["mock_reply"])]
                with mock.patch("assistant.agent.openrouter.chat_message", side_effect=replies) as chat:
                    decision = evals.agent_decide()(case["trip"])
                ok, why = evals.check(case, decision)
                self.assertTrue(ok, why)
                self.assertEqual(chat.call_count, 2)

    def test_the_tools_give_each_case_the_arrival_times_in_its_trip(self):
        case = evals.CASES[1]  # bus at 8:45 official, 8:50 ours, 10 minute walk
        replies = [LOOK_EVERYTHING_UP, answers(case["mock_reply"])]
        with mock.patch("assistant.agent.openrouter.chat_message", side_effect=replies) as chat:
            evals.agent_decide()(case["trip"])
        results = {m["tool_call_id"]: json.loads(m["content"])
                   for m in chat.call_args_list[1].args[0] if m["role"] == "tool"}
        self.assertEqual(results["b"]["arrival"], "2026-11-16T08:45-05:00")
        self.assertEqual(results["c"]["arrival"], "2026-11-16T08:50-05:00")
        self.assertEqual(results["c"]["leave_for_the_stop_by"], "2026-11-16T08:40-05:00")

    def test_a_case_with_no_prediction_says_so(self):
        case = next(c for c in evals.CASES if c["trip"].predicted_arrival is None)
        replies = [LOOK_EVERYTHING_UP, answers(case["mock_reply"])]
        with mock.patch("assistant.agent.openrouter.chat_message", side_effect=replies) as chat:
            evals.agent_decide()(case["trip"])
        results = {m["tool_call_id"]: json.loads(m["content"])
                   for m in chat.call_args_list[1].args[0] if m["role"] == "tool"}
        self.assertIs(results["c"]["available"], False)


class EvaluateAgentCommandTests(SimpleTestCase):
    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_it_scores_every_case_through_the_agent(self):
        replies = iter(reply for case in evals.CASES for reply in (LOOK_EVERYTHING_UP, answers(case["mock_reply"])))
        out = io.StringIO()
        with mock.patch("assistant.agent.openrouter.chat_message", side_effect=lambda *a, **k: next(replies)):
            call_command("evaluate_assistant", "--agent", stdout=out)
        self.assertIn("agent with tools", out.getvalue())
        self.assertIn(f"{len(evals.CASES)}/{len(evals.CASES)} passed", out.getvalue())

    @override_settings(OPENROUTER_API_KEY="test-key")
    def test_an_unreachable_model_is_an_error_not_a_score(self):
        out = io.StringIO()
        with mock.patch("assistant.openrouter.urlopen", side_effect=URLError("no route")):
            with self.assertRaisesRegex(CommandError, "nothing was evaluated"):
                call_command("evaluate_assistant", "--agent", stdout=out)
        self.assertNotIn("PASS", out.getvalue())

    @override_settings(OPENROUTER_API_KEY="")
    def test_it_needs_a_key(self):
        with mock.patch("assistant.openrouter.urlopen") as urlopen:
            with self.assertRaises(CommandError):
                call_command("evaluate_assistant", "--agent", stdout=io.StringIO())
        urlopen.assert_not_called()


class ReportInvalidTests(SimpleTestCase):
    """Asked from the when-to-leave page (#148), a rider is told when the agent
    couldn't answer, rather than shown a "no" it never gave."""

    NOT_JSON = [LOOK_EVERYTHING_UP, answers("Leave soon!"), answers("Leave soon!")]

    def test_an_answer_that_doesnt_fit_raises_with_report_invalid(self):
        with self.assertLogs("assistant.agent", "WARNING"), self.assertRaises(InvalidAnswer):
            decide(self.NOT_JSON, report_invalid=True)

    def test_without_it_the_same_answer_still_counts_as_dont_notify(self):
        with self.assertLogs("assistant.agent", "WARNING"):
            decision, _ = decide(self.NOT_JSON)
        self.assertEqual(decision, DONT_NOTIFY)
