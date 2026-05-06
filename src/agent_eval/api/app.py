from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_eval.api.routers import cases, datasets, generate, traces


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Eval API",
        description="Agent evaluation dataset management API",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(datasets.router)
    app.include_router(cases.router)
    app.include_router(generate.router)
    app.include_router(traces.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
