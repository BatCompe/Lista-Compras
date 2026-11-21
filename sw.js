// Guarda a app no telemóvel para abrir sem internet e recebe ficheiros partilhados (Android).
const CACHE = 'lista-compras-v4';
const FICHEIROS = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FICHEIROS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE && k !== 'partilha').map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // ficheiro partilhado a partir do WhatsApp (menu Partilhar do Android)
  if (e.request.method === 'POST' && url.pathname.endsWith('/partilha')) {
    e.respondWith((async () => {
      try {
        const dados = await e.request.formData();
        const f = dados.get('ficheiro');
        if (f && f.size) {
          const c = await caches.open('partilha');
          await c.put(new URL('ficheiro-partilhado', self.registration.scope).href, new Response(await f.text()));
        } else {
          // link de um produto partilhado a partir do site do Pingo Doce ou do Continente
          const txt = [dados.get('link'), dados.get('texto'), dados.get('titulo')].filter(Boolean).join(' ');
          const m = txt.match(/https?:\/\/\S+/);
          if (m) {
            const c = await caches.open('partilha');
            await c.put(new URL('link-partilhado', self.registration.scope).href, new Response(JSON.stringify({ url: m[0], titulo: String(dados.get('titulo') || '') })));
            return Response.redirect(new URL('./?link=1#precos', self.registration.scope).href, 303);
          }
        }
      } catch (err) {}
      return Response.redirect(new URL('./?importar=1#partilhar', self.registration.scope).href, 303);
    })());
    return;
  }
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  // tenta a internet primeiro (para receber atualizações); sem rede, usa a cópia guardada
  e.respondWith(
    fetch(e.request).then(r => {
      if (r.ok) { const copia = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copia)); }
      return r;
    }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || caches.match('./index.html')))
  );
});
