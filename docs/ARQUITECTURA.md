# Ingeniería de vela — guion para grabar

Guion para grabarte explicando **la ingeniería implementada, por qué, y contra qué la decidimos**.
Estructura: primero la tesis (el determinismo como primitiva), luego el pivote (enfoque inicial →
enfoque final con Jev), luego las alternativas descartadas —el sistema multiagéntico el primero—, y
todo orientado a *tradeoffs*: qué ganamos, qué pagamos, por qué el cambio compensa.

Fuentes: `docs/backbone.md` (v1, histórico) vs `docs/backbon_corrected.md` (rev. 2, vigente).

---

## 0 · La frase de una línea (con la que abres)

> *"Nuestra primitiva no es un modelo, es el determinismo. El LLM nunca asigna recursos y nunca toca
> el mundo: solo dice qué importa. Todo lo que mueve algo es código reproducible y auditable."*

Esto es lo que hay que defender ante el jurado en el criterio *Control*: si te preguntan "¿por qué
el sistema hizo X?", tienes que poder pausar, hacer replay y enseñar la traza. Con un grafo de
agentes eso no se puede. Con esta arquitectura, sí.

---

## 1 · La tesis: determinismo como primitiva de diseño

Todo el sistema se ordena alrededor de **cuatro invariantes que no se rompen** (en v1 eran tres; la
cuarta nace con el pivote):

1. **El LLM nunca toca Minecraft.** Minecraft renderiza un estado que vive en nuestro modelo.
2. **El LLM nunca asigna recursos.** Emite una `Policy` (pesos + restricciones duras); el *solver*
   produce el `Plan`. `Policy` no lleva nunca un campo `assignments`.
3. **Todo evento se escribe al journal antes de repartirse.** Sin journal no hay replay.
4. **Un hecho asumido nunca se disfraza de observado.** Cada `Fact` lleva `kind: observed | inferred
   | assumed_default`. Solo `observed` funda una restricción dura.

El marco teórico es **LLM-Modulo** (Kambhampati et al., ICML 2024): los LLM autorregresivos no
planifican ni se autoverifican de forma fiable. Se usan como **generador aproximado de ideas**,
envueltos en verificadores externos basados en modelo, en un bucle bidireccional. El mismo paper
desmonta la autocrítica: el LLM como su propio crítico *degrada* el rendimiento.

**El reparto de responsabilidades es la diferenciación técnica, y hay que decirlo así:**

| Pieza | Qué decide | Cómo | Determinista |
| --- | --- | --- | --- |
| LLM (planner) | **qué importa** — pesos y restricciones | GPT-5.6 Luna, 1 llamada/replan | No, pero salida pequeña y tipada |
| Solver | **quién va dónde** | `scipy.linear_sum_assignment` | Sí, microsegundos |
| Verificadores | **qué es infactible** | 4 funciones puras | Sí |
| Divergencia | **cuándo replanificar** | escalar sobre `assumptions` | Sí |

La frase de venta: *"un LLM suelto asigna tres camiones al mismo frente y deja el hospital sin
cobertura — y lo hace con una explicación preciosa. El solver no puede, porque las restricciones
duras son coste infinito en la matriz."*

---

## 2 · El pivote: percepción al colgar (v1) → percepción en vivo con Jev (rev. 2)

**Lo único que cambió entre las dos arquitecturas fue el nivel 1, la ingesta.** El resto del sistema
no se enteró: mismo bus, mismo solver, mismos verificadores, misma divergencia, mismo journal, mismo
dashboard. Ese aislamiento es en sí un argumento de ingeniería —las fronteras por eventos tipados
aguantaron un cambio de motor de percepción sin tocar a nadie más.

### El enfoque inicial (backbone v1)

- Percepción **al colgar, una sola vez**, con `fenic.semantic.extract`: el modelo **genera** los
  valores como texto libre (`location_hint: str`).
- Resolver el POI real requería un segundo paso, `semantic.join` contra la tabla de POIs.
- `confidence` = autoinforme del modelo, sin calibrar.
- Los huecos quedaban a `None` en silencio.
- Latencia colgar → giro: **~2 s**, casi todo extracción, porque todo pasaba al colgar.

