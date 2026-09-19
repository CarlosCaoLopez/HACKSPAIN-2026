#!/usr/bin/env bash
# Levanta Paper 1.21 en local. Ver D9: jar pelado, sin Docker.
# El servidor es un dispositivo de salida; `make world` construye lo que se ve.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"

[ -f paper-1.21.jar ] || { echo "falta infra/server/paper-1.21.jar"; exit 1; }
[ -f "$ROOT/.env" ]   || { echo "falta .env en la raíz: cp .env.example .env"; exit 1; }

set -a; source "$ROOT/.env"; set +a
: "${RCON_PASSWORD:?RCON_PASSWORD vacío en .env}"

# La contraseña vive solo en el .env; aquí se inyecta en el fichero de ejecución.
# (`sed -i` con copia vacía: el de macOS exige el sufijo; el de GNU lo acepta así.)
if [ -f server.properties ]; then
  sed -i.bak "s|^rcon.password=.*|rcon.password=${RCON_PASSWORD}|" server.properties && rm -f server.properties.bak
else
  sed "s|__RCON_PASSWORD__|${RCON_PASSWORD}|" server.properties.template > server.properties
fi

echo "eula=true" > eula.txt

# Paper 1.21 exige Java 21+. En el Mac de Hugo el `java` del PATH es 19: se busca el de brew.
JAVA_BIN="${JAVA_BIN:-java}"
if ! "$JAVA_BIN" -version 2>&1 | grep -qE 'version "(2[1-9]|[3-9][0-9])'; then
  for cand in /opt/homebrew/opt/openjdk@21/bin/java /opt/homebrew/opt/openjdk/bin/java /usr/local/opt/openjdk@21/bin/java; do
    [ -x "$cand" ] && JAVA_BIN="$cand" && break
  done
fi
echo "java: $JAVA_BIN"
exec "$JAVA_BIN" -Xms2G -Xmx4G -jar paper-1.21.jar --nogui
