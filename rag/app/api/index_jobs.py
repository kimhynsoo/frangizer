from fastapi import APIRouter, Request
from pydantic import TypeAdapter

from ..auth import check_service_token
from ..errors import ServiceError, unavailable
from ..indexing.job import JobConflict
from ..indexing.models import IndexJob, IndexJobRequest
from ..schemas import ContractModel, Id
from .query import read_json

router = APIRouter()


class JobEnvelope(ContractModel):
    data: IndexJob
    request_id: Id


def service(request):
    check_service_token(request.headers.get("X-Internal-Token"), request.app.state.auth)
    if request.app.state.index_service is None:
        raise unavailable()
    return request.app.state.index_service


@router.post("/internal/v1/index-jobs", status_code=202, response_model=JobEnvelope)
async def submit(request: Request):
    manager = service(request)
    payload = IndexJobRequest.model_validate(await read_json(request))
    try:
        job = await manager.submit(payload)
    except JobConflict:
        raise ServiceError(409, "CONFLICT", "같은 작업 ID에 다른 요청이 있습니다.") from None
    return JobEnvelope(data=job, request_id=request.state.request_id)


@router.get("/internal/v1/index-jobs/{job_id}", response_model=JobEnvelope)
async def get_job(job_id: str, request: Request):
    manager = service(request)
    checked_id = TypeAdapter(Id).validate_python(job_id)
    return JobEnvelope(data=manager.get(checked_id), request_id=request.state.request_id)
