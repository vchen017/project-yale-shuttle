from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


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
