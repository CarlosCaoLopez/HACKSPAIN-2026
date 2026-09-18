// El mapa 2D: unidades, POIs, celdas y flechas de asignación.
// Las coordenadas llegan en (x, z) del mundo Minecraft; la proyección a píxeles
// es cosa de este panel y de nadie más.
//
// Si Minecraft falla, este panel es la demo (plan B nivel 3).
import type { Plan, WorldState } from '../types'

export function MapPanel({ state, plan }: { state: WorldState | null; plan: Plan | null }) {
  throw new Error('not implemented')
}
