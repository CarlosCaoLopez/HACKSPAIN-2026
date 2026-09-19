#!/usr/bin/env bash
# Levanta Paper 1.21 en local. Ver D9: jar pelado, sin Docker.
# El servidor es un dispositivo de salida; `make world` construye lo que se ve.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"

# El jar tiene que ser de la MISMA versión que el cliente (1.21 y 1.21.11 hablan
# protocolos distintos y el cliente dice «outdated server»). Se coge el más nuevo que
# haya; para cambiar de versión, bajar el jar de fill.papermc.io y borrar el viejo.
JAR="$(ls -t paper-*.jar 2>/dev/null | head -1)"
[ -n "$JAR" ] || { echo "falta un infra/server/paper-<version>.jar"; exit 1; }
[ -f "$ROOT/.env" ]   || { echo "falta .env en la raíz: cp .env.example .env"; exit 1; }

# Solo las tres variables de RCON: el .env lleva claves largas de otros servicios y
# no hay motivo para que vivan en el entorno del servidor de Minecraft. Se leen
# línea a línea y sin `eval`: el bash 3.2 de macOS no lee bien `. <(grep ...)`, y
# `source` del .env entero expandiría cualquier `$` que hubiera en una clave.
while IFS= read -r linea; do export "$linea"; done \
  < <(grep -E '^(RCON_HOST|RCON_PORT|RCON_PASSWORD)=' "$ROOT/.env")
: "${RCON_PASSWORD:?RCON_PASSWORD vacío en .env}"

# La contraseña vive solo en el .env; aquí se inyecta en el fichero de ejecución.
# (`sed -i` con copia vacía: el de macOS exige el sufijo; el de GNU lo acepta así.)
if [ -f server.properties ]; then
  sed -i.bak "s|^rcon.password=.*|rcon.password=${RCON_PASSWORD}|" server.properties && rm -f server.properties.bak
else
  sed "s|__RCON_PASSWORD__|${RCON_PASSWORD}|" server.properties.template > server.properties
fi

echo "eula=true" > eula.txt

# spark (el profiler que Paper trae de serie) carga un async-profiler nativo que
# hace SIGSEGV en macOS/arm64 con Java 25 nada más arrancar: el servidor decía
# "Done" y moría un segundo después con RCON a medio abrir. No lo necesitamos
# para nada, así que se apaga siempre, antes de que Paper lo cargue. Paper crea
# config/ en el primer arranque, y ese primer arranque también tiene que salir:
# si no existe, se siembra un fichero mínimo y Paper lo completa con sus defaults.
mkdir -p config
if [ -f config/paper-global.yml ]; then
  # `enabled:` justo debajo de la cabecera `spark:` (dos espacios de sangría).
  sed -i.bak '/^spark:/,/^[a-z]/ s|^  enabled: true|  enabled: false|' config/paper-global.yml && rm -f config/paper-global.yml.bak
else
  printf '_version: 29\nspark:\n  enable-immediately: false\n  enabled: false\n' > config/paper-global.yml
fi

# Paper 1.21 exige Java 21+. El `java` del PATH en este Mac es 19, así que se busca
# otro: primero un 21 de verdad (`java_home`, que lee /Library y ~/Library), luego
# los de brew — ojo, brew enlaza TODOS los `openjdk@NN` al último que instaló, así
# que `openjdk@21` puede ser un 25; vale igual, con spark apagado corre bien.
JAVA_BIN="${JAVA_BIN:-java}"
es_java_21_o_mas() { "$1" -version 2>&1 | grep -qE 'version "(2[1-9]|[3-9][0-9])'; }
if ! es_java_21_o_mas "$JAVA_BIN"; then
  candidatos=()
  if [ -x /usr/libexec/java_home ]; then
    jh="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
    [ -n "$jh" ] && candidatos+=("$jh/bin/java")
  fi
  candidatos+=(
    /opt/homebrew/opt/openjdk@21/bin/java /opt/homebrew/opt/openjdk/bin/java
    /usr/local/opt/openjdk@21/bin/java /usr/local/opt/openjdk/bin/java
  )
  for cand in "${candidatos[@]}"; do
    [ -x "$cand" ] && es_java_21_o_mas "$cand" && JAVA_BIN="$cand" && break
  done
fi
es_java_21_o_mas "$JAVA_BIN" || { echo "no hay Java 21+: brew install openjdk@21"; exit 1; }
echo "java: $JAVA_BIN ($("$JAVA_BIN" -version 2>&1 | head -1))"
echo "jar: $JAR"
exec "$JAVA_BIN" -Xms2G -Xmx4G -jar "$JAR" --nogui
