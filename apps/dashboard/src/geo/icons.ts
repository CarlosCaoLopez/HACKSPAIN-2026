// El catálogo de iconos del mapa real. SPEC-008 REQ-298…300.
//
// SVG dibujado a mano, sin librería de iconos ni emoji: el emoji cambia con el sistema
// operativo del portátil y no se puede colorear con tokens, y las librerías no traen un
// camión de bomberos (su `truck` es un camión cualquiera).
//
// Los `Record<…>` son EXHAUSTIVOS sobre los tipos del contrato a propósito: si mañana
// `contracts/world.py` gana un `UnitKind`, `types.ts` lo trae y el build FALLA aquí hasta
// que tenga icono. Así ningún elemento del mapa se queda sin dibujo en silencio.
//
// Todo va en un lienzo de 24×24 con trazo de 2 px. El color no vive aquí: cada icono trae
// un `tone` y la hoja de estilos lo resuelve con los tokens `--color-vela-*` (REQ-296).
import type { CellState, CivState, POIKind, TaskKind, UnitKind } from '../types'

export type Tone = 'fire' | 'unit' | 'poi' | 'water' | 'warn' | 'call' | 'dim'

export interface IconDef {
  /** El nombre en castellano, que es lo que dice la leyenda (REQ-303). */
  label: string
  /** El interior del `<svg viewBox="0 0 24 24">`. */
  body: string
  tone: Tone
  /** Relleno sólido en vez de trazo (la llama). */
  solid?: boolean
}

export const UNIT_ICON: Record<UnitKind, IconDef> = {
  fire_truck: {
    label: 'camión de bomberos',
    tone: 'unit',
    body: '<rect x="2" y="9" width="12" height="7" rx="1"/><path d="M14 11h4.5l3 3v2H14z"/><circle cx="6" cy="17.5" r="2"/><circle cx="18" cy="17.5" r="2"/><path d="M4 6.5h8M4 6.5V9M8 6.5V9M12 6.5V9"/>',
  },
  ambulance: {
    label: 'ambulancia',
    tone: 'unit',
    body: '<path d="M2 16V8h12v8"/><path d="M14 10.5h4l3.5 3.5V16H14z"/><circle cx="6" cy="17.5" r="2"/><circle cx="18" cy="17.5" r="2"/><path d="M8 10v4M6 12h4"/>',
  },
  drone: {
    label: 'dron',
    tone: 'unit',
    body: '<rect x="9.5" y="9.5" width="5" height="5" rx="1.2"/><circle cx="5" cy="5.5" r="2.5"/><circle cx="19" cy="5.5" r="2.5"/><circle cx="5" cy="18.5" r="2.5"/><circle cx="19" cy="18.5" r="2.5"/><path d="M7 7.5l3 3M17 7.5l-3 3M7 16.5l3-3M17 16.5l-3-3"/>',
  },
  crew: {
    label: 'brigada',
    tone: 'unit',
    body: '<circle cx="8" cy="8" r="2.5"/><path d="M5 5.5h6M3 20v-3.5a5 5 0 0 1 10 0V20"/><circle cx="17" cy="9" r="2"/><path d="M15 20v-2.5a4 4 0 0 1 7 0V20"/>',
  },
}

export const POI_ICON: Record<POIKind, IconDef> = {
  village: {
    label: 'pueblo',
    tone: 'poi',
    body: '<path d="M2.5 20v-8l4.5-4.5 4.5 4.5v8z"/><path d="M12.5 20v-5.5l4-4 5 5V20z"/><path d="M6 20v-4h2v4"/>',
  },
  hospital: {
    label: 'hospital',
    tone: 'poi',
    body: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 8v8M8 12h8"/>',
  },
  shelter: {
    label: 'refugio',
    tone: 'poi',
    body: '<path d="M3 20L12 5l9 15z"/><path d="M12 20v-6"/>',
  },
  base: {
    label: 'parque de bomberos',
    tone: 'poi',
    body: '<path d="M3 20V9l9-5 9 5v11z"/><rect x="8" y="12" width="8" height="8"/><path d="M8 15h8M8 18h8"/>',
  },
  landmark: {
    label: 'lugar de referencia',
    tone: 'poi',
    body: '<path d="M6 21V4M6 5h12l-3 4 3 4H6"/>',
  },
}

/** `intact` no lleva icono: es el fondo (REQ-298). */
export const CELL_ICON: Record<Exclude<CellState, 'intact'>, IconDef> = {
  burning: {
    label: 'fuego',
    tone: 'fire',
    solid: true,
    body: '<path d="M12 2c.6 3.5 5 5.5 5 10.5a5 5 0 0 1-10 0c0-2 1-3.2 2-4.2 0 2 .8 3 2 3 0-4-1-5.5 1-9.3z"/>',
  },
  at_risk: {
    label: 'zona en riesgo',
    tone: 'warn',
    body: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.6"/>',
  },
  burnt: {
    label: 'zona quemada',
    tone: 'dim',
    body: '<path d="M8 20l1-9h6l1 9z"/><path d="M9 11c0-2 1-3 3-3s3 1 3 3"/><path d="M5 20h14"/>',
  },
  flooded: {
    label: 'zona inundada',
    tone: 'water',
    body: '<path d="M2 9c3-3 5 3 8 0s5 3 8 0 3 0 4 0M2 15c3-3 5 3 8 0s5 3 8 0 3 0 4 0"/>',
  },
  dark: {
    label: 'zona sin luz',
    tone: 'dim',
    body: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10c1 1 1 3 1 3h6s0-2 1-3a6 6 0 0 0-4-10z"/><path d="M4 4l16 16"/>',
  },
}

