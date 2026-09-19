"""Mover todo el sistema a un túnel nuevo, de una vez.

El túnel gratuito cambia de URL cada vez que se reinicia el agente, y entonces hay
que repuntar **diez sitios**: `VELA_URL` en los tres entornos (production, staging,
development) de cada uno de los tres workflows de HappyRobot, más el webhook del bot
de Telegram. A mano, en la UI, son diez formularios; olvidar uno deja la demo a
medias y el síntoma —un webhook que no llega— no dice cuál falta.

    uv run python scripts/retunnel.py                      # qué hay puesto ahora
    uv run python scripts/retunnel.py --apply https://xxx.ngrok-free.app

**Nunca aplica sin comprobar antes que la URL nueva contesta.** Apuntar la
configuración de producción a un túnel muerto es peor que no tocarla: los webhooks
fallan en silencio y el agente parece tonto en vez de desconectado.

Notas de red, medidas el sábado en la wifi del evento: solo sale tráfico por 443 y
80. `trycloudflare.com` está bloqueado por nombre **y** por IP, y el canal de datos
de localtunnel usa puertos altos, así que de los tres túneles habituales **solo
ngrok funciona**. Conviene saberlo antes de improvisar.

Esto toca configuración compartida de HappyRobot (P3/Carlos) y el webhook del bot
(P3). Por eso el modo por defecto es de solo lectura.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import httpx

from contracts.settings import settings

ENTORNOS = ("production", "staging", "development")
TIMEOUT_S = 20.0
BOT_DEMO = "taiafox_bot"
"""El bot de la demo. Está escrito aquí, y no se lee del `.env`, a propósito: el
token sale del mismo fichero que el nombre, así que compararlos siempre cuadraría y
no probaría nada. Escrito fuera, delata a la máquina que lleva otro token.

Sin esto, lanzar `--apply` desde un equipo de pruebas cambia los nueve VELA_URL bien
y le pone el webhook al bot equivocado, imprimiendo `ok`. Nueve de diez aciertos y
un verde falso: te deja tranquilo hasta que el jurado manda el pin."""

SONDA = "/api/health"
"""Lo que se le pide a la URL nueva para creerse que está viva. Es un GET sin
efectos: no arranca nada ni toca el run que pueda haber corriendo."""


def _hr() -> httpx.AsyncClient:
    key = settings.happyrobot_org_key or settings.happyrobot_api_key
    if not key:
        raise SystemExit("falta HAPPYROBOT_API_KEY en el .env")
    return httpx.AsyncClient(
        base_url=settings.happyrobot_api_base,
        headers={"Authorization": f"Bearer {key}"},
        timeout=TIMEOUT_S,
    )


async def workflows(c: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await c.get("/workflows/")
    r.raise_for_status()
    return r.json().get("data", [])


async def variables(c: httpx.AsyncClient, workflow_id: str) -> list[dict[str, Any]]:
    r = await c.get(f"/workflows/{workflow_id}/variables")
    r.raise_for_status()
    return r.json().get("data", [])


async def tunel_vivo(url: str) -> tuple[bool, str]:
    """¿Contesta el gateway detrás de esa URL? Devuelve (vale, explicación)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as c:
            r = await c.get(url.rstrip("/") + SONDA)
    except httpx.HTTPError as exc:
        return False, f"no responde ({type(exc).__name__})"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    try:
        datos = r.json()
    except ValueError:
        return False, "contesta, pero no es el gateway (no devuelve JSON)"
    comp = datos.get("components", {})
    return True, f"run {datos.get('run_id', '-')} · componentes {comp}"


