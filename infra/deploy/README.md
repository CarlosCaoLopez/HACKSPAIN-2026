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
  en el pitch. Sin GPU: Mesa/llvmpipe rasteriza por software, y para una cenital fija —sin
  cámara que se mueva— con 10 fps basta. **Cuántos da de verdad a 1280x720 y 16 chunks en
  4 vCPU está sin medir**: es el paso 8, y el único riesgo abierto del despliegue.
- **HLS y no WebRTC.** La wifi del evento solo deja salir 80/443 y bloquea UDP (medido,
  ver `scripts/retunnel.py`). HLS va por HTTPS como cualquier página; cuesta unos
  segundos de latencia, que en una demo de llamadas de un minuto no se notan. MediaMTX
  también sirve WebRTC (`8889`) si algún día hace falta, sin tocar nada más.
- **URL estable.** Se acabó `retunnel.py` cada vez que ngrok cambia: HappyRobot y el
  webhook de Telegram apuntan a `https://taiafox.ignaciogarbayo.com` una vez.
- **VPS x86 y con vCPU dedicada.** Los nativos LWJGL que trae el manifest de Mojang son
  linux-x64: nada de ARM (Hetzner CAX) por barato que salga. **Hetzner CCX33** (8 vCPU AMD
  dedicadas, 32 GB, Falkenstein o Núremberg, cerca de `platform.eu.happyrobot.ai`).
  Se factura por horas: para la semana del hackathon son unos pocos euros.

  El motivo de ir a la gama dedicada y no a una CPX compartida, que sale más barata: la
  cámara renderiza **por software** (llvmpipe, sin GPU) a `1280x720` con `renderDistance:16`
  y encima le pasa un `x264` por delante, así que tiene sus `CAM_CPUS` (4 por defecto)
  clavados al 100 % durante los siete minutos enteros de cada run. Carga máxima sostenida es
  justo el patrón que cubren las políticas de uso justo de las vCPU compartidas, y un
  throttling en mitad de la demo no se puede depurar en directo. La CPX42 funciona; lo que
  no hace es garantizarte el minuto en el que hay gente mirando.

  Presupuesto aproximado de lo que pide el compose: `cam` 4 vCPU, `paper` 4 GB (`MEMORY`),
  `gateway` un solo proceso con core + sim + voice dentro, y `mediamtx` + `caddy` casi nada.

  Si aun con vCPU dedicada no se llega a ~10 fps, el salto bueno **no** es más CPU: es una
  instancia con GPU real (Scaleway RENDER-S y equivalentes), que cambia llvmpipe por
  rasterizado de verdad. Cuesta más montaje (pasar la GPU al contenedor de `cam`), así que
  es plan B y solo si la medición lo pide. Antes, lo barato: bajar a `CAM_RES=960x540` y
  `renderDistance:10`.

  Scaleway u OVH valen igual: el compose no depende del proveedor. Lo que **no** vale es un
  PaaS tipo Fly/Railway/Render, que sirve para el gateway solo pero no para un cliente
  Minecraft con Xvfb.

## Despliegue paso a paso

Tiempo total la primera vez: **40-60 min**, y casi todo es esperar descargas. Los pasos
0 a 3 se pueden hacer desde casa antes de tener el VPS.

### 0 · Antes de empezar, ten a mano

- Una **cuenta de Hetzner** (o del proveedor que sea) con método de pago.
- Un **dominio** y acceso a su DNS. Aquí se llama `taiafox.ignaciogarbayo.com`, p. ej. `taiafox.ignaciogarbayo.com`.
- Una **cuenta de Microsoft con Minecraft Java** comprado. Es la que entrará como cámara.
  No vale Bedrock, y no vale una cuenta sin el juego.
