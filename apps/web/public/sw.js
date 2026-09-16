/* Web Push and an explicitly saved public timetable. Private API responses
   and authenticated app pages are never cached. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
const allowedRoutes = new Set(['#schedule', '#queues', '#notifications', '#questions']);
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
const OFFLINE_CACHE = 'campus-public-offline-v1';
const OFFLINE_FILES = ['/offline.html', '/offline.js', '/offline.css'];
const SNAPSHOT_URL = '/offline-data.json';
const publicLessonKeys = ['id','title','title_en','title_en_auto','kind','room','mode','start','end','subgroup','status','date','starts_at','ends_at','parity','demo'];
self.addEventListener('message', event => {
  if (!['offline-save', 'offline-status', 'offline-delete'].includes(event.data?.type)) return;
  event.waitUntil((async () => {
    try {
      const cache = await caches.open(OFFLINE_CACHE);
      if (event.data.type === 'offline-delete') await cache.delete(SNAPSHOT_URL);
      if (event.data.type === 'offline-save') {
        const {start, end, subgroup} = event.data;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) ||
            !Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end)) ||
            Date.parse(end) < Date.parse(start) || Date.parse(end) - Date.parse(start) > 31*86400000 || ![0,1,2].includes(subgroup)) throw new Error('invalid_range');
        const [settingsResponse, lessonsResponse] = await Promise.all([
          fetch('/api/schedule/guest/settings', {credentials:'omit', cache:'no-store'}),
          fetch('/api/schedule/guest/occurrences?' + new URLSearchParams({start,end,subgroup}), {credentials:'omit', cache:'no-store'}),
        ]);
        if (!settingsResponse.ok || !lessonsResponse.ok) throw new Error('network');
        const settings = await settingsResponse.json(), lessons = await lessonsResponse.json();
        const snapshot = {saved_at:new Date().toISOString(), start, end, subgroup,
          settings:{group:settings.group, timezone:settings.timezone},
          lessons:lessons.map(item => Object.fromEntries(publicLessonKeys.filter(k => k in item).map(k => [k,item[k]])))};
        await cache.addAll(OFFLINE_FILES);
        await cache.put(SNAPSHOT_URL, new Response(JSON.stringify(snapshot), {headers:{'Content-Type':'application/json'}}));
      }
      const saved = await cache.match(SNAPSHOT_URL);
      const data = saved ? await saved.json() : null;
      event.ports[0]?.postMessage({ok:true, saved_at:data?.saved_at || null});
    } catch { event.ports[0]?.postMessage({ok:false}); }
  })());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== 'GET') return;
  if (url.pathname === SNAPSHOT_URL) {
    event.respondWith(caches.open(OFFLINE_CACHE).then(async cache => (await cache.match(SNAPSHOT_URL)) || new Response('null', {headers:{'Content-Type':'application/json'}})));
    return;
  }
  if (OFFLINE_FILES.includes(url.pathname)) {
    event.respondWith(fetch(event.request).catch(async () => (await caches.open(OFFLINE_CACHE)).match(url.pathname)).then(response => response || new Response('Offline copy unavailable', {status:503})));
    return;
  }
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(async () => {
    const cache = await caches.open(OFFLINE_CACHE);
    return (await cache.match('/offline.html')) || new Response(
      '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Campus Flow</title><h1>Campus Flow</h1><p>Нет подключения и сохранённой копии. / Offline; no saved timetable.</p><a href="/">Повторить / Retry</a></html>',
      {status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}});
  }));
});
