"""A rider's saved places (#7): saving one, with at most one Home, and
finding them by name for the trip search.

Every function takes the rider, and every query starts from their places,
so a rider only ever sees their own.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

MOST_PLACES = 20  # per rider: plenty for home, work and favourites
MOST_SUGGESTIONS = 5


class NameTaken(Exception):
    """The rider already has a place with this name."""


def save(user, place):
    """Save the rider's new or changed place. Marking it Home takes Home off
    their other place, so they have one at most.

    The rider's row is locked while saving, so two quick saves can't both make
    a Home (PostgreSQL; SQLite runs one write at a time anyway). The database
    also refuses two Homes or two places with one name.
    """
    place.user = user
    try:
        with transaction.atomic():
            get_user_model().objects.select_for_update().filter(pk=user.pk).first()
            if place.is_home:
                user.places.filter(is_home=True).exclude(pk=place.pk).update(is_home=False)
            place.save()
    except IntegrityError as error:
        raise NameTaken(place.name) from error
    return place


def make_home(user, place):
    """Make one of the rider's places their Home."""
    place.is_home = True
    return save(user, place)


def name_taken(user, name, exclude=None):
    """Whether the rider has another place with this name, ignoring capitals."""
    others = user.places.filter(name__iexact=name)
    if exclude is not None:
        others = others.exclude(pk=exclude.pk)
    return others.exists()


def matching(user, query):
    """The rider's places whose names or addresses have every word they typed,
    Home first, for the trip search's suggestions. With nothing typed, all of
    them, so they can pick one without typing."""
    if not user.is_authenticated:
        return []
    words = query.casefold().split()
    found = [
        place
        for place in user.places.all()
        if all(word in f"{place.name} {place.address}".casefold() for word in words)
    ]
    return [place.as_suggestion() for place in found[:MOST_SUGGESTIONS if words else MOST_PLACES]]


def named(user, name):
    """The rider's place with exactly this name, ignoring capitals, or None:
    a trip typed as "Home" without JavaScript goes from their Home."""
    if not user.is_authenticated or not name:
        return None
    return user.places.filter(name__iexact=name).first()