- Las claves del `.env` que ya usáis: `HAPPYROBOT_API_KEY`, `TYPESAFE_API_KEY`,
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HUMALIKE_API_KEY`, `TELEGRAM_BOT_TOKEN`.

### 1 · Crear el VPS en Hetzner

#### 1.1 · La cuenta (hazlo con antelación)

Regístrate en **<https://accounts.hetzner.com>** y entra luego a la consola de nube:
**<https://console.hetzner.cloud>**.

> **Esto es lo que puede arruinarte el día.** Hetzner **verifica manualmente** las cuentas
> nuevas antes de dejarte crear servidores, y puede pedirte un documento de identidad o un
> primer pago por adelantado. Suele tardar de minutos a unas horas, pero no es instantáneo
> y no hay forma de acelerarlo. **Crea la cuenta el día antes**, no la mañana de la demo.

Ojo con el producto: lo que quieres es **Hetzner Cloud** (`console.hetzner.cloud`), no
*Hetzner Robot* (servidores dedicados físicos, con cuota de instalación y facturación
mensual). Son dos paneles distintos.

#### 1.2 · Proyecto y clave SSH

En la consola, crea un proyecto (por ejemplo `vela`). Todo cuelga de un proyecto.

Antes de crear el servidor, ten tu clave SSH. En **Windows**, desde PowerShell:

```powershell
# ¿Ya tienes una?
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub

# Si no, créala (Enter a todo; la passphrase es opcional pero recomendable)
ssh-keygen -t ed25519 -C "vela-hackspain"
```

Copia el contenido de `id_ed25519.pub` **entero**, empieza por `ssh-ed25519`. En la consola:
*Security* → *SSH Keys* → *Add SSH Key* → pégala y ponle nombre.

Usar clave y no contraseña no es ceremonia: un servidor con 22 abierto y contraseña recibe
intentos de acceso a los pocos minutos de existir.

#### 1.3 · Crear el servidor

*Servers* → **Add Server**:

| Campo | Valor | Por qué |
| --- | --- | --- |
| **Location** | **Falkenstein** o **Núremberg** | Cerca de `platform.eu.happyrobot.ai`: menos latencia en los webhooks de llamada |
| **Image** | **Ubuntu 24.04** | Trae Docker y compose v2 en repos |
| **Type** | pestaña **Dedicated vCPU** → **CCX33** | 8 vCPU AMD dedicadas, 32 GB. La cámara clava 4 vCPU al 100 % todo el run |
| **Networking** | **IPv4** e **IPv6** | La IPv4 es la del registro DNS. Sin IPv4 no hay forma cómoda de apuntar el dominio |
| **SSH Keys** | La que acabas de subir | |
| **Firewall** | Ninguno, o uno con **22, 80 y 443** de entrada | Si lo pones aquí, el `ufw` del paso 3 sobra |
| **Volumes / Backups / Placement** | Nada | Los volúmenes de Docker viven en el disco del servidor |
| **Name** | `vela-demo` | Sale en la consola y en el prompt |

Dale a **Create & Buy now**. En menos de un minuto tienes la IPv4 en la lista de servidores.

**Nada de CAX (Shared vCPU · Arm64)**, por barato que salga: los nativos LWJGL del manifest
de Mojang son linux-x64 y el cliente de la cámara no arranca. Y nada de CPX (Shared vCPU
x86) para esto, por lo del uso justo que se explica arriba.

#### 1.4 · Entrar

```bash
ssh root@<IPv4>
```

La primera vez te pregunta por la huella del host: `yes`. Si te da
`REMOTE HOST IDENTIFICATION HAS CHANGED`, es que reutilizaste una IP de otro servidor
anterior: `ssh-keygen -R <IPv4>` y vuelve a entrar.

#### 1.5 · Lo que cuesta y cómo dejar de pagarlo

Se factura **por horas**, con un tope mensual. Para la semana del hackathon son unos pocos
euros. Mira el precio actual en la propia consola al elegir el tipo: cambia, y cualquier
cifra escrita aquí envejece mal.

**Apagar el servidor NO deja de facturar.** Hetzner cobra por el servidor existiendo, no
por estar encendido. Cuando la demo haya pasado:

- **Delete server** en la consola. Eso sí para el cobro, y se lleva por delante los
  volúmenes de Docker: el mundo de Paper, los runs y la sesión de Minecraft.
- Si quieres poder volver sin repetir todo el montaje, saca antes un **snapshot** (*Images*
  → *Snapshots*). Cuesta bastante menos que el servidor, pero **no es gratis**: bórralo
  también cuando ya no lo necesites.

### 1.6 · Alternativa: DigitalOcean

El único paso con un plazo que no controlas es la verificación de cuenta de Hetzner. Si te
bloquea, **el compose no depende del proveedor**: cambian los pasos 1.1-1.4 y nada más.

#### Lo que cuesta (consultado el 2026-09-20; verifícalo en el panel)

| | Hetzner CCX33 | DigitalOcean CPU-Optimized |
| --- | --- | --- |
| vCPU / RAM | 8 dedicadas / 32 GB | 8 dedicadas / 16 GB |
| Disco | 240 GB | 100 GB |
| Tráfico incluido | 30 TB | 6.000 GiB |
| **Por hora** | **~€0,072** | **$0,25** |
| **Por mes (tope)** | **€48,49** | **$168** |

DigitalOcean sale **unas 3,5 veces más caro**. Pero se factura por horas, y esto no se
tiene encendido un mes:

| Lo tienes | DigitalOcean | Hetzner |
| --- | --- | --- |
| Un día de pruebas | **$6** | ~€1,7 |
| Un fin de semana (3 días) | **$18** | ~€5 |
| Una semana entera | **$42** | ~€12 |

Los 16 GB de DO bastan: el compose pide unos 10 (Paper 4 GB, cliente 3 GB, gateway metro
y medio, resto migajas). Los 32 de Hetzner eran holgura.

El tráfico tampoco es problema: a `CAM_BITRATE=2500k`, un run de 7 minutos gasta 0,13 GB
**por espectador simultáneo**. Con 20 personas mirando a la vez y 50 runs seguidos, 131 GB
— de los 6.000 GiB incluidos. Solo importaría si esto se hiciera viral.

#### 1.6.1 · Cuenta

Regístrate en **<https://cloud.digitalocean.com>**. Tarjeta o PayPal; suele hacer una
retención de verificación de ~$1 que se devuelve. **Con tarjeta válida el alta suele ser
inmediata**, que es justo el motivo de estar aquí. Crea un proyecto (`vela`).

#### 1.6.2 · Clave SSH

La misma que en 1.2. Si aún no la tienes, desde PowerShell:

```powershell
ssh-keygen -t ed25519 -C "vela-hackspain"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

