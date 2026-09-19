// Los huecos de una llamada: qué sabe el sistema y qué no, mientras el vecino habla.
//
// Jev puntúa cada 5 s y cada campo pasa por tres estados que se tienen que distinguir de
// un vistazo, porque son la regla 4 hecha pantalla:
//
// - **hueco gris** (`open` / `asked`): aún no se sabe.
// - **sólido** (`observed`): lo dijo quien llama y Jev lo eligió con confianza sobre el umbral.
// - **gris cursiva** (`assumed_default`): no dio tiempo a preguntar y el LLM lo rellenó.
//   Es una hipótesis que el sistema intenta falsar, NO un hecho: nunca se pinta sólido.
//
// Hay una cuarta vía, y es el beat 4:25: `location_hint` se queda gris porque el vecino
// no sabe decir dónde está, y se pone **sólido** cuando llega el pin de Telegram anclado
// a un POI (o un `poi:<id>:confirmed` observado). No lo pone Jev: lo pone `calls.ts`
// (`Call.located`) y aquí se pinta con su procedencia («· Telegram») para que nadie
// confunda un pin con una respuesta de la llamada.
import type { CallCompleteness, FieldCompleteness } from '../types'
import type { Located } from '../story/calls'
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

/** Sin color (SPEC-009 REQ-323): la regla 4 se distingue por relleno, borde y cursiva. */
function chipClass(status: FieldCompleteness['status']): string {
  switch (status) {
    case 'observed':
      return 'border-vela-ink bg-vela-ink text-vela-panel'
    case 'assumed_default':
      return 'border-vela-edge-bright bg-vela-bg text-vela-dim italic'
    case 'asked':
      return 'border-dashed border-vela-dim text-vela-dim'
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

/** Los campos de Jev con el hueco de ubicación cerrado desde fuera, si procede. Un
 *  `observed` de Jev gana: lo dijo el vecino y se eligió con confianza; el pin solo
 *  rellena lo que la voz dejó abierto. Sin vector de Jev (un chat de Telegram no pasa
 *  por Jev), la ubicación es el único campo que hay, y se pinta igual. */
function withLocated(fields: FieldCompleteness[], located: Located | null): FieldCompleteness[] {
  if (!located) return fields
  const solid: FieldCompleteness = {
    key: 'location_hint',
    status: 'observed',
    value: located.poiId,
    confidence: null,
  }
  const idx = fields.findIndex((f) => f.key === 'location_hint')
  if (idx === -1) return [solid, ...fields]
  if (fields[idx]!.status === 'observed') return fields
  return fields.map((f, i) => (i === idx ? solid : f))
}

const VIA: Record<Located['via'], string> = { telegram: 'Telegram', fact: 'confirmado' }

export function CompletenessPanel({
  completeness,
  located = null,
}: {
  completeness: CallCompleteness | null
  located?: Located | null
}) {
  if (!completeness && !located) return null
  const budget_s = completeness?.budget_s ?? null
  const elapsed_s = completeness?.elapsed_s ?? 0
  const fields = withLocated(completeness?.fields ?? [], located)
  const open = fields.filter((f) => f.status === 'open' || f.status === 'asked').length
  // Verde solo si todo lo dijo quien llama (REQ-321): un campo asumido no es «completa».
  const complete = fields.length > 0 && fields.every((f) => f.status === 'observed')
  const ratio = budget_s ? Math.min(1, elapsed_s / budget_s) : 0
  // Qué chip viene del pin y no de Jev: solo si el pin fue quien lo puso en sólido.
  const viaPin =
    located && !completeness?.fields.some((f) => f.key === 'location_hint' && f.status === 'observed')

  return (
    <div className="mt-1.5 text-sm" aria-label="Completitud de la llamada">
      <p className={complete ? 'text-vela-good' : 'text-vela-dim'}>
        Completitud · {complete ? 'completa' : open > 0 ? `${open} sin resolver` : 'cerrada'}
      </p>
      <ul className="mt-1 flex flex-wrap gap-1">
        {fields.map((field) => (
          <li
            key={field.key}
            className={`rounded-md border px-1.5 py-0.5 text-xs ${chipClass(field.status)}`}
          >
            <span className="opacity-80">{LABEL[field.key] ?? field.key}</span>
            {field.value ? `: ${shown(field)}` : ''}
            {field.key === 'location_hint' && viaPin && located
              ? ` · ${VIA[located.via]}`
              : suffix(field)}
          </li>
        ))}
      </ul>
      {/* El reloj sale de la gravedad: no se retiene una ambulancia rellenando un
          cuestionario. Sin `budget_s` aún no se sabe cuánto tiempo hay. Agotado, en rojo. */}
      {budget_s != null && (
        <div className="mt-1 flex items-center gap-2 text-xs text-vela-dim">
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-vela-edge">
            <span
              className={`block h-full ${ratio >= 1 ? 'bg-vela-replan' : 'bg-vela-ink'}`}
              style={{ width: `${ratio * 100}%` }}
            />
          </span>
          <span className={`tabular-nums ${ratio >= 1 ? 'text-vela-replan' : ''}`}>
            {Math.round(elapsed_s)} / {Math.round(budget_s)} s
          </span>
        </div>
      )}
    </div>
  )
}
