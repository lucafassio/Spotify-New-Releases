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
// los swaps fuera de banda agregan filas al final y sacan del Bloque: aca se reordena y se recuenta, sin llamar al servidor.
// Solo las listas que toco algun swap fuera de banda del request: la navegacion, el Buscador y las fotos no recorren nada.
const touched = new Set();
document.addEventListener('htmx:oobBeforeSwap', function (event) {
  const target = event.detail.target;
  // un delete saca la fila del documento: su lista y su Bloque se anotan antes
  const list = target.closest('[data-sorted]');
  const block = target.closest('[data-bloque]');
  if (list) touched.add(list);
  if (block) touched.add(block);
  // innerHTML y beforeend dejan el target: lo que traiga adentro se mira despues del swap
  touched.add(target);
  const fragment = event.detail.fragment;
  if (fragment && fragment.querySelectorAll) fragment.querySelectorAll('[data-sorted], [data-bloque]').forEach(function (el) { touched.add(el); });
});
function sortList(list) {
  const rows = Array.from(list.children).filter(function (row) { return row.dataset.sort !== undefined; });
  const sorted = rows.slice().sort(function (a, b) { return a.dataset.sort < b.dataset.sort ? -1 : a.dataset.sort > b.dataset.sort ? 1 : 0; });
  if (sorted.some(function (row, index) { return row !== rows[index]; })) list.append.apply(list, sorted);
}
function countBlock(block) {
  // las filas que static/flechas.js oculta mientras espera respuesta no cuentan, igual que en su recuento
  const count = Array.from(block.querySelectorAll('[data-sort]')).filter(function (row) { return row.style.display !== 'none'; }).length;
  block.hidden = count === 0;
  block.querySelector('[data-bloque-count]').textContent = count === 1 ? '1 artista nuevo' : count + ' artistas nuevos';
}
document.addEventListener('htmx:afterRequest', function (event) {
  // si el refresco mismo fallo (red, app ocupada) nadie toco el Indicador
  const refreshed = event.detail.pathInfo.requestPath.match(/\/playlists\/([^/]+)\/refrescar/);
  const reading = refreshed && document.getElementById('ind-' + refreshed[1]);
  if (reading && reading.textContent === 'Buscando...') reading.textContent = 'Sin actualizar';
  const loading = refreshed && document.querySelector('#artistas-' + refreshed[1] + ' [role="status"]');
  if (loading) loading.textContent = 'No pudimos leer esta playlist. Proba mas tarde.';
  if (!touched.size) return;
  const lists = new Set();
  const blocks = new Set();
  touched.forEach(function (el) {
    if (!el.isConnected) return;
    if (el.matches('[data-sorted]')) lists.add(el);
    if (el.matches('[data-bloque]')) blocks.add(el);
    el.querySelectorAll('[data-sorted]').forEach(function (list) { lists.add(list); });
    el.querySelectorAll('[data-bloque]').forEach(function (block) { blocks.add(block); });
  });
  touched.clear();
  lists.forEach(sortList);
  blocks.forEach(countBlock);
});
// rueda con menos deslizamiento que la del navegador: cada panel corre un poco menos y frena enseguida.
// El listener va en cada .scroll, no en el documento: fuera de los paneles la rueda queda nativa y sin JS.
const WHEEL_STEP = 0.7;
const WHEEL_EASE = 0.35;
const glides = new WeakMap();
const wheeled = new WeakSet();
function glide(box) {
  const state = glides.get(box);
  const left = state.target - state.pos;
  if (Math.abs(left) < 1) { box.scrollTop = state.target; state.frame = 0; return; }
  // la posicion se lleva aca: escribir sin leer scrollTop no fuerza layout en cada cuadro
  state.pos += left * WHEEL_EASE;
  box.scrollTop = state.pos;
  state.frame = requestAnimationFrame(function () { glide(box); });
}
function onWheel(event) {
  const box = event.currentTarget;
  // un panel adentro de otro ya la atendio
  if (event.defaultPrevented || event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  const state = glides.get(box) || { target: 0, pos: 0, max: 0, frame: 0 };
  // sin animacion en curso se lee el panel una vez: la barra o un swap pudieron moverlo o cambiar su alto
  if (!state.frame) {
    state.max = box.scrollHeight - box.clientHeight;
    state.pos = state.target = box.scrollTop;
  }
  // un panel que no desborda deja la rueda al navegador
  if (state.max <= 0) return;
  event.preventDefault();
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? box.clientHeight : 1;
  state.target = Math.max(0, Math.min(state.max, state.target + event.deltaY * unit * WHEEL_STEP));
  glides.set(box, state);
  if (!state.frame) state.frame = requestAnimationFrame(function () { glide(box); });
}
function bindWheel(root) {
  const boxes = root.querySelectorAll ? Array.from(root.querySelectorAll('.scroll')) : [];
  if (root.matches && root.matches('.scroll')) boxes.push(root);
  boxes.forEach(function (box) {
    if (wheeled.has(box)) return;
    wheeled.add(box);
    box.addEventListener('wheel', onWheel, { passive: false });
  });
}
// cada swap (vista, fuera de banda, morph) avisa con htmx:load; el back del historial reemplaza el body entero
if (window.htmx) htmx.onLoad(bindWheel);
document.addEventListener('htmx:historyRestore', function () { bindWheel(document); });
document.addEventListener('htmx:sendError', function () {
  const notice = document.getElementById('action-notice');
  if (notice) notice.innerHTML = '<div class="aviso warn">No pudimos conectar. Proba de nuevo.</div>';
});
