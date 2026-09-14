/* Service worker handles Web Push only. No private HTML, API responses or
   authenticated timetable data are stored in a shared offline cache. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
const allowedRoutes = new Set(['#schedule', '#queues', '#notifications']);
function ownerStore(write, value) {
  return new Promise(resolve => {
    const open = indexedDB.open('campus-push', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('settings');
    open.onerror = () => resolve(null);
    open.onsuccess = () => {
      const db = open.result, tx = db.transaction('settings', write ? 'readwrite' : 'readonly');
      const request = write ? tx.objectStore('settings').put(value, 'owner') : tx.objectStore('settings').get('owner');
      let result = null;
      request.onsuccess = () => { result = request.result; };
      tx.oncomplete = () => { db.close(); resolve(result); };
      tx.onerror = () => { db.close(); resolve(null); };
    };
  });
}
self.addEventListener('message', event => {
  if (event.data?.type === 'push-owner') {
    const value = typeof event.data.owner === 'string' ? event.data.owner.slice(0, 36) : null;
    event.waitUntil(ownerStore(true, value).then(() => event.ports[0]?.postMessage('saved')));
  }
});
self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data?.json() || {}; } catch {}
    const owner = await ownerStore(false);
    const current = owner && owner === data.user_id;
    const options = {
      body: current && typeof data.body === 'string' ? data.body.slice(0, 600) : 'Откройте Campus Flow / Open Campus Flow',
      icon: '/icons/icon-192.png', badge: '/icons/badge-96.png',
      tag: 'campus-' + (typeof data.id === 'string' ? data.id.slice(0, 36) : 'update'),
      data: { route: allowedRoutes.has(data.route) ? data.route : '#notifications' },
    };
    // Always show a visible notification as required by browser push policies.
    await self.registration.showNotification(current && typeof data.title === 'string' ? data.title.slice(0, 120) : 'Campus Flow', options);
    for (const client of await self.clients.matchAll({type:'window', includeUncontrolled:true})) client.postMessage({type:'notifications-changed'});
  })());
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const route = allowedRoutes.has(event.notification.data?.route) ? event.notification.data.route : '#notifications';
  const target = new URL('/' + route, self.location.origin).href;
  event.waitUntil((async () => {
    for (const client of await self.clients.matchAll({type:'window',includeUncontrolled:true})) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.navigate(target); return client.focus();
      }
    }
    return self.clients.openWindow(target);
  })());
});
self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => new Response(
    '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Campus Flow</title><h1>Campus Flow</h1><p>Нет подключения. Расписание и очереди требуют связи с сервером.</p><p>You are offline. Reconnect to view the current timetable and queues.</p><a href="/">Повторить / Retry</a></html>',
    {status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}}
  )));
});
