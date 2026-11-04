"""Who the team is, for the A/B test page."""

import hashlib
from functools import cache

import yaml
from django.conf import settings

TEAM_NICKNAME = "cold-flower"  # the team's nickname on the course site
# The page lives at / plus the first 7 hex digits of the SHA-1 of the nickname.
PAGE_HASH = hashlib.sha1(TEAM_NICKNAME.encode()).hexdigest()[:7]


@cache
def members():
    """The team in docs/mgt656/team.yaml: each member's course nickname, first name and GitHub username."""
    team = yaml.safe_load((settings.BASE_DIR / "docs" / "mgt656" / "team.yaml").read_text())
    return team["members"]


def member_nicknames():
    return [member["nickname"] for member in members()]
