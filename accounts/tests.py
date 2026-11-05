from django.contrib.auth import get_user_model
from django.test import TestCase

PASSWORD = "a-long-pass-phrase-9"


class AccountTests(TestCase):
    def test_sign_up_logs_the_new_rider_in(self):
        response = self.client.post(
            "/signup/",
            {"username": "newrider", "email": "new@example.com", "password1": PASSWORD, "password2": PASSWORD},
            follow=True,
        )
        self.assertRedirects(response, "/")
        self.assertContains(response, "newrider")

    def test_sign_up_form_shows_one_short_line_of_password_rules(self):
        response = self.client.get("/signup/")
        self.assertContains(response, "At least 8 characters, not too common, and not only numbers.")
        self.assertNotContains(response, "too similar")

    def test_log_in_and_out(self):
        get_user_model().objects.create_user("rider@example.com", password=PASSWORD)
        self.assertEqual(self.client.get("/login/").status_code, 200)
        response = self.client.post("/login/", {"username": "rider@example.com", "password": PASSWORD})
        self.assertRedirects(response, "/")
        self.assertRedirects(self.client.post("/logout/"), "/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_wrong_password_is_rejected(self):
        get_user_model().objects.create_user("rider@example.com", password=PASSWORD)
        response = self.client.post("/login/", {"username": "rider@example.com", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, 'class="alert alert-danger')  # the error is shown, styled

    def test_forms_use_bootstrap_inputs(self):
        self.assertContains(self.client.get("/login/"), 'class="form-control"', count=2)
        self.assertContains(self.client.get("/signup/"), 'class="form-control"', count=4)

    def test_a_field_with_an_error_is_marked_invalid(self):
        response = self.client.post(
            "/signup/", {"username": "newrider", "password1": PASSWORD, "password2": "something-else-1"}
        )
        self.assertContains(response, "form-control is-invalid")
        self.assertContains(response, 'class="invalid-feedback d-block"')
