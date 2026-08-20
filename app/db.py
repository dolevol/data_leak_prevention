"""Database engine, session factory and the per-request session dependency.

This module owns the *connection* to PostgreSQL.  It exposes three things:

* ``Base``     - the declarative base every ORM model inherits from. Its
  ``metadata`` collects the table definitions and is what ``create_all`` reads.
* ``engine``   - the connection pool. One per process, created at import time.
* ``get_session`` - a FastAPI dependency that hands each request its own
  ``AsyncSession`` and closes it afterwards.

Called by:
    ``app.models`` (imports ``Base``), the routers in ``app/api`` (via
    ``Depends(get_session)``), and ``app.main`` (creates the schema on startup
    and disposes the engine on shutdown).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

_settings = get_settings()


class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models.

    Subclassing it registers a model's table inside ``Base.metadata``, which is
    the catalogue ``metadata.create_all`` iterates over when building the schema.
    """


# The engine holds a pool of connections; creating it does NOT open a connection yet.
# ``pool_pre_ping`` sends a cheap SELECT 1 before reusing a pooled connection, which
# prevents "server closed the connection unexpectedly" after the DB restarts.
engine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_pre_ping=True,
)

# ``expire_on_commit=False`` keeps ORM attributes loaded after ``commit()``.
# In async code this is essential: an expired attribute would trigger a lazy
# refresh during response serialization, outside the async context, and raise.
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session scoped to a single HTTP request.

    FastAPI calls this generator before the endpoint runs, injects the yielded
    session, and resumes the generator once the response is produced - so the
    ``async with`` block always closes the session and returns its connection
    to the pool, even if the endpoint raised.

    Yields:
        AsyncSession: A session bound to the shared engine.
    """
    async with SessionFactory() as session:
        yield session
