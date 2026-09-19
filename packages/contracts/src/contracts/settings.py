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
    happyrobot_hook_evacuation: str = ""  # la URL del incoming hook
    humalike_api_key: str = ""
    typesafe_api_key: str = ""  # P3 · percepción en llamada (Jev). Sin ella: --no-jev
    typesafe_model: str = "jev-1.13.0"
    vela_no_jev: bool = False  # P3 · fuerza el plan B: fenic con Literal, sin bucle
    happyrobot_webcall_url: str = ""  # P3/P4: enlace de la web call del workflow entrante
    judge_phone: str = ""

    # P3 y P4
    webhook_shared_token: str = ""

    # P4
    vela_mode: Literal["demo", "dev", "replay"] = "dev"

    # P4 · modo replay: el gateway alimenta el WS desde un journal en vez de desde
    # el sim. Es lo que hace útil `make dev-dash` y no necesita ni core ni voz.
    vela_replay_file: str = "fixtures/run_golden.jsonl"
    vela_replay_speed: float = 1.0  # 0 = tan rápido como pueda
    vela_replay_loop: bool = False  # útil mientras se pintan paneles

    # P4 · los puentes `action.requested` → sim y `call.requested` → voice. Apagados
    # hasta que P2 y P3 confirmen que no se suscriben ellos: si lo hacen los dos,
    # cada acción se ejecuta dos veces y la unidad se mueve doble en la demo.
    vela_bridges: bool = False


settings = Settings()
