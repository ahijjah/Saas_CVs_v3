from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import engine
from routers import (
    admin, ai_models, ai_prompts, ai_usage, applications, audit_logs, auth, campaigns,
    client_organizations, jobs, platform_config, platform_secrets, public, subscription_plans, tenant,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="CV Analyzer API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)           # no auth — must be before jobs/applications
app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(client_organizations.router)
app.include_router(campaigns.router)
app.include_router(admin.router)
app.include_router(platform_config.router)
app.include_router(subscription_plans.router)
app.include_router(tenant.router)
app.include_router(platform_secrets.router)
app.include_router(ai_models.router)
app.include_router(ai_prompts.router)
app.include_router(ai_usage.router)
app.include_router(audit_logs.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
