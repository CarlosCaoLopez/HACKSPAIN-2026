# Walkthrough de la demo — vela / wildfire_ridge (6 min, live)

Guion segundo-a-segundo del pitch de HackSpain 2026. Escenario `wildfire_ridge` **en vivo**
(sim + RCON + Jev + HappyRobot reales), llamada entrante **real** del vecino de Pueblo B. Cada beat
justifica **qué mostramos y qué pregunta del jurado contesta**.

Fuente: `docs/backbon_corrected.md` (rev. 2), tabla L92–107 + reencuadre L574–576. Injects reales que
mandan el guion (`scenarios/wildfire_ridge.yaml`):
- `wind_shift` bearing 270→300 a **150 s** (2:30)
- `road_cut` `road:wp_sur_01-wp_sur_02` "árbol caído" a **210 s** (3:30)
- `unit_failure` `unit_truck2` "avería de bomba" a **240 s** (4:00)

> **Manda el código, no la spec.** `backbon_corrected.md:311` dice *«no hay llamada saliente a un
> tercero»*; eso quedó superado por las revisiones v11/v12/v14 de `docs/happyrobot-citizen-report.md`.
> Hoy el run hace **cuatro salientes y una entrante**, verificado en journal. Si el guion y la spec se
> contradicen, gana lo que el journal enseña.

Geografía: POIs `poi_base` `poi_pueblo_a`(24 civ,3 inmóviles) `poi_pueblo_b`(15 civ,1 inmóvil)
`poi_hospital` `poi_refugio`. Unidades `unit_truck1` `unit_truck2` `unit_ambulance` `unit_ambulance2`
`unit_drone`. Red en Y: desvío **sur** (`wp_sur_01`/`wp_sur_02`, 162 m, el que se corta) y desvío
**norte** (`wp_nor_01`/`wp_nor_02`, 210 m, el alternativo).

## Las cinco llamadas

| # | Dirección | A quién | `role` | Teléfono | Qué se le dice |
| --- | --- | --- | --- | --- | --- |
| 1 | saliente | retén de bomberos | `fire_crew` | `FIRE_CREW_PHONE` | **lo que pide el solver**: qué camiones, a qué frente, por qué ruta y en cuánto; y lo que queda sin cubrir |
| 2 | saliente | dotación de la ambulancia | `ambulance` | `AMBULANCE_PHONE` | la misma petición, para el rescate. Si no hay ninguna libre, `ambulance_queued`: *cuándo* la habrá |
| 3 | saliente | responsable de Pueblo A | `evacuation` | `JUDGE_PHONE` | la orden de evacuación, **con los medios que ya han confirmado** |
| 4 | saliente | Pueblo B (vecino) | `neighbor_alert` | `NEIGHBOR_PHONE` | puede llegarle gente huyendo; ¿tienen sitio? |
| 5 | **entrante** | un vecino de Pueblo B | — | web call / 112 | lo que ve en el terreno; luego manda el pin por **Telegram** |

Las cuatro salientes van por **un solo hook** de HappyRobot (`HAPPYROBOT_HOOK_EVACUATION`): la rama la
decide el campo `role` del cuerpo, no el workflow (`voice/happyrobot.py:21-41`).

**El orden importa y es lo nuevo:** primero se piden los medios (1 y 2), las unidades **no se mueven**
hasta que su dotación cuelga, y solo entonces se dicta la orden al pueblo (3) con lo que va de verdad.
El agente no le promete ayuda a un pueblo hasta que sabe que la ayuda existe.

## Los dos ejes del reto → dónde vive cada uno

