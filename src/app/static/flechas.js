// Flechas de artista con respuesta inmediata (#27): al click la fila cambia de columna y el contador se mueve;
// la respuesta del servidor confirma (sus swaps fuera de banda reemplazan las filas armadas aca, sin duplicar) o deshace con un Aviso.
(function () {
  // por form, los movimientos que todavia no salieron: la cola del Panel los manda en orden
  const esperando = new WeakMap();
  // por pedido en vuelo, el movimiento que lo origino
  const porPedido = new WeakMap();
  // por artista, cuantos movimientos siguen sin respuesta: solo el ultimo toca sus filas
  const pendientes = new Map();
  let enVuelo = 0;
  // movimiento cuya respuesta se esta aplicando
  let actual = null;
  // de que playlist salio cada artista, para devolverlo al instante si se saca
  const origen = new Map();
  // movimientos que todavia esperan en la cola del Panel
  const sinSalir = new Set();

  function oculta(el) { el.style.display = 'none'; }
  function muestra(el) { el.style.display = ''; }
  function visible(el) { return el.style.display !== 'none'; }

  function filasVisibles(lista) {
    return lista ? Array.from(lista.querySelectorAll('[data-sort]')).filter(visible) : [];
  }

  // inserta en orden alfabetico, con la misma clave que usa el reordenado de base.html
  function ubica(lista, fila) {
    const siguiente = Array.from(lista.children).find(function (otra) {
      return otra !== fila && otra.dataset.sort !== undefined && otra.dataset.sort > fila.dataset.sort;
    });
    lista.insertBefore(fila, siguiente || null);
    htmx.process(fila);
  }

  function playlistAbierta() {
    const abierta = document.querySelector('[data-playlist][aria-expanded="true"]');
    return abierta ? abierta.dataset.playlist : null;
  }

  function nombre(texto) {
    const span = document.createElement('span');
    span.className = 'grow ellip';
    span.textContent = texto;
    return span;
  }

  function formulario(action, tipo, campos, etiqueta, flecha) {
    const form = document.createElement('form');
    form.method = 'post';
    form.action = action;
    form.dataset.flecha = tipo;
    form.setAttribute('hx-post', action);
    form.setAttribute('hx-swap', 'none');
    form.setAttribute('hx-disabled-elt', 'find button');
    Object.keys(campos).forEach(function (campo) {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = campo;
      input.value = campos[campo];
      form.append(input);
    });
    const boton = document.createElement('button');
    boton.className = 'flecha';
    boton.setAttribute('aria-label', etiqueta);
    boton.dataset.n = 'Flecha de artista';
    boton.textContent = flecha;
    form.append(boton);
    return form;
  }

  // misma forma que la macro whitelist_row
  function filaDerecha(canalId, artista, texto, clave, avatar) {
    const fila = document.createElement('div');
    fila.className = 'fila';
    fila.id = 'wl-' + artista;
    fila.dataset.sort = clave;
    fila.dataset.n = 'Fila de artista';
    const form = formulario('/canales/' + canalId + '/artistas/quitar', 'quitar', { artist_id: artista, open: '' }, 'Sacar a ' + texto, '←');
    fila.append(form, avatar.cloneNode(true), nombre(texto));
    return fila;
  }

  // suma a la Columna derecha: reusa la fila si sigue oculta esperando que se confirme una salida
  function entra(op, canalId, artista, texto, clave, avatar) {
    const lista = document.getElementById('whitelist-filas');
    const fila = document.getElementById('wl-' + artista);
    if (fila && !visible(fila)) {
      muestra(fila);
      op.mostradas.push(fila);
    } else if (!fila && lista) {
      const nueva = filaDerecha(canalId, artista, texto, clave, avatar);
      ubica(lista, nueva);
      op.creadas.push(nueva);
    }
  }

  function sale(op, fila, artista, playlist) {
    if (!fila || !visible(fila)) return;
    origen.set(artista, { playlist: playlist, fila: fila.cloneNode(true) });
    oculta(fila);
    op.ocultas.push(fila);
  }

  function mueve(form, canalId) {
    const tipo = form.dataset.flecha;
    const artista = form.elements.artist_id.value;
    const op = { form: form, tipo: tipo, artista: artista, ocultas: [], mostradas: [], creadas: [] };
    const abierta = playlistAbierta();

    if (tipo === 'sumar') {
      const fila = form.closest('.fila');
      entra(op, canalId, artista, fila.querySelector('.ellip').textContent, fila.dataset.sort, fila.querySelector('.avatar'));
      sale(op, fila, artista, form.elements.playlist_id.value);
    } else if (tipo === 'manual') {
      const texto = form.elements.name.value;
      const avatar = form.querySelector('.avatar');
      // el Resultado queda apagado como "added"; el form sigue en la pagina para que htmx termine su pedido
      const apagado = document.createElement('div');
      apagado.id = form.id;
      apagado.className = 'res added';
      apagado.dataset.n = 'Resultado agregado';
      const tag = document.createElement('small');
      tag.className = 'tag';
      tag.textContent = 'added';
      apagado.append(avatar.cloneNode(true), nombre(texto), tag);
      op.idForm = form.id;
      form.removeAttribute('id');
      oculta(form);
      form.after(apagado);
      op.ocultas.push(form);
      op.creadas.push(apagado);
      entra(op, canalId, artista, texto, form.dataset.sort, avatar);
      if (abierta) sale(op, document.getElementById('art-' + abierta + '-' + artista), artista, abierta);
    } else if (tipo === 'quitar') {
      const fila = form.closest('.fila');
      oculta(fila);
      op.ocultas.push(fila);
      // vuelve a la playlist abierta solo si se sabe que es de ahi; si no aparece con la respuesta
      const lista = abierta && document.getElementById('artistas-' + abierta);
      const izquierda = abierta && document.getElementById('art-' + abierta + '-' + artista);
      const vino = origen.get(artista);
      if (izquierda && !visible(izquierda)) {
        muestra(izquierda);
        op.mostradas.push(izquierda);
      } else if (!izquierda && lista && vino && vino.playlist === abierta && !lista.querySelector('small:not([id])')) {
        const vuelta = vino.fila.cloneNode(true);
        muestra(vuelta);
        ubica(lista, vuelta);
        op.creadas.push(vuelta);
      }
    } else {
      return null;
    }
    return op;
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

  // contadores, mensajes de vacio y Bloque de nuevos, contando solo lo que se ve
  function recuenta() {
    const panel = document.getElementById('canal-panel');
    const lista = document.getElementById('whitelist-filas');
    if (!panel || !lista) return;
    const total = filasVisibles(lista).length;
    const texto = total + ' artistas';
    const contador = document.getElementById('whitelist-count');
    if (contador) contador.textContent = texto;
    const fila = document.querySelector('.nav-row[href="/canales/' + panel.dataset.canalId + '"] small');
    if (fila) fila.textContent = texto;
    const vaciar = document.getElementById('wl-vaciar');
    if (vaciar) vaciar.hidden = !total;

    const vacio = document.getElementById('wl-vacio');
    if (total && vacio) vacio.remove();
    if (!total && !vacio) {
      const small = document.createElement('small');
      small.id = 'wl-vacio';
      small.textContent = 'Elegi artistas desde una playlist o buscalos.';
      lista.append(small);
    }

    document.querySelectorAll('[data-bloque]').forEach(function (bloque) {
      const cuantos = filasVisibles(bloque).length;
      bloque.hidden = cuantos === 0;
      bloque.querySelector('[data-bloque-count]').textContent = cuantos === 1 ? '1 artista nuevo' : cuantos + ' artistas nuevos';
    });

    document.querySelectorAll('[id^="artistas-"]').forEach(function (artistas) {
      // cargando o con error de lectura no se toca
      if (artistas.querySelector('small:not([id])')) return;
      const playlist = artistas.id.slice('artistas-'.length);
      const quedan = filasVisibles(artistas).length + filasVisibles(document.getElementById('nuevos-' + playlist)).length;
      const todos = document.getElementById('vacio-' + playlist);
      if (quedan && todos) todos.remove();
      if (!quedan && !todos) {
        const small = document.createElement('small');
        small.id = 'vacio-' + playlist;
        small.textContent = 'Ya estan todos';
        artistas.append(small);
      }
    });
  }

  function deshace(op) {
    op.creadas.forEach(function (el) { el.remove(); });
    op.mostradas.forEach(oculta);
    op.ocultas.forEach(muestra);
    if (op.idForm) op.form.id = op.idForm;
  }

  // captura: corre antes que htmx, aunque el pedido quede en la cola del Panel
  document.addEventListener('submit', function (event) {
    const form = event.target;
    const panel = document.getElementById('canal-panel');
    if (!window.htmx || !panel || !form.matches || !form.matches('form[data-flecha][hx-post]')) return;
    const op = mueve(form, panel.dataset.canalId);
    if (!op) return;
    const cola = esperando.get(form) || [];
    cola.push(op);
    esperando.set(form, cola);
    sinSalir.add(op);
    pendientes.set(op.artista, (pendientes.get(op.artista) || 0) + 1);
    enVuelo += 1;
    recuenta();
  }, true);

  document.addEventListener('htmx:beforeRequest', function (event) {
    const cola = esperando.get(event.detail.elt);
    if (!cola || !cola.length) return;
    const op = cola.shift();
    sinSalir.delete(op);
    porPedido.set(event.detail.xhr, op);
  });

  document.addEventListener('htmx:beforeOnLoad', function (event) {
    actual = porPedido.get(event.detail.xhr) || null;
  });

  function delArtista(id, artista) {
    return id === 'wl-' + artista || id === 'res-' + artista || (id.indexOf('art-') === 0 && id.slice(-artista.length - 1) === '-' + artista);
  }

  // la respuesta de un movimiento viejo no pisa lo que el User hizo despues; la ultima pone todo al dia
  document.addEventListener('htmx:oobBeforeSwap', function (event) {
    if (!actual) return;
    const detail = event.detail;
    const hijos = Array.from(detail.fragment.children || []);
    const ids = [detail.target.id || ''].concat(hijos.map(function (hijo) { return hijo.id || ''; }));
    const deArtista = ids.some(function (id) { return id && delArtista(id, actual.artista); });
    const despues = deArtista ? pendientes.get(actual.artista) > 1 : enVuelo > 1;
    if (despues) {
      detail.shouldSwap = false;
      return;
    }
    // una fila que ya armo el navegador se reemplaza en su lugar en vez de sumarse otra vez
    hijos.forEach(function (hijo) {
      const previa = hijo.id && document.getElementById(hijo.id);
      if (previa && previa !== detail.target) {
        previa.replaceWith(hijo);
        htmx.process(hijo);
      }
    });
  });

  function descuenta(op) {
    pendientes.set(op.artista, pendientes.get(op.artista) - 1);
    if (!pendientes.get(op.artista)) pendientes.delete(op.artista);
    enVuelo -= 1;
  }

  function cierra(op, ok) {
    descuenta(op);
    if (ok) {
      if (op.tipo === 'manual') op.form.remove();
    } else if (!pendientes.has(op.artista)) {
      // si hay otro movimiento del mismo artista en camino, su respuesta deja la fila donde corresponde
      deshace(op);
    }
  }

  document.addEventListener('htmx:afterRequest', function (event) {
    const detail = event.detail;
    actual = null;
    const op = porPedido.get(detail.xhr);
    if (!op) return;
    // htmx repite el evento en un padre si el form salio de la pagina: se atiende una sola vez
    porPedido.delete(detail.xhr);
    const rechazado = detail.xhr.status && detail.xhr.getResponseHeader('HX-Retarget');
    const ok = detail.successful === true && !rechazado;
    cierra(op, ok);
    // un error del servidor no trae Aviso; el de red lo pone base.html y el rechazo lo trae la respuesta
    if (!ok && !rechazado && detail.xhr.status) aviso('No pudimos guardar el cambio. Proba de nuevo.');

    // un movimiento cuyo form salio de la pagina antes de mandarse (la lista se releyo) ya no va a volver
    sinSalir.forEach(function (viejo) {
      if (document.body.contains(viejo.form)) return;
      sinSalir.delete(viejo);
      descuenta(viejo);
    });
    recuenta();
  });
})();
