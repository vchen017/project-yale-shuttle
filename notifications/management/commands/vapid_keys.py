"""Print a new VAPID key pair for push notifications (#27).

    python manage.py vapid_keys

Set the two lines as environment variables on a server; give each site its
own pair. The private key is a secret: never commit it or paste it anywhere
public. Changing a site's keys cuts off every device subscribed with the old
ones, so riders would have to turn notifications on again.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "Print a new VAPID key pair for push notifications."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, "big")
        public = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.stdout.write(f"VAPID_PUBLIC_KEY={b64url(public)}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={b64url(private)}")