export const TASK_ICON: Record<TaskKind, IconDef> = {
  evacuate: {
    label: 'evacuar',
    tone: 'unit',
    body: '<path d="M14 4H6v16h8M10 12h11M17 8l4 4-4 4"/>',
  },
  extinguish: {
    label: 'extinguir',
    tone: 'fire',
    body: '<rect x="8" y="9" width="8" height="12" rx="2"/><path d="M12 9V5h4M16 5l3 2"/>',
  },
  rescue: {
    label: 'rescatar',
    tone: 'warn',
    body: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M5.6 5.6l3.6 3.6M14.8 14.8l3.6 3.6M18.4 5.6l-3.6 3.6M9.2 14.8l-3.6 3.6"/>',
  },
  notify: {
    label: 'avisar',
    tone: 'call',
    body: '<path d="M3 10v4h4l8 5V5L7 10z"/><path d="M18 9a4 4 0 0 1 0 6"/>',
  },
  recon: {
    label: 'reconocer',
    tone: 'unit',
    body: '<circle cx="7" cy="15" r="4"/><circle cx="17" cy="15" r="4"/><path d="M7 11V5h3v6M17 11V5h-3v6M10 15h4"/>',
  },
  restore: {
    label: 'reparar',
    tone: 'unit',
    body: '<path d="M14.5 6.5a4 4 0 0 0-5 5L3 18l3 3 6.5-6.5a4 4 0 0 0 5-5l-3 3-2-2z"/>',
  },
}

/** El tono de la burbuja de civiles de un pueblo, por estado (REQ-301.3). Solo se pintan
 *  `exposed` y `trapped`, pero el `Record` cubre los cinco: un estado nuevo rompe el build. */
export const CIV_TONE: Record<CivState, Tone> = {
  exposed: 'fire',
  trapped: 'warn',
  warned: 'dim',
  evacuating: 'unit',
  safe: 'dim',
}

export const ROAD_CUT_ICON: IconDef = {
  label: 'carretera cortada',
  tone: 'warn',
  body: '<path d="M3 8h18v5H3zM6 13v7M18 13v7M7 8l3 5M12 8l3 5M17 8l3 5"/>',
}

export const FIRMS_ICON: IconDef = {
  label: 'foco satélite FIRMS',
  tone: 'fire',
  body: '<rect x="8.5" y="8.5" width="7" height="7" rx="1" transform="rotate(45 12 12)"/><path d="M3 7l4 4M17 13l4 4M6 3.5l3.5 3.5M14.5 17l3.5 3.5"/>',
}

export const CALLER_ICON: IconDef = {
  label: 'persona llamando',
  tone: 'call',
  body: '<circle cx="9" cy="7.5" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4"/><path d="M17.5 8a4 4 0 0 1 0 6M20.5 5.5a7.5 7.5 0 0 1 0 11"/>',
}

export const WIND_ICON: IconDef = {
  label: 'viento',
  tone: 'dim',
  body: '<path d="M3 12h15M13 7l5 5-5 5"/>',
}

export type BadgeSize = 'unit' | 'poi' | 'small'

/** Los px de cada tamaño de insignia (REQ-299). Sin `px` para poder anclar el marcador. */
export const BADGE_PX: Record<BadgeSize, number> = { unit: 30, poi: 26, small: 22 }

const SVG_NS = 'http://www.w3.org/2000/svg'

/** El `<svg>` del icono como elemento (no como cadena): los rótulos dinámicos —el id de una
 *  unidad— se ponen con `textContent` y no hay forma de colar HTML. */
export function iconSvg(def: IconDef): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('viewBox', '0 0 24 24')
  svg.setAttribute('fill', def.solid ? 'currentColor' : 'none')
  svg.setAttribute('stroke', 'currentColor')
  svg.setAttribute('stroke-width', '2')
  svg.setAttribute('stroke-linecap', 'round')
  svg.setAttribute('stroke-linejoin', 'round')
  svg.setAttribute('aria-hidden', 'true')
  // `body` es una constante de este fichero, nunca un dato del run.
  svg.innerHTML = def.body
  return svg
}

/** La insignia redonda blanca con el icono dentro (REQ-299). El color lo pone la clase de
 *  tono; el estado (borde, pulso, aspa) lo ponen las clases que añade quien la usa. */
export function badgeElement(def: IconDef, size: BadgeSize): HTMLDivElement {
  const badge = document.createElement('div')
  badge.className = `vela-badge vela-size-${size} vela-tone-${def.tone}`
  badge.appendChild(iconSvg(def))
  return badge
}
