"""Un solo `.env` en la raíz, con `.env.example` commiteado.

Cada paquete lee solo sus variables. Los teléfonos en variables y no en el código:
los vais a cambiar cinco minutos antes de subir al escenario.
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
    # Un teléfono por INTERLOCUTOR, no por papel. El papel cambia con el viento —a
    # Pueblo B se le avisa de que puede llegarle gente y, media hora después, se le
    # ordena salir— pero quien coge el teléfono es la misma persona. Con el reparto por
    # papel (`JUDGE_PHONE` ganaba a todos) el mismo móvil recibía las dos órdenes de
    # evacuación, una como Pueblo A y otra como Pueblo B: medido en
    # `runs/run_1dfefd9171f8.jsonl`, seq 169 y 568, los dos a +34601179229.
    #
    # `PHONE_<POI sin el prefijo poi_>`, en mayúsculas: `PHONE_PUEBLO_A`. Sin él manda
    # el `contact_phone` del POI en el escenario.
    phone_pueblo_a: str = ""
    phone_pueblo_b: str = ""
    # Los medios contestan al teléfono como cualquiera: al retén se le llama en cuanto
    # hay fuego, y a la ambulancia solo cuando alguien la ha pedido y está libre. Sin
    # número configurado no se llama y se anota una vez: no se cae en el de otro
    # porque dos llamadas a la vez al mismo móvil dan ocupado, medido el sábado.
    phone_fire_crew: str = ""
    phone_ambulance: str = ""
    # La palanca de los cinco minutos antes de subir al escenario: si está, TODAS las
    # llamadas a pueblos caen aquí. Vacío en un ensayo normal, que es cuando cada uno
    # tiene que atender lo suyo.
    phone_override: str = ""
    # P3 · Telegram: el «dónde» exacto tras la llamada. Sin token, canal ausente.
    telegram_bot_token: str = ""
    telegram_secret_token: str = ""  # `X-Telegram-Bot-Api-Secret-Token` del setWebhook
    telegram_bot_username: str = ""  # sin @: lo que el agente de voz le dice al vecino
    vela_no_telegram: bool = False

    # P3 y P4
    webhook_shared_token: str = ""

    # P4
    vela_mode: Literal["demo", "dev", "replay"] = "dev"

    def phone_for_poi(self, poi_id: str, contact: str = "") -> str:
        """El teléfono de quien atiende ese POI: `PHONE_PUEBLO_A` para `poi_pueblo_a`,
        y si no hay, el `contact_phone` que traiga el escenario.

        `PHONE_OVERRIDE` gana a todo: es el «todo a mi móvil» de antes de salir al
        escenario."""
        if self.phone_override:
            return self.phone_override
        propio = getattr(self, f"phone_{poi_id.removeprefix('poi_')}", "")
        return str(propio or contact or "")

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
