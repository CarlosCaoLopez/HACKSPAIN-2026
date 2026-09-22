#!/usr/bin/env bash
# Lanza la demo entera con pantalla partida: server + world + demo + dashboard +
# director, cada uno en su ventana, y deja el dashboard (Chrome) en la mitad
# izquierda y la GUI de Minecraft en la derecha.
#
#   scripts/demo_pantalla.sh <PLAYER>          # tu usuario de Minecraft
#   scripts/demo_pantalla.sh <PLAYER> --real   # llamadas de verdad (default: --mock-calls)
#
# Env: JAVA_BIN (java 21 para Paper), SCENARIO (default wildfire_ridge).
set -euo pipefail

# --- raíz del repo ---
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# --- argumentos ---
PLAYER="${1:-}"
[ -n "$PLAYER" ] || { echo "uso: $0 <PLAYER> [--real]   (PLAYER = tu usuario de Minecraft)"; exit 1; }
FLAGS="--mock-calls"
[ "${2:-}" = "--real" ] && FLAGS=""
SCENARIO="${SCENARIO:-wildfire_ridge}"
JAVA_BIN="${JAVA_BIN:-/usr/lib/jvm/java-21-openjdk-amd64/bin/java}"

# --- preflight ---
[ -x "$JAVA_BIN" ] || { echo "no existe JAVA_BIN=$JAVA_BIN (java 21). Instala openjdk-21 o exporta JAVA_BIN."; exit 1; }
"$JAVA_BIN" -version 2>&1 | grep -qE 'version "(2[1-9]|[3-9][0-9])' \
  || { echo "JAVA_BIN=$JAVA_BIN no es java 21+"; exit 1; }
[ -f "$ROOT/.env" ] || { echo "falta .env en la raíz: cp .env.example .env"; exit 1; }
[ -f "$ROOT/infra/server/paper-1.21.jar" ] || { echo "falta infra/server/paper-1.21.jar"; exit 1; }
command -v alacritty >/dev/null || { echo "falta alacritty"; exit 1; }

if ! command -v wmctrl >/dev/null; then
  echo "wmctrl no está: se instala una vez (sudo)…"
  sudo apt install -y wmctrl
fi

# --- geometría de la pantalla (mitades) ---
GEO="$(xrandr --current | grep -oP '(?<= connected primary )\d+x\d+' | head -1)"
[ -n "$GEO" ] || GEO="$(xrandr --current | grep -oP '(?<= connected )\d+x\d+' | head -1)"
SW="${GEO%x*}"; SH="${GEO#*x}"
SW="${SW:-1920}"; SH="${SH:-1080}"
HALF=$((SW / 2))
echo "pantalla ${SW}x${SH} · mitad ${HALF}px"

# alacritty / Chrome heredan variables envenenadas por el snap de VSCode
# (GTK_PATH, LOCPATH, GTK_EXE_PREFIX… apuntando a /snap/code) y petan con
# «symbol lookup error … GLIBC_PRIVATE». Se lanzan con ese entorno limpio.
CLEAN=(env -u LD_LIBRARY_PATH -u LD_PRELOAD -u GTK_PATH -u GTK_EXE_PREFIX
  -u GTK_IM_MODULE_FILE -u GDK_PIXBUF_MODULEDIR -u GDK_PIXBUF_MODULE_FILE
  -u GIO_MODULE_DIR -u GSETTINGS_SCHEMA_DIR -u LOCPATH)

# --- helpers ---
wait_port() { echo "  esperando puerto $1…"; until nc -z localhost "$1" 2>/dev/null; do sleep 0.5; done; }
wait_http() { echo "  esperando $1…"; until curl -sf "$1" >/dev/null 2>&1; do sleep 0.5; done; }
term() { "${CLEAN[@]}" alacritty --title "$1" -e bash -lc "cd '$ROOT'; $2; echo; echo '[$1 terminó — Enter para cerrar]'; read" & }

# --- 1. Paper server ---
echo "→ server (java 21)…"
term "vela·server" "JAVA_BIN='$JAVA_BIN' make server"
wait_port 25575

# --- 2. jugador conectado (la GUI ya está abierta) ---
echo
read -r -p "Conéctate a localhost:25565 como '$PLAYER' (online-mode=false) y pulsa Enter… "

# --- 3. mundo por RCON ---
echo "→ world…"
term "vela·world" "make world SCENARIO='$SCENARIO'"
sleep 4

# --- 4. demo (levanta gateway :8000 + POST /api/run + journal en vivo) ---
echo "→ demo (FLAGS='$FLAGS')…"
term "vela·demo" "make demo SCENARIO='$SCENARIO' FLAGS='$FLAGS'"
wait_http "http://localhost:8000/api/health"

# --- 5. dashboard (Vite :5173, proxy a :8000) ---
echo "→ dashboard…"
term "vela·dash" "cd apps/dashboard && { [ -d node_modules ] || pnpm install; } && pnpm dev"
wait_port 5173

# --- 6. director (cámara auto; el journal en vivo ya existe) ---
echo "→ director…"
term "vela·director" "make director PLAYER='$PLAYER' SCENARIO='$SCENARIO'"

# --- 7. tiling: dashboard izquierda, Minecraft derecha ---
echo "→ colocando ventanas…"
"${CLEAN[@]}" google-chrome --new-window --window-position=0,0 --window-size="$HALF,$SH" \
  "http://localhost:5173" >/dev/null 2>&1 &

# dar tiempo a que Chrome cree la ventana
for _ in $(seq 1 20); do wmctrl -lx 2>/dev/null | grep -qi chrome && break; sleep 0.5; done

place() {  # $1 = id de ventana, $2 = X
  wmctrl -i -r "$1" -b remove,maximized_vert,maximized_horz 2>/dev/null || true
  wmctrl -i -r "$1" -e "0,$2,0,$HALF,$SH"
}

CHROME_ID="$(wmctrl -lx 2>/dev/null | grep -i chrome | tail -1 | awk '{print $1}')"
[ -n "$CHROME_ID" ] && place "$CHROME_ID" 0 || echo "  ! no encontré la ventana de Chrome (colócala con Super+←)"

MC_ID="$(wmctrl -l 2>/dev/null | grep -i minecraft | tail -1 | awk '{print $1}')"
[ -n "$MC_ID" ] && place "$MC_ID" "$HALF" || echo "  ! no encontré la ventana de Minecraft (colócala con Super+→)"

echo
echo "listo. Ventanas: vela·server vela·world vela·demo vela·dash vela·director."
echo "Para parar la demo: cierra 'vela·demo' y 'vela·server' (Ctrl-C en cada una)."
