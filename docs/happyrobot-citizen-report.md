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
