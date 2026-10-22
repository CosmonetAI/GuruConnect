import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout

from .config import get_settings
from .connection_routes import router as connection_router
from .db import Database, one
from .enquiry_routes import router as enquiry_router
from .internal_routes import router as internal_router
from .middleware import RequestBoundary, security_headers
from .recording_routes import router as recording_router
from .routes import router as public_router
from .security import JWTVerifier
from .storage import Storage
from .transcript_routes import router as transcript_router
from .walkin_routes import router as walkin_router
from app.main import app as gg_sales_app
from app.zoho import refresh_loop as gg_refresh_loop

log = logging.getLogger("recorder.api")

# Catalog probes do not invoke workspace creation or write rate-limit records.
# The function from the final migration prevents a partially migrated database
# from reporting ready merely because it can accept a connection.
SCHEMA_READINESS_QUERY = """
select
    to_regclass('public.profiles') is not null
    and to_regclass('public.organisations') is not null
    and to_regclass('public.members') is not null
    and to_regclass('public.invites') is not null
    and to_regclass('public.engine_connections') is not null
    and to_regclass('public.workspace_engine_settings') is not null
    and to_regclass('public.audit_events') is not null
    and to_regclass('public.transcript_versions') is not null
    and to_regclass('public.guruconnect_enquiries') is not null
    and to_regclass('public.guruconnect_walkin_visits') is not null
    and to_regclass('public.guruconnect_walkin_history') is not null
    and to_regclass('public.guruconnect_walkin_agents') is not null
    and to_regclass('public.guruconnect_walkin_recordings') is not null
    and to_regclass('public.guruconnect_missed_visitors') is not null
    and to_regclass('recorder_private.conversation_requests') is not null
    and to_regprocedure('public.create_workspace(uuid,text)') is not null
    and to_regprocedure('public.allow_api_request(text,integer,integer)') is not null
    and to_regprocedure('recorder_private.version_job_guard()') is not null
    and to_regprocedure('public.resume_translation_job(uuid,uuid)') is not null
    and exists(select 1 from information_schema.columns where table_schema='public' and table_name='usage_records' and column_name='input_tokens')
    and exists(select 1 from information_schema.columns where table_schema='public' and table_name='usage_records' and column_name='output_tokens')
    and exists(select 1 from information_schema.columns where table_schema='public' and table_name='usage_records' and column_name='provider_model')
    as schema_ready
"""


def create_app(settings=None, *, allow_local=False):
    settings = settings or get_settings()
    if allow_local and settings.environment in {"development", "local"} and not settings.has_cloud_configuration:
        from .local_api import create_local_app
        return create_local_app(settings)

    @asynccontextmanager
    async def lifespan(app):
        log.info("api_starting environment=%s", settings.environment)
        await app.state.db.open()
        gg_refresh_task = asyncio.create_task(gg_refresh_loop())
        try:
            log.info("api_ready")
            yield
        finally:
            log.info("api_stopping")
            gg_refresh_task.cancel()
            await asyncio.gather(gg_refresh_task, return_exceptions=True)
            await app.state.db.close()

    app = FastAPI(title="GuruConnect API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if settings.deployed else "/docs",
                  redoc_url=None if settings.deployed else "/redoc",
                  openapi_url=None if settings.deployed else "/openapi.json")
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.verifier = JWTVerifier(settings)
    app.state.storage = Storage(settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-User-ID", "X-Workspace-ID", "Idempotency-Key"],
                       expose_headers=["Content-Disposition", "X-Request-ID", "Content-Length", "X-Playback-Mode", "X-Playback-Start-Sample", "X-Playback-Sample-Rate"], max_age=600)

    app.add_middleware(RequestBoundary, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default error includes the submitted credential/input value.
        # Dynamic dictionary keys and validator messages can contain caller
        # secrets too. Only the fixed transport location and error type escape.
        errors = [{"location": [error["loc"][0]] if error["loc"] else [], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"detail": {"code": "validation_failed", "errors": errors}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc):
        request_id = getattr(request.state, "request_id", "unavailable")
        # Never log exception text/traceback: provider and database exceptions
        # can embed credentials, signed URLs, customer text or request bodies.
        log.error("unexpected_error request_id=%s exception_type=%s", request_id, type(exc).__name__)
        return JSONResponse({"detail": {"code": "internal_error", "request_id": request_id}}, status_code=500,
                            headers=security_headers(settings, request_id))

    @app.exception_handler(DatabaseError)
    async def database_error(request, exc):
        code = "database_unavailable"
        status = 503
        message = "Retry after refreshing the durable state"
        if exc.sqlstate in {"23505", "23514", "23503", "40001", "40P01", "55P03"}:
            code, status = "conflicting_state", 409
        elif exc.sqlstate == "42501":
            code, status = "access_denied", 403
        elif exc.sqlstate in {"42P01", "42883", "42703"}:
            code = "database_schema_missing"
            message = "Database migration is required; contact the service administrator"
        log.warning("database_error code=%s sqlstate=%s", code, exc.sqlstate)
        return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(PoolTimeout)
    async def pool_error(request, exc):
        return JSONResponse({"detail": {"code": "database_busy"}}, status_code=503, headers={"Retry-After": "3"})

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "api", "version": "1.0.0"}

    @app.get("/ready")
    async def ready():
        if not settings.configured:
            return JSONResponse({"status": "not_ready", "code": "configuration_missing"}, 503)
        try:
            # Readiness must remain bounded even while the connection pool is
            # waiting on an unreachable database or a stalled catalog query.
            async with asyncio.timeout(3):
                async with app.state.db.connection() as connection:
                    schema = await one(connection, SCHEMA_READINESS_QUERY)
            if not schema or not schema["schema_ready"]:
                return JSONResponse({"status": "not_ready", "code": "database_schema_missing"}, 503)
            return {"status": "ready"}
        except Exception:
            return JSONResponse({"status": "not_ready", "code": "database_unavailable"}, 503)

    for router in (public_router, recording_router, transcript_router, connection_router, internal_router, enquiry_router, walkin_router):
        app.include_router(router)
    # One backend process serves both products. Existing GuruConnect routes
    # remain under /api/v1; the GG sales assistant keeps its /api routes.
    app.mount("/", gg_sales_app)
    return app

app = create_app(allow_local=True)
