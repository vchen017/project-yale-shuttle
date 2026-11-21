import hashlib
import os
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Visit
from .team import PAGE_HASH, member_nicknames, members

PAGE = "/ecc1032"
STATS = "/ecc1032/stats"
RESET_WORKFLOW = settings.BASE_DIR / ".github" / "workflows" / "reset-abtest.yml"


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
        self.assertEqual(len(member_nicknames()), 4)
        for member in members():
            self.assertContains(response, f'{member["name"]} <span class="member-nickname">{member["nickname"]}</span>')

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


class ResetTests(TestCase):
    """Deleting our own visits before the graded run (#47)."""

    def setUp(self):
        Visit.objects.create(variant=Visit.KUDOS)
        Visit.objects.create(variant=Visit.KUDOS, clicked_at=timezone.now())
        Visit.objects.create(variant=Visit.THANKS)

    def reset(self, *args):
        out = StringIO()
        call_command("reset_abtest", *args, stdout=out)
        return out.getvalue().strip()

    def test_without_yes_it_only_counts_them(self):
        self.assertEqual(self.reset(), "Would delete 3 visits. Run again with --yes to delete them.")
        self.assertEqual(Visit.objects.count(), 3)

    def test_with_yes_every_visit_goes_and_the_stats_start_from_zero(self):
        self.assertEqual(self.reset("--yes"), "Deleted 3 visits.")
        stats = self.client.get(STATS).json()
        self.assertEqual(stats["kudos"], {"impressions": 0, "clicks": 0})
        self.assertEqual(stats["thanks"], {"impressions": 0, "clicks": 0})


class ResetWorkflowTests(SimpleTestCase):
    """The Actions button that runs reset_abtest on one site (#47)."""

    def setUp(self):
        self.workflow = yaml.safe_load(RESET_WORKFLOW.read_text())
        self.job = self.workflow["jobs"]["reset"]
        self.step = next(step for step in self.job["steps"] if step.get("id") == "reset")

    def test_it_runs_only_when_started_and_on_the_site_whose_name_is_typed(self):
        triggers = self.workflow.get("on", self.workflow.get(True))  # YAML 1.1 reads a bare `on` as true
        self.assertEqual(list(triggers), ["workflow_dispatch"])  # never on a push or a schedule
        self.assertEqual(triggers["workflow_dispatch"]["inputs"]["confirm"]["default"], "")
        # The site whose name has to be typed is the one whose DATABASE_URL secret it gets.
        self.assertEqual(self.job["environment"], "${{ inputs.environment }}")
        self.assertEqual(
            self.step["env"],
            {
                "DATABASE_URL": "${{ secrets.DATABASE_URL }}",
                "SITE": "${{ inputs.environment }}",
                "CONFIRM": "${{ inputs.confirm }}",
            },
        )

    def run_step(self, site, typed, database_url="postgres://someone@db.example/shuttle"):
        """Runs the step's script the way GitHub does, with a stand-in `python`
        that only records how it was called."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "python").write_text('#!/bin/sh\necho "$*" >> "$CALLS"\n')
            (tmp / "python").chmod(0o755)
            env = {
                "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                "CALLS": str(tmp / "calls"),
                "GITHUB_OUTPUT": str(tmp / "output"),
                "SITE": site,
                "CONFIRM": typed,
                "DATABASE_URL": database_url,
            }
            result = subprocess.run(
                ["bash", "-e", "-c", self.step["run"]], env=env, capture_output=True, text=True, timeout=30
            )
            calls = (tmp / "calls").read_text() if (tmp / "calls").exists() else ""
            output = (tmp / "output").read_text() if (tmp / "output").exists() else ""
        return result, [line for line in calls.splitlines() if line.startswith("manage.py")], output

    def test_it_deletes_only_when_the_sites_name_is_typed(self):
        for typed in ["", "yes", "staging", "production please"]:
            with self.subTest(typed=typed):
                result, commands, output = self.run_step("production", typed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands, ["manage.py reset_abtest"])  # only counts them
                self.assertNotIn("deleted=true", output)
                self.assertIn("::notice::Nothing was deleted", result.stdout)
        for site, typed in [("production", "production"), ("production", " Production "), ("staging", "staging")]:
            with self.subTest(site=site, typed=typed):
                result, commands, output = self.run_step(site, typed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands, ["manage.py reset_abtest --yes"])
                self.assertIn("deleted=true", output)

    def test_without_the_sites_database_secret_it_stops_before_django_starts(self):
        result, commands, _ = self.run_step("production", "production", database_url="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(commands, [])
        self.assertIn("::error::Add a DATABASE_URL secret to the production environment", result.stdout)
