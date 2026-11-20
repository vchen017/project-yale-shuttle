from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError

from .models import RESERVED_USERNAMES, User


class BootstrapFormMixin:
    """Give every input Bootstrap's form-control class, plus is-invalid once it has an error."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                self.fields[name].widget.attrs["class"] = "form-control is-invalid"


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    pass


class SignUpForm(BootstrapFormMixin, UserCreationForm):
    class Meta:
        model = User
        fields = ("username", "email")
        help_texts = {"username": "Letters, numbers and @ . + - _ only."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One short line instead of Django's list of password rules.
        self.fields["password1"].help_text = "At least 8 characters, not too common, and not only numbers."
        self.fields["password2"].help_text = ""

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if username and username.lower() in RESERVED_USERNAMES:
            raise ValidationError("That username is reserved.")
        return username
