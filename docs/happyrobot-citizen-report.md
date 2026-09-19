# Workflow `citizen_report` en HappyRobot — checklist de configuración

**Dueño: Hugo.** Es lo que hay que clicar en la UI de HappyRobot para que la llamada entrante hable con nuestro backend. Lo que va en cursiva es el nombre exacto del campo en la UI. Verificado contra `docs.happyrobot.ai` el viernes.

## 0. Antes de abrir la UI

- Servidor local arriba: `python -m voice.dev --dummy-core` (o `make dev-voice` cuando exista el gateway). Escucha en `:8000`. `--dummy-core` hace de core mientras Carlos no emita `call.signal.requested`.
- Túnel: `ngrok http 8000` → copia la URL `https://xxxx.ngrok-free.app`. **Cambia cada vez que arrancas ngrok**: por eso va en una variable de entorno del workflow, no en los nodos.
- `WEBHOOK_SHARED_TOKEN` del `.env` a mano: es la cabecera `X-Vela-Token`.
- En HappyRobot: *Settings > API Keys* → una key → `HAPPYROBOT_API_KEY` en `.env` (la API de plataforma pide una `hr_…`; una `sk_live_…` solo sirve para hooks). **Para la entrante no hace falta número**: HappyRobot pide usar el trigger *Web Call*. El número US, que cubren ellos, es solo para la saliente.

## 1. Variables de entorno del workflow

*Workflow settings > Workflow variables* (o *Settings > Environment variables*):

| Nombre | Valor |
| --- | --- |
| `VELA_URL` | `https://xxxx.ngrok-free.app` (sin barra final) |
| `VELA_TOKEN` | el `WEBHOOK_SHARED_TOKEN` |

## 2. Trigger

*Web Call*. En *Webcall access* está el enlace `https://platform.happyrobot.ai/deployments/{slug}` (uno por entorno); copiarlo a `HAPPYROBOT_WEBCALL_URL` en `.env`. Desactivar *Enhanced security* en el trigger para ese entorno, o pedirá login al abrirlo. No hay `caller_number` (vale `web`): si el agente necesita un teléfono para devolver la llamada, lo pide y lo manda por el tool como `callback_number`.

## 3. Nodo *Inbound Voice Agent*

- *Languages*: `es-ES`. *End-of-turn detection*: *Multilingual v1*. *Voice*: una con acento de España (filtrar por idioma en *Assets > Voices*).
- *Recording disclaimer*: *AI and recording disclosure*, *Recording language: Auto* (obligatorio en la UE; son 3 s al descolgar).
- *Background noise*: *Call center* o *No background noise*. *Voice speed*: 0.95.
- *Max call duration*: 300 s.
- *Transcription context*: «Llamadas de vecinos durante un incendio forestal en un valle de Castilla. Mencionan pueblos, el molino viejo, el hospital, el refugio, la pista del sur y la del norte, personas que no pueden andar, heridos, humo.»
- *Key terms*: `molino viejo`, `pista del sur`, `pista del norte`, `Pueblo A`, `Pueblo B`, `refugio`, `hospital`.
- *Real-time analysis > Custom classifier*: nombre `urgency`, clases `low, medium, critical`, prompt «Urgencia de lo que cuenta el vecino según lo que dice y cómo lo dice». Opcional.
- *Agent Signals*: **ON**. *Start agent response on signal*: **ON**.

### Prompt del agente (nodo *prompt* dentro del agente)

```
Eres el operador de emergencias del 112 durante un incendio forestal. Hablas en
español de España, con calma, frases cortas, sin tecnicismos. Primero pregunta
dónde está la persona. Luego: cuántos son, si alguien no puede moverse por su
cuenta, si hay heridos, y qué carretera o pista está cortada. Repite lo importante
para confirmar. No cuelgues hasta tener la localización.

En cuanto la persona te dé una localización y al menos un dato más (carretera
cortada, personas sin movilidad, heridos), llama a la herramienta `report_fact`
con lo que sepas. Puedes llamarla más de una vez si la persona añade información.
Di al vecino exactamente el campo `message` que devuelve la herramienta.

Recibirás señales durante la llamada:
- `unit_dispatched`: di al vecino exactamente el campo `message`. Es la noticia de
  que va una unidad; es lo más importante que le vas a decir.
- `coach`: es una indicación sobre cómo hablar, no algo que decir. Si `action` es
  `hold`, guarda silencio y deja que la persona termine. Si es `acknowledge`, di
  solo una muletilla corta como «sí, le escucho». Si es `speak` y trae `say`, dilo.
  En todos los casos, en los siguientes turnos adapta el tono (`tone`: calm,
  firm, warm) y el ritmo (`pace`: si es `slow`, frases más cortas y más pausa).
- `followup`: el sistema no ha entendido un dato y te pide que lo preguntes:
  haz al vecino exactamente la pregunta del campo `message`, una sola vez, y
  sigue escuchando.
Nunca repitas una señal ya dicha ni menciones que recibes señales.
```