En el panel: *Settings* → *Security* → *SSH Keys* → **Add SSH Key**. También puedes
pegarla durante la creación del droplet.

#### 1.6.3 · Crear el droplet

**Create** → **Droplets**:

| Campo | Valor | Por qué |
| --- | --- | --- |
| **Region** | **Frankfurt** (`FRA1`) o **Amsterdam** (`AMS3`) | Latencia con `platform.eu.happyrobot.ai` |
| **Image** | **Ubuntu 24.04 (LTS) x64** | Docker y compose v2 en repos. Que ponga **x64**, no ARM |
| **Droplet Type** | **CPU-Optimized** (está bajo *Specialty Droplets*, no es la opción por defecto) | *Basic* es CPU compartida y la cámara la satura |
| **CPU options** | *Regular* o *Premium Intel* | Premium añade NVMe y más red; para esto da igual |
| **Plan** | **8 vCPU / 16 GB — $168/mo, $0.25/hr** | |
| **Authentication** | **SSH Key**, la tuya | Nunca contraseña con el 22 abierto |
| **Hostname** | `vela-demo` | |
| **Backups / Monitoring** | Apagados | Backups se facturan aparte |

**Create Droplet**. En un minuto tienes la IPv4 en la lista.

> **CPU-Optimized, no Basic.** Es el único campo que hay que acertar: los *Basic /
> Premium* son CPU compartida y la cámara mantiene 4 núcleos al 100 % los siete minutos
> del run. En compartida eso es throttling en directo.

#### 1.6.4 · IP reservada (recomendado)

*Networking* → *Reserved IPs* → asigna una al droplet. Es **gratis mientras esté asignada**
(solo se factura si la dejas suelta sin droplet).

Merece la pena porque si destruyes y recreas el droplet —para cambiar de tamaño, o para
dejar de pagar entre día y día— la IP cambiaría y tendrías que volver a tocar el DNS de
Hostinger y esperar propagación. Con IP reservada, el registro `A` de `taiafox` se escribe
una sola vez.

Si la usas, **el registro DNS del paso 2 apunta a la IP reservada**, no a la del droplet.

#### 1.6.5 · Cortafuegos

Dos opciones, elige una:

- **Cloud Firewall de DO** (gratis): *Networking* → *Firewalls* → *Create*. Entrada:
  SSH `22`, HTTP `80`, HTTPS `443` desde *All IPv4* y *All IPv6*. Salida: todo. Asígnalo
  al droplet. Si haces esto, **salta el `ufw` del paso 3**.
- **`ufw` en la máquina**, como en el paso 3. Más simple de ver desde dentro.

#### 1.6.6 · Entrar

