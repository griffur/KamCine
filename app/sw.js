"use strict";
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = String(data.titre || "KamCiné").slice(0, 100);
  const body = String(data.description || "Un événement demande ton attention.").slice(0, 200);
  event.waitUntil(self.registration.showNotification(title, {
    body,
    icon: "/icon.png",
    badge: "/icon.png",
    tag: String(data.id || "kamcine"),
    data: { id: data.id || "", cible: data.cible || null }
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const data = event.notification.data || {};
  event.waitUntil((async () => {
    const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const message = { type: "kamcine-push-click", id: data.id, cible: data.cible };
    for (const client of clientsList) {
      if ("focus" in client) {
        await client.focus();
        client.postMessage(message);
        return;
      }
    }
    const url = new URL("/", self.location.origin);
    url.searchParams.set("kc_push", JSON.stringify({ id: data.id, cible: data.cible }));
    await self.clients.openWindow(url.toString());
  })());
});
