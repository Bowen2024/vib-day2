from uuid import uuid4

from fastapi import APIRouter, Request

from schemas import HealthData, HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return HealthResponse(request_id=request_id, data=HealthData(status="ok"))
