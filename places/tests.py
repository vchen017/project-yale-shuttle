"""Saved places (#7): adding, renaming, moving and deleting them, one Home,
and each rider seeing only their own, here and in the trip search.

The Downtowner feed is mocked at transit.downtowner.urlopen and Photon at
transit.places.urlopen, so no test reaches the network."""

from unittest import mock
from urllib.error import URLError

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from transit.test_next_buses import feed
from transit.test_trips import photon

from . import services
from .models import Place

PICKED = {"location": "Sterling Library, York Street, New Haven", "location_at": "41.311200,-72.929000"}


def place(user, name, lat=41.31, lon=-72.92, **fields):
    return Place.objects.create(user=user, name=name, lat=lat, lon=lon, **fields)


class PlacesTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.feed_urlopen = self.mock("transit.downtowner.urlopen", side_effect=feed())
        self.photon_urlopen = self.mock("transit.places.urlopen", new=photon())
        User = get_user_model()
        self.rider = User.objects.create_user("ana", "ana@example.org", "a-long-password-1")
        self.other = User.objects.create_user("ben", "ben@example.org", "a-long-password-2")
        self.client.force_login(self.rider)

    def mock(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def add(self, name, **fields):
        return self.client.post(reverse("my-places"), {"name": name, **PICKED, **fields})

    def names(self, user=None):
        return [p.name for p in (user or self.rider).places.all()]


class AddingTests(PlacesTestCase):
    def test_a_picked_place_is_saved_with_its_name_and_location(self):
        response = self.add("  Library   study ")
        self.assertRedirects(response, reverse("my-places"), fetch_redirect_response=False)
        saved = self.rider.places.get()
        self.assertEqual(saved.name, "Library study")
        self.assertEqual(saved.address, "Sterling Library, York Street, New Haven")
        self.assertEqual((saved.lat, saved.lon), (41.3112, -72.929))
        self.assertFalse(saved.is_home)
        self.assertContains(self.client.get(reverse("my-places")), "Saved Library study.")
        self.photon_urlopen.assert_not_called()  # a picked place needs no search

    def test_a_typed_stop_is_looked_up(self):
        self.add("Lab", location="science hill", location_at="")
        saved = self.rider.places.get()
        self.assertEqual(saved.address, "Science Hill (shuttle stop)")
        self.assertEqual((saved.lat, saved.lon), (41.316, -72.922))

    def test_a_typed_address_is_looked_up_with_photon(self):
        self.add("Library", location="sterling library", location_at="")
        saved = self.rider.places.get()
        self.assertEqual(saved.address, "Sterling Library, York Street, New Haven")
        self.assertEqual((saved.lat, saved.lon), (41.3112, -72.929))

    def test_an_address_is_still_found_when_the_shuttle_feed_is_down(self):
        self.feed_urlopen.side_effect = URLError("down")
        self.add("Library", location="sterling library", location_at="")
        self.assertEqual(self.names(), ["Library"])

    def test_a_place_we_cant_find_says_so_and_saves_nothing(self):
        self.photon_urlopen.return_value.read.return_value = b'{"features": []}'
        response = self.add("Nowhere", location="Nowhere Lane", location_at="")
        self.assertContains(
            response,
            "We couldn't find “Nowhere Lane” near New Haven. Try a building, a street address or a stop.",
            status_code=400,
            html=True,
        )
        self.assertEqual(self.names(), [])

    def test_coordinates_that_arent_a_place_are_looked_up_instead(self):
        self.add("Lab", location="science hill", location_at="999,0")
        self.assertEqual(self.rider.places.get().lat, 41.316)

    def test_a_name_and_a_location_are_needed(self):
        response = self.client.post(reverse("my-places"), {"name": "   ", "location": "", "location_at": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.context["form"].errors), {"name", "location"})
        self.assertEqual(self.names(), [])

    def test_two_places_cant_share_a_name(self):
        place(self.rider, "Gym")
        response = self.add("gym")
        self.assertContains(response, "You already have a place called “gym”. Pick another name.", status_code=400, html=True)
        self.assertEqual(self.names(), ["Gym"])

    def test_another_rider_can_use_the_same_name(self):
        place(self.other, "Gym")
        self.add("Gym")
        self.assertEqual(self.names(), ["Gym"])

    def test_a_rider_can_save_up_to_twenty_places(self):
        for n in range(services.MOST_PLACES):
            place(self.rider, f"Place {n}")
        response = self.add("One more")
        self.assertContains(response, "You can save up to 20 places.", status_code=400)
        self.assertEqual(self.rider.places.count(), services.MOST_PLACES)

    def test_the_page_lists_them_home_first_then_by_name(self):
        place(self.rider, "zoo")
        place(self.rider, "Apartment", is_home=True, address="1 Elm St")
        place(self.rider, "Gym")
        response = self.client.get(reverse("my-places"))
        self.assertEqual([p.name for p in response.context["places"]], ["Apartment", "Gym", "zoo"])
        self.assertContains(response, "1 Elm St")
        self.assertContains(response, "Saved places · 3 of 20")
        self.assertContains(response, "places/place-picker.js")

    def test_with_none_yet_it_says_how_to_start(self):
        self.assertContains(self.client.get(reverse("my-places")), "No saved places yet. Add your Home to start.")


class RenamingAndDeletingTests(PlacesTestCase):
    def test_renaming_keeps_the_location(self):
        gym = place(self.rider, "Gym", lat=41.3, lon=-72.9, address="Payne Whitney Gym")
        edit = self.client.get(reverse("place-edit", args=[gym.pk]))
        self.assertContains(edit, 'value="Payne Whitney Gym"')
        self.assertContains(edit, 'value="41.300000,-72.900000"')

        response = self.client.post(
            reverse("place-edit", args=[gym.pk]),
            {"name": "Workout", "location": "Payne Whitney Gym", "location_at": "41.300000,-72.900000"},
        )
        self.assertRedirects(response, reverse("my-places"), fetch_redirect_response=False)
        gym.refresh_from_db()
        self.assertEqual((gym.name, gym.address, gym.lat, gym.lon), ("Workout", "Payne Whitney Gym", 41.3, -72.9))
        self.assertContains(self.client.get(reverse("my-places")), "Saved Workout (it was Gym).")

    def test_changing_only_the_capitals_of_a_name_is_allowed(self):
        gym = place(self.rider, "gym")
        self.client.post(reverse("place-edit", args=[gym.pk]), {"name": "Gym", **PICKED})
        gym.refresh_from_db()
        self.assertEqual(gym.name, "Gym")

    def test_a_place_can_move(self):
        gym = place(self.rider, "Gym")
        self.client.post(reverse("place-edit", args=[gym.pk]), {"name": "Gym", "location": "science hill", "location_at": ""})
        gym.refresh_from_db()
        self.assertEqual((gym.address, gym.lat), ("Science Hill (shuttle stop)", 41.316))

    def test_a_new_address_typed_without_javascript_doesnt_keep_the_old_pin(self):
        # The edit form starts with the saved pin in location_at, and without
        # the script nothing clears it when the rider types a new address.
        gym = place(self.rider, "Gym", lat=41.3, lon=-72.9, address="Payne Whitney Gym")
        self.client.post(
            reverse("place-edit", args=[gym.pk]),
            {"name": "Gym", "location": "science hill", "location_at": "41.300000,-72.900000"},
        )
        gym.refresh_from_db()
        self.assertEqual((gym.address, gym.lat, gym.lon), ("Science Hill (shuttle stop)", 41.316, -72.922))

    def test_a_new_pick_keeps_its_own_pin(self):
        gym = place(self.rider, "Gym", lat=41.3, lon=-72.9, address="Payne Whitney Gym")
        self.client.post(reverse("place-edit", args=[gym.pk]), {"name": "Gym", **PICKED})
        gym.refresh_from_db()
        self.assertEqual((gym.address, gym.lat, gym.lon), ("Sterling Library, York Street, New Haven", 41.3112, -72.929))
        self.photon_urlopen.assert_not_called()  # the pick needed no lookup

    def test_renaming_to_another_places_name_is_refused(self):
        place(self.rider, "Work")
        gym = place(self.rider, "Gym")
        response = self.client.post(reverse("place-edit", args=[gym.pk]), {"name": "WORK", **PICKED})
        self.assertContains(response, "You already have a place called “WORK”.")
        self.assertContains(response, "Change Gym")
        gym.refresh_from_db()
        self.assertEqual(gym.name, "Gym")

    def test_deleting_asks_first(self):
        gym = place(self.rider, "Gym")
        response = self.client.get(reverse("place-delete", args=[gym.pk]))
        self.assertContains(response, "Delete Gym?")
        self.assertTrue(Place.objects.filter(pk=gym.pk).exists())

        response = self.client.post(reverse("place-delete", args=[gym.pk]))
        self.assertRedirects(response, reverse("my-places"), fetch_redirect_response=False)
        self.assertFalse(Place.objects.filter(pk=gym.pk).exists())
        self.assertContains(self.client.get(reverse("my-places")), "Deleted Gym.")

    def test_deleting_home_says_youll_have_none(self):
        home = place(self.rider, "Apartment", is_home=True)
        self.assertContains(self.client.get(reverse("place-delete", args=[home.pk])), "It's your Home")


class HomeTests(PlacesTestCase):
    def test_a_new_place_can_be_home(self):
        self.add("Apartment", is_home="on")
        self.assertTrue(self.rider.places.get().is_home)

    def test_marking_another_place_home_moves_it(self):
        place(self.rider, "Old flat", is_home=True)
        self.add("New flat", is_home="on")
        self.assertEqual(list(self.rider.places.filter(is_home=True).values_list("name", flat=True)), ["New flat"])

    def test_make_home_moves_it(self):
        old = place(self.rider, "Old flat", is_home=True)
        new = place(self.rider, "New flat")
        response = self.client.post(reverse("place-home", args=[new.pk]))
        self.assertRedirects(response, reverse("my-places"), fetch_redirect_response=False)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual((old.is_home, new.is_home), (False, True))
        self.assertContains(self.client.get(reverse("my-places")), "New flat is now your Home.")

    def test_make_home_needs_a_post(self):
        gym = place(self.rider, "Gym")
        self.assertEqual(self.client.get(reverse("place-home", args=[gym.pk])).status_code, 405)

    def test_unticking_home_leaves_no_home(self):
        home = place(self.rider, "Apartment", is_home=True)
        self.client.post(reverse("place-edit", args=[home.pk]), {"name": "Apartment", **PICKED})
        home.refresh_from_db()
        self.assertFalse(home.is_home)

    def test_one_riders_home_leaves_anothers_alone(self):
        theirs = place(self.other, "Their flat", is_home=True)
        self.add("My flat", is_home="on")
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_home)

    def test_the_database_refuses_two_homes(self):
        place(self.rider, "One", is_home=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            place(self.rider, "Two", is_home=True)

    def test_the_database_refuses_two_places_with_one_name(self):
        place(self.rider, "Gym")
        with self.assertRaises(services.NameTaken):
            services.save(self.rider, Place(name="GYM", lat=41.3, lon=-72.9))


class OnlyYourOwnTests(PlacesTestCase):
    def test_logging_in_is_needed(self):
        mine = place(self.rider, "Gym")
        self.client.logout()
        for url in [
            reverse("my-places"),
            reverse("place-edit", args=[mine.pk]),
            reverse("place-delete", args=[mine.pk]),
            reverse("place-home", args=[mine.pk]),
        ]:
            response = self.client.post(url, {"name": "Taken over", **PICKED})
            self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)
        self.assertEqual(self.names(), ["Gym"])

    def test_the_list_shows_only_your_places(self):
        place(self.rider, "My gym")
        place(self.other, "Their flat", address="9 Secret St")
        response = self.client.get(reverse("my-places"))
        self.assertContains(response, "My gym")
        self.assertNotContains(response, "Their flat")
        self.assertNotContains(response, "9 Secret St")

    def test_someone_elses_place_is_not_found(self):
        theirs = place(self.other, "Their flat")
        for name in ["place-edit", "place-delete"]:
            self.assertEqual(self.client.get(reverse(name, args=[theirs.pk])).status_code, 404)
        for name in ["place-edit", "place-delete", "place-home"]:
            self.assertEqual(self.client.post(reverse(name, args=[theirs.pk]), {"name": "Mine", **PICKED}).status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual((theirs.name, theirs.is_home), ("Their flat", False))

    def test_places_arent_in_the_admin(self):
        self.assertNotIn(Place, admin.site._registry)  # a home address is personal

    def test_deleting_an_account_deletes_its_places(self):
        place(self.rider, "Gym")
        self.rider.delete()
        self.assertFalse(Place.objects.exists())


class TripSearchTests(PlacesTestCase):
    """Saved places come up first in the trip search (story #5)."""

    def suggest(self, q, **query):
        return self.client.get(reverse("places"), {"q": q, **query})

    def test_an_empty_box_offers_all_your_places_home_first(self):
        place(self.rider, "Gym", address="Payne Whitney Gym")
        place(self.rider, "Apartment", is_home=True, address="1 Elm St", lat=41.30, lon=-72.93)
        place(self.other, "Their flat")
        response = self.suggest("", mine=1)
        self.assertEqual(
            response.json()["places"],
            [
                {"name": "Apartment", "detail": "Home · 1 Elm St", "lat": 41.30, "lon": -72.93, "saved": True, "home": True},
                {"name": "Gym", "detail": "Payne Whitney Gym", "lat": 41.31, "lon": -72.92, "saved": True, "home": False},
            ],
        )
        self.assertEqual(response["Cache-Control"], "private, no-cache")

    def test_your_matching_places_come_before_stops(self):
        place(self.rider, "Union office")
        names = [p["name"] for p in self.suggest("union", mine=1).json()["places"]]
        self.assertEqual(names, ["Union office", "Union Station"])

    def test_a_place_matches_on_its_address_too(self):
        place(self.rider, "Gym", address="Payne Whitney Gym")
        self.assertEqual([p["name"] for p in self.suggest("whitney", mine=1).json()["places"]], ["Gym", "Payne Whitney"])

    def test_saved_places_need_asking_for(self):
        place(self.rider, "Union office")
        response = self.suggest("union")
        self.assertEqual([p["name"] for p in response.json()["places"]], ["Union Station"])
        self.assertEqual(response["Cache-Control"], "max-age=300")

    def test_nobody_logged_in_gets_no_saved_places(self):
        place(self.rider, "Union office")
        self.client.logout()
        self.assertEqual([p["name"] for p in self.suggest("union", mine=1).json()["places"]], ["Union Station"])

    def test_saved_places_come_up_when_the_feed_is_down(self):
        self.feed_urlopen.side_effect = URLError("down")
        place(self.rider, "Union office")
        self.assertEqual([p["name"] for p in self.suggest("union", mine=1).json()["places"]], ["Union office"])

    def test_a_trip_typed_as_a_saved_name_uses_that_place(self):
        place(self.rider, "Apartment", is_home=True, lat=41.3140, lon=-72.9260)
        with mock.patch("transit.next_buses.predict_minutes", return_value=None):
            response = self.client.get(reverse("trip"), {"from": "apartment", "to": "union"})
        self.assertContains(response, "Apartment to Union Station")
        self.assertEqual((response.context["origin"]["lat"], response.context["origin"]["lon"]), (41.3140, -72.9260))
        self.assertEqual(response.context["plan"].board_at["id"], 20)

    def test_someone_elses_place_name_isnt_used(self):
        place(self.other, "Their flat", lat=41.3140, lon=-72.9260)
        self.photon_urlopen.return_value.read.return_value = b'{"features": []}'
        response = self.client.get(reverse("trip"), {"from": "their flat", "to": "union"})
        self.assertContains(response, "find “their flat” near New Haven.")

    def test_the_search_asks_for_saved_places_once_logged_in(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'data-places-url="/places.json?mine=1"')
        self.assertContains(response, 'data-saved-places="yes"')
        self.assertContains(response, f'href="{reverse("my-places")}"')
        self.client.logout()
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'data-places-url="/places.json"')
        self.assertContains(response, 'data-saved-places="no"')
        self.assertNotContains(response, "My places")
