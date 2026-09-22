# Guiones de persona — las cinco llamadas de la demo `vela`

Compañero de `use_cases.md`. Aquel da el **qué** segundo-a-segundo; este da la **voz** de cada
personaje al otro lado del teléfono: quién es, con qué tono, qué dice turno a turno y **qué punto
del jurado justifica cada línea**. Un actor debe poder leer una ficha y doblar la llamada sin más
contexto.

Las líneas base salen de los guiones enlatados (`fixtures/transcripts/*.txt`), los que corren en
`--mock-calls`. **En vivo HappyRobot improvisa sobre esa misma intención**, no lee estas líneas al
pie; lo fijo es el propósito de cada turno y el criterio que dispara.

## Los dos ejes del jurado (fuente: https://hackspain2026.happyrobot.ai/)

**6 preguntas:** (1) qué información importa · (2) qué va primero · (3) a quién se avisa y cuándo ·
(4) dónde van los recursos · (5) siguiente acción concreta y quién la hace · (6) cuándo tirar el
plan.

**4 capacidades:** (A) enterarse · (B) priorizar con los medios que quedan · (C) coordinar de
verdad (acciones reales) · (D) adaptarse a mitad.

Convención de las fichas: `[VELA]` = centro de coordinación (agente); `[PERSONA]` = el personaje.
Las cuatro salientes entran por un solo hook y la rama la decide el campo `role`
(`use_cases.md:33-34`).

---

## 1 · Retén de bomberos — `role: fire_crew`

**Saliente 1 · beat 0:02–0:30 · base: `fixtures/transcripts/fire_crew_dispatch.txt`**

**Quién es.** Jefe de retén en el parque de bomberos. Dos camiones con dotación completa, motor
parado, esperando el teléfono. Es el primero al que se llama.

**Tono / dirección de actor.** Seco, operativo, cero floritura. Contesta en jerga de radio
("dígame", "cambio"). No pregunta por qué: pregunta ruta y tiempo. Transmite que **ya está listo**;
el único freno es la orden.

**Guion turno a turno.**

- `[VELA]` Buenas, llamo del centro de coordinación por el incendio de la cresta oeste.
- `[PERSONA]` Aquí el retén, dígame.
- `[VELA]` Necesitamos **dos camiones** en el frente a 110 m del molino viejo, **por la pista
  sur**. ¿Pueden salir ya los dos?
- `[PERSONA]` Sí, los dos están en el parque con la dotación completa. **Salimos ahora.** ← el
  "vamos" que libera el despacho.
- `[VELA]` Estamos atacando dos focos y hay treinta activos: con dos no llegamos. ¿Pueden
  movilizar más?
- `[PERSONA]` Podemos pedir una autobomba al parque comarcal, pero **tarda veinte minutos** en
  llegar al valle.
- `[VELA]` Anotado, una más en veinte minutos. Les mandamos la ruta por radio.
- `[PERSONA]` Recibido, vamos saliendo. Cambio.

**Qué se ve en pantalla.** `CallsPanel` tarjeta "RETÉN DE BOMBEROS · en curso". En Minecraft
(cámara **5 parque**) **los camiones NO se mueven**: siguen con el motor parado mientras suena el
teléfono. Al colgar, `ActionLog`: `goto unit_truck1` **"con el «vamos» de la dotación"**, y los dos
arrancan a la vez.

**Qué criterios satisface y por qué.**

- **(3) A quién se avisa y cuándo / (5) acción concreta.** No se le "avisa", se le **pide** algo
  ejecutable: qué unidades, a qué frente, por qué ruta, en cuánto. La petición sale del solver
  (`requested_units`), no de un guion.
- **(4) Dónde van los recursos.** Dos camiones a un frente concreto = una asignación del solver
  hecha voz.
- **(C) Coordinar de verdad.** La llamada **decide**, no decora: el `goto` va en `seq`
  **posterior** al `call.ended`. Nadie sale hasta el "salimos ahora" — y eso se ve, que es lo que
  lo hace un beat.
- **(B) Priorizar con lo que queda.** "Con dos no llegamos, autobomba en 20 min" alimenta el
  `coverage`: el sistema sabe qué queda sin cubrir y con qué refuerzo tardío.

