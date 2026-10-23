import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from .api import health, index_jobs, query
from .auth import AuthSettings
from .errors import ServiceError
from .replay import ReplayStore
from .schemas import InternalQuery


def create_app(*, auth: AuthSettings | None = None, agent=None, state_db: str | None = None, demo=False, index_service=None):
    settings = auth or AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.replay = ReplayStore(state_db or os.environ.get("RAG_STATE_DB") or "/var/lib/frangizer/rag/query-results.sqlite3")
        if index_service is not None:
            index_service.recover()
        try:
            yield
        finally:
            if index_service is not None:
                await index_service.close()
            await app.state.replay.close()

    app = FastAPI(title="Frangizer internal counseling API", lifespan=lifespan)
    app.state.auth, app.state.agent, app.state.demo = settings, agent, demo
    app.state.index_service = index_service

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "req-" + uuid4().hex
        request.state.question_id = None
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if demo:
            response.headers["X-Frangizer-Test-Mode"] = "synthetic-fixture"
        return response

    def error_response(request, status, code, message, *, details=(), retryable=False):
        return JSONResponse({
            "error": {
                "code": code, "message": message, "details": list(details),
                "question_id": getattr(request.state, "question_id", None), "retryable": retryable,
            }, "request_id": request.state.request_id,
        }, status_code=status, headers={"X-Request-ID": request.state.request_id})

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return error_response(request, exc.status, exc.code, exc.message, retryable=exc.retryable)

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        # Never echo pydantic's input or provider error messages containing PII.
        details = [{"field": ".".join(str(p) for p in e["loc"]), "code": e["type"],
                    "message": "필드 형식과 허용 범위를 확인해 주세요."} for e in exc.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "요청 형식을 확인해 주세요.", details=details)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return error_response(request, 500, "INTERNAL_ERROR", "요청을 처리하지 못했습니다.")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "MALFORMED_REQUEST"
        return error_response(request, exc.status_code, code, "요청 경로와 메서드를 확인해 주세요.")

    app.include_router(query.router)
    app.include_router(health.router)
    app.include_router(index_jobs.router)

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version="1.0.0", routes=app.routes)
            request_schema = InternalQuery.model_json_schema(ref_template="#/components/schemas/{model}")
            schema["components"]["schemas"].update(request_schema.pop("$defs", {}))
            schema["components"]["schemas"]["InternalQuery"] = request_schema
            schema["components"]["securitySchemes"] = {
                "ServiceAuth": {"type": "apiKey", "in": "header", "name": "X-Internal-Token"},
                "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            }
            operation = schema["paths"]["/rag-query"]["post"]
            operation["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/InternalQuery"},
            }}}
            operation["security"] = [{"ServiceAuth": [], "BearerAuth": []}]
            schema["paths"]["/health"]["get"]["security"] = [{"ServiceAuth": []}]
            index_schema = index_jobs.IndexJobRequest.model_json_schema(ref_template="#/components/schemas/{model}")
            schema["components"]["schemas"].update(index_schema.pop("$defs", {}))
            schema["components"]["schemas"]["IndexJobRequest"] = index_schema
            for path in ("/internal/v1/index-jobs", "/internal/v1/index-jobs/{job_id}"):
                operation = schema["paths"][path]["post" if path.endswith("index-jobs") else "get"]
                operation["security"] = [{"ServiceAuth": []}]
            schema["paths"]["/internal/v1/index-jobs"]["post"]["requestBody"] = {
                "required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/IndexJobRequest"}}},
            }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app
