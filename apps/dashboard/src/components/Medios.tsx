// La luz de medios: una tira de dos fichas —bomberos y ambulancias— con cuántos están
// cogidos de cuántos hay. Va en la cabecera de las dos vistas, así que está en pantalla
// también mientras se mira el mapa, que es donde está la sala cuando entra la llamada.
//
// Se lee de tres metros y de reojo, que es como se va a mirar: el dato es la cifra
// `2/2`, en grande; el resto es contexto y va pequeño. Y **cuando no queda ninguna libre
// la ficha cambia de color y gana una frase** (`ninguna libre`), porque ese es el momento
// del guion —el agente pone al que llama en espera y telefonea a la ambulancia ocupada— y
// tiene que cantar sin que nadie lo señale.
//
// La ficha entera es la luz: verde mientras quede alguno libre, ámbar cuando no queda
// ninguno. Y dentro, tres estados y tres FORMAS además del color (el proyector de la sala
// desatura, y un verde y un ámbar de 14 px a tres metros se parecen más de lo que uno
// cree):
//
//   libre              anillo hueco
//   ocupada            disco lleno
//   fuera de servicio  disco gris tachado
//
// Así «las dos ocupadas» (dos discos) y «una ocupada y otra averiada» (un disco y un
// tachado) no se leen igual, que es justo la distinción que se pierde si solo se mira la
// cifra: `1/2 ocupadas` con la otra averiada NO quiere decir que quede una.
//
// El ámbar es `vela-warn` y no `vela-replan`: el rojo es del banner y solo del banner
// (REQ-077). Quedarse sin ambulancias es el plan B del agente, no un replan.
import { useMemo } from 'react'

import type { Event, WorldState } from '../types'
import { UNIT_ICON, type IconDef } from '../geo/icons'
import { shortId } from '../story/format'
import { UNIT_STATUS } from '../story/labels'
import { MEDIO_LABEL, medios, type Availability, type MedioCount } from '../story/medios'
import { knownTasks, taskName } from '../story/tasks'

export function Medios({ state, events }: { state: WorldState | null; events: Event[] }) {
  const counts = useMemo(() => medios(state, events), [state, events])
  const tasks = useMemo(() => knownTasks(state, events), [state, events])
  // Un tipo sin unidades en el escenario no pinta ficha: `0/0 ambulancias` ocupa el mismo
  // sitio que un dato y no lo es. Sin snapshot todavía no hay ninguna, y la tira entera
  // desaparece en vez de enseñar ceros que no ha comprobado nadie.
  const visible = counts.filter((c) => c.total > 0)
  if (visible.length === 0) return null

  return (
    // `hidden md:flex`: en una ventana estrecha la cabecera es para el título. La tira es
    // de la pantalla de sala, y ahí sobra ancho.
    <div className="ml-auto hidden items-center gap-2 md:flex" aria-label="Medios">
      {visible.map((count) => (
        <Chip key={count.kind} count={count} pois={state?.pois ?? null} tasks={tasks} />
      ))}
    </div>
  )
}

function Chip({
  count,
  pois,
  tasks,
}: {
  count: MedioCount
  pois: WorldState['pois'] | null
  tasks: ReturnType<typeof knownTasks>
}) {
  const label = MEDIO_LABEL[count.kind]
  const tense = count.free === 0
  // Todos trabajando es lo normal en un incendio y dura todo el run; lo que hay que
  // mirar es todos trabajando Y algo esperando sin nadie, que es cuando el agente pone
  // a quien llama en espera y telefonea a una dotación ocupada.
  const alarma = tense && count.waiting > 0
  // Qué hace falta decir con palabras además de la cifra. Con alguna libre y ninguna
  // averiada no hace falta nada: la cifra ya lo dice y una frase de más es una frase que
  // se deja de leer.
  const notes = [
    count.waiting > 0 ? `${count.waiting} esperando` : null,
    tense ? label.none : null,
    count.out > 0 ? `${count.out} fuera de servicio` : null,
  ].filter((n): n is string => n !== null)

  return (
    <div
      // El detalle al pasar el ratón (REQ-090): qué unidad está en qué tarea, con su id.
      title={hint(count, pois, tasks)}
      className={`flex h-11 shrink-0 items-center gap-2.5 rounded-xl border px-3 ${
        alarma
          ? 'border-vela-warn bg-vela-warn-bg'
          : tense
            ? 'border-vela-warn bg-vela-panel'
            : 'border-vela-edge bg-vela-panel'
      }`}
    >
      <Glyph def={UNIT_ICON[count.kind]} />
      {/* La cifra ES la luz: verde o ámbar, nunca gris. Gris sería un contador más, y de
          un contador nadie se entera de reojo. */}
      <span
        className={`text-[26px] font-bold leading-none tabular-nums ${
          tense ? 'text-vela-warn' : 'text-vela-good'
        }`}
      >
        {count.busy}/{count.total}
      </span>
      <span className="flex flex-col leading-[1.15]">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-vela-dim">
          {label.plural}
        </span>
        <span className={`text-[13px] ${tense ? 'font-semibold text-vela-warn' : 'text-vela-dim'}`}>
          {[label.busy, ...notes].join(' · ')}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-1">
        {count.units.map((unit) => (
          <Dot key={unit.id} availability={unit.availability} />
        ))}
      </span>
    </div>
  )
}

/** Un medio, una forma. Nada de texto dentro: a 14 px no se lee. */
function Dot({ availability }: { availability: Availability }) {
  if (availability === 'out') {
    return (
      <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 text-vela-dim" aria-hidden>
        <circle cx="7" cy="7" r="6" fill="currentColor" />
        <path d="M3 11 11 3" stroke="var(--color-vela-panel)" strokeWidth="2.2" fill="none" />
      </svg>
    )
  }
  const busy = availability === 'busy'
  return (
    <svg
      viewBox="0 0 14 14"
      className={`h-3.5 w-3.5 ${busy ? 'text-vela-warn' : 'text-vela-good'}`}
      aria-hidden
    >
      <circle
        cx="7"
        cy="7"
        r={busy ? 6 : 5}
        fill={busy ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeWidth="2.2"
      />
    </svg>
  )
}

/** El mismo dibujo que lleva esa unidad en el mapa (`geo/icons.ts`), para que la ficha y
 *  el marcador se lean como la misma cosa. `body` es una constante, nunca un dato del run. */
function Glyph({ def }: { def: IconDef }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-5 w-5 shrink-0 text-vela-ink"
      aria-hidden
      dangerouslySetInnerHTML={{ __html: def.body }}
    />
  )
}

/** «truck1 · frente 13 11» por línea. La tarea se nombra por lo que es y no por su id
 *  (`story/tasks.ts`), y el id entero de la unidad va delante para la procedencia. */
function hint(
  count: MedioCount,
  pois: WorldState['pois'] | null,
  tasks: ReturnType<typeof knownTasks>,
): string {
  return count.units
    .map((unit) => {
      const who = shortId(unit.id)
      if (unit.availability === 'busy' && unit.taskId) {
        return `${who} · ${taskName(unit.taskId, tasks, pois)}`
      }
      if (unit.availability === 'out') {
        return `${who} · ${UNIT_STATUS.unavailable}${unit.reason ? ` (${unit.reason})` : ''}`
      }
      return `${who} · libre`
    })
    .join('\n')
}
