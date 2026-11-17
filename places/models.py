from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone


class Place(models.Model):
    """A rider's saved place (#7): Home, work, a friend's, with a name the
    rider picks and a location from the same search as the trip boxes.

    Only its rider ever sees it: every view looks places up by the logged-in
    rider, and it isn't in the admin, since a home address is personal.
    Deleting the account deletes its places (#9).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="places")
    name = models.CharField(max_length=40)
    address = models.CharField(
        max_length=200, blank=True, help_text="What the rider picked, such as “Sterling Library, York Street, New Haven”."
    )
    lat = models.FloatField()
    lon = models.FloatField()
    is_home = models.BooleanField("Home", default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-is_home", Lower("name")]
        constraints = [
            models.UniqueConstraint(F("user"), Lower("name"), name="places_one_name_per_rider"),
            models.UniqueConstraint(fields=["user"], condition=Q(is_home=True), name="places_one_home_per_rider"),
        ]

    def __str__(self):
        return self.name

    def as_suggestion(self):
        """The place as the trip search suggests it, like transit.places does."""
        return {
            "name": self.name,
            "detail": "Home" + (f" · {self.address}" if self.address else "") if self.is_home else self.address,
            "lat": self.lat,
            "lon": self.lon,
            "saved": True,
            "home": self.is_home,
        }