**Seis preguntas** (panel del dashboard que la contesta):
| Pregunta del jurado | Panel | Por qué ahí |
| --- | --- | --- |
| Qué información importa | `WhatChangedPanel` | filtra ruido (`isSignificant`), deja solo lo que cambia algo |
| Qué va primero | `PriorityQueue` | rationale de la Policy + orden por severidad |
| A quién se avisa y cuándo | `CallsPanel` | cuatro salientes con guion distinto (retén, ambulancia, Pueblo A, Pueblo B) + la entrante, por voz y por Telegram |
| Dónde van los recursos | `MapPanel` | flechas de asignación del solver |
| Qué se hace ahora | `ActionLog` | cada verbo (`goto`/`rescue`/`announce`) + latencia de respuesta |
| Cuándo tirar el plan | `DivergenceChart` | cruza 0,25 → banner REPLAN |

**Cuatro cosas** (capa de arquitectura que la demuestra):
- **Enterarse** → `belief` reconstruye `WorldState` inmutable de `world.*` + `world.fact.asserted`.
- **Priorizar** → `planner`→`Policy` (pesos), `solver`→`Plan` (asignación), con los medios que
  **quedan** (cuando truck2 se avería a 4:00).
- **Coordinar** → llamadas en las dos direcciones + `action.*` que mueven el mundo, no solo lo proponen.
- **Adaptarse** → replan en los tres injects (viento, corte, avería) + hecho crítico de la llamada.

**El reporte ciudadano entra por dos canales**, no solo por voz: la **llamada** (HappyRobot) y
**Telegram** (pin de ubicación + texto). Los dos son adaptadores de entrada que publican
`world.fact.asserted` por el bus; el Core es agnóstico a si el mundo por debajo es el simulador
(Minecraft) o datos reales de API. La voz aporta el *qué* (rápido, humano); Telegram aporta el
*dónde* **exacto y observado** (un pin GPS no se adivina). Se complementan en el clímax de la demo.

## Apertura (−0:20 → 0:00) — el reencuadre

Pantalla en **Minecraft**, cámara `escorzo` (45° oeste, `make cam PLAYER=<user>`). Valle en Y, dos
pueblos, parque de bomberos. Frase literal (backbone L569):

> "Lo que estáis viendo es Minecraft. No es la demo, es nuestro **simulador**. No puedes probar un
> agente de crisis en una crisis real, así que construimos un mundo donde el desastre pasa mil veces
> y podemos puntuar cada decisión. Esto es lo que **ve** el sistema."

Corte al **dashboard** (segundo monitor). Justifica el invariante 1 sin decirlo: *Minecraft es un
renderizador de un estado que vive en nuestro modelo*.

## Guion segundo-a-segundo

Captura: monitor A = dashboard 1920×1080 (6 paneles), monitor B = Minecraft cámara (cliente
espectador vía OBS). Se narra desde el dashboard y se corta a Minecraft en los beats **[MC]**.

