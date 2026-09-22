// La barra de mandos: el fuego, el viento y el reloj.
//
// Dos cosas que hay que tener delante todo el rato para no invertir un signo:
//   · `bearing_deg` es de dónde VIENE el viento. El fuego se empuja hacia
//     `bearing + 180`. Por eso los botones se etiquetan por dónde va el FUEGO.
//   · el norte es −z, así que «abajo en pantalla» es el sur.
import { useMemo } from 'react'

import type { Scenario } from '../types'

import { bearingDeg } from './engine/geom'
import type { Frame } from './engine/loop'
import { SPEEDS, type SimControls as Controls, type Speed } from './useSimEngine'

/** Las ocho direcciones, etiquetadas por dónde va el FUEGO. Las cuatro cardinales son
 *  las grandes; las diagonales hacen falta porque desde el foco del escenario Pueblo A
 *  y Pueblo B solo distan 35°, y «este» abre un cono que cubre a los dos. */
const DIRECTIONS: Array<{ push: number; arrow: string; name: string; cardinal: boolean }> = [
  { push: 0, arrow: '↑', name: 'norte', cardinal: true },
  { push: 45, arrow: '↗', name: 'noreste', cardinal: false },
  { push: 90, arrow: '→', name: 'este', cardinal: true },
  { push: 135, arrow: '↘', name: 'sureste', cardinal: false },
  { push: 180, arrow: '↓', name: 'sur', cardinal: true },
  { push: 225, arrow: '↙', name: 'suroeste', cardinal: false },
  { push: 270, arrow: '←', name: 'oeste', cardinal: true },
  { push: 315, arrow: '↖', name: 'noroeste', cardinal: false },
]

const COMPASS = ['norte', 'noreste', 'este', 'sureste', 'sur', 'suroeste', 'oeste', 'noroeste']

function compassName(deg: number): string {
  return COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8] ?? ''
}

/** El centroide del fuego, que es desde donde se mide a quién amenaza cada dirección. */
function fireCentroid(frame: Frame, scenario: Scenario): [number, number] | null {
  const size = scenario.hazard.cell_size
  let x = 0
  let z = 0
  let n = 0
  for (const cell of frame.state.cells.values()) {
    if (cell.state !== 'burning') continue
    x += scenario.origin[0] + (cell.cx + 0.5) * size
    z += scenario.origin[1] + (cell.cz + 0.5) * size
    n++
  }
  return n === 0 ? null : [x / n, z / n]
}

