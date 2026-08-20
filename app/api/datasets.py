"""HTTP endpoints for ``/api/datasets`` - Data Sets management.

Structurally identical to the Data Types router, with one difference: the stored
shape (an association table) is not the wire shape, so responses are built with
``DataSetRead.from_model`` instead of being serialized straight from the ORM.

Called by:
    ``app.main`` through ``app.include_router``.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app import crud
from app.api.deps import LimitDep, OffsetDep, SessionDep
from app.constants import DEFAULT_PAGE_SIZE
from app.schemas import DataSetCreate, DataSetRead, DataSetUpdate

router = APIRouter(prefix="/api/datasets", tags=["Data Sets"])


@router.post("", response_model=DataSetRead, status_code=status.HTTP_201_CREATED)
async def create_data_set(payload: DataSetCreate, session: SessionDep) -> DataSetRead:
    """Create a new Data Set referencing existing Data Types.

    Args:
        payload: The Data Set to create.
        session: The request-scoped database session.

    Returns:
        DataSetRead: The created Data Set. Unknown Data Type ids surface as 404
            through the MissingDataTypesError handler.
    """
    data_set = await crud.create_data_set(session, payload)
    return DataSetRead.from_model(data_set)


@router.get("", response_model=list[DataSetRead])
async def list_data_sets(
    session: SessionDep, limit: LimitDep = DEFAULT_PAGE_SIZE, offset: OffsetDep = 0
) -> list[DataSetRead]:
    """List Data Sets, ordered by name.

    Args:
        session: The request-scoped database session.
        limit: Maximum number of items to return.
        offset: Number of items to skip.

    Returns:
        list[DataSetRead]: The requested page.
    """
    data_sets = await crud.list_data_sets(session, limit, offset)
    return [DataSetRead.from_model(data_set) for data_set in data_sets]


@router.get("/{data_set_id}", response_model=DataSetRead)
async def get_data_set(data_set_id: uuid.UUID, session: SessionDep) -> DataSetRead:
    """Fetch a single Data Set.

    Args:
        data_set_id: Identifier from the path; a malformed UUID yields 422.
        session: The request-scoped database session.

    Returns:
        DataSetRead: The requested Data Set.

    Raises:
        HTTPException: 404 if no Data Set has this id.
    """
    data_set = await crud.get_data_set(session, data_set_id)
    if data_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "data set not found")
    return DataSetRead.from_model(data_set)


@router.put("/{data_set_id}", response_model=DataSetRead)
async def update_data_set(
    data_set_id: uuid.UUID, payload: DataSetUpdate, session: SessionDep
) -> DataSetRead:
    """Replace an existing Data Set, including its whole membership list.

    Args:
        data_set_id: Identifier of the Data Set to replace.
        payload: The complete new representation.
        session: The request-scoped database session.

    Returns:
        DataSetRead: The updated Data Set.

    Raises:
        HTTPException: 404 if no Data Set has this id.
    """
    data_set = await crud.update_data_set(session, data_set_id, payload)
    if data_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "data set not found")
    return DataSetRead.from_model(data_set)


@router.delete("/{data_set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_set(data_set_id: uuid.UUID, session: SessionDep) -> None:
    """Delete a Data Set. Its Data Types are left untouched.

    Args:
        data_set_id: Identifier of the Data Set to delete.
        session: The request-scoped database session.

    Raises:
        HTTPException: 404 if no Data Set has this id.
    """
    if not await crud.delete_data_set(session, data_set_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "data set not found")
