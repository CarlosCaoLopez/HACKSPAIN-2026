# Plan · `/demo` autoservicio: teléfonos por petición, Minecraft en la web y despliegue en VPS

> Documento de traspaso. Parte del trabajo **ya está hecho en el árbol** (sin commitear).
> Lo que sigue describe el porqué, lo hecho y lo que queda, con rutas concretas.
> Repo: `HACKSPAIN-2026` (`vela`, HackSpain 2026, reto HappyRobot). Lee antes `CLAUDE.md`.

---

## 1. Contexto y objetivo

Hoy la demo la pilota una persona: `make server` + `make world` + un cliente Minecraft
real en modo espectador en una segunda pantalla capturado por OBS + `make demo`, con los
teléfonos fijos en `.env` y el gateway detrás de un túnel ngrok cuya URL cambia en cada
reinicio (`scripts/retunnel.py` repunta diez sitios a mano).

**Objetivo:** desde una landing externa (otro repo), en `/demo`, cualquiera rellena cinco
teléfonos con su papel, pulsa «empezar», y ve en la web app el Minecraft en vivo mientras
esos móviles reciben las llamadas, igual que en el vídeo de la demo. Sin operador, con URL
estable, en un VPS.

### Decisiones ya tomadas por el usuario (no volver a preguntarlas)

| Decisión | Elegido |
| --- | --- |
| 5º teléfono | **El vecino que llama**: el móvil desde el que el visitante marcará al 112 de la demo. Los otros cuatro son los salientes. |
| Minecraft en la web | **Cliente real headless en el servidor + stream**. Cámara fija de «vista de águila» que enseñe todo: pueblos, fuego, ambulancias. Sin director ni teclas. |
| Dónde vive el formulario | **En la landing** (otro repo), llamando a nuestra API cross-origin. No se hace pantalla de formulario aquí. |
| Puerta de acceso | **Ninguna.** Sin código, sin rate limit por IP. Queda solo el candado técnico de «un run a la vez» + auto-stop. |

### Hallazgos que condicionan el diseño

- Los teléfonos **ya** eran variables de entorno (`PHONE_PUEBLO_A/B`, `PHONE_FIRE_CREW`,
  `PHONE_AMBULANCE`, `PHONE_OVERRIDE`). Lo que faltaba era poder **sobrescribirlos por
  petición** y el quinto (vecino).
- `core.loop` lee el `settings` global en seis sitios (`loop.py:947,1003,1053,1099,1123,1181,1239`)
  y no escucha `run.started`. Con un proceso y un run a la vez, **mutar `settings` durante
  el run** es lo mínimo que funciona y no obliga a tocar `core` (que es de otra persona).
- Paper es **1.21.11** (`infra/server/paper-1.21.11.jar`): el cliente debe ser exactamente
  esa versión o dice «outdated server».
- `prismarine-viewer` llega a 1.21.4 y no pinta `block_display` (los camiones) ni
  `armor_stand` (los carteles). Descartado, como ya decía `docs/backbon_corrected.md:298-304`.
- La wifi del evento solo deja salir por 80/443 y bloquea UDP (medido, ver cabecera de
  `scripts/retunnel.py`): el vídeo tiene que ir por **HLS sobre HTTPS**; WebRTC solo como extra.
- El gateway no servía el dashboard (Vite proxy en dev) y no tenía CORS.

---

## 2. Arquitectura del despliegue

Un VPS x86 con `docker compose`, cinco servicios:

```
Internet ──443──▶ caddy ─┬─ /cam/*        → mediamtx:8888  (HLS del Minecraft)
                         └─ resto          → gateway:8000  (API, WS, webhooks y el dashboard construido)
gateway ── RCON ──▶ paper (itzg/minecraft-server, Paper 1.21.11, mundo plano)
cam ── entra como jugador ──▶ Xvfb + Mesa/llvmpipe + cliente Minecraft real ──▶ ffmpeg ──▶ mediamtx
```

Solo `caddy` publica puertos (80/443). Paper **no** se expone: nadie externo entra al
servidor y hay un único gateway mandando sobre él («un solo mundo a la vez»,
`infra/README.md`).

**Proveedor recomendado:** Hetzner, Falkenstein/Núremberg (cerca de
`platform.eu.happyrobot.ai`). Empezar en **CPX42** (8 vCPU AMD compartidas, 16 GB) y subir
a CCX (vCPU dedicada) solo si la cámara no pasa de ~10 fps. Se factura por horas: para la
semana del hackathon son unos pocos euros. **Nada de ARM** (Hetzner CAX, más barato): los
nativos LWJGL del manifest de Mojang son linux-x64.

