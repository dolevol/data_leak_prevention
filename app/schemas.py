"""Pydantic schemas - the API contract and the validation layer.

Each resource has three schemas: ``*Create`` and ``*Update`` describe what the
client may send, ``*Read`` describes what the service returns.  FastAPI uses
them to validate request bodies - rejecting bad input with HTTP 422 before any
endpoint code runs - and to serialize responses.

Called by:
    The routers in ``app/api``, as type hints on their parameters and as
    ``response_model``.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.constants import (
    MAX_DESCRIPTION_LENGTH,
    DT_MAX_KEYWORD_LENGTH,
    DT_MAX_KEYWORDS,
    MAX_NAME_LENGTH,
    DT_MIN_THRESHOLD,
)
from app.models import DataSet, InspectionType


def _dedupe(values: list) -> list:
    """Remove duplicates from a list while preserving the original order.

    Args:
        values: Any list of hashable items.

    Returns:
        list: The list without repetitions, first occurrence kept.
    """
    # dict preserves insertion order in Python 3.7+, so this is an ordered set.
    return list(dict.fromkeys(values))


def _require_text(value: str, label: str) -> str:
    """Trim a required string and reject it if nothing is left.

    ``Field(min_length=1)`` still accepts "   ", so the emptiness check has to
    happen after trimming.

    Args:
        value: The raw string from the request.
        label: Field name used in the error message.

    Returns:
        str: The trimmed, non-empty string.

    Raises:
        ValueError: If the string is whitespace only.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must not be blank")
    return stripped


def _optional_text(value: str | None) -> str | None:
    """Trim an optional string, collapsing a blank one to None.

    The counterpart of ``_require_text``: here an empty result is legitimate
    and simply means "no value", so nothing is raised.

    Args:
        value: The raw string from the request, or None.

    Returns:
        str | None: The trimmed text, or None when it carries no content.
    """
    if value is None:
        return None
    return value.strip() or None


class DataTypeBase(BaseModel):
    """Fields a client may supply for a Data Type, with their validation rules."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    type: InspectionType = InspectionType.KEYWORDS
    content: list[str] = Field(min_length=1, max_length=DT_MAX_KEYWORDS)
    # Threshold 0 would mark every text - including an empty one - as a violation.
    threshold: int = Field(ge=DT_MIN_THRESHOLD)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        """Trim the name and reject a whitespace-only value.

        Args:
            value: The raw name from the request.

        Returns:
            str: The trimmed name.
        """
        return _require_text(value, "name")

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        """Trim the description, collapsing a blank one to None.

        Args:
            value: The raw description, or None.

        Returns:
            str | None: The trimmed description, or None when empty.
        """
        return _optional_text(value)

    @field_validator("content")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        """Trim every keyword, reject blank ones, and drop duplicates.

        Args:
            value: The raw keyword list from the request.

        Returns:
            list[str]: Trimmed, unique keywords in their original order.

        Raises:
            ValueError: If a keyword is blank or exceeds the length limit.
        """
        keywords = []
        for keyword in value:
            stripped = _require_text(keyword, "keyword")
            if len(stripped) > DT_MAX_KEYWORD_LENGTH:
                raise ValueError(f"keyword exceeds {DT_MAX_KEYWORD_LENGTH} characters")
            keywords.append(stripped)
        return _dedupe(keywords)


class DataTypeCreate(DataTypeBase):
    """Request body of ``POST /api/datatypes``."""


class DataTypeUpdate(DataTypeBase):
    """Request body of ``PUT /api/datatypes/{id}`` - a full replacement."""


class DataTypeRead(DataTypeBase):
    """Representation returned for a Data Type.

    Attributes:
        id: The server-generated identifier.
    """

    # Lets FastAPI build this schema straight from an ORM object's attributes.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class DataSetCreate(BaseModel):
    """Request body of ``POST /api/datasets``."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    data_type_ids: list[uuid.UUID] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        """Trim the name and reject a whitespace-only value.

        Args:
            value: The raw name from the request.

        Returns:
            str: The trimmed name.
        """
        return _require_text(value, "name")

    @field_validator("data_type_ids")
    @classmethod
    def _unique_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        """Drop repeated identifiers so a Data Type is never linked twice.

        Args:
            value: The requested Data Type identifiers.

        Returns:
            list[uuid.UUID]: The identifiers without duplicates.
        """
        return _dedupe(value)


class DataSetUpdate(DataSetCreate):
    """Request body of ``PUT /api/datasets/{id}`` - a full replacement."""


class DataSetRead(BaseModel):
    """Representation returned for a Data Set.

    The storage shape (an association table) differs from the wire shape (a flat
    array of identifiers), so ``from_model`` performs that translation explicitly.

    Attributes:
        id: The server-generated identifier.
        name: The Data Set label.
        data_type_ids: Identifiers of the referenced Data Types.
    """

    id: uuid.UUID
    name: str
    data_type_ids: list[uuid.UUID]

    @classmethod
    def from_model(cls, data_set: DataSet) -> "DataSetRead":
        """Build the API representation from an ORM Data Set.

        Args:
            data_set: A Data Set whose ``data_types`` relationship is loaded.

        Returns:
            DataSetRead: The response object for this Data Set.
        """

        # Creating an alternative constructor 
        # (in addition to the Pydantic's BaseModel default one)
        # to let ORM model.py DataSet class an easy way to transform to DataSetRead

        return cls(
            id=data_set.id,
            name=data_set.name,
            data_type_ids=[data_type.id for data_type in data_set.data_types],
        )
