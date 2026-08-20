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
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.constants import (
    MAX_DESCRIPTION_LENGTH,
    DT_MAX_KEYWORD_LENGTH,
    DT_MAX_KEYWORDS,
    MAX_NAME_LENGTH,
    MAX_SCAN_TEXT_LENGTH,
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


class ScanStatus(str, Enum):
    """Outcome of a scan, using the exact strings required by the specification."""

    MATCH = "match"
    NOT_MATCHED = "not matched"


class Combine(str, Enum):
    """How the per-Data-Type outcomes are combined into a single verdict.

    It lives here, next to the other API enums, because it is a request field.
    ``app.matchers`` imports it from this module, which keeps the dependency
    one-way: the engine knows the contract, the contract knows no engine.
    """

    OR = "or"
    AND = "and"


class ScanRequest(BaseModel):
    """Request body of ``POST /api/scan``.

    Attributes:
        text: The input to inspect.
        data_set_id: The policy to evaluate the text against.
        combine: How the Data Type outcomes form the verdict. Defaults to OR,
            which is what the specification requires, so the field is optional
            and existing clients are unaffected.
    """

    # The upper bound is the practical protection against a denial-of-service
    # attempt; an empty text can never reach any threshold, so it is rejected too.
    text: str = Field(min_length=1, max_length=MAX_SCAN_TEXT_LENGTH)
    data_set_id: uuid.UUID
    combine: Combine = Combine.OR


class DetectedObject(BaseModel):
    """A Data Type that reached its threshold during a scan.

    Attributes:
        id: Identifier of the Data Type that matched.
        name: Its human-readable name, so the caller need not resolve the id.
        match_count: Occurrences found - the same number the threshold was compared against.
    """

    id: uuid.UUID
    name: str
    match_count: int


class ScanResult(BaseModel):
    """Response body of ``POST /api/scan``.

    ``detected_objects`` stays None when nothing matched, and the route is
    declared with ``response_model_exclude_none`` so the field disappears from
    the JSON entirely - which is exactly the ``{"status": "not matched"}``
    payload the specification asks for.

    Attributes:
        status: Whether any Data Type matched.
        detected_objects: The Data Types that matched, or None.
    """

    status: ScanStatus
    detected_objects: list[DetectedObject] | None = None
