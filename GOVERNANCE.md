# Gobernanza de Taiafox

Taiafox nació en HackSpain 2026 (entrega del Grupo Gamma al track de HappyRobot) y lo mantiene un
equipo de cuatro personas. Este documento dice quién decide qué y cómo.

## Contacto

Escribe a cualquier mantenedor por su perfil de GitHub, o abre un issue en el repositorio para
asuntos públicos. Para temas sensibles, usa un mensaje privado.

## Mantenedores y responsabilidades

Todos participan en igualdad de condiciones. La propiedad de ficheros sigue la tabla de
[`CLAUDE.md`](CLAUDE.md).

| Mantenedor | Responsabilidad principal |
| --- | --- |
| Carlos Cao | `packages/core`, `packages/journal`, `voice/happyrobot.py` |
| Hugo Nienhausen | `packages/voice` (percepción, Humalike, webhooks), `core/belief.py`, `core/ingest.py` |
| Luis Garbayo | `packages/sim`, `infra`, `scenarios/*.yaml` |
| Ignacio Garbayo | `apps/gateway`, `apps/dashboard`, `scripts/demo.py` |

`packages/contracts/**` no tiene dueño: es de los cuatro.

Ser dueño de una ruta no da la última palabra sobre ella, pero sí la primera: quien quiera tocarla
avisa a su responsable, o lo pide por evento, no por import.

## Cómo se toman las decisiones

**Comunicación abierta.** Las decisiones se discuten en issues y pull requests, no en privado, para
que todos estén informados y cualquiera pueda opinar.

**Decisiones del día a día**: cualquier mantenedor puede decidir solo (corregir un fallo, mejorar
un texto, refactorizar dentro de su ámbito).

**Decisiones significativas** requieren el acuerdo de **al menos 2 mantenedores**:

- Añadir una dependencia nueva.
- Cambiar un protocolo, un evento o un endpoint.
- Publicar una versión.
- Cambiar la licencia o esta gobernanza.

**Cambios de contrato** (`packages/contracts/**`): añadir un campo opcional con default es libre
(commit y aviso); renombrar, cambiar un tipo o hacerlo obligatorio necesita a los cuatro; borrar
está prohibido.

### Reglas de voto

- Cada mantenedor tiene **un voto**, del mismo peso.
- Se busca **consenso**. Si no llega, se decide por **mayoría simple** (3 de 4). Un empate 2-2 no
  decide: se sigue como estaba y se vuelve a discutir con más información.
- Quien no responde en 7 días se considera abstención; no bloquea.

Ejemplo: Hugo propone sustituir `fenic` por otra librería. Lo abre como issue, tres mantenedores
lo aprueban y el cuarto se abstiene: se aprueba.

## Resolución de conflictos

1. Las personas implicadas lo hablan directamente, con respeto y por escrito en el issue o PR.
2. Si no hay acuerdo, se pide a un mantenedor no implicado que medie.
3. Si sigue sin haberlo, se vota según las reglas de arriba.

Las quejas de conducta no se tratan aquí: siguen el [código de conducta](CODE_OF_CONDUCT.md).

## Incorporar nuevas personas

- **Contribuyentes**: cualquiera puede contribuir siguiendo [CONTRIBUTING.md](CONTRIBUTING.md). Una
  primera contribución aceptada es la mejor tarjeta de presentación.
- **Nuevos mantenedores**: se incorporan **por unanimidad** de los actuales, y solo cuando el
  proyecto lo necesita. Se valora una trayectoria sostenida de contribuciones de calidad y trato
  respetuoso.
- **Salida**: un mantenedor puede dejar el rol cuando quiera, avisando al resto. La inactividad
  prolongada se habla con la persona antes de retirarle el rol.

## Cambios en este documento

Se modifica con el acuerdo de al menos 2 mantenedores, y el cambio se anota en el historial de
git.
