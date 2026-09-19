// La rejilla de celdas del escenario en coordenadas del mundo (x, z) de Minecraft.
//
// Es lo poco que sobrevivió del mapa antiguo de SPEC-006 (borrado en SPEC-008): saber
// en qué celda cae un punto. La proyección a coordenadas reales vive en `anchor.ts`.
export interface Geo {
  origin: [number, number]
  cellSize: number
}

/** La celda que contiene un punto del mundo.
 *
 *  Es una consulta geométrica sobre el estado vigente, no una predicción: saber que un
 *  pueblo está DENTRO de una celda que arde es mirar; saber que el fuego LLEGARÁ es
 *  propagación, y REQ-072 lo deja a P1. */
export function cellIdAt(x: number, z: number, geo: Geo): string {
  const cx = Math.floor((x - geo.origin[0]) / geo.cellSize)
  const cz = Math.floor((z - geo.origin[1]) / geo.cellSize)
  return `cell_${cx}_${cz}`
}