```bash
ssh root@<IPv4-o-IP-reservada>
```

DigitalOcean entra como `root` directamente. A partir de aquí, **el paso 3 y siguientes
son idénticos**.

#### 1.6.7 · Dejar de pagar

Como en Hetzner: **apagar no basta**. Un droplet apagado se sigue facturando, porque
reserva los recursos.

- **Destroy** el droplet (*Destroy* → *Destroy this Droplet*). Se lleva los volúmenes de
  Docker: mundo de Paper, runs y sesión de Minecraft.
- Si quieres poder volver, saca un **snapshot** antes (~$0,06 por GiB y mes): más barato
  que el droplet, pero **no gratis**. Bórralo cuando ya no lo necesites.
- Si creaste una **IP reservada**, suéltala también: sin droplet asignado, se factura.

### 1.7 · Alternativa: Google Cloud con el crédito de $300

Para **un solo día** de demo, el crédito de bienvenida lo cubre de sobra. A cambio es el
montaje más laborioso de los tres: proyecto, API que habilitar, cuotas y reglas de
cortafuegos explícitas.

**Sin crédito, GCP es el más caro de los tres.** `c3-highcpu-8` en `europe-west3` cuesta
**$0,4014/hora** ($293/mes), consultado el 2026-09-20. Un día son ~$9,6 de máquina más
disco e IP: **unos $10**, frente a $6 en DigitalOcean y ~€1,7 en Hetzner. El crédito de
$300 es lo único que lo hace competitivo aquí, y solo mientras dure.

No cojas **Spot** ($0,1483/h) para la demo por barato que salga: una VM spot se puede
expropiar con 30 segundos de aviso, y eso en mitad del pitch es quedarse sin nada.

> **El obstáculo real no es el dinero: es la cuota.** Una cuenta en prueba gratuita trae
> un límite bajo de vCPU por región y **no puede pedir ampliaciones de cuota**. Si tu
> cuota de `CPUS` en la región es menor que 8, no podrás crear la máquina y no habrá
> botón para arreglarlo.
>
> La salida es **pasar la cuenta a facturación de pago** (*Billing* → *Upgrade*). Suena a
> que empiezas a pagar, pero **el crédito de $300 sigue aplicándose**: se gasta primero y
> solo pagarías si lo agotas o caduca a los 90 días. Al actualizar se levantan las
> restricciones de la prueba y ya puedes pedir cuota. **Compruébalo antes de nada** (1.7.2).

#### 1.7.1 · Cuenta y proyecto

1. **<https://console.cloud.google.com>**, entra con tu cuenta de Google.
2. Acepta la prueba gratuita: **$300, 90 días**. Pide tarjeta, con una retención de
   verificación que se devuelve.
3. Arriba a la izquierda, selector de proyecto → **Nuevo proyecto** → nombre `vela`.
4. Habilita la API: *APIs y servicios* → **Compute Engine API** → **Habilitar**. Tarda un
   par de minutos la primera vez y **sin esto no aparece el menú de VMs**.

#### 1.7.2 · Comprueba la cuota ANTES de montar nada

Desde el Cloud Shell del navegador (el icono `>_` arriba a la derecha, no necesita
instalar nada):

```bash
gcloud compute regions describe europe-west3   --format="table(quotas.metric,quotas.limit,quotas.usage)" | grep -i cpus
```

Si el límite de `CPUS` es **8 o más**, adelante. Si es menor, actualiza a facturación de
pago como dice el aviso de arriba y vuelve a mirar.

#### 1.7.3 · Crear la VM

*Compute Engine* → *Instancias de VM* → **Crear instancia**:

| Campo | Valor | Por qué |
| --- | --- | --- |
| **Nombre** | `vela-demo` | |
| **Región / Zona** | `europe-west3` (Fráncfort), cualquier zona | Latencia con `platform.eu.happyrobot.ai` |
| **Configuración** | pestaña **«Optimizado para procesamiento»** → serie **C3** → tipo **`c3-highcpu-8`** | 8 vCPU / 16 GB, justo el presupuesto. En la consola en inglés es *Compute-optimized* |
| **Disco de arranque** | *Cambiar* → **Ubuntu 24.04 LTS (x86/64)**, **60 GB**, balanceado | Imágenes + assets de Mojang + mundo. Con 10 GB no cabe |
| **Cortafuegos** | Marca **Permitir tráfico HTTP** y **Permitir tráfico HTTPS** | GCP no abre 80/443 por defecto; sin esto Caddy no saca certificado |

