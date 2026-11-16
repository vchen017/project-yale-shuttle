// Turns push notifications on and off for this device, on the notifications
// page (#27). The server keeps one subscription per device, and the service
// worker (/sw.js) shows the notifications.
(() => {
  const card = document.getElementById("push");
  if (!card) return;
  const status = card.querySelector("[data-status]");
  const button = card.querySelector("[data-toggle]");
  const { publicKey, subscribeUrl, unsubscribeUrl, csrfToken } = card.dataset;
  // The subscriptions the server has for this rider, so a device removed from
  // the list shows as off here, even if the browser still has its subscription.
  const known = new Set(JSON.parse(document.getElementById("push-endpoints").textContent));

  const isAppleMobile = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const isHomeScreenApp =
    window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function show(text, action) {
    status.textContent = text;
    button.hidden = !action;
    if (action) {
      button.textContent = action.label;
      button.onclick = action.run;
      button.disabled = false;
    }
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify(body),
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
  }

  // The VAPID public key, from URL-safe base64 to the bytes browsers expect.
  function keyBytes(base64) {
    const padded = (base64 + "===".slice((base64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
  }

  async function registration() {
    await navigator.serviceWorker.register("/sw.js");
    return navigator.serviceWorker.ready;
  }

  async function turnOn() {
    button.disabled = true;
    try {
      if ((await Notification.requestPermission()) !== "granted") return refresh();
      const { pushManager } = await registration();
      const subscription =
        (await pushManager.getSubscription()) ||
        (await pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }));
      await post(subscribeUrl, subscription.toJSON());
      window.location.reload(); // to list this device
    } catch (error) {
      show(`Couldn't turn notifications on: ${error.message}.`, { label: "Try again", run: turnOn });
    }
  }

  async function turnOff() {
    button.disabled = true;
    try {
      const { pushManager } = await registration();
      const subscription = await pushManager.getSubscription();
      if (subscription) {
        await post(unsubscribeUrl, { endpoint: subscription.endpoint });
        await subscription.unsubscribe();
      }
      window.location.reload();
    } catch (error) {
      show(`Couldn't turn notifications off: ${error.message}.`, { label: "Try again", run: turnOff });
    }
  }

  async function refresh() {
    if (!publicKey) return show("Notifications aren't set up on this server yet.");
    if (!supported) {
      if (isAppleMobile && !isHomeScreenApp) {
        return show(
          "On iPhone and iPad, add Yale Shuttle to your Home Screen first: tap Share, then " +
            "Add to Home Screen. Open it from there, and turn notifications on.",
        );
      }
      return show("This browser can't receive notifications. Try Chrome, Edge, Firefox or Safari.");
    }
    if (Notification.permission === "denied") {
      return show("Notifications are blocked for this site. Allow them in your browser's settings, then reload.");
    }
    try {
      const { pushManager } = await registration();
      const subscription = await pushManager.getSubscription();
      if (subscription && known.has(subscription.endpoint)) {
        show("Notifications are on for this device.", { label: "Turn off", run: turnOff });
      } else {
        show("Notifications are off for this device.", { label: "Turn on", run: turnOn });
      }
    } catch (error) {
      show(`Couldn't check notifications: ${error.message}.`);
    }
  }

  refresh();
})();
