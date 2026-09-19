// La imagen de satélite del mapa, generada. SPEC-006 REQ-214…217.
//
// No hay teselas: el mundo es Minecraft y sus coordenadas no existen fuera de él. Y el
// escenario tampoco trae relieve, vegetación ni agua. Así que esto es una IMAGEN
// ILUSTRATIVA, determinista (misma semilla y geometría ⇒ mismos bytes) y sin semántica:
// no dibuja agua, cortafuegos ni «bosque denso» que se pueda leer como dato del escenario
// (REQ-216). La única información real que lleva son los edificios de los POIs, que salen
// de `poi.x/z`. Por eso el mapa enseña el chip «relieve ilustrativo» (REQ-217).
//
// Está partido en dos a propósito: `terrainPixels` es una función pura sobre un buffer y
// se puede ejecutar y comparar sin navegador; `renderTerrain` es solo el canvas que lo
// convierte en data URL. Es el ÚNICO fichero de `map/` con colores literales (REQ-230): la
// imagen no es semántica, y meterla en `@theme` sería inflar los tokens con tonos de tierra.
import type { POI, Scenario } from '../types'
import { boxOf, type Box } from './project'

type RGB = readonly [number, number, number]

/** Píxeles por bloque de Minecraft, y tope del lado mayor: por encima de esto el coste de
 *  generarla (REQ-215, < 500 ms) no compensa lo que se ve desde el fondo de la sala. */
const PX_PER_BLOCK = 3
const MAX_SIDE = 1200

const FOREST: RGB = [34, 58, 33]
const WOOD: RGB = [52, 84, 45]
const SCRUB: RGB = [96, 112, 60]
const DRY: RGB = [146, 132, 86]
const BARE: RGB = [170, 152, 112]

/** El encuadre que cubre la imagen: los puntos conocidos del escenario con margen
 *  generoso. El `viewBox` del mapa solo crece (project.ts) y no debe salirse de aquí. */
export function terrainBox(layer: Scenario): Box {
  const points = [
    ...layer.waypoints.map((wp) => ({ x: wp.x, z: wp.z })),
    ...layer.pois.map((p) => ({ x: p.x, z: p.z })),
    ...layer.units.map((u) => ({ x: u.x, z: u.z })),
  ]
  const cell = layer.hazard.cell_size
  const tight = boxOf(points, cell * 2)
  const padX = tight.width * 0.3
  const padZ = tight.height * 0.3
  return {
    minX: tight.minX - padX,
    minZ: tight.minZ - padZ,
    width: tight.width + padX * 2,
    height: tight.height + padZ * 2,
  }
}

/** mulberry32: un RNG de una línea con semilla. `Math.random` haría la imagen distinta en
 *  cada carga y REQ-214 pide byte a byte. */
function rng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Valor de la retícula en (ix, iz), en [0, 1). */
function lattice(ix: number, iz: number, seed: number): number {
  let h = Math.imul(ix, 374761393) ^ Math.imul(iz, 668265263) ^ Math.imul(seed, 2246822519)
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

function smooth(t: number): number {
  return t * t * (3 - 2 * t)
}

function valueNoise(x: number, z: number, seed: number): number {
  const ix = Math.floor(x)
  const iz = Math.floor(z)
  const fx = smooth(x - ix)
  const fz = smooth(z - iz)
  const a = lattice(ix, iz, seed)
  const b = lattice(ix + 1, iz, seed)
  const c = lattice(ix, iz + 1, seed)
  const d = lattice(ix + 1, iz + 1, seed)
  return a + (b - a) * fx + (c - a) * fz + (a - b - c + d) * fx * fz
}

/** Ruido fractal: cada octava, el doble de frecuencia y la mitad de amplitud. */
function fbm(x: number, z: number, seed: number, octaves: number): number {
  let sum = 0
  let amp = 0.5
  let norm = 0
  let f = 1
  for (let i = 0; i < octaves; i++) {
    sum += amp * valueNoise(x * f, z * f, seed + i * 101)
    norm += amp
    amp /= 2
    f *= 2
  }
  return sum / norm
}

function mix(a: RGB, b: RGB, t: number): RGB {
  const k = Math.min(1, Math.max(0, t))
  return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]
}

