import hashlib
import re
from html.parser import HTMLParser

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Visit
from .team import PAGE_HASH, member_nicknames

PAGE = "/ecc1032"
STATS = "/ecc1032/stats"


class _AbtestButtons(HTMLParser):
    """Collects the text of every <button id="abtest"> in a page."""

    def __init__(self, html):
        super().__init__()
        self.texts, self._inside = [], False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "button" and dict(attrs).get("id") == "abtest":
            self.texts.append("")
            self._inside = True

    def handle_endtag(self, tag):
        if tag == "button":
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self.texts[-1] += data


def variant_shown(response):
    """The variant on the page. Fails unless there is exactly one abtest button."""
    texts = [text.strip() for text in _AbtestButtons(response.content.decode()).texts]
    assert len(texts) == 1, f"expected one abtest button, found {texts}"
    return texts[0]


class PagePathTests(TestCase):
    def test_hash_method_matches_the_course_example(self):
        self.assertEqual(hashlib.sha1(b"bright-forest").hexdigest()[:7], "211c220")

    def test_page_lives_at_our_team_hash(self):
        self.assertEqual(PAGE_HASH, "ecc1032")
        self.assertEqual(reverse("abtest:page"), PAGE)
        self.assertEqual(reverse("abtest:stats"), STATS)


class PageTests(TestCase):
    def test_public_page_shows_one_variant_button_and_the_team(self):
        response = self.client.get(PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertIn(variant_shown(response), Visit.VARIANTS)
        for nickname in member_nicknames():
            self.assertContains(response, nickname)

    def test_a_visitor_keeps_their_variant_and_counts_one_impression(self):
        first = variant_shown(self.client.get(PAGE))
        for _ in range(3):
            self.assertEqual(variant_shown(self.client.get(PAGE)), first)
        self.assertEqual(Visit.objects.count(), 1)

    def test_new_visitors_get_each_variant_about_half_the_time(self):
        shown = [variant_shown(Client().get(PAGE)) for _ in range(100)]
        self.assertEqual(Visit.objects.count(), 100)
        # For a fair coin, landing outside 20..80 has a probability of about 1e-9.
        self.assertTrue(20 <= shown.count(Visit.KUDOS) <= 80, shown.count(Visit.KUDOS))

    def test_a_click_counts_once_and_its_redirect_changes_nothing(self):
        client = Client(enforce_csrf_checks=True)  # like a real browser
        page = client.get(PAGE)
        variant = variant_shown(page)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode())[1]

        response = client.post(PAGE, {"csrfmiddlewaretoken": token}, follow=True)
        self.assertRedirects(response, PAGE)
        self.assertEqual(variant_shown(response), variant)
        client.post(PAGE, {"csrfmiddlewaretoken": token})  # a second click

        visit = Visit.objects.get()  # still one visitor, so still one impression
        self.assertEqual(visit.variant, variant)
        self.assertIsNotNone(visit.clicked_at)
        self.assertEqual(self.client.get(STATS).json()[variant], {"impressions": 1, "clicks": 1})

    def test_a_click_from_someone_who_never_saw_the_page_counts_nothing(self):
        self.client.post(PAGE)
        self.assertFalse(Visit.objects.exists())


class StatsTests(TestCase):
    def add_visits(self, variant, impressions, clicks):
        for i in range(impressions):
            Visit.objects.create(variant=variant, clicked_at=timezone.now() if i < clicks else None)

    def test_preferred_has_the_higher_click_rate_not_the_most_clicks(self):
        self.add_visits(Visit.KUDOS, impressions=10, clicks=3)  # 30%
        self.add_visits(Visit.THANKS, impressions=4, clicks=2)  # 50%
        self.assertEqual(
            self.client.get(STATS).json(),
            {
                "preferred": "thanks",
                "kudos": {"impressions": 10, "clicks": 3},
                "thanks": {"impressions": 4, "clicks": 2},
            },
        )

    def test_stats_are_public_json_even_with_no_visits(self):
        response = self.client.get(STATS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn(response.json()["preferred"], Visit.VARIANTS)
