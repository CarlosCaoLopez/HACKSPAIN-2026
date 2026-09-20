# Guía de contribución

Muchas gracias por dedicar un rato a leer esto. De verdad significa mucho. Taiafox es un proyecto
pequeño nacido en HackSpain 2026, y que estés aquí planteándote contribuir es algo que no damos
por sentado.

## Qué esperar de nosotros

Somos cuatro personas mantenedoras y esto lo hacemos en nuestro tiempo libre y entre otras
obligaciones. **Revisar y resolver una contribución puede tardar hasta un mes**, y ese plazo no es
fijo: puede alargarse si coincide con exámenes o entregas. Si pasa un mes sin respuesta, escribe
un comentario en tu PR; probablemente se nos haya pasado, no es falta de interés.

## Preparar el entorno

Necesitas Python 3.12 exacto, [`uv`](https://docs.astral.sh/uv/), `pnpm` y, para el mundo de
Minecraft, Java.

1. **Haz un fork** del repositorio y clónalo:
   ```bash
   git clone https://github.com/<tu-usuario>/HACKSPAIN-2026.git
   cd HACKSPAIN-2026
   ```
2. **Instala las dependencias** (Python con `uv`, dashboard con `pnpm`):
   ```bash
   make install
   ```
3. **Copia la configuración** y rellena solo las claves que vayas a usar. Sin clave de un
   servicio externo, esa pieza se degrada y se anota; no hace falta para desarrollar:
   ```bash
   cp .env.example .env
   ```
4. **Comprueba que todo está limpio** antes de tocar nada:
   ```bash
   make check
   ```
5. **Crea una rama con un nombre descriptivo**, por ejemplo `fix/verificador-cero-unidades` o
   `feat/dashboard-mapa-calor`.

Cada pieza se puede desarrollar sola con el resto simulado: `make dev-core`, `make dev-sim`,
`make dev-voice` o `make dev-dash`.

## Estilo de código

Seguimos lo que el repositorio ya tiene configurado, en `pyproject.toml`:

- **Python**: `ruff` (líneas de 90 caracteres, Python 3.12) y `mypy`, estricto en `contracts`.
- **Dashboard**: TypeScript con el `tsconfig.json` del proyecto. Es el único sitio con TypeScript.
- **Tipos del dashboard**: `apps/dashboard/src/types.ts` se genera con `make types`; no se edita a
  mano.
- **Comentarios**: explican el porqué, no el qué.
- **Errores**: nada de `except: pass`. Un servicio caído se degrada y se anota.

## Reglas de diseño que no se rompen

Están detalladas en [`CLAUDE.md`](CLAUDE.md). Las tres que más se olvidan:

1. El LLM nunca toca Minecraft ni asigna recursos: produce una política, y el solver el plan.
2. Todos importan de `contracts`; nadie importa del paquete de otro. Se habla por el bus.
3. El journal es append-only: si algo cambia, se emite otro evento.

## Tests

```bash
make check     # mypy sobre contracts + pytest
```

Escribe tests donde haya lógica de verdad, no para cubrir líneas. Un fallo corregido lleva un test
que lo habría cazado.

## Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/):

```
<tipo>(<ámbito>): <descripción en imperativo>
```

Tipos habituales: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`. Ejemplos reales del
historial:

```
fix(deploy): sin ACME_EMAIL, Caddy no arrancaba
docs(deploy): contrato de la /demo autoservicio y guía de despliegue
chore(fixtures): capturas reales de FIRMS y Open-Meteo
```

## Proceso de pull request

1. Haz `git pull --rebase` contra la rama base antes de empezar y de abrir el PR.
2. Abre el PR describiendo **qué cambia y por qué**.
3. Un PR necesita la aprobación de **al menos un mantenedor** para fusionarse.
4. Los cambios de contrato (`packages/contracts/**`) necesitan a los cuatro: añadir un campo
   opcional con default es libre; renombrar, cambiar un tipo o borrar, no.

## Definición de hecho

Una contribución está terminada cuando:

- `make check` pasa en verde.
- Hay tests para la lógica nueva.
- No se ha roto ninguna invariante de [`CLAUDE.md`](CLAUDE.md).
- La documentación afectada (README, `docs/`) está actualizada.
- Un mantenedor la ha revisado y aprobado.

## Conducta

Al participar aceptas el [código de conducta](CODE_OF_CONDUCT.md). Y si tienes dudas, abre un issue:
preguntar nunca sobra.
