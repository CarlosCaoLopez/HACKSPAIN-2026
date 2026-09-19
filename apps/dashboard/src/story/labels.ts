// Los enums del contrato, en castellano y en una sola tabla.
//
// Están aquí y no repartidos por los paneles porque el mapa y el panel de cambios
// tienen que llamar a las cosas igual: si el mapa dice "fuera de servicio" y la lista
// dice "unavailable", parecen dos estados distintos.
//
// Las claves son los `Literal` de contracts, así que si P1 añade un estado, TypeScript
// obliga a traducirlo aquí.
import type { CellChanged, CellState, CivState, POIKind, UnitKind, UnitStatus } from '../types'
import type { CallOutcome, OverrideKind, TaskKind, TaskSeverity, Verb } from '../types'

export const UNIT_STATUS: Record<UnitStatus, string> = {
  idle: 'libre',
  moving: 'en ruta',
  working: 'trabajando',
  unavailable: 'fuera de servicio',
}

export const UNIT_KIND: Record<UnitKind, string> = {
  fire_truck: 'camión',
  ambulance: 'ambulancia',
  drone: 'dron',
  crew: 'brigada',
}

export const CELL_STATE: Record<CellState, string> = {
  intact: 'intacta',
  at_risk: 'en riesgo',
  burning: 'ardiendo',
  burnt: 'quemada',
  flooded: 'inundada',
  dark: 'sin luz',
}

/** Por qué cambió una celda. `extinguished` es la única que cuenta una acción nuestra
 *  —un camión la ha sofocado— y por eso es la única que no se filtra como ruido. */
export const CELL_CAUSE: Record<NonNullable<CellChanged['cause']>, string> = {
  spread: 'propagación',
  burnout: 'consumida',
  extinguished: 'apagada por camión',
  inject: 'inject',
  at_risk: 'amenazada',
}

export const CIV_STATE: Record<CivState, string> = {
  exposed: 'expuestos',
  warned: 'avisados',
  evacuating: 'evacuando',
  safe: 'a salvo',
  trapped: 'atrapados',
}

export const POI_KIND: Record<POIKind, string> = {
  village: 'pueblo',
  hospital: 'hospital',
  shelter: 'refugio',
  base: 'base',
  landmark: 'punto',
}

export const OVERRIDE_KIND: Record<OverrideKind, string> = {
  force_assignment: 'asignación forzada',
  veto_assignment: 'asignación vetada',
  assert_fact: 'hecho asertado',
  force_replan: 'replan forzado',
  set_priority: 'prioridad cambiada',
}

export const CALL_OUTCOME: Record<CallOutcome, string> = {
  answered: 'contestada',
  no_answer: 'sin respuesta',
  busy: 'ocupado',
  failed: 'fallida',
  hung_up: 'colgada',
}

/** Los cuatro verbos que acepta `sim.execute`. Si P2 añade un quinto, esto deja de
 *  compilar hasta que se traduzca, que es exactamente lo que quiero: un verbo sin
 *  traducir se pintaría en inglés en mitad de la pantalla y nadie lo vería venir. */
export const VERB: Record<Verb, string> = {
  goto: 'ir a',
  set_marker: 'marcar',
  announce: 'anunciar',
  rescue: 'rescatar',
}

/** Las tareas se nombran por lo que son, no por su id: `task_front_12_7` no se lee, y el
 *  prefijo del id es cosa del core (hoy `task_front_`, ayer `task_ext_`). */
export const TASK_KIND: Record<TaskKind, string> = {
  evacuate: 'evacuar',
  extinguish: 'frente',
  rescue: 'rescatar',
  notify: 'avisar',
  recon: 'reconocer',
  restore: 'restaurar',
}

export const SEVERITY: Record<TaskSeverity, string> = {
  low: 'baja',
  medium: 'media',
  high: 'alta',
  critical: 'crítica',
}
