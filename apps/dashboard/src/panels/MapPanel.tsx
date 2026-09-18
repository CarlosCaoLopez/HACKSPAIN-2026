// El mapa 2D: unidades, POIs, celdas y flechas de asignación.
// Las coordenadas llegan en (x, z) del mundo Minecraft; la proyección a píxeles
// es cosa de este panel y de nadie más.
//
// Si Minecraft falla, este panel es la demo (plan B nivel 3).
import type { Plan, WorldState } from '../types'
import { Panel } from '../components/Panel'

export function MapPanel({ state, plan }: { state: WorldState | null; plan: Plan | null }) {
  const units = state ? Object.keys(state.units).length : 0
  const routes = plan?.assignments.length ?? 0
  return (
    <Panel title="Mapa" count={units}>
      <p>
        sin datos · {units} unidades · {routes} asignaciones
      </p>
    </Panel>
  )
}