**Crear**. Si `c3-highcpu-8` no aparece en el desplegable, esa zona no lo tiene: cambia
de zona dentro de `europe-west3` (`-a`, `-b`, `-c`), o usa `c3-standard-8` (8 vCPU /
32 GB, algo más caro) o `n2-standard-8`. **Evita la familia `e2`** de «De uso general»:
su CPU es variable y la cámara necesita rendimiento sostenido.

> GCP cuenta **hilos, no núcleos**: 8 vCPU son 4 núcleos físicos con hyperthreading. Es
> lo que necesita `CAM_CPUS=4`, pero sin margen. Es normal que rinda algo peor que un
> Hetzner CCX del mismo número.

#### 1.7.4 · IP y DNS

La IP externa que te da es **efímera**: cambia si paras y arrancas la VM. Para un día
vale, pero **crea el registro `A` de `taiafox` después de crear la VM**, no antes.

Si vas a pararla y arrancarla, reserva una estática: *Red de VPC* → *Direcciones IP* →
*Reservar*. Ojo, una estática **sin VM asignada sí se factura**.

#### 1.7.5 · Entrar

Lo más fácil, sin configurar claves: botón **SSH** al lado de la instancia, abre una
terminal en el navegador. O desde tu máquina, con el SDK instalado:

```bash
gcloud compute ssh vela-demo --zone=europe-west3-c
```

GCP te mete como tu usuario, **no como root**: en el paso 3 antepón `sudo` a los
`apt`/`systemctl`/`ufw`, o haz `sudo -i` una vez y sigue igual.

#### 1.7.6 · Al acabar el día, BÓRRALA

Con el crédito es fácil olvidarse, pero el crédito se gasta igual y caduca a los 90 días.

- *Instancias de VM* → selecciona → **Borrar**. Parar no basta: el disco se sigue
  facturando.
- Si reservaste IP estática, libérala.
- Lo más seguro: **borra el proyecto entero** (*IAM y administración* → *Configuración* →
  *Apagar*). Se lleva todo lo que hayas creado sin dejar cabos.

#### 1.7.7 · Cuidado con la salida de red

A diferencia de Hetzner (30 TB) o DO (6.000 GiB), **GCP factura el tráfico de salida
aparte** y lo incluido es mucho menor. Con el vídeo a `CAM_BITRATE=2500k`, cada run de
7 minutos son 0,13 GB **por espectador simultáneo**: un día de demo con poca gente son
unos pocos GB y da igual. Solo importaría si lo enseñas a mucha gente a la vez —
vigílalo en *Facturación* si eso pasa.

### 2 · Apuntar el DNS (Hostinger)

**Un solo dominio sirve todo**: dashboard, API, webhooks y vídeo. No hace falta un
subdominio aparte para el backend, porque el gateway sirve el dashboard construido como
estáticos en `/` y Caddy reparte por ruta:

```
https://taiafox.ignaciogarbayo.com/            → dashboard (React construido)
https://taiafox.ignaciogarbayo.com/api/*       → API del gateway
https://taiafox.ignaciogarbayo.com/webhooks/*  → HappyRobot y Telegram
https://taiafox.ignaciogarbayo.com/ws          → el chorro de eventos
https://taiafox.ignaciogarbayo.com/cam/*       → MediaMTX (HLS del Minecraft)
```

En **hPanel de Hostinger** → *Dominios* → `ignaciogarbayo.com` → **DNS / Nameservers** →
*Administrar registros DNS*. Añade un registro:

| Campo | Valor |
| --- | --- |
| Tipo | **A** |
| Nombre | `taiafox` — solo el subdominio, sin el dominio detrás |
| Apunta a | La **IPv4** del VPS |
| TTL | `60` (el mínimo que deje; con 60 rectificas en un minuto) |

Si Hostinger ya trae un `A` o un `CNAME` para `taiafox`, **bórralo antes**: dos registros
para el mismo nombre se pelean y Caddy falla de forma intermitente, que es lo peor de
depurar. Opcional pero recomendado, un `AAAA` con la IPv6 del VPS.

Hostinger propaga en minutos, no en horas, pero **no sigas hasta que esto conteste**:

```bash
dig +short taiafox.ignaciogarbayo.com          # la IPv4 del VPS, y nada más
```

