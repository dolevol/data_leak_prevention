"""Shared limits and defaults.

These values are enforced in two different places on purpose: the API schemas
reject bad input with HTTP 422 before it reaches the database, and the ORM
column definitions and CHECK constraints act as the last line of defence.
Keeping the numbers here guarantees the two layers can never drift apart.

Called by:
    ``app.models`` (column sizes and CHECK constraints), ``app.schemas``
    (validation rules) and ``app.api.deps`` (pagination bounds).
"""

# Shared by both resources.
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 500

# Data Type specifics.
DT_MAX_KEYWORDS = 1000
DT_MAX_KEYWORD_LENGTH = 100
DT_MIN_THRESHOLD = 1
DT_INSPECTION_TYPE_LENGTH = 20

# Scan input bounds. A huge body is the realistic denial-of-service vector for
# this service, so the limit is part of the security posture, not a formality.
MAX_SCAN_TEXT_LENGTH = 1_000_000

# Pagination bounds applied to every listing endpoint.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
