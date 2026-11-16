"""Sending browser push notifications: the only network code in this app, so
tests mock `webpush` here and nothing else (#27).

A notification goes to the device's push service (Google's for Chrome,
Mozilla's for Firefox, Apple's for Safari), encrypted with the device's keys
and signed with our VAPID key, so the service knows it's from us.
"""

import base64
import binascii
import json
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from pywebpush import WebPushException, webpush

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
TEST_TTL_SECONDS = 60 * 60  # how long a push service keeps trying an offline device

# The push services browsers use. Subscriptions must point at one of them, so
# nobody can make our server send requests anywhere else.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",  # Chrome, Edge on Android, Samsung Internet, Opera
    "android.googleapis.com",
    "push.services.mozilla.com",  # Firefox
    "push.apple.com",  # Safari (web.push.apple.com)
    "notify.windows.com",  # Edge on Windows
)


class PushError(Exception):
    """The push service turned the notification away, or couldn't be reached."""


class PushNotConfigured(PushError):
    """The server has no VAPID keys, so it can't sign notifications."""


def is_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def is_push_service(endpoint):
    """Whether `endpoint` is an https address at a known push service."""
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == known or host.endswith("." + known) for known in PUSH_SERVICE_HOSTS
    )


def valid_keys(p256dh, auth):
    """Whether a subscription's keys are what browsers send: a P-256 public key
    (65 bytes, uncompressed) and a 16-byte secret, both URL-safe base64."""

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    try:
        public, secret = decode(p256dh), decode(auth)
    except (binascii.Error, ValueError, TypeError):
        return False
    return len(public) == 65 and public[0] == 4 and len(secret) == 16


def send(subscription, title, body, url="/", tag=None, ttl=TEST_TTL_SECONDS):
    """Send one notification to one device.

    Returns True once the push service accepts it, or False when the
    subscription can't be used any more: the service says it's gone (the rider
    turned notifications off, or the browser dropped it), or its keys are
    broken. Then the subscription is deleted. Raises PushError for anything
    else, and keeps the subscription.
    """
    if not is_configured():
        raise PushNotConfigured("Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY to send notifications")
    if not valid_keys(subscription.p256dh, subscription.auth):
        logger.warning("Deleting %s: its keys can't encrypt anything", subscription)
        subscription.delete()
        return False
    payload = {"title": title, "body": body, "url": url}
    if tag:
        payload["tag"] = tag  # a newer notification with the same tag replaces this one
    try:
        webpush(
            subscription_info=subscription.info(),
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},  # a new dict: webpush adds to it
            ttl=ttl,
            timeout=TIMEOUT_SECONDS,
        )
    except WebPushException as error:
        status = getattr(error.response, "status_code", None)
        if status in (404, 410):
            logger.info("Deleting %s: its push service says it's gone", subscription)
            subscription.delete()
            return False
        raise PushError(f"The push service said {status or 'nothing'} for {subscription.device}") from error
    except OSError as error:  # timeouts and connection errors
        raise PushError(f"Couldn't reach the push service for {subscription.device}") from error
    except ValueError as error:  # the device's keys were checked above, so it's our VAPID key
        raise PushError("Couldn't sign the notification: check VAPID_PRIVATE_KEY") from error
    subscription.last_sent_at = timezone.now()
    subscription.save(update_fields=["last_sent_at"])
    return True


def describe(sent, gone, failed):
    """send_test's counts as a message for the rider or admin, with its level."""

    def devices(count):
        return f"{count} device" if count == 1 else f"{count} devices"

    parts = []
    if sent:
        parts.append(f"Sent a test notification to {devices(sent)}.")
    if gone:
        parts.append(f"Removed {devices(gone)} that can't get notifications any more.")
    if failed:
        parts.append(f"{devices(failed).capitalize()} failed; the server log has the details.")
    if not parts:
        return messages.INFO, "There are no devices to send to."
    if failed and not sent:
        return messages.ERROR, " ".join(parts)
    return (messages.WARNING if failed or gone else messages.SUCCESS), " ".join(parts)


def send_test(subscriptions):
    """Send a test notification to each subscription; returns (sent, gone, failed) counts."""
    sent = gone = failed = 0
    for subscription in list(subscriptions):
        try:
            if send(
                subscription,
                "Yale Shuttle",
                "Notifications work on this device. We'll use them to tell you when to leave.",
                url="/notifications/",
                tag="test",
            ):
                sent += 1
            else:
                gone += 1
        except PushNotConfigured:
            raise
        except PushError:
            logger.exception("Test notification failed")
            failed += 1
    return sent, gone, failed
