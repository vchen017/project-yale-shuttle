from django import forms

from accounts.forms import BootstrapFormMixin

from .models import CrowdReport


class ReportForm(BootstrapFormMixin, forms.Form):
    """A rider's crowding report: which route, and how busy (#12)."""

    route = forms.TypedChoiceField(label="Your route", coerce=int)
    level = forms.TypedChoiceField(
        label="How busy is it?", coerce=int, choices=CrowdReport.Level.choices, widget=forms.RadioSelect
    )

    def __init__(self, *args, routes, **kwargs):
        super().__init__(*args, **kwargs)
        self.routes = {route["id"]: route for route in routes}
        self.fields["route"].choices = [("", "Pick your route")] + [
            (route["id"], route["name"]) for route in routes
        ]
        self.fields["route"].widget.attrs["class"] = "form-select"

    def full_clean(self):
        super().full_clean()
        # The mixin marks errors with form-control; selects use form-select.
        if "route" in self.fields:
            self.fields["route"].widget.attrs["class"] = self.fields["route"].widget.attrs[
                "class"
            ].replace("form-control", "form-select")