Alternativas descartadas: PaaS tipo Fly/Railway/Render (vale para el gateway solo, pero un
cliente Minecraft con Xvfb necesita CPU dedicada y contenedores compartiendo red y
volumen); seguir con portátil + ngrok (no es autoservicio y la URL cambia).

**Cuenta de Minecraft:** el cliente headless necesita una cuenta Java
(`portablemc login`, código de dispositivo, sesión refrescable en un volumen). Sin ella
entra en offline con `-u <nombre>` y Paper lo acepta (`online-mode=false`), pero es zona
gris del EULA: usar la cuenta de quien graba hoy la demo.

---

## 3. Lo que YA está hecho (en el árbol, sin commitear)

Estado de git al escribir esto: `2dfa482 wip: core, tests, escenario y use_cases pendientes
antes del /demo autoservicio` (commit de lo que había suelto + `git pull --rebase`, ya hecho).
Todo lo de abajo está **modificado o creado y sin commitear**.

### 3.1 Contratos — `packages/contracts/`

`src/contracts/settings.py`:
- `PHONE_KEYS = ("fire_crew", "ambulance", "pueblo_a", "pueblo_b", "neighbor")` — en el
  orden en que suena cada teléfono, que es el del formulario.
- `E164` (regex `^\+[1-9]\d{6,14}$`) y `normalize_phone()` (quita espacios, guiones,
  puntos y paréntesis; no adivina prefijos).
- `Settings.phones()` → los cinco actuales. `Settings.apply_phones(dict)` → los escribe y
  **devuelve los anteriores** para restaurar; una clave fuera de `PHONE_KEYS` lanza `KeyError`.
- Campos nuevos, todos con default (cambio aditivo, libre según `CLAUDE.md`):
  `phone_neighbor`, `happyrobot_inbound_number`, `vela_cors_origins`, `vela_public_url`,
  `vela_landing_url`, `vela_run_max_s` (420), `vela_cam_player`, `vela_cam_hls_url`.
- Se quitó un número de teléfono real que estaba en un comentario (también en `use_cases.md`).

`src/contracts/events.py`: `CallStarted.known_caller: bool = False` (opcional con default).

### 3.2 Voz — `packages/voice/src/voice/webhooks.py`

`_publish_started` marca `known_caller` comparando el `caller_number` normalizado con
`settings.phone_neighbor`. Solo entrantes; una web call (`"web"`) nunca lo es. Helper
`_known_caller(direction, number)`.

### 3.3 Gateway — `apps/gateway/src/gateway/`

`runtime.py`:
- `Runtime.started_at`, `.phones_given`, `.phones_prev`.
- `Runtime.ends_in_s()` → segundos hasta el auto-stop, o `None`.
- `health()` añade `phones` («por petición» / «del .env») y `ends_in_s`. **Nunca un número.**

`main.py`:
- `RunBody.phones: dict[str,str] | None` con `field_validator` que exige clave conocida y
  E.164 normalizado → si no, **422** antes de tocar nada. Un mismo número repetido en
  varios papeles está permitido (el visitante tiene un móvil).
- `start_run(..., phones=...)`: aplica `settings.apply_phones()` **antes** de construir
  `Core`; `stop_run` restaura los del `.env`.
- `_schedule_auto_stop()`: task que para el run a los `VELA_RUN_MAX_S` (0 = nunca). Se da
  de baja de `rt.tasks` antes de llamar a `stop_run` para no cancelarse a sí misma y dejar
  el journal abierto.
- `_place_camera()`: si hay `VELA_CAM_PLAYER` y Minecraft, espera hasta 120 s a que el
  jugador entre (poll cada 5 s por RCON), lo pone en `gamemode spectator` y lo teleporta al
  plano `aguila`. Se anota en `/api/health` → `notes.camera`.
- `POST /api/run` devuelve además `watch_url`, `inbound_number`, `ends_in_s`, `phones`; y
  el 409 de «ya hay run» ahora trae `{reason:"busy", run_id, retry_after_s}`.
- **`GET /api/demo/status`** (nuevo): `busy`, `run_id`, `ends_in_s`, `run_max_s`,
  `inbound_number`, `watch_url`, `landing_url`, `cam_hls_url`, `phone_roles` (los cinco con
  etiqueta y explicación), `default_scenario`. Nunca devuelve teléfonos.
- `PHONE_ROLES`: las etiquetas y explicaciones que pinta la landing, definidas junto a la
  validación para que no se desincronicen.
- CORS (`CORSMiddleware`) solo si `VELA_CORS_ORIGINS` tiene orígenes; nunca `*`.
- `StaticFiles` sobre `apps/dashboard/dist` montado en `/` al final, si la carpeta existe.

### 3.4 Sim — `packages/sim/src/sim/camera.py`

