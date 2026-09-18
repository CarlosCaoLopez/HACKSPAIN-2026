# Ficha de Product Hunt — lista para el domingo

Reto HappyRobot · HackSpain 2026
**Plazo: domingo, totalmente terminada.** Ficha completa, revisada y visible en el pitch. La fecha de lanzamiento se decide después del hackathon.

---

## 1. Antes de empezar: qué hay que decidir

| Campo | Estado |
|---|---|
| Nombre del producto | ⬜ **Pendiente** — bloquea todo lo demás |
| Dominio + landing viva | ⬜ Necesario: PH pide URL y la gente la abre |
| Vídeo/GIF principal | ⬜ El activo que decide el resultado |
| Fecha de lanzamiento | Sin fijar. Se elige después, entre semana, a las 12:01 AM PT (09:01 CEST) |

Nombres de trabajo si no hay decisión: **Aegis** · **Beacon** · **CrisisOS**

---

## 2. Campos de la ficha

### Nombre
```
[NOMBRE]
```

### Tagline
Máximo 60 caracteres. Orientado a resultado, no a feature.

**Opción A — recomendada** (lidera con el gancho visual, 56 car.):
```
Watch AI agents run disaster response — live, in Minecraft
```

**Opción B** (lidera con la sustancia agéntica, 48 car.):
```
AI agents that decide and act when a crisis hits
```

**Opción C** (híbrida, 52 car.):
```
Crisis response agents you can actually watch work
```

### Descripción
```
When a wildfire turns, a grid fails or a river breaks its banks, the
tools emergency teams use today are dashboards. They tell you what
happened. They don't decide anything.

[NOMBRE] is an agentic system that does the deciding. It reads what's
coming in, works out which three of the hundred messages actually
change something, calls the people who need calling, dispatches what
there is rather than what there should be, and throws the plan away
when the wind shifts.

Every decision plays out through AI characters inside Minecraft, so
instead of reading logs you watch the response happen.

Built on HappyRobot + Humalike.
```

### Topics
- Artificial Intelligence
- AI Agents *(o Agentic AI, según cómo aparezca en el selector — es la categoría en la que se lista Humalike)*
- Developer Tools *(si liberáis el puente)*
- Government & Public Sector *(opcional; nicho pero da credibilidad)*

> Minecraft no existe como topic. Va en el tagline y en la primera imagen, que es donde capta al público gamer.

### Enlaces
- Website: landing propia (no GitHub)
- GitHub: solo si el repo es público
- X/Twitter del producto

---

## 3. Galería

El uploader de PH indica los tamaños exactos al subir — formato horizontal. Comprobadlo ahí, no lo deis por hecho.

| # | Contenido | Nota |
|---|---|---|
| 1 | **Vídeo/GIF 15-20s**: el bot de Minecraft ejecutando una decisión real | Nunca al final. **Debe entenderse sin sonido** — la mayoría lo ve en silencio |
| 2 | Interfaz de control: la decisión **y el porqué** | Demuestra que no es un juguete. Cubre el requisito obligatorio del reto |
| 3 | Diagrama de arquitectura: orquestador + agentes especializados | Credibilidad técnica en 3 segundos |
| 4 | Antes/después: el escenario cambia → el plan se rehace | Es la "adaptación" que exige la rúbrica del reto |
| 5 | Cierre con badge de HackSpain 2026 | Contexto de origen |

**Thumbnail:** cuadrado, legible a tamaño diminuto. Si el logo no se lee en 1cm, no sirve.

---

## 4. Comentario del maker

Lo lee entre el **60 y el 80% de los votantes**. Es la pieza de copy más importante de la ficha, por encima de la descripción.