### Por qué no bastaba (los tradeoffs que lo mataron)

1. **Espacio de salida abierto = riesgo de alucinación.** Un `str` libre puede inventar una
   carretera que no existe en el escenario. El *join* la maquilla, no la elimina.
2. **Todo al colgar = latencia contra el clímax.** El beat de la demo es "cuelga y las unidades
   giran". 2 s de extracción en ese instante es justo donde no lo quieres.
3. **Confianza no calibrada = no puedes poner umbrales de riesgo.** Sin un número fiable, no puedes
   decir "marcar una arista `cut` exige 0,85; crear un rescate recuperable, 0,55".
4. **Huecos silenciosos = decisiones sobre datos que no tienes,** sin que se note en pantalla.

### El enfoque final (rev. 2, con TypeSafe Jev)

`jev-1.13` es un **System One Model**: no genera texto, **evalúa preguntas tipadas contra un estado**
y devuelve decisiones estructuradas con distribución de probabilidad y confianza calibrada. Tres
primitivas: `Choice` (elegir de un conjunto), `Score` (puntuar en niveles), `Noul` (probabilidad de
que una afirmación sea cierta). Se evalúan en paralelo, ~0,1 s con todas las preguntas a la vez.

Lo que compra el cambio:

- **Espacio de salida cerrado = garantía estructural.** `CallFacts` deja de ser extracción y pasa a
  ser un **catálogo de preguntas** generado desde el YAML: `location_hint` es un `Choice` sobre los 5
  POIs + `not_stated`; `road_blocked`, un `Choice` sobre las aristas del escenario. *El modelo no
  puede inventar una carretera que no existe porque esa carretera no está en su espacio de salida.*
  Esto es garantía, no umbral. (Ojo al pitch: no digas "100% determinista"; la propia doc avisa de
  que los invariantes entre preguntas distintas no están garantizados.)
- **Percepción en vivo, cada 5 s durante la llamada,** no al colgar. Cuando el vecino cuelga, el
  trabajo de percepción ya está casi hecho. Latencia colgar → giro: **< 1 s**.
- **Confianza calibrada → tres umbrales por riesgo** (`assert_soft_fact` 0,55, `assert_hard_fact`
  0,85, `ask_followup` 0,55) y **presupuesto por gravedad** (`critical` 8 s, `high` 25 s, `medium`
  60 s). No se retiene una ambulancia mientras se completa un cuestionario.
- **Los huecos se gestionan, no se callan.** Campo por encima del umbral → `observed`. Por debajo y
  con presupuesto → el agente pregunta *solo eso*. Por debajo y sin presupuesto → el LLM lo rellena
  como `assumed_default`, **en gris cursiva en pantalla**. De aquí sale el invariante 4: el relleno
  no es una alucinación disfrazada, es una hipótesis que el sistema intenta falsar activamente.

`fenic` no desaparece: sale de la ruta caliente y se queda para el job batch de aprendizaje entre
runs, donde no hay presión de latencia. Y hay plan B: `--no-jev` cae a `fenic.semantic.extract` con
los campos como `Literal` sobre las opciones del YAML — se pierde la confianza calibrada y el bucle
en vivo, se conserva la garantía de espacio cerrado.

### Tabla del pivote (la que enseñas en pantalla)

| | Antes (v1) | Ahora (rev. 2) |
| --- | --- | --- |
| Cuándo se percibe | al colgar, una vez | cada 5 s durante la llamada + tick final |
| Con qué | `fenic` **genera** valores | Jev **elige** entre opciones del escenario |
| `location_hint` | `str` libre | `Choice` sobre 5 POIs + `not_stated` |
| Confianza | autoinforme sin calibrar | derivada de la distribución, calibrada |
| Huecos | `None` en silencio | presupuesto → repregunta → `assumed_default` visible |
| Colgar → giro | ~2 s | **< 1 s** |

---

## 3 · Por qué Humalike: es una emergencia, y al otro lado hay una persona asustada