| T | Minecraft [MC] | Dashboard (panel → qué) | Llamada | Narración / qué pregunta contesta |
| --- | --- | --- | --- | --- |
| **0:00** | Humo en `cell_13_11` (cresta O); POIs en verde `lime` | `WhatChangedPanel`: 1ª fila "incendio detectado" (`world.fire.detected`) | — | "Entra el primer aviso." → **Enterarse** |
| **0:05** | Fuego pinta celdas (netherrack/coal) | `WhatChangedPanel` filtra los `world.tick` (muted): de cien líneas, deja las 3 que cambian algo | — | **Qué información importa**: "Llegan cien mensajes y solo tres cambian algo; el sistema se queda con esos tres y tira el resto." |
| **0:15** | Fuego avanza O→E con viento 270° | `PriorityQueue`: rationale de la Policy ("civiles a sotavento primero") + barras `life_safety`/`immobile_first`; badges `hard_constraints` (`no_unit_into_burning_cell`) | — | **Qué va primero**: el LLM dice *qué importa*, no *quién va*. La Policy **no** tiene campo `assignments` (invariante 2). |
| **0:25** | — | `MapPanel`: 3 flechas de asignación (`plan.emitted`): truck1→frente Pueblo A, truck2→reserva, ambulance→hospital | — | **Dónde van los recursos**: "unidades y sitios que las piden; mandarlas a un lado es dejar el otro esperando." Solver (`scipy linear_sum_assignment`) en 0,04 s. |
| **0:02** | **[MC] los camiones NO se mueven.** Siguen en el parque con el motor parado | `CallsPanel`: tarjeta "RETÉN DE BOMBEROS" · en curso | **Saliente 1 real**: *"Les pedimos 2 camiones: camión 1 por la pista sur, un minuto; camión 2 por la pista sur, un minuto"* | **Pedir, no avisar**: la petición sale del solver (`requested_units`), no de un guion. Y **nadie sale hasta que digan «vamos»** — se ve en pantalla, que es lo que lo hace un beat. |
| **0:30** | **[MC]** El retén confirma → los dos camiones **arrancan a la vez**; `/tp` interpola a 5 Hz | `ActionLog`: `goto unit_truck1` · **"con el «vamos» de la dotación"** | Cuelga. El ack les dicta la ruta: *"Recibido, quedan movilizados. Salen por la pista sur."* | **Coordinar de verdad**: la llamada *decide*, no decora. Medido: `goto` en `seq` **posterior** al `call.ended`, no treinta segundos antes. |
| **0:35** | **[MC]** `poi_pueblo_a` → `orange` (evacuating) | `CallsPanel`: tarjeta "ORDEN DE EVACUACIÓN · PUEBLO A" · en curso | **Saliente 3 real**: la orden, y detrás *"Medios en camino: van 2 ambulancias, 2 camiones…"* | **A quién se avisa y cuándo**: "un vecino, un bombero y un responsable no necesitan lo mismo" — y aquí se ven los tres con guion distinto. Al pueblo no se le promete ayuda hasta saber que existe. |
| **0:35** | — | `CallsPanel`: tarjeta "AVISO AL VECINO · PUEBLO B" | **Saliente 4 real**: *"pueden llegarles del orden de 24 personas, ¿tienen sitio?"* | El cuarto interlocutor, y el que enseña que el mensaje se adapta: al que no arde no se le da una orden, se le avisa. |
| **1:00** | — | `CallsPanel`: tarjetas → *completed*; `WhatChangedPanel`: FACT "orden confirmada" | Cuelgan | Cierre limpio de las salientes. |
| **2:30** | **[MC]** El humo **gira**: viento 270→300, fuego vira al NE hacia Pueblo B | `DivergenceChart`: línea **cruza 0,25**, número rojo; banner REPLAN | — | **Cuándo tirar el plan** + **Adaptarse**: "cambia el viento y el plan de hace veinte minutos ya no vale. ¿Se da cuenta?" Sí: `plan.divergence` con `broken:[...]`. |
| **2:35** | **[MC]** Camiones frenan y **cambian destino** | `PriorityQueue` recarga (nueva Policy); `MapPanel` flechas apuntan distinto | — | Una **sola** llamada al modelo por replan (invariante 7), solo por bandera. |
| **3:30** | **[MC]** El desvío **sur** se pinta a rayas negro/amarillo, troncos cruzados (inject `road_cut`) | `CallsPanel`: tarjeta "AVISO DEL VECINO" (📞 voz) · en curso; transcripción SSE en vivo, línea a línea | **Entrante real**: vecino de Pueblo B llama al 112. HappyRobot SSE stream | **Enterarse** por canal humano. El mundo **no se mueve** hasta que cuelgue (solo el Core trabaja). |
| **3:35** | — | `CompletenessPanel`: 5 campos en gris (open); barra de presupuesto | 1er tick de Jev sobre parcial: `urgency=critical` → presupuesto **8 s** | Jev (`jev-1.13`) cada 5 s manda **todas** las preguntas a la vez, devuelve vector de completitud sin texto. |
| **3:50** | — | `CompletenessPanel`: `road_blocked` → **sólido** (observed ≥0,85); `people_immobile` y **`location_hint` siguen gris** | El vecino **no sabe ubicarse bien**: "estoy cerca de unas casas, al final de la pista, no sé el nombre". Jev fija `road_blocked` pero **no** `location_hint`. Presupuesto obliga a **una** repregunta: "¿hay alguien que no pueda moverse?" (*signal* `kind: followup`, una a la vez) | **Priorizar la escucha** + el límite del canal de voz: se pregunta lo que más reduce incertidumbre, pero la **ubicación precisa** no la puede dar. El hueco queda visible, no inventado. |
| **3:58** | **[MC]** La ambulancia sigue en el hospital, **quieta** | `CallsPanel`: tarjeta "DOTACIÓN DE AMBULANCIA"; `ActionLog` sin `goto` para ella todavía | **Saliente 2 real**: el rescate nace del `immobile` que acaba de entrar por la llamada del vecino → *"les pedimos la ambulancia en Pueblo A, hay 2 personas que no pueden moverse"* | Mismo patrón que el retén, ahora **disparado por la llamada entrante**: un ciudadano informa, el solver decide, se pide el medio, y la ambulancia no sale hasta que la dotación lo confirma. |
| **4:00** | **[MC]** `unit_truck2` se para (inject `unit_failure`); marcador → `unavailable` | `CompletenessPanel`: `people_immobile` se rellena **gris cursiva** (`assumed_default`); `WhatChangedPanel`: FACT gris cursiva | Presupuesto agotado → `gapfill` (LLM) rellena `people_immobile` como `assumed_default`, **no** observado | **Invariante 8** en pantalla: "un hecho asumido nunca se disfraza de observado; va en gris y cursiva. Si ese número está mal, el plan falla y volvemos a replanificar." |
| **4:10** | **[MC]** (truck2 caído) → truck1 y ambulance **giran al desvío norte** en <3 s | `DivergenceChart`: pico rojo + marca de replan; banner "pista sur cortada, confirmado por llamada entrante"; `PriorityQueue` reparte con los medios que **quedan** | Cuelga → `road_blocked` asertado como hard fact | **Adaptarse**: el `road_blocked` **observado** funda restricción dura (solo `observed` puede, inv. 8). `route_feasible` rechaza la ruta sur → replan. **SLA <1 s** de colgar a girar (backbone L122). **Priorizar con los medios que quedan, no los que harían falta.** |
| **4:25** | **[MC]** Marcador del ciudadano cae en el mapa junto a Pueblo B | `MapPanel`: **pin del ciudadano** en su `(x,z)` exacto; `CallsPanel`: tarjeta "AVISO DEL VECINO" con badge **✈ Telegram**; `CompletenessPanel`: `location_hint` pasa de gris a **sólido** (observed) | **Telegram entrante**: ya colgado, el vecino **comparte su ubicación** por el bot. Pin GPS → `citizen.location` + `world.fact.asserted` anclado a `poi_pueblo_b` (`kind: observed`) | **La voz da el *qué*, Telegram da el *dónde* exacto.** Cierra el hueco de `location_hint` que la llamada dejó abierto **sin adivinar**: un pin no se resuelve por *fuzzy match*. Inv. 8: observado, funda/ancla la restricción dura. |
| **5:00** | **[MC]** truck1 llega a Pueblo A; `rescue`: villagers `/tp` al `poi_refugio` con `glowing`; `poi_pueblo_b` → `safe` por ruta norte | `ActionLog`: `rescue shelter=poi_refugio` "hecha"; `DivergenceChart` baja | — | **Qué se hace ahora** cerrado. Meta cumplida. |

