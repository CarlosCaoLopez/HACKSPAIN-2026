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

    # P1 (planner) y P3 (fenic)
    anthropic_api_key: str = ""

    # P3 · telefonía
    happyrobot_api_key: str = ""
    happyrobot_hook_evacuation: str = ""  # la URL del incoming hook
    humalike_api_key: str = ""
    judge_phone: str = ""

    # P3 y P4
    webhook_shared_token: str = ""

    # P4
    vela_mode: Literal["demo", "dev", "replay"] = "dev"


settings = Settings()
