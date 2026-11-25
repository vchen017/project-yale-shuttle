from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/subscribe", views.subscribe, name="push-subscribe"),
    path("notifications/unsubscribe", views.unsubscribe, name="push-unsubscribe"),
    path("notifications/test", views.send_test, name="push-test"),
    path("notifications/<int:pk>/remove", views.remove_device, name="push-remove"),
    path("notifications/ask", views.ask_agent, name="agent-ask"),
    path("notifications/ask/<int:pk>", views.agent_answer, name="agent-answer"),
    # At the root, so the service worker's scope covers the whole site.
    path("sw.js", views.service_worker, name="service-worker"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
]
