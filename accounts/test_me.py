"""The tab bar's "Me" page (#164). Each of its links is also in the top
menu, so a link the page adds shows up twice."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class MePageTests(TestCase):
    def test_a_rider_gets_their_own_pages_and_log_out(self):
        self.client.force_login(get_user_model().objects.create_user("rider@example.com"))
        response = self.client.get(reverse("me"))
        self.assertContains(response, '<nav class="list-group mb-4" aria-label="Your pages">')
        for name in ("my-places", "calendar", "notifications"):
            self.assertContains(response, f'href="{reverse(name)}"', count=2)
        self.assertContains(response, f'action="{reverse("logout")}"', count=2)
        self.assertNotContains(response, f'href="{reverse("admin:index")}"')
        self.assertNotContains(response, f'href="{reverse("login")}"')

    def test_staff_also_get_the_accuracy_dashboard_and_the_admin(self):
        self.client.force_login(get_user_model().objects.create_user("admin@example.com", is_staff=True))
        response = self.client.get(reverse("me"))
        for name in ("accuracy-dashboard", "admin:index"):
            self.assertContains(response, f'href="{reverse(name)}"', count=2)

    def test_a_visitor_gets_log_in_and_sign_up(self):
        response = self.client.get(reverse("me"))
        self.assertEqual(response.status_code, 200)
        for name in ("login", "signup"):
            self.assertContains(response, f'href="{reverse(name)}"', count=2)
        self.assertNotContains(response, 'aria-label="Your pages"')
