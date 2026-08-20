"""Application configuration.

Loads runtime settings from environment variables (and an optional ``.env`` file)
into a single, cached ``Settings`` object.  Every other module asks this module
for configuration instead of reading ``os.environ`` directly, so the whole
configuration surface of the service is declared in exactly one place.

Called by:
    ``app.db`` at import time (for the database URL) and ``app.main`` at startup.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration of the DLP service.

    Attributes:
        database_url: SQLAlchemy connection string. It must use an *async*
            driver (``postgresql+asyncpg``), because the whole service is async.
        db_echo: When True, SQLAlchemy logs every SQL statement it emits.
            Useful while learning the ORM, noisy in production.
    """

    # ``env_file`` is read only if the file exists; real environment variables win over it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dlp:dlp@localhost:5432/dlp"
    db_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    ``lru_cache`` guarantees the environment is parsed once per process, so the
    object can be imported freely without repeated I/O or inconsistent values.

    Returns:
        Settings: The cached configuration object.
    """
    return Settings()