/** Vegetación por «humedad» (0 = seco, 1 = umbrío). Cinco tonos y sin bordes duros: una
 *  rampa continua, no un mapa de usos del suelo. */
function ground(moisture: number): RGB {
  if (moisture > 0.75) return mix(WOOD, FOREST, (moisture - 0.75) / 0.25)
  if (moisture > 0.55) return mix(SCRUB, WOOD, (moisture - 0.55) / 0.2)
  if (moisture > 0.35) return mix(DRY, SCRUB, (moisture - 0.35) / 0.2)
  return mix(BARE, DRY, moisture / 0.35)
}

export interface TerrainImage {
  width: number
  height: number
  /** `ArrayBuffer` y no `ArrayBufferLike`: `ImageData` no acepta un `SharedArrayBuffer`. */
  data: Uint8ClampedArray<ArrayBuffer>
}

function fillRect(
  img: TerrainImage,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  color: RGB,
  alpha = 1,
): void {
  const { width, height, data } = img
  const xa = Math.max(0, Math.floor(x0))
  const xb = Math.min(width, Math.ceil(x1))
  const ya = Math.max(0, Math.floor(y0))
  const yb = Math.min(height, Math.ceil(y1))
  for (let y = ya; y < yb; y++) {
    for (let x = xa; x < xb; x++) {
      const i = (y * width + x) * 4
      data[i] = (data[i] ?? 0) * (1 - alpha) + color[0] * alpha
      data[i + 1] = (data[i + 1] ?? 0) * (1 - alpha) + color[1] * alpha
      data[i + 2] = (data[i + 2] ?? 0) * (1 - alpha) + color[2] * alpha
    }
  }
}

const ROOFS: RGB[] = [
  [168, 92, 70],
  [150, 78, 60],
  [186, 176, 160],
  [128, 122, 116],
  [196, 130, 92],
]
const SHADOW: RGB = [18, 20, 16]

/** Un edificio con su sombra hacia el sureste, que es lo que lo hace leer como cenital. */
function building(img: TerrainImage, cx: number, cy: number, w: number, h: number, roof: RGB) {
  fillRect(img, cx - w / 2 + 1.6, cy - h / 2 + 1.6, cx + w / 2 + 1.6, cy + h / 2 + 1.6, SHADOW, 0.45)
  fillRect(img, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, roof)
}

/** Los POIs son lo único real de la imagen (REQ-216): pueblo = racimo de casas, hospital y
 *  refugio = un bloque grande, base = patio con dos naves. Se sitúan desde `poi.x/z`. */
function stampPoi(img: TerrainImage, poi: POI, box: Box, scale: number, rand: () => number) {
  const cx = (poi.x - box.minX) * scale
  const cy = (poi.z - box.minZ) * scale
  const b = scale // píxeles por bloque
  // Claro de tierra alrededor: un pueblo no está en mitad del monte.
  const r = (poi.kind === 'village' ? 15 : 10) * b
  const { width, height, data } = img
  for (let y = Math.max(0, Math.floor(cy - r)); y < Math.min(height, Math.ceil(cy + r)); y++) {
    for (let x = Math.max(0, Math.floor(cx - r)); x < Math.min(width, Math.ceil(cx + r)); x++) {
      const d = Math.hypot(x - cx, y - cy) / r
      if (d >= 1) continue
      const t = (1 - d) * 0.75
      const i = (y * width + x) * 4
      data[i] = (data[i] ?? 0) * (1 - t) + BARE[0] * t
      data[i + 1] = (data[i + 1] ?? 0) * (1 - t) + BARE[1] * t
      data[i + 2] = (data[i + 2] ?? 0) * (1 - t) + BARE[2] * t
    }
  }
  if (poi.kind === 'village') {
    for (let n = 0; n < 16; n++) {
      const ang = rand() * Math.PI * 2
      const dist = Math.sqrt(rand()) * 11 * b
      building(
        img,
        cx + Math.cos(ang) * dist,
        cy + Math.sin(ang) * dist,
        (2.2 + rand() * 2.2) * b,
        (2 + rand() * 1.8) * b,
        ROOFS[Math.floor(rand() * ROOFS.length)] ?? ROOFS[0]!,
      )
    }
  } else if (poi.kind === 'hospital') {
    building(img, cx, cy, 13 * b, 8 * b, [222, 226, 230])
    building(img, cx + 9 * b, cy - 1 * b, 4 * b, 4 * b, [186, 194, 204])
  } else if (poi.kind === 'shelter') {
    building(img, cx, cy, 10 * b, 7 * b, [120, 138, 156])
  } else if (poi.kind === 'base') {
    fillRect(img, cx - 9 * b, cy - 6 * b, cx + 9 * b, cy + 6 * b, [150, 150, 146], 0.9)
    building(img, cx - 4 * b, cy - 2 * b, 6 * b, 4 * b, [176, 70, 60])
    building(img, cx + 4 * b, cy + 2 * b, 6 * b, 4 * b, [120, 122, 128])
  } else {
    building(img, cx, cy, 4 * b, 4 * b, [186, 176, 160])
  }
}