Si devuelve vacío o una IP de Hostinger (el parking del dominio), Caddy pedirá el
certificado, Let's Encrypt le dirá que no, y a los cinco intentos te mete en su límite
de una hora. Es el error más caro del despliegue: **espera al `dig`**.

> **Ojo con el proxy.** Si algún día mueves el DNS a Cloudflare, el registro tiene que ir
> en **gris (DNS only)**, no en naranja. Con el proxy naranja, Cloudflare termina el TLS
> por su cuenta, Caddy no puede validar y además el WebSocket `/ws` y el HLS pasan por un
> intermediario que no necesitas.

### 2.1 · Dónde vive la landing

La landing (el formulario de los cinco teléfonos) es **otro repo y otro origen**: no se
sirve desde aquí. Dos opciones, y cambian una línea del `.env`:

- **Subdominio aparte** (p. ej. `demo.ignaciogarbayo.com` en Vercel o Netlify): otro
  registro DNS, y en el `.env` del VPS `VELA_CORS_ORIGINS=https://demo.ignaciogarbayo.com`.
  Es lo que asume el plan.
- **La raíz del dominio** (`ignaciogarbayo.com`, tu web de siempre): entonces
  `VELA_CORS_ORIGINS=https://ignaciogarbayo.com`. Cuidado con `www`: si la landing carga
  con `www` delante, ese es **otro origen** y hay que listarlo también, separado por coma.

El origen tiene que ser exacto —esquema, host y puerto—, sin barra al final. `VELA_CORS_ORIGINS`
vacío significa que no se monta el middleware y el formulario dará error de CORS.

### 3 · Preparar el VPS

```bash
ssh root@<IPv4>

apt update && apt upgrade -y
apt install -y docker.io docker-compose-v2 git make
systemctl enable --now docker

# Abrir solo lo necesario, si usas el firewall del sistema
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

Comprueba que Docker vive: `docker run --rm hello-world`.

**En GCP**, dos diferencias: entras como tu usuario y no como `root`, así que antepón
`sudo` a todo (o `sudo -i` una vez y sigue igual); y **sáltate el `ufw`**, porque el
cortafuegos ya lo pusiste al crear la VM con las casillas de HTTP/HTTPS. Poner los dos
no protege más y es un sitio extra donde equivocarse.

**En DigitalOcean**, si creaste un *Cloud Firewall*, sáltate el `ufw` por lo mismo.

### 4 · Clonar el repo y escribir el `.env`

```bash
git clone <url-del-repo> vela && cd vela
cp .env.example .env
nano .env
```

Lo que **no puede quedar vacío**:

| Clave | Qué poner |
| --- | --- |
| `DOMAIN` | `taiafox.ignaciogarbayo.com` — **sin** `https://` y **sin** barra final |
| `ACME_EMAIL` | Tu correo. Let's Encrypt avisa ahí si el certificado va a caducar |
| `RCON_PASSWORD` | Cualquier cadena larga. Paper no se expone, pero el compose la exige |
| `MC_VERSION` | **La misma** que el jar de `infra/server/paper-*.jar` (hoy `1.21.11`). Si no coincide, el cliente dice «outdated server» |
| `MC_PLAYER` | `vela_cam`. Es también el `VELA_CAM_PLAYER` del gateway |
| `VELA_PUBLIC_URL` | `https://taiafox.ignaciogarbayo.com/#/minecraft` |
| `VELA_CORS_ORIGINS` | El origen **exacto** de la landing, sin barra final. Varios, separados por coma. Sin esto el formulario da error de CORS |
| `VELA_LANDING_URL` | La landing, para el enlace «volver» de la sala de espera |
| `HAPPYROBOT_INBOUND_NUMBER` | El número al que llama el visitante haciendo de vecino |
| `PHONE_FIRE_CREW` … `PHONE_NEIGHBOR` | Los cinco por defecto. Solo mandan si alguien arranca **sin** `phones` |
| Las claves de API | Las de arriba |

`VELA_RUN_MAX_S=420`, `VELA_CAM_SHOT=aguila`, `CAM_RES`, `CAM_FPS` y `CAM_CPUS` ya vienen
bien por defecto: no los toques hasta medir los fps (paso 8).

### 5 · Primer arranque

```bash
make deploy-up
```

Tarda. Construye dos imágenes, itzg baja Paper y genera el mundo plano, y el cliente se
descarga los assets de Mojang (~500 MB al volumen `mc-home`). Vigílalo en otra terminal:

```bash
make deploy-ps                 # los cinco, `healthy` o `running`
make deploy-logs S=paper       # «Done (…)! For help, type help»
make deploy-logs S=cam         # «Paper contesta» → «lanzando Minecraft»
```

Cuando Caddy tenga el certificado (`make deploy-logs S=caddy`, busca `certificate obtained`),
`https://taiafox.ignaciogarbayo.com` ya carga el dashboard.

### 6 · La cuenta de Minecraft de la cámara

Hasta aquí el cliente entra **offline** con `MC_PLAYER` y Paper lo acepta, pero eso es zona
gris del EULA. Para entrar con cuenta real:

```bash
make deploy-login EMAIL=<la-cuenta-microsoft>
```

Enseña un código de dispositivo: ábrelo en `microsoft.com/link` desde el móvil y autoriza.
Luego, en el `.env`:

```
MC_LOGIN=<esa misma cuenta>
```

y `make deploy-up` otra vez. La sesión queda en el volumen `mc-home` y `portablemc` la
refresca sola; **caduca si pasan ~90 días sin usarla**.

El código que pide `--auth-no-browser` **no es el de seis dígitos** de Microsoft. Tras
loguearte, el navegador acaba en `theorozier.fr/portablemc/auth#...`: hay que copiar de la
barra de direcciones **todo lo que va después del `#`**, sin el `#`. `portablemc` le pasa
un `parse_qs` y exige que dentro estén las dos claves, `code` e `id_token`; el orden da
igual, así que es normal que empiece por `id_token=`.

> **Al poner `MC_LOGIN` hay que cambiar `MC_PLAYER` también.** Con cuenta real el cliente
> entra con el **nombre de usuario de Minecraft de esa cuenta**, no con `MC_PLAYER` (que
> solo se usa en modo offline). Pero el gateway busca al jugador por `VELA_CAM_PLAYER`,
> que el compose saca de `MC_PLAYER`. Si no coinciden, espera 120 s a alguien que nunca
> aparece y **no coloca la cámara**: hay vídeo, pero del sitio equivocado.
>
> El nombre real lo dice Paper cuando el cliente entra:
>
> ```bash
> make deploy-logs S=paper | grep -i "joined the game" | tail -3
> ```
>
> Ese nombre, tal cual y con sus mayúsculas, va en `MC_PLAYER`. Se comprueba con
> `/api/health`: `notes.camera` tiene que decir «en espectador · plano aguila» y no
> «no ha entrado en 120s».

### 7 · Repuntar HappyRobot y Telegram

Se acabó ngrok: la URL ya no cambia. Una sola vez, desde cualquier portátil con el `.env`:

```bash
uv run python scripts/retunnel.py --apply https://taiafox.ignaciogarbayo.com
```

Repunta los nueve `VELA_URL` de HappyRobot y el webhook de Telegram. Sin argumentos te
enseña a dónde apuntan ahora, que es lo primero que hay que mirar si las llamadas salen
pero no vuelve nada.

### 8 · Medir los fps de la cámara

**Este es el paso que no se puede saltar**, porque es el único riesgo sin medir. Con un run
en marcha:

```bash
docker stats --no-stream                       # `cam` no debería clavar sus 4 vCPU al 100%
make deploy-logs S=cam | grep -i fps
```

Y a ojo en `https://taiafox.ignaciogarbayo.com/#/minecraft`: los camiones tienen que moverse con continuidad,
no a saltos. Si va por debajo de ~10 fps, en este orden:

1. **`CAM_RES=960x540`** en el `.env` y `make deploy-up`. Es la palanca buena: menos
   píxeles que rasterizar y menos que codificar, y no toca el encuadre.
2. **`CAM_FPS=10`**. La cámara está fija: nadie va a notar la diferencia entre 15 y 10.
3. **Más máquina**, o una instancia con GPU (ver arriba).

> **`renderDistance` NO es una palanca, aunque lo parezca.** Bajarlo acerca la niebla, y la
> niebla es lo que limita la altura del plano cenital. Medido en `wildfire_ridge` (radio
> 144 bloques, hace falta Y≥186 para encuadrarlo entero):
>
> | `renderDistance` | Niebla limpia | Y máxima limpia | ¿Encuadra? |
> | --- | --- | --- | --- |
> | **16** | 192 | **191** | **sí** |
> | 12 | 144 | 73 | no |
> | 10 | 120 | — | no, ni a ras de suelo |
>
> O sea: **16 no es holgura, es el mínimo.** Con 12 el valle ya no cabe en plano. Si de
> verdad hace falta bajarlo, hay que encoger antes el escenario en su YAML, y eso mueve
> distancias y tiempos de toda la demo.

