// Reexporta el tipo del grafo para que `explain.ts` no importe de `engine/` hacia
// arriba y hacia abajo a la vez. Es un alias, no una copia.
export type { RoadGraph } from './engine/graph'