/** La imagen entera como buffer RGBA. Pura: no toca el DOM. */
export function terrainPixels(layer: Scenario): { image: TerrainImage; box: Box } {
  const box = terrainBox(layer)
  const scale = Math.min(PX_PER_BLOCK, MAX_SIDE / Math.max(box.width, box.height))
  const width = Math.max(1, Math.round(box.width * scale))
  const height = Math.max(1, Math.round(box.height * scale))
  const seed = layer.seed | 0
  const data = new Uint8ClampedArray(new ArrayBuffer(width * height * 4))

  // Relieve primero, en una rejilla aparte: el sombreado necesita los vecinos.
  const elev = new Float32Array(width * height)
  const moist = new Float32Array(width * height)
  const wl = 60 * scale // longitud de onda del relieve: ~60 bloques
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      elev[y * width + x] = fbm(x / wl, y / wl, seed, 5)
      moist[y * width + x] = fbm(x / (wl * 0.55), y / (wl * 0.55), seed + 7919, 5)
    }
  }

  const grain = rng(seed ^ 0x9e3779b9)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x
      const e = elev[i] ?? 0
      // Luz al noroeste: la pendiente que mira al sureste queda en sombra.
      const dx = (elev[y * width + Math.min(width - 1, x + 1)] ?? e) - (elev[y * width + Math.max(0, x - 1)] ?? e)
      const dz = (elev[Math.min(height - 1, y + 1) * width + x] ?? e) - (elev[Math.max(0, y - 1) * width + x] ?? e)
      const shade = Math.min(1.35, Math.max(0.55, 1 + (dx + dz) * 16))
      // Lo alto es más seco: la humedad baja con la cota, sin que sea un dato de nada.
      const m = (moist[i] ?? 0.5) * 1.15 - (e - 0.5) * 0.6
      const c = ground(m)
      // Copas: ruido de ~3 bloques que solo pesa donde hay monte. Es lo que convierte una
      // mancha verde en algo que se lee como imagen aérea. Sigue sin significar nada.
      const canopy = valueNoise(x / (3 * scale), y / (3 * scale), seed + 31337)
      const leaf = m > 0.5 ? 0.78 + canopy * 0.44 : 0.92 + canopy * 0.16
      const noise = leaf * (0.95 + grain() * 0.1)
      const o = i * 4
      data[o] = c[0] * shade * noise
      data[o + 1] = c[1] * shade * noise
      data[o + 2] = c[2] * shade * noise
      data[o + 3] = 255
    }
  }

  const image: TerrainImage = { width, height, data }
  const rand = rng(seed ^ 0x51ed270b)
  for (const poi of layer.pois) stampPoi(image, poi, box, scale, rand)
  return { image, box }
}

/** Caché por escenario (REQ-215): se genera una vez por run, no por render. */
const cache = new Map<string, { url: string; box: Box }>()

export function renderTerrain(layer: Scenario): { url: string; box: Box } | null {
  const hit = cache.get(layer.id)
  if (hit) return hit
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  // Sin canvas (un entorno raro) el mapa sigue funcionando sobre fondo liso: se degrada,
  // no se rompe.
  if (!ctx) return null
  const { image, box } = terrainPixels(layer)
  canvas.width = image.width
  canvas.height = image.height
  ctx.putImageData(new ImageData(image.data, image.width, image.height), 0, 0)
  const entry = { url: canvas.toDataURL('image/png'), box }
  cache.set(layer.id, entry)
  return entry
}