async def estado() -> int:
    """Qué URL tiene puesta cada workflow, y si el bot apunta al mismo sitio."""
    async with _hr() as c:
        urls: set[str] = set()
        for w in await workflows(c):
            print(f"  {w['name']}")
            for v in await variables(c, w["id"]):
                if v["key"] != "VELA_URL":
                    continue
                vals = {e: v.get(f"value_{e}", "") for e in ENTORNOS}
                distintos = set(vals.values())
                urls |= distintos
                if len(distintos) == 1:
                    print(f"     VELA_URL  {vals['production']}  (los tres entornos)")
                else:
                    for e, val in vals.items():
                        print(f"     VELA_URL  [{e:11}] {val}")

    from voice import telegram  # tarde: solo si hace falta

    # Se dice DE QUÉ bot se habla: el del token del .env, que en una máquina de
    # pruebas no es el de la demo. Un «(ninguno)» sin nombre se lee como «el canal
    # está sin configurar» cuando en realidad es «estás mirando otro bot».
    yo = await telegram.api_call("getMe", {})
    quien = f"@{yo['username']}" if yo else "(sin token válido)"
    info = await telegram.api_call("getWebhookInfo", {})
    bot_url = (info or {}).get("url", "")
    print(f"\n  webhook de {quien}: {bot_url or '(ninguno)'}")

    bases = {u.rstrip("/") for u in urls if u}
    if bot_url:
        bases.add(bot_url.split("/webhooks/")[0].rstrip("/"))
    if len(bases) > 1:
        print("\n  AVISO: no todos apuntan al mismo sitio:")
        for b in sorted(bases):
            print(f"    {b}")
        return 1
    if bases:
        vale, detalle = await tunel_vivo(next(iter(bases)))
        print(f"  ¿vivo? {'sí' if vale else 'NO'} — {detalle}")
    return 0


async def aplicar(url: str, con_telegram: bool) -> int:
    url = url.rstrip("/")
    print(f"comprobando {url}{SONDA} antes de tocar nada…")
    vale, detalle = await tunel_vivo(url)
    if not vale:
        print(f"  NO vale: {detalle}")
        print("  no cambio nada: apuntar a un túnel muerto es peor que no tocarlo.")
        return 1
    print(f"  vive · {detalle}\n")

    tocados = 0
    async with _hr() as c:
        for w in await workflows(c):
            for v in await variables(c, w["id"]):
                if v["key"] != "VELA_URL":
                    continue
                if all(v.get(f"value_{e}") == url for e in ENTORNOS):
                    print(f"  {w['name']:24} VELA_URL ya estaba puesta")
                    continue
                cuerpo = {f"value_{e}": url for e in ENTORNOS}
                r = await c.patch(
                    f"/workflows/{w['id']}/variables/{v['id']}", json=cuerpo
                )
                ok = r.status_code < 400
                print(
                    f"  {w['name']:24} VELA_URL → {url}  "
                    f"{'ok' if ok else f'FALLA {r.status_code} {r.text[:120]}'}"
                )
                tocados += ok

    if con_telegram:
        from voice import telegram

        yo = await telegram.api_call("getMe", {})
        quien = (yo or {}).get("username", "")
        if quien != BOT_DEMO:
            print(
                f"  {'telegram':24} NO lo toco: el token del .env es de "
                f"@{quien or '?'}, no de @{BOT_DEMO}."
            )
            print(
                f"    El webhook de @{BOT_DEMO} sigue apuntando al túnel viejo. "
                "Repítelo desde la máquina de la demo."
            )
            print(f"\n{tocados} variables cambiadas, webhook SIN tocar.")
            return 1

        res = await telegram.api_call(
            "setWebhook",
            {
                "url": f"{url}/webhooks/telegram",
                "allowed_updates": ["message", "edited_message"],
                "drop_pending_updates": True,
                **(
                    {"secret_token": settings.telegram_secret_token}
                    if settings.telegram_secret_token
                    else {}
                ),
            },
        )
        print(
            f"  {'telegram':24} webhook → {url}/webhooks/telegram  "
            f"{'ok' if res is not None else 'FALLA'}"
        )

    print(f"\n{tocados} variables cambiadas. Repasa con el modo sin --apply.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Repuntar todo a un túnel nuevo · vela")
    ap.add_argument("--apply", metavar="URL", help="la URL nueva del túnel")
    ap.add_argument(
        "--no-telegram", action="store_true", help="no tocar el webhook del bot"
    )
    args = ap.parse_args()
    if args.apply:
        sys.exit(asyncio.run(aplicar(args.apply, not args.no_telegram)))
    sys.exit(asyncio.run(estado()))


if __name__ == "__main__":
    main()