*Built-in > Stay silent*: **ON** (con *Play acknowledgement* ON).

## 4. Tool `report_fact` (dentro del agente)

- *Description*: «Usa esta herramienta en cuanto sepas dónde está el vecino y al menos un dato más: carretera cortada, personas que no pueden moverse, heridos. Registra el incidente en el centro de coordinación y devuelve lo que debes decirle al vecino.»
- Desde el sábado el tool es **solo un disparador**: lo que entra al estado lo decide la percepción (Jev, cada 5 s y al colgar), no los parámetros. Los parámetros siguen siendo útiles como texto para el ack y como red de seguridad sin Jev.
- *Message*: *AI*, descripción «Dile al vecino que lo estás anotando, en una frase corta». Ejemplo: «Un momento, lo anoto.»
- *Hold music*: *None*. *Execution*: *Blocking*. *End call after this tool*: OFF.
- *Parameters*:

| Nombre | Descripción | Obligatorio |
| --- | --- | --- |
| `location_hint` | Lugar donde está la persona, tal cual lo dice («el molino viejo», «Pueblo B») | sí |
| `road_blocked` | Carretera o pista que dice que está cortada, tal cual («la pista del sur») | no |
| `people_immobile` | Número de personas que no pueden moverse por su cuenta. Solo el número | no |
| `injuries` | Número de heridos. Solo el número | no |
| `urgency` | `low`, `medium` o `critical` según lo que cuenta | no |
| `callback_number` | Teléfono al que devolver la llamada, si la persona lo da. Solo dígitos con prefijo | no |

- *Nodo hijo: Webhook* → *POST*, *URL* `@VELA_URL/webhooks/happyrobot/fact`, *Headers* `X-Vela-Token: @VELA_TOKEN`, *Content type* `application/json`, *Body (Raw)*:

```json
{
  "session_id": "{{session_id}}",
  "run_id": "{{current.run_id}}",
  "params": {
    "location_hint": "{{location_hint}}",
    "road_blocked": "{{road_blocked}}",
    "people_immobile": "{{people_immobile}}",
    "injuries": "{{injuries}}",
    "urgency": "{{urgency}}",
    "callback_number": "{{callback_number}}"
  }
}
```

  Si en el editor `session_id` no aparece con ese nombre, escribe `@` y busca el id de sesión del agente; el backend acepta también `call_id`.

- **View Tool Call Result** → *Generate* (ejecuta el webhook de verdad contra el túnel: ten el servidor arriba) → exponer `message` y `resolved_poi_name`. Sin esto el workflow no publica.

## 5. Después del agente: nodo *Webhook* de fin de llamada

*POST* `@VELA_URL/webhooks/happyrobot/call`, cabecera `X-Vela-Token: @VELA_TOKEN`, *Body (Raw)*:

```json
{
  "type": "end",
  "session_id": "{{session_id}}",
  "run_id": "{{current.run_id}}",
  "direction": "inbound",
  "status": "{{session_status}}",
  "transcript": "{{agent.transcript}}"
}
```

Los nombres exactos de `session_status` y `agent.transcript` salen del selector `@` sobre el nodo del agente. *Ignore 5XX errors*: ON.

## 6. Inicio de llamada (para que el SSE arranque antes del tool)

*Workflow settings > Signals > Outbound webhooks* → *Add Webhook*: URL `@VELA_URL/webhooks/happyrobot/call`, header `X-Vela-Token`. Si los eventos que manda no llevan `type: start`, no pasa nada: el monitor arranca en el primer tool y la transcripción se recupera del webhook de fin.

