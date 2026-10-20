import time
from collections import OrderedDict
from copy import deepcopy
from threading import Event, Lock


class WebCache:

    def __init__(self, limit=128):
        '''
        Crea un cache acotado con una sola carga concurrente por clave.
            Args:
                limit (int): maximo de entradas en memoria
        '''
        self.limit = limit
        self.entries = OrderedDict()
        self.loading = {}
        self.lock = Lock()
        self.generation = 0

    def get(self, key, load, ttl=60):
        '''
        Reutiliza una entrada vigente o carga una copia nueva, sin guardar errores.
            Args:
                key (tuple): clave cuyo primer elemento identifica al User
                load (callable): obtiene el dato si falta
                ttl (int): segundos de vigencia
            Returns:
                value (object): copia independiente del dato
        '''
        while True:
            with self.lock:
                cached = self.entries.get(key)
                if cached and cached[0] > time.monotonic():
                    self.entries.move_to_end(key)
                    return deepcopy(cached[1])

                pending = self.loading.get(key)
                if pending is None:
                    pending = self.loading[key] = Event()
                    generation = self.generation
                    break

            if not pending.wait(timeout=10):
                raise TimeoutError('La biblioteca sigue cargando. Proba de nuevo.')

        try:
            value = load()
            with self.lock:
                # una reconexion o escritura invalida tambien las cargas que ya estaban en vuelo
                if generation == self.generation:
                    self.entries[key] = (time.monotonic() + ttl, deepcopy(value))
                    self.entries.move_to_end(key)

                    while len(self.entries) > self.limit:
                        self.entries.popitem(last=False)
            return value
        finally:
            with self.lock:
                self.loading.pop(key, None)
                pending.set()

    def clear(self, user_id=None):
        '''
        Descarta las entradas de un User, o todas al cerrar el servidor.
            Args:
                user_id (int): id interno, o None para vaciar todo
        '''
        with self.lock:
            self.generation += 1
            keys = [key for key in self.entries if user_id is None or key[0] == user_id]

            for key in keys:
                self.entries.pop(key, None)
