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
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const open = windows.find((client) => client.url === url);
      if (open) return open.focus();
      // Otherwise take the site's own window there, as an app would, rather than
      // opening a second one (#164): an installed site runs in just the one. Only
      // a window this worker controls can be sent somewhere else.
      const [mine] = await self.clients.matchAll({ type: "window" });
      if (mine) {
        try {
          const focused = await mine.focus();
          if (await focused.navigate(url)) return;
        } catch {
          // It couldn't be moved: open the page instead.
        }
      }
      return self.clients.openWindow(url);
    })(),
  );
});