Nuevo `Shot("aguila", …)`: cenital que encuadra **todos los POIs + waypoints** (el `valle`
de siempre deja fuera hospital y refugio a propósito), con el mismo techo de niebla
`MAX_CENITAL_Y = 175`. En `wildfire_ridge` sale `tp <p> 72 175 8 -90 90`.

> Ojo: hoy `aguila` y `valle` coinciden en `wildfire_ridge` porque el bbox ya topa con el
> techo. **Hay que verlo en el servidor**: si desde 175 no se leen hospital y refugio,
> usar `escorzo` como plano de la /demo o bajar la escala en el YAML.

### 3.5 Dashboard — `apps/dashboard/`

- `pnpm add hls.js` (queda en `package.json` y `pnpm-lock.yaml`).
- `src/hooks/useDemoStatus.ts`: poll cada 5 s a `/api/demo/status` + `useCountdown()` para
  que el reloj no dé saltos entre polls.
- `src/panels/CamPanel.tsx`: `<video>` con HLS (nativo en Safari, `hls.js` en el resto),
  estado `loading | playing | error`, corte a los 15 s sin imagen y botón de reintentar.
  Nunca un rectángulo negro mudo.
- `src/views/MinecraftView.tsx`: vídeo grande + `CallsPanel` al lado + cuenta atrás. Si no
  hay `cam_hls_url`, lo dice y remite al mapa.
- `src/components/Waiting.tsx`: sala de espera cuando no hay run (capa sobre el dashboard,
  con «Ver el panel igualmente» para quien desarrolla).
- Tercera vista `minecraft` en `useView.ts`, `ViewIcon.tsx` (icono de cubo) y `Sidebar.tsx`.
- `App.tsx` monta `useDemoStatus`, la vista nueva y la sala de espera.
- `story/calls.ts` → `Call.knownCaller`; `panels/CallsPanel.tsx` → insignia «tu llamada».
- `src/types.ts` regenerado con `make types` (trae `known_caller`).
- `pnpm typecheck` pasa.

### 3.6 Infra — `infra/deploy/` (carpeta nueva) y raíz

- `Dockerfile.gateway`: tres etapas (uv sync del workspace → genera `types.ts` → Node 22 +
  pnpm 9 construye el dashboard → imagen final con el `dist` dentro). Healthcheck a
  `/api/health`.
- `Dockerfile.cam`: Temurin 21 + Xvfb + Mesa/llvmpipe + ffmpeg + `portablemc`, usuario no root.
- `cam/entrypoint.sh`: levanta Xvfb, espera a Paper por TCP, lanza ffmpeg (x11grab → RTSP a
  MediaMTX) en bucle y el cliente (`portablemc start <ver> -s paper -p 25565`) en bucle.
- `cam/options.txt`: opciones precocinadas del cliente (`renderDistance:16`,
  `pauseOnLostFocus:false`, `skipMultiplayerWarning:true`, sonido a 0, nubes y partículas fuera).
- `mediamtx.yml`: RTSP interno, HLS low-latency en 8888, WebRTC encendido por si acaso.
- `Caddyfile`: TLS automático, `/cam/*` → mediamtx, resto → gateway.
- `docker-compose.yml`: los cinco servicios, `depends_on` con `condition: service_healthy`,
  volúmenes (`runs`, `memory`, `paper-data`, `mc-home`, `caddy-*`), `cpus` para la cámara.
  Validado con `docker compose config`.
- `infra/deploy/README.md`: el porqué de cada decisión, el «una vez», el «cada vez» y el
  «si algo falla».
- Raíz: `.dockerignore` nuevo; `.env.example` con el bloque de la /demo y el de despliegue
  (y arreglados los comentarios al final de línea que el propio fichero desaconseja);
  `Makefile` con `deploy-up | deploy-down | deploy-logs | deploy-ps | deploy-login`.

### 3.7 Tests

`tests/test_gateway_demo.py` (nuevo, 7 tests, **en verde**):
- los teléfonos del run mandan y se devuelven al parar;
- sin `phones` mandan los del `.env`;
- tres formas de teléfono malo → 422 y no arranca nada;
- dos demos a la vez → 409 con `retry_after_s`, y `/api/demo/status` sin teléfonos;
- el run se para solo y deja el journal cerrado.

---

## 4. Lo que QUEDA por hacer

1. **`docs/interfaces.md`**: sección «La /demo autoservicio» con el contrato de abajo, y de
   paso la tabla de endpoints está desfasada (no lista `/api/health`, `/api/feeds`,
   `/api/scenario`, `/webhooks/telegram`). El bloque «Variables de entorno» aún menciona
   `JUDGE_PHONE`, que ya no existe.
2. **`make check`** entero (mypy sobre contracts + pytest) — una sola vez, al final.
   Los tests tocados ya pasan; falta la pasada completa.
