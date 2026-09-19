// El nombre de una tarea para pantalla: por lo que ES (`kind` + objetivo), nunca por el
// prefijo de su id. `task_front_12_7` es una convención del core que ya cambió una vez
// (`task_ext_`), y si el dashboard la lee del id, el día que cambie se pinta basura sin
// que ningún tipo avise. `WorldState.tasks` trae `kind`, `target_cell` y `target_poi`, y
// eso sí es contrato.
import type { Event, POI, Task, VelaEvent, WorldState } from '../types'
import { shortId } from './format'
import { TASK_KIND } from './labels'

/** Las tareas que se conocen AHORA: las del snapshot más cada `task.changed` que haya
 *  llegado después. `useEventStream` no toca `state.tasks` con los eventos (el estado es
 *  del snapshot y solo el snapshot lo pone), así que un frente que nació después de
 *  conectar no estaría en `state.tasks` y su fila saldría con el id pelado. El payload
 *  de `task.changed` ES la `Task` completa: plegarlo es copiarlo, no reimplementar
 *  `belief.apply`. Un `task.changed` posterior gana al snapshot (es más nuevo). */
export function knownTasks(state: WorldState | null, events: Event[]): Record<string, Task> {
  const out: Record<string, Task> = { ...(state?.tasks ?? {}) }
  for (const envelope of events) {
    if (envelope.type !== 'task.changed') continue
    const ev = envelope as unknown as VelaEvent
    if (ev.type !== 'task.changed') continue
    out[ev.payload.task.id] = ev.payload.task
  }
  return out
}

/** «frente 12 7», «evacuar Pueblo B», «avisar Pueblo A»… Sin la tarea en el estado (un
 *  plan que llegó antes que el snapshot) se cae al id legible, y el `hint` de la fila
 *  sigue llevando el id entero para la procedencia. */
export function taskName(
  taskId: string,
  tasks: Record<string, Task> | null | undefined,
  pois: Record<string, POI> | null | undefined,
): string {
  const task = tasks?.[taskId]
  if (!task) return shortId(taskId)
  const verb = TASK_KIND[task.kind]
  if (task.target_cell) {
    const m = /^cell_(-?\d+)_(-?\d+)$/.exec(task.target_cell)
    return m ? `${verb} ${m[1]} ${m[2]}` : `${verb} ${task.target_cell}`
  }
  if (task.target_poi) {
    const poi = pois?.[task.target_poi]
    return `${verb} ${poi?.name ?? shortId(task.target_poi)}`
  }
  return verb
}