export function SimControls({
  frame,
  scenario,
  speed,
  running,
  answersPhone,
  tool,
  controls,
  onTool,
}: {
  frame: Frame
  scenario: Scenario
  speed: Speed
  running: boolean
  answersPhone: boolean
  tool: 'none' | 'ignite' | 'douse'
  controls: Controls
  onTool: (t: 'none' | 'ignite' | 'douse') => void
}) {
  const wind = frame.state.wind
  const push = (wind.bearing_deg + 180) % 360
  // Las celdas que arden AHORA, que es el número que se mira mientras corre. Los focos
  // puestos a mano no bajan nunca (se consumen en 45 s), así que solos no dicen nada.
  let burning = 0
  for (const cell of frame.state.cells.values()) if (cell.state === 'burning') burning++
  const centroid = useMemo(() => fireCentroid(frame, scenario), [frame, scenario])

  /** Qué POI cae en el cono de esa dirección desde el fuego de ahora. Se deriva del
   *  estado: si el frente se mueve, las etiquetas cambian solas. */
  const threatens = (pushDeg: number): string => {
    if (!centroid) return ''
    const hits = scenario.pois
      .filter((p) => p.kind === 'village' || p.kind === 'shelter' || p.kind === 'hospital')
      .filter((p) => {
        const b = bearingDeg(centroid[0], centroid[1], p.x, p.z)
        const gap = Math.abs((((b - pushDeg + 180) % 360) + 360) % 360 - 180)
        return gap <= 22.5
      })
      .map((p) => p.name)
    return hits.join(', ')
  }

  const setPush = (pushDeg: number) => {
    controls.setWind({
      bearing_deg: (pushDeg + 180) % 360,
      // Con velocidad cero una flecha no haría nada y parecería rota.
      speed: wind.speed > 0 ? wind.speed : 1.2,
    })
  }

  const aimAt = (poiId: string) => {
    const poi = scenario.pois.find((p) => p.id === poiId)
    if (!poi || !centroid) return
    setPush(bearingDeg(centroid[0], centroid[1], poi.x, poi.z))
  }

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2 text-sm">
      {/* --- reloj --- */}
      <div className="flex items-center gap-1.5">
        {/* El botón que hay que pulsar, y tiene que parecerlo: relleno y en el color de
            acento mientras está parado. Con el mismo borde gris que los otros doce
            botones de la barra no se encontraba. */}
        <button
          type="button"
          onClick={controls.toggle}
          className={`h-9 rounded-md px-4 font-medium ${
            running
              ? 'border border-vela-edge text-vela-ink hover:border-vela-ink'
              : 'bg-vela-accent text-white hover:brightness-110'
          }`}
        >
          {running ? '⏸ Pausa' : frame.tSim > 0 ? '▶ Seguir' : '▶ Empezar'}
        </button>
        {!running && (
          <button
            type="button"
            onClick={controls.step}
            title="Un segundo de simulación"
            className="h-9 rounded-md border border-vela-edge px-2 text-vela-dim hover:border-vela-ink hover:text-vela-ink"
          >
            +1 s
          </button>
        )}
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => controls.setSpeed(s)}
            className={`h-9 rounded-md border px-2 tabular-nums ${
              s === speed
                ? 'border-vela-ink bg-vela-ink text-white'
                : 'border-vela-edge text-vela-dim hover:border-vela-ink hover:text-vela-ink'
            }`}
          >
            ×{s}
          </button>
        ))}
        <button
          type="button"
          onClick={controls.reset}
          title="Vuelve al principio con la misma semilla: el mismo run, bit a bit"
          className="h-9 rounded-md border border-vela-edge px-2 text-vela-dim hover:border-vela-ink hover:text-vela-ink"
        >
          ⟲
        </button>
      </div>

      <Sep />

      {/* --- fuego --- */}
      <div className="flex items-center gap-1.5">
        <Toggle on={tool === 'ignite'} onClick={() => onTool(tool === 'ignite' ? 'none' : 'ignite')}>
          🔥 Poner foco
        </Toggle>
        <Toggle on={tool === 'douse'} onClick={() => onTool(tool === 'douse' ? 'none' : 'douse')}>
          💧 Quitar
        </Toggle>
        <span className="pl-1 text-vela-dim">
          {frame.ignitions.length === 0
            ? 'sin focos: clica el mapa'
            : `${frame.ignitions.length} ${frame.ignitions.length === 1 ? 'foco' : 'focos'} · ${burning} ardiendo`}
        </span>
      </div>

      <Sep />

      {/* --- viento --- */}
      <div className="flex items-center gap-1">
        {DIRECTIONS.map((d) => {
          const on = Math.abs(((push - d.push + 180 + 360) % 360) - 180) < 22.5
          const who = threatens(d.push)
          return (
            <button
              key={d.push}
              type="button"
              onClick={() => setPush(d.push)}
              title={`El fuego va hacia el ${d.name}${who ? ` · ${who}` : ''} (viento ${Math.round((d.push + 180) % 360)}°)`}
              className={`flex items-center justify-center rounded-md border leading-none ${
                d.cardinal ? 'h-9 w-9 text-lg' : 'h-7 w-7 text-sm'
              } ${
                on
                  ? 'border-vela-ink bg-vela-ink text-white'
                  : 'border-vela-edge text-vela-dim hover:border-vela-ink hover:text-vela-ink'
              }`}
            >
              {d.arrow}
            </button>
          )
        })}
      </div>

      {/* Los atajos: es lo que hace que «primero a Pueblo A y luego a Pueblo B» sea un
          clic. Por debajo solo fijan el rumbo que apunta a ese pueblo. */}
      <div className="flex items-center gap-1">
        {scenario.pois
          .filter((p) => p.kind === 'village')
          .map((p) => (
            <button
              key={p.id}
              type="button"
              disabled={!centroid}
              onClick={() => aimAt(p.id)}
              title={centroid ? `Apuntar el viento a ${p.name}` : 'Enciende un foco primero'}
              className="rounded-md border border-vela-edge px-2 py-1 text-vela-dim hover:border-vela-ink hover:text-vela-ink disabled:opacity-40"
            >
              → {p.name}
            </button>
          ))}
      </div>

      <label className="flex items-center gap-2 text-vela-dim">
        fuerza
        <input
          type="range"
          min={0}
          max={3}
          step={0.1}
          value={wind.speed}
          onChange={(e) =>
            controls.setWind({ bearing_deg: wind.bearing_deg, speed: Number(e.target.value) })
          }
          className="w-24"
        />
        <span className="w-20 tabular-nums">{wind.speed.toFixed(1)} c/min</span>
      </label>

      <Sep />

      {/* --- injects del guion --- */}
      <div className="flex items-center gap-1.5">
        <Small onClick={() => controls.inject('wind_shift', { bearing: 300, speed: 1.8 })}>
          giro de viento
        </Small>
        <Small
          onClick={() =>
            controls.inject('road_cut', { edge: 'road:wp_sur_01-wp_sur_02', cause: 'árbol caído' })
          }
        >
          cortar pista sur
        </Small>
        <Small onClick={() => controls.inject('unit_failure', { unit: 'unit_truck2', reason: 'avería de bomba' })}>
          avería camión 2
        </Small>
        <Toggle on={!answersPhone} onClick={() => controls.setAnswersPhone(!answersPhone)}>
          {answersPhone ? '☎ contestan' : '☎ no contestan'}
        </Toggle>
      </div>

      {/* La lectura en vivo, con los tres marcos a la vez para que la ambigüedad del
          rumbo no exista nunca. */}
      <p className="w-full text-vela-dim">
        viento {Math.round(wind.bearing_deg)}° (viene del {compassName(wind.bearing_deg)}) · empuja
        al {Math.round(push)}° ({compassName(push)}) · {wind.speed.toFixed(1)} celdas/min
        {centroid && threatens(push) ? ` · el fuego va hacia ${threatens(push)}` : ''}
      </p>
    </div>
  )
}

function Sep() {
  return <span className="h-6 w-px bg-vela-edge" aria-hidden />
}

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`h-9 rounded-md border px-3 ${
        on
          ? 'border-vela-ink bg-vela-ink text-white'
          : 'border-vela-edge text-vela-dim hover:border-vela-ink hover:text-vela-ink'
      }`}
    >
      {children}
    </button>
  )
}

function Small({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-9 rounded-md border border-vela-edge px-2 text-vela-dim hover:border-vela-ink hover:text-vela-ink"
    >
      {children}
    </button>
  )
}
