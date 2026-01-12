from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio
import logging
from app.api import emails
from app.config import settings
from app.database import AsyncSessionLocal, init_db
from app.services.queue import queue_service
from app.services.sweeper import SweeperService
from sqlalchemy import text
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting up Azure Email Communication Service Sender")
    if settings.debug:
        await init_db()
    else:
        logger.info(
            "Skipping init_db() schema auto-creation; run migrations before starting"
        )
    await queue_service.connect()

    sweeper_task: asyncio.Task | None = None
    if settings.sweeper_enabled:
        sweeper = SweeperService(
            grace_seconds=settings.sweeper_grace_seconds,
            batch_size=settings.sweeper_batch_size,
            interval_seconds=settings.sweeper_interval_seconds,
            max_requeue_attempts=settings.sweeper_max_requeue_attempts,
        )
        sweeper_task = asyncio.create_task(sweeper.run_forever(AsyncSessionLocal))
        logger.info("Sweeper enabled")

    yield
    # Shutdown
    logger.info("Shutting down")
    if sweeper_task is not None:
        sweeper_task.cancel()
        _, pending = await asyncio.wait({sweeper_task}, timeout=5)
        if pending:
            logger.warning(
                "Sweeper task did not shutdown within timeout; forcing cancel and waiting"
            )
            sweeper_task.cancel()
            await asyncio.gather(sweeper_task, return_exceptions=True)
    await queue_service.disconnect()


app = FastAPI(
    title="Azure Email Communication Service Sender",
    description="REST API for sending emails via Azure Communication Services SMTP Relay",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(emails.router)

if settings.metrics_enabled:
    Instrumentator().instrument(app).expose(
        app,
        endpoint=settings.metrics_path,
        include_in_schema=False,
    )
    logger.info("Prometheus metrics enabled at %s", settings.metrics_path)


async def _dependency_checks() -> dict[str, bool]:
    checks = {"redis": False, "database": False}

    try:
        if queue_service.redis_client is not None:
            await queue_service.redis_client.ping()
            checks["redis"] = True
    except Exception:
        logger.exception("Dependency check failed: redis")

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        logger.exception("Dependency check failed: database")

    return checks


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Azure Email Communication Service Sender",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    checks = await _dependency_checks()
    healthy = all(checks.values())
    payload = {"status": "healthy" if healthy else "unhealthy", "checks": checks}
    if healthy:
        return payload
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )


@app.get("/healthz")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "healthy"}


@app.get("/ready")
@app.get("/readyz")
async def readiness_check():
    """Readiness check endpoint"""
    checks = await _dependency_checks()

    ready = all(checks.values())
    payload = {"status": "ready" if ready else "not_ready", "checks": checks}
    if ready:
        return payload
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )