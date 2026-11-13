// Fotos de artistas al final (#23): el HTML sale con la inicial y data-foto en los avatares sin foto guardada.
// Con la pagina quieta (sin pedidos de htmx en vuelo y con el refresco de la playlist respondido) se piden
// de a una tanda por vez a /artistas/fotos, con fetch directo para no entrar en la cola hx-sync del Panel.
(function () {
  const BATCH = 20;
  // espera para dar por quieta la pagina, pausa entre tandas y espera si Deezer corto la tanda
  const QUIET_MS = 400;
  const PAUSE_MS = 600;
  const RETRY_MS = 5000;
  // un artista que no se resolvio en tantos pedidos se queda con la inicial hasta recargar
  const MAX_TRIES = 4;
  // un refresco colgado no frena las fotos para siempre, ni un pedido de htmx que nunca termino
  const MAX_WAIT_MS = 30000;

  const known = new Map();
  const tries = new Map();
  const inFlight = new Map();
  let running = false;
  let timer = 0;
  let waitingSince = 0;
  // despues de una tanda cortada por Deezer ningun swap adelanta la siguiente
  let notBefore = 0;

  function busy() {
    const now = Date.now();
    inFlight.forEach(function (since, xhr) {
      if (xhr.readyState === 4 || now - since > MAX_WAIT_MS) inFlight.delete(xhr);
    });
    // el Indicador dice Buscando... hasta que responde el refresco de la playlist abierta
    const reading = Array.from(document.querySelectorAll('[id^="ind-"]')).some(function (el) { return el.textContent === 'Buscando...'; });
    if (!inFlight.size && !reading) {
      waitingSince = 0;
      return false;
    }
    waitingSince = waitingSince || now;
    return now - waitingSince < MAX_WAIT_MS;
  }

  function pending() {
    // solo los que se ven: una Fila de playlist plegada no tiene caja
    return Array.from(document.querySelectorAll('[data-foto]')).filter(function (el) { return el.getClientRects().length > 0; });
  }

  function onScreen(el) {
    const box = el.getBoundingClientRect();
    return box.bottom > 0 && box.top < window.innerHeight;
  }

  function show(el, url) {
    el.removeAttribute('data-foto');
    el.removeAttribute('data-foto-nombre');
    if (!url || el.querySelector('img')) return;
    const img = document.createElement('img');
    img.alt = '';
    img.addEventListener('error', function () { img.remove(); });
    img.src = url;
    el.append(img);
  }

  function schedule(delay) {
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (running || !pending().length) return;
      if (busy()) {
        schedule(QUIET_MS);
        return;
      }
      const idle = window.requestIdleCallback || function (callback) { return setTimeout(callback, 1); };
      idle(send, { timeout: 2000 });
    }, Math.max(delay === undefined ? QUIET_MS : delay, notBefore - Date.now()));
  }

  function send() {
    if (running || busy()) {
      schedule(QUIET_MS);
      return;
    }
    // primero los avatares que estan en pantalla
    const avatars = pending();
    avatars.sort(function (a, b) { return onScreen(b) - onScreen(a); });
    const names = new Map();
    avatars.forEach(function (el) {
      const id = el.dataset.foto;
      if (known.has(id)) show(el, known.get(id));
      else if ((tries.get(id) || 0) >= MAX_TRIES) show(el, null);
      else if (!names.has(id) && names.size < BATCH) names.set(id, el.dataset.fotoNombre || '');
    });
    if (!names.size) return;

    running = true;
    const ids = Array.from(names.keys());
    const body = JSON.stringify({ artists: ids.map(function (id) { return { id: id, name: names.get(id) }; }) });
    fetch('/artistas/fotos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: body })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(response.status); })
      .then(function (data) {
        const photos = (data && data.photos) || {};
        let missing = false;
        ids.forEach(function (id) {
          if (Object.prototype.hasOwnProperty.call(photos, id)) {
            known.set(id, photos[id]);
            document.querySelectorAll('[data-foto="' + CSS.escape(id) + '"]').forEach(function (el) { show(el, photos[id]); });
          } else {
            // Deezer limito o no respondio: queda para la proxima tanda
            tries.set(id, (tries.get(id) || 0) + 1);
            missing = true;
          }
        });
        running = false;
        notBefore = Date.now() + (missing ? RETRY_MS : PAUSE_MS);
        schedule(0);
      })
      .catch(function () {
        ids.forEach(function (id) { tries.set(id, (tries.get(id) || 0) + 1); });
        running = false;
        notBefore = Date.now() + RETRY_MS;
        schedule(0);
      });
  }

  document.addEventListener('htmx:beforeRequest', function (event) {
    if (event.detail.xhr) inFlight.set(event.detail.xhr, Date.now());
  });
  document.addEventListener('htmx:afterRequest', function (event) {
    inFlight.delete(event.detail.xhr);
    schedule();
  });
  // un swap (fila que vuelve, Bloque de nuevos, Buscador) puede traer avatares nuevos sin foto
  document.addEventListener('htmx:afterSettle', function () { schedule(); });
  schedule();
})();
