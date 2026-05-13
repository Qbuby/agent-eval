from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_eval.api.routers import (
    auth, benchmark, candidates, cases, config, datasets, evaluation, generate,
    governance, projects, routing, scheduler, traces,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Make sure agent_eval.* INFO logs land in stdout. uvicorn's default config
    # leaves the root logger at WARNING; without this, our diagnostics get swallowed.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("agent_eval").setLevel(logging.INFO)

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
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