## Cierre de la parte Minecraft — de simulación a producción (5:00 → 5:45)

Dos golpes finales, ambos con el run recién terminado en pantalla:

1. **El mismo sistema agéntico va con datos reales.** Frase: *"Esto que habéis visto decidir sobre
   Minecraft es el mismo Core, sin tocar una línea de la lógica. Minecraft es un adaptador; se cambia
   el adaptador y el Core come de sensores, APIs y llamadas reales"* — invariante 1, backbone L51:
   *"El mismo Core funciona con datos reales cambiando el adaptador."* Se enseña que `sim`/RCON es
   solo el borde: `belief`, `planner`, `solver`, `verifiers` no saben si el `WorldState` viene de
   Minecraft o de producción, porque todos hablan por eventos tipados (`contracts`).
2. **Plan de actuación en mapa 2D.** Corte a `MapPanel` a pantalla completa (el mismo panel que ya
   funciona con `--no-minecraft`): geografía real, unidades, flechas de asignación, cortes de vía,
   halos de amenaza. Es la vista "de sala de control": el mismo plan sin depender del render de
   Minecraft. Justifica que la demo no es un juguete — el plano operativo se sostiene solo.

## Automejora entre runs — el bonus track (5:45 → 6:00)

Criterio explícito de puntos extra (backbone L237–245) y "casi ningún equipo lo va a tener
funcionando". **Ojo con el motor**: la percepción en llamada usa **Jev** (TypeSafe); la automejora
es un **bucle estilo Hermes Agent** (`packages/core/memory.py`) que usa **fenic** (Typedef) solo en
la cosecha batch, fuera de la ruta caliente.

