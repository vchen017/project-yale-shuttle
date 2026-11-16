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
  - A word matches the start of any word of the name ("hark" and "harkness"),
    or the initials of consecutive words of the name ("es" for "Edward S.").
  - The whole query matches a building's abbreviation exactly ("sss", "ESH").
"""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "buildings.json"
SHORTEST_QUERY = 2  # characters, as for stops
MOST_BUILDINGS = 4
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
        words = normalize(entry["name"]).split()
        initials = {
            "".join(word[0] for word in words[start:stop])
            for start in range(len(words))
            for stop in range(start + 2, min(len(words), start + LONGEST_INITIALS) + 1)
        }
        buildings.append({
            "place": {
                "name": entry["name"],
                "detail": " · ".join(filter(None, [entry["abbr"] if len(entry["abbr"]) > 1 else "", entry["street"]])),
                "lat": entry["lat"],
                "lon": entry["lon"],
            },
            "abbr": normalize(entry["abbr"]).replace(" ", ""),
            "words": words,
            "initials": initials,
        })
    return buildings


def _matches(query_words, building):
    return all(
        any(word.startswith(query_word) for word in building["words"]) or query_word in building["initials"]
        for query_word in query_words
    )


def search(query, limit=MOST_BUILDINGS):
    """The buildings matching what the rider typed, best first, as places."""
    query = normalize(query)
    if len(query) < SHORTEST_QUERY:
        return []
    query_words = [word for word in query.split() if word not in FILLER]
    scored = []
    for building in _buildings():
        if building["abbr"] and building["abbr"] == query.replace(" ", ""):
            scored.append((0, 0, building))  # the abbreviation itself: "sss", "esh"
        elif query_words and _matches(query_words, building):
            # Closest first: the fewest words of the name left unmatched.
            leftover = len([word for word in building["words"] if word not in FILLER]) - len(query_words)
            scored.append((1, max(leftover, 0), building))
    scored.sort(key=lambda item: (item[0], item[1], item[2]["place"]["name"]))
    return [building["place"] for _, _, building in scored[:limit]]
