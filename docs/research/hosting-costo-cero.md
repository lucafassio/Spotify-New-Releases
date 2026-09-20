# Hosting costo cero para web + scheduler + DB

Ticket: https://github.com/lucafassio/spotify-new-releases/issues/3
Fecha de relevamiento: 2026-09-19. Fuentes primarias (docs/pricing oficiales) salvo donde se marca UNVERIFIED.

## Que necesitamos

- **Web**: FastAPI server-rendered, recibe OAuth callback de Spotify + UI de config. Trafico minimo (~25 users).
- **Scheduler**: al menos 1 corrida por hora (cada user elige hora local). Cada corrida: pocas llamadas a `api.spotify.com`, IO-bound.
- **DB**: refresh tokens, config por user, registro de tracks agregados. Decenas de KB, cientos de filas.
- **Restriccion dura**: cero dinero, siempre. Sin tarjeta si se puede evitar.

## Tabla comparativa

Leyenda: Duerme = el proceso se apaga sin trafico. Estado = conserva disco/DB entre corridas. Tarjeta = exige tarjeta al registrarse.

| Plataforma | Rol posible | Limite free relevante | Duerme | Estado | Tarjeta | Riesgo cambio de terminos | Fuente |
|---|---|---|---|---|---|---|---|
| GitHub Actions `schedule` | Scheduler | 2000 min/mes en repo privado (ilimitado en publico); intervalo minimo 5 min; corre en UTC; puede demorarse en horas pico ("start of every hour") y "some queued jobs may be dropped"; en repo publico se desactiva tras 60 dias sin actividad | N/A (job efimero) | No (runner efimero; DB debe ser externa o commit al repo) | No. Sin metodo de pago el uso se bloquea al agotar cuota, no se cobra | Bajo: en 2026 bajaron precios y "free usage minute quotas remain unchanged" | [billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions), [schedule](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows), [changelog 2026](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/) |
| Oracle Cloud Always Free VM | Web + scheduler + DB (todo en uno) | 2x VM.Standard.E2.1.Micro (1/8 OCPU, 1 GB) o A1 Flex hasta 2 OCPU / 12 GB; 200 GB block storage; IP publica | No, pero Oracle **reclama VMs idle**: 7 dias con CPU p95 < 20%, red < 20% y (A1) memoria < 20% | Si (disco persistente, SQLite en disco) | **Si** ("most users need a mobile phone number and a credit card"; no se cobra salvo upgrade) | Medio: capacity A1 escasa por region (UNVERIFIED, reportes de comunidad), reclamo de idle documentado | [always free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [free tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm) |
| Render Free web service | Web | 750 h instancia/mes por workspace; 512 MB RAM, 0.1 CPU; spin down tras 15 min sin trafico; sin disco persistente; **cron jobs NO tienen tier free** (min USD 1/mes); Postgres free 1 GB expira a los 30 dias | Si (15 min) | No (sin disco; Postgres free expira) | No (sin metodo de pago suspende en vez de cobrar) | Medio: el tier free cambio varias veces (Postgres free ahora expira) | [free](https://render.com/docs/free), [cronjobs](https://render.com/docs/cronjobs) |
| Fly.io | - | **Sin tier free** hoy: pay-as-you-go, maquina minima ~USD 2/mes, maquinas detenidas cobran rootfs; tarjeta o credito prepago min USD 25 | - | - | **Si** | Alto: ya elimino el free tier | [pricing](https://fly.io/docs/about/pricing/), [billing](https://fly.io/docs/about/billing/) |
| Koyeb Free instance | Web | 1 instancia free por org: 0.1 vCPU, 512 MB, 2 GB SSD; Frankfurt o Washington; scale to zero tras 1 h sin trafico; no sirve como Worker; sin Volumes; Postgres free 1 GB pero solo **5 h activas/mes** | Si (1 h) | No (disco efimero, DB free casi inutilizable) | Conflicto: FAQ pricing dice pre-auth hold de USD 29; blog 2023 dice tarjeta solo si no pueden verificar que sos humano. Asumir **probable** | Medio | [instances](https://www.koyeb.com/docs/reference/instances), [faq pricing](https://www.koyeb.com/docs/faqs/pricing), [blog](https://www.koyeb.com/blog/sustaining-free-compute-in-a-hostile-environment) |
| PythonAnywhere Beginner | Web (+ DB en disco) | 1 web app en `user.pythonanywhere.com`; 512 MB disco persistente; 100 s CPU/dia; salida a internet solo a allowlist (`api.spotify.com` y `accounts.spotify.com` **estan** en la lista); desde 2026-01-15: web app expira al **mes** si no la renovas a mano, **scheduled tasks ya no** en free (solo cuentas creadas antes), MySQL solo en tier pago | No mientras este renovada (expira mensualmente) | Si (disco 512 MB; SQLite en disco OK) | No | Alto: acaban de recortar free (ene 2026) | [pricing](https://www.pythonanywhere.com/pricing/), [allowlist](https://www.pythonanywhere.com/whitelist/), [tasks](https://help.pythonanywhere.com/pages/ScheduledTasks/), [cambios 2026](https://blog.pythonanywhere.com/221/) |
| Vercel Hobby | Web (FastAPI como Function) | FastAPI/ASGI soportado nativo (Python 3.12-3.14); 1M invocaciones, 4 CPU-h, 300 s max por function; **cron Hobby: max 1 vez por dia, precision +-59 min**; solo uso no comercial; filesystem efimero | Si (serverless, cold start) | No | No | Medio: cron Hobby ya fue recortado a diario | [cron](https://vercel.com/docs/cron-jobs/usage-and-pricing), [python](https://vercel.com/docs/functions/runtimes/python), [hobby](https://vercel.com/docs/plans/hobby) |
| Cloudflare Workers (Python) | Web + scheduler | Free: 100k req/dia, **10 ms CPU por invocacion**, 50 subrequests/req; Cron Triggers: 5 por cuenta en free, wall clock hasta 15 min; Python Workers corren en Pyodide, requieren flag `python_workers`, FastAPI listado como soportado, `scheduled` handler existe en Python; paquetes solo si hay wheel pure-Python o PyEmscripten ("still in early stages"). D1 (SQLite) free: 5 GB, 5M lecturas/dia, 100k escrituras/dia | No (edge, siempre disponible) | Si via D1/KV | No (UNVERIFIED: doc dice "By default, users have access to the Workers Free plan", no menciona tarjeta) | Medio-alto: Python Workers es beta (UNVERIFIED el estado exacto), limites de CPU free ajustados para Pyodide | [python](https://developers.cloudflare.com/workers/languages/python/), [packages](https://developers.cloudflare.com/workers/languages/python/packages/), [pricing](https://developers.cloudflare.com/workers/platform/pricing/), [limits](https://developers.cloudflare.com/workers/platform/limits/), [cron](https://developers.cloudflare.com/workers/configuration/cron-triggers/) |
| Supabase Free (Postgres) | DB | 2 proyectos activos; 500 MB DB; 5 GB egress; **pausa tras 7 dias de baja actividad** ("a few user requests to the database each day" alcanza); restore manual; 90 dias de restore con un click, luego backup a mano | Si (pausa 7 dias idle) | Si | No (UNVERIFIED: pricing no lo menciona) | Medio: pausas existen para empujar a Pro | [pricing](https://supabase.com/pricing), [pausing](https://supabase.com/docs/guides/platform/free-project-pausing) |
| Neon Free (Postgres) | DB | 0.5 GB/proyecto; 100 CU-h/mes por proyecto (0.25 CU = 400 h); **scale to zero a los 5 min, no desactivable**; 100 proyectos; 6 h de PITR | Si (5 min; cold start en cada conexion) | Si | No (UNVERIFIED: doc no lo menciona) | Medio | [plans](https://neon.com/docs/introduction/plans) |
| Turso Free (SQLite/libSQL) | DB | 100 DBs; 5 GB; 500M lecturas/mes; 10M escrituras/mes; "no credit card required"; **archiva DB tras 10 dias de inactividad** (se desarchiva por API) | Si (archivo a 10 dias) | Si | No | Medio: cambiaron limites varias veces | [pricing](https://turso.tech/pricing), [unarchive](https://docs.turso.tech/api-reference/groups/unarchive) |
| Cloudflare D1 (SQLite) | DB | 5 GB total, 5M filas leidas/dia, 100k escritas/dia; solo accesible desde Workers (o via REST API con token) | No | Si | No (UNVERIFIED) | Bajo-medio | [pricing](https://developers.cloudflare.com/workers/platform/pricing/) |
| SQLite en disco | DB | Solo donde hay disco persistente: Oracle VM (200 GB), PythonAnywhere (512 MB). Render free, Koyeb free, Vercel, GitHub Actions: NO | N/A | Depende del host | - | Depende del host | ver filas correspondientes |

### Notas transversales

- **Scheduler hourly** solo lo cumplen gratis: GitHub Actions (5 min minimo, con jitter), Oracle VM (cron/systemd propio), Cloudflare Cron Triggers. NO: PythonAnywhere (sin tasks en free nuevas), Vercel Hobby (diario), Render (cron pago), Koyeb (sin workers free).
- **Jitter GitHub Actions**: usar un minuto raro (ej. `17 * * * *`), no `0 * * * *`. El job debe ser idempotente y tolerar corridas dropeadas: calcular "users vencidos desde la ultima corrida exitosa" en vez de "users cuya hora es ahora".
- **Consumo GitHub Actions**: 24 corridas/dia x ~1-2 min de setup+ejecucion = 720-1500 min/mes. Entra en los 2000 de repo privado pero con poco margen si se suma CI. Repo publico = ilimitado (secrets siguen ocultos).
- **Keep-alive de DBs que duermen**: un scheduler hourly que toca la DB evita la pausa de Supabase (7 dias) y el archivo de Turso (10 dias). Neon igual despierta en cada conexion (cold start de segundos, aceptable para batch).
- **OAuth callback**: necesita URL estable y HTTPS. Todas las opciones web dan subdominio HTTPS gratis. Oracle VM requiere configurar TLS a mano (Caddy/Let's Encrypt) o usar Cloudflare delante.
- **Tarjeta**: las unicas opciones web sin tarjeta y sin dormir son Cloudflare Workers (Python en beta) y PythonAnywhere (renovacion manual mensual). Oracle exige tarjeta; Koyeb probable; Fly obligatoria.

## Combinaciones viables

### A. GitHub Actions (scheduler) + Render Free (web) + Turso o Supabase (DB)

- Web: FastAPI en Render free. Duerme a los 15 min; el OAuth callback tarda ~1 min en despertar la primera vez (aceptable: el user espera una vez).
- Scheduler: workflow `schedule` cada hora en el mismo repo, corre el script Python contra la DB externa. Sin runner persistente, no hay estado local.
- DB: Turso (SQLite remoto, sin tarjeta, 10 dias de archivo) o Supabase (Postgres, 7 dias de pausa). El cron hourly la mantiene despierta.
- Trade-offs: 3 proveedores, 3 puntos de falla y 3 juegos de secrets. Jitter y drops de Actions obligan a logica idempotente. Render y las DBs free tienen historial de recortes. Cero tarjeta en toda la cadena.

### B. Oracle Cloud Always Free VM (todo en uno)

- Web + scheduler (cron/systemd) + SQLite en disco en una sola VM. Sin sleep, sin jitter, sin limites de horas. Estado en disco propio, backups por rsync/git.
- Trade-offs: exige tarjeta al registrarse (no se cobra sin upgrade). Oracle reclama VMs idle (7 dias < 20% CPU/red): un cron hourly liviano NO alcanza, hay que asegurar carga o aceptar que la reclamen y recrear. Capacity de A1 escasa (UNVERIFIED); E2.1.Micro (1 GB RAM) alcanza para FastAPI + SQLite. TLS y hardening de la VM son responsabilidad propia (Windows local + SSH, mas trabajo de ops).

### C. Cloudflare Workers Python (web + cron) + D1 (DB)

- Todo en un proveedor, sin tarjeta, sin sleep, Cron Triggers nativos (5 en free), D1 es SQLite gestionado.
- Trade-offs: Python Workers = Pyodide, beta. FastAPI esta listado como soportado pero cualquier dependencia sin wheel pure-Python/PyEmscripten rompe (ej. clientes HTTP con extensiones C). 10 ms de CPU por invocacion en free es muy poco para Pyodide (UNVERIFIED si aplica igual a cron; el limite documentado para cron es 15 min wall clock). Requiere reescribir la app al modelo Worker (no uvicorn, fetch/scheduled handlers). Riesgo alto de pelearse con la plataforma en vez de con el producto.

### Variante de A: GitHub Actions + PythonAnywhere (web + SQLite en disco)

- PythonAnywhere free tiene disco persistente y `api.spotify.com` / `accounts.spotify.com` en la allowlist, asi que web + SQLite viven ahi sin tarjeta. Pero: sin scheduled tasks en free (desde 2026-01-15) y la web app expira cada mes si no se renueva a mano. El scheduler tendria que ser GitHub Actions llamando a un endpoint HTTP de la app (que a su vez corre el job contra su SQLite local), lo que ata el job a los 100 s CPU/dia del plan free. Fragil; se lista por completitud.

## Descartados y por que

- **Fly.io**: sin free tier, tarjeta obligatoria.
- **Koyeb**: la instancia free sirve solo como web (duerme a la hora, sin workers, sin volumes) y la DB free tiene 5 h activas/mes. Tarjeta probable.
- **Vercel Hobby como scheduler**: cron diario con +-59 min no cumple "al menos hourly". Sirve solo como web (FastAPI nativo), combinable con GitHub Actions como scheduler si se prefiere a Render (sin sleep de 15 min pero con cold start serverless).
- **Render cron**: minimo USD 1/mes.

## Pendientes / UNVERIFIED

- Estado exacto (beta/GA) de Cloudflare Python Workers y si el limite de 10 ms CPU aplica a invocaciones cron en free.
- Si Supabase, Neon y Cloudflare piden tarjeta en el signup (docs no lo mencionan; Turso dice explicitamente que no).
- Disponibilidad real de capacity A1 en Oracle (reportes de comunidad, no doc oficial).
- Politica exacta de tarjeta en Koyeb (dos fuentes oficiales se contradicen).