Mecanismo (cuatro piezas, `core/memory.py`):
1. **`harvest`** — al terminar cada run, un **job batch** carga todos los journals anteriores
   (`runs/*.jsonl`) en **fenic** y, con `semantic.extract` sobre `LearnedRule`, saca patrones tipo
   *"cuando el viento gira más de 60°, evacuar antes de reasignar extinción"*, agregados por
   `(trigger, body)`. Solo persisten las de **soporte en ≥2 runs**. Sin presión de latencia — terreno
   natural de fenic.
2. **Una regla, un fichero.** Cada una se escribe en `memory/rules/<slug>.md` con frontmatter
   (`trigger`, `support`, `confidence`, `lineage`). Sustituye al blob único `memory/policy_rules.md`.
3. **`select` — gating determinista, nunca el LLM.** El planner solo ve las reglas cuyo `trigger`
   casa con el `WorldState` actual (*progressive disclosure*), sobre un mini-DSL de comparaciones sin
   `eval`. Prompt corto, y el `lineage` (`run:seq`) deja ver *qué* regla influyó en *qué* decisión.
4. **`apply_patch` — mejora por patch.** Un run posterior confirma o contradice la regla y mueve su
   `confidence` (EWMA hacia 1.0 si acabó sin civiles expuestos), sin reescribir el cuerpo.

Qué se muestra: **run 1 contra run 12 en pantalla partida, con la puntuación de cada uno** (backbone
L241). El run 12 evita antes el error que el run 1 cometió, y la regla en `memory/rules/` (con su
`lineage`) explica por qué. Cierra el eje **Adaptarse** en su forma fuerte: no solo replanifica dentro
de un run, mejora entre runs.

> Dónde vive cada motor (para no confundir a nadie en el pitch):
> | Uso | Estado | Motor |
> | --- | --- | --- |
> | Percepción en llamada (hot path) | activo | **Jev** (TypeSafe) — no es un LLM, reemplazó a fenic |
> | Razonamiento / `Policy` (nivel 2) | activo | **LLM aparte** (OpenAI GPT-5.6 Luna), 1 llamada por replan |
> | Fallback sin clave / `VELA_NO_JEV=true` | legacy, plan B | fenic con `Literal` |
> | Automejora entre runs (bonus) | activo | bucle Hermes; **fenic** `semantic.extract` → `LearnedRule` solo en `harvest` |

## Beats que hay que clavar (y por qué ganan puntos)

1. **0:05 filtro de ruido** — señalar que `WhatChangedPanel` esconde los `world.tick`. Respuesta
   literal a "cien mensajes, tres importan".
2. **0:15 Policy sin `assignments`** — abrir el JSON de la Policy en un lateral: cero campo de
   asignación. Responde la **responsabilidad/auditoría** del jurado.
