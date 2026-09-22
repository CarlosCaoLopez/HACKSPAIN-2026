# Guion DEFINITIVO de las llamadas — qué decimos nosotros para provocar la demo

Este es el guion operativo **canónico**: lo que dice cada uno de nosotros al teléfono para que la
simulación produzca exactamente los beats de `use_cases.md`, y para que cada línea conteste una de
las preguntas/capacidades del reto. Sustituye a `guiones.md` (escritorio) y a
`docs/guiones_personas.md` cuando se contradigan.

> **Verificado contra el código** (`core/calls.py`, `voice/__init__.py`, `core/tasks.py`,
> `sim/runner.py`, `scenarios/wildfire_ridge.yaml`). Cada disparador nombra el campo real que la
> herramienta `reportar_situacion` (salientes) o Jev (entrante) fija en el estado.

## Dos correcciones respecto a use_cases.md (léelas antes de ensayar)

`use_cases.md` tiene dos incoherencias con la sim implementada. Las resolvemos así, y el guion de
abajo YA las incorpora:

1. **El rescate va a Pueblo B, no a "Pueblo A".** Un inmóvil solo funda un rescate si se **ancla a
   un POI** (`voice/__init__.py:123-133`: sin POI resuelto, «hecho sin ubicar, no entra al estado»).
   El ciudadano de la entrante es de **Pueblo B**, así que el inmóvil ancla a `poi_pueblo_b` → el
   rescate y la ambulancia van a **Pueblo B**. Y encaja solo: con la pista sur cortada, la ambulancia
   entra a Pueblo B **por el desvío norte** — que es justo el beat 4:10 de use_cases. Coherente:
   Pueblo A se evacúa al principio (viento 270), y cuando el viento gira a 300 el que arde es B.

2. **La ambulancia solo puede rescatar; el camión NO.** `unit_truck*` tiene `capabilities:
   [extinguish]` y `capacity: 0` → el solver le pone coste ∞ a cualquier tarea de rescate/evacuación
   (`solver.py:515`). Solo la ambulancia (`transport`) mueve personas. **Nunca** digáis al ciudadano
   «va el camión 2 a por vosotros»: quien va a por los inmóviles es la ambulancia.

## Reglas de oro (lo que NO hay que decir)

- **En la llamada a Pueblo A (evacuación) NO deis un número de inmóviles.** El agente lo pregunta
  (`expect=["confirmation","headcount","immobile"]`), pero si respondéis un número se fija
  `poi:poi_pueblo_a:immobile` y el rescate nace **a las 0:40**, no a las 3:58. Contestad «aún lo
  estamos comprobando». El rescate tiene que nacer de la **entrante**.
- **En la llamada a Pueblo A NO mencionéis la vía cortada.** El corte lo funda el ciudadano en la
  entrante (observado → restricción dura → replan cronometrado). Si lo sembráis antes, el replan al
  desvío norte salta a destiempo y se pierde el SLA de <1 s del beat 4:10.
- **Camión = apagar. Ambulancia = personas.** (ver corrección 2).

## Línea de tiempo (injects automáticos en gris)

| t | Evento | Quién habla |
| --- | --- | --- |
| 0:05 | Saliente 1 · retén de bomberos | Carlos |
| 0:35 | Saliente 3 · responsable Pueblo A | — |
| 0:35 | Saliente 4 · vecino Pueblo B | — |
| *2:30* | *inject `wind_shift` 270→300: el fuego vira a Pueblo B* | *(auto)* |
| 3:30 | **Entrante** · ciudadano de Pueblo B al 112 | nosotros hacemos la llamada |
| *3:30* | *inject `road_cut`: pista sur (`wp_sur_01-wp_sur_02`) cortada* | *(auto)* |
| 3:58 | Saliente 2 · dotación de la ambulancia | — |
| *4:00* | *inject `unit_failure`: `unit_truck2` avería de bomba* | *(auto)* |
| *4:10* | *truck1 + ambulancia giran al **desvío norte*** | *(auto)* |
| 4:25 | **Telegram** · el ciudadano comparte ubicación (ya colgado) | nosotros, desde el móvil |

---

## 1 · Retén de bomberos — `role: fire_crew` · FIRE_CREW_PHONE · ~0:05

**El agente dirá** (aprox.): *«Incendio forestal declarado a ~110 m de El molino viejo. Les pedimos
2 camiones: camión 1 por la pista sur, camión 2 por la pista sur, ~1 min cada uno. ¿Pueden salir ya
los dos?»*

