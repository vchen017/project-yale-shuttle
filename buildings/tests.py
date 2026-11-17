"""Finding a Yale building by its everyday name (issue #98). Photon and the
shuttle feed are mocked, so no test reaches the network."""

import json
from unittest import mock
from urllib.error import URLError

from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse

from transit import places
from transit.downtowner import FeedUnavailable

from . import search

ESH = "Edward S. Harkness Memorial Hall"
WLH = "William L. Harkness Hall"
GROVE = {"id": 20, "name": "Grove Street", "lat": 41.313, "lon": -72.925, "routes": [1], "url": "/stops/20/"}
HARKNESS_STOP = {"id": 21, "name": "Harkness Tower", "lat": 41.3108, "lon": -72.9272, "routes": [1], "url": "/stops/21/"}


def names(query):
    return [place["name"] for place in search.search(query)]


class FindingABuildingTests(SimpleTestCase):
    def test_the_way_riders_say_it_finds_the_official_name(self):
        for typed in ("es harkness", "es harkness hall", "ES Harkness", "e.s. harkness", "e s harkness", "es hark", "  es   harkness  "):
            with self.subTest(typed):
                self.assertEqual(names(typed), [ESH])

    def test_a_shortened_name_finds_every_building_it_could_mean_closest_first(self):
        self.assertEqual(names("harkness"), [WLH, ESH])
        self.assertEqual(names("Harkness Hall"), [WLH, ESH])  # "hall" is filler

    def test_the_other_harkness_isnt_suggested_for_the_es_one(self):
        self.assertNotIn(WLH, names("es harkness"))

    def test_abbreviations_find_their_building_first(self):
        self.assertEqual(names("SSS"), ["Sheffield-Sterling-Strathcona Hall"])
        self.assertEqual(names("esh"), [ESH])
        self.assertEqual(names("SML"), ["Sterling Memorial Library"])
        self.assertEqual(names("es")[0], "Ezra Stiles College")  # ES is Ezra Stiles's abbreviation

    def test_punctuation_and_capitals_dont_matter(self):
        self.assertEqual(names("LINSLY-CHITTENDEN"), ["Linsly-Chittenden Hall"])
        self.assertEqual(names("linsly chittenden"), ["Linsly-Chittenden Hall"])
        self.assertEqual(names("Branford COLLEGE"), ["Branford College"])

    def test_every_word_has_to_match(self):
        self.assertEqual(names("kline tower"), ["Kline Tower"])
        self.assertEqual(names("kline zebra"), [])

    def test_too_short_or_only_filler_finds_nothing(self):
        for typed in ("", " ", "e", "hall", "the building", "bldg"):
            with self.subTest(typed):
                self.assertEqual(names(typed), [])

    def test_a_building_we_dont_have_finds_nothing(self):
        self.assertEqual(names("Woolsey"), [])

    def test_the_name_most_people_mean_comes_first(self):
        """Find my bus plans to the top suggestion, so "sterling" must not mean the medical school (review of #98)."""
        self.assertEqual(names("sterling")[0], "Sterling Memorial Library")
        self.assertEqual(names("bass"), ["Bass Library", "Bass Center for Molecular and Structural Biology"])
        self.assertEqual(names("bass center"), ["Bass Center for Molecular and Structural Biology"])
        self.assertEqual(names("sterling library"), ["Sterling Memorial Library"])

    def test_a_short_name_beats_an_abbreviation_which_beats_a_partial_match(self):
        # "bass": Bass Library's short name, then Bass Center's abbreviation (BASS).
        self.assertEqual(names("BASS"), ["Bass Library", "Bass Center for Molecular and Structural Biology"])
        # "ES": Ezra Stiles's abbreviation, then buildings whose name only starts that way.
        self.assertEqual(names("es")[0], "Ezra Stiles College")

    def test_also_known_as_names_find_the_building_under_its_official_name(self):
        self.assertEqual(names("lawrence hall"), ["Lawrance Hall"])  # OpenStreetMap's spelling
        self.assertEqual(names("university theater"), ["University Theatre"])
        self.assertEqual(names("hopper"), ["Grace Hopper College"])

    def test_every_sterling_building_is_listed_with_the_library_first(self):
        found = names("sterling")
        self.assertEqual(found[0], "Sterling Memorial Library")
        self.assertEqual(
            set(found),
            {
                "Sterling Memorial Library", "Sterling Hall of Medicine", "Sterling Law Building",
                "Sheffield-Sterling-Strathcona Hall", "Sterling Chemistry Laboratory", "Sterling Divinity Quadrangle",
            },
        )

    def test_a_broad_word_is_capped_so_the_list_stays_short(self):
        self.assertEqual(len(names("college")), search.MOST_BUILDINGS)  # 14 match

    def test_a_result_is_a_place_like_a_stop_or_a_photon_result(self):
        [place] = search.search("esh")
        self.assertEqual(place, {"name": ESH, "detail": "ESH · 367 Cedar Street", "lat": 41.30439, "lon": -72.93409})

    def test_one_letter_abbreviations_arent_shown_as_the_detail(self):
        [place] = search.search("connecticut hall")
        self.assertEqual(place["name"], "Connecticut Hall")
        self.assertNotIn("C ·", place["detail"])

    def test_a_building_with_no_street_still_says_where_it_is(self):
        [place] = search.search("sml")
        self.assertEqual(place["detail"], "SML · Yale University")
        [place] = search.search("welch")
        self.assertEqual(place["detail"], "Yale University")  # a one-letter abbreviation isn't shown


class TheDataTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = json.loads(search.DATA.read_text(encoding="utf-8"))
        cls.buildings = cls.data["buildings"]

    def test_every_building_has_what_matching_and_the_trip_search_need(self):
        for building in self.buildings:
            with self.subTest(building["name"]):
                self.assertTrue(building["abbr"] and building["name"])
                self.assertIn(building["source"], self.data["sources"])
                self.assertIsInstance(building["street"], str)

    def test_names_and_abbreviations_are_unique(self):
        self.assertEqual(len({b["name"].casefold() for b in self.buildings}), len(self.buildings))
        self.assertEqual(len({b["abbr"].casefold() for b in self.buildings}), len(self.buildings))

    def test_every_building_is_on_or_near_campus(self):
        """A wrong match elsewhere (another Connecticut Hall, say) would send a rider miles away."""
        for building in self.buildings:
            with self.subTest(building["name"]):
                self.assertTrue(41.29 <= building["lat"] <= 41.33 and -72.945 <= building["lon"] <= -72.91)

    def test_every_also_known_as_name_is_a_different_name(self):
        for building in self.buildings:
            for other in building.get("also_known_as", []) + building.get("short_names", []):
                with self.subTest(building["name"], other=other):
                    self.assertNotEqual(search.normalize(other), search.normalize(building["name"]))

    def test_a_short_name_belongs_to_only_one_building(self):
        short = [normalize for b in self.buildings for normalize in (search.normalize(n) for n in b.get("short_names", []))]
        self.assertEqual(len(short), len(set(short)))
        self.assertEqual(set(short), {"sterling", "bass"})  # a team decision (review of #98): don't add more without asking

    def test_the_sources_are_cited(self):
        for key in ("college", "medicine", "coordinates"):
            self.assertTrue(self.data["sources"][key]["url"].startswith("https://"))

    def test_the_well_known_buildings_are_there(self):
        official = {b["name"] for b in self.buildings}
        for name in (ESH, WLH, "Sterling Memorial Library", "Beinecke Rare Book and Manuscript Library", "Payne Whitney Gymnasium"):
            self.assertIn(name, official)


def photon_answering(features):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({"features": features}).encode()
    return mock.Mock(return_value=response)


PHOTON_STERLING = [{"geometry": {"coordinates": [-72.9290, 41.3112]}, "properties": {"name": "Sterling Library", "street": "York Street", "city": "New Haven"}}]


class InTheTripSearchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("transit.places.urlopen")
    @mock.patch("transit.views.served_stops", return_value=[GROVE])
    def test_the_suggestions_include_buildings_without_asking_photon(self, served_stops, urlopen):
        response = self.client.get(reverse("places"), {"q": "es harkness"})
        self.assertEqual([place["name"] for place in response.json()["places"]], [ESH])
        urlopen.assert_not_called()  # the server never asks Photon for suggestions

    @mock.patch("transit.views.served_stops", return_value=[GROVE, HARKNESS_STOP])
    def test_our_stops_come_before_our_buildings(self, served_stops):
        found = [place["name"] for place in self.client.get(reverse("places"), {"q": "harkness"}).json()["places"]]
        self.assertEqual(found, ["Harkness Tower", WLH, ESH])

    @mock.patch("transit.views.served_stops", side_effect=FeedUnavailable("down"))
    def test_buildings_still_suggest_when_the_shuttle_feed_is_down(self, served_stops):
        found = self.client.get(reverse("places"), {"q": "esh"}).json()["places"]
        self.assertEqual([place["name"] for place in found], [ESH])

    @mock.patch("transit.places.urlopen", new_callable=lambda: photon_answering(PHOTON_STERLING))
    def test_a_typed_name_without_javascript_finds_our_building_before_photons(self, urlopen):
        found = places.search("sterling", [GROVE])
        self.assertEqual(found[0]["name"], "Sterling Memorial Library")  # Find my bus takes the top one
        self.assertEqual(found[-1]["name"], "Sterling Library")  # Photon's results come last

    @mock.patch("transit.places.urlopen", side_effect=URLError("down"))
    def test_buildings_still_suggest_when_photon_is_down(self, urlopen):
        with self.assertLogs("transit.places", "WARNING"):
            found = places.search("es harkness", [GROVE])
        self.assertEqual([place["name"] for place in found], [ESH])

    @mock.patch("transit.places.urlopen", new_callable=lambda: photon_answering([]))
    def test_picking_a_building_gives_the_trip_search_its_coordinates(self, urlopen):
        [place] = places.search("esh", [GROVE])
        self.assertEqual((place["lat"], place["lon"]), (41.30439, -72.93409))
        self.assertIn("367 Cedar Street", place["detail"])
