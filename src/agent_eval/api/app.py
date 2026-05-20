from contextlib import asynccontextmanager
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_eval.api.middleware import RequestContextMiddleware
from agent_eval.api.routers import (
    auth, benchmark, candidates, cases, config, datasets, evaluation, generate,
    governance, projects, routing, scheduler, traces,
)
from agent_eval.config import settings
from agent_eval.logging_config import setup_logging

setup_logging(settings.logging.level, settings.logging.format)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from agent_eval.config_service import config_service
    from agent_eval.db import close_db
    from agent_eval.evaluation.langfuse_runner import sweep_orphaned_runs
    from agent_eval.scheduler.service import SchedulerService

    await config_service.init_defaults()

    # Mark any test_runs left in 'running' from a previous process as 'interrupted'.
    try:
        n = await sweep_orphaned_runs()
        if n:
            logger.info("swept %d orphaned eval runs to 'interrupted'", n)
    except Exception as e:
        logger.warning("orphaned eval run sweep failed: %s", e)

    svc = SchedulerService()
    scheduler.set_scheduler(svc)
    await svc.start()

    yield

    await svc.stop()
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Eval API",
        description="Agent evaluation dataset management API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware execution order is "last registered, first executed" — CORS
    # must be the outermost layer to handle OPTIONS preflight, so register the
    # request context middleware first (it ends up *inside* CORS at runtime).
    app.add_middleware(
        RequestContextMiddleware,
        log_request_body=settings.logging.request_body,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "-")
        logger.exception(
            "unhandled error: method=%s path=%s request_id=%s",
            request.method, request.url.path, rid,
        )
        body: dict = {
            "detail": f"Internal server error: {type(exc).__name__}",
            "request_id": rid,
        }
        if settings.logging.debug:
            body["error_type"] = type(exc).__name__
            body["error_message"] = str(exc)
            body["traceback"] = traceback.format_exc()
        return JSONResponse(
            status_code=500,
            content=body,
            headers={"X-Request-ID": rid},
        )

    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(benchmark.router)
    app.include_router(candidates.router)
    app.include_router(datasets.router)
    app.include_router(cases.router)
    app.include_router(generate.router)
    app.include_router(traces.router)
    app.include_router(config.router)
    app.include_router(governance.router)
    app.include_router(routing.router)
    app.include_router(scheduler.router)
    app.include_router(evaluation.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