3. **2:30 divergencia cruza 0,25** — el gráfico cruzando el umbral **justo antes** del banner es la
   prueba visual de "cuándo tirar el plan".
4. **4:00 gris cursiva** — único beat donde un hecho **asumido** se ve distinto de uno **observado**.
   Diferenciador técnico; narrarlo despacio.
5. **4:10 <1 s de colgar a girar** — cronometrar en voz alta. La promesa de "adaptarse a mitad".
6. **5:45 run 1 vs run 12** — el bonus. Pantalla partida con las dos puntuaciones.

## Red de seguridad (si algo cae en vivo)

Guion idéntico, se degrada por capas (backbone L574–581) y se **anota** en el header (badge), nunca
`except: pass`:
- Jev cae → `--no-jev` (**fenic con `Literal`**, sin loop en llamada; el beat 3:50 pierde la
  repregunta, el 4:00 sigue mostrando `assumed_default`).
- HappyRobot cae → `--mock-calls` (SSE replay de `fixtures/transcripts/`, badge "llamadas simuladas";
  el endpoint `/webhooks/happyrobot/fact` sigue siendo real, budget+completeness intactos). **Las
  cinco llamadas tienen guion enlatado** (`evacuation_order`, `neighbor_alert`, `fire_crew_dispatch`,
  `ambulance_dispatch`, `status_check`): un intent sin guion dejaría a sus unidades retenidas hasta
  agotar el plazo, así que ahora una llamada que no se puede simular se cierra igual con
  `outcome="failed"` y las unidades salen.
- **Nadie contesta al teléfono** → a los 45 s de sim (`DISPATCH_HOLD_S`) las unidades salen igual y el
  `ActionLog` lo pinta: **"salió SIN confirmar"** (`dispatch_confirmed=false`). La demo nunca se queda
  con los camiones congelados, y la degradación se ve en vez de esconderse.
- Minecraft cae → `--no-minecraft` (mapa 2D del `MapPanel` — que además ya es el cierre de sala de
  control, así que degradar no se nota).
- Telegram cae / sin token → `VELA_NO_TELEGRAM=true` (canal ausente, badge lo anota en el header); el
  guion no cambia, la ubicación se queda difusa como en el beat 3:50, igual que sin el canal.
- Portátil cae → vídeo pregrabado + QR en el repo.

## Verificación (ensayo antes de la demo)

1. `make server` → Paper 1.21, RCON 25575.
2. `make world SCENARIO=wildfire_ridge` → geografía idempotente (POIs, unidades, 20 villagers).
3. `make cam PLAYER=<usuario> SCENARIO=wildfire_ridge` → teclas **1-6** =
   `valle`/`escorzo`/`pueblo`/`frente`/`parque`/`hospital`. Las dos últimas son los sitios donde una
   unidad **espera al teléfono**, que es el beat nuevo y no se podía enseñar con las cuatro de antes.

   **Quién conduce la cámara.** `make cam` y `make director` mandan los dos `/tp` al mismo jugador y
   no se coordinan: un evento urgente del director roba el plano en medio segundo, y su
   `gamemode spectator` de arranque rompe el `--follow`. Elige uno:
   - **`make director PLAYER=<u>`** — conduce él solo, siguiendo el journal. Cubre los beats de
     despacho: se queda en el parque los treinta segundos de la llamada al retén y corta en seco
     cuando cuelgan y arrancan los camiones.
   - **`make cam` + `make narra PLAYER=<u>`** — conduces tú con las teclas y el director te va
     diciendo por consola qué deberías estar mirando (`[cámara sugerida: …]`) sin tocar nada.

   Señales de cámara por beat, para el que conduce a mano:

   | Beat | Tecla | Por qué |
   | --- | --- | --- |
   | −0:20 apertura | **2** `escorzo` | el reencuadre: «esto es el simulador» |
   | 0:02 se pide al retén | **5** `parque` | los dos camiones quietos con el teléfono sonando |
   | 0:30 confirman | **5** y aguanta | arrancan los dos a la vez: el pago de la espera |
   | 0:35 orden a Pueblo A | **3** `pueblo` | escala humana, los 24 civiles |
   | 2:30 giro de viento | **1** `valle` | las dos pistas a la vez, o el replan no se lee |
   | 3:58 se pide la ambulancia | **6** `hospital` | la ambulancia que no arranca |
   | 4:10 colgar → giro | **1** `valle` | el SLA de <1 s, y hay que ver las dos pistas |
   | 5:00 rescate | **3** `pueblo` | cierre |