**No** pongas un nodo Webhook entre el trigger y el agente para mandar el `start`: ni el trigger *Web Call* ni el *Inbound to number* exponen `session_id` (solo lo tiene el agente, y el backend lo exige y lo usa de clave del monitor), así que el cuerpo iría vacío (`422`) o con otro id (tarjeta fantasma); y además retrasaría el descolgar lo que tarde el túnel. Detalle en el anexo del paso 8.

## 7. Publicar y probar

1. *Publish*. Abre el enlace de la web call (Chrome, permiso de micrófono) en el portátil o el móvil y pulsa para hablar. Di: «Hola, estoy en el molino viejo, la pista del sur está cortada por un árbol y hay tres personas en la casa de al lado que no pueden andar».
2. En el log del servidor tienen que aparecer `world.fact.asserted road:wp_sur_03-wp_sur_04:cut=True`, `poi:poi_molino:immobile=3`, y `call.affect fear:0.x` si el token de Humalike está.
3. El agente tiene que decirte el `message` del ack («Anotado, Molino viejo, …»).
4. Con la llamada aún abierta, la señal del replan. Si el servidor corre con `--dummy-core`, sale sola 300 ms después del hecho de carretera cortada (es lo que hará el core de Carlos). Si no, mándala a mano:

```bash
curl -s localhost:8000/dev/calls   # coge el session_id
curl -s -X POST localhost:8000/dev/signal -H 'content-type: application/json' \
  -d '{"call_id": "<session_id>", "key": "unit_dispatched",
       "payload": {"unit": "camión 2", "route": "pista norte", "eta_s": 40}}'
```

   El agente tiene que decir «Ya va camión 2 por pista norte, llega en 40 segundos…». `GET /dev/latency` da los tramos: hecho → petición → señal aceptada, y si Humalike llegó a tiempo. Objetivo: `fact_to_sent_ms ≤ 2000`, y de oído, desde tu frase hasta que el agente la repite, ≤ 3 s.
5. Cuelga. En el log: `call.ended health_score=0.xx`. `GET /dev/events?n=40` para ver la secuencia completa.

## Fallos típicos

| Síntoma | Causa |
| --- | --- |
| El tool devuelve 401 | `VELA_TOKEN` no coincide con `WEBHOOK_SHARED_TOKEN` |
| El tool devuelve 422 `session_id` | El body no lleva `session_id` ni `call_id`: revisa el nombre de la variable en el selector `@` |
| El agente no dice el ack | En *Tool Call Result* no está expuesto `message` |
| La señal no llega | *Agent Signals* apagado, o `call_id` del curl no es el `session_id` de HappyRobot (mira `/dev/calls`) |
| No hay `call.affect` | `HUMALIKE_API_KEY` vacío o sin créditos (`402` en el log, una vez) |
| Sin `call.transcript.partial` en vivo | `HAPPYROBOT_API_KEY` vacío o el SSE devolvió 4xx; la transcripción llega igual al colgar |

## Anexo · workflow saliente `test` → `evacuation_order` (montado por API el sábado)

Estado (sábado 11:30): **publicado y vivo en development, versión 8**, origen `+1 484 558 1911` (Twilio; el Telnyx `+1 361 210 1724` no tiene salida internacional, `SIP 403`). Llamada real contestada: 63 s, transcripción por turnos, `call.ended` con `task_id` y `analyze`, del workflow `test` (`301cfio7aosi`). Probado de punta a punta con el `trigger()` de Carlos: el hook acepta con la key `sk_live_` como `Bearer`, el agente resuelve el `to`, marca desde `+1 361 210 1724` y el webhook de fin llega a `/webhooks/happyrobot/call` con `run_id` (el de la plataforma, el mismo que devolvió el hook), `task_id`, `to`, `status` y transcripción. Con el número Telnyx la llamada la rechazaba el operador (`sip_code 403`, sin salida internacional); con el Twilio `vela-out-twilio` conecta. Un intento fallido queda archivado como `call.ended` con `outcome: failed` (o `no_answer` si salta el buzón), así que el core puede reintentar o escalar.

Nodos (ids de la versión 2):

