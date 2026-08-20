"""HTTP endpoint for ``/api/scan`` - synchronous text inspection.

Synchronous here refers to the API contract: the caller receives the verdict in
the same HTTP response, never a job id to poll. It says nothing about the
process topology - the service still scales by running several workers.

The endpoint only loads, delegates and translates. The inspection logic itself -
thresholds and the OR/AND combination - lives in ``app.matchers.detect``.

Called by:
    ``app.main`` through ``app.include_router``.
"""

from fastapi import APIRouter, HTTPException, status

from app import crud
from app.api.deps import SessionDep
from app.matchers import detect
from app.schemas import ScanRequest, ScanResult, ScanStatus

router = APIRouter(prefix="/api/scan", tags=["Scan"])


@router.post(
    "",
    response_model=ScanResult,
    # Drops ``detected_objects`` from the JSON when nothing matched, so the
    # response is exactly {"status": "not matched"} as the specification requires.
    response_model_exclude_none=True,
)
async def scan_text(payload: ScanRequest, session: SessionDep) -> ScanResult:
    """Scan a text against a Data Set and return the result immediately.

    Args:
        payload: The text to inspect, the Data Set to evaluate it against, and
            how to combine the per-Data-Type outcomes.
        session: The request-scoped database session.

    Returns:
        ScanResult: ``match`` with the detected Data Types, or ``not matched``.

    Raises:
        HTTPException: 404 if no Data Set has this id.
    """
    # One query loads the Data Set and, through lazy="selectin", all its members.
    data_set = await crud.get_data_set(session, payload.data_set_id)
    if data_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "data set not found")

    detected = detect(data_set.data_types, payload.text, payload.combine)
    if not detected:
        return ScanResult(status=ScanStatus.NOT_MATCHED)
    return ScanResult(status=ScanStatus.MATCH, detected_objects=detected)