4. `uv run python -m voice.jev --gate` → 10 transcripciones ES con clave real; **si falla, ir a
   `--no-jev`** antes de empezar.
5. `make demo` (con `HAPPYROBOT_API_KEY`, `HUMALIKE_API_KEY`, `TYPESAFE_API_KEY` en `.env`) → correr
   los 6 min una vez y cronometrar los injects (150/210/240 s).
6. Verificar en el dashboard contra `runs/<run_id>.jsonl` que aparezcan ≥1 vez: `plan.divergence`,
   `plan.replan.started`, `world.fact.asserted` con `kind:assumed_default`, `call.completeness` con un
   campo `observed` y otro `assumed_default`.
6b. **El invariante del despacho**, que es lo que hay que comprobar de un vistazo: el `seq` del
   `action.requested` de un camión tiene que ser **mayor** que el del `call.ended` del retén.

   ```
   python3 - runs/<run_id>.jsonl <<'EOF'
   import json,sys
   ev=[json.loads(l) for l in open(sys.argv[1])]
   fin=[e['seq'] for e in ev if e['type']=='call.ended']
   go=[(e['seq'],e['payload']['args']['unit_id'],e['payload'].get('dispatch_confirmed'))
       for e in ev if e['type']=='action.requested' and e['payload']['verb']=='goto']
   print('primer call.ended:', min(fin) if fin else None)
   for s,u,c in go[:6]: print(f'  goto {u} seq={s} confirmado={c}')
   EOF
   ```

   Y en los cuerpos: la llamada al retén **no** puede decir "todavía no hay ninguna unidad asignada"
   en `unit_eta`, tiene que traer `requested_units` y `coverage`, y la de evacuación
   `committed_resources`. Medido el 19/09 en `runs/run_726b7bab939a.jsonl`: `call.req fire_crew` en
   t=1 → `call.ended` seq 98 → `goto unit_truck1` y `goto unit_truck2` seq 99 y 100, los dos
   `CONFIRMADO` → `call.req evacuation` seq 101.
7. Automejora: `runs/` con ≥2 journals buenos → correr `harvest` de `core/memory.py`, comprobar que
   `memory/rules/` se puebla (un fichero por regla, con `trigger`/`support`/`confidence`/`lineage`) y
   que `select` inyecta al planner solo las reglas cuyo `trigger` casa con el estado. Correr los 12
   runs (backbone L537) para tener el run 1 vs run 12.
8. Ensayar el corte MC↔dashboard en los beats **[MC]** (0:02, 0:30, 0:35, 2:30, 3:30, 3:58, 4:00,
   4:10, 4:25, 5:00) con la tabla de teclas del paso 3
   y el cierre a `MapPanel` full-screen.
9. Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_SECRET_TOKEN` en `.env`, `setWebhook` sobre el mismo túnel
   de las llamadas): desde el móvil, colgar la llamada y **compartir ubicación** + un texto; comprobar
   en `runs/<run_id>.jsonl` que aparezcan `citizen.location`, `call.started` con `channel:telegram` y
   `world.fact.asserted` anclado al `poi_pueblo_b` con `kind:observed`; y en el dashboard, el pin en
   `MapPanel` y `location_hint` sólido en `CompletenessPanel`.