```
Hey Product Hunt 👋

We built this in 48 hours at HackSpain 2026, for HappyRobot's
challenge: can AI manage a crisis?

The problem we kept coming back to: the software emergency teams
actually use — Everbridge, Veoci, ArcGIS — is passive. It aggregates,
it visualises, it alerts. It doesn't decide, and it doesn't act. A
human still has to read everything and work out what to do, at the
exact moment they have the least time to do it.

So we built the opposite. [NOMBRE] runs a loop that answers six
questions over and over as the situation moves:
– which of these hundred messages actually change something
– what gets attended first
– who gets called, what they're told, in what order
– where the three ambulances go when five places want them
– what the next concrete action is, and who does it
– and when the plan from twenty minutes ago stops being valid

It doesn't just propose. It makes the calls, sends the messages,
opens the tickets.

Now, the Minecraft part — and this is the bit people assume is a
gimmick. It isn't. Watching a multi-agent system through logs is
miserable, and a dashboard flattens everything into rows. Putting
each agent in a body, in a world, means you see the response happen:
who went where, what got left uncovered, where the plan broke. It's
the supervision layer, and it's genuinely the fastest way we found
to understand what our own system was doing.

We also plugged in Humalike, because an agent that phones a panicking
resident cannot sound the same as one briefing a fire chief. Without
that, people hang up. Social intelligence turned out to be a
functional requirement, not a nice-to-have.

What we'd love feedback on:
1. If you work in emergency response — what's the first thing that
   would make you distrust a system like this?
2. Is the Minecraft layer useful to you, or just fun to watch? We
   genuinely can't tell yet.
3. What scenario should we break it with next?

Happy to answer anything about the architecture.
```

> ⚠️ **No hay ninguna petición de votos en ese texto, y no debe haberla.** Ni aquí, ni en X, ni en DMs. El algoritmo de PH penaliza en silencio y los moderadores en público. Se pide feedback.

---

## 5. Sembrar la conversación

Los comentarios pesan más que los votos sueltos: un lanzamiento con 300 votos y 80 comentarios suele rankear por encima de uno con 400 y 15.

Dejad preparadas 4-5 personas de confianza con **preguntas reales** para el día que lancéis (no elogios, que se notan):

- "How does it handle conflicting reports from two sources?"
- "What happens when the LLM decides something wrong — is there a kill switch?"
- "Could this run offline? In a blackout you don't have connectivity."
- "Is the Minecraft bridge open source?"
- "How do you evaluate whether the decisions were actually good?"

Tened las respuestas escritas de antemano. El día del lanzamiento no hay tiempo de pensarlas.

---

## 6. Qué hay que tener hecho el domingo

1. Producto creado en PH con **todos** los campos de arriba rellenos
2. Galería subida y thumbnail puesto
3. Comentario del maker escrito, revisado en voz alta y guardado
4. Fecha sin fijar: se elige después del hackathon, un día entre semana a las 12:01 AM PT (09:01 CEST). Lo que importa el domingo es que la ficha esté **completa**, no que tenga fecha
5. Comprobar si sigue existiendo la página de teaser / "Coming Soon": **las fuentes de 2026 se contradicen** sobre si la retiraron. Si existe, activadla; si no, la conversación pre-lanzamiento se hace en los foros de PH
6. Pestaña abierta con la ficha para el pitch

**Calentar las cuentas del equipo:** que cada maker comente de forma genuina en 2-3 lanzamientos ajenos este fin de semana. Perfiles nuevos y vacíos votando en bloque el día del lanzamiento es exactamente el patrón que el algoritmo marca.

---

## 7. Cómo se usa en el pitch del domingo

Se enseña la pantalla y se dice, literalmente:

> "La ficha de Product Hunt está montada — aquí la tenéis, completa.
>
> No la lanzamos este fin de semana a propósito: PH es de un solo tiro, queda público para siempre, y de viernes a lunes no hay tráfico. Un puesto #40 con 25 votos es peor que no tener ficha. Lanzamos en cuanto tengamos la lista detrás."

Eso demuestra criterio de go-to-market, que es lo que un VC evalúa en un equipo de hackathon. Vale más que un ranking mediocre real.

---

## 8. Checklist para el domingo

- [ ] Nombre decidido
- [ ] Dominio + landing viva con captura de email y analytics
- [ ] Vídeo/GIF principal montado y legible sin sonido
- [ ] 5 imágenes de galería
- [ ] Thumbnail cuadrado
- [ ] Tagline elegido (A, B o C)
- [ ] Descripción con el nombre sustituido
- [ ] Comentario del maker con el nombre sustituido y revisado en voz alta
- [ ] Topics marcados
- [ ] Ficha completa guardada en PH, sin fecha fijada
- [ ] Cuentas del equipo calentadas
- [ ] 4-5 personas con preguntas preparadas
- [ ] Humalike contactado
- [ ] Pestaña lista para enseñar en el pitch
