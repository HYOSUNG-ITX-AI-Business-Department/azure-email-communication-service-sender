from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
from app.api import emails
from app.database import AsyncSessionLocal, init_db
from app.services.queue import queue_service
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting up Azure Email Communication Service Sender")
    await init_db()
    await queue_service.connect()
    yield
    # Shutdown
    logger.info("Shutting down")
    await queue_service.disconnect()


app = FastAPI(
    title="Azure Email Communication Service Sender",
    description="REST API for sending emails via Azure Communication Services SMTP Relay",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(emails.router)


async def _dependency_checks() -> dict[str, bool]:
    checks = {"redis": False, "database": False}

    try:
        if queue_service.redis_client is not None:
            await queue_service.redis_client.ping()
            checks["redis"] = True
    except RedisError:
        logger.exception("Dependency check failed: redis")

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = True
    except SQLAlchemyError:
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
    import os
    import uvicorn
    from app.config import settings
    
    reload = os.getenv("DEBUG", "false").lower() == "true"
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
    )