| Decimos | Dispara (campo) | Provoca en la sim |
| --- | --- | --- |
| **«Sí, salimos ya los dos.»** | `confirmed_order=true` → `confirmation` | Suelta truck1 y truck2 con `dispatch_confirmed=true`. Hasta decirlo, **no se mueve nadie** (los `goto` van en `seq` posterior al `call.ended`). |
| Cuando pregunte por más dotaciones: **«Podemos pedir una autobomba al parque comarcal, veinte minutos.»** | `extra_units` (nº) + plazo en `notes` | Alimenta `coverage`: el sistema sabe qué frente queda sin cubrir y con qué refuerzo tardío. |

**No digáis** que no pueden salir (salvo que queráis enseñar el degradado: publicaría
`unit:<id>:available=false` y el solver reparte sin ese camión).

**Reto:** (3) a quién se avisa / (5) acción concreta — no se le «avisa», se le **pide** algo
ejecutable salido del solver (`requested_units`). (4) dónde van los recursos. (C) coordinar de
verdad (la llamada decide, no decora). (B) priorizar con lo que queda (el déficit de la autobomba).

---

## 2 · Dotación de la ambulancia — `role: ambulance` · AMBULANCE_PHONE · ~3:58

> Esta llamada **la lanza el sistema solo** cuando el rescate de Pueblo B ha nacido de la entrante
> (paso 5). El agente dirá el POI y el número que el ciudadano reportó — o sea **Pueblo B, 2
> inmóviles**, por el desvío norte.

**El agente dirá** (aprox.): *«Les piden una ambulancia en Pueblo B: dos personas que no pueden
moverse. La pista sur está cortada, se entra por el desvío norte. ¿Pueden ir ya?»*

| Decimos | Dispara (campo) | Provoca en la sim |
| --- | --- | --- |
| **«Sí, salimos del hospital ahora mismo.»** | `confirmed_order=true` → `confirmation` | Suelta la ambulancia (mismo patrón que el retén: no arranca hasta este «sí»). |
| Si pregunta por más unidades: **«Podemos llevar cuatro por viaje; si son más, mandamos la segunda.»** | `extra_units` | Cierra el dimensionado; con >4 saltaría `ambulance_queued`. |

**Reto:** (2) qué va primero — el rescate se prioriza **en el momento** en que el inmóvil entra por
la entrante, no estaba en el plan inicial. (D) adaptarse — saliente **disparada por la entrante**.
(C) coordinar — no sale hasta el «sí». (4)/(5) recursos y acción con números.

---

## 3 · Responsable de Pueblo A — `role: evacuation` · JUDGE_PHONE · ~0:35

**El agente dirá** (aprox.): *«Orden de evacuar Pueblo A por la pista sur en los próximos ~6 min
hacia el refugio municipal. Medios en camino: 2 camiones ya confirmados…»*

| Decimos | Dispara (campo) | Provoca en la sim |
| --- | --- | --- |
| **«Entendido, la aceptamos.»** | `confirmed_order=true` → `confirmation` | `poi_pueblo_a` → naranja (evacuating). |
| **«Veinticuatro personas.»** | `headcount=24` | Censo al modelo. |
| Si pregunta por inmóviles: **«Aún lo estamos comprobando, no lo sé todavía.»** | *(deja `immobile` sin fijar)* | ⚠️ **Crítico**: mantiene `poi_pueblo_a:immobile` vacío → el rescate NO nace aquí, nace en la entrante a las 3:58 (beat de use_cases). |

**No mencionéis la vía cortada** (regla de oro).

**Reto:** (3) a quién se avisa y cuándo — es una **orden con medios reales detrás** (por eso va
después del retén). (5) acción concreta y responsable («salgan por la ruta que les demos, no antes»).
(A) enterarse — aporta el censo (24).

---

## 4 · Vecino de Pueblo B — `role: neighbor_alert` · NEIGHBOR_PHONE · ~0:35

> A las 0:35 a Pueblo B **aún no le arde nada** (el viento gira a las 2:30). Es el contraste: al que
> no está en peligro se le **avisa y se le pregunta acogida**, no se le da una orden.

**El agente dirá** (aprox.): *«No tienen el fuego encima, pero conviene que lo sepan: se está
evacuando Pueblo A y pueden llegarles personas huyendo. ¿Tienen sitio para acogerlas?»*

| Decimos | Dispara (campo) | Provoca en la sim |
| --- | --- | --- |
| **«Sí, el polideportivo está abierto, caben de sobra.»** | `capacity_available=true` → `shelter_ready` | Entra al modelo como capacidad de acogida. |
| **«Mantas y agua nos vendrían bien, pero podemos empezar sin eso.»** | recursos en `notes` | Recursos que faltan, sin bloquear. |