Separa las dos capas de voz, porque es lo que confunde a todo el mundo:

- **HappyRobot = infraestructura de voz y ejecución.** Telefonía real (SIP/PSTN), audio
  bidireccional de baja latencia, tools, workflows. Es quien descuelga y quien locuta. Requisito del
  reto.
- **Humalike = capa de inteligencia conversacional encima.** *No toca la telefonía ni la lógica de
  negocio.* Lee a la persona y ajusta cómo se comunica el agente.

El argumento de por qué está: **es una crisis, y el canal es una llamada de voz con un humano bajo
estrés.** El *qué* decir lo calcula el Core en código puro (el `Advice`: ruta segura, refugio,
unidad, ETA). Pero el *cómo* decírselo a alguien con `fear: 0.8` importa, y no es determinista ni
queremos que lo sea. Ahí entra Humalike:

- `foresee` (Theory of Mind): antes de que el agente hable, devuelve `mental_state` del vecino,
  `predicted_reaction` y un `refined_reply` en la voz del operador. En el camino de la respuesta.
- `analyze` (Social Observability): al colgar, `health_score` y errores sociales con su arreglo →
  van al journal y al **bonus de aprendizaje** (los hallazgos ajustan el prompt del agente, no el del
  planner).
- `personas`: genera los 20 vecinos sintéticos coherentes para el ruido de llamadas.

El tradeoff: metes una dependencia externa más y latencia (1–1,5 s de `foresee`), pero **en paralelo
con el planner y el solver**, y con timeout: si `foresee` tarda más de 1,5 s se responde el borrador
y se sigue. Nunca bloquea el ciclo. Regla de oro del repo: *un servicio caído se degrada y se anota,
nunca un `except: pass`.*

---

## 4 · Las alternativas que valoramos y descartamos (orientado a tradeoffs)

Esta es la parte que hay que "inventar" con criterio, porque no la dejamos escrita. Todas son
coherentes con lo que sí decidimos.

### 4.1 · Sistema multiagéntico (el grafo de agentes LLM) — DESCARTADO, el principal

La tentación: seis agentes LLM que se hablan entre ellos (uno percibe, uno prioriza, uno asigna, uno
verifica, uno habla, uno coordina). Por qué no:

- **Latencia acumulada.** Cada salto entre agentes es ~0,5 s. En una demo de 6 min con un clímax de
  "<1 s colgar → giro", un pipeline de agentes se come el presupuesto entero solo en *handoffs*.
- **Superficie de alucinación multiplicada.** Cada agente es una oportunidad de inventar. Y el paper
  de Kambhampati es explícito: encadenar LLMs y que se critiquen entre ellos *degrada* el resultado
  y produce falsos positivos frente a verificadores externos correctos.
- **No auditable = suspende en *Control*.** No puedes explicar en el pitch por qué el sistema hizo lo
  que hizo si la decisión emergió de una conversación entre seis modelos. No hay traza reproducible.
- **No reproducible = no hay bonus de aprendizaje.** El bonus (run 1 vs run 12) exige poder repetir
  un run exacto. Con temperatura y handoffs no hay run reproducible que comparar.

El contraste que lo remata: aquí hay **exactamente una** llamada al modelo de razonamiento por
replan, y solo con bandera (divergencia > 0,25, violación de restricción dura, o hecho `critical`).
Todo lo demás es código determinista. Eso es lo que permite pausar, hacer replay y enseñar la traza
completa. *El multiagéntico optimiza para parecer inteligente; nosotros optimizamos para ser
auditables.*

### 4.2 · Un solo LLM monolítico que lo hace todo — DESCARTADO

Percibir + priorizar + asignar + hablar en una sola cabeza. Es lo contrario del multiagéntico y
falla por lo mismo de fondo: **el LLM asignando recursos**. Asigna tres camiones al mismo frente,
deja el hospital sin cobertura, y lo explica de maravilla. Ni reproducible ni verificable. Rompe los
invariantes 1 y 2 de golpe.

### 4.3 · Percepción con `fenic.semantic.extract` en la ruta caliente — PIVOTADO

