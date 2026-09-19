// La luz de medios: cuántas ambulancias y cuántos camiones hay, cuántos están cogidos y
// cuántos quedan. Es el dato que decide la escena que importa del H3 —el agente pone al que
// llama en espera porque NO HAY AMBULANCIA LIBRE— y hasta ahora había que deducirlo
// leyendo la cola de prioridades unidad a unidad.
//
// El criterio de «ocupada» NO se inventa aquí: es el mismo que usa el core para decidir a
// quién telefonea (`core/loop.py::_free_unit`, «ni averiada, ni metida en OTRA tarea
// abierta»). Si la pantalla dijera «queda una libre» y el core llamara al de la espera,
// la pantalla estaría mintiendo justo en el momento que se está enseñando.
//
//   fuera de servicio  `status === 'unavailable'`            (avería, o un retén que ha
//                                                             dicho por teléfono que no)
//   ocupada            tiene `task_id`, esa tarea existe y no está `done`
//   libre              el resto
//
// El orden importa y es exclusivo: **fuera de servicio gana**. Una unidad averiada con una
// tarea abierta encima no está trabajando en ella, está parada; contarla como ocupada
// haría que «2/2 ocupadas» y «1 ocupada + 1 averiada» se pintaran igual, y para quien mira
// no es lo mismo (una se libera sola, la otra no).
//
// --- Por qué hay que plegar eventos y no basta `state` ---------------------------------
//
// `useEventStream` solo pone `state` con el SNAPSHOT (del resto de eventos deriva `seq` y
// `t_sim` y nada más), y el gateway manda un único snapshot al conectar. O sea: en una demo
// de seis minutos `state.units[*].task_id` es la foto del segundo cero para siempre. Con
// eso, la luz nunca se encendería.
//
// Lo que se pliega es lo MISMO que pliega `belief.apply` en el core, campo por campo, y solo
// los cuatro campos que esta luz pinta:
//
//   `plan.emitted`        → `unit.task_id = assignment.task_id`   (belief.py, PLAN_EMITTED)
//   `task.changed`        → `tasks[id]`, con su `done`            (vía `knownTasks`)
//   `world.unit.status`   → `unit.status`
//   `world.fact.asserted` con clave `unit:<id>:available`
//                         → `status = idle | unavailable`         (belief.py, apply_fact)
//
// Ese último es el que hace falta para el guion del teléfono: cuando el retén dice que no
// puede salir, `voice/webhooks.py` asierta `unit:<id>:available=false` y el core lo convierte
// en `unavailable` DENTRO de su estado, sin emitir `world.unit.status`. Sin plegarlo aquí, la
// dotación que acaba de decir que no por teléfono seguiría contando como libre en pantalla.
//
// No se pliega el `status = moving` que `belief.apply` pone con un plan nuevo: el estado de
// movimiento lo dice el sim con sus `world.unit.status`, y aquí de `status` solo se mira si
// es `unavailable`.
import type { Event, UnitKind, UnitStatus, VelaEvent, WorldState } from '../types'
import { knownTasks } from './tasks'

/** Los medios que se cuentan, en el orden en el que se pintan. Los drones quedan fuera a
 *  propósito: no llevan a nadie ni apagan nada, y una luz con cuatro cifras deja de leerse
 *  de un vistazo. Si mañana hacen falta, se añaden aquí y el resto no se toca. */
export const MEDIOS_KINDS: readonly UnitKind[] = ['fire_truck', 'ambulance']

/** Los rótulos de la luz. No salen de `UNIT_KIND` (`story/labels.ts`) a propósito: allí
 *  se nombra UNA unidad (`camión`, `ambulancia`) y aquí se nombra el SERVICIO del que se
 *  dice cuánto queda. En una sala se pregunta «¿quedan bomberos?», no «¿queda algún
 *  camión?», y el plural también cambia la concordancia (`ocupados` / `ocupadas`), que si
 *  se calcula a ojo acaba en «1/2 ocupados» sobre las ambulancias.
 *
 *  Exhaustivo sobre `UnitKind` a propósito, como los iconos: si `contracts` gana un tipo
 *  de unidad, esto deja de compilar hasta que tenga rótulo, en vez de pintarse en inglés. */
export const MEDIO_LABEL: Record<UnitKind, { plural: string; busy: string; none: string }> = {
  fire_truck: { plural: 'Bomberos', busy: 'ocupados', none: 'ninguno libre' },
  ambulance: { plural: 'Ambulancias', busy: 'ocupadas', none: 'ninguna libre' },
  drone: { plural: 'Drones', busy: 'ocupados', none: 'ninguno libre' },
  crew: { plural: 'Brigadas', busy: 'ocupadas', none: 'ninguna libre' },
}

/** Las tres situaciones, excluyentes. */
export type Availability = 'busy' | 'free' | 'out'

export interface MedioUnit {
  id: string
  availability: Availability
  status: UnitStatus
  /** La tarea que la tiene cogida. Solo cuando `availability === 'busy'`: un `task_id` de
   *  una tarea ya cerrada no dice nada de lo que la unidad está haciendo ahora. */
  taskId: string | null
  /** Por qué está fuera de servicio, si algún evento lo dijo. */
  reason: string | null
}

