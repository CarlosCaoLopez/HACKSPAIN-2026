# La /demo autoservicio en un VPS · infra/deploy

Lo que hay aquí levanta la demo entera en un servidor, sin portátil ni persona delante:
la landing (otro repo) manda cinco teléfonos a `POST /api/run`, el visitante ve el
Minecraft en directo en el dashboard y le suenan los móviles. Cinco contenedores:

```
Internet ──443──▶ caddy ─┬─ /cam/*              → mediamtx:8888 (HLS del Minecraft)
                         └─ todo lo demás       → gateway:8000  (API, WS, webhooks y el dashboard construido)
gateway ── RCON ──▶ paper (itzg/minecraft-server, Paper 1.21.11, mundo plano que el sim construye en cada run)
cam ── entra como jugador en paper ──▶ Xvfb + llvmpipe + cliente Minecraft real ──▶ ffmpeg ──▶ mediamtx
```

Solo `caddy` publica puertos. Paper **no** se expone a internet: nadie externo entra al
servidor y hay un único gateway mandando sobre él (la regla de «un solo mundo a la vez»
de `infra/README.md` se cumple por construcción).

## Por qué así (y no de otra forma)

- **Cliente real y no un visor web.** El sim pinta los camiones como `block_display` y los
  carteles como `armor_stand`, que `prismarine-viewer` no dibuja (y su último soporte es
  1.21.4; Paper aquí es 1.21.11). El cliente oficial lo pinta todo exactamente igual que
  en el pitch. Sin GPU: Mesa/llvmpipe rasteriza por software; a 1280x720 y 16 chunks de
  distancia va a 10-20 fps en 4 vCPU x86, que para una cenital fija sobra.
- **HLS y no WebRTC.** La wifi del evento solo deja salir 80/443 y bloquea UDP (medido,
  ver `scripts/retunnel.py`). HLS va por HTTPS como cualquier página; cuesta unos
  segundos de latencia, que en una demo de llamadas de un minuto no se notan. MediaMTX
  también sirve WebRTC (`8889`) si algún día hace falta, sin tocar nada más.
- **URL estable.** Se acabó `retunnel.py` cada vez que ngrok cambia: HappyRobot y el
  webhook de Telegram apuntan a `https://<DOMAIN>` una vez.
- **VPS x86.** Los nativos LWJGL que trae el manifest de Mojang son linux-x64: nada de
  ARM (Hetzner CAX) por barato que salga. Empezar en un **Hetzner CPX42** (8 vCPU AMD
  compartidas, 16 GB, Falkenstein/Núremberg, cerca de `platform.eu.happyrobot.ai`) y
  subir a CCX (vCPU dedicada) solo si la cámara no pasa de ~10 fps. Se factura por horas.

## Una vez

1. **VPS** con Docker y el plugin de compose (`apt install docker.io docker-compose-v2`
   o el script de get.docker.com). Abrir 80 y 443. Un registro DNS `A` de `<DOMAIN>`
   al VPS.
2. **Clonar el repo** en el VPS y copiar el `.env` (el de la raíz, el mismo de siempre)
   con el bloque «Despliegue» de `.env.example` relleno: `DOMAIN`, `MC_VERSION` (la de
   `infra/server/paper-*.jar`), `RCON_PASSWORD`, y los `PHONE_*` por defecto, que son
   los que mandan si alguien arranca sin `phones`.
3. `make deploy-up`. La primera vez tarda: construye dos imágenes, itzg baja Paper y el
   cliente baja assets de Mojang (~500 MB al volumen `mc-home`).
4. **Cuenta de Minecraft** de la cámara: `make deploy-login EMAIL=<cuenta>` enseña un
   código de dispositivo para meter en microsoft.com/link; luego `MC_LOGIN=<ese email>`
   en el `.env` y otro `make deploy-up`. Sin login el cliente entra offline con
   `MC_PLAYER` y Paper lo acepta (`online-mode=false`), pero es zona gris del EULA: usar
   la cuenta de quien graba hoy la demo.
5. **HappyRobot y Telegram** a la URL nueva, una sola vez:
   `uv run python scripts/retunnel.py --apply https://<DOMAIN>` (desde cualquier
   portátil con el `.env`; comprueba `/api/health` antes de tocar nada).
6. **CORS**: `VELA_CORS_ORIGINS=https://<landing>` en el `.env` para que la landing pueda
   llamar a la API desde el navegador. Y `VELA_LANDING_URL=https://<landing>/demo` para
   el enlace de la pantalla de espera.

## Cada vez que se despliega código

```
git pull && make deploy-up        # reconstruye lo que cambió; los volúmenes se quedan
make deploy-ps                    # los cinco `healthy`/`running`
make deploy-logs S=cam            # «Paper contesta», «lanzando Minecraft», y sin bucles de salida
```

Ensayo sin marcar a nadie: `curl -X POST https://<DOMAIN>/api/run -H 'content-type: application/json'
-d '{"scenario_id":"wildfire_ridge","mock_calls":true}'` y abrir `https://<DOMAIN>/#/minecraft`.
El run se para solo a los `VELA_RUN_MAX_S` segundos (420); antes, `POST /api/run/stop`.

## Lo que la landing tiene que hacer

Está en `docs/interfaces.md` («La /demo autoservicio»): `GET /api/demo/status` para saber si
está libre y pintar los cinco papeles, `POST /api/run` con `phones`, y llevar al visitante a
`watch_url`. Los cinco teléfonos son obligatorios; el mismo número puede repetirse.

## Si algo falla

- `cam` en bucle «el cliente ha salido»: casi siempre versión distinta de Paper
  (`MC_VERSION`) o la sesión de Microsoft caducada (`make deploy-login` otra vez).
  `make deploy-logs S=cam` lo dice.
- El vídeo dice «Conectando…» y no arranca: `curl https://<DOMAIN>/cam/vela/index.m3u8`
  tiene que devolver una playlist. Si 404, ffmpeg no está publicando (mira `cam`); si
  Caddy da 502, `mediamtx` está caído.
- El plano no es el de águila: `VELA_CAM_PLAYER` y `MC_PLAYER` no coinciden, o el cliente
  entró después de los 120 s que el gateway espera (`/api/health` → `notes.camera`).
  Arrancar otro run lo vuelve a colocar.
- Paper `unhealthy` al arrancar: la primera vez tarda hasta un minuto (baja el jar y
  genera el mundo plano); `start_period` ya lo contempla.
- Llamadas que no vuelven (unidades retenidas hasta agotar plazo): el `VELA_URL` de
  HappyRobot no es este dominio. `scripts/retunnel.py` sin argumentos lo enseña.
