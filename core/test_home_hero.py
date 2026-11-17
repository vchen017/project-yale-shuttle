"""The home page's hero has no second set of account buttons: logging in and
signing up live in the navbar, on every page (#86). In its own file, so it
doesn't collide with other pull requests that add to core/tests.py."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class HeroTests(TestCase):
    def test_a_visitor_finds_log_in_and_sign_up_once_in_the_navbar(self):
        response = self.client.get("/")
        self.assertNotContains(response, "Create an account")
        self.assertContains(response, 'href="/login/"', count=1)
        self.assertContains(response, 'href="/signup/"', count=1)
        self.assertContains(response, '<a class="nav-link" href="/login/">Log in</a>', html=True)
        self.assertContains(response, '<a class="nav-link" href="/signup/">Sign up</a>', html=True)

    def test_the_navbar_marks_the_page_youre_on(self):
        response = self.client.get(reverse("login"))
        self.assertContains(
            response, '<a class="nav-link active" aria-current="page" href="/login/">Log in</a>', html=True
        )
        self.assertContains(response, '<a class="nav-link" href="/signup/">Sign up</a>', html=True)

    def test_a_rider_is_welcomed_without_the_admin_links(self):
        self.client.force_login(get_user_model().objects.create_user("rider@example.com"))
        response = self.client.get("/")
        self.assertContains(response, "Welcome back, <strong>rider@example.com</strong>")
        self.assertNotContains(response, "Open the admin")

    def test_staff_are_welcomed_with_the_admin_links(self):
        self.client.force_login(get_user_model().objects.create_user("admin@example.com", is_staff=True))
        response = self.client.get("/")
        self.assertContains(response, "Welcome back, <strong>admin@example.com</strong>")
        self.assertContains(response, "Open the admin")
        self.assertContains(response, "Post an alert")

    def test_the_navbar_welcomes_a_rider_on_every_page(self):
        self.client.force_login(get_user_model().objects.create_user("rider@example.com"))
        response = self.client.get(reverse("my-places"))
        self.assertContains(response, "Welcome back, <strong>rider@example.com</strong>")
        self.assertContains(response, '<li class="nav-item navbar-section d-xl-none">Account</li>', html=True)
