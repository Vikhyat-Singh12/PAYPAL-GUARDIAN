from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings
from app.core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables
    await init_db()
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AI financial governance and authorization layer for agentic commerce.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include API v1 endpoints
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint to verify service status."""
    return {
        "status": "healthy",
        "service": "paypal-guardian",
        "milestone": "3 - Atomic Reservation",
    }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint providing service metadata."""
    return {
        "name": settings.PROJECT_NAME,
        "description": "Financial governance and authorization layer for autonomous agents.",
        "docs_url": "/docs",
        "milestone": 3,
    }
