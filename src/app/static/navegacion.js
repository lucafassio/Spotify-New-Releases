// la precarga deja la vista en la cache del navegador unos segundos: solo el click que la sigue puede usarla
const PRELOAD_TTL = 10000;
document.addEventListener('htmx:configRequest', function (event) {
  const headers = event.detail.headers;
  const source = event.detail.elt;
  if (event.detail.verb !== 'get' || headers['HX-Boosted'] !== 'true') return;
  if (headers['HX-Preloaded'] === 'true') {
    headers['Cache-Control'] = 'no-cache';
    source.preloadedAt = Date.now();
    return;
  }
  const fresh = source.preloadedAt && Date.now() - source.preloadedAt < PRELOAD_TTL;
  source.preloadedAt = 0;
  // un link que sigue en pantalla despues del click vuelve a precargar en el proximo hover
  if (source.preloadState === 'DONE') source.preloadState = 'READY';
  // sin precarga reciente se va al servidor: una vista guardada antes de una accion quedaria vieja
  if (!fresh) headers['Cache-Control'] = 'no-cache';
});
// el back que trae la pagina del servidor reemplaza el body: un click en vuelo swapearia en el #vista viejo, desconectado
const navigations = new Set();
document.addEventListener('htmx:beforeSend', function (event) {
  if (!event.detail.boosted) return;
  const xhr = event.detail.xhr;
  navigations.add(xhr);
  xhr.addEventListener('loadend', function () { navigations.delete(xhr); });
});
document.addEventListener('htmx:historyRestore', function () {
  navigations.forEach(function (xhr) { xhr.abort(); });
  navigations.clear();
});
