# Licencia de Taiafox y de sus componentes

Taiafox se publica bajo **Apache License 2.0** (identificador SPDX `Apache-2.0`). El texto
completo está en [`LICENSE`](../LICENSE) y en [`LICENSES/Apache-2.0.txt`](../LICENSES/Apache-2.0.txt).
Como todo el software libre, se entrega **sin garantías**.

## Por qué Apache-2.0

- **Cláusula de patentes explícita.** Quien contribuye concede una licencia de patentes sobre su
  contribución y la pierde si demanda por infracción. El proyecto toca telefonía, LLM y
  simulación, ámbitos donde las patentes existen.
- **Permisiva.** Cualquiera puede usar, modificar y redistribuir el código, incluso en productos
  cerrados, conservando el aviso de copyright y la licencia. No hay copyleft que frene la adopción.
- **Compatible con nuestras dependencias**, que son en su mayoría permisivas (tabla siguiente).
- **Aceptada por OSI y por REUSE**, con identificador SPDX estándar.

Una advertencia honesta: Apache-2.0 **no** obliga a publicar el código de un servicio que use
Taiafox por red. Si algún día se quisiera esa protección, la alternativa sería AGPL-3.0.

## Dependencias directas

Las licencias son las habituales de cada proyecto; conviene revisarlas al añadir o actualizar una
dependencia.

| Dependencia | Uso | Licencia | ¿Compatible con Apache-2.0? |
| --- | --- | --- | --- |
| pydantic, pydantic-settings | Contratos y configuración | MIT | Sí |
| FastAPI, uvicorn | Gateway | MIT / BSD-3-Clause | Sí |
| httpx, websockets | Clientes y sockets | BSD-3-Clause | Sí |
| numpy, scipy | Solver de asignación | BSD-3-Clause | Sí |
| PyYAML | Escenarios | MIT | Sí |
| mcrcon | RCON a Minecraft | MIT | Sí |
| anthropic, openai | Clientes de LLM | MIT / Apache-2.0 | Sí |
| fenic, typesafe-sdk | Batch y percepción en llamada | Sin comprobar | Pendiente de verificar |
| React, Vite, Tailwind CSS, Leaflet, hls.js | Dashboard | MIT / BSD-2-Clause / Apache-2.0 | Sí |

## Servicios y recursos externos

- **Paper (servidor de Minecraft)**: se ejecuta como proceso aparte y no se redistribuye aquí.
  Minecraft es un producto de Mojang y su EULA aplica a quien lo use.
- **HappyRobot, Humalike, TypeSafe, OpenAI, Anthropic**: APIs de terceros con sus propios
  términos; las claves no forman parte del repositorio.
- **Datos de NASA FIRMS, Open-Meteo y DGT** (capturas en `fixtures/`): revisa los términos de uso
  de cada proveedor antes de redistribuirlos fuera de este repositorio.

## Regla para nuevas dependencias

Antes de añadir una, comprueba su licencia. Se aceptan las permisivas (MIT, BSD, Apache-2.0). Las
de copyleft fuerte (GPL, AGPL) exigen el acuerdo de >= 2 mantenedores, según la
[gobernanza](../GOVERNANCE.md).
