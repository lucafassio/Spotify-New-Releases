document.addEventListener('htmx:configRequest', function (event) {
  const panel = document.getElementById('canal-panel');
  // solo en POST: en un GET boosteado quedaria pegado a la URL de la pagina
  if (!panel || event.detail.verb !== 'post' || !event.detail.path.startsWith('/canales/' + panel.dataset.canalId + '/')) return;
  const opened = panel.querySelector('[data-playlist][aria-expanded="true"]');
  event.detail.parameters.open = opened ? opened.dataset.playlist : '';
});
// una sola Fila de playlist abierta: plegar borra el Bloque de nuevos y al reabrir se mezcla en la lista
document.addEventListener('click', function (event) {
  const link = event.target.closest('[data-artists-url]');
  if (!link || !window.htmx || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
  const detail = document.getElementById('detalle-' + link.dataset.playlist);
  if (!detail) return;
  event.preventDefault();
  event.stopPropagation();
  const opening = link.getAttribute('aria-expanded') !== 'true';
  document.querySelectorAll('[data-playlist][aria-expanded="true"]').forEach(function (other) {
    const open = document.getElementById('detalle-' + other.dataset.playlist);
    other.setAttribute('aria-expanded', 'false');
    open.hidden = true;
    open.replaceChildren();
  });
  document.getElementById('indicador').replaceChildren();
  const url = new URL(location.href);
  if (opening) url.searchParams.set('open', link.dataset.playlist); else url.searchParams.delete('open');
  history.replaceState(history.state, '', url);
  if (!opening) return;
  link.setAttribute('aria-expanded', 'true');
  detail.hidden = false;
  detail.innerHTML = '<small role="status">Cargando artistas...</small>';
  htmx.ajax('GET', link.dataset.artistsUrl, {source: link, target: detail, swap: 'innerHTML'}).finally(function () {
    if (!detail.querySelector('[data-sorted]')) detail.replaceChildren();
  }).catch(function () {
    const notice = document.getElementById('action-notice');
    if (notice) notice.innerHTML = '<div class="aviso warn">No pudimos cargar los artistas. Proba de nuevo.</div>';
  });
}, true);
// los swaps fuera de banda agregan filas al final y sacan del Bloque: aca se reordena y se recuenta, sin llamar al servidor
document.addEventListener('htmx:afterRequest', function (event) {
  // si el refresco mismo fallo (red, app ocupada) nadie toco el Indicador
  const refreshed = event.detail.pathInfo.requestPath.match(/\/playlists\/([^/]+)\/refrescar/);
  const reading = refreshed && document.getElementById('ind-' + refreshed[1]);
  if (reading && reading.textContent === 'Buscando...') reading.textContent = 'Sin actualizar';
  const loading = refreshed && document.querySelector('#artistas-' + refreshed[1] + ' [role="status"]');
  if (loading) loading.textContent = 'No pudimos leer esta playlist. Proba mas tarde.';
  document.querySelectorAll('[data-sorted]').forEach(function (list) {
    const rows = Array.from(list.children).filter(function (row) { return row.dataset.sort !== undefined; });
    const sorted = rows.slice().sort(function (a, b) { return a.dataset.sort < b.dataset.sort ? -1 : a.dataset.sort > b.dataset.sort ? 1 : 0; });
    if (sorted.some(function (row, index) { return row !== rows[index]; })) list.append.apply(list, sorted);
  });
  document.querySelectorAll('[data-bloque]').forEach(function (block) {
    const count = block.querySelectorAll('[data-sort]').length;
    block.hidden = count === 0;
    block.querySelector('[data-bloque-count]').textContent = count === 1 ? '1 artista nuevo' : count + ' artistas nuevos';
  });
});
// rueda con menos deslizamiento que la del navegador: cada panel corre un poco menos y frena enseguida
const WHEEL_STEP = 0.7;
const WHEEL_EASE = 0.35;
const glides = new WeakMap();
function glide(box) {
  const state = glides.get(box);
  const left = state.target - box.scrollTop;
  if (Math.abs(left) < 1) { box.scrollTop = state.target; state.frame = 0; return; }
  box.scrollTop += left * WHEEL_EASE;
  state.frame = requestAnimationFrame(function () { glide(box); });
}
document.addEventListener('wheel', function (event) {
  const box = event.target.closest('.scroll');
  if (!box || event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY) || box.scrollHeight <= box.clientHeight) return;
  event.preventDefault();
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? box.clientHeight : 1;
  const state = glides.get(box) || { target: 0, frame: 0 };
  // sin animacion en curso se parte de donde esta: la barra o un swap pudieron moverlo
  const from = state.frame ? state.target : box.scrollTop;
  state.target = Math.max(0, Math.min(box.scrollHeight - box.clientHeight, from + event.deltaY * unit * WHEEL_STEP));
  glides.set(box, state);
  if (!state.frame) state.frame = requestAnimationFrame(function () { glide(box); });
}, { passive: false });
document.addEventListener('htmx:sendError', function () {
  const notice = document.getElementById('action-notice');
  if (notice) notice.innerHTML = '<div class="aviso warn">No pudimos conectar. Proba de nuevo.</div>';
});
