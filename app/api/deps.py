"""Shared FastAPI dependencies for the HTTP layer.

Declaring the session dependency once keeps every endpoint signature short and
guarantees all routers obtain their session the same way.

Called by:
    ``app/api/datatypes.py`` and ``app/api/datasets.py``.
"""

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import MAX_PAGE_SIZE
from app.db import get_session

# ``Annotated[X, Depends(f)]`` is the modern FastAPI way to declare an injected
# parameter: the alias can be reused as a plain type hint on every endpoint.
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Pagination bounds are enforced by FastAPI itself, so an endpoint never sees
# limit=1000000; an out-of-range value is rejected with HTTP 422.
LimitDep = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
OffsetDep = Annotated[int, Query(ge=0)]
