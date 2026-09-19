"""Un solo `.env` en la raíz, con `.env.example` commiteado.

Cada paquete lee solo sus variables. `JUDGE_PHONE` en una variable y no en el
código: lo vais a cambiar cinco minutos antes de subir al escenario.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # P2 · mundo
    rcon_host: str = "localhost"
    rcon_port: int = 25575
    rcon_password: str = ""

    # P3 (fenic)
    anthropic_api_key: str = ""
    openai_api_key: str = ""  # P1 y P3: fenic usa gpt-5.6-luna si está; si no, Anthropic

    # P1 · planner (GPT-5.6 Luna)

    # P3 · telefonía
    happyrobot_api_key: str = ""
    happyrobot_api_base: str = "https://platform.eu.happyrobot.ai/api/v2"  # P3: región EU
    happyrobot_org_key: str = ""  # clave de organización (hr_): API de plataforma
    happyrobot_hook_evacuation: str = ""  # la URL del incoming hook
    humalike_api_key: str = ""
    typesafe_api_key: str = ""  # P3 · percepción en llamada (Jev). Sin ella: --no-jev
    typesafe_model: str = "jev-1.13.0"
    vela_no_jev: bool = False  # P3 · fuerza el plan B: fenic con Literal, sin bucle
    happyrobot_webcall_url: str = ""  # P3/P4: enlace de la web call del workflow entrante
    judge_phone: str = ""
    # El teléfono del pueblo que NO está en peligro: el que recibe el aviso de que
    # pueden llegarle vecinos del otro. Es por papel, no por pueblo (cuál está a salvo
    # cambia con el viento). Sin él, ese aviso cae en `judge_phone` como el resto.
    neighbor_phone: str = ""
    # Los medios contestan al teléfono como cualquiera: al retén se le llama en cuanto
    # hay fuego, y a la ambulancia solo cuando alguien la ha pedido y está libre. Sin
    # número configurado no se llama y se anota una vez (no se cae en `judge_phone`:
    # dos llamadas a la vez al mismo móvil dan ocupado, medido el sábado).
    fire_crew_phone: str = ""
    ambulance_phone: str = ""
    # P3 · Telegram: el «dónde» exacto tras la llamada. Sin token, canal ausente.
    telegram_bot_token: str = ""
    telegram_secret_token: str = ""  # `X-Telegram-Bot-Api-Secret-Token` del setWebhook
    telegram_bot_username: str = ""  # sin @: lo que el agente de voz le dice al vecino
    vela_no_telegram: bool = False

    # P3 y P4
    webhook_shared_token: str = ""

    # P4
    vela_mode: Literal["demo", "dev", "replay"] = "dev"

    # P4 · modo replay: el gateway alimenta el WS desde un journal en vez de desde
    # el sim. Es lo que hace útil `make dev-dash` y no necesita ni core ni voz.
    vela_replay_file: str = "fixtures/run_golden.jsonl"
    vela_replay_speed: float = 1.0  # 0 = tan rápido como pueda
    vela_replay_loop: bool = False  # útil mientras se pintan paneles

    # P4 · los puentes `action.requested` → sim y `call.requested` → voice. Encendidos:
    # ni `Sim` ni `VoiceGateway` se suscriben a esos eventos, así que sin ellos ninguna
    # orden llega al mundo. `VELA_BRIDGES=false` los apaga si P2 o P3 suscriben ellos
    # (si no, cada acción se ejecutaría dos veces; `/api/health` lo cuenta).
    vela_bridges: bool = True

    # P4 · fuentes reales (SPEC-007): datos de APIs públicas que entran como
    # `world.fact.asserted` con procedencia. `off` deja la demo exactamente como estaba.
    vela_feeds: Literal["off", "live", "recorded"] = "off"
    vela_feeds_only: str = ""  # "open_meteo,dgt,firms,aemet"; vacío = todas
    vela_feeds_dir: str = (
        "fixtures/feeds"  # dónde se guardan y de dónde se leen las capturas
    )
    firms_map_key: str = ""  # NASA FIRMS (gratis, por email). Sin ella: FIRMS `off`
    aemet_api_key: str = ""  # AEMET OpenData (gratis, por email). Sin ella: AEMET `off`


settings = Settings()