| Nodo | Tipo | Detalle |
| --- | --- | --- |
| Receive external update | trigger Webhook | Variables bajo `data.*` (`data.to`, `data.poi_name`, `data.route_name`, `data.deadline_min`, `data.hazard_kind`, `data.severity`, `data.run_id`, `data.task_id`). Payload de ejemplo enviado a `hooks/301cfio7aosi/9wsaydvqsgke` |
| Coordinador 112 | Outbound Voice Agent | `to` = objeto variable `{group_id: <persistent_id del trigger>, variable_id: "data.to"}` (la forma cruda `{{$var:…}}` **no** se resuelve en campos de párrafo; el aviso *missing variable* al publicar es solo un aviso). `from_number` = `{type: static, static: {id: "+13612101724", name: "+13612101724"}}`: la plataforma busca el **trunk por su nombre**, ni el id del número ni el uuid del trunk valen. Voz Daniel HR, `es`, disclaimer UE, 180 s, buzón → colgar, `gracefully_handle_invalid_phone`, signals ON |
| Prompt | prompt | Orden de evacuación con las variables del trigger como `{{<persistent_id del trigger>.data.poi_name}}` (probado en llamada: `{{$var:…}}`, `{{poi_name}}` y `@trigger.poi_name` se leen literales; `{{data.x}}` y `{{trigger.x}}` dan `<no-value>`); modelo `gpt-5.6-luna`. Sale "incompleto" en el listado igual que el del entrante; no bloquea |
| POST call end | Webhook POST | `@VELA_URL/webhooks/happyrobot/call` con `X-Vela-Token`, cuerpo crudo con `{{$var:…}}`: `run_id` = `{{$var:current.run_id}}` (los campos del trigger en cuerpos crudos pueden resolver al payload de ejemplo), `session_id`/`status`/`transcript` del agente, `task_id`/`to` del trigger (`data.*`) |

Cosas aprendidas de la API que no están en la documentación: las variables de un nodo se direccionan por su `persistent_id` (el de la versión original, no el de la bifurcación); los campos de un trigger Webhook cuelgan de `data.`; la API es la de la región de la organización (`platform.eu.happyrobot.ai`), la US rechaza la key; el hook responde `run_id`, no `call_id`; `update-a-node` es `PUT` y el `type` del cuerpo tiene que coincidir con el del nodo (el trigger creado en la UI es `action`).

El entrante `citizen_report` (`ikdg6o9mjj9h`) está **publicado y vivo en development** desde la API: enlace de la web call `https://platform.eu.happyrobot.ai/deployments/development/ikdg6o9mjj9h`, sin login.

## Anexo · plan B del entrante por teléfono: `citizen_report_phone`

La wifi del evento bloquea UDP y la web call (WebRTC) no levanta el audio: el websocket de señalización conecta pero `could not establish pc connection`. Con hotspot del móvil debería ir. Como no puede depender de eso, hay una copia del entrante que se atiende **por teléfono**: workflow `citizen_report_phone` (`wyoxcfeop329`), trigger *Inbound to number* sobre el Twilio `+1 484 558 1911`, mismo agente, mismo prompt, mismo tool `report_fact` y mismos webhooks, **publicado y vivo en producción** (la lista de números del trigger solo cuenta como asignación de producción; el `Missing numbers` al publicar en development se resuelve desde la UI asignando el número a ese entorno).

Uso: el vecino llama al `+1 484 558 1911` desde un móvil (llamada internacional a EE. UU., la paga quien llama). El resto es idéntico: tool → hechos → ack con plan → `call.ended`. El mismo número es el origen de la saliente `evacuation_order`; las dos cosas conviven.

Montado por API: `POST /workflows/{slug}/duplicate` (solo copia; el trigger se sustituye a mano), `DELETE /versions/{v}/nodes/{id}` exige cuerpo JSON `{}`, el trigger copiado se actualiza con `type: "action"`, y `numbers` es una lista plana de `{id, name, number}` tal como la devuelve `available_options.phone_numbers`.

## Anexo · paso 8 de la demo: ubicación por Telegram (montado por API el sábado ~13:45)

