from django import forms

from accounts.forms import BootstrapFormMixin

from .models import RiderReport


class ReliabilityForm(BootstrapFormMixin, forms.Form):
    """A rider's report of a bus that never came or skipped their stop (#6)."""

    route = forms.TypedChoiceField(label="Your route", coerce=int)
    stop = forms.TypedChoiceField(label="Your stop", coerce=int)
    kind = forms.ChoiceField(
        label="What happened?",
        choices=RiderReport.Kind.choices,
        widget=forms.RadioSelect,
        initial=RiderReport.Kind.NEVER_CAME,
    )

    def __init__(self, *args, routes, **kwargs):
        super().__init__(*args, **kwargs)
        self.routes = {route["id"]: route for route in routes}
        self.fields["route"].choices = [("", "Pick your route")] + [
            (route["id"], route["name"]) for route in routes
        ]
        # Stops grouped by route, so a rider picks from their route's own stops.
        self.fields["stop"].choices = [("", "Pick your stop")] + [
            (route["name"], [(stop["id"], stop["name"]) for stop in route["stops"]])
            for route in routes
        ]
        self.fields["route"].widget.attrs["class"] = "form-select"
        self.fields["stop"].widget.attrs["class"] = "form-select"

    def full_clean(self):
        super().full_clean()
        # The mixin marks errors with form-control; selects use form-select.
        for name in ["route", "stop"]:
            if name in self.fields:
                attrs = self.fields[name].widget.attrs
                attrs["class"] = attrs["class"].replace("form-control", "form-select")

    def clean(self):
        cleaned = super().clean()
        route = self.routes.get(cleaned.get("route"))
        stop_id = cleaned.get("stop")
        if route and stop_id is not None and stop_id not in {stop["id"] for stop in route["stops"]}:
            self.add_error("stop", "That stop isn't on the route you picked.")
        return cleaned
