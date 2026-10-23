from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..auth import check_service_token

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    check_service_token(request.headers.get("X-Internal-Token"), request.app.state.auth)
    agent = request.app.state.agent
    ready = agent is not None and agent.retriever.ready and not request.app.state.demo
    return JSONResponse({"status": "ready" if ready else "not_ready"}, status_code=200 if ready else 503)