Estado (sábado ~14:20): `citizen_report` (`ikdg6o9mjj9h`) **v3 viva en development** y `citizen_report_phone` (`wyoxcfeop329`) **v3 viva en production**; v1 y v2 quedan publicadas-no-vivas por si hay que volver. Los dos llevan el mismo prompt (bloque final abajo), el tool `report_fact` expone `message`, `resolved_poi_name`, `telegram_hint` y `telegram_bot`, y hay una variable de workflow `TELEGRAM_BOT` (dev/staging/prod = `vela_112_bot`, **provisional y ya no referenciada desde el prompt**: el nombre del bot lo pone el backend dentro de `message` a partir de `TELEGRAM_BOT_USERNAME` del `.env`; la variable queda por si hace falta en otro nodo). `VELA_URL` y `VELA_TOKEN` no se han tocado (VELA_URL apunta al túnel `https://75ec-79-117-104-222.ngrok-free.app` en los tres entornos de los dos workflows, releído por API tras publicar). Webhooks comprobados en la v3: `POST fact` → `{{VELA_URL}}/webhooks/happyrobot/fact` y `POST call end` → `{{VELA_URL}}/webhooks/happyrobot/call`, los dos con `X-Vela-Token` e *Ignore 5XX*. Agent Signals ON y *Start agent response on signal* ON en ambos.

**Hallazgo importante**: en `citizen_report_phone` (el de la demo, **0 runs hasta hoy**) el *Tool Call Result* de `report_fact` se había generado contra un 422 del backend y el esquema era `{"error": "{\"detail\":\"session_id\"}"}` con solo `error` expuesto: el agente **no habría recibido `message`** al llamar. Arreglado regenerando el esquema (abajo).

### Bloque final del prompt (v3, idéntico en los dos workflows)

```
Si el vecino no sabe decir dónde está exactamente:
- Llama a report_fact igualmente en cuanto tengas cualquier dato: un lugar aproximado («cerca de unas casas al final de la pista»), una carretera o pista cortada, personas que no pueden moverse o heridos. No esperes a tener la ubicación exacta.
- Di exactamente el campo "message" del resultado de la herramienta, sin añadir nada: si hay que pedir la ubicación por Telegram, la petición ya viene dentro de "message". No la repitas con otras palabras.
- Si "telegram_hint" es true, no cuelgues: quédate en línea, pregunta al vecino si ha podido enviar la ubicación y sigue atendiendo las señales unit_dispatched y coach como siempre.
```

Por qué cambió respecto a la v2: el `message` del ack **ya** lleva la frase «Si tiene Telegram, mande su ubicación al bot @… y sabremos exactamente dónde está» (`voice/webhooks.py`, `with_telegram_hint` + `ensure_bot_mentioned`; el backend es el dueño de la frase), y el bloque v2 mandaba decir `message` y además pedir la ubicación, así que el agente lo decía dos veces. También se ha quitado el inciso `{{use_case_variables.TELEGRAM_BOT}}`: la plataforma lo reescribía a `{{ index . "use_case_variables.TELEGRAM_BOT" }}` al guardar (reconoce la referencia), pero no se había oído resolverse en una llamada real y el riesgo de que el agente leyese el nombre de la variable en voz alta no compensaba. El resto del prompt (operador 112, señales `unit_dispatched`/`coach`) no cambia. El bloque va en segunda persona (tú), como el resto del prompt; el vecino sigue tratado de usted.

### Webhook de inicio de llamada: por qué no se ha añadido por API

Se quería un nodo Webhook antes del agente con `{"type":"start","session_id":…,"run_id":…,"direction":"inbound"}` para que la tarjeta de voz naciese al descolgar. No es viable tal cual:

- `available-vars` de los dos workflows: el trigger *Web Call* no expone **ninguna** variable y el *Inbound to number* expone solo `from_number`, `to_number`, `room_name`, `raw_headers`, `user_to_user`. `session_id` existe únicamente en el grupo del agente (`{{$var:<persistent_id del agente>.session_id}}`), y un nodo anterior al agente no lo tiene todavía.
- El backend (`_on_start` en `voice/webhooks.py`) exige `session_id` (422 sin él) y lo usa como clave del monitor; con otro id (p. ej. `room_name` o `run_id`) crearía un monitor y una `call.started` fantasma que nunca casarían con el `session_id` del tool y del webhook de fin.
- Un nodo bloqueante entre trigger y agente retrasa el descolgar lo que tarde el túnel (y si el túnel está caído, hasta el timeout).

