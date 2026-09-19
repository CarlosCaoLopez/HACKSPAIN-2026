// Los huecos de una llamada: qué sabe el sistema y qué no, mientras el vecino habla.
//
// Jev puntúa cada 5 s y cada campo pasa por tres estados que se tienen que distinguir de
// un vistazo, porque son la regla 4 hecha pantalla:
//
// - **hueco gris** (`open` / `asked`): aún no se sabe.
// - **sólido** (`observed`): lo dijo quien llama y Jev lo eligió con confianza sobre el umbral.
// - **gris cursiva** (`assumed_default`): no dio tiempo a preguntar y el LLM lo rellenó.
//   Es una hipótesis que el sistema intenta falsar, NO un hecho: nunca se pinta sólido.
import type { CallCompleteness, FieldCompleteness } from '../types'
import { pct, shortId } from '../story/format'

const LABEL: Record<string, string> = {
  location_hint: 'dónde',
  road_blocked: 'carretera cortada',
  people_immobile: 'sin poder moverse',
  urgency: 'gravedad',
}

const URGENCY: Record<string, string> = { low: 'baja', medium: 'media', critical: 'crítica' }

/** El valor tal cual se lee: ids del escenario sin prefijo, `5plus` como «5 o más». */
function shown(field: FieldCompleteness): string {
  const value = field.value
  if (!value) return ''
  if (field.key === 'urgency') return URGENCY[value] ?? value
  if (field.key === 'road_blocked') return value.replace(/^road:/, '').replace(/_/g, ' ')
  if (field.key === 'people_immobile') return value === '5plus' ? '5 o más' : value
  return shortId(value)
}

function chipClass(status: FieldCompleteness['status']): string {
  switch (status) {
    case 'observed':
      return 'border-vela-ink bg-vela-ink text-vela-panel'
    case 'assumed_default':
      return 'border-vela-edge-bright bg-vela-bg text-vela-dim italic'
    case 'asked':
      return 'border-dashed border-vela-call text-vela-dim'
    default:
      return 'border-dashed border-vela-edge-bright text-vela-dim'
  }
}

function suffix(field: FieldCompleteness): string {
  switch (field.status) {
    case 'observed':
      return field.confidence != null ? ` · ${pct(field.confidence)}` : ''
    case 'assumed_default':
      return ' · asumido'
    case 'asked':
      return ' · preguntando'
    default:
      return ''
  }
}

export function CompletenessPanel({ completeness }: { completeness: CallCompleteness }) {
  const { fields, budget_s, elapsed_s } = completeness
  const open = fields.filter((f) => f.status === 'open' || f.status === 'asked').length
  const ratio = budget_s ? Math.min(1, elapsed_s / budget_s) : 0

  return (
    <div className="mt-1.5" aria-label="Completitud de la llamada">
      <p className="text-xs font-bold tracking-wide text-vela-dim">
        COMPLETITUD{open > 0 ? ` · ${open} sin resolver` : ' · cerrada'}
      </p>
      <ul className="mt-1 flex flex-wrap gap-1">
        {fields.map((field) => (
          <li
            key={field.key}
            className={`rounded-md border px-1.5 py-0.5 text-xs ${chipClass(field.status)}`}
          >
            <span className="opacity-80">{LABEL[field.key] ?? field.key}</span>
            {field.value ? `: ${shown(field)}` : ''}
            {suffix(field)}
          </li>
        ))}
      </ul>
      {/* El reloj sale de la gravedad: no se retiene una ambulancia rellenando un
          cuestionario. Sin `budget_s` aún no se sabe cuánto tiempo hay. */}
      {budget_s != null && (
        <div className="mt-1 flex items-center gap-2 text-xs text-vela-dim">
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-vela-edge">
            <span
              className={`block h-full ${ratio >= 1 ? 'bg-vela-warn' : 'bg-vela-accent'}`}
              style={{ width: `${ratio * 100}%` }}
            />
          </span>
          <span className="tabular-nums">
            {Math.round(elapsed_s)} / {Math.round(budget_s)} s
          </span>
        </div>
      )}
    </div>
  )
}
