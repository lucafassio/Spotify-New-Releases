// Asa de playlist: arrastrar una Fila de playlist la cambia de lugar; al soltar se guarda el orden del User, que vale para todos sus Canales.
(function () {
  let fila = null;
  let antes = null;

  function movibles(lista) {
    return Array.from(lista.querySelectorAll(':scope > .pl:not(.off)'));
  }

  function ids(lista) {
    return movibles(lista).map(function (el) { return el.id.slice('pl-'.length); }).join(',');
  }

  function aviso(texto) {
    const notice = document.getElementById('action-notice');
    if (!notice) return;
    const div = document.createElement('div');
    div.className = 'aviso warn';
    div.dataset.n = 'Aviso de accion';
    div.textContent = texto;
    notice.replaceChildren(div);
  }

  document.addEventListener('dragstart', function (event) {
    const asa = event.target.closest && event.target.closest('.asa');
    if (!asa) return;
    fila = asa.closest('.pl');
    antes = movibles(fila.parentNode);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', fila.id);
    event.dataTransfer.setDragImage(fila, 16, 16);
    // despues de que el navegador saco la imagen de arrastre
    setTimeout(function () { if (fila) fila.classList.add('arrastrando'); });
  });

  document.addEventListener('dragover', function (event) {
    if (!fila) return;
    event.preventDefault();
    const sobre = event.target.closest && event.target.closest('.pl:not(.off)');
    if (!sobre || sobre === fila || sobre.parentNode !== fila.parentNode) return;
    const caja = sobre.getBoundingClientRect();
    const despues = event.clientY > caja.top + caja.height / 2;
    const destino = despues ? sobre.nextElementSibling : sobre;
    if (destino !== fila && fila.nextElementSibling !== destino) fila.parentNode.insertBefore(fila, destino);
  });

  document.addEventListener('drop', function (event) {
    if (fila) event.preventDefault();
  });

  document.addEventListener('dragend', function () {
    if (!fila) return;
    const lista = fila.parentNode;
    const previas = antes;
    fila.classList.remove('arrastrando');
    fila = null;
    antes = null;

    const orden = ids(lista);
    if (orden === previas.map(function (el) { return el.id.slice('pl-'.length); }).join(',')) return;

    function deshace() {
      // las No disponible siguen al fondo: las movibles vuelven delante de la primera de ellas
      const fondo = lista.querySelector(':scope > .pl.off');
      previas.forEach(function (el) { lista.insertBefore(el, fondo); });
      aviso('No pudimos guardar el orden. Proba de nuevo.');
    }

    fetch('/playlists/orden', { method: 'POST', body: new URLSearchParams({ ids: orden }), credentials: 'same-origin' })
      .then(function (response) { if (!response.ok) deshace(); })
      .catch(deshace);
  });
})();