Alternativas: (a) *Workflow settings > Signals > Outbound webhooks* en la UI (sección 6), que manda eventos de sesión con el id real; (b) cambiar el backend para aceptar `run_id` como clave provisional y reconciliar con `session_id` en el primer tool. Hoy se queda como está: la tarjeta nace en el primer `report_fact`.

### Endpoints usados (base `https://platform.eu.happyrobot.ai/api/v2`, `Authorization: Bearer <HAPPYROBOT_API_KEY>`)

| Para | Llamada |
| --- | --- |
| Leer workflow y versión viva | `GET /workflows/{slug}` (`latest_version`, `live_version`) · `GET /workflows/{slug}/versions` |
| Leer nodos de una versión | `GET /versions/{version_id}/nodes` (`data[]`: `prompt` lleva `prompt_md`, `initial_message`, `model`; `tool` lleva `function` con `parameters`; los Webhook son `action` con `configuration.url/body/headers`) |
| Variables disponibles en un nodo (y su sintaxis) | `GET /versions/{version_id}/nodes/{node_id}/available-vars` (grupo `use_case_variables` = variables de workflow; el agente expone `session_id`, `status`, `transcript`) |
| Editar una versión viva | **no se puede** (`400 Cannot change a published or live version`): `POST /versions/{live_id}/fork` → nueva versión sin publicar con ids de nodo **nuevos** (los `persistent_id` se conservan, por eso los `{{$var:<persistent_id>.campo}}` de los cuerpos siguen valiendo) |
| Prompt | `PUT /versions/{fork_id}/nodes/{prompt_node_id}` con `{"type":"prompt","prompt_md":…,"initial_message":…,"initial_message_uninterruptible":…,"model":…}` |
| Tool Call Result | `POST /versions/{version_id}/tools/{tool_id}/tool-call-result/inspect` (cuerpo `{}`; es POST, con GET da 404; bajo `/nodes/{tool_id}/…` da 404; sobre una versión **viva** responde `400 Cannot change a published or live version`, así que se inspecciona en el fork antes de publicar) · `POST …/tool-call-result/generate` con `{"environment":"staging"}` (**ejecuta el webhook de verdad** con valores de ejemplo: `session_id` real del agente y `params` vacíos) · `PUT …/tool-call-result/visibility` con `{"node_id":<POST fact>,"exposed_fields":[…]}` (lista completa; solo admite campos que existan en el esquema generado: `400 Unknown generated field path(s)`) |
| Variables de workflow | `GET/POST /workflows/{slug}/variables` (`key`, `value_development`, `value_staging`, `value_production`, obligatorios los tres) · `PATCH /workflows/{slug}/variables/{variable_id}` con solo los campos que cambian |
| Publicar | `POST /versions/{fork_id}/publish` con `{"environment":"development"|"production","force":true}` (`force` retira la versión viva anterior; sin él pide `unpublish_version_id`) |

`webhook_payload` en el `PUT` de un nodo **no** sirve para fijar el esquema de un Webhook hijo de tool (se acepta con 200 pero el Tool Call Result no cambia); solo cuenta para el trigger Webhook.

### Cómo regenerar el Tool Call Result sin pasar por el gateway real

El `generate` dispara `POST {{VELA_URL}}/webhooks/happyrobot/fact` con `params` vacíos, así que contra el gateway de verdad publica hechos falsos (o devuelve 422). Truco usado: un mock local que responde el JSON del ack completo (`ack, message, resolved_poi_name, facts_published, plan_included, telegram_hint, telegram_bot`), un segundo túnel sobre el **mismo agente ngrok** vía su API local (`POST localhost:4040/api/tunnels {"name":"vela_mock","addr":"8791","proto":"http"}`; se borra con `DELETE localhost:4040/api/tunnels/vela_mock`), `VELA_URL` de **staging** apuntando al mock (`PATCH …/variables/{id} {"value_staging": …}`), `generate` con `environment: staging`, luego `visibility` y `VELA_URL` de staging de vuelta al túnel real. Los valores dev/prod no se tocan y la versión viva no se entera. Si el backend añade campos nuevos al ack hay que repetirlo: los campos nuevos nacen ocultos.