export interface MedioCount {
  kind: UnitKind
  total: number
  busy: number
  free: number
  out: number
  /** Ordenadas por id, para que la fila no baile entre renders. */
  units: MedioUnit[]
  /** Tareas abiertas que piden este tipo de medio y se han quedado SIN unidad en el
   *  último plan (`Plan.unassigned_tasks`). Es lo que separa «todos trabajando», que
   *  en un incendio es lo normal y dura todo el run, de «todos trabajando y hay algo
   *  esperando», que es el momento en el que el agente pone a alguien en espera y
   *  telefonea a una dotación ocupada. Sin esto la luz de bomberos está en ámbar de
   *  principio a fin y deja de mirarse. */
  waiting: number
}

/** El estado vivo de una unidad: lo que trae el snapshot, con los eventos posteriores
 *  encima. */
interface Live {
  kind: UnitKind
  status: UnitStatus
  taskId: string | null
  reason: string | null
}

/** `unit:<unit_id>:available` — clave de contrato (`contracts/factkeys.py`). */
const AVAILABLE_KEY = /^unit:([^:]+):available$/

function liveUnits(state: WorldState | null, events: Event[]): Map<string, Live> {
  const live = new Map<string, Live>()
  for (const unit of Object.values(state?.units ?? {})) {
    live.set(unit.id, {
      kind: unit.kind,
      status: unit.status,
      taskId: unit.task_id ?? null,
      reason: null,
    })
  }
  for (const envelope of events) {
    const ev = envelope as unknown as VelaEvent
    switch (ev.type) {
      case 'plan.emitted':
        for (const a of ev.payload.assignments) {
          const before = live.get(a.unit_id)
          if (before) live.set(a.unit_id, { ...before, taskId: a.task_id })
        }
        break
      case 'world.unit.status': {
        const before = live.get(ev.payload.unit_id)
        if (!before) break
        live.set(ev.payload.unit_id, {
          ...before,
          status: ev.payload.status,
          reason: ev.payload.status === 'unavailable' ? ev.payload.reason || null : null,
        })
        break
      }
      case 'world.fact.asserted': {
        const m = AVAILABLE_KEY.exec(ev.payload.key)
        if (!m?.[1]) break
        const before = live.get(m[1])
        if (!before) break
        const ok = Boolean(ev.payload.value)
        live.set(m[1], {
          ...before,
          status: ok ? 'idle' : 'unavailable',
          reason: ok ? null : ev.payload.source,
        })
        break
      }
      default:
        break
    }
    // Una unidad que aparece por primera vez en un evento y no está en el snapshot se
    // ignora: sin `kind` no se sabe en qué columna va, e inventárselo sería contar una
    // ambulancia que a lo mejor es un dron.
  }
  return live
}

/** El recuento por tipo. `state` sin snapshot todavía devuelve todos los tipos a cero:
 *  quien pinta decide si eso se enseña o no. */
/** Qué capacidad pide cada tipo de medio: la misma pareja `required_capability` /
 *  `Unit.capabilities` con la que el solver decide quién puede coger qué. Un rescate
 *  sin ambulancia pide `transport`; un frente sin camión, `extinguish`. */
const MEDIO_CAPABILITY: Record<UnitKind, string> = {
  fire_truck: 'extinguish',
  ambulance: 'transport',
  drone: 'recon',
  crew: 'extinguish',
}

/** Las tareas del último plan que se quedaron sin nadie, por capacidad que piden. */
function waitingByCapability(
  events: Event[],
  tasks: Record<string, { required_capability: string; done: boolean } | undefined>,
): Record<string, number> {
  // El último plan manda. `WorldState` no lleva el plan (el snapshot trae el mundo, no
  // la decisión), así que sin ningún `plan.emitted` todavía no hay nada esperando: es
  // el estado real del segundo cero, no un dato que falte.
  let unassigned: string[] = []
  for (const envelope of events) {
    const ev = envelope as unknown as VelaEvent
    if (ev.type === 'plan.emitted') unassigned = ev.payload.unassigned_tasks
  }
  const out: Record<string, number> = {}
  for (const id of unassigned) {
    const task = tasks[id]
    if (!task || task.done) continue
    out[task.required_capability] = (out[task.required_capability] ?? 0) + 1
  }
  return out
}

export function medios(state: WorldState | null, events: Event[]): MedioCount[] {
  const live = liveUnits(state, events)
  const tasks = knownTasks(state, events)
  const waiting = waitingByCapability(events, tasks)

  return MEDIOS_KINDS.map((kind) => {
    const units: MedioUnit[] = []
    for (const [id, unit] of live) {
      if (unit.kind !== kind) continue
      const task = unit.taskId ? tasks[unit.taskId] : undefined
      const open = task !== undefined && !task.done
      // Averiada primero: una unidad parada no está trabajando aunque lleve una tarea.
      const availability: Availability =
        unit.status === 'unavailable' ? 'out' : open ? 'busy' : 'free'
      units.push({
        id,
        availability,
        status: unit.status,
        taskId: availability === 'busy' ? unit.taskId : null,
        reason: unit.reason,
      })
    }
    units.sort((a, b) => a.id.localeCompare(b.id))
    return {
      kind,
      total: units.length,
      busy: units.filter((u) => u.availability === 'busy').length,
      free: units.filter((u) => u.availability === 'free').length,
      out: units.filter((u) => u.availability === 'out').length,
      units,
      waiting: waiting[MEDIO_CAPABILITY[kind]] ?? 0,
    }
  })
}
