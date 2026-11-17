from django import forms

from accounts.forms import BootstrapFormMixin
from transit import places as search
from transit.downtowner import FeedUnavailable
from transit.next_buses import served_stops

from . import services
from .models import Place


def label(place):
    """How a suggested place reads once picked, as place-picker.js writes it:
    "Sterling Library, York Street, New Haven" or "Science Hill (shuttle stop)"."""
    if place["detail"] == "Shuttle stop":
        return f"{place['name']} (shuttle stop)"
    return ", ".join(filter(None, [place["name"], place["detail"]]))


class PlaceForm(BootstrapFormMixin, forms.ModelForm):
    """A saved place's name, where it is, and whether it's Home (#7).

    The location box suggests places as the rider types, like the trip search
    (place-picker.js), and a pick fills in location_at ("41.3112,-72.9290").
    Without a pick, we look up what was typed: our stops, then Photon, which
    answers from laptops but not from Render, so the script picks the top
    suggestion before the form is sent.
    """

    location = forms.CharField(label="Where is it?", max_length=200)
    location_at = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Place
        fields = ["name", "is_home"]
        labels = {"name": "Name", "is_home": "This is my Home"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["name"].widget.attrs.update(placeholder="Home, Work, Gym…", autocomplete="off")
        self.fields["location"].widget.attrs.update(
            {
                "placeholder": "An address, a Yale building or a stop",
                "autocomplete": "off",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "place-location-places",
            }
        )
        if self.instance.pk and not self.is_bound:
            self.initial["location"] = self.instance.address or self.instance.name
            self.initial["location_at"] = f"{self.instance.lat:.6f},{self.instance.lon:.6f}"

    def full_clean(self):
        super().full_clean()
        # Checkboxes and the hidden field aren't text boxes.
        self.fields["is_home"].widget.attrs["class"] = "form-check-input"
        self.fields["location_at"].widget.attrs.pop("class", None)

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError("Give the place a name.")
        if services.name_taken(self.user, name, exclude=self.instance if self.instance.pk else None):
            raise forms.ValidationError(f"You already have a place called “{name}”. Pick another name.")
        return name

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and self.user.places.count() >= services.MOST_PLACES:
            raise forms.ValidationError(
                f"You can save up to {services.MOST_PLACES} places. Delete one you don't use to add another."
            )
        typed = " ".join(cleaned.get("location", "").split())
        if not typed:
            return cleaned
        at = search.point(cleaned.get("location_at") or "")
        if at:
            self.instance.address, (self.instance.lat, self.instance.lon) = typed, at
            return cleaned
        try:
            stops = served_stops()
        except FeedUnavailable:
            stops = []  # addresses can still be found
        found = search.search(typed, stops)
        if not found:
            self.add_error(
                "location", f"We couldn't find “{typed}” near New Haven. Try a building, a street address or a stop."
            )
            return cleaned
        top = found[0]
        self.instance.address = label(top)
        self.instance.lat, self.instance.lon = top["lat"], top["lon"]
        return cleaned
