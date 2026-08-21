"""SQLAlchemy ORM models - the physical shape of the data in PostgreSQL.

Three tables implement the many-to-many relationship required by the spec:
``data_types``, ``data_sets`` and the association table ``dataset_data_types``.

Column sizes and CHECK constraints repeat the rules already enforced by
``app.schemas``. That duplication is intentional: Pydantic protects the API,
the database protects the data against bugs and against any future writer that
does not go through this service.

Called by:
    ``app.crud`` (every query), and indirectly ``app.main`` - importing the
    routers imports this module, which registers the tables on ``Base.metadata``
    before ``create_all`` runs.
"""

import uuid
from enum import Enum

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Column,
    Enum as SAEnum,
    ForeignKey,
    String,
    Table,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import (
    DT_INSPECTION_TYPE_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    DT_MIN_THRESHOLD,
)
from app.db import Base


class InspectionType(str, Enum):
    """Inspection engines a Data Type can use.

    Only keyword inspection is required right now, but this enum is the single
    extension point for future engines (substring, regex, pattern, LLM):
    adding a member here plus a matcher implementation is the whole change.

    Inheriting from ``str`` makes members compare and serialize as plain
    strings, so JSON payloads stay ``"keywords"`` rather than an object.
    """

    KEYWORDS = "keywords"


# An association table (not a model class) - it carries no data of its own,
# only the pair of foreign keys, so a plain Table is the minimal representation.
dataset_data_types = Table(
    "dataset_data_types",
    Base.metadata,
    # Deleting a Data Set silently drops its memberships: the links are worthless without it.
    Column("dataset_id", Uuid, ForeignKey("data_sets.id", ondelete="CASCADE"), primary_key=True),
    # Deleting a Data Type that is still in use is REFUSED by the database;
    Column("data_type_id", Uuid, ForeignKey("data_types.id", ondelete="RESTRICT"), primary_key=True),
)


class DataType(Base):
    """A single inspection rule: a list of keywords plus a match threshold.

    Attributes:
        id: Server-generated identifier (never accepted from the client).
        name: Human-readable unique label, e.g. "Sensitive Keywords".
        description: Optional free text explaining what the rule detects.
        type: Which inspection engine evaluates this rule.
        content: The keywords to look for.
        threshold: Minimum number of keyword occurrences required to declare a match.
    """

    __tablename__ = "data_types"

    # Reaching one of these means a validation bug upstream, so they surface as 500,
    # not as a client error - see the sqlstate handling in app.crud.
    __table_args__ = (
        CheckConstraint(f"threshold >= {DT_MIN_THRESHOLD}", name="ck_data_types_threshold_min"),
        CheckConstraint("cardinality(content) > 0", name="ck_data_types_content_not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Indexed because it is unique: the index is what the database uses to enforce uniqueness.
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(
        String(MAX_DESCRIPTION_LENGTH), default=None
    )
    # ``native_enum=False`` stores a VARCHAR guarded by a CHECK constraint instead of a
    # PostgreSQL ENUM type - adding a new inspection type later is then a constraint
    # change rather than an ALTER TYPE. 
    type: Mapped[InspectionType] = mapped_column(
        SAEnum(
            InspectionType,
            native_enum=False,
            length=DT_INSPECTION_TYPE_LENGTH,
            # SQLAlchemy calls ``values_callable`` with the enum
            # class itself (InspectionType), so iterating it yields the members and this stores
            # their values - "keywords" - instead of the default, which would be the names.
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        default=InspectionType.KEYWORDS,
    )
    # A PostgreSQL text[] column. A separate keywords table would only pay off if we
    # ever needed "which Data Types contain word X"; we always read the whole list.
    content: Mapped[list[str]] = mapped_column(ARRAY(String))
    threshold: Mapped[int] = mapped_column()


class DataSet(Base):
    """A named group of Data Types, evaluated together during a scan.

    Attributes:
        id: Server-generated identifier.
        name: Human-readable unique label, e.g. "HR Policy".
        data_types: The Data Types this set references, loaded eagerly.
    """

    __tablename__ = "data_sets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), unique=True, index=True)
    # ``lazy="joined"`` loads the members in the SAME statement as the parent, through a
    # LEFT OUTER JOIN. Eager loading of some kind is mandatory here: touching
    # ``data_set.data_types`` lazily would emit IO in async context and raise MissingGreenlet.
    data_types: Mapped[list[DataType]] = relationship(
        secondary=dataset_data_types,
        lazy="joined",
        order_by=DataType.name,
    )