---

## 2 · Dotación de la ambulancia — `role: ambulance`

**Saliente 2 · beat 3:58 · base: `fixtures/transcripts/ambulance_dispatch.txt`**

**Quién es.** Conductor/sanitario de la ambulancia, en el hospital. No sale por defecto: **su
llamada nace de un hecho** — las personas inmóviles que un ciudadano acaba de reportar por la
entrante.

**Tono / dirección de actor.** Rápido, clínico. Pregunta lo justo para dimensionar el traslado
(cuántos, si caben en un viaje). Sin dramatismo.

**Guion turno a turno.**

- `[VELA]` Centro de coordinación. Les piden una ambulancia en Pueblo A por el incendio forestal.
- `[PERSONA]` Dígame qué tenemos.
- `[VELA]` **Dos personas que no pueden moverse solas.** No hay carretera cortada: se entra por la
  pista sur. ¿Pueden ir ya?
- `[PERSONA]` Sí, **salimos del hospital ahora mismo.**
- `[VELA]` ¿Cuántas personas pueden llevar en un viaje?
- `[PERSONA]` Cuatro. Si son más de cuatro habría que mandar la segunda unidad. ← dispara
  `ambulance_queued` si hiciera falta.
- `[VELA]` Anotado, son dos. Les mandamos la ruta.
- `[PERSONA]` Recibido, vamos para allá.

**Qué se ve en pantalla.** `CallsPanel` tarjeta "DOTACIÓN DE AMBULANCIA". Antes de colgar, en
Minecraft (cámara **6 hospital**) la ambulancia sigue **quieta**; `ActionLog` sin `goto` para ella
todavía. Al confirmar, arranca.

**Qué criterios satisface y por qué.**

- **(2) Qué va primero.** El rescate se prioriza **en el momento** en que el `immobile` entra por
  la llamada del vecino — no estaba en el plan inicial.
- **(4)/(5) Recursos y acción concreta.** Ambulancia a Pueblo A, dos inmóviles, capacidad 4; si
  fueran más, segunda unidad. Quién hace qué, con números.
- **(D) Adaptarse.** Es una saliente **disparada por la entrante**: ciudadano informa → solver
  decide → se pide el medio. El mismo patrón del retén, pero nacido a mitad de operación.
- **(C) Coordinar de verdad.** Igual que el retén: no sale hasta que la dotación cuelga
  confirmando.

---

## 3 · Responsable de Pueblo A — `role: evacuation`

**Saliente 3 · beat 0:35 · base: `fixtures/transcripts/evacuation_order.txt`**

**Quién es.** Vecino de referencia / alcalde pedáneo de Pueblo A (24 civiles, 3 inmóviles). El
pueblo que **sí** está amenazado. Recibe una **orden**, no una consulta.

**Tono / dirección de actor.** Preocupado pero cooperativo. Ya ve el humo. Aporta el dato que
**rompe el plan** sin saber que lo rompe ("el puente sur lo cortaron ayer"). Necesita que le digan
exactamente qué hacer y cuándo.

**Guion turno a turno.**

- `[VELA]` Buenas tardes, del centro de coordinación por el incendio de la cresta oeste.
- `[PERSONA]` Sí, dígame, estamos viendo el humo desde aquí.
- `[VELA]` Se ha ordenado la **evacuación preventiva** de Pueblo A. Salgan por la carretera sur,
  hacia el refugio municipal.
- `[PERSONA]` ¿Por el sur? Es que **el puente del sur lo cortaron ayer por obras.** ← siembra el
  replan.
- `[VELA]` Entendido, tomo nota: la salida sur no es practicable. ¿Cuánta gente hay ahora?
- `[PERSONA]` Unas veinte. Y en la casa del molino hay **tres mayores que no pueden andar solos.**
- `[VELA]` De acuerdo: tres inmóviles en el molino. Mandamos una ambulancia y les avisamos de la
  ruta alternativa. **No salgan hasta que les llamemos.**
- `[PERSONA]` Entendido, esperamos aquí. Gracias.

