"""predict_minutes with and without the live feed (#83). In its own file, so it
doesn't collide with other pull requests that add to history/tests.py."""

from unittest import mock

from django.test import TestCase

from history import predictions

FEED = [{"route_id": 1, "bus_id": 7, "bus_name": "#7", "minutes": 3}]


@mock.patch("history.predictions._stop_is_served", return_value=True)
@mock.patch("history.predictions.downtowner.etas", return_value=FEED)
class PredictionSourceTests(TestCase):
    def test_by_default_the_live_feed_answers_first(self, etas, served):
        self.assertEqual(predictions.predict_minutes(1, 10), 3.0)
        etas.assert_called_once_with(10)

    def test_without_the_feed_only_our_own_data_answers(self, etas, served):
        # An empty database: no recorded polls, no history, so no prediction,
        # rather than the feed's 3 minutes presented as ours.
        self.assertIsNone(predictions.predict_minutes(1, 10, use_live_feed=False))
        etas.assert_not_called()