**Ojo (aprendido en la v3)**: el `fork` **no copia el esquema regenerado** del Tool Call Result: la bifurcación de la v2 (que exponía `telegram_hint`/`telegram_bot`) nació con el esquema base de la v1 (`ack, message, plan_included, facts_published, resolved_poi_name`) y solo `message` y `resolved_poi_name` expuestos. Es decir, **cada vez que se bifurca para tocar el prompt hay que repetir `generate` + `visibility`** antes de publicar, o el agente se queda sin `telegram_hint`. El `generate` manda el `session_id` de ejemplo del agente (`764a5f01-…` en el web, `0a024f80-…` en el phone) y `params` vacíos; el mock de la v3 respondió el ack completo con `telegram_hint: true` y el bot dentro de `message`. Mock y túnel auxiliar se retiran al terminar (`DELETE localhost:4040/api/tunnels/vela_mock`); comprobado que solo queda el túnel `command_line` a `:8000`.

### Para probar (Hugo)

1. Poner el usuario real del bot en `TELEGRAM_BOT` (los tres entornos) en los dos workflows.
2. Llamar al `+1 484 558 1911` y decir «estoy cerca de unas casas al final de la pista, la pista del sur está cortada, no sé el nombre del sitio». El agente tiene que llamar al tool con el lugar aproximado y decir el `message` del ack **una sola vez** (la petición de Telegram va dentro), y con `telegram_hint: true` **no colgar**: preguntar si ha podido mandar la ubicación y seguir en línea.
3. Mandar el pin por Telegram: en el journal `citizen.location`, `call.started` con `channel: telegram` y la unidad hacia el POI anclado.

## Anexo · la llamada saliente al pueblo (v11, sábado tarde)

El workflow `test` (slug `301cfio7aosi`) ya no dicta un guion fijo: es **un solo
workflow para dos llamadas distintas**, y la rama la decide el backend.

- `core.calls` construye el guion completo y lo manda en el cuerpo del hook:
  `role` (`evacuation` | `neighbor_alert`), `situation_brief` (lo que hay que
  contar), `checklist` (lo que hay que preguntar) y `advice_rules` (lo que **no**
  puede inventarse). Al pueblo que arde se le dicta la evacuación; al vecino se le
  avisa de que puede llegarle gente. Está en `pytest`, que es más de lo que puede
  decirse de un prompt.
- Con el guion viajan los **datos en vivo** del `WorldState` de ese segundo:
  `resources` (qué unidades hay y qué están haciendo), `fire_status` (distancia del
  frente y si el viento empuja hacia el pueblo), `roads_status` (cortes, con su
  nombre y su causa) y `unit_eta` (qué unidad va y cuándo llega). El prompt los
  enseña bajo «Datos en tiempo real» y obliga a recomendar solo con ellos.
- Tool nuevo `reportar_situacion` (nodo hijo del agente) → `POST village` →
  `{{VELA_URL}}/webhooks/happyrobot/village` con `call_id` (`current.run_id`),
  `poi_id` y `role` del trigger, y los `params` del tool. El backend publica
  `poi:<id>:headcount|immobile|injuries|confirmed|shelter_ready` como `observed`
  (el POI no se adivina: lo pone el core), y responde `message` +
  `ambulance_dispatched`, que son los campos expuestos al agente. Si hay inmóviles,
  el rescate lo crea `core.tasks._rescue` antes de que el agente hable.

### Dos cosas que costaron encontrar

**Crear nodos**: `POST /versions/{version_id}/nodes` con `{"nodes":[…]}` y
`parent_node_id` (no `parent_id`: da `400 Exactly one of parent_node_id or
parent_node_index is required`). Un Webhook hijo de tool necesita
`event_id: 01926f2b-2973-7ebf-ada1-e984251e27ec` e
`integration_id: 01926a93-064a-7a00-81b2-8ab78d5907e0`, los mismos en todos los
workflows.

