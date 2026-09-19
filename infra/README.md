# Minecraft en la demo · procedimiento en este Mac

Paper 1.21 en local, sin Docker. El servidor es un dispositivo de salida: `make world`
construye el valle desde `scenarios/wildfire_ridge.yaml` y el sim lo pinta por RCON.
Verificado el 19 de septiembre contra un Paper real (build 130): `make world` tarda
2–5 s (3580 comandos), y un run de 6 minutos a 4× no genera ningún error de RCON.

## Antes de empezar (una vez)

- `.env` en la raíz con `RCON_HOST=localhost`, `RCON_PORT=25575` y `RCON_PASSWORD=<la vuestra>`.
  `start.sh` la inyecta en `server.properties` y el sim la lee de `contracts.settings`.
- Java 21+. `start.sh` lo busca solo: primero un JDK 21 de verdad (`/usr/libexec/java_home -v 21`,
  que en este Mac encuentra `~/Library/Java/JavaVirtualMachines/ms-21.0.10`), luego los de brew.
  Ojo: brew enlaza todos los `openjdk@NN` al último que instaló (aquí, el 25); vale igual.
- El profiler `spark` que Paper trae de serie hace `SIGSEGV` en macOS/arm64 con Java 25.
  `start.sh` lo deja apagado en `config/paper-global.yml` en cada arranque. No lo reactivéis.
- Cliente de Minecraft **Java Edition 1.21** (la misma versión que el servidor: Paper 1.21
  no acepta 1.21.1 ni 1.20.x sin ViaVersion). `online-mode=false`: vale cualquier usuario,
  sin cuenta.

## El día de la demo, en orden

1. **Servidor** (terminal 1, se queda abierta): `make server`. Listo cuando dice
   `Done (…s)! For help, type "help"` y `RCON running on 0.0.0.0:25575`. Tarda ~12 s.
2. **Mundo**: `make world`. Idempotente: mata todo lo que lleva `tag=vela`, borra la
   cicatriz del fuego anterior y lo levanta de nuevo. Se puede lanzar las veces que haga falta.
   El sim vuelve a hacerlo solo al arrancar cada run (`POST /api/run`), así que no hace falta
   entre runs; sí conviene antes de entrar con el cliente para verlo entero.
3. **Cliente**: Multijugador → Conexión directa → `localhost:25565`. Al entrar se aparece en
   el spawn del mundo plano, no en el valle: la cámara lo lleva.
4. **Cámara** (terminal 2, **con el foco** durante el pitch):
   `make cam PLAYER=<tu usuario de Minecraft>`. Espera a que el jugador esté dentro, lo pone
   en espectador y desde entonces las teclas de esa terminal mueven la cámara:
   - `1` valle · cenital del valle, explica el mecanismo y la Y
   - `2` escorzo · el valle a 45° desde el oeste, se lee mejor que el cenital
   - `3` pueblo · plano de Pueblo A, la escala y los civiles
   - `4` frente · el frente de fuego a ras, las llamas
   - `q` sale. Ctrl-C también.
   Minecraft en la segunda pantalla sigue renderizando sin foco; OBS captura esa ventana.
   Alternativa sin terminal enfocada: `uv run python -m sim.camera --follow <usuario>`
   (1-4 del inventario dentro del juego, en creativo con F1 para ocultar el HUD).
   Un encuadre suelto: `uv run python -m sim.camera valle --who <usuario>`.
5. **Run**: `make demo` (o `POST /api/run` con `"minecraft": true`). El sim reconstruye el mundo
   al arrancar (2 s) y al parar el run (`/api/run/stop`) borra fuego y entidades: no paréis el
   run hasta que la cámara haya dejado de grabar.

## Qué comprobar en el ensayo

- En el juego: seis carteles flotantes con los nombres de los POIs, dos camiones rojos y el
  dron en la base, la ambulancia en el hospital, aldeanos delante de los dos pueblos.
- El fuego nace en `cell_18_7` (bloques x 72..75, z 28..31, junto a `wp_sur_01`): netherrack
  con fuego encima; lo quemado queda en `coal_block`. Con la vista `frente` se ve nacer.
- A los 150 s el viento vira y el frente tira al sureste, hacia Pueblo B; a los 210 s la pista
  sur se pinta a franjas negras y amarillas con dos troncos cruzados; a los 240 s truck2 se para.
- Los marcadores de los pueblos (la plataforma de color bajo cada edificio) pasan a rojo cuando
  arde algo a menos de 45 m y a naranja cuando el frente ya ha pasado.

## Si algo falla

- `make world` dice `no hay RCON en localhost:25575`: el servidor no está o aún arranca.
- `RCON rechaza la contraseña`: `server.properties` no coincide con el `.env`; `make server`
  la vuelve a inyectar en cada arranque.
- El cliente no conecta: versión distinta de 1.21, o el servidor todavía no ha dicho `Done`.
- La cámara dice `esperando a que <usuario> entre`: el nombre no coincide con el del cliente
  (distingue mayúsculas) o el jugador aún no ha entrado.
- `make cam` sin terminal (nohup, script): aborta con un aviso; necesita stdin de verdad.
- **Un solo mundo a la vez contra este servidor.** `make world`, un run (`POST /api/run`)
  y `stop` hacen todos `kill @e[tag=vela]` + `forceload`: si dos personas o dos procesos
  atacan el mismo Paper (dos gateways, un `make world` en mitad de un run, un run de otro
  portátil apuntando aquí), uno le borra el mundo al otro en pleno pitch. Comprobado el
  19 de septiembre: `pytest` **no** toca el servidor (usa dobles), así que la suite es
  segura; el peligro es otro proceso de verdad. En la demo, un único gateway manda.