**Reto:** (3) a quién se avisa — el caso fuerte: cuatro interlocutores, cuatro registros; aviso ≠
orden. (A) enterarse — convierte la saliente en captura de estado. (5) acción concreta = **no actuar
todavía** y decirlo explícito.

---

## 5 · Ciudadano de Pueblo B al 112 — **entrante** · web call / 112 · 3:30 → 4:25

**El clímax. La hacemos nosotros**, hablando como alguien asustado (repite, salta de un dato a otro,
desordenado). El agente responde y cada 5 s Jev tritura lo dicho. Tres cosas, **en este orden**:

| Momento | Decimos | Dispara | Provoca en la sim |
| --- | --- | --- | --- |
| ~3:40 | **«La pista del sur está cortada, hay un árbol caído.»** | Jev fija `road_blocked` (observado ≥0,85) → `road:wp_sur_01-wp_sur_02` | Restricción dura → `route_feasible` rechaza la sur → **replan al desvío norte**. Es el **SLA <1 s** de colgar a girar. |
| ~3:45 | **«Estoy en Pueblo B, junto al molino viejo.»** | Jev resuelve el POI → `poi_pueblo_b` (ancla los hechos siguientes) | Sin esto el inmóvil «no entra al estado». Da la referencia **gruesa**; el punto exacto queda para Telegram. |
| cuando repregunte | **«Hay dos personas que no pueden moverse.»** | `people_immobile=2` → `poi:poi_pueblo_b:immobile=2` (observado) | **Nace `task_rescue_poi_pueblo_b`** (crítica) → dispara la saliente 2 (ambulancia) a las 3:58. |
| ~3:50 | **«No sé la calle exacta, estoy al final de una pista entre casas.»** | mantiene la **precisión** de `location_hint` incompleta | El hueco fino que **Telegram** cierra; se ve gris en `CompletenessPanel`. |
| tras colgar (4:25) | **compartir la ubicación por Telegram** | `citizen.location` → `world.fact.asserted` anclado a `poi_pueblo_b` (`kind: observed`) | Pin exacto en `MapPanel`; `location_hint` pasa a **sólido**. |

> **Por qué esta versión y no «no sé el nombre» a secas** (como decía use_cases): si no damos un POI
> resoluble por voz, el inmóvil se descarta y el rescate no nace hasta el pin de Telegram (4:25), y
> la ambulancia se iría a las 4:25, no a las 3:58. Damos la referencia **gruesa** («Pueblo B, junto
> al molino») para fundar el rescate a tiempo, y dejamos el **punto exacto** para el pin — así Jev da
> el *qué* y Telegram el *dónde exacto*, sin adivinar nada.

**Decid lo del árbol caído antes de colgar, no al final:** el replan se cronometra desde que colgáis.

**Reto:** (1) qué información importa — Jev retiene `road_blocked` e `immobile` de todo lo que suelta
el vecino, y el presupuesto obliga a **una** repregunta. (6) cuándo tirar el plan — el
`road_blocked` observado funda la restricción dura → replan. (A) enterarse por canal humano / (D)
adaptarse — la entrada más caótica mueve unidades reales. **Invariante 8** — la voz da el *qué*; el
*dónde exacto* lo cierra el pin de Telegram (observado), no un fuzzy match.

---

## Cobertura: persona × criterios del reto

| Persona | 1 info | 2 prio | 3 avisar | 4 recursos | 5 acción | 6 tirar plan | A entera | B prioriza | C coordina | D adapta |
| --- | :--: | :--: | :--: | :--: | :--: | :--: | :--: | :--: | :--: | :--: |
| 1 Retén bomberos | | | ● | ● | ● | | | ● | ● | |
| 2 Dotación ambulancia | | ● | | ● | ● | | | | ● | ● |
| 3 Responsable Pueblo A | | | ● | | ● | | ● | | | |
| 4 Vecino Pueblo B | | | ● | ● | ● | | ● | | | |
| 5 Ciudadano 112 (+Telegram) | ● | | | | | ● | ● | | | ● |

Entre las cinco se tocan las seis preguntas y las cuatro capacidades al menos una vez.

> En vivo HappyRobot improvisa sobre esta **intención**, no lee las líneas al pie: lo fijo es el
> propósito de cada turno y el campo que dispara. Si una llamada no se puede simular, se cierra con
> `outcome="failed"` y las unidades salen igual — la demo nunca se queda con las unidades congeladas.