**Qué se ve en pantalla.** `CallsPanel` tarjeta "ORDEN DE EVACUACIÓN · PUEBLO A · en curso". En
Minecraft (cámara **3 pueblo**, escala humana, los 24 civiles) `poi_pueblo_a` → **naranja**
(evacuating). Detrás de la orden: *"Medios en camino: 2 ambulancias, 2 camiones…"* — los que **ya
han confirmado**.

**Qué criterios satisface y por qué.**

- **(3) A quién se avisa y cuándo.** El mensaje es una **orden con medios reales detrás**. Al
  pueblo no se le promete ayuda hasta saber que existe (por eso va después de las salientes 1 y 2).
- **(5) Acción concreta y quién.** "Salgan por la ruta alternativa", "no salgan hasta que les
  llamemos": instrucción ejecutable, con el reparto de responsabilidad claro.
- **(6)/(D) Cuándo tirar el plan / adaptarse.** Su "el puente sur está cortado" es una entrada
  humana que, cruzada con el inject `road_cut`, funda el descarte de la ruta sur.
- **(A) Enterarse.** Aporta censo (≈20) e inmóviles del molino (3): información que el sistema no
  tenía y que redimensiona el rescate.

---

## 4 · Vecino de Pueblo B — `role: neighbor_alert`

**Saliente 4 · beat 0:35 · base: `fixtures/transcripts/neighbor_alert.txt`**

**Quién es.** Responsable de Pueblo B (15 civiles). **No le arde nada.** Es el contraste que
demuestra que el mensaje se **adapta al destinatario**: al que no está en peligro no se le da una
orden, se le avisa y se le pregunta capacidad de acogida.

**Tono / dirección de actor.** Calmado, colaborador, sin urgencia. Ofrece recursos con
naturalidad. La llamada es corta y de bajo estrés a propósito — el jurado tiene que **notar la
diferencia de registro** con la del Pueblo A.

**Guion turno a turno.**

- `[VELA]` Buenas, del centro de coordinación. **No tienen el fuego encima, pero conviene que lo
  sepan.**
- `[PERSONA]` Dígame, estamos viendo el humo desde el pueblo.
- `[VELA]` Se está evacuando Pueblo A y es posible que en las próximas horas les lleguen personas
  huyendo. ¿**Tienen sitio** para acogerlas?
- `[PERSONA]` Sí, el polideportivo está abierto y ahí caben de sobra.
- `[VELA]` ¿Necesitan algún recurso para eso?
- `[PERSONA]` Mantas y agua nos vendrían bien, pero podemos empezar sin ello.
- `[VELA]` Perfecto, queda anotado. **No hace falta que hagan nada más de momento**; les avisamos
  si cambia algo.
- `[PERSONA]` De acuerdo, gracias por avisar.

**Qué se ve en pantalla.** `CallsPanel` tarjeta "AVISO AL VECINO · PUEBLO B". Sin cambio de estado
en su POI (no evacúa): la ausencia de acción **es** el punto.

**Qué criterios satisface y por qué.**

- **(3) A quién se avisa y cuándo — el caso fuerte.** Cuatro interlocutores, cuatro registros: aquí
  se ve que "un vecino, un bombero y un responsable no necesitan lo mismo". Aviso ≠ orden.
- **(A) Enterarse.** Convierte una llamada saliente en **captura de estado**: plazas de acogida,
  recursos que faltan (mantas, agua). Entra al modelo como capacidad disponible.
- **(5) Acción concreta.** La acción concreta correcta aquí es **no actuar todavía** y decirlo
  explícito ("no hace falta que hagan nada más"): calibrar el esfuerzo del receptor es una
  decisión, no un olvido.

---

## 5 · Ciudadano que llama al 112 — entrante (web call / 112)

**Entrante · beat 3:30–4:25 · base: `fixtures/transcripts/citizen_report.jsonl`**

**Quién es.** Vecino de Pueblo B atrapado junto al molino viejo. No es personal de emergencias:
está asustado, da la información **desordenada** y **no sabe ubicarse con precisión**. Es la persona
más rica de la demo porque toca cuatro criterios y el clímax de percepción.

