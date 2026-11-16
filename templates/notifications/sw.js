{% load static %}// Yale Shuttle's service worker (#27): it shows push notifications, and opens
// the page a notification points to when it's tapped. It's served from /sw.js,
// at the site's root, so it covers every page.

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data.text() };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Yale Shuttle", {
      body: data.body || "",
      icon: "{% static 'notifications/icon-192.png' %}",
      tag: data.tag,
      data: { url: data.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data.url, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((client) => client.url === url);
      return open ? open.focus() : self.clients.openWindow(url);
    }),
  );
});