### 9 · Ensayo completo antes de enseñárselo a nadie

Primero sin marcar a nadie:

```bash
curl -X POST https://taiafox.ignaciogarbayo.com/api/run   -H 'content-type: application/json'   -d '{"scenario_id":"wildfire_ridge","mock_calls":true}'
```

Abre `https://taiafox.ignaciogarbayo.com/#/minecraft`: valle completo, fuego a los ~60 s, camiones moviéndose.
Comprueba `https://taiafox.ignaciogarbayo.com/api/health` → `notes.camera` dice «en espectador · plano aguila»
y **no** dice «RECORTA».

Y luego el de verdad, con los cinco móviles del equipo, desde la landing:

- Las cuatro salientes suenan **en orden**: retén y ambulancia primero, y nadie se mueve
  hasta que cuelguen; luego Pueblo A.
- La entrante desde el móvil «vecino» aparece en el panel de llamadas con la insignia
  **«tu llamada»**.
- A los 7 minutos el run se para solo y `GET /api/demo/status` vuelve a `busy: false`.
- En una red que solo deje salir por 80/443 (el hotspot del móvil vale), el vídeo carga
  igual: es HLS sobre HTTPS.

## Cada vez que se despliega código

```
git pull && make deploy-up        # reconstruye lo que cambió; los volúmenes se quedan
make deploy-ps                    # los cinco `healthy`/`running`
make deploy-logs S=cam            # «Paper contesta», «lanzando Minecraft», y sin bucles de salida
```

Ensayo sin marcar a nadie: `curl -X POST https://taiafox.ignaciogarbayo.com/api/run -H 'content-type: application/json'
-d '{"scenario_id":"wildfire_ridge","mock_calls":true}'` y abrir `https://taiafox.ignaciogarbayo.com/#/minecraft`.
El run se para solo a los `VELA_RUN_MAX_S` segundos (420); antes, `POST /api/run/stop`.

## Lo que la landing tiene que hacer

Está en `docs/interfaces.md` («La /demo autoservicio»): `GET /api/demo/status` para saber si
está libre y pintar los cinco papeles, `POST /api/run` con `phones`, y llevar al visitante a
`watch_url`. Los cinco teléfonos son obligatorios; el mismo número puede repetirse.

## Si algo falla

- `cam` en bucle «el cliente ha salido»: casi siempre versión distinta de Paper
  (`MC_VERSION`) o la sesión de Microsoft caducada (`make deploy-login` otra vez).
  `make deploy-logs S=cam` lo dice.
- El vídeo dice «Conectando…» y no arranca: `curl https://taiafox.ignaciogarbayo.com/cam/vela/index.m3u8`
  tiene que devolver una playlist. Si 404, ffmpeg no está publicando (mira `cam`); si
  Caddy da 502, `mediamtx` está caído.
- El plano no es el de águila: `VELA_CAM_PLAYER` y `MC_PLAYER` no coinciden, o el cliente
  entró después de los 120 s que el gateway espera (`/api/health` → `notes.camera`).
  Arrancar otro run lo vuelve a colocar.
- `notes.camera` dice **RECORTA**: el escenario no cabe entero bajo el techo de niebla, y
  algo (un pueblo, el hospital) se está quedando fuera de plano. No se arregla subiendo la
  cámara —por encima del techo los marcadores llegan grises—, sino encogiendo el escenario
  en su YAML o subiendo `renderDistance` en `cam/options.txt`, que cuesta fps. Con
  `renderDistance:16` y los escenarios de hoy no debería aparecer nunca.
- El vídeo se ve pero el plano está girado: `CENITAL_YAW` es 180 para que el norte quede
  arriba. Si alguien lo toca, el valle sale de lado y la mitad se pierde.
- Paper `unhealthy` al arrancar: la primera vez tarda hasta un minuto (baja el jar y
  genera el mundo plano); `start_period` ya lo contempla.
- Llamadas que no vuelven (unidades retenidas hasta agotar plazo): el `VELA_URL` de
  HappyRobot no es este dominio. `scripts/retunnel.py` sin argumentos lo enseña.
