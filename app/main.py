from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging
from app.api import emails
from app.database import init_db
from app.services.queue import queue_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    return {"status": "healthy"}


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
