"""El traspaso llamada → Telegram cuando el vecino está perdido.

Un perdido no puede decir dónde está, así que el punto exacto tiene que llegar por
el otro canal. La condición original —«no hemos podido ubicarlo»— no basta: el
resolutor se conforma con un solo token en común y coloca a un perdido en un pueblo
con total confianza. Si él dice que está perdido, su palabra pesa más.
"""

from voice.webhooks import needs_telegram, says_lost

VECINO = "vecino"
AGENTE = "operador"


def turno(quien: str, texto: str) -> dict[str, str]:
    return {"speaker": quien, "text": texto}


def test_sin_poi_se_pide_como_siempre():
    """La condición original no cambia."""
    assert needs_telegram(None) is True
    assert needs_telegram(None, [turno(VECINO, "veo humo")]) is True


def test_con_poi_y_sin_decir_que_esta_perdido_no_cambia_nada():
    """El resto de llamadas se comportan exactamente igual que antes."""
    assert needs_telegram("poi_pueblo_a") is False
    assert needs_telegram("poi_pueblo_a", [turno(VECINO, "estoy en Pueblo A")]) is False
    assert needs_telegram("poi_pueblo_b", [turno(VECINO, "hay tres personas")]) is False


def test_si_dice_que_esta_perdido_se_pide_aunque_haya_poi():
    """El caso que importa: el resolutor cree saberlo y el vecino dice que no."""
    charla = [
        turno(VECINO, "Estoy perdido, no sé dónde estoy."),
        turno(AGENTE, "¿Hay algún punto de referencia?"),
        turno(VECINO, "Al final de una pista, junto a unas casas."),
    ]
    assert needs_telegram("poi_pueblo_a", charla) is True


def test_formas_de_decirlo():
    for frase in (
        "estoy perdido",
        "estamos perdidos en el monte",
        "me he perdido",
        "no sé dónde estoy",
        "no se donde me encuentro",
        "no reconozco nada de esto",
    ):
        assert says_lost([turno(VECINO, frase)]) is True, frase


def test_no_se_dispara_con_cualquier_cosa():
    for frase in (
        "estoy en Pueblo A",
        "hay tres personas y una herida",
        "la pista del sur está cortada",
        "no sé si la carretera está cortada",
    ):
        assert says_lost([turno(VECINO, frase)]) is False, frase


def test_solo_cuenta_si_lo_dice_el_vecino():
    """Dicho por el agente no es una declaración del vecino: sale del `message` que
    su prompt le manda repetir, y realimentaría la condición en cada vuelta."""
    charla = [turno(AGENTE, "¿Está usted perdido? No sé dónde está.")]
    assert says_lost(charla) is False
    assert needs_telegram("poi_pueblo_a", charla) is False
