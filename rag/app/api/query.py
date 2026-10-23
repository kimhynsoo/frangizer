import asyncio
import json

from fastapi import APIRouter, Request

from ..auth import check_jwt, check_principal, check_service_token
from ..errors import ServiceError, unavailable
from ..privacy import redact_query
from ..schemas import AnswerEnvelope, InternalQuery

router = APIRouter()


async def read_json(request: Request):
    if request.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
        raise ServiceError(415, "UNSUPPORTED_MEDIA_TYPE", "JSON 요청이 필요합니다.")
    try:
        # A bounded body avoids buffering an arbitrary upload on a query endpoint.
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 131072:
                raise ServiceError(413, "FILE_TOO_LARGE", "질문 요청이 너무 큽니다.")
            chunks.append(chunk)
        raw = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ServiceError(400, "MALFORMED_REQUEST", "JSON 형식을 확인해 주세요.") from None
    return raw


@router.post("/rag-query", response_model=AnswerEnvelope)
async def query(request: Request):
    check_service_token(request.headers.get("X-Internal-Token"), request.app.state.auth)
    claims = check_jwt(request.headers.get("Authorization"), request.app.state.auth)
    raw = await read_json(request)
    context = InternalQuery.model_validate(raw)
    request.state.question_id = context.question_id
    check_principal(context.principal, claims)
    if request.app.state.agent is None:
        raise unavailable()
    safe_context = redact_query(context)
    try:
        async with asyncio.timeout(context.deadline_ms / 1000):
            result = await request.app.state.replay.execute(
                context, lambda: request.app.state.agent.run(safe_context),
            )
    except TimeoutError:
        raise ServiceError(504, "UPSTREAM_TIMEOUT", "상담 처리 제한 시간을 초과했습니다.", retryable=True) from None
    return AnswerEnvelope(data=result, request_id=request.state.request_id)