Fue nuestra primera elección (v1). La abandonamos en la ruta caliente por lo del §2: espacio de
salida abierto, confianza sin calibrar, todo al colgar. **No la tiramos**: la reutilizamos donde su
tradeoff sí compensa —el batch de aprendizaje entre runs, sin presión de latencia— y como plan B
`--no-jev`. Decisión de ingeniería madura: no es "esto estaba mal", es "esto está en la capa
equivocada".

### 4.4 · El LLM como su propio verificador — DESCARTADO

Tentador porque es gratis de montar. Descartado por evidencia directa (Kambhampati): la autocrítica
degrada y produce falsos positivos. En su lugar, **cuatro verificadores que son funciones puras**
(`route_feasible`, `coverage_maintained`, `no_double_booking`, `civilian_reachable`), cada una
devuelve `None` o un `Violation` con texto legible que vuelve al planner como crítica. Dos intentos,
y a la tercera se cae al plan del solver con pesos neutros. **La demo nunca se queda sin plan.**

### 4.5 · IA/bots dentro de Minecraft (mineflayer) — DESCARTADO temprano

Habría metido Node en el servidor y pathfinding no determinista. En su lugar, el mundo es un
**dispositivo de salida**: RCON + `/tp`, `/fill`, `/setblock`, con Dijkstra sobre el grafo del YAML.
El mismo escenario produce el mismo incendio en cada ensayo (semilla del RNG en el YAML). Bonus:
sin Node en el backend, el debate de lenguaje se cierra solo (Python 3.12, que es lo que `fenic`
exige).

### 4.6 · Aprendizaje: blob único vs una regla por fichero — EVOLUCIONÓ

v1: un `memory/policy_rules.md` que se inyecta en bloque. rev. 2: **bucle estilo Hermes** —una regla
por fichero con frontmatter, *gating* determinista (el planner solo ve las reglas cuyo `trigger`
casa con el `WorldState`, nunca lo decide el LLM), y mejora por patch. Tradeoff: más fontanería, pero
el jurado ve *qué* regla influyó en *qué* decisión por su `lineage`.

---

## 5 · Guion de grabación (orden sugerido, ~4–5 min)

1. **Abre con la tesis** (§0): determinismo como primitiva, no el modelo.
2. **Enseña el diagrama** de `ARQUITECTURA` (HappyRobot → Jev → LLM·Policy → Solver → orden). Señala
   que el LLM está en una caja y solo sale una `Policy`.
3. **Cuenta el reparto** LLM decide qué / solver decide quién (§1) con la frase de los tres camiones.
4. **El pivote** (§2): "empezamos percibiendo al colgar con extracción de texto libre; teníamos dos
   problemas, alucinación y latencia. Pivotamos a Jev." Enseña la tabla del pivote. Remata con "el
   espacio de salida del modelo es el conjunto de objetos de nuestro fichero de escenario".
5. **Humalike** (§3): es una emergencia, al otro lado hay una persona; el Core calcula el qué, Humalike
   ajusta el cómo, en paralelo y con timeout.
6. **Las alternativas** (§4): dedica el grueso al multiagéntico (§4.1) — latencia, alucinación,
   auditabilidad, reproducibilidad. Menciona el monolito y la autocrítica como los dos extremos que
   también rechazamos.
7. **Cierra** volviendo a *Control*: "cualquier decisión de esta demo la puedo pausar, reproducir y
   explicar línea a línea. Eso es lo que un grafo de agentes no te da."

### Munición de tradeoffs (frases sueltas para soltar)

- "El multiagéntico optimiza para parecer inteligente; nosotros para ser auditables."
- "El LLM es un generador de hipótesis, no un ejecutor. Todo lo que mueve algo es determinista."
- "No es un umbral de confianza, es una garantía estructural: la carretera que no está en el YAML no
  está en el espacio de salida del modelo."
- "Un hecho asumido nunca se disfraza de observado: entra en gris y el sistema lo intenta falsar."
- "Una sola llamada al modelo de razonamiento por replan, y solo con bandera. El resto es código."
