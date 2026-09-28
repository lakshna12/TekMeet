"""TekMeet Backend Main Application Entrypoint."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.calls import router as calls_router
from app.api.meetings import router as meetings_router
from app.api.scheduler import router as scheduler_router
from app.api.transcripts import router as transcripts_router
from app.api.summaries import router as summaries_router
from app.core.config import settings
from app.services.meeting_scheduler import meeting_scheduler

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifespan events."""
    logger.info("Starting %s (v%s) in %s mode", settings.app_name, settings.app_version, settings.environment)
    is_valid, missing = settings.validate_azure_credentials()
    if is_valid:
        logger.info(
            "Azure configuration found (Tenant: %s, Client: %s)",
            settings.get_masked_tenant_id(),
            settings.get_masked_client_id(),
        )
    else:
        logger.warning("Azure credentials missing in environment: %s", ", ".join(missing))

    if settings.auto_start_scheduler:
        logger.info("AUTO_START_SCHEDULER enabled. Starting background meeting scheduler loop...")
        meeting_scheduler.start()

    yield

    if meeting_scheduler.is_running:
        logger.info("Stopping background meeting scheduler...")
        meeting_scheduler.stop()

    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="TekMeet Auto-Join Meeting Bot - Backend Service",
    lifespan=lifespan,
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router)
app.include_router(calls_router)
app.include_router(meetings_router)
app.include_router(scheduler_router)
app.include_router(transcripts_router)
app.include_router(summaries_router)


@app.get("/", tags=["Health"])
def root():
    """Root endpoint for service discovery and health check."""
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "status": "online",
        "auth_configured": settings.validate_azure_credentials()[0],
    }
