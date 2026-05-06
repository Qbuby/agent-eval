from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_eval.api.routers import auth, cases, config, datasets, generate, traces


@asynccontextmanager
async def lifespan(app: FastAPI):
    from agent_eval.config_service import config_service

    await config_service.init_defaults()
    yield


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

    app.include_router(auth.router)
    app.include_router(datasets.router)
    app.include_router(cases.router)
    app.include_router(generate.router)
    app.include_router(traces.router)
    app.include_router(config.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
