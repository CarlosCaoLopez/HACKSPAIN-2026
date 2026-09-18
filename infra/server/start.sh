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
if [ -f server.properties ]; then
  sed -i "s|^rcon.password=.*|rcon.password=${RCON_PASSWORD}|" server.properties
else
  sed "s|__RCON_PASSWORD__|${RCON_PASSWORD}|" server.properties.template > server.properties
fi

echo "eula=true" > eula.txt

exec java -Xms2G -Xmx4G -jar paper-1.21.jar --nogui
