"""Find a Yale building by the name riders use for it (issue #98).

Riders type "es harkness", "E.S. Harkness" or "SSS"; the official name is
"Edward S. Harkness Memorial Hall". `search("es harkness")` returns the building
as a place for the trip search, the same shape as a stop or a Photon result:

    [{"name": "Edward S. Harkness Memorial Hall",
      "detail": "ESH · 367 Cedar Street", "lat": 41.30439, "lon": -72.93409}]

The buildings are in data/buildings.json, with the sources of every name. This
runs on the server with no outside calls, so buildings still suggest when Photon
is down.

How a query matches (every word of the query must match):
  - It ignores capitals and punctuation ("E.S." becomes "es"), and filler words
    such as "hall", "building" and "bldg", which riders add or leave off.
  - A word matches the start of any word of a name ("hark" and "harkness"), or
    the initials of consecutive words of it ("es" for "Edward S."). A building's
    names are its official name and its also_known_as names, such as
    OpenStreetMap's "Lawrence Hall" for "Lawrance Hall".
  - The whole query matches a building's abbreviation exactly ("sss", "ESH").

Best first, in this order:
  1. The whole query is one of the building's short_names or also_known_as names.
     A short name is a bare word riders use when several buildings share it:
     "sterling" is Sterling Memorial Library, "bass" is Bass Library. Find my bus
     plans to the top suggestion when nothing is picked, so this matters.
  2. The whole query is the building's abbreviation.
  3. Every word matches; the fewest unmatched words of the name first.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "buildings.json"
SHORTEST_QUERY = 2  # characters, as for stops
MOST_BUILDINGS = 6  # enough for every "sterling" building; a longer list is hard to scroll on a phone
LONGEST_INITIALS = 4  # words
FILLER = {"hall", "building", "bldg", "the", "of", "at", "and", "for"}


def normalize(text):
    """Lower case, without punctuation: "E.S. Harkness-Hall's" is "es harkness halls"."""
    text = text.casefold().replace("&", " and ").replace("-", " ").replace("/", " ")
    text = re.sub(r"[.'’,]", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


@lru_cache(maxsize=1)
def _buildings():
    """The buildings with what matching needs, read once."""
    buildings = []
    for entry in json.loads(DATA.read_text(encoding="utf-8"))["buildings"]:
        buildings.append({
            "place": {
                "name": entry["name"],
                "detail": " · ".join(filter(None, [entry["abbr"] if len(entry["abbr"]) > 1 else "", entry["street"] or "Yale University"])),
                "lat": entry["lat"],
                "lon": entry["lon"],
            },
            "abbr": normalize(entry["abbr"]).replace(" ", ""),
            "forms": [_form(entry["name"])] + [_form(name) for name in entry.get("also_known_as", [])],
            "everyday": {_without_filler(name) for name in entry.get("also_known_as", []) + entry.get("short_names", [])},
        })
    return buildings


def _form(name):
    """One name of a building, ready to match: its words and the initials of runs of them."""
    words = normalize(name).split()
    initials = {
        "".join(word[0] for word in words[start:stop])
        for start in range(len(words))
        for stop in range(start + 2, min(len(words), start + LONGEST_INITIALS) + 1)
    }
    return {"words": words, "initials": initials}


def _without_filler(text):
    return " ".join(word for word in normalize(text).split() if word not in FILLER)


def _leftover(query_words, form):
    """How many words of this name the query leaves unmatched, or None if a query word matches nothing."""
    for query_word in query_words:
        if not (any(word.startswith(query_word) for word in form["words"]) or query_word in form["initials"]):
            return None
    return max(len([word for word in form["words"] if word not in FILLER]) - len(query_words), 0)


def search(query, limit=MOST_BUILDINGS):
    """The buildings matching what the rider typed, best first, as places."""
    query = normalize(query)
    if len(query) < SHORTEST_QUERY:
        return []
    query_words = [word for word in query.split() if word not in FILLER]
    everyday = " ".join(query_words)
    scored = []
    for building in _buildings():
        if everyday and everyday in building["everyday"]:
            scored.append((0, 0, building))  # "sterling", "bass": what riders call it
        elif building["abbr"] and building["abbr"] == query.replace(" ", ""):
            scored.append((1, 0, building))  # the abbreviation itself: "sss", "esh"
        elif query_words:
            # Closest first: the fewest words of the name left over. The official name sets the
            # rank when it matches; the also_known_as names only help a building match at all.
            leftovers = [left for left in (_leftover(query_words, form) for form in building["forms"]) if left is not None]
            if leftovers:
                scored.append((2, leftovers[0], building))
    scored.sort(key=lambda item: (item[0], item[1], item[2]["place"]["name"]))
    return [building["place"] for _, _, building in scored[:limit]]
