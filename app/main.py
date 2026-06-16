from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.infra.object_storage import object_storage
from app.observability.tracing import setup_tracing
from app.security.hardening import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.api import (routes_auth, routes_workspace, routes_run, routes_stream,
                     routes_approval, routes_artifact, routes_usage,
                     routes_skill, routes_agent, routes_policy,
                     routes_sandbox, routes_admin, routes_billing,
                     routes_risk, routes_internal, routes_ws, routes_memory)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing("agent-api")
    await object_storage.ensure_bucket()
    if settings.EVENT_BUS == "kafka":
        from app.eventbus.kafka_client import ensure_topics
        await ensure_topics()
    yield
    if settings.EVENT_BUS == "kafka":
        from app.eventbus.kafka_client import KafkaProducerHolder
        await KafkaProducerHolder.close()


app = FastAPI(title="Multi-Tenant Distributed AI Agent Platform",
              version="1.0.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
if settings.ENV == "production":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "Last-Event-ID"],
)

for r in (routes_auth, routes_workspace, routes_run, routes_stream,
          routes_approval, routes_artifact, routes_usage, routes_skill,
          routes_agent, routes_policy, routes_sandbox, routes_admin,
          routes_billing, routes_risk, routes_internal, routes_ws,
          routes_memory):
    app.include_router(r.router)

@app.get("/healthz")
async def healthz():
    return {"ok": True}

app.mount("/", StaticFiles(directory="app/static", html=True), name="console")