**Tono / dirección de actor.** Nervioso, repite palabras ("hola, hola", "gracias, gracias"),
salta de un dato a otro. **No** sabe el nombre del sitio ("estoy cerca de unas casas, al final de
la pista, no sé el nombre"). Al final, cuando ya ha colgado, **manda el pin por Telegram** — ahí es
donde llega el dónde exacto.

**Guion turno a turno (voz).**

- `[VELA]` Emergencias, dígame. ¿Dónde está usted?
- `[PERSONA]` Hola, hola, estoy en el molino viejo, hay mucho humo, no sé qué hacer.
- `[VELA]` Le escucho. ¿Está usted bien? ¿Hay alguien más con usted?
- `[PERSONA]` Yo sí, pero **la pista del sur está cortada por un árbol** y en la casa de al lado
  hay **tres personas que no pueden andar.** ← Jev fija `road_blocked` (observed ≥0,85) y
  `people_immobile`, pero **no** `location_hint`.
- `[VELA]` Anotado, molino viejo, pista sur cortada, tres personas sin movilidad. Estoy avisando a
  los equipos, no cuelgue.
- `[PERSONA]` Vale, vale... ¿pero van a venir?
- `[VELA]` Ya va el camión 2 por la **pista norte**, llega en 40 segundos. No se mueva de donde
  está.
- `[PERSONA]` Gracias, gracias, me quedo aquí.

**Coda — Telegram (beat 4:25, ya colgado).** El vecino **comparte su ubicación** por el bot. Pin
GPS → `citizen.location` + `world.fact.asserted` anclado a `poi_pueblo_b` (`kind: observed`).

**Qué se ve en pantalla.** `CallsPanel` tarjeta "AVISO DEL VECINO (📞 voz)", transcripción SSE en
vivo línea a línea. `CompletenessPanel`: 5 campos en gris (open) → `road_blocked` **sólido**,
`location_hint` **sigue gris**. `DivergenceChart` pico rojo + banner "pista sur cortada, confirmado
por llamada entrante". En Telegram: badge **✈ Telegram**, `location_hint` pasa a **sólido**, pin en
`MapPanel`. Mientras habla, el mundo **no se mueve** (solo el Core trabaja).

**Qué criterios satisface y por qué.**

- **(1) Qué información importa.** Jev (`jev-1.13`) tritura el parcial cada 5 s y devuelve un vector
  de completitud sin texto: de todo lo que dice el vecino, el sistema retiene `road_blocked` e
  `immobile`. El presupuesto obliga a **una** repregunta, la que más reduce incertidumbre.
- **(6) Cuándo tirar el plan.** El `road_blocked` **observado** funda una restricción dura
  (`route_feasible` rechaza la ruta sur) → replan. **SLA <1 s** de colgar a girar los camiones.
- **(A) Enterarse por canal humano / (D) adaptarse.** La entrada más caótica (una persona en
  pánico) acaba moviendo unidades reales.
- **Invariante 8 en pantalla (observado ≠ asumido).** La voz da el **qué**; el hueco de
  `location_hint` **no se inventa** por fuzzy match — lo cierra el **pin de Telegram** (observado),
  que ancla la restricción dura. Un dato asumido iría gris cursiva; este va sólido porque es
  observado.

---

## Cobertura: persona × criterios

Entre las cinco personas se tocan los seis puntos y las cuatro capacidades al menos una vez.

| Persona | 1 info | 2 prio | 3 avisar | 4 recursos | 5 acción | 6 tirar plan | A entera | B prioriza | C coordina | D adapta |
| --- | :--: | :--: | :--: | :--: | :--: | :--: | :--: | :--: | :--: | :--: |
| Retén bomberos | | | ● | ● | ● | | | ● | ● | |
| Dotación ambulancia | | ● | | ● | ● | | | | ● | ● |
| Responsable Pueblo A | | | ● | | ● | ● | ● | | | ● |
| Vecino Pueblo B | | | ● | | ● | | ● | | | |
| Ciudadano 112 (+ Telegram) | ● | | | | | ● | ● | | | ● |

> Las líneas son la base enlatada de `--mock-calls` (`fixtures/transcripts/`). En vivo, HappyRobot
> improvisa sobre la **misma intención**: lo que no cambia es el propósito de cada turno y el
> criterio que dispara. Si una llamada no se puede simular, se cierra igual con `outcome="failed"`
> y las unidades salen (`use_cases.md:176-177`) — la demo nunca se queda con los camiones
> congelados.