**Enseñarle al trigger las variables nuevas**: el *incoming hook* no tiene esquema
propio, aprende del último cuerpo que recibió, así que un campo nuevo en el backend
no existe para el prompt hasta que llega una llamada de verdad. Se le enseña sin
llamar a nadie con `PUT /versions/{fork}/nodes/{trigger_id}` y
`{"type":"action","event_id":"01929b66-a335-7514-a159-cae2fe715286","webhook_payload":…}`
(el `event_id` es obligatorio o responde 400). **El ejemplo tiene que ir envuelto**
como la entrega real —`{"data":{…},"headers":{…},"method":"POST","query":{}}`—: con
el cuerpo pelado las variables pierden el prefijo `data.` y todos los
`{{$var:<persistent>.data.campo}}` (prompt y `POST call end`) dejan de resolver.
El `missing_variables` que devuelve `publish` sale igualmente y es cosmético: lo que
manda es `available-vars` del nodo en la versión viva.

El esquema del Tool Call Result se generó contra un gateway propio en `:8021` con un
túnel auxiliar y `VELA_URL` de **staging** apuntando allí (mismo truco que la v3),
sin tocar el gateway de la demo ni los valores de dev/prod. Con `params` vacíos el
endpoint responde bien y no publica nada, así que aquí no hizo falta mock.

## Anexo · llamar a los medios (v12)

El mismo workflow saliente hace ya cuatro llamadas distintas, y ninguna necesitó
tocar la plataforma salvo para que el trigger aprendiera los campos nuevos: el guion
entero viaja en el cuerpo del hook.

| `role` | Cuándo | Qué pregunta |
| --- | --- | --- |
| `evacuation` | El pueblo con el frente más cerca | recuento, heridos, inmóviles, si acepta |
| `neighbor_alert` | Los demás pueblos, hasta que el fuego les llega a 60 m | si pueden acoger gente |
| `fire_crew` | Al detectarse fuego, una vez por run | si pueden salir ya |
| `ambulance` | Solo si un rescate lo pide y la unidad no está en otra cosa | si pueden ir ya |

Las dos últimas llevan `unit_id` en el cuerpo. Un «no podemos salir» entra como
`unit:<id>:available=false` (clave de contrato, `belief` la aplica) y el solver
reparte en el siguiente plan con los medios que quedan: la llamada cambia el plan, no
solo el registro. El prompt saluda con `callee` («el retén de bomberos», «la dotación
de la ambulancia», «el responsable de Pueblo A»), que lo calcula el backend.

`PHONE_FIRE_CREW` y `PHONE_AMBULANCE` en el `.env`. Sin número, esa llamada no se
hace y se anota una vez: no se cae en el de otro a propósito, porque dos llamadas
simultáneas al mismo móvil dan ocupado (visto el sábado con las dos órdenes de
evacuación). Los pueblos van por `PHONE_<POI>`, que es del interlocutor y no del papel.

**Cada fork pierde el esquema del Tool Call Result**: hay que repetir `generate` +
`visibility` antes de publicar, o el agente se queda sin `message` y habla de memoria.

## Anexo · la cola de ambulancias (v14)

Dos ambulancias y dos camiones en el escenario. Cuando llega un rescate:

- Si queda una ambulancia libre, se la llama (`role: ambulance`) y va.
- Si están las dos ocupadas, **no se cancela la llamada**: se telefonea a la que
  antes vaya a terminar (`role: ambulance_queued`) y se le preguntan dos cosas, si
  podrán ir después y en cuántos minutos. Esa respuesta vuelve al que sigue esperando
  al teléfono como una señal `queued` sobre su llamada viva (`waiting_call_id`, que
  el core saca del `call_id` del hecho `poi:<id>:immobile` que pidió el rescate).
- `must_go_next` lo decide la gravedad del rescate: con un caso crítico el guion no
  negocia, le dice a la dotación que en cuanto termine va directa allí.

Campos nuevos en el cuerpo del hook: `unit_id`, `must_go_next`, `waiting_call_id`,
`immobile`. Parámetro nuevo del tool: `available_after_min`. Como siempre, cada fork
obliga a repetir `generate` + `visibility`.

Ojo con el cuerpo del nodo Webhook del tool: se edita con
`PUT /versions/{fork}/nodes/{id}` mandando `type`, `event_id`, `integration_id` y la
`configuration` entera (no acepta un parche parcial), y los `{{$var:…}}` del `raw` se
escriben a mano. Es fácil olvidarse de un campo y que el backend no se entere de nada:
la v13 salió sin `unit_id` y el camino de medios quedaba muerto.
