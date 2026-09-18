// La cola de prioridades con su justificación, y los tres botones de override
// sobre cada tarjeta de asignación: forzar, vetar, replanificar.
import type { Plan } from '../types'
import { Panel } from '../components/Panel'

export function PriorityQueue({ plan }: { plan: Plan | null }) {
  return <Panel title="Cola de prioridad" count={plan?.assignments.length ?? 0} />
}