3. **Commit** (el usuario pidió no commitear todavía).
4. **Probar el compose en local** con Docker arrancado:
   `DOMAIN=localhost docker compose -f infra/deploy/docker-compose.yml up --build`.
   Sin dominio real Caddy no saca certificado: para probar en local, quitar TLS del
   `Caddyfile` o entrar directo al gateway.
5. **Desplegar en el VPS** siguiendo `infra/deploy/README.md`, y **medir los fps** de la
   cámara. Es el riesgo sin medir: si en CPX42 no llega a ~10 fps a 1280x720, bajar a
   960x540 y `renderDistance:10`, o pasar a CCX.
6. **Verificar el plano `aguila`** en el servidor de verdad (ver aviso en 3.4).
7. **`scripts/retunnel.py --apply https://<DOMAIN>`** una vez, para HappyRobot (9 `VELA_URL`)
   y el webhook de Telegram.
8. **La landing** (otro repo): formulario de cinco campos contra el contrato de abajo.
9. Opcional: quitar `infra/docker-compose.yml` (el viejo, de solo Paper, que ya no usa nadie
   y ahora confunde con el de `infra/deploy/`).

---

## 5. Contrato para la landing (otro repo)

```
GET  https://demo.<dominio>/api/demo/status
  → {busy:false, inbound_number:"+1…", run_max_s:420, watch_url, landing_url,
     phone_roles:[{key,label,explica} ×5], default_scenario:"wildfire_ridge"}
  → {busy:true,  run_id, ends_in_s, watch_url, …}

POST https://demo.<dominio>/api/run
  {"scenario_id":"wildfire_ridge",
   "phones":{"fire_crew":"+34…","ambulance":"+34…","pueblo_a":"+34…","pueblo_b":"+34…","neighbor":"+34…"}}
  → 200 {run_id, watch_url, inbound_number, ends_in_s, phones:"por petición", minecraft, mock_calls}
  → 409 {detail:{reason:"busy", run_id, retry_after_s}}
  → 422 si alguna clave no es de PHONE_KEYS o algún número no es E.164
```

Los cinco campos son **obligatorios** en la landing, cada uno con su explicación (la que
devuelve `phone_roles`). Hay que avisar de que **se puede repetir el mismo número**, pero
que entonces llegarán varias llamadas seguidas al mismo móvil y alguna puede dar ocupado.
Tras el 200, la landing lleva al visitante a `watch_url` (redirección o iframe).
El `inbound_number` es al que **el visitante** llama como vecino.

Requiere `VELA_CORS_ORIGINS=https://<landing>` en el `.env` del gateway.

---

## 6. Verificación

- **Tests**: `uv run pytest -q tests/test_gateway_demo.py tests/test_gateway_run_flow.py`
  y, al final y una sola vez, `make check`. En el dashboard, `pnpm typecheck`.
- **Local sin Docker**: `make dev-voice`, y
  `curl -X POST localhost:8000/api/run -H 'content-type: application/json' -d '{"scenario_id":"wildfire_ridge","minecraft":false,"mock_calls":true,"phones":{…}}'`;
  comprobar `GET /api/demo/status` antes y después.
- **Local con Docker**: `docker compose -f infra/deploy/docker-compose.yml up --build`,
  abrir `http://localhost/#/minecraft`, ver el valle, el fuego a los ~60 s y los camiones
  moverse.
- **VPS**: un run real con los cinco móviles del equipo. Comprobar las cuatro salientes en
  orden (retén y ambulancia primero, nadie se mueve hasta que cuelguen, luego Pueblo A),
  que la entrante desde el móvil «vecino» sale con la insignia «tu llamada», y que a los
  7 minutos el run se para solo.
- **Red restringida** (hotspot con solo 80/443): el vídeo tiene que cargar igual por HLS.

---

## 7. Riesgos a decir en voz alta

- **fps de llvmpipe: sin medir.** Es lo primero que hay que comprobar en el VPS.
- **Sin puerta de acceso** (decisión explícita del usuario): cualquiera con la URL marca a
  números de España a cargo de la cuenta de HappyRobot. El único freno es un run cada 7
  minutos. Si aparece abuso, lo más barato de añadir es un campo «código» en `RunBody`
  contra una variable de entorno.
- **La sesión de Minecraft** caduca si pasan ~90 días sin usarse: `make deploy-login` otra vez.
- **Un solo Paper, un solo gateway**: nadie del equipo debe apuntar su `.env` al RCON del VPS
  mientras haya una demo viva.
- **Datos personales**: los teléfonos del visitante viven solo en memoria del proceso
  mientras dura el run. No se escriben en el journal ni salen por `/api/health` ni por
  `/api/demo/status`. Mantenerlo así.
