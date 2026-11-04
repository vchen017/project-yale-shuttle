from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignUpForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username", "email")
        help_texts = {"username": "Letters, numbers and @ . + - _ only."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One short line instead of Django's list of password rules.
        self.fields["password1"].help_text = "At least 8 characters, not too common, and not only numbers."
        self.fields["password2"].help_text = ""
