"""Internal data-access layer - every database query of the service lives here.

The routers in ``app/api`` never build a query themselves; they call these
functions and translate the outcome into HTTP.  Nothing in this module imports
FastAPI, which keeps it reusable: the scan endpoint of iteration 2 will load a
Data Set through the very same ``get_data_set``.

Failure is reported in two ways:
    * ``None`` / ``False`` - the row simply does not exist (the caller decides
      that this means 404).
    * A domain exception - the database refused the change, or the payload
      referenced something missing.

Called by:
    ``app/api/datatypes.py`` and ``app/api/datasets.py``.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataSet, DataType
from app.schemas import DataSetCreate, DataSetUpdate, DataTypeCreate, DataTypeUpdate


# PostgreSQL SQLSTATE codes, used to tell one constraint violation from another.
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"

# Fallbacks for call sites where the violation is not expected to be reachable.
_DEFAULT_UNIQUE_DETAIL = "a resource with these values already exists"
_DEFAULT_REFERENCE_DETAIL = "resource is still referenced by another resource"


class ConflictError(Exception):
    """Raised when a database constraint rejects the requested change.

    Covers a duplicate name (unique violation) and deleting a Data Type that is
    still referenced by a Data Set (foreign-key RESTRICT). Both map to HTTP 409.
    """


class MissingDataTypesError(Exception):
    """Raised when a Data Set payload references Data Types that do not exist.

    Maps to HTTP 404.
    """


async def _commit(
    session: AsyncSession,
    unique_detail: str = _DEFAULT_UNIQUE_DETAIL,
    reference_detail: str = _DEFAULT_REFERENCE_DETAIL,
) -> None:
    """Commit the transaction, mapping constraint violations to the right error.

    Every constraint failure arrives as the same ``IntegrityError``, so the
    SQLSTATE code of the underlying driver error is what tells them apart:

    * unique violation - the client picked a name that is taken (409).
    * foreign-key violation - the row is still referenced, or the row it
      references vanished in a concurrent request (409).
    * anything else, a CHECK violation in particular - the API schemas should
      have rejected this input, so it is a bug in the service and must not be
      disguised as a client error. It is re-raised and surfaces as 500.

    Args:
        session: The active database session.
        unique_detail: Message to report when a unique index rejects the row.
            Only writes that set a unique column need to pass it.
        reference_detail: Message to report when a foreign key rejects the row.
            Only deletes and membership changes need to pass it.

    Raises:
        ConflictError: If a unique or foreign-key constraint rejected the change.
        IntegrityError: For any other constraint violation.
    """
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        # ``error.orig`` is the driver-level exception; asyncpg exposes ``sqlstate``.
        sqlstate = getattr(error.orig, "sqlstate", None)
        if sqlstate == _UNIQUE_VIOLATION:
            raise ConflictError(unique_detail) from error
        if sqlstate == _FOREIGN_KEY_VIOLATION:
            raise ConflictError(reference_detail) from error
        raise


async def _load_data_types(session: AsyncSession, ids: list[uuid.UUID]) -> list[DataType]:
    """Fetch Data Types by id, failing if any of them is missing.

    Args:
        session: The active database session.
        ids: The requested identifiers, already de-duplicated by the schema.

    Returns:
        list[DataType]: The Data Types, in the order the client listed them.

    Raises:
        MissingDataTypesError: If at least one identifier has no matching row.
    """
    result = await session.execute(select(DataType).where(DataType.id.in_(ids)))
    found = {data_type.id: data_type for data_type in result.scalars()}
    missing = [str(identifier) for identifier in ids if identifier not in found]
    if missing:
        raise MissingDataTypesError(f"unknown data_type_ids: {', '.join(missing)}")
    return [found[identifier] for identifier in ids]


# --------------------------------------------------------------------------- #
# Data Types
# --------------------------------------------------------------------------- #


async def create_data_type(session: AsyncSession, payload: DataTypeCreate) -> DataType:
    """Insert a new Data Type.

    Args:
        session: The active database session.
        payload: The validated request body.

    Returns:
        DataType: The persisted Data Type, including its generated id.

    Raises:
        ConflictError: If the name is already taken.
    """
    data_type = DataType(**payload.model_dump())
    session.add(data_type)
    await _commit(session, f"data type named '{payload.name}' already exists")
    return data_type


async def get_data_type(session: AsyncSession, data_type_id: uuid.UUID) -> DataType | None:
    """Fetch a single Data Type by id.

    Args:
        session: The active database session.
        data_type_id: The identifier to look up.

    Returns:
        DataType | None: The Data Type, or None if it does not exist.
    """
    return await session.get(DataType, data_type_id)


async def list_data_types(session: AsyncSession, limit: int, offset: int) -> list[DataType]:
    """Fetch a page of Data Types ordered by name.

    Args:
        session: The active database session.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        list[DataType]: The requested page.
    """
    # Ordering is required for pagination to be stable across requests.
    statement = select(DataType).order_by(DataType.name).limit(limit).offset(offset)
    result = await session.execute(statement)
    return list(result.scalars())


async def update_data_type(
    session: AsyncSession, data_type_id: uuid.UUID, payload: DataTypeUpdate
) -> DataType | None:
    """Replace every field of an existing Data Type.

    Args:
        session: The active database session.
        data_type_id: The identifier to update.
        payload: The validated request body holding the new values.

    Returns:
        DataType | None: The updated Data Type, or None if it does not exist.

    Raises:
        ConflictError: If the new name is already taken by another Data Type.
    """
    data_type = await session.get(DataType, data_type_id)
    if data_type is None:
        return None
    # PUT is a full replacement, so every field is assigned unconditionally.
    for field, value in payload.model_dump().items():
        setattr(data_type, field, value)
    await _commit(session, f"data type named '{payload.name}' already exists")
    return data_type


async def delete_data_type(session: AsyncSession, data_type_id: uuid.UUID) -> bool:
    """Delete a Data Type that is not referenced by any Data Set.

    Args:
        session: The active database session.
        data_type_id: The identifier to delete.

    Returns:
        bool: True if a row was deleted, False if it did not exist.

    Raises:
        ConflictError: If the Data Type is still a member of some Data Set;
            the ON DELETE RESTRICT constraint is what refuses the deletion.
    """
    data_type = await session.get(DataType, data_type_id)
    if data_type is None:
        return False
    await session.delete(data_type)
    await _commit(session, reference_detail="data type is referenced by a data set")
    return True


# --------------------------------------------------------------------------- #
# Data Sets
# --------------------------------------------------------------------------- #


async def create_data_set(session: AsyncSession, payload: DataSetCreate) -> DataSet:
    """Insert a new Data Set and link it to existing Data Types.

    Args:
        session: The active database session.
        payload: The validated request body.

    Returns:
        DataSet: The persisted Data Set with its members loaded.

    Raises:
        MissingDataTypesError: If a referenced Data Type does not exist.
        ConflictError: If the name is already taken.
    """
    members = await _load_data_types(session, payload.data_type_ids)
    # Assigning the relationship makes SQLAlchemy write the association rows for us.
    data_set = DataSet(name=payload.name, data_types=members)
    session.add(data_set)
    await _commit(session, f"data set named '{payload.name}' already exists")
    return data_set


async def get_data_set(session: AsyncSession, data_set_id: uuid.UUID) -> DataSet | None:
    """Fetch a single Data Set by id, with its Data Types loaded.

    Args:
        session: The active database session.
        data_set_id: The identifier to look up.

    Returns:
        DataSet | None: The Data Set, or None if it does not exist.
    """
    # ``lazy="joined"`` on the relationship pulls the members in the same statement.
    return await session.get(DataSet, data_set_id)


async def list_data_sets(session: AsyncSession, limit: int, offset: int) -> list[DataSet]:
    """Fetch a page of Data Sets ordered by name.

    Args:
        session: The active database session.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        list[DataSet]: The requested page.
    """
    statement = select(DataSet).order_by(DataSet.name).limit(limit).offset(offset)
    result = await session.execute(statement)
    # ``unique()`` is mandatory with a joined eager load of a collection: the JOIN returns one
    # row per member, and this collapses them back into one object per Data Set. SQLAlchemy
    # raises rather than guessing, so forgetting it is a loud error, not a silent duplicate.
    return list(result.unique().scalars())


async def update_data_set(
    session: AsyncSession, data_set_id: uuid.UUID, payload: DataSetUpdate
) -> DataSet | None:
    """Replace the name and the whole membership list of a Data Set.

    Args:
        session: The active database session.
        data_set_id: The identifier to update.
        payload: The validated request body holding the new values.

    Returns:
        DataSet | None: The updated Data Set, or None if it does not exist.

    Raises:
        MissingDataTypesError: If a referenced Data Type does not exist.
        ConflictError: If the new name is already taken.
    """
    data_set = await session.get(DataSet, data_set_id)
    if data_set is None:
        return None
    members = await _load_data_types(session, payload.data_type_ids)
    data_set.name = payload.name
    # Replacing the list makes SQLAlchemy diff it and emit the needed INSERT/DELETE rows.
    data_set.data_types = members
    await _commit(session, f"data set named '{payload.name}' already exists")
    return data_set


async def delete_data_set(session: AsyncSession, data_set_id: uuid.UUID) -> bool:
    """Delete a Data Set and its memberships, leaving the Data Types intact.

    Args:
        session: The active database session.
        data_set_id: The identifier to delete.

    Returns:
        bool: True if a row was deleted, False if it did not exist.
    """
    data_set = await session.get(DataSet, data_set_id)
    if data_set is None:
        return False
    await session.delete(data_set)
    await _commit(session)
    return True
