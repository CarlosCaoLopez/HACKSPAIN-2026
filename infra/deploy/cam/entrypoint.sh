#!/usr/bin/env bash
# Arranca el monitor virtual, espera a Paper, graba la pantalla hacia MediaMTX y lanza el
# cliente Minecraft contra el servidor, en bucle: si el cliente se cae (o el run lo
# desconecta), vuelve a entrar. El gateway lo pone en espectador y en el plano `aguila`
# al arrancar cada run (`VELA_CAM_PLAYER`).
set -euo pipefail

: "${MC_VERSION:=1.21.11}"        # la MISMA que Paper; si no, «outdated server»
: "${MC_PLAYER:=vela_cam}"        # = VELA_CAM_PLAYER del gateway
: "${MC_LOGIN:=}"                 # email de la cuenta ya logueada con `portablemc login`; vacío = offline
: "${MC_SERVER:=paper}"
: "${MC_PORT:=25565}"
: "${MC_XMX:=3G}"
: "${CAM_RES:=1280x720}"
: "${CAM_FPS:=15}"
: "${CAM_BITRATE:=2500k}"
: "${MEDIAMTX_URL:=rtsp://mediamtx:8554/vela}"

log() { printf 'cam · %s\n' "$*"; }

# 1. El monitor.
Xvfb "$DISPLAY" -screen 0 "${CAM_RES}x24" -nolisten tcp -ac +extension GLX +render -noreset &
sleep 1
log "Xvfb en $DISPLAY a $CAM_RES · $(glxinfo -B 2>/dev/null | grep -E 'OpenGL (renderer|version)' | tr '\n' ' ' || echo 'sin glxinfo')"

# 2. Las opciones del cliente, solo la primera vez (luego manda lo que haya en el volumen).
mkdir -p "$HOME/.minecraft"
[ -f "$HOME/.minecraft/options.txt" ] || cp /opt/vela/options.txt "$HOME/.minecraft/options.txt"

# 3. Paper tiene que estar escuchando: itzg tarda ~30 s la primera vez.
until (exec 3<>"/dev/tcp/$MC_SERVER/$MC_PORT") 2>/dev/null; do
  log "esperando a $MC_SERVER:$MC_PORT"
  sleep 3
done
log "Paper contesta en $MC_SERVER:$MC_PORT"

# 4. La grabación, en bucle: si MediaMTX aún no está (o se reinicia), ffmpeg sale y se relanza.
(
  while true; do
    ffmpeg -hide_banner -loglevel warning -nostdin \
      -f x11grab -framerate "$CAM_FPS" -video_size "$CAM_RES" -draw_mouse 0 -i "$DISPLAY" \
      -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
      -g $((CAM_FPS * 2)) -b:v "$CAM_BITRATE" -maxrate "$CAM_BITRATE" -bufsize "$CAM_BITRATE" \
      -f rtsp -rtsp_transport tcp "$MEDIAMTX_URL" || true
    log "ffmpeg ha salido, se relanza en 3 s"
    sleep 3
  done
) &

# 5. El cliente, en bucle. `-s/-p` es «entra directamente en este servidor» (quick play
# en 1.20+). Con `MC_LOGIN` usa la sesión guardada por `portablemc login`; sin él entra
# offline con el nombre (Paper tiene `online-mode=false`).
while true; do
  if [ -n "$MC_LOGIN" ]; then auth=(-l "$MC_LOGIN"); else auth=(-u "$MC_PLAYER"); fi
  log "lanzando Minecraft $MC_VERSION como ${MC_LOGIN:-$MC_PLAYER} → $MC_SERVER:$MC_PORT"
  portablemc start "$MC_VERSION" "${auth[@]}" -s "$MC_SERVER" -p "$MC_PORT" \
    --resolution "$CAM_RES" --disable-chat \
    --jvm-args "-Xmx${MC_XMX} -Xms1G -XX:+UseG1GC -Djava.awt.headless=false" || true
  log "el cliente ha salido, vuelve a entrar en 5 s"
  sleep 5
done
