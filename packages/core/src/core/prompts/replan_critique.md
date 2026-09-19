<!-- Segunda vuelta: el plan anterior falló un verificador y vuelve con la crítica.
     Máximo dos vueltas (contracts.plan.MAX_REPLAN_ROUNDS); a la tercera se cae al
     plan del solver con pesos neutros. Mismas reglas de catálogo que planner.md:
     solo pesos y restricciones del catálogo van entre backticks. -->

# Situación

El plan anterior fue rechazado por los verificadores. Ajusta la política para que
el solver produzca un plan factible: relaja o cambia pesos y, sobre todo, corrige
las restricciones duras que provocaron el rechazo. Sigues sin asignar unidades;
solo emites una política corregida llamando a emit_policy una vez.

Recuerda los catálogos: pesos (`life_safety`, `immobile_first`,
`structure_protection`, `containment`, `response_time`) y restricciones
(`no_unit_into_burning_cell`, `hospital_min_coverage:n`,
`no_civilian_route_through:wp_id`, `reserve_capability:cap:n`).

# Estado

<<STATE>>

# Violaciones del plan anterior

<<VIOLATIONS>>

# Salida

Llama a emit_policy con una política corregida: mismos campos que antes
(rationale, weights, hard_constraints, horizon_s, escalate_to_human, notify),
resolviendo las violaciones listadas.
