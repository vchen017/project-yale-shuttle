from django import forms

from accounts.forms import BootstrapFormMixin


class CalendarForm(BootstrapFormMixin, forms.Form):
    """The ICS address a rider connects their calendar with (#25).

    No Google sign-in: every calendar app can share a secret web address that
    serves the same events as an .ics file.
    """

    ics_url = forms.URLField(
        label="Calendar address (ICS)",
        max_length=500,
        help_text=(
            "The web address your calendar shares, ending in .ics. "
            "In Google Calendar: Settings → your calendar → “Secret address in iCal format”."
        ),
    )

    def clean_ics_url(self):
        return self.cleaned_data["ics_url"].strip()
