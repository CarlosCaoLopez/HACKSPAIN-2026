# Workflow `citizen_report` en HappyRobot — checklist de configuración

**Dueño: Hugo.** Es lo que hay que clicar en la UI de HappyRobot para que la llamada entrante hable con nuestro backend. Lo que va en cursiva es el nombre exacto del campo en la UI. Verificado contra `docs.happyrobot.ai` el viernes.

## 0. Antes de abrir la UI

- Servidor local arriba: `python -m voice.dev` (o `make dev-voice` cuando exista el gateway). Escucha en `:8000`.
- Túnel: `ngrok http 8000` → copia la URL `https://xxxx.ngrok-free.app`. **Cambia cada vez que arrancas ngrok**: por eso va en una variable de entorno del workflow, no en los nodos.
- `WEBHOOK_SHARED_TOKEN` del `.env` a mano: es la cabecera `X-Vela-Token`.
- En HappyRobot: *Settings > API Keys* → una key → `HAPPYROBOT_API_KEY` en `.env`. *Assets > Telephony* → un número con *Calling status: Synced* (inbound).

## 1. Variables de entorno del workflow

*Workflow settings > Workflow variables* (o *Settings > Environment variables*):

| Nombre | Valor |
| --- | --- |
| `VELA_URL` | `https://xxxx.ngrok-free.app` (sin barra final) |
| `VELA_TOKEN` | el `WEBHOOK_SHARED_TOKEN` |

## 2. Trigger

*Inbound to Number* → asignar el número. Expone `caller_number`, `called_number`.

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
Nunca repitas una señal ya dicha ni menciones que recibes señales.
```

*Built-in > Stay silent*: **ON** (con *Play acknowledgement* ON).

## 4. Tool `report_fact` (dentro del agente)

- *Description*: «Usa esta herramienta en cuanto sepas dónde está el vecino y al menos un dato más: carretera cortada, personas que no pueden moverse, heridos. Registra el incidente en el centro de coordinación y devuelve lo que debes decirle al vecino.»
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

- *Nodo hijo: Webhook* → *POST*, *URL* `@VELA_URL/webhooks/happyrobot/fact`, *Headers* `X-Vela-Token: @VELA_TOKEN`, *Content type* `application/json`, *Body (Raw)*:

```json
{
  "session_id": "{{session_id}}",
  "run_id": "{{current.run_id}}",
  "caller_number": "{{trigger.caller_number}}",
  "params": {
    "location_hint": "{{location_hint}}",
    "road_blocked": "{{road_blocked}}",
    "people_immobile": "{{people_immobile}}",
    "injuries": "{{injuries}}",
    "urgency": "{{urgency}}"
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
  "caller_number": "{{trigger.caller_number}}",
  "transcript": "{{agent.transcript}}"
}
```

Los nombres exactos de `session_status` y `agent.transcript` salen del selector `@` sobre el nodo del agente. *Ignore 5XX errors*: ON.

## 6. Inicio de llamada (para que el SSE arranque antes del tool)

*Workflow settings > Signals > Outbound webhooks* → *Add Webhook*: URL `@VELA_URL/webhooks/happyrobot/call`, header `X-Vela-Token`. Si los eventos que manda no llevan `type: start`, no pasa nada: el monitor arranca en el primer tool y la transcripción se recupera del webhook de fin.

## 7. Publicar y probar

1. *Publish*. Llama al número desde tu móvil. Di: «Hola, estoy en el molino viejo, la pista del sur está cortada por un árbol y hay tres personas en la casa de al lado que no pueden andar».
2. En el log del servidor tienen que aparecer `world.fact.asserted road:wp_sur_03-wp_sur_04:cut=True`, `poi:poi_molino:immobile=3`, y `call.affect fear:0.x` si el token de Humalike está.
3. El agente tiene que decirte el `message` del ack («Anotado, Molino viejo, …»).
4. Con la llamada aún abierta, manda la señal a mano (lo que hará el core tras el replan):

```bash
curl -s localhost:8000/dev/calls   # coge el session_id
curl -s -X POST localhost:8000/dev/signal -H 'content-type: application/json' \
  -d '{"call_id": "<session_id>", "key": "unit_dispatched",
       "payload": {"unit": "camión 2", "route": "pista norte", "eta_s": 40}}'
```

   El agente tiene que decir «Ya va camión 2 por pista norte, llega en 40 segundos…». Cronómetro desde el `curl` hasta que empieza a hablar: objetivo ≤ 2 s.
5. Cuelga. En el log: `call.ended health_score=0.xx`. `GET /dev/events?n=40` para ver la secuencia completa.

## Fallos típicos

| Síntoma | Causa |
| --- | --- |
| El tool devuelve 401 | `VELA_TOKEN` no coincide con `WEBHOOK_SHARED_TOKEN` |
| El tool devuelve 422 `session_id` | El body no lleva `session_id` ni `call_id`: revisa el nombre de la variable en el selector `@` |
| El agente no dice el ack | En *Tool Call Result* no está expuesto `message` |
| La señal no llega | *Agent Signals* apagado, o `call_id` del curl no es el `session_id` de HappyRobot (mira `/dev/calls`) |
| No hay `call.affect` | `HUMANLIKE_API_KEY` vacío o sin créditos (`402` en el log, una vez) |
| Sin `call.transcript.partial` en vivo | `HAPPYROBOT_API_KEY` vacío o el SSE devolvió 4xx; la transcripción llega igual al colgar |
