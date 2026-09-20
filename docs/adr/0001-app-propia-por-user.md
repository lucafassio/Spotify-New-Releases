---
status: accepted
---

# Cada User puede traer su propia Spotify App

Spotify limita una app en development mode a 5 cuentas autorizadas y solo aprueba
extended quota a empresas, asi que un unico registro de app nos deja en Luca + 4.
Decidimos que el servicio soporte dos modos de conexion: la **Shared App** (el registro de
Luca, cero setup para el User, 4 cupos reservados para amigos no tecnicos) y la **Own App**
(el User registra su propia app en el dashboard de Spotify, pega client id y secret en la
web, y usa sus propios 5 cupos). Own App es el modo preferido; requiere que el User tenga
Premium, que es un requisito de Spotify sobre el dueno del registro y no lo podemos absorber.

## Consecuencias

- El flujo OAuth usa el client id del User, no uno global. El redirect URI es el mismo para
  todos (nuestro servidor) y cada User lo registra en su app.
- Hay que guardar client id y client secret por User, cifrados, junto con sus tokens.
- Cada Own App tiene su propia cuota de rate limit y de quota por cuenta de desarrollador,
  lo que de paso reparte la carga.
- El onboarding de Own App necesita instrucciones paso a paso en la UI (crear app, redirect
  URI exacto, User Management con su propio email).
