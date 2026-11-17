import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.auth.routes import router as auth_router
from app.core.config import get_settings
from app.db.session import get_engine
from app.organizations.routes import router as organizations_router
from app.workspaces.routes import router as workspaces_router

settings = get_settings()
schema_heads = set(
    ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations")).get_heads()
)
app = FastAPI(
    title="OMNIMIND AI", docs_url="/docs" if settings.environment != "production" else None
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.request_id = str(uuid.uuid4())
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (
        request.headers.get("origin") not in settings.allowed_origins
    ):
        response: Response = JSONResponse(
            status_code=403,
            content={
                "code": "origin_denied",
                "request_id": request.state.request_id,
                "retryable": False,
            },
        )
    else:
        response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def error(
    request: Request, status: int, code: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        headers=headers,
        content={
            "code": code,
            "request_id": request.state.request_id,
            "retryable": status in {429, 503},
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(request, exc.status_code, str(exc.detail), exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic's default detail may echo passwords and other submitted values.
    return error(request, 422, "invalid_request")


@app.exception_handler(IntegrityError)
async def constraint_error(request: Request, exc: IntegrityError) -> JSONResponse:
    return error(request, 409, "resource_conflict")


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    return error(request, 503, "database_unavailable")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            applied = set(connection.scalars(text("SELECT version_num FROM alembic_version")))
            if applied != schema_heads:
                raise HTTPException(503, "migration_required")
    except SQLAlchemyError as exc:
        raise HTTPException(503, "database_unavailable") from exc
    return {"status": "ready"}


app.include_router(auth_router, prefix="/api/v1")
app.include_router(organizations_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")
