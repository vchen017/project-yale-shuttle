from django import forms

from accounts.forms import BootstrapFormMixin


class TripForm(BootstrapFormMixin, forms.Form):
    """Where the rider starts and, optionally, where they're going (#24)."""

    start = forms.TypedChoiceField(label="From", coerce=int)
    to = forms.TypedChoiceField(label="To", coerce=int, required=False, empty_value=None)

    def __init__(self, *args, stops, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(stop["id"], stop["name"]) for stop in stops]
        self.fields["start"].choices = [("", "Pick a stop")] + choices
        self.fields["to"].choices = [("", "Any stop")] + choices
        self._use_select_class()

    def full_clean(self):
        super().full_clean()
        self._use_select_class()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("to") is not None and cleaned.get("to") == cleaned.get("start"):
            self.add_error("to", "Pick a different stop from where you start.")
        return cleaned

    def _use_select_class(self):
        # The mixin gives inputs form-control; Bootstrap styles selects with form-select.
        for field in self.fields.values():
            field.widget.attrs["class"] = field.widget.attrs["class"].replace("form-control", "form-select")
